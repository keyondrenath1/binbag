"""`binbag serve`: the scan, the rules and the clean-up, in your browser.

Runs entirely on your machine. The server listens on 127.0.0.1 only, and every
API call must carry a random token that's baked into the page it serves, so
no other website (and nothing else on your network) can drive it. It also
checks the Host header, which stops DNS-rebinding tricks.

Rules you set here are written to binbag.rules.toml beside your config, never
into binbag.toml itself.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
import webbrowser
from collections import Counter
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .config import Config, ConfigError
from .imap_client import ImapError
from .model import Message, Verdict, root_domain
from .rulefile import RuleFile
from .rules import Contacts, Scorer


class Session:
    """One account's scan: what was read, how it scored, what was moved."""

    def __init__(self) -> None:
        self.status = "idle"          # idle | scanning | moving | error
        self.error = ""
        self.log: List[str] = []
        self.messages: List[Message] = []
        self.contacts = Contacts()
        self.validity: Dict[str, Optional[int]] = {}
        self.verdicts: List[Verdict] = []
        self.scanned_at: Optional[str] = None
        self.last_move: Optional[Dict[str, Any]] = None
        self.progress: Optional[Dict[str, Any]] = None

    @property
    def busy(self) -> bool:
        return self.status in ("scanning", "moving")


class State:
    """Every account's session. Rules are shared across accounts: a newsletter
    is junk whichever inbox it lands in. One mailbox job runs at a time."""

    def __init__(self, config_path: Optional[Path], account: Optional[str] = None):
        self.config_path = config_path
        self.lock = threading.RLock()
        base = Config.load(config_path, account)
        self.names = [a.name for a in base.accounts]
        self.first = base.account.name
        self.sessions: Dict[str, Session] = {n: Session() for n in self.names}

    # -- config and rules -----------------------------------------------------

    def resolve(self, name: Optional[str]) -> str:
        if not name:
            return self.first
        for n in self.names:
            if n.lower() == str(name).lower():
                return n
        raise ConfigError(f"No account called {name!r}.")

    def cfg(self, name: str) -> Config:
        return Config.load(self.config_path, name)

    def rule_file(self) -> RuleFile:
        cfg = self.cfg(self.first)
        assert cfg.rules_path
        return RuleFile.load(cfg.rules_path)

    def rescore(self, name: str) -> None:
        sess = self.sessions[name]
        cfg = self.cfg(name)
        sess.verdicts = Scorer(cfg.rules, sess.contacts).judge_all(sess.messages) if sess.messages else []

    def rescore_all(self) -> None:
        for n in self.names:
            if self.sessions[n].messages:
                self.rescore(n)

    @property
    def busy(self) -> bool:
        return any(s.busy for s in self.sessions.values())

    # -- long-running jobs ------------------------------------------------------

    def _say(self, name: str):
        def say(*parts: Any) -> None:
            line = " ".join(str(p) for p in parts).strip()
            if line:
                with self.lock:
                    sess = self.sessions[name]
                    sess.log.append(line)
                    sess.log = sess.log[-200:]
        return say

    def start_scan(self, name: str, limit: int = 0, last_days: int = 0) -> bool:
        with self.lock:
            if self.busy:
                return False
            sess = self.sessions[name]
            sess.status, sess.error, sess.log = "scanning", "", []
            sess.progress = {"stage": "connecting", "folder": "", "done": 0, "total": 0}
        threading.Thread(target=self._scan, args=(name, limit, last_days), daemon=True).start()
        return True

    def _scan(self, name: str, limit: int, last_days: int) -> None:
        from .cli import MONTHS, fetch_mailbox

        sess = self.sessions[name]
        say = self._say(name)
        try:
            cfg = self.cfg(name)
            criteria: List[str] = []
            if last_days > 0:
                since = datetime.now() - timedelta(days=last_days)
                criteria = ["SINCE", f"{since.day:02d}-{MONTHS[since.month - 1]}-{since.year}"]
            folders = cfg.rules.folders or ["INBOX"]

            def progress(folder: str, done: int, total: int) -> None:
                with self.lock:
                    sess.progress = {"stage": "reading", "folder": folder, "done": done, "total": total}

            messages, contacts, validity = fetch_mailbox(cfg, folders, criteria, limit, say, progress)
            with self.lock:
                sess.messages, sess.contacts, sess.validity = messages, contacts, validity
                self.rescore(name)
                sess.scanned_at = datetime.now().strftime("%H:%M")
                sess.last_move = None
                sess.progress = None
                sess.status = "idle"
                say(f"  Scored {len(messages)} messages.")
        except (ConfigError, ImapError, OSError) as exc:
            with self.lock:
                sess.status, sess.error, sess.progress = "error", str(exc), None

    def start_move(self, name: str, expected: int) -> Optional[str]:
        with self.lock:
            if self.busy:
                return "Busy: wait for the current job to finish."
            sess = self.sessions[name]
            junk = [v for v in sess.verdicts if v.is_junk]
            if not junk:
                return "Nothing to move."
            if expected != len(junk):
                # The page was showing a different number from what would move.
                return f"The junk count changed to {len(junk)}. Check the list and try again."
            sess.status, sess.error = "moving", ""
            sess.progress = {"stage": "moving", "folder": "", "done": 0, "total": len(junk)}
        threading.Thread(target=self._move, args=(name, junk), daemon=True).start()
        return None

    def _move(self, name: str, junk: List[Verdict]) -> None:
        from .cli import move_junk

        sess = self.sessions[name]
        say = self._say(name)
        try:
            total, dest, skipped = move_junk(self.cfg(name), junk, sess.validity, say)
            moved_ids = {(v.message.folder, v.message.uid) for v in junk if v.message.folder not in skipped}
            with self.lock:
                sess.messages = [m for m in sess.messages if (m.folder, m.uid) not in moved_ids]
                self.rescore(name)
                sess.last_move = {"moved": total, "dest": dest, "skipped": skipped,
                                  "at": datetime.now().strftime("%H:%M")}
                sess.progress = None
                sess.status = "idle"
                say(f"  Moved {total} to {dest}. Restore from there if needed.")
        except (ConfigError, ImapError, OSError) as exc:
            with self.lock:
                sess.status, sess.error, sess.progress = "error", str(exc), None

    # -- what the page renders --------------------------------------------------

    def snapshot(self, name: Optional[str] = None) -> Dict[str, Any]:
        with self.lock:
            name = self.resolve(name)
            cfg = self.cfg(name)
            sess = self.sessions[name]
            rf = RuleFile.load(cfg.rules_path) if cfg.rules_path else None
            senders = _senders(sess.verdicts, cfg, rf) if sess.verdicts else []
            junk = sum(1 for v in sess.verdicts if v.is_junk)
            accounts = []
            for a in cfg.accounts:
                s = self.sessions.get(a.name)
                accounts.append({
                    "name": a.name, "username": a.username,
                    "scanned": bool(s and s.scanned_at),
                    "junk": sum(1 for v in s.verdicts if v.is_junk) if s else 0,
                    "status": s.status if s else "idle",
                })
            return {
                "version": __version__,
                "account": name,
                "username": cfg.account.username,
                "accounts": accounts,
                "folders": cfg.rules.folders or ["INBOX"],
                "threshold": cfg.rules.threshold,
                "status": sess.status,
                "busy_elsewhere": self.busy and not sess.busy,
                "error": sess.error,
                "log": sess.log[-12:],
                "scanned_at": sess.scanned_at,
                "totals": {"scanned": len(sess.verdicts), "junk": junk, "kept": len(sess.verdicts) - junk,
                           "unread": sum(1 for v in sess.verdicts if v.message.is_unread)},
                "senders": senders,
                "last_move": sess.last_move,
                "progress": sess.progress,
                "rules_file": str(cfg.rules_path) if cfg.rules_path else "",
            }


