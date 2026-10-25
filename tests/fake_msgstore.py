"""Build synthetic WhatsApp msgstore databases for the tests. No real data involved."""

import sqlite3

SCHEMA = """
CREATE TABLE jid (_id INTEGER PRIMARY KEY, user TEXT, server TEXT, raw_string TEXT);
CREATE TABLE chat (_id INTEGER PRIMARY KEY, jid_row_id INTEGER, subject TEXT);
CREATE TABLE message (
    _id INTEGER PRIMARY KEY, chat_row_id INTEGER, from_me INTEGER, key_id TEXT,
    sender_jid_row_id INTEGER, status INTEGER, timestamp INTEGER, message_type INTEGER,
    text_data TEXT, sort_id INTEGER
);
CREATE TABLE message_media (
    message_row_id INTEGER PRIMARY KEY, chat_row_id INTEGER, file_path TEXT,
    file_size INTEGER, mime_type TEXT
);
"""

PHONE_A = "31600000001"
PHONE_B = "39300000002"
GROUP_NAME = "Synthetic Family Group"
SECRET_WORD = "zebracake"  # appears in every normal text message, must vanish with --redact

T0 = 1_700_000_000_000  # 2023-11-14, milliseconds


class Builder:
    def __init__(self, path):
        self.con = sqlite3.connect(path)
        self.con.executescript(SCHEMA)
        self.next_id = 2
        self.con.executemany(
            "INSERT INTO jid VALUES (?,?,?,?)",
            [
                (1, PHONE_A, "s.whatsapp.net", f"{PHONE_A}@s.whatsapp.net"),
                (2, PHONE_B, "s.whatsapp.net", f"{PHONE_B}@s.whatsapp.net"),
                (3, "120363000000000001", "g.us", "120363000000000001@g.us"),
            ],
        )
        self.con.executemany(
            "INSERT INTO chat VALUES (?,?,?)", [(1, 1, None), (2, 2, None), (3, 3, GROUP_NAME)]
        )
        # WhatsApp always has this internal placeholder row: _id 1, no chat, no text.
        self.con.execute("INSERT INTO message VALUES (1,-1,0,'-1',NULL,0,0,0,NULL,1)")

    def add(self, chat, msg_type=0, text=None, ts=None, sender=1, from_me=0, media_size=None):
        mid = self.next_id
        self.next_id += 1
        if msg_type == 0 and text is None:
            text = f"message {mid} about {SECRET_WORD}"
        self.con.execute(
            "INSERT INTO message VALUES (?,?,?,?,?,?,?,?,?,?)",
            (mid, chat, from_me, f"k{mid}", sender, 13, T0 + mid * 60_000 if ts is None else ts,
             msg_type, text, mid),
        )
        if media_size is not None:
            self.con.execute(
                "INSERT INTO message_media VALUES (?,?,?,?,?)", (mid, chat, f"Media/{mid}.jpg", media_size, "image/jpeg")
            )
        return mid

    def normal_history(self, n=300):
        for i in range(n):
            chat = (1, 2, 3)[i % 3]
            if i % 7 == 0:
                self.add(chat, msg_type=1, media_size=250_000, from_me=i % 2)
            else:
                self.add(chat, from_me=i % 2)

    def close(self):
        self.con.commit()
        self.con.close()


def build_clean(path):
    b = Builder(path)
    b.normal_history()
    b.close()


def build_broken(path):
    """Returns the ids of the planted problems."""
    b = Builder(path)
    b.normal_history()
    planted = {
        "empty_text": b.add(3, msg_type=0, text=""),
        "empty_text_null": None,
        "view_once": b.add(1, msg_type=42),
        "orphans": [b.add(77), b.add(77, msg_type=1, media_size=10), b.add(78)],
        "missing_sender": b.add(2, sender=999),
        "zero_ts": b.add(2, ts=0),
    }
    # NULL text (not just ""), the classic case from the gist.
    planted["empty_text_null"] = b.next_id
    b.con.execute(
        "INSERT INTO message VALUES (?,?,?,?,?,?,?,?,?,?)",
        (b.next_id, 2, 0, "knull", 2, 13, T0 + b.next_id * 60_000, 0, None, b.next_id),
    )
    b.next_id += 1
    b.normal_history(30)
    b.close()
    return planted
