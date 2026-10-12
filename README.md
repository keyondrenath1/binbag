# binbag

Find the junk in your mailbox and bin it, from your own computer. You see exactly what will go and why before anything moves, and nothing is ever erased: junk goes to your Bin, where you can still restore it.

It works with Gmail, iCloud, Outlook, Hotmail, Yahoo, Fastmail and anything else that speaks IMAP, and the results show up on every device, phone included, because it cleans the mailbox itself rather than one app's view of it.

```
  74 messages from 7 senders are going in the bin.

  Gym Chain        All 14 going in the bin. Has an unsubscribe link, so it's bulk mail.   [Keep | Auto | Bin]
  LinkedIn         All 13 going in the bin. Has an unsubscribe link, so it's bulk mail.   [Keep | Auto | Bin]
  Wine Club        3 of 7 going in the bin.                                               [Keep | Auto | Bin]
```

## Getting started, step by step

You'll need a Mac, Linux or Windows machine with **Python 3.11 or newer**, and about ten minutes (twenty for Outlook or Hotmail, which needs a one-off Microsoft setup).

### Step 1. Install binbag

Open a terminal and run:

```bash
git clone https://github.com/keyondrenath1/binbag
cd binbag
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Check it worked with `binbag --version`.

The `.venv` line gives binbag its own copy of Python, so it can't clash with conda, Homebrew or anything else on your machine. **In every new terminal window, run `cd binbag` and `source .venv/bin/activate` before using binbag.** If you ever see `command not found: binbag`, that step was skipped.

On Windows, use `py -m venv .venv` and `.venv\Scripts\activate` instead.

### Step 2. Create your config file

Pick your provider and put in your own address:

```bash
binbag init --provider gmail --username you@gmail.com
```

Providers: `gmail`, `icloud`, `outlook` (also `hotmail`, `live`, `msn`), `yahoo`, `fastmail`, `proton`. This writes `binbag.toml` in the current folder. It never contains a password, and it's in `.gitignore`.

### Step 3. Let binbag into your mailbox

Your normal password won't work here. Follow the section for your provider.

#### Gmail

1. Turn on 2-Step Verification if it isn't already: [myaccount.google.com/security](https://myaccount.google.com/security).
2. Create an app password at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords). Call it `binbag`. Google shows a 16-character password once: copy it.
3. In your terminal:

   ```bash
   export BINBAG_PASSWORD='abcd efgh ijkl mnop'
   ```

   That lasts until you close the terminal. To make it permanent, add the same line to `~/.zshrc` (Mac) or `~/.bashrc` (Linux).

#### iCloud

1. Two-factor authentication must be on (it is for almost every Apple Account).
2. Go to [account.apple.com](https://account.apple.com), open **Sign-In and Security**, then **App-Specific Passwords**, and create one called `binbag`.
3. `export BINBAG_PASSWORD='xxxx-xxxx-xxxx-xxxx'`

#### Yahoo

1. Go to **Account Security** in your Yahoo account settings and choose **Generate app password**. Call it `binbag`.
2. `export BINBAG_PASSWORD='the app password'`

#### Fastmail

1. In Fastmail, go to **Settings**, then **Privacy & Security**, and create an app password with IMAP access.
2. `export BINBAG_PASSWORD='the app password'`

#### Outlook, Hotmail, Live and MSN

Microsoft switched off passwords and app passwords for these accounts in September 2024, so binbag signs in with Microsoft's own login instead. You need a free Microsoft "app registration" first. It sounds technical; it's a form with four fields.

1. **Get into the Azure portal.** Go to [portal.azure.com](https://portal.azure.com) and sign in with your Outlook or Hotmail address. If you see an error mentioning `AADSTS16000` or "does not exist in tenant", your account needs a free Azure account first: sign up at [azure.microsoft.com/free](https://azure.microsoft.com/free) and choose **Try Azure for free** (it asks for a card to check your identity; the free account has a spending limit, so it isn't charged). Then go back to the portal.
2. **Register binbag.** Search the top bar for **App registrations**, choose **New registration**, name it `binbag`, and under **Supported account types** pick **Personal accounts only** (or an option that includes personal accounts, if you also want a work mailbox). Leave the redirect URI empty and press **Register**.
3. **Copy the ID.** On the page that opens, copy the **Application (client) ID**. It isn't a password; it just names your registration.
4. **Allow sign-in from a terminal.** In the left menu, open **Manage**, then **Authentication**. Set **Allow public client flows** to **Yes** and press **Save**. Skip this and step 6 fails with `unauthorized_client`.
5. **Put the ID in your config.** Open `binbag.toml` and paste it between the quotes on the `oauth_client_id` line.
6. **Sign in:**

   ```bash
   binbag login
   ```

   It shows a link and a short code. Open the link in any browser, type the code, sign in, and allow access to your mail. binbag saves the sign-in to `~/.config/binbag/tokens/`, readable only by you, and renews it by itself from then on.

You can ignore any Azure notices about multifactor authentication or "elevated access". They're about Azure's own tools, not binbag. Never press **Upgrade** on the free account; binbag needs nothing paid.

### Step 4. Check the connection

```bash
binbag folders
```

You should see your folders, with your Bin listed next to **Bin/Trash**. If you get an error instead, see [Troubleshooting](#troubleshooting).

### Step 5. See what's junk

```bash
binbag serve
```

Your browser opens binbag. Press **Scan inbox**. binbag reads the headers of every message (never the contents), which takes a minute or two for a few thousand, and changes nothing while it does.

You'll get a list of senders, biggest offenders first, each with what binbag plans to do and why. For each sender pick:

- **Keep**: always keep everything from them.
- **Auto**: let binbag judge each message (the default).
- **Bin**: always bin everything from them.

Click a sender to open it and you can also:

- **Clear out the backlog.** Bin anything from them older than 3 months, a year, 2 years or a date you pick. Perfect for receipts: keep this year's, bin the rest.
- **Apply to their whole domain.** Everyone at `shop.example.com`, not just one address.
- **Unsubscribe** at the source, using the sender's own link.

Every change updates the numbers straight away, and the little message that appears has an **Undo**.

### Step 6. Move the junk

When the list looks right, press the yellow **Move to the Bin** button and confirm. Everything goes to your mail's Bin (or Deleted Items, or Trash, whatever your provider calls it). It's still there if you change your mind: drag it back in your mail app.

### Step 7. Empty your Bin when you're happy

binbag never empties it for you. Give it a day or two, then empty it in your mail app. That's also when your storage frees up.

## Doing it from the terminal instead

Everything the browser page does is also a command:

```
binbag scan                    score everything, write binbag.report.html, change nothing
binbag scan --csv all.csv      also export every message's verdict to a spreadsheet
binbag clean --apply           move the junk to the Bin (asks first; add -y to skip the question)
binbag senders -n 40           who sends the most, with unsubscribe links
binbag folders                 what your server calls the Bin, Sent and Junk
binbag accounts                list your accounts and whether they're signed in
binbag login                   sign in to Outlook/Hotmail, once
binbag demo                    try it on a fake mailbox, no account needed
```

Useful options: `--threshold 45` bins more, `--threshold 75` bins less. `--last-days 90` only looks at recent mail (the default is all of it). `--limit 500` for a cautious first run. `--folder "Archive"` scans another folder (repeat it for several).

A sensible first run from the terminal:

```bash
binbag scan --limit 500
open binbag.report.html      # Linux: xdg-open
```

## More than one mailbox

Say you've set up Hotmail and now want Gmail too.

1. Open `binbag.toml`. Change the line `[account]` to `[[accounts]]` and add `name = "hotmail"` underneath it.
2. Add a block for the new mailbox, above `[rules]`:

   ```toml
   [[accounts]]
   name = "gmail"
   provider = "gmail"
   username = "you@gmail.com"
   ```

3. Create a Gmail app password as in [Step 3](#gmail), then give it to binbag under that account's own name:

   ```bash
   export BINBAG_PASSWORD_GMAIL='abcd efgh ijkl mnop'
   ```

4. Check both are ready with `binbag accounts`, then run `binbag serve`. A menu at the top switches between mailboxes.

The first account in the file is the default. Pick another anywhere with `-a`: `binbag scan -a gmail`, `binbag login -a hotmail`. Each mailbox is scanned and cleaned on its own, and nothing ever moves between them. Your rules (Keep, Bin, backlog clear-outs) apply to every mailbox, because a newsletter is junk wherever it lands.

With several accounts, each app password lives in `BINBAG_PASSWORD_<NAME>` (the account's name in capitals), unless you set `password_env` yourself. Each account can also have its own `folders` and `destination`.

## How binbag decides

Every message gets a score from signals. Anything at or above the threshold (60 by default) is junk:

| Signal | Points |
|---|---|
| Has an unsubscribe link (it was sent to a list) | 40 |
| Gmail put it in Promotions / Social / Forums | 35 / 25 / 20 |
| Sent to a mailing list, or marked bulk | 20 / 15 |
| Sent through a bulk platform (Mailchimp, SendGrid, ...) | 20 |
| Marketing words in the subject | up to 24 |
| Never opened and over 3 months / 1 month old | 25 / 15 |
| Lots of mail from a sender you've never replied to | up to 12 |
| From a no-reply style address | 12 |
| Subject uses look-alike letters to dodge filters ("ᑭaid", "eIigible") | 50 |
| You've written to someone at that organisation | minus 25 |

Some things **protect** a message outright, whatever the score: anything you flagged or starred, replied to, sent by someone you've emailed (binbag reads your Sent folder to learn who), on your Keep list, under 3 days old, or with a subject that looks like a receipt, order, booking, delivery, statement, bill, security code or anything else you'd regret losing.

Some things **bin** a message outright: senders you've set to Bin, backlog clear-outs once mail passes the age you chose, senders whose address is unreadable garbage (always a spoof), and strangers on Gmail or Hotmail addresses sending sales pitches stamped with random anti-filter tags.

The order, so there are no surprises: **Bin** rules first (the one thing that overrides a flag, so use them for senders you never want), then anything you flagged or replied to, then spoofed senders, throwaway-account spam and backlog clear-outs, then the protections, then the score.

The bias is deliberate: keeping a newsletter you didn't want costs you two seconds. Binning a booking confirmation costs you a flight.

## Your config file

`binbag init` writes this for you. Every key is optional apart from the account.

```toml
[account]
provider = "gmail"
username = "you@gmail.com"
password_env = "BINBAG_PASSWORD"

