"""Sending work to an AI assistant from the Qt window: the send dialog every entry point
opens (a note card, one of its items), the project form, the quiet runs of a session with
their result windows and history.

Daykeeper calls no model itself (assistants.py): the dialog shows the whole assignment —
exactly what leaves the encrypted vault — and hands it to Claude Code or Codex in a
console (`terminal`) or to `claude -p` whose answer comes back here (`quiet`). The GeckIt
channel is offered only once the core says it is ready.

The quiet runs live in one AiRuns per window (`controller`): their threads wake the window
through a Waker, a window shows the answer, a toast says it is ready. A lock stops them and
forgets their answers: an answer may quote what the vault holds."""

from __future__ import annotations

import os
import re
import time
import weakref
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any

import shiboken6
from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QVBoxLayout, QWidget

from ... import assistants as ai
from ... import palette
from ...assistant_runs import QuietRun, QuietRuns
from ...i18n import _
from ...journal import today
from ...runner import open_in_console
from ...settings import private
from .. import theme
from ..errors import guard_events
from ..jobs import Waker
from ..widgets.common import IconLabel
from .base import Dialog, button, label, paint_caption, register

ANSWERS_DIR = "answers"
GLYPH = "📤"  # the drawn paper plane of the section, the dialog and the toasts
CHANNEL_LABELS = {"terminal": "Terminal", "quiet": "Quiet — the answer comes here", "geckit": "GeckIt"}
FIRST_LINE_MAX = 200  # characters of an answer's first line written into a note's text
TITLE_MAX = 80  # characters of a checklist item used as an assignment title


def agents() -> list[str]:
    """The assistants installed on this computer (one place for the tests to replace)."""
    return ai.installed_agents()


def offered_channels(agent: str = "claude") -> list[str]:
    """The channels the dialog and the settings offer: GeckIt only once the core has it
    ready (it does not yet), the quiet one only for Claude Code (quiet_argv)."""
    out = ["terminal"]
    if agent in ("", "claude"):
        out.append("quiet")
    if ai.GECKIT_CHANNEL_READY and ai.geckit_available(ai.geckit_install()):
        out.append("geckit")
    return out


def channel_label(channel: str) -> str:
    return _(CHANNEL_LABELS.get(channel, channel))


def data_dir(shell: Any) -> Path:
    return Path(shell.vault_path).parent


def projects_of(shell: Any) -> list[ai.Project]:
    return ai.read_projects(shell.session.settings())


def save_projects(shell: Any, projects: list[ai.Project]) -> bool:
    update: dict = {}
    ai.write_projects(update, projects)
    return bool(shell.session.write_settings(update))


def tidy(text: str) -> str:
    """A rendered template without the empty lines a missing value left behind."""
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def template_title(t: ai.Template) -> str:
    return _(t.title) if t.preset else t.title


# ---- the project form


