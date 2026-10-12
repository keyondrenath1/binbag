"""Expire rules: keep a sender's recent mail, bin their back catalogue."""

from __future__ import annotations

from datetime import date

import pytest

from binbag.config import Config, ConfigError, ExpireRule, Rules
from binbag.rules import Contacts, Scorer

from conftest import make


def scorer(*rules: ExpireRule, contacts: Contacts | None = None, **overrides) -> Scorer:
    r = Rules(threshold=60, min_age_days=3, learn_from_sent=False, expire=list(rules))
    for key, value in overrides.items():
        setattr(r, key, value)
    return Scorer(r, contacts or Contacts())


OLD_AMAZON = dict(sender="auto-confirm@amazon.co.uk", subject="Your Amazon.co.uk order #204-1", unsubscribe=False)


def test_old_receipt_from_expired_sender_is_binned_despite_transactional_subject():
    s = scorer(ExpireRule("amazon.co.uk", older_than_days=365))
    v = s.judge(make(**OLD_AMAZON, age_days=900))
    assert v.is_junk
    assert "expire rule" in v.reasons[0]


def test_recent_receipt_from_expired_sender_is_kept():
    s = scorer(ExpireRule("amazon.co.uk", older_than_days=365))
    v = s.judge(make(**OLD_AMAZON, age_days=20))
    assert not v.is_junk
    assert any("transactional" in p for p in v.protections)


def test_before_date_cutoff():
    s = scorer(ExpireRule("uber.com", before=date(2025, 1, 1)))
    assert s.judge(make(sender="uber@uber.com", subject="Your Tuesday trip", age_days=4000)).is_junk
    assert not s.judge(make(sender="uber@uber.com", subject="Your Tuesday trip", age_days=5)).is_junk


def test_domain_rule_covers_subdomains_but_not_lookalikes():
    rule = ExpireRule("amazon.co.uk", older_than_days=30)
    assert rule.matches_sender("shipment-tracking@amazon.co.uk")
    assert rule.matches_sender("x@marketplace.amazon.co.uk")
    assert not rule.matches_sender("x@notamazon.co.uk")
    assert not rule.matches_sender("x@amazon.co.uk.evil.example")


def test_address_rule_is_exact():
    rule = ExpireRule("uberone@uber.com", older_than_days=30)
    assert rule.matches_sender("uberone@uber.com")
    assert not rule.matches_sender("uber@uber.com")


@pytest.mark.parametrize("kwargs", [dict(flagged=True), dict(answered=True)])
def test_flagged_and_replied_still_win(kwargs):
    s = scorer(ExpireRule("amazon.co.uk", older_than_days=30))
    v = s.judge(make(**OLD_AMAZON, age_days=900, **kwargs))
    assert not v.is_junk


def test_expiry_beats_allowlist_and_contacts():
    contacts = Contacts(addresses={"auto-confirm@amazon.co.uk"})
    s = scorer(ExpireRule("amazon.co.uk", older_than_days=30), contacts=contacts, allow=["amazon.co.uk"])
    assert s.judge(make(**OLD_AMAZON, age_days=900)).is_junk


def test_other_senders_unaffected():
    s = scorer(ExpireRule("amazon.co.uk", older_than_days=30))
    v = s.judge(make(sender="noreply@hmrc.gov.uk", subject="Your tax statement", age_days=900, unsubscribe=False))
    assert not v.is_junk


def test_undated_mail_is_never_expired():
    s = scorer(ExpireRule("amazon.co.uk", older_than_days=30))
    msg = make(**OLD_AMAZON, age_days=900)
    msg.internal_date = None
    assert not s.judge(msg).is_junk


def test_both_limits_use_the_earlier_cutoff():
    rule = ExpireRule("x.com", before=date(2020, 1, 1), older_than_days=30)
    assert rule.cutoff().year == 2020


# -- config ----------------------------------------------------------------


def _cfg(expire):
    return Config.from_dict({"account": {"provider": "gmail", "username": "a@b.c"}, "rules": {"expire": expire}})


def test_config_parses_expire_rules_from_toml_types():
    import tomllib

    raw = tomllib.loads(
        """
[account]
provider = "gmail"
username = "a@b.c"

[[rules.expire]]
sender = "Amazon.co.uk"
before = 2024-01-01

[[rules.expire]]
sender = "uber.com"
older_than_days = 365
"""
    )
    rules = Config.from_dict(raw).rules.expire
    assert rules[0] == ExpireRule("amazon.co.uk", before=date(2024, 1, 1))
    assert rules[1] == ExpireRule("uber.com", older_than_days=365)


def test_config_accepts_string_dates():
    assert _cfg([{"sender": "a.com", "before": "2023-06-30"}]).rules.expire[0].before == date(2023, 6, 30)


@pytest.mark.parametrize(
    "bad, msg",
    [
        ({"before": "2024-01-01"}, "sender"),
        ({"sender": "a.com"}, "before or older_than_days"),
        ({"sender": "a.com", "before": "last year"}, "date"),
        ({"sender": "a.com", "older_than_days": 0}, "at least 1"),
    ],
)
def test_config_rejects_bad_expire_rules(bad, msg):
    with pytest.raises(ConfigError, match=msg):
        _cfg([bad])
