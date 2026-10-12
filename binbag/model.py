"""Plain data types shared across binbag."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import getaddresses, parsedate_to_datetime
from typing import Dict, List, Optional

_ADDR_RE = re.compile(r"[^@<>\s,;\"']+@[^@<>\s,;\"']+")


def decode_mime(value: Optional[str]) -> str:
    """Turn a raw MIME header into readable text, never raising."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return value.strip()


def addresses_in(value: Optional[str]) -> List[str]:
    """Every email address in a header value, lowercased and de-duplicated."""
    if not value:
        return []
    found: List[str] = []
    try:
        for _, addr in getaddresses([value]):
            if addr and "@" in addr:
                found.append(addr.strip().lower())
    except Exception:
        pass
    if not found:
        found = [m.group(0).lower() for m in _ADDR_RE.finditer(value)]
    seen, out = set(), []
    for a in found:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


def domain_of(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower() if "@" in address else ""


def root_domain(address_or_domain: str) -> str:
    """co.uk-aware-ish root domain. Good enough for grouping senders."""
    d = domain_of(address_or_domain) or address_or_domain.lower()
    parts = [p for p in d.split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    penultimate = parts[-2]
    if penultimate in {"co", "com", "org", "net", "gov", "ac", "sch"} and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


@dataclass
class Message:
    """One message, header-only. binbag never downloads message bodies."""

    uid: str
    folder: str
    flags: List[str] = field(default_factory=list)
    headers: Dict[str, str] = field(default_factory=dict)
    internal_date: Optional[datetime] = None
    gmail_labels: List[str] = field(default_factory=list)

    # -- convenience views -------------------------------------------------

    def header(self, name: str) -> str:
        value = self.headers.get(name.lower(), "")
        return value if isinstance(value, str) else str(value)

    @property
    def subject(self) -> str:
        return decode_mime(self.header("subject"))

    @property
    def from_display(self) -> str:
        return decode_mime(self.header("from"))

    @property
    def from_address(self) -> str:
        addrs = addresses_in(self.header("from"))
        return addrs[0] if addrs else ""

    @property
    def from_domain(self) -> str:
        return domain_of(self.from_address)

    @property
    def date(self) -> Optional[datetime]:
        if self.internal_date:
            return self.internal_date
        raw = self.header("date")
        if not raw:
            return None
        try:
            dt = parsedate_to_datetime(raw)
        except Exception:
            return None
        if dt is not None and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    @property
    def age_days(self) -> Optional[int]:
        dt = self.date
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - dt).days)

    @property
    def is_unread(self) -> bool:
        return "\\Seen" not in self.flags

    @property
    def is_flagged(self) -> bool:
        return "\\Flagged" in self.flags or "\\Starred" in self.gmail_labels

    @property
    def is_answered(self) -> bool:
        return "\\Answered" in self.flags

    @property
    def unsubscribe_link(self) -> str:
        """The http(s) or mailto target from List-Unsubscribe, if any."""
        raw = self.header("list-unsubscribe")
        if not raw:
            return ""
        candidates = re.findall(r"<([^>]+)>", raw) or [raw]
        for c in candidates:
            if c.lower().startswith("http"):
                return c.strip()
        return candidates[0].strip()


@dataclass
class Verdict:
    """Why binbag thinks a message is (or isn't) junk."""

    message: Message
    score: int
    reasons: List[str] = field(default_factory=list)
    protections: List[str] = field(default_factory=list)
    protected: bool = False

    @property
    def is_junk(self) -> bool:
        return not self.protected and self.score > 0
