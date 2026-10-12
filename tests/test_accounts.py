"""Several mailboxes in one config."""

from __future__ import annotations

import http.client
import json
import threading
import time
from contextlib import contextmanager

import pytest

from binbag import cli, web
from binbag.config import Config, ConfigError

from conftest import make
from test_cli import FakeBox

MULTI = """
[[accounts]]
name = "personal"
provider = "hotmail"
username = "me@hotmail.co.uk"
oauth_client_id = "abc"

[[accounts]]
name = "work"
provider = "gmail"
username = "me@company.example"
folders = ["INBOX", "Newsletters"]

[[accounts]]
provider = "icloud"
username = "me@icloud.com"
destination = "junk"

[rules]
learn_from_sent = false
block = ["spam.example"]
folders = ["INBOX"]
"""


def write(tmp_path, text):
    p = tmp_path / "binbag.toml"
    p.write_text(text)
    return p


def test_first_account_is_the_default(tmp_path):
    cfg = Config.load(write(tmp_path, MULTI))
    assert cfg.account.name == "personal"
    assert cfg.account.auth == "oauth2"
    assert [a.name for a in cfg.accounts] == ["personal", "work", "me@icloud.com"]


def test_pick_by_name_or_address(tmp_path):
    path = write(tmp_path, MULTI)
    assert Config.load(path, "work").account.username == "me@company.example"
    assert Config.load(path, "ME@ICLOUD.COM").account.host == "imap.mail.me.com"


def test_unknown_account_lists_the_real_ones(tmp_path):
    with pytest.raises(ConfigError, match="personal, work"):
        Config.load(write(tmp_path, MULTI), "holiday")


def test_per_account_folders_and_destination_override(tmp_path):
    path = write(tmp_path, MULTI)
    assert Config.load(path, "work").rules.folders == ["INBOX", "Newsletters"]
    assert Config.load(path, "personal").rules.folders == ["INBOX"]
    assert Config.load(path, "me@icloud.com").destination == "junk"
    assert Config.load(path, "work").destination == "trash"


def test_rules_are_shared_across_accounts(tmp_path):
    path = write(tmp_path, MULTI)
    assert Config.load(path, "personal").rules.block == Config.load(path, "work").rules.block == ["spam.example"]


def test_each_account_gets_its_own_password_variable(tmp_path):
    path = write(tmp_path, MULTI)
    assert Config.load(path, "work").account.password_env == "BINBAG_PASSWORD_WORK"
    assert Config.load(path, "me@icloud.com").account.password_env == "BINBAG_PASSWORD_ME_ICLOUD_COM"


def test_single_account_configs_are_unchanged(tmp_path):
    cfg = Config.load(write(tmp_path, '[account]\nprovider = "gmail"\nusername = "a@gmail.com"\n'))
    assert cfg.account.password_env == "BINBAG_PASSWORD"
    assert cfg.account.name == "a@gmail.com"
    assert len(cfg.accounts) == 1


@pytest.mark.parametrize("bad, msg", [
    ('[account]\nprovider="gmail"\nusername="a@b.c"\n[[accounts]]\nprovider="gmail"\nusername="d@e.f"\n', "not both"),
    ('[[accounts]]\nname="x"\nprovider="gmail"\nusername="a@b.c"\n[[accounts]]\nname="X"\nprovider="gmail"\nusername="d@e.f"\n', "unique"),
])
def test_bad_multi_configs(tmp_path, bad, msg):
    with pytest.raises(ConfigError, match=msg):
        Config.load(write(tmp_path, bad))


# -- the CLI and the browser ---------------------------------------------------


@pytest.fixture
def two_mailboxes(monkeypatch, tmp_path):
    boxes = {
        "me@hotmail.co.uk": {"messages": [make(uid="1", sender="deals@shop.example.com", subject="Flash sale")]},
        "me@company.example": {"messages": [make(uid="9", sender="news@vendor.example", subject="Weekly roundup deals"),
                                            make(uid="10", sender="boss@company.example", subject="Re: Q3 plan",
                                                 unsubscribe=False, seen=True, answered=True)]},
    }
    for b in boxes.values():
        b.update({"uidvalidity": {"INBOX": 1, "Newsletters": 2}, "selects": [], "searches": [], "moved": []})

    @contextmanager
    def fake_open(account, verbose=False):
        yield FakeBox(boxes[account.username])

    monkeypatch.setattr(cli, "open_mailbox", fake_open)
    path = write(tmp_path, MULTI.replace('folders = ["INBOX", "Newsletters"]', ""))
    return path, boxes


def test_cli_clean_only_touches_the_chosen_account(two_mailboxes):
    path, boxes = two_mailboxes
    assert cli.main(["clean", "--apply", "--yes", "-a", "work", "-c", str(path), "-q", "-r", "-"]) == 0
    assert boxes["me@company.example"]["moved"] == [("INBOX", ["9"], "Trash")]
    assert boxes["me@hotmail.co.uk"]["moved"] == []


def test_browser_switches_between_accounts(two_mailboxes):
    path, boxes = two_mailboxes
    state = web.State(path)
    server, token = web.make_server(state)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def call(method, url, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        headers = {"Host": f"127.0.0.1:{port}", "X-Binbag-Token": token, "Content-Type": "application/json"}
        conn.request(method, url, body=json.dumps(body) if body is not None else None, headers=headers)
        r = conn.getresponse()
        data = json.loads(r.read())
        conn.close()
        return r.status, data

    def wait(account):
        for _ in range(100):
            _, st = call("GET", f"/api/state?account={account}")
            if st["status"] not in ("scanning", "moving"):
                return st
            time.sleep(0.02)

    try:
        _, st = call("GET", "/api/state")
        assert st["account"] == "personal" and [a["name"] for a in st["accounts"]] == ["personal", "work", "me@icloud.com"]

        assert call("POST", "/api/scan", {"account": "work"})[0] == 200
        st = wait("work")
        assert st["username"] == "me@company.example" and st["totals"]["scanned"] == 2
        assert wait("personal")["totals"]["scanned"] == 0          # untouched until scanned

        call("POST", "/api/scan", {"account": "personal"})
        wait("personal")
        # A rule set while looking at one account re-scores every scanned account.
        code, st = call("POST", "/api/rule", {"account": "personal", "kind": "block", "sender": "vendor.example"})
        assert code == 200
        work = wait("work")
        assert next(s for s in work["senders"] if s["address"] == "news@vendor.example")["rule"]["kind"] == "block"

        assert call("POST", "/api/clean", {"account": "work", "expected": work["totals"]["junk"]})[0] == 200
        wait("work")
        assert boxes["me@company.example"]["moved"] == [("INBOX", ["9"], "Trash")]
        assert boxes["me@hotmail.co.uk"]["moved"] == []
        assert call("POST", "/api/scan", {"account": "nope"})[0] == 400
    finally:
        server.shutdown()
