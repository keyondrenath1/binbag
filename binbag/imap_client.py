"""A thin, careful wrapper around imaplib.

Design rules, in order of importance:

1. Never download a message body. Headers only. Faster, and it keeps the
   blast radius of a bug tiny.
2. Never EXPUNGE, and never CLOSE a folder opened for writing: CLOSE
   silently expunges every message flagged \\Deleted, including ones another
   mail app flagged and hasn't purged yet. Junk is *moved*, so every decision
   is reversible from the Bin for as long as your provider keeps it.
3. Anything that mutates the mailbox lives in exactly one method: `move`.
"""

from __future__ import annotations

import imaplib
import re
import socket
from contextlib import contextmanager
from datetime import datetime, timezone
from email.parser import BytesHeaderParser
from typing import Dict, Iterable, Iterator, List, Optional, Sequence

from . import imap_utf7, oauth
from .config import Account
from .model import Message

imaplib._MAXLINE = 10_000_000  # some providers send very long LIST lines

HEADER_FIELDS = [
    "FROM",
    "TO",
    "CC",
    "SUBJECT",
    "DATE",
    "REPLY-TO",
    "RETURN-PATH",
    "SENDER",
    "LIST-UNSUBSCRIBE",
    "LIST-ID",
    "PRECEDENCE",
    "AUTO-SUBMITTED",
    "X-MAILER",
    "X-CAMPAIGN",
    "X-CAMPAIGNID",
    "X-MAILGUN-SID",
    "X-FEEDBACK-ID",
    "MESSAGE-ID",
    "IN-REPLY-TO",
]

_LIST_RE = re.compile(rb'\((?P<flags>[^)]*)\)\s+"?(?P<delim>[^" ]+)"?\s+(?P<name>.+)')
_UID_RE = re.compile(rb"UID\s+(\d+)")
_FLAGS_RE = re.compile(rb"FLAGS\s+\(([^)]*)\)")
_INTDATE_RE = re.compile(rb'INTERNALDATE\s+"([^"]+)"')
_GMLABELS_RE = re.compile(rb"X-GM-LABELS\s+\(([^)]*)\)")
_UIDVALIDITY_RE = re.compile(rb"(\d+)")

#: Gmail's inbox tabs. IMAP doesn't put these in X-GM-LABELS, but Gmail's own
#: search syntax (X-GM-RAW) can find them.
GMAIL_CATEGORIES = {
    "promotions": "\\Category Promotions",
    "social": "\\Category Social",
    "forums": "\\Category Forums",
}


class ImapError(RuntimeError):
    pass


