"""The Error log window: what errors.log and crash.log beside the vault hold, newest first,
and the report a user sends to the author.

One window, not modal, reached from Settings → General → About (and the app menu on
macOS) and from the toast after a crash. The list shows each entry's time, where it
happened and the error; the traceback folds under it. Below the list is the report
exactly as it leaves the computer (errlog.build_report: scrubbed of the home folder, the
user name, e-mails, tokens and every web address but its host), so what “Copy report” and
“Save report…” hand over is what the user has read. Nothing of the vault is shown, so the
window stays open through a lock."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import shiboken6
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QPlainTextEdit, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from ... import errlog, palette
from ...i18n import _, language
from .. import theme
from ..errors import guard_events
from ..widgets.common import IconLabel, divider
from .base import ask_yes_no, button, label, paint_caption, show_error
from .fileview import open_with_system

SIZE = (900, 720)


def report_text(window: QWidget | None, folder: Path) -> str:
    """The report of `folder`'s logs, with the screen scale of `window` and the UI language."""
    screen = window.screen() if window is not None else QGuiApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen is not None else 1.0
    scale = f"{round(ratio * 100)}%" + (f", text ×{theme.TEXT_SCALE:g}" if theme.TEXT_SCALE != 1.0 else "")
    return errlog.build_report(folder, errlog.environment(scale, language()))


def what_it_holds() -> str:
    return _("The report holds the app version, the system, the screen scale, the language, the last {n} errors with their code lines and the last crash. "
             "Your home folder, user name, e-mails, tokens and web addresses are hidden; it holds no passwords and nothing from the vault.").format(n=errlog.REPORT_ENTRIES)


@guard_events
class ErrorLogWindow(QWidget):
    """The window; `refresh()` reads the files again."""

    def __init__(self, shell: Any):
        super().__init__(shell, Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setObjectName("ErrorLog")
        self.shell = shell
        self.folder = Path(shell.vault_path).parent
        self.setWindowTitle(f"{_('Error log')} — {palette.APP_TITLE}")
        self.resize(*SIZE)
        self.setMinimumSize(560, 420)

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(16, 12, 16, 4)
        head = IconLabel("⚠ " + _("Error log"), "", self, size=18)
        head.setFont(theme.font(13, bold=True))
        bar.addWidget(head)
        bar.addStretch(1)
        self.copy_btn = button(_("Copy report"), "primary", self)
        self.copy_btn.clicked.connect(self.copy_report)
        self.save_btn = button(_("Save report…"), "small", self)
        self.save_btn.clicked.connect(self.save_report)
        self.folder_btn = button(_("Open folder"), "small", self)
        self.folder_btn.clicked.connect(self.open_folder)
        self.clear_btn = button(_("Clear"), "small", self)
        self.clear_btn.clicked.connect(self.clear)
        for b in (self.copy_btn, self.save_btn, self.folder_btn, self.clear_btn):
            bar.addWidget(b)
        col.addLayout(bar)
        note = label(what_it_holds(), "muted", self)
        note.setWordWrap(True)
        note.setContentsMargins(16, 0, 16, 0)
        col.addWidget(note)
        col.addSpacing(8)
        col.addWidget(divider(self))

        split = QSplitter(Qt.Orientation.Vertical, self)
        top = QWidget(split)
        tl = QVBoxLayout(top)
        tl.setContentsMargins(16, 10, 16, 6)
        self.count = label("", "section", top)
        tl.addWidget(self.count)
        self.tree = QTreeWidget(top)
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels([_("When"), _("Where it happened"), _("Error")])
        self.tree.setUniformRowHeights(False)
        self.tree.setWordWrap(False)
        tl.addWidget(self.tree, 1)
        self.empty = label(_("No errors recorded."), "muted", top)
        tl.addWidget(self.empty)
        bottom = QWidget(split)
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(16, 6, 16, 12)
        bl.addWidget(label(_("The report, exactly as it is copied or saved").upper(), "section", bottom))
        self.preview = QPlainTextEdit(bottom)
        self.preview.setReadOnly(True)
        self.preview.setFont(theme.font(9, mono=True))
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        bl.addWidget(self.preview, 1)
        split.addWidget(top)
        split.addWidget(bottom)
        split.setSizes([360, 300])
        col.addWidget(split, 1)
        QShortcut(QKeySequence("Escape"), self, self.close)
        p = shell.frameGeometry()
        self.move(p.x() + max(0, (p.width() - SIZE[0]) // 2), p.y() + max(0, (p.height() - SIZE[1]) // 3))
        self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        paint_caption(self)

    def refresh(self) -> None:
        entries = errlog.read_entries(self.folder / errlog.NAME)
        self.tree.clear()
        mono = theme.font(9, mono=True)
        crash = errlog.last_crash(self.folder)
        if crash:
            first, _sep, rest = crash.partition("\n")
            item = QTreeWidgetItem(self.tree, [first.split(errlog.SEP)[1].removeprefix("started ") if errlog.SEP in first else "", _("crash"), _("The app ended with a crash")])
            child = QTreeWidgetItem(item, [rest or first])
            child.setFont(0, mono)
            child.setFirstColumnSpanned(True)
        for e in entries:
            item = QTreeWidgetItem(self.tree, [e.when, e.where, e.title])
            item.setToolTip(2, e.title)
            child = QTreeWidgetItem(item, [e.body])
            child.setFont(0, mono)
            child.setFirstColumnSpanned(True)
        self.tree.resizeColumnToContents(0)
        self.tree.resizeColumnToContents(1)
        self.count.setText(_("Errors: {n}").format(n=len(entries)).upper())
        has = bool(entries or crash)
        self.tree.setVisible(has)
        self.empty.setVisible(not has)
        self.report = report_text(self, self.folder)
        self.preview.setPlainText(self.report)

    def copy_report(self) -> None:
        copy = getattr(self.shell, "copy_to_clipboard", None)
        if copy is not None:
            copy(self.report, secret=False)
        else:
            QGuiApplication.clipboard().setText(self.report)

    def save_report(self) -> None:
        name = f"daykeeper-error-report-{time.strftime('%Y%m%d-%H%M')}.md"
        target, _f = QFileDialog.getSaveFileName(self, _("Where to save the report"), str(Path.home() / name), "Markdown (*.md);;" + _("Text") + " (*.txt)")
        if not target:
            return
        try:
            Path(target).write_text(self.report, encoding="utf-8")
        except OSError as exc:
            show_error(self, str(exc))
            return
        set_status = getattr(self.shell, "set_status", None)
        if set_status is not None:
            set_status(_("Saved: {name}").format(name=Path(target).name))

    def open_folder(self) -> None:
        try:
            open_with_system(self.folder)
        except OSError as exc:
            show_error(self, str(exc))

    def clear(self) -> None:
        if not ask_yes_no(self, _("Delete the error log and the last crash? The report will be empty until the next error.")):
            return
        errlog.clear(self.folder)
        self.refresh()


def open_error_log(shell: Any) -> ErrorLogWindow:
    """The one Error log window of `shell`, raised when it is open already."""
    win = getattr(shell, "error_log_window", None)
    if win is None or not shiboken6.isValid(win):
        win = ErrorLogWindow(shell)
        shell.error_log_window = win
    else:
        win.refresh()
    win.show()
    win.raise_()
    win.activateWindow()
    return win
