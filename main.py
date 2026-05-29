import json
import os
import sys

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from loguru import logger
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

BASE = "https://www.pythonanywhere.com"
LOGIN_URL = f"{BASE}/login/?next=/"

BROWSER_HEADERS = {
    "Sec-Ch-Ua": '"Not-A.Brand";v="24", "Chromium";v="146"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8,"
        "application/signed-exchange;v=b3;q=0.7"
    ),
}

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class PythonAnywhereError(Exception):
    """Base for all errors from this client."""


class PythonAnywhereConfigError(PythonAnywhereError):
    """Permanent — PA_ACCOUNTS missing or malformed. Do not retry."""


class TransientHTTPError(PythonAnywhereError):
    """Network/5xx/429 — safe to retry."""


class PythonAnywhereAuthError(PythonAnywhereError):
    """Permanent — bad credentials or unexpected page layout. Do not retry."""


class PythonAnywhereExtendError(PythonAnywhereError):
    """Permanent — extend endpoint refused the action. Do not retry."""


# --- log redaction -----------------------------------------------------------
# Usernames (and the default <username>.pythonanywhere.com domain / dashboard
# URLs that embed them) would otherwise appear in plaintext logs. On a public
# repo, GitHub Actions logs and the uploaded failure artifact are world-readable,
# so we mask registered secrets everywhere they appear in log output.

_SECRETS: set[str] = set()


def register_secret(value: str) -> None:
    """Register a value to be masked in all subsequent log output."""
    if value and value.strip():
        _SECRETS.add(value)


def _mask(value: str) -> str:
    """Reveal first 2 + last 2 chars, fixed-length masked middle.

    Short values (<= 4 chars) reveal only the first char so they aren't
    effectively exposed. The middle is always '***' so the original length
    is not leaked.
    """
    if len(value) <= 4:
        return f"{value[0]}***" if value else "***"
    return f"{value[:2]}***{value[-2:]}"


def redact(text: str) -> str:
    """Replace every registered secret in ``text`` with its masked form.

    Longest secrets first so a secret that is a substring of another
    (e.g. 'alice' within 'alicepro') doesn't get half-masked.
    """
    for secret in sorted(_SECRETS, key=len, reverse=True):
        if secret in text:
            text = text.replace(secret, _mask(secret))
    return text


def _redact_patcher(record) -> None:
    """Loguru patcher: redact the message and any string values in ``extra``."""
    record["message"] = redact(record["message"])
    extra = record["extra"]
    for key, value in list(extra.items()):
        if isinstance(value, str):
            extra[key] = redact(value)


def configure_logging() -> None:
    logger.remove()
    logger.configure(patcher=_redact_patcher)
    logger.add(
        sys.stderr,
        level=os.environ.get("PA_LOG_LEVEL", "INFO"),
        format=(
            "<green>{time:HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{extra}</cyan> "
            "<level>{message}</level>"
        ),
        backtrace=False,
        diagnose=False,
    )
    logger.add(
        "pa_extend.log",
        level="DEBUG",
        rotation="1 MB",
        retention=5,
        enqueue=True,
    )


@retry(
    retry=retry_if_exception_type(TransientHTTPError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=15),
    before_sleep=before_sleep_log(logger, "WARNING"),
    reraise=True,
)
def http_request(
    session: requests.Session,
    method: str,
    url: str,
    **kwargs,
) -> requests.Response:
    """Issue an HTTP request, classifying failures as transient vs permanent.

    Transient (ConnectionError, Timeout, 5xx, 429) raises TransientHTTPError
    and is retried with exponential backoff. Everything else propagates.
    """
    kwargs.setdefault("timeout", 20)
    logger.debug(f"{method} {url}")
    try:
        resp = session.request(method, url, **kwargs)
    except (requests.ConnectionError, requests.Timeout) as exc:
        raise TransientHTTPError(f"{method} {url}: {exc}") from exc
    except requests.RequestException as exc:
        raise PythonAnywhereError(f"{method} {url}: {exc}") from exc

    if resp.status_code in RETRYABLE_STATUS:
        raise TransientHTTPError(
            f"{method} {url} returned {resp.status_code}"
        )
    return resp


