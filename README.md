# binbag

Find the junk in your mailbox and bin it, from the terminal. Dry run by default, and it never erases anything.

```
  Scanned 4,182 messages
  Junk    1,946  (score >= 60)
  Keeping 2,236

  Worst offenders
  --------------------------------------------------------------
    214  LinkedIn <notifications-noreply@linkedin.com>
    167  Groupon <deals@r.groupon.com>
     98  Medium Daily Digest <noreply@medium.com>
```

## Why this exists

The Mail app on an iPhone is a closed box. Nothing can reach inside it. What it *is* showing you is an IMAP account, and IMAP has been an open protocol since 1986. Clean the account and the phone catches up in seconds, because the phone was only ever a window onto it.

So binbag talks to Gmail, iCloud, Outlook, Yahoo, Fastmail or anything else that speaks IMAP, and the results appear on every device you own.

## Install

```bash
git clone https://github.com/keyondrenath1/binbag
cd binbag
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Python 3.11 or newer. No dependencies: everything it needs is in the standard library. The virtual environment keeps it away from any other Python on your machine (conda, Homebrew, the system one), which is the usual cause of `command not found` or "requires a different Python". In a new terminal, `source .venv/bin/activate` again before running `binbag`.

## Try it without connecting anything

```bash
binbag demo
```

That scores a fake mailbox and writes `binbag.demo.html`. Open it, see how the scoring reads, decide whether you trust it. Nothing touches the network.

## Point it at a real mailbox

```bash
binbag init --provider gmail --username you@gmail.com
export BINBAG_PASSWORD='xxxx xxxx xxxx xxxx'
binbag scan
```

You need an **app-specific password**, not your normal one, and two-factor authentication switched on:

| Provider | Where |
|---|---|
| Gmail | [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) |
| iCloud | account.apple.com → Sign-In and Security → App-Specific Passwords |
| Yahoo | Account Security → Generate app password |
| Outlook, Hotmail, Live | No app passwords any more: use OAuth2, [below](#outlook-and-hotmail) |

`binbag scan` changes nothing. It reads headers, scores everything, prints a summary and writes `binbag.report.html` grouped by sender with unsubscribe links. Read it. Adjust. Then:

```bash
binbag clean --apply
```

## Outlook and Hotmail

Microsoft switched off password and app-password IMAP logins for Outlook.com, Hotmail and Live addresses on 16 September 2024. The only way in now is OAuth2, which binbag does with Microsoft's device code sign-in: it prints a short code, you enter it on a Microsoft page in any browser, and binbag never sees your password.

You need your own free Azure app registration. It takes about five minutes, once:

1. Go to [portal.azure.com](https://portal.azure.com), sign in with any Microsoft account, open **App registrations** and choose **New registration**.
2. Name it `binbag`. Under **Supported account types** pick **Personal Microsoft accounts only** (or a multi-tenant option that includes personal accounts if you also want a work mailbox). Leave the redirect URI empty. Register.
3. Copy the **Application (client) ID** from the overview page.
4. Open **Authentication**, set **Allow public client flows** to **Yes**, and save.

No API permissions need adding: binbag asks for IMAP access when you sign in, and you approve it there. If the portal says your account has no directory to register apps in, create a free Azure account first.

Then:

```bash
binbag init --provider hotmail --username you@hotmail.co.uk
# paste the client ID into oauth_client_id in binbag.toml
binbag login
binbag scan
```

`binbag login` saves a refresh token to `~/.config/binbag/tokens/`, readable by you only and never inside the repo. binbag refreshes it on its own after that. If it ever stops working (you changed your password, or revoked access at account.microsoft.com), run `binbag login` again.

For a Microsoft 365 work or school mailbox, set `oauth_tenant = "organizations"` or your tenant ID. Your IT admin may need to approve the app.

## Commands

```
binbag demo                    fake mailbox, no credentials, see the output shape
binbag init                    write a starter binbag.toml
binbag login                   sign in to Outlook/Hotmail with OAuth2, once
binbag folders                 what your server calls the Bin, Sent and Junk
binbag scan                    score everything, write a report, change nothing
binbag clean --apply           move the junk to the Bin
binbag senders -n 40           who is filling your inbox, with unsubscribe links
```

Useful flags: `--csv all.csv` writes every message's verdict, kept and junk, to a spreadsheet you can sort and filter. `--threshold 45` bins more, `--threshold 75` bins less. `--folder "Archive"` (repeatable) widens the sweep. By default every message in the folder is scanned, however old; `--last-days 90` narrows it. `--limit 500` for a cautious first run.

## How it decides

Two separate mechanisms, and the difference matters.

**Signals** add up to a score out of 100. An unsubscribe header is worth 40, because mail carrying one was sent to a list. Gmail's own Promotions label is worth 35. Sending infrastructure like Mailchimp or SendGrid in the envelope, 20. Marketing language in the subject, up to 24. Never opened and three months old, 25. Volume from a sender you've never once replied to, up to 12. Anything at or above your threshold is junk.

**Protections** override the score entirely. Flagged. Replied to. From someone in your Sent folder. On your allowlist. Under a few days old. Or, most importantly, transactional: a subject containing *receipt*, *your order*, *booking*, *verification code*, *statement*, *renewal* and about seventy other phrases. A dispatch note from a shop arrives through the same marketing platform as their sale emails and carries the same unsubscribe header. Scoring alone would bin it. Protections mean it can't be.

The bias is deliberate and asymmetric: keeping a newsletter you didn't want costs you two seconds, binning a booking confirmation costs you a flight.

Before scoring anything, binbag reads your Sent folder and builds a list of everyone you've actually written to. Those people become untouchable. It's the single strongest signal available and it costs one folder read.

**Expire rules** are the third mechanism, for senders whose recent mail you want and whose back catalogue you don't. An expire rule bins a sender's mail once it's older than a date or an age, *even if it looks transactional*: that's what it's for. Your own actions still win: a flagged message, or one you replied to, is never expired.

```toml
[[rules.expire]]
sender = "amazon.co.uk"      # a domain covers its subdomains: shipment-tracking@, auto-confirm@, ...
before = 2024-01-01

