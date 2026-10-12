"""Config loading. Credentials come from the environment, never from the repo."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_CONFIG_NAMES = ("binbag.toml", ".binbag.toml")

# Known provider hosts, so `binbag init` can guess for you.
PROVIDERS: Dict[str, Dict[str, Any]] = {
    "gmail": {"host": "imap.gmail.com", "port": 993},
    "icloud": {"host": "imap.mail.me.com", "port": 993},
    # Microsoft switched off password and app-password IMAP logins for
    # Outlook.com and Hotmail on 16 September 2024. OAuth2 is the only way in.
    "outlook": {"host": "outlook.office365.com", "port": 993, "auth": "oauth2"},
    "yahoo": {"host": "imap.mail.yahoo.com", "port": 993},
    "fastmail": {"host": "imap.fastmail.com", "port": 993},
    "proton": {"host": "127.0.0.1", "port": 1143},  # Proton Mail Bridge
}
# Other names people reach for. Same server, same rules.
PROVIDER_ALIASES = {"hotmail": "outlook", "live": "outlook", "msn": "outlook", "office365": "outlook"}

AUTH_METHODS = {"password", "oauth2"}


class ConfigError(RuntimeError):
    pass


@dataclass
class Account:
    host: str
    port: int = 993
    username: str = ""
    password_env: str = "BINBAG_PASSWORD"
    starttls: bool = False
    #: "password" (an app password from the environment) or "oauth2".
    auth: str = "password"
    #: OAuth2 only: the Application (client) ID of your own Azure app registration.
    oauth_client_id: str = ""
    #: OAuth2 only: "consumers" for Outlook.com/Hotmail, "organizations" or a
    #: tenant ID for Microsoft 365 work accounts.
    oauth_tenant: str = "consumers"
    #: OAuth2 only: where the refresh token lives. Never inside the repo.
    token_file: str = ""
    #: A short label for this account, e.g. "personal" or "work". Defaults to
    #: the username. Pick one with `-a NAME`.
    name: str = ""
    #: Per-account overrides of [rules] folders and the top-level destination.
    folders: List[str] = field(default_factory=list)
    destination: str = ""

    @property
    def token_path(self) -> Path:
        if self.token_file:
            return Path(self.token_file).expanduser()
        safe = "".join(c if c.isalnum() or c in "@._-" else "_" for c in self.username)
        return Path.home() / ".config" / "binbag" / "tokens" / f"{safe}.json"

    @property
    def password(self) -> str:
        pw = os.environ.get(self.password_env, "")
        if not pw:
            raise ConfigError(
                f"No password found in ${self.password_env}. "
                f"Set it first, e.g.  export {self.password_env}='your-app-password'"
            )
        return pw


@dataclass
class ExpireRule:
    """Bin mail from one sender once it is older than a cut-off.

    For senders whose recent mail you want (receipts, delivery notes) but whose
    back catalogue you don't. Matches a full address, or a domain and anything
    beneath it, so "amazon.co.uk" also covers "shipment-tracking@amazon.co.uk"
    and "auto-confirm@marketplace.amazon.co.uk".
    """

    sender: str
    before: Optional[date] = None
    older_than_days: Optional[int] = None

    def matches_sender(self, address: str) -> bool:
        address = address.lower()
        target = self.sender.lower().lstrip("@")
        if "@" in target:
            return address == target
        domain = address.rsplit("@", 1)[-1] if "@" in address else address
        return domain == target or domain.endswith("." + target)

    def cutoff(self, now: Optional[datetime] = None) -> datetime:
        """The moment before which matching mail is binned. UTC."""
        now = now or datetime.now(timezone.utc)
        cuts = []
        if self.before is not None:
            cuts.append(datetime(self.before.year, self.before.month, self.before.day, tzinfo=timezone.utc))
        if self.older_than_days is not None:
            cuts.append(now - timedelta(days=self.older_than_days))
        # Both given: the earlier cut-off wins, so the rule bins less, not more.
        return min(cuts)

    def describe(self) -> str:
        parts = []
        if self.before is not None:
            parts.append(f"before {self.before.isoformat()}")
        if self.older_than_days is not None:
            parts.append(f"over {self.older_than_days} days old")
        return f"{self.sender} " + " and ".join(parts)


@dataclass
class Rules:
    #: Score at or above which a message is treated as junk.
    threshold: int = 60
    #: Only consider mail older than this. Protects anything still in play.
    min_age_days: int = 3
    #: Folders to scan. Empty means "the inbox only".
    folders: List[str] = field(default_factory=list)
    #: Never touch mail involving these addresses or domains.
    allow: List[str] = field(default_factory=list)
    #: Always junk, whatever else the scoring says.
    block: List[str] = field(default_factory=list)
    #: Build a protective contact list from the Sent folder.
    learn_from_sent: bool = True
    #: How many recent sent messages to learn contacts from.
    sent_sample: int = 2000
    #: Extra subject substrings that mark a message as junk (case-insensitive).
    junk_subjects: List[str] = field(default_factory=list)
    #: Extra subject substrings that protect a message (case-insensitive).
    keep_subjects: List[str] = field(default_factory=list)
    #: Per-sender age limits. See ExpireRule.
    expire: List[ExpireRule] = field(default_factory=list)


@dataclass
class Config:
    account: Account
    rules: Rules = field(default_factory=Rules)
    #: Where junk goes. "trash" (recoverable) or "junk" (spam folder).
    destination: str = "trash"
    #: The file this config came from, when loaded from disk.
    path: Optional[Path] = None
    #: Every account in the file. `account` is the one selected for this run.
    accounts: List[Account] = field(default_factory=list)

    @property
    def rules_path(self) -> Optional[Path]:
        """binbag.rules.toml beside the config: rules set from `binbag serve`."""
        from .rulefile import rules_path_for

        return rules_path_for(self.path) if self.path else None

    @classmethod
    def load(cls, path: Optional[Path] = None, account: Optional[str] = None) -> "Config":
        path = _resolve_path(path)
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
        cfg = cls.from_dict(raw, account)
        cfg.path = path.resolve()
        cfg._merge_rule_file()
        return cfg

    def _merge_rule_file(self) -> None:
        from .rulefile import RuleFile

        rp = self.rules_path
        if not rp or not rp.exists():
            return
        try:
            extra = RuleFile.load(rp)
            expire = [_parse_expire(e) for e in extra.expire]
        except (tomllib.TOMLDecodeError, ConfigError) as exc:
            raise ConfigError(f"{rp.name} is invalid: {exc}") from exc
        # Browser rules are per sender and replace each other, so a sender set
        # there overrides the same sender in binbag.toml, never stacks with it.
        overridden = set(extra.block) | set(extra.allow) | {e.sender for e in expire}
        self.rules.block = [b for b in self.rules.block if b not in overridden] + extra.block
        self.rules.allow = [a for a in self.rules.allow if a not in overridden] + extra.allow
        self.rules.expire = [e for e in self.rules.expire if e.sender not in overridden] + expire

    @classmethod
    def from_dict(cls, raw: Dict[str, Any], account_name: Optional[str] = None) -> "Config":
        if "accounts" in raw and "account" in raw:
            raise ConfigError("Use either [account] or [[accounts]], not both.")
        if "accounts" in raw:
            entries = raw["accounts"]
            if not isinstance(entries, list) or not entries:
                raise ConfigError("[[accounts]] needs at least one account.")
            accounts = [_parse_account(dict(e), multi=True) for e in entries]
        else:
            accounts = [_parse_account(dict(raw.get("account", {})), multi=False)]
        names = [a.name.lower() for a in accounts]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ConfigError(f"Two accounts share the name {dupes[0]!r}. Give each a unique `name`.")
        account = _select_account(accounts, account_name)

        r = raw.get("rules", {})
        rules = Rules(
            threshold=int(r.get("threshold", 60)),
            min_age_days=int(r.get("min_age_days", 3)),
            folders=[str(f) for f in r.get("folders", [])],
            allow=[str(a).lower().strip() for a in r.get("allow", [])],
            block=[str(b).lower().strip() for b in r.get("block", [])],
            learn_from_sent=bool(r.get("learn_from_sent", True)),
            sent_sample=int(r.get("sent_sample", 2000)),
            junk_subjects=[str(s).lower() for s in r.get("junk_subjects", [])],
            keep_subjects=[str(s).lower() for s in r.get("keep_subjects", [])],
            expire=[_parse_expire(e) for e in r.get("expire", [])],
        )

        destination = str(account.destination or raw.get("destination", "trash")).lower()
        if destination not in {"trash", "junk"}:
            raise ConfigError("destination must be 'trash' or 'junk'.")
        if account.folders:
            rules.folders = list(account.folders)

        return cls(account=account, rules=rules, destination=destination, accounts=accounts)


def _parse_account(acct_raw: Dict[str, Any], multi: bool) -> Account:
    where = "[[accounts]]" if multi else "[account]"
    provider = str(acct_raw.pop("provider", "")).lower()
    provider = PROVIDER_ALIASES.get(provider, provider)
    if provider:
        if provider not in PROVIDERS:
            raise ConfigError(
                f"Unknown provider {provider!r}. Known: {', '.join(sorted(PROVIDERS))}. "
                "Or drop `provider` and set `host` yourself."
            )
        for key, value in PROVIDERS[provider].items():
            acct_raw.setdefault(key, value)
    if not acct_raw.get("host"):
        raise ConfigError(f"{where} host (or provider) is required.")
    if not acct_raw.get("username"):
        raise ConfigError(f"{where} username is required." if multi else "account.username is required.")

    username = str(acct_raw["username"])
    name = str(acct_raw.get("name") or username).strip()
    # One shared BINBAG_PASSWORD can't serve two accounts, so with several
    # accounts each gets its own variable unless one is set explicitly:
    # name "work" -> BINBAG_PASSWORD_WORK.
    default_env = "BINBAG_PASSWORD"
    if multi:
        default_env += "_" + "".join(c if c.isalnum() else "_" for c in name).upper()
    account = Account(
        host=str(acct_raw["host"]),
        port=int(acct_raw.get("port", 993)),
        username=username,
        password_env=str(acct_raw.get("password_env", default_env)),
        starttls=bool(acct_raw.get("starttls", False)),
        auth=str(acct_raw.get("auth", "password")).lower(),
        oauth_client_id=str(acct_raw.get("oauth_client_id", "")).strip(),
        oauth_tenant=str(acct_raw.get("oauth_tenant", "consumers")).strip() or "consumers",
        token_file=str(acct_raw.get("token_file", "")),
        name=name,
        folders=[str(f) for f in acct_raw.get("folders", [])],
        destination=str(acct_raw.get("destination", "")).lower(),
    )
    if account.auth not in AUTH_METHODS:
        raise ConfigError("account.auth must be 'password' or 'oauth2'.")
    if account.destination and account.destination not in {"trash", "junk"}:
        raise ConfigError("destination must be 'trash' or 'junk'.")
    return account


def _select_account(accounts: List[Account], wanted: Optional[str]) -> Account:
    if not wanted:
        return accounts[0]
    w = wanted.lower().strip()
    for a in accounts:
        if a.name.lower() == w or a.username.lower() == w:
            return a
    known = ", ".join(a.name for a in accounts)
    raise ConfigError(f"No account called {wanted!r}. This config has: {known}.")


def _parse_expire(raw: Any) -> ExpireRule:
    if not isinstance(raw, dict):
        raise ConfigError("Each [[rules.expire]] entry needs sender and before or older_than_days.")
    sender = str(raw.get("sender", "")).strip().lower()
    if not sender:
        raise ConfigError("[[rules.expire]] needs a sender: an address or a domain.")
    before = raw.get("before")
    if before is not None:
        if isinstance(before, datetime):
            before = before.date()
        elif isinstance(before, str):
            try:
                before = date.fromisoformat(before)
            except ValueError as exc:
                raise ConfigError(f"[[rules.expire]] before must be a date like 2024-01-01, got {before!r}") from exc
        elif not isinstance(before, date):
            raise ConfigError(f"[[rules.expire]] before must be a date like 2024-01-01, got {before!r}")
    older = raw.get("older_than_days")
    if older is not None:
        older = int(older)
        if older < 1:
            raise ConfigError("[[rules.expire]] older_than_days must be at least 1.")
    if before is None and older is None:
        raise ConfigError(f"[[rules.expire]] for {sender!r} needs before or older_than_days.")
    return ExpireRule(sender=sender, before=before, older_than_days=older)


def _resolve_path(path: Optional[Path]) -> Path:
    if path is not None:
        if not path.exists():
            raise ConfigError(f"Config file not found: {path}")
        return path
    for name in DEFAULT_CONFIG_NAMES:
        candidate = Path.cwd() / name
        if candidate.exists():
            return candidate
    home = Path.home() / ".config" / "binbag" / "binbag.toml"
    if home.exists():
        return home
    raise ConfigError(
        "No config found. Run `binbag init` to write a starter binbag.toml."
    )


STARTER_CONFIG = """\
# binbag config. This file holds no password: keep it that way.
# Put your app password in the environment instead:
#   export BINBAG_PASSWORD='xxxx xxxx xxxx xxxx'

