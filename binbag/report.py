"""Terminal and HTML reporting."""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Sequence

from .rules import SenderSummary, summarise_senders
from .model import Verdict


def terminal_summary(verdicts: Sequence[Verdict], threshold: int, limit: int = 25) -> str:
    junk = [v for v in verdicts if v.is_junk]
    protected = [v for v in verdicts if v.protected]
    lines: list[str] = []
    lines.append("")
    lines.append(f"  Scanned {len(verdicts)} messages")
    lines.append(f"  Junk    {len(junk)}  (score >= {threshold})")
    lines.append(f"  Keeping {len(protected)}")
    lines.append("")
    if not junk:
        lines.append("  Nothing to bin. Your inbox is cleaner than you think.")
        return "\n".join(lines)

    senders = [s for s in summarise_senders(junk) if s.junk]
    lines.append("  Worst offenders")
    lines.append("  " + "-" * 62)
    for s in senders[:limit]:
        label = s.display if len(s.display) <= 42 else s.display[:39] + "..."
        lines.append(f"  {s.junk:>5}  {label}")
    if len(senders) > limit:
        lines.append(f"  {'':>5}  ... and {len(senders) - limit} more senders")
    lines.append("")
    return "\n".join(lines)


def write_html(
    verdicts: Sequence[Verdict],
    path: Path,
    threshold: int,
    account: str,
    applied: bool = False,
) -> Path:
    junk = [v for v in verdicts if v.is_junk]
    kept = [v for v in verdicts if v.protected]
    senders = [s for s in summarise_senders(junk) if s.junk]
    generated = datetime.now().strftime("%d %B %Y, %H:%M")
    verb = "Binned" if applied else "Would bin"

    rows = "\n".join(_sender_block(s, junk) for s in senders)
    kept_rows = "\n".join(_kept_row(v) for v in _interesting_kept(kept))

    doc = f"""<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>binbag report</title>
<style>
  :root {{
    --bg: #fbfaf8; --fg: #1c1b19; --muted: #6b6862; --line: #e3e0da;
    --card: #ffffff; --accent: #b4472a; --good: #3f6d4e;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #16150f; --fg: #eceae4; --muted: #9a968c; --line: #2f2d26;
      --card: #1e1c16; --accent: #e0724f; --good: #7fb392;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 2rem 1.25rem 5rem; background: var(--bg); color: var(--fg);
    font: 15px/1.55 ui-sans-serif, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
  }}
  main {{ max-width: 940px; margin: 0 auto; }}
  h1 {{ font-size: 1.65rem; margin: 0 0 .25rem; letter-spacing: -.02em; }}
  .sub {{ color: var(--muted); margin: 0 0 2rem; }}
  .stats {{ display: flex; flex-wrap: wrap; gap: .75rem; margin-bottom: 2.25rem; }}
  .stat {{
    flex: 1 1 150px; background: var(--card); border: 1px solid var(--line);
    border-radius: 10px; padding: .9rem 1rem;
  }}
  .stat b {{ display: block; font-size: 1.7rem; font-weight: 650; letter-spacing: -.02em; }}
  .stat span {{ color: var(--muted); font-size: .82rem; text-transform: uppercase; letter-spacing: .06em; }}
  h2 {{ font-size: .82rem; text-transform: uppercase; letter-spacing: .08em;
        color: var(--muted); margin: 2.5rem 0 .75rem; font-weight: 600; }}
  details {{ background: var(--card); border: 1px solid var(--line); border-radius: 10px;
             margin-bottom: .5rem; overflow: hidden; }}
  summary {{ cursor: pointer; padding: .8rem 1rem; display: flex; align-items: baseline;
             gap: .75rem; list-style: none; }}
  summary::-webkit-details-marker {{ display: none; }}
  summary::before {{ content: "\\25B8"; color: var(--muted); transition: transform .15s; }}
  details[open] summary::before {{ transform: rotate(90deg); }}
  .count {{ font-variant-numeric: tabular-nums; font-weight: 650; color: var(--accent);
            min-width: 2.5ch; text-align: right; }}
  .who {{ flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .addr {{ color: var(--muted); font-size: .85em; }}
  .unsub {{ font-size: .8rem; color: var(--muted); white-space: nowrap; }}
  .unsub a {{ color: var(--accent); }}
  ul.msgs {{ margin: 0; padding: 0 1rem 1rem 3.1rem; list-style: none; }}
  ul.msgs li {{ padding: .5rem 0; border-top: 1px solid var(--line); }}
  .subj {{ display: block; }}
  .why {{ color: var(--muted); font-size: .85em; }}
  .score {{ font-variant-numeric: tabular-nums; color: var(--muted); font-size: .85em; }}
  table {{ width: 100%; border-collapse: collapse; font-size: .9rem; }}
  td {{ padding: .5rem .6rem; border-top: 1px solid var(--line); vertical-align: top; }}
  td.keep {{ color: var(--good); white-space: nowrap; }}
  .foot {{ margin-top: 3rem; color: var(--muted); font-size: .85rem;
           border-top: 1px solid var(--line); padding-top: 1rem; }}
  .wrap {{ overflow-x: auto; }}
</style>
</head>
<body>
<main>
  <h1>binbag report</h1>
  <p class="sub">{html.escape(account)} &middot; {generated}</p>

  <div class="stats">
    <div class="stat"><b>{len(verdicts)}</b><span>scanned</span></div>
    <div class="stat"><b>{len(junk)}</b><span>{verb.lower()}</span></div>
    <div class="stat"><b>{len(kept)}</b><span>kept</span></div>
    <div class="stat"><b>{len(senders)}</b><span>junk senders</span></div>
  </div>

  <h2>{verb} &mdash; grouped by sender</h2>
  {rows or "<p>Nothing scored above the threshold.</p>"}

  <h2>Close calls that were kept</h2>
  <div class="wrap"><table>{kept_rows or "<tr><td>Nothing borderline.</td></tr>"}</table></div>

  <p class="foot">
    {"Messages were moved to your Bin and can be restored from there." if applied
      else "Nothing has been moved. This was a dry run: re-run with --apply to act on it."}
    Threshold {threshold}. binbag only ever moves mail, it never erases it.
  </p>
</main>
</body>
</html>
"""
    path.write_text(doc, encoding="utf-8")
    return path