class ProjectDialog(Dialog):
    """One line of the project map: the GitLab path and the folder of its checkout. The
    folder must be a git checkout; a GitLab path is in the map once."""

    def __init__(self, parent: QWidget, project: ai.Project, on_save, others: list[ai.Project] | None = None):
        super().__init__(parent, _("Project"))
        self.on_save, self.others = on_save, others or []
        width = self.fontMetrics().averageCharWidth() * 46
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        self.gitlab = QLineEdit(project.gitlab, self)
        self.gitlab.setPlaceholderText("group/app")
        self.gitlab.setMinimumWidth(width)
        self.folder = QLineEdit(project.folder, self)
        self.folder.setMinimumWidth(width)
        browse = button(_("Browse…"), "small", self)
        browse.clicked.connect(self.browse)
        grid.addWidget(label(_("GitLab path"), "", self), 0, 0)
        grid.addWidget(self.gitlab, 0, 1, 1, 2)
        hint = label(_("As in the address of the project, e.g. group/app."), "muted", self)
        grid.addWidget(hint, 1, 1, 1, 2)
        grid.addWidget(label(_("Folder"), "", self), 2, 0)
        grid.addWidget(self.folder, 2, 1)
        grid.addWidget(browse, 2, 2)
        hint = label(_("The checkout of that project on this computer: the assistant works there."), "muted", self)
        hint.setWordWrap(True)
        grid.addWidget(hint, 3, 1, 1, 2)
        self.error = label("", "error", self)
        self.error.setWordWrap(True)
        grid.addWidget(self.error, 4, 1, 1, 2)
        self.box.addLayout(grid, 0, 0)
        self.editor_buttons(1, 1, self.save)
        self.keys(self.save)
        (self.folder if project.gitlab else self.gitlab).setFocus()
        self.open()

    def browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, _("The project's folder"), self.folder.text())
        if folder and shiboken6.isValid(self):  # the idle lock does not wait for a file dialog
            self.folder.setText(os.path.normpath(folder))

    def project(self) -> ai.Project:
        return ai.Project(self.gitlab.text().strip().strip("/"), self.folder.text().strip())

    def save(self) -> None:
        p = self.project()
        if not p.folder:
            self.error.setText(_("Choose the project's folder."))
            return
        if not ai.is_repo(p.folder):
            self.error.setText(_("Not a git checkout: {path}").format(path=p.folder))
            return
        if p.gitlab and any(o.gitlab.lower() == p.gitlab.lower() for o in self.others):
            self.error.setText(_("{path} is in the map already.").format(path=p.gitlab))
            return
        self.accept()
        self.on_save(p)


# ---- the send dialog


