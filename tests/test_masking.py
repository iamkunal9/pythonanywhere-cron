import pytest

import main
from main import _mask, redact, register_secret


@pytest.fixture(autouse=True)
def clear_secrets():
    main._SECRETS.clear()
    yield
    main._SECRETS.clear()


def test_mask_long_username():
    assert _mask("pietlibrary") == "pi***ry"


def test_mask_five_chars():
    assert _mask("alice") == "al***ce"


def test_mask_short_username():
    assert _mask("bob") == "b***"


def test_mask_two_chars():
    assert _mask("ab") == "a***"


def test_mask_empty():
    assert _mask("") == "***"


def test_redact_replaces_registered_secret():
    register_secret("pietlibrary")
    assert redact("user=pietlibrary done") == "user=pi***ry done"


def test_redact_inside_url():
    register_secret("pietlibrary")
    url = "https://www.pythonanywhere.com/user/pietlibrary/webapps/"
    redacted = redact(url)
    assert "pietlibrary" not in redacted
    assert "pi***ry" in redacted


def test_redact_default_domain():
    register_secret("pietlibrary")
    assert redact("pietlibrary.pythonanywhere.com") == "pi***ry.pythonanywhere.com"


def test_redact_no_secret_registered():
    assert redact("nothing secret here") == "nothing secret here"


def test_redact_longest_first():
    register_secret("alice")
    register_secret("alicepro")
    # "alicepro" masked as a whole, not as alice + pro
    assert redact("alicepro") == "al***ro"


def test_register_secret_ignores_blank():
    register_secret("")
    register_secret("   ")
    assert main._SECRETS == set()
