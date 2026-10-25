#!/usr/bin/env python3
"""
wa-transfer-doctor: find the messages and chats that make WhatsApp's
Android -> iPhone transfer get stuck. Everything runs on your own computer.

Checks:
  1. SQLite-level corruption (PRAGMA quick_check / integrity_check)
  2. "Text" messages with no text (the usual culprit; in the app they show a
     red "!" or "Waiting for this message")
  3. Media / view-once messages with no media record
  4. Messages pointing to a chat that no longer exists
  5. Messages whose sender does not exist
  6. Missing, zero or future timestamps
  7. Huge chats (message count and recorded media size)

The input file is never modified. Encrypted backups (.crypt12/14/15) are
decrypted with `wadecrypt` (from wa-crypt-tools) into a private temporary
folder that is deleted at the end unless --keep-decrypted is given.

Based on https://gist.github.com/rkharsan/2aa3c56b9aa1107439dd8881f680abe3
and the comments under it. See README.md for the full guide.
"""

import argparse
import csv
import datetime as dt
import getpass
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

__version__ = "0.1.0"

# Best-effort labels for message.message_type. WhatsApp does not document these.
TYPE_LABELS = {
    0: "text",
    1: "image",
    2: "audio/voice",
    3: "video",
    4: "contact card",
    5: "location",
    7: "system",
    8: "call",
    9: "document",
    10: "missed call",
    13: "GIF",
    14: "contacts list",
    15: "deleted",
    16: "live location",
    20: "sticker",
    42: "view-once image",
    43: "view-once video",
    46: "poll",
    64: "deleted by admin",
    66: "poll",
    82: "view-once voice",
}
MEDIA_TYPES = (1, 2, 3, 9, 13, 20, 42, 43, 82)
VIEW_ONCE_TYPES = (42, 43, 82)
PHONE_SERVERS = ("s.whatsapp.net", "c.us")
SQLITE_HEADER = b"SQLite format 3\x00"
CSV_FILES = (
    "empty_text_messages.csv",
    "media_without_record.csv",
    "orphan_messages.csv",
    "bad_timestamps.csv",
    "chat_sizes.csv",
)


class DoctorError(Exception):
    """An error meant for the user: printed without a traceback."""


# --------------------------------------------------------------------------- formatting


def type_label(msg_type):
    return TYPE_LABELS.get(msg_type, f"type {msg_type}")


def fmt_ts(ms):
    if ms is None:
        return "?"
    try:
        return dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return f"invalid({ms})"


def fmt_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def mask_phone(digits):
    if len(digits) < 6:
        return "+" + "•" * len(digits)
    return "+" + digits[:2] + "•" * (len(digits) - 5) + digits[-3:]


class Redactor:
    """Turns chat names and message text into display strings, optionally redacted."""

    def __init__(self, enabled):
        self.enabled = enabled

    def chat(self, row):
        chat_id = row["chat_id"]
        if chat_id is None:
            return f"missing chat #{row['chat_row_id']}"
        if row["subject"]:
            return f"group #{chat_id}" if self.enabled else row["subject"]
        user, server, raw = row["jid_user"], row["jid_server"], row["jid_raw"]
        if user and server in PHONE_SERVERS:
            return mask_phone(user) if self.enabled else "+" + user
        if self.enabled or not (raw or user):
            return f"chat #{chat_id}"
        return raw or user

    def text(self, text, msg_type, width=70):
        if text:
            if self.enabled:
                return f"[{type_label(msg_type)}, {len(text)} chars]"
            flat = " ".join(str(text).split())
            return flat if len(flat) <= width else flat[: width - 1] + "…"
        return "[EMPTY TEXT]" if msg_type == 0 else f"[{type_label(msg_type)}]"


# --------------------------------------------------------------------------- decryption


def is_sqlite(path):
    with open(path, "rb") as f:
        return f.read(16) == SQLITE_HEADER