class SendDialog(Dialog):
    """What goes to the assistant, shown in full before anything leaves: the title, the
    assistant, the channel, the project and the editable text. The text in the field when
    Send is pressed is what is written to the assignment file. `gitlab_path` — the
    project the work is about (a merge request's): offered to add when not in the map."""

    def __init__(self, shell: Any, kind: str, templates: list[ai.Template], context: dict[str, str], title: str = "",
                 gitlab_path: str = "", links: list[str] | None = None):
        super().__init__(shell, _("Send to AI"))
        self.shell, self.kind, self.context = shell, kind, context
        self.templates = templates or [t for t in ai.PRESETS if t.kind == kind] or [ai.PRESETS[0]]
        self.given_title, self.gitlab_path, self.links = title, gitlab_path, list(links or [])
        settings = shell.session.settings()
        self.defaults = ai.read_defaults(settings)
        self.projects = ai.read_projects(settings)
        self.installed = agents()
        last = ai.read_last_choice(settings, kind)
        width = self.fontMetrics().averageCharWidth() * 96
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        row = 0
        self.template = QComboBox(self)
        for t in self.templates:
            self.template.addItem(template_title(t), t.id)
        if len(self.templates) > 1:
            grid.addWidget(label(_("Template"), "", self), row, 0)
            grid.addWidget(self.template, row, 1)
            row += 1
        else:
            self.template.hide()
        self.title = QLineEdit(self)
        grid.addWidget(label(_("Title"), "", self), row, 0)
        grid.addWidget(self.title, row, 1)
        row += 1
        self.agent = QComboBox(self)
        for a in self.installed:
            self.agent.addItem(ai.AGENT_NAMES[a], a)
        grid.addWidget(label(_("Assistant"), "", self), row, 0)
        grid.addWidget(self.agent, row, 1)
        row += 1
        if not self.installed:
            none = label(_("Neither Claude Code nor Codex was found on this computer. Install one, or copy the text and paste it yourself."), "error", self)
            none.setWordWrap(True)
            grid.addWidget(none, row, 1)
            row += 1
            self.agent.setEnabled(False)
        self.channel = QComboBox(self)
        grid.addWidget(label(_("Channel"), "", self), row, 0)
        grid.addWidget(self.channel, row, 1)
        row += 1
        self.channel_hint = label("", "muted", self)
        self.channel_hint.setWordWrap(True)
        grid.addWidget(self.channel_hint, row, 1)
        row += 1
        self.project = QComboBox(self)
        grid.addWidget(label(_("Project"), "", self), row, 0)
        grid.addWidget(self.project, row, 1)
        row += 1
        self.missing = QWidget(self)
        ml = QHBoxLayout(self.missing)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.addWidget(label(_("{path} is not in the project map.").format(path=gitlab_path), "muted", self.missing))
        add = button(_("Add it…"), "small", self.missing)
        add.clicked.connect(self.add_project)
        ml.addWidget(add)
        ml.addStretch(1)
        grid.addWidget(self.missing, row, 1)
        row += 1
        grid.addWidget(label(_("Text"), "", self), row, 0, Qt.AlignmentFlag.AlignTop)
        self.text = QPlainTextEdit(self)
        self.text.setFont(theme.font(10, mono=True))
        self.text.setMinimumWidth(width)
        self.text.setFixedHeight(self.text.fontMetrics().lineSpacing() * 16 + 16)
        self.text.setTabChangesFocus(True)
        grid.addWidget(self.text, row, 1)
        row += 1
        note = label(_("This is all the assistant gets: the text leaves the encrypted vault as a file in the data folder. Edit it freely."), "muted", self)
        note.setWordWrap(True)
        grid.addWidget(note, row, 1)
        row += 1
        self.error = label("", "error", self)
        self.error.setWordWrap(True)
        grid.addWidget(self.error, row, 1)
        self.box.addLayout(grid, 0, 0)
        foot = QHBoxLayout()
        foot.addStretch(1)
        self.send_btn = button(_("Send"), "primary", self)
        self.send_btn.clicked.connect(self.send)
        self.send_btn.setEnabled(bool(self.installed))
        copy = button(_("Copy text"), "", self)
        copy.clicked.connect(self.copy)
        cancel = button(_("Cancel"), "", self)
        cancel.clicked.connect(self.reject)
        for b in (self.send_btn, copy, cancel):
            foot.addWidget(b)
        self.box.addLayout(foot, 1, 0)

        agent = last.agent if last else (self.templates[0].agent or self.defaults.agent)
        if agent in self.installed:
            self.agent.setCurrentIndex(self.installed.index(agent))
        template_channel = self.templates[0].channel
        self.wanted_channel = last.channel if last else (template_channel if template_channel != "terminal" else self.defaults.channel)
        self.fill_channels()
        self.agent.currentIndexChanged.connect(lambda _i: self.fill_channels())
        self.channel.currentIndexChanged.connect(lambda _i: self.paint_channel())
        self.fill_projects(gitlab_path, last.project if last else "")
        self.template.currentIndexChanged.connect(lambda _i: self.fill_text())
        self.project.currentIndexChanged.connect(lambda _i: self.project_changed())
        self.prepared = False  # the text came made (send_assignment): never rendered again
        self.fill_text()
        self.keys(None)  # Return types a new line in the text, it does not send
        self.text.setFocus()
        self.open()

    # ---- the choices

    def current_template(self) -> ai.Template:
        return self.templates[max(0, self.template.currentIndex())]

    def fill_text(self) -> None:
        """The template filled from the context: the title and the text (an edit made to
        the text is replaced — picking another template asks for that)."""
        project = self.current_project()
        a = ai.assignment_from(self.current_template(), self.context, title=self.given_title, folder=project.folder if project else "")
        self.base = a
        self.title.setText(a.title)
        self.text.setPlainText(tidy(a.text))

    def project_changed(self) -> None:
        """A template with {folder} follows the project, unless the text was edited."""
        base = getattr(self, "base", None)
        if (base is not None and not self.prepared and "{folder}" in self.current_template().body
                and self.text.toPlainText() == tidy(base.text)):
            self.fill_text()

    def fill_channels(self) -> None:
        keep = self.channel.currentData() or self.wanted_channel
        self.channel.blockSignals(True)
        self.channel.clear()
        channels = offered_channels(self.agent.currentData() or "claude")
        for c in channels:
            self.channel.addItem(channel_label(c), c)
        self.channel.setCurrentIndex(channels.index(keep) if keep in channels else 0)
        self.channel.blockSignals(False)
        self.paint_channel()

    def paint_channel(self) -> None:
        quiet = self.channel.currentData() == "quiet"
        self.channel_hint.setText(_("Claude Code answers once, without tools, in a scratch folder; the answer opens here.") if quiet
                                  else _("A console window opens in the project folder with the assistant told to read the assignment."))
        self.project.setEnabled(not quiet)

    def fill_projects(self, gitlab_path: str = "", wanted: str = "") -> None:
        self.project.clear()
        self.project.addItem(_("No project — the home folder"), "")
        for p in self.projects:
            self.project.addItem(f"{p.gitlab}  —  {p.folder}" if p.gitlab else p.folder, p.folder)
        chosen = ai.project_for(self.projects, gitlab_path) if gitlab_path else None
        chosen = chosen or ai.project_named(self.projects, wanted)
        if chosen is not None:
            self.project.setCurrentIndex(1 + self.projects.index(chosen))
        self.missing.setVisible(bool(gitlab_path) and ai.project_for(self.projects, gitlab_path) is None)

    def current_project(self) -> ai.Project | None:
        folder = self.project.currentData()
        return next((p for p in self.projects if folder and p.folder == folder), None)

    def add_project(self) -> ProjectDialog:
        def saved(p: ai.Project) -> None:
            self.projects.append(p)
            if not save_projects(self.shell, self.projects):
                self.error.setText(_("Could not write settings.json"))
            if shiboken6.isValid(self):
                self.fill_projects(p.gitlab or self.gitlab_path)

        return ProjectDialog(self, ai.Project(gitlab=self.gitlab_path), saved, self.projects)

    # ---- the buttons

    def assignment(self) -> ai.Assignment:
        project = self.current_project()
        channel = self.channel.currentData() or "terminal"
        return replace(
            self.base,
            title=self.title.text().strip() or self.base.title,
            text=self.text.toPlainText(),
            agent=self.agent.currentData() or "claude",
            channel=channel,
            folder=project.folder if project and channel != "quiet" else "",
            project=project.geckit_name if project else "",
            links=self.links,
        )

    def copy(self) -> None:
        self.shell.copy_to_clipboard(self.text.toPlainText(), secret=False)

    def send(self) -> None:
        self.error.setText("")
        if not self.installed or not self.agent.currentData():
            self.error.setText(_("No assistant is installed."))
            return
        if not self.text.toPlainText().strip():
            self.error.setText(_("The text is empty."))
            return
        a = self.assignment()
        settings = self.shell.session.settings()
        ai.remember_choice(settings, self.kind, ai.Choice(a.agent, a.channel, a.project))
        self.shell.session.write_settings({"ai_last_choice": settings["ai_last_choice"]})
        if a.channel == "quiet":  # the text goes on stdin: no file on disk
            controller(self.shell).start(a)
            self.accept()
            return
        try:
            path = ai.assignment_file(data_dir(self.shell), a)
        except (OSError, ValueError) as exc:
            self.error.setText(_("Could not write the assignment: {error}").format(error=exc))
            return
        plan = ai.terminal_plan(a, path, a.agent)
        problem = plan.problem
        if not problem:
            try:
                ai.write_script(plan)
                if not open_in_console(plan):
                    problem = _("No terminal program was found.")
            except OSError as exc:
                problem = _("Could not open a console: {error}").format(error=exc)
        if problem:
            path.unlink(missing_ok=True)  # nobody will read it: no copy of the text left behind
            self.error.setText(problem)
            return
        self.shell.set_status(_("Sent to {name} in a console").format(name=ai.AGENT_NAMES.get(a.agent, a.agent)))
        self.accept()


