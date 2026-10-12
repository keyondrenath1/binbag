"""Rules you set from the browser, kept apart from your hand-written config.

`binbag serve` never rewrites binbag.toml: you may have comments and layout in
there you care about. Its changes go to binbag.rules.toml beside it instead,
which Config.load merges in. Plain TOML, safe to edit or delete by hand.
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

HEADER = (
    "# Written by `binbag serve`. Merged into binbag.toml's [rules] when binbag runs.\n"
    "# Safe to edit by hand, or delete to drop every rule set from the browser.\n\n"
)


@dataclass
class RuleFile:
    path: Path
    block: List[str] = field(default_factory=list)
    allow: List[str] = field(default_factory=list)
    expire: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "RuleFile":
        rf = cls(path=path)
        if not path.exists():
            return rf
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
        rf.block = [str(b).lower().strip() for b in raw.get("block", [])]
        rf.allow = [str(a).lower().strip() for a in raw.get("allow", [])]
        rf.expire = [dict(e) for e in raw.get("expire", []) if isinstance(e, dict)]
        return rf

    # -- edits: one rule per sender, so setting a new one replaces the old --

    def clear(self, sender: str) -> None:
        sender = sender.lower().strip()
        self.block = [b for b in self.block if b != sender]
        self.allow = [a for a in self.allow if a != sender]
        self.expire = [e for e in self.expire if str(e.get("sender", "")).lower() != sender]

    def set_block(self, sender: str) -> None:
        self.clear(sender)
        self.block.append(sender.lower().strip())

    def set_allow(self, sender: str) -> None:
        self.clear(sender)
        self.allow.append(sender.lower().strip())

    def set_expire(self, sender: str, before: Optional[date] = None, older_than_days: Optional[int] = None) -> None:
        if before is None and older_than_days is None:
            raise ValueError("expire needs before or older_than_days")
        self.clear(sender)
        rule: Dict[str, Any] = {"sender": sender.lower().strip()}
        if before is not None:
            rule["before"] = before
        if older_than_days is not None:
            rule["older_than_days"] = int(older_than_days)
        self.expire.append(rule)

    def rule_for(self, sender: str) -> Optional[Dict[str, Any]]:
        sender = sender.lower().strip()
        if sender in self.block:
            return {"kind": "block", "sender": sender}
        if sender in self.allow:
            return {"kind": "allow", "sender": sender}
        for e in self.expire:
            if str(e.get("sender", "")).lower() == sender:
                out = {"kind": "expire", "sender": sender}
                if e.get("before") is not None:
                    out["before"] = str(e["before"])
                if e.get("older_than_days") is not None:
                    out["older_than_days"] = int(e["older_than_days"])
                return out
        return None

    def save(self) -> None:
        lines = [HEADER]
        lines.append("block = [\n" + "".join(f"  {_s(b)},\n" for b in sorted(set(self.block))) + "]\n")
        lines.append("allow = [\n" + "".join(f"  {_s(a)},\n" for a in sorted(set(self.allow))) + "]\n")
        for e in self.expire:
            lines.append("\n[[expire]]\n")
            lines.append(f"sender = {_s(str(e['sender']))}\n")
            if e.get("before") is not None:
                b = e["before"]
                lines.append(f"before = {b.isoformat() if isinstance(b, date) else date.fromisoformat(str(b)).isoformat()}\n")
            if e.get("older_than_days") is not None:
                lines.append(f"older_than_days = {int(e['older_than_days'])}\n")
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text("".join(lines), encoding="utf-8")
        os.replace(tmp, self.path)


def _s(value: str) -> str:
    """A TOML basic string. JSON's escaping is a valid subset of TOML's."""
    return json.dumps(value, ensure_ascii=False)


def rules_path_for(config_path: Path) -> Path:
    return config_path.with_name("binbag.rules.toml")
