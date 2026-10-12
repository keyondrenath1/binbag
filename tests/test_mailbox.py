"""Mailbox behaviour against a scripted server: what actually goes over the wire."""

from __future__ import annotations

import pytest

from binbag.config import Account
from binbag.imap_client import Mailbox

from fakes import FakeConn


def box_with(conn: FakeConn) -> Mailbox:
    box = Mailbox(Account(host="imap.example.com", username="me@example.com"))
    box._conn = conn
    return box


@pytest.mark.parametrize("caps", [("IMAP4REV1", "MOVE", "UNSELECT"), ("IMAP4REV1", "MOVE")])
def test_closing_after_a_move_never_expunges(caps):
    conn = FakeConn(capabilities=caps)
    box = box_with(conn)
    box.select("INBOX", readonly=False)
    box.move(["5", "6"], "Trash")
    box.close()
    sent = conn.commands()
    assert "CLOSE" not in sent
    assert "UID EXPUNGE" not in sent
    assert sent[-1] == "LOGOUT"


def test_unselect_used_when_the_server_offers_it():
    conn = FakeConn(capabilities=("IMAP4REV1", "UNSELECT"))
    box = box_with(conn)
    box.select("INBOX", readonly=False)
    box.close()
    assert conn.commands()[-2:] == ["UNSELECT", "LOGOUT"]


def test_select_records_uidvalidity():
    conn = FakeConn(uidvalidity={"INBOX": 1700000001})
    box = box_with(conn)
    box.select("INBOX")
    assert box.uidvalidity["INBOX"] == 1700000001


def test_select_tolerates_missing_uidvalidity():
    box = box_with(FakeConn())
    box.select("INBOX")
    assert box.uidvalidity["INBOX"] is None


def test_gmail_categories_found_with_x_gm_raw():
    conn = FakeConn(
        capabilities=("IMAP4REV1", "X-GM-EXT-1"),
        search_results={
            'X-GM-RAW "category:promotions"': ["10", "11"],
            'X-GM-RAW "category:social"': ["12"],
        },
    )
    box = box_with(conn)
    box.select("INBOX")
    cats = box.gmail_categories()
    assert cats == {
        "10": "\\Category Promotions",
        "11": "\\Category Promotions",
        "12": "\\Category Social",
    }


def test_gmail_categories_empty_on_other_servers():
    conn = FakeConn(capabilities=("IMAP4REV1",))
    box = box_with(conn)
    assert box.gmail_categories() == {}
    assert not any(c[0] == "UID" for c in conn.calls)
