# pythonanywhere-extend-api

Auto-clicks the "Run until 3 months from today" button on PythonAnywhere's free-tier web app
dashboard, so your app doesn't expire while you're away. Logs in with credentials from `.env`,
re-extracts CSRF tokens per page (Django rotates them), and POSTs the extend form.

## Setup with uv

[uv](https://docs.astral.sh/uv/) is the recommended runner — it manages the venv, installs deps,
and pins a lockfile for reproducible runs.

```powershell
# install uv if you don't have it (Windows PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# from the project directory
uv sync                    # creates .venv, installs deps from pyproject.toml, writes uv.lock
```

Create a `.env` file in the project root. Credentials live in a single `PA_ACCOUNTS`
variable holding a JSON array of accounts. In `.env` it must be on **one line**:

```env
PA_ACCOUNTS=[{"username":"your_username","password":"your_password"}]
```

Add more accounts by appending objects to the array. An optional `"domain"` overrides the
default `<username>.pythonanywhere.com`:

```env
PA_ACCOUNTS=[{"username":"alice","password":"pw1"},{"username":"bob","password":"pw2","domain":"www.bob.com"}]
```

## Run

```powershell
uv run main.py
```

That's it. Exit code 0 on success.

## Run without uv (plain pip)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

## Exit codes

| Code | Meaning                                    | Action                          |
| ---- | ------------------------------------------ | ------------------------------- |
| 0    | every account extended successfully        | nothing                         |
| 1    | one or more accounts failed                | inspect logs for which / why    |
| 2    | PA_ACCOUNTS missing / empty / invalid JSON | fix `.env` or the GitHub secret |

Per-account detail (login rejected, extend refused, network exhausted, etc.) is logged
bound to each `user=<username>`; the process exits `1` if any account failed.

## Logs

Console output is colored INFO+ logs. A full DEBUG log is written to `pa_extend.log`
(rotated at 1MB, 5 files kept).

**Usernames are masked** in all log output (`pietlibrary` → `pi***ry`), including the
dashboard URLs and the default domain. Passwords are never logged. This keeps account
identities out of public GitHub Actions logs and the failure-log artifact. Masking is a
loguru patcher applied to every sink, so it covers the console and the file log alike.

Override log level:

```powershell
$env:PA_LOG_LEVEL='DEBUG'; uv run main.py
```

## GitHub Actions (scheduled, multi-account)

A workflow at `.github/workflows/extend.yml` runs every Monday 06:00 UTC and can also be
triggered manually (Actions tab → **Run workflow**).

1. Add a repository secret named `PA_ACCOUNTS`
   (Settings → Secrets and variables → Actions → New repository secret). Paste the JSON
   array — multi-line is fine in the GitHub secret box:

   ```json
   [
     {"username": "alice", "password": "alice-secret"},
     {"username": "bob",   "password": "bob-secret"}
   ]
   ```

2. The job installs deps with uv and runs `main.py` across all accounts. It attempts every
   account and exits non-zero if any fail, so the workflow turns **red** and GitHub emails
   you. The full `pa_extend.log` is uploaded as a build artifact on failure.

> GitHub disables scheduled workflows after 60 days of repo inactivity, and cron runs can be
> delayed under load. The weekly cadence (vs. the ~3-month extend window) leaves plenty of
> buffer; you can always trigger a manual run.

## Scheduling (Windows Task Scheduler)

Daily at 6am — run uv directly so the venv is resolved automatically:

```
Program:   uv
Arguments: run main.py
Start in:  D:\pythonanywhere-extend-api
```

Or on Linux/macOS via cron:

```
0 6 * * * cd /path/to/pythonanywhere-extend-api && /usr/local/bin/uv run main.py
```

The script is idempotent — PA only allows one extension per 24h, so a daily run is fine.

## Architecture

Single-file (`main.py`) by design — login and extend share the same HTTP client, CSRF flow, and
error model, so splitting would just add import noise. Natural split point if you add more
actions (restart, quota check, etc.): `client.py` (session + login + http_request) /
`actions.py` (extend, restart, ...) / `cli.py`.

Key design choices:

- **Selective retries** (`tenacity`) — only `TransientHTTPError` (connection, timeout, 429, 5xx)
  is retried. Auth failures fail fast so we don't hammer the server with bad credentials.
- **Re-extract CSRF per page** — Django rotates the form token after login; reusing the old one
  yields 403.
- **Success oracle = final URL** — PA's extend endpoint returns 302 → webapps page on success
  rather than a JSON status, so we check `resp.url` instead of parsing flash messages.
