"""The Error log window on a temporary vault, without a screen: it lists the entries newest
first, shows the scrubbed report, copies it to the fake clipboard, saves it, asks before
it clears; Qt's warnings reach errors.log under a rate limit; the toast after a crash
shows once."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import reset_globals
from tests.qt_helpers import close_window, open_window, pump, qt_app, require_qt

require_qt()

import shiboken6  # noqa: E402
from PySide6.QtCore import QtMsgType  # noqa: E402

from daykeeper import errlog  # noqa: E402
from daykeeper.qt.dialogs import errorlog as errorlog_dialog  # noqa: E402
from daykeeper.qt.errors import install_qt_messages  # noqa: E402
from daykeeper.qt.widgets.toast import Toast  # noqa: E402


def setUpModule():
    qt_app()


def fail(log: Path, n: int) -> None:
    try:
        raise ValueError(f"broken {n} glpat-{'Ab12Cd34Ef56Gh78Ij90'}")  # joined: not a token in the source
    except ValueError as exc:
        errlog.write_error(log, type(exc), exc, exc.__traceback__, "window")


class ErrorLogWindowTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.win = open_window(self.tmp.name, unlock=False)
        self.folder = Path(self.tmp.name)
        self.log = self.folder / errlog.NAME

    def tearDown(self) -> None:
        try:
            close_window(self.win)
            for toast in list(Toast.stack):
                if shiboken6.isValid(toast):
                    toast.close()
            pump()
        finally:
            self.tmp.cleanup()
            reset_globals()

    def test_lists_copies_saves_and_clears(self):
        for n in range(3):
            fail(self.log, n)
        dlg = self.win.error_log()
        pump()
        self.assertIs(self.win.error_log(), dlg, "one window, raised again")
        titles = [dlg.tree.topLevelItem(i).text(2) for i in range(dlg.tree.topLevelItemCount())]
        self.assertEqual(titles, [f"ValueError: broken {n} <hidden>" for n in (2, 1, 0)])
        first = dlg.tree.topLevelItem(0)
        self.assertEqual(first.text(1), "window")
        self.assertEqual(first.childCount(), 1, "the traceback folds under the entry")
        self.assertFalse(first.isExpanded())
        self.assertIn("Traceback", first.child(0).text(0))
        self.assertEqual(dlg.preview.toPlainText(), dlg.report, "the report is shown as it leaves")
        self.assertIn("# Daykeeper error report", dlg.report)

        dlg.copy_btn.click()
        self.assertEqual(self.win.fake_clip.text, dlg.report)

        target = self.folder / "out" / "report.md"
        target.parent.mkdir()
        with mock.patch.object(errorlog_dialog.QFileDialog, "getSaveFileName", return_value=(str(target), "")):
            dlg.save_btn.click()
        self.assertEqual(target.read_text(encoding="utf-8"), dlg.report)

        with mock.patch.object(errorlog_dialog, "ask_yes_no", return_value=False) as asked:
            dlg.clear_btn.click()
        asked.assert_called_once()
        self.assertTrue(self.log.exists(), "No keeps the log")
        with mock.patch.object(errorlog_dialog, "ask_yes_no", return_value=True):
            dlg.clear_btn.click()
        self.assertFalse(self.log.exists())
        self.assertEqual(dlg.tree.topLevelItemCount(), 0)
        self.assertTrue(dlg.empty.isVisibleTo(dlg))
        dlg.close()

    def test_open_folder(self):
        dlg = self.win.error_log()
        with mock.patch.object(errorlog_dialog, "open_with_system") as opened:
            dlg.folder_btn.click()
        opened.assert_called_once_with(self.folder)
        dlg.close()

    def test_the_toast_after_a_crash_shows_once(self):
        (self.folder / errlog.CRASH_NAME).write_text("Daykeeper crash log | started 2026-01-02 03:04:05\nFatal Python error: Aborted\n", encoding="utf-8")
        shown = []
        for _start in range(2):
            crashed = errlog.start_crash_log(self.folder)
            errlog.stop_crash_log()
            if crashed:
                shown.append(self.win.tell_crash())
        self.assertEqual(len(shown), 1)
        toast = shown[0]
        self.assertIn("Error log", toast.text.text())
        toast._action()
        pump()
        dlg = self.win.error_log_window
        self.assertIsNotNone(dlg)
        self.assertEqual(dlg.tree.topLevelItem(0).text(1), "crash")
        self.assertIn("Fatal Python error: Aborted", dlg.report)
        dlg.close()


class QtMessagesTest(unittest.TestCase):
    def test_warnings_are_rate_limited(self):
        from PySide6.QtCore import qInstallMessageHandler

        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / errlog.NAME
            handler = install_qt_messages(log, per_minute=3)
            qInstallMessageHandler(None)  # the test calls the handler itself; Qt's own messages go as before
            with mock.patch("sys.stderr", None):
                for n in range(10):
                    handler(QtMsgType.QtWarningMsg, None, f"warning {n}")
                handler(QtMsgType.QtDebugMsg, None, "debug")
                handler(QtMsgType.QtCriticalMsg, None, "critical")
            entries = errlog.read_entries(log)
        self.assertEqual([e.title for e in reversed(entries)], ["warning 0", "warning 1", "warning 2"])
        self.assertEqual({e.where for e in entries}, {"Qt warning"})


if __name__ == "__main__":
    unittest.main()
