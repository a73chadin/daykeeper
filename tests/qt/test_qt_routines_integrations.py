"""The Routine and Integrations tabs of the Qt window: a routine runs and reports, the
schedule and the script acceptance; the GitLab check fills the feed, the badge and the
toasts; the settings are kept."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from unittest import mock

from tests.helpers import reset_globals
from tests.qt_helpers import close_window, open_window, pump, qt_app, require_qt, type_text, wait_until

require_qt()

import shiboken6  # noqa: E402
from PySide6.QtCore import QPoint  # noqa: E402

from daykeeper.gitlab import CheckResult, Event  # noqa: E402
from daykeeper.integrations import Integrations  # noqa: E402
from daykeeper.qt.pages import integrations as integ_page  # noqa: E402
from daykeeper.qt.pages import routines as routines_page  # noqa: E402
from daykeeper.qt.widgets.toast import Toast  # noqa: E402
from daykeeper.routines import Schedule  # noqa: E402


def setUpModule():
    qt_app()


class RoutinesTest(unittest.TestCase):
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

    def test_the_editor_makes_a_tile(self):
        self.page.routine_dialog(None)
        parts = self.page.dialog_parts
        parts["kind"].setCurrentIndex(1)  # command
        type_text(parts["name"], "Say hi")
        parts["target"].setText("echo hi")
        parts["at"].setText("9:3x")
        parts["save"]()
        self.assertEqual(parts["error"].text(), "Time must look like 09:30")
        parts["at"].setText("09:30")
        parts["save"]()
        routines = self.win.vault.routines.ordered()
        self.assertEqual([r.name for r in routines], ["Say hi"])
        self.assertEqual([it.kind for it in self.page.board.items], ["heading", "tile"])

    def test_a_command_runs_reports_and_shows_its_output(self):
        r = self.win.vault.routines.add(name="Echo", kind="command", target=f'"{sys.executable}" -c "print(\'from the run\')"')
        self.page.refresh()
        self.win.runs.run_routine(r)
        run = self.win.runs.latest(r)
        self.assertIsNotNone(run)
        self.assertTrue(wait_until(lambda: not run.running and r.last_code is not None, 15_000), "the run ended")
        self.assertEqual(run.code, 0)
        self.assertTrue(any("done in" in t.text.text() for t in Toast.stack), "a toast says it is done")
        window = self.win.runs.open_output(run)
        self.assertIn("from the run", window.text.toPlainText())
        self.assertEqual(self.page.log.count(), 1)
        self.win.lock()
        pump()
        self.assertEqual(Toast.stack, [], "the toast went with the lock")

    def test_an_auto_run_waits_for_an_accepted_script(self):
        script = f"{self.tmp.name}/job.py"
        with open(script, "w", encoding="utf-8") as fh:
            fh.write("print(1)\n")
        r = self.win.vault.routines.add(name="Job", kind="script", target=script, schedule=Schedule(True, "", []))
        self.win.runs.run_routine(r, auto=True)
        self.assertIsNone(self.win.runs.latest(r), "never accepted: it does not start by itself")
        self.assertTrue(any("accept the script" in t.text.text() for t in Toast.stack))

    def _wait_output(self, r):
        run = self.win.runs.latest(r)
        self.assertIsNotNone(run, "the event started it")
        self.assertTrue(wait_until(lambda: not run.running and r.last_code is not None, 15_000), "the run ended")
        return run

    def test_an_event_starts_a_command_with_its_variables_and_the_line_as_typed(self):
        self.win.background_jobs = True
        line = f'"{sys.executable}" -c "import os; print(os.environ[\'DAYKEEPER_REF\'] + \'|\' + os.environ[\'DAYKEEPER_TITLE\'] + \'|{{title}}\')"'
        r = self.win.vault.routines.add(name="On merge", kind="command", target=line, schedule=Schedule(on_events=["merged"]))
        self.win.hub.publish([Event("merged", "g/r!7", "Fix & ship", "https://gitlab.example.com/g/r/-/merge_requests/7", "Merged")])
        run = self._wait_output(r)
        self.assertEqual(run.code, 0, run.lines)
        self.assertIn("g/r!7|Fix & ship|{title}", run.lines, "the event comes through the environment; the braces of a command stay as typed")
        self.assertEqual(r.target, line)
        self.assertGreater(r.last_run, 0, "the run is marked on the routine itself")

    def test_an_event_fills_the_arguments_of_a_script_quoted(self):
        self.win.background_jobs = True
        r = self.win.vault.routines.add(
            name="Py", kind="script", target=sys.executable, schedule=Schedule(on_events=["merged"]),
            args='-c "import os, sys; print(sys.argv[1:], os.environ[\'DAYKEEPER_EVENT\'])" {title}',
        )
        args = r.args
        routines_page.accept_script(r)
        self.win.hub.publish([Event("merged", "g/r!8", "Two words", "", "Merged")])
        run = self._wait_output(r)
        self.assertEqual(run.code, 0, run.lines)
        self.assertIn("['Two words'] merged", run.lines, "a title with a space stays one argument")
        self.assertEqual(r.args, args, "the routine keeps its braces")

    def test_an_event_respects_the_accepted_script(self):
        self.win.background_jobs = True
        script = f"{self.tmp.name}/job.py"
        with open(script, "w", encoding="utf-8") as fh:
            fh.write("print(1)\n")
        r = self.win.vault.routines.add(name="Job", kind="script", target=script, schedule=Schedule(on_events=["merged"]))
        self.win.hub.publish([Event("merged", "g/r!9", "T", "", "Merged")])
        self.assertIsNone(self.win.runs.latest(r), "never accepted: an event does not start it")

    def test_a_storm_of_events_starts_at_most_the_limit(self):
        self.win.background_jobs = True
        self.win.vault.routines.add(name="Each", kind="url", target="https://example.com", schedule=Schedule(on_events=["merged"]))
        events = [Event("merged", f"g/r!{i}", "T", "", "Merged") for i in range(routines_page.EVENT_RUNS_MAX + 2)]
        with mock.patch.object(self.win.runs, "run_routine") as run:
            self.win.hub.publish(events)
        self.assertEqual(run.call_count, routines_page.EVENT_RUNS_MAX)
        self.assertEqual([c.kwargs["event"].mr for c in run.call_args_list], [e.mr for e in events[: routines_page.EVENT_RUNS_MAX]])
        self.assertIn("2 more not started", self.win.main_screen.status.text())

    def test_events_start_nothing_without_background_jobs(self):
        self.win.vault.routines.add(name="Each", kind="url", target="https://example.com", schedule=Schedule(on_events=["merged"]))
        with mock.patch.object(self.win.runs, "run_routine") as run:
            self.win.hub.publish([Event("merged", "g/r!1", "T", "", "Merged")])  # a preview copy
        run.assert_not_called()

    def test_menu_and_delete(self):
        r = self.win.vault.routines.add(name="X", kind="url", target="https://example.com")
        self.page.refresh()
        self.page.routine_menu(r, QPoint(0, 0))
        self.assertIn("Edit…", [a.text() for a in self.page.last_menu.actions()])
        self.page.last_menu.close()
        with mock.patch.object(routines_page, "ask_yes_no", return_value=True):
            self.page._delete(r)
        self.assertEqual(self.win.vault.routines.ordered(), [])


class IntegrationsTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.win = open_window(self.tmp.name)
        vault = self.win.vault
        entry = vault.add_entry(name="GitLab token", kind="token")
        entry.fields[0].value = "glpat-test"
        gl = vault.integrations.gitlab
        gl.url = "https://gitlab.example.com"
        gl.entry_id = entry.id
        self.win.show_page("integrations")
        pump()
        self.page = self.win.page("integrations")

    def tearDown(self):
        for t in list(Toast.stack):
            t.close()
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()

    def test_a_check_fills_the_feed_the_badge_and_the_toasts(self):
        events = [Event("approved", "g/r!1", "Fix the thing", "https://gitlab.example.com/g/r/-/merge_requests/1", "Approved by {who}", {"who": "@ann"})]
        result = CheckResult("me", {"g/r!1": {}}, events, ["g/r"])
        self.win.vault.integrations.gitlab_state = {"g/r!1": {}}  # not the first check
        with mock.patch.object(integ_page, "gitlab_check", return_value=result) as check, mock.patch.object(integ_page.QApplication, "alert") as alert:
            self.win.gitlab.check_now()
            self.assertTrue(wait_until(lambda: not self.win.gitlab._busy, 5000))
        self.assertEqual(check.call_args.args[1], "glpat-test", "the token comes from the entry")
        alert.assert_called_once()
        self.assertEqual(self.page.feed.count(), 1)
        self.assertIn("(1)", self.win.main_screen.tab_buttons["integrations"].text())
        self.assertTrue(any("GitLab" in t.findChild(type(t.text)).text() for t in Toast.stack))
        with mock.patch("webbrowser.open") as browser:
            self.page._open_item(self.page.feed.item(0))
        browser.assert_called_once()
        self.assertEqual(self.win.vault.integrations.unread(), 0)

    def test_a_check_publishes_its_events_and_passes_the_watched_branches(self):
        events = [Event("merged", "g/r!2", "Ship it", "", "Merged")]
        result = CheckResult("me", {"g/r!2": {}}, events, ["g/r"], pipelines={"g/r@main": {"status": "success"}})
        integ = self.win.vault.integrations
        integ.gitlab_state = {"g/r!2": {}}
        integ.gitlab_pipelines = {"g/r@main": {"status": "failed"}}
        got = []
        self.win.hub.subscribe(got.extend)
        with mock.patch.object(integ_page, "gitlab_check", return_value=result) as check, mock.patch.object(integ_page.QApplication, "alert"):
            self.win.gitlab.check_now()
            self.assertTrue(wait_until(lambda: not self.win.gitlab._busy, 5000))
        self.assertEqual(check.call_args.args[6], {"g/r@main": {"status": "failed"}}, "the last look at the branches goes to the check")
        self.assertEqual([e.mr for e in got], ["g/r!2"])
        self.assertEqual(integ.gitlab_pipelines, {"g/r@main": {"status": "success"}})

    def test_a_failing_subscriber_goes_to_the_error_log(self):
        def broken(events):
            raise RuntimeError("subscriber broke")

        got = []
        self.win.hub.subscribe(broken)
        self.win.hub.subscribe(got.extend)
        self.win.hub.publish([Event("merged", "g/r!3", "T", "", "Merged")])
        self.assertEqual(len(got), 1, "the others still get the batch")
        log = (self.win.vault_path.parent / "errors.log").read_text(encoding="utf-8")
        self.assertIn("subscriber broke", log)

    def test_a_failed_check_says_so(self):
        with mock.patch.object(integ_page, "gitlab_check", side_effect=integ_page.GitLabError("Token rejected")):
            self.win.gitlab.check_now()
            self.assertTrue(wait_until(lambda: not self.win.gitlab._busy, 5000))
        self.assertIn("Check failed", self.win.gitlab.status)

    def test_settings_are_kept(self):
        self.page.render_page()
        checks = [w for w in self.page.settings.findChildren(integ_page.QCheckBox) if w.text() == "Watch"]
        checks[0].setChecked(True)
        self.assertTrue(self.win.vault.integrations.gitlab.enabled)

    def test_the_feed_goes_under_the_settings_when_narrow(self):
        self.win.resize(1600, 800)
        pump()
        self.assertTrue(self.page._aside)
        self.win.resize(900, 800)
        pump()
        self.assertFalse(self.page._aside)

    def test_a_watch_is_added_and_removed(self):
        self.page.render_page()
        dlg = self.page.watch_dialog()
        parts = self.page.watch_parts
        parts["save"]()
        self.assertEqual(parts["error"].text(), "Fill in the project and the branch")
        type_text(parts["project"], "group/repo")
        type_text(parts["ref"], "main")
        parts["source"].setCurrentIndex(1)  # by schedule
        type_text(parts["name"], "Nightly")
        parts["save"]()
        pump()
        gl = self.win.vault.integrations.gitlab
        self.assertEqual([(w.project, w.ref, w.source, w.name) for w in gl.pipelines], [("group/repo", "main", "schedule", "Nightly")])
        self.assertFalse(shiboken6.isValid(dlg) and dlg.isVisible(), "the dialog closed")
        key = gl.pipelines[0].key()
        self.assertIn("Nightly — group/repo · main · by schedule", self.page.watch_rows[key].text())
        self.assertEqual(Integrations.from_dict(self.win.vault.integrations.to_dict()).gitlab.pipelines, gl.pipelines, "kept in the vault")
        self.page.watch_dialog()
        again = self.page.watch_parts
        type_text(again["project"], "group/repo")
        type_text(again["ref"], "main")
        again["source"].setCurrentIndex(1)
        again["save"]()
        self.assertEqual(again["error"].text(), "This branch is already watched")
        again["dialog"].reject()
        self.win.vault.integrations.gitlab_pipelines[key] = {"error": "HTTP 404 on /projects"}
        self.page.render_page()
        self.assertEqual(self.page.watch_errors[key].text(), "HTTP 404 on /projects", "the row's last error shows next to it")
        self.page.remove_watch(key)
        self.assertEqual(gl.pipelines, [])
        self.assertNotIn(key, self.win.vault.integrations.gitlab_pipelines)
        self.assertEqual(self.page.watch_rows, {})

    def test_the_watch_rules_are_saved(self):
        self.page.render_page()
        gl = self.win.vault.integrations.gitlab
        self.assertFalse(gl.rule("watch_fixed"))
        self.page.watch_ticks["watch_fixed"].setChecked(True)
        self.page.watch_ticks["watch_failed"].setChecked(False)
        self.assertTrue(gl.rule("watch_fixed"))
        self.assertFalse(gl.rule("watch_failed"))
        self.assertEqual(set(self.page.watch_ticks), set(integ_page.WATCH_RULES))

    def test_a_pipeline_event_shows_in_the_feed_with_its_icon(self):
        events = [
            Event("watch_failed", "g/r@main", "Nightly", "https://gitlab.example.com/g/r/-/pipelines/9", "The pipeline failed on {name}", {"name": "Nightly"}),
            Event("branch_commits", "g/r@main", "Nightly", "", "{n} new commits on {name}, the last by {author}", {"name": "Nightly", "n": 2, "author": "Ann"}),
        ]
        result = CheckResult("me", {}, events, [], pipelines={"g/r@main": {"status": "failed"}})
        self.win.vault.integrations.gitlab_state = {"g/r!1": {}}
        with mock.patch.object(integ_page, "gitlab_check", return_value=result), mock.patch.object(integ_page.QApplication, "alert"):
            self.win.gitlab.check_now()
            self.assertTrue(wait_until(lambda: not self.win.gitlab._busy, 5000))
        shown = [self.page.feed.item(i).data(integ_page.EVENT_ROLE) for i in range(self.page.feed.count())]
        self.assertEqual({integ_page.event_icon(ev) for ev in shown}, {"pipe_failed", "commit"})
        for name in ("pipe_failed", "pipe_fixed", "commit", "branch"):
            self.assertIn(name, integ_page.icons.DRAW, "a drawn picture, not a glyph")
        failed = next(ev for ev in shown if ev.kind == "watch_failed")
        self.assertEqual(integ_page.event_text(failed), "The pipeline failed on Nightly")
        self.assertFalse(self.page.feed.grab().isNull())

    def test_jira_has_its_form_and_the_feed(self):
        self.page.connections.setCurrentRow(1)
        pump()
        self.assertTrue(self.page.feed_panel.isVisible(), "Jira events share the feed")
        self.assertEqual(self.page.status_owner, "jira")
        self.assertIn("url", self.page.jira_parts)


def _issue(key: str, status: str, summary: str = "Fix the login") -> dict:
    return {
        "key": key,
        "fields": {
            "summary": summary, "status": {"name": status, "statusCategory": {"key": "indeterminate"}},
            "assignee": {"name": "me", "key": "me"}, "reporter": {"name": "me", "key": "me"},
            "issuetype": {"name": "Story"}, "comment": {"comments": [], "total": 0},
            "created": "2026-10-14T10:00:00.000+0000", "updated": "2026-10-14T10:00:00.000+0000",
        },
    }


class FakeJira:
    """A fake `fetch` of JiraClient: answers by the path, records every call; never the network."""

    def __init__(self, issues=(), status: int = 200):
        self.issues = list(issues)
        self.status = status
        self.calls: list[tuple[str, str, bytes | None]] = []

    def __call__(self, url, headers, method="GET", data=None):
        self.calls.append((method, url, data))
        if self.status != 200:
            return self.status, b"{}"
        if "/myself" in url:
            return 200, json.dumps({"name": "me", "key": "me", "displayName": "Me"}).encode()
        if "/transitions" in url:
            if method == "POST":
                return 204, b""
            return 200, json.dumps({"transitions": [{"id": "31", "name": "Done", "to": {"name": "Done"}}]}).encode()
        if "/search" in url:
            return 200, json.dumps({"issues": self.issues, "total": len(self.issues)}).encode()
        return 404, b"{}"


class JiraTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.win = open_window(self.tmp.name)
        vault = self.win.vault
        entry = vault.add_entry(name="Jira token", kind="token")
        entry.fields[0].value = "jira-test-token"
        self.entry = entry
        js = vault.integrations.jira
        js.url = "https://jira.example.com"
        js.entry_id = entry.id
        self.fake = FakeJira([_issue("DEMO-1", "Ready for test")])
        self.win.jira.fetch = self.fake
        self.win.show_page("integrations")
        pump()
        self.page = self.win.page("integrations")
        self.page.connections.setCurrentRow(1)
        pump()

    def tearDown(self):
        for t in list(Toast.stack):
            t.close()
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()

    def _check(self):
        with mock.patch.object(integ_page.QApplication, "alert") as alert:
            self.win.jira.check_now()
            self.assertTrue(wait_until(lambda: not self.win.jira._busy, 5000))
        return alert

    def test_the_form_saves_the_settings(self):
        parts = self.page.jira_parts
        js = self.win.vault.integrations.jira
        self.assertEqual(parts["jql"].placeholderText(), integ_page.DEFAULT_JQL)
        parts["statuses"].clear()
        type_text(parts["statuses"], "Ready for test, In review, ready for test")
        type_text(parts["jql"], "project = DEMO")
        type_text(parts["fail"], "Test failed")
        parts["watch"].setChecked(True)
        self.page.jira_ticks["reopened"].setChecked(True)
        self.assertEqual(js.statuses, ["Ready for test", "In review"])
        self.assertEqual(js.jql, "project = DEMO")
        self.assertEqual(js.transitions, {"fail": "Test failed"})
        self.assertTrue(js.enabled)
        self.assertTrue(js.rule("reopened"))
        self.assertIn("●", self.page.connections.item(1).text())
        kept = Integrations.from_dict(self.win.vault.integrations.to_dict()).jira
        self.assertEqual((kept.statuses, kept.jql, kept.transitions), (js.statuses, js.jql, js.transitions))

    def test_transitions_are_empty_by_default_and_nothing_is_sent(self):
        js = self.win.vault.integrations.jira
        self.assertEqual(js.transitions, {})
        self.assertEqual((self.page.jira_parts["pass"].text(), self.page.jira_parts["fail"].text()), ("", ""))
        self.assertFalse(self.win.jira.transition("DEMO-1", "pass"))
        pump(50)
        self.assertEqual(self.fake.calls, [], "nothing goes to Jira")

    def test_a_set_transition_is_sent_by_name(self):
        self.win.vault.integrations.jira.transitions = {"pass": "Done"}
        self.assertTrue(self.win.jira.transition("DEMO-1", "pass"))
        self.assertTrue(wait_until(lambda: "DEMO-1" in self.win.jira.status, 5000))
        self.assertIn("moved", self.win.jira.status)
        posts = [c for c in self.fake.calls if c[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(json.loads(posts[0][2]), {"transition": {"id": "31"}})

    def test_the_first_check_is_a_baseline(self):
        got = []
        self.win.hub.subscribe(got.extend)
        alert = self._check()
        integ = self.win.vault.integrations
        self.assertIn("DEMO-1", integ.jira_state)
        self.assertEqual(integ.feed, [])
        self.assertEqual(got, [])
        alert.assert_not_called()
        self.assertIn("Connected to Jira as @me", self.page.status_label.text())

    def test_a_check_fills_the_feed_raises_toasts_and_publishes(self):
        integ = self.win.vault.integrations
        integ.jira_state = {"DEMO-1": {"status": "In Progress", "category": "indeterminate", "me": "me", "comments": 0, "mine": True}}
        got = []
        self.win.hub.subscribe(got.extend)
        alert = self._check()
        alert.assert_called_once()
        self.assertEqual([(e.source, e.kind, e.mr) for e in got], [("jira", "status", "DEMO-1")])
        self.assertEqual(self.page.feed.count(), 1)
        ev = self.page.feed.item(0).data(integ_page.EVENT_ROLE)
        self.assertEqual(integ_page.event_icon(ev), "ticket")
        self.assertEqual(integ_page.event_text(ev), "DEMO-1 moved to Ready for test")
        self.assertTrue(any("Jira" in t.findChild(type(t.text)).text() for t in Toast.stack))
        self.assertIn("(1)", self.win.main_screen.tab_buttons["integrations"].text())
        self.assertIn("new events: 1", self.win.jira.status)
        self.assertTrue(all(c[1].startswith("https://jira.example.com/rest/api/2/") for c in self.fake.calls))

    def test_a_jira_error_shows_in_the_status(self):
        self.fake.status = 401
        self._check()
        self.assertIn("Jira check failed: 401 Unauthorized", self.win.jira.status)
        self.assertEqual(self.page.status_label.text(), self.win.jira.status)
        self.assertGreater(self.win.vault.integrations.jira_checked, 0, "the next one waits the interval")

    def test_check_connection_names_the_user(self):
        self.win.jira.check_connection()
        self.assertTrue(wait_until(lambda: "@me" in self.win.jira.status, 5000))
        self.assertEqual(self.win.vault.integrations.jira.username, "me")

    def test_no_token_no_request(self):
        self.win.vault.integrations.jira.entry_id = ""
        self.win.jira.check_now()
        self.assertFalse(self.win.jira._busy)
        self.assertIn("Fill in the address", self.win.jira.status)
        self.assertEqual(self.fake.calls, [])


if __name__ == "__main__":
    unittest.main()
