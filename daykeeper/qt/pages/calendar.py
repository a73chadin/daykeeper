"""The Calendar tab of the Qt window: a month grid that gathers what every tab holds for a
day (calendar_model.month_cells), the panel of the chosen day, the editor of an own
event, the import of an .ics file, and the reminders of the events.

The grid is one painted widget, as the tiles are (widgets/tiles.py): 42 cells in one
paint, so a resize or a month switch only moves rectangles. A cell shows its day number
and, under it, up to three chips: the own events, the expiring keys and the routines
with a time; the counters (journal items, open/total note items, integration events)
sit beside the number, or take a chip line when the cell is too narrow for them.

The reminders (`CalendarReminders`) are the window's, made once and kept through a lock
like the runs of routines; while the vault is locked they are silent."""

from __future__ import annotations

import os
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from datetime import date, datetime, timedelta
from typing import Any

import shiboken6
from PySide6.QtCore import QDate, QEvent, QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QHelpEvent, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QComboBox, QDateEdit, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidgetItem, QPlainTextEdit, QSizePolicy, QToolTip, QVBoxLayout, QWidget

from ...calendar_model import MAX_REMIND_MIN, REPEATS, TIME_RE, CalEvent, DayCell, month_cells, read_ics
from ...expiry import Expiry, expiring
from ...i18n import WEEKDAYS, _, fmt_day
from ...journal import today
from .. import icons, theme
from ..dialogs.base import Dialog, button, label, show_error
from ..dialogs.expiry import field_label
from ..errors import guard_events
from ..timers import Ticker
from ..widgets.common import icon_button, more_button, section
from ..widgets.countlist import KEY_ROLE, add_count_item, count_list
from ..widgets.toast import Toast
from ..widgets.tiles import elide, glyph_px, rounded
from .base import TabPage

MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")  # UI keys
REPEAT_LABELS = {"": "Does not repeat", "weekly": "Every week", "monthly": "Every month", "yearly": "Every year"}
VIEW_FROM = ("journal", "notes", "routines", "vault", "integrations", "calendar")  # what the grid draws from
REMIND_TICK_MS = 30_000
MAX_ICS_BYTES = 20 * 1024 * 1024  # a calendar export is far smaller; a bigger file is not read at all
EXPIRY_HORIZON = 100 * 366  # days: every key with a date shows on its day, however far
CHIP_LINES = 3
CELL_PAD = 6  # px inside a cell
CHIP_GAP = 2  # px between chip lines
REMIND_CHOICES = (0, 5, 10, 15, 30, 60, 120, 24 * 60, MAX_REMIND_MIN)  # minutes before the start
NO_END = QDate(2000, 1, 1)  # the "until" field at this date means: no end
_views: weakref.WeakKeyDictionary[Any, tuple[int, int, str]] = weakref.WeakKeyDictionary()  # the month a window looked at


def month_title(year: int, month: int) -> str:
    return f"{_(MONTHS[month - 1])} {year}"