[[rules.expire]]
sender = "uber.com"
older_than_days = 365        # rolling: always keeps the last year

[[rules.expire]]
sender = "alerts@bank.example"   # or one exact address
older_than_days = 730
```

The full order of precedence: blocklist, then flagged or replied to, then expire rules, then the other protections, then the score.

## The safety rails

- **Nothing is erased, ever.** Junk is *moved* to the Bin. There is exactly one mutating method in the codebase and it issues `UID MOVE`. Restore anything for as long as your provider keeps deleted mail, which is usually 30 days.
- **No `CLOSE`, ever.** IMAP's `CLOSE` on a folder opened for writing silently expunges everything flagged as deleted, including mail another app flagged and hasn't purged. binbag leaves folders with `UNSELECT`, or simply logs out, and a test fails if `CLOSE` ever creeps back in.
- **UIDs are checked before moving.** A scan records each folder's `UIDVALIDITY`. If the server has renumbered the folder by the time you `clean`, that folder is skipped rather than risk moving the wrong messages.
- **Dry run by default.** `clean` without `--apply` reports and stops. With `--apply` it still asks before moving, unless you pass `--yes`.
- **Headers only.** Message bodies are never downloaded. Faster, and it keeps the blast radius of any bug very small.
- **Read-only unless moving.** Folders are opened read-only for every scan; the move path is the only thing that opens one for writing, and it refuses to run against a read-only handle.
- **Your password never enters the repo.** It's read from an environment variable, and OAuth tokens live in `~/.config/binbag/tokens/` with owner-only permissions. `binbag.toml` and generated reports are in `.gitignore`.

## Config

```toml
[account]
provider = "gmail"
username = "you@gmail.com"
password_env = "BINBAG_PASSWORD"

destination = "trash"      # or "junk"

[rules]
threshold = 60             # lower bins more; 75 cautious, 45 brutal
min_age_days = 3           # leave the last few days alone
folders = ["INBOX"]

allow = ["hmrc.gov.uk", "school.example.com", "solicitor@firm.example"]
block = ["nuisance-sender.example"]

learn_from_sent = true
sent_sample = 2000

junk_subjects = ["your weekly horoscope"]
keep_subjects = ["parents evening", "rent"]

[[rules.expire]]
sender = "amazon.co.uk"
before = 2024-01-01
```

For Outlook or Hotmail the `[account]` block looks like this instead:

```toml
[account]
provider = "hotmail"           # or outlook, live
username = "you@hotmail.co.uk"
auth = "oauth2"
oauth_client_id = "00000000-0000-0000-0000-000000000000"
oauth_tenant = "consumers"     # "organizations" for work or school
```

`allow` and `block` take whole domains or single addresses. `block` wins over everything, including the transactional protections, so use it sparingly.

## A sensible first run

```bash
binbag scan --last-days 90 --limit 500 --threshold 70
```

Read the report. If the "close calls that were kept" table looks right and the junk list has no surprises in it, widen the net and lower the threshold. Once you trust it, `binbag clean --apply`.

## Tests

```bash
pip install -e '.[dev]'
pytest
```

The tests in `tests/test_rules.py`, `tests/test_safety.py` and `tests/test_mailbox.py` are the ones that matter. They assert that receipts, booking confirmations, verification codes, flagged mail and anyone you've ever emailed survive contact with the scorer even when every junk signal is firing at once. If those go red, the tool is dangerous, not merely broken.

## What it deliberately doesn't do

OAuth only where the provider leaves no choice, which today means Microsoft; Gmail, iCloud, Yahoo and Fastmail all still take app passwords, so no Google Cloud project to set up. No message bodies, so no content-based classification and no AI in the loop. No permanent deletion. No background daemon. It's a thing you run, read, and act on.

## Licence

MIT.
