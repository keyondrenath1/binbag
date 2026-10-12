"""Command line interface.

    binbag init                 write a starter config
    binbag login                sign in to Outlook/Hotmail (OAuth2), once
    binbag serve                the same, in your browser: scan, set rules, clean
    binbag accounts             list the accounts in your config
    binbag folders              show what the server calls things
    binbag scan                 dry run: score everything, write a report
    binbag clean --apply        move the junk to the Bin
    binbag senders              who is filling your inbox, with unsubscribe links
"""

from __future__ import annotations

import argparse
import os
import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from . import __version__
from .config import (
    OAUTH_AUTH_BLOCK,
    PASSWORD_AUTH_BLOCK,
    PROVIDER_ALIASES,
    PROVIDERS,
    STARTER_CONFIG,
    Config,
    ConfigError,
)
from .imap_client import ImapError, Mailbox, open_mailbox
from .model import Message, Verdict
from .report import terminal_summary, write_html
from .rules import Contacts, Scorer, summarise_senders

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    try:
        return args.func(args)
    except (ConfigError, ImapError) as exc:
        print(f"\n  {exc}\n", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n  Stopped. Nothing was changed.\n", file=sys.stderr)
        return 130


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="binbag", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"binbag {__version__}")
    sub = p.add_subparsers(dest="command")

    init = sub.add_parser("init", help="write a starter binbag.toml")
    init.add_argument("--provider", default="gmail", choices=sorted(set(PROVIDERS) | set(PROVIDER_ALIASES)))
    init.add_argument("--username", default="you@example.com")
    init.add_argument("-o", "--out", type=Path, default=Path("binbag.toml"))
    init.set_defaults(func=cmd_init)

    serve = sub.add_parser("serve", help="open binbag in your browser (runs locally)")
    serve.add_argument("-c", "--config", type=Path, default=None, help="path to binbag.toml")
    serve.add_argument("-a", "--account", default=None, help="account to show first")
    serve.add_argument("-p", "--port", type=int, default=8765)
    serve.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    serve.set_defaults(func=cmd_serve)

    login = sub.add_parser("login", help="sign in to Outlook/Hotmail with OAuth2 (one-off)")
    _common(login)
    login.set_defaults(func=cmd_login)

    accounts = sub.add_parser("accounts", help="list the accounts in your config")
    accounts.add_argument("-c", "--config", type=Path, default=None, help="path to binbag.toml")
    accounts.set_defaults(func=cmd_accounts)

    folders = sub.add_parser("folders", help="list mailbox folders and special ones")
    _common(folders)
    folders.set_defaults(func=cmd_folders)

    scan = sub.add_parser("scan", help="score mail and write a report, changing nothing")
    _common(scan)
    _scan_opts(scan)
    scan.set_defaults(func=cmd_scan)

    clean = sub.add_parser("clean", help="move junk to the Bin")
    _common(clean)
    _scan_opts(clean)
    clean.add_argument("--apply", action="store_true",
                       help="actually move the messages. Without this it's a dry run.")
    clean.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    clean.set_defaults(func=cmd_clean)

    demo = sub.add_parser("demo", help="run against a fake mailbox, no credentials needed")
    demo.add_argument("-t", "--threshold", type=int, default=60)
    demo.add_argument("-r", "--report", type=Path, default=Path("binbag.demo.html"))
    demo.add_argument("-q", "--quiet", action="store_true")
    demo.set_defaults(func=cmd_demo)

    senders = sub.add_parser("senders", help="who is filling your inbox, with unsubscribe links")
    _common(senders)
    _scan_opts(senders)
    senders.add_argument("-n", "--top", type=int, default=30)
    senders.set_defaults(func=cmd_senders)

    return p


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("-c", "--config", type=Path, default=None, help="path to binbag.toml")
    p.add_argument("-a", "--account", default=None,
                   help="which account, by name or address (default: the first in the config)")
    p.add_argument("-q", "--quiet", action="store_true")


def _scan_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("-f", "--folder", action="append", dest="folders",
                   help="folder to scan; repeatable. Overrides the config.")
    p.add_argument("-t", "--threshold", type=int, default=None,
                   help="junk score cutoff (default from config, usually 60)")
    p.add_argument("--last-days", type=int, default=None,
                   help="only look at mail from the last N days (default: all of it)")
    p.add_argument("--all-time", action="store_true",
                   help="ignore --last-days and scan everything (the default)")
    p.add_argument("--limit", type=int, default=0, help="cap how many messages to score")
    p.add_argument("-r", "--report", type=Path, default=Path("binbag.report.html"),
                   help="where to write the HTML report ('-' to skip)")
    p.add_argument("--csv", type=Path, default=None,
                   help="also write every message's verdict to a CSV file (kept and junk)")


