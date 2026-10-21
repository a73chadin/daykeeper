"""The update (daykeeper/update.py) and the release signing (tools/sign_release.py): the
version order, the daily gate and its switch, the one request, the signature, the
checksums, the file picked for each kind of copy, the swap scripts (run for real against
temporary folders: on Windows with a dummy exe, elsewhere with sh), and the leftovers.

No network: a fake serves the release from memory. Every key here is made by the test."""
from __future__ import annotations

import base64
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

from daykeeper import __version__, update
from tests.update_fakes import FakeHttp, SigningPair, release_json, signed_release, zip_of

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import sign_release  # noqa: E402


class FakeConn:
    """http.client.HTTPSConnection standing in: answers from `script` (url → (status,
    headers, body)), remembers what was asked and how it was made."""

    made: list[dict] = []
    script: dict[str, tuple[int, dict, bytes]] = {}

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port = host, port
        FakeConn.made.append({"host": host, "port": port, "timeout": timeout, "context": context})
        self.closed = False

    def request(self, method, path, headers=None):
        FakeConn.made[-1].update(method=method, path=path, headers=dict(headers or {}))
        self.url = f"https://{self.host}{path}"

    def getresponse(self):
        status, headers, body = FakeConn.script[self.url]
        resp = io.BytesIO(body)
        resp.status, resp.headers = status, headers  # type: ignore[attr-defined]
        resp.getheader = headers.get  # type: ignore[attr-defined]
        return resp

    def close(self):
        self.closed = True


def fake_conn(script: dict[str, tuple[int, dict, bytes]]):
    FakeConn.made, FakeConn.script = [], script
    return mock.patch("http.client.HTTPSConnection", FakeConn)


class TlsTest(unittest.TestCase):
    """daykeeper/tls.py: certifi's CA list on a frozen macOS build, the system's elsewhere
    (a fake certifi and a fake ssl: nothing is installed or opened)."""

    def contexts(self, platform: str, frozen: bool) -> list[dict]:
        from daykeeper import tls

        made: list[dict] = []
        fake_certifi = types.ModuleType("certifi")
        fake_certifi.where = lambda: "/Applications/Daykeeper.app/Contents/Frameworks/certifi/cacert.pem"  # type: ignore[attr-defined]
        with mock.patch.dict(sys.modules, {"certifi": fake_certifi}), mock.patch.object(tls.ssl, "create_default_context", side_effect=lambda **kw: made.append(kw) or object()):
            tls.context(platform, frozen)
        return made

    def test_a_frozen_mac_build_trusts_certifi(self):
        self.assertEqual(self.contexts("darwin", True), [{"cafile": "/Applications/Daykeeper.app/Contents/Frameworks/certifi/cacert.pem"}])
        self.assertEqual(self.contexts("darwin", False), [{}], "a run from source: the system's store")
        self.assertEqual(self.contexts("win32", True), [{}], "Windows: the system's store")

    def test_the_update_check_uses_it(self):
        from daykeeper import tls

        sentinel = object()
        with mock.patch.object(tls, "context", return_value=sentinel) as ctx, fake_conn({update.LATEST_URL: (200, {}, b"{}")}):
            update.Http().get(update.LATEST_URL)
        ctx.assert_called_once_with()
        self.assertIs(FakeConn.made[0]["context"], sentinel)


class VersionTest(unittest.TestCase):
    def test_numbers_compare_as_numbers(self):
        self.assertTrue(update.is_newer("2.0.10", "2.0.9"))
        self.assertTrue(update.is_newer("2.1.0", "2.0.99"))
        self.assertTrue(update.is_newer("10.0.0", "9.9.9"))
        self.assertFalse(update.is_newer("2.0.9", "2.0.10"))
        self.assertFalse(update.is_newer("2.0.0", "2.0.0"))
        self.assertFalse(update.is_newer("v2.0.0", "2.0"), "a missing patch number is 0")

    def test_a_release_comes_after_its_pre_releases(self):
        order = ["2.1.0-alpha", "2.1.0-alpha.1", "2.1.0-beta.1", "2.1.0-beta.2", "2.1.0-beta.10", "2.1.0-rc.1", "2.1.0", "2.1.1-beta.1"]
        for a, b in zip(order, order[1:], strict=False):
            self.assertTrue(update.is_newer(b, a), f"{b} > {a}")
            self.assertFalse(update.is_newer(a, b), f"{a} < {b}")
        self.assertTrue(update.is_prerelease("v2.1.0-rc.1"))
        self.assertFalse(update.is_prerelease("v2.1.0"))

    def test_what_is_not_a_version_is_never_newer(self):
        for junk in ("", "latest", "2", "2.x.0", "v2.0.0 beta", None):
            self.assertIsNone(update.parse_version(junk))  # type: ignore[arg-type]
            self.assertFalse(update.is_newer(junk, "1.0.0"))  # type: ignore[arg-type]


class PickTest(unittest.TestCase):
    def releases(self):
        return update.releases_from_json([
            release_json("2.0.1", []),
            release_json("2.1.0-beta.1", [], prerelease=True),
            release_json("2.0.10", []),
            {**release_json("3.0.0", []), "draft": True},
            {"tag_name": "nightly", "assets": []},
        ])

    def test_the_newest_release_and_no_pre_release_unless_asked(self):
        self.assertEqual(update.pick(self.releases(), "2.0.0").version, "2.0.10")
        self.assertEqual(update.pick(self.releases(), "2.0.0", prereleases=True).version, "2.1.0-beta.1")
        self.assertIsNone(update.pick(self.releases(), "2.0.10"))
        self.assertIsNone(update.pick(self.releases(), "2.1.0"), "a draft is never offered")

    def test_a_tag_with_a_suffix_is_a_pre_release_even_when_not_marked(self):
        r = update.releases_from_json(release_json("2.2.0-rc.1", []))[0]
        self.assertTrue(r.prerelease)
        self.assertIsNone(update.pick([r], "2.0.0"))

    def test_a_pre_release_user_is_offered_the_release(self):
        r = update.releases_from_json(release_json("2.1.0", []))[0]
        self.assertEqual(update.pick([r], "2.1.0-beta.2").version, "2.1.0")

    def test_the_notes_are_the_release_own_section_of_the_changelog(self):
        body = "# Changes\n\n## 2.1.0 — updates\n\n- signed updates\n\n## 2.0.0 — Qt\n\n- one window\n"
        r = update.releases_from_json(release_json("2.1.0", [], body=body))[0]
        self.assertEqual(update.notes_for(r), "## 2.1.0 — updates\n\n- signed updates")
        r = update.releases_from_json(release_json("2.1.1", [], body="Just a fix."))[0]
        self.assertEqual(update.notes_for(r), "Just a fix.")


