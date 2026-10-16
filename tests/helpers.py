"""Shared helpers for the tests: resetting module-level state and finding a display."""
from __future__ import annotations

import os
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
    """A Tk root can be made here (a headless CI runner has no display).

    macOS always has one, and there the probe itself did harm: after a root made and
    destroyed, the next root's update() never returned. The window tests are skipped on
    the macOS runner: there, windows of a hidden main window hung as well (the settings
    of the smoke run) — the app on macOS is checked by the screenshots workflow, where the
    window is on screen as in use."""
    if sys.platform == "darwin":
        return not os.environ.get("CI")
    try:
        root = tk.Tk()
    except tk.TclError:
        return False
    root.destroy()
    return True


def hide(win: tk.Misc) -> None:
    """Keep a test window off the screen, except on macOS: there Tk 8.6 never returns from
    update() over a hidden window that has not been drawn yet (the app's own main loop does
    not get stuck, the test's forced update does)."""
    if sys.platform != "darwin":
        win.withdraw()  # type: ignore[attr-defined]