def remind_label(minutes: int) -> str:
    if minutes <= 0:
        return _("no reminder")
    if minutes % (24 * 60) == 0:
        return _("{n} d before").format(n=minutes // (24 * 60))
    if minutes % 60 == 0:
        return _("{n} h before").format(n=minutes // 60)
    return _("{n} min before").format(n=minutes)


def http_link(url: str) -> bool:
    """Only a web page is ever opened from an event: never a program or a file."""
    return url.strip().lower().startswith(("https://", "http://"))


def normal_time(text: str) -> str | None:
    """"9:30" → "09:30"; "" → "" (all day); None — not a time."""
    text = text.strip()
    if not text:
        return ""
    if len(text) == 4 and text[1] == ":":
        text = "0" + text
    return text if TIME_RE.match(text) else None


@dataclass
class Chip:
    icon: str  # the picture's name (icons.DRAW)
    text: str
    kind: str  # "event", "expiry", "routine" — a line; "journal", "notes", "feed" — a counter


def chips(cell: DayCell) -> list[Chip]:
    """Everything a cell can show, the lines first (events, expiring keys, routines), then
    the counters."""
    out = [Chip("bell", f"{e.time} {e.title or _('(untitled)')}".strip(), "event") for e in cell.events]
    out += [Chip("key", name or _("(untitled)"), "expiry") for name in cell.expiries]
    out += [Chip("bolt", text, "routine") for text in cell.routines]
    if cell.journal:
        out.append(Chip("journal", str(cell.journal), "journal"))
    if cell.notes_total:
        out.append(Chip("note", f"{cell.notes_open}/{cell.notes_total}", "notes"))
    if cell.feed:
        out.append(Chip("plug", str(cell.feed), "feed"))
    return out


def day_tip(cell: DayCell) -> str:
    names = {"journal": _("Journal items"), "notes": _("Open note items"), "feed": _("Integration events")}
    lines = [fmt_day(cell.date)]
    for c in chips(cell):
        lines.append(f"{names[c.kind]}: {c.text}" if c.kind in names else c.text)
    return "\n".join(lines)


# ---------------------------------------------------------------- the month grid


@guard_events
class MonthGrid(QWidget):
    """Six weeks of seven days, Monday first, in one widget. `pick(date)` on a click or
    an arrow key, `activate(date)` on a double click or Enter."""

    def __init__(self, pick: Callable[[str], object], activate: Callable[[str], object], parent: QWidget | None = None):
        super().__init__(parent)
        self.pick, self.activate = pick, activate
        self.rows: list[list[DayCell]] = []
        self.flat: list[DayCell] = []
        self.selected = ""
        self.hot = -1
        self.fonts = {"head": theme.font(9, bold=True), "day": theme.font(10, bold=True), "chip": theme.font(9)}
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_cells(self, rows: list[list[DayCell]], selected: str) -> None:
        self.rows = rows
        self.flat = [c for row in rows for c in row]
        self.selected = selected
        self.hot = -1
        self.update()

    def select(self, day: str) -> None:
        self.selected = day
        self.update()

    def index_of(self, day: str) -> int:
        return next((i for i, c in enumerate(self.flat) if c.date == day), -1)

    # ---- geometry

    def header_height(self) -> int:
        return QFontMetrics(self.fonts["head"]).height() + 10

    def cell_rect(self, index: int) -> QRect:
        top = self.header_height()
        w, h = self.width() / 7, (self.height() - top) / 6
        row, col = divmod(index, 7)
        x0, x1 = round(col * w), round((col + 1) * w)
        y0, y1 = top + round(row * h), top + round((row + 1) * h)
        return QRect(x0, y0, x1 - x0, y1 - y0)

    def index_at(self, pos: QPoint) -> int:
        top = self.header_height()
        if pos.y() < top or not self.flat or self.width() <= 0:
            return -1
        col = min(6, max(0, int(pos.x() * 7 / self.width())))
        row = min(5, max(0, int((pos.y() - top) * 6 / max(1, self.height() - top))))
        i = row * 7 + col
        return i if i < len(self.flat) else -1

    def sizeHint(self) -> QSize:
        return QSize(theme.px(700), theme.px(520))

    def minimumSizeHint(self) -> QSize:
        return QSize(theme.px(7 * 52), theme.px(6 * 48))

    # ---- painting

    def paintEvent(self, event: QPaintEvent) -> None:
        t = theme.current()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(t.CARD))
        head = self.header_height()
        p.setFont(self.fonts["head"])
        for col, name in enumerate(WEEKDAYS):
            r = self.cell_rect(col)
            p.setPen(QColor(t.MUTED))
            p.drawText(QRect(r.x(), 0, r.width(), head), int(Qt.AlignmentFlag.AlignCenter), _(name))
        clip = event.rect()
        for i, cell in enumerate(self.flat):
            r = self.cell_rect(i)
            if r.intersects(clip):
                self._paint_cell(p, r, cell, i)
        p.setPen(QPen(QColor(t.BORDER), 1))
        for row in range(7):
            y = self.cell_rect(row * 7).y() if row < 6 else self.height() - 1
            p.drawLine(0, y, self.width(), y)
        for col in range(1, 7):
            x = self.cell_rect(col).x()
            p.drawLine(x, head, x, self.height())
        p.end()

    def _outline(self, p: QPainter, r: QRect, colour: str) -> None:
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(colour), 2))
        p.drawRoundedRect(QRectF(r).adjusted(2, 2, -2, -2), 4, 4)

    def _paint_cell(self, p: QPainter, r: QRect, cell: DayCell, index: int) -> None:
        t = theme.current()
        base = t.SIDEBAR if cell.weekend else t.CARD
        if cell.date == self.selected:
            base = theme.blend(t.ACCENT_SOFT, base, 0.7)
        elif index == self.hot:
            base = t.HOVER
        p.fillRect(r, QColor(base))
        if cell.today:
            self._outline(p, r, t.ACCENT)
        p.save()
        if not cell.in_month:
            p.setOpacity(0.45)
        inner = r.adjusted(CELL_PAD, CELL_PAD - 2, -CELL_PAD, -CELL_PAD)
        day_fm = QFontMetrics(self.fonts["day"])
        chip_fm = QFontMetrics(self.fonts["chip"])
        number = str(int(cell.date[8:10]))
        p.setFont(self.fonts["day"])
        p.setPen(QColor(t.LINK if cell.today else (t.MUTED if cell.weekend else t.TEXT)))
        number_w = day_fm.horizontalAdvance(number)
        p.drawText(QRect(inner.x(), inner.y(), number_w + 2, day_fm.height()), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), number)
        all_chips = chips(cell)
        lines = [c for c in all_chips if c.kind in ("event", "expiry", "routine")]
        counters = [c for c in all_chips if c not in lines]
        p.setFont(self.fonts["chip"])
        side = glyph_px(chip_fm)
        room = inner.width() - number_w - 8
        counters_w = sum(side + 3 + chip_fm.horizontalAdvance(c.text) for c in counters) + 6 * max(0, len(counters) - 1)
        line_h = chip_fm.height() + 2
        y = inner.y() + day_fm.height() + 3
        if counters and counters_w <= room:
            self._counters(p, QRect(inner.right() - counters_w + 1, inner.y(), counters_w, day_fm.height()), counters, chip_fm, side)
        elif counters:
            lines = [Chip("", "", "counters")] + lines
        fit = max(0, min(CHIP_LINES, (inner.bottom() + 1 - y + CHIP_GAP) // (line_h + CHIP_GAP)))
        shown = lines if len(lines) <= fit else lines[:max(0, fit - 1)]
        for c in shown:
            box = QRect(inner.x(), y, inner.width(), line_h)
            if c.kind == "counters":
                self._counters(p, box, counters, chip_fm, side)
            else:
                self._chip(p, box, c, chip_fm, side)
            y += line_h + CHIP_GAP
        if len(shown) < len(lines) and fit > 0:
            p.setPen(QColor(t.MUTED))
            p.drawText(QRect(inner.x() + 4, y, inner.width() - 4, line_h), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), _("+{n} more").format(n=len(lines) - len(shown)))
        p.restore()

    def _counters(self, p: QPainter, box: QRect, counters: list[Chip], fm: QFontMetrics, side: int) -> None:
        colour = theme.current().MUTED
        x = box.x()
        for c in counters:
            w = fm.horizontalAdvance(c.text)
            if x + side + 3 + w > box.right() + 1:
                break
            icons.paint(p, c.icon, QRect(x, box.y(), side, box.height()), colour, size=side)
            p.setPen(QColor(colour))
            p.drawText(QRect(x + side + 3, box.y(), w + 1, box.height()), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), c.text)
            x += side + 3 + w + 6

    def _chip(self, p: QPainter, box: QRect, c: Chip, fm: QFontMetrics, side: int) -> None:
        t = theme.current()
        if c.kind == "event":
            fill, ink = t.ACCENT_SOFT, t.ACCENT_INK
        elif c.kind == "expiry":
            fill, ink = t.DANGER_SOFT, t.DANGER
        else:
            fill, ink = "", t.MUTED
        if fill:
            rounded(p, QRectF(box), 4, fill)
        x = box.x() + 4
        icons.paint(p, c.icon, QRect(x, box.y(), side, box.height()), ink, size=side)
        x += side + 4
        p.setPen(QColor(ink))
        p.drawText(QRect(x, box.y(), box.right() - x - 2, box.height()), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), elide(fm, c.text, box.right() - x - 2))

    # ---- the pointer and the keys

    def mousePressEvent(self, event: QMouseEvent) -> None:
        i = self.index_at(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and i >= 0:
            self.setFocus()
            self.pick(self.flat[i].date)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        i = self.index_at(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and i >= 0:
            self.activate(self.flat[i].date)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        i = self.index_at(event.position().toPoint())
        if i != self.hot:
            old, self.hot = self.hot, i
            for k in (old, i):
                if k >= 0:
                    self.update(self.cell_rect(k))
            self.setCursor(Qt.CursorShape.PointingHandCursor if i >= 0 else Qt.CursorShape.ArrowCursor)

    def leaveEvent(self, event: QEvent) -> None:
        if self.hot >= 0:
            old, self.hot = self.hot, -1
            self.update(self.cell_rect(old))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        steps = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1, Qt.Key.Key_Up: -7, Qt.Key.Key_Down: 7}
        key = Qt.Key(event.key())
        if key in steps and self.selected:
            self.pick((date.fromisoformat(self.selected) + timedelta(days=steps[key])).isoformat())
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.selected:
            self.activate(self.selected)
            return
        super().keyPressEvent(event)

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip and isinstance(event, QHelpEvent):
            i = self.index_at(event.pos())
            if i >= 0:
                QToolTip.showText(event.globalPos(), day_tip(self.flat[i]), self, self.cell_rect(i))
            else:
                QToolTip.hideText()
            return True
        return super().event(event)


# ---------------------------------------------------------------- the tab


class CalendarPage(TabPage):
    """The month grid on the right, the chosen day on the left."""

    name = "calendar"

    def build(self) -> None:
        now = date.fromisoformat(today())
        self.year, self.month, self.selected = _views.get(self.shell, (now.year, now.month, now.isoformat()))
        self.rows: list[list[DayCell]] = []
        self.day_expiries: list[Expiry] = []
        self._found: list[Expiry] = []
        self.dialog_parts: dict[str, Any] = {}
        self.last_dialog: Dialog | None = None
        _splitter, left, col, right = self.split(300, 240)
        self.day_title = QLabel("", left)
        self.day_title.setFont(theme.font(13, bold=True))
        col.addWidget(self.day_title)
        col.addWidget(section(_("ON THIS DAY"), left))
        self.day_list = count_list(left)
        self.day_list.itemActivated.connect(self._open_row)
        self.day_list.setToolTip(_("Double-click opens the item"))
        col.addWidget(self.day_list, 1)
        self.journal_btn = button(_("Open the journal day"), "", left)
        self.journal_btn.clicked.connect(self.open_journal)
        self.notes_btn = button(_("Notes of the day"), "", left)
        self.notes_btn.clicked.connect(self.open_notes)
        self.add_btn = button(_("+ Event"), "primary", left)
        self.add_btn.clicked.connect(lambda: self.event_dialog(None))
        for b in (self.journal_btn, self.notes_btn, self.add_btn):
            col.addWidget(b)

        body = QVBoxLayout(right)
        body.setContentsMargins(24, 18, 24, 20)
        body.setSpacing(12)
        top = QHBoxLayout()
        top.setSpacing(4)
        self.prev_btn = icon_button("prev", _("Previous month"), lambda: self.shift(-1), right)
        self.next_btn = icon_button("next", _("Next month"), lambda: self.shift(+1), right)
        self.title = QLabel("", right)
        self.title.setFont(theme.font(15, bold=True))
        self.today_btn = button(_("Today"), "", right)
        self.today_btn.clicked.connect(lambda: self.go_to(today()))
        more = more_button([(_("Import .ics…"), lambda: self.import_ics())], _("Meetings from a calendar file (Outlook, Teams, Google)"), right)
        top.addWidget(self.prev_btn)
        top.addWidget(self.next_btn)
        top.addSpacing(8)
        top.addWidget(self.title)
        top.addStretch(1)
        top.addWidget(self.today_btn)
        top.addWidget(more)
        body.addLayout(top)
        self.grid = MonthGrid(self.pick, lambda day: self.event_dialog(None, day), right)
        body.addWidget(self.grid, 1)

    # ---- what the window asks for

    def view_key(self) -> tuple:
        return self.shell.session.rev.of(*VIEW_FROM) + (today(), self.secrets_open())

    def secrets_open(self) -> bool:
        """Behind the Passwords guard a key that runs out shows no entry name."""
        security = getattr(self.shell, "security", None)
        return security is None or bool(security.secrets_open())

    def refresh(self, keep_scroll: bool = False) -> None:
        self.show_month()

    def new_item(self) -> None:
        self.event_dialog(None)

    def cancel_jobs(self) -> None:
        if self.last_dialog is not None and shiboken6.isValid(self.last_dialog):
            self.last_dialog.close()

    # ---- the month

    def expiries(self) -> list[Expiry]:
        """Every key with a date: the grid shows the day it runs out, dismissed or not."""
        if self.vault is None:
            return []
        return expiring(self.vault, date.fromisoformat(today()), EXPIRY_HORIZON, {})

    def show_month(self) -> None:
        if self.vault is None:
            return
        found = self.expiries()
        self.rows = month_cells(self.year, self.month, self.vault, found, today())
        if not self.secrets_open():
            for row in self.rows:
                for c in row:
                    c.expiries = [_("key expires")] * len(c.expiries)
        self._found = found
        self.title.setText(month_title(self.year, self.month))
        cell = self.cell(self.selected)
        if cell is None or not cell.in_month:
            self.selected = date(self.year, self.month, 1).isoformat()
        self.grid.set_cells(self.rows, self.selected)
        self.fill_day()
        _views[self.shell] = (self.year, self.month, self.selected)

    def go_to(self, day: str) -> None:
        d = date.fromisoformat(day)
        self.year, self.month, self.selected = d.year, d.month, day
        self.show_month()

    def shift(self, step: int) -> None:
        n = self.year * 12 + self.month - 1 + step
        year, month = divmod(n, 12)
        month += 1
        now = date.fromisoformat(today())
        self.go_to(now.isoformat() if (now.year, now.month) == (year, month) else date(year, month, 1).isoformat())

    def pick(self, day: str) -> None:
        cell = self.cell(day)
        if cell is None or not cell.in_month:
            self.go_to(day)
            return
        self.selected = day
        self.grid.select(day)
        self.fill_day()
        _views[self.shell] = (self.year, self.month, self.selected)

    def cell(self, day: str) -> DayCell | None:
        return next((c for row in self.rows for c in row if c.date == day), None)

    # ---- the chosen day

    def fill_day(self) -> None:
        cell = self.cell(self.selected)
        if cell is None or self.vault is None:
            return
        self.day_title.setText(fmt_day(cell.date) + (_("  · today") if cell.today else ""))
        lst = self.day_list
        lst.clear()
        for e in cell.events:
            add_count_item(lst, "  🔔 " + (e.title or _("(untitled)")), "event:" + e.id, e.time or _("all day"))
        self.day_expiries = [x for x in self._found if x.when.isoformat() == cell.date]
        for i, x in enumerate(self.day_expiries):
            text = (x.entry_name or _("(untitled)")) + " · " + _(field_label(x.field)) if self.secrets_open() else _("key expires")
            add_count_item(lst, "  🔑 " + text, f"expiry:{i}", _("expires"))
        for text in cell.routines:
            at, _sep, name = text.partition(" ")
            add_count_item(lst, "  ⚡ " + (name or at), "routines", at if name else "")
        if cell.journal:
            add_count_item(lst, "  📓 " + _("Journal items"), "journal", cell.journal)
        if cell.notes_total:
            add_count_item(lst, "  🗒 " + _("Open note items"), "notes", f"{cell.notes_open} / {cell.notes_total}")
        if cell.feed:
            add_count_item(lst, "  🔌 " + _("Integration events"), "integrations", cell.feed)
        if not lst.count():
            empty = QListWidgetItem("  " + _("Nothing on this day"), lst)
            empty.setData(KEY_ROLE, "")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
        is_today = cell.date == today()
        self.journal_btn.setEnabled(is_today or cell.date in self.vault.journal.days)
        self.notes_btn.setEnabled(is_today or bool(self.vault.notes.for_day(cell.date)))

    def row_keys(self) -> list[str]:
        return [str(self.day_list.item(i).data(KEY_ROLE) or "") for i in range(self.day_list.count())]

    def _open_row(self, item: QListWidgetItem) -> None:
        key = str(item.data(KEY_ROLE) or "")
        if key.startswith("event:") and self.vault is not None:
            event = self.vault.calendar.get(key.split(":", 1)[1])
            if event is not None:
                self.event_dialog(event)
        elif key.startswith("expiry:"):
            i = int(key.split(":", 1)[1])
            if 0 <= i < len(self.day_expiries):
                self.shell.expiry.open_entry(self.day_expiries[i])
        elif key == "journal":
            self.open_journal()
        elif key == "notes":
            self.open_notes()
        elif key in ("routines", "integrations"):
            self.shell.show_page(key)

    def open_journal(self) -> None:
        day = self.selected
        self.shell.show_page("journal")
        if self.shell.vault is not None and day in self.shell.vault.journal.days:
            self.shell.page("journal").open_day(day)

    def open_notes(self) -> None:
        day = self.selected
        self.shell.show_page("notes")
        if self.shell.vault is not None:
            self.shell.page("notes").refresh_days(select=day)

    # ---- the editor of an event

    def event_dialog(self, event: CalEvent | None, day: str = "") -> Dialog | None:
        if self.vault is None:
            return None
        dlg = event_editor(self, event, day or self.selected or today())
        self.last_dialog = dlg
        return dlg

    def saved(self, day: str) -> None:
        """The editor wrote the calendar: the month of `day` is shown with it chosen."""
        self.go_to(day)

    # ---- .ics

    def import_ics(self, path: str | None = None) -> tuple[int, int] | None:
        """Meetings from an .ics file: added and updated by UID. Returns (added, updated)."""
        shell = self.shell
        if shell.vault is None:
            return None
        if path is None:
            path, _f = QFileDialog.getOpenFileName(shell, _("A calendar file (.ics)"), "", _("Calendar files") + " (*.ics);;" + _("All files") + " (*.*)")
        if not path or shell.vault is None or not shiboken6.isValid(self):  # locked under the file dialog
            return None
        try:
            if os.path.getsize(path) > MAX_ICS_BYTES:
                show_error(shell, _("The file is too big for a calendar: {mb} MB at most").format(mb=MAX_ICS_BYTES // (1024 * 1024)))
                return None
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                text = f.read(MAX_ICS_BYTES + 1)
        except OSError as exc:
            show_error(shell, _("Could not read the file:\n{error}").format(error=exc))
            return None
        events = read_ics(text)
        if not events:
            shell.set_status(_("No events in the file"))
            return 0, 0
        added, updated = shell.vault.calendar.upsert_ics(events)
        shell.mark_dirty("calendar")
        self.show_month()
        shell.set_status(_("Calendar: added {added}, updated {updated}").format(added=added, updated=updated))
        return added, updated


def event_editor(page: CalendarPage, event: CalEvent | None, day: str) -> Dialog:
    """Title, date, time (empty — all day), repeat and until, the reminder, link and note.
    Not blocking: the parts are in `page.dialog_parts` (the tests fill them)."""
    shell = page.shell
    dlg = Dialog(shell, _("New event") if event is None else _("Event"))
    width = QFontMetrics(dlg.font()).averageCharWidth() * 44
    grid = QGridLayout()
    grid.setHorizontalSpacing(12)
    grid.setVerticalSpacing(8)

    def date_edit(value: str) -> QDateEdit:
        edit = QDateEdit(dlg)
        edit.setCalendarPopup(True)
        edit.setDisplayFormat("dd.MM.yyyy")
        q = QDate.fromString(value, "yyyy-MM-dd")
        edit.setDate(q if q.isValid() else QDate.currentDate())
        return edit

    title = QLineEdit(event.title if event else "", dlg)
    title.setMinimumWidth(width)
    when = date_edit(event.date if event else day)
    at = QLineEdit(event.time if event else "", dlg)
    at.setPlaceholderText(_("HH:MM; empty — all day"))
    repeat = QComboBox(dlg)
    for r in REPEATS:
        repeat.addItem(_(REPEAT_LABELS[r]), r)
    repeat.setCurrentIndex(REPEATS.index(event.repeat) if event and event.repeat in REPEATS else 0)
    until = date_edit(event.until if event and event.until else "")
    until.setMinimumDate(NO_END)
    until.setSpecialValueText(_("no end"))
    if not (event and event.until):
        until.setDate(NO_END)
    remind = QComboBox(dlg)
    current = min(event.remind_min, MAX_REMIND_MIN) if event else 0
    for minutes in sorted(set(REMIND_CHOICES) | {current}):  # a value from an .ics file keeps its place
        remind.addItem(remind_label(minutes), minutes)
    remind.setCurrentIndex(remind.findData(current))
    url = QLineEdit(event.url if event else "", dlg)
    url.setPlaceholderText("https://…")
    open_link = icon_button("↗", _("Open the link"), lambda: _open_url(url.text()), dlg)
    url_row = QHBoxLayout()
    url_row.setSpacing(4)
    url_row.addWidget(url, 1)
    url_row.addWidget(open_link)
    note = QPlainTextEdit(event.note if event else "", dlg)
    note.setTabChangesFocus(True)
    note.setFixedHeight(note.fontMetrics().lineSpacing() * 4 + 14)
    err = label("", "error", dlg)
    err.setWordWrap(True)
    rows: list[tuple[str, Any]] = [
        (_("Title"), title), (_("Date"), when), (_("Time"), at), (_("Repeat"), repeat), (_("Until"), until),
        (_("Remind"), remind), (_("Link"), url_row), (_("Note"), note),
    ]
    for n, (caption, widget) in enumerate(rows):
        grid.addWidget(label(caption, "", dlg), n, 0, Qt.AlignmentFlag.AlignTop if widget is note else Qt.AlignmentFlag.AlignVCenter)
        if isinstance(widget, QHBoxLayout):
            grid.addLayout(widget, n, 1)
        else:
            grid.addWidget(widget, n, 1)
    dlg.box.addLayout(grid, 0, 0)
    dlg.box.addWidget(err, 1, 0)
    line = QFrame(dlg)
    line.setFixedHeight(8)
    dlg.box.addWidget(line, 2, 0)

    def sync() -> None:
        until.setEnabled(bool(repeat.currentData()))
        remind.setEnabled(bool(at.text().strip()))
        open_link.setEnabled(http_link(url.text()))

    def save() -> None:
        vault = shell.vault
        if vault is None:
            dlg.reject()
            return
        name = title.text().strip()
        if not name:
            err.setText(_("Give the event a title"))
            return
        start = when.date().toString("yyyy-MM-dd")
        time_text = normal_time(at.text())
        if time_text is None:
            err.setText(_("The time is HH:MM, e.g. 09:30; empty — all day"))
            return
        rep = str(repeat.currentData() or "")
        end = "" if not rep or until.date() == NO_END else until.date().toString("yyyy-MM-dd")
        if end and end < start:
            err.setText(_("“Until” is before the date of the event"))
            return
        minutes = max(0, min(int(remind.currentData() or 0), MAX_REMIND_MIN)) if time_text else 0
        fields = {"time": time_text, "repeat": rep, "until": end, "remind_min": minutes, "url": url.text().strip(), "note": note.toPlainText().rstrip()}
        if event is None:
            vault.calendar.add(name, start, **fields)
        else:
            live = vault.calendar.get(event.id)
            if live is None:  # gone under the dialog (an undo): saved as a new one
                vault.calendar.add(name, start, **fields)
            else:
                if (live.date, live.time, live.repeat, live.remind_min) != (start, time_text, rep, minutes):
                    live.notified = ""  # a new time is a new reminder
                live.title, live.date = name, start
                for k, v in fields.items():
                    setattr(live, k, v)
        shell.mark_dirty("calendar")
        dlg.accept()
        if shiboken6.isValid(page):
            page.saved(start)

    def delete() -> None:
        vault = shell.vault
        live = vault.calendar.get(event.id) if vault is not None and event is not None else None
        if live is not None and vault is not None:
            vault.calendar.remove(live)
            shell.mark_dirty("calendar")
            shell.set_status(_("Event deleted: {name}. Ctrl+Z puts it back").format(name=live.title))
        dlg.accept()
        if shiboken6.isValid(page):
            page.show_month()

    for signal in (repeat.currentIndexChanged, at.textChanged, url.textChanged):
        signal.connect(lambda *_a: sync())
    sync()
    dlg.editor_buttons(3, 1, save, delete if event is not None else None)
    dlg.keys(save)
    page.dialog_parts = {"title": title, "date": when, "time": at, "repeat": repeat, "until": until, "remind": remind, "url": url, "note": note, "error": err, "save": save, "delete": delete, "open_link": open_link}
    title.setFocus()
    dlg.open()
    return dlg


def _open_url(url: str) -> None:
    if http_link(url):
        import webbrowser

        webbrowser.open(url.strip())


# ---------------------------------------------------------------- reminders


class CalendarReminders:
    """Every REMIND_TICK_MS: the events whose reminder is due (Calendar.due marks each, so
    it is shown once) become toasts; a click on one opens the tab on its day. Made once
    with the window and kept through a lock; silent while the vault is locked or the
    Calendar tab is switched off. A reminder whose whole window passed while the app was
    locked or closed is not shown afterwards (no catch-up). `now` is the clock (the tests
    set it)."""

    def __init__(self, shell: Any):
        self.shell = shell
        self.now: Callable[[], datetime] = datetime.now
        self.ticker = Ticker(shell, REMIND_TICK_MS, self.tick)
        if getattr(shell, "background_jobs", True):  # a preview copy reminds of nothing
            self.ticker.start()

    def tick(self) -> list[CalEvent]:
        vault = self.shell.vault
        if vault is None or not self.shell.tab_on("calendar"):
            return []
        now = self.now()
        due = vault.calendar.due(now)
        if not due:
            return []
        for e in due:
            day = e.notified or now.date().isoformat()
            Toast(self.shell, "🔔  " + (e.title or _("(untitled)")), self.when_text(e, day, now), action=partial(self.open_day, day))
        self.shell.mark_dirty(None)  # `notified` is saved: the reminder is not shown again
        return due

    @staticmethod
    def when_text(e: CalEvent, day: str, now: datetime) -> str:
        start = e.starts_at(date.fromisoformat(day))
        minutes = max(0, int((start - now).total_seconds() // 60)) if start is not None else 0
        when = _("Today at {time}").format(time=e.time) if day == now.date().isoformat() else _("{day} at {time}").format(day=fmt_day(day), time=e.time)
        return when + " · " + _("in {n} min").format(n=minutes)

    def open_day(self, day: str) -> None:
        shell = self.shell
        if shell.vault is None:
            return
        shell.show_page("calendar")
        shell.page("calendar").go_to(day)