class Mailbox:
    def __init__(self, account: Account, timeout: int = 30, verbose: bool = False):
        self.account = account
        self.timeout = timeout
        self.verbose = verbose
        self._conn: Optional[imaplib.IMAP4] = None
        self._folders: Optional[List[tuple[str, List[str]]]] = None
        self._selected: Optional[str] = None
        self._readonly = True
        #: UIDVALIDITY per folder, as seen on the last SELECT. UIDs are only
        #: meaningful alongside it.
        self.uidvalidity: Dict[str, Optional[int]] = {}

    # -- connection --------------------------------------------------------

    def connect(self) -> None:
        acct = self.account
        # Get the credential before opening a socket, so a missing password or
        # sign-in fails instantly instead of after a connection timeout.
        if acct.auth == "oauth2":
            token = oauth.access_token(acct)
            password = ""
        else:
            token = ""
            password = acct.password
        try:
            if acct.starttls:
                conn: imaplib.IMAP4 = imaplib.IMAP4(acct.host, acct.port, timeout=self.timeout)
                conn.starttls()
            else:
                conn = imaplib.IMAP4_SSL(acct.host, acct.port, timeout=self.timeout)
        except (socket.error, OSError) as exc:
            raise ImapError(f"Could not reach {acct.host}:{acct.port} — {exc}") from exc
        try:
            if acct.auth == "oauth2":
                conn.authenticate("XOAUTH2", lambda _challenge: oauth.xoauth2_string(acct.username, token))
            else:
                conn.login(acct.username, password)
        except imaplib.IMAP4.error as exc:
            _safe_logout(conn)
            if acct.auth == "oauth2":
                hint = (
                    "The OAuth token was refused. Run `binbag login` again, and check the "
                    "app registration requested IMAP access."
                )
            elif "outlook" in acct.host or "office365" in acct.host:
                hint = (
                    "Outlook.com and Hotmail no longer accept passwords or app passwords "
                    "over IMAP. Set auth = \"oauth2\" and run `binbag login`."
                )
            else:
                hint = (
                    "For Gmail and iCloud you need an app-specific password, not your "
                    "normal one, and two-factor auth must be on."
                )
            raise ImapError(f"Login refused for {acct.username}. {hint}\nServer said: {exc}") from exc
        self._conn = conn

    def close(self) -> None:
        """Log out without ever sending CLOSE.

        CLOSE on a folder opened read-write permanently expunges everything
        flagged \\Deleted in it, ours or not. UNSELECT (RFC 3691) leaves the
        folder without expunging; LOGOUT never expunges, so where UNSELECT
        isn't supported we just log out.
        """
        if self._conn is None:
            return
        try:
            if self._selected and "UNSELECT" in self.capabilities:
                self._conn.unselect()
        except Exception:
            pass
        _safe_logout(self._conn)
        self._conn = None
        self._selected = None

    @property
    def conn(self) -> imaplib.IMAP4:
        if self._conn is None:
            raise ImapError("Not connected. Call connect() first.")
        return self._conn

    @property
    def capabilities(self) -> Sequence[str]:
        return [c.decode() if isinstance(c, bytes) else c for c in self.conn.capabilities]

    @property
    def is_gmail(self) -> bool:
        return "X-GM-EXT-1" in self.capabilities

    # -- folders -----------------------------------------------------------

    def folders(self) -> List[tuple[str, List[str]]]:
        """[(display_name, [attributes]), ...] with names already decoded."""
        if self._folders is not None:
            return self._folders
        typ, data = self.conn.list()
        if typ != "OK":
            raise ImapError("LIST failed")
        out: List[tuple[str, List[str]]] = []
        for line in data:
            if not line:
                continue
            if isinstance(line, tuple):  # rare literal form
                line = b" ".join(part for part in line if isinstance(part, bytes))
            match = _LIST_RE.match(line)
            if not match:
                continue
            flags = [f.decode(errors="replace") for f in match.group("flags").split()]
            raw_name = match.group("name").strip()
            if raw_name.startswith(b'"') and raw_name.endswith(b'"'):
                raw_name = raw_name[1:-1]
            out.append((imap_utf7.decode(raw_name), flags))
        self._folders = out
        return out

    def find_special(self, attribute: str, fallbacks: Iterable[str] = ()) -> Optional[str]:
        """Locate a folder by SPECIAL-USE attribute, e.g. '\\\\Trash'.

        Gmail calls its bin "[Gmail]/Bin" in the UK and "[Gmail]/Trash" in the
        US, so never hardcode the name: ask the server what it flagged.
        """
        wanted = attribute.lower()
        for name, flags in self.folders():
            if any(f.lower() == wanted for f in flags):
                return name
        lowered = {name.lower(): name for name, _ in self.folders()}
        for candidate in fallbacks:
            if candidate.lower() in lowered:
                return lowered[candidate.lower()]
        return None

    def trash_folder(self) -> Optional[str]:
        return self.find_special(
            "\\Trash", ["[Gmail]/Bin", "[Gmail]/Trash", "Trash", "Bin", "Deleted Items", "Deleted Messages"]
        )

    def junk_folder(self) -> Optional[str]:
        return self.find_special(
            "\\Junk", ["[Gmail]/Spam", "Spam", "Junk", "Junk E-mail", "Junk Email"]
        )

    def sent_folder(self) -> Optional[str]:
        return self.find_special(
            "\\Sent", ["[Gmail]/Sent Mail", "Sent", "Sent Items", "Sent Messages"]
        )

    def select(self, folder: str, readonly: bool = True) -> int:
        """Select a folder and return its message count."""
        typ, data = self.conn.select(_quote(folder), readonly=readonly)
        if typ != "OK":
            raise ImapError(f"Cannot open folder {folder!r}: {_first(data)}")
        self._selected = folder
        self._readonly = readonly
        self.uidvalidity[folder] = self._read_uidvalidity()
        try:
            return int(_first(data) or 0)
        except (TypeError, ValueError):
            return 0

    def _read_uidvalidity(self) -> Optional[int]:
        try:
            _typ, data = self.conn.response("UIDVALIDITY")
        except Exception:
            return None
        raw = _first(data)
        if isinstance(raw, str):
            raw = raw.encode()
        if not isinstance(raw, bytes):
            return None
        match = _UIDVALIDITY_RE.search(raw)
        return int(match.group(1)) if match else None

    # -- reading -----------------------------------------------------------

    def search(self, *criteria: str) -> List[str]:
        args = criteria or ("ALL",)
        typ, data = self.conn.uid("SEARCH", None, *args)  # type: ignore[arg-type]
        if typ != "OK":
            raise ImapError(f"SEARCH failed: {_first(data)}")
        raw = _first(data) or b""
        if isinstance(raw, str):
            raw = raw.encode()
        return [uid.decode() for uid in raw.split()]

    def gmail_categories(self) -> Dict[str, str]:
        """{uid: label} for Gmail inbox tabs in the selected folder.

        Gmail keeps its Promotions/Social/Forums tabs out of X-GM-LABELS, so
        ask with its own search syntax instead. Empty on any other server.
        """
        if not self.is_gmail:
            return {}
        found: Dict[str, str] = {}
        for category, label in GMAIL_CATEGORIES.items():
            try:
                uids = self.search("X-GM-RAW", f'"category:{category}"')
            except ImapError:
                continue
            for uid in uids:
                found.setdefault(uid, label)
        return found

    def fetch_headers(
        self, uids: Sequence[str], batch_size: int = 200, progress=None
    ) -> List[Message]:
        """Header-only fetch, batched. Bodies are never requested."""
        if not uids:
            return []
        gmail = self.is_gmail
        parts = ["UID", "FLAGS", "INTERNALDATE"]
        if gmail:
            parts.append("X-GM-LABELS")
        parts.append("BODY.PEEK[HEADER.FIELDS (%s)]" % " ".join(HEADER_FIELDS))
        spec = "(%s)" % " ".join(parts)

        messages: List[Message] = []
        parser = BytesHeaderParser()
        for start in range(0, len(uids), batch_size):
            chunk = uids[start : start + batch_size]
            typ, data = self.conn.uid("FETCH", ",".join(chunk), spec)
            if typ != "OK":
                raise ImapError(f"FETCH failed: {_first(data)}")
            for item in data:
                if not isinstance(item, tuple) or len(item) < 2:
                    continue
                prefix, payload = item[0], item[1]
                if not isinstance(prefix, bytes) or not isinstance(payload, bytes):
                    continue
                uid_match = _UID_RE.search(prefix)
                if not uid_match:
                    continue
                headers = {
                    k.lower(): _header_text(v) for k, v in parser.parsebytes(payload).items()
                }
                messages.append(
                    Message(
                        uid=uid_match.group(1).decode(),
                        folder=self._selected or "",
                        flags=_parse_flags(prefix),
                        headers=headers,
                        internal_date=_parse_internaldate(prefix),
                        gmail_labels=_parse_gm_labels(prefix),
                    )
                )
            if progress:
                progress(min(start + batch_size, len(uids)), len(uids))
        return messages

    # -- the only mutating call in the codebase ----------------------------

    def move(self, uids: Sequence[str], destination: str, batch_size: int = 200) -> int:
        """Move messages to `destination`. Never expunges, never erases."""
        if not uids:
            return 0
        if self._readonly:
            raise ImapError(
                "Refusing to move: folder was opened read-only. This is a bug, "
                "not a config problem — please report it."
            )
        moved = 0
        has_move = "MOVE" in self.capabilities
        dest = _quote(destination)
        for start in range(0, len(uids), batch_size):
            chunk = ",".join(uids[start : start + batch_size])
            if has_move:
                typ, data = self.conn.uid("MOVE", chunk, dest)
                if typ != "OK":
                    raise ImapError(f"MOVE failed: {_first(data)}")
            else:
                typ, data = self.conn.uid("COPY", chunk, dest)
                if typ != "OK":
                    raise ImapError(f"COPY failed: {_first(data)}")
                typ, data = self.conn.uid("STORE", chunk, "+FLAGS", "(\\Deleted)")
                if typ != "OK":
                    raise ImapError(f"STORE \\Deleted failed: {_first(data)}")
                # UID EXPUNGE only removes the copies we just flagged, and only
                # where the server supports UIDPLUS. Without it we leave the
                # originals flagged and let the mail client tidy up: still
                # reversible, still nothing erased by us.
                if "UIDPLUS" in self.capabilities:
                    self.conn.uid("EXPUNGE", chunk)
            moved += len(chunk.split(","))
        return moved


