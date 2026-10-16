"""Shared helpers for the tests: resetting module-level state and finding a display."""
from __future__ import annotations

import tkinter as tk

from daykeeper import journal
from daykeeper.i18n import set_language
from daykeeper.settings import Links


def reset_globals() -> None:
    """Put the module-level state back: the UI language and the report links. Tests that
    change either call this in setUp and tearDown, so the order of the tests does not matter."""
    set_language("en")
    journal.configure(Links())


def has_display() -> bool:
    """A Tk root can be made here (a headless CI runner has no display)."""
    try:
        root = tk.Tk()
    except tk.TclError:
        return False
    root.destroy()
    return True
