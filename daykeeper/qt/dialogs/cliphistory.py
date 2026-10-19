"""The clipboard history in the Qt window: the watch that records what is copied while the
vault is open (cliphistory.ClipHistory, sealed with the vault's field key), the button on
the top bar and the history window behind it (also opened by /clip of the quick search).

Off until the user turns it on (Settings → Security). Never recorded: a text carrying the
"keep out of history" mark (Daykeeper's own secret copies, other password managers'),
a secret Daykeeper itself just copied (by its hash: macOS has no mark), a token-looking
text when that setting is on, anything while the vault is locked or the watch is paused.

Windows: every POLL_MS the clipboard's change counter (opens nothing); the text is read
only when the counter moved. Elsewhere: the Qt clipboard text once a second, compared
with the hash of the last one seen. The hash lives in memory only."""

from __future__ import annotations

import hashlib
import sys
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import shiboken6
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QToolButton, QVBoxLayout, QWidget

from ... import clipboard
from ...cliphistory import ClipItem, looks_secret, usable
from ...clipboard import SecretClipboard
from ...i18n import _
from .. import icons
from ..errors import guard_events
from ..timers import Ticker
from .base import button, label, paint_caption, register

POLL_MS = 500  # the Windows counter is cheap: twice a second
SLOW_EVERY = 2  # elsewhere the text itself is read: every second tick, once a second
SECRETS_KEPT = 8  # hashes of the last secrets Daykeeper copied, never recorded
LINE_CHARS = 90  # the first line shown in a row, at most
MASK = "••••••••"
ITEM_PADDING = 12  # px: the style sheet's padding of a list item, above and below, taken from the row


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def first_line(text: str) -> str:
    """The first non-empty line, cut to LINE_CHARS; a token-looking text shows its first
    characters only (the window may be open while someone looks at the screen)."""
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), text.strip())
    if looks_secret(text):
        return line[:4] + MASK
    return line if len(line) <= LINE_CHARS else line[: LINE_CHARS - 1] + "…"


def when(ts: float, now: float | None = None) -> str:
    """The time of a row: the clock today, the day and the clock before that."""
    t, today = time.localtime(ts), time.localtime(now if now is not None else time.time())
    same_day = (t.tm_year, t.tm_yday) == (today.tm_year, today.tm_yday)
    return time.strftime("%H:%M" if same_day else "%d.%m %H:%M", t)


class ClipWatch:
    """Made once with the window. `refresh()` after unlock and after the settings change,
    `on_lock()` at a lock, `note_secret(digest)` for every secret the window copies.

    The clipboard functions are attributes so a test puts fakes here: a test must never
    read the real clipboard."""

    def __init__(self, window: Any):
        self.window = window
        self.paused = False
        self.dialog: ClipHistoryWindow | None = None
        self.windows = sys.platform == "win32"
        self.sequence_number: Callable[[], int | None] = clipboard.sequence_number
        self.excluded: Callable[[], bool] = clipboard.excluded_from_history
        self.read_system: Callable[[], str | None] = clipboard.read_text
        self.read_qt: Callable[[], str | None] = lambda: window._qt_clip_get()
        self.clock: Callable[[], float] = time.time
        self._ticker = Ticker(window, POLL_MS, self.tick)
        self._seq: int | None = None
        self._seen: str | None = None  # hash of the text last seen (the slow way)
        self._ticks = 0
        self._secrets: deque[str] = deque(maxlen=SECRETS_KEPT)
        self._ignore: str | None = None  # hash of a text the history window copied back

    # ---- state

    @property
    def vault(self) -> Any:
        return self.window.vault

    def enabled(self) -> bool:
        """Recording is on: the vault is open, the setting is on, and this is not a preview
        copy (a backup looked at on its own must not fill up beside the real window)."""
        return self.vault is not None and bool(self.vault.security.clip_history) and bool(self.window.background_jobs)

    def refresh(self) -> None:
        """Start or stop the ticks by the setting; the top-bar button follows."""
        if self.enabled():
            if not self._ticker.running:
                self._prime()
                self._ticker.start()
        else:
            self._ticker.stop()
        self.window.update_clip_button()

    def on_lock(self) -> None:
        self._ticker.stop()
        self._seq, self._seen = None, None
        self._secrets.clear()
        self._ignore = None
        self.close_dialog()

    def set_paused(self, paused: bool) -> None:
        """Pause: nothing is recorded; what was copied during the pause is not picked up
        after it either."""
        self.paused = bool(paused)
        if not self.paused:
            self._prime()

    def note_secret(self, digest: str) -> None:
        """The window copied a secret with this hash (SecretClipboard.digest)."""
        self._secrets.append(digest)

    def ignore_once(self, text: str) -> None:
        """The history window put this text back: not recorded again as new."""
        self._ignore = SecretClipboard.digest(text)

    def _prime(self) -> None:
        """What is on the clipboard now counts as seen: it was copied before (locked, paused)."""
        self._ticks = 0
        seq = self.sequence_number() if self.windows else None
        self._seq = seq
        if seq is None:
            text = self._read()
            self._seen = _hash(text) if text else None

    def _read(self) -> str | None:
        if self.windows:
            return self.read_system()
        return self.read_qt()

    # ---- the tick

    def tick(self) -> None:
        if not self.enabled():
            self._ticker.stop()
            return
        if self.paused or not usable(self.vault.clip_sealer):
            return
        seq = self.sequence_number() if self.windows else None
        if seq is not None:
            if seq == self._seq:
                return
            self._seq = seq
            if self.excluded():
                return
            text = self.read_system()
        else:
            self._ticks += 1
            if self._ticks % SLOW_EVERY:
                return
            if self.windows and self.excluded():
                return
            text = self._read()
            seen = _hash(text) if text else None
            if seen == self._seen:
                return
            self._seen = seen
        if text:
            self.record(text)

    def record(self, text: str) -> bool:
        """Put `text` into the history unless a rule says no; True when recorded."""
        vault = self.vault
        if vault is None or not text:
            return False
        digest = SecretClipboard.digest(text)
        if digest in self._secrets:
            return False
        if digest == self._ignore:
            self._ignore = None
            return False
        prefs = vault.security
        if prefs.clip_skip_secrets and looks_secret(text):
            return False
        if not vault.clipboard.add(text, vault.clip_sealer, self.clock(), prefs.clip_limit):
            return False
        self.window.save_later()  # saved with the vault; not an undo step
        if self.dialog is not None and shiboken6.isValid(self.dialog):
            self.dialog.reload()
        return True

    # ---- the window

    def open(self) -> ClipHistoryWindow | None:
        """The history window; behind the Passwords guard it opens after the code (None then):
        what other apps copied may be a password too."""
        if self.vault is None:
            return None
        security = getattr(self.window, "security", None)
        if security is not None and not security.secrets_open():
            security.require_secrets(self.open)
            return None
        dlg = self.dialog
        if dlg is not None and shiboken6.isValid(dlg) and dlg.isVisible():
            dlg.reload()
            dlg.raise_()
            dlg.activateWindow()
            return dlg
        self.dialog = ClipHistoryWindow(self.window, self)
        self.dialog.show()
        return self.dialog

    def close_dialog(self) -> None:
        dlg, self.dialog = self.dialog, None
        if dlg is not None and shiboken6.isValid(dlg):
            dlg.close()


