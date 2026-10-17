"""The “Bookmarks” tab: groups on the left, tiles on the right.

A tile opens its address with a click. A tile tied to a vault entry shows the entry's
login and copies its password; while the Passwords tab is locked the password row asks
for the code first. “Open and copy password” does both in one click.

A page of the window (TabPage)."""

from __future__ import annotations

import base64
import time
import tkinter as tk
from tkinter import ttk

from . import siteicon
from .bookmarks import TILE_COLORS, Bookmark, entry_login, entry_secret, entry_url, has_secret, host_of, is_web, normalize_url
from . import palette
from .dialogs import Dialog, ask_text, ask_yes_no, show_error, show_info
from .i18n import _
from .jobs import JobPool
from .bookmark_tile import ICON_SIZE, REVEAL_SEC, BookmarkTile
from .popup import Dropdown, PopupMenu
from .sidebar import SidebarKeeper
from .tabpage import Shell, TabPage, make_scrollable
from .timers import Debounce, Ticker
from .widgets import Placeholder, TileGrid, Tooltip, make_fonts, more_button

ALL = "__all__"
UNGROUPED = "__ungrouped__"


def rounded_rect(canvas: tk.Canvas, x0: float, y0: float, x1: float, y1: float, r: float, **kw) -> int:
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return canvas.create_polygon(pts, smooth=True, **kw)


