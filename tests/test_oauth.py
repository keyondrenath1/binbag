"""OAuth2 for Outlook and Hotmail, with Microsoft's endpoints scripted."""

from __future__ import annotations

import json
import stat

import pytest

from binbag import oauth
from binbag.config import Account, Config, ConfigError
from binbag.imap_client import Mailbox

from fakes import FakeConn


@pytest.fixture
def account(tmp_path):
    return Account(
        host="outlook.office365.com",
        username="me@hotmail.co.uk",
        auth="oauth2",
        oauth_client_id="client-123",
        token_file=str(tmp_path / "tok.json"),
    )


class Script:
    """Replays canned Microsoft responses and records what was asked."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, url, form):
        self.requests.append((url, dict(form)))
        return self.responses.pop(0)


def test_hotmail_and_outlook_default_to_oauth2():
    for provider in ("outlook", "hotmail", "live"):
        cfg = Config.from_dict({"account": {"provider": provider, "username": "me@hotmail.co.uk"}})
        assert cfg.account.auth == "oauth2"
        assert cfg.account.host == "outlook.office365.com"


def test_auth_must_be_known():
    with pytest.raises(ConfigError, match="auth"):
        Config.from_dict({"account": {"provider": "gmail", "username": "a@b.c", "auth": "magic"}})


def test_login_polls_until_approved_and_saves_private_token(account):
    post = Script(
        {"device_code": "dc", "user_code": "ABCD-1234", "verification_uri": "https://microsoft.com/devicelogin",
         "interval": 1, "expires_in": 600},
        {"error": "authorization_pending"},
        {"error": "slow_down"},
        {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600},
    )
    said = []
    tokens = oauth.login(account, say=said.append, post=post, sleep=lambda _s: None)

    assert tokens["access_token"] == "at-1"
    assert any("ABCD-1234" in line for line in said)
    assert post.requests[0][0].startswith("https://login.microsoftonline.com/consumers/")
    assert "IMAP.AccessAsUser.All" in post.requests[0][1]["scope"]
    saved = json.loads(account.token_path.read_text())
    assert saved["refresh_token"] == "rt-1"
    assert stat.S_IMODE(account.token_path.stat().st_mode) == 0o600


def test_login_reports_declined_sign_in(account):
    post = Script(
        {"device_code": "dc", "user_code": "X", "interval": 1, "expires_in": 600},
        {"error": "access_denied", "error_description": "The user declined."},
    )
    with pytest.raises(oauth.OAuthError, match="declined"):
        oauth.login(account, say=lambda _l: None, post=post, sleep=lambda _s: None)


def test_login_needs_a_client_id(account):
    account.oauth_client_id = ""
    with pytest.raises(oauth.OAuthError, match="oauth_client_id"):
        oauth.login(account, say=lambda _l: None, post=Script(), sleep=lambda _s: None)


def test_fresh_token_used_without_network(account):
    oauth.save_tokens(account.token_path, {"access_token": "at", "refresh_token": "rt",
                                           "expires_at": 10_000, "username": account.username})
    post = Script()
    assert oauth.access_token(account, post=post, now=1_000) == "at"
    assert post.requests == []


def test_expired_token_refreshes_and_rotates(account):
    oauth.save_tokens(account.token_path, {"access_token": "old", "refresh_token": "rt-old",
                                           "expires_at": 1_000, "username": account.username})
    post = Script({"access_token": "new", "refresh_token": "rt-new", "expires_in": 3600})
    assert oauth.access_token(account, post=post, now=999) == "new"
    assert post.requests[0][1]["grant_type"] == "refresh_token"
    assert post.requests[0][1]["refresh_token"] == "rt-old"
    assert json.loads(account.token_path.read_text())["refresh_token"] == "rt-new"


def test_refresh_keeps_old_refresh_token_if_none_returned(account):
    oauth.save_tokens(account.token_path, {"access_token": "old", "refresh_token": "rt-old",
                                           "expires_at": 0, "username": account.username})
    oauth.access_token(account, post=Script({"access_token": "new", "expires_in": 3600}), now=5)
    assert json.loads(account.token_path.read_text())["refresh_token"] == "rt-old"


def test_revoked_refresh_token_says_to_log_in_again(account):
    oauth.save_tokens(account.token_path, {"access_token": "", "refresh_token": "rt",
                                           "expires_at": 0, "username": account.username})
    with pytest.raises(oauth.OAuthError, match="binbag login"):
        oauth.access_token(account, post=Script({"error": "invalid_grant"}), now=5)


def test_not_signed_in(account):
    with pytest.raises(oauth.OAuthError, match="binbag login"):
        oauth.access_token(account, post=Script())


def test_token_for_another_user_is_refused(account):
    oauth.save_tokens(account.token_path, {"access_token": "at", "refresh_token": "rt",
                                           "expires_at": 10**12, "username": "someone@else.com"})
    with pytest.raises(oauth.OAuthError, match="someone@else.com"):
        oauth.access_token(account, post=Script())


def test_xoauth2_string_format():
    assert oauth.xoauth2_string("me@x.com", "TOKEN") == b"user=me@x.com\x01auth=Bearer TOKEN\x01\x01"


def test_mailbox_authenticates_with_xoauth2(account, monkeypatch):
    oauth.save_tokens(account.token_path, {"access_token": "at", "refresh_token": "rt",
                                           "expires_at": 10**12, "username": account.username})
    conn = FakeConn()
    monkeypatch.setattr("binbag.imap_client.imaplib.IMAP4_SSL", lambda *a, **k: conn)
    box = Mailbox(account)
    box.connect()
    assert conn.calls[0] == ("AUTHENTICATE", "XOAUTH2", oauth.xoauth2_string(account.username, "at"))
    assert ("LOGIN", account.username) not in conn.calls


def test_oauth_missing_sign_in_fails_before_any_socket(account, monkeypatch):
    def explode(*_a, **_k):  # pragma: no cover
        raise AssertionError("opened a socket before checking the sign-in")

    monkeypatch.setattr("binbag.imap_client.imaplib.IMAP4_SSL", explode)
    with pytest.raises(oauth.OAuthError):
        Mailbox(account).connect()
