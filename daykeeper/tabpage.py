"""What a tab is made of: a page with a frame of its own, a sidebar on the left and a
scrollable area on the right, a view key that says when it must be rebuilt, and the
settler that gives the inner frame the width of its canvas once a resize pauses."""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk
from typing import Protocol

from . import palette
from .journal import today
from .revisions import VIEW_DOMAINS
from .lock_view import SecurityController
from .session import Session
from .sidebar import SidebarKeeper
from .theme import Painter
from .timers import Debounce
from .vault import Entry, Vault

RESIZE_SETTLE_MS = 120  # a window resize that pauses this long has ended
CHUNK_FIRST_MS = 120  # a tab builds this much of itself before it is shown (ChunkBuilder)
CHUNK_MS = 80  # ... and this much per slice after that, between repaints
SIDEBAR_PADDING = (16, 16, 12, 12)


class Settler:
    """Gives the inner frame of a scrollable canvas the width of the canvas once a window
    resize pauses: re-laying out and repainting every card on each step of a drag made
    the cards shake. A tab underneath waits until it is shown (show_page calls it)."""

    def __init__(self, app, canvas: tk.Canvas, window_item: int):
        self.app, self.canvas, self.item = app, canvas, window_item
        self.pause = Debounce(canvas, RESIZE_SETTLE_MS, self)
        self.width = 0

    def __call__(self) -> None:
        width = self.canvas.winfo_width()
        if width > 1 and width != self.width:
            self.width = width
            self.canvas.itemconfigure(self.item, width=width)

    def on_resize(self, _event: tk.Event) -> None:
        self.cancel()
        if not self.width:
            self()
        elif self.app.view_of(self.canvas) in (None, self.app.current_view()):
            self.pause.touch()

    def cancel(self) -> None:
        self.pause.cancel()


def make_scrollable(app, parent: tk.Widget, padding: tuple[int, int, int, int]) -> tuple[tk.Canvas, ttk.Scrollbar, ttk.Frame]:
    """A canvas with vertical scrolling and an inner frame of full width."""
    canvas = tk.Canvas(parent, bg=palette.CARD, highlightthickness=0, bd=0)
    canvas.scrollable_canvas = True
    vsb = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vsb.set)
    vsb.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    inner = ttk.Frame(canvas, style="Card.TFrame", padding=padding)
    win = canvas.create_window((0, 0), window=inner, anchor="nw")
    inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    settler = Settler(app, canvas, win)
    canvas.bind("<Configure>", settler.on_resize)
    app.add_settler(canvas, settler)
    return canvas, vsb, inner


class Shell(Protocol):
    """What a page may ask of the window (App is the one object that provides it).
    Pages change the models and call `mark_dirty`; they never save or lock themselves."""

    session: Session
    painter: Painter
    mode: str
    background_jobs: bool  # False in a preview copy: no routines on a schedule, no site icons, no GitLab

    @property
    def vault(self) -> Vault | None: ...

    def mark_dirty(self, domain: str | None = None) -> None: ...

    def set_status(self, text: str) -> None: ...

    def show_page(self, mode: str, select_entry: Entry | None = None) -> None: ...

    def round_style(self, fill: str, border: str | None, radius: float = 12, outer: str = palette.CARD, width: float = 1.0) -> str: ...

    def build_in_chunks(self, name: str, items: list, build, after_chunk, done) -> None: ...

    def finish_chunks(self, name: str) -> None: ...

    def copy_to_clipboard(self, value: str, secret: bool = True) -> None: ...

    security: SecurityController  # secrets_open(), require_secrets(then)

    def page(self, mode: str) -> TabPage: ...  # the tab, made on its first visit

    def tab_on(self, mode: str) -> bool: ...

    def apply_tabs(self) -> None: ...

    def view_of(self, widget: tk.Misc) -> tk.Misc | None: ...

    def current_view(self) -> tk.Misc | None: ...

    def add_settler(self, canvas: tk.Canvas, settler: Settler) -> None: ...

    def update_lock_button(self) -> None: ...

    def update_integrations_badge(self) -> None: ...

    def default_gitlab_url(self) -> str: ...


