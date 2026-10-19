"""Import from another password manager's plain export (importers.py): KeePass XML / CSV,
Bitwarden JSON / CSV. The file is checked by its size before it is read, its format is
told by its content, and the plan — counts only, no names and no values — is shown before
anything is written. The vault as it was is kept beside it first, as the full import does;
the export lies on disk in the open, so it is deleted afterwards unless the user says no.

`import_other_dialog` returns the dialog it opened (None when it ended before one);
`source` stands in for the file dialog (the tests pass it)."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QButtonGroup, QCheckBox, QFileDialog, QHBoxLayout, QLabel, QRadioButton

from ... import importers
from ...i18n import _
from .. import theme
from .base import Dialog, button, label, show_error, show_info


def format_name(fmt: str) -> str:
    return {
        importers.KEEPASS_XML: "KeePass XML",
        importers.KEEPASS_CSV: "KeePass CSV",
        importers.BITWARDEN_JSON: "Bitwarden JSON",
        importers.BITWARDEN_CSV: "Bitwarden CSV",
    }.get(fmt, fmt)


def keep_copy(shell: Any) -> Path:
    """The vault as it is now, as `<name>-before-import-<time>.pkv` beside it (the name the
    full import gives: Settings → Data → Copies lists it). OSError comes through."""
    path = shell.vault_path
    stamp = time.strftime("%Y%m%d-%H%M%S")
    keep = path.with_name(f"{path.stem}-before-import-{stamp}{path.suffix}")
    shell.vault.save_copy(keep)
    return keep


def _load(shell: Any, source: str) -> tuple[str, list[importers.Imported]] | None:
    """(format, items) of the export; None after telling the user why not."""
    try:
        importers.check_size(source)  # before a byte is read
        data = Path(source).read_bytes()
        fmt = importers.detect(data, Path(source).suffix)
        if fmt is None:
            raise importers.ImportFailed(_("This is not a KeePass or Bitwarden export"))
        items = importers.read(data, fmt)
    except importers.ImportFailed as exc:
        show_error(shell, str(exc))
        return None
    except OSError as exc:
        show_error(shell, _("Could not read the file:\n{error}").format(error=exc))
        return None
    if not items:
        show_info(shell, _("The export has no entries."))
        return None
    return fmt, items


def _drop_plan(plan: importers.ImportPlan | None) -> list[importers.Imported]:
    """Empty a plan that is replaced or done (its matches hold copies of the missing
    values); the items it held are returned, their fields still in them."""
    if plan is None:
        return []
    held = plan.new + [m.item for m in plan.matches]
    for m in plan.matches:
        m.missing.clear()
    plan.new.clear()
    plan.matches.clear()
    return held


def import_other_dialog(shell: Any, source: str | None = None) -> Dialog | None:
    """Pick an export, show the plan, write it (see the module text)."""
    if shell.vault is None:
        return None
    shell.save_now()
    if source is None:
        source, _f = QFileDialog.getOpenFileName(shell, _("A KeePass or Bitwarden export"), "", _("Exports") + " (*.xml *.csv *.json);;" + _("All files") + " (*.*)")
    if not source or shell.vault is None:  # the idle lock does not wait for a file dialog
        return None
    loaded = _load(shell, source)
    if loaded is None or shell.vault is None:
        return None
    fmt, items = loaded
    state: dict[str, Any] = {"plan": importers.plan(items, shell.vault, merge=False)}

    dlg = Dialog(shell, _("Import from KeePass or Bitwarden"))
    head = QLabel(_("Entries found: {n}").format(n=len(items)) + f" · {format_name(fmt)}", dlg)
    head.setFont(theme.font(13, bold=True))
    dlg.box.addWidget(head, 0, 0)
    dlg.box.addWidget(label(_("Groups and folders become lists; the main list gets what had no group.\nNothing is written until you press Import. Values are not shown here."), "muted", dlg), 1, 0)
    summary = label("", "", dlg)
    dlg.box.addWidget(summary, 2, 0)
    matches = len(state["plan"].matches)
    choice = QButtonGroup(dlg)
    skip = QRadioButton(_("Skip them"), dlg)
    merge = QRadioButton(_("Add the fields they lack (values already there stay)"), dlg)
    skip.setChecked(True)
    choice.addButton(skip)
    choice.addButton(merge)
    if matches:
        dlg.box.addWidget(label(_("Entries with the same name in the same list already in the vault: {n}").format(n=matches), "", dlg), 3, 0)
        dlg.box.addWidget(skip, 4, 0)
        dlg.box.addWidget(merge, 5, 0)
    else:
        skip.hide()
        merge.hide()
    remove = QCheckBox(_("Delete the export file after import"), dlg)
    remove.setChecked(True)
    remove.setToolTip(_("The export holds every password in plain text."))
    dlg.box.addWidget(remove, 6, 0)
    dlg.box.addWidget(label(str(source), "muted", dlg), 7, 0)
    err = label("", "error", dlg)
    dlg.box.addWidget(err, 8, 0)

    def replan() -> None:
        if shell.vault is None or "plan" not in state:  # wiped: the dialog is done
            return
        old, state["plan"] = state["plan"], importers.plan(items, shell.vault, merge=merge.isChecked())
        _drop_plan(old)
        summary.setText(_("Will be added: {summary}").format(summary=state["plan"].summary()))

    merge.toggled.connect(lambda _on: replan())
    replan()

    def apply() -> None:
        if shell.vault is None or "plan" not in state:
            return
        replan()
        try:
            keep = keep_copy(shell)
        except OSError as exc:
            err.setText(_("Could not keep a copy of the vault: {error}").format(error=exc))
            return
        entries, fields = importers.apply(state["plan"], shell.vault)
        dlg.accept()  # wipe() drops the plain values
        shell.page("vault").reload()  # marks the vault changed: one undo step, saved soon
        shell.save_now()
        result = _("entries: {entries}, fields: {fields}").format(entries=entries, fields=fields)
        if remove.isChecked():
            try:
                os.remove(source)
            except OSError as exc:
                result += " · " + _("the export file was not deleted: {error}").format(error=exc)
            else:
                result += " · " + _("the export file was deleted")
        shell.set_status(_("Import: {summary}").format(summary=result) + " · " + _("the vault as it was: {path}").format(path=keep.name))

    row = QHBoxLayout()
    row.addStretch(1)
    go = button(_("Import"), "primary", dlg)
    go.clicked.connect(apply)
    cancel = button(_("Cancel"), "", dlg)
    cancel.clicked.connect(dlg.reject)
    row.addWidget(go)
    row.addWidget(cancel)
    dlg.box.addLayout(row, 9, 0)
    def wipe(_result: int = 0) -> None:
        """Import or cancel: the plain passwords of the export go — out of the items, the
        plan, the state and the parts the tests reach (a string cannot be overwritten in
        Python; what can be done is to leave no reference to it)."""
        lost = list(items) + _drop_plan(state.pop("plan", None))
        for item in lost:
            item.fields.clear()
            item.note = ""
        items.clear()
        state.clear()
        parts = getattr(dlg, "parts", None)
        if isinstance(parts, dict):
            parts.pop("apply", None)
            parts.pop("plan", None)

    dlg.finished.connect(wipe)
    dlg.keys(apply)
    dlg.open()
    dlg.parts = {"skip": skip, "merge": merge, "remove": remove, "summary": summary, "error": err, "apply": apply, "plan": lambda: state.get("plan")}  # type: ignore[attr-defined]
    return dlg
