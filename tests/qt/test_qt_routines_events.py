"""The Routine tab and integration events: the editor's “Also run on an event” block keeps
the rules and projects, the tile says it starts on an event, the run log names the event
behind a run and the runs the storm limit cut."""
from __future__ import annotations

import sys
import tempfile
import unittest
from unittest import mock

from tests.helpers import reset_globals
from tests.qt_helpers import close_window, open_window, pump, qt_app, require_qt, wait_until

require_qt()

from daykeeper import gitlab, jira  # noqa: E402
from daykeeper.gitlab import Event  # noqa: E402
from daykeeper.qt.pages import routines as routines_page  # noqa: E402
from daykeeper.qt.widgets.toast import Toast  # noqa: E402
from daykeeper.routines import Schedule  # noqa: E402


def setUpModule():
    qt_app()


class EventRulesTest(unittest.TestCase):
    def test_the_rule_codes_of_the_sources_do_not_overlap(self):
        """Schedule.on_events matches by the kind alone: a Jira code equal to a GitLab one
        would start a routine on the wrong integration."""
        self.assertEqual(set(gitlab.ALL_RULES) & set(jira.RULES), set())
        self.assertEqual(routines_page.event_rule_codes(), set(gitlab.ALL_RULES) | set(jira.RULES))

    def test_projects_are_split_on_commas_and_spaces(self):
        self.assertEqual(routines_page.parse_projects(" group/repo, group/other ;group/repo  sub/x/ "), ["group/repo", "group/other", "sub/x"])
        self.assertEqual(routines_page.parse_projects("  "), [])


class RoutineEventsTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.win = open_window(self.tmp.name)
        self.win.show_page("routines")
        pump()
        self.page = self.win.page("routines")

    def tearDown(self):
        for t in list(Toast.stack):
            t.close()
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()

    def _log_texts(self) -> list[str]:
        return [self.page.log.item(i).text() for i in range(self.page.log.count())]

    def test_the_editor_saves_and_reloads_the_events_and_projects(self):
        self.page.routine_dialog(None)
        parts = self.page.dialog_parts
        self.assertFalse(parts["projects"].isVisible(), "no rule ticked: the details stay hidden")
        parts["name"].setText("On merge")
        parts["target"].setText("echo hi")
        parts["events"]["merged"].setChecked(True)
        parts["events"]["status"].setChecked(True)
        self.assertTrue(parts["projects"].isVisible())
        parts["projects"].setText("group/repo, group/other,")
        parts["save"]()
        r = self.win.vault.routines.ordered()[0]
        self.assertEqual(r.schedule.on_events, ["merged", "status"])
        self.assertEqual(r.schedule.projects, ["group/repo", "group/other"])
        self.page.routine_dialog(r)
        parts = self.page.dialog_parts
        self.assertEqual({code for code, box in parts["events"].items() if box.isChecked()}, {"merged", "status"})
        self.assertEqual(parts["projects"].text(), "group/repo, group/other")
        self.page.last_dialog.reject()

    def test_editing_keeps_the_events_and_a_rule_it_does_not_know(self):
        r = self.win.vault.routines.add(name="Old", kind="command", target="echo hi", schedule=Schedule(on_events=["watch_failed", "later_rule"], projects=["group/repo"]))
        self.page.routine_dialog(r)
        parts = self.page.dialog_parts
        parts["name"].setText("Renamed")
        parts["save"]()
        self.assertEqual(r.name, "Renamed")
        self.assertEqual(r.schedule.on_events, ["watch_failed", "later_rule"])
        self.assertEqual(r.schedule.projects, ["group/repo"])
        self.page.routine_dialog(r)
        self.page.dialog_parts["events"]["watch_failed"].setChecked(False)
        self.page.dialog_parts["projects"].setText("")
        self.page.dialog_parts["save"]()
        self.assertEqual(r.schedule.on_events, ["later_rule"], "unticked goes; the unknown code stays")
        self.assertEqual(r.schedule.projects, [])

    def test_the_command_warning_is_shown_only_for_a_command(self):
        self.page.routine_dialog(None)
        parts = self.page.dialog_parts
        parts["events"]["merged"].setChecked(True)
        for i, kind in enumerate(routines_page.KINDS):
            parts["kind"].setCurrentIndex(i)
            self.assertEqual(parts["command_warning"].isVisible(), kind == "command", kind)
            self.assertEqual("{title}" in parts["event_help"].text(), kind == "script", kind)
            self.assertEqual("DAYKEEPER_TITLE" in parts["event_help"].text(), kind in ("script", "command"), kind)
        self.page.last_dialog.reject()

    def test_the_tile_has_an_on_event_chip(self):
        plain = self.win.vault.routines.add(name="Plain", kind="url", target="https://example.com")
        on_event = self.win.vault.routines.add(name="Hooked", kind="url", target="https://example.com", schedule=Schedule(on_events=["merged"]))
        self.page.refresh()
        self.assertNotIn("📡 on event", self.page.painter.chips(plain))
        self.assertIn("📡 on event", self.page.painter.chips(on_event))
        self.assertEqual(routines_page.icons.name_of("📡"), "broadcast", "the chip's glyph is drawn")
        self.assertGreater(self.page.painter.height(on_event, 300), self.page.painter.height(plain, 300), "the chip row takes room")

    def test_an_event_run_shows_its_ref_in_the_log(self):
        self.win.background_jobs = True
        r = self.win.vault.routines.add(name="On merge", kind="command", target=f'"{sys.executable}" -c "print(1)"', schedule=Schedule(on_events=["merged"]))
        self.win.runs.run_routine(r)  # by hand: no event mark
        self.win.hub.publish([Event("merged", "group/repo!7", "Ship it", "", "Merged")])
        self.assertTrue(wait_until(lambda: all(not run.running for run in self.win.runs.runs), 15_000))
        self.page.refresh_sidebar()
        texts = self._log_texts()
        self.assertEqual(len(texts), 2)
        self.assertTrue(texts[0].endswith("On merge · group/repo!7"), texts)
        self.assertTrue(texts[1].endswith("On merge"), texts)
        self.assertIn("Ship it", self.page.log.item(0).toolTip())

    def test_runs_the_storm_limit_cut_show_in_the_log(self):
        self.win.background_jobs = True
        self.win.vault.routines.add(name="Each", kind="url", target="https://example.com", schedule=Schedule(on_events=["merged"]))
        events = [Event("merged", f"group/repo!{i}", "T", "", "Merged") for i in range(routines_page.EVENT_RUNS_MAX + 3)]
        with mock.patch.object(self.win.runs, "run_routine"):
            self.win.hub.publish(events)
        self.assertEqual(self.win.runs.skipped[0][1], 3)
        self.assertTrue(any(t.endswith("skipped 3 by event") for t in self._log_texts()), self._log_texts())


if __name__ == "__main__":
    unittest.main()