def find_wadecrypt():
    # Next to our own interpreter first: `uv tool install` / `pipx install` put
    # wa-crypt-tools' scripts there without adding them to PATH.
    bin_dir = Path(sys.executable).parent
    for name in ("wadecrypt", "wadecrypt.exe"):
        candidate = bin_dir / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which("wadecrypt")


def decrypt(src, key, workdir):
    exe = find_wadecrypt()
    if not exe:
        raise DoctorError(
            "wadecrypt not found. Install wa-crypt-tools first:\n"
            "  uv tool install wa-crypt-tools    (or: pipx install wa-crypt-tools)"
        )
    out = workdir / "decrypted.db"
    print(f"Decrypting {src.name} into a private temporary folder ...")
    result = subprocess.run([exe, key, str(src), str(out)], cwd=workdir)
    if result.returncode != 0 or not out.exists() or not is_sqlite(out):
        raise DoctorError(
            "Decryption failed.\n"
            "  .crypt15: use the 64-digit end-to-end backup key (WhatsApp > Settings > Chats >\n"
            "            Chat backup > End-to-end encrypted backup).\n"
            "  .crypt14/.crypt12: needs the 'key' file from /data/data/com.whatsapp/files/key,\n"
            "            which can only be read on a rooted phone. The easier route is to turn on\n"
            "            end-to-end encrypted backup with a 64-digit key and back up again,\n"
            "            which produces a .crypt15 file."
        )
    return out


# --------------------------------------------------------------------------- report


