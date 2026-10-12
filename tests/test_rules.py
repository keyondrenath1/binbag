"""The safety tests. If these ever go red, binbag is dangerous, not broken."""

from __future__ import annotations

import pytest

from binbag.config import Rules
from binbag.rules import Contacts, Scorer, summarise_senders

from conftest import make


def scorer(contacts: Contacts | None = None, **overrides) -> Scorer:
    rules = Rules(threshold=60, min_age_days=3, learn_from_sent=False)
    for key, value in overrides.items():
        setattr(rules, key, value)
    return Scorer(rules, contacts or Contacts())


# -- things that must be binned -------------------------------------------


def test_obvious_marketing_blast_is_junk():
    v = scorer().judge(make())
    assert v.is_junk
    assert v.score >= 60


def test_gmail_promotions_label_counts():
    v = scorer().judge(
        make(subject="Our autumn range has landed", labels=["\\Category Promotions"])
    )
    assert v.is_junk


def test_esp_infrastructure_is_a_signal():
    v = scorer().judge(
        make(subject="Weekly roundup", return_path="bounce@mail123.sendgrid.net")
    )
    assert v.is_junk


def test_blocklist_beats_everything():
    s = scorer(block=["nuisance.example.com"])
    v = s.judge(make(sender="ceo@nuisance.example.com", subject="Your invoice", unsubscribe=False))
    assert v.is_junk
    assert v.score == 100


# -- things that must never be binned -------------------------------------


@pytest.mark.parametrize(
    "subject",
    [
        "Your order #A1029 has been dispatched",
        "Receipt for your payment",
        "Your booking confirmation for 12 October",
        "Your verification code is 448192",
        "Action required: card payment failed",
        "Your HMRC tax statement is ready",
        "Appointment reminder",
        "Your Netflix renewal",
        "Your parcel from ASOS has been delivered",
        "Your Thursday evening trip with Uber",
        "Your ride with Bolt on 3 March",
    ],
)
def test_transactional_mail_survives_even_with_bulk_headers(subject):
    """The single most important behaviour in the whole tool."""
    v = scorer().judge(make(subject=subject, labels=["\\Category Promotions"]))
    assert v.protected, f"{subject!r} would have been binned"
    assert not v.is_junk


def test_flagged_mail_is_untouchable():
    v = scorer().judge(make(flagged=True))
    assert v.protected


def test_replied_to_mail_is_untouchable():
    v = scorer().judge(make(answered=True))
    assert v.protected


def test_people_you_have_emailed_are_untouchable():
    contacts = Contacts(addresses={"news@shop.example.com"})
    s = Scorer(Rules(threshold=60, learn_from_sent=False), contacts)
    v = s.judge(make())
    assert v.protected
    assert "you have emailed this person" in v.protections


def test_recent_mail_is_left_alone():
    v = scorer(min_age_days=7).judge(make(age_days=2))
    assert v.protected
    assert "too recent" in v.protections[0]


def test_allowlisted_domain_survives():
    s = scorer(allow=["shop.example.com"])
    assert s.judge(make()).protected


def test_organisation_you_write_to_is_softened():
    contacts = Contacts(addresses={"support@shop.example.com"}, domains={"shop.example.com"})
    s = Scorer(Rules(threshold=60, learn_from_sent=False), contacts)
    plain = scorer().judge(make())
    softened = s.judge(make())
    assert softened.score < plain.score


def test_personal_mail_from_a_stranger_is_kept():
    v = scorer().judge(
        make(
            sender="jane@somewhere.example",
            subject="Coffee next week?",
            unsubscribe=False,
            age_days=200,
        )
    )
    assert not v.is_junk


# -- corpus behaviour ------------------------------------------------------


def test_replying_once_protects_the_whole_sender():
    s = scorer()
    corpus = [make(uid="1", answered=True, seen=True)] + [
        make(uid=str(i), subject="Flash sale inside") for i in range(2, 8)
    ]
    verdicts = s.judge_all(corpus)
    assert all(v.protected for v in verdicts), [v.reasons for v in verdicts if v.is_junk]


def test_high_volume_sender_scores_higher():
    s = scorer()
    corpus = [make(uid=str(i), subject="Newsletter") for i in range(15)]
    verdicts = s.judge_all(corpus)
    assert any("15 messages from this sender" in r for v in verdicts for r in v.reasons)


def test_sender_summary_groups_and_ranks():
    s = scorer()
    corpus = [make(uid=str(i)) for i in range(5)] + [
        make(uid="x", sender="one@other.example", subject="Sale ends today")
    ]
    summaries = summarise_senders(s.judge_all(corpus))
    assert summaries[0].address == "news@shop.example.com"
    assert summaries[0].junk == 5
    assert summaries[0].unsubscribe.startswith("https://")


def test_score_is_bounded():
    v = scorer().judge(
        make(
            subject="sale deal discount voucher promo last chance hurry",
            labels=["\\Spam", "\\Category Promotions"],
            precedence="bulk",
            list_id="<promo.shop.example.com>",
            return_path="x@mailchimp.com",
        )
    )
    assert 0 <= v.score <= 100


# -- spoofed and disguised mail ---------------------------------------------


def test_unreadable_sender_address_is_binned_even_with_receipt_subject():
    s = scorer()
    v = s.judge(make(sender="\ufffd\ufffd\ufffd-\ufffd\ufffd@\ufffd\ufffd.co.uk",
                     subject="Save up to 75% on your energy bills!", unsubscribe=False))
    assert v.is_junk
    assert "spoofed" in v.reasons[0]


def test_garbled_display_name_with_real_address_is_not_spoofed():
    msg = make(sender="orders@realshop.example", subject="Your order #551 has been dispatched", unsubscribe=False)
    msg.headers["from"] = '"Caf\ufffd Shop" <orders@realshop.example>'
    assert not scorer().judge(msg).is_junk


def test_flagged_spoof_still_kept():
    msg = make(sender="\ufffd\ufffd@\ufffd.com", subject="Final notice", flagged=True)
    assert not scorer().judge(msg).is_junk


@pytest.mark.parametrize(
    "subject",
    [
        "£193m already ᑭaid out 𝖙o ｄiesel owners",
        "Re: ᴄ𝟶ɴɢʀᴀᴛs - ᴛʜᴏᴜsᴀɴᴅs ɴ𝟶ᴛɪғɪᴄᴀᴛɪᴏɴ",
        "Are you eIigible for fuIIy funded soIar paneIs",
    ],
)
def test_disguised_subjects_are_junk(subject):
    v = scorer().judge(make(sender="newsletters.x1@random-domain.example", subject=subject, unsubscribe=False))
    assert v.is_junk, (v.score, v.reasons)


@pytest.mark.parametrize("subject", ["Introducing iPhone", "Your IKEA order", "MIT Technology Review", "Café opening"])
def test_normal_capitals_and_accents_are_not_disguise(subject):
    from binbag.rules import _looks_disguised
    assert _looks_disguised(subject) is None
