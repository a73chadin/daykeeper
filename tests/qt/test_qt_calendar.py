"""The Calendar tab of the Qt window: the tab in the bar and in Settings → Tabs, the chips
of a day on the month grid, the panel of a clicked day, the event editor with undo, the
.ics import, and the reminders (once each, never while locked)."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from tests.helpers import reset_globals
from tests.qt_helpers import close_window, open_window, pump, qt_app, require_qt

require_qt()

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QCheckBox  # noqa: E402

from daykeeper.calendar_model import MAX_REMIND_MIN  # noqa: E402
from daykeeper.journal import today  # noqa: E402
from daykeeper.qt.pages import calendar as calendar_page  # noqa: E402
from daykeeper.qt.pages.calendar import chips, month_title, normal_time  # noqa: E402
from daykeeper.qt.widgets.toast import Toast  # noqa: E402
from daykeeper.routines import Schedule  # noqa: E402
from daykeeper.settings import read_settings  # noqa: E402
from daykeeper.tabs import visible_tabs  # noqa: E402

ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:demo-1@example.test
DTSTART:20301010T100000
DTEND:20301010T110000
SUMMARY:{title}
END:VEVENT
END:VCALENDAR
"""


def setUpModule():
    qt_app()


class PureTest(unittest.TestCase):
    def test_the_tab_comes_last_for_an_order_saved_before_it(self):
        self.assertEqual(visible_tabs({})[-1], "calendar")
        self.assertEqual(visible_tabs({"tab_order": ["notes", "vault"]})[-1], "calendar")
        self.assertNotIn("calendar", visible_tabs({"tabs_hidden": ["calendar"]}))

    def test_time_is_normalised(self):
        self.assertEqual(normal_time("9:30"), "09:30")
        self.assertEqual(normal_time(" "), "")
        self.assertIsNone(normal_time("25:00"))
        self.assertIsNone(normal_time("noon"))

    def test_month_title_in_the_catalogue(self):
        from daykeeper.i18n import set_language

        set_language("ru")
        self.addCleanup(set_language, "en")
        self.assertEqual(month_title(2026, 10), "Октябрь 2026")


class CalendarTabTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.win = open_window(self.tmp.name)

    def tearDown(self):
        for t in list(Toast.stack):
            t.close()
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()

    def open_tab(self):
        self.win.show_page("calendar")
        pump()
        return self.win.page("calendar")

    def fill_today(self) -> str:
        v = self.win.vault
        day = today()
        jday = v.journal.get(day, create=True)
        jday.add("ok", "APP-1")
        note = v.notes.add(day, title="Plan")
        note.add_item("one")
        note.add_item("two", done=True)
        v.routines.add(name="Backup", schedule=Schedule(at="9:05", days=list(range(7))))
        v.calendar.add("Standup", day, time="11:00")
        for domain in ("journal", "notes", "routines", "calendar"):
            self.win.mark_dirty(domain)
        return day

    # ---- the tab

    def test_the_tab_appears_follows_ctrl_number_and_can_be_hidden(self):
        buttons = list(self.win.main_screen.tab_buttons)
        self.assertIn("calendar", buttons)
        self.win.show_tab_number(buttons.index("calendar") + 1)
        self.assertEqual(self.win.mode, "calendar")
        self.win.settings_dialog("tabs")
        pump()
        dlg = self.win._settings_win
        box = next(b for b in dlg.pages["tabs"].findChildren(QCheckBox) if "Calendar" in b.text())
        box.setChecked(False)
        pump()
        dlg.close()
        self.assertNotIn("calendar", self.win.main_screen.tab_buttons)
        self.assertIn("calendar", read_settings(self.win.session.settings_path)["tabs_hidden"])
        self.assertNotEqual(self.win.mode, "calendar")

    def test_the_grid_shows_chips_of_a_busy_day_and_redraws_on_changes(self):
        page = self.open_tab()
        day = self.fill_today()
        self.win.show_page("calendar")
        pump()
        kinds = [c.kind for c in chips(page.cell(day))]
        for kind in ("event", "routine", "journal", "notes"):
            self.assertIn(kind, kinds)
        self.assertIn("1/2", [c.text for c in chips(page.cell(day))])
        self.assertFalse(page.grid.grab().isNull())  # the paint runs through every cell
        self.win.vault.calendar.add("Demo", day, time="15:00")
        self.win.mark_dirty("calendar")
        self.win.show_page("calendar")
        self.assertEqual([e.title for e in page.cell(day).events], ["Standup", "Demo"])

    def test_clicking_a_day_fills_the_panel(self):
        page = self.open_tab()
        d = date.fromisoformat(today())
        other = date(d.year, d.month, 1 if d.day != 1 else 2).isoformat()
        event = self.win.vault.calendar.add("Review", other, time="10:00")
        self.win.mark_dirty("calendar")
        self.win.show_page("calendar")
        pump()
        i = page.grid.index_of(other)
        QTest.mouseClick(page.grid, Qt.MouseButton.LeftButton, pos=page.grid.cell_rect(i).center())
        self.assertEqual(page.selected, other)
        self.assertEqual(page.row_keys(), ["event:" + event.id])
        self.assertIn(str(int(other[8:])), page.day_title.text())

    def test_the_month_arrows_and_today(self):
        page = self.open_tab()
        start = (page.year, page.month)
        page.next_btn.click()
        self.assertNotEqual((page.year, page.month), start)
        page.today_btn.click()
        self.assertEqual((page.year, page.month, page.selected), start + (today(),))

    # ---- the editor

    def test_the_editor_adds_edits_deletes_and_undo_restores(self):
        page = self.open_tab()
        day = today()
        page.event_dialog(None, day)
        parts = page.dialog_parts
        parts["save"]()
        self.assertTrue(parts["error"].text(), "a title is required")
        parts["title"].setText("Standup")
        parts["time"].setText("25:00")
        parts["save"]()
        self.assertEqual(self.win.vault.calendar.events, [])
        parts["time"].setText("9:30")
        parts["remind"].setCurrentIndex(parts["remind"].findData(5))
        parts["url"].setText("file:///etc/passwd")
        self.assertFalse(parts["open_link"].isEnabled(), "only a web page is opened")
        parts["url"].setText("https://example.test/meet")
        self.assertTrue(parts["open_link"].isEnabled())
        parts["save"]()
        self.win._capture.flush()
        [event] = self.win.vault.calendar.events
        self.assertEqual((event.title, event.date, event.time, event.remind_min), ("Standup", day, "09:30", 5))
        self.assertIn("event:" + event.id, page.row_keys())

        page.event_dialog(event)
        parts = page.dialog_parts
        parts["title"].setText("Daily")
        parts["repeat"].setCurrentIndex(1)
        parts["save"]()
        self.win._capture.flush()
        self.assertEqual((event.title, event.repeat), ("Daily", "weekly"))

        page.event_dialog(event)
        page.dialog_parts["delete"]()
        self.win._capture.flush()
        self.assertEqual(self.win.vault.calendar.events, [])
        self.win.undo()
        pump()
        [back] = self.win.vault.calendar.events
        self.assertEqual((back.title, back.repeat), ("Daily", "weekly"))
        page = self.win.page("calendar")  # the undo rebuilt the tab
        self.assertIn("event:" + back.id, page.row_keys())

    def test_the_reminder_is_clamped_and_an_odd_value_kept(self):
        page = self.open_tab()
        odd = self.win.vault.calendar.add("Imported", today(), time="10:00", remind_min=7)
        big = self.win.vault.calendar.add("Too early", today(), time="10:00", remind_min=MAX_REMIND_MIN * 3)
        page.event_dialog(odd)
        self.assertEqual(page.dialog_parts["remind"].currentData(), 7)
        page.last_dialog.reject()
        page.event_dialog(big)
        page.dialog_parts["save"]()
        self.assertEqual(big.remind_min, MAX_REMIND_MIN)

    # ---- .ics

    def test_ics_import_adds_and_reimport_updates(self):
        page = self.open_tab()
        path = Path(self.tmp.name) / "meetings.ics"
        path.write_text(ICS.format(title="Demo"), encoding="utf-8")
        self.assertEqual(page.import_ics(str(path)), (1, 0))
        self.assertIn("1", self.win.main_screen.status.text())
        path.write_text(ICS.format(title="Demo moved"), encoding="utf-8")
        self.assertEqual(page.import_ics(str(path)), (0, 1))
        [event] = self.win.vault.calendar.events
        self.assertEqual((event.title, event.date, event.time), ("Demo moved", "2030-10-10", "10:00"))
        with mock.patch.object(calendar_page, "show_error") as told:
            self.assertEqual(page.import_ics(str(Path(self.tmp.name) / "missing.ics")), None)
        told.assert_called_once()

    # ---- reminders

    def test_a_reminder_fires_once_and_its_toast_opens_the_day(self):
        self.win.vault.calendar.add("Demo", "2030-01-15", time="10:00", remind_min=15)
        reminders = self.win.reminders
        reminders.now = lambda: datetime(2030, 1, 15, 9, 50)
        fired = reminders.tick()
        self.assertEqual([e.title for e in fired], ["Demo"])
        self.assertEqual(len(Toast.stack), 1)
        self.assertIn("10 min", Toast.stack[0].text.text())
        self.assertEqual(reminders.tick(), [])
        self.assertEqual(len(Toast.stack), 1)
        QTest.mouseClick(Toast.stack[0], Qt.MouseButton.LeftButton)
        pump()
        self.assertEqual(self.win.mode, "calendar")
        self.assertEqual(self.win.page("calendar").selected, "2030-01-15")

    def test_behind_the_guard_a_key_shows_without_its_name(self):
        from datetime import timedelta

        from daykeeper.security import GUARD_PIN
        from daykeeper.vault import Field

        v = self.win.vault
        day = (date.fromisoformat(today()) + timedelta(days=3)).isoformat()
        entry = v.add_entry(name="Secret project PAT", kind="token")
        entry.fields.append(Field("valid until", day))
        v.security.vault_guard = GUARD_PIN
        v.security.set_pin("2468")
        self.win.mark_dirty("vault")
        self.win.security.lock_secrets()
        page = self.open_tab()
        page.go_to(day)
        texts = [c.text for c in chips(page.cell(day)) if c.kind == "expiry"]
        self.assertEqual(texts, ["key expires"])
        rows = [page.day_list.item(i).text() for i in range(page.day_list.count())]
        self.assertTrue(any("key expires" in r for r in rows), rows)
        self.assertFalse(any("Secret project" in r for r in rows), "no entry name in the panel")
        self.assertFalse(page.grid.grab().isNull())
        self.win.security.unlock_secrets()
        page = self.open_tab()  # the guard is part of what the tab shows: it is rebuilt
        page.go_to(day)
        self.assertEqual([c.text for c in chips(page.cell(day)) if c.kind == "expiry"], ["Secret project PAT"])
        rows = [page.day_list.item(i).text() for i in range(page.day_list.count())]
        self.assertTrue(any("Secret project PAT" in r for r in rows), rows)

    def test_nothing_fires_while_locked(self):
        self.win.vault.calendar.add("Demo", "2030-01-15", time="10:00", remind_min=15)
        self.win.lock()
        pump()
        reminders = self.win.reminders
        reminders.now = lambda: datetime(2030, 1, 15, 9, 50)
        self.assertEqual(reminders.tick(), [])
        self.assertEqual(Toast.stack, [])

    def test_nothing_fires_while_the_tab_is_off(self):
        self.win.vault.calendar.add("Demo", "2030-01-15", time="10:00", remind_min=15)
        reminders = self.win.reminders
        reminders.now = lambda: datetime(2030, 1, 15, 9, 50)
        with mock.patch.object(self.win, "tab_on", lambda mode: mode != "calendar"):
            self.assertEqual(reminders.tick(), [])
        self.assertEqual(Toast.stack, [])
        self.assertEqual([e.title for e in reminders.tick()], ["Demo"], "the tab back on: the reminder still due is shown")


if __name__ == "__main__":
    unittest.main()
