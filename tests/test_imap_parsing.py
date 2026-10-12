"""Parsing the shapes real IMAP servers actually return."""

from __future__ import annotations

from email.parser import BytesHeaderParser

from binbag import imap_client as ic


FETCH_PREFIX = (
    b'12 (UID 4471 FLAGS (\\Seen) INTERNALDATE "03-Feb-2026 09:14:22 +0000" '
    b'X-GM-LABELS ("\\\\Important" "\\\\Category Promotions") '
    b"BODY[HEADER.FIELDS (FROM SUBJECT)] {112}"
)

FETCH_BODY = (
    b"From: Big Shop <news@shop.example.com>\r\n"
    b"Subject: =?utf-8?q?50=25_off_everything?=\r\n"
    b"List-Unsubscribe: <https://shop.example.com/u/1>\r\n"
    b"\r\n"
)


def test_parses_uid_flags_and_date():
    assert ic._UID_RE.search(FETCH_PREFIX).group(1) == b"4471"
    assert ic._parse_flags(FETCH_PREFIX) == ["\\Seen"]
    dt = ic._parse_internaldate(FETCH_PREFIX)
    assert dt is not None and dt.year == 2026 and dt.month == 2


def test_parses_gmail_labels_with_quotes_and_spaces():
    labels = ic._parse_gm_labels(FETCH_PREFIX)
    assert "\\\\Category Promotions" in labels or "\\Category Promotions" in " ".join(labels)
    assert len(labels) == 2


def test_header_payload_decodes_mime_subject():
    from binbag.model import Message

    headers = {k.lower(): v for k, v in BytesHeaderParser().parsebytes(FETCH_BODY).items()}
    msg = Message(uid="4471", folder="INBOX", headers=headers)
    assert msg.subject == "50% off everything"
    assert msg.from_address == "news@shop.example.com"
    assert msg.from_domain == "shop.example.com"
    assert msg.unsubscribe_link == "https://shop.example.com/u/1"


def test_list_response_parsing():
    lines = [
        b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Bin"',
        b'(\\HasNoChildren) "/" INBOX',
        b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"',
        b'(\\HasNoChildren) "/" "Archive/&AOk-t&AOk-"',
    ]
    parsed = []
    for line in lines:
        m = ic._LIST_RE.match(line)
        assert m, line
        raw = m.group("name").strip().strip(b'"')
        parsed.append((ic.imap_utf7.decode(raw), [f.decode() for f in m.group("flags").split()]))

    names = [p[0] for p in parsed]
    assert names[:3] == ["[Gmail]/Bin", "INBOX", "[Gmail]/Sent Mail"]
    assert names[3] == "Archive/été"
    assert "\\Trash" in parsed[0][1]


def test_quote_escapes_and_encodes():
    assert ic._quote("INBOX") == '"INBOX"'
    assert ic._quote("[Gmail]/Sent Mail") == '"[Gmail]/Sent Mail"'
    assert "&" in ic._quote("Archive/été")


def test_root_domain_handles_couk():
    from binbag.model import root_domain

    assert root_domain("mail.marks.co.uk") == "marks.co.uk"
    assert root_domain("news@shop.example.com") == "example.com"
    assert root_domain("example.com") == "example.com"
    assert root_domain("a.b.c.school.sch.uk") == "school.sch.uk"


def test_raw_8bit_headers_become_plain_text():
    """Real mailboxes contain headers with unencoded Latin-1 bytes. The parser
    returns those as Header objects; the scorer must only ever see str."""
    from binbag.config import Rules
    from binbag.model import Message
    from binbag.rules import Contacts, Scorer

    raw = (
        b"From: Caf\xe9 Nero <news@caffenero.example>\r\n"
        b"Sender: J\xe9r\xf4me <bounce@mail.sendgrid.net>\r\n"
        b"Subject: Caf\xe9 offer: 20% off\r\n"
        b"List-Unsubscribe: <https://caffenero.example/u>\r\n"
        b"\r\n"
    )
    parsed = BytesHeaderParser().parsebytes(raw)
    headers = {k.lower(): ic._header_text(v) for k, v in parsed.items()}
    assert all(isinstance(v, str) for v in headers.values())

    msg = Message(uid="9", folder="INBOX", headers=headers)
    verdict = Scorer(Rules(learn_from_sent=False), Contacts()).judge(msg)
    assert msg.from_address == "news@caffenero.example"
    assert verdict.score > 0


def test_message_header_coerces_stray_header_objects():
    from email.header import Header

    from binbag.model import Message

    msg = Message(uid="1", folder="INBOX", headers={"sender": Header("Jérôme <x@y.com>")})
    assert isinstance(msg.header("sender"), str)
