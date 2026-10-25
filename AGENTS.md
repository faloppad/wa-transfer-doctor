# AGENTS.md

Guide for AI assistants and coding agents. There are two cases: helping a user run the tool, and changing the code. The full user guide is in [README.md](README.md).

## Why this tool exists

It finds the WhatsApp message or chat that makes an Android-to-iPhone transfer hang. It does this **without the user's messages ever reaching an AI model or any online service**. Your job is to keep that true.

## Hard rules when helping a user

1. **Never read the data yourself.** Don't open, read, `cat`, `head`, `strings`, query, copy, or upload:
   - backup files (`msgstore*.db.crypt12`, `.crypt14`, `.crypt15`)
   - decrypted databases (`*.db`, including `decrypted_msgstore.db` from `--keep-decrypted`)
   - the unredacted report folder (`report.txt` and CSVs produced without `--redact`)

   Don't run `sqlite3` on them and don't write your own scripts that read them.
2. **Never ask for the backup key in chat**, and don't put it in a command you run. Leave out `--key`. The tool then asks for the key in the terminal, where nothing is echoed. If you can't hand the terminal to the user, give them the command to run themselves.
3. **Always use `--redact`** when you will read the output, and write it to a separate folder.
4. **Read only** `summary.json` (preferred) or `report.txt` from the redacted folder.
5. **Don't quote message text** from any source. Refer to messages by redacted chat label, date, time, sender (`me`/`them`) and type.
6. Never use `--keep-decrypted` unless the user asks for it, and remind them to delete the file afterwards. The tool refuses to combine it with `--redact`.

## Commands

Installed:

```
wa-transfer-doctor PATH/msgstore.db.crypt15 --redact --out wa_report_redacted
```

From a clone (the script is stdlib only; decryption needs `wadecrypt` from wa-crypt-tools on `PATH`):

```
python3 -I wa_transfer_doctor.py PATH/msgstore.db.crypt15 --redact --out wa_report_redacted
```

Already decrypted database (no key needed):

```
wa-transfer-doctor PATH/decrypted.db --redact --out wa_report_redacted
```

Useful options: `--top N`, `--context N`, `--max-examples N`, `--big-messages N`, `--big-media-gb N`, `--full-integrity`. Run `--help` for the full list.

Install, if needed:

```
uv tool install git+https://github.com/faloppad/wa-transfer-doctor
# or: pipx install git+https://github.com/faloppad/wa-transfer-doctor
# for the bare script: uv tool install wa-crypt-tools
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Checks completed. Says nothing about findings; read `summary.json`. |
| 1 | Error: file missing, wrong key, not a WhatsApp DB, `wadecrypt` not found, or interrupted. The message is on stderr. |
| 2 | Bad command-line arguments. |

## `summary.json`

```
{
  "tool": "wa-transfer-doctor",
  "version": "0.1.0",
  "generated_at": "<ISO timestamp>",
  "source_file": "<file name>",
  "redacted": true,
  "stats": {
    "messages": <int>, "chats": <int or null>,
    "first_message": "<YYYY-MM-DD HH:MM:SS>", "last_message": "<...>",
    "types": {"<type label>": <count>, ...}
  },
  "checks": [
    {"id": "<check id>", "title": "<str>", "status": "ok|problem|suspect|skipped",
     "count": <int>, "items": [ ... ]}
  ],
  "findings": <number of checks with status problem or suspect>
}
```

Before using the contents, check that `redacted` is `true`. If it is `false`, stop reading and rerun with `--redact`.

Message items have `message_id`, `chat_row_id`, `chat`, `time`, `from_me`, `type` (plus `text` for `orphan_messages` and `raw_timestamp` for `bad_timestamps`). `big_chats` items have `chat_row_id`, `chat`, `messages`, `media_items`, `media_bytes`, `huge`; they list only the huge chats when there are any, otherwise the largest chats. Its `count` is the number of chats with `huge: true`. Every check has at most `--max-examples` (default 50) items. Redacted labels look like `+31••••••542`, `group #2`, `chat #17`, `missing chat #77`. Text is replaced with `[<type>, N chars]`, e.g. `[text, 22 chars]`. A full redacted example is in [docs/sample-summary.json](docs/sample-summary.json).

