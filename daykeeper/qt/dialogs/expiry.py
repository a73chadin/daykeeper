"""Keys that run out, in the Qt window: the watch that finds them (expiry.expiring) after
a change of the vault and every hour, the count on the top bar, the list window behind
it, the toast once a day after unlock and the coloured "Expires in 3 days" text the list
and the card show.

Only open fields are read (the core never unseals a secret field for a date). What the
user dismissed lives in settings.json: `expiry.seen` maps Expiry.key to the last day it
stays hidden, or to "date:<day>" for "Do not remind" (hidden while the date stays that
day, expiry.is_hidden), `expiry.shown` is the day of the last toast, `expiry.days` the warning
window (0 — off)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from dataclasses import dataclass
from typing import Any

import shiboken6
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QWidget

from ...expiry import CERT_FIELD, NEVER_PREFIX, Expiry, expiring, is_hidden, never_value
from ...i18n import _, fmt_day
from ...vault import Entry
from .. import icons, theme
from ..errors import guard_events
from ..timers import Debounce, Ticker
from .base import Dialog, button, label

DAY_CHOICES = (7, 14, 30, 0)  # the warning window in Settings → Security; 0 — off
DEFAULT_DAYS = 7
REMIND_AGAIN_DAYS = 7  # "Remind in a week"
OLD_FOREVER = "9999-12-31"  # the "Do not remind" of older versions: turned into "date:<day>" on the next look
REFRESH_SETTLE_MS = 600  # a burst of edits is looked at once
REFRESH_EVERY_MS = 3_600_000  # the day turns, a date comes closer
TOAST_ITEMS = 5  # names in the toast; the rest as "and N more"
TONE_ICON = theme.px(13)  # px, the clock before a coloured expiry text


# ---- words and colours


def expiry_settings(settings: dict) -> dict:
    value = settings.get("expiry")
    return dict(value) if isinstance(value, dict) else {}


def warn_days(settings: dict) -> int:
    """The warning window in days; 0 — warnings are off."""
    raw = expiry_settings(settings).get("days", DEFAULT_DAYS)
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_DAYS
    return max(0, days)


def days_label(days: int) -> str:
    return _("off") if not days else _("{n} days").format(n=days)


def field_label(field: str) -> str:
    return _("certificate") if field == CERT_FIELD else field


def expires_text(days_left: int) -> str:
    """The line under a date field of the card."""
    if days_left > 1:
        return _("Expires in {n} days").format(n=days_left)
    if days_left == 1:
        return _("Expires tomorrow")
    if days_left == 0:
        return _("Expires today")
    if days_left == -1:
        return _("Expired yesterday")
    return _("Expired {n} days ago").format(n=-days_left)


def when_text(days_left: int) -> str:
    """The short form for the toast and the list window."""
    if days_left > 1:
        return _("in {n} days").format(n=days_left)
    if days_left == 1:
        return _("tomorrow")
    if days_left == 0:
        return _("today")
    if days_left == -1:
        return _("yesterday")
    return _("{n} days ago").format(n=-days_left)


def badge_text(days_left: int) -> str:
    """The compact badge of a list row."""
    if days_left < 0:
        return _("expired")
    if days_left == 0:
        return _("today")
    return _("{n} d").format(n=days_left)


def tone_colour(days_left: int, warn: int) -> str:
    """Red when expired, orange (the dark amber of links) within the warning window,
    muted otherwise."""
    t = theme.current()
    if days_left < 0:
        return t.DANGER
    if warn and days_left <= warn:
        return t.LINK
    return t.MUTED


@guard_events
class ToneText(QWidget):
    """A drawn clock and a short text in the colour of how soon the date is; the colour is
    read at every paint, so a theme switch needs nothing."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.text = ""
        self.days_left = 0
        self.warn = 0
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def set(self, text: str, days_left: int, warn: int) -> None:
        self.text, self.days_left, self.warn = text, days_left, warn
        self.updateGeometry()
        self.update()

    def colour(self) -> str:
        return tone_colour(self.days_left, self.warn)

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        return QSize(TONE_ICON + 6 + fm.horizontalAdvance(self.text) + 2, max(fm.height(), TONE_ICON) + 2)

    def minimumSizeHint(self) -> QSize:
        return QSize(TONE_ICON, self.sizeHint().height())

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        colour = QColor(self.colour())
        h = self.height()
        icons.paint(p, "clock", QRectF(0, 0, TONE_ICON, h), colour, size=TONE_ICON)
        p.setPen(colour)
        rest = QRectF(TONE_ICON + 6, 0, max(0, self.width() - TONE_ICON - 6), h)
        p.drawText(rest, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), self.fontMetrics().elidedText(self.text, Qt.TextElideMode.ElideRight, int(rest.width())))
        p.end()


@dataclass
class _OneEntry:
    entries: list[Entry]


