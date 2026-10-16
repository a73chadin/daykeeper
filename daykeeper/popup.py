"""Menus and drop-down lists in the app's look.

Windows draws tk.Menu and the list of a ttk.Combobox itself, in the old grey style, and
takes neither colours nor rounding. PopupMenu covers the part of the tk.Menu interface the
app uses (commands, check and radio items, submenus, separators, shortcuts on the right)
and draws a white card with rounded corners; Dropdown is a read-only combobox made of a
field-like button and such a menu.

One menu is open at a time. It holds the grab while open, so a click anywhere else in the
app closes it, and the keyboard works as in a system menu (arrows, Enter, Esc).
"""

from __future__ import annotations

import re
import time
import tkinter as tk
from tkinter import ttk

from . import palette, win32
from .i18n import _

# colours and font of a menu, from the palette
LOOK = {
    "font": palette.FONT,
    "size": 10,
    "bg": palette.WHITE,
    "text": palette.TEXT,
    "muted": palette.MUTED,
    "disabled": palette.DISABLED_TEXT,
    "border": palette.BORDER,
    "hover": palette.ACCENT_SOFT,
    "ink": palette.ACCENT_INK,
}
CLICK_GRACE_S = 0.3  # a release this soon after opening belongs to the click that opened the menu
MAX_ROWS = 13  # a longer menu scrolls: a list of forty entries took the whole screen height
TYPE_AHEAD_S = 1.0  # letters typed this close together search together
TAG = "DaykeeperPopup"
SEARCH_FROM = 8  # a Dropdown with more values than this filters as you type


def round_corners(win: tk.Toplevel, border: str) -> bool:
    """Windows 11 rounds the window and draws its outline; False elsewhere."""
    return win32.round_corners(win32.window_handle(win.winfo_id()), border)


def work_area(widget: tk.Misc, x: int, y: int) -> tuple[int, int, int, int]:
    """Left, top, right and bottom of the screen area (taskbar excluded) of the monitor the
    point is on. With a second monitor a menu has to stay on that one: clamping to the main
    screen threw it to the far edge of another monitor."""
    area = win32.work_area_at(x, y)
    if area is not None:
        return area
    root = widget.winfo_toplevel()
    left, top = root.winfo_vrootx(), root.winfo_vrooty()
    return left, top, left + root.winfo_screenwidth(), top + root.winfo_screenheight() - 48


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))


