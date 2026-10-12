"""A synthetic mailbox, so you can see exactly how binbag behaves before you
point it at anything real. No network, no credentials, no risk.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import List

from .model import Message

_JUNK = [
    ("Zara", "news@e.zara.example", ["Up to 50% off: autumn edit", "New arrivals just dropped",
                                     "Last chance: sale ends tonight", "We miss you, here's 20% off"]),
    ("LinkedIn", "notifications-noreply@linkedin.example", ["You appeared in 9 searches this week",
                                                            "5 new jobs for you", "Your network is talking"]),
    ("Groupon", "deals@r.groupon.example", ["Flash sale: 70% off spa days", "Deals near you this weekend",
                                            "Your cart is waiting"]),
    ("Medium Daily", "noreply@medium.example", ["Your weekly digest", "Recommended for you",
                                                "Trending on Medium today"]),
    ("Wine Club", "hello@mail.wineclub.example", ["Exclusive offer: free delivery", "Claim your case",
                                                  "Black Friday starts now"]),
    ("Gym Chain", "marketing@sendgrid.gymchain.example", ["Join us for a free class",
                                                          "Don't miss our January offer"]),
    ("SaaS Co", "product@mktomail.saasco.example", ["Webinar: scaling your stack",
                                                    "Upgrade now and save 30%"]),
]

_KEEP = [
    ("Monzo", "no-reply@monzo.example", "Your monthly statement is ready", True),
    ("British Airways", "noreply@ba.example", "Your booking confirmation MHX82K", True),
    ("Amazon", "shipment@amazon.example", "Your order #204-8871 has been dispatched", True),
    ("HMRC", "noreply@hmrc.gov.uk", "Your tax statement", True),
    ("GitHub", "noreply@github.example", "Your verification code is 771204", True),
    ("Jane Okafor", "jane@studio.example", "Re: Thursday's session", False),
    ("Dad", "dad@family.example", "Photos from the weekend", False),
    ("Recruiter", "sam@talent.example", "Interview confirmed for Tuesday", False),
]


def corpus(seed: int = 7) -> List[Message]:
    rng = random.Random(seed)
    now = datetime.now(timezone.utc)
    messages: List[Message] = []
    uid = 0

    for name, addr, subjects in _JUNK:
        for _ in range(rng.randint(4, 14)):
            uid += 1
            subject = rng.choice(subjects)
            age = rng.randint(8, 300)
            messages.append(
                Message(
                    uid=str(uid),
                    folder="INBOX",
                    flags=["\\Seen"] if rng.random() < 0.25 else [],
                    headers={
                        "from": f"{name} <{addr}>",
                        "to": "you@example.com",
                        "subject": subject,
                        "list-unsubscribe": f"<https://{addr.split('@')[1]}/unsubscribe>",
                        "return-path": f"bounce-{uid}@{addr.split('@')[1]}",
                        "precedence": "bulk",
                    },
                    internal_date=now - timedelta(days=age),
                    gmail_labels=["\\Category Promotions"] if rng.random() < 0.7 else [],
                )
            )

    for name, addr, subject, bulkish in _KEEP:
        uid += 1
        headers = {
            "from": f"{name} <{addr}>",
            "to": "you@example.com",
            "subject": subject,
        }
        if bulkish:
            headers["list-unsubscribe"] = f"<https://{addr.split('@')[1]}/unsubscribe>"
            headers["precedence"] = "bulk"
        messages.append(
            Message(
                uid=str(uid),
                folder="INBOX",
                flags=["\\Seen"],
                headers=headers,
                internal_date=now - timedelta(days=rng.randint(10, 120)),
                gmail_labels=["\\Category Promotions"] if bulkish and rng.random() < 0.5 else [],
            )
        )

    # One thread you actually replied to, plus its siblings.
    for i in range(4):
        uid += 1
        messages.append(
            Message(
                uid=str(uid),
                folder="INBOX",
                flags=["\\Seen", "\\Answered"] if i == 0 else ["\\Seen"],
                headers={
                    "from": "Bookings <bookings@venue.example>",
                    "to": "you@example.com",
                    "subject": "Re: availability in November",
                },
                internal_date=now - timedelta(days=20 + i),
            )
        )

    rng.shuffle(messages)
    return messages
