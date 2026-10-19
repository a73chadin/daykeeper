"""The “Integrations” tab of the Qt window: connections on the left, the selected one's
settings and the feed of notifications on the right.

GitLab and Jira are checked in background threads every N minutes while the vault is
open, each by its own watcher; the token is read from a vault entry and never shown. Once
a day the GitLab check also asks the token's last day and puts it on that entry's "valid
until" field, so the expiry reminders cover it (silent when the server does not say). New
events of both go to one feed (kept in the vault), a counter on the tab, a pop-up in the
corner of the screen and the event hub; the taskbar button asks for attention
(QApplication.alert, on every system)."""

from __future__ import annotations

import functools
import time
import webbrowser
from collections.abc import Callable
from datetime import date
from typing import Any

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from ...bookmarks import entry_login, entry_secret
from ...expiry import set_date
from ...gitlab import INTERVALS, PIPELINE_SOURCES, RULES, WATCH_RULES, Event, GitLabError, PipelineWatch, check as gitlab_check
from ...i18n import _
from ...jira import DEFAULT_JQL, RULES as JIRA_RULES, JiraClient, JiraError, JiraSettings, check as jira_check, parse_statuses, transition_by_name
from ...jobs import JobPool, JobResult
from ...journal import ISSUE_KEY_RE, normalize_item
from ...journal_layout import Layout, current as current_layout
from .. import icons, theme
from ..dialogs.base import Dialog, button, label
from ..jobs import Waker
from ..timers import Ticker
from ..widgets.common import IconLabel, icon_button, scroll_area, section, with_icon
from ..widgets.countlist import count_list
from ..widgets.flow import flow_host
from ..widgets.toast import Toast
from .base import TabPage
from ..errors import guard_events

POLL_TICK_MS = 15_000  # how often the app asks “is a check due?”
FEED_MIN_WIDTH = 380  # px; narrower than settings + this, the notifications go under the settings
FEED_PAGE = 20
EVENT_ICONS = {
    "approved": "✅", "resolved": "💬", "pipeline": "❌", "merged": "🎉",
    "review_requested": "👀", "my_threads_resolved": "✔", "replies": "↩",
    "watch_failed": "pipe_failed", "watch_fixed": "pipe_fixed", "branch_commits": "commit",
}
JIRA_ICONS = {"status": "ticket", "assigned_bug": "bug", "commented": "comment", "reopened": "undo"}
ACTION_GAP = 6  # px between the MR title and a card's action row
EVENT_ROLE = Qt.ItemDataRole.UserRole + 4
MORE_ROLE = Qt.ItemDataRole.UserRole + 5


def event_icon(ev: Event) -> str:
    """The drawn picture of an event in the feed and its pop-up (Jira's kinds are its own)."""
    if ev.source == "jira":
        return JIRA_ICONS.get(ev.kind, "ticket")
    return EVENT_ICONS.get(ev.kind, "🔔")


def source_text(source: str) -> str:
    return {"schedule": _("by schedule"), "push": _("on push")}.get(source, _("any source"))


def watch_text(w: PipelineWatch) -> str:
    """“group/repo · main · by schedule”, after the name when it has one."""
    where = f"{w.project} · {w.ref} · {source_text(w.source)}"
    return f"{w.name.strip()} — {where}" if w.name.strip() else where


def jira_myself(url: str, token: str, login: str, fetch: Any = None) -> dict:
    """“Check connection”, on the watcher's thread: who the token belongs to."""
    return JiraClient(url, token, login, fetch=fetch).myself()


def jira_transition(url: str, token: str, login: str, key: str, name: str, fetch: Any = None) -> str:
    """A verdict transition, on the watcher's thread; the id that was sent."""
    return transition_by_name(JiraClient(url, token, login, fetch=fetch), key, name)


def verdict_of_column(column: str, layout: Layout | None = None) -> str:
    """"pass" or "fail" when the journal column `column` is where the word `pass` / `fail`
    files an item (its command word, or the tester's old aliases), else "": the layout
    decides, not the column's name."""
    lay = layout or current_layout()
    for verdict in ("pass", "fail"):
        if lay.resolve(verdict) == ("column", column):
            return verdict
    return ""


def _user_name(me: dict) -> str:
    return str(me.get("name") or me.get("displayName") or me.get("emailAddress") or me.get("accountId") or "")


def mr_actions(ev: Event) -> list[tuple[str, str]]:
    """The action row of a feed card about a merge request: (code, text). “Review” for an MR
    I review (or whose part is unknown), “Fixes” for one of mine; none for other events."""
    if ev.source != "gitlab" or "!" not in ev.mr or not ev.mr.rsplit("!", 1)[1].isdigit():
        return []
    out = [("preview", _("Preview"))]
    if ev.role == "author":
        out.append(("fix", _("Fixes")))
    else:
        out.append(("review", _("Review")))
    return out


def event_text(ev: Event) -> str:
    try:
        return _(ev.text).format(**ev.args)
    except (KeyError, IndexError, ValueError):
        return ev.text


