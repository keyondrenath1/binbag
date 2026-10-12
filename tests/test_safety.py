"""Guard rails that must not regress."""

from __future__ import annotations

import inspect

import pytest

from binbag import imap_client
from binbag.config import Account, Config, ConfigError
from binbag.imap_client import ImapError, Mailbox


def test_close_is_never_sent():
    """CLOSE on a read-write folder expunges everything flagged \\Deleted.

    That breaks the never-erase promise for mail binbag didn't even touch, so
    the CLOSE command must not appear anywhere in the client.
    """
    source = inspect.getsource(imap_client)
    assert "conn.close(" not in source
    assert '"CLOSE"' not in source


def test_move_is_the_only_mutating_method():
    """If a second mutating call appears, this test should make you justify it."""
    source = inspect.getsource(imap_client)
    for verb in ("STORE", "COPY", "EXPUNGE", "MOVE"):
        outside = [
            line
            for line in source.splitlines()
            if f'"{verb}"' in line and "def move" not in line
        ]
        # every occurrence must sit inside Mailbox.move
        move_src = inspect.getsource(Mailbox.move)
        for line in outside:
            assert line.strip() in move_src, f"{verb} used outside move(): {line.strip()}"


def test_no_expunge_without_uidplus():
    src = inspect.getsource(Mailbox.move)
    assert "UIDPLUS" in src, "EXPUNGE must be gated on UIDPLUS support"


def test_bodies_are_never_fetched():
    src = inspect.getsource(imap_client)
    assert "BODY.PEEK[HEADER.FIELDS" in src
    assert "RFC822" not in src
    assert "BODY[TEXT]" not in src


def test_move_refuses_on_readonly_selection():
    box = Mailbox(Account(host="example.invalid", username="a@b.c"))
    box._readonly = True
    with pytest.raises(ImapError, match="read-only"):
        box.move(["1"], "Trash")


def test_move_of_nothing_is_a_no_op():
    box = Mailbox(Account(host="example.invalid", username="a@b.c"))
    box._readonly = True
    assert box.move([], "Trash") == 0


def test_missing_password_fails_before_any_socket(monkeypatch):
    monkeypatch.delenv("BINBAG_PASSWORD", raising=False)
    box = Mailbox(Account(host="example.invalid", username="a@b.c"))

    def explode(*_args, **_kwargs):  # pragma: no cover
        raise AssertionError("opened a socket before checking the password")

    monkeypatch.setattr(imap_client.imaplib, "IMAP4_SSL", explode)
    with pytest.raises(ConfigError, match="BINBAG_PASSWORD"):
        box.connect()


def test_config_rejects_unknown_provider():
    with pytest.raises(ConfigError, match="Unknown provider"):
        Config.from_dict({"account": {"provider": "carrierpigeon", "username": "a@b.c"}})


def test_config_rejects_bad_destination():
    with pytest.raises(ConfigError, match="destination"):
        Config.from_dict(
            {"account": {"provider": "gmail", "username": "a@b.c"}, "destination": "erase"}
        )


def test_config_requires_username():
    with pytest.raises(ConfigError, match="username"):
        Config.from_dict({"account": {"provider": "gmail"}})


def test_demo_never_bins_a_must_keep_message():
    from binbag.config import Rules
    from binbag.demo import _KEEP, corpus
    from binbag.rules import Contacts, Scorer

    verdicts = Scorer(Rules(threshold=45, learn_from_sent=False), Contacts()).judge_all(corpus())
    must_keep = {addr for _, addr, _, _ in _KEEP}
    binned = [v for v in verdicts if v.is_junk and v.message.from_address in must_keep]
    assert not binned, [(v.message.subject, v.reasons) for v in binned]
