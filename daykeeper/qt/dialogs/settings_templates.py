"""Text templates in the Qt window: the "Templates" section of the settings (the list and
an editor) and using a template from the journal or a note card — the questions of the
template in one small form, the text into the clipboard (not a secret), the task link
opened when the template says so. The model is `templates`; this module only lays out
controls and gathers the context."""

from __future__ import annotations

import webbrowser
from datetime import datetime
from typing import Any

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QVBoxLayout, QWidget

from ... import templates as tpl
from ...i18n import _
from ...journal import item_url, normalize_item
from .. import theme
from ..errors import guard_events
from .base import Dialog, ask_yes_no, button, label, show_error

HELP = (
    "{key} {key_url} — the issue key of the selected line and its link; {mr} {mr_url} — the MR; {today}, {date:%d.%m.%Y} — the date; "
    "{me} — your GitLab login; {clipboard} — the clipboard text; {note_title} {note_items} — the note it is inserted from; "
    "{?…} — a question with your words instead of the dots, asked in a form before the text is made. A brace not in this list stays as typed."
)


def stored(shell: Any) -> list[tpl.Template]:
    """The templates of settings.json; the ready-made ones while the section was never
    opened (they are written there on its first opening)."""
    settings = shell.session.settings()
    return tpl.load(settings) if "templates" in settings else tpl.presets()


def place_templates(shell: Any, place: str) -> list[tpl.Template]:
    return [t for t in stored(shell) if place in t.places]


def _uses(t: tpl.Template, *names: str) -> bool:
    return any(m.group(1).partition(":")[0] in names for m in tpl.PLACEHOLDER_RE.finditer(t.text))


def ask_answers(parent: Any, title: str, questions: list[str]) -> dict[str, str] | None:
    """One small form with a field per question; None — cancelled."""
    dlg = Dialog(parent, title)
    fields: list[QLineEdit] = []
    for row, question in enumerate(questions):
        dlg.box.addWidget(label(question, "", dlg), row, 0)
        entry = QLineEdit(dlg)
        entry.setMinimumWidth(entry.fontMetrics().averageCharWidth() * 34)
        dlg.box.addWidget(entry, row, 1)
        fields.append(entry)
    buttons = QHBoxLayout()
    buttons.addStretch(1)
    done = button(_("Copy"), "primary", dlg)
    cancel = button(_("Cancel"), "", dlg)
    done.clicked.connect(dlg.accept)
    cancel.clicked.connect(dlg.reject)
    buttons.addWidget(done)
    buttons.addWidget(cancel)
    dlg.box.addLayout(buttons, len(questions), 0, 1, 2)
    dlg.keys(dlg.accept)
    if fields:
        fields[0].setFocus()
    answers: list[dict[str, str]] = []
    dlg.accepted.connect(lambda: answers.append({q: f.text().strip() for q, f in zip(questions, fields, strict=True)}))
    dlg.exec()
    return answers[0] if answers else None


def use_template(shell: Any, t: tpl.Template, context: dict[str, str], now: datetime | None = None) -> str | None:
    """Fill `t` and copy it; None when the form was cancelled or the window locked."""
    if shell.vault is None:
        return None
    context = dict(context)
    username = shell.vault.integrations.gitlab.username
    if username:
        context["me"] = username
    if _uses(t, "clipboard"):  # read only when asked for: it may hold anything
        context["clipboard"] = shell._qt_clip_get() or ""
    questions = tpl.questions(t)
    key_question = _("Issue key")  # asked when the text uses {key} or {key_url} and no line is selected
    ask_key = "key" not in context and _uses(t, "key", "key_url")
    if ask_key:
        questions = [key_question] + [q for q in questions if q != key_question]
    answers: dict[str, str] = {}
    if questions:
        got = ask_answers(shell, t.name, questions)
        if got is None or shell.vault is None:  # cancelled, or locked under the form
            return None
        answers = got
    if ask_key:
        key = normalize_item(answers.get(key_question, ""))
        context["key"], context["key_url"] = key, item_url(key) if key else ""
    text = tpl.render(t, context, answers, now or datetime.now())
    shell.copy_to_clipboard(text, secret=False)
    shell.set_status(_("“{name}” copied to the clipboard").format(name=t.name))
    url = context.get("key_url", "")
    if t.open_link and url.startswith(("http://", "https://")):
        webbrowser.open(url)
    return text