@guard_events
class ClipRow(QWidget):
    """One row: the first line, the time and the length under it, pin and delete."""

    def __init__(self, parent: QWidget, owner: ClipHistoryWindow, item: ClipItem, text: str):
        super().__init__(parent)
        self.item = item
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 2, 0, 2)
        row.setSpacing(6)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title = label(first_line(text), "", self)
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.meta = label(when(item.ts) + " · " + _("{n} chars").format(n=len(text)), "muted", self)
        col.addWidget(self.title)
        col.addWidget(self.meta)
        row.addLayout(col, 1)
        self.pin = QToolButton(self)
        self.pin.setAutoRaise(True)
        self.pin.setCheckable(True)
        self.pin.setChecked(item.pinned)
        self.pin.setIcon(icons.button("pin" if item.pinned else "pin_off"))
        self.pin.setIconSize(QSize(16, 16))
        self.pin.setToolTip(_("Unpin") if item.pinned else _("Pin: stays when the history is cleared or full"))
        self.pin.clicked.connect(lambda _c=False: owner.toggle_pin(item))
        self.delete = QToolButton(self)
        self.delete.setAutoRaise(True)
        self.delete.setIcon(icons.button("trash", "danger"))
        self.delete.setIconSize(QSize(16, 16))
        self.delete.setToolTip(_("Delete"))
        self.delete.clicked.connect(lambda _c=False: owner.delete(item))
        row.addWidget(self.pin)
        row.addWidget(self.delete)


