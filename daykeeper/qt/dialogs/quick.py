"""The quick window of the Qt window: a narrow card on top of
everything, opened by the hotkey for the whole computer or Ctrl+K inside the app. A line
to type in and up to eight results; Enter does what the result is for, → or Tab lists
its other actions, Esc or a click elsewhere hides it. A line that reads as a journal entry
(“pass DEMO-12”) shows what would be filed instead of results, and Enter files it.

The index is built when the window opens and dropped when it hides (quick.index): no
secret value is in it, a secret is unsealed only by the action that copies it, behind the
Passwords guard. `QuickAccess` is the part kept with the main window: the hotkey (a
thread of hotkey.WindowsHotkey that wakes the GUI thread through a Waker), the recent
choices and what every action does; the window itself is made on first use."""

from __future__ import annotations

import time
import webbrowser
from typing import Any

import shiboken6
from PySide6.QtCore import QEvent, QObject, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QKeyEvent, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from ... import templates as tpl, totp
from ...bookmarks import entry_login, entry_secret, entry_url
from ...hotkey import IS_WINDOWS, WindowsHotkey, parse_combo
from ...i18n import _
from ...journal import today
from ...journal_layout import current as current_layout
from ...quick import ACTION_LABELS, Item, QuickEntry, index, parse_entry, rank, write_entry
from .. import icons, theme
from ..errors import guard_events
from ..jobs import Waker

DEFAULT_HOTKEY = "ctrl+shift+space"
LOCAL_SHORTCUT = "Ctrl+K"  # inside the app (one line of the window's shortcut table)
RECENT_KEEP = 30
ROWS = 8
WIDTH = 640  # px before the text scale
ROW_H = 46  # px before the text scale
WEB = ("https://", "http://")

Hotkey = WindowsHotkey  # what start_hotkey makes; the tests put a fake one here
HOTKEY_SUPPORTED = IS_WINDOWS  # elsewhere the setting is hidden and only Ctrl+K opens the window

KIND_ICONS = {"locked": "padlock", "entry": "key", "bookmark": "bookmark", "note": "note", "routine": "bolt", "template": "template", "command": "slash"}
COMMAND_ICONS = {"lock": "padlock", "digest": "bell", "today": "journal", "clip": "clipboard", "settings": "gear"}
ACTION_ICONS = {
    "copy_password": "key", "copy_login": "user", "copy_code": "shield", "open_site": "external",
    "open": "external", "run": "play", "fill": "template", "show": "eye", "unlock": "padlock",
}
# commands another part of the window provides; absent ones stay out of the results
OPTIONAL_COMMANDS: dict[str, tuple[str, ...]] = {
    "digest": ("show_digest", "digest_dialog", "open_digest"),
    "clip": ("show_clip_history", "clip_history", "clipboard_history", "clip_history_dialog"),
}


def quick_settings(settings: dict) -> dict:
    value = settings.get("quick")
    return dict(value) if isinstance(value, dict) else {}


def _window_method(shell: Any, command: str) -> Any:
    for name in OPTIONAL_COMMANDS.get(command, ()):
        fn = getattr(shell, name, None)
        if callable(fn):
            return fn
    return None


def totp_code(entry: Any, now: float) -> str:
    """The 2FA code of `entry` at `now`; the key is unsealed for this call only."""
    f = next((f for f in entry.fields if totp.is_totp_field(f.key) and f.filled and not f.unreadable), None)
    t = totp.parse(f.value) if f is not None else None
    return totp.code(t, now) if t is not None else ""


