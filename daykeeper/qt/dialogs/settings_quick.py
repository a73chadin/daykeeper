"""The "Quick access" section of the settings: the hotkey that opens the quick window from
anywhere (Windows only; elsewhere the group is not shown and Ctrl+K inside the app opens
it), why it does not work when it does not, and the list of recent choices. The window
and the hotkey are `quick.QuickAccess`; this module only lays out controls."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from ...i18n import _, hotkeys
from ..errors import guard_events
from . import quick
from .base import button, label


@guard_events
class QuickSection(QWidget):
    def __init__(self, win: Any):
        from .settings import Section

        super().__init__()
        self.win, self.shell = win, win.shell
        self.access: quick.QuickAccess = self.shell.quick
        self.section = Section(win)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.section)
        s = self.section
        s.head(_("Quick access"), _("A small window over everything: find a password, a bookmark, a note or a routine and act on it, or type a journal entry such as “pass DEMO-12”."))

        self.combo: QLineEdit | None = None
        self.state = label("", "", s.inner)
        self.state.setWordWrap(True)
        if quick.HOTKEY_SUPPORTED:
            g = s.group(_("Hotkey for the whole computer"), _("Works in any program while Daykeeper runs. Modifiers: ctrl, alt, shift, win; then a key: a letter, a digit, space, F1–F12…"))
            row = QHBoxLayout()
            self.combo = QLineEdit(self.access.combo(), s.inner)
            self.combo.setMinimumWidth(self.combo.fontMetrics().averageCharWidth() * 28)
            self.combo.setPlaceholderText(quick.DEFAULT_HOTKEY)
            self.combo.returnPressed.connect(self.apply)
            row.addWidget(self.combo)
            self.apply_btn = button(_("Apply"), "primary", s.inner)
            self.apply_btn.clicked.connect(self.apply)
            row.addWidget(self.apply_btn)
            row.addStretch(1)
            g.addLayout(row)
            g.addWidget(self.state)
            self.show_state(self.access.problem)
        g = s.group(_("Inside the app"))
        g.addWidget(label(_("{keys} opens the same window while Daykeeper is in front.").format(keys=hotkeys(quick.LOCAL_SHORTCUT)), "muted", s.inner))

        g = s.group(_("Recent choices"), _("What you pick in the quick window comes first next time. Only the names of the choices are kept, never a password."))
        row = QHBoxLayout()
        self.clear_btn = button(_("Clear the recent list"), "", s.inner)
        self.clear_btn.clicked.connect(self.clear_recent)
        row.addWidget(self.clear_btn)
        self.recent_state = label("", "muted", s.inner)
        row.addWidget(self.recent_state)
        row.addStretch(1)
        g.addLayout(row)
        self._count_recent()
        s.end()

    def show_state(self, problem: str) -> None:
        if problem:
            self.state.setProperty("role", "error")
            self.state.setText(problem)
        else:
            self.state.setProperty("role", "ok")
            self.state.setText("✓ " + _("{combo} opens the quick window").format(combo=self.access.combo()))
        self.state.style().unpolish(self.state)
        self.state.style().polish(self.state)

    def apply(self) -> None:
        if self.combo is None:
            return
        problem = self.access.set_combo(self.combo.text())
        self.show_state(problem)
        if not problem:
            self.combo.setText(self.access.combo())

    def clear_recent(self) -> None:
        self.access.clear_recent()
        self._count_recent()

    def _count_recent(self) -> None:
        n = len(self.access.recent())
        self.recent_state.setText(_("Remembered: {n}").format(n=n) if n else _("The list is empty"))
        self.clear_btn.setEnabled(bool(n))
