from binbag import imap_utf7


def test_ascii_roundtrip():
    assert imap_utf7.decode(b"INBOX") == "INBOX"
    assert imap_utf7.encode("INBOX") == b"INBOX"


def test_literal_ampersand():
    assert imap_utf7.decode(b"Bits &- Bobs") == "Bits & Bobs"
    assert imap_utf7.encode("Bits & Bobs") == b"Bits &- Bobs"


def test_non_ascii_roundtrip():
    for name in ["Été", "受信箱", "Gelöscht", "Корзина"]:
        assert imap_utf7.decode(imap_utf7.encode(name)) == name


def test_known_wire_values():
    # RFC 3501 worked examples.
    assert imap_utf7.decode(b"~peter/mail/&U,BTFw-/&ZeVnLIqe-") == "~peter/mail/台北/日本語"


def test_decode_never_raises_on_rubbish():
    assert isinstance(imap_utf7.decode(b"&&&broken"), str)
