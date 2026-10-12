"""A scripted stand-in for imaplib.IMAP4, recording every command it gets."""

from __future__ import annotations

from typing import Dict, List, Optional


class FakeConn:
    def __init__(
        self,
        capabilities=("IMAP4REV1", "MOVE", "UIDPLUS", "UNSELECT"),
        uidvalidity: Optional[Dict[str, int]] = None,
        search_results: Optional[Dict[str, List[str]]] = None,
    ):
        self.capabilities = tuple(capabilities)
        self.calls: List[tuple] = []
        self._uidvalidity = uidvalidity or {}
        self._search = search_results or {}
        self._last_selected: Optional[str] = None

    # connection lifecycle
    def login(self, user, password):
        self.calls.append(("LOGIN", user))
        return "OK", [b"logged in"]

    def authenticate(self, mechanism, authobject):
        self.calls.append(("AUTHENTICATE", mechanism, authobject(b"")))
        return "OK", [b"authenticated"]

    def logout(self):
        self.calls.append(("LOGOUT",))
        return "BYE", [b""]

    def close(self):  # must never be called
        self.calls.append(("CLOSE",))
        return "OK", [b""]

    def unselect(self):
        self.calls.append(("UNSELECT",))
        return "OK", [b""]

    # folders
    def select(self, mailbox, readonly=False):
        name = mailbox.strip('"')
        self._last_selected = name
        self.calls.append(("EXAMINE" if readonly else "SELECT", name))
        return "OK", [b"3"]

    def response(self, code):
        if code == "UIDVALIDITY" and self._last_selected in self._uidvalidity:
            return code, [str(self._uidvalidity[self._last_selected]).encode()]
        return code, [None]

    # commands
    def uid(self, command, *args):
        self.calls.append(("UID", command) + tuple(a for a in args if a is not None))
        if command == "SEARCH":
            key = " ".join(a for a in args if a)
            return "OK", [" ".join(self._search.get(key, [])).encode()]
        return "OK", [b""]

    def commands(self):
        return [c[0] if c[0] != "UID" else "UID " + c[1] for c in self.calls]
