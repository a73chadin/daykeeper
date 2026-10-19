"""The merge request preview and the MR actions of the feed, in the Qt window: the page loads
in the background and shows the rendered Markdown, an error shows its text, only web links
open, the page is written owner-only; the feed offers the actions by my part in the MR;
Review / Fixes / Brief go to `send_to_ai` when the window has it and are copied otherwise.
A fake `fetch` stands in for GitLab: never the network."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import reset_globals
from tests.qt_helpers import close_window, open_window, pump, qt_app, require_qt, wait_until

require_qt()

import shiboken6  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from daykeeper.assistants import Assignment  # noqa: E402
from daykeeper.gitlab import Event  # noqa: E402
from daykeeper.qt.dialogs import fileview  # noqa: E402
from daykeeper.qt.dialogs import mr_preview as preview_mod  # noqa: E402
from daykeeper.qt.pages import integrations as integ_page  # noqa: E402
from daykeeper.qt.widgets.toast import Toast  # noqa: E402

URL = "https://gitlab.example.com/group/app/-/merge_requests/12"
MR = {
    "iid": 12, "project_id": 7, "title": "Add the export", "web_url": URL, "state": "opened",
    "references": {"full": "group/app!12"}, "author": {"name": "Ann", "username": "ann"},
    "source_branch": "feature/export", "target_branch": "main",
    "description": "See [the notes](notes.md) and [the docs](https://example.com/docs).\n\n- [ ] a box",
}
CHANGES = {**MR, "changes": [{"old_path": "app.py", "new_path": "app.py", "diff": "@@ -1 +1 @@\n-old line\n+new line\n"}]}


class FakeGitLab:
    """GitLabClient's `fetch`: answers by the path, records the calls."""

    def __init__(self, status: int = 200):
        self.status = status
        self.calls: list[str] = []

    def __call__(self, url: str, headers: dict) -> tuple[int, bytes]:
        self.calls.append(url)
        if self.status != 200:
            return self.status, b"{}"
        body = CHANGES if url.split("?")[0].endswith("/changes") else MR
        return 200, json.dumps(body).encode("utf-8")


def mr_event(role: str = "reviewer", project_id: int = 7) -> Event:
    return Event("review_requested", "group/app!12", "Add the export", URL, "Review requested", project_id=project_id, role=role)


def setUpModule():
    qt_app()


class MrPreviewTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.errors: list = []
        self.win = open_window(self.tmp.name, errors=self.errors)
        vault = self.win.vault
        entry = vault.add_entry(name="GitLab token", kind="token")
        entry.fields[0].value = "glpat-test"
        gl = vault.integrations.gitlab
        gl.url = "https://gitlab.example.com"
        gl.entry_id = entry.id
        gl.username = "me"
        self.fake = FakeGitLab()
        self.win.gitlab.fetch = self.fake
        self.win.show_page("integrations")
        pump()
        self.page = self.win.page("integrations")
        self.windows: list = []

    def tearDown(self):
        for w in self.windows:
            w.close()
        for t in list(Toast.stack):
            t.close()
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()
        self.assertEqual(self.errors, [], "no error reached the window's report")

    def open_preview(self, ev: Event | None = None):
        ev = ev or mr_event()
        self.win.vault.integrations.feed = [ev]
        self.page.render_feed()
        win = self.page.feed_action(ev, "preview")
        self.windows.append(win)
        return win

    def loaded(self, ev: Event | None = None):
        win = self.open_preview(ev)
        self.assertTrue(wait_until(lambda: win.state != "loading", 5000))
        return win

    # ---- the page

    def test_the_preview_loads_and_shows_the_rendered_markdown(self):
        win = self.open_preview()
        self.assertEqual(win.view.toPlainText(), "Loading…", "loading first, on the window's thread")
        self.assertTrue(wait_until(lambda: win.state == "ready", 5000))
        text = win.view.toPlainText()
        self.assertIn("Add the export", text)
        self.assertIn("+new line", text)
        self.assertTrue(any("/projects/7/merge_requests/12/changes" in c for c in self.fake.calls), "by the project id of the event")
        path = Path(self.tmp.name) / "previews" / "group-app-12.md"
        self.assertEqual(win.path, path)
        self.assertEqual(path.read_text(encoding="utf-8"), win.markdown)
        self.assertTrue(win.edit_btn.isHidden(), "read only")

    def test_an_older_record_falls_back_to_the_path(self):
        self.loaded(mr_event(project_id=0))
        self.assertTrue(any("/projects/group%2Fapp/merge_requests/12" in c for c in self.fake.calls))

    def test_an_error_shows_its_text(self):
        self.fake.status = 404
        win = self.loaded()
        self.assertEqual(win.state, "error")
        self.assertIn("HTTP 404", win.view.toPlainText())
        self.assertFalse(win.brief_btn.isEnabled())

    def test_no_token_says_what_is_missing(self):
        self.win.vault.integrations.gitlab.entry_id = ""
        win = self.open_preview()
        self.assertEqual(win.state, "error")
        self.assertEqual(self.fake.calls, [])

    def test_only_web_links_open_from_the_page(self):
        win = self.loaded()
        anchors = []
        block = win.view.document().begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                fmt = it.fragment().charFormat()
                if fmt.isAnchor():
                    anchors.append(fmt.anchorHref())
                it += 1
            block = block.next()
        self.assertTrue(anchors)
        self.assertTrue(all(a.startswith("link:https://") for a in anchors), anchors)
        with mock.patch("webbrowser.open") as browser, mock.patch.object(fileview, "open_with_system") as system, \
                mock.patch.object(fileview, "show_in_folder") as folder, mock.patch.object(fileview, "FileViewer") as viewer:
            for href in ("notes.md", "../settings.json", "file:///C:/Windows/notepad.exe", "mailto:a@example.com", "javascript:x"):
                self.assertFalse(win._open_link(href), href)
            browser.assert_not_called()
            self.assertTrue(win._open_link("https://example.com/docs"))
        browser.assert_called_once_with("https://example.com/docs")
        system.assert_not_called()
        folder.assert_not_called()
        viewer.assert_not_called()

    def test_task_boxes_of_the_page_do_not_flip(self):
        win = self.loaded()
        before = win.path.read_text(encoding="utf-8")
        win._toggle_task(0)
        win.toggle_edit()
        self.assertFalse(win.editing)
        self.assertEqual(win.path.read_text(encoding="utf-8"), before)

    def test_the_page_is_private(self):
        calls = []
        real = preview_mod.private

        def spy(path, folder=False):
            calls.append((Path(path), folder))
            real(path, folder=folder)

        with mock.patch("daykeeper.vault.private", side_effect=spy), mock.patch.object(preview_mod, "private", side_effect=spy):
            win = self.loaded()
        self.assertIn((win.path, False), calls, "the file owner-only")
        self.assertIn((win.path.parent, True), calls, "its folder too")
        if os.name == "posix":
            self.assertEqual(win.path.stat().st_mode & 0o777, 0o600)

    def test_open_in_browser_is_web_only(self):
        win = self.loaded()
        with mock.patch("webbrowser.open") as browser:
            self.assertTrue(win.open_in_browser())
            win.url = "file:///C:/x"
            self.assertFalse(win.open_in_browser())
        browser.assert_called_once_with(URL)

    # ---- the buttons

    def test_fixes_only_for_my_merge_requests(self):
        win = self.loaded(mr_event("reviewer"))
        self.assertTrue(win.fix_btn.isHidden())
        mine = self.loaded(mr_event("author"))
        self.assertFalse(mine.fix_btn.isHidden())

    def test_an_unknown_part_is_mine_when_i_wrote_it(self):
        self.win.vault.integrations.gitlab.username = "ann"
        win = self.loaded(mr_event(""))
        self.assertEqual(win.role, "author")
        self.assertFalse(win.fix_btn.isHidden())

    def test_review_goes_to_send_to_ai_when_the_window_has_it(self):
        repo = Path(self.tmp.name) / "app"
        (repo / ".git").mkdir(parents=True)
        self.win.session.settings = lambda: {"ai_projects": [{"gitlab": "group/app", "folder": str(repo)}]}
        sent = mock.Mock()
        self.win.send_to_ai = sent
        win = self.loaded()
        self.assertTrue(win.ask("mr_review"))
        a = sent.call_args.args[0]
        self.assertIsInstance(a, Assignment)
        self.assertEqual(a.kind, "mr_review")
        self.assertIn(URL, a.text)
        self.assertEqual(a.folder, str(repo), "the folder from the project map")
        self.assertEqual(a.links, [URL])
        self.assertFalse(sent.call_args.kwargs["quiet"])
        self.assertIsNone(self.win.fake_clip.text)

    def test_review_is_copied_without_send_to_ai(self):
        self.win.send_to_ai = None  # a window without the send dialog
        win = self.loaded()
        self.assertFalse(win.ask("mr_review"))
        self.assertIn(URL, self.win.fake_clip.text or "")
        self.assertIn("Review merge request", self.win.fake_clip.text or "")
        self.assertEqual(win.status.text(), "Copied: paste it into your assistant")

    def test_brief_goes_quiet_with_the_diff_after_the_prompt_and_shows_the_answer(self):
        sent = mock.Mock()
        self.win.send_to_ai = sent
        win = self.loaded()
        self.assertTrue(win.ask_brief())
        a = sent.call_args.args[0]
        self.assertEqual(a.kind, "mr_brief")
        self.assertTrue(sent.call_args.kwargs["quiet"])
        self.assertTrue(a.text.startswith("Summarize this merge request"))
        self.assertIn("+new line", a.text)
        sent.call_args.kwargs["on_answer"]("It adds an export. Look at app.py first.")
        self.assertFalse(win.brief.isHidden())
        self.assertEqual(win.brief_text.text(), "It adds an export. Look at app.py first.")
        self.assertEqual(win.brief_text.textFormat(), Qt.TextFormat.PlainText)

    def test_an_answer_after_the_window_closed_goes_nowhere(self):
        sent = mock.Mock()
        self.win.send_to_ai = sent
        win = self.loaded()
        win.ask_brief()
        self.windows.remove(win)
        win.close()
        pump()
        self.assertFalse(shiboken6.isValid(win), "the closed window is gone")
        sent.call_args.kwargs["on_answer"]("late")  # no error

    def test_brief_is_copied_without_send_to_ai(self):
        self.win.send_to_ai = None  # a window without the send dialog
        win = self.loaded()
        self.assertFalse(win.ask_brief())
        self.assertIn("+new line", self.win.fake_clip.text or "")
        self.assertTrue(win.brief.isHidden())

    # ---- the feed

    def test_the_feed_offers_the_actions_by_my_part(self):
        codes = lambda ev: [c for c, _t in integ_page.mr_actions(ev)]  # noqa: E731
        self.assertEqual(codes(mr_event("reviewer")), ["preview", "review"])
        self.assertEqual(codes(mr_event("")), ["preview", "review"])
        self.assertEqual(codes(mr_event("author")), ["preview", "fix"])
        self.assertEqual(codes(Event("watch_failed", "group/app@main", "Nightly", "", "Failed")), [])
        self.assertEqual(codes(Event("status", "APP-1", "Bug", "", "Moved", source="jira")), [])

    def test_a_click_on_an_action_link_runs_it_and_not_the_browser(self):
        ev = mr_event("author")
        self.win.vault.integrations.feed = [ev]
        self.page.render_feed()
        pump()
        item = self.page.feed.item(0)
        rects = self.page.feed.itemDelegate().action_rects(self.page.feed.visualItemRect(item), ev)
        self.assertEqual([c for c, _t, _r in rects], ["preview", "fix"])
        sent = mock.Mock()
        self.win.send_to_ai = sent
        with mock.patch("webbrowser.open") as browser:
            QTest.mouseClick(self.page.feed.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, rects[1][2].center())
            pump()
        browser.assert_not_called()
        self.assertEqual(sent.call_args.args[0].kind, "mr_fix")
        self.assertTrue(ev.read)

    def test_review_from_the_feed_is_copied_without_send_to_ai(self):
        self.win.send_to_ai = None  # a window without the send dialog
        ev = mr_event("reviewer")
        self.win.vault.integrations.feed = [ev]
        self.assertFalse(self.page.feed_action(ev, "review"))
        self.assertIn(URL, self.win.fake_clip.text or "")
        self.assertIn("group/app !12", self.win.fake_clip.text or "")

    def test_the_feed_with_actions_paints(self):
        self.win.vault.integrations.feed = [mr_event("author"), mr_event("reviewer"), Event("watch_failed", "group/app@main", "Nightly", "", "Failed")]
        self.page.render_feed()
        pump()
        self.assertFalse(self.page.feed.grab().isNull())
        tall = self.page.feed.visualItemRect(self.page.feed.item(0)).height()
        plain = self.page.feed.visualItemRect(self.page.feed.item(2)).height()
        self.assertGreater(tall, plain, "room for the action row")


    def test_brief_without_claude_says_so_in_the_brief(self):
        win = self.loaded()
        with mock.patch("daykeeper.qt.dialogs.ai_send.agents", return_value=[]):
            self.assertTrue(win.ask_brief())
        self.assertIn("Claude Code was not found", win.brief_text.text())


if __name__ == "__main__":
    unittest.main()
