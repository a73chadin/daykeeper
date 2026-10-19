"""The journal over a period (stats of the shared code): a window with the period menu, the
numbers per column and kind of activity, a heat map of the days, a table by weeks and the
most frequent notes, and the same as Markdown to copy or save.

Not modal: a click on a day of the heat map opens it in the journal behind, and the window
stays for the next one. The heat map is one painted widget (a year is 371 cells, one paint)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import shiboken6
from PySide6.QtCore import QDate, QEvent, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QHelpEvent, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ... import stats
from ...i18n import WEEKDAYS, _, fmt_day, fmt_month
from ...journal import today
from ...journal_layout import Layout, current as current_layout
from .. import theme
from ..errors import guard_events
from ..widgets.common import scroll_area, section, with_icon
from ..widgets.flow import flow_host
from .base import button, label, paint_caption, register, show_error

CUSTOM = "custom"
REPORT_SIZE = (900, 720)
CELL_MAX = 18  # px of a heat map cell at most; it shrinks to fit a long period in the width
CELL_MIN = 6
CELL_GAP = 3


def heat_colours() -> list[str]:
    """The five steps of the heat map in the current theme: an empty day, then the accent
    colour in four strengths over the card."""
    t = theme.current()
    return [theme.blend(t.BORDER, t.CARD, 0.7)] + [theme.blend(t.ACCENT, t.CARD, s) for s in (0.3, 0.52, 0.76, 1.0)]


@guard_events
class HeatMap(QWidget):
    """Weeks as columns, Monday…Sunday as rows; a cell per day of the period. `tip(day)` is
    the hover text of a day, `open_day(day)` runs on a click."""

    def __init__(self, tip: Callable[[str], str], open_day: Callable[[str], object], parent: QWidget | None = None):
        super().__init__(parent)
        self.tip, self.open_day = tip, open_day
        self.per_day: dict[str, int] = {}
        self.levels: dict[str, int] = {}
        self.first_monday = date.today()
        self.weeks = 1
        self.hot = ""
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.small = theme.font(9)

    def set_days(self, per_day: dict[str, int]) -> None:
        self.per_day = per_day
        self.levels = stats.heat_levels(per_day)
        days = sorted(per_day)
        if days:
            first, last = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
            self.first_monday = first - timedelta(days=first.weekday())
            self.weeks = (last - self.first_monday).days // 7 + 1
        else:
            self.first_monday, self.weeks = date.today(), 1
        self.hot = ""
        self.updateGeometry()
        self.update()

    # ---- geometry

    def _left(self) -> int:
        fm = QFontMetrics(self.small)
        return max(fm.horizontalAdvance(_(d)) for d in WEEKDAYS) + 8

    def _top(self) -> int:
        return QFontMetrics(self.small).height() + 4

    def _cell(self, width: int) -> int:
        room = (width - self._left()) // max(1, self.weeks) - CELL_GAP
        return max(CELL_MIN, min(CELL_MAX, room))

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._top() + 7 * (self._cell(width) + CELL_GAP)

    def sizeHint(self) -> QSize:
        width = self._left() + self.weeks * (CELL_MAX + CELL_GAP)
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self) -> QSize:
        width = self._left() + self.weeks * (CELL_MIN + CELL_GAP)
        return QSize(width, self.heightForWidth(width))

    def cell_rect(self, day: str) -> QRect:
        n = (date.fromisoformat(day) - self.first_monday).days
        cell = self._cell(self.width())
        step = cell + CELL_GAP
        return QRect(self._left() + (n // 7) * step, self._top() + (n % 7) * step, cell, cell)

    def day_at(self, pos: QPoint) -> str:
        cell = self._cell(self.width())
        step = cell + CELL_GAP
        x, y = pos.x() - self._left(), pos.y() - self._top()
        if x < 0 or y < 0 or x % step >= cell or y % step >= cell:
            return ""
        week, weekday = x // step, y // step
        if week >= self.weeks or weekday >= 7:
            return ""
        day = (self.first_monday + timedelta(days=week * 7 + weekday)).isoformat()
        return day if day in self.per_day else ""

    # ---- painting

    def month_captions(self) -> list[tuple[int, str]]:
        """(week column, caption): one a month, over the week that holds its first day
        ("Oct 2026"); a period that begins mid-month has its month over the first column.
        The year only on the first caption and on January."""
        out: list[tuple[int, str]] = []
        for week in range(self.weeks):
            monday = self.first_monday + timedelta(days=week * 7)
            days = [d for d in (monday + timedelta(days=k) for k in range(7)) if d.isoformat() in self.per_day]
            first = next((d for d in days if d.day == 1), None)
            if first is None and not out and days:
                first = days[0]  # the period begins mid-month: its month over the first column
            if first is None:
                continue
            out.append((week, fmt_month(first.year, first.month, with_year=len(out) == 0 or first.month == 1)))
        return out

    def paintEvent(self, event: QPaintEvent) -> None:
        t = theme.current()
        colours = [QColor(c) for c in heat_colours()]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(self.small)
        p.setPen(QColor(t.MUTED))
        cell = self._cell(self.width())
        step = cell + CELL_GAP
        top, left = self._top(), self._left()
        fm = QFontMetrics(self.small)
        for row in (0, 2, 4):  # Mon, Wed, Fri, as a contribution graph labels them; a line high even over a small cell
            y = top + row * step + cell // 2 - fm.height() // 2
            p.drawText(QRect(0, y, left - 6, fm.height()), int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), _(WEEKDAYS[row]))
        next_free = left
        for week, text in self.month_captions():
            x = left + week * step
            if x >= next_free:
                p.drawText(QRect(x, 0, 200, top - 4), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text)
                next_free = x + fm.horizontalAdvance(text) + 6
        radius = max(1.5, cell / 5)
        p.setPen(Qt.PenStyle.NoPen)
        for day, level in self.levels.items():
            p.setBrush(colours[level])
            p.drawRoundedRect(self.cell_rect(day), radius, radius)
        if self.hot:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QColor(t.TEXT))
            p.drawRoundedRect(self.cell_rect(self.hot).adjusted(0, 0, -1, -1), radius, radius)
        p.end()

    # ---- the pointer

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        day = self.day_at(event.position().toPoint())
        if day != self.hot:
            self.hot = day
            self.update()

    def leaveEvent(self, event: QEvent) -> None:
        if self.hot:
            self.hot = ""
            self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        day = self.day_at(event.position().toPoint())
        if day and event.button() == Qt.MouseButton.LeftButton:
            self.open_day(day)
            return
        super().mousePressEvent(event)

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip and isinstance(event, QHelpEvent):
            day = self.day_at(event.pos())
            if day:
                QToolTip.showText(event.globalPos(), self.tip(day), self, self.cell_rect(day))
            else:
                QToolTip.hideText()
                event.ignore()
            return True
        return super().event(event)


def number_card(value: int, text: str, parent: QWidget) -> QFrame:
    """A rounded card: a big number over its label."""
    card = QFrame(parent)
    card.setObjectName("Group")
    col = QVBoxLayout(card)
    col.setContentsMargins(16, 10, 16, 10)
    col.setSpacing(2)
    big = QLabel(str(value), card)
    big.setFont(theme.font(17, bold=True))
    col.addWidget(big)
    col.addWidget(label(text, "muted", card))
    card.setProperty("value", value)
    return card


@guard_events
class PeriodReport(QDialog):
    """See the module text. `summary` is what the window shows now."""

    def __init__(self, shell: Any):
        super().__init__(shell)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)  # see dialogs.base.Dialog
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setWindowTitle(_("Journal for a period"))
        self.setModal(False)
        self.shell = shell
        self.layout_: Layout = current_layout()
        register(shell, self)
        w, h = REPORT_SIZE
        geo = shell.screen().availableGeometry() if shell.screen() is not None else None
        if geo is not None:
            w, h = min(w, int(geo.width() * 0.9)), min(h, int(geo.height() * 0.9))
        self.resize(w, h)
        self.setMinimumSize(560, 420)
        p = shell.frameGeometry()
        self.move(p.x() + (p.width() - w) // 2, p.y() + max(0, (p.height() - h) // 3))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(28, 20, 28, 12)
        head.setSpacing(8)
        head.addWidget(label(_("Period"), "", self))
        self.kind = QComboBox(self)
        for kind, text in stats.KIND_LABELS.items():
            self.kind.addItem(_(text), kind)
        self.kind.addItem(_("Own dates"), CUSTOM)
        self.kind.setCurrentIndex(list(stats.KIND_LABELS).index("month"))
        self.kind.activated.connect(lambda _i: self._kind_changed())
        head.addWidget(self.kind)
        self.start = self._date_edit()
        self.dash = label("–", "", self)
        self.end = self._date_edit()
        for widget in (self.start, self.dash, self.end):
            head.addWidget(widget)
        head.addStretch(1)
        self.title = label("", "muted", self)
        head.addWidget(self.title)
        outer.addLayout(head)

        area, inner, col = scroll_area(self, (28, 4, 28, 16))
        self.area = area
        outer.addWidget(area, 1)
        self.cards_host, self.cards = flow_host(inner, 8)
        col.addWidget(self.cards_host)
        col.addSpacing(18)
        col.addWidget(section(_("BY DAY"), inner))
        col.addSpacing(6)
        self.heat = HeatMap(self._day_tip, self.open_day, inner)
        col.addWidget(self.heat)
        legend = QHBoxLayout()
        legend.setSpacing(4)
        legend.addWidget(label(_("Less"), "muted", inner))
        self.swatches: list[QLabel] = []
        for _n in range(5):
            sw = QLabel(inner)
            sw.setFixedSize(12, 12)
            legend.addWidget(sw)
            self.swatches.append(sw)
        legend.addWidget(label(_("More"), "muted", inner))
        legend.addSpacing(16)
        self.days_label = label("", "muted", inner)
        legend.addWidget(self.days_label)
        legend.addStretch(1)
        col.addSpacing(6)
        col.addLayout(legend)
        self.weeks_title = section(_("BY WEEK"), inner)
        col.addSpacing(18)
        col.addWidget(self.weeks_title)
        col.addSpacing(6)
        self.table = QTableWidget(inner)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.verticalHeader().setVisible(False)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setFont(theme.font(10))  # the dialog's text, not the platform's default (larger on macOS)
        self.table.horizontalHeader().setFont(theme.font(10))
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        col.addWidget(self.table)
        self.notes_title = section(_("FREQUENT NOTES"), inner)
        col.addSpacing(18)
        col.addWidget(self.notes_title)
        col.addSpacing(6)
        self.notes = label("", "", inner)
        self.notes.setWordWrap(True)
        self.notes.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        col.addWidget(self.notes)
        col.addStretch(1)

        line = QFrame(self)
        line.setObjectName("Divider")
        line.setFixedHeight(1)
        outer.addWidget(line)
        foot = QHBoxLayout()
        foot.setContentsMargins(28, 12, 28, 16)
        copy = button("", "primary", self)
        with_icon(copy, _("📋  Copy as text"), on_accent=True)
        copy.setToolTip(_("A Markdown table for a letter or a review"))
        copy.clicked.connect(self.copy_text)
        foot.addWidget(copy)
        save = button(_("Save .md"), "", self)
        save.clicked.connect(lambda: self.save_md())
        foot.addWidget(save)
        foot.addStretch(1)
        close = button(_("Close"), "", self)
        close.clicked.connect(self.close)
        foot.addWidget(close)
        outer.addLayout(foot)
        self._kind_changed()

    def _date_edit(self) -> QDateEdit:
        edit = QDateEdit(self)
        edit.setCalendarPopup(True)
        edit.setDisplayFormat("dd.MM.yyyy")
        edit.dateChanged.connect(lambda _d: self._dates_changed())
        return edit

    def showEvent(self, event) -> None:
        super().showEvent(event)
        paint_caption(self)

    def on_lock(self) -> None:
        """The window locks: nothing of its own to keep; `close` follows."""

    # ---- the period

    def _kind_changed(self) -> None:
        kind = self.kind.currentData()
        custom = kind == CUSTOM
        for widget in (self.start, self.dash, self.end):
            widget.setVisible(custom)
        if not custom:
            span = stats.period(kind, date.fromisoformat(today()))
            self._set_dates(span)
            self.show_period(span)
        else:
            self._dates_changed()

    def _set_dates(self, span: stats.Period) -> None:
        for edit, iso in ((self.start, span.start), (self.end, span.end)):
            edit.blockSignals(True)
            edit.setDate(QDate.fromString(iso, "yyyy-MM-dd"))
            edit.blockSignals(False)

    def _dates_changed(self) -> None:
        if self.kind.currentData() != CUSTOM:
            return
        start = self.start.date().toString("yyyy-MM-dd")
        end = self.end.date().toString("yyyy-MM-dd")
        try:
            span = stats.custom(start, end)
        except ValueError:
            return
        self.show_period(span)

    def set_period(self, kind: str, start: str = "", end: str = "") -> None:
        """Pick a period from code (the tests): a kind of stats.KINDS, or CUSTOM with dates."""
        self.kind.setCurrentIndex(self.kind.findData(kind))
        if kind == CUSTOM:
            self._set_dates(stats.custom(start, end))
        self._kind_changed()

    def show_period(self, span: stats.Period) -> None:
        if self.shell.vault is None:
            return
        self.shell.flush_edits()
        self.layout_ = current_layout()
        self.summary = stats.summarize(self.shell.vault.journal, span, self.layout_)
        self.title.setText(_("{start} – {end}").format(start=fmt_day(span.start), end=fmt_day(span.end)))
        self._fill_cards()
        self.heat.set_days(self.summary.per_day)
        for sw, colour in zip(self.swatches, heat_colours(), strict=True):
            sw.setStyleSheet(f"background: {colour}; border-radius: 3px;")
        self.days_label.setText(_("Days with entries: {n} of {total}").format(n=self.summary.days_with_entries, total=len(self.summary.per_day)))
        self._fill_weeks()
        self._fill_notes()

    def labels(self) -> list[tuple[str, str, str]]:
        """(id, "column" or "kind", label) of what the summary counts, in the layout's order."""
        out = [(c.id, "column", _(c.label)) for c in self.layout_.visible_columns() if c.id in self.summary.columns]
        return out + [(k.id, "kind", _(k.label)) for k in self.layout_.visible_kinds() if k.id in self.summary.kinds]

    def _fill_cards(self) -> None:
        while self.cards.count():
            item = self.cards.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.hide()
                w.deleteLater()
        self.card_widgets: dict[str, QFrame] = {}
        for ident, where, text in self.labels():
            value = self.summary.columns[ident] if where == "column" else self.summary.kinds[ident]
            card = number_card(value, text, self.cards_host)
            self.cards.addWidget(card)
            self.card_widgets[ident] = card
        total = number_card(self.summary.total, _("Total"), self.cards_host)
        self.cards.addWidget(total)
        self.card_widgets["_total"] = total
        self.cards_host.updateGeometry()

    def _fill_weeks(self) -> None:
        weeks = self.summary.per_week
        shown = len(weeks) > 1
        self.weeks_title.setVisible(shown)
        self.table.setVisible(shown)
        if not shown:
            return
        labels = self.labels()
        self.table.clear()
        self.table.setColumnCount(len(labels) + 2)
        self.table.setRowCount(len(weeks))
        self.table.setHorizontalHeaderLabels([_("Week")] + [text for _i, _w, text in labels] + [_("Total")])
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        for r, week in enumerate(weeks):
            self.table.setItem(r, 0, QTableWidgetItem(date.fromisoformat(week.start).strftime("%d.%m.%Y")))
            values = [week.columns.get(i, 0) if w == "column" else week.kinds.get(i, 0) for i, w, _t in labels] + [week.total]
            for c, value in enumerate(values, start=1):
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(right)
                self.table.setItem(r, c, item)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)  # measured once here, not on every resize of the window
        self.table.resizeColumnsToContents()
        pad = theme.px(8)  # the native header's margins are not in its size hint on macOS
        for c in range(self.table.columnCount()):
            self.table.setColumnWidth(c, self.table.columnWidth(c) + pad)
        header.setStretchLastSection(False)  # stretched, the last column ran under the scroll bar on macOS
        rows = self.table.verticalHeader()
        height = header.height() + sum(rows.sectionSize(r) for r in range(len(weeks))) + 2 * self.table.frameWidth()
        bar = self.table.horizontalScrollBar()
        self.table.setFixedHeight(height + (bar.sizeHint().height() if self.table.horizontalScrollBarPolicy() != Qt.ScrollBarPolicy.ScrollBarAlwaysOff else 0))

    def _fill_notes(self) -> None:
        notes = self.summary.notes[:10]
        self.notes_title.setVisible(bool(notes))
        self.notes.setVisible(bool(notes))
        self.notes.setText("\n".join(f"•  {text} — {n}" if n > 1 else f"•  {text}" for text, n in notes))

    # ---- the days

    def _day_tip(self, day: str) -> str:
        lines = [fmt_day(day)]
        jday = self.shell.vault.journal.days.get(day) if self.shell.vault is not None else None
        if jday is not None:
            for c in self.layout_.visible_columns():
                n = len(jday.columns.get(c.id, []))
                if n:
                    lines.append(f"{_(c.label)}: {n}")
            for k in self.layout_.visible_kinds():
                n = sum(1 for a in jday.activities if a.get("kind") == k.id)
                if n:
                    lines.append(f"{_(k.label)}: {n}")
        lines.append(_("Total: {n}").format(n=self.summary.per_day.get(day, 0)))
        return "\n".join(lines)

    def open_day(self, day: str) -> bool:
        """Show `day` in the journal behind; a day the journal does not have says so."""
        vault = self.shell.vault
        if vault is None:
            return False
        if day not in vault.journal.days:
            self.shell.set_status(_("No entries on {day}").format(day=fmt_day(day)))
            return False
        self.shell.show_page("journal")
        self.shell.page("journal").open_day(day)
        return True

    # ---- out

    def markdown(self) -> str:
        return stats.as_markdown(self.summary, self.layout_)

    def copy_text(self) -> None:
        self.shell.copy_to_clipboard(self.markdown(), secret=False)
        self.shell.set_status(_("Report copied to the clipboard"))

    def save_md(self, target: str | None = None) -> Path | None:
        if target is None:
            name = f"daykeeper-{self.summary.period.start}-{self.summary.period.end}.md"
            target, _f = QFileDialog.getSaveFileName(self, _("Where to save the report"), name, "Markdown (*.md);;" + _("All files") + " (*.*)")
        if not target or not shiboken6.isValid(self) or self.shell.vault is None:  # locked under the file dialog
            return None
        path = Path(target)
        try:
            path.write_text(self.markdown() + "\n", encoding="utf-8")
        except OSError as exc:
            show_error(self, str(exc))
            return None
        self.shell.set_status(_("Saved: {path}").format(path=path))
        return path


def period_report(shell: Any) -> PeriodReport | None:
    """Open the window over the main one (one call, one window)."""
    if shell.vault is None:
        return None
    dlg = PeriodReport(shell)
    dlg.show()
    return dlg