def _senders(verdicts: List[Verdict], cfg: Optional[Config], rf: Optional[RuleFile]) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Verdict]] = {}
    for v in verdicts:
        groups.setdefault(v.message.from_address or "(unknown)", []).append(v)

    base_rules = cfg.rules if cfg else None
    out = []
    for addr, vs in groups.items():
        domain = addr.rsplit("@", 1)[-1] if "@" in addr else ""
        dates = [v.message.date for v in vs if v.message.date]
        junk = [v for v in vs if v.is_junk]
        why = Counter((v.reasons or ["?"])[0] for v in junk).most_common(1)
        keep_why = Counter((v.protections or ["below the junk threshold"])[0] for v in vs if not v.is_junk).most_common(1)
        rule = None
        for target in (addr, domain):
            if rf and target:
                rule = rf.rule_for(target)
                if rule:
                    rule["source"] = "browser"
                    break
        if not rule and base_rules:
            for target in (addr, domain, root_domain(domain) if domain else ""):
                if target and target in base_rules.block:
                    rule = {"kind": "block", "sender": target, "source": "config"}
                elif target and target in base_rules.allow:
                    rule = {"kind": "allow", "sender": target, "source": "config"}
                else:
                    for e in base_rules.expire:
                        if e.sender == target:
                            rule = {"kind": "expire", "sender": target, "source": "config",
                                    "before": e.before.isoformat() if e.before else None,
                                    "older_than_days": e.older_than_days}
                if rule:
                    break
        unsub = next((v.message.unsubscribe_link for v in vs if v.message.unsubscribe_link.startswith("http")), "")
        out.append({
            "address": addr,
            "domain": domain,
            "display": _display_name(vs[0].message.from_display) or addr,
            "total": len(vs),
            "junk": len(junk),
            "unread": sum(1 for v in vs if v.message.is_unread),
            "newest": max(dates).date().isoformat() if dates else "",
            "oldest": min(dates).date().isoformat() if dates else "",
            "why": why[0][0] if why else "",
            "keep_why": keep_why[0][0] if keep_why else "",
            "rule": rule,
            "unsubscribe": unsub,
            "messages": [
                {"date": v.message.date.date().isoformat() if v.message.date else "",
                 "subject": v.message.subject or "(no subject)",
                 "junk": v.is_junk, "score": v.score,
                 "why": (v.reasons or v.protections or [""])[0],
                 "unread": v.message.is_unread}
                for v in sorted(vs, key=lambda v: v.message.date.timestamp() if v.message.date else 0, reverse=True)[:25]
            ],
        })
    out.sort(key=lambda s: (-s["junk"], -s["total"], s["address"]))
    return out


