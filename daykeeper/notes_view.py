"""The “Notes” tab: days and search on the left, cards of the day on the right.

A card has a title, free text, a checklist and attached files. Checklist items are added
with the “+” row at the bottom of the list (Enter adds and keeps typing); done items move
below under a “Done (n)” header that folds. The “⋯” menu holds the card settings: colour,
width, folding of done items, files, carrying over, deletion.

A card is one canvas (NoteCard): everything on it is drawn, and a field — the title, the
text, an item, the “+” row — becomes a real widget placed over its spot only while it is
being edited (a click, Enter or a typed key open it; Esc, Enter or a click elsewhere close
it). A card of frames, entries and text boxes was about twenty Tk windows — a tab of a busy
day took seconds to lay out and paint; a canvas is one window.

A page of the window (TabPage)."""

from __future__ import annotations

import base64
import time
import tkinter as tk
from tkinter import filedialog, ttk

from . import palette
from .i18n import _, fmt_day, hotkeys
from .layout import CARD_GAP, CARD_MIN_WIDTH, column_width, flow_layout
from .theme import blend, shape
from .popup import PopupMenu
from .journal import today
from .md_view import FileViewer, open_with_system, show_in_folder
from .notes import MAX_SIZE, NOTE_COLORS, Attachment, Note, NoteItem
from .dialogs import ask_yes_no, show_warning
from .sidebar import SidebarKeeper
from .tabpage import Shell, TabPage, make_scrollable
from .timers import IdleOnce
from .widgets import Placeholder, Tooltip, WrapLine, make_fonts

CARD_SMALLEST = 200  # the narrowest a card can be dragged
RESIZE_CURSORS = {"x": "sb_h_double_arrow", "y": "sb_v_double_arrow", "xy": "size_nw_se"}
UNFINISHED = "__unfinished__"  # a “day” on the left: everything unfinished across all days
WITH_FILES = "__files__"  # a “day” on the left: every card with attached files
FILE_ICONS = {".md": "📝", ".markdown": "📝", ".txt": "📄", ".log": "📄", ".pdf": "📕", ".png": "🖼", ".jpg": "🖼", ".jpeg": "🖼"}

RADIUS = 14  # px at 100 %: the card's corners
PAD = 14  # inside the card, left and right
PAD_Y = 10  # inside the card, top and bottom
CHECK = 18  # the check box
TEXT_X = 26  # from the left pad to an item's text: the check box and a gap
RM_W = 18  # the “✕” at the right edge of a row
ROW_PAD = 3  # above and below an item
PREVIEW_LINES = 8  # the text of a card shows this many lines; the editor scrolls the rest
PREVIEW_CHARS = 1200  # ... and this many characters at most
MAX_EDIT_LINES = 12  # the text editor grows to this many lines while typing


def editable_look(bg: str) -> dict:
    """The options that make a field placed over a card look like a field: a thin border
    in the card's colour that turns to the accent while it has the focus."""
    return {"highlightthickness": 1, "highlightbackground": bg, "highlightcolor": palette.ACCENT, "cursor": "xterm"}


def corner_images(root: tk.Misc, radius: int, fill: str, border: str, matte: str) -> tuple[tk.PhotoImage, ...]:
    """The four corners of a rounded box of this look, anti-aliased, opaque (drawn over
    `matte`); made once per look and kept on the window (images die with their Tk)."""
    cache: dict[tuple, tuple[tk.PhotoImage, ...]] = root.__dict__.setdefault("_note_corners", {})
    key = (radius, fill, border, matte)
    images = cache.get(key)
    if images is None:
        size = 2 * radius + 2
        whole = tk.PhotoImage(master=root, data=base64.b64encode(shape(size, size, radius, fill=fill, border=border, matte=matte)))
        out = []
        for x0, y0 in ((0, 0), (size - radius, 0), (0, size - radius), (size - radius, size - radius)):
            corner = tk.PhotoImage(master=root, width=radius, height=radius)
            corner.tk.call(corner, "copy", whole, "-from", x0, y0, x0 + radius, y0 + radius)
            out.append(corner)
        images = cache[key] = tuple(out)
    return images


