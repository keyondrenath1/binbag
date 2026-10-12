"""The scan -> clean pipeline end to end, with the mailbox faked."""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from binbag import cli

from conftest import make


class FakeBox:
    """Stands in for Mailbox. Each open_mailbox() call gets a fresh one, like a
    fresh connection, sharing the same `server` state."""

    def __init__(self, server):
        self.server = server
        self.uidvalidity = {}
        self._folder = None

    def sent_folder(self):
        return None

    def trash_folder(self):
        return "Trash"

    def junk_folder(self):
        return "Spam"

    def select(self, folder, readonly=True):
        self._folder = folder
        self.server["selects"].append((folder, readonly))
        self.uidvalidity[folder] = self.server["uidvalidity"][folder]
        return len(self.server["messages"])

    def search(self, *criteria):
        self.server["searches"].append(criteria)
        return [m.uid for m in self.server["messages"]]

    def fetch_headers(self, uids, progress=None):
        found = [m for m in self.server["messages"] if m.uid in uids]
        if progress:
            progress(len(found), len(found))
        return found

    def gmail_categories(self):
        return self.server.get("categories", {})

    def move(self, uids, dest):
        self.server["moved"].append((self._folder, list(uids), dest))
        return len(uids)


@pytest.fixture
def server(monkeypatch, tmp_path):
    state = {
        "messages": [
            make(uid="1", sender="deals@shop.example.com", subject="Flash sale"),
            make(uid="2", sender="noreply@hmrc.gov.uk", subject="Your tax statement", unsubscribe=False),
            make(uid="3", sender="hello@brand.example.com", subject="New arrivals and trending deals",
                 unsubscribe=False, age_days=10, seen=True),
        ],
        "uidvalidity": {"INBOX": 42},
        "selects": [],
        "searches": [],
        "moved": [],
    }

    @contextmanager
    def fake_open(_account, verbose=False):
        yield FakeBox(state)

    monkeypatch.setattr(cli, "open_mailbox", fake_open)
    cfg = tmp_path / "binbag.toml"
    cfg.write_text('[account]\nprovider = "gmail"\nusername = "me@gmail.com"\n'
                   "[rules]\nlearn_from_sent = false\n")
    state["config"] = str(cfg)
    state["report"] = str(tmp_path / "r.html")
    return state


def run(server, *args):
    return cli.main([*args, "-c", server["config"], "-q", "-r", server["report"]])


def test_scan_reads_all_of_time_by_default(server):
    assert run(server, "scan") == 0
    assert server["searches"] == [()]  # no SINCE criterion


def test_last_days_still_narrows(server):
    run(server, "scan", "--last-days", "30")
    assert server["searches"][0][0] == "SINCE"


def test_gmail_promotions_tab_now_counts(server):
    # Message 3 alone is too weak to be junk...
    verdicts = cli._run_scan(cli.build_parser().parse_args(["scan", "-c", server["config"], "-q"]))[1]
    assert not next(v for v in verdicts if v.message.uid == "3").is_junk
    # ...until Gmail says it lives in the Promotions tab.
    server["categories"] = {"3": "\\Category Promotions"}
    for m in server["messages"]:
        m.gmail_labels.clear()
    verdicts = cli._run_scan(cli.build_parser().parse_args(["scan", "-c", server["config"], "-q"]))[1]
    assert next(v for v in verdicts if v.message.uid == "3").is_junk


def test_clean_apply_moves_only_junk_and_never_erases(server):
    assert run(server, "clean", "--apply", "--yes") == 0
    assert server["moved"] == [("INBOX", ["1"], "Trash")]


def test_clean_refuses_when_uidvalidity_changes(server, monkeypatch):
    original = FakeBox.select

    def renumbering_select(self, folder, readonly=True):
        result = original(self, folder, readonly)
        if not readonly:  # the move connection sees a renumbered folder
            self.uidvalidity[folder] = 43
        return result

    monkeypatch.setattr(FakeBox, "select", renumbering_select)
    assert run(server, "clean", "--apply", "--yes") == 0
    assert server["moved"] == []


def test_clean_without_apply_is_a_dry_run(server):
    run(server, "clean")
    assert server["moved"] == []
    assert all(readonly for _, readonly in server["selects"])


def test_init_for_hotmail_writes_oauth_config(tmp_path):
    out = tmp_path / "binbag.toml"
    assert cli.main(["init", "--provider", "hotmail", "--username", "me@hotmail.co.uk", "-o", str(out)]) == 0
    text = out.read_text()
    assert 'auth = "oauth2"' in text
    assert 'provider = "outlook"' in text
    from binbag.config import Config

    cfg = Config.load(out)
    assert cfg.account.auth == "oauth2"


def test_init_for_gmail_writes_password_config(tmp_path):
    out = tmp_path / "binbag.toml"
    cli.main(["init", "--provider", "gmail", "--username", "me@gmail.com", "-o", str(out)])
    text = out.read_text()
    assert "BINBAG_PASSWORD" in text and "oauth2" not in text


def test_scan_csv_lists_every_message_kept_and_junk(server, tmp_path):
    import csv

    out = tmp_path / "all.csv"
    assert run(server, "scan", "--csv", str(out)) == 0
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert len(rows) == len(server["messages"])
    verdicts = {r["uid"]: r["verdict"] for r in rows}
    assert verdicts["1"] == "junk"
    assert verdicts["2"] == "keep"  # HMRC statement
    assert all(r["from_address"] for r in rows)


def test_confirm_prompt_names_the_real_folder(server, monkeypatch):
    asked = []
    monkeypatch.setattr("builtins.input", lambda prompt: asked.append(prompt) or "n")
    assert run(server, "clean", "--apply") == 1
    assert "'Trash'" in asked[0]
    assert server["moved"] == []
