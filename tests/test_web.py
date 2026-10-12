"""`binbag serve`: the local web UI, driven over real HTTP against a fake mailbox."""

from __future__ import annotations

import http.client
import json
import threading
import time
from contextlib import contextmanager
from datetime import date

import pytest

from binbag import cli, web
from binbag.config import Config
from binbag.rulefile import RuleFile

from conftest import make
from test_cli import FakeBox


@pytest.fixture
def app(monkeypatch, tmp_path):
    server_state = {
        "messages": [
            make(uid="1", sender="deals@shop.example.com", subject="Flash sale"),
            make(uid="2", sender="noreply@hmrc.gov.uk", subject="Your tax statement", unsubscribe=False),
            make(uid="3", sender="friend@pals.example", subject="Lunch on Friday?", unsubscribe=False, seen=True),
            make(uid="4", sender="orders@store.example", subject="Your order #77 has been dispatched",
                 unsubscribe=False, age_days=900),
        ],
        "uidvalidity": {"INBOX": 7},
        "selects": [], "searches": [], "moved": [],
    }

    @contextmanager
    def fake_open(_account, verbose=False):
        yield FakeBox(server_state)

    monkeypatch.setattr(cli, "open_mailbox", fake_open)
    cfg = tmp_path / "binbag.toml"
    original = '# my notes\n[account]\nprovider = "gmail"\nusername = "me@gmail.com"\n[rules]\nlearn_from_sent = false\n'
    cfg.write_text(original)

    state = web.State(cfg)
    server, token = web.make_server(state)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def call(method, path, body=None, token_=token, host=f"127.0.0.1:{port}"):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        headers = {"Host": host}
        if token_ is not None:
            headers["X-Binbag-Token"] = token_
        data = None
        if body is not None:
            data = json.dumps(body)
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        try:
            return resp.status, json.loads(raw)
        except ValueError:
            return resp.status, raw.decode()

    def scan():
        assert call("POST", "/api/scan", {})[0] == 200
        for _ in range(100):
            code, st = call("GET", "/api/state")
            if st["status"] != "scanning":
                return st
            time.sleep(0.02)
        raise AssertionError("scan never finished")

    yield {"call": call, "scan": scan, "server": server_state, "cfg": cfg, "original": original,
           "token": token, "state": state}
    server.shutdown()


def sender(st, address):
    return next(s for s in st["senders"] if s["address"] == address)


# -- the guards --------------------------------------------------------------


def test_page_is_served_with_its_token(app):
    code, page = app["call"]("GET", "/", token_=None)
    assert code == 200 and app["token"] in page and "__TOKEN__" not in page


def test_api_refuses_requests_without_the_token(app):
    assert app["call"]("GET", "/api/state", token_=None)[0] == 403
    assert app["call"]("POST", "/api/scan", {}, token_="wrong")[0] == 403
    assert app["call"]("POST", "/api/clean", {"expected": 1}, token_=None)[0] == 403


def test_foreign_host_header_is_refused(app):
    # DNS rebinding: a page on evil.example resolving to 127.0.0.1.
    assert app["call"]("GET", "/", token_=None, host="evil.example:8765")[0] == 403
    assert app["call"]("GET", "/api/state", host="evil.example")[0] == 403


# -- scan, rules, move -------------------------------------------------------


def test_scan_groups_by_sender(app):
    st = app["scan"]()
    assert st["totals"]["scanned"] == 4
    assert sender(st, "deals@shop.example.com")["junk"] == 1
    assert sender(st, "noreply@hmrc.gov.uk")["junk"] == 0
    assert all(c[1] for c in app["server"]["selects"])  # read-only throughout


def test_rules_go_to_the_rules_file_and_rescore_live(app):
    app["scan"]()
    code, st = app["call"]("POST", "/api/rule", {"kind": "block", "sender": "friend@pals.example"})
    assert code == 200
    assert sender(st, "friend@pals.example")["junk"] == 1
    assert sender(st, "friend@pals.example")["rule"]["source"] == "browser"
    # binbag.toml is untouched, comments and all; the rule lives beside it.
    assert app["cfg"].read_text() == app["original"]
    assert RuleFile.load(app["cfg"].with_name("binbag.rules.toml")).block == ["friend@pals.example"]

    code, st = app["call"]("POST", "/api/rule", {"kind": "clear", "sender": "friend@pals.example"})
    assert sender(st, "friend@pals.example")["junk"] == 0


def test_expire_rule_from_the_browser(app):
    app["scan"]()
    code, st = app["call"]("POST", "/api/rule", {"kind": "expire", "sender": "store.example", "before": "2025-01-01"})
    assert code == 200
    assert sender(st, "orders@store.example")["junk"] == 1  # 900-day-old receipt, now expired


def test_bad_rule_input_is_rejected(app):
    app["scan"]()
    assert app["call"]("POST", "/api/rule", {"kind": "block", "sender": 'x"\n[evil]'})[0] == 400
    assert app["call"]("POST", "/api/rule", {"kind": "nuke", "sender": "a@b.c"})[0] == 400
    assert app["call"]("POST", "/api/rule", {"kind": "expire", "sender": "a.com"})[0] == 400


def test_move_needs_the_count_the_page_showed(app):
    st = app["scan"]()
    junk = st["totals"]["junk"]
    assert app["call"]("POST", "/api/clean", {"expected": junk + 5})[0] == 409
    assert app["server"]["moved"] == []

    assert app["call"]("POST", "/api/clean", {"expected": junk})[0] == 200
    for _ in range(100):
        code, st = app["call"]("GET", "/api/state")
        if st["status"] != "moving":
            break
        time.sleep(0.02)
    assert app["server"]["moved"] == [("INBOX", ["1"], "Trash")]
    assert st["totals"]["scanned"] == 3          # moved mail drops out of the view
    assert st["last_move"]["moved"] == 1


# -- the rules file ----------------------------------------------------------


def test_rule_file_round_trips_awkward_values(tmp_path):
    rf = RuleFile(tmp_path / "binbag.rules.toml")
    rf.set_block('odd"name@example.com')
    rf.set_allow("café.example")
    rf.set_expire("uber.com", before=date(2025, 1, 1), older_than_days=400)
    rf.save()
    again = RuleFile.load(rf.path)
    assert again.block == ['odd"name@example.com']
    assert again.allow == ["café.example"]
    assert again.rule_for("uber.com") == {"kind": "expire", "sender": "uber.com", "before": "2025-01-01", "older_than_days": 400}


def test_browser_rule_overrides_the_same_sender_in_config(tmp_path):
    cfg = tmp_path / "binbag.toml"
    cfg.write_text('[account]\nprovider = "gmail"\nusername = "a@b.c"\n[rules]\nallow = ["shop.example"]\n')
    rf = RuleFile(tmp_path / "binbag.rules.toml")
    rf.set_block("shop.example")
    rf.save()
    rules = Config.load(cfg).rules
    assert "shop.example" in rules.block and "shop.example" not in rules.allow


def test_broken_rules_file_gives_a_clear_error(tmp_path):
    from binbag.config import ConfigError

    cfg = tmp_path / "binbag.toml"
    cfg.write_text('[account]\nprovider = "gmail"\nusername = "a@b.c"\n')
    (tmp_path / "binbag.rules.toml").write_text("block = [unclosed")
    with pytest.raises(ConfigError, match="binbag.rules.toml"):
        Config.load(cfg)


def test_display_name_is_just_the_name():
    assert web._display_name('"Gym Chain" <m@gym.example>') == "Gym Chain"
    assert web._display_name("m@gym.example") == ""
