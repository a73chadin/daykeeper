"""Undo and redo inside one-line fields. A Tk text box has its own undo; an entry has none,
so Ctrl+Z in a field used to roll back the whole change of the app instead of the typing.

Installed once for the application: every entry remembers what it held before each burst
of typing (keys less than BURST_S apart are one step, as in a text editor). `undo(entry)`
and `redo(entry)` say whether they had something to do; when not, the window's own undo
takes over."""

from __future__ import annotations

import time
import tkinter as tk

BURST_S = 0.8
LIMIT = 100
CLASSES = ("TEntry", "Entry")


class _State:
    def __init__(self, value: str):
        self.last = value
        self.undo: list[str] = []
        self.redo: list[str] = []
        self.at = 0.0


def _state(widget) -> _State:
    st = getattr(widget, "_field_undo", None)
    if st is None:
        st = _State(widget.get())
        widget._field_undo = st
    return st


def _note(event: tk.Event) -> None:
    """After a key, a paste or a cut: a changed value opens a new step when the last one is
    older than a burst."""
    widget = event.widget
    try:
        value = widget.get()
    except (tk.TclError, AttributeError):
        return
    st = _state(widget)
    if value == st.last:
        return
    now = time.monotonic()
    if now - st.at > BURST_S or not st.undo:
        st.undo.append(st.last)
        del st.undo[:-LIMIT]
    st.last, st.at = value, now
    st.redo.clear()


def _sync(event: tk.Event) -> None:
    """On focus: a value the app put in meanwhile (another day, another entry in the same
    field) starts a fresh history — an undo must never bring back what another item held."""
    widget = event.widget
    try:
        value = widget.get()
    except (tk.TclError, AttributeError):
        return
    st = _state(widget)
    if value != st.last:
        st.last, st.at = value, 0.0
        st.undo.clear()
        st.redo.clear()


def install(root: tk.Misc) -> None:
    for cls in CLASSES:
        root.bind_class(cls, "<FocusIn>", _sync, add="+")
        for seq in ("<KeyRelease>", "<<Paste>>", "<<Cut>>"):
            root.bind_class(cls, seq, lambda e: e.widget.after_idle(lambda: _note(e)), add="+")


def is_field(widget) -> bool:
    return widget is not None and widget.winfo_class() in CLASSES


def _put(widget, value: str) -> None:
    st = _state(widget)
    widget.delete(0, "end")
    widget.insert(0, value)
    widget.icursor("end")
    st.last, st.at = value, 0.0


def undo(widget) -> bool:
    st = _state(widget)
    current = widget.get()
    if current != st.last:  # typed but not noted yet
        st.undo.append(st.last)
        st.last = current
    if not st.undo:
        return False
    st.redo.append(current)
    _put(widget, st.undo.pop())
    return True


def redo(widget) -> bool:
    st = _state(widget)
    if not st.redo:
        return False
    st.undo.append(widget.get())
    _put(widget, st.redo.pop())
    return True