## Turning check results into instructions

Checks run in the order `integrity`, `empty_text`, `media_without_record`, `orphan_messages`, `missing_sender`, `bad_timestamps`, `big_chats`. Work through the results in this priority order instead:

| Priority | Check id | Typical status | Tell the user |
|---|---|---|---|
| 1 | `empty_text` | problem | The most likely cause. For each item: chat label, date/time, sender. Ask them to find it in **their own** unredacted `report.txt` (it shows neighbouring messages) and delete it on the Android phone (long-press > Delete for me). Many items in one small chat: suggest Export chat, then delete the chat. Items in a chat that also shows up in `big_chats`: delete single messages, not the chat. |
| 2 | `media_without_record` | suspect | Same procedure. View-once items first; one known case was a broken view-once photo. |
| 3 | `bad_timestamps` | suspect | Delete if visible in the app; usually at the top or bottom of the chat. |
| 4 | `big_chats` | suspect | Only if the transfer still fails after 1 to 3. First delete the chat's media (Settings > Storage and data > Manage storage), then if needed export and delete the chat. Known case: ~280k messages, 8 GB. |
| 5 | `integrity` | problem | Rare. Ask for a fresh backup and rerun. If it persists, the database file is damaged; this tool can't fix that. |
| 6 | `orphan_messages` | suspect | Messages left over from deleted chats. Not visible in the app, can't be deleted there, usually harmless. Say so; don't send the user hunting for them. |
| 7 | `missing_sender` | suspect | Low priority, usually harmless, not fixable in the app. |

`skipped` means the database lacks the tables that check needs. Mention it, don't treat it as a finding.

After the user deletes things:

1. Back up again on the phone: Settings > Chats > Chat backup > Back up.
2. Optionally copy the new `msgstore.db.crypt15` and rerun the tool to confirm.
3. Retry the transfer. If it stalls at a **different** percentage, there is another bad record: repeat.

If nothing is found, point them to "Nothing was found" in the README's FAQ.

## Common user problems

- **`.crypt14` file and no key file:** the key file is root-only. Have them turn on end-to-end encrypted backup with a 64-digit key (Settings > Chats > Chat backup > End-to-end encrypted backup), back up, and use the new `msgstore.db.crypt15`.
- **Decryption failed:** wrong or outdated key, incomplete copy, or wrong file.
- **`wadecrypt` not found:** `uv tool install wa-crypt-tools` or `pipx install wa-crypt-tools`, then a new terminal.
- **Backup location:** `Android/media/com.whatsapp/WhatsApp/Databases/msgstore.db.crypt15`. Business: `Android/media/com.whatsapp.w4b/WhatsApp Business/Databases/`.

## Conventions for contributors

- Single module `wa_transfer_doctor.py`, console script `wa-transfer-doctor`.
- **Standard library only**, Python 3.8+. No `match`, no `X | Y` type unions or `list[int]`-style generics at runtime, no `dict | dict`, no `str.removeprefix`.
- Decryption only through the `wadecrypt` subprocess. Don't import wa-crypt-tools.
- Open databases read-only and immutable (`?mode=ro&immutable=1`). Never write to user files.
- No network access, ever. No telemetry, no update checks.
- WhatsApp's schema changes. Read table and column names at runtime and degrade to `skipped` instead of crashing.
- Every user-facing string that can contain message text, phone numbers or chat names must go through the redaction path, so `--redact` output stays clean. Add a test for any new output.
- Keep `summary.json` backward compatible: add fields, don't rename or remove them. Bump the version if you have to.
- Tests: `python3 -m unittest discover -s tests`. Fixtures are **synthetic only**, built in code. Use obviously fake numbers (`31600000001`) and text.
- **Never commit real data**: backups, decrypted databases, real reports, real phone numbers or names. Check `git status` before committing.
- Update README.md (options table, checks, output files) and this file when you change flags, checks or the JSON shape.
