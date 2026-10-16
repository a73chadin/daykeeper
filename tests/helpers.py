"""Shared helpers for the tests: resetting module-level state and finding a display."""
from __future__ import annotations

import sys
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
    """A Tk root can be made here (a headless CI runner has no display). macOS always has
    one, and there the probe itself did harm: after a root made and destroyed, the next
    root's update() never returned (the macOS runner hung on the first window test)."""
    if sys.platform == "darwin":
        return True
    try:
        root = tk.Tk()
    except tk.TclError:
        return False
    root.destroy()
    return True
