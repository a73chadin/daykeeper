"""Fixes from the October security and speed review: the GitLab token stays on its
server, saves do not overwrite another window's save, backups survive autosaves,
hostile icons and broken shortcuts are refused, exports are owner-only."""
import os
import struct
import tempfile
import time
import unittest
import zlib
from pathlib import Path
from unittest import mock

from daykeeper import siteicon
from daykeeper import vault as vault_module
from daykeeper.gitlab import Event, GitLabClient, GitLabError, web_url
from daykeeper.routines import read_shortcut, resolve_shortcut
from daykeeper.session import Session
from daykeeper.vault import ChangedOnDisk, Vault, backups_to_drop


class TokenStaysHomeTest(unittest.TestCase):
    def test_plain_http_is_refused_except_on_this_machine(self):
        client = GitLabClient("http://gitlab.example.com", "tok")
        with mock.patch("http.client.HTTPConnection") as conn:
            with self.assertRaises(GitLabError):
                client._request(client.base + "/user", {"PRIVATE-TOKEN": "tok"})
            conn.assert_not_called()

    def test_a_redirect_to_another_host_does_not_carry_the_token(self):
        client = GitLabClient("https://gitlab.example.com", "tok")
        first = mock.Mock(status=302, read=lambda: b"", getheader=lambda _h: "https://evil.example.net/api/v4/user")
        sent = []
        conn = mock.Mock(request=lambda *a, **k: sent.append(k["headers"]), getresponse=lambda: first)
        with mock.patch("http.client.HTTPSConnection", return_value=conn), self.assertRaises(GitLabError):
            client._request(client.base + "/user", {"PRIVATE-TOKEN": "tok"})
        self.assertEqual(len(sent), 1, "the second host was never asked")

    def test_a_redirect_on_the_same_host_is_followed(self):
        client = GitLabClient("https://gitlab.example.com", "tok")
        answers = iter([
            mock.Mock(status=301, read=lambda: b"", getheader=lambda _h: "/api/v4/user?x=1"),
            mock.Mock(status=200, read=lambda: b"{}", getheader=lambda _h: None),
        ])
        conn = mock.Mock(getresponse=lambda: next(answers))
        with mock.patch("http.client.HTTPSConnection", return_value=conn):
            self.assertEqual(client._request(client.base + "/user", {}), (200, b"{}"))

    def test_only_web_links_are_opened(self):
        self.assertEqual(web_url("https://gl/x/-/merge_requests/1"), "https://gl/x/-/merge_requests/1")
        self.assertEqual(web_url("file:///C:/Windows/System32/calc.exe"), "")
        self.assertEqual(web_url("javascript:alert(1)"), "")
        self.assertEqual(Event.from_dict({"url": "ms-settings:"}).url, "")


class SaveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "v.pkv"
        Vault.create(self.path, "master-pass-1")

    def test_an_unchanged_vault_is_not_written(self):
        v = Vault.open(self.path, "master-pass-1")
        before = self.path.stat().st_mtime_ns
        self.assertFalse(v.save())
        self.assertEqual(self.path.stat().st_mtime_ns, before)
        v.add_entry("one")
        self.assertTrue(v.save())

    def test_a_save_by_another_window_is_not_overwritten(self):
        mine = Vault.open(self.path, "master-pass-1")
        other = Vault.open(self.path, "master-pass-1")
        other.add_entry("from the other window")
        other.save()
        mine.add_entry("from this window")
        with self.assertRaises(ChangedOnDisk):
            mine.save()
        self.assertEqual([e.name for e in Vault.open(self.path, "master-pass-1").entries], ["from the other window"])

    def test_backups_are_not_washed_out_by_autosaves(self):
        v = Vault.open(self.path, "master-pass-1")
        for i in range(30):
            v.add_entry(f"e{i}")
            v.save()
        self.assertEqual(len(list((self.path.parent / "backup").glob("v-*.pkv"))), 1, "one copy per BACKUP_EVERY")

    def test_old_backups_keep_one_copy_a_day(self):
        now = time.mktime((2026, 10, 15, 12, 0, 0, 0, 0, -1))
        names = [f"v-202610{d:02d}-{h:02d}0000.pkv" for d in range(1, 16) for h in (9, 18)]
        copies = [Path(n) for n in sorted(names)]
        dropped = backups_to_drop(copies, now)
        kept = [c for c in copies if c not in dropped]
        self.assertTrue(set(copies[-vault_module.BACKUP_KEEP:]) <= set(kept))
        self.assertIn(Path("v-20261002-180000.pkv"), kept, "the last copy of a day within BACKUP_DAYS")
        self.assertNotIn(Path("v-20261002-090000.pkv"), kept)
        self.assertNotIn(Path("v-20261001-180000.pkv"), kept, "older than BACKUP_DAYS")


@unittest.skipUnless(os.name == "posix", "file modes are POSIX-only")
class ExportModeTest(unittest.TestCase):
    def test_a_clear_text_export_is_owner_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(Path(tmp) / "v.pkv")
            s.unlock("master-pass-1")
            target = Path(tmp) / "out.csv"
            s.write_export("csv", target)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)


class HostileIconTest(unittest.TestCase):
    @staticmethod
    def png_with(width, height, idat):
        def chunk(kind, body):
            return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")

    def test_a_zip_bomb_is_not_unpacked(self):
        bomb = zlib.compress(b"\x00" * (64 * 1024 * 1024), 9)  # 64 MiB of zeros in ~64 KiB
        with self.assertRaises(ValueError):
            siteicon.decode_png(self.png_with(16, 16, bomb))

    def test_a_picture_larger_than_the_limit_is_skipped(self):
        side = siteicon.MAX_SIDE + 1
        with self.assertRaises(ValueError):
            siteicon.decode_png(self.png_with(side, side, zlib.compress(b"")))

    def test_a_bmp_palette_cannot_claim_billions_of_colours(self):
        size = 4
        header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 8, 0, 0, 0, 0, 0xFFFFFFFF, 0)
        body = header + b"\x00\x00\xff\x00" * 256 + b"\x00" * (4 * size) + b"\x00" * (4 * size)
        ico = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", size, size, 0, 0, 1, 8, len(body), 22) + body
        p = siteicon.decode(ico)
        self.assertEqual(tuple(p.rgba[:4]), (255, 0, 0, 255))


class BrokenShortcutTest(unittest.TestCase):
    @staticmethod
    def broken() -> bytes:
        header = struct.pack("<I16sI", 0x4C, b"\x01\x14\x02" + b"\x00" * 13, 0x02 | 0x80) + b"\x00" * (0x4C - 24)
        info = struct.pack("<9I", 0x40, 0x24, 1, 0, 0, 0, 0, 0x24, 0) + "AB".encode("utf-16-le") + b"C"  # no terminator
        return header + info

    def test_an_unterminated_path_ends_the_parse(self):
        with self.assertRaises(ValueError):
            read_shortcut(self.broken())

    def test_resolve_gives_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "bad.lnk"
            f.write_bytes(self.broken())
            self.assertIsNone(resolve_shortcut(str(f)))


if __name__ == "__main__":
    unittest.main()
