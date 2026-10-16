"""Undo inside one-line fields: bursts of typing, a value the app put in, redo."""

import tkinter as tk
import unittest
from tkinter import ttk
from unittest import mock

from daykeeper import fieldundo


def key(entry):
    fieldundo._note(type("E", (), {"widget": entry})())


class FieldUndoTest(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest("no display")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.entry = ttk.Entry(self.root)
        self.clock = [100.0]
        patcher = mock.patch.object(fieldundo.time, "monotonic", lambda: self.clock[0])
        patcher.start()
        self.addCleanup(patcher.stop)

    def type(self, text, gap=0.1):
        for ch in text:
            self.clock[0] += gap
            self.entry.insert("end", ch)
            key(self.entry)

    def test_a_burst_is_one_step(self):
        fieldundo._state(self.entry)
        self.type("hello")
        self.clock[0] += 2
        self.type(" world")
        self.assertTrue(fieldundo.undo(self.entry))
        self.assertEqual(self.entry.get(), "hello")
        self.assertTrue(fieldundo.undo(self.entry))
        self.assertEqual(self.entry.get(), "")
        self.assertFalse(fieldundo.undo(self.entry), "nothing left: the app's undo takes over")
        self.assertTrue(fieldundo.redo(self.entry))
        self.assertTrue(fieldundo.redo(self.entry))
        self.assertEqual(self.entry.get(), "hello world")
        self.assertFalse(fieldundo.redo(self.entry))

    def test_a_value_the_app_put_in_starts_afresh(self):
        fieldundo._state(self.entry)
        self.type("day one")
        self.entry.delete(0, "end")
        self.entry.insert(0, "day two")  # another day loaded into the same field
        fieldundo._sync(type("E", (), {"widget": self.entry})())
        self.assertFalse(fieldundo.undo(self.entry))
        self.assertEqual(self.entry.get(), "day two")


if __name__ == "__main__":
    unittest.main()
