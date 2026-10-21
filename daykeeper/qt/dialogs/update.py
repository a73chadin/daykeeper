"""Updates in the window: the daily check, the quiet note that a version is out, the line in
Settings → General → About, and the dialog that shows what is new and does the update.

The check and the download run on threads (jobs.run_in_thread); everything that decides
lives in daykeeper/update.py. Nothing happens without a click: the check only tells, the
dialog shows the release notes first, and only "Update now" downloads. Before anything is
replaced the vault is saved and copied to the backup folder; a vault that cannot be saved
stops the update."""

from __future__ import annotations

import shutil
import sys
import threading
import time
from functools import partial
from pathlib import Path
from typing import Any

import shiboken6
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QCheckBox, QHBoxLayout, QPlainTextEdit, QProgressBar, QVBoxLayout, QWidget

from ... import __version__, update
from ...i18n import _
from .. import theme
from ..errors import guard_events
from ..jobs import run_in_thread
from ..timers import Debounce, Ticker
from .base import Dialog, button, label, show_info

FIRST_CHECK_MS = 60_000  # the first automatic check a minute after the start: the start stays quiet
TICK_MS = 3_600_000  # then once an hour the daily gate is asked (a computer left on for days)
PROGRESS_MS = 100


def reason_text(reason: str, executable: Path, platform: str = sys.platform) -> str:
    """update.why_not in words."""
    if reason == "readonly" and platform == "darwin":
        app = update.app_location(platform, executable)
        folder = app.parent if app is not None else executable.parent
        return _("Daykeeper cannot write to the folder that holds Daykeeper.app ({folder}). Move Daykeeper.app to a folder of your own, such as Applications, and update again.").format(folder=folder)
    if reason == "preview":
        return _("This is a preview copy: updates are installed from the main window.")
    if reason == "source":
        return _("This copy runs from the source code: update it with git, not from here.")
    if reason == "translocated":
        return _("macOS runs this copy from a protected place. Move Daykeeper.app to Applications, start it from there and update again.")
    if reason == "readonly":
        return _("Daykeeper cannot write next to its folder ({folder}). Move the folder somewhere of your own, or use the installer.").format(folder=executable.parent)
    if reason:
        return _("On this system Daykeeper is updated by hand: download the new version from the releases page.")
    return ""


