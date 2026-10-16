"""Export entries to a readable UNENCRYPTED file: text for a human or CSV for spreadsheets."""

from __future__ import annotations

import csv
import io
import time

from .i18n import _
from .settings import APP_NAME
from .vault import MAIN_GROUP, Entry, Field, main_group_label

RULE = "─" * 64
HEAVY_RULE = "═" * 64
WARNING = "WARNING: this file is not encrypted. Delete it once it is no longer needed."


def _stamp(ts: float) -> str:
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(ts))


def _sorted(entries: list[Entry]) -> list[Entry]:
    """As on screen: the main list first, then the other lists alphabetically, manual order inside."""
    return sorted(entries, key=lambda e: (e.group != MAIN_GROUP, e.group.lower(), e.order))


def _group_label(e: Entry) -> str:
    return e.group or main_group_label()


def export_text(entries: list[Entry], now: float | None = None) -> str:
    """Text: a header, then every entry as a block «name / kind / fields / note»."""
    out = [
        _("{app} — export from {stamp}").format(app=APP_NAME, stamp=_stamp(now if now is not None else time.time())),
        _("Entries: {n}").format(n=len(entries)),
        "",
        _(WARNING),
        "",
    ]
    for e in _sorted(entries):
        out += [HEAVY_RULE, e.name or _("(untitled)"), _("Kind: {kind} · List: {group}").format(kind=e.kind_label, group=_group_label(e)), RULE]
        width = max((len(f.key) for f in e.fields), default=0)
        for f in e.fields:
            key = f.key or _("(field)")
            value = f.value
            if "\n" in value:  # multi-line value — on new lines, indented
                value = "\n" + "\n".join("    " + line for line in value.splitlines())
            out.append(f"{key.ljust(width)}  {value}")
        if e.note.strip():
            out.append("")
            out.append(_("Note:"))
            out += ["    " + line for line in e.note.splitlines()]
        out.append(_("Updated: {stamp}").format(stamp=_stamp(e.updated)))
        out.append("")
    return "\n".join(out)


CSV_HEADER = ["Name", "Kind", "Field", "Value", "Note", "Updated", "List"]  # UI keys
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_cell(value: str) -> str:
    """A cell Excel and LibreOffice show as text: a value starting like a formula gets a
    leading apostrophe, so “=HYPERLINK(…)” in a field is not executed when the file opens."""
    return "'" + value if value.startswith(FORMULA_START) else value


def export_csv(entries: list[Entry]) -> str:
    """CSV in the "long" shape: one row per field, the note on the first row of the entry."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([_(h) for h in CSV_HEADER])  # fixed words, no formula risk
    for e in _sorted(entries):
        rows: list[Field | None] = list(e.fields) or [None]
        for i, f in enumerate(rows):
            writer.writerow(
                csv_cell(cell)
                for cell in [
                    e.name,
                    e.kind_label,
                    f.key if f else "",
                    f.value if f else "",
                    e.note if i == 0 else "",
                    _stamp(e.updated),
                    _group_label(e),
                ]
            )
    return buf.getvalue()