class GitLabWatcher:
    """The GitLab check through the life of the window: the pool the check runs in, the
    tick that asks whether one is due, the text of the last outcome. Kept through a lock."""

    def __init__(self, shell: Any):
        self.shell = shell
        self._wake = Waker(self._drain, owner=shell)  # the answer is taken as it comes
        self._jobs = JobPool(workers=1, name="gitlab", notify=self._wake)
        self._busy = False
        self.status = ""
        self._ticker = Ticker(shell, POLL_TICK_MS, self._tick)  # is a check due by the clock
        self._ticker.start()

    @property
    def page(self) -> IntegrationsPage | None:
        return self.shell.pages.get("integrations")

    def close(self) -> None:
        self._ticker.stop()
        self._wake.close()
        self._jobs.close()

    def _token(self) -> str:
        vault = self.shell.vault
        entry = vault.entry_by_id(vault.integrations.gitlab.entry_id) if vault else None
        return entry_secret(entry) if entry is not None else ""

    def _tick(self) -> None:
        vault = self.shell.vault
        if vault is None or self._busy or not self.shell.background_jobs or not self.shell.tab_on("integrations"):
            return
        integ = vault.integrations
        gl = integ.gitlab
        if not gl.enabled or not gl.url or not self._token():
            return
        if time.time() - integ.gitlab_checked >= gl.interval_min * 60:
            self.check_now(quiet=True)

    def check_now(self, quiet: bool = False) -> None:
        vault = self.shell.vault
        if vault is None or self._busy:
            return
        gl = vault.integrations.gitlab
        token = self._token()
        if not gl.url or not token:
            self.shell.set_status(_("Fill in the address and pick the entry with the token"))
            return
        self._busy = True
        self.shell.set_status(_("Checking…"))
        settings = type(gl).from_dict(gl.to_dict())  # a copy for the thread
        integ = vault.integrations
        old = dict(integ.gitlab_state)
        pipelines = {k: dict(v) for k, v in integ.gitlab_pipelines.items()}  # a copy for the thread
        ask_expiry = integ.token_expiry_due(time.time())
        self._jobs.submit("check", gitlab_check, gl.url, token, settings, old, None, integ.since(time.time()), pipelines, ask_expiry, quiet=quiet)

    def _drain(self) -> None:
        results = self._jobs.drain()
        if not results:
            return
        self._busy = False
        vault = self.shell.vault
        if vault is None:
            return
        integ = vault.integrations
        result = results[-1]
        if not result.ok:
            exc = result.error
            integ.note_failed_check(time.time())
            self.set_status(_("Check failed: {error}").format(error=_(str(exc) if isinstance(exc, GitLabError) else repr(exc))))
            self.shell.mark_dirty("integrations")
            return
        found = result.value
        first = integ.apply_check(found, time.time())
        if found.token_expires:
            self._take_token_expiry(found.token_expires)
        if found.events:
            self._notify(found.events)
            self.shell.hub.publish(found.events)  # routines by event and the other subscribers
        self.shell.mark_dirty("integrations")
        if first:
            self.set_status(_("Connected as @{user}. Watching {n} MR(s); changes from now on will be reported.").format(user=integ.gitlab.username, n=len(found.snapshot)))
        else:
            self.set_status(_("Checked at {time}: {n} MR(s), new events: {events}").format(time=time.strftime("%H:%M"), n=len(found.snapshot), events=len(found.events)))
        self.shell.update_integrations_badge()
        page = self.page
        if page is not None:
            page.render_feed()

    def _take_token_expiry(self, day: str) -> None:
        """The token's last day the server told: onto the entry's "valid until" field, where
        the expiry reminders see it like any other date. A day the field already says, or a
        secret date field (never unsealed), leaves the entry as it is."""
        vault = self.shell.vault
        entry = vault.entry_by_id(vault.integrations.gitlab.entry_id) if vault is not None else None
        if entry is None or not set_date(entry, date.fromisoformat(day), self.shell.expiry.today()):
            return
        self.shell.mark_dirty("vault")  # the expiry watch looks again
        page = self.shell.pages.get("vault")
        if page is not None:
            page.entry_changed_outside(entry)

    def _notify(self, events: list[Event]) -> None:
        for ev in events[:4]:
            Toast(self.shell, f"{event_icon(ev)}  GitLab · {ev.title[:60]}", event_text(ev), ev.url)
        if len(events) > 4:
            Toast(self.shell, "GitLab", _("And {n} more — see the Integrations tab").format(n=len(events) - 4))
        QApplication.alert(self.shell)

    def set_status(self, text: str) -> None:
        self.status = text
        self.shell.set_status(text)
        page = self.page
        if page is not None and page.status_label is not None and page.status_owner == "gitlab":
            page.status_label.setText(text)