@guard_events
class UpdateWatch(QObject):
    """Made once with the window. `available` — the release to offer (None: none known);
    `state` — "", "checking", "latest" or "error" (then `error` says why). `changed` fires
    when any of them changes. The fields below `http` are the seams the tests fill."""

    changed = Signal()

    def __init__(self, window: Any):
        super().__init__(window)
        self.window = window
        self.http: Any = update.Http()
        self.platform = sys.platform
        self.executable = Path(sys.executable)
        self.frozen = bool(getattr(sys, "frozen", False))
        self.launch = update.launch_detached
        self.quit_app = self._quit
        self.now = time.time
        self.available: update.Release | None = None
        self.state = ""
        self.error = ""
        self.dialog: UpdateDialog | None = None
        self._tell_due = False
        self._first = Debounce(window, FIRST_CHECK_MS, self.maybe_check)
        self._daily = Ticker(window, TICK_MS, self.maybe_check)

    # ---- settings

    def settings(self) -> dict:
        return self.window.session.settings()

    @property
    def enabled(self) -> bool:
        return update.check_enabled(self.settings())

    @property
    def prereleases(self) -> bool:
        return update.prereleases_wanted(self.settings())

    def set_enabled(self, on: bool) -> None:
        self.window.session.write_settings({update.SETTING_ON: bool(on)})
        if on:
            self._daily.start()
        else:
            self._first.cancel()
            self._daily.stop()

    def set_prereleases(self, on: bool) -> None:
        self.window.session.write_settings({update.SETTING_PRE: bool(on)})
        if not on and self.available is not None and self.available.prerelease:
            self.available = None
            self.state = ""
            self.changed.emit()

    # ---- when

    def start(self) -> None:
        """A built app's main window, at start: what an earlier update left goes, and the
        daily check is booked. A run from source and a preview copy check only on demand."""
        if not (self.frozen and self.window.background_jobs):
            return
        platform, exe = self.platform, self.executable
        threading.Thread(target=lambda: update.sweep(update.leftovers(platform, exe)), name="update-sweep", daemon=True).start()
        if self.enabled:
            self._first.touch()
            self._daily.start()

    def maybe_check(self) -> None:
        if update.check_due(self.settings(), self.now()):
            self.check()

    def on_unlock(self) -> None:
        """A note found while the window was locked is shown now."""
        self.tell()

    # ---- the check

    def check(self, manual: bool = False) -> None:
        if self.state == "checking":
            return
        self.state, self.error = "checking", ""
        self.changed.emit()
        http, pre = self.http, self.prereleases
        run_in_thread(lambda: update.check(http, __version__, pre), partial(self._checked, manual), owner=self, name="update-check")

    def _checked(self, manual: bool, result: Any, error: BaseException | None) -> None:
        if error is not None:
            # not stamped: the daily gate stays open, and the hourly ticker tries again
            self.state, self.error = "error", str(error) or type(error).__name__
        else:
            self.window.session.write_settings({update.SETTING_CHECKED: self.now()})
            self.available = result
            self.state = "latest" if result is None else ""
            if result is not None and not manual:
                self._tell_due = True
        self.changed.emit()
        self.tell()

    def tell(self) -> None:
        """The quiet note, once per version, while the vault is open (a toast closes on
        lock; it would only flash)."""
        release = self.available
        if not self._tell_due or release is None or self.window.vault is None:
            return
        self._tell_due = False
        if self.settings().get(update.SETTING_TOLD) == release.version:
            return
        self.window.session.write_settings({update.SETTING_TOLD: release.version})
        from ..widgets.toast import Toast

        Toast(self.window, "⟳  Daykeeper", _("Version {version} is out — Update…").format(version=release.version), action=self.open_dialog)

    # ---- the update

    def blocked(self) -> str:
        """Why this copy must not update itself, in words; "" — it may."""
        return reason_text(update.why_not(self.frozen, bool(getattr(self.window, "preview", False)), self.platform, self.executable), self.executable, self.platform)

    def open_dialog(self) -> UpdateDialog | None:
        release = self.available
        if release is None or self.window.vault is None:
            return None
        reason = self.blocked()
        if reason:
            show_info(self.window, reason)
            return None
        if self.dialog is not None and shiboken6.isValid(self.dialog) and self.dialog.isVisible():
            self.dialog.raise_()
            self.dialog.activateWindow()
            return self.dialog
        self.dialog = UpdateDialog(self.window, self, release)
        self.dialog.open()
        return self.dialog

    def apply(self, prepared: update.Prepared) -> None:
        """Start what puts the new copy in place, then quit: it waits for this process."""
        self.launch(update.apply_command(prepared, self.platform, self.executable), self.platform)
        self.quit_app()

    def _quit(self) -> None:
        self.window.close()
        QApplication.quit()


def discard(prepared: update.Prepared) -> None:
    """A verified update that will not be used: its files go."""
    for path in (prepared.workdir, prepared.staging):
        if path is not None:
            shutil.rmtree(path, ignore_errors=True)


