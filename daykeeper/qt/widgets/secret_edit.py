"""A one-line field for a password, a PIN or a secret value.

QLineEdit keeps an undo history even in password mode and has no switch to turn it off;
`clear()` is an undoable edit: once the field is shown (the eye), Ctrl+Z brings the
password back. `setText` resets the history. This class makes the safe way the only way: `clear`
and `wipe` both go through `setText("")`. It also keeps the secret away from the input
method's prediction and memory, and has no context menu (Paste / Delete stay enabled on a
password field and `cut()` erases the text without copying it).

Copy and Cut of a shown secret (Ctrl+C / Ctrl+X / Ctrl+Insert / Shift+Delete, or the
`copy()` / `cut()` slots) do not go to the clipboard the Qt way, which would leave the
password in the Windows clipboard history: they go through the window's
`copy_to_clipboard(text, secret=True)` — marked "not in history", told to the clipboard
history, wiped later. `copy_handler` (set by the owner) or the nearest window up the
parents that has `copy_to_clipboard` does it; with neither the copy is refused. A field
the owner says is plain (`secret = False`, or a card row whose field is not secret) copies
as any line edit."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeyEvent, QKeySequence
from PySide6.QtWidgets import QLineEdit, QWidget

from .. import icons
from ..errors import guard_events

SECRET_HINTS = Qt.InputMethodHint.ImhSensitiveData | Qt.InputMethodHint.ImhNoPredictiveText | Qt.InputMethodHint.ImhNoAutoUppercase | Qt.InputMethodHint.ImhHiddenText


@guard_events
class SecretEdit(QLineEdit):
    """Hidden by default; `set_shown(True)` shows the text (the eye), `wipe()` empties
    the field so that nothing can be undone back into it."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setEchoMode(QLineEdit.EchoMode.Password)
        self.setInputMethodHints(SECRET_HINTS)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setDragEnabled(False)
        self._eye: QAction | None = None
        self.secret: bool | None = None  # None — decided by the owner's field (see is_secret)
        self.copy_handler: Callable[[str], object] | None = None  # copies `text` as a secret

    # ---- copy and cut

    def is_secret(self) -> bool:
        """True unless the owner says the value is plain: `secret = False`, or the card row
        this field belongs to holds a field that is not secret."""
        if self.secret is not None:
            return self.secret
        field = getattr(self.parentWidget(), "f", None)
        flag = getattr(field, "secret", None)
        return flag if isinstance(flag, bool) else True

    def _secret_copier(self) -> Callable[[str], object] | None:
        if self.copy_handler is not None:
            return self.copy_handler
        w: QWidget | None = self.window()
        while w is not None:
            fn = getattr(w, "copy_to_clipboard", None)
            if callable(fn):
                return lambda text: fn(text, secret=True)
            w = w.parentWidget()
        return None

    def _copy_secret(self) -> bool:
        """The selection of a shown secret through the window; False — nothing copied."""
        text = self.selectedText()
        if not text or not self.shown():
            return False  # hidden: Qt refuses too
        copier = self._secret_copier()
        if copier is None:
            return False  # no safe way: refused rather than put in the open
        copier(text)
        return True

    def copy(self) -> None:  # the slot of QLineEdit, made safe for a secret
        if self.is_secret():
            self._copy_secret()
        else:
            super().copy()

    def cut(self) -> None:  # the slot of QLineEdit, made safe for a secret
        if not self.is_secret():
            super().cut()
        elif not self.isReadOnly() and self._copy_secret():
            self.del_()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self.is_secret():
            if event.matches(QKeySequence.StandardKey.Copy):
                self.copy()
                event.accept()
                return
            if event.matches(QKeySequence.StandardKey.Cut):
                self.cut()
                event.accept()
                return
        super().keyPressEvent(event)

    def clear(self) -> None:  # the slot of QLineEdit, made safe
        self.wipe()

    def wipe(self) -> None:
        """Empty the field and its undo history (see the module text)."""
        self.setText("")

    def shown(self) -> bool:
        return self.echoMode() == QLineEdit.EchoMode.Normal

    def set_shown(self, on: bool) -> None:
        self.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password)
        if self._eye is not None:
            self._eye.setIcon(icons.eye(shown=on))

    def add_eye(self, tooltip: str) -> QAction:
        """The eye inside the field at its right edge; a click shows or hides the text.
        The returned action is triggered by the click (several fields can share one state)."""
        self._eye = self.addAction(icons.eye(), QLineEdit.ActionPosition.TrailingPosition)
        self._eye.setToolTip(tooltip)
        return self._eye