class CheckTest(unittest.TestCase):
    def test_the_daily_gate(self):
        now = 1_800_000_000.0
        self.assertTrue(update.check_due({}, now), "never checked")
        self.assertFalse(update.check_due({update.SETTING_CHECKED: now - 3600}, now))
        self.assertFalse(update.check_due({update.SETTING_CHECKED: now - update.CHECK_EVERY + 1}, now))
        self.assertTrue(update.check_due({update.SETTING_CHECKED: now - update.CHECK_EVERY}, now))
        self.assertTrue(update.check_due({update.SETTING_CHECKED: now + 3600}, now), "a clock set back does not stop the checks for good")
        self.assertTrue(update.check_due({update.SETTING_CHECKED: "yesterday"}, now))

    def test_the_check_is_on_by_default_and_can_be_turned_off(self):
        self.assertTrue(update.check_enabled({}))
        self.assertTrue(update.check_due({}, 0))
        off = {update.SETTING_ON: False}
        self.assertFalse(update.check_enabled(off))
        self.assertFalse(update.check_due(off, time.time()))
        self.assertFalse(update.prereleases_wanted({}))
        self.assertTrue(update.prereleases_wanted({update.SETTING_PRE: True}))

    def test_one_request_to_the_latest_release_or_the_list(self):
        http = FakeHttp(api=release_json("9.0.0", []))
        self.assertEqual(update.check(http, "2.0.0").version, "9.0.0")
        self.assertEqual(http.asked, [update.LATEST_URL])
        http = FakeHttp(api=[release_json("9.0.0-beta.1", [], prerelease=True), release_json("8.0.0", [])])
        self.assertEqual(update.check(http, "2.0.0", prereleases=True).version, "9.0.0-beta.1")
        self.assertEqual(http.asked, [update.LIST_URL])
        self.assertIsNone(update.check(FakeHttp(api=release_json("2.0.0", [])), "2.0.0"))

    def test_the_request_carries_only_the_user_agent_and_goes_over_https(self):
        """The real Http: one GET with User-Agent "Daykeeper/<version>" and Accept, a
        timeout, HTTPS only (the opener is a fake: nothing leaves)."""
        with fake_conn({update.LATEST_URL: (200, {}, b"{}")}):
            self.assertEqual(update.Http(timeout=7).get(update.LATEST_URL), b"{}")
        seen = FakeConn.made[0]
        self.assertEqual((seen["method"], seen["host"], seen["port"], seen["timeout"]), ("GET", "api.github.com", 443, 7))
        self.assertEqual(seen["path"], "/repos/a73chadin/daykeeper/releases/latest")
        self.assertEqual({k.lower(): v for k, v in seen["headers"].items()}, {"user-agent": f"Daykeeper/{__version__}", "accept": "application/octet-stream"},
                         "no Connection: close — with it GitHub's answer stalled on some networks")
        with self.assertRaises(update.UpdateError):
            update.Http().get("http://api.github.com/repos/x/releases/latest")

    def test_a_redirect_to_plain_http_is_refused(self):
        with fake_conn({update.LATEST_URL: (302, {"Location": "http://example.invalid/file"}, b"")}), self.assertRaises(update.UpdateError):
            update.Http().get(update.LATEST_URL)
        self.assertEqual(len(FakeConn.made), 1, "nothing was asked of the plain-HTTP address")

    def test_a_download_follows_redirects_to_the_file_host(self):
        first = "https://github.com/o/r/releases/download/v1/a.zip"
        script = {
            first: (302, {"Location": "https://objects.example.com/a.zip?sig=1"}, b""),
            "https://objects.example.com/a.zip?sig=1": (200, {"Content-Length": "5"}, b"hello"),
        }
        with fake_conn(script):
            size, chunks = update.Http().stream(first)
            self.assertEqual((size, b"".join(chunks)), (5, b"hello"))
        self.assertEqual([m["host"] for m in FakeConn.made], ["github.com", "objects.example.com"])
        self.assertEqual(FakeConn.made[1]["path"], "/a.zip?sig=1")

    def test_an_error_status_and_a_redirect_loop_are_plain_messages(self):
        with fake_conn({update.LATEST_URL: (404, {}, b"")}), self.assertRaisesRegex(update.UpdateError, "HTTP 404"):
            update.Http().get(update.LATEST_URL)
        with fake_conn({update.LATEST_URL: (302, {"Location": update.LATEST_URL}, b"")}), self.assertRaises(update.UpdateError):
            update.Http().get(update.LATEST_URL)
        self.assertEqual(len(FakeConn.made), update.MAX_REDIRECTS + 1)


class SignatureTest(unittest.TestCase):
    def setUp(self):
        self.key = SigningPair()

    def test_good_bad_and_unreadable(self):
        data = b"abc  Daykeeper-win64.zip\n"
        update.verify_signature(data, self.key.sign(data), self.key.public)
        with self.assertRaises(update.UpdateError):
            update.verify_signature(data + b" ", self.key.sign(data), self.key.public)
        with self.assertRaises(update.UpdateError):
            update.verify_signature(data, SigningPair().sign(data), self.key.public)
        for junk in (b"", b"not base64!", base64.b64encode(b"short")):
            with self.assertRaises(update.UpdateError):
                update.verify_signature(data, junk, self.key.public)

    def test_the_release_keys_are_the_ones_the_app_carries(self):
        self.assertIsInstance(update.PUBLIC_KEYS, tuple)
        self.assertGreaterEqual(len(update.PUBLIC_KEYS), 1)
        self.assertEqual(len(set(update.PUBLIC_KEYS)), len(update.PUBLIC_KEYS), "no key twice")
        for key in update.PUBLIC_KEYS:
            self.assertEqual(len(base64.b64decode(key, validate=True)), 32)
        self.assertEqual(update.PUBLIC_KEYS[0], "PfX6chME57winjkX63RGCzrV8Ey+owju5vhriuDzrdM=", "the key every release so far is signed with")
        with self.assertRaises(update.UpdateError):
            update.verify_signature(b"x", self.key.sign(b"x"))  # a test key never passes for the release keys

    def test_a_signature_by_any_of_the_keys_holds(self):
        current, spare, stranger = SigningPair(), SigningPair(), SigningPair()
        data = b"# daykeeper v9.0.0\n"
        with mock.patch.object(update, "PUBLIC_KEYS", (current.public, spare.public)):
            update.verify_signature(data, current.sign(data))
            update.verify_signature(data, spare.sign(data))
            with self.assertRaises(update.UpdateError):
                update.verify_signature(data, stranger.sign(data))
            with self.assertRaises(update.UpdateError):
                update.verify_signature(data + b"x", spare.sign(data))
        update.verify_signature(data, spare.sign(data), (current.public, spare.public))
        with self.assertRaises(update.UpdateError):
            update.verify_signature(data, spare.sign(data), current.public)

    def test_the_sums_file_lines_and_its_version_line(self):
        h = "ab" * 32
        text = f"# daykeeper v2.1.0\n{h}  Daykeeper-win64.zip\n{h.upper()} *Daykeeper 2.dmg\nnoise\n"
        self.assertEqual(update.parse_sums(text), {"Daykeeper-win64.zip": h, "Daykeeper 2.dmg": h})
        self.assertEqual(update.sums_tag(text), "v2.1.0")
        self.assertIsNone(update.sums_tag(f"{h}  Daykeeper-win64.zip\n# daykeeper v2.1.0\n"), "only the first line counts")
        self.assertIsNone(update.sums_tag("# daykeeper \n"))
        self.assertIsNone(update.sums_tag(""))


class AssetPickTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_installed_portable_and_mac(self):
        installed = self.tmp / "Programs" / "Daykeeper"
        installed.mkdir(parents=True)
        (installed / "Daykeeper.exe").write_bytes(b"")
        (installed / "unins000.exe").write_bytes(b"")
        portable = self.tmp / "tools" / "Daykeeper"
        portable.mkdir(parents=True)
        (portable / "Daykeeper.exe").write_bytes(b"")
        self.assertEqual(update.install_kind("win32", installed / "Daykeeper.exe", registered=lambda: str(installed) + os.sep), update.INSTALLED)
        self.assertEqual(update.install_kind("win32", portable / "Daykeeper.exe", registered=lambda: str(installed)), update.PORTABLE)
        mac_exe = Path("/Applications/Daykeeper.app/Contents/MacOS/Daykeeper")
        self.assertEqual(update.install_kind("darwin", mac_exe), update.MAC_APP)
        self.assertEqual(update.app_location("darwin", mac_exe), Path("/Applications/Daykeeper.app"))
        self.assertEqual(update.install_kind("darwin", Path("/usr/local/bin/daykeeper")), "")
        self.assertEqual(update.install_kind("linux", Path("/opt/daykeeper/Daykeeper")), "")
        self.assertEqual(update.asset_name("2.1.0", "win32", update.INSTALLED), "Daykeeper-2.1.0-setup.exe")
        self.assertEqual(update.asset_name("2.1.0-beta.1", "win32", update.INSTALLED), "Daykeeper-2.1.0-beta.1-setup.exe")
        self.assertEqual(update.asset_name("2.1.0", "win32", update.PORTABLE), "Daykeeper-win64.zip")
        self.assertEqual(update.asset_name("2.1.0", "darwin", update.MAC_APP), "Daykeeper-macos.zip")
        self.assertIsNone(update.asset_name("2.1.0", "linux", ""))

    def test_who_may_update_itself(self):
        exe = self.tmp / "Daykeeper" / "Daykeeper.exe"
        exe.parent.mkdir()
        self.assertEqual(update.why_not(frozen=True, preview=True, platform="win32", executable=exe), "preview")
        self.assertEqual(update.why_not(frozen=False, preview=False, platform="win32", executable=exe), "source")
        self.assertEqual(update.why_not(frozen=True, preview=False, platform="linux", executable=exe), "platform")
        self.assertEqual(update.why_not(frozen=True, preview=False, platform="win32", executable=exe), "")
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["Daykeeper"], "the probe folder is removed again")
        moved = Path("/private/var/folders/x/AppTranslocation/ABC/d/Daykeeper.app/Contents/MacOS/Daykeeper")
        self.assertEqual(update.why_not(frozen=True, preview=False, platform="darwin", executable=moved), "translocated")

    def test_readonly_is_found_by_making_a_folder_not_by_the_mode_bits(self):
        """os.access says yes on a folder an ACL or a read-only volume closes: the probe
        is a real folder made and removed next to the app."""
        exe = self.tmp / "Daykeeper" / "Daykeeper.exe"
        exe.parent.mkdir()
        made: list[str] = []

        def refuse(*args, **kwargs):
            made.append(str(kwargs.get("dir")))
            raise PermissionError(13, "Access is denied")

        with mock.patch.object(update.os, "access", return_value=True), mock.patch.object(update.tempfile, "mkdtemp", side_effect=refuse):
            self.assertEqual(update.why_not(frozen=True, preview=False, platform="win32", executable=exe), "readonly")
        self.assertEqual(made, [str(self.tmp)], "probed in the folder that holds the app folder")
        with mock.patch.object(update.os, "access", return_value=False):
            self.assertEqual(update.why_not(frozen=True, preview=False, platform="win32", executable=exe), "", "the mode bits are not asked")
        self.assertTrue(update.writable(self.tmp))
        self.assertFalse(update.writable(self.tmp / "missing"))
        app = self.tmp / "Apps" / "Daykeeper.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        with mock.patch.object(update.tempfile, "mkdtemp", side_effect=refuse):
            self.assertEqual(update.why_not(frozen=True, preview=False, platform="darwin", executable=app / "Contents" / "MacOS" / "Daykeeper"), "readonly")
        self.assertEqual(made[-1], str(self.tmp / "Apps"), "macOS: the folder that holds the bundle")
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["Apps", "Daykeeper"])

    def test_installed_needs_the_uninstall_entry_of_this_folder(self):
        """unins*.exe beside the exe is not enough: the setup's HKCU uninstall entry must
        name this folder (a portable folder with a stray uninstaller is portable)."""
        folder = self.tmp / "Programs" / "Daykeeper"
        folder.mkdir(parents=True)
        (folder / "Daykeeper.exe").write_bytes(b"")
        (folder / "unins000.exe").write_bytes(b"")
        exe = folder / "Daykeeper.exe"
        self.assertEqual(update.install_kind("win32", exe, registered=lambda: None), update.PORTABLE)
        self.assertEqual(update.install_kind("win32", exe, registered=lambda: str(self.tmp / "Elsewhere")), update.PORTABLE)
        self.assertEqual(update.install_kind("win32", exe, registered=lambda: str(folder)), update.INSTALLED)
        with mock.patch.object(update, "registered_install_dir", return_value=str(folder) + os.sep):
            self.assertEqual(update.install_kind("win32", exe), update.INSTALLED, "the default reads the registry function at call time")
        (folder / "unins000.exe").unlink()
        self.assertEqual(update.install_kind("win32", exe, registered=lambda: str(folder)), update.PORTABLE, "the uninstaller is still required")
        self.assertTrue(update.UNINSTALL_KEY.endswith("\\" + update.APP_ID + "_is1"))
        iss = (ROOT / "packaging" / "daykeeper.iss").read_text(encoding="utf-8-sig")
        self.assertIn("AppId={" + update.APP_ID + "\n", iss.replace("\r\n", "\n"), "the AppId of the setup")

    def test_the_registry_is_only_read(self):
        """registered_install_dir opens the key for reading and gives InstallLocation; no
        key — None. A fake winreg: the real registry is never touched here."""
        calls: list[tuple] = []

        class Key:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        fake = mock.MagicMock()
        fake.HKEY_CURRENT_USER = "HKCU"
        fake.OpenKey.side_effect = lambda root, path, *rest: (calls.append((root, path, *rest)), Key())[1]
        fake.QueryValueEx.return_value = ("C:\\Users\\u\\AppData\\Local\\Programs\\Daykeeper\\", 1)
        with mock.patch.dict(sys.modules, {"winreg": fake}):
            self.assertEqual(update.registered_install_dir(), "C:\\Users\\u\\AppData\\Local\\Programs\\Daykeeper\\")
            self.assertEqual(calls, [("HKCU", update.UNINSTALL_KEY)], "opened with the default access: read")
            fake.QueryValueEx.assert_called_once_with(mock.ANY, "InstallLocation")
            fake.OpenKey.side_effect = FileNotFoundError(2, "no key")
            self.assertIsNone(update.registered_install_dir())
        for name in ("SetValue", "SetValueEx", "CreateKey", "CreateKeyEx", "DeleteKey", "DeleteValue"):
            self.assertFalse(getattr(fake, name).called, name)


