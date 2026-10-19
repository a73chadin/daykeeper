"""The guard and the lock of the Qt window from the keyboard and the prompts: the card's copy
keys (Ctrl+Shift+C, Ctrl+B) give nothing while the Passwords tab is locked; ten wrong codes
in a row lock the whole app; a lock keeps what is typed in an open file viewer; and every
tab builds in the dark theme. A fake clipboard stands in for the real one."""
from __future__ import annotations

import itertools
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import reset_globals
from tests.qt_helpers import close_window, open_window, pump, qt_app, require_qt, type_text

require_qt()

import shiboken6  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from daykeeper.qt import theme  # noqa: E402
from daykeeper.qt.app import page_types  # noqa: E402
from daykeeper.qt.dialogs.fileview import FileViewer  # noqa: E402
from daykeeper.security import GUARD_PIN, LOCK_AFTER  # noqa: E402

PIN = "2468"


def setUpModule():
    qt_app()


class GuardedWindowCase(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.errors: list[str] = []
        self.win = open_window(self.tmp.name, errors=self.errors)
        vault = self.win.vault
        self.entry = vault.add_entry(name="Bank")
        self.entry.fields[0].value = "demo-login"
        self.entry.fields[1].value = "demo-secret"
        vault.security.vault_guard = GUARD_PIN
        vault.security.set_pin(PIN)
        self.win.show_main()
        self.page = self.win.page("vault")

    def tearDown(self):
        if shiboken6.isValid(self.win):
            close_window(self.win)
        self.tmp.cleanup()
        reset_globals()


class CopyKeysTest(GuardedWindowCase):
    def press(self, keys: str) -> None:
        self.win.actions_by_keys[keys].trigger()
        pump()

    def test_the_copy_keys_respect_the_guard(self):
        win = self.win
        win.security.unlock_secrets()
        win.show_page("vault", select_entry=self.entry)
        pump()
        self.assertIs(self.page.current, self.entry)
        win.security.lock_secrets()
        pump()
        self.assertTrue(self.page.guarded)
        for keys in ("Ctrl+Shift+C", "Ctrl+B"):
            self.press(keys)
            self.assertIsNone(win.fake_clip.text, f"{keys} copied while the passwords are locked")

        win.security.unlock_secrets()
        win.show_page("vault", select_entry=self.entry)
        pump()
        self.assertFalse(self.page.guarded)
        self.press("Ctrl+Shift+C")
        self.assertEqual(win.fake_clip.text, "demo-secret")
        self.press("Ctrl+B")
        self.assertEqual(win.fake_clip.text, "demo-login")
        self.assertEqual(self.errors, [])


class TooManyCodesTest(GuardedWindowCase):
    def test_ten_wrong_codes_lock_the_whole_app(self):
        win = self.win
        win.security.lock_secrets()
        win.show_page("vault")
        pump()
        screen = self.page.guard_screen
        clock = itertools.count(10_000, 120)  # each try comes after the pause has run out
        with mock.patch("daykeeper.qt.lock.time") as fake_time:
            fake_time.monotonic.side_effect = lambda: float(next(clock))
            for n in range(1, LOCK_AFTER + 1):
                type_text(screen.code, "0000")
                QTest.keyClick(screen.code, Qt.Key.Key_Return)
                pump()
                if n < LOCK_AFTER:
                    self.assertIsNotNone(win.vault, f"still open after {n} wrong codes")
                    self.assertEqual(screen.error.text(), "Wrong code")
        pump()
        self.assertIsNone(win.vault, "the whole app is locked: the master password is needed")
        self.assertIs(win.screens.currentWidget(), win.unlock_screen)


class LockKeepsViewerEditsTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.win = open_window(self.tmp.name)

    def tearDown(self):
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()

    def test_a_lock_saves_the_edits_of_an_open_file_viewer(self):
        md = Path(self.tmp.name) / "plan.md"
        md.write_text("# Plan\n\n- [ ] first\n", encoding="utf-8")
        viewer = FileViewer(self.win, md)
        viewer.show()
        pump()
        viewer.toggle_edit()
        viewer.editor.setPlainText("# Plan\n\n- [ ] first\n- [ ] typed before the lock\n")
        self.win.lock()
        pump()
        self.assertIsNone(self.win.vault)
        self.assertIn("typed before the lock", md.read_text(encoding="utf-8"), "the edit is in the file")
        self.assertFalse(shiboken6.isValid(viewer) and viewer.isVisible(), "the viewer is closed")


class DarkThemeTabsTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        theme.apply(qt_app(), theme.DARK)
        self.tmp = tempfile.TemporaryDirectory()
        self.errors: list[str] = []
        self.win = open_window(self.tmp.name, errors=self.errors)

    def tearDown(self):
        close_window(self.win)
        theme.apply(qt_app(), theme.LIGHT)
        self.tmp.cleanup()
        reset_globals()

    def test_every_tab_builds_in_the_dark_theme(self):
        self.assertIs(theme.current(), theme.DARK)
        for mode in page_types():
            self.win.show_page(mode)
            pump()
            page = self.win.pages[mode]
            self.assertTrue(page.isVisible(), mode)
            self.assertEqual(page.palette().color(QPalette.ColorRole.Window).name().upper(), theme.DARK.BG.upper(), mode)
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