def card_expiries(entry: Entry, today: date) -> list[Expiry]:
    """Every date the core sees in one entry, however far (the card shows them all)."""
    return expiring(_OneEntry([entry]), today, 10**6, {})


# ---- the watch


class ExpiryWatch:
    """Made once with the window. `items` — what to remind of now (dismissals left out),
    `badges` — entry id → the fewest days left within the window (for the list rows)."""

    def __init__(self, window: Any):
        self.window = window
        self.items: list[Expiry] = []
        self.badges: dict[str, int] = {}
        self.today: Callable[[], date] = date.today  # tests put a frozen day here
        self.dialog: ExpiryDialog | None = None
        self.toast: Any = None
        self._toast_due = False
        self._later = Debounce(window, REFRESH_SETTLE_MS, self.refresh)
        self._hourly = Ticker(window, REFRESH_EVERY_MS, self.refresh)

    # ---- settings

    def settings(self) -> dict:
        return expiry_settings(self.window.session.settings())

    def warn(self) -> int:
        return warn_days(self.window.session.settings())

    def _write(self, **update: Any) -> None:
        merged = self.settings()
        merged.update(update)
        self.window.session.write_settings({"expiry": merged})

    def set_days(self, days: int) -> None:
        self._write(days=int(days))
        self.refresh()

    # ---- when

    def touch(self) -> None:
        """The vault changed: look again once the edits pause."""
        if self.window.vault is not None:
            self._later.touch()

    def on_unlock(self) -> None:
        """After the main screen is up: look soon (not inside the unlock itself, which has
        a speed target) and toast once a day."""
        self._toast_due = True
        self._later.touch()
        self._hourly.start()

    def on_lock(self) -> None:
        self._later.cancel()
        self._hourly.stop()
        self._toast_due = False
        self.items, self.badges = [], {}
        self._show_count()

    # ---- looking

    def refresh(self) -> None:
        vault = self.window.vault
        if vault is None:
            self.on_lock()
            return
        self._later.cancel()
        today = self.today()
        days = self.warn()
        every = expiring(vault, today, 10**6, {}) if days else []  # one scan: the window below, the dismissals here
        found = [x for x in every if x.days_left <= days]
        seen = self._prune_seen(vault, every if days else None)
        self.items = [x for x in found if not is_hidden(seen.get(x.key), x, today)]  # the rule of expiry.expiring, on the same scan
        badges: dict[str, int] = {}
        for x in found:
            badges[x.entry_id] = min(badges.get(x.entry_id, x.days_left), x.days_left)
        self.badges = badges
        self._show_count()
        page = self.window.pages.get("vault")
        if page is not None:
            page.expiry_changed()
        if self.dialog is not None and shiboken6.isValid(self.dialog):
            if self.secrets_open():
                self.dialog.fill()
            else:
                self.dialog.close()  # the guard came down while it was open: the names go
        if self._toast_due:
            self._toast_due = False
            self._toast(today)

    def _prune_seen(self, vault: Any, every: list[Expiry] | None) -> dict:
        """The dismissals of entries that still exist; the keys of deleted entries are
        dropped from settings.json. "Do not remind" of an older version (OLD_FOREVER) is
        tied to the date the item has now, and one whose date has changed (or gone) is
        dropped: the reminder is back. `every` — all the dates found, however far; None (the
        warnings are off, nothing was looked at) keeps those as they are."""
        seen = self.settings().get("seen")
        if not isinstance(seen, dict):
            return {}
        ids = {e.id for e in vault.entries}
        when = {x.key: x for x in every or []}
        kept: dict[str, Any] = {}
        for k, v in seen.items():
            if not isinstance(k, str) or k.partition(":")[0] not in ids:
                continue
            if every is None:
                kept[k] = v
                continue
            item = when.get(k)
            if v == OLD_FOREVER and item is not None:
                v = never_value(item)
            elif str(v).startswith(NEVER_PREFIX) and (item is None or not is_hidden(v, item, item.when)):
                continue  # the date changed since "Do not remind"
            kept[k] = v
        if kept != seen:
            self._write(seen=kept)
        return kept

    def secrets_open(self) -> bool:
        security = getattr(self.window, "security", None)
        return security is None or bool(security.secrets_open())

    def _show_count(self) -> None:
        screen = getattr(self.window, "main_screen", None)
        if screen is not None:
            screen.set_expiry_count(len(self.items))

    def _toast(self, today: date) -> None:
        stamp = today.isoformat()
        if not self.items or self.settings().get("shown") == stamp:
            return
        from ..widgets.toast import Toast

        self._write(shown=stamp)
        if not self.secrets_open():  # behind the Passwords guard: a number, no names
            text = _("{n} keys expire soon").format(n=len(self.items))
            self.toast = Toast(self.window, "⏰  " + _("Expiring keys"), text, action=self.open_list)
            return
        names = [x.entry_name for x in self.items]
        parts = []
        for x in self.items[:TOAST_ITEMS]:
            name = x.entry_name or _("(untitled)")
            if names.count(x.entry_name) > 1:
                name = f"{name} · {field_label(x.field)}"
            parts.append(f"{name} ({when_text(x.days_left)})")
        text = _("Expires: {items}").format(items=", ".join(parts))
        if len(self.items) > TOAST_ITEMS:
            text += " " + _("and {n} more").format(n=len(self.items) - TOAST_ITEMS)
        self.toast = Toast(self.window, "⏰  " + _("Expiring keys"), text, action=self.open_list)

    # ---- acting

    def dismiss(self, item: Expiry, until: str) -> None:
        """Hide `item` up to and including the day `until` (ISO)."""
        seen = self.settings().get("seen")
        seen = dict(seen) if isinstance(seen, dict) else {}
        seen[item.key] = until
        self._write(seen=seen)
        self.refresh()

    def remind_later(self, item: Expiry) -> None:
        self.dismiss(item, (self.today() + timedelta(days=REMIND_AGAIN_DAYS - 1)).isoformat())

    def never(self, item: Expiry) -> None:
        """Hidden while the item's date stays the same."""
        self.dismiss(item, never_value(item))

    def open_entry(self, item: Expiry) -> None:
        vault = self.window.vault
        entry = vault.entry_by_id(item.entry_id) if vault is not None else None
        if entry is None:
            return
        if self.dialog is not None and shiboken6.isValid(self.dialog):
            self.dialog.close()
        self.window.show_page("vault")
        self.window.page("vault").select_entry(entry)  # asks for the code first behind the guard

    def open_list(self) -> ExpiryDialog | None:
        """The list window; behind the Passwords guard it opens after the code (None then)."""
        if self.window.vault is None:
            return None
        if not self.secrets_open():
            self.window.security.require_secrets(self.open_list)
            return None
        if self.dialog is not None and shiboken6.isValid(self.dialog) and self.dialog.isVisible():
            self.dialog.raise_()
            return self.dialog
        self.dialog = ExpiryDialog(self)
        self.dialog.open()
        return self.dialog