class JiraWatcher:
    """The Jira check, the same way as GitLabWatcher: its own pool and waker, its own tick
    by the interval of the Jira settings, light checks between the hourly full ones
    (`Integrations.jira_since`), the events into the shared feed, the pop-ups and the hub.
    “Check connection” and the verdict transition (`transition`) run on the same thread.
    `fetch` stays None (the network); tests put a fake one there."""

    def __init__(self, shell: Any):
        self.shell = shell
        self.fetch: Callable[..., tuple[int, bytes]] | None = None
        self._wake = Waker(self._drain, owner=shell)
        self._jobs = JobPool(workers=1, name="jira", notify=self._wake)
        self._busy = False
        self.status = ""
        self._ticker = Ticker(shell, POLL_TICK_MS, self._tick)
        self._ticker.start()

    @property
    def page(self) -> IntegrationsPage | None:
        return self.shell.pages.get("integrations")

    def close(self) -> None:
        self._ticker.stop()
        self._wake.close()
        self._jobs.close()

    def _credentials(self) -> tuple[str, str]:
        """The token and the login (the e-mail Jira Cloud wants) of the picked entry."""
        vault = self.shell.vault
        entry = vault.entry_by_id(vault.integrations.jira.entry_id) if vault else None
        return (entry_secret(entry), entry_login(entry)) if entry is not None else ("", "")

    def _ready(self) -> tuple[JiraSettings, str, str] | None:
        vault = self.shell.vault
        if vault is None:
            return None
        js = vault.integrations.jira
        token, login = self._credentials()
        if not js.url or not token:
            self.set_status(_("Fill in the address and pick the entry with the token"))
            return None
        return js, token, login

    def _tick(self) -> None:
        vault = self.shell.vault
        if vault is None or self._busy or not self.shell.background_jobs or not self.shell.tab_on("integrations"):
            return
        integ = vault.integrations
        js = integ.jira
        if not js.enabled or not js.url or not self._credentials()[0]:
            return
        if time.time() - integ.jira_checked >= js.interval_min * 60:
            self.check_now(quiet=True)

    def check_now(self, quiet: bool = False) -> None:
        if self._busy:
            return
        ready = self._ready()
        if ready is None:
            return
        js, token, login = ready
        integ = self.shell.vault.integrations
        self._busy = True
        self.shell.set_status(_("Checking…"))
        settings = JiraSettings.from_dict(js.to_dict())  # a copy for the thread
        old = {k: dict(v) for k, v in integ.jira_state.items()}
        now = time.time()
        self._jobs.submit("check", jira_check, js.url, token, login, settings, old, self.fetch, integ.jira_since(now), integ.jira_synced, quiet=quiet)

    def check_connection(self) -> None:
        """Who the token belongs to, in the background: “@login” in the status."""
        ready = self._ready()
        if ready is None:
            return
        js, token, login = ready
        self.set_status(_("Connecting…"))
        self._jobs.submit("myself", jira_myself, js.url, token, login, self.fetch)

    def transition(self, key: str, verdict: str) -> bool:
        """Move the issue `key` by the transition the Jira settings name for `verdict`
        ("pass" or "fail"), in the background; the outcome goes to the status line. Nothing
        is sent when no transition is set for that verdict (the default) or Jira is not set
        up: False then. Meant for the journal, after the user confirmed the move."""
        vault = self.shell.vault
        key = key.strip()
        if vault is None or not key:
            return False
        js = vault.integrations.jira
        name = js.transitions.get(verdict, "").strip()
        token, login = self._credentials()
        if not name or not js.url or not token:
            return False
        self._jobs.submit("transition", jira_transition, js.url, token, login, key, name, self.fetch, key=key, name=name)
        return True

    def offer_verdict(self, column: str, items: list[str]) -> list[str]:
        """Items just added to the journal column `column`: when it is the "pass" or "fail"
        column and the Jira settings name a transition for that verdict, a toast
        “KEY-1 → <transition>?” for each issue key among them; only its click calls
        `transition`. The keys offered; none when Jira is not set up, its tab is off or this
        is a preview copy."""
        verdict = verdict_of_column(column)
        vault = self.shell.vault
        if not verdict or vault is None or not self.shell.background_jobs or not self.shell.tab_on("integrations"):
            return []
        js = vault.integrations.jira
        name = js.transitions.get(verdict, "").strip()
        if not name or not js.url or not self._credentials()[0]:
            return []
        offered: list[str] = []
        for item in items:
            key = normalize_item(item)
            if not ISSUE_KEY_RE.fullmatch(key) or key in offered:
                continue
            Toast(self.shell, f"🧭  {key} → {name}?", _("Click to move it in Jira; without a click nothing is sent"), action=functools.partial(self.transition, key, verdict))
            offered.append(key)
        return offered

    def _drain(self) -> None:
        for result in self._jobs.drain():
            if result.kind == "check":
                self._busy = False
                self._checked(result)
            elif result.kind == "myself":
                self._connected(result)
            else:
                self._moved(result)

    @staticmethod
    def _error(exc: BaseException | None) -> str:
        """The error as the user reads it: Jira's own words translated, else the plain text."""
        if isinstance(exc, JiraError):
            return _(str(exc))
        return str(exc) or type(exc).__name__

    def _checked(self, result: JobResult) -> None:
        vault = self.shell.vault
        if vault is None:
            return
        integ = vault.integrations
        if not result.ok:
            integ.note_failed_jira_check(time.time())
            self.set_status(_("Jira check failed: {error}").format(error=self._error(result.error)))
            self.shell.mark_dirty("integrations")
            return
        found = result.value
        first = integ.apply_jira_check(found, time.time())
        if found.events:
            self._notify(found.events)
            self.shell.hub.publish(found.events)  # routines by event and the other subscribers
        self.shell.mark_dirty("integrations")
        if first:
            self.set_status(_("Connected to Jira as @{user}. Watching {n} issue(s); changes from now on will be reported.").format(user=integ.jira.username, n=len(found.snapshot)))
        else:
            self.set_status(_("Checked at {time}: {n} issue(s), new events: {events}").format(time=time.strftime("%H:%M"), n=len(found.snapshot), events=len(found.events)))
        self.shell.update_integrations_badge()
        page = self.page
        if page is not None:
            page.render_feed()

    def _connected(self, result: JobResult) -> None:
        vault = self.shell.vault
        if vault is None:
            return
        if not result.ok:
            self.set_status(_("Could not connect: {error}").format(error=self._error(result.error)))
            return
        js = vault.integrations.jira
        js.username = _user_name(result.value)
        self.shell.mark_dirty("integrations")
        self.set_status(_("Signed in as @{user}").format(user=js.username))

    def _moved(self, result: JobResult) -> None:
        key, name = result.meta.get("key", ""), result.meta.get("name", "")
        if result.ok:
            self.set_status(_("{key}: moved by “{name}”").format(key=key, name=name))
        else:
            self.set_status(_("Could not move {key}: {error}").format(key=key, error=self._error(result.error)))

    def _notify(self, events: list[Event]) -> None:
        for ev in events[:4]:
            Toast(self.shell, f"{event_icon(ev)}  Jira · {ev.title[:60]}", event_text(ev), ev.url)
        if len(events) > 4:
            Toast(self.shell, "Jira", _("And {n} more — see the Integrations tab").format(n=len(events) - 4))
        QApplication.alert(self.shell)

    def set_status(self, text: str) -> None:
        self.status = text
        self.shell.set_status(text)
        page = self.page
        if page is not None and page.status_label is not None and page.status_owner == "jira":
            page.status_label.setText(text)


