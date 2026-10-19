"""The morning digest in the Qt window (digest.py builds it): a window over the main one
that leaves the main one usable, with what the day starts with — the plans, open note
items, MRs waiting for review, keys that run out, the night and the unread notifications.

It comes up by itself once a day: SHOW_DELAY_MS after an unlock (the main screen is up and
the unlock routines have started), when Settings → General allows it and it was not shown
today. A day with nothing to show opens no window but still counts as shown: an unlock
later that day finds the same nothing, and the build is not repeated at every unlock. The
☀ button of the top bar and Ctrl+D open it any time without counting the day.

A line acts by what it is about: a link opens in the browser (http and https only), a tab
comes up, a key that runs out opens its entry (the Passwords guard asks for the code
first). The window stays open after a click, so several lines can be followed; it closes
on lock (registered with the main window) and on Escape."""

from __future__ import annotations

import time
import webbrowser
from collections.abc import Callable
from datetime import date
from typing import Any

import shiboken6
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ...digest import EXPIRING, TAB_INTEGRATIONS, TAB_JOURNAL, TAB_NOTES, TAB_VAULT, Line, Section, as_text, build, mark_shown, should_show, switched
from ...i18n import _, fmt_day
from .. import icons, theme
from ..errors import guard_events
from ..timers import Debounce
from ..widgets.common import scroll_area
from .base import Dialog, button, label
from .expiry import expiry_settings, warn_days

SHOW_DELAY_MS = 1200  # after the unlock: the main screen is on screen, the unlock routines started
WIDTH = 640  # px of the window at the system's text size
MARK = theme.px(14)  # px, the ✓ / ✗ / bullet before a line
HEAD_ICON = 22  # px, the sun before the day
SCREEN_SHARE = 0.6  # the most of the screen's height the sections take before they scroll
OPEN_TAB = {  # the button of a section
    TAB_JOURNAL: "Open the journal",
    TAB_NOTES: "Open notes",
    TAB_INTEGRATIONS: "Open integrations",
    TAB_VAULT: "Open passwords",
}


def web_link(url: str) -> bool:
    """Only a web address goes to the browser (no file:, no javascript:)."""
    return url.strip().lower().startswith(("http://", "https://"))


@guard_events
class DigestLine(QWidget):
    """One line, painted: a drawn mark (✓ green, ✗ red, a dot otherwise) and the text,
    elided to the width; a line that leads somewhere has the hand cursor and is
    underlined under the pointer. Colours are read at every paint (a theme switch)."""

    def __init__(self, line: Line, on_click: Callable[[Line], object] | None, parent: QWidget | None = None):
        super().__init__(parent)
        self.line = line
        self.on_click = on_click
        self.hover = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if on_click is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(line.text + (f"\n{line.url}" if line.url else ""))

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        return QSize(MARK + 8 + fm.horizontalAdvance(self.line.text) + 2, max(fm.height(), MARK) + 6)

    def minimumSizeHint(self) -> QSize:
        return QSize(MARK * 4, self.sizeHint().height())

    def paintEvent(self, event: QPaintEvent) -> None:
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self.height()
        box = QRectF(0, 0, MARK, h)
        if self.line.ok is None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.MUTED))
            p.drawEllipse(box.center(), MARK * 0.14, MARK * 0.14)
        else:
            icons.paint(p, "check" if self.line.ok else "cross", box, t.SUCCESS if self.line.ok else t.DANGER, size=MARK)
        font = QFont(self.font())
        font.setUnderline(self.hover and self.on_click is not None)
        p.setFont(font)
        p.setPen(QColor(t.TEXT))
        rest = QRectF(MARK + 8, 0, max(0, self.width() - MARK - 8), h)
        text = self.fontMetrics().elidedText(self.line.text, Qt.TextElideMode.ElideRight, int(rest.width()))
        p.drawText(rest, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text)
        p.end()

    def enterEvent(self, event) -> None:
        self.hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.on_click is not None and self.rect().contains(event.position().toPoint()):
            self.on_click(self.line)
            return
        super().mouseReleaseEvent(event)