def send_assignment(shell: Any, a: ai.Assignment, gitlab_path: str = "") -> SendDialog:
    """An assignment made elsewhere (a merge request's), shown in the send dialog as it is."""
    templates = [t for t in ai.PRESETS if t.id == a.kind or t.kind == a.kind][:1]
    dialog = SendDialog(shell, a.kind, templates, {}, title=a.title, gitlab_path=gitlab_path, links=a.links)
    dialog.prepared = True
    dialog.base = a
    dialog.title.setText(a.title)
    dialog.text.setPlainText(tidy(a.text))
    return dialog


def note_templates(shell: Any) -> list[ai.Template]:
    return ai.active_templates(ai.read_templates(shell.session.settings()), "note_task")


def send_note(shell: Any, note: Any) -> SendDialog:
    """The whole card: its title, its text and its open items."""
    context = ai.note_context(note.title, note.text, [i.text for i in note.open_items()])
    return SendDialog(shell, "note_task", note_templates(shell), context, title=note.title or _("A note"))


def send_note_item(shell: Any, note: Any, item: Any) -> SendDialog:
    """One item as the task; the card's title as its context."""
    context = ai.note_context(note.title, "", [item.text])
    title = item.text if len(item.text) <= TITLE_MAX else item.text[: TITLE_MAX - 1] + "…"
    return SendDialog(shell, "note_task", note_templates(shell), context, title=title)