class PrepareTest(unittest.TestCase):
    """The download and its checks, end to end on files in memory."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dk update "))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.key = SigningPair()
        patcher = mock.patch.object(update, "PUBLIC_KEYS", (self.key.public,))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.installed = self.tmp / "Programs" / "Daykeeper"
        patcher = mock.patch.object(update, "registered_install_dir", return_value=str(self.installed))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.installed.mkdir(parents=True)
        (self.installed / "Daykeeper.exe").write_bytes(b"old")
        (self.installed / "unins000.exe").write_bytes(b"")
        self.portable = self.tmp / "my tools" / "Daykeeper"
        self.portable.mkdir(parents=True)
        (self.portable / "Daykeeper.exe").write_bytes(b"old")
        self.setup = update.setup_name("2.1.0")
        self.assets = {self.setup: b"MZ setup", "Daykeeper-win64.zip": zip_of({"Daykeeper/Daykeeper.exe": b"new", "Daykeeper/_internal/x.dll": b"x"}),
                       "Daykeeper-macos.zip": b"PK mac"}

    def work(self) -> Path:
        return Path(tempfile.mkdtemp(prefix=update.TEMP_PREFIX, dir=self.tmp))

    def test_installed_gets_the_verified_setup(self):
        release, http = signed_release(self.key, "2.1.0", self.assets)
        progress = update.Progress()
        got = update.prepare(release, "win32", self.installed / "Daykeeper.exe", http, progress, workdir=self.work())
        self.assertEqual(got.kind, update.INSTALLED)
        self.assertEqual(got.setup.read_bytes(), b"MZ setup")
        self.assertEqual(progress.done, len(b"MZ setup"))
        self.assertEqual([u.rsplit("/", 1)[1] for u in http.asked], [update.SUMS_NAME, update.SIG_NAME, self.setup])

    def test_portable_is_unpacked_next_to_the_app_and_the_zip_goes(self):
        release, http = signed_release(self.key, "2.1.0", self.assets)
        work = self.work()
        got = update.prepare(release, "win32", self.portable / "Daykeeper.exe", http, workdir=work)
        self.assertEqual(got.kind, update.PORTABLE)
        self.assertEqual(got.staging, self.portable.with_name("Daykeeper.update"))
        self.assertEqual((got.new_copy / "Daykeeper.exe").read_bytes(), b"new")
        self.assertFalse(work.exists(), "the downloaded zip is deleted once unpacked")
        self.assertEqual((self.portable / "Daykeeper.exe").read_bytes(), b"old", "nothing replaced yet")

    def test_mac_hands_the_bundle_zip_to_its_unpacker(self):
        """The zip goes to the unpacker (ditto on macOS; a fake here) and the new bundle is
        looked for inside the staging folder."""
        release, http = signed_release(self.key, "2.1.0", self.assets)
        app = self.tmp / "Applications" / "Daykeeper.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)

        def fake_ditto(archive: Path, dest: Path, platform: str) -> None:
            self.assertEqual(archive.read_bytes(), b"PK mac")
            (dest / "Daykeeper.app" / "Contents" / "MacOS").mkdir(parents=True)
            (dest / "Daykeeper.app" / "Contents" / "MacOS" / "Daykeeper").write_bytes(b"new")

        got = update.prepare(release, "darwin", app / "Contents" / "MacOS" / "Daykeeper", http, workdir=self.work(), unpacker=fake_ditto)
        self.assertEqual(got.new_copy, app.with_name("Daykeeper.app.update") / "Daykeeper.app")
        self.assertIn("Daykeeper-macos.zip", http.asked[-1])

    def assert_refused(self, release, http, exe, words: str) -> None:
        work = self.work()
        with self.assertRaises(update.UpdateError) as ctx:
            update.prepare(release, "win32", exe, http, workdir=work)
        self.assertIn(words, str(ctx.exception))
        self.assertFalse(work.exists(), "the download is deleted")
        self.assertFalse(update.staging_for(exe.parent).exists())

    def test_a_swapped_file_is_refused_and_deleted(self):
        def swap(served):
            served[self.setup] = b"MZ something else"

        release, http = signed_release(self.key, "2.1.0", self.assets, tamper=swap)
        self.assert_refused(release, http, self.installed / "Daykeeper.exe", "checksum differs")

    def test_a_swapped_zip_is_refused_before_it_is_unpacked(self):
        def swap(served):
            served["Daykeeper-win64.zip"] = zip_of({"Daykeeper/Daykeeper.exe": b"evil"})

        release, http = signed_release(self.key, "2.1.0", self.assets, tamper=swap)
        self.assert_refused(release, http, self.portable / "Daykeeper.exe", "checksum differs")

    def test_sums_signed_by_another_key_are_refused(self):
        release, http = signed_release(self.key, "2.1.0", self.assets, sign_with=SigningPair())
        self.assert_refused(release, http, self.installed / "Daykeeper.exe", "does not match")
        self.assertNotIn(self.setup, " ".join(http.asked), "nothing else is downloaded")

    def test_sums_changed_after_signing_are_refused(self):
        def change(served):
            served[update.SUMS_NAME] = served[update.SUMS_NAME].replace(b"  ", b"  x")

        release, http = signed_release(self.key, "2.1.0", self.assets, tamper=change)
        self.assert_refused(release, http, self.installed / "Daykeeper.exe", "does not match")

    def test_a_release_without_a_signature_is_refused(self):
        release, http = signed_release(self.key, "2.1.0", self.assets, with_sig=False)
        self.assert_refused(release, http, self.installed / "Daykeeper.exe", update.SIG_NAME)

    def test_a_file_missing_from_the_sums_is_refused(self):
        release, http = signed_release(self.key, "2.1.0", {"Daykeeper-win64.zip": self.assets["Daykeeper-win64.zip"]})
        self.assert_refused(release, http, self.installed / "Daykeeper.exe", "not in the signed list")

    def test_an_older_signed_list_replayed_under_a_newer_tag_is_refused(self):
        """The release page offers v9.9.9, but its SHA256SUMS.txt and .sig are the real
        signed ones of v2.1.0 (the zip names carry no version): refused before any
        download, because the signed first line names v2.1.0."""
        old, old_http = signed_release(self.key, "2.1.0", self.assets)
        old_files = {u.rsplit("/", 1)[1]: b for u, b in old_http.files.items()}
        replay, http = signed_release(self.key, "9.9.9", {"Daykeeper-win64.zip": old_files["Daykeeper-win64.zip"]})
        for name in (update.SUMS_NAME, update.SIG_NAME):
            http.files[f"{update.DOWNLOAD_PREFIX}v9.9.9/{name}"] = old_files[name]
        self.assertIn(b"# daykeeper v2.1.0\n", old_files[update.SUMS_NAME])
        self.assert_refused(replay, http, self.portable / "Daykeeper.exe", "is for v2.1.0, not for v9.9.9")
        self.assertNotIn("Daykeeper-win64.zip", " ".join(http.asked), "nothing else is downloaded")

    def test_a_signed_list_without_its_version_line_is_refused(self):
        release, http = signed_release(self.key, "2.1.0", self.assets, sums_tag=None)
        self.assert_refused(release, http, self.portable / "Daykeeper.exe", "does not name its version")
        release, http = signed_release(self.key, "2.1.0", self.assets, sums_tag="2.1.0")
        self.assert_refused(release, http, self.portable / "Daykeeper.exe", "is for 2.1.0, not for v2.1.0")

    def test_a_staging_folder_that_is_not_ours_is_kept_aside(self):
        """A `Daykeeper.update` the user made is renamed, never deleted; ours (it carries
        the mark) is replaced."""
        staging = update.staging_for(self.portable)
        staging.mkdir()
        (staging / "mine.txt").write_text("keep")
        release, http = signed_release(self.key, "2.1.0", self.assets)
        got = update.prepare(release, "win32", self.portable / "Daykeeper.exe", http, workdir=self.work())
        self.assertTrue((got.staging / update.STAGING_MARK).is_file())
        kept = [p for p in self.portable.parent.iterdir() if p.name.startswith("Daykeeper.update.")]
        self.assertEqual(len(kept), 1)
        self.assertEqual((kept[0] / "mine.txt").read_text(), "keep")
        release, http = signed_release(self.key, "2.1.0", self.assets)
        update.prepare(release, "win32", self.portable / "Daykeeper.exe", http, workdir=self.work())
        self.assertEqual(len([p for p in self.portable.parent.iterdir() if p.name.startswith("Daykeeper.update.")]), 1, "our own staging is just replaced")

    def test_an_archive_with_a_path_outside_its_folder_is_refused(self):
        for evil in ("../evil.txt", "Daykeeper/../../evil.txt"):
            dest = self.tmp / "unpack" / "Daykeeper.update"
            archive = self.tmp / "evil.zip"
            archive.write_bytes(zip_of({"Daykeeper/Daykeeper.exe": b"new", evil: b"x"}))
            with self.assertRaises(update.UpdateError) as ctx:
                update.unpack(archive, dest, "win32")
            self.assertIn("outside its folder", str(ctx.exception))
            self.assertFalse((self.tmp / "unpack" / "evil.txt").exists())
            self.assertFalse((self.tmp / "evil.txt").exists())
            self.assertEqual(list(dest.iterdir()), [], "nothing is extracted when one path is bad")
        release, http = signed_release(self.key, "2.1.0", {**self.assets, "Daykeeper-win64.zip": zip_of({"Daykeeper/Daykeeper.exe": b"new", "../evil.txt": b"x"})})
        self.assert_refused(release, http, self.portable / "Daykeeper.exe", "outside its folder")
        self.assertFalse((self.portable.parent / "evil.txt").exists())

    def test_a_file_from_elsewhere_is_refused(self):
        release, http = signed_release(self.key, "2.1.0", self.assets)
        moved = update.Release(release.version, release.tag, assets=tuple(
            update.Asset(a.name, "https://example.invalid/" + a.name) if a.name == self.setup else a for a in release.assets))
        self.assert_refused(moved, http, self.installed / "Daykeeper.exe", "release page")

    def test_cancel_stops_the_download_and_deletes_it(self):
        release, http = signed_release(self.key, "2.1.0", self.assets)
        http.gate = threading.Event()
        cancel = threading.Event()
        work = self.work()
        errors: list[BaseException] = []

        def run():
            try:
                update.prepare(release, "win32", self.installed / "Daykeeper.exe", http, cancel=cancel, workdir=work)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        t = threading.Thread(target=run)
        t.start()
        self.assertTrue(http.in_stream.wait(5))
        cancel.set()
        http.gate.set()
        t.join(5)
        self.assertEqual([type(e) for e in errors], [update.Cancelled])
        self.assertFalse(work.exists())


class SwapScriptTest(unittest.TestCase):
    """The scripts that put a new copy in place after the app quits."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dk swap "))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_windows_script_quotes_every_path(self):
        app = Path(r"C:\Users\O'Neil\My Apps [x]\Daykeeper")
        script = update.windows_swap_script(4242, app / "Daykeeper.exe", app, Path(r"C:\Users\O'Neil\My Apps [x]\Daykeeper.update\Daykeeper"),
                                            Path(r"C:\Users\O'Neil\My Apps [x]\Daykeeper.update"), Path(r"C:\Temp\Daykeeper-update.log"))
        self.assertIn(r"$app = 'C:\Users\O''Neil\My Apps [x]\Daykeeper'", script)
        self.assertIn(r"$old = 'C:\Users\O''Neil\My Apps [x]\Daykeeper.old'", script)
        self.assertIn("Get-Process -Id 4242", script)
        self.assertNotIn(" -Path ", script, "-LiteralPath only: brackets are wildcards to -Path")
        cmd = update.powershell_command(script, system_root=r"C:\Windows")
        self.assertEqual(cmd[0], r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
        self.assertEqual(base64.b64decode(cmd[-1]).decode("utf-16-le"), script)
        self.assertIn("-EncodedCommand", cmd)

    def test_the_setup_waits_for_the_app_then_runs_silently_and_starts_it_again(self):
        """The installed copy: a detached PowerShell waits for this process (when it is
        still the app), then runs the setup with the silent switches, force-closing a copy
        that still holds the files."""
        switches = ["/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/FORCECLOSEAPPLICATIONS", "/LAUNCH=1"]
        self.assertEqual(update.setup_command(Path(r"C:\T\Daykeeper-2.1.0-setup.exe"))[1:], switches)
        setup = Path(r"C:\Users\O'Neil\Temp [1]\daykeeper-update-x\Daykeeper-2.1.0-setup.exe")
        prepared = update.Prepared(update.INSTALLED, "2.1.0", setup=setup)
        cmd = update.apply_command(prepared, "win32", r"C:\P\Daykeeper\Daykeeper.exe", pid=4242, log=Path(r"C:\Temp\Daykeeper-update.log"))
        self.assertTrue(cmd[0].lower().endswith("powershell.exe"), "not the setup itself: it would start while the app holds its files")
        script = base64.b64decode(cmd[-1]).decode("utf-16-le")
        self.assertIn("Get-Process -Id 4242", script)
        self.assertIn(r"$exe = 'C:\P\Daykeeper\Daykeeper.exe'", script)
        self.assertIn("WaitForExit(120000)", script)
        self.assertIn(r"$setup = 'C:\Users\O''Neil\Temp [1]\daykeeper-update-x\Daykeeper-2.1.0-setup.exe'", script)
        self.assertIn("$info.Arguments = '" + " ".join(switches) + "'", script)
        self.assertLess(script.index("WaitForExit"), script.index("Process]::Start($info)"), "the setup starts after the wait")
        self.assertNotIn(" -Path ", script)

    def test_mac_script_quotes_every_path(self):
        app = PurePosixPath("/Users/o'neil/My Apps/Daykeeper.app")
        script = update.mac_swap_script(77, app, app.with_name("Daykeeper.app.update") / "Daykeeper.app", app.with_name("Daykeeper.app.update"), PurePosixPath("/tmp/l.log"))
        self.assertIn("""APP='/Users/o'"'"'neil/My Apps/Daykeeper.app'""", script)
        self.assertIn("""OLD='/Users/o'"'"'neil/My Apps/Daykeeper.app.old'""", script)
        self.assertIn("kill -0 77", script)
        self.assertIn('/usr/bin/open "$APP"', script)
        self.assertIn("ps -p 77 -o comm=", script, "the process is waited for only while it is still the app")
        self.assertIn("""EXE='/Users/o'"'"'neil/My Apps/Daykeeper.app/Contents/MacOS/Daykeeper'""", script)
        self.assertIn('if [ -f "$OLD/Contents/MacOS/Daykeeper" ]; then', script, "only a Daykeeper bundle is deleted")
        self.assertIn(f'if [ -f "$STAGING/{update.STAGING_MARK}" ]; then rm -rf "$STAGING"; fi', script)

    def test_the_scripts_delete_only_what_is_a_daykeeper_build(self):
        app = Path(r"C:\Apps\Daykeeper")
        script = update.windows_swap_script(1, app / "Daykeeper.exe", app, app.with_name("Daykeeper.update") / "Daykeeper", app.with_name("Daykeeper.update"), Path(r"C:\T\l.log"))
        self.assertIn("if (Test-Path -LiteralPath (Join-Path $old $exeName) -PathType Leaf) { Remove-Item", script)
        self.assertIn("$aside = Aside $old", script)
        self.assertIn(f"$mark = '{update.STAGING_MARK}'", script)
        self.assertIn("if (Test-Path -LiteralPath (Join-Path $staging $mark) -PathType Leaf) { Remove-Item -LiteralPath $staging", script)
        self.assertEqual(script.count("Remove-Item"), 2, "the two guarded deletes and no other")

    def wait_for(self, path: Path, seconds: float = 60) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if path.exists() and path.stat().st_size:
                return True
            time.sleep(0.2)
        return False

    @unittest.skipUnless(sys.platform == "win32", "PowerShell")
    def test_windows_swap_runs_against_temp_folders(self):
        """A stand-in for the app process quits, the script swaps the folders and starts
        the dummy exe from the new place; the old folder stays as Daykeeper.old."""
        dummy = dummy_windows_exe(self.tmp / "dummy-src")
        if dummy is None:
            self.skipTest("no pythonw.exe to make a dummy exe from")
        root = self.tmp / "My Apps [x] 'q'"
        app = root / "Daykeeper"
        staging = root / "Daykeeper.update"
        new = staging / "Daykeeper"
        shutil.copytree(dummy, app)
        shutil.copytree(dummy, new)
        (staging / update.STAGING_MARK).write_bytes(b"")
        (app / "which.txt").write_text("old")
        (new / "which.txt").write_text("new")
        evidence, marker = self.evidence_dir()
        log = self.tmp / "swap log.txt"
        stand_in = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])  # noqa: S603
        script = update.windows_swap_script(stand_in.pid, Path(sys.executable), app, new, staging, log)
        env = {**os.environ, "PYTHONPATH": str(evidence), "PYTHONHOME": sys.base_prefix}
        with mock.patch.dict(os.environ, env, clear=True):
            runner = update.launch_detached(update.powershell_command(script), "win32")
        self.addCleanup(runner.wait, 60)
        stand_in.wait(10)
        self.assertTrue(self.wait_for(marker), log.read_text(encoding="utf-8-sig") if log.exists() else "no log")
        started = Path(marker.read_text(encoding="utf-8").strip())
        self.assertEqual(os.path.normcase(started), os.path.normcase(app / "Daykeeper.exe"))
        self.assertEqual((app / "which.txt").read_text(), "new")
        self.assertEqual((root / "Daykeeper.old" / "which.txt").read_text(), "old")
        self.assertFalse(staging.exists())
        text = log.read_text(encoding="utf-8-sig")
        self.assertIn("swapped", text)
        self.assertIn("started", text)
        # the new copy, at its start, sweeps the old one (update.leftovers)
        self.assertIn(root / "Daykeeper.old", update.leftovers("win32", app / "Daykeeper.exe", tempdir=str(self.tmp)))

    @unittest.skipUnless(sys.platform == "win32", "PowerShell")
    def test_windows_swap_keeps_an_old_folder_that_is_not_daykeeper(self):
        """A `Daykeeper.old` with the user's own files (no Daykeeper.exe in it) is renamed
        aside, never deleted; the swap goes on."""
        dummy = dummy_windows_exe(self.tmp / "dummy-src")
        if dummy is None:
            self.skipTest("no pythonw.exe to make a dummy exe from")
        root = self.tmp / "Apps"
        app = root / "Daykeeper"
        staging = root / "Daykeeper.update"
        new = staging / "Daykeeper"
        shutil.copytree(dummy, app)
        shutil.copytree(dummy, new)
        (staging / update.STAGING_MARK).write_bytes(b"")
        (new / "which.txt").write_text("new")
        (root / "Daykeeper.old").mkdir()
        (root / "Daykeeper.old" / "notes.txt").write_text("mine")
        evidence, marker = self.evidence_dir()
        log = self.tmp / "swap.log"
        script = update.windows_swap_script(999_999_999, Path(sys.executable), app, new, staging, log)
        env = {**os.environ, "PYTHONPATH": str(evidence), "PYTHONHOME": sys.base_prefix}
        with mock.patch.dict(os.environ, env, clear=True):
            runner = update.launch_detached(update.powershell_command(script), "win32")
        self.addCleanup(runner.wait, 60)
        self.assertTrue(self.wait_for(marker), log.read_text(encoding="utf-8-sig") if log.exists() else "no log")
        self.assertEqual((app / "which.txt").read_text(), "new")
        kept = [p for p in root.iterdir() if p.name.startswith("Daykeeper.old.")]
        self.assertEqual(len(kept), 1, log.read_text(encoding="utf-8-sig"))
        self.assertEqual((kept[0] / "notes.txt").read_text(), "mine")
        self.assertTrue((root / "Daykeeper.old" / "Daykeeper.exe").is_file(), "the swapped-out copy took the name")
        self.assertIn("kept a folder that is not Daykeeper", log.read_text(encoding="utf-8-sig"))

    @unittest.skipUnless(sys.platform == "win32", "PowerShell")
    def test_the_setup_waiter_runs_the_setup_once_the_app_has_quit(self):
        """For real, with a dummy setup (the dummy exe: it records that it started, with
        which arguments) and a stand-in for the app that quits after a moment."""
        dummy = dummy_windows_exe(self.tmp / "dummy-src")
        if dummy is None:
            self.skipTest("no pythonw.exe to make a dummy exe from")
        setup = dummy / "Daykeeper.exe"
        evidence = self.tmp / "evidence"
        evidence.mkdir()
        marker = self.tmp / "started.txt"
        quit_at = self.tmp / "quit.txt"
        (evidence / "sitecustomize.py").write_text(
            f"import sys, time\nopen({str(marker)!r}, 'w', encoding='utf-8').write(repr((time.time(), sys.orig_argv[1:])))\n", encoding="utf-8")
        stand_in = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep(1.5); open({str(quit_at)!r}, 'w').write(repr(time.time()))"])  # noqa: S603
        log = self.tmp / "setup.log"
        cmd = update.apply_command(update.Prepared(update.INSTALLED, "2.1.0", setup=setup), "win32", sys.executable, pid=stand_in.pid, log=log)
        env = {**os.environ, "PYTHONPATH": str(evidence), "PYTHONHOME": sys.base_prefix}
        with mock.patch.dict(os.environ, env, clear=True):
            runner = update.launch_detached(cmd, "win32")
        self.addCleanup(runner.wait, 60)
        stand_in.wait(10)
        self.assertTrue(self.wait_for(marker), log.read_text(encoding="utf-8-sig") if log.exists() else "no log")
        import ast

        started, args = ast.literal_eval(marker.read_text(encoding="utf-8"))
        self.assertGreaterEqual(started, float(quit_at.read_text()), "the setup started after the app quit")
        self.assertEqual(args, list(update.SETUP_SWITCHES))
        self.assertIn("started the setup", log.read_text(encoding="utf-8-sig"))

    @unittest.skipUnless(sys.platform == "win32", "PowerShell")
    def test_windows_swap_puts_the_old_copy_back_when_the_new_one_is_missing(self):
        dummy = dummy_windows_exe(self.tmp / "dummy-src")
        if dummy is None:
            self.skipTest("no pythonw.exe to make a dummy exe from")
        app = self.tmp / "Apps" / "Daykeeper"
        shutil.copytree(dummy, app)
        (app / "which.txt").write_text("old")
        evidence, marker = self.evidence_dir()
        log = self.tmp / "swap.log"
        script = update.windows_swap_script(999_999_999, Path(sys.executable), app, self.tmp / "Apps" / "missing" / "Daykeeper", self.tmp / "Apps" / "missing", log)
        env = {**os.environ, "PYTHONPATH": str(evidence), "PYTHONHOME": sys.base_prefix}
        with mock.patch.dict(os.environ, env, clear=True):
            runner = update.launch_detached(update.powershell_command(script), "win32")
        self.addCleanup(runner.wait, 60)
        self.assertTrue(self.wait_for(marker), log.read_text(encoding="utf-8-sig") if log.exists() else "no log")
        self.assertEqual((app / "which.txt").read_text(), "old")
        self.assertFalse((self.tmp / "Apps" / "Daykeeper.old").exists())
        self.assertIn("failed", log.read_text(encoding="utf-8-sig"))

    @unittest.skipUnless(os.name == "posix", "sh")
    def test_mac_swap_runs_against_temp_folders(self):
        app = self.tmp / "My Apps" / "Daykeeper.app"
        staging = update.staging_for(app)
        new = staging / "Daykeeper.app"
        for folder, which in ((app, "old"), (new, "new")):
            (folder / "Contents" / "MacOS").mkdir(parents=True)
            (folder / "which.txt").write_text(which)
        (staging / update.STAGING_MARK).write_bytes(b"")
        evidence, marker = self.evidence_dir()
        opener = self.tmp / "open.py"
        opener.write_text(f"import sys; open({str(marker)!r}, 'w').write(sys.argv[1])")
        stand_in = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1)"])  # noqa: S603
        script = update.mac_swap_script(stand_in.pid, app, new, staging, self.tmp / "swap.log", launch=(sys.executable, str(opener)))
        runner = update.launch_detached(["/bin/sh", "-c", script], "darwin")
        self.addCleanup(runner.wait, 60)
        stand_in.wait(10)
        self.assertTrue(self.wait_for(marker), (self.tmp / "swap.log").read_text() if (self.tmp / "swap.log").exists() else "no log")
        self.assertEqual(marker.read_text(), str(app))
        self.assertEqual((app / "which.txt").read_text(), "new")
        self.assertEqual((update.old_for(app) / "which.txt").read_text(), "old")
        self.assertFalse(staging.exists())

    @unittest.skipUnless(os.name == "posix", "sh")
    def test_mac_swap_keeps_an_old_folder_that_is_not_daykeeper(self):
        app = self.tmp / "Apps" / "Daykeeper.app"
        staging = update.staging_for(app)
        new = staging / "Daykeeper.app"
        for folder, which in ((app, "old"), (new, "new")):
            (folder / "Contents" / "MacOS").mkdir(parents=True)
            (folder / "Contents" / "MacOS" / "Daykeeper").write_bytes(b"")
            (folder / "which.txt").write_text(which)
        (staging / update.STAGING_MARK).write_bytes(b"")
        mine = update.old_for(app)
        mine.mkdir()
        (mine / "notes.txt").write_text("mine")
        evidence, marker = self.evidence_dir()
        opener = self.tmp / "open.py"
        opener.write_text(f"import sys; open({str(marker)!r}, 'w').write(sys.argv[1])")
        log = self.tmp / "swap.log"
        script = update.mac_swap_script(999_999_999, app, new, staging, log, launch=(sys.executable, str(opener)))
        runner = update.launch_detached(["/bin/sh", "-c", script], "darwin")
        self.addCleanup(runner.wait, 60)
        self.assertTrue(self.wait_for(marker), log.read_text() if log.exists() else "no log")
        self.assertEqual((app / "which.txt").read_text(), "new")
        self.assertEqual((mine / "which.txt").read_text(), "old", "the swapped-out bundle took the name")
        kept = [p for p in app.parent.iterdir() if p.name.startswith("Daykeeper.app.old.")]
        self.assertEqual(len(kept), 1, log.read_text())
        self.assertEqual((kept[0] / "notes.txt").read_text(), "mine")
        self.assertFalse(staging.exists())

    def evidence_dir(self) -> tuple[Path, Path]:
        """A sitecustomize that writes which program started (the dummy exe is Python)."""
        evidence = self.tmp / "evidence"
        evidence.mkdir()
        marker = self.tmp / "started.txt"
        (evidence / "sitecustomize.py").write_text(f"import sys\nopen({str(marker)!r}, 'w', encoding='utf-8').write(sys.executable)\n", encoding="utf-8")
        return evidence, marker