[account]
provider = "{provider}"     # gmail | icloud | outlook | yahoo | fastmail | proton
username = "{username}"
{auth_block}
# Junk is moved here, never erased. "trash" is recoverable for ~30 days.
destination = "trash"

[rules]
threshold = 60          # 0-100ish. Lower bins more. 75 is cautious, 45 is brutal.
min_age_days = 3        # leave the last few days of mail alone
folders = ["INBOX"]     # add more folder names to widen the sweep

# Never bin anything involving these. Addresses or whole domains.
allow = [
  "hmrc.gov.uk",
  "gov.uk",
]

# Always bin these, whatever the score says.
block = []

# Learn a protective contact list from everyone you've ever emailed.
learn_from_sent = true
sent_sample = 2000

# Keep a sender's recent mail, bin their back catalogue. Uncomment and edit.
# Expire rules beat the transactional protections, so old receipts can go.
# Flagged mail and mail you replied to are still never touched.
#
# [[rules.expire]]
# sender = "amazon.co.uk"        # a domain covers its subdomains too
# before = 2024-01-01
#
# [[rules.expire]]
# sender = "uber.com"
# older_than_days = 365
"""

PASSWORD_AUTH_BLOCK = """password_env = "BINBAG_PASSWORD"
"""

OAUTH_AUTH_BLOCK = """# Outlook.com and Hotmail need OAuth2. App passwords stopped working in 2024.
# Paste the Application (client) ID from your Azure app registration below,
# then run `binbag login` once. See the README for the five-minute setup.
auth = "oauth2"
oauth_client_id = ""
oauth_tenant = "consumers"   # "organizations" for a work or school account
"""