class QuickAccess:
    """Made once with the main window and kept through a lock."""

    def __init__(self, shell: Any):
        self.shell = shell
        self.window: QuickWindow | None = None
        self.hotkey: Any = None
        self.problem = ""  # why the hotkey is not working, for the settings
        self._wake = Waker(self.on_hotkey, owner=shell)

    # ---- settings

    def settings(self) -> dict:
        return quick_settings(self.shell.session.settings())

    def combo(self) -> str:
        value = self.settings().get("hotkey")
        return value.strip() if isinstance(value, str) and value.strip() else DEFAULT_HOTKEY

    def recent(self) -> list[str]:
        value = self.settings().get("recent")
        return [k for k in value if isinstance(k, str)] if isinstance(value, list) else []

    def _write(self, **update: Any) -> bool:
        return bool(self.shell.session.write_settings({"quick": {**self.settings(), **update}}))

    def remember(self, key: str) -> None:
        self._write(recent=([key] + [k for k in self.recent() if k != key])[:RECENT_KEEP])

    def clear_recent(self) -> None:
        self._write(recent=[])

    # ---- the hotkey

    def start_hotkey(self) -> str:
        """Register the combination of the settings; "" or the problem in words."""
        self.stop_hotkey()
        if not HOTKEY_SUPPORTED or not getattr(self.shell, "background_jobs", True):  # a preview copy takes no key from the computer
            self.problem = ""
            return ""
        hotkey = Hotkey(self.combo(), notify=self._wake)
        self.problem = hotkey.start()
        self.hotkey = None if self.problem else hotkey
        return self.problem

    def stop_hotkey(self) -> None:
        hotkey, self.hotkey = self.hotkey, None
        if hotkey is not None:
            hotkey.stop()

    def set_combo(self, text: str) -> str:
        """A new combination from the settings: saved and registered in place of the old one
        when it can be read; "" or the problem in words."""
        combo = "+".join(p.strip().lower() for p in text.split("+"))
        if parse_combo(combo) is None:
            return _("This key combination cannot be read: {combo}").format(combo=text.strip())
        if not self._write(hotkey=combo):
            return _("Could not save the settings")
        return self.start_hotkey()

    def on_hotkey(self) -> None:
        """A press of the hotkey, on the GUI thread (the Waker)."""
        if self.shell.vault is None:
            self.bring_main()
            self.shell.show_unlock()
            return
        self.show()

    def close(self) -> None:
        """The main window closes: the hotkey is given back to Windows."""
        self.stop_hotkey()
        self._wake.close()
        win, self.window = self.window, None
        if win is not None and shiboken6.isValid(win):
            win.close()
            win.deleteLater()

    # ---- the window

    def show(self) -> None:
        if self.shell.vault is None:
            self.bring_main()
            return
        if self.window is None or not shiboken6.isValid(self.window):
            self.window = QuickWindow(self)
        self.window.open()

    def bring_main(self) -> None:
        win = self.shell
        if win.isMinimized():
            win.showNormal()
        win.show()
        win.raise_()
        win.activateWindow()

    def secrets_open(self) -> bool:
        return bool(self.shell.security.secrets_open())

    def items(self) -> list[Item]:
        """The index of this opening; while the Passwords guard is down it holds no vault
        entry at all (the window shows the unlock row instead)."""
        vault = self.shell.vault
        if vault is None:
            return []
        from .settings_templates import place_templates

        found = index(vault, place_templates(self.shell, "quick"), current_layout(), today(), secrets=self.secrets_open())
        return [i for i in found if i.kind != "command" or i.id not in OPTIONAL_COMMANDS or _window_method(self.shell, i.id) is not None]

    # ---- what the actions do

    def file_entry(self, entry: QuickEntry) -> int:
        """Write a quick journal entry into the open vault; one undo step."""
        vault = self.shell.vault
        if vault is None:
            return 0
        before = list(vault.journal.days[entry.date].section(entry.status)) if entry.section == "column" and entry.date in vault.journal.days else []
        added = write_entry(entry, vault.journal)
        if added:
            self.shell.mark_dirty("journal")
            capture = getattr(self.shell, "_capture", None)
            if capture is not None:
                capture.flush()  # its own undo step at once, not merged with what follows
            page = self.shell.pages.get("journal")
            if page is not None:
                page.refresh()
            self.shell.set_status(_("Added to the journal: {what}").format(what=entry.label))
            jira = getattr(self.shell, "jira", None)
            if jira is not None and entry.section == "column":
                new = [x for x in vault.journal.days[entry.date].section(entry.status) if x not in before]
                jira.offer_verdict(entry.status, new)
        else:
            self.shell.set_status(_("Already in the journal: {what}").format(what=entry.label))
        return added

    def run(self, item: Item, action: str | None) -> None:
        """Do `action` of `item` (the window is already hidden)."""
        if action is None or self.shell.vault is None:
            return
        self.remember(item.key)
        handler = getattr(self, f"_run_{item.kind}", None)
        if handler is not None:
            handler(item.id, action)

    def unlock(self) -> None:
        """The unlock row: the guard's code over the main window, then the window again
        with the entries in it."""
        self._secret(self.show)

    def _secret(self, then: Any) -> None:
        """`then` once the Passwords guard is open: the prompt asks over the main window."""
        security = self.shell.security
        if not security.secrets_open():
            self.bring_main()
        security.require_secrets(then)

    def _copy_from_entry(self, entry: Any, action: str) -> bool:
        if action == "copy_password":
            self._secret(lambda: self.shell.copy_to_clipboard(entry_secret(entry), secret=True))
        elif action == "copy_login":
            self.shell.copy_to_clipboard(entry_login(entry), secret=False)
        elif action == "copy_code":
            self._secret(lambda: self._copy_code(entry))
        else:
            return False
        return True

    def _copy_code(self, entry: Any) -> None:
        code = totp_code(entry, time.time())
        if not code:
            self.shell.set_status(_("The 2FA key of this entry is not a key Daykeeper can read"))
            return
        self.shell.copy_to_clipboard(code, secret=True)

    def _open_web(self, url: str) -> bool:
        if not url.lower().startswith(WEB):
            self.shell.set_status(_("Only web addresses open from the quick window"))
            return False
        webbrowser.open(url)
        return True

    def _run_entry(self, ident: str, action: str) -> None:
        entry = self.shell.vault.entry_by_id(ident)
        if entry is None or self._copy_from_entry(entry, action):
            return
        if action == "open_site":
            self._open_web(entry_url(entry))
        elif action == "show":
            self.bring_main()
            self.shell.show_page("vault")
            self.shell.page("vault").select_entry(entry)  # asks the guard's code itself

    def _run_bookmark(self, ident: str, action: str) -> None:
        vault = self.shell.vault
        bm = next((b for b in vault.bookmarks.items if b.id == ident), None)
        if bm is None:
            return
        if action == "open":
            if self._open_web(bm.url):
                bm.opened = time.time()
                self.shell.mark_dirty(None)
        elif action == "show":
            self.bring_main()
            self.shell.show_page("bookmarks")
            self.shell.page("bookmarks")._refresh_groups(select=f"g:{bm.group}" if bm.group else None)
        else:
            entry = vault.entry_by_id(bm.entry_id) if bm.entry_id else None
            if entry is not None:
                self._copy_from_entry(entry, action)

    def _run_note(self, ident: str, action: str) -> None:
        note = next((n for n in self.shell.vault.notes.notes if n.id == ident), None)
        if note is None:
            return
        self.bring_main()
        self.shell.show_page("notes")
        page = self.shell.page("notes")
        page.refresh_days(select=note.date)
        card = getattr(page, "cards", {}).get(note.id)
        if card is not None:
            card.setFocus()

    def _run_routine(self, ident: str, action: str) -> None:
        r = next((r for r in self.shell.vault.routines.ordered() if r.id == ident), None)
        if r is None:
            return
        if action == "run" and self.shell.runs is not None:
            self.shell.runs.run_routine(r)
        elif action == "show":
            self.bring_main()
            self.shell.show_page("routines")

    def _run_template(self, ident: str, action: str) -> None:
        from .settings_templates import place_templates, use_template

        t = next((t for t in place_templates(self.shell, "quick") if t.id == ident), None)
        if t is None:
            return
        asks = any(m.group(1).startswith("?") or m.group(1).partition(":")[0] in ("key", "key_url") for m in tpl.PLACEHOLDER_RE.finditer(t.text))
        if asks:
            self.bring_main()  # its form opens over the main window
        use_template(self.shell, t, {})

    def _run_command(self, ident: str, action: str) -> None:
        shell = self.shell
        if ident == "lock":
            shell.lock()
        elif ident == "today":
            self.bring_main()
            shell.show_page("journal")
            shell.page("journal").open_day(today())
        elif ident == "settings":
            self.bring_main()
            shell.settings_dialog()
        else:
            fn = _window_method(shell, ident)
            if fn is not None:
                fn()


