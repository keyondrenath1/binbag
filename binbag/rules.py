"""The scoring engine.

Every message gets a score out of roughly 100 and a list of reasons. Anything
at or above the threshold is junk. Some signals don't score at all: they
*protect*, and a protected message is never binned no matter how spammy it
looks. Receipts that arrive from a marketing platform are the whole reason
that distinction exists.

The bias is deliberate and asymmetric. Keeping a newsletter you didn't want
costs you two seconds. Binning a booking confirmation costs you a flight.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set

from .config import Rules
from .model import Message, Verdict, addresses_in, root_domain

FREEMAIL = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "hotmail.co.uk",
    "yahoo.com", "yahoo.co.uk", "icloud.com", "me.com", "mac.com", "aol.com",
    "protonmail.com", "proton.me", "live.com", "live.co.uk", "msn.com", "gmx.com",
    "btinternet.com", "sky.com", "virginmedia.com", "talktalk.net",
}

# Bulk-sending infrastructure. Seeing these in the envelope means the mail was
# blasted to a list, which is a strong but not conclusive junk signal.
ESP_MARKERS = (
    "sendgrid", "mailchimp", "mcsv.net", "mailchimpapp", "mandrillapp", "mailgun",
    "sparkpostmail", "amazonses", "klaviyomail", "klaviyo", "braze", "sendinblue",
    "brevo", "hubspot", "marketo", "mktomail", "salesforce", "exacttarget",
    "eloqua", "responsys", "cheetahmail", "epsl1", "icptrack", "constantcontact",
    "rsgsv.net", "list-manage.com", "customeriomail", "postmarkapp", "intercom",
    "createsend", "cmail", "campaign-archive", "substack", "beehiiv", "convertkit",
    "ck.page", "activehosted", "drip", "iterable", "cordial", "attentivemobile",
)

MARKETING_PHRASES = (
    "% off", "off your", "sale", "save now", "shop now", "buy now", "deal",
    "deals", "discount", "voucher", "promo", "offer ends", "limited time",
    "last chance", "don't miss", "dont miss", "hurry", "flash sale", "black friday",
    "cyber monday", "clearance", "free shipping", "free delivery", "exclusive offer",
    "webinar", "join us", "newsletter", "this week's", "weekly digest", "roundup",
    "new arrivals", "back in stock", "trending", "recommended for you",
    "you might like", "upgrade now", "act now", "unlock", "giveaway", "sweepstake",
    "win a", "claim your", "just for you", "we miss you", "come back",
    "your cart", "still thinking", "abandoned", "survey", "rate your",
)

# If a subject looks like any of these, binbag will not touch the message.
# Over-inclusive on purpose.
TRANSACTIONAL_PHRASES = (
    "receipt", "invoice", "your order", "order #", "order confirm", "order number",
    "dispatched", "despatched", "shipped", "out for delivery", "delivery",
    "delivered", "parcel", "your trip", "trip with", "your ride", "ride with",
    "tracking", "refund", "statement", "payment", "paid", "direct debit",
    "standing order", "bill", "booking", "reservation", "itinerary", "boarding",
    "check-in", "e-ticket", "your ticket", "confirmation", "confirmed",
    "confirm your", "verify", "verification", "security code", "one-time",
    "one time passcode", "otp", "password", "reset your", "sign-in", "sign in",
    "login", "log in", "two-factor", "2fa", "authenticator", "appointment",
    "prescription", "renewal", "renew your", "expiring soon", "contract",
    "tenancy", "payslip", "tax", "hmrc", "dvla", "nhs", "court", "summons",
    "insurance", "policy number", "claim number", "account closed",
    "suspicious activity", "unusual sign", "was accessed", "terms of service",
    "privacy policy", "has been updated", "important notice", "action required",
    "final notice", "overdue", "failed payment", "card declined", "expiring card",
    "interview", "offer letter", "your application", "visa", "passport",
)

# Letters from other scripts and Unicode's maths/small-caps blocks that look
# like Latin ones. Legitimate senders type "paid"; spammers type "ᑭaid" or
# "𝖕aid" to slip past filters.
_HOMOGLYPH_RANGES = (
    (0x1D400, 0x1D7FF),  # mathematical alphanumerics: 𝐀 𝖆 𝟶
    (0xFF21, 0xFF5A),    # fullwidth Latin: Ａ ｄ
    (0x1D00, 0x1D2B),    # small capitals: ᴄ ɴ ʀ
    (0x1400, 0x167F),    # Canadian syllabics, used for ᑭ ᑕ ᗷ
    (0x13A0, 0x13FF),    # Cherokee, used for Ꭺ Ᏼ
)
# Capital I or digit 1 where a lowercase l belongs: "eIigible", "fuIIy".
_FAKE_L_RE = re.compile(r"[a-z]I[a-z]|[a-z]II[a-z]")


def _looks_disguised(subject: str) -> Optional[str]:
    odd = sum(1 for ch in subject if any(lo <= ord(ch) <= hi for lo, hi in _HOMOGLYPH_RANGES))
    if odd >= 2:
        return "subject uses look-alike characters to dodge filters"
    if _FAKE_L_RE.search(subject):
        return "subject swaps a capital I in for a lowercase l"
    return None


# Bulk spammers send from throwaway personal accounts and stamp each subject
# with a random tag so no two are identical: "...Fat Removal bYc", "...hf9",
# "Membership has Expired#af50m". A tag is a short token that mixes letters
# and digits, mixes case oddly, or is a stray block of capitals after a
# normal word.
_TAG_RE = re.compile(r"(?:^|[\s#_!])([A-Za-z0-9]{1,6})[\"'.]?\s*$")
_SALES_RE = re.compile(
    r"[$£€]\s?\d|\d+\s?%|\b(free|save|off|win|winner|won|reward|claim|selected|"
    r"expired|offer|discount|deal|prize|gift|bonus|limited|plan|coverage|protection|"
    r"warranty|quote|surprise|confirm\s?receipt)\b",
    re.I,
)
# Short tokens that legitimately end a subject and must never count as tags.
_NOT_TAGS = {
    "UK", "US", "USA", "EU", "TV", "PC", "AI", "ID", "PDF", "FAQ", "VAT", "NHS", "HMRC",
    "DVLA", "UPS", "DHL", "DPD", "BBC", "OK", "AM", "PM", "UAE", "IT", "HR", "CV", "PS",
}
_UNIT_RE = re.compile(r"^\d+(p|k|m|bn|gb|mb|tb|kg|km|mph|x|st|nd|rd|th|am|pm|s)$", re.I)


def _random_tag(subject: str) -> Optional[str]:
    match = _TAG_RE.search(subject.strip())
    if not match:
        return None
    tag = match.group(1)
    if tag.upper() in _NOT_TAGS or _UNIT_RE.match(tag):
        return None
    before = subject.strip()[: match.start(1)].rstrip(" #_!").split()
    prev = before[-1] if before else ""
    has_digit = any(c.isdigit() for c in tag)
    has_alpha = any(c.isalpha() for c in tag)
    if has_digit and has_alpha:
        return tag                       # f3b, hf9, 1z5, X6, #c7, axG537
    if has_alpha and len(tag) <= 3 and not (tag.isupper() or tag.islower() or tag.istitle()):
        return tag                       # eWw, bYc, YIl, nA, uK
    if tag.isupper() and 2 <= len(tag) <= 4 and prev and not prev.isupper():
        return tag                       # "...Ever Need PRT", "...Only $2! FPCP"
    return None


_NOREPLY_RE = re.compile(
    r"\b(no[-_.]?reply|do[-_.]?not[-_.]?reply|donotreply|notifications?|"
    r"mailer|bounce|newsletter|news|marketing|promo|offers?|deals?|info|hello|"
    r"team|updates?|digest)\b"
)


@dataclass
class Contacts:
    """People you have actually written to. The strongest protection there is."""

    addresses: Set[str] = field(default_factory=set)
    domains: Set[str] = field(default_factory=set)

    @classmethod
    def from_sent(cls, sent_messages: Iterable[Message]) -> "Contacts":
        addresses: Set[str] = set()
        domains: Set[str] = set()
        for msg in sent_messages:
            for header in ("to", "cc"):
                for addr in addresses_in(msg.header(header)):
                    addresses.add(addr)
                    dom = addr.rsplit("@", 1)[-1]
                    if dom and dom not in FREEMAIL:
                        domains.add(dom)
                        domains.add(root_domain(dom))
        return cls(addresses=addresses, domains=domains)


@dataclass
class Signal:
    points: int
    reason: str


class Scorer:
    def __init__(self, rules: Rules, contacts: Optional[Contacts] = None):
        self.rules = rules
        self.contacts = contacts or Contacts()
        self._allow_exact = {a for a in rules.allow if "@" in a}
        self._allow_domains = {a.lstrip("@") for a in rules.allow if "@" not in a}
        self._block_exact = {b for b in rules.block if "@" in b}
        self._block_domains = {b.lstrip("@") for b in rules.block if "@" not in b}
        # Populated by `prepare` so per-sender volume can inform the score.
        self._sender_counts: Counter = Counter()
        self._sender_answered: Set[str] = set()

    # -- corpus-level pass -------------------------------------------------

    def prepare(self, messages: Sequence[Message]) -> None:
        """Learn per-sender volume before scoring anything individually."""
        self._sender_counts = Counter(m.from_address for m in messages if m.from_address)
        self._sender_answered = {
            m.from_address for m in messages if m.is_answered and m.from_address
        }

    # -- per-message -------------------------------------------------------

    def judge(self, msg: Message) -> Verdict:
        sender = msg.from_address
        domain = msg.from_domain
        root = root_domain(domain) if domain else ""
        subject = msg.subject.lower()
        blob = " ".join(
            filter(
                None,
                [
                    msg.header("return-path"),
                    msg.header("sender"),
                    msg.header("list-id"),
                    msg.header("x-feedback-id"),
                    msg.header("x-mailer"),
                    msg.header("message-id"),
                    domain,
                ],
            )
        ).lower()

        # 1. Blocklist wins outright.
        if sender in self._block_exact or domain in self._block_domains or root in self._block_domains:
            return Verdict(msg, 100, ["on your blocklist"], [], protected=False)

        # 2. Hard protections. Your own deliberate actions on a message beat
        #    every rule, including expiry.
        protections: List[str] = []
        if msg.is_flagged:
            protections.append("flagged or starred")
        if msg.is_answered:
            protections.append("you replied to it")
        if protections:
            return Verdict(msg, 0, [], protections, protected=True)

        # 3. Spoofed senders. A sender *address* (not just the display name)
        #    full of undecodable bytes is never a real company: mail systems
        #    can't even route a reply to it. Receipt-looking subjects from
        #    such a sender are bait, so this beats the soft protections.
        if sender and "\ufffd" in sender:
            return Verdict(msg, 100, ["sender address is unreadable, so it's spoofed or malformed"], [], protected=False)

        # 3b. Throwaway-account spam. A stranger on a personal address
        #     (Gmail, Hotmail ...) whose subject carries a random anti-filter
        #     tag *and* sales wording. Each alone is too weak: a sole trader
        #     on Gmail may well send "Invoice INV23", and plenty of strangers
        #     say "free". Together they're the pattern. People you've written
        #     to or replied to, and your allowlist, are exempt.
        spam = self._throwaway_spam(msg, sender, domain, root)
        if spam:
            return Verdict(msg, 100, [spam], [], protected=False)

        # 4. Expiry. You've said exactly which sender and how old, so this
        #    beats the softer protections below, transactional ones included:
        #    that's the point of it. Undated mail is never expired.
        expired = self._expiry(msg)
        if expired:
            return Verdict(msg, 100, [expired], [], protected=False)

        # 5. Soft protections. Any one of these and the message is untouchable.
        if sender and sender in self.contacts.addresses:
            protections.append("you have emailed this person")
        if sender and sender in self._sender_answered:
            protections.append("you have replied to this sender before")
        if sender in self._allow_exact or domain in self._allow_domains or root in self._allow_domains:
            protections.append("on your allowlist")
        age = msg.age_days
        if age is not None and age < self.rules.min_age_days:
            protections.append(f"too recent (under {self.rules.min_age_days} days old)")
        hit = _first_match(subject, TRANSACTIONAL_PHRASES)
        if hit:
            protections.append(f"looks transactional ({hit!r} in subject)")
        keep_hit = _first_match(subject, tuple(self.rules.keep_subjects))
        if keep_hit:
            protections.append(f"matches your keep list ({keep_hit!r})")

        if protections:
            return Verdict(msg, 0, [], protections, protected=True)

        # 6. Scoring.
        signals: List[Signal] = []

        if msg.header("list-unsubscribe"):
            signals.append(Signal(40, "has an unsubscribe link, so it's bulk mail"))
        if msg.header("list-id"):
            signals.append(Signal(20, "sent to a mailing list"))
        precedence = msg.header("precedence").lower()
        if precedence in {"bulk", "list", "junk"}:
            signals.append(Signal(15, f"Precedence: {precedence}"))
        if msg.header("auto-submitted").lower() not in ("", "no"):
            signals.append(Signal(8, "machine-generated"))

        labels = {lbl.lower() for lbl in msg.gmail_labels}
        if "\\spam" in labels:
            signals.append(Signal(100, "Gmail already called it spam"))
        if "\\category promotions" in labels or "\\promotions" in labels:
            signals.append(Signal(35, "Gmail filed it under Promotions"))
        if "\\category social" in labels or "\\social" in labels:
            signals.append(Signal(25, "Gmail filed it under Social"))
        if "\\category forums" in labels:
            signals.append(Signal(20, "Gmail filed it under Forums"))

        esp = _first_match(blob, ESP_MARKERS)
        if esp:
            signals.append(Signal(20, f"sent through a bulk mail platform ({esp})"))

        local = sender.split("@", 1)[0] if "@" in sender else ""
        if local and _NOREPLY_RE.search(local):
            signals.append(Signal(12, f"from a no-reply style address ({local}@)"))

        phrases = [p for p in MARKETING_PHRASES if p in subject][:3]
        if phrases:
            signals.append(
                Signal(min(8 * len(phrases), 24), "marketing language: " + ", ".join(repr(p) for p in phrases))
            )
        disguised = _looks_disguised(msg.subject)
        if disguised:
            signals.append(Signal(50, disguised))
        junk_hit = _first_match(subject, tuple(self.rules.junk_subjects))
        if junk_hit:
            signals.append(Signal(40, f"matches your junk list ({junk_hit!r})"))

        if msg.is_unread and age is not None:
            if age >= 90:
                signals.append(Signal(25, f"never opened, {age} days old"))
            elif age >= 30:
                signals.append(Signal(15, f"never opened, {age} days old"))

        volume = self._sender_counts.get(sender, 0)
        if volume >= 10:
            signals.append(Signal(12, f"{volume} messages from this sender, none replied to"))
        elif volume >= 5:
            signals.append(Signal(6, f"{volume} messages from this sender, none replied to"))

        # Soft protection: you've emailed this organisation, just not this address.
        if root and (domain in self.contacts.domains or root in self.contacts.domains):
            signals.append(Signal(-25, "you've corresponded with this organisation"))

        score = max(0, min(100, sum(s.points for s in signals)))
        reasons = [s.reason for s in signals if s.points > 0]
        soft = [s.reason for s in signals if s.points < 0]

        if score < self.rules.threshold:
            return Verdict(msg, score, reasons, soft + ["below the junk threshold"], protected=True)
        return Verdict(msg, score, reasons, soft, protected=False)

    def _throwaway_spam(self, msg: Message, sender: str, domain: str, root: str) -> Optional[str]:
        if not sender or domain not in FREEMAIL:
            return None
        if sender in self.contacts.addresses or sender in self._sender_answered:
            return None
        if sender in self._allow_exact or domain in self._allow_domains or root in self._allow_domains:
            return None
        subject = msg.subject
        tag = _random_tag(subject)
        if not tag or not _SALES_RE.search(subject):
            return None
        return f"stranger on a personal address, sales pitch stamped with a random tag ({tag!r})"

    def _expiry(self, msg: Message) -> Optional[str]:
        if not self.rules.expire or not msg.from_address:
            return None
        sent = msg.date
        if sent is None:
            return None
        for rule in self.rules.expire:
            if rule.matches_sender(msg.from_address) and sent < rule.cutoff():
                return f"expire rule: {rule.describe()} (sent {sent.date().isoformat()})"
        return None

    def judge_all(self, messages: Sequence[Message]) -> List[Verdict]:
        self.prepare(messages)
        return [self.judge(m) for m in messages]


def _first_match(haystack: str, needles: Sequence[str]) -> Optional[str]:
    for needle in needles:
        if needle and needle in haystack:
            return needle
    return None


@dataclass
class SenderSummary:
    address: str
    display: str
    total: int = 0
    junk: int = 0
    unread: int = 0
    unsubscribe: str = ""

    @property
    def junk_share(self) -> float:
        return self.junk / self.total if self.total else 0.0


def summarise_senders(verdicts: Sequence[Verdict]) -> List[SenderSummary]:
    """Group verdicts by sender, newsletter-first, for the unsubscribe view."""
    buckets: Dict[str, SenderSummary] = {}
    for v in verdicts:
        addr = v.message.from_address or "(unknown)"
        s = buckets.get(addr)
        if s is None:
            s = buckets[addr] = SenderSummary(address=addr, display=v.message.from_display or addr)
        s.total += 1
        if v.is_junk:
            s.junk += 1
        if v.message.is_unread:
            s.unread += 1
        if not s.unsubscribe:
            s.unsubscribe = v.message.unsubscribe_link
    return sorted(buckets.values(), key=lambda s: (-s.junk, -s.total, s.address))
