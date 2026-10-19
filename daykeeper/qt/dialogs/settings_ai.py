"""The "Send to AI" section of the Qt settings window: the assistant and channel the send
dialog starts with, the model of the quiet channel, the project map (a GitLab path → the
folder of its checkout), the assignment templates, the clean-up of old assignment files
and the answers of this session.

Everything here is plain settings.json (assistants.py reads and writes it); this module
only lays out the controls. Each change is written at once, as the hook rules are."""

from __future__ import annotations

from functools import partial
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ... import assistants as ai
from ...i18n import _
from .. import theme
from ..widgets.common import icon_button
from . import ai_send
from .base import Dialog, button, label, show_error

KIND_LABELS = {
    "mr_review": "Review a merge request",
    "mr_fix": "Fix the review notes",
    "note_task": "A note as a task",
    "mr_brief": "Brief a merge request",
    "custom": "Other",
}


def kind_label(kind: str) -> str:
    return _(KIND_LABELS.get(kind, kind))


class AiSection(QWidget):
    def __init__(self, win: Any):
        from .settings import Section

        super().__init__()
        self.win, self.shell = win, win.shell
        self.s = Section(win)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.s)
        self.s.head(_("Send to AI"), _("Daykeeper calls no model itself and keeps no key for one: it hands an assignment to Claude Code or Codex that you already use, on your own plan. You always see the whole text before it goes."))
        self._assistant()
        self._projects()
        self._templates()
        self._files()
        self.s.end()

    def settings(self) -> dict:
        return self.shell.session.settings()

    def write(self, update: dict) -> bool:
        if not self.shell.session.write_settings(update):
            show_error(self.win, _("Could not write settings.json"))
            return False
        return True

    def _muted(self, g: QVBoxLayout, text: str) -> QLabel:
        lab = label(text, "muted", self.s.inner)
        lab.setWordWrap(True)
        g.addWidget(lab)
        return lab

    # ---- the assistant

    def _assistant(self) -> None:
        g = self.s.group(_("Assistant"), _("What the send dialog starts with; it remembers your last choice for each kind of assignment."))
        inner = self.s.inner
        defaults = ai.read_defaults(self.settings())
        installed = ai_send.agents()
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        self.agent = QComboBox(inner)
        for a in installed:
            self.agent.addItem(ai.AGENT_NAMES[a], a)
        if defaults.agent in installed:
            self.agent.setCurrentIndex(installed.index(defaults.agent))
        self.agent.setEnabled(bool(installed))
        grid.addWidget(QLabel(_("Assistant"), inner), 0, 0)
        grid.addWidget(self.agent, 0, 1)
        self.channel = QComboBox(inner)
        grid.addWidget(QLabel(_("Channel"), inner), 1, 0)
        grid.addWidget(self.channel, 1, 1)
        self.model = QLineEdit(defaults.model, inner)
        self.model.setPlaceholderText(_("the assistant's default"))
        self.model.setToolTip(_("A model name the assistant knows, e.g. sonnet. Empty — the assistant decides."))
        grid.addWidget(QLabel(_("Model of the quiet channel"), inner), 2, 0)
        grid.addWidget(self.model, 2, 1)
        grid.setColumnStretch(2, 1)
        g.addLayout(grid)
        if not installed:
            warn = label(_("Neither Claude Code nor Codex was found on this computer. Install one and open the settings again; until then the send dialog can only copy the text."), "error", inner)
            warn.setWordWrap(True)
            g.addWidget(warn)
        self._muted(g, _("Terminal — a console window with the assistant in the project folder. Quiet — Claude Code answers once, without tools, and the answer opens here."))
        self._fill_channels(defaults.channel)
        self.agent.currentIndexChanged.connect(self._agent_changed)
        self.channel.currentIndexChanged.connect(lambda _i: self.save_defaults())
        self.model.editingFinished.connect(self.save_defaults)

    def _agent_changed(self, _index: int) -> None:
        self._fill_channels(self.channel.currentData())
        self.save_defaults()

    def _fill_channels(self, keep: str | None) -> None:
        channels = ai_send.offered_channels(self.agent.currentData() or "claude")
        self.channel.blockSignals(True)
        self.channel.clear()
        for c in channels:
            self.channel.addItem(ai_send.channel_label(c), c)
        self.channel.setCurrentIndex(channels.index(keep) if keep in channels else 0)
        self.channel.blockSignals(False)

    def save_defaults(self) -> bool:
        old = ai.read_defaults(self.settings())
        new = ai.Defaults(self.agent.currentData() or old.agent, self.channel.currentData() or old.channel, self.model.text().strip(), old.geckit)
        update: dict = {}
        ai.write_defaults(update, new)
        return self.write(update)

    # ---- the project map

    def _projects(self) -> None:
        g = self.s.group(_("Projects"), _("Where the assistant works on a repository: the folder of its checkout. A merge request is sent to the folder of its project."))
        inner = self.s.inner
        self.table = QTableWidget(0, 2, inner)
        self.table.setHorizontalHeaderLabels([_("GitLab path"), _("Folder")])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(theme.px(130))
        self.table.cellDoubleClicked.connect(lambda r, _c: self.edit_project(r))
        g.addWidget(self.table)
        row = QHBoxLayout()
        add = button(_("+ Add project"), "small", inner)
        add.clicked.connect(lambda: self.edit_project(None))
        edit = button(_("Edit…"), "small", inner)
        edit.clicked.connect(lambda: self.edit_project(self.table.currentRow()) if self.table.currentRow() >= 0 else None)
        remove = button(_("Remove"), "danger", inner)
        remove.clicked.connect(self.remove_project)
        for b in (add, edit, remove):
            row.addWidget(b)
        row.addStretch(1)
        g.addLayout(row)
        self._muted(g, _("A folder that is no longer a git checkout drops out of the map by itself."))
        self.paint_projects()

    def projects(self) -> list[ai.Project]:
        return ai.read_projects(self.settings())

    def paint_projects(self) -> None:
        projects = self.projects()
        self.table.setRowCount(len(projects))
        for r, p in enumerate(projects):
            self.table.setItem(r, 0, QTableWidgetItem(p.gitlab or "—"))
            self.table.setItem(r, 1, QTableWidgetItem(p.folder))

    def edit_project(self, index: int | None) -> ai_send.ProjectDialog | None:
        projects = self.projects()
        if index is not None and not 0 <= index < len(projects):
            return None
        current = projects[index] if index is not None else ai.Project()
        others = [p for i, p in enumerate(projects) if i != index]

        def saved(p: ai.Project) -> None:
            fresh = self.projects()
            if index is not None and index < len(fresh):
                fresh[index] = p
            else:
                fresh.append(p)
            if ai_send.save_projects(self.shell, fresh):
                self.paint_projects()
            else:
                show_error(self.win, _("Could not write settings.json"))

        return ai_send.ProjectDialog(self.win, current, saved, others)

    def remove_project(self) -> None:
        row = self.table.currentRow()
        projects = self.projects()
        if not 0 <= row < len(projects):
            return
        del projects[row]
        if ai_send.save_projects(self.shell, projects):
            self.paint_projects()

    # ---- templates

    def _templates(self) -> None:
        g = self.s.group(_("Assignment templates"), _("The text the send dialog starts with. Ready-made ones can be switched off; your own can be added, changed and deleted."))
        self.templates_host = QWidget(self.s.inner)
        self.templates_col = QVBoxLayout(self.templates_host)
        self.templates_col.setContentsMargins(0, 0, 0, 0)
        g.addWidget(self.templates_host)
        add = button(_("+ New template"), "small", self.s.inner)
        add.clicked.connect(lambda: self.edit_template(None))
        g.addWidget(add, 0, Qt.AlignmentFlag.AlignLeft)
        self.paint_templates()

    def templates(self) -> list[ai.Template]:
        return ai.read_templates(self.settings())

    def save_templates(self, templates: list[ai.Template]) -> bool:
        update: dict = {}
        ai.write_templates(update, templates)
        return self.write(update)

    def paint_templates(self) -> None:
        while self.templates_col.count():
            item = self.templates_col.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.hide()
                w.deleteLater()
        for i, t in enumerate(self.templates()):
            row = QWidget(self.templates_host)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            on = QCheckBox(ai_send.template_title(t), row)
            on.setChecked(t.enabled)
            on.setToolTip(t.body)
            on.toggled.connect(partial(self._template_on, i))
            rl.addWidget(on)
            # a ready-made template's title already names its kind
            what = (_("ready-made") if t.preset else kind_label(t.kind)) + "  ·  " + ai_send.channel_label(t.channel)
            rl.addWidget(label(what, "muted", row), 1)
            if not t.preset:
                rl.addWidget(icon_button("✎", _("Edit"), partial(self.edit_template, i), row))
                rl.addWidget(icon_button("✕", _("Delete"), partial(self._delete_template, i), row))
            self.templates_col.addWidget(row)

    def _template_on(self, index: int, on: bool) -> None:
        templates = self.templates()
        templates[index].enabled = on
        self.save_templates(templates)

    def _delete_template(self, index: int) -> None:
        templates = self.templates()
        if templates[index].preset:
            return
        del templates[index]
        if self.save_templates(templates):
            self.paint_templates()

    def edit_template(self, index: int | None) -> TemplateDialog:
        templates = self.templates()
        current = templates[index] if index is not None else ai.Template(ai.new_template_id(), "note_task", "", "")
        return TemplateDialog(self.win, current, lambda t: self._template_saved(index, t))

    def _template_saved(self, index: int | None, t: ai.Template) -> None:
        templates = self.templates()
        if index is None:
            templates.append(t)
        else:
            templates[index] = t
        if self.save_templates(templates):
            self.paint_templates()

    # ---- files and answers

    def _files(self) -> None:
        g = self.s.group(_("Assignment files"), _("Each assignment is written as a file the assistant reads; the quiet answers saved to notes go beside them."))
        inner = self.s.inner
        path = QLabel(str(ai.assignments_dir(ai_send.data_dir(self.shell))), inner)
        path.setFont(theme.font(9, mono=True))
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        g.addWidget(path)
        row = QHBoxLayout()
        prune = button(_("Delete assignments older than {n} days").format(n=ai.KEEP_DAYS), "small", inner)
        prune.clicked.connect(self.prune)
        history = button(_("Recent answers…"), "small", inner)
        history.setToolTip(_("The quiet answers of this session: open one again, copy it or save it to a note."))
        history.clicked.connect(lambda: ai_send.HistoryDialog(self.shell))
        row.addWidget(prune)
        row.addWidget(history)
        row.addStretch(1)
        g.addLayout(row)
        self.pruned = label("", "ok", inner)
        g.addWidget(self.pruned)

    def prune(self) -> int:
        try:
            gone = ai.prune_assignments(ai_send.data_dir(self.shell))
        except OSError as exc:
            show_error(self.win, str(exc))
            return 0
        self.pruned.setText("✓ " + _("Deleted: {n}").format(n=gone))
        return gone