def extract_csrf_middleware_token(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    field = soup.find("input", attrs={"name": "csrfmiddlewaretoken"})
    if field is None or not field.get("value"):
        raise PythonAnywhereAuthError(
            "csrfmiddlewaretoken not found — page layout may have changed."
        )
    return field["value"]


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    return session


def login(session: requests.Session, username: str, password: str) -> requests.Response:
    log = logger.bind(stage="login", user=username)
    log.info("loading login page")
    get_resp = http_request(session, "GET", LOGIN_URL)
    if get_resp.status_code != 200:
        raise PythonAnywhereAuthError(
            f"login page returned {get_resp.status_code}"
        )

    csrf_token = extract_csrf_middleware_token(get_resp.text)
    log.debug(f"got csrf form token ({csrf_token[:8]}...)")

    payload = {
        "csrfmiddlewaretoken": csrf_token,
        "auth-username": username,
        "auth-password": password,
        "login_view-current_step": "auth",
    }
    post_headers = {
        "Origin": BASE,
        "Referer": LOGIN_URL,
        "Content-Type": "application/x-www-form-urlencoded",
    }

    log.info("submitting credentials")
    post_resp = http_request(
        session,
        "POST",
        LOGIN_URL,
        headers=post_headers,
        data=payload,
        allow_redirects=True,
    )

    if post_resp.status_code >= 400:
        raise PythonAnywhereAuthError(
            f"login POST returned {post_resp.status_code}"
        )

    landed_back_on_login = post_resp.url.rstrip("/").endswith("/login")
    if landed_back_on_login or 'name="auth-password"' in post_resp.text:
        raise PythonAnywhereAuthError(
            "login rejected — check PA_USERNAME / PA_PASSWORD."
        )

    log.success(f"logged in (landed: {post_resp.url})")
    return post_resp


def extend_webapp(
    session: requests.Session,
    username: str,
    domain: str | None = None,
) -> requests.Response:
    if domain is None:
        domain = f"{username}.pythonanywhere.com"

    webapps_url = f"{BASE}/user/{username}/webapps/"
    extend_url = f"{BASE}/user/{username}/webapps/{domain}/extend"
    log = logger.bind(stage="extend", user=username, domain=domain)

    log.info("loading webapps page")
    page = http_request(session, "GET", webapps_url)
    if page.status_code != 200:
        raise PythonAnywhereExtendError(
            f"webapps page returned {page.status_code}"
        )
    if 'name="auth-password"' in page.text:
        raise PythonAnywhereExtendError(
            "webapps page returned login form — session not authenticated."
        )

    csrf_token = extract_csrf_middleware_token(page.text)
    log.debug(f"got csrf form token ({csrf_token[:8]}...)")

    post_headers = {
        "Origin": BASE,
        "Referer": webapps_url,
        "Content-Type": "application/x-www-form-urlencoded",
    }

    log.info("submitting extend")
    resp = http_request(
        session,
        "POST",
        extend_url,
        headers=post_headers,
        data={"csrfmiddlewaretoken": csrf_token},
        allow_redirects=True,
    )

    if resp.status_code >= 400:
        raise PythonAnywhereExtendError(
            f"extend POST returned {resp.status_code}"
        )

    log.success(f"extended (landed: {resp.url})")
    return resp


def load_accounts() -> list[dict]:
    """Parse and validate PA_ACCOUNTS into a list of account dicts.

    Each account has non-empty 'username' and 'password'; 'domain' is optional.
    Raises PythonAnywhereConfigError on any missing/malformed configuration.
    """
    raw = os.environ.get("PA_ACCOUNTS", "").strip()
    if not raw:
        raise PythonAnywhereConfigError(
            "PA_ACCOUNTS is not set — provide a JSON array of "
            '{"username", "password"} objects.'
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PythonAnywhereConfigError(
            f"PA_ACCOUNTS is not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, list) or not data:
        raise PythonAnywhereConfigError(
            "PA_ACCOUNTS must be a non-empty JSON array."
        )

    accounts: list[dict] = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise PythonAnywhereConfigError(
                f"PA_ACCOUNTS[{i}] must be an object with username/password."
            )
        username = entry.get("username")
        password = entry.get("password")
        if not username or not password:
            raise PythonAnywhereConfigError(
                f"PA_ACCOUNTS[{i}] needs non-empty 'username' and 'password'."
            )
        account = {"username": username, "password": password}
        if entry.get("domain"):
            account["domain"] = entry["domain"]
        accounts.append(account)

    return accounts


def main() -> int:
    configure_logging()
    load_dotenv()

    try:
        accounts = load_accounts()
    except PythonAnywhereConfigError as exc:
        logger.error(f"configuration error: {exc}")
        return 2

    for account in accounts:
        register_secret(account["username"])
        register_secret(account["password"])

    failures: list[str] = []
    for account in accounts:
        username = account["username"]
        log = logger.bind(user=username)
        session = build_session()
        try:
            login(session, username, account["password"])
            extend_webapp(session, username, account.get("domain"))
        except PythonAnywhereError as exc:
            log.error(f"extend failed: {exc}")
            failures.append(username)
        except Exception:
            log.exception("unexpected error")
            failures.append(username)

    total = len(accounts)
    ok = total - len(failures)
    if failures:
        logger.error(
            f"{ok}/{total} accounts extended — failed: {', '.join(failures)}"
        )
        return 1

    logger.success(f"{ok}/{total} accounts extended")
    return 0


if __name__ == "__main__":
    sys.exit(main())
