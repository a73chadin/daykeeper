"""The update in the window (qt/dialogs/update.py), with a fake network and a fake launcher:
the quiet note once per version and not while locked, the line in Settings → About, the
dialog with the release notes, Cancel, a refused download, a vault that cannot be saved,
and the backup copy made before anything is started. Nothing is downloaded for real,
nothing is installed, nothing is started, and the app never quits."""
from __future__ import annotations

import base64
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import reset_globals
from tests.qt_helpers import close_window, open_window, pump, qt_app, require_qt, wait_until

require_qt()

from daykeeper import update  # noqa: E402
from daykeeper.qt import jobs  # noqa: E402
from daykeeper.qt.dialogs import update as update_ui  # noqa: E402
from daykeeper.qt.widgets.toast import Toast  # noqa: E402
from daykeeper.settings import read_settings  # noqa: E402
from tests.update_fakes import SigningPair, signed_release  # noqa: E402

PASSWORD = "test-master-1"
NOTES = "# Changes\n\n## 9.1.0 — signed updates\n\n- the app updates itself\n\n## 2.0.0 — Qt\n\n- one window\n"


def setUpModule():
    qt_app()


class UpdateUiTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = Path(tempfile.mkdtemp(prefix="dk ui update "))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        temp = self.tmp / "temp"
        temp.mkdir()
        patcher = mock.patch.object(tempfile, "tempdir", str(temp))  # update.prepare's download folder
        patcher.start()
        self.addCleanup(patcher.stop)
        self.temp = temp
        self.key = SigningPair()
        patcher = mock.patch.object(update, "PUBLIC_KEYS", (self.key.public,))
        patcher.start()
        self.addCleanup(patcher.stop)
        for t in list(Toast.stack):
            t.close()
        self.win = open_window(self.tmp / "vault", password=PASSWORD)
        self.win.preview = False  # open_window makes a preview window; an update needs the main one
        self.app_dir = self.tmp / "Programs" / "Daykeeper"
        self.app_dir.mkdir(parents=True)
        (self.app_dir / "Daykeeper.exe").write_bytes(b"old")
        (self.app_dir / "unins000.exe").write_bytes(b"")
        patcher = mock.patch.object(update, "registered_install_dir", return_value=str(self.app_dir))  # the setup's uninstall entry, faked
        patcher.start()
        self.addCleanup(patcher.stop)
        self.setup_name = update.setup_name("9.1.0")
        self.release, self.http = signed_release(self.key, "9.1.0", {self.setup_name: b"MZ new setup", "Daykeeper-win64.zip": b"PK"}, body=NOTES)
        self.watch = self.win.updates
        self.watch.http = self.http
        self.watch.platform = "win32"
        self.watch.executable = self.app_dir / "Daykeeper.exe"
        self.watch.frozen = True
        self.launched: list[tuple[list[str], int]] = []
        self.quits: list[int] = []
        self.watch.launch = lambda cmd, platform: self.launched.append((cmd, self.backups()))
        self.watch.quit_app = lambda: self.quits.append(1)

    def tearDown(self):
        for t in list(Toast.stack):
            t.close()
        close_window(self.win)
        wait_until(lambda: jobs.pending() == 0, 3000)
        reset_globals()

    def backups(self) -> int:
        folder = self.win.vault_path.parent / "backup"
        return len(list(folder.glob("*.pkv"))) if folder.is_dir() else 0

    def settle(self) -> None:
        self.assertTrue(wait_until(lambda: jobs.pending() == 0 and self.watch.state != "checking", 5000))
        pump()

    def toasts(self) -> list[str]:
        return [t.text.text() for t in Toast.stack]

    # ---- the check

    def test_a_new_version_is_told_once_quietly(self):
        self.watch.check()
        self.settle()
        self.assertEqual(self.watch.available.version, "9.1.0")
        self.assertEqual(self.toasts(), ["Version 9.1.0 is out — Update…"])
        self.assertEqual(self.http.asked, [update.LATEST_URL], "one request, nothing else")
        settings = read_settings(self.win.session.settings_path)
        self.assertEqual(settings[update.SETTING_TOLD], "9.1.0")
        self.assertIsInstance(settings[update.SETTING_CHECKED], float)
        for t in list(Toast.stack):
            t.close()
        self.watch.check()
        self.settle()
        self.assertEqual(self.toasts(), [], "the same version is told once")

    def test_found_while_locked_is_told_after_the_unlock(self):
        self.win.lock()
        self.watch.check()
        self.settle()
        self.assertEqual(self.toasts(), [])
        self.win.unlock(PASSWORD)
        pump()
        self.assertEqual(self.toasts(), ["Version 9.1.0 is out — Update…"])

    def test_the_daily_gate_and_the_switch(self):
        self.watch.now = lambda: 2_000_000_000.0
        self.watch.maybe_check()
        self.settle()
        self.assertEqual(len(self.http.asked), 1)
        self.watch.maybe_check()
        self.settle()
        self.assertEqual(len(self.http.asked), 1, "once a day")
        self.watch.now = lambda: 2_000_000_000.0 + update.CHECK_EVERY
        self.watch.set_enabled(False)
        self.watch.maybe_check()
        self.assertEqual(len(self.http.asked), 1, "off: no request at all")
        self.watch.set_enabled(True)
        self.watch.maybe_check()
        self.settle()
        self.assertEqual(len(self.http.asked), 2)

    def test_a_run_from_source_books_no_check(self):
        self.watch.frozen = False
        self.watch.start()
        self.assertFalse(self.watch._first.pending)
        self.assertFalse(self.watch._daily.running)

    def test_the_about_line(self):
        self.win.settings_dialog("general")
        pump()
        row = self.win._settings_win.update_row
        self.assertFalse(row.update_btn.isVisibleTo(row))
        self.assertEqual(row.status.text(), "Not checked yet.")
        row.check_btn.click()
        self.settle()
        self.assertEqual(row.status.text(), "Version 9.1.0 is out —")
        self.assertTrue(row.update_btn.isVisibleTo(row))
        self.assertEqual(self.toasts(), [], "a check asked for by hand answers in its own line")
        self.http.fail_with = update.UpdateError("HTTP 503")
        row.check_btn.click()
        self.settle()
        self.assertEqual(row.status.text(), "Version 9.1.0 is out —", "a failed check does not forget what was found")
        self.watch.available = None
        self.watch.changed.emit()
        self.assertEqual(row.status.text(), "Could not check for updates: HTTP 503")
        row.daily.setChecked(False)
        row.pre.setChecked(True)
        settings = read_settings(self.win.session.settings_path)
        self.assertIs(settings[update.SETTING_ON], False)
        self.assertIs(settings[update.SETTING_PRE], True)

    # ---- the update

    def open_dialog(self) -> update_ui.UpdateDialog:
        self.watch.check(manual=True)
        self.settle()
        dlg = self.watch.open_dialog()
        self.assertIsNotNone(dlg)
        pump()
        return dlg

    def test_the_dialog_shows_the_notes_then_backs_up_and_starts_the_setup(self):
        dlg = self.open_dialog()
        self.assertEqual(dlg.notes.toPlainText(), "## 9.1.0 — signed updates\n\n- the app updates itself")
        self.assertEqual(len(self.http.asked), 1, "nothing is downloaded before the click")
        before = self.backups()
        dlg.go.click()
        self.assertTrue(wait_until(lambda: bool(self.launched), 5000), dlg.status.text())
        cmd, backups_at_launch = self.launched[0]
        self.assertGreater(backups_at_launch, before, "the vault was copied before the update started")
        self.assertTrue(cmd[0].lower().endswith("powershell.exe"), "a waiter starts the setup once the app has quit")
        script = base64.b64decode(cmd[-1]).decode("utf-16-le")
        setup = re.search(r"(?m)^\$setup = '(.*)'$", script).group(1).replace("''", "'")
        self.assertTrue(setup.endswith(self.setup_name))
        self.assertEqual(Path(setup).read_bytes(), b"MZ new setup")
        self.assertIn("$info.Arguments = '/SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /FORCECLOSEAPPLICATIONS /LAUNCH=1'", script)
        self.assertEqual(self.quits, [1])

    def test_cancel_stops_the_download_and_deletes_it(self):
        dlg = self.open_dialog()
        import threading

        self.http.gate = threading.Event()
        dlg.go.click()
        self.assertTrue(wait_until(self.http.in_stream.is_set, 5000))
        dlg.cancel.click()
        self.http.gate.set()
        self.assertTrue(wait_until(lambda: jobs.pending() == 0, 5000))
        pump()
        self.assertEqual(self.launched, [])
        self.assertEqual(self.quits, [])
        self.assertEqual(list(self.temp.iterdir()), [], "the download folder is gone")

    def test_a_refused_download_says_why_and_starts_nothing(self):
        url = next(u for u in self.http.files if u.endswith(self.setup_name))
        self.http.files[url] = b"MZ swapped"
        dlg = self.open_dialog()
        before = self.backups()
        dlg.go.click()
        self.assertTrue(wait_until(lambda: "refused" in dlg.status.text(), 5000), dlg.status.text())
        self.assertIn("checksum differs", dlg.status.text())
        self.assertEqual(self.launched, [])
        self.assertEqual(self.backups(), before)
        self.assertEqual(list(self.temp.iterdir()), [])

    def test_a_vault_that_cannot_be_saved_stops_the_update(self):
        dlg = self.open_dialog()
        with mock.patch.object(self.win, "save_now", return_value="error"):
            dlg.go.click()
        pump()
        self.assertIn("could not be saved", dlg.status.text())
        self.assertEqual(len(self.http.asked), 1, "nothing downloaded")
        self.assertEqual(self.launched, [])

    def test_typed_text_is_saved_before_the_update(self):
        dlg = self.open_dialog()
        calls: list[str] = []
        real_flush, real_save = self.win.flush_edits, self.win.save_now
        self.win.flush_edits = lambda: (calls.append("flush"), real_flush())[1]  # type: ignore[method-assign]
        self.win.save_now = lambda: (calls.append("save"), real_save())[1]  # type: ignore[method-assign]
        dlg.go.click()
        self.assertTrue(wait_until(lambda: bool(self.launched), 5000))
        self.assertEqual(calls[:2], ["flush", "save"])

    def test_a_preview_or_a_source_run_never_updates(self):
        self.watch.check(manual=True)
        self.settle()
        with mock.patch.object(update_ui, "show_info") as told:
            self.watch.frozen = False
            self.assertIsNone(self.watch.open_dialog())
            self.assertIn("source code", told.call_args[0][1])
            self.watch.frozen = True
            self.win.preview = True
            self.assertIsNone(self.watch.open_dialog())
            self.assertIn("preview copy", told.call_args[0][1])
        self.assertEqual(self.launched, [])

    def test_a_portable_folder_gets_a_swap_script_and_stays_as_it_is_until_the_app_quits(self):
        portable = self.tmp / "my tools" / "Daykeeper"
        portable.mkdir(parents=True)
        (portable / "Daykeeper.exe").write_bytes(b"old")
        from tests.update_fakes import zip_of

        zipped = zip_of({"Daykeeper/Daykeeper.exe": b"new"})
        self.release, self.http = signed_release(self.key, "9.1.0", {self.setup_name: b"MZ", "Daykeeper-win64.zip": zipped}, body=NOTES)
        self.watch.http = self.http
        self.watch.executable = portable / "Daykeeper.exe"
        dlg = self.open_dialog()
        dlg.go.click()
        self.assertTrue(wait_until(lambda: bool(self.launched), 5000), dlg.status.text())
        cmd = self.launched[0][0]
        self.assertTrue(cmd[0].lower().endswith("powershell.exe"))
        self.assertEqual(((portable.parent / "Daykeeper.update" / "Daykeeper" / "Daykeeper.exe")).read_bytes(), b"new")
        self.assertEqual((portable / "Daykeeper.exe").read_bytes(), b"old", "the swap waits for the app to quit")
        self.assertEqual(list(self.temp.iterdir()), [], "the zip is gone")

    def test_a_failed_check_is_not_stamped_and_is_tried_again(self):
        """Only a check that got an answer counts for the daily gate: after an error the
        hourly ticker asks again."""
        self.watch.now = lambda: 2_000_000_000.0
        self.http.fail_with = update.UpdateError("HTTP 503")
        self.watch.maybe_check()
        self.settle()
        self.assertEqual(self.watch.state, "error")
        self.assertNotIn(update.SETTING_CHECKED, read_settings(self.win.session.settings_path))
        self.http.fail_with = None
        self.watch.maybe_check()  # the next tick
        self.settle()
        self.assertEqual(len(self.http.asked), 2, "tried again")
        self.assertEqual(read_settings(self.win.session.settings_path)[update.SETTING_CHECKED], 2_000_000_000.0)
        self.watch.maybe_check()
        self.settle()
        self.assertEqual(len(self.http.asked), 2, "a good check closes the gate for a day")

    def test_a_failure_that_is_not_a_refusal_is_told_plainly(self):
        dlg = self.open_dialog()
        with mock.patch.object(update, "prepare", side_effect=PermissionError(13, "Access is denied")):
            dlg.go.click()
            self.assertTrue(wait_until(lambda: "Could not install" in dlg.status.text(), 5000), dlg.status.text())
        self.assertEqual(dlg.status.text(), "Could not install the update: Access is denied. Nothing was changed, and the download was deleted.")
        self.assertNotIn("refused", dlg.status.text())
        self.assertTrue(dlg.go.isEnabled())
        self.assertEqual(self.launched, [])

    def test_a_vault_locked_during_the_download_starts_nothing(self):
        """The download finished after the lock: the prepared update is deleted, the vault
        is not saved or copied, and nothing starts."""
        dlg = self.open_dialog()
        staging = self.tmp / "my tools" / "Daykeeper.update"
        (staging / "Daykeeper").mkdir(parents=True)
        work = self.temp / (update.TEMP_PREFIX + "x")
        work.mkdir()
        prepared = update.Prepared(update.PORTABLE, "9.1.0", staging=staging, new_copy=staging / "Daykeeper", workdir=work)
        before = self.backups()
        with mock.patch.object(type(self.win), "vault", new_callable=mock.PropertyMock, return_value=None), mock.patch.object(self.win, "save_now") as save:
            dlg._prepared(prepared, None)
        save.assert_not_called()
        self.assertFalse(staging.exists())
        self.assertFalse(work.exists())
        self.assertEqual(self.launched, [])
        self.assertEqual(self.quits, [])
        self.assertEqual(self.backups(), before)
        self.assertIn("locked", dlg.status.text())

    def test_the_readonly_reason_names_the_right_folder(self):
        exe = Path(r"C:\Program Files\Daykeeper\Daykeeper.exe")
        text = update_ui.reason_text("readonly", exe, "win32")
        self.assertIn(str(exe.parent), text)
        self.assertIn("installer", text)
        mac_exe = Path("/Volumes/Shared/Apps/Daykeeper.app/Contents/MacOS/Daykeeper")
        text = update_ui.reason_text("readonly", mac_exe, "darwin")
        self.assertIn(f"({Path('/Volumes/Shared/Apps')})", text, "the folder that holds the bundle")
        self.assertNotIn("Contents", text)
        self.assertNotIn("installer", text, "macOS has no installer")


if __name__ == "__main__":
    unittest.main()
