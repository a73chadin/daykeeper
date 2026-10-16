"""The “Passwords” tab: the list of entries on the left, the card with its fields on the
right. Lists (groups) switch with the drop-down over the list; the search looks across
all lists and says where a hit lives. Everything the window needs from it goes through
`TabPage` (refresh, new_item, focus_search) and `select_entry`."""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import font as tkfont, ttk

from .clipboard import CLEAR_SEC
from .dialogs import BULLET, ask_text, ask_yes_no, show_info, show_warning
from .fieldicons import summary as field_summary
from .i18n import _, fmt_time, hotkeys
from .palette import FONT, WHITE
from .popup import Dropdown, PopupMenu
from .sidebar import SidebarKeeper
from .tabpage import Shell, TabPage, make_scrollable
from .timers import Debounce
from .vault import DEFAULT_KIND, KINDS, MAIN_GROUP, Entry, Field, generate_password, main_group_label
from .widgets import Placeholder, RowTip, TextField, Tooltip, more_button, divider

# Preset fields for the “+ Add field” button: (menu label key, field name key, secret?).
FIELD_PRESETS: list[tuple[str, str, bool]] = [
    ("🎟  Token", "token", True),
    ("👤  Login", "login", False),
    ("🔑  Password", "password", True),
    ("🌐  Site / URL", "site", False),
    ("✉  Mail", "mail", False),
    ("📞  Phone", "phone", False),
    ("📅  Valid until", "valid until", False),
    ("🔢  PIN code", "PIN", True),
    ("🛡  2FA key", "2FA key", True),
    ("🗝  Private key", "private key", True),
]

MAX_ROW_ICONS = 4  # more fields than this show as “+N”; the tooltip lists them all

ENTRY_KIND_ICONS = {
    "login": "🔑",
    "token": "🎟",
    "key": "🗝",
    "card": "💳",
    "note": "📝",
    "other": "📦",
}
SEARCH_SETTLE_MS = 150  # the list is filtered once the typing pauses this long