@guard_events
class ClipHistoryWindow(QDialog):
    """The history: a search box, the list (click — copy back), Pause and Clear all.
    Not modal; closes on lock (registered with the window). Texts are opened only to be
    shown; nothing opened is kept beyond the rows on screen."""

    def __init__(self, shell: Any, watch: ClipWatch):
        super().__init__(shell)
        self.shell, self.watch = shell, watch
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setWindowTitle(_("Clipboard history"))
        self.setModal(False)
        register(shell, self)
        col = QVBoxLayout(self)
        col.setContentsMargins(16, 16, 16, 16)
        col.setSpacing(8)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText(_("Search the history"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self.reload())
        col.addWidget(self.search)
        self.note = label("", "muted", self)
        self.note.setWordWrap(True)
        col.addWidget(self.note)
        self.list = QListWidget(self)
        self.list.setObjectName("ClipList")
        self.list.setUniformItemSizes(False)
        self.list.itemClicked.connect(self._picked)
        self.list.itemActivated.connect(self._picked)
        col.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.pause = button(_("Pause"), "", self)
        self.pause.setCheckable(True)
        self.pause.setIcon(icons.plain("pause"))
        self.pause.setToolTip(_("Stop recording for now; what is copied meanwhile is not picked up later"))
        self.pause.toggled.connect(self._pause_toggled)
        self.clear_btn = button(_("Clear all"), "danger", self)
        self.clear_btn.setToolTip(_("Forget every text; pinned ones stay"))
        self.clear_btn.clicked.connect(self.clear_all)
        close = button(_("Close"), "", self)
        close.clicked.connect(self.close)
        row.addWidget(self.pause)
        row.addWidget(self.clear_btn)
        row.addStretch(1)
        row.addWidget(close)
        col.addLayout(row)
        fm = self.fontMetrics()
        self.resize(fm.averageCharWidth() * 70 + 40, fm.lineSpacing() * 30)
        self._sync_pause()
        self.reload()
        QTimer.singleShot(0, self.search, self.search.setFocus)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        paint_caption(self)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def on_lock(self) -> None:
        self.list.clear()

    def _sync_pause(self) -> None:
        self.pause.blockSignals(True)
        self.pause.setChecked(self.watch.paused)
        self.pause.blockSignals(False)
        self.pause.setText(_("Resume") if self.watch.paused else _("Pause"))

    def _pause_toggled(self, on: bool) -> None:
        self.watch.set_paused(on)
        self._sync_pause()
        self._show_note()

    def _show_note(self) -> None:
        if not self.watch.enabled():
            text = _("The history is off: Settings → Security.")
        elif self.watch.paused:
            text = _("Paused: nothing is recorded.")
        elif self.list.count() == 0:
            text = _("Nothing here yet: copy a text anywhere and it appears here.") if not self.search.text().strip() else _("Nothing found")
        else:
            text = ""
        self.note.setText(text)
        self.note.setVisible(bool(text))

    def reload(self) -> None:
        """The rows anew from the history, filtered by the search box."""
        vault = self.shell.vault
        self.list.clear()
        if vault is None:
            return
        sealer = vault.clip_sealer
        query = self.search.text().strip().casefold()
        for item in vault.clipboard.items:
            text = item.text(sealer)
            if not text or (query and query not in text.casefold()):
                continue
            entry = QListWidgetItem(self.list)
            row = ClipRow(self.list, self, item, text)
            row.ensurePolished()  # the style sheet's fonts first: a hint from the default font cut the lines
            entry.setSizeHint(row.sizeHint() + QSize(0, ITEM_PADDING))
            self.list.setItemWidget(entry, row)
        self._show_note()

    def rows(self) -> list[ClipRow]:
        out = []
        for i in range(self.list.count()):
            w = self.list.itemWidget(self.list.item(i))
            if isinstance(w, ClipRow):
                out.append(w)
        return out

    def _index(self, item: ClipItem) -> int:
        vault = self.shell.vault
        if vault is None:
            return -1
        return next((i for i, x in enumerate(vault.clipboard.items) if x is item), -1)

    def _picked(self, entry: QListWidgetItem) -> None:
        row = self.list.itemWidget(entry)
        if isinstance(row, ClipRow):
            self.copy_back(row.item)

    def copy_back(self, item: ClipItem) -> None:
        """Put the text back on the clipboard: a normal copy, or a secret copy (hidden
        from the clipboard history, wiped later) when it looks like a token."""
        vault = self.shell.vault
        if vault is None:
            return
        text = item.text(vault.clip_sealer)
        if not text:
            return
        if looks_secret(text):
            # the guard first; the secret copy marks its own hash (note_secret), so no
            # ignore_once: a refused copy must not hide the next real copy of the same text
            self.shell.security.require_secrets(lambda: self._copy_secret(item))
            return
        self.shell.copy_to_clipboard(text, secret=False)
        self.watch.ignore_once(text)  # after the put: a failed put raised before this

    def _copy_secret(self, item: ClipItem) -> None:
        vault = self.shell.vault
        text = item.text(vault.clip_sealer) if vault is not None else ""
        if text:
            self.shell.copy_to_clipboard(text, secret=True)

    def toggle_pin(self, item: ClipItem) -> None:
        i = self._index(item)
        if i >= 0 and self.shell.vault.clipboard.pin(i, not item.pinned):
            self.shell.save_later()
        self.reload()

    def delete(self, item: ClipItem) -> None:
        i = self._index(item)
        if i >= 0 and self.shell.vault.clipboard.remove(i):
            self.shell.save_later()
        self.reload()

    def clear_all(self) -> None:
        vault = self.shell.vault
        if vault is None or not vault.clipboard.items:
            return
        vault.clipboard.clear(keep_pinned=True)
        self.shell.save_later()
        self.reload()
        self.shell.set_status(_("The clipboard history was cleared"))