class Report:
    """Collects the human-readable report, the CSV files and the JSON summary."""

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.lines = []
        self.checks = []
        self.stats = {}
        self.section = 0
        # CSVs are only written when non-empty, so remove ones left by an earlier run
        # (possibly unredacted) instead of mixing them into this report.
        for name in CSV_FILES:
            (out_dir / name).unlink(missing_ok=True)

    def line(self, text=""):
        print(text)
        self.lines.append(text)

    def heading(self, title):
        self.section += 1
        self.line("")
        self.line("=" * 78)
        self.line(f"{self.section}. {title}")
        self.line("=" * 78)

    def add_check(self, check_id, title, status, count=0, items=()):
        self.checks.append(
            {"id": check_id, "title": title, "status": status, "count": count, "items": list(items)}
        )

    @property
    def findings(self):
        return sum(1 for c in self.checks if c["status"] in ("problem", "suspect"))

    def write_csv(self, name, records):
        if not records:
            return
        path = self.out_dir / name
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)
        self.line(f"  -> full list in {path.name}")

    def save(self, source_name, redacted):
        txt = self.out_dir / "report.txt"
        txt.write_text("\n".join(self.lines) + "\n", encoding="utf-8")
        summary = {
            "tool": "wa-transfer-doctor",
            "version": __version__,
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "source_file": source_name,
            "redacted": redacted,
            "stats": self.stats,
            "checks": self.checks,
            "findings": self.findings,
        }
        (self.out_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return txt


# --------------------------------------------------------------------------- analysis


class Doctor:
    def __init__(self, con, report, redactor, opts):
        self.con = con
        self.r = report
        self.red = redactor
        self.opts = opts
        self.tables = {
            row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
        }
        if "message" not in self.tables or "text_data" not in self.cols("message"):
            raise DoctorError(
                "This does not look like a current WhatsApp msgstore (no message.text_data).\n"
                f"Tables found: {', '.join(sorted(self.tables)[:40]) or 'none'}"
            )
        self.has_chat = "chat" in self.tables
        self.has_jid = "jid" in self.tables
        self.has_media = "message_media" in self.tables
        self.order_col = "sort_id" if "sort_id" in self.cols("message") else "timestamp"
        self._build_chat_sql()

    def cols(self, table):
        if table not in self.tables:
            return set()
        return {row[1] for row in self.con.execute(f"PRAGMA table_info('{table}')")}

    def q(self, sql, params=()):
        return self.con.execute(sql, params).fetchall()

    def _build_chat_sql(self):
        """Chat columns (aliases c = chat, j = jid) selected once and labelled in Python."""
        chat_cols = self.cols("chat")
        join_jid = self.has_chat and self.has_jid and "jid_row_id" in chat_cols
        jid_cols = self.cols("jid") if join_jid else set()

        def col(alias, name, available):
            return f"{alias}.{name}" if name in available else "NULL"

        self.chat_fields = (
            f"{'c._id' if self.has_chat else 'NULL'} AS chat_id, "
            f"{col('c', 'subject', chat_cols)} AS subject, "
            f"{col('j', 'user', jid_cols)} AS jid_user, "
            f"{col('j', 'server', jid_cols)} AS jid_server, "
            f"{col('j', 'raw_string', jid_cols)} AS jid_raw"
        )
        joins = ""
        if self.has_chat:
            joins += "LEFT JOIN chat c ON c._id = {chat_ref} "
            if join_jid:
                joins += "LEFT JOIN jid j ON j._id = c.jid_row_id "
        self.chat_joins = joins

    # -- message helpers -------------------------------------------------------

    def messages(self, where):
        joins = self.chat_joins.format(chat_ref="m.chat_row_id")
        return self.q(
            f"SELECT m._id AS id, m.chat_row_id, m.timestamp, m.from_me, m.message_type, "
            f"m.text_data, m.{self.order_col} AS ord, {self.chat_fields} "
            f"FROM message m {joins}WHERE {where} ORDER BY m.timestamp"
        )

    def record(self, row):
        return {
            "message_id": row["id"],
            "chat_row_id": row["chat_row_id"],
            "chat": self.red.chat(row),
            "time": fmt_ts(row["timestamp"]),
            "from_me": bool(row["from_me"]),
            "type": type_label(row["message_type"]),
        }

    def context(self, row):
        oc, n = self.order_col, self.opts.context
        sql = (
            f"SELECT timestamp, from_me, message_type, text_data FROM message "
            f"WHERE chat_row_id = ? AND {oc} {{op}} ? ORDER BY {oc} {{direction}} LIMIT ?"
        )
        args = (row["chat_row_id"], row["ord"], n)
        before = self.q(sql.format(op="<", direction="DESC"), args)[::-1]
        after = self.q(sql.format(op=">", direction="ASC"), args)
        return before, after

    def msg_line(self, row, marker="        ", suffix=""):
        who = "me  " if row["from_me"] else "them"
        text = self.red.text(row["text_data"], row["message_type"])
        return f"{marker}{fmt_ts(row['timestamp'])}  {who}  {text}{suffix}"

    def print_with_context(self, row):
        who = "me" if row["from_me"] else "them"
        self.r.line(
            f"  #{row['id']}  chat: {self.red.chat(row)}  |  {fmt_ts(row['timestamp'])}  |  "
            f"from {who}  |  {type_label(row['message_type'])}"
        )
        before, after = self.context(row)
        for b in before:
            self.r.line(self.msg_line(b))
        self.r.line(self.msg_line(row, marker="     >> ", suffix="   <-- suspect"))
        for a in after:
            self.r.line(self.msg_line(a))
        self.r.line("")

    # -- checks ----------------------------------------------------------------

    def run(self):
        self.overview()
        self.integrity()
        self.empty_text()
        self.media_without_record()
        self.orphan_messages()
        self.missing_sender()
        self.bad_timestamps()
        self.big_chats()

    def overview(self):
        self.r.line("")
        self.r.line("OVERVIEW")
        self.r.line("-" * 78)
        n_msg = self.q("SELECT COUNT(*) FROM message")[0][0]
        n_chat = self.q("SELECT COUNT(*) FROM chat")[0][0] if self.has_chat else None
        first, last = self.q("SELECT MIN(timestamp), MAX(timestamp) FROM message WHERE timestamp > 0")[0]
        types = self.q("SELECT message_type, COUNT(*) FROM message GROUP BY 1 ORDER BY 2 DESC")
        self.r.line(f"Messages: {n_msg:,}   Chats: {n_chat if n_chat is not None else '?'}")
        self.r.line(f"Date range: {fmt_ts(first)}  ->  {fmt_ts(last)}")
        self.r.line("Messages by type:")
        type_counts = {}
        for t, n in types:
            label = type_label(t)
            type_counts[label] = type_counts.get(label, 0) + n
            self.r.line(f"  {label:<22} {n:>10,}")
        self.r.stats = {
            "messages": n_msg,
            "chats": n_chat,
            "first_message": fmt_ts(first),
            "last_message": fmt_ts(last),
            "types": type_counts,
        }

    def integrity(self):
        pragma = "integrity_check" if self.opts.full_integrity else "quick_check"
        title = "SQLite integrity"
        self.r.heading(f"{title.upper()} ({pragma})")
        rows = [r[0] for r in self.q(f"PRAGMA {pragma}(100)")]
        if rows == ["ok"]:
            self.r.line("OK: no structural corruption in the database file.")
            self.r.add_check("integrity", title, "ok")
            return
        self.r.line("PROBLEM: SQLite reports structural corruption:")
        for msg in rows[: self.opts.max_examples]:
            self.r.line(f"  {msg}")
        self.r.add_check(
            "integrity", title, "problem", len(rows), [{"message": m} for m in rows[: self.opts.max_examples]]
        )

    def _message_check(self, check_id, title, status, rows, intro, csv_name):
        """Shared output for checks that produce a list of suspect messages."""
        if not rows:
            self.r.line("OK: none found.")
            self.r.add_check(check_id, title, "ok")
            return
        for text in intro:
            self.r.line(text)
        self.r.line("")
        for row in rows[: self.opts.max_examples]:
            self.print_with_context(row)
        records = [self.record(row) for row in rows]
        self.r.write_csv(csv_name, records)
        self.r.add_check(check_id, title, status, len(rows), records[: self.opts.max_examples])

    def empty_text(self):
        title = "Text messages with no text"
        self.r.heading(title.upper() + " (main check)")
        base = "m.message_type = 0 AND (m.text_data IS NULL OR TRIM(m.text_data) = '')"
        placeholders = self.q(f"SELECT COUNT(*) FROM message m WHERE {base} AND m.chat_row_id <= 0")[0][0]
        if placeholders:
            self.r.line(
                f"(Ignored {placeholders} internal placeholder row(s) with no chat, e.g. _id 1; "
                "these are normal.)"
            )
        rows = self.messages(base + " AND m.chat_row_id > 0")
        self._message_check(
            "empty_text",
            title,
            "problem",
            rows,
            [
                f"PROBLEM: {len(rows)} empty text message(s). Find each one in its chat (it often",
                "shows a red '!' or 'Waiting for this message'), delete it, back up again and retry.",
            ],
            "empty_text_messages.csv",
        )

    def media_without_record(self):
        title = "Media / view-once messages without a media record"
        self.r.heading(title.upper())
        if not self.has_media:
            self.r.line("Skipped: no message_media table.")
            self.r.add_check("media_without_record", title, "skipped")
            return
        types = ",".join(map(str, MEDIA_TYPES))
        rows = self.messages(
            f"m.message_type IN ({types}) AND m.chat_row_id > 0 "
            "AND NOT EXISTS (SELECT 1 FROM message_media mm WHERE mm.message_row_id = m._id)"
        )
        # View-once first: a broken view-once photo is a documented culprit.
        rows.sort(key=lambda r: (r["message_type"] not in VIEW_ONCE_TYPES, r["timestamp"] or 0))
        view_once = sum(1 for r in rows if r["message_type"] in VIEW_ONCE_TYPES)
        self._message_check(
            "media_without_record",
            title,
            "suspect",
            rows,
            [
                f"SUSPECT: {len(rows)} media message(s) have no media record ({view_once} view-once).",
                "Weaker lead than empty text messages, but a broken view-once photo has blocked transfers.",
            ],
            "media_without_record.csv",
        )

    def orphan_messages(self):
        title = "Messages pointing to a chat that does not exist"
        self.r.heading(title.upper())
        if not self.has_chat:
            self.r.line("Skipped: no chat table.")
            self.r.add_check("orphan_messages", title, "skipped")
            return
        rows = self.messages(
            "m.chat_row_id > 0 AND NOT EXISTS (SELECT 1 FROM chat c2 WHERE c2._id = m.chat_row_id)"
        )
        if not rows:
            self.r.line("OK: none found.")
            self.r.add_check("orphan_messages", title, "ok")
            return
        self.r.line(f"SUSPECT: {len(rows):,} message(s) belong to a chat that no longer exists. They are")
        self.r.line("invisible in the app, so they cannot be deleted from the phone. Usually leftovers")
        self.r.line("from deleted chats; look at them only if the other fixes do not help.\n")
        groups = {}
        for row in rows:
            groups.setdefault(row["chat_row_id"], []).append(row)
        for chat_id, members in groups.items():
            types = {}
            for m in members:
                label = type_label(m["message_type"])
                types[label] = types.get(label, 0) + 1
            type_str = ", ".join(f"{n} {t}" for t, n in sorted(types.items(), key=lambda x: -x[1]))
            self.r.line(
                f"  missing chat #{chat_id}: {len(members)} msg(s), "
                f"{fmt_ts(members[0]['timestamp'])} -> {fmt_ts(members[-1]['timestamp'])}  ({type_str})"
            )
            for m in members[:3]:
                self.r.line(self.msg_line(m, marker="      "))
        records = []
        for row in rows:
            rec = self.record(row)
            rec["text"] = self.red.text(row["text_data"], row["message_type"], width=200)
            records.append(rec)
        self.r.write_csv("orphan_messages.csv", records)
        self.r.add_check("orphan_messages", title, "suspect", len(rows), records[: self.opts.max_examples])

    def missing_sender(self):
        title = "Messages whose sender does not exist"
        self.r.heading(title.upper())
        if not (self.has_jid and "sender_jid_row_id" in self.cols("message")):
            self.r.line("Skipped: no sender information in this database.")
            self.r.add_check("missing_sender", title, "skipped")
            return
        rows = self.messages(
            "m.sender_jid_row_id > 0 AND NOT EXISTS (SELECT 1 FROM jid j2 WHERE j2._id = m.sender_jid_row_id)"
        )
        if not rows:
            self.r.line("OK: none found.")
            self.r.add_check("missing_sender", title, "ok")
            return
        self.r.line(f"SUSPECT: {len(rows):,} message(s) have a sender that does not exist.")
        for row in rows[:10]:
            self.r.line(self.msg_line(row, marker=f"  #{row['id']}  {self.red.chat(row)}  "))
        records = [self.record(row) for row in rows]
        self.r.add_check("missing_sender", title, "suspect", len(rows), records[: self.opts.max_examples])

    def bad_timestamps(self):
        title = "Missing, zero or future timestamps"
        self.r.heading(title.upper())
        future = int((dt.datetime.now() + dt.timedelta(days=2)).timestamp() * 1000)
        rows = self.messages(
            f"m.chat_row_id > 0 AND (m.timestamp IS NULL OR m.timestamp <= 0 OR m.timestamp > {future})"
        )
        if not rows:
            self.r.line("OK: none found.")
            self.r.add_check("bad_timestamps", title, "ok")
            return
        self.r.line(f"SUSPECT: {len(rows):,} message(s) have a missing, zero or future timestamp.")
        records = []
        for row in rows:
            rec = self.record(row)
            rec["raw_timestamp"] = row["timestamp"]
            records.append(rec)
        for row, rec in list(zip(rows, records))[:10]:
            self.r.line(
                f"  #{rec['message_id']}  chat: {rec['chat']}  ts={rec['raw_timestamp']}  {rec['type']}  "
                f"{self.red.text(row['text_data'], row['message_type'])}"
            )
        self.r.write_csv("bad_timestamps.csv", records)
        self.r.add_check("bad_timestamps", title, "suspect", len(rows), records[: self.opts.max_examples])

    def big_chats(self):
        title = "Largest chats"
        self.r.heading(f"{title.upper()} (top {self.opts.top})")
        if not self.has_chat:
            self.r.line("Skipped: no chat table.")
            self.r.add_check("big_chats", title, "skipped")
            return
        media_join, media_cols = "", "0 AS n_media, 0 AS bytes"
        if self.has_media and {"chat_row_id", "file_size"} <= self.cols("message_media"):
            media_join = (
                "LEFT JOIN (SELECT chat_row_id, COUNT(*) AS n_media, SUM(COALESCE(file_size, 0)) AS bytes "
                "FROM message_media GROUP BY chat_row_id) mm ON mm.chat_row_id = c._id "
            )
            media_cols = "COALESCE(mm.n_media, 0) AS n_media, COALESCE(mm.bytes, 0) AS bytes"
        joins = self.chat_joins.format(chat_ref="mc.chat_row_id").replace("LEFT JOIN chat", "JOIN chat", 1)
        rows = self.q(
            f"SELECT mc.chat_row_id, mc.n, {media_cols}, {self.chat_fields} "
            "FROM (SELECT chat_row_id, COUNT(*) AS n FROM message WHERE chat_row_id > 0 "
            "GROUP BY chat_row_id) mc "
            f"{joins}{media_join}ORDER BY mc.n DESC"
        )
        if not rows:
            self.r.line("No chats found.")
            self.r.add_check("big_chats", title, "ok")
            return

        big_bytes = self.opts.big_media_gb * 1024**3

        def is_huge(row):
            return row["n"] >= self.opts.big_messages or row["bytes"] >= big_bytes

        total_msgs = sum(r["n"] for r in rows)
        total_bytes = sum(r["bytes"] for r in rows)
        shown = {r["chat_row_id"] for r in rows[: self.opts.top]}
        shown |= {r["chat_row_id"] for r in sorted(rows, key=lambda r: r["bytes"], reverse=True)[:5]}

        self.r.line(f"{'chat':<40} {'messages':>10} {'% of all':>8} {'media':>8} {'media size':>11}")
        for row in rows:
            if row["chat_row_id"] not in shown:
                continue
            pct = 100 * row["n"] / total_msgs if total_msgs else 0
            flag = "  <-- huge" if is_huge(row) else ""
            self.r.line(
                f"{self.red.chat(row)[:40]:<40} {row['n']:>10,} {pct:>7.1f}% {row['n_media']:>8,} "
                f"{fmt_bytes(row['bytes']):>11}{flag}"
            )
        self.r.line(f"\nTotal media size recorded in the database: {fmt_bytes(total_bytes)}")
        self.r.line("(This is what WhatsApp has recorded, not necessarily what is still on the phone.)")

        records = [
            {
                "chat_row_id": row["chat_row_id"],
                "chat": self.red.chat(row),
                "messages": row["n"],
                "media_items": row["n_media"],
                "media_bytes": row["bytes"],
                "huge": is_huge(row),
            }
            for row in rows
        ]
        huge = [rec for rec in records if rec["huge"]]
        if huge:
            self.r.line(
                f"\nSUSPECT: {len(huge)} chat(s) exceed {self.opts.big_messages:,} messages or "
                f"{fmt_bytes(big_bytes)} of media.\nIf the other checks are clean, try clearing media from, "
                "or exporting and then deleting, the largest chat."
            )
        self.r.write_csv("chat_sizes.csv", records)
        self.r.add_check(
            "big_chats",
            title,
            "suspect" if huge else "ok",
            len(huge),
            (huge or records)[: self.opts.max_examples],
        )


# --------------------------------------------------------------------------- main


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        prog="wa-transfer-doctor",
        description="Find the messages and chats that make WhatsApp's Android -> iPhone transfer "
        "get stuck. Runs fully offline; the input file is never modified.",
        epilog="Full guide: README.md. Example: wa-transfer-doctor msgstore.db.crypt15 --redact",
    )
    ap.add_argument("db", type=Path, help="msgstore.db.crypt12/14/15, or an already decrypted SQLite file")
    ap.add_argument("--key", help="64-digit hex key (crypt15) or path to a key file; prompted for if omitted")
    ap.add_argument("--out", type=Path, default=Path("wa_report"), help="report folder (default: ./wa_report)")
    ap.add_argument("--redact", action="store_true", help="hide message text, phone numbers and group names")
    ap.add_argument("--top", type=int, default=15, help="chats to list in the size ranking (default: 15)")
    ap.add_argument("--context", type=int, default=3, help="messages shown before/after a suspect (default: 3)")
    ap.add_argument("--max-examples", type=int, default=50, help="suspects printed per check (default: 50)")
    ap.add_argument("--big-messages", type=int, default=100_000, help="flag chats with this many messages")
    ap.add_argument("--big-media-gb", type=float, default=2.0, help="flag chats with this much media (GB)")
    ap.add_argument("--full-integrity", action="store_true", help="run the slower PRAGMA integrity_check")
    ap.add_argument("--keep-decrypted", action="store_true", help="keep the decrypted copy in the report folder")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = ap.parse_args(argv)
    if args.redact and args.keep_decrypted:
        ap.error("--keep-decrypted cannot be combined with --redact (the redacted folder is meant to be shared)")
    return args