class VaultPage(TabPage):
    """The Passwords tab. `current` is the entry on the card, `group` the list shown."""

    name = "vault"

    def __init__(self, app: Shell, parent: tk.Misc):
        self.current: Entry | None = None
        self.group: str = MAIN_GROUP  # which list is shown on the left
        self._visible: list[Entry] = []
        self._loading = False
        self._kind_font = tkfont.Font(family=FONT, size=11)  # measures the kind column of the list
        self._kind_widths: dict[str, int] = {}  # text → its width in that font
        self._vault_keeper: SidebarKeeper | None = None  # pins the sidebar; made after both panes
        self.entry_group_box: Dropdown | None = None  # on the card, made after the sidebar
        super().__init__(app, parent)

    def build(self, frame: ttk.Frame) -> None:
        paned = ttk.PanedWindow(frame, orient="horizontal")
        paned.pack(fill="both", expand=True)
        self._build_sidebar(paned)
        self._build_card(paned)
        self._vault_keeper = SidebarKeeper(paned, minimum=self._sidebar_min, width=max(self.app.painter.px(400), self._sidebar_min))

    # ---- what the window asks for

    def open_first(self) -> None:
        """After the screen is built: the list, its first entry on the card, the count in the status."""
        self.refresh_list()
        if self._visible:
            self._load_entry(self._visible[0])
            self._select_in_tree(self._visible[0])
        else:
            self._load_entry(None)
        self.app.set_status(_("Entries: {n}").format(n=len(self.vault.entries)))

    def new_item(self) -> None:
        self.new_entry()

    def focus_search(self) -> None:
        self._search_entry.focus_set()

    def select_entry(self, entry: Entry) -> None:
        """Show `entry` on the card, switching to its list first (a bookmark tile's link)."""
        if entry.group != self.group:
            self.group = entry.group
            self._refresh_group_widgets()
        self.search_var.set("")
        self.refresh_list()
        self._load_entry(entry)
        self._select_in_tree(entry)

    def reload(self) -> None:
        """The vault changed under the tab (an import): the lists, the list and the card follow."""
        self._refresh_group_widgets()
        self.refresh_list()
        if self.current is not None:
            self._load_entry(self.current)
        self.edited()

    def edited(self, touch_entry: bool = True) -> None:
        """Save soon. `touch_entry` — the open entry was edited: its stamp moves, and a tile
        tied to it shows its name and login, so Bookmarks are told too."""
        if touch_entry and self.current is not None:
            self.current.updated = time.time()
            if self.updated_label.winfo_exists():
                self.updated_label.config(text=_("Updated {stamp}").format(stamp=fmt_time(self.current.updated)))
            if self._tied_to_bookmark(self.current):
                self.app.session.touch("bookmarks")
        self.app.mark_dirty("vault")

    # ---- the tab itself (as it was in the window)

    def _build_sidebar(self, paned: ttk.PanedWindow) -> None:
        left = ttk.Frame(paned, style="Sidebar.TFrame", padding=(16, 16, 12, 12))
        paned.add(left, weight=0)  # the card takes the resize; SidebarKeeper pins this width

        # list switcher + the menu that manages lists
        group_row = ttk.Frame(left, style="Sidebar.TFrame")
        group_row.pack(fill="x", pady=(0, 10))
        ttk.Label(group_row, text=_("List"), style="SidebarMuted.TLabel").pack(side="left", padx=(0, 8))
        self.group_var = tk.StringVar()
        self.group_box = Dropdown(group_row, textvariable=self.group_var, style="SidebarDropdown.TButton")
        self.group_box.pack(side="left", fill="x", expand=True)
        self.group_box.bind("<<ComboboxSelected>>", self._on_group_select)
        more = more_button(group_row, [(_("New list…"), self.new_group), (_("Rename list…"), self.rename_group), (_("Delete list"), self.delete_group)])
        more.pack(side="left", padx=(6, 0))
        Tooltip(more, _("Create, rename or delete a list"))

        # search with a placeholder hint over the field
        self.search_var = tk.StringVar()
        self._search_later = Debounce(self.frame, SEARCH_SETTLE_MS, self.refresh_list)
        self.search_var.trace_add("write", lambda *_: self._on_search_change())
        search = ttk.Entry(left, textvariable=self.search_var, font=(FONT, 11))
        search.pack(fill="x")
        self._search_entry = search
        self._search_hint = Placeholder(search, hotkeys(_("🔍  Search  (Ctrl+F)")), WHITE, var=self.search_var, padx=13, hide_on_focus=False)

        self.tree = ttk.Treeview(left, columns=("kind",), show="tree", selectmode="browse")
        self.tree.column("#0", stretch=True, width=150, minwidth=120)
        self.tree.column("kind", width=96, anchor="e", stretch=False)
        RowTip(self.tree, self._entry_tip)
        self.tree.pack(fill="both", expand=True, pady=(12, 12))
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Delete>", lambda _e: self.delete_entry())
        self.tree.bind("<Button-3>", self._show_context_menu)

        self._empty_hint = ttk.Label(left, text="", style="SidebarMuted.TLabel", justify="center")

        btns = ttk.Frame(left, style="Sidebar.TFrame")
        btns.pack(fill="x")
        new_btn = ttk.Button(btns, text=_("+ New entry") + "   ▾", style="Primary.TButton")
        new_menu = PopupMenu(new_btn)
        for kind, label in KINDS.items():
            new_menu.add_command(label=f"{ENTRY_KIND_ICONS[kind]}  {label}", command=lambda k=kind: self.new_entry(k))
        new_btn.config(command=lambda: new_menu.post_below(new_btn))
        new_btn.pack(side="left")
        delete = ttk.Button(btns, text="🗑", style="DangerIcon.TButton", command=self.delete_entry)
        delete.pack(side="right")
        Tooltip(delete, _("Delete"))
        down = ttk.Button(btns, text="▼", style="SidebarIcon.TButton", command=lambda: self.move_entry(+1))
        down.pack(side="right", padx=(2, 6))
        Tooltip(down, _("Down  (Alt+↓)"))
        up = ttk.Button(btns, text="▲", style="SidebarIcon.TButton", command=lambda: self.move_entry(-1))
        up.pack(side="right", padx=(6, 2))
        Tooltip(up, _("Up  (Alt+↑)"))
        self._refresh_group_widgets()
        left.update_idletasks()
        self._sidebar_min = max(self.app.painter.px(300), left.winfo_reqwidth())  # narrower would cut the list and the buttons

    def _build_card(self, paned: ttk.PanedWindow) -> None:
        right = ttk.Frame(paned, style="Card.TFrame")
        paned.add(right, weight=1)

        self.card_canvas, self.card_vsb, card = make_scrollable(self.app, right, (32, 24, 28, 24))
        self.card = card
        self._card_win = self.card_canvas.find_withtag("all")[0]

        # ---- header: the name large, kind and date below
        self.name_var = tk.StringVar()
        self.name_var.trace_add("write", self._on_name_change)
        self.name_entry = ttk.Entry(card, textvariable=self.name_var, font=(FONT, 17, "bold"))
        self.name_entry.pack(fill="x", ipady=2)

        meta = ttk.Frame(card, style="Card.TFrame")
        meta.pack(fill="x", pady=(10, 0))
        ttk.Label(meta, text=_("Kind"), style="CardMuted.TLabel").pack(side="left")
        self.kind_var = tk.StringVar()
        self.kind_box = Dropdown(meta, textvariable=self.kind_var, values=[f"{ENTRY_KIND_ICONS[k]}  {v}" for k, v in KINDS.items()], width=22)
        self.kind_box.pack(side="left", padx=(8, 0))
        self.kind_box.bind("<<ComboboxSelected>>", self._on_kind_change)
        ttk.Label(meta, text=_("List"), style="CardMuted.TLabel").pack(side="left", padx=(18, 0))
        self.entry_group_var = tk.StringVar()
        self.entry_group_box = Dropdown(meta, textvariable=self.entry_group_var, width=16)
        self.entry_group_box.pack(side="left", padx=(8, 0))
        self.entry_group_box.bind("<<ComboboxSelected>>", self._on_entry_group_change)
        self.updated_label = ttk.Label(meta, text="", style="CardMuted.TLabel")
        self.updated_label.pack(side="right")

        divider(card).pack(fill="x", pady=(18, 14))

        # ---- fields
        ttk.Label(card, text=_("FIELDS"), style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        self.fields_frame = ttk.Frame(card, style="Card.TFrame")
        self.fields_frame.pack(fill="x")
        self.fields_frame.columnconfigure(1, weight=1)
        self.add_field_btn = ttk.Button(card, text=_("+ Add field  ▾"), style="Link.TButton", command=self._show_add_field_menu)
        self.add_field_btn.pack(anchor="w", pady=(6, 0))
        Tooltip(self.add_field_btn, _("Token, login, password, site… or a custom field"))

        # ---- note
        ttk.Label(card, text=_("NOTE"), style="Section.TLabel").pack(anchor="w", pady=(18, 6))
        self.note = TextField(card, 8)
        self.note.pack(fill="x")
        self.note.bind("<<Modified>>", self._on_note_change)

        # ---- placeholder when there is no entry
        self.placeholder = ttk.Label(
            right,
            text=_("Pick an entry on the left\nor create a new one"),
            style="CardMuted.TLabel",
            font=(FONT, 13),
            justify="center",
        )

    def _on_search_change(self) -> None:
        """Typing a query: the list follows once the typing pauses — each letter refiltered
        a list of thousands. Clearing the field shows the whole list at once."""
        if self.search_var.get().strip():
            self._search_later.touch()
        else:
            self._search_later.cancel()
            self.refresh_list()

    def cancel_jobs(self) -> None:
        self._search_later.cancel()

    def _entry_tip(self, iid: str) -> str:
        entry = self.vault.entry_by_id(iid) if self.vault is not None else None
        if entry is None:
            return ""
        names = field_summary(entry.fields)[1]
        return (entry.name or _("(untitled)")) + "\n" + entry.kind_label + "\n" + (_("Fields: {names}").format(names=", ".join(names)) if names else _("No filled fields"))

    def refresh_list(self) -> None:
        if self.vault is None:
            return
        query = self.search_var.get()
        self._visible = self.vault.search(query, self.group)
        self.tree.delete(*self.tree.get_children())
        rights: list[str] = []
        for e in self._visible:
            icon = ENTRY_KIND_ICONS.get(e.kind, ENTRY_KIND_ICONS[DEFAULT_KIND])
            right = self._row_right(e, query)
            self.tree.insert("", "end", iid=e.id, text=f"  {icon}  {e.name or _('(untitled)')}", values=(right,))
            rights.append(right)
        self._fit_kind_column(rights)
        if self.current is not None and self.tree.exists(self.current.id):
            self._select_in_tree(self.current)
        if self._visible:
            self._empty_hint.pack_forget()
        else:
            query = self.search_var.get().strip()
            self._empty_hint.config(text=_("Nothing found") if query else _("This list is empty.\nCreate an entry or move one here."))
            self._empty_hint.pack(before=self.tree, pady=(40, 0))

    def _row_right(self, e: Entry, query: str) -> str:
        """The right cell of a list row: icons of the filled fields, the kind while none is."""
        if query.strip() and e.group != self.group:  # found in another list — say which
            return _("in “{group}”").format(group=e.group or main_group_label())
        icons = field_summary(e.fields)[0].split()  # what is inside, not the kind it started as
        if not icons:
            return e.kind_label
        return " ".join(icons[:MAX_ROW_ICONS]) + (f" +{len(icons) - MAX_ROW_ICONS}" if len(icons) > MAX_ROW_ICONS else "")

    def _refresh_row(self, e: Entry) -> None:
        """A field was typed into: its icon shows up in the list row at once."""
        if not self.tree.exists(e.id):
            return
        right = self._row_right(e, self.search_var.get())
        if self.tree.set(e.id, "kind") != right:
            self.tree.set(e.id, "kind", right)
            self._fit_kind_column([self.tree.set(i, "kind") for i in self.tree.get_children()])

    def _fit_kind_column(self, texts: list[str]) -> None:
        """The right column as wide as its widest cell (icons grow with the screen scale),
        and the sidebar never narrower than the list needs. Runs on every keystroke that
        changes a row, so the font is made once and each text is measured once."""
        widths = self._kind_widths
        for t in texts:
            if t not in widths:
                widths[t] = self._kind_font.measure(t)
        pad = self.app.painter.px(18)
        width = max([widths[t] + pad for t in texts] + [self.app.painter.px(60)])
        width = min(width, self.app.painter.px(240))
        if int(self.tree.column("kind", "width")) == width:
            return
        self.tree.column("kind", width=width)
        self.tree.column("#0", width=self.app.painter.px(190))  # names keep their room: the sidebar widens instead
        keeper = self._vault_keeper
        if keeper is None:
            return
        self.tree.master.update_idletasks()
        keeper.minimum = max(300, self.tree.master.winfo_reqwidth())
        if keeper.width is not None and keeper.width < keeper.minimum:
            keeper.width = keeper.minimum
        keeper.place()

    def _select_in_tree(self, entry: Entry) -> None:
        if not self.tree.exists(entry.id):
            return
        self._loading = True  # programmatic selection — do not reload the card
        try:
            self.tree.selection_set(entry.id)
            self.tree.see(entry.id)
        finally:
            self._loading = False

    def _on_select(self, _event=None) -> None:
        if self._loading:
            return
        sel = self.tree.selection()
        if not sel:
            return
        entry = next((e for e in self._visible if e.id == sel[0]), None)
        if entry is None:
            return
        if self.current is None or entry.id != self.current.id:
            self._load_entry(entry)
        if entry.group != self.group:  # a search hit from another list was picked — switch to that list
            self.group = entry.group
            self.group_var.set(entry.group or main_group_label())

    def new_entry(self, kind: str = DEFAULT_KIND) -> None:
        if self.vault is None:
            return
        entry = self.vault.add_entry(kind=kind, group=self.group)
        self.current = entry
        self.search_var.set("")
        self.refresh_list()
        self._load_entry(entry)
        self._select_in_tree(entry)
        self.name_entry.focus_set()
        self.name_entry.select_range(0, "end")
        self.edited()

    def delete_entry(self) -> None:
        if self.vault is None or self.current is None:
            return
        if not ask_yes_no(self.app, _("Delete “{name}”?").format(name=self.current.name)):
            return
        if self._tied_to_bookmark(self.current):
            self.app.session.touch("bookmarks")  # the tiles lose their login and link
        self.vault.remove_entry(self.current)
        self.current = None
        self.refresh_list()
        if self._visible:
            self._load_entry(self._visible[0])
            self._select_in_tree(self._visible[0])
        else:
            self._load_entry(None)
        self.edited()

    def _set_card_enabled(self, enabled: bool) -> None:
        # the canvas stays (otherwise the right pane collapses to zero), only the card is hidden
        self.card_canvas.itemconfigure(self._card_win, state="normal" if enabled else "hidden")
        if enabled:
            self.placeholder.place_forget()
            self.card_vsb.pack(side="right", fill="y")
        else:
            self.card_vsb.pack_forget()
            self.placeholder.place(relx=0.5, rely=0.45, anchor="center")

    def _load_entry(self, entry: Entry | None) -> None:
        self._loading = True
        try:
            self.current = entry
            for child in self.fields_frame.winfo_children():
                child.destroy()
            self.note.delete("1.0", "end")
            if entry is None:
                self.name_var.set("")
                self._set_card_enabled(False)
                return
            self._set_card_enabled(True)
            self.name_var.set(entry.name)
            self.kind_var.set(f"{ENTRY_KIND_ICONS.get(entry.kind, ENTRY_KIND_ICONS[DEFAULT_KIND])}  {entry.kind_label}")
            self.entry_group_var.set(entry.group or main_group_label())
            self.updated_label.config(text=_("Updated {stamp}").format(stamp=fmt_time(entry.updated)))
            self.note.insert("1.0", entry.note)
            self.note.edit_modified(False)
            for i, f in enumerate(entry.fields):
                self._build_field_row(i, f)
            self.card_canvas.yview_moveto(0)
        finally:
            self._loading = False

    def _build_field_row(self, row: int, f: Field) -> None:
        frame = self.fields_frame
        key_var = tk.StringVar(value=f.key)
        val_var = tk.StringVar(value=f.value)
        secret_var = tk.BooleanVar(value=f.secret)

        key_entry = ttk.Entry(frame, textvariable=key_var, width=15, style="Key.TEntry", font=(FONT, 10))
        key_entry.grid(row=row, column=0, sticky="ew", pady=4)
        val_entry = ttk.Entry(frame, textvariable=val_var, width=10, show=BULLET if f.secret else "", font=(FONT, 11))
        val_entry.grid(row=row, column=1, sticky="ew", padx=(8, 8), pady=4)

        def sync(*_):
            if self._loading:
                return
            f.key, f.value, f.secret = key_var.get(), val_var.get(), secret_var.get()
            self.edited()
            if self.current is not None:
                self._refresh_row(self.current)

        key_var.trace_add("write", sync)
        val_var.trace_add("write", sync)

        def toggle_show():
            val_entry.config(show="" if val_entry.cget("show") else BULLET)

        def toggle_secret():
            val_entry.config(show=BULLET if secret_var.get() else "")
            sync()

        eye = ttk.Button(frame, text="👁", style="Icon.TButton", command=toggle_show)
        eye.grid(row=row, column=2, padx=1)
        Tooltip(eye, _("Show / hide value"))

        copy_btn = ttk.Button(
            frame, text=_("Copy"), style="Small.TButton", command=lambda: self.app.copy_to_clipboard(val_var.get())
        )
        copy_btn.grid(row=row, column=3, padx=(4, 1))
        Tooltip(copy_btn, _("To the clipboard; cleared after {n} s").format(n=CLEAR_SEC))

        gen = ttk.Button(frame, text="🎲", style="Icon.TButton", command=lambda: val_var.set(generate_password()))
        gen.grid(row=row, column=4, padx=1)
        Tooltip(gen, _("Generate password"))

        lock = ttk.Checkbutton(frame, text="🔒", style="Toggle.Toolbutton", variable=secret_var, command=toggle_secret)
        lock.grid(row=row, column=5, padx=1)
        Tooltip(lock, _("Secret: show as dots, do not search by value"))

        rm = ttk.Button(frame, text="✕", style="Icon.TButton", command=lambda: self.remove_field(f))
        rm.grid(row=row, column=6, padx=(1, 0))
        Tooltip(rm, _("Delete field"))

    def _show_add_field_menu(self) -> None:
        """Preset field menu under the button: the chosen field is added with the right lock state."""
        if self.current is None:
            return
        menu = PopupMenu(self.app, tearoff=False)
        for label, key, secret in FIELD_PRESETS:
            menu.add_command(label=_(label), command=lambda k=key, s=secret: self.add_field(_(k), s))
        menu.add_separator()
        menu.add_command(label=_("Custom field…"), command=self.add_field)
        btn = self.add_field_btn
        try:
            menu.tk_popup(btn.winfo_rootx(), btn.winfo_rooty() + btn.winfo_height())
        finally:
            menu.grab_release()

    def add_field(self, key: str = "", secret: bool = False) -> None:
        if self.current is None:
            return
        self.current.fields.append(Field(key, secret=secret))
        self._load_entry(self.current)
        self.edited()
        rows = self.fields_frame.grid_slaves(column=1 if key else 0)
        if rows:
            rows[0].focus_set()  # grid_slaves returns in reverse order — [0] is the last row
        self.app.update_idletasks()
        self.card_canvas.yview_moveto(1.0)

    def remove_field(self, f: Field) -> None:
        if self.current is None:
            return
        self.current.fields = [x for x in self.current.fields if x is not f]
        self._load_entry(self.current)
        self.edited()
        self._refresh_row(self.current)

    def _on_name_change(self, *_) -> None:
        if self._loading or self.current is None:
            return
        self.current.name = self.name_var.get()
        self._name_row(self.current)
        self.edited()

    def _name_row(self, e: Entry) -> None:
        """The entry's row says its new name or kind. Only that row: rebuilding the list on
        every letter of a name cost a list of thousands of rows each time. A search may
        no longer find the entry, so then the list is filtered again."""
        if self.search_var.get().strip() or not self.tree.exists(e.id):
            self.refresh_list()
            return
        icon = ENTRY_KIND_ICONS.get(e.kind, ENTRY_KIND_ICONS[DEFAULT_KIND])
        self.tree.item(e.id, text=f"  {icon}  {e.name or _('(untitled)')}")
        self._refresh_row(e)

    def _on_kind_change(self, _event=None) -> None:
        if self._loading or self.current is None:
            return
        idx = self.kind_box.current()
        if idx < 0:
            return
        self.current.kind = list(KINDS)[idx]
        self._name_row(self.current)
        self.edited()

    def _on_entry_group_change(self, _event=None) -> None:
        if self._loading or self.current is None:
            return
        self.set_entry_group(self.current, self._group_from_label(self.entry_group_var.get()))

    def set_entry_group(self, entry: Entry, group: str) -> None:
        """Move the entry to list `group` and show it there."""
        if self.vault is None or entry.group == group:
            return
        self.vault.set_group(entry, group)
        self.group = group
        self.group_var.set(group or main_group_label())
        self.search_var.set("")
        self.refresh_list()
        self._select_in_tree(entry)
        self.edited()
        self.app.set_status(_("“{name}” → list “{group}”").format(name=entry.name, group=group or main_group_label()))

    def move_entry(self, delta: int) -> None:
        if self.vault is None or self.current is None:
            return
        if self.search_var.get().strip():
            self.app.set_status(_("Order changes in the list, not in search results"))
            return
        if self.vault.move_entry(self.current, delta):
            self.refresh_list()
            self.edited()

    def _group_values(self) -> list[str]:
        return [main_group_label()] + list(self.vault.groups) if self.vault else [main_group_label()]

    def _group_from_label(self, label: str) -> str:
        return MAIN_GROUP if label == main_group_label() else label

    def _refresh_group_widgets(self) -> None:
        values = self._group_values()
        self.group_box.config(values=values)
        self.group_var.set(self.group or main_group_label())
        if self.entry_group_box is not None and self.entry_group_box.winfo_exists():
            self.entry_group_box.config(values=values)

    def _on_group_select(self, _event=None) -> None:
        self.group = self._group_from_label(self.group_var.get())
        self.search_var.set("")
        self.current = None
        self.refresh_list()
        if self._visible:
            self._load_entry(self._visible[0])
            self._select_in_tree(self._visible[0])
        else:
            self._load_entry(None)

    def new_group(self) -> None:
        if self.vault is None:
            return
        name = ask_text(self.app, _("New list"), _("List name"))
        if name is None:
            return
        try:
            self.group = self.vault.add_group(name)
        except ValueError as exc:
            show_warning(self.app, str(exc))
            return
        self._refresh_group_widgets()
        self._on_group_select()
        self.edited()

    def rename_group(self) -> None:
        if self.vault is None:
            return
        if self.group == MAIN_GROUP:
            show_info(self.app, _("The main list cannot be renamed."))
            return
        name = ask_text(self.app, _("Rename list"), _("New name"), initial=self.group)
        if name is None:
            return
        try:
            self.group = self.vault.rename_group(self.group, name)
        except ValueError as exc:
            show_warning(self.app, str(exc))
            return
        self._refresh_group_widgets()
        if self.current is not None:
            self.entry_group_var.set(self.current.group or main_group_label())
        self.edited()

    def delete_group(self) -> None:
        if self.vault is None:
            return
        if self.group == MAIN_GROUP:
            show_info(self.app, _("The main list cannot be deleted."))
            return
        count = len(self.vault.search("", self.group))
        if not ask_yes_no(self.app, _("Delete list “{name}”?\nEntries in it: {n}. They will not be lost, they move to the main list.").format(name=self.group, n=count)):
            return
        self.vault.remove_group(self.group)
        self.group = MAIN_GROUP
        self._refresh_group_widgets()
        self._on_group_select()
        self.edited()

    def _show_context_menu(self, event: tk.Event) -> None:
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        self._on_select()
        menu = PopupMenu(self.app, tearoff=False)
        menu.add_command(label=_("Up\tAlt+↑"), command=lambda: self.move_entry(-1))
        menu.add_command(label=_("Down\tAlt+↓"), command=lambda: self.move_entry(+1))
        menu.add_separator()
        move_to = PopupMenu(menu, tearoff=False)
        for label in self._group_values():
            group = self._group_from_label(label)
            move_to.add_command(
                label=label,
                state="disabled" if self.current is not None and group == self.current.group else "normal",
                command=lambda g=group: self.current is not None and self.set_entry_group(self.current, g),
            )
        move_to.add_separator()
        move_to.add_command(label=_("New list…"), command=self._move_to_new_group)
        menu.add_cascade(label=_("Move to list"), menu=move_to)
        menu.add_command(label=_("Make a bookmark…"), command=lambda: self.current is not None and self.app.page("bookmarks").from_entry(self.current))
        menu.add_separator()
        menu.add_command(label=_("Delete\tDelete"), command=self.delete_entry)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _move_to_new_group(self) -> None:
        entry = self.current
        if self.vault is None or entry is None:
            return
        name = ask_text(self.app, _("New list"), _("List name"))
        if name is None:
            return
        try:
            group = self.vault.add_group(name)
        except ValueError as exc:
            show_warning(self.app, str(exc))
            return
        self._refresh_group_widgets()
        self.set_entry_group(entry, group)

    def _on_note_change(self, _event=None) -> None:
        if not self.note.edit_modified():
            return
        self.note.edit_modified(False)
        if self._loading or self.current is None:
            return
        self.current.note = self.note.get("1.0", "end-1c")
        self.edited()

    def _tied_to_bookmark(self, entry: Entry) -> bool:
        return self.vault is not None and any(b.entry_id == entry.id for b in self.vault.bookmarks.items)
