"""Sending work to an AI assistant from the Qt window: the "Send to AI" settings section, the
send dialog (the full text, the edits written to the assignment file, the terminal and the
quiet channels), the result window and Save to note, and the quiet runs stopped by a lock.

No real console opens (open_in_console is replaced) and no real assistant runs: the quiet
channel starts a Python stand-in that prints what `claude -p --output-format json` would.
GeckIt is never offered."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import reset_globals
from tests.qt_helpers import close_window, open_window, pump, qt_app, require_qt, wait_until

require_qt()

import shiboken6  # noqa: E402
from PySide6.QtCore import QPoint  # noqa: E402

from daykeeper import assistants as ai  # noqa: E402
from daykeeper.journal import today  # noqa: E402
from daykeeper.qt.dialogs import ai_send  # noqa: E402
from daykeeper.settings import read_settings  # noqa: E402

STAND_IN = """
import json, os, sys
text = sys.stdin.read()
print(json.dumps({"type": "result", "result": "First line of the answer\\nThe rest. Got: " + text.strip()
                  + (" KEY-LEAKED" if os.environ.get("ANTHROPIC_API_KEY") else ""),
                  "total_cost_usd": 0.0123, "duration_ms": 1500, "is_error": False}))
"""
SLOW = "import sys, time\nsys.stdin.read()\ntime.sleep(60)\n"


def setUpModule():
    qt_app()


class AiSendTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.agents = mock.patch.object(ai_send, "agents", return_value=["claude"])
        self.agents.start()
        self.win = open_window(self.tmp.name)
        self.note = self.win.vault.notes.add(today(), title="Plan the release", text="Check the changelog")
        self.note.add_item("write notes")
        self.note.add_item("tag the build")
        self.note.toggle(self.note.items[1])

    def tearDown(self):
        if self.win.ai_runs is not None:
            self.win.ai_runs.runs.stop_all()
        close_window(self.win)
        self.agents.stop()
        self.tmp.cleanup()
        reset_globals()

    def settings(self) -> dict:
        return read_settings(self.win.session.settings_path)

    def repo(self, name: str) -> str:
        folder = self.root / name
        (folder / ".git").mkdir(parents=True)
        return str(folder)

    def stand_in(self, code: str) -> list[str]:
        path = self.root / f"stand_in_{abs(hash(code))}.py"
        path.write_text(code, encoding="utf-8")
        return [sys.executable, str(path)]

    # ---- settings

    def open_section(self):
        self.win.settings_dialog("ai")
        pump()
        return self.win._settings_win.pages["ai"]

    def test_the_section_saves_defaults_projects_and_templates(self):
        page = self.open_section()
        channels = [page.channel.itemData(i) for i in range(page.channel.count())]
        self.assertEqual(channels, ["terminal", "quiet"], "GeckIt is not offered while the core says it is not ready")
        page.channel.setCurrentIndex(1)
        page.model.setText("sonnet")
        page.model.editingFinished.emit()
        self.assertEqual(self.settings()["ai_defaults"]["channel"], "quiet")
        self.assertEqual(self.settings()["ai_defaults"]["model"], "sonnet")

        dlg = page.edit_project(None)
        dlg.gitlab.setText("group/app")
        dlg.folder.setText(str(self.root / "not-a-repo"))
        dlg.save()
        self.assertIn("Not a git checkout", dlg.error.text())
        folder = self.repo("app")
        dlg.folder.setText(folder)
        dlg.save()
        pump()
        self.assertEqual(self.settings()["ai_projects"], [{"gitlab": "group/app", "folder": folder, "geckit": ""}])
        self.assertEqual(page.table.rowCount(), 1)
        twin = page.edit_project(None)
        twin.gitlab.setText("Group/App")
        twin.folder.setText(self.repo("other"))
        twin.save()
        self.assertIn("in the map already", twin.error.text())
        twin.reject()
        page.table.setCurrentCell(0, 0)
        page.remove_project()
        self.assertEqual(self.settings()["ai_projects"], [])

        page._template_on([t.id for t in page.templates()].index("mr_brief"), False)
        self.assertFalse(self.settings()["ai_templates"]["presets"]["mr_brief"])
        tdlg = page.edit_template(None)
        tdlg.name.setText("Daily summary")
        tdlg.body.setPlainText("Summarize {note_title}")
        tdlg.save()
        pump()
        custom = self.settings()["ai_templates"]["custom"]
        self.assertEqual([(c["title"], c["body"], c["kind"]) for c in custom], [("Daily summary", "Summarize {note_title}", "note_task")])
        page._delete_template(len(page.templates()) - 1)
        self.assertEqual(self.settings()["ai_templates"]["custom"], [])

    def test_old_assignments_are_pruned(self):
        folder = ai.assignments_dir(self.root)
        folder.mkdir()
        old, new = folder / "aaaaaa111111.md", folder / "bbbbbb222222.md"
        old.write_text("x")
        new.write_text("y")
        past = time.time() - 40 * 86400
        os.utime(old, (past, past))
        page = self.open_section()
        self.assertEqual(page.prune(), 1)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())

    def test_no_assistant_installed_is_said(self):
        with mock.patch.object(ai_send, "agents", return_value=[]):
            page = self.open_section()
            self.assertFalse(page.agent.isEnabled())
            dlg = ai_send.send_note(self.win, self.note)
            self.assertFalse(dlg.send_btn.isEnabled())
            dlg.copy()
            self.assertIn("Plan the release", self.win.fake_clip.text)
            dlg.reject()

    # ---- the send dialog

    def test_note_menus_offer_sending(self):
        self.win.show_page("notes")
        pump()
        page = self.win.page("notes")
        page.note_menu(self.note, QPoint(10, 10))
        self.assertIn("Send to AI…", [a.text() for a in page.last_menu.actions()])
        page.last_menu.close()
        card = page.cards[self.note.id]
        page.note_item_menu(card, self.note, self.note.items[0], QPoint(10, 10))
        self.assertIn("Send this item to AI…", [a.text() for a in page.last_menu.actions()])
        page.last_menu.close()
        pump()

    def test_the_dialog_shows_the_whole_note(self):
        dlg = ai_send.send_note(self.win, self.note)
        text = dlg.text.toPlainText()
        for part in ("Plan the release", "Check the changelog", "- write notes"):
            self.assertIn(part, text)
        self.assertNotIn("tag the build", text, "a done item is not part of the task")
        channels = [dlg.channel.itemData(i) for i in range(dlg.channel.count())]
        self.assertNotIn("geckit", channels)
        dlg.reject()
        item = ai_send.send_note_item(self.win, self.note, self.note.items[0])
        self.assertEqual(item.title.text(), "write notes")
        self.assertIn("Plan the release", item.text.toPlainText())
        self.assertNotIn("Check the changelog", item.text.toPlainText())
        item.reject()

    def test_terminal_sends_the_edited_text(self):
        folder = self.repo("app")
        self.win.session.write_settings({"ai_projects": [{"gitlab": "group/app", "folder": folder}]})
        dlg = ai_send.send_note(self.win, self.note)
        dlg.project.setCurrentIndex(1)
        dlg.text.setPlainText("Only this edited text leaves the vault")
        with mock.patch.object(ai_send, "open_in_console", return_value=True) as console:
            dlg.send()
        console.assert_called_once()
        plan = console.call_args.args[0]
        files = list(ai.assignments_dir(self.root).glob("*.md"))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_text(encoding="utf-8"), "Only this edited text leaves the vault")
        if sys.platform == "win32":
            script = Path(plan.script_path)
            self.assertTrue(script.exists())
            body = script.read_text(encoding="utf-8")
            self.assertIn(folder, body)
            self.assertIn(str(files[0]), body)
        else:
            self.assertEqual(plan.cwd, folder)
        self.assertEqual(self.settings()["ai_last_choice"]["note_task"], {"agent": "claude", "channel": "terminal", "project": "app"})
        again = ai_send.send_note(self.win, self.note)
        self.assertEqual(again.project.currentData(), folder, "the last choice comes back")
        again.reject()

    def test_a_missing_project_can_be_added_from_the_dialog(self):
        dlg = ai_send.SendDialog(self.win, "note_task", [], ai.note_context("t", "x", []), gitlab_path="group/app")
        self.assertFalse(dlg.missing.isHidden())
        form = dlg.add_project()
        self.assertEqual(form.gitlab.text(), "group/app")
        folder = self.repo("app")
        form.folder.setText(folder)
        form.save()
        pump()
        self.assertTrue(dlg.missing.isHidden())
        self.assertEqual(dlg.project.currentData(), folder)
        self.assertEqual(self.settings()["ai_projects"][0]["gitlab"], "group/app")
        dlg.reject()

    def test_a_terminal_problem_is_shown(self):
        dlg = ai_send.send_note(self.win, self.note)
        with mock.patch.object(ai, "terminal_plan", return_value=ai.ConsolePlan(problem="The project folder is not there: X")), \
                mock.patch.object(ai_send, "open_in_console") as console:
            dlg.send()
        console.assert_not_called()
        self.assertIn("not there", dlg.error.text())
        dlg.reject()

    # ---- the quiet channel

    def send_quiet(self, code: str):
        runs = ai_send.controller(self.win)
        runs.program_argv = self.stand_in(code)
        dlg = ai_send.send_note(self.win, self.note)
        dlg.channel.setCurrentIndex([dlg.channel.itemData(i) for i in range(dlg.channel.count())].index("quiet"))
        dlg.text.setPlainText("Say hello")
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "not-for-the-child"}), mock.patch.object(ai_send, "open_in_console") as console:
            dlg.send()
        console.assert_not_called()
        self.assertEqual(len(runs.history()), 1)
        return runs, runs.history()[0]

    def test_quiet_shows_the_answer_and_saves_it_to_a_note(self):
        runs, run = self.send_quiet(STAND_IN)
        self.assertIn("AI: working", self.win.main_screen.status.text())
        self.assertTrue(wait_until(lambda: not run.running and run not in runs.running() and runs.windows.get(run) is not None
                                   and "✓" in runs.windows[run].state.text(), 15000), run.error)
        win = runs.windows[run]
        self.assertIn("First line of the answer", win.text.toPlainText())
        self.assertIn("Got: Say hello", win.text.toPlainText())
        self.assertNotIn("KEY-LEAKED", win.text.toPlainText(), "the child never gets the full environment")
        self.assertIn("$0.0123", win.state.text())
        self.assertIn("1.5 s", win.state.text())
        self.assertIn("answer is ready", self.win.main_screen.status.text())
        win.copy()
        self.assertIn("First line", self.win.fake_clip.text)
        win.save()
        note = self.note  # today's note of the same title is extended, not doubled
        self.assertEqual(note.text, "Check the changelog\nFirst line of the answer")
        self.assertEqual(len(note.attachments), 1)
        path = Path(note.attachments[0].path)
        self.assertEqual(path.parent, self.root / "answers")
        self.assertIn("The rest.", path.read_text(encoding="utf-8"))
        history = ai_send.HistoryDialog(self.win)
        self.assertEqual(history.list.count(), 1)
        history.reject()
        win.close()
        pump()

    def test_a_lock_stops_quiet_runs_and_forgets_them(self):
        runs, run = self.send_quiet(SLOW)
        window = runs.windows[run]
        self.assertTrue(run.running)
        self.win.lock()
        self.assertTrue(wait_until(lambda: not run.running, 15000))
        self.assertEqual(run.error, "Stopped")
        self.assertEqual(runs.history(), [])
        pump()
        self.assertFalse(shiboken6.isValid(window) and window.isVisible())

    def test_a_merge_request_assignment_opens_in_the_dialog_as_made(self):
        a = ai.assignment_from(next(t for t in ai.PRESETS if t.id == "mr_review"),
                               ai.mr_context("https://gitlab.example.com/group/app/-/merge_requests/7", "Fix login", "group/app", "7"),
                               links=["https://gitlab.example.com/group/app/-/merge_requests/7"])
        dlg = self.win.send_to_ai(a, gitlab_path="group/app")
        self.assertIsInstance(dlg, ai_send.SendDialog)
        self.assertIn("merge_requests/7", dlg.text.toPlainText())
        self.assertFalse(dlg.missing.isHidden(), "an unmapped project is offered to add")
        self.assertEqual(dlg.assignment().links, a.links)
        dlg.reject()

    def test_a_quiet_ask_with_a_hook_answers_the_hook_not_a_window(self):
        runs = ai_send.controller(self.win)
        runs.program_argv = self.stand_in(STAND_IN)
        got: list[str] = []
        a = ai.Assignment("mr_brief", "Brief", "Summarize this")
        run = self.win.send_to_ai(a, quiet=True, on_answer=got.append)
        self.assertTrue(wait_until(lambda: bool(got), 15000), run.error)
        self.assertIn("Got: Summarize this", got[0])
        self.assertNotIn(run, runs.windows)
        self.assertNotIn(run, runs.hooks)


    def test_a_quiet_run_that_cannot_start_answers_its_hook_at_once(self):
        runs = ai_send.controller(self.win)
        runs.program_argv = [str(self.root / "no-such-program.exe")]
        got: list[str] = []
        run = self.win.send_to_ai(ai.Assignment("mr_brief", "Brief", "Summarize this"), quiet=True, on_answer=got.append)
        self.assertFalse(run.running)
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith("No answer:"), got[0])
        self.assertNotIn(run, runs.hooks)

    def test_a_quiet_ask_without_claude_says_so(self):
        got: list[str] = []
        with mock.patch.object(ai_send, "agents", return_value=["codex"]):
            self.assertIsNone(self.win.send_to_ai(ai.Assignment("mr_brief", "Brief", "x"), quiet=True, on_answer=got.append))
        self.assertIn("Claude Code was not found", got[0])
        self.assertIsNone(self.win.ai_runs)

    def test_the_quiet_channel_writes_no_assignment_file(self):
        self.send_quiet(SLOW)
        self.assertFalse(list((self.root / "assignments").glob("*.md")) if (self.root / "assignments").exists() else [])

    def test_a_refused_console_leaves_no_assignment_file(self):
        dlg = ai_send.send_note(self.win, self.note)
        with mock.patch.object(ai_send, "open_in_console", return_value=False):
            dlg.send()
        self.assertIn("No terminal program", dlg.error.text())
        folder = self.root / "assignments"
        self.assertEqual(list(folder.glob("*.md")) if folder.exists() else [], [])
        dlg.reject()

    def test_a_run_pushed_out_of_the_history_is_still_stopped_on_lock(self):
        runs = ai_send.controller(self.win)
        runs.program_argv = self.stand_in(SLOW)
        first = runs.start(ai.Assignment("note_task", "first", "x"))
        runs.runs.history = type(runs.runs.history)(runs.runs.history, maxlen=1)
        runs.start(ai.Assignment("note_task", "second", "y"))
        self.assertIn(first, runs.runs.overflow)
        self.assertIn(first, runs.running())
        runs.on_lock()
        self.assertTrue(wait_until(lambda: not first.running, 10000))


if __name__ == "__main__":
    unittest.main()
