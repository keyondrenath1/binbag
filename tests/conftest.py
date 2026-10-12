from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from binbag.model import Message


def make(
    sender: str = "news@shop.example.com",
    subject: str = "50% off everything",
    *,
    display: Optional[str] = None,
    age_days: int = 40,
    seen: bool = False,
    answered: bool = False,
    flagged: bool = False,
    unsubscribe: bool = True,
    list_id: Optional[str] = None,
    precedence: Optional[str] = None,
    return_path: Optional[str] = None,
    labels: Optional[list[str]] = None,
    folder: str = "INBOX",
    uid: str = "1",
) -> Message:
    flags = []
    if seen:
        flags.append("\\Seen")
    if answered:
        flags.append("\\Answered")
    if flagged:
        flags.append("\\Flagged")

    headers = {
        "from": f'"{display or sender.split("@")[0]}" <{sender}>',
        "subject": subject,
        "to": "me@example.com",
    }
    if unsubscribe:
        headers["list-unsubscribe"] = "<https://shop.example.com/unsub?id=1>"
    if list_id:
        headers["list-id"] = list_id
    if precedence:
        headers["precedence"] = precedence
    if return_path:
        headers["return-path"] = return_path

    return Message(
        uid=uid,
        folder=folder,
        flags=flags,
        headers=headers,
        internal_date=datetime.now(timezone.utc) - timedelta(days=age_days),
        gmail_labels=labels or [],
    )
