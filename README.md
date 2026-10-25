# wa-transfer-doctor

Find the message or chat that makes a WhatsApp transfer from Android to iPhone get stuck.

Your chats are checked on your own computer. Nothing is uploaded, not to a server and not to an AI model. If you want help reading the results, `--redact` produces a report with no message text and masked phone numbers, so you can share that instead of your chats.

```
python3 -I wa_transfer_doctor.py msgstore.db.crypt15
```

---

## Contents

- [The problem](#the-problem)
- [Privacy: why this runs locally](#privacy-why-this-runs-locally)
- [How it works](#how-it-works)
- [What you need](#what-you-need)
- [Step 1: Make a backup you can decrypt](#step-1-make-a-backup-you-can-decrypt)
- [Step 2: Copy the backup to your computer](#step-2-copy-the-backup-to-your-computer)
- [Step 3: Install](#step-3-install)
- [Step 4: Run it](#step-4-run-it)
- [Step 5: Read the report](#step-5-read-the-report)
- [Step 6: Fix it on the phone](#step-6-fix-it-on-the-phone)
- [Step 7: Back up again and retry the transfer](#step-7-back-up-again-and-retry-the-transfer)
- [Command-line options](#command-line-options)
- [Output files](#output-files)
- [Getting help without sharing your chats (`--redact`)](#getting-help-without-sharing-your-chats---redact)
- [Troubleshooting and FAQ](#troubleshooting-and-faq)
- [Privacy and security notes](#privacy-and-security-notes)
- [For AI agents](#for-ai-agents)
- [Development](#development)
- [Credits](#credits)
- [Disclaimer](#disclaimer)
- [License](#license)

---

## The problem

You are moving from an Android phone to an iPhone. You use Apple's **Move to iOS** app, or WhatsApp's own "Move chats to iOS" option. The WhatsApp part of the transfer starts, then stops at a percentage and never moves again. You try again and it stops at the **same percentage**.

When the stall point is always the same, the usual cause is one bad record in the WhatsApp message database. The transfer reaches it, fails to convert it, and hangs. Common culprits:

- A text message that has no text. This is a message that never finished arriving or got damaged. In the app it often shows a red "!" or "Waiting for this message".
- A broken view-once photo, video, or voice message.
- One chat that is very large. One reported case was a single chat with about 280,000 messages and 8 GB of media, which stalled at 27%.

WhatsApp doesn't tell you which message is the problem. This tool finds the candidates and tells you where they are (which chat, what date, and the messages around them) so you can delete them on the Android phone and try again.

The method comes from [this gist by rkharsan](https://gist.github.com/rkharsan/2aa3c56b9aa1107439dd8881f680abe3) and the people who commented on it. This tool automates it and adds a few more checks.

---

## Privacy: why this runs locally

Your WhatsApp database holds every message you have sent or received. A common shortcut is to decrypt it and upload it to an AI chat service or an online "fixer" so it can find the problem. That hands your whole message history, and everyone else's messages to you, to a third party.

This tool avoids that:

- **It runs on your computer.** It makes no network connections. Once it is installed you can turn Wi-Fi off and run it to check.
- **Your original files are never changed.** The backup is decrypted into a private temporary folder. The copy is opened read-only and deleted at the end, unless you ask to keep it.
- **You decide what to share.** The report stays on your computer. If you want someone (a friend, a support forum, or an AI assistant) to help you read it, run with `--redact`. That version leaves out message text and hides phone numbers and group names.

---

## How it works

1. **Decrypt.** WhatsApp backups are encrypted (`.crypt12`, `.crypt14`, `.crypt15`). The tool calls `wadecrypt` from [wa-crypt-tools](https://github.com/ElDavoo/wa-crypt-tools) with your key and writes a decrypted copy to a temporary folder only you can read. If you pass a file that is already decrypted, this step is skipped.
2. **Open read-only.** The decrypted copy is a normal SQLite database. It is opened in read-only, immutable mode, so SQLite never writes to it.
3. **Run checks.** A set of SQL queries looks for the known causes (listed below).
4. **Report.** Results are printed and saved as `report.txt`, `summary.json`, and CSV files in `./wa_report`.
5. **Clean up.** The temporary decrypted copy is deleted.

### The checks

| # | Check id | Status if found | What it looks for |
|---|---|---|---|
| 1 | `integrity` | problem | Damage to the database file itself (SQLite `quick_check`, or `integrity_check` with `--full-integrity`). |
| 2 | `empty_text` | **problem** | Text messages with no text. **This is the main check and the most common cause.** |
| 3 | `media_without_record` | suspect | Photo, video, voice, document, sticker or view-once messages with no media record. |
| 4 | `orphan_messages` | suspect | Messages that belong to a chat that no longer exists. |
| 5 | `missing_sender` | suspect | Messages whose sender record is missing. |
| 6 | `bad_timestamps` | suspect | Messages with a missing, zero, or future date. |
| 7 | `big_chats` | suspect | Chats over 100,000 messages or 2 GB of media (both limits can be changed). |

"Problem" means the thing the transfer most likely chokes on. "Suspect" means worth looking at if fixing the problems doesn't help.

#### 1. Database integrity

If SQLite reports damage, the file itself is corrupted, not just one message. That's rare. If you see it, make a fresh backup and run the tool again before doing anything else. If it still shows damage, the problem may be on the phone's storage.

#### 2. Text messages with no text (`empty_text`)

A normal text message always has text. A text message with nothing in it usually means the message never finished arriving, failed to decrypt, or got damaged. In WhatsApp these often show a red exclamation mark or "Waiting for this message. This may take a while." This is the cause in most of the success stories under the original gist.

The tool ignores internal placeholder rows that every WhatsApp database has (for example message `_id 1`, which belongs to no chat). Those are normal and match the gist's query, which confused some people who ran it by hand.

For each suspect message the report shows the chat, the date and time, who sent it, and the 3 messages before and after it. Use these to scroll to it on the phone.

**What to do:** delete these messages on the Android phone. See [Step 6](#step-6-fix-it-on-the-phone).

#### 3. Media or view-once messages without a media record (`media_without_record`)

Every photo, video, voice note, document, GIF or sticker message should have a matching media record. One person's transfer was blocked by a single broken view-once photo. View-once messages are listed first.

**What to do:** if check 2 is clean, or fixing it didn't help, delete these messages too.

#### 4. Messages from chats that no longer exist (`orphan_messages`)

These are messages left behind in the database after a chat was deleted. You **can't** see them in the app, so you can't delete them from the phone. The report groups them by the missing chat id and shows their date range and types.

They are listed so you know they exist. In practice, fix checks 2, 3 and 7 first. If the transfer still fails and these are the only findings left, see [the FAQ](#the-only-findings-left-are-orphan-messages).

#### 5. Messages with a missing sender (`missing_sender`)

A message points to a contact record that doesn't exist. This is usually harmless, but it is a sign of database inconsistency. Low priority.

#### 6. Missing, zero, or future dates (`bad_timestamps`)

A message with no date, a date of zero (1 January 1970), or a date in the future. These can confuse a conversion process. If the chat is visible in the app, these messages usually show up at the very top or bottom of the chat.

#### 7. Huge chats (`big_chats`)

The report lists the largest chats by message count, with their media count and media size. Chats over `--big-messages` (default 100,000) messages or `--big-media-gb` (default 2 GB) of media are flagged as huge.

This check comes from a comment under the original gist: no corrupt messages were found, and the cause was one chat with about 280,000 messages and 8 GB of media. Deleting that chat fixed the transfer.

**What to do:** only if the other fixes didn't help. First try deleting that chat's media (Settings > Storage and data > Manage storage, pick the chat, delete large files). If that's not enough, export the chat (open the chat > ⋮ > More > Export chat) and then delete it.

The media size comes from what WhatsApp has recorded in the database. Files you already deleted from the phone may still be counted.

---

## What you need

- The Android phone with WhatsApp (the old phone).
- A computer: macOS, Linux or Windows.
- A USB cable, or another way to copy a file from the phone to the computer.
- Python 3.8 or newer. macOS and most Linux systems already have it. On Windows, install it from [python.org](https://www.python.org/downloads/) or the Microsoft Store.
- About 15 to 30 minutes.

You don't need to root the phone, as long as you use the 64-digit key method in Step 1.

---

## Step 1: Make a backup you can decrypt

WhatsApp keeps a local backup of your chats on the phone, encrypted. To decrypt it on your computer you need the key.

The simplest way is to turn on **end-to-end encrypted backup with a 64-digit key**. Backups made this way are `.crypt15` files, and the 64-digit key you write down is all you need to decrypt them.

On the Android phone:

1. Open WhatsApp.
2. Go to **Settings > Chats > Chat backup > End-to-end encrypted backup**.
3. Tap **Turn on**.
4. Choose **Use 64-digit encryption key instead** (don't choose a password).
5. Write the key down or save it in your password manager. Keep it private: anyone with the key and the backup file can read your chats.
6. Finish the setup.
7. Back on the Chat backup screen, tap **Back up**. Wait until it finishes.

This also changes your Google Drive backup to end-to-end encrypted. You can turn it off again after you're done.

### Why not use the `.crypt14` files I already have?

Older or non-end-to-end backups are `.crypt14` (or `.crypt12`). Those are encrypted with a key file that WhatsApp keeps in its private app folder (`/data/data/com.whatsapp/files/key`). Android doesn't let you read that folder unless the phone is rooted. If you already have that key file, you can use it with `--key /path/to/key`. Otherwise, use the 64-digit method above to get a `.crypt15` file.

---

## Step 2: Copy the backup to your computer

The backup is in this folder on the phone:

```
Android/media/com.whatsapp/WhatsApp/Databases/
```

For **WhatsApp Business**:

```
Android/media/com.whatsapp.w4b/WhatsApp Business/Databases/
```

You need the file called **`msgstore.db.crypt15`**. That's the latest full backup. Files with a date in the name (`msgstore-2026-01-01.1.db.crypt15`) are older backups. Files with `increment` in the name are partial backups and aren't needed.

Pick one way to copy it:

### Option A: USB file transfer (no extra tools)

1. Connect the phone to the computer with a USB cable.
2. On the phone, tap the USB notification and choose **File transfer**.
3. Windows: open the phone in File Explorer. Linux: open it in your file manager. macOS: install [Android File Transfer](https://www.android.com/filetransfer/) or [OpenMTP](https://openmtp.ganeshrvel.com/), because Finder can't open Android phones.
4. Go to the folder above and copy `msgstore.db.crypt15` to your computer, for example into a new folder called `whatsapp-backup`.

### Option B: adb (for people who already use it)

1. On the phone, turn on Developer options and USB debugging.
2. Connect the phone and run:

```
adb devices
adb pull /sdcard/Android/media/com.whatsapp/WhatsApp/Databases/msgstore.db.crypt15 .
```

### Option C: phone file manager

Open the phone's Files app, find the folder above, and share or move the file to the computer however you normally do. Avoid uploading it to a cloud service if you can. The file is encrypted, but a direct copy is better.

---

## Step 3: Install

Pick one.

### Option A: install as a command (recommended)

With [uv](https://docs.astral.sh/uv/):

```
uv tool install git+https://github.com/faloppad/wa-transfer-doctor
```

Or with [pipx](https://pipx.pypa.io/):

```
pipx install git+https://github.com/faloppad/wa-transfer-doctor
```

This installs the `wa-transfer-doctor` command and its one dependency, [wa-crypt-tools](https://github.com/ElDavoo/wa-crypt-tools), which does the decryption.

### Option B: run the script directly

```
git clone https://github.com/faloppad/wa-transfer-doctor
cd wa-transfer-doctor
```

The script uses only Python's standard library. To decrypt backups it also needs `wadecrypt`, so install wa-crypt-tools separately:

```
uv tool install wa-crypt-tools
# or
pipx install wa-crypt-tools
```

If you already have a decrypted `.db` file, you don't need wa-crypt-tools.

---

## Step 4: Run it

If you installed it as a command:

```
wa-transfer-doctor path/to/msgstore.db.crypt15
```

If you are running the script:

```
python3 -I wa_transfer_doctor.py path/to/msgstore.db.crypt15
```

On Windows, use `py -I wa_transfer_doctor.py ...` if `python3` isn't found.

You'll be asked for the key:

```
Backup key (64 hex digits) or key file path:
```

Type or paste the 64-digit key and press Enter. Nothing appears while you type, which is normal. Spaces in the key are fine.

The `-I` flag makes Python ignore files in the current folder and environment variables. It's a good habit when you run a script near files you didn't create.

Other ways to run it:

```
# a .crypt14 / .crypt12 backup with the key file from a rooted phone
wa-transfer-doctor msgstore.db.crypt14 --key path/to/key

# a database you already decrypted
wa-transfer-doctor decrypted.db

# a report you can share safely
wa-transfer-doctor msgstore.db.crypt15 --redact

# put the report somewhere else
wa-transfer-doctor msgstore.db.crypt15 --out ~/Desktop/wa_report
```

A large database (several hundred thousand messages) usually takes under a minute.

---

## Step 5: Read the report

The report prints on screen and is saved to `wa_report/report.txt`. Here is an excerpt from a synthetic test database (the phone numbers and messages are fake). The full report, run without `--redact`, is in [docs/sample-report.txt](docs/sample-report.txt):

```
==============================================================================
2. TEXT MESSAGES WITH NO TEXT (main check)
==============================================================================
(Ignored 1 internal placeholder row(s) with no chat, e.g. _id 1; these are normal.)
PROBLEM: 2 empty text message(s). Find each one in its chat (it often
shows a red '!' or 'Waiting for this message'), delete it, back up again and retry.

  #302  chat: Synthetic Family Group  |  2023-11-15 03:15:20  |  from them  |  text
        2023-11-15 03:08:20  me    message 295 about zebracake
        2023-11-15 03:11:20  them  message 298 about zebracake
        2023-11-15 03:14:20  me    message 301 about zebracake
     >> 2023-11-15 03:15:20  them  [EMPTY TEXT]   <-- suspect
        2023-11-15 03:25:20  them  message 312 about zebracake
        2023-11-15 03:28:20  me    message 315 about zebracake
        2023-11-15 03:31:20  them  message 318 about zebracake

  #309  chat: +39300000002  |  2023-11-15 03:22:20  |  from them  |  text
        ...

  -> full list in empty_text_messages.csv
```

How to read it:

- `#302` is the message's internal id. You won't see it in the app; it's there for reference.
- `chat:` is the chat. For a one-to-one chat it's the phone number (`+39300000002`). For a group it's the group name.
- The date and time are in your computer's time zone.
- `from me` / `from them` tells you who sent it.
- The lines above and below the `>>` line are the neighbouring messages in that chat. Look for them in the app to find the right spot.

At the end there is a **SUMMARY** that lists the checks that found something and says where to start:

```
5 check(s) found something: empty_text, media_without_record, orphan_messages, missing_sender, bad_timestamps.
Start with empty_text, then media_without_record, then the huge chats.
```

Work in this order:

1. Check 2 (`empty_text`): fix these first.
2. Check 3 (`media_without_record`): view-once messages first.
3. Check 6 (`bad_timestamps`), if the chat is visible in the app.
4. Check 7 (`big_chats`): only if the transfer still fails.
5. Checks 4 and 5: there is usually nothing to do from the phone.

---

## Step 6: Fix it on the phone

Do this on the **Android** phone, with WhatsApp open.

### Find the message

1. Open the chat named in the report.
2. Search for one of the neighbouring messages: open the chat, tap ⋮ > **Search**, and type a few words from a line just above or below the suspect. Or scroll to the date.
3. The bad message is usually easy to spot: a red "!", "Waiting for this message", an empty bubble, or a photo that won't load.

### Delete it

- **One message:** long-press it > trash icon > **Delete for me**.
- **Several bad messages in a small chat you don't need:** it's often quicker to delete the whole chat. Export it first if you want a copy: open the chat > ⋮ > More > **Export chat**. Then go back to the chat list, long-press the chat > trash icon.
- **Bad messages in a large or important chat:** delete just those messages, one at a time. Don't delete the whole chat.

You can't see orphan messages (check 4) in the app, so there is nothing to delete for those.

### Before you continue

Make a note of what you deleted. If the transfer still fails, run the tool again on a fresh backup to confirm the messages are gone.

---

## Step 7: Back up again and retry the transfer

1. On the Android phone: **Settings > Chats > Chat backup > Back up**. This writes a new local backup without the deleted messages.
2. Optional: copy the new `msgstore.db.crypt15` to your computer and run the tool again. The fixed problems should be gone.
3. Retry the transfer to the iPhone (reset the iPhone and use Move to iOS, or use WhatsApp's transfer option, whichever you used before).

If it gets past the old stall point, it worked. If it stops at a **different** percentage, there is probably a second bad message. Run the tool again on a fresh backup and repeat.

After the transfer, you can delete the backup copy from your computer and the `wa_report` folder.

---

## Command-line options

```
wa-transfer-doctor DB [options]
```

| Option | Default | What it does |
|---|---|---|
| `DB` | (required) | `msgstore.db.crypt15`, `.crypt14`, `.crypt12`, or an already decrypted SQLite file. |
| `--key KEY` | ask | The 64-digit key (for `.crypt15`) or the path to a key file. If you leave it out you are asked for it, and what you type isn't shown. |
| `--out DIR` | `./wa_report` | Where to write the report. The folder is created so only your user can read it. |
| `--redact` | off | Leave out message text and mask phone numbers and group names in all output. Use this when you want to share the report. |
| `--top N` | 15 | How many chats to list in the size ranking. |
| `--context N` | 3 | How many messages to show before and after each suspect. |
| `--max-examples N` | 50 | How many suspects to print per check, and the most `items` per check in `summary.json`. The CSV files always have all of them. |
| `--big-messages N` | 100000 | A chat with at least this many messages is flagged as huge. |
| `--big-media-gb N` | 2 | A chat with at least this many GB of media is flagged as huge. |
| `--full-integrity` | off | Run SQLite's slower, complete `integrity_check` instead of `quick_check`. |
| `--keep-decrypted` | off | Keep the decrypted database in the report folder. It holds every chat in plain text. Delete it when you are done. Can't be combined with `--redact`. |
| `--version` | | Print the version and exit. |
| `-h`, `--help` | | Show help. |

Exit codes:

| Code | Meaning |
|---|---|
| 0 | The checks ran. This doesn't mean nothing was found; see the report or `summary.json`. |
| 1 | Error (file not found, wrong key, not a WhatsApp database, `wadecrypt` missing), or interrupted with Ctrl-C. |
| 2 | Wrong command-line arguments. |

---

## Output files

Everything goes into the `--out` folder (`./wa_report` by default). CSV files are only written when there is something in them; CSV files left in the folder by an earlier run are deleted first.

| File | Contents |
|---|---|
| `report.txt` | The full report, same as what's printed on screen. |
| `summary.json` | The same results in a structured form, for scripts and AI agents. |
| `empty_text_messages.csv` | Every text message with no text. |
| `media_without_record.csv` | Every media or view-once message with no media record. |
| `orphan_messages.csv` | Every message whose chat no longer exists. |
| `bad_timestamps.csv` | Every message with a missing, zero, or future date. |
| `chat_sizes.csv` | Every chat with message count, media count and media size. |
| `decrypted_msgstore.db` | Only with `--keep-decrypted`. The plain-text database. |

Without `--redact`, `report.txt` and the CSV files contain phone numbers, group names and short snippets of messages. Treat them as private.

### `summary.json`

Shortened example from the synthetic test database, run with `--redact`. The full file is in [docs/sample-summary.json](docs/sample-summary.json).

```json
{
  "tool": "wa-transfer-doctor",
  "version": "0.1.0",
  "generated_at": "2026-10-25T09:07:45",
  "source_file": "msgstore.db",
  "redacted": true,
  "stats": {
    "messages": 339,
    "chats": 3,
    "first_message": "2023-11-14 22:15:20",
    "last_message": "2023-11-15 03:52:20",
    "types": {"text": 289, "image": 49, "view-once image": 1}
  },
  "checks": [
    {"id": "integrity", "title": "SQLite integrity", "status": "ok", "count": 0, "items": []},
    {"id": "empty_text", "title": "Text messages with no text", "status": "problem", "count": 2,
     "items": [
       {"message_id": 302, "chat_row_id": 3, "chat": "group #3",
        "time": "2023-11-15 03:15:20", "from_me": false, "type": "text"}
     ]},
    ...
    {"id": "big_chats", "title": "Largest chats", "status": "ok", "count": 0,
     "items": [
       {"chat_row_id": 2, "chat": "+39••••••002", "messages": 113,
        "media_items": 16, "media_bytes": 4000000, "huge": false}
     ]}
  ],
  "findings": 5
}
```

- `status` is one of `ok`, `problem`, `suspect`, `skipped`.
- `count` is the number of matching messages. For `big_chats` it's the number of chats flagged as huge. For `integrity` it's the number of problems SQLite reported (at most 100).
- `items` holds the details, at most `--max-examples` per check. Message checks: `message_id`, `chat_row_id`, `chat`, `time`, `from_me`, `type`, plus `text` for orphan messages and `raw_timestamp` for bad timestamps. `big_chats`: `chat_row_id`, `chat`, `messages`, `media_items`, `media_bytes` and `huge`, for the chats flagged as huge, or for the largest chats if none is flagged. `integrity`: one `message` per problem. Labels and text are redacted when `redacted` is `true`.
- `findings` is the number of checks whose status is `problem` or `suspect`.

---

## Getting help without sharing your chats (`--redact`)

If you'd like someone to look at the results with you, run:

```
wa-transfer-doctor msgstore.db.crypt15 --redact --out wa_report_redacted
```

In this report:

| Without `--redact` | With `--redact` |
|---|---|
| `See you tomorrow at 8!` | `[text, 22 chars]` |
| `+31600000542` | `+31••••••542` (first 2 and last 3 digits kept) |
| `Family group` | `group #2` |
| other chat names | `chat #17` |

Dates, message types, message lengths, counts, sizes and internal ids stay, because those are what's needed to find the problem. The file name of the backup also stays. You can share `report.txt` or `summary.json` from that folder with a person or paste it into an AI assistant. They can tell you which chat and date to look at, and you match it up with the unredacted report on your own computer.

Even redacted, the report shows when you messaged a masked number and how often. If that matters to you, read it before you share it.

---

## Troubleshooting and FAQ

### "Decryption failed"

- For `.crypt15`: check the 64-digit key. It must be the key that was active when **this** backup was made. If you turned end-to-end backup off and on again, the key changed. Make a new backup with the current key.
- For `.crypt14` / `.crypt12`: you need the key file from `/data/data/com.whatsapp/files/key`, which requires root. Use the 64-digit method in [Step 1](#step-1-make-a-backup-you-can-decrypt) instead.
- Make sure the file copied completely. Compare its size with the one on the phone.

### "wadecrypt not found"

wa-crypt-tools isn't installed, or its folder isn't on your `PATH`.

```
uv tool install wa-crypt-tools
# or
pipx install wa-crypt-tools
```

Then open a new terminal. With uv you may need `uv tool update-shell`; with pipx, `pipx ensurepath`.

If you installed wa-transfer-doctor with uv or pipx, wa-crypt-tools came with it and should be found automatically.

### "This does not look like a current WhatsApp msgstore"

You passed the wrong file. Use `msgstore.db.crypt15`, not `wa.db`, `axolotl.db`, `chatsettings.db`, or a media file. Very old WhatsApp versions use a different database layout that isn't supported.

### Nothing was found

That happens. Some commenters on the original gist found no bad messages. Things to try:

- Run with `--full-integrity`.
- Look at the `big_chats` section even if nothing is flagged. A chat much bigger than the rest is still worth trying. You can lower the limits, for example `--big-messages 50000 --big-media-gb 1`.
- Make sure you checked the **latest** backup (`msgstore.db.crypt15`, not a dated one) and that it was made just before.
- Check the basics: enough free space on the iPhone, both phones on the same Wi-Fi and plugged in, latest WhatsApp on both, screen kept on during the transfer.

### It got further but stopped at a different percentage

There is probably a second bad record. Make a new backup, run the tool again, fix what it finds, and retry. Some people needed two or three rounds.

### The transfer finishes, but older chats are missing

This tool targets transfers that hang. A transfer that completes but drops older history is a different problem. The report can still help: look at `bad_timestamps` and at chats around the date where history stops.

### The only findings left are orphan messages

Orphan messages can't be removed from the app. Most people don't need to do anything about them. If the transfer still fails and nothing else is flagged, the options are limited: clearing all chat history (Settings > Chats > Chat history > Delete all chats) removes them along with everything else, so export important chats first. Editing the database by hand and re-encrypting it is possible but risky, and this tool doesn't do it.

### WhatsApp asks for a verification code on the old phone

That's normal during transfers. Log back in to WhatsApp on the Android phone with the code, then register on the iPhone again when you retry.

### Does this work for WhatsApp Business?

Yes. Copy the backup from `Android/media/com.whatsapp.w4b/WhatsApp Business/Databases/`. The database layout is the same.

### Does this work from iPhone to Android, or for an iPhone backup?

No. It reads Android backups only.

### Will this delete or change anything?

No. It only reads. You do the deleting yourself, in WhatsApp, on the phone.

### macOS notes

- Python 3 is preinstalled, or macOS will offer to install the developer tools when you first run `python3`.
- Finder can't browse Android phones. Use Android File Transfer, OpenMTP, or adb.
- If macOS blocks access to Downloads or Desktop from the terminal, allow it in System Settings > Privacy & Security > Files and Folders.

### Windows notes

- Use `py` instead of `python3` if the latter isn't recognised.
- Paths with spaces need quotes: `wa-transfer-doctor "C:\Users\me\whatsapp backup\msgstore.db.crypt15"`.
- The phone shows up in File Explorer when USB mode is set to File transfer.

### Linux notes

- Most distributions mount Android phones over MTP in the file manager. If not, install `gvfs-mtp` or use adb.
- On distributions that block system-wide pip installs, use uv or pipx as shown above.

---

## Privacy and security notes

- **The decrypted database is your entire chat history in plain text.** The tool keeps it in a private temporary folder and deletes it at the end. With `--keep-decrypted` it stays in the report folder until you delete it.
- **The report contains private data** (phone numbers, group names, message snippets) unless you use `--redact`. Delete the `wa_report` folder when you are done.
- **Your key is as sensitive as your chats.** Anyone with the key and a backup file can read them.
  - Prefer typing it at the prompt. If you pass it with `--key`, it goes into your shell history.
  - The key is passed to `wadecrypt` as a command-line argument, so other users on the same computer could see it in the process list for the few seconds decryption takes. On a personal computer this isn't a concern; on a shared one, keep it in mind.
  - Don't paste the key into a chat, an email, or an AI assistant.
- **No network access.** The tool doesn't connect to anything. You can check by turning Wi-Fi off before you run it.
- When you're done, delete the backup copy on your computer. You can also turn off end-to-end encrypted backup on the phone if you don't want it.

---

## For AI agents

This section is for coding agents and AI assistants helping someone use this tool. The same rules are in [AGENTS.md](AGENTS.md).

The point of this tool is that the user's messages are never read by an AI model. Keep it that way.

**Rules**

1. Never open, read, query, copy or upload the backup (`*.crypt*`), a decrypted database, or `--keep-decrypted` output. Don't run `sqlite3` on them. Don't write your own scripts against them.
2. Never ask for the backup key in chat. Let the user run the command themselves and type the key at the prompt. If you run the command for them, leave out `--key` so they type it into the terminal.
3. Run with `--redact`, and read only `summary.json` or `report.txt` from the redacted output folder.
4. Don't read the unredacted report or CSV files. If you need more detail, ask the user to look at their copy.
5. Don't quote message text back to the user from any source.

**Commands**

```
wa-transfer-doctor path/to/msgstore.db.crypt15 --redact --out wa_report_redacted
# or
python3 -I wa_transfer_doctor.py path/to/msgstore.db.crypt15 --redact --out wa_report_redacted
```

Then read `wa_report_redacted/summary.json`.

**Turning results into instructions**

| Check id | If not `ok` |
|---|---|
| `integrity` | Ask for a fresh backup and rerun. If it persists, the database file is damaged; nothing in this tool fixes that. |
| `empty_text` | Main fix. For each item, tell the user the chat (as the redacted label), date, time and sender, and ask them to find it in their unredacted `report.txt` and delete it on the phone. Several items in one small chat: suggest exporting and deleting the chat. Items in a chat listed in `big_chats`: delete single messages only. |
| `media_without_record` | Same, view-once items first. Do this after `empty_text`, or together with it. |
| `bad_timestamps` | Delete if the chat is visible on the phone; they usually sit at the top or bottom of the chat. |
| `big_chats` | Only if the transfer still fails after the above. Suggest deleting the chat's media, then exporting and deleting the chat. |
| `orphan_messages`, `missing_sender` | Explain that these can't be fixed from the app and usually don't need to be. |

After fixes: the user backs up again on the phone and retries the transfer. If it stalls at a new percentage, rerun the tool on the new backup.

Exit code 0 means the checks completed, whether or not anything was found. Use `findings` and each check's `status`.

---

## Development

```
git clone https://github.com/faloppad/wa-transfer-doctor
cd wa-transfer-doctor
python3 -m unittest discover -s tests
```

- One file, `wa_transfer_doctor.py`. Standard library only. Python 3.8 or newer.
- Decryption is delegated to `wadecrypt` from wa-crypt-tools. The tool runs it as a subprocess and doesn't import it.
- The tests build a **synthetic** WhatsApp-like database with planted problems and check that each one is found. They don't need wa-crypt-tools or any real backup.
- **Never commit real data**: no backups, no decrypted databases, no reports from real chats, no real phone numbers in examples or tests. The `.gitignore` excludes `*.crypt*`, `*.db`, key files and `wa_report*/`, but check `git status` before you commit.
- WhatsApp changes its schema over time. The tool reads table and column names at runtime and skips what's missing. If a check breaks on a newer database, open an issue with the table and column names (from `PRAGMA table_info`), never with data.

Contributions welcome, especially new checks backed by a real case of a stuck transfer. Describe the case without sharing anyone's messages.

---

## Credits

- The method comes from [rkharsan's gist](https://gist.github.com/rkharsan/2aa3c56b9aa1107439dd8881f680abe3) and the people in its comments who reported what worked for them: empty text messages, a broken view-once photo, and a huge chat.
- Decryption is done by [wa-crypt-tools](https://github.com/ElDavoo/wa-crypt-tools) by ElDavoo.

---

## Disclaimer

This is an unofficial tool. It is not affiliated with, endorsed by, or supported by WhatsApp, Meta, Apple, or Google.

It reads data and never changes your files, but you use it at your own risk. Deleting messages or chats on the phone can't be undone, so export anything you want to keep first.

Use it only on your own WhatsApp data, or with the clear consent of the person it belongs to.

---

## License

MIT. See [LICENSE](LICENSE).
