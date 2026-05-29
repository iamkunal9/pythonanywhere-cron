import pytest

from main import PythonAnywhereConfigError, load_accounts


def test_single_account(monkeypatch):
    monkeypatch.setenv(
        "PA_ACCOUNTS", '[{"username": "alice", "password": "pw1"}]'
    )
    assert load_accounts() == [{"username": "alice", "password": "pw1"}]


def test_multiple_accounts(monkeypatch):
    monkeypatch.setenv(
        "PA_ACCOUNTS",
        '[{"username": "alice", "password": "pw1"},'
        ' {"username": "bob", "password": "pw2"}]',
    )
    accounts = load_accounts()
    assert [a["username"] for a in accounts] == ["alice", "bob"]


def test_optional_domain_preserved(monkeypatch):
    monkeypatch.setenv(
        "PA_ACCOUNTS",
        '[{"username": "bob", "password": "pw2", "domain": "www.bob.com"}]',
    )
    assert load_accounts()[0]["domain"] == "www.bob.com"


def test_missing_env_raises(monkeypatch):
    monkeypatch.delenv("PA_ACCOUNTS", raising=False)
    with pytest.raises(PythonAnywhereConfigError):
        load_accounts()


def test_blank_env_raises(monkeypatch):
    monkeypatch.setenv("PA_ACCOUNTS", "   ")
    with pytest.raises(PythonAnywhereConfigError):
        load_accounts()


def test_invalid_json_raises(monkeypatch):
    monkeypatch.setenv("PA_ACCOUNTS", "[{not json}]")
    with pytest.raises(PythonAnywhereConfigError):
        load_accounts()


def test_empty_array_raises(monkeypatch):
    monkeypatch.setenv("PA_ACCOUNTS", "[]")
    with pytest.raises(PythonAnywhereConfigError):
        load_accounts()


def test_non_array_raises(monkeypatch):
    monkeypatch.setenv("PA_ACCOUNTS", '{"username": "a", "password": "b"}')
    with pytest.raises(PythonAnywhereConfigError):
        load_accounts()


def test_missing_password_raises(monkeypatch):
    monkeypatch.setenv("PA_ACCOUNTS", '[{"username": "alice"}]')
    with pytest.raises(PythonAnywhereConfigError):
        load_accounts()


def test_entry_not_object_raises(monkeypatch):
    monkeypatch.setenv("PA_ACCOUNTS", '["alice:pw1"]')
    with pytest.raises(PythonAnywhereConfigError):
        load_accounts()
