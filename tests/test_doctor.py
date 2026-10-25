import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fake_msgstore as fake  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / "wa_transfer_doctor.py"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class DoctorTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def run_doctor(self, db, *flags, out_name="report"):
        out = self.tmp / out_name
        proc = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), str(db), "--out", str(out), *flags],
            capture_output=True,
            text=True,
        )
        return proc, out

    def summary(self, out):
        return json.loads((out / "summary.json").read_text(encoding="utf-8"))

    @staticmethod
    def statuses(summary):
        return {c["id"]: c["status"] for c in summary["checks"]}

    @staticmethod
    def check(summary, check_id):
        return next(c for c in summary["checks"] if c["id"] == check_id)


class TestBrokenDatabase(DoctorTestCase):
    def setUp(self):
        super().setUp()
        self.db = self.tmp / "broken.db"
        self.planted = fake.build_broken(self.db)

    def test_finds_planted_problems(self):
        proc, out = self.run_doctor(self.db)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        s = self.summary(out)
        self.assertEqual(
            self.statuses(s),
            {
                "integrity": "ok",
                "empty_text": "problem",
                "media_without_record": "suspect",
                "orphan_messages": "suspect",
                "missing_sender": "suspect",
                "bad_timestamps": "suspect",
                "big_chats": "ok",
            },
        )
        self.assertEqual(s["findings"], 5)
        self.assertEqual(s["tool"], "wa-transfer-doctor")
        self.assertEqual(s["source_file"], "broken.db")
        self.assertFalse(s["redacted"])

        empty_ids = {i["message_id"] for i in self.check(s, "empty_text")["items"]}
        self.assertEqual(empty_ids, {self.planted["empty_text"], self.planted["empty_text_null"]})
        self.assertEqual(
            [i["message_id"] for i in self.check(s, "media_without_record")["items"]], [self.planted["view_once"]]
        )
        self.assertEqual(self.check(s, "orphan_messages")["count"], 3)
        self.assertEqual(self.check(s, "missing_sender")["items"][0]["message_id"], self.planted["missing_sender"])
        self.assertEqual(self.check(s, "bad_timestamps")["items"][0]["raw_timestamp"], 0)

        for name in ("report.txt", "empty_text_messages.csv", "media_without_record.csv",
                     "orphan_messages.csv", "bad_timestamps.csv", "chat_sizes.csv"):
            self.assertTrue((out / name).is_file(), name)

    def test_placeholder_row_is_ignored(self):
        _, out = self.run_doctor(self.db)
        s = self.summary(out)
        ids = {i["message_id"] for c in s["checks"] for i in c["items"] if "message_id" in i}
        self.assertNotIn(1, ids)
        self.assertIn("Ignored 1 internal placeholder", (out / "report.txt").read_text(encoding="utf-8"))

    def test_input_is_not_modified(self):
        before = sha256(self.db)
        files_before = sorted(p.name for p in self.tmp.iterdir())
        self.run_doctor(self.db)
        self.assertEqual(sha256(self.db), before)
        # No -wal / -shm / journal files appear next to the input.
        files_after = sorted(p.name for p in self.tmp.iterdir() if p.name != "report")
        self.assertEqual(files_after, files_before)

    def test_redact_leaks_nothing(self):
        proc, out = self.run_doctor(self.db, "--redact")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        leaks = [fake.SECRET_WORD, fake.PHONE_A, fake.PHONE_B, fake.GROUP_NAME, "120363000000000001"]
        outputs = {p.name: p.read_text(encoding="utf-8") for p in out.iterdir()}
        outputs["stdout"] = proc.stdout
        for name, content in outputs.items():
            for secret in leaks:
                self.assertNotIn(secret, content, f"{secret!r} leaked into {name}")
        s = self.summary(out)
        self.assertTrue(s["redacted"])
        chats = {i["chat"] for c in s["checks"] for i in c["items"] if "chat" in i}
        self.assertIn("+31" + "•" * 6 + "001", chats)
        self.assertIn("group #3", chats)

    def test_big_chat_threshold(self):
        _, out = self.run_doctor(self.db, "--big-messages", "100")
        s = self.summary(out)
        big = self.check(s, "big_chats")
        self.assertEqual(big["status"], "suspect")
        self.assertTrue(all(i["huge"] for i in big["items"]))
        self.assertGreater(big["count"], 0)

    def test_stale_csv_from_earlier_run_is_removed(self):
        _, out = self.run_doctor(self.db)
        self.assertTrue((out / "empty_text_messages.csv").is_file())
        clean = self.tmp / "clean.db"
        fake.build_clean(clean)
        self.run_doctor(clean, "--redact")
        self.assertFalse((out / "empty_text_messages.csv").exists())
        for p in out.iterdir():
            self.assertNotIn(fake.SECRET_WORD, p.read_text(encoding="utf-8"), p.name)


class TestCleanDatabase(DoctorTestCase):
    def test_clean_db_has_no_findings(self):
        db = self.tmp / "clean.db"
        fake.build_clean(db)
        proc, out = self.run_doctor(db)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        s = self.summary(out)
        self.assertEqual(s["findings"], 0)
        self.assertTrue(all(st == "ok" for st in self.statuses(s).values()))
        self.assertFalse((out / "empty_text_messages.csv").exists())
        self.assertTrue((out / "chat_sizes.csv").exists())


class TestErrors(DoctorTestCase):
    def test_missing_file(self):
        proc, _ = self.run_doctor(self.tmp / "nope.db")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Not found", proc.stderr)

    def test_not_a_whatsapp_db(self):
        import sqlite3

        db = self.tmp / "other.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE foo (x)")
        con.close()
        proc, _ = self.run_doctor(db)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("does not look like", proc.stderr)

    def test_bad_usage(self):
        proc = subprocess.run([sys.executable, "-I", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)

    def test_keep_decrypted_with_redact_is_refused(self):
        proc, _ = self.run_doctor(self.tmp / "x.db", "--redact", "--keep-decrypted")
        self.assertEqual(proc.returncode, 2)

    def test_key_file_path_with_spaces(self):
        sys.path.insert(0, str(SCRIPT.parent))
        import wa_transfer_doctor as doctor

        key = self.tmp / "my keys" / "key"
        key.parent.mkdir()
        key.write_bytes(b"synthetic")
        self.assertEqual(doctor.resolve_key(f"  {key}  "), str(key.resolve()))
        self.assertEqual(doctor.resolve_key("ab cd"), "abcd")

    def test_version(self):
        proc = subprocess.run([sys.executable, "-I", str(SCRIPT), "--version"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("0.1.0", proc.stdout)


if __name__ == "__main__":
    unittest.main()