# -- commands --------------------------------------------------------------


def cmd_init(args) -> int:
    if args.out.exists():
        print(f"  {args.out} already exists. Not overwriting it.")
        return 1
    provider = PROVIDER_ALIASES.get(args.provider, args.provider)
    oauth = PROVIDERS.get(provider, {}).get("auth") == "oauth2"
    args.out.write_text(
        STARTER_CONFIG.format(
            provider=provider,
            username=args.username,
            auth_block=OAUTH_AUTH_BLOCK if oauth else PASSWORD_AUTH_BLOCK,
        ),
        encoding="utf-8",
    )
    if oauth:
        print(f"""
  Wrote {args.out}

  Next:
    1. Register an app in Azure (free, five minutes; see the README):
         https://portal.azure.com  ->  App registrations  ->  New registration
    2. Paste its Application (client) ID into oauth_client_id in {args.out}
    3. binbag login
    4. binbag scan
""")
        return 0
    env = "BINBAG_PASSWORD"
    print(f"""
  Wrote {args.out}

  Next:
    1. Create an app password for {provider}:
         gmail   https://myaccount.google.com/apppasswords
         icloud  https://account.apple.com  ->  Sign-In and Security
         yahoo   Account Security  ->  Generate app password
    2. export {env}='the app password'
    3. binbag scan
""")
    return 0


def cmd_serve(args) -> int:
    from .web import serve

    return serve(args.config, port=args.port, open_browser=not args.no_browser, account=args.account)


def cmd_accounts(args) -> int:
    cfg = Config.load(args.config)
    print()
    for i, a in enumerate(cfg.accounts):
        if a.auth == "oauth2":
            signed_in = a.token_path.exists()
            how = "OAuth2, " + ("signed in" if signed_in else "not signed in: run `binbag login -a " + a.name + "`")
        else:
            how = f"app password from ${a.password_env}" + ("" if os.environ.get(a.password_env) else " (not set)")
        default = "  (default)" if i == 0 else ""
        label = a.name if a.name == a.username else f"{a.name}  ({a.username})"
        print(f"  {label:<44} {how}{default}")
    print("\n  Pick one with -a NAME, e.g.  binbag scan -a " + cfg.accounts[-1].name + "\n")
    return 0


def cmd_login(args) -> int:
    from . import oauth

    cfg = Config.load(args.config, getattr(args, 'account', None))
    if cfg.account.auth != "oauth2":
        print(f"  {cfg.account.username} uses a password, not OAuth2. Nothing to sign in to.\n"
              "  (Set auth = \"oauth2\" under [account] if this is an Outlook or Hotmail address.)\n")
        return 1
    oauth.login(cfg.account)
    print(f"\n  Signed in. Token saved to {cfg.account.token_path} (readable by you only).")
    print("  binbag refreshes it on its own from now on. Run `binbag login` again if it ever stops working.\n")
    return 0


def cmd_folders(args) -> int:
    cfg = Config.load(args.config, getattr(args, 'account', None))
    with open_mailbox(cfg.account) as box:
        specials = {
            "Bin/Trash": box.trash_folder(),
            "Junk/Spam": box.junk_folder(),
            "Sent": box.sent_folder(),
        }
        print("\n  Special folders")
        for label, name in specials.items():
            print(f"    {label:<12} {name or '(not advertised by the server)'}")
        print("\n  All folders")
        for name, flags in box.folders():
            marks = " ".join(f for f in flags if not f.startswith("\\Has"))
            print(f"    {name}{('   ' + marks) if marks else ''}")
        print()
    return 0


def cmd_scan(args) -> int:
    cfg, verdicts, account_label, _ = _run_scan(args)
    _finish(args, cfg, verdicts, account_label, applied=False)
    print("  Dry run. Nothing was moved. Use `binbag clean --apply` to act on this.\n")
    return 0