@guard_events
class UpdateDialog(Dialog):
    """What is new in the release, then the download, the checks and the update itself."""

    def __init__(self, window: Any, watch: UpdateWatch, release: update.Release):
        super().__init__(window, _("Update to {version}").format(version=release.version))
        self.shell, self.watch, self.release = window, watch, release
        self.cancel_event: threading.Event | None = None
        self.progress = update.Progress()
        self.running = False
        self.box.addWidget(label(_("Daykeeper {new} is out; this is {current}.").format(new=release.version, current=__version__), "heading", self), 0, 0)
        self.notes = QPlainTextEdit(self)
        self.notes.setReadOnly(True)
        self.notes.setPlainText(update.notes_for(release) or _("The release has no notes."))
        self.notes.setMinimumSize(theme.px(560), theme.px(260))
        self.box.addWidget(self.notes, 1, 0)
        hint = label(_("Before the update the vault is saved and copied to the backup folder. The download is used only when it matches the release's signed checksums. The app then closes and starts again in the new version."), "muted", self)
        hint.setWordWrap(True)
        self.box.addWidget(hint, 2, 0)
        self.bar = QProgressBar(self)
        self.bar.setTextVisible(False)
        self.bar.hide()
        self.box.addWidget(self.bar, 3, 0)
        self.status = label("", "muted", self)
        self.status.setWordWrap(True)
        self.box.addWidget(self.status, 4, 0)
        row = QHBoxLayout()
        row.addStretch(1)
        self.go = button(_("Update now"), "primary", self)
        self.go.clicked.connect(self.start)
        self.cancel = button(_("Cancel"), "", self)
        self.cancel.clicked.connect(self.reject)
        row.addWidget(self.go)
        row.addWidget(self.cancel)
        self.box.addLayout(row, 5, 0)
        self._timer = QTimer(self)
        self._timer.setInterval(PROGRESS_MS)
        self._timer.timeout.connect(self._show_progress)

    def _say(self, text: str, role: str = "muted") -> None:
        self.status.setProperty("role", role)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.status.setText(text)

    def _save(self) -> bool:
        """Whatever is typed goes into the vault and the vault to disk; False — it did not."""
        self.shell.flush_edits()
        return self.shell.save_now() == "ok"

    def start(self) -> None:
        if self.running:
            return
        reason = self.watch.blocked()
        if reason:
            self._say(reason, "error")
            return
        if not self._save():
            self._say(_("The vault could not be saved, so nothing was updated. Save it (Ctrl+S) and try again."), "error")
            return
        self.running = True
        self.go.setEnabled(False)
        self.cancel_event = threading.Event()
        self.progress = update.Progress()
        self.bar.setRange(0, 0)
        self.bar.show()
        self._say(_("Checking the signature of the release…"))
        self._timer.start()
        w = self.watch
        release, cancel, progress = self.release, self.cancel_event, self.progress
        run_in_thread(lambda: update.prepare(release, w.platform, w.executable, w.http, progress, cancel), self._prepared, owner=self, name="update-download")

    def _show_progress(self) -> None:
        p = self.progress
        if p.stage == "download":
            if p.total > 0:
                self.bar.setRange(0, 1000)
                self.bar.setValue(int(p.done * 1000 / p.total))
            mb = p.done / 1024 / 1024
            self._say(_("Downloading: {done:.1f} of {total:.1f} MB").format(done=mb, total=p.total / 1024 / 1024) if p.total else _("Downloading: {done:.1f} MB").format(done=mb))
        elif p.stage == "unpack":
            self.bar.setRange(0, 0)
            self._say(_("Unpacking…"))

    def _prepared(self, prepared: Any, error: BaseException | None) -> None:
        self.running = False
        self._timer.stop()
        self.bar.hide()
        if isinstance(error, update.Cancelled):
            self._say(_("Cancelled. The download was deleted."))
            return
        if error is not None:
            self.go.setEnabled(True)
            reason = str(error) or type(error).__name__
            if isinstance(error, update.UpdateError):
                self._say(_("The update was refused: {reason}. Nothing was changed, and the download was deleted.").format(reason=reason), "error")
            else:  # a disk or system failure, not a check that said no
                self._say(_("Could not install the update: {reason}. Nothing was changed, and the download was deleted.").format(reason=getattr(error, "strerror", None) or reason), "error")
            return
        if self.shell.vault is None:  # locked while the download ran: nothing starts behind the lock
            discard(prepared)
            self.go.setEnabled(True)
            self._say(_("The vault was locked, so nothing was updated. The download was deleted."), "error")
            return
        if not self._save():
            discard(prepared)
            self.go.setEnabled(True)
            self._say(_("The vault could not be saved, so nothing was updated. Save it (Ctrl+S) and try again."), "error")
            return
        try:
            if self.shell.vault is not None:
                self.shell.vault.backup_now()
        except OSError as exc:
            discard(prepared)
            self.go.setEnabled(True)
            self._say(_("No copy of the vault could be made, so nothing was updated: {error}").format(error=exc.strerror or exc), "error")
            return
        self._say(_("Starting the update…"))
        try:
            self.watch.apply(prepared)
        except (OSError, update.UpdateError) as exc:
            discard(prepared)
            self.go.setEnabled(True)
            self._say(_("The update could not start: {error}").format(error=getattr(exc, "strerror", None) or exc), "error")

    def reject(self) -> None:
        """Cancel: a download under way stops, and what it fetched is deleted (update.prepare)."""
        if self.cancel_event is not None:
            self.cancel_event.set()
        self._timer.stop()
        super().reject()

    def closeEvent(self, event) -> None:
        if self.cancel_event is not None:
            self.cancel_event.set()
        super().closeEvent(event)

    def on_lock(self) -> None:
        if self.cancel_event is not None:
            self.cancel_event.set()


