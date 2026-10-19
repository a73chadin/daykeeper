"""The morning digest in the Qt window: once a day a moment after unlock (not inside the
unlock, not twice a day, again the next day), nothing on an empty day, the ☀ button and
Ctrl+D without counting the day, what each line does (a link, a tab, an entry behind the
guard), Copy as text into the fake clipboard, Don't show again and the setting. Days are
frozen and the timer is flushed: nothing here sleeps."""
from __future__ import annotations

import tempfile
import time
import unittest
from datetime import date
from unittest import mock

from tests.helpers import reset_globals
from tests.qt_helpers import close_window, open_window, pump, qt_app, require_qt

require_qt()

import shiboken6  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from daykeeper import digest as core  # noqa: E402
from daykeeper.qt.dialogs import digest as digest_ui  # noqa: E402
from daykeeper.qt.dialogs.digest import DigestWindow  # noqa: E402
from daykeeper.security import GUARD_PIN  # noqa: E402
from daykeeper.settings import read_settings  # noqa: E402
from daykeeper.vault import Field  # noqa: E402

PASSWORD = "test-master-1"
TODAY = date(2026, 10, 18)
MR_URL = "https://git.example.com/group/repo/-/merge_requests/7"


def setUpModule():
    qt_app()


def full_sections() -> list[core.Section]:
    """Every section, full: what the screenshots and the open time are measured on."""
    L, S = core.Line, core.Section
    return [
        S(core.PLANS, [L(t, tab="journal") for t in ("DEMO-3 retest", "weekly cleanup", "write the cases for DEMO-5", "review group/repo!7", "update the team notes", "call with the team at 15:00")], 7, "journal"),
        S(core.NOTES, [L(f"open item {n}", tab="notes") for n in range(6)], 9, "notes", "3 cards · 9 items"),
        S(core.REVIEWS, [L(f"repo!{n} Fix the sync of the board — @sam", url=f"https://git.example.com/group/repo/-/merge_requests/{n}", ref=f"group/repo!{n}") for n in range(6)], 8, "integrations"),
        S(core.EXPIRING, [L("GitLab PAT · valid until — in 3 d", tab="vault", ref="x1"), L("VPN · expires — expired 2 d ago", tab="vault", ok=False, ref="x2")], 2, "vault"),
        S(core.NIGHT, [L("Nightly qa — failed at 03:12", url="https://git.example.com/p/1", tab="integrations", ok=False), L("Nightly qa-us — green at 04:40", url="https://git.example.com/p/2", tab="integrations", ok=True), L("Routine “backup” — code 1 at 02:00", ok=False)], 3),
        S(core.FEED, [L(f"repo!{n} — @sam commented", url=f"https://git.example.com/c/{n}") for n in range(6)], 6, "integrations"),
    ]


class DigestTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.win = open_window(self.tmp.name)
        self.ctl = self.win.digest
        self.ctl._due.cancel()  # the unlock of open_window asked for one: each test decides
        self.ctl.today = lambda: TODAY

    def tearDown(self):
        self.win.close_child_windows()
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()

    def settings(self) -> dict:
        return read_settings(self.win.session.settings_path).get("digest", {})

    def fill(self) -> None:
        vault = self.win.vault
        vault.journal.get(TODAY.isoformat(), create=True).plans = "• DEMO-3 retest\n- weekly cleanup"
        vault.integrations.gitlab_state["group/repo!7"] = {"reviewer": True, "role": "reviewer", "title": "Fix sync", "author": "sam", "url": MR_URL}
        self.entry = vault.add_entry(name="GitLab PAT", kind="token")
        self.entry.fields.append(Field("valid until", "21.10.2026"))

    def relock(self, day: date) -> None:
        self.win.lock()
        self.ctl.today = lambda: day
        self.win.unlock(PASSWORD)
        pump()

    def shown(self) -> DigestWindow | None:
        d = self.ctl.dialog
        return d if d is not None and shiboken6.isValid(d) and d.isVisible() else None

    def test_once_a_day_after_unlock(self):
        self.fill()
        self.relock(TODAY)
        self.assertIsNone(self.shown(), "not inside the unlock itself")
        self.assertTrue(self.ctl._due.pending)
        self.assertEqual(self.ctl._due.delay_ms, digest_ui.SHOW_DELAY_MS)
        self.ctl._due.flush()
        dlg = self.shown()
        self.assertIsNotNone(dlg)
        self.assertFalse(dlg.isModal(), "the main window stays usable")
        self.assertEqual(self.settings().get("shown"), "2026-10-18")
        self.assertEqual([s.title for s in dlg.sections], [core.PLANS, core.REVIEWS, core.EXPIRING])
        self.assertEqual(set(dlg.section_buttons), {core.PLANS, core.REVIEWS, core.EXPIRING})
        dlg.close()
        pump()

        self.relock(TODAY)
        self.ctl._due.flush()
        self.assertIsNone(self.shown(), "not twice a day")

        self.relock(date(2026, 10, 19))
        self.ctl._due.flush()
        self.assertIsNotNone(self.shown(), "again the next day")
        self.assertEqual(self.settings().get("shown"), "2026-10-19")

    def test_the_lock_cancels_and_closes(self):
        self.fill()
        self.ctl.open()
        self.assertIsNotNone(self.shown())
        self.ctl.on_unlock()
        self.win.lock()
        pump()
        self.assertIsNone(self.shown(), "closed on lock")
        self.assertFalse(self.ctl._due.pending)

    def test_the_passwords_guard_closes_it(self):
        """It names expiring entries; built while the guard was open it must not outlive it."""
        self.fill()
        self.ctl.open()
        self.assertIsNotNone(self.shown())
        vault = self.win.vault
        vault.security.vault_guard = GUARD_PIN
        vault.security.set_pin("2468")
        self.win.security.lock_secrets()
        pump()
        self.assertIsNone(self.shown(), "closed with the guard")

    def test_nothing_on_an_empty_day_but_the_day_counts(self):
        self.ctl.show_due()
        self.assertIsNone(self.shown())
        self.assertEqual(self.settings().get("shown"), "2026-10-18", "an empty day is marked: the next unlock does not build it again")

    def test_button_and_ctrl_d_open_without_counting_the_day(self):
        self.fill()
        self.win.main_screen.digest_btn.click()
        dlg = self.shown()
        self.assertIsNotNone(dlg)
        self.assertNotIn("shown", self.settings())
        dlg.close()
        pump()
        self.assertIsNone(self.shown())
        self.win.actions_by_keys["Ctrl+D"].trigger()
        self.assertIsNotNone(self.shown())
        self.assertNotIn("shown", self.settings())
        self.assertTrue(core.should_show(self.settings(), "2026-10-18"), "the once-a-day digest still comes")
        self.assertEqual([k for k in self.win.actions_by_keys if k == "Ctrl+D"], ["Ctrl+D"])

    def test_ctrl_d_on_an_empty_day_says_so(self):
        dlg = self.ctl.open()
        self.assertEqual(dlg.sections, [])
        self.assertEqual(dlg.lines, [])

    def test_escape_closes(self):
        self.fill()
        dlg = self.ctl.open()
        QTest.keyClick(dlg, Qt.Key.Key_Escape)
        pump()
        self.assertIsNone(self.shown())

    def line(self, dlg: DigestWindow, start: str):
        return next(w for w in dlg.lines if w.line.text.startswith(start))

    def test_a_link_opens_the_browser(self):
        self.fill()
        dlg = self.ctl.open()
        w = self.line(dlg, "repo!7")
        self.assertEqual(w.line.url, MR_URL)
        with mock.patch.object(digest_ui.webbrowser, "open") as browser:
            QTest.mouseClick(w, Qt.MouseButton.LeftButton)
            browser.assert_called_once_with(MR_URL)
        with mock.patch.object(digest_ui.webbrowser, "open") as browser:
            self.ctl.open_line(core.Line("bad", url="javascript:alert(1)"))
            self.ctl.open_line(core.Line("file", url="file:///C:/x"))
            browser.assert_not_called()

    def test_a_tab_line_and_a_section_button_switch_the_tab(self):
        self.fill()
        dlg = self.ctl.open()
        self.win.show_page("vault")
        QTest.mouseClick(self.line(dlg, "DEMO-3"), Qt.MouseButton.LeftButton)
        self.assertEqual(self.win.mode, "journal")
        dlg.section_buttons[core.REVIEWS].click()
        self.assertEqual(self.win.mode, "integrations")
        self.assertTrue(dlg.isVisible(), "the digest stays for the next line")

    def test_an_expiring_line_opens_the_entry_behind_the_guard(self):
        self.fill()
        other = self.win.vault.add_entry(name="Other")
        vault = self.win.vault
        vault.security.vault_guard = GUARD_PIN
        vault.security.set_pin("2468")
        self.win.show_main()
        page = self.win.page("vault")
        page.select_entry(other)
        self.win.show_page("journal")
        dlg = self.ctl.open()  # opened while the guard is up; it locks behind it
        self.win.security.lock_secrets()
        w = self.line(dlg, "GitLab PAT")
        self.assertEqual(w.line.ref, self.entry.id)
        with mock.patch("daykeeper.qt.lock.ask_secret") as prompt:
            QTest.mouseClick(w, Qt.MouseButton.LeftButton)
            self.assertEqual(self.win.mode, "vault")
            prompt.assert_called_once()
            self.assertIsNot(page.current, self.entry, "nothing shown before the code")
            then = prompt.call_args.args[5]
        self.win.security.unlock_secrets()
        then()
        pump()
        self.assertIs(self.win.page("vault").current, self.entry)

    def test_behind_the_guard_the_expiring_section_is_a_count(self):
        self.fill()
        vault = self.win.vault
        vault.security.vault_guard = GUARD_PIN
        vault.security.set_pin("2468")
        self.win.show_main()
        self.win.security.lock_secrets()
        dlg = self.ctl.open()
        section = next(s for s in dlg.sections if s.title == core.EXPIRING)
        self.assertEqual([ln.text for ln in section.lines], ["1 keys expire soon"])
        self.assertEqual(section.more, 0)
        texts = repr([w.line.text for w in dlg.lines]) + digest_ui.as_text(dlg.sections)
        self.assertNotIn("GitLab PAT", texts, "no entry name behind the guard")
        QTest.mouseClick(self.line(dlg, "1 keys"), Qt.MouseButton.LeftButton)
        self.assertEqual(self.win.mode, "vault", "the line opens the tab, which asks its code")
        self.win.security.unlock_secrets()
        dlg = self.ctl.open()
        self.assertEqual(self.line(dlg, "GitLab PAT").line.ref, self.entry.id, "the names are back")

    def test_copy_as_text_into_the_clipboard_not_as_a_secret(self):
        self.fill()
        dlg = self.ctl.open()
        dlg.copy_btn.click()
        self.assertEqual(self.win.fake_clip.text, core.as_text(dlg.sections))
        self.assertIn(MR_URL, self.win.fake_clip.text)
        self.assertFalse(self.win._clip_wipe.pending, "plain text is not wiped like a password")

    def test_dont_show_again_and_the_setting(self):
        self.fill()
        dlg = self.ctl.open()
        dlg.off_btn.click()
        pump()
        self.assertIsNone(self.shown())
        self.assertIs(self.settings().get("on_unlock"), False)
        self.ctl.show_due()
        self.assertIsNone(self.shown(), "turned off")

        self.win.settings_dialog("general")
        box = self.win._settings_win.digest_check
        self.assertFalse(box.isChecked())
        box.setChecked(True)
        self.assertIs(self.settings().get("on_unlock"), True)
        self.ctl.show_due()
        self.assertIsNotNone(self.shown())
        box.setChecked(False)
        self.assertIs(self.settings().get("on_unlock"), False)
        self.assertEqual(self.settings().get("shown"), "2026-10-18", "other keys kept")
        self.win.close_settings()

    def test_full_digest_opens_fast_and_paints_in_both_themes(self):
        from daykeeper.qt import theme

        app = qt_app()
        sections = full_sections()
        start = time.perf_counter()
        dlg = self.ctl.open(sections)
        pump()
        took = time.perf_counter() - start
        self.assertEqual(len(dlg.lines), sum(len(s.lines) for s in sections))
        self.assertLess(took, 0.3, f"opened in {took * 1000:.0f} ms")  # the target is 100 ms; slack for a busy test machine
        dlg.grab()
        theme.apply(app, theme.DARK)
        try:
            dlg.grab()
        finally:
            theme.apply(app, theme.LIGHT)


if __name__ == "__main__":
    unittest.main()