@guard_events
class DigestWindow(Dialog):
    """The day, the sections, and Copy as text / Don't show again / Close."""

    def __init__(self, control: DigestControl, sections: list[Section], today: str):
        super().__init__(control.window, _("Morning digest"))
        self.setModal(False)  # the main window stays usable: a line opens a tab behind it
        self.control = control
        self.sections = sections
        self.lines: list[DigestLine] = []  # tests reach the lines and buttons here
        self.section_buttons: dict[str, Any] = {}  # section title (UI key) → its button

        head = QHBoxLayout()
        head.setSpacing(10)
        sun = QLabel(self)
        sun.setPixmap(icons.icon("sun", lambda: theme.current().LINK).pixmap(QSize(HEAD_ICON, HEAD_ICON)))
        day = QLabel(fmt_day(today), self)
        day.setFont(theme.font(15, bold=True))
        head.addWidget(sun)
        head.addWidget(day)
        head.addStretch(1)
        self.box.addLayout(head, 0, 0)

        area, inner, col = scroll_area(self, margins=(0, 8, 8, 8), name="DigestScroll")
        col.setSpacing(4)
        if not sections:
            col.addWidget(label(_("Nothing for today: no plans, no open items, no news of the night."), "muted", inner))
        for n, s in enumerate(sections):
            if n:
                col.addSpacing(12)
            self._section(s, inner, col)
        col.addStretch(1)
        screen = self.screen() or QApplication.primaryScreen()
        cap = int(screen.availableGeometry().height() * SCREEN_SHARE) if screen is not None else 600
        area.setMinimumHeight(min(inner.sizeHint().height(), cap))
        self.box.addWidget(area, 1, 0)

        foot = QHBoxLayout()
        foot.setContentsMargins(0, 10, 0, 0)
        self.copy_btn = button(_("Copy as text"), "", self)
        self.copy_btn.setToolTip(_("The whole digest as plain text, links included"))
        self.copy_btn.clicked.connect(self.copy_text)
        self.off_btn = button(_("Don't show again"), "", self)
        self.off_btn.setToolTip(_("Turn it back on in Settings → General"))
        self.off_btn.clicked.connect(self.turn_off)
        self.close_btn = button(_("Close"), "primary", self)
        self.close_btn.clicked.connect(self.close)
        foot.addWidget(self.copy_btn)
        foot.addStretch(1)
        foot.addWidget(self.off_btn)
        foot.addWidget(self.close_btn)
        self.box.addLayout(foot, 2, 0)
        self.box.setRowStretch(1, 1)

    def _section(self, s: Section, parent: QWidget, col: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(10)
        title = QLabel(_(s.title), parent)
        title.setFont(theme.font(12, bold=True))
        row.addWidget(title)
        if s.count > 1 or s.more:
            row.addWidget(label(str(s.count), "muted", parent))
        if s.detail:
            row.addWidget(label(s.detail, "muted", parent))
        row.addStretch(1)
        if s.tab in OPEN_TAB:
            b = button(_(OPEN_TAB[s.tab]), "small", parent)
            b.clicked.connect(lambda _c=False, tab=s.tab: self.control.open_tab(tab))
            row.addWidget(b)
            self.section_buttons[s.title] = b
        col.addLayout(row)
        for line in s.lines:
            w = DigestLine(line, self.control.open_line if self.control.leads_somewhere(line) else None, parent)
            w.setContentsMargins(0, 0, 0, 0)
            holder = QHBoxLayout()
            holder.setContentsMargins(14, 0, 0, 0)
            holder.addWidget(w)
            col.addLayout(holder)
            self.lines.append(w)
        if s.more:
            more = label(_("…and {n} more").format(n=s.more), "muted", parent)
            more.setContentsMargins(14 + MARK + 8, 0, 0, 0)
            col.addWidget(more)

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        return QSize(max(hint.width(), theme.px(WIDTH)), hint.height())

    def copy_text(self) -> None:
        self.control.window.copy_to_clipboard(as_text(self.sections), secret=False)

    def turn_off(self) -> None:
        self.control.turn_off()
        self.close()


class DigestControl:
    """Made once with the window: the timer after an unlock, the once-a-day rule and the
    actions of the lines. `today` — tests put a frozen day here."""

    def __init__(self, window: Any):
        self.window = window
        self.today: Callable[[], date] = date.today
        self.now: Callable[[], float] = time.time
        self.dialog: DigestWindow | None = None
        self._due = Debounce(window, SHOW_DELAY_MS, self.show_due)

    # ---- settings

    def value(self) -> Any:
        return self.window.session.settings().get("digest")

    def on_unlock_enabled(self) -> bool:
        value = self.value()
        return bool(value.get("on_unlock", True)) if isinstance(value, dict) else True

    def set_on_unlock(self, on: bool) -> None:
        self.window.session.write_settings({"digest": switched(self.value(), on)})

    def turn_off(self) -> None:
        self.set_on_unlock(False)

    # ---- when

    def on_unlock(self) -> None:
        self._due.touch()

    def on_lock(self) -> None:
        self._due.cancel()  # the window itself closes with the other children of the lock

    def sections(self) -> list[Section]:
        settings = self.window.session.settings()
        days = warn_days(settings)
        seen = expiry_settings(settings).get("seen")
        sections = build(self.window.vault, self.today().isoformat(), now=self.now(),
                         expiry_days=days if days else -10**9,  # warnings off: no keys in the digest either
                         seen=seen if isinstance(seen, dict) else {})
        if not self.secrets_open():
            sections = [self._count_only(s) if s.title == EXPIRING else s for s in sections]
        return sections

    def secrets_open(self) -> bool:
        security = getattr(self.window, "security", None)
        return security is None or bool(security.secrets_open())

    @staticmethod
    def _count_only(section: Section) -> Section:
        """The expiring keys behind the Passwords guard: how many, not which."""
        line = Line(_("{n} keys expire soon").format(n=section.count), tab=TAB_VAULT)
        return Section(section.title, [line], 1, section.tab, section.detail)

    def show_due(self) -> None:
        """The once-a-day look after an unlock; the day counts even when there is nothing."""
        if self.window.vault is None:
            return
        today = self.today().isoformat()
        value = self.value()
        if not should_show(value, today):
            return
        sections = self.sections()
        self.window.session.write_settings({"digest": mark_shown(value, today)})
        if sections:
            self.open(sections)

    def open(self, sections: list[Section] | None = None) -> DigestWindow | None:
        """Show the digest now (the ☀ button, Ctrl+D): built anew, the day not counted."""
        if self.window.vault is None:
            return None
        if self.dialog is not None and shiboken6.isValid(self.dialog):
            self.dialog.close()
        self.dialog = DigestWindow(self, self.sections() if sections is None else sections, self.today().isoformat())
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
        return self.dialog

    # ---- acting

    def leads_somewhere(self, line: Line) -> bool:
        return web_link(line.url) or bool(line.tab)

    def open_line(self, line: Line) -> None:
        if web_link(line.url):
            webbrowser.open(line.url)
        elif line.tab == TAB_VAULT and line.ref:
            self.open_entry(line.ref)
        elif line.tab:
            self.open_tab(line.tab)

    def open_tab(self, tab: str) -> None:
        if self.window.vault is not None:
            self.window.show_page(tab)

    def open_entry(self, entry_id: str) -> None:
        vault = self.window.vault
        entry = vault.entry_by_id(entry_id) if vault is not None else None
        if entry is None:
            self.open_tab(TAB_VAULT)
            return
        self.window.show_page(TAB_VAULT)
        self.window.page(TAB_VAULT).select_entry(entry)  # asks for the code first behind the guard