@guard_events
class EventDelegate(QStyledItemDelegate):
    """A notification: the icon, the text and the MR title wrapped, the time at the right;
    unread ones on the soft accent."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.f_row, self.f_small = theme.font(11), theme.font(9)
        self.fm_row, self.fm_small = QFontMetrics(self.f_row), QFontMetrics(self.f_small)

    def _texts(self, index: QModelIndex | QPersistentModelIndex) -> tuple[Event | None, str]:
        ev = index.data(EVENT_ROLE)
        return ev, str(index.data(MORE_ROLE) or "")

    def _width(self, option: QStyleOptionViewItem) -> int:
        view = self.parent()
        w = option.rect.width()
        if w <= 0 and isinstance(view, QListWidget):
            w = view.viewport().width()
        return max(120, w)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        ev, more = self._texts(index)
        w = self._width(option)
        if ev is None:
            return QSize(w, self.fm_small.height() + 14)
        room = w - 2 * 12 - 26 - self.fm_small.horizontalAdvance("00.00 00:00") - 12
        wrap = int(Qt.TextFlag.TextWordWrap)
        h = self.fm_row.boundingRect(QRect(0, 0, room, 10_000), wrap, event_text(ev)).height()
        h += self.fm_small.boundingRect(QRect(0, 0, room, 10_000), wrap, ev.title).height()
        if mr_actions(ev):
            h += self.fm_small.height() + ACTION_GAP
        return QSize(w, h + 20)

    def action_rects(self, rect: QRect, ev: Event) -> list[tuple[str, str, QRect]]:
        """Where the links of the card's action row stand: (code, text, rect) — painted by
        `paint`, hit by the feed's clicks."""
        x, y = rect.x() + 12 + 26, rect.bottom() - 10 - self.fm_small.height()
        out = []
        for code, text in mr_actions(ev):
            w = self.fm_small.horizontalAdvance(text)
            out.append((code, text, QRect(x, y, w, self.fm_small.height())))
            x += w + 18
        return out

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        t = theme.current()
        r = option.rect
        ev, more = self._texts(index)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if ev is None:
            painter.setFont(self.f_small)
            painter.setPen(QColor(t.LINK))
            painter.drawText(r.adjusted(4, 0, 0, 0), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), more)
            painter.restore()
            return
        box = QRectF(r).adjusted(0.5, 3.5, -0.5, -3.5)
        hot = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.setPen(QColor(t.ACCENT if hot else t.BORDER))
        painter.setBrush(QColor(t.TILE if ev.read else t.ACCENT_SOFT))
        painter.drawRoundedRect(box, 10, 10)
        x = r.x() + 12
        painter.setFont(self.f_row)
        icons.paint(painter, event_icon(ev), QRect(x, r.y() + 10, 18, self.fm_row.height()), t.TEXT, size=18)
        stamp = time.strftime("%d.%m %H:%M", time.localtime(ev.ts))
        sw = self.fm_small.horizontalAdvance(stamp)
        painter.setFont(self.f_small)
        painter.setPen(QColor(t.MUTED))
        painter.drawText(QRect(r.right() - 12 - sw, r.y() + 10, sw, self.fm_small.height()), int(Qt.AlignmentFlag.AlignRight), stamp)
        room = r.width() - 2 * 12 - 26 - sw - 12
        wrap = int(Qt.TextFlag.TextWordWrap)
        painter.setFont(self.f_row)
        painter.setPen(QColor(t.TEXT))
        main = event_text(ev)
        mh = self.fm_row.boundingRect(QRect(0, 0, room, 10_000), wrap, main).height()
        painter.drawText(QRect(x + 26, r.y() + 10, room, mh), wrap, main)
        painter.setFont(self.f_small)
        painter.setPen(QColor(t.MUTED))
        painter.drawText(QRect(x + 26, r.y() + 10 + mh, room, r.height()), wrap, ev.title)
        painter.setPen(QColor(t.LINK))
        for _code, text, at in self.action_rects(r, ev):
            painter.drawText(at, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text)
        painter.restore()


