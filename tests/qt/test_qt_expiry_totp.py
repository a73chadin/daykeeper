"""Expiring keys and 2FA codes in the Qt window: the badge of a list row, the line under a
date field, the count on the top bar and its list window, the toast once a day, the
warning window in Settings → Security; the live 2FA code of the card, its copy as a secret,
the ticks that run only while somebody can see the code, and the shield of a bookmark tile
behind the Passwords guard. Times are frozen: nothing here sleeps."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from unittest import mock

from tests.helpers import reset_globals
from tests.qt_helpers import close_window, open_window, pump, qt_app, require_qt, wait_until

require_qt()

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from daykeeper.gitlab import CheckResult  # noqa: E402
from daykeeper.qt import theme  # noqa: E402
from daykeeper.qt.dialogs import expiry as expiry_ui  # noqa: E402
from daykeeper.qt.pages import integrations as integ_page  # noqa: E402
from daykeeper.qt.pages import vault as vault_page  # noqa: E402
from daykeeper.qt.widgets.toast import Toast  # noqa: E402
from daykeeper.security import GUARD_PIN  # noqa: E402
from daykeeper.settings import read_settings  # noqa: E402
from daykeeper.vault import Field  # noqa: E402

TODAY = date(2026, 10, 18)
RFC_KEY = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # RFC 6238 test secret "12345678901234567890", base32
RFC_URI = f"otpauth://totp/Example:me?secret={RFC_KEY}&digits=8&issuer=Example"
AT_59 = "94287082"  # RFC 6238, SHA-1, T = 59


def setUpModule():
    qt_app()


def close_toasts() -> None:
    for toast in list(Toast.stack):
        toast.close()
    pump()


class ExpiryTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.win = open_window(self.tmp.name)
        self.watch = self.win.expiry
        self.watch.today = lambda: TODAY
        vault = self.win.vault
        self.soon = vault.add_entry(name="GitLab PAT", kind="token")
        self.soon.fields.append(Field("valid until", "21.10.2026"))
        self.gone = vault.add_entry(name="VPN", kind="note")
        self.gone.fields.append(Field("expires", "2026-10-16"))
        self.hidden = vault.add_entry(name="Card", kind="note")
        self.hidden.fields.append(Field("valid until", "19.10.2026", secret=True))  # secret: never read for a date
        self.far = vault.add_entry(name="Far", kind="note")
        self.far.fields.append(Field("valid until", "2027-12-31"))
        self.win.show_main()
        self.page = self.win.page("vault")
        self.watch._toast_due = False  # the unlock of open_window asked for one: each test decides
        self.watch.refresh()

    def tearDown(self):
        close_toasts()
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()

    def settings(self) -> dict:
        return read_settings(self.win.session.settings_path).get("expiry", {})

    def badge(self, entry):
        model = self.page.model
        return model.data(model.index(model.row_of(entry)), vault_page.BADGE_ROLE)

    def test_badges_and_the_card_line(self):
        self.assertEqual(self.badge(self.soon), 3)
        self.assertEqual(self.badge(self.gone), -2)
        self.assertIsNone(self.badge(self.hidden), "a secret field is not unsealed for a date")
        self.assertIsNone(self.badge(self.far), "outside the warning window")
        self.assertEqual(expiry_ui.badge_text(3), "3 d")
        self.assertEqual(expiry_ui.badge_text(-2), "expired")
        self.assertEqual(expiry_ui.tone_colour(3, 7), theme.current().LINK)
        self.assertEqual(expiry_ui.tone_colour(-2, 7), theme.current().DANGER)
        self.page.list.grab()  # the delegate paints the badge without an error

        self.page.select_entry(self.soon)
        self.assertEqual([r.f.filled for r in self.page.fields.rows if r.f.key == "valid until"], [False, True], "the token template's own empty field, then ours")
        row = next(r for r in self.page.fields.rows if r.f.key == "valid until" and r.f.filled)
        self.assertFalse(row.expiry_line.isHidden())
        self.assertEqual(row.expiry_line.text, "Expires in 3 days")
        self.assertTrue(all(r.expiry_line.isHidden() for r in self.page.fields.rows if r is not row))
        self.page.select_entry(self.gone)
        row = next(r for r in self.page.fields.rows if r.f.key == "expires")
        self.assertEqual(row.expiry_line.text, "Expired 2 days ago")
        self.page.select_entry(self.far)
        row = next(r for r in self.page.fields.rows if r.f.key == "valid until")
        self.assertEqual(row.expiry_line.colour(), theme.current().MUTED, "far away: the line is there, quiet")

    def test_the_line_follows_typing(self):
        self.page.select_entry(self.far)
        row = next(r for r in self.page.fields.rows if r.f.key == "valid until")
        row.value.setText("19.10.2026")
        row.value.textEdited.emit("19.10.2026")
        self.assertEqual(row.expiry_line.text, "Expires tomorrow")
        self.assertTrue(self.watch._later.pending, "the watch looks again once the edits pause")
        self.watch._later.flush()
        self.assertEqual(self.badge(self.far), 1)

    def test_top_bar_count_and_the_list_dismissals(self):
        btn = self.win.main_screen.expiry_btn
        self.assertFalse(btn.isHidden())
        self.assertEqual(btn.text(), "2")
        btn.click()
        dlg = self.watch.dialog
        self.assertIsNotNone(dlg)
        self.assertEqual([r["item"].entry_name for r in dlg.rows], ["VPN", "GitLab PAT"], "soonest first")
        dlg.rows[1]["later"].click()
        self.assertEqual(self.settings()["seen"][f"{self.soon.id}:valid until"], "2026-10-24")
        self.assertEqual(btn.text(), "1")
        self.assertEqual([r["item"].entry_name for r in self.watch.dialog.rows], ["VPN"])
        self.watch.dialog.rows[0]["never"].click()
        self.assertEqual(self.settings()["seen"][f"{self.gone.id}:expires"], "date:2026-10-16", "until the date changes")
        self.assertTrue(btn.isHidden(), "nothing left to remind of")
        self.assertEqual(self.badge(self.gone), -2, "the list still says it")
        self.watch.today = lambda: date(2026, 10, 25)
        self.watch.refresh()
        self.assertEqual([x.entry_name for x in self.watch.items], ["GitLab PAT"], "a week later it is back")

    def test_open_goes_to_the_entry(self):
        dlg = self.watch.open_list()
        dlg.rows[0]["open"].click()
        pump()
        self.assertEqual(self.win.mode, "vault")
        self.assertIs(self.page.current, self.gone)

    def test_the_toast_once_a_day(self):
        self.watch.toast = None
        self.watch.on_unlock()
        self.assertIsNone(self.watch.toast, "not inside the unlock itself")
        self.watch._later.flush()
        toast = self.watch.toast
        self.assertIsNotNone(toast)
        self.assertIn("VPN (2 days ago)", toast.text.text())
        self.assertIn("GitLab PAT (in 3 days)", toast.text.text())
        self.assertEqual(self.settings()["shown"], "2026-10-18")
        self.watch.toast = None
        self.watch.on_unlock()
        self.watch._later.flush()
        self.assertIsNone(self.watch.toast, "once a day")
        self.watch.today = lambda: date(2026, 10, 19)
        self.watch.on_unlock()
        self.watch._later.flush()
        self.assertIsNotNone(self.watch.toast, "the next day again")

    def guard_down(self) -> None:
        vault = self.win.vault
        vault.security.vault_guard = GUARD_PIN
        vault.security.set_pin("2468")
        self.win.security.lock_secrets()

    def test_behind_the_guard_the_toast_is_a_count(self):
        self.guard_down()
        self.watch.toast = None
        self.watch.on_unlock()
        self.watch._later.flush()
        text = self.watch.toast.text.text()
        self.assertEqual(text, "2 keys expire soon")
        self.assertNotIn("VPN", text)
        self.assertEqual(self.win.main_screen.expiry_btn.text(), "2", "the number stays on the top bar")

    def test_behind_the_guard_the_list_asks_the_code_first(self):
        self.guard_down()
        with mock.patch("daykeeper.qt.lock.ask_secret") as prompt:
            self.assertIsNone(self.watch.open_list())
        prompt.assert_called_once()
        self.assertIsNone(self.watch.dialog, "no window before the code")
        accept, then = prompt.call_args.args[4], prompt.call_args.args[5]
        self.assertIs(accept("2468"), True)
        then()
        dlg = self.watch.dialog
        self.assertIsNotNone(dlg)
        self.assertEqual([r["item"].entry_name for r in dlg.rows], ["VPN", "GitLab PAT"], "the names after the code")

    def test_dismissals_of_deleted_entries_are_pruned(self):
        self.watch.never(self.item(self.gone))
        self.watch.remind_later(self.item(self.soon))
        seen = dict(self.settings()["seen"])
        seen["deleted-entry:valid until"] = "date:2026-10-21"
        self.watch._write(seen=seen)
        self.win.vault.remove_entry(self.soon)
        self.watch.refresh()
        self.assertEqual(self.settings()["seen"], {f"{self.gone.id}:expires": "date:2026-10-16"}, "an existing entry keeps its Do not remind")

    def test_do_not_remind_lasts_until_the_date_changes(self):
        key = f"{self.gone.id}:expires"
        self.watch.never(self.item(self.gone))
        self.watch.today = lambda: date(2026, 12, 1)
        self.watch.refresh()
        self.assertNotIn("VPN", [x.entry_name for x in self.watch.items], "weeks later, the same date: still quiet")
        self.gone.fields[0].value = "2026-12-05"  # the user sets a new date
        self.watch.refresh()
        self.assertIn("VPN", [x.entry_name for x in self.watch.items], "a new date brings the reminder back")
        self.assertNotIn(key, self.settings()["seen"], "the stale dismissal is dropped")

    def test_an_old_do_not_remind_is_kept_and_tied_to_the_date(self):
        key = f"{self.gone.id}:expires"
        self.watch._write(seen={key: expiry_ui.OLD_FOREVER})
        self.watch.refresh()
        self.assertNotIn("VPN", [x.entry_name for x in self.watch.items])
        self.assertEqual(self.settings()["seen"][key], "date:2026-10-16", "read, then tied to the date it has now")
        self.gone.fields[0].value = "2026-10-20"
        self.watch.refresh()
        self.assertIn("VPN", [x.entry_name for x in self.watch.items])

    def test_warnings_off_keep_the_dismissals(self):
        key = f"{self.gone.id}:expires"
        self.watch.never(self.item(self.gone))
        self.watch.set_days(0)
        self.assertEqual(self.settings()["seen"][key], "date:2026-10-16")

    def gitlab_check(self, expires, asked=True):
        """One watcher check with a fake answer (no network); the args it was called with."""
        result = CheckResult("me", {}, [], token_asked=asked, token_expires=expires)
        with mock.patch.object(integ_page, "gitlab_check", return_value=result) as check, mock.patch.object(integ_page.QApplication, "alert"):
            self.win.gitlab.check_now()
            self.assertTrue(wait_until(lambda: not self.win.gitlab._busy, 5000))
        return check.call_args.args

    def test_the_gitlab_token_expiry_lands_on_its_entry(self):
        vault = self.win.vault
        token = vault.add_entry(name="GitLab token", kind="token")
        token.fields[0].value = "glpat-sample"
        gl = vault.integrations.gitlab
        gl.url, gl.entry_id = "https://gitlab.example.com", token.id
        args = self.gitlab_check("2026-10-20")
        self.assertIs(args[7], True, "the first check asks the token's last day")
        field = next(f for f in token.fields if f.key == "valid until")
        self.assertEqual(field.value, "20.10.2026")
        self.watch._later.flush()
        self.assertIn(("GitLab token", 2), [(x.entry_name, x.days_left) for x in self.watch.items], "the reminders cover it")
        self.assertIs(self.gitlab_check(None, asked=False)[7], False, "not again the same day")

    def test_a_gitlab_that_does_not_say_leaves_the_field(self):
        vault = self.win.vault
        token = vault.add_entry(name="GitLab token", kind="token")
        token.fields[0].value = "glpat-sample"
        token.fields[2].value = "31.12.2026"  # the user's own date
        gl = vault.integrations.gitlab
        gl.url, gl.entry_id = "https://gitlab.example.com", token.id
        self.gitlab_check(None)  # 403 / 404: token_expiry answered None
        self.assertEqual(token.fields[2].value, "31.12.2026")
        self.gitlab_check("")  # a token without an end
        self.assertEqual(token.fields[2].value, "31.12.2026")

    def item(self, entry):
        return next(x for x in self.watch.items if x.entry_id == entry.id)

    def test_the_warning_window_in_settings(self):
        self.win.settings_dialog("security")
        dlg = self.win._settings_win
        dlg.unlocked = True
        dlg.show_section("security", rebuild=True)
        box = dlg.pages["security"].parts["expiry"]
        self.assertEqual(box.currentData(), 7)
        save = dlg.pages["security"].parts["save"]
        i = box.findData(30)
        box.setCurrentIndex(i)
        box.activated.emit(i)
        self.assertNotIn("days", self.settings(), "not before Save, like its neighbours")
        save()
        self.assertEqual(self.settings()["days"], 30)
        self.assertEqual(self.badge(self.far), None)
        i = box.findData(0)
        box.setCurrentIndex(i)
        box.activated.emit(i)
        save()
        self.assertEqual(self.settings()["days"], 0)
        self.assertEqual(self.watch.items, [])
        self.assertIsNone(self.badge(self.soon), "off: no badges")
        self.assertTrue(self.win.main_screen.expiry_btn.isHidden())

    def test_lock_forgets_the_list(self):
        self.win.lock()
        self.assertEqual(self.watch.items, [])
        self.assertTrue(self.win.main_screen.expiry_btn.isHidden())


class TotpTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.win = open_window(self.tmp.name)
        vault = self.win.vault
        self.entry = vault.add_entry(name="Mail")
        self.entry.fields[1].value = "pw"
        self.entry.fields.append(Field("2FA key", RFC_URI, secret=True))
        self.other = vault.add_entry(name="Plain", kind="note")
        self.win.save_now()  # the vault binds the field: its key is sealed from here on
        self.win.show_main()
        self.page = self.win.page("vault")
        self.page.clock = lambda: 59.0
        self.page.select_entry(self.entry)
        pump()

    def tearDown(self):
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()

    def row(self):
        return next(r for r in self.page.fields.rows if r.f.key == "2FA key")

    def test_the_card_shows_the_code_of_the_moment(self):
        f = self.row().f
        self.assertIsNotNone(f._sealed, "the key stays sealed in the vault")
        view = self.row().totp_view
        self.assertIsNotNone(view)
        self.assertEqual(view.code.text(), "9428 7082 · 1 s")
        self.assertEqual(view.ring.left, 1)
        self.assertTrue(self.page._totp_ticker.running)
        self.page.clock = lambda: 1111111109.0
        self.page.tick_totp()
        self.assertEqual(view.code.text(), "0708 1804 · 1 s")  # RFC 6238, SHA-1, T = 1111111109
        self.assertNotIn(RFC_KEY, view.code.text())

    def test_a_bad_key_says_so(self):
        row = self.row()
        row.value.setFocus()
        row.value.setText("not a key")
        row.value.textEdited.emit("not a key")
        self.assertFalse(row.totp_view.invalid.isHidden())
        self.assertTrue(row.totp_view.copy.isHidden())

    def test_copy_goes_as_a_secret(self):
        with mock.patch.object(self.win, "copy_to_clipboard", wraps=self.win.copy_to_clipboard) as copy:
            self.row().totp_view.copy.click()
        copy.assert_called_once_with(AT_59, secret=True)
        self.assertEqual(self.win.fake_clip.text, AT_59)
        self.assertTrue(self.win._clip_wipe.pending, "wiped after the delay, as a password")

    def test_the_ticks_run_only_while_the_code_is_seen(self):
        ticker = self.page._totp_ticker
        self.assertTrue(ticker.running)
        self.page.select_entry(self.other)
        self.assertFalse(ticker.running, "an entry without a 2FA key")
        self.page.select_entry(self.entry)
        self.assertTrue(ticker.running)
        self.win.show_page("journal")
        pump()
        self.assertFalse(ticker.running, "another tab")
        self.win.show_page("vault")
        pump()
        self.assertTrue(ticker.running)
        self.page._load_entry(None)
        self.assertFalse(ticker.running, "no card")
        self.page.select_entry(self.entry)
        vault = self.win.vault
        vault.security.vault_guard = GUARD_PIN
        vault.security.set_pin("2468")
        self.win.security.lock_secrets()
        self.assertFalse(ticker.running, "the guard locked")
        self.assertFalse(self.page.tick_totp())
        self.win.security.unlock_secrets()
        pump()
        self.assertTrue(ticker.running, "the entry comes back with its code")


class TileCodeTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.win = open_window(self.tmp.name)
        vault = self.win.vault
        self.entry = vault.add_entry(name="Git")
        self.entry.fields[0].value = "me@example.com"
        self.entry.fields[1].value = "tile-secret"
        self.entry.fields.append(Field("2FA key", RFC_KEY, secret=True))
        self.plain_entry = vault.add_entry(name="Wiki")
        self.plain_entry.fields[1].value = "x"
        self.tied = vault.bookmarks.add("GitLab", "https://git.example.com", "", entry_id=self.entry.id)
        self.no_code = vault.bookmarks.add("Wiki", "https://wiki.example.com", "", entry_id=self.plain_entry.id)
        vault.security.vault_guard = GUARD_PIN
        vault.security.set_pin("1357")
        self.win.show_main()
        self.win.show_page("bookmarks")
        pump()
        self.page = self.win.page("bookmarks")
        self.page.clock = lambda: 59.0

    def tearDown(self):
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()

    def zones(self, bm):
        board = self.page.board
        return dict(self.page.painter.zones(bm, board.rect_of(board.index_of(bm))))

    def test_the_shield_copies_the_code_behind_the_guard(self):
        self.assertIn("totp", self.zones(self.tied))
        self.assertNotIn("totp", self.zones(self.no_code))
        self.page.board.grab()  # paints the shield without an error
        self.win.security.lock_secrets()
        self.assertIn("totp", self.zones(self.tied), "the shield stays; it asks for the code")
        with mock.patch("daykeeper.qt.lock.ask_secret") as prompt:
            self.page._activate(self.tied, "totp")
        prompt.assert_called_once()
        self.assertIsNone(self.win.fake_clip.text, "nothing copied while locked")
        self.win.security.unlock_secrets()
        with mock.patch.object(self.win, "copy_to_clipboard", wraps=self.win.copy_to_clipboard) as copy:
            self.page._activate(self.tied, "totp")
        copy.assert_called_once_with("287082", secret=True)  # RFC 6238 at T = 59, six digits
        self.assertEqual(self.win.fake_clip.text, "287082")

    def ctrl_enter(self, bm) -> None:
        board = self.page.board
        board.setFocus()
        board.focused = board.index_of(bm)
        QTest.keyClick(board, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)

    def test_ctrl_enter_copies_the_code_of_the_selected_tile(self):
        with mock.patch("webbrowser.open") as browser:
            self.ctrl_enter(self.tied)
        browser.assert_not_called()
        self.assertEqual(self.win.fake_clip.text, "287082")
        self.assertTrue(self.win._clip_wipe.pending, "wiped after the delay, as a password")

    def test_ctrl_enter_without_a_2fa_key_says_so(self):
        self.ctrl_enter(self.no_code)
        self.assertIsNone(self.win.fake_clip.text)
        self.assertEqual(self.win.main_screen.status.text(), "This entry has no 2FA key")

    def test_ctrl_enter_does_nothing_behind_the_guard(self):
        self.win.security.lock_secrets()
        pump()
        with mock.patch("daykeeper.qt.lock.ask_secret") as prompt:
            self.ctrl_enter(self.tied)
        prompt.assert_not_called()
        self.assertIsNone(self.win.fake_clip.text)


if __name__ == "__main__":
    unittest.main()