# ---- the quiet runs


def answer_time(run: QuietRun) -> float:
    return run.duration_ms / 1000 if run.duration_ms is not None else run.duration()


def run_state(run: QuietRun) -> str:
    if run.running:
        return _("working…")
    if not run.ok:
        return _("✗ no answer")
    text = _("✓ answered in {s} s").format(s=round(answer_time(run), 1))
    if run.cost is not None:
        text += f"  ·  ${run.cost:.4f}"
    return text


class AiRuns:
    """The quiet runs of one window: started here, finished on the GUI thread through a
    Waker, each with its result window. `program_argv` replaces the assistant's program
    (the tests run a Python stand-in); the child gets the environment without the
    variables that take it off the user's plan, never all of it."""

    def __init__(self, shell: Any):
        self.shell = shell
        self._wake = Waker(self._pump, owner=shell)
        self.runs = QuietRuns(notify=lambda _run: self._wake())
        self.program_argv: list[str] | None = None
        self.windows: dict[QuietRun, ResultWindow] = {}
        self.hooks: dict[QuietRun, Callable[[str], None]] = {}  # whoever asked wants the answer back
        self._told: weakref.WeakSet[QuietRun] = weakref.WeakSet()

    def argv(self, a: ai.Assignment) -> list[str]:
        model = ai.read_defaults(self.shell.session.settings()).model
        argv = ai.quiet_argv(a, "claude", model, program=ai.claude_command())
        return self.program_argv + argv[1:] if self.program_argv else argv

    def start(self, a: ai.Assignment, on_answer: Callable[[str], None] | None = None) -> QuietRun:
        """`on_answer` — the answer (or why there is none) goes back to it instead of a
        result window of its own."""
        run = self.runs.start(a, self.argv(a), env=ai.plan_env(os.environ))
        self.paint_status()
        if on_answer is None:
            self.open_window(run)
        else:
            self.hooks[run] = on_answer
        if not run.running:
            self._wake()  # it could not start: its notice came before it was in the history
        return run

    def running(self) -> list[QuietRun]:
        return self.runs.running()

    def history(self) -> list[QuietRun]:
        """Newest first."""
        return list(reversed(self.runs.history))

    def open_window(self, run: QuietRun) -> ResultWindow:
        win = self.windows.get(run)
        if win is None or not shiboken6.isValid(win):
            win = self.windows[run] = ResultWindow(self.shell, run, self)
        win.show()
        win.raise_()
        return win

    def forget_window(self, run: QuietRun) -> None:
        self.windows.pop(run, None)

    def paint_status(self) -> None:
        n = len(self.running())
        if n:
            self.shell.set_status(_("AI: working…") + (f" ({n})" if n > 1 else ""))

    def _pump(self) -> None:
        from ..widgets.toast import Toast

        for run in self.runs.every():
            if run.running or run in self._told:
                continue
            self._told.add(run)
            if run in self.runs.overflow:
                self.runs.overflow.remove(run)
            hook = self.hooks.pop(run, None)
            if self.shell.vault is None:
                continue  # locked meanwhile: nothing of the answer is shown
            if hook is not None:
                hook(run.text if run.ok else _("No answer: {error}").format(error=run.error))
            win = self.windows.get(run)
            if win is not None and shiboken6.isValid(win):
                win.refresh()
            if not self.running():
                self.shell.set_status(_("AI: the answer is ready") if run.ok else _("AI: no answer"))
            # the toast outlives a lock: it names no title and quotes nothing of the answer
            Toast(self.shell, f"{GLYPH}  " + _("AI"), _("The answer is ready") if run.ok else _("The assistant did not answer"),
                  action=partial(self._open_if_unlocked, run))

    def _open_if_unlocked(self, run: QuietRun) -> None:
        if self.shell.vault is not None and run in self.runs.history:
            self.open_window(run)

    def on_lock(self) -> None:
        """Stop every quiet run and forget the answers: an answer may quote the vault."""
        self.runs.stop_all()
        self.runs.history.clear()
        self.runs.overflow.clear()
        self.windows.clear()
        self.hooks.clear()

    def close(self) -> None:
        self.runs.stop_all()
        self._wake.close()


