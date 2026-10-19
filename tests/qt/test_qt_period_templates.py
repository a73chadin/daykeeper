"""The journal over a period and the text templates in the Qt window: the report window
counts what stats.summarize counts, a click on the heat map opens the day, the text is
copied and saved; the Templates section seeds the ready-made ones and saves; a template
asks its questions in one form, keeps unknown braces and goes into the (fake) clipboard."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from tests.helpers import reset_globals
from tests.qt_helpers import close_window, open_window, pump, qt_app, require_qt

require_qt()

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit  # noqa: E402

from daykeeper import journal, stats, templates  # noqa: E402
from daykeeper.i18n import set_language  # noqa: E402
from daykeeper.journal_layout import ROLE_NOTE, current as current_layout  # noqa: E402
from daykeeper.qt.dialogs import settings_templates  # noqa: E402
from daykeeper.qt.dialogs import stats as stats_dialog  # noqa: E402
from daykeeper.settings import Links, read_settings  # noqa: E402


def setUpModule():
    qt_app()


def fill_form(answers: list[str]) -> None:
    """Answer the template form that is about to open (it blocks in exec())."""

    def fill() -> None:
        dlg = QApplication.activeModalWidget()
        if dlg is None:
            QTimer.singleShot(10, fill)
            return
        for edit, text in zip(dlg.findChildren(QLineEdit), answers, strict=False):
            edit.setText(text)
        dlg.accept()

    QTimer.singleShot(0, fill)


class Base(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.win = open_window(self.tmp.name)
        self.win.show_page("journal")
        pump()
        self.page = self.win.page("journal")

    def tearDown(self):
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()


class PeriodReportTest(Base):
    def setUp(self):
        super().setUp()
        layout = current_layout()
        self.columns = layout.visible_columns()
        self.note = next(k for k in layout.visible_kinds() if k.role == ROLE_NOTE)
        self.start = date(2026, 3, 2)  # a Monday
        j = self.win.vault.journal
        for n in range(0, 30, 3):
            day = j.get((self.start + timedelta(days=n)).isoformat(), create=True)
            for i in range(n % 4 + 1):
                day.add(self.columns[0].id, f"DEMO-{n}{i}")
            if len(self.columns) > 1:
                day.add(self.columns[1].id, f"DEMO-9{n}")
            day.add_activity(self.note.id, "standup" if n % 2 else f"call {n}")
        self.span = stats.custom(self.start, self.start + timedelta(days=30))
        self.dlg = self.page.period_dialog()
        self.dlg.set_period(stats_dialog.CUSTOM, self.span.start, self.span.end)
        pump()

    def tearDown(self):
        self.dlg.close()
        super().tearDown()

    def test_the_numbers_are_those_of_the_summary(self):
        want = stats.summarize(self.win.vault.journal, self.span, current_layout())
        self.assertEqual(self.dlg.summary, want)
        for cid, n in want.columns.items():
            self.assertEqual(self.dlg.card_widgets[cid].property("value"), n)
        for kid, n in want.kinds.items():
            self.assertEqual(self.dlg.card_widgets[kid].property("value"), n)
        self.assertEqual(self.dlg.card_widgets["_total"].property("value"), want.total)
        self.assertEqual(self.dlg.table.rowCount(), len(want.per_week))
        self.assertTrue(self.dlg.table.isVisibleTo(self.dlg))
        self.assertIn("standup — 5", self.dlg.notes.text())

    def test_a_named_period(self):
        self.dlg.set_period("week")
        self.assertEqual(self.dlg.summary.period, stats.period("week", date.fromisoformat(journal.today())))
        self.assertFalse(self.dlg.table.isVisibleTo(self.dlg), "one week: no table by weeks")
        self.assertFalse(self.dlg.start.isVisibleTo(self.dlg))

    def test_a_click_on_the_heat_map_opens_the_day(self):
        day = (self.start + timedelta(days=9)).isoformat()
        heat = self.dlg.heat
        self.assertEqual(heat.day_at(heat.cell_rect(day).center()), day)
        self.assertIn("Total: ", heat.tip(day))
        QTest.mouseClick(heat, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, heat.cell_rect(day).center())
        pump()
        self.assertEqual(self.page.jday.date, day)
        empty = (self.start + timedelta(days=1)).isoformat()
        self.assertFalse(self.dlg.open_day(empty), "a day the journal does not have")

    def test_copy_as_text_and_save(self):
        self.dlg.copy_text()
        self.assertEqual(self.win.fake_clip.text, stats.as_markdown(self.dlg.summary, current_layout()))
        target = Path(self.tmp.name) / "report.md"
        with mock.patch.object(stats_dialog.QFileDialog, "getSaveFileName", return_value=(str(target), "")):
            self.assertEqual(self.dlg.save_md(), target)
        self.assertEqual(target.read_text(encoding="utf-8").strip(), self.win.fake_clip.text.strip())

    def test_the_shortcut_is_there(self):
        self.assertIn("Ctrl+Shift+R", self.win.actions_by_keys)


class HeatMapCaptionTest(unittest.TestCase):
    """Month names over the heat map, one a month, over the week of its first day."""

    def tearDown(self):
        reset_globals()

    def captions(self, start: date, end: date) -> list[tuple[int, str]]:
        heat = stats_dialog.HeatMap(lambda _d: "", lambda _d: None)
        days = (end - start).days + 1
        heat.set_days({(start + timedelta(days=k)).isoformat(): 0 for k in range(days)})
        try:
            return heat.month_captions()
        finally:
            heat.deleteLater()

    def test_month_names_with_the_year_first_and_on_january(self):
        got = self.captions(date(2026, 10, 1), date(2027, 1, 31))  # Thu 1 Oct 2026
        self.assertEqual([t for _w, t in got], ["Oct 2026", "Nov", "Dec", "Jan 2027"])
        self.assertEqual([w for w, _t in got], [0, 4, 9, 13], "the week that holds the 1st (weeks from Mon 28 Sep): Sun 1 Nov — 4, Tue 1 Dec — 9, Fri 1 Jan — 13")

    def test_a_period_from_mid_month(self):
        self.assertEqual(self.captions(date(2026, 9, 15), date(2026, 10, 20)), [(0, "Sep 2026"), (2, "Oct")])
        self.assertEqual(self.captions(date(2026, 9, 29), date(2026, 10, 20))[0], (0, "Oct 2026"), "the 1st in the first week names it")

    def test_russian_month_names(self):
        set_language("ru")
        self.assertEqual([t for _w, t in self.captions(date(2026, 10, 1), date(2027, 1, 31))], ["Окт 2026", "Ноя", "Дек", "Янв 2027"])


class TemplatesTest(Base):
    def put_templates(self, items):
        self.win.session.write_settings({"templates": [t.to_dict() for t in items]})

    def test_the_section_seeds_the_presets_and_saves(self):
        settings_path = self.win.vault_path.parent / "settings.json"
        self.assertNotIn("templates", read_settings(settings_path))
        self.win.settings_dialog("templates")
        pump()
        sec = self.win._settings_win.pages["templates"]
        self.assertEqual([t["id"] for t in read_settings(settings_path)["templates"]], [t.id for t in templates.presets()])
        self.assertEqual(sec.list.count(), 3)
        sec.add()
        sec.name.setText("Mine")
        sec._edited()
        sec.text.setPlainText("Hello {me}")
        sec.places["notes"].setChecked(True)
        sec.places["quick"].setChecked(False)
        self.assertTrue(sec.save())
        saved = templates.load(read_settings(settings_path))
        self.assertEqual(saved[-1].name, "Mine")
        self.assertEqual(saved[-1].text, "Hello {me}")
        self.assertEqual(saved[-1].places, ["journal", "notes"])
        self.assertIn("Mine", sec.list.item(3).text())
        sec.delete(confirm=False)
        self.assertTrue(sec.save())
        self.assertEqual(len(templates.load(read_settings(settings_path))), 3)
        self.win.close_settings()

    def test_a_template_asks_renders_and_copies(self):
        journal.configure(Links(jira_url="https://example.com"))
        t = templates.Template("t1", "Check", "{key} on {?Env}: {key_url} {unknown}", ["journal"], open_link=True)
        self.put_templates([t])
        self.win.vault.integrations.gitlab.username = "demo"
        column = current_layout().visible_columns()[0]
        field = self.page.sections[column.id]
        field.setPlainText("DEMO-1\nDEMO-2")
        field.setFocus()
        cursor = field.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        field.setTextCursor(cursor)
        pump()
        context = self.page.selection_context()
        self.assertEqual(context, {"key": "DEMO-2", "key_url": "https://example.com/browse/DEMO-2"})
        menu = self.page.template_menu()
        names = [a.text() for a in menu.actions()]
        self.assertIn("Check", names)
        menu.close()
        fill_form(["qa"])
        with mock.patch.object(settings_templates.webbrowser, "open") as browser:
            text = settings_templates.use_template(self.win, t, context)
        self.assertEqual(text, "DEMO-2 on qa: https://example.com/browse/DEMO-2 {unknown}")
        self.assertEqual(self.win.fake_clip.text, text)
        browser.assert_called_once_with("https://example.com/browse/DEMO-2")

    def test_no_selected_line_asks_the_key_and_a_cancel_copies_nothing(self):
        t = templates.Template("t2", "Plain", "{key} by {me}", ["journal"])
        self.win.vault.integrations.gitlab.username = "demo"
        fill_form(["DEMO-7"])
        with mock.patch.object(settings_templates.webbrowser, "open") as browser:
            self.assertEqual(settings_templates.use_template(self.win, t, {}), "DEMO-7 by demo")
        browser.assert_not_called()
        self.win.fake_clip.text = None

        def cancel() -> None:
            dlg = QApplication.activeModalWidget()
            if dlg is None:
                QTimer.singleShot(10, cancel)
                return
            dlg.reject()

        QTimer.singleShot(0, cancel)
        self.assertIsNone(settings_templates.use_template(self.win, t, {}))
        self.assertIsNone(self.win.fake_clip.text)

    def test_insert_template_from_a_note(self):
        self.put_templates([templates.Template("n1", "Note text", "# {note_title}\n{note_items}", ["notes"])])
        self.win.show_page("notes")
        pump()
        notes_page = self.win.page("notes")
        note = self.win.vault.notes.add(journal.today(), title="Plan")
        note.add_item("first")
        from PySide6.QtWidgets import QMenu

        menu = QMenu()
        notes_page._fill_template_menu(menu, note)
        action = next(a for a in menu.actions() if a.text() == "Note text")
        action.trigger()
        self.assertEqual(self.win.fake_clip.text, "# Plan\n- first")
        menu.deleteLater()


if __name__ == "__main__":
    unittest.main()