def cmd_clean(args) -> int:
    cfg, verdicts, account_label, scanned_validity = _run_scan(args)
    junk = [v for v in verdicts if v.is_junk]
    _finish(args, cfg, verdicts, account_label, applied=False)

    if not args.apply:
        print("  Dry run: --apply not given, so nothing moved.\n")
        return 0
    if not junk:
        print("  Nothing to move.\n")
        return 0

    if not args.yes:
        with open_mailbox(cfg.account) as box:
            destination = _destination_label(box, cfg)
        answer = input(f"  Move {len(junk)} messages to {destination!r}? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("  Cancelled. Nothing moved.\n")
            return 1

    total, dest, skipped = move_junk(cfg, junk, scanned_validity, say=print)
    print(f"\n  Done. {total} messages are now in {dest!r} and can be restored from there.\n")
    if skipped:
        print(f"  Not touched: {', '.join(skipped)}.\n")

    if args.report and str(args.report) != "-":
        write_html(verdicts, args.report, _threshold(cfg, args), account_label, applied=True)
    return 0


def cmd_demo(args) -> int:
    from .config import Rules
    from .demo import corpus

    rules = Rules(threshold=args.threshold, min_age_days=3, learn_from_sent=False)
    verdicts = Scorer(rules, Contacts()).judge_all(corpus())
    if not args.quiet:
        print(terminal_summary(verdicts, args.threshold))
    path = write_html(verdicts, args.report, args.threshold, "demo mailbox (fake data)", False)
    print(f"  Report: {path.resolve()}\n")
    return 0


def cmd_senders(args) -> int:
    cfg, verdicts, _, _ = _run_scan(args)
    summaries = summarise_senders(verdicts)
    print(f"\n  {'junk':>5} {'total':>6}  sender")
    print("  " + "-" * 70)
    for s in summaries[: args.top]:
        label = s.display if len(s.display) <= 44 else s.display[:41] + "..."
        flag = "  [unsub]" if s.unsubscribe.startswith("http") else ""
        print(f"  {s.junk:>5} {s.total:>6}  {label}{flag}")
    print()
    for s in summaries[: args.top]:
        if s.unsubscribe.startswith("http") and s.junk:
            print(f"  {s.address}\n    {s.unsubscribe}")
    print()
    return 0


# -- shared machinery ------------------------------------------------------


def move_junk(cfg: Config, junk: Sequence[Verdict], scanned_validity: Dict[str, Optional[int]],
              say=lambda *a: None) -> tuple[int, str, List[str]]:
    """Move these verdicts' messages to the Bin. Returns (moved, bin name, skipped folders).

    Shared by `clean --apply` and `binbag serve`, so both get the same checks.
    """
    by_folder: Dict[str, List[str]] = {}
    for v in junk:
        by_folder.setdefault(v.message.folder, []).append(v.message.uid)

    total = 0
    skipped: List[str] = []
    with open_mailbox(cfg.account) as box:
        dest = _resolve_destination(box, cfg)
        for folder, uids in by_folder.items():
            if folder == dest:
                continue  # already in the Bin
            box.select(folder, readonly=False)
            # UIDs only mean anything alongside the folder's UIDVALIDITY. If the
            # server renumbered the folder since the scan, these UIDs may now
            # point at different messages. Refuse rather than guess.
            before, now = scanned_validity.get(folder), box.uidvalidity.get(folder)
            if before is None or now is None or before != now:
                skipped.append(folder)
                say(f"  {folder}: skipped, the server renumbered this folder since the scan "
                    f"(UIDVALIDITY {before} -> {now}). Re-run to rescan it.")
                continue
            moved = box.move(uids, dest)
            total += moved
            say(f"  {folder}: moved {moved}")
    return total, dest, skipped


def fetch_mailbox(cfg: Config, folders: Sequence[str], criteria: Sequence[str] = (), limit: int = 0,
                  say=lambda *a: None, progress=None) -> tuple[List[Message], Contacts, Dict[str, Optional[int]]]:
    """Read headers from the given folders, read-only. No scoring."""
    with open_mailbox(cfg.account) as box:
        contacts = Contacts()
        if cfg.rules.learn_from_sent:
            contacts = _learn_contacts(box, cfg, say)

        messages: List[Message] = []
        validity: Dict[str, Optional[int]] = {}
        for folder in folders:
            say(f"  Reading {folder} ...")
            box.select(folder, readonly=True)
            validity[folder] = box.uidvalidity.get(folder)
            uids = box.search(*criteria)
            if limit:
                uids = uids[-limit:]
            if not uids:
                say(f"    nothing matched in {folder}")
                continue
            if progress:
                progress(folder, 0, len(uids))
                fetched = box.fetch_headers(uids, progress=lambda done, total, f=folder: progress(f, done, total))
            else:
                fetched = box.fetch_headers(uids)
            _tag_gmail_categories(box, fetched)
            messages.extend(fetched)
            say(f"    {len(uids)} messages")
    return messages, contacts, validity


def _run_scan(args) -> tuple[Config, List[Verdict], str, Dict[str, Optional[int]]]:
    cfg = Config.load(args.config, getattr(args, 'account', None))
    folders = args.folders or cfg.rules.folders or ["INBOX"]
    threshold = _threshold(cfg, args)
    cfg.rules.threshold = threshold
    say = (lambda *a: None) if args.quiet else (lambda *a: print(*a))

    criteria: List[str] = []
    if not args.all_time and args.last_days and args.last_days > 0:
        since = datetime.now() - timedelta(days=args.last_days)
        criteria = ["SINCE", f"{since.day:02d}-{MONTHS[since.month - 1]}-{since.year}"]

    messages, contacts, validity = fetch_mailbox(cfg, folders, criteria, args.limit, say)

    scorer = Scorer(cfg.rules, contacts)
    verdicts = scorer.judge_all(messages)
    account_label = f"{cfg.account.username} ({', '.join(folders)})"
    return cfg, verdicts, account_label, validity


def _tag_gmail_categories(box: Mailbox, messages: List[Message]) -> None:
    """Gmail hides its inbox tabs from X-GM-LABELS; add them back so the
    Promotions/Social/Forums signals can fire."""
    categories = box.gmail_categories()
    if not categories:
        return
    for msg in messages:
        label = categories.get(msg.uid)
        if label and label not in msg.gmail_labels:
            msg.gmail_labels.append(label)


def _learn_contacts(box: Mailbox, cfg: Config, say) -> Contacts:
    sent = box.sent_folder()
    if not sent:
        say("  No Sent folder advertised; skipping contact learning.")
        return Contacts()
    say(f"  Learning who you actually write to, from {sent} ...")
    try:
        box.select(sent, readonly=True)
        uids = box.search("ALL")[-cfg.rules.sent_sample :]
        contacts = Contacts.from_sent(box.fetch_headers(uids))
    except ImapError as exc:
        say(f"  Could not read {sent} ({exc}); continuing without it.")
        return Contacts()
    say(f"    {len(contacts.addresses)} addresses protected")
    return contacts


def _finish(args, cfg: Config, verdicts, account_label: str, applied: bool) -> None:
    if not args.quiet:
        print(terminal_summary(verdicts, _threshold(cfg, args)))
    if args.report and str(args.report) != "-":
        path = write_html(verdicts, args.report, _threshold(cfg, args), account_label, applied)
        print(f"  Report: {path.resolve()}\n")
    if getattr(args, "csv", None):
        path = write_csv(verdicts, args.csv)
        print(f"  CSV:    {path.resolve()}  (contains your senders and subjects: don't commit it)\n")


CSV_FIELDS = ["folder", "uid", "date", "unread", "flagged", "verdict", "score",
              "from_address", "from_name", "subject", "reasons", "protections"]


def write_csv(verdicts: Sequence[Verdict], path: Path) -> Path:
    """Every message, one row each, newest first. For reviewing what was kept."""
    rows = sorted(verdicts, key=lambda v: v.message.date.timestamp() if v.message.date else 0, reverse=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        out = csv.writer(fh)
        out.writerow(CSV_FIELDS)
        for v in rows:
            m = v.message
            out.writerow([
                m.folder, m.uid, m.date.date().isoformat() if m.date else "",
                "yes" if m.is_unread else "", "yes" if m.is_flagged else "",
                "junk" if v.is_junk else "keep", v.score,
                m.from_address, m.from_display, m.subject,
                "; ".join(v.reasons), "; ".join(v.protections),
            ])
    return path


def _threshold(cfg: Config, args) -> int:
    return args.threshold if getattr(args, "threshold", None) is not None else cfg.rules.threshold


def _destination(cfg: Config, args) -> str:
    return "the Bin" if cfg.destination == "trash" else "the Junk folder"


def _destination_label(box: Mailbox, cfg: Config) -> str:
    """What the server actually calls it: 'Deleted', '[Gmail]/Bin', 'Trash' ..."""
    try:
        return _resolve_destination(box, cfg)
    except ImapError:
        return _destination(cfg, None)


def _resolve_destination(box: Mailbox, cfg: Config) -> str:
    dest = box.trash_folder() if cfg.destination == "trash" else box.junk_folder()
    if not dest:
        raise ImapError(
            f"Could not find a {cfg.destination} folder on this server. "
            "Run `binbag folders` to see what it calls things."
        )
    return dest


if __name__ == "__main__":
    raise SystemExit(main())