class PopupMenu:
    """A drop-in for tk.Menu as the app uses it."""

    _open: PopupMenu | None = None  # the top menu on screen

    def __init__(self, master, tearoff: bool = False, **_kw):
        while isinstance(master, PopupMenu):  # a submenu is made with its menu as the master
            master = master.master
        self.master: tk.Misc = master
        self.items: list[dict] = []
        self.win: tk.Toplevel | None = None
        self.rows: list[tk.Frame | None] = []
        self.parent: PopupMenu | None = None
        self.child: PopupMenu | None = None
        self.active = -1
        self._posted_at = 0.0
        self._focus: tk.Misc | None = None
        self._grab: tk.Misc | None = None  # a modal dialog's grab, given back on close
        self.canvas: tk.Canvas | None = None  # set when the menu is too long and scrolls
        self._typed = ""
        self._typed_at = 0.0
        self.search = False  # typing filters the rows (a search line on top) instead of jumping
        self.query = ""
        self.hidden: set[int] = set()
        self._search_label: tk.Label | None = None

    # ---- building, as tk.Menu

    def add_command(self, label: str = "", command=None, accelerator: str = "", state: str = "normal", background: str | None = None, **_kw) -> None:
        if "\t" in label:  # “Save\tCtrl+S”: the shortcut goes to the right
            label, tail = label.split("\t", 1)
            accelerator = accelerator or tail
        self.items.append({"kind": "command", "label": label, "command": command, "accel": accelerator, "state": state, "swatch": background})

    def add_checkbutton(self, label: str = "", variable=None, command=None, onvalue=True, offvalue=False, state: str = "normal", **_kw) -> None:
        self.items.append({"kind": "check", "label": label, "variable": variable, "command": command, "on": onvalue, "off": offvalue, "state": state})

    def add_radiobutton(self, label: str = "", variable=None, value=None, command=None, state: str = "normal", **_kw) -> None:
        self.items.append({"kind": "radio", "label": label, "variable": variable, "value": value, "command": command, "state": state})

    def add_cascade(self, label: str = "", menu: PopupMenu | None = None, state: str = "normal", **_kw) -> None:
        self.items.append({"kind": "cascade", "label": label, "menu": menu, "state": state})

    def add_separator(self, **_kw) -> None:
        self.items.append({"kind": "separator"})

    def delete(self, _first=0, _last=None) -> None:
        self.items.clear()

    def grab_release(self) -> None:
        """tk.Menu needs it after tk_popup; here the menu releases the grab itself."""

    def destroy(self) -> None:
        self.unpost()

    # ---- showing

    def tk_popup(self, x: int, y: int, _entry=None) -> None:
        self.post(x + 1, y + 1)

    def post(self, x: int, y: int) -> None:
        if PopupMenu._open is not None:
            PopupMenu._open.unpost()
        if not self.items:
            return
        try:
            self._focus = self.master.focus_get()
        except (KeyError, tk.TclError):  # focus in a combobox list or another app
            self._focus = None
        try:
            self._grab = self.master.grab_current()
        except (KeyError, tk.TclError):
            self._grab = None
        self._show(x, y)
        PopupMenu._open = self
        self._posted_at = time.monotonic()
        if self.win is None:
            return
        try:
            self.win.grab_set()
        except tk.TclError:  # another grab (a dialog being closed): the menu still works by mouse
            pass
        self.win.focus_force()

    def post_below(self, widget: tk.Widget, align: str = "left", match_width: bool = False) -> None:
        """Open under `widget` (above it when there is no room below), left or right aligned."""
        widget.update_idletasks()
        self._min_width = widget.winfo_width() if match_width else 0
        self.post(widget.winfo_rootx(), widget.winfo_rooty() + widget.winfo_height() + 4)
        if self.win is None:
            return
        w, h = self.win.winfo_width(), self.win.winfo_height()
        left, top, right, bottom = work_area(widget, widget.winfo_rootx(), widget.winfo_rooty())
        x = widget.winfo_rootx() + (widget.winfo_width() - w if align == "right" else 0)
        y = widget.winfo_rooty() + widget.winfo_height() + 4
        if y + h > bottom:
            y = widget.winfo_rooty() - h - 4
        self.win.geometry(f"+{_clamp(x, left, right - w)}+{_clamp(y, top, bottom - h)}")

    def _show(self, x: int, y: int) -> None:
        look = LOOK
        font = (look["font"], look["size"])
        win = self.win = tk.Toplevel(self.master)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=look["bg"])
        round_corners(win, look["border"])
        # the outline is drawn either way: where Windows rounds the window it lies under its border
        outline = tk.Frame(win, bg=look["border"])
        outline.pack(fill="both", expand=True)
        self.query, self.hidden, self._search_label = "", set(), None
        if self.search:
            head = tk.Frame(outline, bg=look["bg"], padx=14, pady=7)
            head.pack(side="top", fill="x", padx=1, pady=(1, 0))
            self._search_label = tk.Label(head, text="", bg=look["bg"], font=font, anchor="w")
            self._search_label.pack(side="left", fill="x", expand=True)
            tk.Frame(outline, bg=look["border"], height=1).pack(side="top", fill="x", padx=1)
            self._show_query()
        body = tk.Frame(outline, bg=look["bg"], padx=4, pady=4)
        body.pack(fill="both", expand=True, padx=1, pady=1)
        marks = any(it["kind"] in ("check", "radio") for it in self.items)
        swatches = any(it.get("swatch") for it in self.items)
        self.rows = []
        for i, it in enumerate(self.items):
            if it["kind"] == "separator":
                tk.Frame(body, bg=look["border"], height=1).pack(fill="x", padx=8, pady=4)
                self.rows.append(None)
                continue
            enabled = it.get("state", "normal") != "disabled"
            fg = look["text"] if enabled else look["disabled"]
            row = tk.Frame(body, bg=look["bg"], padx=10, pady=5)
            row.pack(fill="x")
            parts: list[tk.Widget] = []
            if marks:
                parts.append(tk.Label(row, text=self._mark(it), bg=look["bg"], fg=look["ink"], font=font, width=2, anchor="w"))
            if swatches:
                dot = tk.Canvas(row, width=14, height=14, bg=look["bg"], highlightthickness=0)
                if it.get("swatch"):
                    dot.create_oval(1, 1, 13, 13, fill=it["swatch"], outline=look["border"])
                parts.append(dot)
            parts.append(tk.Label(row, text=it["label"], bg=look["bg"], fg=fg, font=font, anchor="w"))
            for part in parts:
                part.pack(side="left", padx=(0, 8) if part is not parts[-1] else 0)
            tail = "›" if it["kind"] == "cascade" else it.get("accel", "")
            if tail:
                tk.Label(row, text=tail, bg=look["bg"], fg=look["muted"], font=font).pack(side="right", padx=(24, 0))
            self.rows.append(row)
            for w in (row, *row.winfo_children()):
                w._popup_slot = (self, i)  # type: ignore[attr-defined]
        left, top, right, bottom = work_area(self.master, x, y)
        self.canvas = None
        body.update_idletasks()
        rows = [r for r in self.rows if r is not None]
        row_h = rows[0].winfo_reqheight() if rows else 30
        limit = min(MAX_ROWS * row_h + 8, bottom - top - 16)
        if body.winfo_reqheight() > limit:
            body.pack_forget()
            bar = ttk.Scrollbar(outline, orient="vertical")
            canvas = self.canvas = tk.Canvas(outline, bg=look["bg"], highlightthickness=0, width=body.winfo_reqwidth(),
                                             height=limit, yscrollincrement=row_h, yscrollcommand=bar.set)
            bar.configure(command=canvas.yview)
            bar.pack(side="right", fill="y", pady=1, padx=(0, 1))
            canvas.pack(side="left", fill="both", expand=True, padx=(1, 0), pady=1)
            item = canvas.create_window(0, 0, window=body, anchor="nw")
            canvas.bind("<Configure>", lambda e: canvas.itemconfigure(item, width=e.width))  # rows as wide as the menu
            body.lift(canvas)  # made before the canvas, it would lie under it
            canvas.configure(scrollregion=(0, 0, body.winfo_reqwidth(), body.winfo_reqheight()))
            for w in (canvas, bar):
                w._popup_slot = (self, -1)  # type: ignore[attr-defined]
        for w in (win, outline, body, *body.winfo_children(), *outline.winfo_children()):
            w._popup_slot = getattr(w, "_popup_slot", (self, -1))  # type: ignore[attr-defined]
        if self._search_label is not None:
            self._search_label._popup_slot = (self, -1)  # type: ignore[attr-defined]
        self._tag(win)
        win.update_idletasks()
        width = max(win.winfo_reqwidth(), getattr(self, "_min_width", 0))
        height = win.winfo_reqheight()
        win.geometry(f"{width}x{height}+{_clamp(x, left, right - width - 4)}+{_clamp(y, top, bottom - height)}")
        win.deiconify()

    @staticmethod
    def _mark(it: dict) -> str:
        var = it.get("variable")
        if var is None:
            return ""
        try:
            value = var.get()
        except tk.TclError:
            return ""
        if it["kind"] == "check":
            return "✓" if value == it["on"] or (it["on"] is True and value in (1, "1")) else ""
        return "✓" if value == it["value"] else ""

    def _tag(self, widget: tk.Misc) -> None:
        widget.bindtags((TAG, *widget.bindtags()))
        for child in widget.winfo_children():
            self._tag(child)

    # ---- state

    def _top(self) -> PopupMenu:
        m = self
        while m.parent is not None:
            m = m.parent
        return m

    def _deepest(self) -> PopupMenu:
        m = self
        while m.child is not None:
            m = m.child
        return m

    def activate(self, index: int) -> None:
        if index == self.active:
            return
        look = LOOK
        for i, bg in ((self.active, look["bg"]), (index, look["hover"])):
            row = self.rows[i] if 0 <= i < len(self.rows) else None
            if row is None or not row.winfo_exists():
                continue
            enabled = self.items[i].get("state", "normal") != "disabled"
            row.configure(bg=bg if enabled else look["bg"])
            for w in row.winfo_children():
                w.configure(bg=bg if enabled else look["bg"])
        self.active = index
        self._scroll_to(index)
        if self.child is not None and (index < 0 or self.items[index].get("menu") is not self.child):
            self.child.unpost_self()

    def _scroll_to(self, index: int) -> None:
        """Keep the active row of a scrolling menu in view."""
        row = self.rows[index] if 0 <= index < len(self.rows) else None
        canvas = self.canvas
        if canvas is None or row is None or not canvas.winfo_exists():
            return
        canvas.update_idletasks()  # rows in a just opened menu get their places now
        total = max(1, row.master.winfo_reqheight())
        top, bottom = canvas.canvasy(0), canvas.canvasy(canvas.winfo_height())
        y, h = row.winfo_y(), row.winfo_height()
        if y < top:
            canvas.yview_moveto(y / total)
        elif y + h > bottom:
            canvas.yview_moveto((y + h - canvas.winfo_height()) / total)

    def selectable(self) -> list[int]:
        """Rows the arrows and Enter go to: not separators, not disabled, not filtered out."""
        return [i for i, it in enumerate(self.items) if it["kind"] != "separator" and it.get("state") != "disabled" and i not in self.hidden]

    def _show_query(self) -> None:
        label = self._search_label
        if label is not None and label.winfo_exists():
            label.configure(text=f"🔍  {self.query}" if self.query else "🔍  " + _("Type to search"),
                            fg=LOOK["text"] if self.query else LOOK["muted"])

    def filter(self, query: str) -> None:
        """Show only the rows whose label holds `query` (any case, anywhere in the label)."""
        self.query = query
        self._show_query()
        wanted = query.casefold().strip()
        self.hidden = {i for i, it in enumerate(self.items) if wanted and (it["kind"] == "separator" or wanted not in str(it.get("label", "")).casefold())}
        for row in self.rows:
            if row is not None:
                row.pack_forget()
        for i, row in enumerate(self.rows):
            if row is not None and i not in self.hidden:
                row.pack(fill="x")
        if self.canvas is not None and self.canvas.winfo_exists():
            body = next((r.master for r in self.rows if r is not None), None)
            if body is not None:
                body.update_idletasks()
                self.canvas.configure(scrollregion=(0, 0, body.winfo_reqwidth(), body.winfo_reqheight()))
            self.canvas.yview_moveto(0)
        shown = self.selectable()
        if self.active in self.hidden:
            self.activate(-1)  # repaints the row it leaves
        if shown:
            self.activate(shown[0])

    def type_ahead(self, char: str) -> None:
        """Typing jumps to the first item that starts with the typed letters."""
        now = time.monotonic()
        self._typed = (self._typed if now - self._typed_at < TYPE_AHEAD_S else "") + char.casefold()
        self._typed_at = now
        for i, it in enumerate(self.items):
            label = re.sub(r"^\W+", "", str(it.get("label", "")))  # past an icon in front
            if it["kind"] != "separator" and it.get("state") != "disabled" and label.casefold().startswith(self._typed):
                self.activate(i)
                return

    def scroll(self, units: int) -> None:
        if self.canvas is not None and self.canvas.winfo_exists():
            self.canvas.yview_scroll(units, "units")

    def open_child(self, index: int) -> None:
        it = self.items[index]
        menu = it.get("menu")
        if it["kind"] != "cascade" or menu is None or it.get("state") == "disabled" or menu is self.child:
            return
        if self.child is not None:
            self.child.unpost_self()
        row = self.rows[index]
        if row is None or self.win is None:
            return
        menu.parent = self
        self.child = menu
        x, y = self.win.winfo_rootx() + self.win.winfo_width() - 2, row.winfo_rooty() - 4
        menu._show(x, y)
        # no room on the right: open to the left
        if menu.win is not None and menu.win.winfo_rootx() < x - 6:
            left = work_area(self.master, x, y)[0]
            menu.win.geometry(f"+{max(left, self.win.winfo_rootx() - menu.win.winfo_reqwidth() + 2)}+{menu.win.winfo_rooty()}")

    def unpost_self(self) -> None:
        """Close this menu and its submenus, not the menus above it."""
        if self.child is not None:
            self.child.unpost_self()
        if self.win is not None and self.win.winfo_exists():
            self.win.destroy()
        self.win = None
        self.canvas = None
        self.rows = []
        self.active = -1
        if self.parent is not None:
            self.parent.child = None
            self.parent = None

    def unpost(self) -> None:
        """Close the whole menu chain this menu belongs to."""
        top = self._top()
        if top.win is not None and top.win.winfo_exists():
            try:
                top.win.grab_release()
            except tk.TclError:
                pass
        top.unpost_self()
        if PopupMenu._open is top:
            PopupMenu._open = None
        grab, top._grab = top._grab, None
        if grab is not None:
            try:
                if grab.winfo_exists():
                    grab.grab_set()
            except tk.TclError:
                pass
        focus = top._focus
        top._focus = None
        if focus is not None:
            try:
                if focus.winfo_exists():
                    focus.focus_set()
            except tk.TclError:
                pass

    def invoke(self, index: int) -> None:
        it = self.items[index]
        if it.get("state") == "disabled" or it["kind"] in ("separator", "cascade"):
            return
        self.unpost()
        var = it.get("variable")
        if it["kind"] == "check" and var is not None:
            var.set(it["off"] if self._mark(it) else it["on"])
        elif it["kind"] == "radio" and var is not None:
            var.set(it["value"])
        command = it.get("command")
        if command is not None:
            self.master.after_idle(command)  # after the grab is gone: commands open dialogs


