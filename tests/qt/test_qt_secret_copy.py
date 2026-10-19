"""Copy and Cut out of a shown secret field: Ctrl+C / Ctrl+X go through the window's
copy_to_clipboard as a secret (marked, told to the clipboard history, wiped later), never
the Qt way; without a window the copy is refused; a plain field copies as any line edit.

The window's clipboard is the fake of open_window; the one plain-field test reads the
in-memory clipboard of the offscreen platform, never the system one."""
from __future__ import annotations

import tempfile
import unittest

from tests.helpers import reset_globals
from tests.qt_helpers import close_window, open_window, pump, qt_app, require_qt

require_qt()

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from daykeeper.qt.widgets.secret_edit import SecretEdit  # noqa: E402

CTRL = Qt.KeyboardModifier.ControlModifier


def setUpModule():
    qt_app()


class SecretEditCopyTest(unittest.TestCase):
    def make(self, text: str = "hunter-two") -> SecretEdit:
        edit = SecretEdit()
        self.addCleanup(edit.deleteLater)
        edit.setText(text)
        edit.set_shown(True)
        edit.selectAll()
        return edit

    def test_ctrl_c_and_ctrl_x_go_to_the_handler(self):
        edit = self.make()
        got: list[str] = []
        edit.copy_handler = got.append
        QTest.keyClick(edit, Qt.Key.Key_C, CTRL)
        self.assertEqual(got, ["hunter-two"])
        edit.copy()
        self.assertEqual(got, ["hunter-two"] * 2, "the slot too")
        QTest.keyClick(edit, Qt.Key.Key_X, CTRL)
        self.assertEqual(got, ["hunter-two"] * 3)
        self.assertEqual(edit.text(), "", "cut removes the selection after the copy")

    def test_hidden_or_without_a_window_nothing_is_copied(self):
        edit = self.make()
        got: list[str] = []
        edit.copy_handler = got.append
        edit.set_shown(False)
        QTest.keyClick(edit, Qt.Key.Key_C, CTRL)
        self.assertEqual(got, [], "dots: nothing to copy")
        edit.set_shown(True)
        edit.copy_handler = None  # no owner and no window with copy_to_clipboard
        QTest.keyClick(edit, Qt.Key.Key_C, CTRL)  # refused; must not raise
        edit.cut()
        self.assertEqual(edit.text(), "hunter-two", "a refused cut leaves the text")

    def test_a_plain_field_copies_the_qt_way(self):
        if QGuiApplication.platformName() != "offscreen":
            self.skipTest("only the offscreen clipboard may be used")
        edit = self.make("plain-login")
        edit.secret = False
        got: list[str] = []
        edit.copy_handler = got.append
        QTest.keyClick(edit, Qt.Key.Key_C, CTRL)
        self.assertEqual(got, [])
        self.assertEqual(QGuiApplication.clipboard().text(), "plain-login")
        QGuiApplication.clipboard().clear()


class CardFieldCopyTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.win = open_window(self.tmp.name)
        vault = self.win.vault
        self.entry = vault.add_entry(name="Mail")
        self.entry.fields[0].value = "me@example.com"
        self.entry.fields[1].value = "card-secret-1"
        self.win.show_main()
        self.win.show_page("vault")
        self.page = self.win.page("vault")
        self.page.select_entry(self.entry)
        pump()

    def tearDown(self):
        self.win._clip_wipe.cancel()
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()

    def test_ctrl_c_on_a_revealed_secret_is_a_secret_copy(self):
        row = next(r for r in self.page.fields.rows if r.f.secret)
        row.toggle_reveal()
        row.value.selectAll()
        calls: list[tuple[str, bool]] = []
        real = self.win.copy_to_clipboard

        def spy(value: str, secret: bool = True) -> None:
            calls.append((value, secret))
            real(value, secret=secret)

        self.win.copy_to_clipboard = spy
        QTest.keyClick(row.value, Qt.Key.Key_C, CTRL)
        self.assertEqual(calls, [("card-secret-1", True)])
        self.assertEqual(self.win.fake_clip.text, "card-secret-1")
        self.assertIn(self.win._clip.digest("card-secret-1"), self.win.clip._secrets, "the history is told")
        self.assertTrue(self.win._clip_wipe.pending, "wiped later")

    def test_a_plain_card_field_is_not_a_secret_copy(self):
        row = next(r for r in self.page.fields.rows if not r.f.secret)
        self.assertFalse(row.value.is_secret())
        secret_row = next(r for r in self.page.fields.rows if r.f.secret)
        self.assertTrue(secret_row.value.is_secret())


if __name__ == "__main__":
    unittest.main()