destination = "trash"          # or "junk" to send it to the spam folder instead

[rules]
threshold = 60                 # lower bins more: 75 is cautious, 45 is brutal
min_age_days = 3               # never touch the last few days
folders = ["INBOX"]

allow = ["hmrc.gov.uk", "school.example.com", "solicitor@firm.example"]   # always keep
block = ["nuisance.example"]                                              # always bin

learn_from_sent = true         # protect everyone you've emailed
sent_sample = 2000

junk_subjects = ["your weekly horoscope"]
keep_subjects = ["parents evening", "rent"]

[[rules.expire]]               # backlog clear-out: bin their mail once it's this old
sender = "amazon.co.uk"        # a domain covers its subdomains too
before = 2024-01-01            # or: older_than_days = 365
```

Changes you make in the browser are saved to `binbag.rules.toml` next to it, so your own `binbag.toml`, comments and all, is never rewritten. A rule set in the browser overrides the same sender in `binbag.toml`.

## The safety rails

- **Nothing is erased, ever.** Junk is moved to your Bin with IMAP `MOVE`. There's exactly one place in the code that changes your mailbox, and tests fail if another appears.
- **No `CLOSE`.** That IMAP command quietly erases mail other apps have marked for deletion. binbag never sends it, and a test makes sure.
- **Headers only.** Message contents are never downloaded.
- **Read-only until you say so.** Scans open folders read-only. Only the move opens one for writing.
- **Checked before moving.** If your server renumbered a folder between the scan and the move, binbag skips that folder rather than risk moving the wrong messages.
- **Your browser page is yours alone.** `binbag serve` only listens on your own computer, every request needs a one-time key baked into the page, and it ignores requests addressed to any other name. No website you have open can reach it.
- **Nothing personal in the repo.** Passwords come from environment variables. Microsoft sign-ins live in `~/.config/binbag/tokens/`. Your config, browser rules, reports and CSVs are all in `.gitignore`.

## Troubleshooting

**`command not found: binbag`.** You're in a terminal where binbag's environment isn't active. Run `cd binbag` then `source .venv/bin/activate`.

**`requires a different Python: 3.10 not in '>=3.11'`.** Your `pip` belongs to an older Python (often conda's). Use the `python3 -m venv .venv` steps in [Step 1](#step-1-install-binbag), which build the environment from your newer Python.

**`Login refused` on Gmail, iCloud or Yahoo.** You used your normal password, or 2-Step Verification is off. Create an app password ([Step 3](#step-3-let-binbag-into-your-mailbox)) and check it's in the right variable: `BINBAG_PASSWORD`, or `BINBAG_PASSWORD_<NAME>` with several accounts. `binbag accounts` shows which variable each account reads.

**`Login refused` on Outlook or Hotmail.** Microsoft no longer accepts passwords there. Set `auth = "oauth2"` and follow [the Outlook steps](#outlook-hotmail-live-and-msn).

**`AADSTS16000` when opening the Azure portal.** Your Microsoft account has no Azure directory yet. Create the free Azure account (step 1 of the Outlook section), then try again.

**`unauthorized_client` or `invalid_client` during `binbag login`.** **Allow public client flows** isn't set to **Yes** on your app registration, or it was saved for the wrong account type. Fix it under **Authentication** and log in again.

**`Your saved sign-in has expired` or `invalid_grant`.** You changed your Microsoft password or removed binbag's access. Run `binbag login` again.

**`Could not find a trash folder`.** Run `binbag folders` to see what your server calls things, then set `destination` or check the folder exists.

**The browser page says binbag has stopped.** The terminal running `binbag serve` was closed or stopped. Run it again; the page reconnects by itself.

**Your mailbox is still full after a clean-up.** Moved mail still counts until you empty your Bin. Do that in your mail app once you're sure.

## Try it without an account

```bash
binbag demo
```

Scores a made-up mailbox and writes `binbag.demo.html`, so you can see how it judges things before pointing it at anything real.

## Tests

```bash
pip install -e '.[dev]'
pytest
```

`tests/test_rules.py`, `tests/test_safety.py` and `tests/test_mailbox.py` are the ones that matter. They check that receipts, booking confirmations, security codes, flagged mail and anyone you've emailed survive even when every junk signal fires at once, and that nothing in the code can erase mail. If those fail, binbag is dangerous, not just broken.

## What it deliberately doesn't do

It doesn't read message contents, use AI, delete anything permanently, run in the background, or send your mail anywhere. It's a tool you run, read and act on.

## Licence

MIT.