class NoteCard(tk.Canvas):
    """One note as one canvas. `redraw()` draws it from the note; `zones` is what is where
    (for the pointer and the keyboard); `edit(kind, payload)` puts a real field over a spot."""

    HOVER_REDRAW = {"item", "check", "rm", "file", "frm", "more", "colour", "attach"}

    def __init__(self, page: NotesPage, master: tk.Misc, note: Note, width: int):
        super().__init__(master, bg=palette.CARD, highlightthickness=0, bd=0, width=width, height=10, takefocus=1)
        self.page, self.note = page, note
        self.body = self  # the resize helper binds on the card and its body
        self.app = page.app
        self.px = page.app.painter.px
        self.fonts = page._nfonts
        self._width, self._height = width, 0
        self.natural_h = 0  # the height the content asks for
        self.min_h = 0  # the content without the text, plus two lines of it
        self.zones: list[tuple[int, int, int, int, str, object]] = []  # x0, y0, x1, y1, kind, payload
        self.hot: tuple[str, object] | None = None  # the zone under the pointer
        self.sel = -1  # the field picked from the keyboard
        self.editor: tk.Widget | None = None
        self.editing: tuple[str, object] | None = None
        self.add_var = tk.StringVar()  # what is typed in the “+” row
        self.tip = Tooltip(self, "", attach=False)
        self._text_box = (0, 0, 0, 0)  # where the text sits (x, y, w, h)
        self._boxes: dict[object, tuple[int, int, int, int]] = {}  # "add" / id(item) → x, y, w, h
        self.redraw()
        self.bind("<Configure>", self._on_configure)
        self.bind("<Button-1>", self._click)
        self.bind("<Button-3>", self._right_click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda _e: self._set_hot(None))
        self.bind("<ButtonPress>", lambda _e: self.tip.hide(), add="+")
        # Tk sends a FocusIn to the card for an editor placed on it too: only the card’s own focus picks a field
        self.bind("<FocusIn>", lambda _e: self._own_focus() and self._select(0 if self.sel < 0 else self.sel))
        self.bind("<FocusOut>", lambda _e: self._own_focus() or self._select(-1))
        self.bind("<Up>", lambda _e: (self._select(self.sel - 1), "break")[1])
        self.bind("<Down>", lambda _e: (self._select(self.sel + 1), "break")[1])
        for key in ("<Return>", "<KP_Enter>", "<space>"):
            self.bind(key, lambda _e: (self._edit_selected(), "break")[1])
        self.bind("<Key>", self._typed)

    # ---- drawing

    def _on_configure(self, event: tk.Event) -> None:
        if (event.width, event.height) != (self._width, self._height):
            self._width, self._height = event.width, event.height
            self.redraw()

    def tips(self, kind: str, payload: object) -> str:
        if kind == "pin":
            return _("Unpin") if self.note.pinned else _("Pin to the top of the day")
        if kind == "file":
            att = payload
            if not isinstance(att, Attachment):
                return ""
            return att.path if att.exists() else _("File not found: {path}").format(path=att.path)
        return {
            "plus": _("Add a checklist item (Enter adds and keeps typing)"),
            "clear": _("Remove the done items"),
            "carry": _("Undone items as a new card for today"),
            "more": _("Card settings"),
            "colour": _("Card colour"),
            "attach": _("Attach files"),
            "frm": _("Detach the file (the file itself stays on disk)"),
        }.get(kind, "")

    def redraw(self) -> None:
        """Everything from the note. The editor, if open, is moved to its spot."""
        note, px, f = self.note, self.px, self.fonts
        self.delete("all")
        self.zones = []
        self._boxes = {}
        w, pad, pad_y = self._width, px(PAD), px(PAD_Y)
        bg, accent = note.bg(), note.accent()
        y = pad_y

        # title and pin
        title = note.title
        self.create_text(pad, y, text=title or _("Title"), font=f["title"], fill=palette.TEXT if title else palette.MUTED, anchor="nw", width=w - 2 * pad - px(34), tags="title")
        tb = self.bbox("title")
        self.create_text(w - pad, y, text="📌" if note.pinned else "📍", font=f["icon"], fill=palette.TEXT if note.pinned else palette.MUTED, anchor="ne", tags="pin")
        pb = self.bbox("pin")
        self._zone(pad - px(2), y - px(2), pb[0] - px(4), max(tb[3], pb[3]) + px(2), "title", None)
        self._zone(pb[0] - px(4), pb[1] - px(2), w, pb[3] + px(2), "pin", None)
        self._boxes["title"] = (pad - px(2), tb[1] - px(2), pb[0] - px(6) - pad + px(2), tb[3] - tb[1] + px(4))
        y = max(tb[3], pb[3]) + px(6)

        # text (a preview of the first lines; the editor holds all of it)
        text = note.text
        lines = text.split("\n")
        preview = "\n".join(lines[:PREVIEW_LINES])
        cut = len(lines) > PREVIEW_LINES or len(preview) > PREVIEW_CHARS
        preview = preview[:PREVIEW_CHARS]
        if cut:
            preview = preview.rstrip() + " …"
        line = f["text"].metrics("linespace")
        self.create_text(pad + px(2), y + px(4), text=preview or _("Note text…"), font=f["text"], fill=palette.TEXT if text else palette.MUTED, anchor="nw", width=w - 2 * pad - px(4), tags="text")
        natural_text_h = max(2 * line + px(8), self.bbox("text")[3] - y + px(4))
        text_h = natural_text_h
        if self.editing == ("text", None) and self.editor is not None:
            text_h = max(text_h, self.editor.winfo_reqheight() + px(2))
        y_text = y
        y += text_h

        # the checklist: progress, open items, the “+” row, done items
        done_items, open_items = note.done_items(), note.open_items()
        total = len(note.items)
        y += px(14)
        if total:
            n_done = len(done_items)
            label = _("✓ {done} / {total}").format(done=n_done, total=total) if n_done == total else f"{n_done} / {total}"
            colour = palette.SUCCESS if n_done == total else palette.MUTED
            cid = self.create_text(w - pad, y, text=label, font=f["small_bold"], fill=colour, anchor="ne")
            cb = self.bbox(cid)
            mid = (cb[1] + cb[3]) // 2
            span = max(0, cb[0] - px(8) - pad)
            self.create_rectangle(pad, mid - 2, pad + span, mid + 2, fill=palette.BORDER, outline="")
            filled = int(span * n_done / total)
            if filled:
                self.create_rectangle(pad, mid - 2, pad + filled, mid + 2, fill=colour if n_done == total else accent, outline="")
            y = cb[3] + px(6)
        y = self._draw_items(open_items, y, done=False)
        # the “+” row
        plus_y = y + px(8)
        self.create_text(pad + px(1), plus_y - px(3), text="+", font=f["plus"], fill=accent, anchor="nw", tags="plus")
        typed = self.add_var.get()
        self.create_text(pad + px(TEXT_X), plus_y, text=typed or _("Add item"), font=f["item"], fill=palette.TEXT if typed else palette.MUTED, anchor="nw", width=w - 2 * pad - px(TEXT_X), tags="add")
        ab = self.bbox("add")
        row_h = max(ab[3] - plus_y, f["item"].metrics("linespace"))
        self._zone(pad, plus_y - px(2), pad + px(TEXT_X) - px(4), plus_y + row_h + px(2), "plus", None)
        self._zone(pad + px(TEXT_X) - px(4), plus_y - px(2), w - pad, plus_y + row_h + px(2), "add", None)
        self._boxes["add"] = (pad + px(TEXT_X) - px(3), plus_y - px(2), w - 2 * pad - px(TEXT_X) + px(3), row_h + px(4))
        y = plus_y + row_h + px(4)
        if done_items:
            y += px(12)
            arrow = "▸" if note.hide_done else "▾"
            self.create_text(pad, y, text=f"{arrow} " + _("Done ({n})").format(n=len(done_items)), font=f["small_bold"], fill=palette.MUTED, anchor="nw", tags="fold")
            self.create_text(w - pad, y, text=_("Clear"), font=f["small"], fill=palette.MUTED, anchor="ne", tags="clear")
            fb, clb = self.bbox("fold"), self.bbox("clear")
            self._zone(pad - px(2), fb[1] - px(2), fb[2] + px(4), fb[3] + px(2), "fold", None)
            self._zone(clb[0] - px(4), clb[1] - px(2), w, clb[3] + px(2), "clear", None)
            y = max(fb[3], clb[3]) + px(4)
            if not note.hide_done:
                y = self._draw_items(done_items, y, done=True)

        # files
        if note.attachments:
            y += px(12)
            for att in note.attachments:
                exists = att.exists()
                icon = FILE_ICONS.get(att.suffix, "📄") if exists else "⚠"
                box_top = y
                fid = self.create_text(pad + px(8), y + px(4), text=f"{icon}  {att.name}", font=f["item"], fill=palette.TEXT if exists else palette.MUTED, anchor="nw", width=w - 2 * pad - px(40))
                fb = self.bbox(fid)
                row_h = fb[3] - y + px(8)
                hot = self.hot is not None and self.hot[0] in ("file", "frm") and self.hot[1] is att
                box = self.create_rectangle(pad, box_top, w - pad, box_top + row_h, outline=accent if hot else palette.BORDER, fill="")
                self.tag_lower(box, fid)
                self.create_text(w - pad - px(8), y + px(5), text="✕", font=f["small"], fill=palette.MUTED, anchor="ne")
                self._zone(w - pad - px(RM_W) - px(4), box_top, w - pad, box_top + row_h, "frm", att)
                self._zone(pad, box_top, w - pad - px(RM_W) - px(4), box_top + row_h, "file", att)
                y = box_top + row_h + px(4)

        # footer
        y += px(14)
        stamp = time.strftime("%H:%M", time.localtime(note.updated))
        self.create_text(pad, y, text=stamp, font=f["small"], fill=palette.MUTED, anchor="nw", tags="stamp")
        x = self.bbox("stamp")[2] + px(10)
        if self.page._across_days():
            self.create_text(x, y, text=fmt_day(note.date), font=f["small"], fill=palette.MUTED, anchor="nw", tags="day")
            x = self.bbox("day")[2] + px(10)
        if note.undone_items() and note.date != today():
            self.create_text(x, y, text=_("⤴ to today"), font=f["small_bold"], fill=accent, anchor="nw", tags="carry")
            cb = self.bbox("carry")
            self._zone(cb[0] - px(2), cb[1] - px(2), cb[2] + px(2), cb[3] + px(2), "carry", None)
        right = w - pad
        foot_bottom = y + f["small"].metrics("linespace")
        for kind, glyph, gap in (("more", "⋯", 0), ("colour", "🎨", 9), ("attach", "📎", 9)):
            right -= px(gap)
            hot = self.hot is not None and self.hot[0] == kind
            self.create_text(right, y - px(3), text=glyph, font=f["icon"], fill=palette.TEXT if hot else palette.MUTED, anchor="ne", tags=kind)
            gb = self.bbox(kind)
            self._zone(gb[0] - px(3), gb[1] - px(2), gb[2] + px(3), gb[3] + px(2), kind, None)
            right = gb[0]
            foot_bottom = max(foot_bottom, gb[3])
        y = foot_bottom + pad_y

        # the heights the page lays the card out by, then the background under everything
        self.natural_h = y
        self.min_h = y - natural_text_h + 2 * line + px(8)
        stretch = self._height - y if note.height and self._height > y else 0
        self._text_box = (pad, y_text, w - 2 * pad, text_h + stretch)
        if stretch:  # dragged taller: the room goes to the text, the rest moves down
            for tag in ("plus", "add", "fold", "clear", "stamp", "day", "carry", "more", "colour", "attach"):
                self.move(tag, 0, stretch)
            self._stretch_rest(y_text + text_h, stretch)
        self._draw_background(w, y + stretch, bg, palette.BORDER if not note.color else blend(accent, bg, 0.35))
        self.configure(height=y)
        if self.sel >= 0:
            self._draw_selection()
        if self.editor is not None:
            self._place_editor()

    def _stretch_rest(self, below: int, by: int) -> None:
        """A dragged-taller card: everything drawn under the text moves down by `by`."""
        for item in self.find_all():
            if "bg" in self.gettags(item):
                continue
            box = self.bbox(item)
            if box is not None and box[1] >= below and item not in self.find_withtag("moved"):
                tags = self.gettags(item)
                if not any(t in ("plus", "add", "fold", "clear", "stamp", "day", "carry", "more", "colour", "attach") for t in tags):
                    self.move(item, 0, by)
        self.zones = [(x0, y0 + by, x1, y1 + by, k, p) if y0 >= below else (x0, y0, x1, y1, k, p) for x0, y0, x1, y1, k, p in self.zones]
        self._boxes = {k: (x, y + by, w, h) if y >= below else (x, y, w, h) for k, (x, y, w, h) in self._boxes.items()}

    def _draw_items(self, items: list[NoteItem], y: int, done: bool) -> int:
        """The rows of open or done items from `y`; the y under the last one."""
        px, f, w, pad = self.px, self.fonts, self._width, self.px(PAD)
        font, fill = (f["item_done"], palette.MUTED) if done else (f["item"], palette.TEXT)
        size, row_pad, x_text = px(CHECK), px(ROW_PAD), pad + px(TEXT_X)
        wrap = w - 2 * pad - px(TEXT_X) - px(RM_W)
        for item in items:
            y += row_pad
            tid = self.create_text(x_text, y, text=item.text, anchor="nw", width=wrap, font=font, fill=fill, tags=f"item{id(item)}")
            h = max(self.bbox(tid)[3] - y, size)
            hot = self.hot is not None and self.hot[0] in ("item", "check", "rm") and self.hot[1] is item
            self._draw_check(pad, y + px(2), size, done, hot and not done)
            self.create_text(w - pad, y + px(2), text="✕", anchor="ne", font=f["small"], fill=palette.MUTED if hot else self.note.bg())
            self._zone(pad - px(2), y - row_pad, x_text - px(4), y + h + row_pad, "check", item)
            self._zone(x_text - px(4), y - row_pad, w - pad - px(RM_W), y + h + row_pad, "item", item)
            self._zone(w - pad - px(RM_W), y - row_pad, w - pad + px(2), y + h + row_pad, "rm", item)
            self._boxes[id(item)] = (x_text - px(3), y - px(2), wrap + px(3) + px(RM_W) - px(4), h + px(4))
            y += h + row_pad
        return y

    def _draw_check(self, x0: int, y0: int, size: int, done: bool, hover: bool) -> None:
        s, r = size - 2, 5
        x0, y0 = x0 + 1, y0 + 1
        pts = [x0 + r, y0, x0 + s - r, y0, x0 + s, y0, x0 + s, y0 + r, x0 + s, y0 + s - r, x0 + s, y0 + s, x0 + s - r, y0 + s, x0 + r, y0 + s, x0, y0 + s, x0, y0 + s - r, x0, y0 + r, x0, y0]
        accent = self.note.accent()
        if done:
            self.create_polygon(pts, smooth=True, fill=accent, outline=accent)
            self.create_line(x0 + 4, y0 + 8.5, x0 + 7, y0 + 11.5, x0 + 12.5, y0 + 5, fill=palette.ON_ACCENT, width=2.2, capstyle="round", joinstyle="round")
        else:
            self.create_polygon(pts, smooth=True, fill=palette.FIELD, outline=accent if hover else palette.MUTED, width=1.6)

    def _draw_background(self, w: int, h: int, bg: str, outline: str) -> None:
        r = self.px(RADIUS)
        tl, tr, bl, br = corner_images(self.app, r, bg, outline, palette.CARD)
        self.create_rectangle(r, 0, w - r, h, fill=bg, outline="", tags="bg")
        self.create_rectangle(0, r, w, h - r, fill=bg, outline="", tags="bg")
        for img, x, y in ((tl, 0, 0), (tr, w - r, 0), (bl, 0, h - r), (br, w - r, h - r)):
            self.create_image(x, y, image=img, anchor="nw", tags="bg")
        for x0, y0, x1, y1 in ((r, 0, w - r, 0), (r, h - 1, w - r, h - 1), (0, r, 0, h - r), (w - 1, r, w - 1, h - r)):
            self.create_line(x0, y0, x1, y1, fill=outline, tags="bg")
        self.tag_lower("bg")

    def _zone(self, x0: int, y0: int, x1: int, y1: int, kind: str, payload: object) -> None:
        self.zones.append((x0, y0, x1, y1, kind, payload))

    def zone_at(self, x: int, y: int) -> tuple[str, object] | None:
        for x0, y0, x1, y1, kind, payload in self.zones:
            if x0 <= x < x1 and y0 <= y < y1:
                return kind, payload
        if self._text_box[1] <= y < self._text_box[1] + self._text_box[3]:
            return "text", None
        return None

    # ---- the pointer

    def _set_hot(self, zone: tuple[str, object] | None) -> None:
        if zone == self.hot:
            return
        old, self.hot = self.hot, zone
        # the rows and glyphs change their look under the pointer: drawn again (canvas items, cheap)
        if any(z is not None and z[0] in self.HOVER_REDRAW for z in (old, zone)):
            self.redraw()
        self.tip.hide()
        if zone is None:
            self.configure(cursor="")
            return
        kind, payload = zone
        self.configure(cursor="xterm" if kind in ("title", "text", "item", "add") else "hand2")
        text = self.tips(kind, payload)
        if text:
            self.tip.text = text
            self.tip.anchor = lambda: (self.winfo_pointerx(), self.winfo_pointery() + 18)
            self.tip.schedule()

    def _motion(self, event: tk.Event) -> None:
        self._set_hot(self.zone_at(event.x, event.y))

    def _click(self, event: tk.Event) -> str | None:
        zone = self.zone_at(event.x, event.y)
        if zone is None:
            return None
        kind, payload = zone
        page, note = self.page, self.note
        if kind in ("title", "text", "add"):
            self.edit(kind, None, at=(event.x, event.y))
        elif kind == "item":
            self.edit("item", payload, at=(event.x, event.y))
        elif kind == "pin":
            page._note_pin(note)
        elif kind == "check" and isinstance(payload, NoteItem):
            page._note_toggle(self, note, payload)
        elif kind == "rm" and isinstance(payload, NoteItem):
            page._note_remove_item(self, note, payload)
        elif kind == "plus":
            if self.add_var.get().strip():
                page._note_add_item(self, note, self.add_var)
            else:
                self.edit("add")
        elif kind == "fold":
            page._note_fold_done(self, note)
        elif kind == "clear":
            page._note_clear_done(self, note)
        elif kind == "file" and isinstance(payload, Attachment):
            page._open_attachment(payload)
        elif kind == "frm" and isinstance(payload, Attachment):
            page._note_detach(note, payload)
        elif kind == "carry":
            page._note_carry_over(note)
        elif kind == "more":
            page._note_menu(note, event)
        elif kind == "colour":
            page._note_color_menu(note, event)
        elif kind == "attach":
            page._note_attach(note)
        return "break"

    def _right_click(self, event: tk.Event) -> None:
        zone = self.zone_at(event.x, event.y)
        kind, payload = zone if zone is not None else ("", None)
        if kind in ("item", "check", "rm") and isinstance(payload, NoteItem):
            self.page._note_item_menu(self, self.note, payload, event)
        elif kind in ("file", "frm") and isinstance(payload, Attachment):
            self.page._file_menu(self.note, payload, event)
        else:
            self.page._note_menu(self.note, event)

    # ---- the keyboard: Tab reaches the card, ↑/↓ pick a field, Enter or a key opens it

    def spots(self) -> list[tuple[str, object]]:
        """The fields in reading order: the title, the text, the items, the “+” row."""
        out: list[tuple[str, object]] = [("title", None), ("text", None)]
        out += [("item", i) for i in self.note.open_items()]
        out.append(("add", None))
        if not self.note.hide_done:
            out += [("item", i) for i in self.note.done_items()]
        return out

    def _own_focus(self) -> bool:
        try:
            return self.focus_get() is self
        except (KeyError, tk.TclError):
            return False

    def _select(self, i: int) -> None:
        spots = self.spots()
        i = -1 if i < 0 else min(i, len(spots) - 1)
        if i == self.sel:
            return
        self.sel = i
        self.delete("sel")
        if i >= 0:
            self._draw_selection()

    def _draw_selection(self) -> None:
        spots = self.spots()
        if not 0 <= self.sel < len(spots):
            return
        box = self._box_of(*spots[self.sel])
        if box is not None:
            x, y, w, h = box
            self.create_rectangle(x - 1, y - 1, x + w + 1, y + h + 1, outline=palette.ACCENT, tags="sel")

    def _edit_selected(self) -> None:
        spots = self.spots()
        if 0 <= self.sel < len(spots):
            self.edit(*spots[self.sel])

    def _typed(self, event: tk.Event) -> str | None:
        """A printable key on the picked field opens it with that key typed."""
        if self.sel < 0 or not event.char or not event.char.isprintable() or event.state & 0x4:
            return None
        self._edit_selected()
        editor = self.editor
        if isinstance(editor, (tk.Entry, tk.Text)):
            editor.insert("insert", event.char)
        return "break"

    # ---- the editors: a real field over the spot, one at a time

    def _box_of(self, kind: str, payload: object) -> tuple[int, int, int, int] | None:
        if kind == "text":
            return self._text_box
        if kind == "item":
            return self._boxes.get(id(payload))
        return self._boxes.get(kind)

    def edit(self, kind: str, payload: object = None, at: tuple[int, int] | None = None) -> None:
        """Open the editor of a field; `at` — the click, where the cursor goes (the end without it)."""
        if self.editor is not None:
            if self.editing == (kind, payload):
                self.editor.focus_set()
                return
            self.close()
        box = self._box_of(kind, payload)
        if box is None:
            return
        page, note, bg, f = self.page, self.note, self.note.bg(), self.fonts
        self.editing = (kind, payload)
        editor: tk.Entry | tk.Text
        if kind == "title":
            var = tk.StringVar(value=note.title)
            editor = tk.Entry(self._editor_master(), textvariable=var, font=f["title"], bg=bg, fg=palette.TEXT, relief="flat", bd=0, insertbackground=palette.TEXT, **editable_look(bg))
            var.trace_add("write", lambda *_: page._note_edit(note, "title", var.get()))
            editor.var = var  # type: ignore[attr-defined]  # alive while the editor is
            editor.bind("<Return>", lambda _e: (self.edit("text"), "break")[1])
        elif kind == "text":
            editor = tk.Text(self._editor_master(), font=f["text"], bg=bg, fg=palette.TEXT, relief="flat", bd=0, wrap="word", undo=True, insertbackground=palette.TEXT, padx=2, pady=4, height=max(2, min(MAX_EDIT_LINES, note.text.count("\n") + 2)), **editable_look(bg))
            editor.insert("1.0", note.text)
            editor.edit_reset()
            editor.edit_modified(False)
            editor.bind("<<Modified>>", lambda _e, w=editor: page._note_text_modified(note, w))
            editor.bind("<KeyRelease>", lambda _e, w=editor: self._text_grew(w))
        elif kind == "add":
            editor = tk.Entry(self._editor_master(), textvariable=self.add_var, font=f["item"], bg=bg, fg=palette.TEXT, relief="flat", bd=0, insertbackground=palette.TEXT, **editable_look(bg))
            editor.bind("<Return>", lambda _e: page._note_add_item(self, note, self.add_var))
            editor._commit_typed = lambda: page._commit_left_item(self, note, editor, self.add_var, clicked_away=True)  # type: ignore[attr-defined]
            page._watch_clicks_away()
        else:  # an item
            item = payload
            if not isinstance(item, NoteItem):
                self.editing = None
                return
            editor = WrapLine(self._editor_master(), item.text, lambda value: page._note_item_edit(note, item, value), font=f["item_done" if item.done else "item"], bg=bg, fg=palette.MUTED if item.done else palette.TEXT, relief="flat", bd=0, insertbackground=palette.TEXT, **editable_look(bg))
            editor.bind("<Return>", lambda _e: (self.edit("add"), "break")[1])
            editor.bind("<Alt-Up>", lambda _e: page._note_move_item(self, note, item, -1))
            editor.bind("<Alt-Down>", lambda _e: page._note_move_item(self, note, item, +1))
            editor.bind("<BackSpace>", lambda _e, w=editor: page._note_backspace_item(self, note, item, w))
        self.editor = editor
        editor.bind("<Tab>", lambda _e: (self._neighbour(+1), "break")[1])
        editor.bind("<Shift-Tab>", lambda _e: (self._neighbour(-1), "break")[1])
        editor.bind("<Escape>", lambda _e: (self.close(), self.focus_set(), "break")[2])
        editor.bind("<FocusOut>", lambda _e: editor.after_idle(self._left), add="+")
        editor.bind("<Enter>", lambda _e: editor.config(highlightbackground=palette.BORDER), add="+")
        editor.bind("<Leave>", lambda _e: editor.config(highlightbackground=bg), add="+")
        editor.bind("<Button-3>", self._right_click_editor)
        self.itemconfigure(kind if kind in ("title", "text", "add") else f"item{id(payload)}", state="hidden")
        self._place_editor()
        editor.focus_set()
        if at is not None and isinstance(editor, tk.Text):
            editor.update_idletasks()  # laid out first: the index under the click needs the lines
            editor.mark_set("insert", editor.index(f"@{at[0] - box[0]},{at[1] - box[1]}"))
        elif at is not None and isinstance(editor, tk.Entry):
            editor.update_idletasks()
            editor.icursor(editor.index(f"@{at[0] - box[0]}"))
        elif isinstance(editor, tk.Text):
            editor.mark_set("insert", "end-1c")
        else:
            editor.icursor("end")

    def _right_click_editor(self, event: tk.Event) -> None:
        kind, payload = self.editing if self.editing else ("", None)
        if kind == "item" and isinstance(payload, NoteItem):
            self.page._note_item_menu(self, self.note, payload, event)
        else:
            self.page._note_menu(self.note, event)

    def _editor_master(self) -> tk.Misc:
        """The editor belongs to the frame around the scroll canvas and is only placed over
        the card (`in_`). Made a child of the card — or of anything inside the canvas's
        window item — every key cost Tk ten times more (7 ms instead of 0.7 on a 10 KB
        paragraph, 19 instead of 2 on a 30 KB one): a change inside a canvas window item
        with laid-out siblings makes the canvas recompute the item on each key."""
        return self.page.ncanvas.master

    def _place_editor(self) -> None:
        if self.editor is None or self.editing is None:
            return
        box = self._box_of(*self.editing)
        if box is None:
            return
        x, y, w, h = box
        if isinstance(self.editor, tk.Text):
            self.editor.place(in_=self, x=x, y=y, width=w, height=h)
        else:
            self.editor.place(in_=self, x=x, y=y, width=w)
        self.editor.lift()

    def destroy(self) -> None:
        """The editor is not a child of the card (see `_editor_master`): it goes with the card by hand."""
        editor, self.editor, self.editing = self.editor, None, None
        if editor is not None and editor.winfo_exists():
            editor.destroy()
        super().destroy()

    def _text_grew(self, editor: tk.Text) -> None:
        """Typing in the text: the editor grows with its lines (to a limit) and the rest of
        the card moves down — the height is set only when it really changes, Tk lays the
        whole text out again on any configure."""
        if self.note.height:
            return
        lines = int(editor.index("end-1c").split(".")[0])
        height = max(2, min(MAX_EDIT_LINES, lines + 1))
        if int(editor.cget("height")) != height:
            editor.config(height=height)
            self.redraw()

    def _neighbour(self, step: int) -> None:
        """Tab / Shift+Tab: the next or the previous field of the card; past the last one
        — the next card."""
        spots = self.spots()
        try:
            i = spots.index(self.editing) + step  # type: ignore[arg-type]
        except ValueError:
            i = 0
        self.close()
        if 0 <= i < len(spots):
            self.edit(*spots[i])
        else:
            self.page.focus_card_after(self, step)

    def _left(self) -> None:
        """The editor lost the focus (a click elsewhere, another window): back to the drawing.
        Not while the focus is still here (the app only lost the foreground)."""
        editor = self.editor
        if editor is None or not editor.winfo_exists():
            return
        try:
            focus = editor.focus_get()
        except (KeyError, tk.TclError):
            focus = None
        if focus is not editor:
            self.close()

    def close(self) -> None:
        """The editor goes; the card shows the field as it is now."""
        editor, self.editor, self.editing = self.editor, None, None
        if editor is None:
            return
        if editor.winfo_exists():
            editor.destroy()
        if self.winfo_exists():
            self.redraw()

    @property
    def text_widget(self) -> tk.Text | None:
        """The text editor while it is open."""
        return self.editor if isinstance(self.editor, tk.Text) else None


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
        search = ttk.Entry(left, textvariable=self.nsearch_var, font=(palette.FONT, palette.pt(11)))
        search.pack(fill="x", pady=(0, 10))
        self._nsearch_entry = search
        Placeholder(search, _("🔍 Search notes"), palette.FIELD, var=self.nsearch_var, padx=8)
        Tooltip(search, _("Search across all days: title, text, items, file names"))
        ttk.Label(left, text=_("DAYS"), style="SidebarMuted.TLabel", font=(palette.FONT, palette.pt(10), "bold")).pack(anchor="w", pady=(0, 6))
        self.ndays_tree = ttk.Treeview(left, columns=("counts",), show="tree", selectmode="browse")
        self.ndays_tree.column("#0", width=170, stretch=True)
        self.ndays_tree.column("counts", width=70, anchor="e", stretch=False)
        self.ndays_tree.pack(fill="both", expand=True)
        self.ndays_tree.bind("<<TreeviewSelect>>", self._on_nday_select)
        self.ndays_tree.bind("<Button-1>", self._on_nday_click, add="+")
        self.ndays_tree.tag_configure("today", font=(palette.FONT, palette.pt(11), "bold"))
        Tooltip(self.ndays_tree, _("done / total items"))
        btns = ttk.Frame(left, style="Sidebar.TFrame")
        btns.pack(side="bottom", fill="x", pady=(12, 0), before=self.ndays_tree)  # packed first from the bottom: a short window shrinks the list, not the buttons
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
        self.ntitle = ttk.Label(self.ngrid, text="", style="Card.TLabel", font=(palette.FONT, palette.pt(17), "bold"))
        self.ntitle.grid(row=0, column=0, columnspan=99, sticky="w", pady=(0, 12))
        self.nempty = ttk.Label(self.ngrid, text="", style="CardMuted.TLabel", font=(palette.FONT, palette.pt(12)))
        self._ncards: dict[str, NoteCard] = {}
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
        """Draw one card again (colour, files) and move the ones below it."""
        card = self._ncards.get(note.id)
        if card is None:
            return
        card.redraw()
        self._place_note_cards()

    def _avail_width(self) -> int:
        """The width the cards share."""
        avail = self.nflow.winfo_width()
        if avail <= 1:
            avail = max(CARD_MIN_WIDTH, self.ncanvas.winfo_width() - 48)
        return avail

    def _card_width(self, note: Note, avail: int) -> int:
        """The width of a card in screen pixels: as dragged, else by the columns. Never
        narrower than CARD_SMALLEST, never wider than the space."""
        px = self.app.painter.px
        cols, col = column_width(avail, px(CARD_GAP), px(CARD_MIN_WIDTH))
        if note.width:
            w = px(note.width)
        else:
            span = min(note.span, cols)
            w = round(col * span + px(CARD_GAP) * (span - 1))
        return max(min(px(CARD_SMALLEST), avail), min(w, avail))

    def _card_size(self, note: Note, card: NoteCard, avail: int) -> tuple[int, int]:
        """The width and height of a card in screen pixels: as dragged, else by the columns
        and the content."""
        w = self._card_width(note, avail)
        if w != card._width:  # a new width wraps the text anew: the natural height follows
            card._width = w
            card.redraw()
        h = max(self.app.painter.px(note.height), self._card_min_height(card)) if note.height else card.natural_h
        return w, h

    def _card_min_height(self, card: NoteCard) -> int:
        """The content of a card without its text: the text shrinks to two lines, no lower."""
        return card.min_h

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
        avail = self._avail_width()
        cards = [(n, self._ncards.get(n.id)) for n in shown]
        cards = [(n, c) for n, c in cards if c is not None]
        sizes = [self._card_size(n, c, avail) for n, c in cards]
        spots, total = flow_layout(sizes, avail, self.app.painter.px(CARD_GAP))
        for (_n, card), (w, h), (x, y) in zip(cards, sizes, spots, strict=True):
            card.place(x=x, y=y, width=w, height=h)
        if int(self.nflow.cget("height")) != total + CARD_GAP:
            self.nflow.configure(height=total + CARD_GAP)

    def focus_card_after(self, card: NoteCard, step: int) -> None:
        """Tab past the last field of a card: the first field of the next card (Shift+Tab —
        the “+” row of the previous one)."""
        order = [self._ncards[n.id] for n in self._nshown if n.id in self._ncards]
        try:
            i = order.index(card) + step
        except ValueError:
            return
        if 0 <= i < len(order):
            order[i].edit("title" if step > 0 else "add")
        else:
            card.focus_set()

    # ---- one card

    def _build_note_card(self, note: Note) -> NoteCard:
        card = NoteCard(self, self.nflow, note, self._card_width(note, self._avail_width()))
        card.bind("<Configure>", self._relayout_soon, add="+")
        self._resizable(card, note)
        return card

    def _resizable(self, card: NoteCard, note: Note) -> None:
        """Resize a card as a window, pixel by pixel: the right edge for the width, the
        bottom edge for the height, the bottom-right corner for both. A double click on an
        edge gives that size back to the columns or the content."""
        edge = self.app.painter.px(10)
        scale = self.app.painter.scale
        drag: dict = {}
        hover = {"zone": ""}

        def zone_at(e) -> str:
            x, y = e.x_root - card.winfo_rootx(), e.y_root - card.winfo_rooty()
            w, h = card.winfo_width(), card.winfo_height()
            right = w - edge <= x <= w + 2
            bottom = h - edge <= y <= h + 2
            return "xy" if right and bottom else "x" if right else "y" if bottom else ""

        def show(zone: str) -> None:
            if zone != hover["zone"]:
                hover["zone"] = zone
                if zone:
                    card.configure(cursor=RESIZE_CURSORS[zone])
                elif not card.hot:
                    card.configure(cursor="")

        def motion(e):
            if drag:
                return drag_to(e)
            zone = zone_at(e)
            show(zone)
            return "break" if zone else None  # on an edge the card's own hover stays quiet

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
                # Tk gives the second of two quick clicks to this binding instead of
                # <Button-1>: off an edge it is a click like any other
                return card._click(e)
            if "x" in zone:
                note.width = 0
            if "y" in zone:
                note.height = 0
            self._note_changed(note)
            self._place_note_cards()
            return "break"

        # bound before the card's own handlers (bind puts a new binding first when the old
        # ones were added with "+"): on an edge the press is the drag's, not a click
        for seq, fn in (("<Motion>", motion), ("<Leave>", leave), ("<ButtonPress-1>", press), ("<B1-Motion>", motion), ("<ButtonRelease-1>", release), ("<Double-Button-1>", double)):
            card.bind(seq, fn, add="+")

    # ---- checklist

    def _render_checklist(self, card: NoteCard, note: Note) -> None:
        """Draw the card again after a change to its items and move the ones below."""
        card.redraw()
        self._place_note_cards()

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

    def _note_add_item(self, card: NoteCard, note: Note, var: tk.StringVar, keep_focus: bool = True) -> str:
        item = note.add_item(var.get())
        if item is None:
            return "break"
        var.set("")
        self._render_checklist(card, note)
        self._note_changed(note, refresh_days=True)
        if keep_focus:
            card.edit("add")
        return "break"

    def _commit_left_item(self, card: NoteCard, note: Note, entry: tk.Entry, var: tk.StringVar, clicked_away: bool = False) -> None:
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

    def _note_toggle(self, card: NoteCard, note: Note, item: NoteItem) -> None:
        note.toggle(item)
        if card.editing == ("item", item):
            card.close()
        self._render_checklist(card, note)
        self._note_changed(note, refresh_days=True)

    def _note_remove_item(self, card: NoteCard, note: Note, item: NoteItem) -> None:
        note.remove_item(item)
        if card.editing == ("item", item):
            card.close()
        self._render_checklist(card, note)
        self._note_changed(note, refresh_days=True)

    def _note_backspace_item(self, card: NoteCard, note: Note, item: NoteItem, field: WrapLine) -> str | None:
        """Backspace in an empty item removes it and puts the cursor at the end of the one above."""
        if field.value():
            return None
        same = note.done_items() if item.done else note.open_items()
        idx = same.index(item) if item in same else 0
        card.close()  # its <FocusOut> must not run on an item that is gone
        self._note_remove_item(card, note, item)
        same = note.done_items() if item.done else note.open_items()
        if idx > 0 and same:
            card.edit("item", same[idx - 1])
        else:
            card.edit("add")
        return "break"

    def _note_move_item(self, card: NoteCard, note: Note, item: NoteItem, delta: int) -> str:
        if note.move_item(item, delta):
            card.close()
            self._render_checklist(card, note)
            self._note_changed(note)
            card.edit("item", item)
        return "break"

    def _note_item_menu(self, card: NoteCard, note: Note, item: NoteItem, event: tk.Event) -> None:
        menu = PopupMenu(self.app, tearoff=False)
        menu.add_command(label=_("Mark as open") if item.done else _("Mark as done"), command=lambda: self._note_toggle(card, note, item))
        menu.add_command(label=_("Move up") + "\tAlt+↑", command=lambda: self._note_move_item(card, note, item, -1))
        menu.add_command(label=_("Move down") + "\tAlt+↓", command=lambda: self._note_move_item(card, note, item, +1))
        menu.add_separator()
        menu.add_command(label=_("Delete item"), command=lambda: self._note_remove_item(card, note, item))
        menu.tk_popup(event.x_root, event.y_root)

    def _note_fold_done(self, card: NoteCard, note: Note) -> None:
        note.hide_done = not note.hide_done
        self._render_checklist(card, note)
        self._note_changed(note)

    def _note_clear_done(self, card: NoteCard, note: Note) -> None:
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
        card = self._ncards.get(note.id)
        if card is not None:
            card.close()  # the editor carries the old colour
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
            self._place_note_cards()
            card.edit("add" if checklist else "title")