def dummy_windows_exe(folder: Path) -> Path | None:
    """A folder with a Daykeeper.exe that starts without a window and exits at once: the
    base Python's pythonw.exe with its DLLs (PYTHONHOME points it at its library)."""
    base = Path(sys.base_prefix)
    pythonw = base / "pythonw.exe"
    if not pythonw.is_file():
        return None
    folder.mkdir(parents=True)
    shutil.copy2(pythonw, folder / "Daykeeper.exe")
    for dll in base.glob("*.dll"):
        shutil.copy2(dll, folder / dll.name)
    return folder


class LeftoversTest(unittest.TestCase):
    def test_what_an_update_leaves_is_found(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        app = tmp / "apps" / "Daykeeper"
        app.mkdir(parents=True)
        old = tmp / "apps" / "Daykeeper.old"
        old.mkdir()
        (old / "Daykeeper.exe").write_bytes(b"")
        (tmp / "apps" / "Daykeeper.update").mkdir()
        (tmp / "apps" / "Daykeeper.update" / update.STAGING_MARK).write_bytes(b"")
        not_ours = tmp / "apps" / "Other.old"
        not_ours.mkdir()
        temp = tmp / "temp"
        stale = temp / (update.TEMP_PREFIX + "abc")
        fresh = temp / (update.TEMP_PREFIX + "def")
        stale.mkdir(parents=True)
        fresh.mkdir()
        os.utime(stale, (time.time() - 2 * update.STALE_TEMP,) * 2)
        found = update.leftovers("win32", app / "Daykeeper.exe", tempdir=str(temp))
        self.assertEqual(sorted(found), sorted([old, tmp / "apps" / "Daykeeper.update", stale]))
        self.assertEqual(update.sweep(found), 3)
        self.assertTrue(fresh.exists() and not_ours.exists())

    def test_an_old_folder_without_an_app_in_it_is_left_alone(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "Daykeeper").mkdir()
        (tmp / "Daykeeper.old").mkdir()
        (tmp / "Daykeeper.old" / "notes.txt").write_text("mine")
        (tmp / "Daykeeper.update").mkdir()
        (tmp / "Daykeeper.update" / "notes.txt").write_text("mine too")
        self.assertEqual(update.leftovers("win32", tmp / "Daykeeper" / "Daykeeper.exe", tempdir=str(tmp / "none")), [])
        self.assertEqual(update.leftovers("darwin", tmp / "Daykeeper.app" / "Contents" / "MacOS" / "Daykeeper", tempdir=str(tmp / "none")), [])


class SignReleaseTest(unittest.TestCase):
    """tools/sign_release.py with a key made here: its signature verifies with the public
    half, a missing key fails, and the key never shows in what it prints."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sums = self.tmp / "SHA256SUMS.txt"
        self.sums.write_bytes(b"# daykeeper v2.1.0\n" + b"0" * 64 + b"  Daykeeper-win64.zip\n")
        self.key = SigningPair()

    def run_main(self, env, public=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sign_release.main(["sign_release.py", str(self.sums)], env=env, public_key=public)
        return code, out.getvalue() + err.getvalue()

    def test_the_signature_verifies_with_the_public_key(self):
        secret = self.key.private_b64()
        code, said = self.run_main({sign_release.ENV: secret}, self.key.public)
        self.assertEqual(code, 0, said)
        sig = (self.tmp / "SHA256SUMS.txt.sig").read_bytes()
        update.verify_signature(self.sums.read_bytes(), sig, self.key.public)
        self.assertEqual(len(base64.b64decode(sig.strip())), 64)
        self.assertNotIn(secret, said)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["SHA256SUMS.txt", "SHA256SUMS.txt.sig"], "nothing else is written")

    def test_a_missing_or_broken_key_fails(self):
        for env in ({}, {sign_release.ENV: ""}, {sign_release.ENV: "%%%"}, {sign_release.ENV: base64.b64encode(b"x" * 31).decode()}):
            code, said = self.run_main(env, self.key.public)
            self.assertEqual(code, 2, said)
            self.assertFalse((self.tmp / "SHA256SUMS.txt.sig").exists())

    def test_a_list_without_its_version_line_is_not_signed(self):
        """The app refuses such a list; the release fails here instead."""
        self.sums.write_bytes(b"0" * 64 + b"  Daykeeper-win64.zip\n")
        secret = self.key.private_b64()
        code, said = self.run_main({sign_release.ENV: secret}, self.key.public)
        self.assertEqual(code, 2, said)
        self.assertIn("# daykeeper <tag>", said)
        self.assertFalse((self.tmp / "SHA256SUMS.txt.sig").exists())
        self.assertNotIn(secret, said)

    def test_a_key_that_is_not_the_app_key_fails_and_leaves_no_signature(self):
        secret = SigningPair().private_b64()
        code, said = self.run_main({sign_release.ENV: secret}, self.key.public)
        self.assertEqual(code, 1)
        self.assertFalse((self.tmp / "SHA256SUMS.txt.sig").exists())
        self.assertNotIn(secret, said)


if __name__ == "__main__":
    unittest.main()
