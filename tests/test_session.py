"""Session: create, open, save, the inbox, lock, the language, the export — without a window."""
import tempfile
import unittest
from pathlib import Path

from daykeeper.journal import append_inbox, today
from daykeeper.session import Session
from daykeeper.settings import write_settings
from daykeeper.vault import Vault, WrongPassword
from tests.helpers import reset_globals

PW = "session-master-1"


class SessionTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "s.pkv"

    def tearDown(self):
        self.tmp.cleanup()
        reset_globals()

    def test_create_change_save_and_reopen(self):
        s = Session(self.path)
        self.assertFalse(s.exists)
        self.assertFalse(s.open)
        s.unlock(PW)
        self.assertTrue(s.exists)
        self.assertTrue(s.open)
        s.vault.add_entry("One")
        s.touch("vault")
        self.assertEqual(s.rev.of("vault", "notes"), (1, 0))
        s.save()
        self.assertTrue((self.path.parent / "backup").exists(), "a backup is made before every save")
        s.lock()
        self.assertIsNone(s.vault)
        s.save()  # locked: nothing happens
        again = Session(self.path)
        again.unlock(PW)
        self.assertEqual([e.name for e in again.vault.entries], ["One"])

    def test_wrong_password(self):
        Session(self.path).unlock(PW)
        with self.assertRaises(WrongPassword):
            Session(self.path).unlock("not-it")

    def test_inbox_is_filed_and_deleted(self):
        s = Session(self.path)
        self.assertIsNone(s.pull_inbox(), "locked")
        s.unlock(PW)
        self.assertIsNone(s.pull_inbox(), "no inbox")
        append_inbox(s.inbox_path, "pass", ["APP-1", "APP-2"])
        append_inbox(s.inbox_path, "pass", ["APP-1"])  # a duplicate is read but not counted as new
        self.assertEqual(s.pull_inbox(), (2, 3))
        self.assertFalse(s.inbox_path.exists())
        self.assertEqual(Vault.open(self.path, PW).journal.get(today()).section("ok"), ["APP-1", "APP-2"], "saved")

    def test_settings_language_and_links(self):
        write_settings(self.path.parent / "settings.json", {"language": "ru", "jira_url": "https://t.example.com/", "geometry": "1x1"})
        s = Session(self.path)
        self.assertEqual(s.language(), "ru")
        self.assertEqual(s.links().jira_url, "https://t.example.com")
        self.assertTrue(s.write_settings({"language": "en"}))
        self.assertEqual(s.settings()["language"], "en")
        self.assertEqual(s.settings()["geometry"], "1x1", "other keys stay")
        s.settings()["language"] = "xx"
        self.assertEqual(s.settings()["language"], "en", "a copy is handed out")
        self.assertIn(Session(self.path.parent / "fresh" / "v.pkv").language(), ("en", "ru"), "no settings: the system decides")

    def test_export_only_with_the_master_password(self):
        s = Session(self.path)
        s.unlock(PW)
        s.vault.add_entry("Secret")
        out = self.path.parent / "out.txt"
        self.assertFalse(s.export("txt", out, "wrong"))
        self.assertFalse(out.exists(), "a wrong password writes nothing")
        self.assertTrue(s.export("txt", out, PW))
        self.assertIn("Secret", out.read_text(encoding="utf-8"))
        csv = self.path.parent / "out.csv"
        self.assertEqual(s.write_export("csv", csv), 1)
        self.assertTrue(csv.read_bytes().startswith(b"\xef\xbb\xbf"), "Excel wants the BOM")


if __name__ == "__main__":
    unittest.main()
