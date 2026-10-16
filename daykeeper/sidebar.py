"""Keep the sidebar of a horizontal paned window visible.

ttk shares a window resize between panes by weight and never gives space back to a pane
that has shrunk to zero. A sidebar placed at 400 px on a maximized 4K window therefore
disappears for good once the window is restored to its normal size. This helper pins the
sidebar to the width the user chose and never lets it drop below a minimum.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

CARD_MIN = 360  # the right pane keeps at least this many pixels


class SidebarKeeper:
    """Pins the first pane of ``paned`` to ``width`` (or to its own natural width when
    ``None``) and clamps it to ``[minimum, total - card_min]``. Dragging the sash changes
    the remembered width; a drag below the minimum snaps back. ``card_min`` — in pixels
    at 100 %, CARD_MIN unless the right pane needs more."""

    def __init__(self, paned: ttk.PanedWindow, minimum: int, width: int | None = None, card_min: int = CARD_MIN):
        self.paned = paned
        self.minimum = minimum
        self.width = width
        self.card_min = card_min
        self._forced: tuple[int, int] | None = None  # the (sash, pane width) last set again
        self._dragging = False
        paned.bind("<Configure>", self.place, add="+")
        paned.bind("<ButtonPress-1>", self._press, add="+")
        paned.bind("<ButtonRelease-1>", self._release, add="+")
        first = paned.panes()[0]
        paned.nametowidget(first).bind("<Configure>", self.place, add="+")
        self.place()

    def target(self, total: int) -> int:
        """The sash position for a paned window ``total`` pixels wide."""
        want = self.width if self.width is not None else self.paned.sashpos(0)
        painter = getattr(self.paned._root(), "painter", None)
        card_min = round(self.card_min * painter.scale) if painter is not None else self.card_min
        return max(self.minimum, min(want, total - card_min))

    def place(self, _event: tk.Event | None = None) -> None:
        if self._dragging:
            return
        total = self.paned.winfo_width()
        if total <= 1:  # not laid out yet; the first <Configure> places the sash
            return
        want = self.target(total)
        if self.paned.sashpos(0) != want:
            self.paned.sashpos(0, want)
            return
        # the pane's own width too: ttk may keep the sash where it was told while the pane
        # stays at the width it had (a pane with a fixed width, laid out before the sash).
        # Once per case and on screen only: Tk on macOS lays out no panes in a hidden window,
        # and setting the sash again and again there never ended
        pane = self.paned.nametowidget(self.paned.panes()[0])
        case = (want, pane.winfo_width())
        if abs(case[1] - want) > 1 and case != self._forced and self.paned.winfo_ismapped():
            self._forced = case
            self.paned.sashpos(0, want)

    def _press(self, event: tk.Event) -> None:
        self._dragging = self.paned.identify(event.x, event.y) != ""

    def _release(self, _event: tk.Event) -> None:
        if not self._dragging:
            return
        self._dragging = False
        self.width = max(self.minimum, self.paned.sashpos(0))
        self.place()