def _display_name(from_header: str) -> str:
    """'Gym Chain <marketing@gym.example>' -> 'Gym Chain'."""
    from email.utils import parseaddr

    name, _addr = parseaddr(from_header or "")
    return name.strip().strip('"').strip()


# -- HTTP -------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    state: State
    token: str
    port: int
    server_version = "binbag"

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    # -- guards --

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def _token_ok(self) -> bool:
        return secrets.compare_digest(self.headers.get("X-Binbag-Token", ""), self.token)

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                         "connect-src 'self'; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, data: Any) -> None:
        self._send(code, json.dumps(data).encode(), "application/json; charset=utf-8")

    def _body(self) -> Dict[str, Any]:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 100_000:
            raise ValueError("request too large")
        return json.loads(self.rfile.read(n) or b"{}")

    # -- routes --

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, b"Forbidden", "text/plain")
        if self.path in ("/", "/index.html"):
            page = PAGE.replace("__TOKEN__", self.token)
            return self._send(200, page.encode(), "text/html; charset=utf-8")
        if self.path.split("?")[0] == "/api/state":
            if not self._token_ok():
                return self._json(403, {"error": "bad token"})
            from urllib.parse import parse_qs, urlparse

            name = (parse_qs(urlparse(self.path).query).get("account") or [None])[0]
            try:
                return self._json(200, self.state.snapshot(name))
            except ConfigError as exc:
                return self._json(400, {"error": str(exc)})
        self._send(404, b"Not found", "text/plain")

    def do_POST(self):
        if not self._host_ok() or not self._token_ok():
            return self._json(403, {"error": "forbidden"})
        try:
            body = self._body()
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"error": "bad request"})
        st = self.state
        try:
            name = st.resolve(body.get("account"))
        except ConfigError as exc:
            return self._json(400, {"error": str(exc)})

        if self.path == "/api/scan":
            ok = st.start_scan(name, limit=int(body.get("limit") or 0), last_days=int(body.get("last_days") or 0))
            return self._json(200 if ok else 409, {"ok": ok} if ok else {"error": "Busy: another scan or move is running."})

        if self.path == "/api/rule":
            sender = str(body.get("sender", "")).strip().lower()
            kind = body.get("kind")
            if not sender or len(sender) > 254 or any(c in sender for c in '"\n\r'):
                return self._json(400, {"error": "bad sender"})
            with st.lock:
                if st.busy:
                    return self._json(409, {"error": "Busy: wait for the current job to finish."})
                rf = st.rule_file()
                try:
                    if kind == "block":
                        rf.set_block(sender)
                    elif kind == "allow":
                        rf.set_allow(sender)
                    elif kind == "expire":
                        before = body.get("before")
                        days = body.get("older_than_days")
                        rf.set_expire(sender,
                                      before=date.fromisoformat(before) if before else None,
                                      older_than_days=int(days) if days else None)
                    elif kind == "clear":
                        rf.clear(sender)
                    else:
                        return self._json(400, {"error": "unknown rule"})
                    rf.save()
                    st.rescore_all()
                except (ValueError, ConfigError) as exc:
                    return self._json(400, {"error": str(exc)})
            return self._json(200, st.snapshot(name))

        if self.path == "/api/clean":
            err = st.start_move(name, int(body.get("expected", -1)))
            return self._json(409 if err else 200, {"error": err} if err else {"ok": True})

        self._json(404, {"error": "not found"})


def serve(config_path: Optional[Path], port: int = 8765, open_browser: bool = True,
          account: Optional[str] = None) -> int:
    state = State(config_path, account)
    token = secrets.token_urlsafe(24)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    actual = server.server_address[1]
    Handler.state, Handler.token, Handler.port = state, token, actual
    url = f"http://127.0.0.1:{actual}/"
    print(f"\n  binbag is running at {url}")
    print(f"  Accounts: {', '.join(state.names)}. Only this computer can reach it.")
    print("  Press Ctrl+C to stop.\n")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.\n")
    finally:
        server.server_close()
    return 0


def make_server(state: State, port: int = 0):
    """For tests: a server bound to a free port, plus its token."""
    token = secrets.token_urlsafe(24)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    Handler.state, Handler.token, Handler.port = state, token, server.server_address[1]
    return server, token


PAGE = (Path(__file__).with_name("web_page.html")).read_text(encoding="utf-8") if (Path(__file__).with_name("web_page.html")).exists() else ""