@guard_events
class IntegrationsPage(TabPage):
    """The connections on the left, the settings of the picked one and the feed on the right."""

    name = "integrations"

    def build(self) -> None:
        self.status_label: QLabel | None = None
        self.status_owner = ""  # whose status the label shows: "gitlab" or "jira"
        self._feed_count = FEED_PAGE
        self._settings_width = 640
        _splitter, left, col, right = self.split(260, 220)
        col.addWidget(section(_("CONNECTIONS"), left))
        self.connections = count_list(left)
        for key, text in (("gitlab", "  🦊 GitLab"), ("jira", "  🧭 Jira")):
            item = QListWidgetItem(text, self.connections)
            item.setData(Qt.ItemDataRole.UserRole, key)
        self.connections.setCurrentRow(0)
        self.connections.currentRowChanged.connect(lambda _r: self.render_page())
        col.addWidget(self.connections, 1)

        self.columns = QBoxLayout(QBoxLayout.Direction.LeftToRight, right)
        self.columns.setContentsMargins(0, 0, 0, 0)
        self.columns.setSpacing(0)
        self.settings_area, self.settings, self.settings_col = scroll_area(right, (28, 20, 28, 20))
        self.feed_panel = QWidget(right)
        self.feed_panel.setObjectName("Card")
        fcol = QVBoxLayout(self.feed_panel)
        fcol.setContentsMargins(20, 20, 28, 20)
        head = QHBoxLayout()
        head.addWidget(section(_("NOTIFICATIONS"), self.feed_panel))
        head.addStretch(1)
        read = button(_("Mark all read"), "link", self.feed_panel)
        read.clicked.connect(self._mark_all_read)
        clear = button(_("Clear"), "link", self.feed_panel)
        clear.clicked.connect(self._clear_feed)
        head.addWidget(read)
        head.addWidget(clear)
        fcol.addLayout(head)
        self.feed = QListWidget(self.feed_panel)
        self.feed.setObjectName("Feed")
        self.feed.setItemDelegate(EventDelegate(self.feed))
        self.feed.setMouseTracking(True)
        self.feed.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.feed.setWordWrap(True)
        self.feed.itemClicked.connect(self._open_item)
        fcol.addWidget(self.feed, 1)
        self.feed_empty = label(_("Nothing yet. Events appear here and as pop-ups in the corner of the screen."), "muted", self.feed_panel)
        self.feed_empty.setWordWrap(True)
        fcol.addWidget(self.feed_empty)
        self.columns.addWidget(self.settings_area, 0)
        self.columns.addWidget(self.feed_panel, 1)
        right.installEventFilter(self)
        self._right = right
        self._aside: bool | None = None
        self.feed.viewport().installEventFilter(self)  # the links of a card's action row

    # ---- what the window asks for

    def view_key(self) -> tuple:
        jira = getattr(self.shell, "jira", None)
        return super().view_key() + (self.shell.gitlab.status, jira.status if jira is not None else "")

    def refresh(self, keep_scroll: bool = False) -> None:
        self.render_page()

    def eventFilter(self, obj, event) -> bool:
        from PySide6.QtCore import QEvent

        if obj is self._right and event.type() == QEvent.Type.Resize:
            self._place_columns()
        elif obj is self.feed.viewport() and event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            hit = self.action_at(event.position().toPoint())
            if hit is not None:
                self.feed_action(*hit)
                return True  # not a click on the card: the browser stays shut
        elif obj is self.feed.viewport() and event.type() == QEvent.Type.MouseMove:
            hot = self.action_at(event.position().toPoint()) is not None
            self.feed.viewport().setCursor(Qt.CursorShape.PointingHandCursor if hot else Qt.CursorShape.ArrowCursor)
        return False

    def _place_columns(self) -> None:
        """The feed right of the settings when the width allows, else under them."""
        aside = self._right.width() >= self._settings_width + FEED_MIN_WIDTH
        if aside == self._aside:
            return
        self._aside = aside
        self.columns.setDirection(QBoxLayout.Direction.LeftToRight if aside else QBoxLayout.Direction.TopToBottom)
        if aside:
            self.settings_area.setFixedWidth(self._settings_width)
        else:
            self.settings_area.setMinimumWidth(0)
            self.settings_area.setMaximumWidth(16_777_215)
        self.columns.setStretch(0, 0 if aside else 3)
        self.columns.setStretch(1, 1 if aside else 2)

    def selected(self) -> str:
        item = self.connections.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else "gitlab"

    def update_badge(self) -> None:
        self.shell.update_integrations_badge()

    # ---- the settings

    def _clear_settings(self) -> None:
        while self.settings_col.count():
            item = self.settings_col.takeAt(0)
            if item is None:
                break
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
            else:
                inner = item.layout()
                if inner is not None:
                    QWidget().setLayout(inner)  # the layout and its widgets go with a throwaway owner
        self.status_label = None
        self.status_owner = ""

    def _mark_connections(self) -> None:
        """The dot after a connection that is watched."""
        integ = self.vault.integrations
        self.connections.item(0).setText("  🦊 GitLab" + ("   ●" if integ.gitlab.enabled else ""))
        self.connections.item(1).setText("  🧭 Jira" + ("   ●" if integ.jira.enabled else ""))

    def _entry_combo(self, entry_id: str) -> QComboBox:
        """The vault entry whose secret field holds the token."""
        entries = sorted(self.vault.entries, key=lambda e: e.name.lower())
        combo = QComboBox(self.settings)
        combo.addItem(_("— pick an entry —"), "")
        for e in entries:
            combo.addItem(e.name, e.id)
        current = self.vault.entry_by_id(entry_id)
        combo.setCurrentIndex(entries.index(current) + 1 if current in entries else 0)
        return combo

    def _interval_combo(self, minutes: int) -> QComboBox:
        combo = QComboBox(self.settings)
        for n in INTERVALS:
            combo.addItem(_("every {n} min").format(n=n), n)
        combo.setCurrentIndex(INTERVALS.index(minutes) if minutes in INTERVALS else 0)
        return combo

    def _rule_ticks(self, rules: dict[str, tuple[str, bool]], icon_of: dict[str, str], on: Callable[[str], bool],
                    flip: Callable[[str, bool], None]) -> dict[str, QCheckBox]:
        """A flow of rule check boxes, each with its drawn icon."""
        host, flow = flow_host(self.settings, 18)
        ticks: dict[str, QCheckBox] = {}
        for code, (text, _default) in rules.items():
            tick = QCheckBox(host)
            with_icon(tick, f"{icon_of[code]}  {_(text)}")
            tick.setChecked(on(code))
            tick.toggled.connect(lambda checked, c=code: flip(c, checked))
            flow.addWidget(tick)
            ticks[code] = tick
        self.settings_col.addWidget(host)
        return ticks

    def _status_label(self, owner: str, text: str) -> None:
        self.status_label = label(text, "muted", self.settings)  # under the buttons: a long status wraps, never cut
        self.status_label.setWordWrap(True)
        self.status_owner = owner
        self.settings_col.addWidget(self.status_label)

    def render_page(self) -> None:
        if self.vault is None:
            return
        self._clear_settings()
        self._mark_connections()
        if self.selected() == "jira":
            self._render_jira()
        else:
            self._render_gitlab()
        self._aside = None
        self._place_columns()
        self.render_feed()

    def _render_gitlab(self) -> None:
        integ = self.vault.integrations
        gl = integ.gitlab
        body, col = self.settings, self.settings_col
        head = QHBoxLayout()
        head.addWidget(IconLabel("🦊 GitLab", "heading", body, size=22))
        head.addStretch(1)
        watch = QCheckBox(_("Watch"), body)
        watch.setChecked(gl.enabled)

        def toggle(on: bool) -> None:
            gl.enabled = on
            self.shell.mark_dirty("integrations")
            self._mark_connections()
            if gl.enabled and not integ.gitlab_state:
                self.shell.gitlab.check_now()

        watch.toggled.connect(toggle)
        head.addWidget(watch)
        col.addLayout(head)
        about = label(_("Checks your open merge requests every few minutes and tells you what changed. The token needs the read_api scope and is taken from a password entry."), "muted", body)
        about.setWordWrap(True)
        col.addSpacing(4)
        col.addWidget(about)
        col.addSpacing(14)

        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setHorizontalSpacing(14)
        url = QLineEdit(gl.url or self.shell.default_gitlab_url(), body)
        url.setPlaceholderText("https://gitlab.example.com")
        url.setMinimumWidth(QFontMetrics(url.font()).averageCharWidth() * 36)
        token = self._entry_combo(gl.entry_id)
        interval = self._interval_combo(gl.interval_min)
        form.addRow(_("Address"), url)
        form.addRow(_("Token from entry"), token)
        form.addRow("", label(_("its secret field holds the token"), "muted", body))
        form.addRow(_("Check"), interval)
        col.addLayout(form)

        def save_form(*_args: object) -> None:
            gl.url = url.text().strip().rstrip("/")
            gl.entry_id = token.currentData() or ""
            gl.interval_min = int(interval.currentData())
            self.shell.mark_dirty("integrations")

        url.textEdited.connect(save_form)
        token.activated.connect(save_form)
        interval.activated.connect(save_form)

        col.addSpacing(18)
        col.addWidget(section(_("NOTIFY ME WHEN"), body))
        col.addSpacing(6)

        def flip(c: str, on: bool) -> None:
            gl.rules[c] = on
            integ.gitlab_full = 0.0  # a rule just on has no facts yet: the next check asks about all
            self.shell.mark_dirty("integrations")

        self._rule_ticks(RULES, EVENT_ICONS, gl.rule, flip)
        self._render_projects()
        self._render_pipelines()
        col.addSpacing(16)
        check = button(_("Check now"), "primary", body)
        check.clicked.connect(lambda: self.shell.gitlab.check_now())
        col.addWidget(check, 0, Qt.AlignmentFlag.AlignLeft)
        col.addSpacing(6)
        who = _("Signed in as @{user}").format(user=gl.username) if gl.username else ""
        self._status_label("gitlab", self.shell.gitlab.status or who)
        col.addStretch(1)
        self._settings_width = max(560, min(760, body.sizeHint().width() + 20))

    def _render_projects(self) -> None:
        integ = self.vault.integrations
        gl = integ.gitlab
        body, col = self.settings, self.settings_col
        col.addSpacing(18)
        col.addWidget(section(_("PROJECTS"), body))
        known = sorted(set(integ.gitlab_projects) | set(gl.projects))

        def hint() -> str:
            if not known:
                return _("The projects with your open merge requests appear here after the first check.")
            return _("Only the ticked projects are watched.") if gl.projects else _("None ticked — all projects are watched.")

        note = label(hint(), "muted", body)
        note.setWordWrap(True)
        col.addWidget(note)
        host, flow = flow_host(body, 18)
        ticks: dict[str, QCheckBox] = {}

        def changed(_on: bool) -> None:
            gl.projects = [p for p in known if ticks[p].isChecked()]
            integ.gitlab_full = 0.0
            note.setText(hint())
            self.shell.mark_dirty("integrations")

        for path in known:
            tick = QCheckBox(path, host)
            tick.setChecked(path in gl.projects)
            tick.toggled.connect(changed)
            ticks[path] = tick
            flow.addWidget(tick)
        col.addWidget(host)
        self.project_ticks = ticks

    def _render_pipelines(self) -> None:
        """The watched branches: a row each (with the error of its last look under it),
        “Add”, and the rules about them."""
        integ = self.vault.integrations
        gl = integ.gitlab
        body, col = self.settings, self.settings_col
        col.addSpacing(18)
        col.addWidget(section(_("PIPELINES AND BRANCHES"), body))
        if gl.pipelines:
            hint = _("The newest pipeline and the new commits of these branches are watched, whoever started them.")
        else:
            hint = _("No branches watched yet. Add one to hear when its nightly pipeline fails or new commits land.")
        note = label(hint, "muted", body)
        note.setWordWrap(True)
        col.addWidget(note)
        col.addSpacing(4)
        self.watch_rows: dict[str, IconLabel] = {}
        self.watch_errors: dict[str, QLabel] = {}
        for w in gl.pipelines:
            key = w.key()
            row = QHBoxLayout()
            row.setSpacing(6)
            text = IconLabel(f"branch  {watch_text(w)}", "", body)
            text.setWordWrap(True)
            row.addWidget(text, 1)
            row.addWidget(icon_button("🗑", _("Stop watching"), functools.partial(self.remove_watch, key), body))
            col.addLayout(row)
            self.watch_rows[key] = text
            error = str(integ.gitlab_pipelines.get(key, {}).get("error") or "")
            if error:
                err = label(_(error), "error", body)
                err.setWordWrap(True)
                err.setContentsMargins(24, 0, 0, 0)  # under the text, past the icon
                col.addWidget(err)
                self.watch_errors[key] = err
        add = button(_("+ Add a branch…"), "link", body)
        add.clicked.connect(lambda: self.watch_dialog())
        col.addWidget(add, 0, Qt.AlignmentFlag.AlignLeft)
        col.addSpacing(6)

        def flip(c: str, on: bool) -> None:
            gl.rules[c] = on
            self.shell.mark_dirty("integrations")

        self.watch_ticks = self._rule_ticks(WATCH_RULES, EVENT_ICONS, gl.rule, flip)

    def watch_dialog(self) -> Dialog:
        """Add a watched branch: the project, the branch or tag, which pipelines count, a
        name for the feed. Not blocking: the dialog is returned at once."""
        gl = self.vault.integrations.gitlab
        dlg = Dialog(self.shell, _("Watch a branch"))
        project = QLineEdit(dlg)
        project.setPlaceholderText("group/repo")
        project.setMinimumWidth(project.fontMetrics().averageCharWidth() * 34)
        ref = QLineEdit(dlg)
        ref.setPlaceholderText("main")
        source = QComboBox(dlg)
        for code in PIPELINE_SOURCES:
            source.addItem(source_text(code), code)
        name = QLineEdit(dlg)
        name.setPlaceholderText(_("optional — how the feed calls it"))
        rows = ((_("Project"), project), (_("Branch or tag"), ref), (_("Pipelines"), source), (_("Name"), name))
        for row, (text, widget) in enumerate(rows):
            dlg.box.addWidget(label(text, "", dlg), row, 0)
            dlg.box.addWidget(widget, row, 1)
        err = label("", "error", dlg)
        err.setWordWrap(True)
        dlg.box.addWidget(err, len(rows), 0, 1, 2)

        def save() -> None:
            w = PipelineWatch.from_dict({"project": project.text(), "ref": ref.text(), "source": source.currentData(), "name": name.text().strip()})
            if w is None:
                err.setText(_("Fill in the project and the branch"))
                return
            if any(x.key() == w.key() for x in gl.pipelines):
                err.setText(_("This branch is already watched"))
                return
            gl.pipelines.append(w)
            self.shell.mark_dirty("integrations")
            dlg.accept()
            self.render_page()

        dlg.editor_buttons(len(rows) + 1, 2, save)
        dlg.keys(save)
        self.watch_parts = {"project": project, "ref": ref, "source": source, "name": name, "error": err, "save": save, "dialog": dlg}
        project.setFocus()
        dlg.open()
        return dlg

    def remove_watch(self, key: str) -> None:
        integ = self.vault.integrations
        integ.gitlab.pipelines = [w for w in integ.gitlab.pipelines if w.key() != key]
        integ.gitlab_pipelines.pop(key, None)
        self.shell.mark_dirty("integrations")
        self.render_page()

    def _render_jira(self) -> None:
        integ = self.vault.integrations
        js = integ.jira
        body, col = self.settings, self.settings_col
        head = QHBoxLayout()
        head.addWidget(IconLabel("🧭 Jira", "heading", body, size=22))
        head.addStretch(1)
        watch = QCheckBox(_("Watch"), body)
        watch.setChecked(js.enabled)

        def toggle(on: bool) -> None:
            js.enabled = on
            self.shell.mark_dirty("integrations")
            self._mark_connections()
            if js.enabled and not integ.jira_state:
                self.shell.jira.check_now()

        watch.toggled.connect(toggle)
        head.addWidget(watch)
        col.addLayout(head)
        about = label(_("Checks the issues of your filter every few minutes and tells you what changed. The token is taken from a password entry: on Jira Cloud (*.atlassian.net) an API token, with your e-mail in the entry's login field; on your own server a personal access token."), "muted", body)
        about.setWordWrap(True)
        col.addSpacing(4)
        col.addWidget(about)
        col.addSpacing(14)

        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setHorizontalSpacing(14)
        url = QLineEdit(js.url, body)
        url.setPlaceholderText("https://example.atlassian.net")
        url.setMinimumWidth(QFontMetrics(url.font()).averageCharWidth() * 36)
        token = self._entry_combo(js.entry_id)
        interval = self._interval_combo(js.interval_min)
        statuses = QLineEdit(", ".join(js.statuses), body)
        statuses.setPlaceholderText("Ready for test, In review")
        jql = QLineEdit(js.jql, body)
        jql.setPlaceholderText(DEFAULT_JQL)
        jql.setCursorPosition(0)
        form.addRow(_("Address"), url)
        form.addRow(_("Token from entry"), token)
        form.addRow("", label(_("its secret field holds the token; for Jira Cloud its login field holds your e-mail"), "muted", body))
        form.addRow(_("Check"), interval)
        form.addRow(_("Watched statuses"), statuses)
        form.addRow("", label(_("comma separated"), "muted", body))
        form.addRow(_("JQL filter"), jql)
        form.addRow("", label(_("empty — the issues assigned to you, reported by you or watched"), "muted", body))
        col.addLayout(form)

        col.addSpacing(18)
        col.addWidget(section(_("NOTIFY ME WHEN"), body))
        col.addSpacing(6)

        def flip(c: str, on: bool) -> None:
            js.rules[c] = on
            self.shell.mark_dirty("integrations")

        self.jira_ticks = self._rule_ticks(JIRA_RULES, JIRA_ICONS, js.rule, flip)

        col.addSpacing(18)
        col.addWidget(section(_("AFTER A VERDICT"), body))
        verdict_note = label(_("The transition offered after you mark an issue verified or failed. Empty — nothing is ever sent to Jira."), "muted", body)
        verdict_note.setWordWrap(True)
        col.addWidget(verdict_note)
        col.addSpacing(6)
        moves = QFormLayout()
        moves.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        moves.setHorizontalSpacing(14)
        moved: dict[str, QLineEdit] = {}
        for verdict, text in (("pass", _("Verified — move to")), ("fail", _("Failed — move to"))):
            edit = QLineEdit(js.transitions.get(verdict, ""), body)
            edit.setPlaceholderText(_("not set"))
            moves.addRow(text, edit)
            moved[verdict] = edit
        col.addLayout(moves)

        def save_form(*_args: object) -> None:
            js.url = url.text().strip().rstrip("/")
            js.entry_id = token.currentData() or ""
            js.interval_min = int(interval.currentData())
            js.statuses = parse_statuses(statuses.text())
            if jql.text().strip() != js.jql:
                js.jql = jql.text().strip()
                integ.jira_full = 0.0  # another filter: the next check reads all of it
            js.transitions = {v: e.text().strip() for v, e in moved.items() if e.text().strip()}
            self.shell.mark_dirty("integrations")

        for edit in (url, statuses, jql, *moved.values()):
            edit.textEdited.connect(save_form)
        token.activated.connect(save_form)
        interval.activated.connect(save_form)
        self.jira_parts = {"url": url, "token": token, "interval": interval, "statuses": statuses, "jql": jql, "pass": moved["pass"], "fail": moved["fail"], "watch": watch}

        col.addSpacing(16)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        check = button(_("Check now"), "primary", body)
        check.clicked.connect(lambda: self.shell.jira.check_now())
        connect = button(_("Check connection"), "", body)
        connect.clicked.connect(lambda: self.shell.jira.check_connection())
        buttons.addWidget(check)
        buttons.addWidget(connect)
        buttons.addStretch(1)
        col.addLayout(buttons)
        col.addSpacing(6)
        who = _("Signed in as @{user}").format(user=js.username) if js.username else ""
        self._status_label("jira", self.shell.jira.status or who)
        col.addStretch(1)
        self._settings_width = max(560, min(760, body.sizeHint().width() + 20))

    # ---- the feed

    def render_feed(self) -> None:
        if self.vault is None:
            return
        feed = self.vault.integrations.feed
        self.feed.clear()
        self.feed_empty.setVisible(not feed)
        self.feed.setVisible(bool(feed))
        for ev in feed[:self._feed_count]:
            item = QListWidgetItem(self.feed)
            item.setData(EVENT_ROLE, ev)
        rest = len(feed) - self._feed_count
        if rest > 0:
            more = QListWidgetItem(self.feed)
            more.setData(MORE_ROLE, _("Show more ({n})").format(n=rest))

    def _open_item(self, item: QListWidgetItem) -> None:
        ev = item.data(EVENT_ROLE)
        if ev is None:
            self._feed_count += FEED_PAGE
            self.render_feed()
            return
        ev.read = True
        if ev.url.startswith(("https://", "http://")):
            webbrowser.open(ev.url)
        self.shell.mark_dirty("integrations")
        self.render_feed()
        self.update_badge()

    def action_at(self, pos: QPoint) -> tuple[Event, str] | None:
        """The card event and the action whose link is under `pos` (viewport coordinates)."""
        item = self.feed.itemAt(pos)
        delegate = self.feed.itemDelegate()
        if item is None or not isinstance(delegate, EventDelegate):
            return None
        ev = item.data(EVENT_ROLE)
        if ev is None:
            return None
        for code, _text, at in delegate.action_rects(self.feed.visualItemRect(item), ev):
            if at.adjusted(-4, -3, 4, 3).contains(pos):
                return ev, code
        return None

    def feed_action(self, ev: Event, code: str) -> Any:
        """A link of a card's action row: “preview” opens the MR preview (returned), “review”
        and “fix” hand the assignment over (see mr_preview.send). The card counts as read."""
        from ..dialogs.mr_preview import MrPreview, send_mr, target_of

        target = target_of(ev)
        if target is None:
            return None
        ev.read = True
        self.shell.mark_dirty("integrations")
        self.render_feed()
        self.update_badge()
        if code == "preview":
            preview = MrPreview(self.shell, target, fetch=getattr(self.shell.gitlab, "fetch", None))
            preview.show()
            return preview
        return send_mr(self.shell, "mr_review" if code == "review" else "mr_fix", target)

    def _mark_all_read(self) -> None:
        self.vault.integrations.mark_all_read()
        self.shell.mark_dirty("integrations")
        self.render_feed()
        self.update_badge()

    def _clear_feed(self) -> None:
        self.vault.integrations.clear_feed()
        self.shell.mark_dirty("integrations")
        self.render_feed()
        self.update_badge()