class BookmarksPage(TabPage):
    """The Bookmarks tab. `_bgroup` is the group on the left (ALL, UNGROUPED or "g:<name>")."""

    name = "bookmarks"

    def __init__(self, app: Shell, parent: tk.Misc):
        self._bloading = False  # the group list is being filled: its selection event is not a click
        self._bsite_on = True  # settings: ask the sites for their icons and colours
        super().__init__(app, parent)

    # ---- what the window asks for

    def refresh(self, keep_scroll: bool = False) -> None:
        self._refresh_bgroups(keep_scroll=keep_scroll)

    def new_item(self, entry=None) -> None:
        """A new bookmark; `entry` — tied to this vault entry (the Passwords context menu)."""
        self.bookmark_dialog(None, entry=entry)

    def focus_search(self) -> None:
        self._bsearch_entry.focus_set()

    def cancel_jobs(self) -> None:
        """The tab is going away: a password hide still waiting would hit its destroyed
        widgets. Revealed passwords are hidden with it; the site answers stop being polled."""
        for hide in self.revealed.values():
            hide.cancel()
        self.revealed.clear()
        self._bsite_poll.stop()
        self._bsite_jobs.close()  # queued site fetches are dropped: their tiles are gone

    def from_entry(self, entry) -> None:
        """A bookmark for a vault entry: its name, its address, tied to it."""
        self.app.show_page("bookmarks")
        self.bookmark_dialog(None, entry=entry)

    # ---- building the tab

    def build(self, frame: ttk.Frame) -> None:
        paned = ttk.PanedWindow(frame, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned, style="Sidebar.TFrame", padding=(16, 16, 12, 12))
        paned.add(left, weight=0)
        self.bsearch_var = tk.StringVar()
        self.bsearch_var.trace_add("write", lambda *_: self._refresh_bookmarks())
        search = ttk.Entry(left, textvariable=self.bsearch_var, font=(palette.FONT, palette.pt(11)))
        search.pack(fill="x", pady=(0, 10))
        self._bsearch_entry = search
        Placeholder(search, _("🔍 Search bookmarks"), palette.FIELD, var=self.bsearch_var, padx=8)
        head = ttk.Frame(left, style="Sidebar.TFrame")
        head.pack(fill="x", pady=(0, 6))
        ttk.Label(head, text=_("GROUPS"), style="SidebarMuted.TLabel", font=(palette.FONT, palette.pt(10), "bold")).pack(side="left")
        more = more_button(head, [(_("New group…"), self._bgroup_new), (_("Rename group…"), self._bgroup_rename), (_("Delete group"), self._bgroup_delete)])
        more.pack(side="right")
        self.bgroups_tree = ttk.Treeview(left, columns=("count",), show="tree", selectmode="browse")
        self.bgroups_tree.column("#0", width=170, stretch=True)
        self.bgroups_tree.column("count", width=50, anchor="e", stretch=False)
        self.bgroups_tree.pack(fill="both", expand=True)
        self.bgroups_tree.bind("<<TreeviewSelect>>", self._on_bgroup_select)
        self.bgroups_tree.bind("<Button-1>", self._on_bgroup_click, add="+")
        btns = ttk.Frame(left, style="Sidebar.TFrame")
        btns.pack(side="bottom", fill="x", pady=(12, 0), before=self.bgroups_tree)  # packed first from the bottom: a short window shrinks the list, not the buttons
        ttk.Button(btns, text=_("+ Bookmark"), style="Primary.TButton", command=lambda: self.bookmark_dialog(None)).pack(side="left")

        host = ttk.Frame(paned, style="Card.TFrame")
        paned.add(host, weight=1)
        SidebarKeeper(paned, minimum=self.app.painter.px(240))
        self.bcanvas, _vsb, self.bgrid = make_scrollable(self.app, host, (24, 20, 24, 20))
        self._btile_grid = TileGrid(self.bgrid)  # lays _blayout out in the columns the width allows
        self._bgroup = ALL
        self.fonts = make_fonts({
            "title": (12, "bold"), "host": (9,), "row": (10,), "mono": (10, "mono"), "avatar": (15, "bold"),
            "section": (10, "bold"), "head": (17, "bold"), "head_count": (13,), "button": (12,),
        })
        self._blayout = []
        self._blines: list[tk.Widget] = []  # the heading, section titles and hints (made anew each time)
        self.revealed: dict[str, Debounce] = {}  # bookmark id → the timer that hides it again
        self._bicons: dict[tuple, tk.PhotoImage] = {}
        self._bsite_jobs = JobPool(workers=3, name="site-icon")  # "site": (SiteInfo, small PNG); "small": PNG
        self._bsite_busy: set[str] = set()  # bookmark ids whose site is being asked
        self._bsmall_busy: set[str] = set()  # bookmark ids whose icon is being shrunk
        self._bsite_poll = Ticker(self.app, 250, self._take_sites)  # runs while any job is out
        self._btiles: dict[str, BookmarkTile] = {}  # bookmark id → its tile on screen

    # ---- groups on the left

    def _refresh_bgroups(self, select: str | None = None, keep_scroll: bool = False) -> None:
        if self.vault is None:
            return
        bms = self.vault.bookmarks
        tree = self.bgroups_tree
        tree.delete(*tree.get_children())
        tree.insert("", "end", iid=ALL, text=_("  ★ All"), values=(str(len(bms.items)) if bms.items else "",))
        ungrouped = bms.in_group("")
        if ungrouped and bms.groups:
            tree.insert("", "end", iid=UNGROUPED, text=_("  Without a group"), values=(str(len(ungrouped)),))
        for g in bms.groups:
            n = len(bms.in_group(g))
            tree.insert("", "end", iid=f"g:{g}", text=f"  {g}", values=(str(n) if n else "",))
        target = select or self._bgroup
        if not tree.exists(target):
            target = ALL
        self._bgroup = target
        self._bloading = True
        try:
            tree.selection_set(target)
        finally:
            self._bloading = False
        self._refresh_bookmarks(keep_scroll=keep_scroll)

    def _on_bgroup_select(self, _event=None) -> None:
        if self._bloading or self.vault is None:
            return
        sel = self.bgroups_tree.selection()
        # Tk delivers <<TreeviewSelect>> after _refresh_bgroups has picked the group itself
        # (and once more when tree.delete dropped the old selection): that group is already
        # on screen, so there is nothing to draw again
        if not sel or sel[0] == self._bgroup:
            return
        self._bgroup = sel[0]
        if self.bsearch_var.get():
            self.bsearch_var.set("")  # its trace draws the tiles
        else:
            self._refresh_bookmarks()

    def _on_bgroup_click(self, event: tk.Event) -> None:
        """A click on the group already selected while a search is on: back to the group."""
        if self.bsearch_var.get() and self.bgroups_tree.identify_row(event.y) == self._bgroup:
            self.bsearch_var.set("")

    def _current_bgroup(self) -> str | None:
        """The group a new bookmark goes to: the selected one, "" for none."""
        if self._bgroup.startswith("g:"):
            return self._bgroup[2:]
        return ""

    def _bgroup_new(self) -> None:
        name = (ask_text(self.app, _("New group"), _("Group name")) or "").strip()
        if not name:
            return
        if name in self.vault.bookmarks.groups:
            show_error(self.app, _("Group “{name}” already exists").format(name=name))
            return
        self.vault.bookmarks.groups.append(name)
        self.app.mark_dirty("bookmarks")
        self._refresh_bgroups(select=f"g:{name}")

    def _bgroup_rename(self) -> None:
        old = self._current_bgroup()
        if not old:
            show_info(self.app, _("Pick a group on the left first."))
            return
        new = (ask_text(self.app, _("Rename group"), _("Group name"), old) or "").strip()
        if not new or new == old:
            return
        try:
            self.vault.bookmarks.rename_group(old, new)
        except ValueError:
            show_error(self.app, _("Group name is empty or taken"))
            return
        self.app.mark_dirty("bookmarks")
        self._refresh_bgroups(select=f"g:{new}")

    def _bgroup_delete(self) -> None:
        name = self._current_bgroup()
        if not name:
            show_info(self.app, _("Pick a group on the left first."))
            return
        if not ask_yes_no(self.app, _("Delete group “{name}”? Its bookmarks stay, without a group.").format(name=name)):
            return
        self.vault.bookmarks.remove_group(name)
        self.app.mark_dirty("bookmarks")
        self._refresh_bgroups(select=ALL)

    # ---- tiles

    def _bookmark_sections(self) -> tuple[str, list[tuple[str, list[Bookmark]]]]:
        """(the heading of the view, [(section title, bookmarks)])."""
        bms = self.vault.bookmarks
        query = self.bsearch_var.get().strip()
        if query:
            return _("Search “{query}”").format(query=query), [("", bms.search(query))]
        if self._bgroup == UNGROUPED:
            return _("Without a group"), [("", bms.in_group(""))]
        if self._bgroup != ALL:
            name = self._bgroup[2:]
            return name, [("", bms.in_group(name))]
        sections = [(g or (_("Without a group") if bms.groups else ""), bms.in_group(g)) for g in ["", *bms.groups]]
        return _("All bookmarks"), [(t, items) for t, items in sections if items]

    def _refresh_bookmarks(self, keep_scroll: bool = False) -> None:
        """Show the bookmarks of the group or search. A tile whose bookmark (and the entry
        it is tied to) reads the same as when it was built is kept; the others are made
        anew, in slices, so a long list shows its first screen at once."""
        if self.vault is None:
            return
        top = self.bcanvas.yview()[0]
        self._bsite_on = self.site_icons_on()
        for widget in self._blines:
            widget.destroy()
        self._blines = []
        query = self.bsearch_var.get().strip()
        heading, sections = self._bookmark_sections()
        self._blayout: list[tuple[str, object]] = []
        self._btile_grid.layout = self._blayout
        total = sum(len(items) for _t, items in sections)
        head = tk.Frame(self.bgrid, bg=palette.CARD)
        tk.Label(head, text=heading, bg=palette.CARD, fg=palette.TEXT, font=self.fonts["head"]).pack(side="left")
        if total:
            tk.Label(head, text=str(total), bg=palette.CARD, fg=palette.MUTED, font=self.fonts["head_count"]).pack(side="left", padx=(10, 0), pady=(4, 0))
        self._blines.append(head)
        self._blayout.append(("line", (head, (0, 14))))
        if not total:
            if not query:
                hint = tk.Label(
                    self.bgrid,
                    text=_("“+ Bookmark” on the left adds a tile: a link to a service, and if you tie it to a password entry — its login and password in one click."),
                    bg=palette.CARD, fg=palette.MUTED, font=self.fonts["row"], justify="left", wraplength=560,
                )
            else:
                hint = tk.Label(self.bgrid, text=_("Nothing found."), bg=palette.CARD, fg=palette.MUTED, font=self.fonts["row"])
            self._blines.append(hint)
            self._blayout.append(("line", (hint, (0, 0))))
        plan: list[tuple[Bookmark, list]] = []
        for title, items in sections:
            if title and len(sections) > 1:
                label = tk.Label(self.bgrid, text=title.upper(), bg=palette.CARD, fg=palette.MUTED, font=self.fonts["section"])
                self._blines.append(label)
                self._blayout.append(("line", (label, (4, 8))))
            tiles: list[tk.Widget] = []
            self._blayout.append(("tiles", tiles))
            plan.extend((bm, tiles) for bm in items)
        wanted = {bm.id for bm, _tiles in plan}
        for bid, card in list(self._btiles.items()):
            if bid not in wanted:
                card.destroy()
                del self._btiles[bid]

        def build(pair: tuple[Bookmark, list]) -> None:
            bm, tiles = pair
            tiles.append(self._tile_for(bm))

        def done() -> None:
            self._btile_grid.restack()
            self.bcanvas.update_idletasks()
            self.bcanvas.yview_moveto(top if keep_scroll else 0)
            self._ask_sites()

        self.app.build_in_chunks("bookmarks", plan, build, self._btile_grid.place, done)

    def _tile_signature(self, bm: Bookmark) -> tuple:
        """Everything a tile shows; the same signature — the same tile will do."""
        entry = self.vault.entry_by_id(bm.entry_id)
        icon = bm.small_icon_at(self.app.painter.px(ICON_SIZE)) if self._bsite_on and not bm.icon.strip() else ""
        return (
            bm.title, bm.host, bm.icon, bm.colour(), icon, bm.url, bm.note,
            entry.id if entry is not None else "", entry.name if entry is not None else "",
            entry_login(entry) if entry is not None else "", has_secret(entry) if entry is not None else False,
            self.app.security.secrets_open(),
        )

    def _tile_for(self, bm: Bookmark) -> tk.Widget:
        """The tile of a bookmark: the one on screen when it still reads the same, else a new one."""
        sig = self._tile_signature(bm)
        card = self._btiles.get(bm.id)
        if card is not None and getattr(card, "sig", None) == sig:
            return card
        if card is not None:
            card.destroy()
        card = self._build_tile(bm)
        card.sig = sig  # type: ignore[attr-defined]
        return card
    def _build_tile(self, bm: Bookmark) -> BookmarkTile:
        """A tile: one canvas (bookmark_tile) — a tile of frames and labels was twenty windows."""
        tile = BookmarkTile(self.bgrid, self, bm)
        self._btiles[bm.id] = tile
        return tile

    def _show_secret(self, bid: str, revealed: bool) -> bool:
        """The password row of one tile shows or hides the password in place; False — the
        tile has no such row right now (locked, not on screen), the tab must be redrawn."""
        tile = self._btiles.get(bid)
        if tile is None or not tile.winfo_exists() or "secret" not in tile._rows():
            return False
        tile.draw()  # it reads `revealed` itself
        return True

    def reveal(self, bm: Bookmark) -> None:
        """The eye shows the password for REVEAL_SEC seconds; a second click hides it at once."""
        if bm.id in self.revealed:
            self._hide_revealed(bm.id)
            return

        def show():
            hide = self.revealed[bm.id] = Debounce(self.app, REVEAL_SEC * 1000, self._hide_revealed)
            hide.touch(bm.id)
            if not self._show_secret(bm.id, True):
                self._refresh_bookmarks(keep_scroll=True)

        self.app.security.require_secrets(show)

    def _hide_revealed(self, bid: str) -> None:
        hide = self.revealed.pop(bid, None)
        if hide is None:
            return
        hide.cancel()
        if not self._show_secret(bid, False):
            self._refresh_bookmarks(keep_scroll=True)

    # ---- the look of the site: its icon and colour, asked in the background

    def site_icons_on(self) -> bool:
        return bool(self.app.session.settings().get("site_icons", True))

    def site_icon(self, bm: Bookmark, size: int) -> tk.PhotoImage | None:
        """The stored site icon at `size`; None — no icon, icons are off, or the icon is not
        shrunk to this size yet (an older file): then it is shrunk in the background and the
        tile is repainted when it is ready; the letter shows meanwhile."""
        if not bm.favicon or not self._bsite_on:
            return None
        small = bm.small_icon_at(size)
        if not small:
            self._ask_small_icon(bm, size)
            return None
        key = (bm.id, hash(small), size)
        if key not in self._bicons:
            try:
                self._bicons[key] = tk.PhotoImage(master=self.app, data=small)
            except tk.TclError:  # a damaged icon: the letter instead
                return None
        return self._bicons[key]

    def _poll_sites(self) -> None:
        """Answers of the background jobs are taken on the window's thread, every 250 ms while any is out."""
        self._bsite_poll.start()

    def _ask_small_icon(self, bm: Bookmark, size: int) -> None:
        """Shrink the stored icon to `size` in the background (an older file has only the big one)."""
        if bm.id in self._bsmall_busy or not self.app.background_jobs:
            return
        self._bsmall_busy.add(bm.id)

        def work(favicon: str = bm.favicon) -> bytes:
            try:
                return siteicon.shrink_png(base64.b64decode(favicon), size)
            except Exception:  # noqa: BLE001 — a damaged icon: the letter stays
                return b""

        self._bsite_jobs.submit("small", work, bid=bm.id, favicon=bm.favicon, size=size)
        self._poll_sites()

    def _ask_sites(self, force: Bookmark | None = None) -> None:
        """Ask the sites of the bookmarks that need it for their icon and colour; the icon
        comes back already shrunk to the badge size."""
        if self.vault is None or not self.app.background_jobs or not self._bsite_on:
            return
        now = time.time()
        wanted = [force] if force is not None else [b for b in self.vault.bookmarks.items if b.needs_site_info(now)]
        size = self.app.painter.px(ICON_SIZE)
        for bm in wanted:
            if bm.id in self._bsite_busy or not bm.url.startswith(("http://", "https://")):
                continue
            self._bsite_busy.add(bm.id)

            def work(url: str = bm.url) -> tuple[siteicon.SiteInfo, bytes]:
                info = siteicon.fetch(url)
                small = b""
                if info.icon:
                    try:
                        small = siteicon.shrink_png(info.icon, size)
                    except Exception:  # noqa: BLE001 — the big icon is kept, the letter shows
                        small = b""
                return info, small

            self._bsite_jobs.submit("site", work, bid=bm.id, url=bm.url)
            self._poll_sites()

    def _take_sites(self) -> bool:
        """Apply what the background jobs brought: the look of a site, or an icon shrunk
        for the badge. Only the badge of that tile is painted again, so nothing else moves
        and no tab is marked for a rebuild. A site's answer is saved; a shrunk icon is only
        kept with the next save. False once nothing is out: the ticker stops."""
        for result in self._bsite_jobs.drain():
            bid = result.meta["bid"]
            (self._bsite_busy if result.kind == "site" else self._bsmall_busy).discard(bid)
            bm = self.vault.bookmarks.get(bid) if self.vault is not None else None
            if bm is None or not result.ok:
                continue  # deleted or locked meanwhile; a job that failed leaves the letter
            if result.kind == "site":
                info, small = result.value
                if not bm.apply_site_info(result.meta["url"], info.icon, info.colour, small, self.app.painter.px(ICON_SIZE), time.time()):
                    continue  # re-addressed meanwhile
                self.app.mark_dirty(None)
            elif not bm.apply_small_icon(result.meta["favicon"], result.value, result.meta["size"]):
                continue  # the icon changed meanwhile, or could not be shrunk
            tile = self._btiles.get(bid)
            if tile is not None and tile.winfo_exists():
                tile.draw()
                tile.sig = self._tile_signature(bm)  # type: ignore[attr-defined]  # the tile now reads the new look
        return bool(self._bsite_busy or self._bsmall_busy)


    def _refresh_site(self, bm: Bookmark) -> None:
        bm.site_checked = 0.0
        self._ask_sites(force=bm)
        self.app.set_status(_("Asking {host} for its icon…").format(host=bm.host))

    def open_bookmark(self, bm: Bookmark, copy_secret: bool = False) -> None:
        if not bm.url:
            show_info(self.app, _("This bookmark has no address."))
            return
        if not is_web(bm.url):
            show_info(self.app, _("A bookmark opens web addresses only (http, https, mailto). A file or a program is started from the Routine tab."))
            return

        def go():
            if copy_secret:
                entry = self.vault.entry_by_id(bm.entry_id)
                if entry is not None and has_secret(entry):
                    self.app.copy_to_clipboard(entry_secret(entry))
            import webbrowser  # 20 ms at start otherwise, for the first click on a tile

            webbrowser.open(bm.url)
            bm.opened = time.time()
            self.app.mark_dirty(None)  # no tile shows the stamp

        if copy_secret:
            self.app.security.require_secrets(go)
        else:
            go()

    def tile_menu(self, bm: Bookmark, event: tk.Event) -> None:
        menu = PopupMenu(self.app, tearoff=False)
        menu.add_command(label=_("Open"), command=lambda: self.open_bookmark(bm))
        menu.add_command(label=_("Copy address"), command=lambda: self.app.copy_to_clipboard(bm.url, secret=False))
        menu.add_command(label=_("Edit…"), command=lambda: self.bookmark_dialog(bm))
        if bm.url.startswith(("http://", "https://")) and self.site_icons_on():
            menu.add_command(label=_("Refresh the site icon"), command=lambda: self._refresh_site(bm))
        menu.add_separator()
        menu.add_command(label=_("Move left"), command=lambda: self._bookmark_move(bm, -1))
        menu.add_command(label=_("Move right"), command=lambda: self._bookmark_move(bm, +1))
        menu.add_separator()
        menu.add_command(label=_("Delete"), command=lambda: self._bookmark_delete(bm))
        menu.tk_popup(event.x_root, event.y_root)

    def _bookmark_move(self, bm: Bookmark, delta: int) -> None:
        if self.vault.bookmarks.move(bm, delta):
            self.app.mark_dirty("bookmarks")
            self._refresh_bookmarks(keep_scroll=True)

    def _bookmark_delete(self, bm: Bookmark) -> None:
        if not ask_yes_no(self.app, _("Delete bookmark “{title}”?").format(title=bm.title or bm.host)):
            return
        self.vault.bookmarks.remove(bm)
        self.app.mark_dirty("bookmarks")
        self._refresh_bgroups()

    # ---- editor

    def bookmark_dialog(self, bm: Bookmark | None, entry=None) -> None:
        """Create (bm is None) or edit a bookmark. `entry` — tie a new bookmark to this vault entry."""
        if self.vault is None:
            return
        bms = self.vault.bookmarks
        dlg = Dialog(self.app, _("New bookmark") if bm is None else _("Bookmark"))
        box = dlg.box
        entries = sorted(self.vault.entries, key=lambda e: e.name.lower())
        none_label = _("— not tied —")
        entry_labels = [none_label] + [f"{e.name}  ·  {e.kind_label}" for e in entries]

        start_entry = entry or (self.vault.entry_by_id(bm.entry_id) if bm else None)
        title_var = tk.StringVar(value=bm.title if bm else (entry.name if entry else ""))
        url_var = tk.StringVar(value=bm.url if bm else (entry_url(entry) if entry else ""))
        group_var = tk.StringVar(value=bm.group if bm else (self._current_bgroup() or ""))
        icon_var = tk.StringVar(value=bm.icon if bm else "")
        color_var = tk.StringVar(value=bm.color if bm else "")
        entry_var = tk.StringVar(value=entry_labels[entries.index(start_entry) + 1] if start_entry in entries else none_label)
        note_var = tk.StringVar(value=bm.note if bm else "")

        rows = [
            (_("Title"), ttk.Entry(box, textvariable=title_var, width=44)),
            (_("Address"), ttk.Entry(box, textvariable=url_var, width=44)),
            (_("Group"), ttk.Combobox(box, textvariable=group_var, values=bms.groups, width=42)),
            (_("Password entry"), Dropdown(box, textvariable=entry_var, values=entry_labels, width=42)),
            (_("Icon"), ttk.Frame(box)),
            (_("Note"), ttk.Entry(box, textvariable=note_var, width=44)),
        ]
        for i, (label, widget) in enumerate(rows):
            ttk.Label(box, text=label).grid(row=i, column=0, sticky="w", padx=(0, 12), pady=4)
            widget.grid(row=i, column=1, sticky="w", pady=4)
        icon_box = rows[4][1]
        ttk.Entry(icon_box, textvariable=icon_var, width=6).pack(side="left")
        ttk.Label(icon_box, text=_("An emoji or one or two letters; empty — the first letter of the title"), style="Muted.TLabel").pack(side="left", padx=(8, 0))
        hint = ttk.Label(box, text="", style="Muted.TLabel")
        hint.grid(row=len(rows), column=1, sticky="w")

        colours = ttk.Frame(box)
        colours.grid(row=len(rows) + 1, column=1, sticky="w", pady=(6, 0))
        ttk.Label(box, text=_("Colour")).grid(row=len(rows) + 1, column=0, sticky="w", padx=(0, 12), pady=(6, 0))
        swatches: dict[str, tk.Canvas] = {}

        def paint():
            for code, canvas in swatches.items():
                canvas.delete("all")
                colour = dict((c, v) for c, _l, v in TILE_COLORS)[code]
                if not code and bm is not None and bm.site_colour:
                    colour = bm.site_colour
                rounded_rect(canvas, 2, 2, 24, 24, 7, fill=colour, outline=palette.TEXT if code == color_var.get() else colour, width=2)
                if not code:  # “the colour of the site”
                    canvas.create_text(13, 13, text="✦", fill=palette.ON_ACCENT, font=(palette.FONT, palette.pt(9), "bold"))

        for code, label, _colour in TILE_COLORS:
            canvas = tk.Canvas(colours, width=26, height=26, bg=palette.BG, highlightthickness=0, cursor="hand2")
            canvas.pack(side="left", padx=(0, 4))
            canvas.bind("<Button-1>", lambda _e, c=code: (color_var.set(c), paint()))
            Tooltip(canvas, _(label))
            swatches[code] = canvas
        paint()

        def suggest(*_args):
            """Tie to an entry whose address has the same host, if the user has not picked one."""
            if entry_var.get() != none_label or not url_var.get().strip():
                hint.config(text="")
                return
            found = bms.suggest_entry(url_var.get(), entries)
            target = self.vault.entry_by_id(found)
            if target is not None:
                entry_var.set(entry_labels[entries.index(target) + 1])
                hint.config(text=_("Tied to “{name}” by the address").format(name=target.name))

        url_var.trace_add("write", suggest)

        def picked_entry(*_):
            label = entry_var.get()
            if label == none_label:
                return None
            target = entries[entry_labels.index(label) - 1]
            if not url_var.get().strip() and entry_url(target):
                url_var.set(entry_url(target))
            if not title_var.get().strip():
                title_var.set(target.name)
            return target

        entry_var.trace_add("write", lambda *_: picked_entry())
        err = ttk.Label(box, text="", style="Error.TLabel")
        err.grid(row=len(rows) + 2, column=0, columnspan=2)

        def save(_e=None):
            url = normalize_url(url_var.get())
            title = title_var.get().strip() or host_of(url)
            if not title and not url:
                err.config(text=_("Fill in a title or an address"))
                return
            target = picked_entry()
            fields = {
                "title": title, "url": url, "group": group_var.get(), "icon": icon_var.get().strip(),
                "color": color_var.get(), "entry_id": target.id if target else "", "note": note_var.get().strip(),
            }
            if bm is None:
                group = fields.pop("group")
                created = bms.add(fields.pop("title"), fields.pop("url"), group, **fields)
                select = f"g:{created.group}" if created.group else (ALL if self._bgroup == ALL else ALL)
            else:
                bms.update(bm, **fields)
                select = None
            self.app.mark_dirty("bookmarks")
            dlg.destroy()
            self._refresh_bgroups(select=select)

        dlg.editor_buttons(len(rows) + 3, 2, (14, 0), save, delete=(lambda: (dlg.destroy(), self._bookmark_delete(bm))) if bm is not None else None)
        dlg.keys(save)
        rows[0][1].focus_set()
        if entry is not None:
            suggest()
