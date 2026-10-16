"""The “Notes” tab: days and search on the left, cards of the day on the right.

A card has a title, free text, a checklist and attached files. Checklist items are added
with the “+” row at the bottom of the list (Enter adds and keeps typing); done items move
below under a “Done (n)” header that folds. The “⋯” menu holds the card settings: colour,
width, folding of done items, files, carrying over, deletion.

A page of the window (TabPage)."""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import filedialog, ttk

from . import palette
from .i18n import _, fmt_day, hotkeys
from .layout import CARD_GAP, CARD_MIN_WIDTH, column_width, flow_layout
from .theme import RoundCard, blend
from .popup import PopupMenu
from .journal import today
from .md_view import FileViewer, open_with_system, show_in_folder
from .notes import MAX_SIZE, NOTE_COLORS, Attachment, Note, NoteItem
from .dialogs import ask_yes_no, show_warning
from .sidebar import SidebarKeeper
from .tabpage import Shell, TabPage, make_scrollable
from .timers import IdleOnce
from .widgets import Placeholder, TextPlaceholder, Tooltip, WrapLine, glyph_button, make_fonts

CARD_SMALLEST = 200  # the narrowest a card can be dragged
RESIZE_CURSORS = {"x": "sb_h_double_arrow", "y": "sb_v_double_arrow", "xy": "size_nw_se"}
UNFINISHED = "__unfinished__"  # a “day” on the left: everything unfinished across all days
WITH_FILES = "__files__"  # a “day” on the left: every card with attached files
FILE_ICONS = {".md": "📝", ".markdown": "📝", ".txt": "📄", ".log": "📄", ".pdf": "📕", ".png": "🖼", ".jpg": "🖼", ".jpeg": "🖼"}


