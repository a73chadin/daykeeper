"""The quick window of the Qt window: typing ranks, Enter copies a password into the (fake)
clipboard and asks the guard's code first, →/Tab lists the actions, Esc hides, a journal
entry is filed and undone, recent choices are kept; the hotkey (a fake one: no test
registers a real key) is restarted by the settings and its press shows the window."""
from __future__ import annotations

import tempfile
import threading
import unittest
from unittest import mock

from tests.helpers import reset_globals
from tests.qt_helpers import close_window, open_window, pump, qt_app, require_qt, type_text, wait_until

require_qt()

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from daykeeper.journal import today  # noqa: E402
from daykeeper.qt.dialogs import quick as quick_ui  # noqa: E402
from daykeeper.security import GUARD_PIN  # noqa: E402


def setUpModule():
    qt_app()


class FakeHotkey:
    """Stands in for hotkey.WindowsHotkey: remembers what it was asked, registers nothing."""

    made: list[FakeHotkey] = []
    problem = ""

    def __init__(self, combo, queue=None, notify=None):
        self.combo, self.notify = combo, notify
        self.started = self.stopped = False
        FakeHotkey.made.append(self)

    def start(self) -> str:
        self.started = True
        return FakeHotkey.problem

    def stop(self) -> None:
        self.stopped = True