def _slot_at(widget: tk.Misc, x: int, y: int) -> tuple[PopupMenu, int] | None:
    try:
        under = widget.winfo_containing(x, y)
    except (KeyError, tk.TclError):
        return None
    while under is not None:
        slot = getattr(under, "_popup_slot", None)
        if slot is not None and slot[0].win is not None:
            return slot
        under = under.master
    return None


def _on_motion(event: tk.Event) -> None:
    top = PopupMenu._open
    if top is None:
        return
    slot = _slot_at(event.widget, event.x_root, event.y_root)
    if slot is None:
        return
    menu, index = slot
    if index < 0:
        return
    menu.activate(index)
    if menu.items[index]["kind"] == "cascade":
        menu.open_child(index)


def _on_press(event: tk.Event) -> str:
    top = PopupMenu._open
    if top is None:
        return ""
    if isinstance(event.widget, ttk.Scrollbar):  # the scroll bar of a long menu does its own work
        return ""
    slot = _slot_at(event.widget, event.x_root, event.y_root)
    if slot is None:
        top.unpost()  # a click outside closes the menu, as a system menu does
        return "break"
    menu, index = slot
    if index >= 0 and menu.items[index]["kind"] == "cascade":
        menu.open_child(index)
    return "break"


def _on_release(event: tk.Event) -> str:
    top = PopupMenu._open
    if top is None or time.monotonic() - top._posted_at < CLICK_GRACE_S or isinstance(event.widget, ttk.Scrollbar):
        return ""
    slot = _slot_at(event.widget, event.x_root, event.y_root)
    if slot is not None and slot[1] >= 0:
        slot[0].invoke(slot[1])
    return "break"