def controller(shell: Any) -> AiRuns:
    """The window's quiet runs, made on first use."""
    runs = getattr(shell, "ai_runs", None)
    if runs is None:
        runs = shell.ai_runs = AiRuns(shell)
    return runs


def save_answer(shell: Any, run: QuietRun) -> Any:
    """The answer into today's note of the same title (made when there is none): its first
    line into the text, the whole answer as an attached .md file written owner-only into
    <data>/answers/. The note, or None while locked."""
    from ...vault import write_private

    if shell.vault is None or not run.text:
        return None
    title = run.assignment.title or _("AI answer")
    folder = data_dir(shell) / ANSWERS_DIR
    if not folder.exists():
        folder.mkdir(parents=True, exist_ok=True)
        private(folder, folder=True)
    path = folder / f"{run.assignment.id}-{int(run.started)}.md"
    write_private(path, f"# {title}\n\n{run.text}\n".encode())
    notes = shell.vault.notes
    note = next((n for n in notes.for_day(today()) if n.title == title), None) or notes.add(today(), title=title)
    first = next((line.strip() for line in run.text.splitlines() if line.strip()), "")[:FIRST_LINE_MAX]
    note.text = (note.text.rstrip() + "\n" if note.text.strip() else "") + first
    note.attach(path)
    note.touch()
    shell.mark_dirty("notes")
    page = shell.pages.get("notes")
    if page is not None:
        page.refresh_days(select=today())
    shell.set_status(_("The answer is in today's note “{title}”").format(title=title))
    return note