class TemplateDialog(Dialog):
    """One own template: a name, the kind of assignment it is for, the text with its
    placeholders, the goal and the channel it goes to."""

    def __init__(self, parent: QWidget, template: ai.Template, on_save):
        super().__init__(parent, _("Assignment template"))
        self.on_save, self.original = on_save, template
        width = self.fontMetrics().averageCharWidth() * 70
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        self.name = QLineEdit(template.title, self)
        self.name.setMinimumWidth(width)
        self.kind = QComboBox(self)
        for k in ai.KINDS:
            self.kind.addItem(kind_label(k), k)
        self.kind.setCurrentIndex(ai.KINDS.index(template.kind) if template.kind in ai.KINDS else ai.KINDS.index("custom"))
        self.body = QPlainTextEdit(template.body, self)
        self.body.setFont(theme.font(10, mono=True))
        self.body.setFixedHeight(self.body.fontMetrics().lineSpacing() * 10 + 16)
        self.body.setTabChangesFocus(True)
        self.goal = QLineEdit(template.goal, self)
        self.channel = QComboBox(self)
        channels = ai_send.offered_channels()
        for c in channels:
            self.channel.addItem(ai_send.channel_label(c), c)
        self.channel.setCurrentIndex(channels.index(template.channel) if template.channel in channels else 0)
        rows = [
            (_("Name"), self.name, ""),
            (_("For"), self.kind, _("The send dialog offers the template for this kind of assignment.")),
            (_("Text"), self.body, _("Placeholders: {names}. A brace not in this list stays as typed.").format(names=", ".join("{" + p + "}" for p in ai.PLACEHOLDERS))),
            (_("Goal"), self.goal, _("When the work is done, in one line; placeholders work here too.")),
            (_("Channel"), self.channel, ""),
        ]
        r = 0
        for text, widget, hint in rows:
            grid.addWidget(QLabel(text, self), r, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(widget, r, 1)
            r += 1
            if hint:
                note = label(hint, "muted", self)
                note.setWordWrap(True)
                grid.addWidget(note, r, 1)
                r += 1
        self.error = label("", "error", self)
        grid.addWidget(self.error, r, 1)
        self.box.addLayout(grid, 0, 0)
        self.editor_buttons(1, 1, self.save)
        self.name.setFocus()
        self.open()

    def template(self) -> ai.Template:
        return ai.Template(self.original.id, self.kind.currentData(), self.name.text().strip(), self.body.toPlainText(),
                           goal=self.goal.text().strip(), channel=self.channel.currentData(), agent=self.original.agent,
                           enabled=self.original.enabled)

    def save(self) -> None:
        t = self.template()
        if not t.title:
            self.error.setText(_("Give the template a name."))
            return
        if not t.body.strip():
            self.error.setText(_("The text is empty."))
            return
        self.accept()
        self.on_save(t)