@guard_events
class ExpiryDialog(Dialog):
    """What expires soon: entry, field, date and three actions a row."""

    def __init__(self, watch: ExpiryWatch):
        super().__init__(watch.window, _("Expiring keys"))
        self.watch = watch
        self.body: QWidget | None = None
        self.rows: list[dict[str, Any]] = []  # tests reach the buttons here
        self.box.addWidget(label(_("Dates are read from open fields only: a secret field is never unsealed for this."), "muted", self), 1, 0)
        foot = QHBoxLayout()
        foot.addStretch(1)
        close = button(_("Close"), "", self)
        close.clicked.connect(self.close)
        foot.addWidget(close)
        self.box.addLayout(foot, 2, 0)
        self.keys(self.close)
        self.fill()

    def fill(self) -> None:
        if self.body is not None:
            self.box.removeWidget(self.body)
            self.body.hide()
            self.body.deleteLater()
        body = QWidget(self)
        grid = QGridLayout(body)
        grid.setContentsMargins(0, 0, 0, 8)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        self.rows = []
        items = self.watch.items
        warn = self.watch.warn()
        if not items:
            grid.addWidget(label(_("Nothing expires soon."), "", body), 0, 0)
        else:
            for col, text in enumerate((_("Entry"), _("Field"), _("Date"))):
                grid.addWidget(label(text.upper(), "section", body), 0, col)
        for i, x in enumerate(items, 1):
            name = QLabel(x.entry_name or _("(untitled)"), body)
            name.setFont(theme.font(11, bold=True))
            grid.addWidget(name, i, 0)
            grid.addWidget(label(field_label(x.field), "muted", body), i, 1)
            when = ToneText(body)
            when.set(f"{fmt_day(x.when.isoformat())} · {when_text(x.days_left)}", x.days_left, warn)
            grid.addWidget(when, i, 2)
            acts = QHBoxLayout()
            acts.setSpacing(4)
            open_btn = button(_("Open"), "small", body)
            later_btn = button(_("Remind in a week"), "small", body)
            never_btn = button(_("Do not remind"), "small", body)
            open_btn.clicked.connect(lambda _c=False, it=x: self.watch.open_entry(it))
            later_btn.clicked.connect(lambda _c=False, it=x: self.watch.remind_later(it))
            never_btn.clicked.connect(lambda _c=False, it=x: self.watch.never(it))
            for b in (open_btn, later_btn, never_btn):
                acts.addWidget(b)
            grid.addLayout(acts, i, 3)
            self.rows.append({"item": x, "open": open_btn, "later": later_btn, "never": never_btn})
        grid.setColumnStretch(0, 1)
        self.body = body
        self.box.addWidget(body, 0, 0)
