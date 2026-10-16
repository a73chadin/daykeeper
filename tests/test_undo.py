"""Undo and redo: the history itself and the vault's parts it puts back."""

import tempfile
import unittest
from pathlib import Path

from daykeeper.undo import History
from daykeeper.vault import UNDO_DOMAINS, Vault


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.data = {"a": "1", "b": "x"}
        self.h = History(lambda d: self.data[d], ("a", "b"))
        self.h.reset()

    def write(self, d, v):
        self.data[d] = v

    def test_undo_and_redo(self):
        self.data["a"] = "2"
        self.assertTrue(self.h.capture())
        self.data["a"] = "3"
        self.data["b"] = "y"
        self.h.capture()
        self.assertEqual(self.h.undo(self.write), {"a", "b"})
        self.assertEqual(self.data, {"a": "2", "b": "x"})
        self.assertEqual(self.h.undo(self.write), {"a"})
        self.assertEqual(self.data["a"], "1")
        self.assertEqual(self.h.undo(self.write), set(), "nothing more")
        self.h.redo(self.write)
        self.h.redo(self.write)
        self.assertEqual(self.data, {"a": "3", "b": "y"})
        self.assertFalse(self.h.can_redo)

    def test_nothing_changed_is_no_step(self):
        self.assertFalse(self.h.capture())
        self.assertFalse(self.h.can_undo)

    def test_a_new_change_ends_the_redo(self):
        self.data["a"] = "2"
        self.h.capture()
        self.h.undo(self.write)
        self.data["b"] = "z"
        self.assertEqual(self.h.redo(self.write), set())
        self.assertEqual(self.data, {"a": "1", "b": "z"})

    def test_an_uncaptured_change_is_undone_first(self):
        self.data["a"] = "2"  # the window has not captured it yet
        self.h.undo(self.write)
        self.assertEqual(self.data["a"], "1")

    def test_rebase_keeps_what_came_by_itself(self):
        self.data["a"] = "2"
        self.data["b"] = "y"
        self.h.capture()
        self.data["b"] = "y+inbox"  # arrived on its own
        self.h.rebase("b")
        self.assertEqual(self.h.undo(self.write), {"a"})
        self.assertEqual(self.data, {"a": "1", "b": "y+inbox"})

    def test_limit(self):
        h = History(lambda d: self.data[d], ("a",), limit=3)
        h.reset()
        for i in range(10):
            self.data["a"] = str(i)
            h.capture()
        self.assertEqual(len(h.undo_steps), 3)


class VaultDomainsTest(unittest.TestCase):
    def test_every_domain_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = Vault.create(Path(tmp) / "v.pkv", "master-pass-1")
            v.add_entry("one")
            v.journal.get("2026-10-13", create=True).add("ok", "APP-1")
            before = {d: v.domain_state(d) for d in UNDO_DOMAINS}
            v.add_entry("two")
            v.journal.get("2026-10-13").add("ok", "APP-2")
            for d in UNDO_DOMAINS:
                v.set_domain_state(d, before[d])
            self.assertEqual([e.name for e in v.entries], ["one"])
            self.assertEqual(v.journal.get("2026-10-13").section("ok"), ["APP-1"])
            self.assertEqual({d: v.domain_state(d) for d in UNDO_DOMAINS}, before)

    def test_a_routines_run_and_a_bookmarks_icon_are_not_undone(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = Vault.create(Path(tmp) / "v.pkv", "master-pass-1")
            r = v.routines.add(name="build", target="build.bat")
            b = v.bookmarks.add(title="site", url="https://example.com")
            routines, marks = v.domain_state("routines"), v.domain_state("bookmarks")
            r.mark_started(123.0, 0)
            b.favicon, b.opened = "data", 99.0
            self.assertEqual(v.domain_state("routines"), routines, "a run is not a change to undo")
            self.assertEqual(v.domain_state("bookmarks"), marks, "nor a fetched icon")
            r.name = "build all"
            v.set_domain_state("routines", routines)
            self.assertIs(v.routines.items[0], r, "the routine object stays: a run under way keeps it")
            self.assertEqual((r.name, r.last_run), ("build", 123.0))
            b.title = "renamed"
            v.set_domain_state("bookmarks", marks)
            self.assertEqual((v.bookmarks.items[0].title, v.bookmarks.items[0].favicon), ("site", "data"))


if __name__ == "__main__":
    unittest.main()