def resolve_key(arg_key):
    key = arg_key or getpass.getpass("Backup key (64 hex digits) or key file path: ")
    key = key.strip()
    path = Path(key).expanduser()
    if key and path.is_file():
        return str(path.resolve())
    key = key.replace(" ", "")
    if not key:
        raise DoctorError("A key is needed to decrypt an encrypted backup.")
    return key


def run(args):
    src = args.db.expanduser().resolve()
    if not src.is_file():
        raise DoctorError(f"Not found: {src}")
    out_dir = args.out.expanduser().resolve()
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    tmp = None
    try:
        if is_sqlite(src):
            db_path = src
        else:
            key = resolve_key(args.key)
            tmp = Path(tempfile.mkdtemp(prefix="wa_decrypt_"))
            db_path = decrypt(src, key, tmp)

        # Read-only + immutable: SQLite never writes to the file, not even -wal/-shm files.
        con = sqlite3.connect(db_path.as_uri() + "?mode=ro&immutable=1", uri=True)
        con.row_factory = sqlite3.Row
        try:
            report = Report(out_dir)
            report.line(
                f"wa-transfer-doctor {__version__}  |  {src.name}  |  {dt.datetime.now():%Y-%m-%d %H:%M}"
                + ("  |  redacted" if args.redact else "")
            )
            Doctor(con, report, Redactor(args.redact), args).run()
        except sqlite3.DatabaseError as e:
            raise DoctorError(f"Could not read the database: {e}") from e
        finally:
            con.close()

        report.line("")
        report.line("=" * 78)
        report.line("SUMMARY")
        report.line("=" * 78)
        if report.findings:
            flagged = ", ".join(c["id"] for c in report.checks if c["status"] in ("problem", "suspect"))
            report.line(f"{report.findings} check(s) found something: {flagged}.")
            report.line("Start with empty_text, then media_without_record, then the huge chats.")
        else:
            report.line("No obvious problems found. The cause may be elsewhere (storage, app versions, Wi-Fi).")
        txt = report.save(src.name, args.redact)
        print(f"\nReport saved to {txt}")
        print(f"Summary saved to {out_dir / 'summary.json'}")

        if tmp and args.keep_decrypted:
            kept = out_dir / "decrypted_msgstore.db"
            shutil.move(str(db_path), str(kept))
            print(f"Decrypted copy kept at {kept} (every chat in plain text; delete it when done).")
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
            if tmp.exists():
                print(f"warning: could not delete the decrypted copy in {tmp}; delete it by hand.", file=sys.stderr)


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def main(argv=None):
    # Group names can contain characters the console encoding lacks (e.g. Windows + redirect).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    # Closing the terminal or `kill` should still delete the decrypted copy.
    for name in ("SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is not None and signal.getsignal(sig) == signal.SIG_DFL:
            signal.signal(sig, _interrupt)
    args = parse_args(argv)
    try:
        run(args)
    except DoctorError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