def _on_key(event: tk.Event) -> str:
    top = PopupMenu._open
    if top is None:
        return ""
    menu = top._deepest()
    key = event.keysym
    enabled = menu.selectable()
    if menu.search and key == "BackSpace":
        menu.filter(menu.query[:-1])
        return "break"
    if menu.search and event.char and event.char.isprintable():
        menu.filter(menu.query + event.char)
        return "break"
    if key == "Escape":
        if menu.parent is not None:
            menu.unpost_self()
        else:
            top.unpost()
    elif key in ("Down", "Up") and enabled:
        pos = enabled.index(menu.active) if menu.active in enabled else (-1 if key == "Down" else len(enabled))
        menu.activate(enabled[(pos + (1 if key == "Down" else -1)) % len(enabled)])
    elif key == "Right" and menu.active >= 0 and menu.items[menu.active]["kind"] == "cascade":
        menu.open_child(menu.active)
        if menu.child is not None:
            child_enabled = [i for i, it in enumerate(menu.child.items) if it["kind"] != "separator"]
            if child_enabled:
                menu.child.activate(child_enabled[0])
    elif key == "Left" and menu.parent is not None:
        menu.unpost_self()
    elif key in ("Return", "KP_Enter") and menu.active >= 0:
        if menu.items[menu.active]["kind"] == "cascade":
            menu.open_child(menu.active)
        else:
            menu.invoke(menu.active)
    elif key in ("Prior", "Next"):
        for _i in range(MAX_ROWS - 1):
            _on_key_step(menu, "Up" if key == "Prior" else "Down")
    elif event.char and event.char.isprintable() and (event.char != " " or menu._typed):
        menu.type_ahead(event.char)
    elif key == "space" and menu.active >= 0:
        menu.invoke(menu.active)
    return "break"