def _sender_block(summary: SenderSummary, junk: Sequence[Verdict]) -> str:
    mine = [v for v in junk if (v.message.from_address or "(unknown)") == summary.address]
    items = "\n".join(
        "<li><span class=\"subj\">{subj}</span>"
        "<span class=\"why\">{score} &middot; {why}</span></li>".format(
            subj=html.escape(v.message.subject or "(no subject)"),
            score=v.score,
            why=html.escape("; ".join(v.reasons[:3])),
        )
        for v in sorted(mine, key=lambda v: -v.score)[:60]
    )
    more = f"<li class=\"why\">... and {len(mine) - 60} more</li>" if len(mine) > 60 else ""
    unsub = ""
    if summary.unsubscribe.startswith("http"):
        unsub = f'<span class="unsub"><a href="{html.escape(summary.unsubscribe)}" rel="noreferrer noopener">unsubscribe</a></span>'
    display = html.escape(summary.display)
    addr = html.escape(summary.address)
    return f"""<details>
  <summary><span class="count">{summary.junk}</span>
    <span class="who">{display}<br><span class="addr">{addr}</span></span>
    {unsub}</summary>
  <ul class="msgs">{items}{more}</ul>
</details>"""


def _interesting_kept(kept: Sequence[Verdict], limit: int = 40) -> list[Verdict]:
    """Show the near-misses, not the thousand obviously-fine messages."""
    scored = [v for v in kept if v.score > 0 or v.protections]
    scored.sort(key=lambda v: -v.score)
    return scored[:limit]


def _kept_row(v: Verdict) -> str:
    why = v.protections[0] if v.protections else "below threshold"
    return (
        "<tr>"
        f'<td class="keep">kept</td>'
        f"<td>{html.escape(v.message.from_display or v.message.from_address)}<br>"
        f'<span class="why">{html.escape(v.message.subject or "(no subject)")}</span></td>'
        f'<td class="why">{html.escape(why)}</td>'
        f'<td class="score">{v.score}</td>'
        "</tr>"
    )
