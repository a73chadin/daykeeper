"""The “From a call…” wizard of the Notes tab (calls.py holds everything that needs no
window): a transcript or a recording of a call becomes a note card of today with a
summary, notes and tasks, the transcript attached.

Four screens in one window:

1. the file: a transcript (.txt, .md, .vtt, .srt) is read as is; a recording goes through
   whisper.cpp first — when whisper-cli or its model is missing the screen says so and
   offers the settings;
2. the progress: ffmpeg makes a WAV of 16 kHz mono in a temp folder when the recording
   needs it, then whisper-cli transcribes it, then the assistant writes the digest over
   the quiet channel (`claude -p`). Every step is a child process read by a thread, so
   the window never waits on it; Stop kills the child with everything it started;
3. (the digest has no screen of its own: its answer fills the review);
4. the review: the title, the summary, the notes and the tasks, each item with a check box
   (unchecked ones are left out), every text editable; “Create note” makes the card.

Without Claude Code the wizard still makes a card — the title and the transcript, no
summary — and says why. An answer Daykeeper cannot read shows as it came, as an error,
and the card can still be made from the transcript alone.

The window is not modal: a recording takes minutes to transcribe and the rest of the app
stays usable meanwhile. It closes on lock (registered with the main window), and closing
it any way stops what runs and removes the temp folder.

`CallWatch` looks at a folder (Downloads by default) while the vault is open: a call's
transcript downloaded there — from a meeting service that writes one — is offered by a
toast, and a click on it opens the wizard on that file."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

import shiboken6
from PySide6.QtCore import QFileSystemWatcher, Qt
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QProgressBar, QVBoxLayout, QWidget

from ...assistant_runs import CREATE_NO_WINDOW, QuietRun, QuietRuns, kill_tree
from ...assistants import Assignment, claude_command, quiet_argv, read_defaults
from ...calls import (
    MAX_TRANSCRIPT,
    SETTING_LANGUAGE,
    SETTING_THREADS,
    SETTING_WATCH,
    SETTING_WATCH_SINCE,
    TASK_MARK,
    Digest,
    Task,
    decode_text,
    digest_prompt,
    ffmpeg_argv,
    is_audio,
    is_transcript,
    meeting_name,
    needs_conversion,
    new_call_id,
    new_transcripts,
    note_from_digest,
    parse_digest,
    parse_transcript,
    transcribe_argv,
    transcript_file,
    watch_folder,
    whisper_binary,
    whisper_model,
)
from ...i18n import _
from ...journal import today
from .. import theme
from ..errors import guard_events
from ..jobs import Waker, run_in_thread
from ..timers import Debounce, Ticker
from .base import Dialog, button, label

WIDTH = 600  # px of the window at the system's text size
DIGEST_PATIENCE = 300.0  # seconds the digest of a long call is waited on
ERR_TAIL = 1500
WATCH_EVERY_MS = 30_000  # the folder is looked at this often, besides on its change
WATCH_SETTLE_MS = 3_000  # after a change of the folder: a download ends with a rename or two
OFFER_MS = 60_000  # the toast offering a transcript stays this long
OFFERS_AT_ONCE = 3  # more transcripts found in one look are left to "From a call…"
FILE, PROGRESS, REVIEW = 0, 1, 2  # the screens


def default_assistant(assignment: Assignment, settings: Mapping, which: Callable[[str], str | None] = shutil.which) -> list[str] | None:
    """The quiet channel's command line, or None when Claude Code is not installed (the
    quiet channel runs Claude Code only)."""
    if not which("claude"):
        return None
    return quiet_argv(assignment, "claude", read_defaults(settings).model, claude_command())


@dataclass
class CallTools:
    """What the wizard looks up and starts. The defaults are the real programs; the tests
    pass stand-ins (a Python child for whisper and the assistant, fake lookups), so nothing
    real is ever started or looked at there."""

    which: Callable[[str], str | None] = shutil.which
    exists: Callable[[str], bool] = os.path.isfile
    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)
    transcribe: Callable[[str, str, str, str, int], list[str]] = transcribe_argv
    ffmpeg: Callable[[str, str], list[str] | None] | None = None  # None — ffmpeg_argv with `which`
    assistant: Callable[[Assignment, Mapping], list[str] | None] | None = None  # None — default_assistant with `which`
    runs: Callable[..., QuietRuns] = QuietRuns

    def ffmpeg_command(self, src: str, dst: str) -> list[str] | None:
        return self.ffmpeg(src, dst) if self.ffmpeg is not None else ffmpeg_argv(src, dst, self.which)

    def assistant_command(self, assignment: Assignment, settings: Mapping) -> list[str] | None:
        if self.assistant is not None:
            return self.assistant(assignment, settings)
        return default_assistant(assignment, settings, self.which)


def _decode(raw: bytes | None) -> str:
    return (raw or b"").decode("utf-8", errors="replace")


class Child:
    """A program run in the background: started here (an OSError goes to the caller), read
    to the end by a thread, then `done(code, out, err)` on the window's thread — never
    after `stop`, which kills it with everything it started."""

    def __init__(self, argv: list[str], done: Callable[[int, str, str], None], owner: QWidget):
        self.argv = argv
        self.stopped = False
        self._done = done
        self.proc = subprocess.Popen(  # noqa: S603 — whisper-cli / ffmpeg, the argv built by calls.py
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            start_new_session=sys.platform != "win32",  # its own group: a stop ends its children too
        )
        run_in_thread(self.proc.communicate, self._finished, owner, name="call-child")

    def _finished(self, result: Any, error: BaseException | None) -> None:
        if self.stopped:
            return
        out, err = result if result is not None else (b"", str(error or "").encode())
        self._done(self.proc.returncode if self.proc.returncode is not None else -1, _decode(out), _decode(err))

    def stop(self) -> None:
        self.stopped = True
        kill_tree(self.proc)

    @property
    def running(self) -> bool:
        return self.proc.poll() is None


def _check_item(text: str, parent: QListWidget) -> QListWidgetItem:
    item = QListWidgetItem(text, parent)
    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsUserCheckable)
    item.setCheckState(Qt.CheckState.Checked)
    return item


def _checked(lst: QListWidget) -> list[str]:
    out = []
    for i in range(lst.count()):
        item = lst.item(i)
        text = " ".join(item.text().split())
        if item.checkState() == Qt.CheckState.Checked and text:
            out.append(text)
    return out


@guard_events
class CallWizard(Dialog):
    """The wizard over the Notes tab `page`. `choose(path)` is what the file screen does
    with a picked file (the tests call it directly)."""

    def __init__(self, page: Any, tools: CallTools | None = None):
        super().__init__(page.shell, _("Note from a call"))
        self.setModal(False)  # transcribing takes minutes: the window stays usable meanwhile
        self.page = page
        self.shell = page.shell
        self.tools = tools or CallTools()
        self.child: Child | None = None
        self.run: QuietRun | None = None
        self.runs: QuietRuns | None = None
        self.tmp: str | None = None
        self.source = ""
        self.transcript = ""
        self.meeting = ""  # the meeting's name from the transcript's heading: the card's title
        self.created: Any = None  # the note made, once there is one
        self._waker = Waker(self._run_answered, self)
        # the screens one above the other, only the current one shown: the window takes the
        # height of the screen shown (a QStackedWidget asks for its tallest one)
        self.screens: list[QWidget] = []
        self._current = FILE
        self.stack = QWidget(self)
        self.stack.setMinimumWidth(theme.px(WIDTH))
        self._stack_col = QVBoxLayout(self.stack)
        self._stack_col.setContentsMargins(0, 0, 0, 0)
        self.box.addWidget(self.stack, 0, 0)
        self._build_file()
        self._build_progress()
        self._build_review()
        self.finished.connect(self._closed)
        self.show_screen(FILE)

    # ---- the screens

    def _screen(self) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget(self.stack)
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        self._stack_col.addWidget(w)
        self.screens.append(w)
        return w, col

    def _buttons(self, col: QVBoxLayout, *buttons: QWidget) -> None:
        row = QHBoxLayout()
        row.addStretch(1)
        for b in buttons:
            row.addWidget(b)
        col.addSpacing(8)
        col.addLayout(row)

    def _build_file(self) -> None:
        w, col = self._screen()
        col.addWidget(label(_("Note from a call"), "heading", w))
        hint = label(_("A transcript (.txt, .md, .vtt, .srt) is read as is. A recording (.wav, .mp3, .m4a, .ogg …) is first transcribed on this computer by whisper.cpp; nothing leaves it except the transcript sent to your assistant for the digest."), "muted", w)
        hint.setWordWrap(True)
        col.addWidget(hint)
        col.addSpacing(6)
        row = QHBoxLayout()
        self.path_edit = QLineEdit(w)
        self.path_edit.setPlaceholderText(_("The file of the call"))
        row.addWidget(self.path_edit, 1)
        pick = button(_("Choose…"), "small", w)
        pick.clicked.connect(self.pick_file)
        row.addWidget(pick)
        col.addLayout(row)
        self.file_error = label("", "error", w)
        self.file_error.setWordWrap(True)
        self.file_error.hide()
        col.addWidget(self.file_error)
        self.settings_btn = button(_("Open the settings"), "small", w)
        self.settings_btn.clicked.connect(self.open_settings)
        self.settings_btn.hide()
        col.addWidget(self.settings_btn, 0, Qt.AlignmentFlag.AlignLeft)
        col.addStretch(1)
        self.next_btn = button(_("Next"), "primary", w)
        self.next_btn.clicked.connect(self.next_from_file)
        cancel = button(_("Cancel"), "", w)
        cancel.clicked.connect(self.reject)
        self._buttons(col, self.next_btn, cancel)

    def _build_progress(self) -> None:
        w, col = self._screen()
        col.addWidget(label(_("Note from a call"), "heading", w))
        self.step_label = label("", "", w)
        self.step_label.setWordWrap(True)
        col.addWidget(self.step_label)
        self.bar = QProgressBar(w)
        self.bar.setRange(0, 0)  # busy: neither whisper nor the assistant says how far it is
        self.bar.setTextVisible(False)
        col.addWidget(self.bar)
        note = label(_("It runs in the background: the rest of the window stays usable."), "muted", w)
        note.setWordWrap(True)
        col.addWidget(note)
        col.addStretch(1)
        self.stop_btn = button(_("Stop"), "danger", w)
        self.stop_btn.clicked.connect(self.stop)
        self._buttons(col, self.stop_btn)

    def _build_review(self) -> None:
        w, col = self._screen()
        col.addWidget(label(_("Check the note"), "heading", w))
        self.review_note = label("", "muted", w)
        self.review_note.setWordWrap(True)
        self.review_note.hide()
        col.addWidget(self.review_note)
        self.review_error = label("", "error", w)
        self.review_error.setWordWrap(True)
        self.review_error.hide()
        col.addWidget(self.review_error)
        self.raw = QPlainTextEdit(w)
        self.raw.setReadOnly(True)
        self.raw.setFixedHeight(theme.px(90))
        self.raw.hide()
        col.addWidget(self.raw)
        col.addWidget(label(_("Title"), "section", w))
        self.title_edit = QLineEdit(w)
        col.addWidget(self.title_edit)
        col.addWidget(label(_("Summary"), "section", w))
        self.summary = QPlainTextEdit(w)
        self.summary.setFixedHeight(theme.px(96))
        col.addWidget(self.summary)
        col.addWidget(label(_("Notes"), "section", w))
        self.notes_list = QListWidget(w)
        self.notes_list.setFixedHeight(theme.px(110))
        col.addWidget(self.notes_list)
        col.addWidget(label(_("Tasks — the checked ones become items marked with an arrow"), "section", w))
        self.tasks_list = QListWidget(w)
        self.tasks_list.setFixedHeight(theme.px(110))
        col.addWidget(self.tasks_list)
        tip = label(_("Double-click an item to edit it. The transcript is attached to the card as a file only you can read."), "muted", w)
        tip.setWordWrap(True)
        col.addWidget(tip)
        self.create_btn = button(_("Create note"), "primary", w)
        self.create_btn.clicked.connect(self.create_note)
        back = button(_("Another file"), "", w)
        back.clicked.connect(lambda: self.show_screen(FILE))
        cancel = button(_("Cancel"), "", w)
        cancel.clicked.connect(self.reject)
        self._buttons(col, self.create_btn, back, cancel)

    def show_screen(self, n: int) -> None:
        for i, w in enumerate(self.screens):
            w.setVisible(i == n)
        self._current = n
        if self.isVisible():  # fit the new screen: a short one after the review shrinks the window back
            self.box.activate()
            self.resize(self.width(), self.sizeHint().height())
        self.keys({FILE: self.next_from_file, PROGRESS: None, REVIEW: self.create_note}[n])
        if n == FILE:
            self.path_edit.setFocus()

    @property
    def step(self) -> int:
        return self._current

    # ---- the file

    def _settings(self) -> Mapping:
        return self.shell.session.settings()

    def file_failed(self, text: str, settings: bool = False) -> None:
        self._end_work()
        self.file_error.setText(text)
        self.file_error.show()
        self.settings_btn.setVisible(settings)
        self.show_screen(FILE)

    def pick_file(self) -> None:
        name = _("Transcripts and recordings") + " (*.txt *.md *.markdown *.vtt *.srt *.wav *.mp3 *.m4a *.ogg *.oga *.opus *.webm *.flac *.aac *.mp4 *.wma)"
        path, _filter = QFileDialog.getOpenFileName(self, _("The file of the call"), "", name + ";;" + _("All files") + " (*.*)")
        if path and shiboken6.isValid(self):
            self.path_edit.setText(path)
            self.choose(path)

    def next_from_file(self) -> None:
        self.choose(self.path_edit.text().strip().strip('"'))

    def open_settings(self) -> None:
        self.shell.settings_dialog("general")

    def choose(self, path: str) -> None:
        """Go on with the file at `path`: read a transcript, or transcribe a recording."""
        self.file_error.hide()
        self.settings_btn.hide()
        self.path_edit.setText(path)
        if not path or not os.path.isfile(path):
            self.file_failed(_("File not found: {path}").format(path=path or "—"))
            return
        self.source = path
        if is_transcript(path):
            self._read_transcript(path)
        elif is_audio(path):
            self._transcribe(path)
        else:
            self.file_failed(_("Neither a transcript nor a recording: pick a .txt, .md, .vtt or .srt file, or a .wav, .mp3, .m4a or .ogg recording."))

    def _read_transcript(self, path: str) -> None:
        try:
            if os.path.getsize(path) > MAX_TRANSCRIPT:
                self.file_failed(_("The file is larger than {n} MB: that is not a transcript.").format(n=MAX_TRANSCRIPT // (1024 * 1024)))
                return
            text = decode_text(Path(path).read_bytes())  # UTF-8, UTF-16 or a Russian Windows code page
        except OSError as exc:
            self.file_failed(_("Could not read the file: {error}").format(error=exc.strerror or exc))
            return
        if Path(path).suffix.lower() in (".vtt", ".srt"):
            text = parse_transcript(text)  # subtitles: the cue numbers and times go
        self._have_transcript(text.strip())

    # ---- the recording

    def _transcribe(self, path: str) -> None:
        settings = self._settings()
        binary = whisper_binary(settings, self.tools.which, self.tools.exists, self.tools.environ)
        if binary is None:
            self.file_failed(_("whisper.cpp is not found, and a recording needs it to become text. Set the path to whisper-cli in the settings (General → Calls), put whisper-cli on PATH, or pick a transcript instead."), settings=True)
            return
        model = whisper_model(settings, binary, exists=self.tools.exists)
        if model is None:
            self.file_failed(_("whisper-cli is there, but no model for it (a ggml-*.bin file). Download one and set its path in the settings (General → Calls)."), settings=True)
            return
        try:
            with open(path, "rb") as f:
                head = f.read(64)
        except OSError as exc:
            self.file_failed(_("Could not read the file: {error}").format(error=exc.strerror or exc))
            return
        self.tmp = tempfile.mkdtemp(prefix="daykeeper-call-")
        wav = os.path.join(self.tmp, "call.wav")
        language = str(settings.get(SETTING_LANGUAGE) or "auto")
        try:
            threads = int(settings.get(SETTING_THREADS) or 0)
        except (TypeError, ValueError):
            threads = 0
        convert_first = needs_conversion(path, head)
        whisper = self.tools.transcribe(binary, model, wav if convert_first else path, language, threads)
        if convert_first:
            convert = self.tools.ffmpeg_command(path, wav)
            if convert is None:
                self.file_failed(_("This recording has to be converted to WAV (16 kHz, mono) first, and ffmpeg is not found. Install ffmpeg or save the recording as such a WAV."))
                return
            self._start_child(convert, _("Converting the recording…"), lambda code, out, err: self._converted(code, err, whisper))
        else:
            self._start_whisper(whisper)

    def _start_child(self, argv: list[str], step: str, done: Callable[[int, str, str], None]) -> None:
        try:
            self.child = Child(argv, done, self)
        except OSError as exc:
            self.file_failed(_("Could not start {program}: {error}").format(program=os.path.basename(argv[0]) if argv else "", error=exc.strerror or exc))
            return
        self.step_label.setText(step)
        self.show_screen(PROGRESS)

    def _converted(self, code: int, err: str, whisper: list[str]) -> None:
        self.child = None
        if code != 0:
            self.file_failed(_("ffmpeg could not convert the recording:") + "\n" + (err.strip()[-ERR_TAIL:] or _("exit code {code}").format(code=code)))
            return
        self._start_whisper(whisper)

    def _start_whisper(self, argv: list[str]) -> None:
        self._start_child(argv, _("Transcribing the recording with whisper.cpp… A long call takes minutes."), self._transcribed)

    def _transcribed(self, code: int, out: str, err: str) -> None:
        self.child = None
        if code != 0:
            self.file_failed(_("whisper-cli could not transcribe the recording:") + "\n" + (err.strip()[-ERR_TAIL:] or _("exit code {code}").format(code=code)))
            return
        self._remove_tmp()
        self._have_transcript(parse_transcript(out))

    # ---- the digest

    def _have_transcript(self, text: str) -> None:
        if not text:
            self.file_failed(_("There is no text in it: nothing to make a note of."))
            return
        self.transcript = text
        self.meeting = meeting_name(text)
        settings = self._settings()
        assignment = Assignment(kind="custom", title=_("Digest of a call"), text=digest_prompt(text), channel="quiet")
        argv = self.tools.assistant_command(assignment, settings)
        if argv is None:
            self.show_review(Digest(), note=_("No AI assistant is installed (Claude Code writes the digest), so the card gets the title and the transcript only, without a summary, notes or tasks."))
            return
        self.runs = self.tools.runs(notify=lambda _run: self._waker(), timeout=DIGEST_PATIENCE)
        self.run = self.runs.start(assignment, argv)
        self.step_label.setText(_("The assistant is writing the digest…"))
        self.show_screen(PROGRESS)
        if not self.run.running:  # could not even start: its notify has already come
            self._run_answered()

    def _run_answered(self) -> None:
        run = self.run
        if run is None or run.running or not shiboken6.isValid(self):
            return
        self.run = None
        if run.stopped:
            return
        if run.error:
            self.show_review(Digest(), error=_("The assistant gave no digest; the card can still be made from the transcript."), raw=run.error)
            return
        digest = parse_digest(run.text)
        if digest.is_empty():
            self.show_review(Digest(), error=_("The assistant's answer could not be read as a digest. It is shown below as it came; the card can still be made from the transcript."), raw=run.text)
            return
        self.show_review(digest)

    # ---- the review

    def show_review(self, digest: Digest, note: str = "", error: str = "", raw: str = "") -> None:
        self._end_work()
        self.review_note.setText(note)
        self.review_note.setVisible(bool(note))
        self.review_error.setText(error)
        self.review_error.setVisible(bool(error))
        self.raw.setPlainText(raw)
        self.raw.setVisible(bool(raw))
        self.title_edit.setText(note_from_digest(digest, today(), self.meeting).title)
        self.summary.setPlainText(digest.summary)
        self.notes_list.clear()
        for text in digest.notes:
            _check_item(text, self.notes_list)
        self.tasks_list.clear()
        for task in digest.tasks:
            extra = ", ".join(x for x in (task.owner, task.due) if x)
            _check_item(task.text + (f" ({extra})" if extra else ""), self.tasks_list)
        self.show_screen(REVIEW)

    def digest(self) -> Digest:
        """What the review holds now: only the checked items, as edited."""
        tasks = [Task(t[len(TASK_MARK):] if t.startswith(TASK_MARK) else t) for t in _checked(self.tasks_list)]
        return Digest(self.summary.toPlainText().strip(), _checked(self.notes_list), tasks)

    def create_note(self) -> None:
        if self.step != REVIEW or not self.transcript:
            return
        page = self.page
        if not shiboken6.isValid(page) or page.vault is None:
            self.reject()
            return
        draft = note_from_digest(self.digest(), today(), self.meeting)
        title = " ".join(self.title_edit.text().split()) or draft.title
        try:
            path = transcript_file(Path(self.shell.vault_path).parent, new_call_id(), self.transcript)
        except OSError as exc:
            self.review_error.setText(_("Could not save the transcript: {error}").format(error=exc.strerror or exc))
            self.review_error.show()
            return
        note = page.vault.notes.add(draft.date, title=title, text=draft.text)
        for item in draft.items:
            note.add_item(item)
        note.attach(path)
        self.shell.mark_dirty("notes")
        page.refresh_days(select=draft.date)
        self.shell.set_status(_("Note made from the call: {title}").format(title=title))
        self.created = note
        self.accept()

    # ---- stopping and cleaning up

    def stop(self) -> None:
        """Stop what runs (the child is killed) and go back to the file screen."""
        self._end_work()
        self.file_error.setText(_("Stopped"))
        self.file_error.show()
        self.show_screen(FILE)

    def _end_work(self) -> None:
        if self.child is not None:
            self.child.stop()
            self.child = None
        if self.run is not None:
            run, self.run = self.run, None
            run.stop()
        self._remove_tmp()

    def _remove_tmp(self) -> None:
        if self.tmp is not None:
            shutil.rmtree(self.tmp, ignore_errors=True)
            self.tmp = None

    def _closed(self, _result: int = 0) -> None:
        self._end_work()
        self._waker.close()

    def on_lock(self) -> None:
        self._end_work()


class CallWatch:
    """Made once with the window. While the vault is open and the setting is on, the
    watched folder is looked at on its change and every WATCH_EVERY_MS; every transcript
    that came since the last look gets a toast, a click on it opens the wizard with the
    file. A preview window looks at nothing (`active`)."""

    def __init__(self, window: Any):
        self.window = window
        self.active = bool(getattr(window, "background_jobs", True))
        self.now: Callable[[], float] = time.time  # tests move the clock
        self.offered: list[Path] = []  # every file a toast was shown for (the tests read it)
        self.toasts: list[Any] = []
        self.wizard: CallWizard | None = None
        self.tools: CallTools | None = None  # the wizard's programs; tests pass stand-ins
        self._folder = ""
        self._later = Debounce(window, WATCH_SETTLE_MS, self.look)
        self._ticker = Ticker(window, WATCH_EVERY_MS, self.look)
        self._fs = QFileSystemWatcher(window)
        self._fs.directoryChanged.connect(lambda _path: self._later.touch())

    def enabled(self) -> bool:
        return self.active and bool(self.window.session.settings().get(SETTING_WATCH, True))

    def folder(self) -> Path:
        return watch_folder(self.window.session.settings())

    # ---- when

    def on_unlock(self) -> None:
        self.restart()

    def on_lock(self) -> None:
        self._later.cancel()
        self._ticker.stop()
        self._watch("")
        self.toasts = []  # they close on lock by themselves (the window's register_child)

    def restart(self) -> None:
        """The vault was opened or the setting changed: watch what the settings say now."""
        self._later.cancel()
        if self.window.vault is None or not self.enabled():
            self._ticker.stop()
            self._watch("")
            return
        self._watch(str(self.folder()))
        self._ticker.start()
        self._later.touch()

    def _watch(self, folder: str) -> None:
        if self._folder == folder:
            return
        if self._folder:
            self._fs.removePath(self._folder)
        self._folder = folder if folder and os.path.isdir(folder) else ""
        if self._folder:
            self._fs.addPath(self._folder)

    # ---- looking

    def look(self) -> list[Path]:
        """One look at the folder; the transcripts found are offered and returned."""
        if self.window.vault is None or not self.enabled():
            return []
        folder = self.folder()
        self._watch(str(folder))  # made after the vault was opened: watched from now on
        settings = self.window.session.settings()
        raw = settings.get(SETTING_WATCH_SINCE)
        since = float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else None
        seen = new_transcripts(folder, since, self.now())
        if seen.since != since:
            self.window.session.write_settings({SETTING_WATCH_SINCE: seen.since})
        for path in seen.found[-OFFERS_AT_ONCE:]:
            self.offer(path)
        return seen.found

    def offer(self, path: Path) -> None:
        from ..widgets.toast import Toast

        self.offered.append(path)
        text = _("{name} — click to make a note of the call.").format(name=path.name)
        self.toasts.append(Toast(self.window, "🎙  " + _("A call's transcript"), text, action=partial(self.open, path), ms=OFFER_MS))

    def open(self, path: Path) -> CallWizard | None:
        """The wizard on the Notes tab, already reading `path`."""
        if self.window.vault is None:
            return None
        self.window.show_page("notes")
        self.wizard = self.window.page("notes").from_call(self.tools)
        if self.wizard is not None:
            self.wizard.choose(str(path))
        return self.wizard