@guard_events
class ResultWindow(QWidget):
    """One quiet answer (the look of a routine's output window): the text, its time and
    cost, Copy, Save to note, Repeat; Stop while it runs. Closes on lock."""

    def __init__(self, shell: Any, run: QuietRun, owner: AiRuns):
        super().__init__(shell, Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.shell, self.run, self.owner = shell, run, owner
        self.setWindowTitle(f"{run.assignment.title} — {palette.APP_TITLE}")
        self.setObjectName("FileViewer")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.resize(820, 520)
        register(shell, self)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(14, 10, 14, 10)
        head = IconLabel(f"{GLYPH}  {run.assignment.title}", "", self, size=18)
        head.setFont(theme.font(13, bold=True))
        bar.addWidget(head, 1)
        self.state = label("", "muted", self)
        bar.addWidget(self.state)
        bar.addSpacing(12)
        self.stop_btn = button(_("Stop"), "danger", self)
        self.stop_btn.clicked.connect(run.stop)
        self.copy_btn = button(_("Copy"), "small", self)
        self.copy_btn.clicked.connect(self.copy)
        self.save_btn = button(_("Save to note"), "small", self)
        self.save_btn.setToolTip(_("Into today's note of the same title: the first line into its text, the whole answer as an attached file."))
        self.save_btn.clicked.connect(self.save)
        self.again_btn = button(_("Repeat"), "small", self)
        self.again_btn.setToolTip(_("Sends the same text again."))
        self.again_btn.clicked.connect(self.again)
        close = button(_("Close"), "", self)
        close.clicked.connect(self.close)
        for w in (self.stop_btn, self.copy_btn, self.save_btn, self.again_btn, close):
            bar.addWidget(w)
        col.addLayout(bar)
        self.text = QPlainTextEdit(self)
        self.text.setProperty("role", "console")
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.text.setFont(theme.font(10, mono=True))
        col.addWidget(self.text, 1)
        QShortcut(QKeySequence("Escape"), self, self.close)
        self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        paint_caption(self)

    def refresh(self) -> None:
        run = self.run
        t = theme.current()
        self.state.setText(run_state(run))
        done = not run.running
        self.stop_btn.setVisible(not done)
        self.again_btn.setVisible(done)
        self.copy_btn.setEnabled(run.ok)
        self.save_btn.setEnabled(run.ok)
        if done:
            self.state.setStyleSheet(f"color: {t.SUCCESS if run.ok else t.DANGER};")
            self.text.setPlainText(run.text if run.ok else run.error)
        else:
            self.text.setPlainText(_("The assistant is working on it. The answer appears here; you can close this window and open it again from the history."))

    def copy(self) -> None:
        self.shell.copy_to_clipboard(self.run.text, secret=False)

    def save(self) -> None:
        if save_answer(self.shell, self.run) is not None:
            self.save_btn.setEnabled(False)

    def again(self) -> None:
        a = replace(self.run.assignment, created=time.time())
        self.close()
        self.owner.start(a)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.owner.forget_window(self.run)
        event.accept()


class HistoryDialog(Dialog):
    """The quiet answers of this session (the last 20), newest first; one opens its window."""

    def __init__(self, shell: Any):
        super().__init__(shell, _("Recent answers"))
        self.shell = shell
        self.runs = controller(shell).history()
        self.list = QListWidget(self)
        self.list.setMinimumWidth(self.fontMetrics().averageCharWidth() * 80)
        self.list.setMinimumHeight(theme.px(220))
        for run in self.runs:
            when = time.strftime("%H:%M", time.localtime(run.started))
            QListWidgetItem(f"{when}   {run.assignment.title}   ·   {run_state(run)}", self.list)
        if not self.runs:
            QListWidgetItem(_("No answers in this session yet."), self.list).setFlags(Qt.ItemFlag.NoItemFlags)
        self.list.itemActivated.connect(lambda _i: self.open_picked())
        self.box.addWidget(label(_("Kept in memory until the app locks or closes."), "muted", self), 0, 0)
        self.box.addWidget(self.list, 1, 0)
        foot = QHBoxLayout()
        foot.addStretch(1)
        go = button(_("Open"), "primary", self)
        go.clicked.connect(self.open_picked)
        go.setEnabled(bool(self.runs))
        close = button(_("Close"), "", self)
        close.clicked.connect(self.reject)
        foot.addWidget(go)
        foot.addWidget(close)
        self.box.addLayout(foot, 2, 0)
        if self.runs:
            self.list.setCurrentRow(0)
        self.keys(self.open_picked)
        self.open()

    def open_picked(self) -> None:
        row = self.list.currentRow()
        if not 0 <= row < len(self.runs):
            return
        run = self.runs[row]
        self.accept()
        controller(self.shell).open_window(run)