def _on_key_step(menu: PopupMenu, key: str) -> None:
    enabled = menu.selectable()
    if enabled:
        pos = enabled.index(menu.active) if menu.active in enabled else -1
        menu.activate(enabled[max(0, min(len(enabled) - 1, pos + (1 if key == "Down" else -1)))])


def _on_wheel(event: tk.Event) -> str:
    top = PopupMenu._open
    if top is None:
        return ""
    slot = _slot_at(event.widget, event.x_root, event.y_root)
    menu = slot[0] if slot is not None else top._deepest()
    menu.scroll(-1 if event.delta > 0 else 1)
    return "break"


def _on_focus_out(_event: tk.Event) -> None:
    top = PopupMenu._open
    if top is None or top.win is None:
        return

    def check() -> None:
        if PopupMenu._open is not top or top.win is None:
            return
        try:
            gone = top.win.focus_get() is None  # the app lost the focus (Alt+Tab, another app)
        except (KeyError, tk.TclError):
            gone = False
        if gone:
            top.unpost()

    top.win.after(80, check)


def install(root: tk.Tk) -> None:
    """The bindings every menu window shares; once per app."""
    root.bind_class(TAG, "<Motion>", _on_motion)
    root.bind_class(TAG, "<ButtonPress>", _on_press)
    root.bind_class(TAG, "<ButtonRelease-1>", _on_release)
    root.bind_class(TAG, "<Key>", _on_key)
    root.bind_class(TAG, "<FocusOut>", _on_focus_out)
    root.bind_class(TAG, "<MouseWheel>", _on_wheel)