@guard_events
class UpdateRow(QWidget):
    """The update lines of Settings → General → About: what the check found, "Update…",
    "Check for updates", and the two switches."""

    def __init__(self, watch: UpdateWatch, parent: QWidget | None = None):
        super().__init__(parent)
        self.watch = watch
        preview = bool(getattr(watch.window, "preview", False))
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 4, 0, 4)
        col.setSpacing(6)
        line = QHBoxLayout()
        self.status = label("", "muted", self)
        self.status.setWordWrap(True)
        self.update_btn = button(_("Update…"), "small", self)
        self.update_btn.setToolTip(_("Shows what is new first; nothing is downloaded until you confirm."))
        self.update_btn.clicked.connect(lambda: watch.open_dialog())
        line.addWidget(self.status, 0)
        line.addWidget(self.update_btn, 0)
        line.addStretch(1)
        self._line = line
        col.addLayout(line)
        row = QHBoxLayout()
        self.check_btn = button(_("Check for updates"), "small", self)
        self.check_btn.setToolTip(_("Asks GitHub once which release is the latest. Nothing about you or your data is sent."))
        self.check_btn.clicked.connect(lambda: watch.check(manual=True))
        row.addWidget(self.check_btn)
        row.addStretch(1)
        col.addLayout(row)
        self.daily = QCheckBox(_("Check for updates once a day"), self)
        self.daily.setToolTip(_("One request to GitHub a day, with no data of yours; a new version is only announced, never installed by itself."))
        self.daily.setChecked(watch.enabled)
        self.daily.toggled.connect(watch.set_enabled)
        col.addWidget(self.daily)
        self.pre = QCheckBox(_("Offer test versions (pre-releases)"), self)
        self.pre.setToolTip(_("Versions still in testing, like 2.1.0-beta.1. Off: only finished releases are offered."))
        self.pre.setChecked(watch.prereleases)
        self.pre.toggled.connect(watch.set_prereleases)
        col.addWidget(self.pre)
        if preview:
            for w in (self.update_btn, self.check_btn, self.daily, self.pre):
                w.setEnabled(False)
        watch.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        w = self.watch
        release = w.available
        self.update_btn.setVisible(release is not None)
        # "Version X is out —" stays one line with "Update…" right after it; any other text
        # (an error can be long) wraps over the free width — a wrapped label beside a stretch
        # was given its narrowest size, a word or two a line
        self.status.setWordWrap(release is None)
        self._line.setStretch(0, 0 if release is not None else 1)  # the text
        self._line.setStretch(2, 1 if release is not None else 0)  # the space after the button
        self.check_btn.setEnabled(w.state != "checking" and not getattr(w.window, "preview", False))
        if getattr(w.window, "preview", False):
            text = _("This is a preview copy: updates are installed from the main window.")
        elif release is not None:
            text = _("Version {version} is out —").format(version=release.version)
        elif w.state == "checking":
            text = _("Checking for updates…")
        elif w.state == "error":
            text = _("Could not check for updates: {error}").format(error=w.error)
        elif w.state == "latest":
            text = _("This is the latest version.")
        else:
            last = w.settings().get(update.SETTING_CHECKED)
            if isinstance(last, (int, float)) and not isinstance(last, bool) and last > 0:
                text = _("Last checked {when}.").format(when=time.strftime("%d.%m.%Y %H:%M", time.localtime(last)))
            else:
                text = _("Not checked yet.")
        self.status.setProperty("role", "ok" if release is not None else "error" if w.state == "error" else "muted")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.status.setText(text)
        self.status.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