class CheckBox(tk.Canvas):
    """A rounded check box drawn on a canvas: outlined when open, filled with a tick when done."""

    SIZE = 18

    def __init__(self, master: tk.Misc, bg: str, accent: str, muted: str, checked: bool, command):
        super().__init__(master, width=self.SIZE, height=self.SIZE, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self.accent, self.muted, self.checked, self.hover = accent, muted, checked, False
        self.bind("<Button-1>", lambda _e: command())
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.draw()

    def _set_hover(self, on: bool) -> None:
        self.hover = on
        self.draw()

    def set(self, checked: bool) -> None:
        self.checked = checked
        self.draw()

    def draw(self) -> None:
        self.delete("all")
        s, r = self.SIZE - 2, 5
        x0 = y0 = 1
        pts = [x0 + r, y0, s - r, y0, s, y0, s, y0 + r, s, s - r, s, s, s - r, s, x0 + r, s, x0, s, x0, s - r, x0, y0 + r, x0, y0]
        if self.checked:
            self.create_polygon(pts, smooth=True, fill=self.accent, outline=self.accent)
            self.create_line(5, 9.5, 8, 12.5, 13.5, 6, fill=palette.ON_ACCENT, width=2.2, capstyle="round", joinstyle="round")
        else:
            self.create_polygon(pts, smooth=True, fill=palette.FIELD, outline=self.accent if self.hover else self.muted, width=1.6)


class NotesPage(TabPage):
    """The Notes tab. `nday` is the day (or the list) on the left, None before the first visit."""

    name = "notes"

    def __init__(self, app: Shell, parent: tk.Misc):
        self.nday: str | None = None
        self._nloading = False
        self._nshown: list[Note] = []  # the cards on screen, in order
        super().__init__(app, parent)

    # ---- what the window asks for

    def refresh(self, keep_scroll: bool = False) -> None:
        self._refresh_ndays(keep_scroll=keep_scroll)

    def new_item(self) -> None:
        self.new_note()

    def focus_search(self) -> None:
        self._nsearch_entry.focus_set()

    def cancel_jobs(self) -> None:
        """The tab is going away: a relayout waiting in after_idle would hit its destroyed widgets."""
        self._nflow_relayout.cancel()

    # ---- building the tab

    def build(self, frame: ttk.Frame) -> None:
        paned = ttk.PanedWindow(frame, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned, style="Sidebar.TFrame", padding=(16, 16, 12, 12))
        paned.add(left, weight=0)
        self.nsearch_var = tk.StringVar()
        self.nsearch_var.trace_add("write", lambda *_: self._render_notes())
        search = ttk.Entry(left, textvariable=self.nsearch_var, font=(palette.FONT, 11))
        search.pack(fill="x", pady=(0, 10))
        self._nsearch_entry = search
        Placeholder(search, _("🔍 Search notes"), palette.FIELD, var=self.nsearch_var, padx=8)
        Tooltip(search, _("Search across all days: title, text, items, file names"))
        ttk.Label(left, text=_("DAYS"), style="SidebarMuted.TLabel", font=(palette.FONT, 10, "bold")).pack(anchor="w", pady=(0, 6))
        self.ndays_tree = ttk.Treeview(left, columns=("counts",), show="tree", selectmode="browse")
        self.ndays_tree.column("#0", width=170, stretch=True)
        self.ndays_tree.column("counts", width=70, anchor="e", stretch=False)
        self.ndays_tree.pack(fill="both", expand=True)
        self.ndays_tree.bind("<<TreeviewSelect>>", self._on_nday_select)
        self.ndays_tree.bind("<Button-1>", self._on_nday_click, add="+")
        self.ndays_tree.tag_configure("today", font=(palette.FONT, 11, "bold"))
        Tooltip(self.ndays_tree, _("done / total items"))
        btns = ttk.Frame(left, style="Sidebar.TFrame")
        btns.pack(fill="x", pady=(12, 0))
        ttk.Button(btns, text=_("+ Note"), style="Primary.TButton", command=self.new_note).pack(side="left")
        ttk.Button(btns, text=_("+ Checklist"), style="Small.TButton", command=lambda: self.new_note(checklist=True)).pack(side="left", padx=(8, 0))

        host = ttk.Frame(paned, style="Card.TFrame")
        paned.add(host, weight=1)
        SidebarKeeper(paned, minimum=self.app.painter.px(240))
        self.ncanvas, _vsb, self.ngrid = make_scrollable(self.app, host, (24, 20, 24, 20))
        self.ngrid.columnconfigure(0, weight=1)
        # cards are placed by hand (flow_layout): a card is as wide and tall as dragged,
        # pixel by pixel, which a grid of columns cannot give
        self.nflow = tk.Frame(self.ngrid, bg=palette.CARD, height=1)
        self.nflow.grid(row=1, column=0, columnspan=99, sticky="ew")
        self.nflow.bind("<Configure>", self._on_nflow_resize, add="+")
        self._nflow_relayout = IdleOnce(self.app, self._relayout_now)
        self.ntitle = ttk.Label(self.ngrid, text="", style="Card.TLabel", font=(palette.FONT, 17, "bold"))
        self.ntitle.grid(row=0, column=0, columnspan=99, sticky="w", pady=(0, 12))
        self.nempty = ttk.Label(self.ngrid, text="", style="CardMuted.TLabel", font=(palette.FONT, 12))
        self._ncards: dict[str, tk.Frame] = {}
        self._nflow_width = 0
        self._nfonts = make_fonts({
            "title": (13, "bold"), "text": (11,), "item": (11,), "item_done": (11, "strike"),
            "small": (9,), "small_bold": (9, "bold"), "icon": (12,), "plus": (14, "bold"),
        })

    # ---- day list

    def _nselected_day(self) -> str:
        return self.nday or today()

    def _refresh_ndays(self, select: str | None = None, keep_scroll: bool = False) -> None:
        if self.vault is None:
            return
        notes = self.vault.notes
        tree = self.ndays_tree
        tree.delete(*tree.get_children())
        unfinished = notes.unfinished()
        tree.insert("", "end", iid=UNFINISHED, text=_("  ⏳ Unfinished"), values=(str(len(unfinished)) if unfinished else "",))
        with_files = notes.with_files()
        tree.insert("", "end", iid=WITH_FILES, text=_("  📎 With files"), values=(str(len(with_files)) if with_files else "",))
        dates = notes.dates()
        if today() not in dates:
            dates.insert(0, today())
        for date in dates:
            done, total = notes.day_progress(date)
            tree.insert("", "end", iid=date, text=f"  {fmt_day(date)}", values=(f"{done} / {total}" if total else "",), tags=("today",) if date == today() else ())
        target = select or self._nselected_day()
        if not tree.exists(target):
            target = today()
        self.nday = target
        self._nloading = True
        try:
            tree.selection_set(target)
            tree.see(target)
        finally:
            self._nloading = False
        self._render_notes(keep_scroll=keep_scroll)

    def _on_nday_select(self, _event=None) -> None:
        if self._nloading or self.vault is None:
            return
        sel = self.ndays_tree.selection()
        # Tk delivers <<TreeviewSelect>> after _refresh_ndays has picked the day itself (and
        # once more when tree.delete dropped the old selection): that day is already on
        # screen, so there is nothing to draw again
        if not sel or sel[0] == self.nday:
            return
        self.nday = sel[0]
        if self.nsearch_var.get():
            self.nsearch_var.set("")  # picking a day clears the search; its trace draws the cards
        else:
            self._render_notes()

    def _on_nday_click(self, event: tk.Event) -> None:
        """A click on the day already selected while a search is on: back to the day."""
        if self.nsearch_var.get() and self.ndays_tree.identify_row(event.y) == self.nday:
            self.nsearch_var.set("")

    # ---- card grid

    def _on_nflow_resize(self, event: tk.Event) -> None:
        if event.width != self._nflow_width:
            self._nflow_width = event.width
            self._place_note_cards()

    def _relayout_soon(self, _event=None) -> None:
        """A card changed its own height (typing, items): move the ones below, once."""
        self._nflow_relayout.touch()

    def _relayout_now(self) -> None:
        if self.nflow.winfo_exists():
            self._place_note_cards()


    def _visible_notes(self) -> tuple[str, list[Note]]:
        notes = self.vault.notes
        query = self.nsearch_var.get().strip()
        if query:
            hits = notes.search(query)
            return _("Search “{query}”: {n}").format(query=query, n=len(hits)), hits
        if self.nday == UNFINISHED:
            hits = notes.unfinished()
            return _("Unfinished: {n}").format(n=len(hits)), hits
        if self.nday == WITH_FILES:
            hits = notes.with_files()
            return _("With files: {n}").format(n=len(hits)), hits
        return fmt_day(self.nday) + (_("  · today") if self.nday == today() else ""), notes.for_day(self.nday)

    def _across_days(self) -> bool:
        return self.nday in (UNFINISHED, WITH_FILES) or bool(self.nsearch_var.get().strip())

    def _render_notes(self, keep_scroll: bool = False) -> None:
        """Rebuild the cards from scratch (day change, search, deletion, order change)."""
        if self.vault is None:
            return
        top = self.ncanvas.yview()[0]
        for card in self._ncards.values():
            card.destroy()
        self._ncards = {}
        title, shown = self._visible_notes()
        self.ntitle.config(text=title)
        self._nshown = shown

        def build(note: Note) -> None:
            self._ncards[note.id] = self._build_note_card(note)

        def done() -> None:
            self.ncanvas.update_idletasks()
            self.ncanvas.yview_moveto(top if keep_scroll else 0)

        # in slices: the first screen of a long day shows at once, the rest between repaints
        self.app.build_in_chunks("notes", shown, build, self._place_note_cards, done)

    def _refresh_card(self, note: Note) -> None:
        """Rebuild one card in place (colour, width, files) without touching the others."""
        old = self._ncards.get(note.id)
        if old is None:
            return
        top = self.ncanvas.yview()[0]
        old.destroy()
        self._ncards[note.id] = self._build_note_card(note)
        self._place_note_cards()
        self.ncanvas.update_idletasks()
        self.ncanvas.yview_moveto(top)

    def _card_size(self, note: Note, card: tk.Frame, avail: int) -> tuple[int, int]:
        """The width and height of a card in screen pixels: as dragged, else by the columns
        and the content. Never narrower than CARD_SMALLEST, never wider than the space."""
        px = self.app.painter.px
        cols, col = column_width(avail, px(CARD_GAP), px(CARD_MIN_WIDTH))
        if note.width:
            w = px(note.width)
        else:
            span = min(note.span, cols)
            w = round(col * span + px(CARD_GAP) * (span - 1))
        w = max(min(px(CARD_SMALLEST), avail), min(w, avail))
        h = max(px(note.height), self._card_min_height(card)) if note.height else card.winfo_reqheight()
        return w, h

    def _card_min_height(self, card: tk.Frame) -> int:
        """The content of a card without its text: the text shrinks to two lines, no lower."""
        text = card.text_widget  # type: ignore[attr-defined]
        line = self._nfonts["text"].metrics("linespace")
        return card.winfo_reqheight() - text.winfo_reqheight() + 2 * line + 12

    def _place_note_cards(self) -> None:
        self.nempty.grid_forget()
        shown = self._nshown
        if not shown:
            self.nempty.config(
                text=hotkeys(_("Nothing here yet. “+ Note” or “+ Checklist” on the left; Ctrl+N also adds a note."))
                if not self.nsearch_var.get().strip()
                else _("Nothing found.")
            )
            self.nempty.grid(row=2, column=0, columnspan=99, sticky="w")
            self.nflow.configure(height=1)
            return
        avail = self.nflow.winfo_width()
        if avail <= 1:
            avail = max(CARD_MIN_WIDTH, self.ncanvas.winfo_width() - 48)
        cards = [(n, self._ncards.get(n.id)) for n in shown]
        cards = [(n, c) for n, c in cards if c is not None]
        sizes = [self._card_size(n, c, avail) for n, c in cards]
        spots, total = flow_layout(sizes, avail, self.app.painter.px(CARD_GAP))
        for (_n, card), (w, h), (x, y) in zip(cards, sizes, spots, strict=True):
            card.place(x=x, y=y, width=w, height=h)
        if int(self.nflow.cget("height")) != total + CARD_GAP:
            self.nflow.configure(height=total + CARD_GAP)

    # ---- one card

    def _build_note_card(self, note: Note) -> tk.Frame:
        bg = note.bg()
        outline = palette.BORDER if not note.color else blend(note.accent(), bg, 0.35)
        card = RoundCard(self.nflow, self.app, bg, outline, radius=14, outer=palette.CARD, padx=8, pady=6)
        card.note = note  # type: ignore[attr-defined]
        inner = card.body
        card.inner = inner  # type: ignore[attr-defined]

        # ---- title + pin
        head = tk.Frame(inner, bg=bg)
        head.pack(fill="x")
        title_var = tk.StringVar(value=note.title)
        title = tk.Entry(head, textvariable=title_var, font=self._nfonts["title"], bg=bg, fg=palette.TEXT, relief="flat", bd=0, insertbackground=palette.TEXT)
        title.pack(side="left", fill="x", expand=True)
        card.title_entry = title  # type: ignore[attr-defined]
        self._editable(title, bg)
        Placeholder(title, _("Title"), bg, var=title_var, padx=2)
        title_var.trace_add("write", lambda *_: self._note_edit(note, "title", title_var.get()))
        title.bind("<Return>", lambda _e: card.text_widget.focus_set())  # type: ignore[attr-defined]
        pin = glyph_button(head, "📌" if note.pinned else "📍", bg, self._nfonts["icon"], lambda: self._note_pin(note), _("Unpin") if note.pinned else _("Pin to the top of the day"), active=note.pinned)
        pin.pack(side="right")

        # ---- text
        text = tk.Text(inner, height=2 if note.height else max(2, min(8, note.text.count("\n") + 2)), font=self._nfonts["text"], bg=bg, fg=palette.TEXT, relief="flat", bd=0, wrap="word", undo=True, insertbackground=palette.TEXT, padx=2, pady=4)
        text.insert("1.0", note.text)
        text.edit_modified(False)
        text.pack(fill="both", expand=True, pady=(2, 0))  # a card dragged taller gives the room to the text
        self._editable(text, bg)
        TextPlaceholder(text, _("Note text…"), bg)
        text.bind("<<Modified>>", lambda _e, w=text: self._note_text_modified(note, w))
        text.bind("<KeyRelease>", lambda _e, w=text: note.height or self._grow_text(w))
        card.text_widget = text  # type: ignore[attr-defined]

        # ---- checklist: progress, open items, the “+” row, done items
        checklist = tk.Frame(inner, bg=bg)
        checklist.pack(fill="x", pady=(6, 0))
        card.checklist = checklist  # type: ignore[attr-defined]
        progress = tk.Frame(checklist, bg=bg)
        card.progress_row = progress  # type: ignore[attr-defined]
        count = tk.Label(progress, text="", bg=bg, fg=palette.MUTED, font=self._nfonts["small_bold"])
        count.pack(side="right", padx=(8, 0))  # packed first, so the bar never squeezes it
        bar = tk.Canvas(progress, height=4, width=10, bg=bg, highlightthickness=0, bd=0)
        bar.pack(side="left", fill="x", expand=True, pady=(0, 2))
        bar.bind("<Configure>", lambda _e: self._draw_progress(card, note))
        card.progress_bar = bar  # type: ignore[attr-defined]
        card.progress_count = count  # type: ignore[attr-defined]
        open_box = tk.Frame(checklist, bg=bg)
        open_box.pack(fill="x")
        card.open_box = open_box  # type: ignore[attr-defined]

        add_row = tk.Frame(checklist, bg=bg)
        add_row.pack(fill="x", pady=(2, 0))
        add_var = tk.StringVar()
        plus = tk.Label(add_row, text="+", bg=bg, fg=note.accent(), font=self._nfonts["plus"], cursor="hand2", width=1)
        plus.pack(side="left", padx=(1, 7))
        add = tk.Entry(add_row, textvariable=add_var, font=self._nfonts["item"], bg=bg, fg=palette.TEXT, relief="flat", bd=0, insertbackground=palette.TEXT)
        add.pack(side="left", fill="x", expand=True)
        self._editable(add, bg)
        Placeholder(add, _("Add item"), bg, var=add_var, padx=2)
        add.bind("<Return>", lambda _e: self._note_add_item(card, note, add_var))
        add.bind("<Escape>", lambda _e: (add_var.set(""), card.focus_set()))
        # typed and left without Enter (a click elsewhere, another tab): it still becomes an item
        add.bind("<FocusOut>", lambda _e: add.after_idle(lambda: self._commit_left_item(card, note, add, add_var)), add="+")
        add._commit_typed = lambda: self._commit_left_item(card, note, add, add_var, clicked_away=True)  # type: ignore[attr-defined]
        self._watch_clicks_away()
        plus.bind("<Button-1>", lambda _e: self._note_add_item(card, note, add_var) if add_var.get().strip() else add.focus_set())
        Tooltip(plus, _("Add a checklist item (Enter adds and keeps typing)"))
        card.add_entry = add  # type: ignore[attr-defined]
        card.add_var = add_var  # type: ignore[attr-defined]

        done_head = tk.Frame(checklist, bg=bg)
        card.done_head = done_head  # type: ignore[attr-defined]
        fold = tk.Label(done_head, text="", bg=bg, fg=palette.MUTED, font=self._nfonts["small_bold"], cursor="hand2")
        fold.pack(side="left")
        fold.bind("<Button-1>", lambda _e: self._note_fold_done(card, note))
        card.done_fold = fold  # type: ignore[attr-defined]
        clear = tk.Label(done_head, text=_("Clear"), bg=bg, fg=palette.MUTED, font=self._nfonts["small"], cursor="hand2")
        clear.pack(side="right")
        clear.bind("<Button-1>", lambda _e: self._note_clear_done(card, note))
        Tooltip(clear, _("Remove the done items"))
        done_box = tk.Frame(checklist, bg=bg)
        card.done_box = done_box  # type: ignore[attr-defined]
        self._render_checklist(card, note)

        # ---- files
        if note.attachments:
            files = tk.Frame(inner, bg=bg)
            files.pack(fill="x", pady=(8, 0))
            for att in note.attachments:
                self._build_file_row(files, note, att, bg)

        # ---- footer
        foot = tk.Frame(inner, bg=bg)
        foot.pack(fill="x", pady=(10, 0))
        stamp = tk.Label(foot, text=time.strftime("%H:%M", time.localtime(note.updated)), bg=bg, fg=palette.MUTED, font=self._nfonts["small"])
        stamp.pack(side="left")
        if self._across_days():
            tk.Label(foot, text=fmt_day(note.date), bg=bg, fg=palette.MUTED, font=self._nfonts["small"]).pack(side="left", padx=(10, 0))
        if note.undone_items() and note.date != today():
            carry = tk.Label(foot, text=_("⤴ to today"), bg=bg, fg=note.accent(), cursor="hand2", font=self._nfonts["small_bold"])
            carry.pack(side="left", padx=(10, 0))
            carry.bind("<Button-1>", lambda _e: self._note_carry_over(note))
            Tooltip(carry, _("Undone items as a new card for today"))
        more = glyph_button(foot, "⋯", bg, self._nfonts["icon"], None, _("Card settings"))
        more.bind("<Button-1>", lambda e: self._note_menu(note, e))
        more.pack(side="right")
        colour = glyph_button(foot, "🎨", bg, self._nfonts["icon"], None, _("Card colour"))
        colour.bind("<Button-1>", lambda e: self._note_color_menu(note, e))
        colour.pack(side="right", padx=(0, 6))
        glyph_button(foot, "📎", bg, self._nfonts["icon"], lambda: self._note_attach(note), _("Attach files")).pack(side="right", padx=(0, 6))
        self._resizable(card, note, text)
        card.bind("<Button-3>", lambda e: self._note_menu(note, e))
        inner.bind("<Button-3>", lambda e: self._note_menu(note, e))
        return card

    def _resizable(self, card: RoundCard, note: Note, text: tk.Text) -> None:
        """Resize a card as a window, pixel by pixel: the right edge for the width, the
        bottom edge for the height, the bottom-right corner for both. A double click on an
        edge gives that size back to the columns or the content."""
        edge = self.app.painter.px(10)
        scale = self.app.painter.scale
        drag: dict = {}
        hover = {"zone": ""}
        card.bind("<Configure>", self._relayout_soon, add="+")

        def zone_at(e) -> str:
            x, y = e.x_root - card.winfo_rootx(), e.y_root - card.winfo_rooty()
            w, h = card.winfo_width(), card.winfo_height()
            right = w - edge <= x <= w + 2
            bottom = h - edge <= y <= h + 2
            return "xy" if right and bottom else "x" if right else "y" if bottom else ""

        def show(zone: str) -> None:
            if zone != hover["zone"]:
                hover["zone"] = zone
                cursor = RESIZE_CURSORS.get(zone, "")
                card.configure(cursor=cursor)
                card.body.configure(cursor=cursor)

        def motion(e):
            if drag:
                return drag_to(e)
            show(zone_at(e))
            return None

        def leave(_e):
            if not drag:
                show("")

        def press(e):
            zone = zone_at(e)
            if not zone:
                return None
            show(zone)
            drag.update(zone=zone, x=e.x_root, y=e.y_root, w=card.winfo_width(), h=card.winfo_height(), before=(note.width, note.height))
            return "break"

        def drag_to(e):
            if "x" in drag["zone"]:
                width = max(self.app.painter.px(CARD_SMALLEST), drag["w"] + e.x_root - drag["x"])
                note.width = min(MAX_SIZE, round(width / scale))
            if "y" in drag["zone"]:
                if not note.height:
                    text.config(height=2)  # the card's height now decides, the text fills it
                height = max(self._card_min_height(card), drag["h"] + e.y_root - drag["y"])
                note.height = min(MAX_SIZE, round(height / scale))
            self._place_note_cards()
            return "break"

        def release(_e):
            if not drag:
                return None
            before = drag["before"]
            drag.clear()
            if (note.width, note.height) != before:
                self._note_changed(note)
            return "break"

        def double(e):
            zone = zone_at(e)
            if not zone:
                return None
            if "x" in zone:
                note.width = 0
            if "y" in zone:
                note.height = 0
                self._grow_text(text)
            self._note_changed(note)
            self._place_note_cards()
            return "break"

        for w in (card, card.body):
            w.bind("<Motion>", motion, add="+")
            w.bind("<Leave>", leave, add="+")
            w.bind("<ButtonPress-1>", press, add="+")
            w.bind("<B1-Motion>", motion, add="+")
            w.bind("<ButtonRelease-1>", release, add="+")
            w.bind("<Double-Button-1>", double, add="+")

    # ---- checklist

    def _render_checklist(self, card: tk.Frame, note: Note) -> None:
        """(Re)draw the item rows of a card: open items above the “+” row, done ones below."""
        for box in (card.open_box, card.done_box):  # type: ignore[attr-defined]
            for child in box.winfo_children():
                child.destroy()
        card.item_rows = {}  # type: ignore[attr-defined]  # id(item) → its row
        for item in note.open_items():
            self._build_note_item_row(card, card.open_box, note, item)  # type: ignore[attr-defined]
        if not note.hide_done:
            for item in note.done_items():
                self._build_note_item_row(card, card.done_box, note, item)  # type: ignore[attr-defined]
        self._update_done_head(card, note)
        self._update_note_progress(card, note)

    def _update_done_head(self, card: tk.Frame, note: Note) -> None:
        """The “Done (n)” header and the box under it: shown when there are done items."""
        done = note.done_items()
        card.done_head.pack_forget()  # type: ignore[attr-defined]
        card.done_box.pack_forget()  # type: ignore[attr-defined]
        if done:
            arrow = "▸" if note.hide_done else "▾"
            card.done_fold.config(text=f"{arrow} " + _("Done ({n})").format(n=len(done)))  # type: ignore[attr-defined]
            card.done_head.pack(fill="x", pady=(8, 2))  # type: ignore[attr-defined]
            if not note.hide_done:
                card.done_box.pack(fill="x")  # type: ignore[attr-defined]

    def _move_item_row(self, card: tk.Frame, note: Note, item: NoteItem) -> None:
        """An item was ticked or unticked: its row alone is made anew in the other box, at
        its place there; the rest of the card stays as it is."""
        old = card.item_rows.pop(id(item), None)  # type: ignore[attr-defined]
        if old is not None:
            old.destroy()
        if item.done and note.hide_done:
            return  # done items are folded away
        box = card.done_box if item.done else card.open_box  # type: ignore[attr-defined]
        siblings = note.done_items() if item.done else note.open_items()
        after = siblings[siblings.index(item) + 1 :]
        before = next((card.item_rows[id(s)] for s in after if id(s) in card.item_rows), None)  # type: ignore[attr-defined]
        self._build_note_item_row(card, box, note, item, before=before)

    def _build_note_item_row(self, card: tk.Frame, box: tk.Frame, note: Note, item: NoteItem, before: tk.Frame | None = None) -> None:
        bg = note.bg()
        row = tk.Frame(box, bg=bg)
        row.pack(fill="x", pady=1, **({"before": before} if before is not None else {}))
        card.item_rows[id(item)] = row  # type: ignore[attr-defined]
        chk = CheckBox(row, bg, note.accent(), palette.MUTED, item.done, lambda: self._note_toggle(card, note, item))
        chk.pack(side="left", anchor="n", padx=(0, 8), pady=(3, 0))  # by the first line of a wrapped item
        # a long item wraps inside the card instead of running past its edge
        entry = WrapLine(
            row, item.text, lambda value: self._note_item_edit(note, item, value), font=self._nfonts["item_done" if item.done else "item"],
            bg=bg, fg=palette.MUTED if item.done else palette.TEXT, relief="flat", bd=0, insertbackground=palette.TEXT,
        )
        entry.pack(side="left", fill="x", expand=True)
        self._editable(entry, bg)
        entry.bind("<Return>", lambda _e: (card.add_entry.focus_set(), "break")[1])  # type: ignore[attr-defined]
        entry.bind("<Alt-Up>", lambda _e: self._note_move_item(card, note, item, -1))
        entry.bind("<Alt-Down>", lambda _e: self._note_move_item(card, note, item, +1))
        entry.bind("<BackSpace>", lambda _e: self._note_backspace_item(card, note, item, entry))
        rm = tk.Label(row, text="✕", bg=bg, fg=bg, cursor="hand2", font=self._nfonts["small"])
        rm.pack(side="right", padx=(6, 0))
        rm.bind("<Button-1>", lambda _e: self._note_remove_item(card, note, item))
        rm.bind("<Enter>", lambda _e: rm.config(fg=palette.DANGER))

        def hover(on: bool) -> None:
            if not on:  # leaving into a child of the row still counts as inside
                under = row.winfo_containing(*row.winfo_pointerxy())
                while under is not None and under is not row:
                    under = under.master
                on = under is row
            rm.config(fg=palette.MUTED if on else bg)

        for w in (row, entry, chk):
            w.bind("<Enter>", lambda _e: hover(True), add="+")
            w.bind("<Leave>", lambda _e: hover(False), add="+")
        menu_cmd = lambda e: self._note_item_menu(card, note, item, e)  # noqa: E731
        for w in (row, entry):
            w.bind("<Button-3>", menu_cmd)

    def _update_note_progress(self, card: tk.Frame, note: Note) -> None:
        done, total = note.progress()
        row = card.progress_row  # type: ignore[attr-defined]
        if not total:
            row.pack_forget()
            return
        if not row.winfo_ismapped():
            row.pack(fill="x", before=card.open_box, pady=(0, 4))  # type: ignore[attr-defined]
        label = card.progress_count  # type: ignore[attr-defined]
        if done == total:
            label.config(text=_("✓ {done} / {total}").format(done=done, total=total), fg=palette.SUCCESS)
        else:
            label.config(text=f"{done} / {total}", fg=palette.MUTED)
        self._draw_progress(card, note)

    def _draw_progress(self, card: tk.Frame, note: Note) -> None:
        bar = card.progress_bar  # type: ignore[attr-defined]
        done, total = note.progress()
        width = bar.winfo_width()
        bar.delete("all")
        if not total or width <= 1:
            return
        bar.create_rectangle(0, 0, width, 4, fill=palette.BORDER, outline="")
        filled = int(width * done / total)
        if filled:
            bar.create_rectangle(0, 0, filled, 4, fill=palette.SUCCESS if done == total else note.accent(), outline="")

    # ---- files

    def _build_file_row(self, parent: tk.Frame, note: Note, att: Attachment, bg: str) -> None:
        exists = att.exists()
        frame = RoundCard(parent, self.app, bg, palette.BORDER, radius=8, outer=bg, padx=4, pady=1, cursor="hand2")
        frame.pack(fill="x", pady=2)
        row = frame.body
        row.config(cursor="hand2")
        icon = FILE_ICONS.get(att.suffix, "📄") if exists else "⚠"
        name = tk.Label(row, text=f"{icon}  {att.name}", bg=bg, fg=palette.TEXT if exists else palette.MUTED, font=self._nfonts["item"], anchor="w", cursor="hand2")
        name.pack(side="left", fill="x", expand=True)
        Tooltip(name, att.path if exists else _("File not found: {path}").format(path=att.path))
        rm = tk.Label(row, text="✕", bg=bg, fg=palette.MUTED, cursor="hand2", font=self._nfonts["small"])
        rm.pack(side="right")
        rm.bind("<Button-1>", lambda _e: self._note_detach(note, att))
        Tooltip(rm, _("Detach the file (the file itself stays on disk)"))
        for w in (row, name):
            w.bind("<Button-1>", lambda _e: self._open_attachment(att))
            w.bind("<Button-3>", lambda e: self._file_menu(note, att, e))
            w.bind("<Enter>", lambda _e: frame.set_border(note.accent()), add="+")
            w.bind("<Leave>", lambda _e: frame.set_border(palette.BORDER), add="+")

    def _open_attachment(self, att: Attachment) -> None:
        if not att.exists():
            show_warning(self.app, _("File not found: {path}").format(path=att.path))
            return
        if att.is_text():
            FileViewer(self.app, att.path)
        else:
            open_with_system(att.path)

    def _file_menu(self, note: Note, att: Attachment, event: tk.Event) -> None:
        menu = PopupMenu(self.app, tearoff=False)
        menu.add_command(label=_("Open"), command=lambda: self._open_attachment(att))
        menu.add_command(label=_("Open in the system app"), command=lambda: att.exists() and open_with_system(att.path))
        menu.add_command(label=_("Show in folder"), command=lambda: att.exists() and show_in_folder(att.path))
        menu.add_command(label=_("Copy path"), command=lambda: (self.app.clipboard_clear(), self.app.clipboard_append(att.path)))
        menu.add_separator()
        menu.add_command(label=_("Detach"), command=lambda: self._note_detach(note, att))
        menu.tk_popup(event.x_root, event.y_root)

    def _note_attach(self, note: Note) -> None:
        paths = filedialog.askopenfilenames(
            parent=self.app,
            title=_("Attach files to the note"),
            filetypes=[(_("Markdown and text"), "*.md *.markdown *.txt"), (_("All files"), "*.*")],
        )
        added = sum(1 for p in paths if note.attach(p) is not None)
        if not added:
            return
        self._note_changed(note)
        self._refresh_card(note)
        self._refresh_ndays_counts_only()
        self.app.set_status(_("Files attached: {n}").format(n=added))

    def _note_detach(self, note: Note, att: Attachment) -> None:
        note.detach(att)
        self._note_changed(note)
        self._refresh_card(note)
        self._refresh_ndays_counts_only()

    # ---- hints in empty fields

    def _editable(self, widget: tk.Entry | tk.Text, bg: str) -> None:
        """A field in a card looks like a field: a border on hover, blue while typing."""
        widget.config(highlightthickness=1, highlightbackground=bg, highlightcolor=palette.ACCENT, cursor="xterm")
        widget.bind("<Enter>", lambda _e: widget.config(highlightbackground=palette.BORDER), add="+")
        widget.bind("<Leave>", lambda _e: widget.config(highlightbackground=bg), add="+")


    @staticmethod
    def _grow_text(text: tk.Text) -> None:
        lines = int(text.index("end-1c").split(".")[0])
        text.config(height=max(2, min(12, lines + 1)))

    # ---- changes

    def _note_changed(self, note: Note, refresh_days: bool = False) -> None:
        """An edit made in a card: the card shows it already, so the tab is not rebuilt on
        its next visit — unless the list shown is picked by content (Unfinished, With files,
        a search), where the edit may add or drop a card."""
        note.touch()
        self.app.mark_dirty("notes" if self._across_days() else None)
        if refresh_days:
            self._refresh_ndays_counts_only()

    def _refresh_ndays_counts_only(self) -> None:
        """The numbers on the left without rebuilding the cards."""
        notes = self.vault.notes
        tree = self.ndays_tree
        for iid in tree.get_children():
            if iid == UNFINISHED:
                n = len(notes.unfinished())
                tree.set(iid, "counts", str(n) if n else "")
            elif iid == WITH_FILES:
                n = len(notes.with_files())
                tree.set(iid, "counts", str(n) if n else "")
            else:
                done, total = notes.day_progress(iid)
                tree.set(iid, "counts", f"{done} / {total}" if total else "")

    def _note_edit(self, note: Note, attr: str, value: str) -> None:
        if getattr(note, attr) == value:
            return
        setattr(note, attr, value)
        self._note_changed(note)

    def _note_text_modified(self, note: Note, widget: tk.Text) -> None:
        if not widget.edit_modified():
            return
        widget.edit_modified(False)
        self._note_edit(note, "text", widget.get("1.0", "end-1c"))

    def _note_item_edit(self, note: Note, item: NoteItem, value: str) -> None:
        if item.text == value:
            return
        item.text = value
        self._note_changed(note)

    def _note_add_item(self, card: tk.Frame, note: Note, var: tk.StringVar, keep_focus: bool = True) -> str:
        item = note.add_item(var.get())
        if item is None:
            return "break"
        var.set("")
        self._build_note_item_row(card, card.open_box, note, item)  # type: ignore[attr-defined]
        self._update_note_progress(card, note)
        self._note_changed(note, refresh_days=True)
        if keep_focus:
            card.add_entry.focus_set()  # type: ignore[attr-defined]
        return "break"

    def _commit_left_item(self, card: tk.Frame, note: Note, entry: tk.Entry, var: tk.StringVar, clicked_away: bool = False) -> None:
        """The text left in "Add item" becomes an item once the user is elsewhere in the app.
        Not when the whole window lost the focus (Alt+Tab): the typing goes on on return."""
        if not entry.winfo_exists() or not var.get().strip():
            return
        try:
            focus = self.app.focus_get()
        except (KeyError, tk.TclError):
            focus = None
        if focus is None or (focus is entry and not clicked_away):
            return
        self._note_add_item(card, note, var, keep_focus=False)

    def _watch_clicks_away(self) -> None:
        """A click on something that takes no focus (the background, a label) leaves the
        focus in "Add item", so no <FocusOut> comes: catch the click itself, once per app."""
        app = self.app
        if getattr(app, "_clicks_away_watched", False):  # the page is made anew on every unlock
            return
        app._clicks_away_watched = True  # type: ignore[attr-defined]

        def on_click(event: tk.Event) -> None:
            try:
                focus = app.focus_get()
            except (KeyError, tk.TclError):
                return
            commit = getattr(focus, "_commit_typed", None)
            if commit is not None and event.widget is not focus:
                commit()

        app.bind_all("<Button-1>", on_click, add="+")

    def _note_toggle(self, card: tk.Frame, note: Note, item: NoteItem) -> None:
        note.toggle(item)
        self._move_item_row(card, note, item)
        self._update_done_head(card, note)
        self._update_note_progress(card, note)
        self._note_changed(note, refresh_days=True)

    def _note_remove_item(self, card: tk.Frame, note: Note, item: NoteItem) -> None:
        note.remove_item(item)
        row = card.item_rows.pop(id(item), None)  # type: ignore[attr-defined]
        if row is not None:
            row.destroy()
        self._update_done_head(card, note)
        self._update_note_progress(card, note)
        self._note_changed(note, refresh_days=True)

    def _note_backspace_item(self, card: tk.Frame, note: Note, item: NoteItem, field: WrapLine) -> str | None:
        """Backspace in an empty item removes it and puts the cursor at the end of the one above."""
        if field.value():
            return None
        same = note.done_items() if item.done else note.open_items()
        idx = same.index(item) if item in same else 0
        self._note_remove_item(card, note, item)
        box = card.done_box if item.done else card.open_box  # type: ignore[attr-defined]
        rows = box.winfo_children()
        target = rows[idx - 1] if idx > 0 and rows else None
        if target is not None:
            entry = next((w for w in target.winfo_children() if isinstance(w, WrapLine)), None)
            if entry is not None:
                entry.focus_set()
                entry.cursor_to_end()
        else:
            card.add_entry.focus_set()  # type: ignore[attr-defined]
        return "break"

    def _note_move_item(self, card: tk.Frame, note: Note, item: NoteItem, delta: int) -> str:
        if note.move_item(item, delta):
            self._render_checklist(card, note)
            self._note_changed(note)
            box = card.done_box if item.done else card.open_box  # type: ignore[attr-defined]
            same = note.done_items() if item.done else note.open_items()
            rows = box.winfo_children()
            if item in same and same.index(item) < len(rows):
                entry = next((w for w in rows[same.index(item)].winfo_children() if isinstance(w, WrapLine)), None)
                if entry is not None:
                    entry.focus_set()
        return "break"

    def _note_item_menu(self, card: tk.Frame, note: Note, item: NoteItem, event: tk.Event) -> None:
        menu = PopupMenu(self.app, tearoff=False)
        menu.add_command(label=_("Mark as open") if item.done else _("Mark as done"), command=lambda: self._note_toggle(card, note, item))
        menu.add_command(label=_("Move up") + "\tAlt+↑", command=lambda: self._note_move_item(card, note, item, -1))
        menu.add_command(label=_("Move down") + "\tAlt+↓", command=lambda: self._note_move_item(card, note, item, +1))
        menu.add_separator()
        menu.add_command(label=_("Delete item"), command=lambda: self._note_remove_item(card, note, item))
        menu.tk_popup(event.x_root, event.y_root)

    def _note_fold_done(self, card: tk.Frame, note: Note) -> None:
        note.hide_done = not note.hide_done
        self._render_checklist(card, note)
        self._note_changed(note)

    def _note_clear_done(self, card: tk.Frame, note: Note) -> None:
        removed = note.clear_done()
        if removed:
            self._render_checklist(card, note)
            self._note_changed(note, refresh_days=True)
            self.app.set_status(_("Done items removed: {n}").format(n=removed))

    def _note_pin(self, note: Note) -> None:
        note.pinned = not note.pinned
        self._note_changed(note)
        self._render_notes(keep_scroll=True)

    def _note_menu(self, note: Note, event: tk.Event) -> None:
        """Card settings: width, folding, colour, files, carrying over, deletion."""
        card = self._ncards.get(note.id)
        menu = PopupMenu(self.app, tearoff=False)
        wide = tk.BooleanVar(value=note.span > 1)
        fold = tk.BooleanVar(value=note.hide_done)
        menu.add_checkbutton(label=_("Wide card"), variable=wide, command=lambda: self._note_set_wide(note, wide.get()))
        menu.add_checkbutton(label=_("Fold done items"), variable=fold, command=lambda: card is not None and self._note_fold_done(card, note))
        colours = PopupMenu(menu, tearoff=False)
        self._fill_color_menu(colours, note)
        menu.add_cascade(label=_("Colour"), menu=colours)
        menu.add_separator()
        menu.add_command(label=_("Attach files…"), command=lambda: self._note_attach(note))
        if note.done_items() and card is not None:
            menu.add_command(label=_("Clear done items"), command=lambda: self._note_clear_done(card, note))
        if note.undone_items():
            menu.add_command(label=_("Copy undone items to today"), command=lambda: self._note_carry_over(note))
        menu.add_separator()
        menu.add_command(label=_("Delete note"), command=lambda: self._note_delete(note))
        menu._vars = (wide, fold)  # type: ignore[attr-defined]  # keep the variables alive while the menu is open
        menu.tk_popup(event.x_root, event.y_root)

    def _fill_color_menu(self, menu: PopupMenu, note: Note) -> None:
        for code, label, _bg, accent in NOTE_COLORS:
            menu.add_command(label=_(label), background=accent, accelerator="✓" if code == note.color else "", command=lambda c=code: self._note_set_color(note, c))

    def _note_color_menu(self, note: Note, event: tk.Event) -> None:
        menu = PopupMenu(self.app, tearoff=False)
        self._fill_color_menu(menu, note)
        menu.tk_popup(event.x_root, event.y_root)

    def _note_set_color(self, note: Note, code: str) -> None:
        if note.color == code:
            return
        note.color = code
        self._note_changed(note)
        self._refresh_card(note)

    def _note_set_wide(self, note: Note, wide: bool) -> None:
        if (note.span > 1) == wide and not note.width:
            return
        note.span = 2 if wide else 1
        note.width = 0  # the switch puts the card back on the columns
        self._note_changed(note)
        self._place_note_cards()

    def _note_carry_over(self, note: Note) -> None:
        copy = self.vault.notes.carry_over(note)
        if copy is None:
            return
        self.app.mark_dirty("notes")
        self._refresh_ndays(select=today())
        self.app.set_status(_("Carried over to today: {n} item(s)").format(n=len(copy.items)))

    def _note_delete(self, note: Note) -> None:
        if not note.is_empty() and not ask_yes_no(self.app, _("Delete note “{title}”?").format(title=note.title or _("untitled"))):
            return
        self.vault.notes.remove(note)
        self.app.mark_dirty("notes")
        self._refresh_ndays()

    def new_note(self, checklist: bool = False) -> None:
        if self.vault is None:
            return
        date = self.nday if self.nday not in (None, UNFINISHED, WITH_FILES) else today()
        if self.nsearch_var.get():
            self.nsearch_var.set("")  # its trace draws the cards once; _refresh_ndays draws them again
        note = self.vault.notes.add(date)
        self.app.mark_dirty("notes")
        self._refresh_ndays(select=date)
        self.app.finish_chunks("notes")  # the new card must be there to type into
        card = self._ncards.get(note.id)
        if card is not None:
            (card.add_entry if checklist else card.title_entry).focus_set()  # type: ignore[attr-defined]