class Dropdown(ttk.Button):
    """A read-only combobox: a field-like button with the values in a PopupMenu. Speaks the
    part of the ttk.Combobox interface the app uses and sends <<ComboboxSelected>>."""

    def __init__(self, master, textvariable: tk.StringVar | None = None, values=(), width: int | None = None, state: str = "readonly", style: str = "Dropdown.TButton", **_kw):
        self.var = textvariable if textvariable is not None else tk.StringVar(master)
        self._values = [str(v) for v in values]
        kw = {"width": width} if width else {}
        super().__init__(master, style=style, command=self._open, **kw)
        self._trace = self.var.trace_add("write", lambda *_a: self._sync())
        if state == "disabled":
            self.state(["disabled"])
        self._sync()

    def _sync(self) -> None:
        if self.winfo_exists():
            ttk.Button.configure(self, text=self.var.get())

    def _open(self) -> None:
        menu = PopupMenu(self)
        menu.search = len(self._values) > SEARCH_FROM
        for value in self._values:
            menu.add_radiobutton(label=value, variable=self.var, value=value, command=self._picked)
        menu.post_below(self, match_width=True)
        if self.var.get() in self._values and menu.win is not None:  # a long list opens at the picked value
            index = self._values.index(self.var.get())
            menu.activate(index)
            menu.win.after(15, lambda: menu._scroll_to(index))  # the rows have their places once on screen

    def _picked(self) -> None:
        if self.winfo_exists():
            self.event_generate("<<ComboboxSelected>>")

    def get(self) -> str:
        return self.var.get()

    def set(self, value: str) -> None:
        self.var.set(value)

    def current(self, index: int | None = None) -> int:
        if index is not None:
            self.var.set(self._values[index])
            return index
        value = self.var.get()
        return self._values.index(value) if value in self._values else -1

    def configure(self, cnf=None, **kw):
        if "values" in kw:
            self._values = [str(v) for v in kw.pop("values")]
        if "state" in kw:
            self.state(["disabled"] if kw.pop("state") == "disabled" else ["!disabled"])
        if cnf or kw:
            return ttk.Button.configure(self, cnf, **kw)
        return None

    config = configure

    def cget(self, key: str):
        return tuple(self._values) if key == "values" else ttk.Button.cget(self, key)

    def __setitem__(self, key, value) -> None:
        self.configure(**{key: value})

    def __getitem__(self, key):
        return self.cget(key)

    def destroy(self) -> None:
        try:
            self.var.trace_remove("write", self._trace)
        except (tk.TclError, ValueError):
            pass
        super().destroy()