@guard_events
class TemplatesSection(QWidget):
    """The list on top, the editor of the picked one below; edits go into a copy, "Save"
    writes the list into settings.json."""

    def __init__(self, win: Any):
        from .settings import Section

        super().__init__()
        self.win, self.shell = win, win.shell
        settings = self.shell.session.settings()
        if "templates" not in settings:  # the first opening: the ready-made ones become the user's
            self.shell.session.write_settings({"templates": tpl.save(settings, tpl.presets())["templates"]})
            settings = self.shell.session.settings()
        self.items: list[tpl.Template] = tpl.load(settings)
        self.current: tpl.Template | None = None
        self._loading = False
        self.section = Section(win)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.section)
        s = self.section
        s.head(_("Templates"), _("Texts you type again and again: a verification comment, a defect report, an MR description. The journal's “Template” button and the note card menu fill them in and copy the result."))

        g = s.group(_("Your templates"))
        self.list = QListWidget(s.inner)
        self.list.setMinimumHeight(theme.px(150))
        self.list.currentRowChanged.connect(self._picked)
        g.addWidget(self.list)
        row = QHBoxLayout()
        add = button(_("+ New template"), "small", s.inner)
        add.clicked.connect(self.add)
        row.addWidget(add)
        row.addStretch(1)
        self.delete_btn = button(_("Delete"), "danger", s.inner)
        self.delete_btn.clicked.connect(lambda: self.delete())
        row.addWidget(self.delete_btn)
        g.addLayout(row)

        g = s.group(_("Template"))
        g.addWidget(label(_("Name"), "muted", s.inner))
        self.name = QLineEdit(s.inner)
        self.name.textEdited.connect(self._edited)
        g.addWidget(self.name)
        g.addSpacing(6)
        g.addWidget(label(_("Text"), "muted", s.inner))
        self.text = QPlainTextEdit(s.inner)
        self.text.setFont(theme.font(10, mono=True))
        self.text.setFixedHeight(self.text.fontMetrics().lineSpacing() * 9 + 16)
        self.text.setTabChangesFocus(True)
        self.text.textChanged.connect(self._edited)
        g.addWidget(self.text)
        hint = label(_(HELP), "muted", s.inner)
        hint.setWordWrap(True)
        g.addWidget(hint)
        g.addSpacing(6)
        g.addWidget(label(_("Where to show it"), "muted", s.inner))
        self.places: dict[str, QCheckBox] = {}
        places_row = QHBoxLayout()
        for place in tpl.PLACES:
            box = QCheckBox(_(tpl.PLACE_LABELS[place]), s.inner)
            box.toggled.connect(self._edited)
            places_row.addWidget(box)
            self.places[place] = box
        places_row.addStretch(1)
        g.addLayout(places_row)
        self.open_link = QCheckBox(_("Open the task link after inserting"), s.inner)
        self.open_link.setToolTip(_("After the text is copied, {key_url} opens in the browser"))
        self.open_link.toggled.connect(self._edited)
        g.addWidget(self.open_link)
        self.editor_widgets = [self.name, self.text, self.open_link, self.delete_btn, *self.places.values()]

        save = button(_("Save"), "primary", s)
        save.clicked.connect(lambda: self.save())
        s.foot_box.addWidget(save)
        self.shown_saved = s.saved_label(_("Saved"))
        s.end()
        self.paint(0)

    # ---- the list

    @staticmethod
    def row_text(t: tpl.Template) -> str:
        places = ", ".join(_(tpl.PLACE_LABELS[p]) for p in t.places) or _("nowhere")
        return f"{t.name or _('(no name)')}   ·   {places}"

    def paint(self, select: int) -> None:
        self._loading = True
        try:
            self.list.clear()
            for t in self.items:
                QListWidgetItem(self.row_text(t), self.list)
        finally:
            self._loading = False
        if self.items:
            self.list.setCurrentRow(max(0, min(select, len(self.items) - 1)))
        else:
            self._load(None)

    def _picked(self, row: int) -> None:
        if not self._loading:
            self._load(self.items[row] if 0 <= row < len(self.items) else None)

    def _load(self, t: tpl.Template | None) -> None:
        self.current = t
        self._loading = True
        try:
            for w in self.editor_widgets:
                w.setEnabled(t is not None)
            self.name.setText(t.name if t else "")
            self.text.setPlainText(t.text if t else "")
            for place, box in self.places.items():
                box.setChecked(t is not None and place in t.places)
            self.open_link.setChecked(bool(t and t.open_link))
        finally:
            self._loading = False

    def _edited(self, *_args: object) -> None:
        t = self.current
        if self._loading or t is None:
            return
        t.name = self.name.text()
        t.text = self.text.toPlainText()
        t.places = [p for p in tpl.PLACES if self.places[p].isChecked()]
        t.open_link = self.open_link.isChecked()
        item = self.list.item(self.items.index(t))
        if item is not None:
            item.setText(self.row_text(t))

    def add(self) -> None:
        import uuid

        self.items.append(tpl.Template(uuid.uuid4().hex, _("New template"), "{key_url}\n"))
        self.paint(len(self.items) - 1)
        self.name.setFocus()
        self.name.selectAll()

    def delete(self, confirm: bool = True) -> None:
        t = self.current
        if t is None:
            return
        if confirm and not ask_yes_no(self.win, _("Delete the template “{name}”?").format(name=t.name)):
            return
        index = self.items.index(t)
        del self.items[index]
        self.paint(index)

    # ---- save

    def save(self) -> bool:
        empty = [t for t in self.items if not t.name.strip() or not t.text.strip()]
        if empty:
            self.paint(self.items.index(empty[0]))
            show_error(self.win, _("A template needs a name and a text."))
            return False
        settings = self.shell.session.settings()
        if not self.shell.session.write_settings({"templates": tpl.save(settings, self.items)["templates"]}):
            show_error(self.win, _("Could not write settings.json"))
            return False
        self.shell.set_status(_("Templates saved"))
        self.shown_saved()
        return True
