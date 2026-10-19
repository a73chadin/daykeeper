"""Shared helpers for the tests: resetting module-level state."""
from __future__ import annotations

from daykeeper import journal, journal_layout
from daykeeper.i18n import set_language
from daykeeper.settings import Links


def reset_globals() -> None:
    """Put the module-level state back: the UI language, the report links and the journal
    layout in use (a window or the helper configures the one of its settings). Tests that
    change any call this in setUp and tearDown, so the order of the tests does not matter."""
    set_language("en")
    journal.configure(Links())
    journal_layout.configure(journal_layout.preset("qa"))
