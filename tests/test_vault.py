import unittest
import tempfile
from pathlib import Path

from daykeeper.export import CSV_HEADER, export_csv, export_text
from daykeeper.vault import KINDS, BadFile, Entry, Field, Vault, WrongPassword, generate_password


class VaultTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "v.pkv"

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip(self):
        v = Vault.create(self.path, "master-pass-1")
        e = v.add_entry("GitLab")
        e.fields.append(Field("PAT", "glpat-xyz", secret=True))
        e.note = "port 2222"
        v.save()

        v2 = Vault.open(self.path, "master-pass-1")
        self.assertEqual(len(v2.entries), 1)
        got = v2.entries[0]
        self.assertEqual(got.name, "GitLab")
        self.assertEqual(got.note, "port 2222")
        self.assertEqual([f.key for f in got.fields], ["login", "password", "site", "PAT"])
        self.assertTrue(got.fields[3].secret)
        self.assertEqual(got.fields[3].value, "glpat-xyz")
        self.assertEqual(got.kind, "login")

    def test_wrong_password(self):
        Vault.create(self.path, "master-pass-1")
        with self.assertRaises(WrongPassword):
            Vault.open(self.path, "master-pass-2")

    def test_tampered_file(self):
        Vault.create(self.path, "master-pass-1")
        blob = bytearray(self.path.read_bytes())
        blob[-1] ^= 0xFF
        self.path.write_bytes(bytes(blob))
        with self.assertRaises(WrongPassword):
            Vault.open(self.path, "master-pass-1")

    def test_not_a_vault(self):
        self.path.write_bytes(b"hello")
        with self.assertRaises(BadFile):
            Vault.open(self.path, "x")

    def test_plaintext_not_in_file(self):
        v = Vault.create(self.path, "master-pass-1")
        v.add_entry("SuperSecretServiceName")
        v.save()
        self.assertNotIn(b"SuperSecretServiceName", self.path.read_bytes())

    def test_change_password(self):
        v = Vault.create(self.path, "old-password")
        v.add_entry("A")
        v.change_password("new-password")
        with self.assertRaises(WrongPassword):
            Vault.open(self.path, "old-password")
        self.assertEqual(Vault.open(self.path, "new-password").entries[0].name, "A")

    def test_backup_created(self):
        v = Vault.create(self.path, "master-pass-1")
        v.add_entry("A")
        v.save()
        backups = list((self.path.parent / "backup").glob("v-*.pkv"))
        self.assertEqual(len(backups), 1)

    def test_search(self):
        v = Vault.create(self.path, "master-pass-1")
        a = v.add_entry("Jira")
        a.fields.append(Field("token", "hidden-token", secret=True))
        a.fields.append(Field("url", "jira.example.com"))
        v.add_entry("GitLab")
        self.assertEqual([e.name for e in v.search("")], ["Jira", "GitLab"])  # creation order, not alphabetical
        self.assertEqual([e.name for e in v.search("example")], ["Jira"])
        self.assertEqual(v.search("hidden-token"), [])  # secrets do not take part in search

    def test_kinds_templates_and_roundtrip(self):
        v = Vault.create(self.path, "master-pass-1")
        t = v.add_entry(kind="token")
        self.assertEqual(t.name, "New token")
        self.assertEqual([(f.key, f.secret) for f in t.fields][0], ("token", True))
        v.add_entry(kind="note")
        v.add_entry(kind="nonsense")  # unknown kind → login/password
        v.save()
        kinds = sorted(e.kind for e in Vault.open(self.path, "master-pass-1").entries)
        self.assertEqual(kinds, ["login", "note", "token"])
        self.assertEqual(set(KINDS), {"login", "token", "key", "card", "note", "other"})

    def test_old_file_without_kind(self):
        e = Entry.from_dict({"name": "X", "fields": [], "note": ""})
        self.assertEqual(e.kind, "login")
        self.assertEqual(e.kind_label, "Login and password")

    def test_search_by_kind_label(self):
        v = Vault.create(self.path, "master-pass-1")
        v.add_entry("A", kind="token")
        v.add_entry("B", kind="login")
        self.assertEqual([e.name for e in v.search("api token")], ["A"])

    def test_check_password(self):
        v = Vault.create(self.path, "master-pass-1")
        self.assertTrue(v.check_password("master-pass-1"))
        self.assertFalse(v.check_password("master-pass-2"))
        v.change_password("master-pass-3")
        self.assertTrue(v.check_password("master-pass-3"))
        self.assertFalse(v.check_password("master-pass-1"))

    def _export_fixture(self):
        v = Vault.create(self.path, "master-pass-1")
        g = v.add_entry("GitLab", kind="token")
        g.fields[0].value = "glpat-secret"
        g.fields[1].value = "gitlab.example.com"
        g.note = "port 2222" + chr(10) + "second line"
        g.updated = 0
        a = v.add_entry("Amazon", kind="login")
        a.fields[0].value = "alice"
        a.fields[1].value = "p@ss"
        n = v.add_entry("Empty", kind="note")
        n.updated = 0
        return v

    def test_export_text(self):
        v = self._export_fixture()
        text = export_text(v.entries, now=0)
        self.assertIn("Entries: 3", text)
        self.assertIn("not encrypted", text)
        # secrets are exported in plain text, in list order, with kind, list and note
        self.assertLess(text.index("GitLab"), text.index("Amazon"))
        self.assertIn("Kind: API token · List: Main", text)
        self.assertIn("glpat-secret", text)
        self.assertIn("p@ss", text)
        self.assertIn("    port 2222" + chr(10) + "    second line", text)
        self.assertIn("Empty", text)

    def test_export_csv(self):
        import csv
        import io

        v = self._export_fixture()
        rows = list(csv.reader(io.StringIO(export_csv(v.entries))))
        self.assertEqual(rows[0], CSV_HEADER)
        amazon = [r for r in rows if r[0] == "Amazon"]
        self.assertEqual(amazon[0][:4], ["Amazon", "Login and password", "login", "alice"])
        self.assertEqual(amazon[1][:4], ["Amazon", "Login and password", "password", "p@ss"])
        self.assertEqual(amazon[0][6], "Main")
        gitlab = [r for r in rows if r[0] == "GitLab"]
        self.assertEqual(gitlab[0][4], "port 2222" + chr(10) + "second line")  # the note is on the first row of the entry
        self.assertEqual(gitlab[1][4], "")
        self.assertEqual([r for r in rows if r[0] == "Empty"][0][2:4], ["", ""])  # an entry without fields — one row

    def test_move_entry(self):
        v = Vault.create(self.path, "master-pass-1")
        a, _b, c = v.add_entry("A"), v.add_entry("B"), v.add_entry("C")
        self.assertTrue(v.move_entry(c, -1))
        self.assertEqual([e.name for e in v.search("")], ["A", "C", "B"])
        self.assertFalse(v.move_entry(a, -1))  # already first
        self.assertTrue(v.move_entry(a, +1))
        v.save()
        self.assertEqual([e.name for e in Vault.open(self.path, "master-pass-1").search("")], ["C", "A", "B"])

    def test_groups(self):
        v = Vault.create(self.path, "master-pass-1")
        a, _b = v.add_entry("Ax"), v.add_entry("By")
        work = v.add_group("  Work ")
        self.assertEqual(work, "Work")
        with self.assertRaises(ValueError):
            v.add_group("Work")
        with self.assertRaises(ValueError):
            v.add_group("Main")
        v.set_group(a, "Work")
        c = v.add_entry("Cz", group="Work")
        self.assertEqual([e.name for e in v.search("")], ["By"])  # main
        self.assertEqual([e.name for e in v.search("", "Work")], ["Ax", "Cz"])
        self.assertEqual([e.name for e in v.search("", None)], ["By", "Ax", "Cz"])  # all, in manual order
        self.assertEqual([e.name for e in v.search("ax")], ["Ax"])  # search — across all lists
        self.assertTrue(v.move_entry(c, -1))  # moving — inside its own list
        self.assertEqual([e.name for e in v.search("", "Work")], ["Cz", "Ax"])

        v.rename_group("Work", "Office")
        self.assertEqual(v.groups, ["Office"])
        self.assertEqual(a.group, "Office")
        v.save()
        v2 = Vault.open(self.path, "master-pass-1")
        self.assertEqual(v2.groups, ["Office"])
        self.assertEqual([e.name for e in v2.search("", "Office")], ["Cz", "Ax"])

        v2.remove_group("Office")  # entries are not lost — they go to the main list, to the end
        self.assertEqual(v2.groups, [])
        self.assertEqual([e.name for e in v2.search("")], ["By", "Cz", "Ax"])

    def test_old_file_gets_order_and_groups(self):
        v = Vault.create(self.path, "master-pass-1")
        v.entries = [Entry.from_dict({"name": n}) for n in ("X", "Y", "Z")]
        v.entries[1].group = "Old"  # a list missing from groups
        v._normalize()
        self.assertEqual([e.order for e in v.entries], [0, 1, 2])
        self.assertEqual(v.groups, ["Old"])

    def test_generate_password(self):
        p = generate_password(24)
        self.assertEqual(len(p), 24)
        self.assertNotEqual(p, generate_password(24))

    def test_file_format_v5_keys_round_trip(self):
        """What goes into the file: the eight sections of version 5 with the field key, and
        the keys an older version still reads (`wide` next to `span` on a note, `order`)."""
        import json

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        from daykeeper.vault import read_header

        v = Vault.create(self.path, "master-pass-1")
        v.add_entry("Entry")
        v.notes.add(None, title="Wide", span=2)
        v.bookmarks.add("Site", "example.com")
        blob = v._encrypt()
        header, _salt, _params, nonce = read_header(blob)
        data = json.loads(AESGCM(v._key).decrypt(nonce, blob[len(header):], header))
        self.assertEqual(data["version"], 5)
        self.assertEqual(
            sorted(data), sorted(["version", "field_key", "groups", "entries", "journal", "notes", "bookmarks", "security", "integrations", "routines"])
        )
        note = data["notes"][0]
        self.assertEqual((note["span"], note["wide"]), (2, True))
        self.assertIn("order", data["entries"][0])
        self.assertIn("kind", data["entries"][0])
        self.assertEqual(sorted(data["bookmarks"]), ["groups", "items"])
        for key in ("favicon", "favicon_small", "favicon_small_px", "site_colour", "site_checked"):
            self.assertIn(key, data["bookmarks"]["items"][0])
        back = Vault.open(self.path, "master-pass-1")
        self.assertEqual([e.name for e in back.entries], [])  # not saved yet: the file holds the empty vault
        v.save()
        back = Vault.open(self.path, "master-pass-1")
        self.assertEqual([e.name for e in back.entries], ["Entry"])
        self.assertEqual(back.notes.notes[0].span, 2)


if __name__ == "__main__":
    unittest.main()
