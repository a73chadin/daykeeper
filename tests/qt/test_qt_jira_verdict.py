"""The Jira transition offered after a verdict: a key added to the journal's "pass" or
"fail" column (the Journal tab's quick add or the quick window's entry) shows a toast
“KEY-1 → <transition>?” when the Jira settings name a transition for that verdict; only
its click sends the move. The Jira answers come from a fake fetch: never the network."""
from __future__ import annotations

import json
import tempfile
import unittest

from tests.helpers import reset_globals
from tests.qt_helpers import close_window, open_window, pump, qt_app, require_qt, wait_until

require_qt()

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QLabel  # noqa: E402

from daykeeper.journal_layout import Column, Layout, preset  # noqa: E402
from daykeeper.qt.pages.integrations import verdict_of_column  # noqa: E402
from daykeeper.qt.widgets.toast import Toast  # noqa: E402
from daykeeper.journal import today  # noqa: E402
from daykeeper.quick import QuickEntry  # noqa: E402
from tests.qt.test_qt_routines_integrations import FakeJira  # noqa: E402


def setUpModule():
    qt_app()


class VerdictOfColumnTest(unittest.TestCase):
    def test_the_layout_says_which_column_is_pass_or_fail(self):
        qa = preset("qa")
        self.assertEqual([verdict_of_column(c.id, qa) for c in qa.columns], ["pass", "fail", ""])
        self.assertEqual([verdict_of_column(c.id, preset("dev")) for c in preset("dev").columns], ["", "", ""])
        own = Layout("custom", [Column("good", "Looks good", command="pass"), Column("bad", "Broken", command="fail")])
        self.assertEqual((verdict_of_column("good", own), verdict_of_column("bad", own)), ("pass", "fail"), "the command word, not the name")


class JiraVerdictTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.win = open_window(self.tmp.name)
        self.win.background_jobs = True  # open_window makes a preview copy, which sends nothing
        vault = self.win.vault
        entry = vault.add_entry(name="Jira token", kind="token")
        entry.fields[0].value = "jira-test-token"
        js = vault.integrations.jira
        js.url = "https://jira.example.com"
        js.entry_id = entry.id
        js.transitions = {"pass": "Done"}
        self.fake = FakeJira()
        self.win.jira.fetch = self.fake
        self.win.show_page("journal")
        pump()
        self.page = self.win.page("journal")

    def tearDown(self):
        for t in list(Toast.stack):
            t.close()
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()

    def add(self, text: str, column: str) -> None:
        self.page.quick.setText(text)
        self.page._quick_add(column)
        pump()

    def posts(self) -> list:
        return [c for c in self.fake.calls if c[0] == "POST"]

    def test_a_pass_key_offers_the_move_and_a_click_sends_it_once(self):
        self.add("DEMO-1 DEMO-2", "ok")
        titles = [t.findChild(QLabel, "ToastTitle").text().strip() for t in Toast.stack]
        self.assertEqual(titles, ["DEMO-1 → Done?", "DEMO-2 → Done?"], "one toast per key")
        self.assertEqual(self.fake.calls, [], "nothing sent before a click")
        first = Toast.stack[0]
        QTest.mouseClick(first, Qt.MouseButton.LeftButton)
        pump()
        self.assertTrue(wait_until(lambda: "DEMO-1" in self.win.jira.status and "moved" in self.win.jira.status, 5000))
        posts = self.posts()
        self.assertEqual(len(posts), 1)
        self.assertIn("/issue/DEMO-1/transitions", posts[0][1])
        self.assertEqual(json.loads(posts[0][2]), {"transition": {"id": "31"}})

    def test_without_a_click_nothing_is_sent(self):
        self.assertEqual(self.win.jira.offer_verdict("ok", ["DEMO-1"]), ["DEMO-1"])
        for t in list(Toast.stack):
            t.close()  # it went by itself
        pump(50)
        self.assertEqual(self.fake.calls, [])

    def test_no_toast_without_a_transition_or_jira(self):
        self.add("DEMO-1", "failed")  # no transition named for "fail"
        self.assertEqual(Toast.stack, [])
        self.add("DEMO-2", "bugs")  # not a verdict column
        self.assertEqual(Toast.stack, [])
        self.add("just text", "ok")  # not an issue key
        self.assertEqual(Toast.stack, [])
        self.win.vault.integrations.jira.url = ""
        self.add("DEMO-3", "ok")  # Jira is not set up
        self.assertEqual(Toast.stack, [])
        self.win.vault.integrations.jira.url = "https://jira.example.com"
        self.add("DEMO-3", "ok")  # already in the column: nothing new, nothing offered
        self.assertEqual(Toast.stack, [])
        self.win.background_jobs = False
        self.assertEqual(self.win.jira.offer_verdict("ok", ["DEMO-4"]), [], "a preview copy offers nothing")
        pump(50)
        self.assertEqual(self.fake.calls, [])

    def test_the_quick_window_entry_offers_it_too(self):
        self.win.vault.integrations.jira.transitions = {"fail": "Test failed"}
        self.win.quick.file_entry(QuickEntry("failed", ["DEMO-7"], today(), "fail DEMO-7"))
        pump()
        self.assertEqual(len(Toast.stack), 1)
        self.assertIn("DEMO-7", self.win.vault.journal.days[today()].section("failed"))
        self.win.quick.file_entry(QuickEntry("failed", ["DEMO-7"], today(), "fail DEMO-7"))
        pump()
        self.assertEqual(len(Toast.stack), 1, "an item already there is not offered again")
        self.assertEqual(self.fake.calls, [])


if __name__ == "__main__":
    unittest.main()
