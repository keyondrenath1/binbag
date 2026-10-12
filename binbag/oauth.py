"""OAuth2 for Microsoft mailboxes (Outlook.com, Hotmail, Live, Microsoft 365).

Microsoft switched off password and app-password IMAP logins for personal
accounts on 16 September 2024, so this is the only way in. It uses the device
code flow: binbag prints a short code, you type it into a Microsoft page in any
browser, and binbag never sees your password.

Standard library only. Tokens are cached in a file readable by you alone,
outside the repo, and the refresh token is rotated every time it's used.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .config import Account, ConfigError

SCOPE = "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"
AUTHORITY = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0"

#: Refresh this many seconds before the access token actually expires.
EXPIRY_MARGIN = 120

Poster = Callable[[str, Dict[str, str]], Dict[str, Any]]


class OAuthError(ConfigError):
    """Sign-in failed or needs redoing. Subclasses ConfigError so the CLI
    reports it cleanly instead of with a traceback."""


# -- HTTP ------------------------------------------------------------------


def _post(url: str, form: Dict[str, str]) -> Dict[str, Any]:
    """POST a form, return the JSON body. Microsoft puts OAuth errors in a 400
    body, so those come back as data rather than as exceptions."""
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(url, data=data, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode())
        except Exception:
            raise OAuthError(f"Microsoft sign-in returned HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise OAuthError(f"Could not reach Microsoft sign-in: {exc.reason}") from exc


def _endpoint(account: Account, path: str) -> str:
    return AUTHORITY.format(tenant=urllib.parse.quote(account.oauth_tenant or "consumers")) + path


def _require_client_id(account: Account) -> str:
    if not account.oauth_client_id:
        raise OAuthError(
            "account.oauth_client_id is empty. Outlook and Hotmail need an Azure app "
            "registration of your own: the README walks through it in five minutes."
        )
    return account.oauth_client_id


# -- token cache -----------------------------------------------------------


def load_tokens(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_tokens(path: Path, tokens: Dict[str, Any]) -> None:
    """Write atomically with 0600 permissions, so no other user can read it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(tokens, fh)
    os.replace(tmp, path)


def _store(account: Account, response: Dict[str, Any], previous: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if "access_token" not in response:
        raise OAuthError(_describe_error(response))
    tokens = {
        "access_token": response["access_token"],
        # Microsoft rotates refresh tokens; keep the old one only if no new one came back.
        "refresh_token": response.get("refresh_token") or (previous or {}).get("refresh_token", ""),
        "expires_at": int(time.time()) + int(response.get("expires_in", 3600)),
        "username": account.username,
        "client_id": account.oauth_client_id,
        "tenant": account.oauth_tenant,
    }
    save_tokens(account.token_path, tokens)
    return tokens


def _describe_error(response: Dict[str, Any]) -> str:
    code = response.get("error", "unknown_error")
    desc = str(response.get("error_description", "")).split("\r\n")[0].split("\n")[0]
    hints = {
        "invalid_grant": "Your saved sign-in has expired or been revoked. Run `binbag login` again.",
        "unauthorized_client": "The app registration doesn't allow this. Check 'Allow public client flows' is Yes, and that it accepts personal Microsoft accounts.",
        "invalid_client": "The app registration doesn't allow this. Check 'Allow public client flows' is Yes.",
        "access_denied": "Sign-in was declined.",
        "expired_token": "The code expired before it was entered. Run `binbag login` again.",
    }
    hint = hints.get(code, "")
    return f"Microsoft sign-in failed ({code}). {hint} {desc}".strip()


# -- the device code flow --------------------------------------------------


def login(
    account: Account,
    say: Callable[[str], None] = print,
    post: Poster = _post,
    sleep: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    """Interactive one-off sign-in. Saves tokens and returns them."""
    client_id = _require_client_id(account)
    start = post(_endpoint(account, "/devicecode"), {"client_id": client_id, "scope": SCOPE})
    if "device_code" not in start:
        raise OAuthError(_describe_error(start))

    say("")
    say(f"  1. Open   {start.get('verification_uri', 'https://microsoft.com/devicelogin')}")
    say(f"  2. Enter  {start['user_code']}")
    say(f"  3. Sign in as {account.username} and allow access to your mail.")
    say("")
    say("  Waiting for you to finish in the browser ...")

    interval = int(start.get("interval", 5))
    deadline = time.time() + int(start.get("expires_in", 900))
    while time.time() < deadline:
        sleep(interval)
        resp = post(
            _endpoint(account, "/token"),
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": client_id,
                "device_code": start["device_code"],
            },
        )
        error = resp.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        return _store(account, resp)
    raise OAuthError(_describe_error({"error": "expired_token"}))


def access_token(account: Account, post: Poster = _post, now: Optional[float] = None) -> str:
    """A valid access token, refreshing silently when needed. Never interactive."""
    _require_client_id(account)
    tokens = load_tokens(account.token_path)
    if not tokens or not tokens.get("refresh_token"):
        raise OAuthError(f"Not signed in to {account.username}. Run `binbag login` first.")
    if tokens.get("username", "").lower() != account.username.lower():
        raise OAuthError(
            f"The saved sign-in at {account.token_path} is for {tokens.get('username')!r}, "
            f"not {account.username!r}. Run `binbag login`."
        )
    current = now if now is not None else time.time()
    if tokens.get("access_token") and int(tokens.get("expires_at", 0)) - EXPIRY_MARGIN > current:
        return tokens["access_token"]

    resp = post(
        _endpoint(account, "/token"),
        {
            "grant_type": "refresh_token",
            "client_id": account.oauth_client_id,
            "refresh_token": tokens["refresh_token"],
            "scope": SCOPE,
        },
    )
    return _store(account, resp, previous=tokens)["access_token"]


def xoauth2_string(username: str, token: str) -> bytes:
    """The SASL XOAUTH2 initial response, as imaplib.authenticate wants it."""
    return f"user={username}\x01auth=Bearer {token}\x01\x01".encode()