class Base(unittest.TestCase):
    def setUp(self):
        reset_globals()
        FakeHotkey.made, FakeHotkey.problem = [], ""
        patches = [mock.patch.object(quick_ui, "Hotkey", FakeHotkey), mock.patch.object(quick_ui, "HOTKEY_SUPPORTED", True)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.win = open_window(self.tmp.name)
        vault = self.win.vault
        self.git = vault.add_entry(name="GitHub")
        self.git.fields[0].value = "me@example.com"
        self.git.fields[1].value = "quick-secret"
        self.mail = vault.add_entry(name="Mail of the team")
        self.mail.fields[0].value = "team@example.com"
        self.mail.fields[1].value = "mail-secret"
        vault.bookmarks.add("Docs", "https://docs.example.com", "")
        self.win.show_main()
        pump()
        self.access = self.win.quick

    def tearDown(self):
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()

    def open_quick(self):
        self.access.show()
        pump()
        qw = self.access.window
        self.assertTrue(qw.isVisible())
        return qw

    def key(self, qw, key):
        QTest.keyClick(qw.edit, key)
        pump()


class QuickWindowTest(Base):
    def test_typing_ranks_and_nothing_secret_is_shown(self):
        qw = self.open_quick()
        type_text(qw.edit, "git")
        self.assertEqual(qw.shown[0].title, "GitHub")
        self.assertEqual(qw.view.rows[0][1], "GitHub")
        self.assertLessEqual(len(qw.view.rows), quick_ui.ROWS)
        everything = repr([(i.title, i.subtitle, i.keywords) for i in qw.items])
        self.assertNotIn("quick-secret", everything)
        qw.edit.setText("team")
        self.assertEqual(qw.shown[0].title, "Mail of the team", "a word inside the title")

    def test_enter_copies_the_password_and_records_recent(self):
        qw = self.open_quick()
        type_text(qw.edit, "github")
        self.key(qw, Qt.Key.Key_Return)
        self.assertFalse(qw.isVisible())
        self.assertEqual(self.win.fake_clip.text, "quick-secret")
        self.assertEqual(self.access.recent(), [f"entry:{self.git.id}"])
        qw = self.open_quick()
        self.assertEqual(qw.shown[0].title, "GitHub", "the recent choice comes first with no query")

    def test_the_guard_closing_takes_the_open_window_with_it(self):
        """Built while the guard was open, the window would go on naming entries behind it."""
        qw = self.open_quick()
        type_text(qw.edit, "github")
        self.lock_guard()
        pump()
        self.assertFalse(qw.isVisible(), "the window went with the guard")
        self.assertIsNone(self.win.fake_clip.text)

    def test_the_guard_asks_its_code_before_a_password(self):
        qw = self.open_quick()
        type_text(qw.edit, "github")
        self.lock_guard()
        self.win.security.vault_locked = False  # as if the guard were open when the window was built
        qw = self.open_quick()
        type_text(qw.edit, "github")
        self.win.security.vault_locked = True  # and closed again without the window noticing
        with mock.patch("daykeeper.qt.lock.ask_secret") as prompt:
            self.key(qw, Qt.Key.Key_Return)
        prompt.assert_called_once()
        self.assertIsNone(self.win.fake_clip.text, "nothing copied before the code")
        accept, then = prompt.call_args.args[4], prompt.call_args.args[5]
        self.assertIsNot(accept("0000"), True)
        self.assertIs(accept("1357"), True)
        then()
        self.assertEqual(self.win.fake_clip.text, "quick-secret")

    def lock_guard(self):
        vault = self.win.vault
        vault.security.vault_guard = GUARD_PIN
        vault.security.set_pin("1357")
        self.win.security.lock_secrets()

    def test_behind_the_guard_no_entry_is_listed_and_the_first_row_unlocks(self):
        bm = self.win.vault.bookmarks.items[0]
        bm.entry_id = self.git.id
        self.lock_guard()
        with mock.patch("daykeeper.qt.lock.ask_secret") as prompt:
            qw = self.open_quick()
        prompt.assert_not_called()  # opening the window asks nothing
        self.assertEqual([i for i in qw.items if i.kind == "entry"], [])
        self.assertEqual(qw.view.rows[0][1], "Passwords are locked — Enter to unlock")
        type_text(qw.edit, "git")
        titles = [r[1] for r in qw.view.rows]
        self.assertNotIn("GitHub", titles)
        self.assertEqual(titles[0], "Passwords are locked — Enter to unlock", "first whatever is typed")
        qw.edit.setText("docs")
        self.assertEqual(qw.shown[1].title, "Docs", "bookmarks stay")
        self.assertEqual(qw.shown[1].actions, ["open", "show"], "no action of the tied entry")
        everything = repr([(i.title, i.subtitle, i.keywords) for i in qw.items])
        self.assertNotIn("GitHub", everything)
        self.assertNotIn("Mail of the team", everything)
        qw.edit.setText("")
        with mock.patch("daykeeper.qt.lock.ask_secret") as prompt:
            self.key(qw, Qt.Key.Key_Return)
        prompt.assert_called_once()
        self.assertEqual(self.access.recent(), [], "the unlock row is not a recent choice")
        accept, then = prompt.call_args.args[4], prompt.call_args.args[5]
        self.assertIs(accept("1357"), True)
        then()
        pump()
        qw = self.access.window
        self.assertTrue(qw.isVisible(), "the window comes back")
        self.assertFalse(qw.locked)
        type_text(qw.edit, "github")
        self.assertEqual(qw.shown[0].title, "GitHub", "the entries after the code")
        self.assertNotIn("Passwords are locked — Enter to unlock", [r[1] for r in qw.view.rows])

    def test_tab_and_right_list_the_actions(self):
        qw = self.open_quick()
        type_text(qw.edit, "github")
        self.key(qw, Qt.Key.Key_Tab)
        self.assertEqual(qw.mode, "actions")
        labels = [r[1] for r in qw.view.rows]
        self.assertEqual(labels[:2], ["Copy password", "Copy login"])
        self.assertIn("Show in the app", labels)
        self.key(qw, Qt.Key.Key_Left)
        self.assertEqual(qw.mode, "results")
        self.key(qw, Qt.Key.Key_Right)
        self.assertEqual(qw.mode, "actions")
        self.key(qw, Qt.Key.Key_Down)  # Copy login
        self.key(qw, Qt.Key.Key_Return)
        self.assertEqual(self.win.fake_clip.text, "me@example.com")

    def test_esc_hides_and_drops_the_index(self):
        qw = self.open_quick()
        type_text(qw.edit, "git")
        self.key(qw, Qt.Key.Key_Escape)
        self.assertFalse(qw.isVisible())
        self.assertEqual(qw.items, [])
        self.assertEqual(qw.edit.text(), "")

    def test_lock_hides_it(self):
        qw = self.open_quick()
        self.win.lock()
        pump()
        self.assertFalse(qw.isVisible())

    def test_bookmark_opens_only_web_addresses(self):
        qw = self.open_quick()
        type_text(qw.edit, "docs")
        with mock.patch("webbrowser.open") as browser:
            self.key(qw, Qt.Key.Key_Return)
        browser.assert_called_once_with("https://docs.example.com")

    def test_command_settings_opens_the_quick_section(self):
        qw = self.open_quick()
        type_text(qw.edit, "/settings")
        self.key(qw, Qt.Key.Key_Return)
        self.assertIsNotNone(self.win._settings_win)
        self.win._settings_win.show_section("quick")
        pump()
        self.win.close_settings()

    def test_local_shortcut(self):
        self.assertIn("Ctrl+K", self.win.actions_by_keys)
        self.win.actions_by_keys["Ctrl+K"].trigger()
        pump()
        self.assertTrue(self.access.window.isVisible())


class QuickEntryTest(Base):
    def test_entry_is_previewed_written_and_undone(self):
        qw = self.open_quick()
        type_text(qw.edit, "pass DEMO-12 DEMO-13")
        self.assertEqual(qw.mode, "entry")
        self.assertEqual(len(qw.view.rows), 1)
        self.assertEqual(qw.view.rows[0][0], "journal")
        self.assertIn("DEMO-12, DEMO-13", qw.view.rows[0][1])
        self.key(qw, Qt.Key.Key_Return)
        filed = lambda: [i for items in self.win.vault.journal.get(today(), create=True).columns.values() for i in items]  # noqa: E731
        self.assertEqual(sorted(filed()), ["DEMO-12", "DEMO-13"])
        self.assertIn("DEMO-12", self.win.main_screen.status.text())
        self.win.undo()
        pump()
        self.assertEqual(filed(), [], "Ctrl+Z takes the entry back")

    def test_a_token_is_not_an_entry(self):
        qw = self.open_quick()
        type_text(qw.edit, "pass glpat-AbCdEfGhIjKlMnOpQrSt")
        self.assertNotEqual(qw.mode, "entry")


class HotkeyTest(Base):
    def setUp(self):
        super().setUp()
        self.win.background_jobs = True  # open_window makes a preview copy, which takes no key

    def test_a_preview_copy_registers_nothing(self):
        self.win.background_jobs = False
        self.assertEqual(self.access.start_hotkey(), "")
        self.assertEqual(self.access.set_combo("Ctrl+Alt+J"), "")
        self.assertEqual(self.access.combo(), "ctrl+alt+j", "the combination is still saved")
        self.assertEqual(FakeHotkey.made, [], "no key taken from the computer")
        self.assertIsNone(self.access.hotkey)

    def test_start_change_and_stop(self):
        self.assertEqual(self.access.start_hotkey(), "")
        first = FakeHotkey.made[-1]
        self.assertEqual(first.combo, quick_ui.DEFAULT_HOTKEY)
        self.assertTrue(first.started)
        self.assertEqual(self.access.set_combo("Ctrl+Alt+J"), "")
        self.assertTrue(first.stopped, "the old combination is given back first")
        self.assertEqual(FakeHotkey.made[-1].combo, "ctrl+alt+j")
        self.assertEqual(self.access.combo(), "ctrl+alt+j")
        self.assertIn("cannot be read", self.access.set_combo("ctrl+nothing"))
        self.assertEqual(self.access.combo(), "ctrl+alt+j", "a bad combination is not saved")
        last = FakeHotkey.made[-1]
        self.access.close()
        self.assertTrue(last.stopped)

    def test_settings_section_restarts_and_shows_the_problem(self):
        self.access.start_hotkey()
        self.win.settings_dialog("quick")
        pump()
        section = self.win._settings_win.pages["quick"]
        FakeHotkey.problem = "ctrl+shift+k is already taken by another program"
        section.combo.setText("ctrl+shift+k")
        section.apply()
        self.assertEqual(FakeHotkey.made[-1].combo, "ctrl+shift+k")
        self.assertIn("already taken", section.state.text())
        self.access.remember("entry:x")
        section.clear_recent()
        self.assertEqual(self.access.recent(), [])
        self.win.close_settings()

    def test_a_press_shows_the_window_or_the_unlock_screen(self):
        self.access.start_hotkey()
        press = FakeHotkey.made[-1].notify
        threading.Thread(target=press).start()  # as the hotkey thread does
        self.assertTrue(wait_until(lambda: self.access.window is not None and self.access.window.isVisible()))
        self.access.window.hide()
        self.win.lock()
        pump()
        threading.Thread(target=press).start()
        pump(50)
        self.assertTrue(wait_until(lambda: self.win.screens.currentWidget() is self.win.unlock_screen))
        self.assertFalse(self.access.window.isVisible())


if __name__ == "__main__":
    unittest.main()
