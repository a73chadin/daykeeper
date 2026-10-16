"""Hardening: forged headers, backups after a password change, CSV formulas, file modes."""
import os
import struct
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from daykeeper.export import csv_cell, export_csv
from daykeeper.vault import HEADER_FMT, HEADER_LEN, MAGIC, BadFile, Entry, Field, Vault, WrongPassword, check_params


def forge(path: Path, iterations: int, memory_kib: int, lanes: int) -> None:
    """Rewrite the Argon2 parameters in a vault header."""
    blob = path.read_bytes()
    _m, salt, _i, _mem, _l, nonce = struct.unpack(HEADER_FMT, blob[:HEADER_LEN])
    path.write_bytes(struct.pack(HEADER_FMT, MAGIC, salt, iterations, memory_kib, lanes, nonce) + blob[HEADER_LEN:])


class ForgedHeaderTest(unittest.TestCase):
    def test_bounds(self):
        check_params(3, 64 * 1024, 4)
        for bad in ((3, 64 * 1024, 0), (0, 64 * 1024, 4), (3, 4 * 1024 * 1024, 4), (10_000, 1024, 1), (3, 16, 4)):
            with self.assertRaises(BadFile, msg=bad):
                check_params(*bad)

    def test_open_refuses_before_deriving_a_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v.pkv"
            Vault.create(path, "master-pass-1")
            for params in ((3, 64 * 1024, 0), (3, 3_000_000, 4), (1_000_000, 64 * 1024, 4)):
                forge(path, *params)
                with mock.patch("daykeeper.vault._derive_key") as derive, self.assertRaises(BadFile):
                    Vault.open(path, "master-pass-1")
                derive.assert_not_called()

    def test_tampered_params_within_bounds_fail_authentication(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v.pkv"
            Vault.create(path, "master-pass-1")
            forge(path, 2, 32 * 1024, 2)  # the header is the AAD, so any change is caught
            with self.assertRaises(WrongPassword):
                Vault.open(path, "master-pass-1")


class BackupsAfterPasswordChangeTest(unittest.TestCase):
    @mock.patch("daykeeper.vault.BACKUP_EVERY", 0)  # a copy on every save, as before the throttle
    def test_backups_follow_the_new_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v.pkv"
            v = Vault.create(path, "old-password-1")
            v.add_entry("one")
            v.save()
            time.sleep(1.1)  # backups are named by the second
            v.add_entry("two")
            v.save()
            backups = Path(tmp) / "backup"
            stale = backups / "v-19990101-000000.pkv"
            other = Vault.create(Path(tmp) / "older" / "x.pkv", "even-older-pw")  # a copy under another password
            stale.write_bytes(other.path.read_bytes())
            loose = Path(tmp) / "vault-before-merge.pkv"
            loose.write_bytes(path.read_bytes())
            foreign = Path(tmp) / "someone-else.pkv"
            foreign.write_bytes(other.path.read_bytes())

            rekeyed, deleted = v.change_password("new-password-1")

            self.assertEqual(deleted, 1)
            self.assertFalse(stale.exists())
            self.assertGreaterEqual(rekeyed, 3)  # two or more backups + the copy next to the vault
            for f in [*backups.glob("*.pkv"), loose]:
                Vault.open(f, "new-password-1")  # opens with the new password
                with self.assertRaises(WrongPassword):
                    Vault.open(f, "old-password-1")
            self.assertTrue(foreign.exists(), "a file under another password next to the vault is never deleted")
            Vault.open(foreign, "even-older-pw")


class CsvFormulaTest(unittest.TestCase):
    def test_formula_like_cells_get_an_apostrophe(self):
        for raw in ("=1+1", "+7", "-x", "@SUM(A1)", "\tx", "\rx"):
            self.assertEqual(csv_cell(raw), "'" + raw)
        for raw in ("", "plain", "a=b", "1-2"):
            self.assertEqual(csv_cell(raw), raw)

    def test_export_escapes_names_values_and_notes(self):
        e = Entry(name="=HYPERLINK(\"http://x\")", fields=[Field("password", "-secret", True)], note="@note")
        text = export_csv([e])
        self.assertIn("'=HYPERLINK", text)
        self.assertIn("'-secret", text)
        self.assertIn("'@note", text)


@unittest.skipUnless(os.name == "posix", "file modes are POSIX-only")
class FileModesTest(unittest.TestCase):
    def test_vault_backup_and_settings_are_owner_only(self):
        from daykeeper.settings import write_settings

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data" / "v.pkv"
            v = Vault.create(path, "master-pass-1")
            v.save()
            write_settings(path.parent / "settings.json", {"a": 1})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual((path.parent / "settings.json").stat().st_mode & 0o777, 0o600)
            for b in (path.parent / "backup").glob("*.pkv"):
                self.assertEqual(b.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()


class OlderFileTest(unittest.TestCase):
    def test_a_version_3_file_opens_with_empty_new_sections(self):
        """A vault written before bookmarks, locks, integrations and routines existed."""
        import json

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v.pkv"
            v = Vault.create(path, "master-pass-1")
            payload = {"version": 3, "groups": [], "entries": [{"name": "Old", "fields": []}], "journal": [], "notes": [{"title": "n", "items": []}]}
            path.write_bytes(v._seal(json.dumps(payload).encode("utf-8")))
            old = Vault.open(path, "master-pass-1")
            self.assertEqual([e.name for e in old.entries], ["Old"])
            self.assertEqual((old.bookmarks.items, old.routines.items, old.integrations.feed), ([], [], []))
            self.assertFalse(old.security.guarded)
            self.assertEqual(old.notes.notes[0].attachments, [])
            old.save()
            self.assertEqual(Vault.open(path, "master-pass-1").entries[0].name, "Old")