@contextmanager
def open_mailbox(account: Account, verbose: bool = False) -> Iterator[Mailbox]:
    box = Mailbox(account, verbose=verbose)
    box.connect()
    try:
        yield box
    finally:
        box.close()


# -- parsing helpers -------------------------------------------------------


def _header_text(value) -> str:
    """Header values as plain text, whatever the parser hands back.

    A header with raw 8-bit bytes (a sender name typed in Latin-1, say, with
    no MIME encoding) comes back from the parser as an email.header.Header
    object, not a str. Flatten it here so nothing downstream ever sees one.
    Undecodable bytes become U+FFFD rather than an exception.
    """
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:
        return ""


def _safe_logout(conn: imaplib.IMAP4) -> None:
    try:
        conn.logout()
    except Exception:
        pass


def _quote(name: str) -> str:
    encoded = imap_utf7.encode(name).decode("latin-1")
    return '"%s"' % encoded.replace("\\", "\\\\").replace('"', '\\"')


def _first(data):
    if not data:
        return None
    return data[0]


def _parse_flags(prefix: bytes) -> List[str]:
    match = _FLAGS_RE.search(prefix)
    if not match:
        return []
    return [f.decode(errors="replace") for f in match.group(1).split()]


def _parse_gm_labels(prefix: bytes) -> List[str]:
    match = _GMLABELS_RE.search(prefix)
    if not match:
        return []
    raw = match.group(1).decode(errors="replace")
    return [lbl.strip('"') for lbl in re.findall(r'"[^"]*"|\S+', raw)]


def _parse_internaldate(prefix: bytes) -> Optional[datetime]:
    match = _INTDATE_RE.search(prefix)
    if not match:
        return None
    try:
        dt = datetime.strptime(match.group(1).decode(), "%d-%b-%Y %H:%M:%S %z")
    except ValueError:
        return None
    return dt.astimezone(timezone.utc)