@guard_events
class ResultsView(QWidget):
    """The rows under the line: a drawn icon, the title and a quieter second line. Painted
    in one widget (eight rows need no item views)."""

    def __init__(self, owner: QuickWindow, parent: QWidget):
        super().__init__(parent)
        self.owner = owner
        self.rows: list[tuple[str, str, str]] = []  # (icon, title, subtitle)
        self.current = 0
        self.empty_text = ""
        self.row_h = theme.px(ROW_H)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set_rows(self, rows: list[tuple[str, str, str]], current: int = 0) -> None:
        self.rows = rows
        self.current = max(0, min(current, len(rows) - 1))
        self.setFixedHeight(self.row_h * max(1, len(rows)))
        self.update()

    def step(self, step: int) -> None:
        if self.rows:
            self.current = (self.current + step) % len(self.rows)
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        if not self.rows:
            p.setPen(QColor(t.MUTED))
            p.setFont(theme.font(10))
            p.drawText(QRect(0, 0, w, self.row_h), Qt.AlignmentFlag.AlignCenter, self.empty_text)
            p.end()
            return
        title_font, sub_font = theme.font(11), theme.font(9)
        p.setFont(title_font)
        tm = p.fontMetrics()
        p.setFont(sub_font)
        sm = p.fontMetrics()
        icon = theme.px(20)
        pad = theme.px(12)
        for n, (name, title, sub) in enumerate(self.rows):
            y = n * self.row_h
            selected = n == self.current
            if selected:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(t.ACCENT_SOFT))
                p.drawRoundedRect(QRect(4, y + 2, w - 8, self.row_h - 4), 8, 8)
            icons.paint(p, name, QRect(pad, y, icon, self.row_h), t.ACCENT_INK if selected else t.MUTED, size=icon)
            x = pad + icon + pad
            room = w - x - pad
            if sub:
                top = y + (self.row_h - tm.height() - sm.height()) // 2
                p.setFont(title_font)
                p.setPen(QColor(t.TEXT))
                p.drawText(QRect(x, top, room, tm.height()), Qt.AlignmentFlag.AlignVCenter, tm.elidedText(title, Qt.TextElideMode.ElideRight, room))
                p.setFont(sub_font)
                p.setPen(QColor(t.MUTED))
                p.drawText(QRect(x, top + tm.height(), room, sm.height()), Qt.AlignmentFlag.AlignVCenter, sm.elidedText(sub, Qt.TextElideMode.ElideRight, room))
            else:
                p.setFont(title_font)
                p.setPen(QColor(t.TEXT))
                p.drawText(QRect(x, y, room, self.row_h), Qt.AlignmentFlag.AlignVCenter, tm.elidedText(title, Qt.TextElideMode.ElideRight, room))
        p.end()

    def _row_at(self, y: float) -> int:
        n = int(y // self.row_h)
        return n if 0 <= n < len(self.rows) else -1

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        n = self._row_at(event.position().y())
        if n >= 0 and n != self.current:
            self.current = n
            self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        n = self._row_at(event.position().y())
        if event.button() == Qt.MouseButton.LeftButton and n >= 0:
            self.current = n
            self.owner.activate()


@guard_events
class QuickWindow(QWidget):
    """The card itself; made once, filled at every opening, emptied when it hides."""

    def __init__(self, access: QuickAccess):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)  # the rounded corners show what is under them
        self.setObjectName("QuickWindow")
        self.access = access
        self.shell = access.shell
        self.items: list[Item] = []
        self.shown: list[Item] = []
        self.recent: list[str] = []
        self.entry: QuickEntry | None = None
        self.mode = "results"  # "results" | "actions" | "entry"
        self.action_item: Item | None = None
        self.locked = False  # the Passwords guard was down when the index was built
        self.card = QFrame(self)
        self.card.setObjectName("Quick")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.card)
        col = QVBoxLayout(self.card)
        col.setContentsMargins(theme.px(8), theme.px(8), theme.px(8), theme.px(8))
        col.setSpacing(theme.px(4))
        row = QHBoxLayout()
        row.setContentsMargins(theme.px(8), 0, theme.px(4), 0)
        self.glass = QLabel(self.card)
        self.glass.setFixedSize(theme.px(20), theme.px(20))
        row.addWidget(self.glass)
        self.edit = QLineEdit(self.card)
        self.edit.setObjectName("QuickInput")
        self.edit.setFont(theme.font(14))
        self.edit.textChanged.connect(self._typed)
        self.edit.installEventFilter(self)
        row.addWidget(self.edit, 1)
        col.addLayout(row)
        line = QFrame(self.card)
        line.setObjectName("QuickDivider")
        line.setFixedHeight(1)
        col.addWidget(line)
        self.view = ResultsView(self, self.card)
        col.addWidget(self.view)
        self.hint = QLabel(self.card)
        self.hint.setObjectName("QuickHint")
        self.hint.setContentsMargins(theme.px(8), 0, theme.px(8), 0)
        col.addWidget(self.hint)

    # ---- opening and hiding

    def _style(self) -> None:
        t = theme.current()
        self.card.setStyleSheet(
            f"QFrame#Quick {{ background: {t.CARD}; border: 1px solid {t.BORDER}; border-radius: 12px; }}"
            f"QLineEdit#QuickInput {{ background: transparent; border: none; color: {t.TEXT}; padding: 6px 2px; }}"
            f"QFrame#QuickDivider {{ background: {t.BORDER}; border: none; }}"
            f"QLabel#QuickHint {{ color: {t.MUTED}; background: transparent; border: none; font-size: {theme.font_pt(9)}pt; }}"
        )
        self.glass.setPixmap(icons.plain("search").pixmap(theme.px(20), theme.px(20)))
        self.edit.setPlaceholderText(_("Search passwords, bookmarks, notes, routines… or type a journal entry"))

    def open(self) -> None:
        """Build the index, place the card over the screen of the main window, take the focus."""
        self._style()
        self.locked = not self.access.secrets_open()
        self.items = self.access.items()
        self.recent = self.access.recent()
        self.edit.blockSignals(True)
        self.edit.clear()
        self.edit.blockSignals(False)
        self._typed("")
        register = getattr(self.shell, "register_child", None)
        if register is not None:
            register(self)  # the lock hides it (the register is emptied at every lock)
        self.place()
        self.show()
        self.raise_()
        self.activateWindow()
        self.edit.setFocus()

    def place(self) -> None:
        screen = self.shell.screen() if shiboken6.isValid(self.shell) else None
        screen = screen or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        w = min(theme.px(WIDTH), area.width() - 40)
        self.setFixedWidth(w)
        self.adjustSize()
        self.move(area.x() + (area.width() - w) // 2, area.y() + area.height() // 6)

    def fit(self) -> None:
        """The height follows the rows at once (the layout would wait for the next event)."""
        for lay in (self.card.layout(), self.layout()):
            if lay is not None:
                lay.activate()
        self.resize(self.width(), self.sizeHint().height())

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.items, self.shown, self.entry, self.action_item = [], [], None, None  # the index is never kept
        self.view.set_rows([])
        self.edit.blockSignals(True)
        self.edit.clear()
        self.edit.blockSignals(False)

    def on_lock(self) -> None:
        self.hide()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isVisible() and not self.isActiveWindow():
            self.hide()  # a click elsewhere

    # ---- what is shown

    def _typed(self, text: str) -> None:
        self.mode, self.action_item = "results", None
        self.entry = parse_entry(text, current_layout(), today()) if text.strip() else None
        if self.entry is not None:
            self.mode = "entry"
            self.shown = []
            self.view.set_rows([("journal", self.entry.label, _("Enter — add to the journal"))])
            self.hint.setText(_("Enter — add to the journal · Esc — close"))
        else:
            self.shown = rank(self.items, text, self.recent, ROWS - 1 if self.locked else ROWS)
            if self.locked:
                self.shown.insert(0, self._unlock_item())
            self.view.empty_text = _("Nothing found")
            self.view.set_rows([self._row(i) for i in self.shown])
            self.hint.setText(_("↑↓ — choose · Enter — {action} · → — more actions · Esc — close").format(action=self._default_label()))
        self.fit()

    @staticmethod
    def _unlock_item() -> Item:
        return Item("locked", "unlock", _("Passwords are locked — Enter to unlock"), "", [], ["unlock"])

    @staticmethod
    def _row(item: Item) -> tuple[str, str, str]:
        name = COMMAND_ICONS.get(item.id, "slash") if item.kind == "command" else KIND_ICONS.get(item.kind, "plain")
        return name, item.title, item.subtitle

    def _default_label(self) -> str:
        item = self.selected()
        return _(ACTION_LABELS.get(item.actions[0], "Run")).lower() if item is not None and item.actions else _("Run").lower()

    def selected(self) -> Item | None:
        if self.mode != "results" or not self.shown:
            return None
        return self.shown[self.view.current]

    def show_actions(self) -> bool:
        item = self.selected()
        if item is None or not item.actions:
            return False
        self.mode, self.action_item = "actions", item
        self.view.set_rows([(ACTION_ICONS.get(a, "plain"), _(ACTION_LABELS.get(a, "Run")), item.title) for a in item.actions])
        self.hint.setText(_("Enter — do it · ← — back · Esc — close"))
        self.fit()
        return True

    def back(self) -> None:
        keep = self.shown.index(self.action_item) if self.action_item in self.shown else 0
        self.mode, self.action_item = "results", None
        self.view.set_rows([self._row(i) for i in self.shown], keep)
        self.hint.setText(_("↑↓ — choose · Enter — {action} · → — more actions · Esc — close").format(action=self._default_label()))
        self.fit()

    def activate(self) -> None:
        """Enter (or a click on a row)."""
        if self.mode == "entry":
            entry = self.entry
            self.hide()
            if entry is not None:
                self.access.file_entry(entry)
            return
        action: str | None
        if self.mode == "actions" and self.action_item is not None:
            item, action = self.action_item, self.action_item.actions[self.view.current]
        else:
            chosen = self.selected()
            if chosen is None:
                return
            item, action = chosen, (chosen.actions[0] if chosen.actions else None)
        self.hide()
        if item.kind == "locked":
            self.access.unlock()
            return
        self.access.run(item, action)

    # ---- keys

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is not self.edit or event.type() != QEvent.Type.KeyPress or not isinstance(event, QKeyEvent):
            return False
        key = event.key()
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            self.view.step(1 if key == Qt.Key.Key_Down else -1)
            if self.mode == "results":
                self.hint.setText(_("↑↓ — choose · Enter — {action} · → — more actions · Esc — close").format(action=self._default_label()))
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.activate()
            return True
        if key == Qt.Key.Key_Tab or (key == Qt.Key.Key_Right and self.edit.cursorPosition() == len(self.edit.text()) and self.mode == "results"):
            if self.mode == "results":
                self.show_actions()
            return True
        if self.mode == "actions" and key in (Qt.Key.Key_Left, Qt.Key.Key_Backtab):
            self.back()
            return True
        if key == Qt.Key.Key_Escape:
            if self.mode == "actions":
                self.back()
            else:
                self.hide()
            return True
        return False

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)