class ChunkBuilder:
    """Builds the cards of a long tab in slices of time: as many as fit now, the rest
    between repaints, so the first screen shows at once instead of after the last tile."""

    def __init__(self, app: tk.Misc):
        self.app = app
        self._chunk_jobs: dict[str, str] = {}  # a tab being built in slices → its pending job
        self._chunk_rest: dict[str, object] = {}  # → what finishes the build at once

    def build(self, name: str, items: list, build, after_chunk, done, first_ms: int = CHUNK_FIRST_MS) -> None:
        """Make `build(item)` for every item: as many as fit in a slice of time now, the
        rest between repaints — a long tab shows its first screen at once instead of
        after its last tile. `after_chunk()` lays out what is there, `done()` runs at the
        end. A new build of the same tab drops the one still waiting; `finish`
        completes it at once (a new card must be there to focus)."""
        self.cancel(name)
        state = {"i": 0}

        def step(budget_ms: float) -> None:
            t0 = time.perf_counter()
            while state["i"] < len(items) and (time.perf_counter() - t0) * 1000 < budget_ms:
                build(items[state["i"]])
                state["i"] += 1
            after_chunk()
            if state["i"] < len(items):
                # after_idle lets the new widgets paint first; the timer then keeps the next
                # slice out of update_idletasks, which would otherwise run every slice at once
                self._chunk_jobs[name] = self.app.after_idle(lambda: self._chunk_timer(name, lambda: step(CHUNK_MS)))
            else:
                self._chunk_jobs.pop(name, None)
                self._chunk_rest.pop(name, None)
                done()

        self._chunk_rest[name] = lambda: step(float("inf"))
        step(first_ms)

    def _chunk_timer(self, name: str, fn) -> None:
        self._chunk_jobs[name] = self.app.after(1, fn)

    def cancel(self, name: str) -> None:
        job = self._chunk_jobs.pop(name, None)
        if job is not None:
            self.app.after_cancel(job)
        self._chunk_rest.pop(name, None)

    def building(self) -> list[str]:
        """The tabs with slices still to make."""
        return list(self._chunk_jobs)

    def cancel_all(self) -> None:
        for name in list(self._chunk_jobs):
            self.cancel(name)

    def finish(self, name: str) -> None:
        """What a slice build of `name` still had to make is made now."""
        rest = self._chunk_rest.get(name)
        if rest is not None:
            job = self._chunk_jobs.pop(name, None)
            if job is not None:
                self.app.after_cancel(job)
            rest()


class TabPage:
    """One tab: `frame` is placed over the content area by the window; the page builds
    itself into it in `build`. The window asks `view_key()` before showing the page and
    calls `refresh()` when it changed; `on_shown()` on every visit; `focus_search()` and
    `new_item()` for the shortcuts; `cancel_jobs()` when the screen goes away."""

    name = ""  # the mode the window knows the page by; also its domain in VIEW_DOMAINS

    def __init__(self, app: Shell, parent: tk.Misc):
        self.app = app
        self.frame = ttk.Frame(parent)
        self.build(self.frame)

    @property
    def vault(self):
        return self.app.vault

    def build(self, frame: ttk.Frame) -> None:
        raise NotImplementedError

    def view_key(self) -> tuple:
        """What the page shows depends on: the changes to the domains it draws from and the day."""
        return self.app.session.rev.of(*VIEW_DOMAINS.get(self.name, ())) + (today(),)

    def refresh(self, keep_scroll: bool = False) -> None:
        """Rebuild for a visit after the data changed; what was scrolled to stays scrolled to."""

    def on_shown(self) -> None:
        """Every visit, after refresh when there was one (the clock moved, say)."""

    def focus_search(self) -> None:
        """Ctrl+F on this tab."""

    def new_item(self) -> None:
        """Ctrl+N on this tab."""

    def cancel_jobs(self) -> None:
        """The screen is going away (lock, language switch): jobs still waiting would hit destroyed widgets."""

    # ---- building blocks

    def sidebar(self, paned: ttk.PanedWindow) -> ttk.Frame:
        """The left pane; the card on the right takes the resize."""
        left = ttk.Frame(paned, style="Sidebar.TFrame", padding=SIDEBAR_PADDING)
        paned.add(left, weight=0)
        return left

    def scrolled_host(self, paned: ttk.PanedWindow, padding: tuple[int, int, int, int], minimum: int = 240, width: int | None = None) -> tuple[ttk.Frame, tk.Canvas, ttk.Scrollbar, ttk.Frame]:
        """The right pane with a scrollable inner frame; the sidebar is pinned to its width."""
        host = ttk.Frame(paned, style="Card.TFrame")
        paned.add(host, weight=1)
        px = self.app.painter.px  # the numbers are at 100 %
        SidebarKeeper(paned, minimum=px(minimum), width=None if width is None else px(width))
        canvas, vsb, inner = make_scrollable(self.app, host, padding)
        return host, canvas, vsb, inner
