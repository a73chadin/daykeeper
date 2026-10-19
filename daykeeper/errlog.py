"""errors.log next to the vault: what went wrong in a click, a timer, a thread or Qt itself.
A built app has no console, so without it an error is just "nothing happened". Each entry
is the exception's type, message and traceback (code lines only: no field values, no
frame locals) under a header with the time, where it happened and the versions. Token-shaped
strings and the query of a web address are hidden before anything is written.
Owner-only, kept under LIMIT by one older file. No tkinter, no Qt.

crash.log beside it catches what Python cannot report itself: a segfault or an abort ends
the process before any handler runs, and faulthandler writes the stacks of the threads
there. It holds this run only (a header, then whatever the fault left); a start that finds
more than the header in it keeps that as crash.log.1 — the last crash — and says so once.

`build_report` makes the text a user sends: the versions, the last entries and the last
crash, scrubbed by `scrub` of the home folder, the user name, e-mails, tokens and every
web address but its host."""

from __future__ import annotations

import faulthandler
import os
import platform
import re
import sys
import threading
import time
import traceback
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from . import __version__
from .settings import private

NAME = "errors.log"
LIMIT = 256 * 1024  # bytes; then errors.log becomes errors.log.1 and a new one starts
CRASH_NAME = "crash.log"
CRASH_KEPT = CRASH_NAME + ".1"  # the last run that ended in a crash
CRASH_LIMIT = 64 * 1024  # bytes of a crash kept; a dump of every thread is a few KB
CLEAN_EXIT = "---- clean exit"  # the last line of crash.log of a run that ended normally
ENTRY = "---- "  # every entry of errors.log starts a line with it
SEP = " | "
REPORT_ENTRIES = 20

_lock = threading.Lock()
_versions: dict[str, str] = {}  # "qt" is set by the Qt window (this module does not import Qt)
_crash_file: IO[str] | None = None


def log_path_for(vault_path: Path) -> Path:
    return Path(vault_path).parent / NAME


def set_qt_version(version: str) -> None:
    _versions["qt"] = version


def os_name() -> str:
    """"Windows 11 (10.0.26200)", "macOS 15.1", "Linux 6.8.0"; asked once."""
    if "os" not in _versions:
        if sys.platform == "darwin":
            name = f"macOS {platform.mac_ver()[0]}"
        elif sys.platform == "win32":  # not platform.version(): a WMI query, 60 ms at the start
            v = sys.getwindowsversion()
            name = f"Windows {11 if v.major == 10 and v.build >= 22000 else v.major} ({v.major}.{v.minor}.{v.build})"
        else:
            name = f"{platform.system()} {platform.release()}"
        _versions["os"] = name
    return _versions["os"]


def versions() -> list[str]:
    """The app, Python, Qt (when known) and the system, as the header of an entry names them."""
    out = [f"Daykeeper {__version__}", f"Python {platform.python_version()}"]
    if _versions.get("qt"):
        out.append(f"Qt {_versions['qt']}")
    return out + [os_name()]


def _header(where: str) -> str:
    return ENTRY + SEP.join([time.strftime("%Y-%m-%d %H:%M:%S"), where or "app", *versions()]) + "\n"


def _append(path: Path, text: str) -> bool:
    try:
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > LIMIT:
                path.replace(path.with_name(path.name + ".1"))
            new = not path.exists()
            with path.open("a", encoding="utf-8") as f:
                f.write(text)
            if new:
                private(path)
    except OSError:
        return False
    return True


def write_error(path: Path, exc_type, exc, tb, where: str = "") -> bool:
    """Append one error with its traceback; False when the file could not be written."""
    body = "".join(traceback.format_exception(exc_type, exc, tb))
    return _append(path, _header(where) + hide_secrets(body))


def write_message(path: Path, where: str, text: str) -> bool:
    """Append a message without a traceback (a Qt warning)."""
    return _append(path, _header(where) + hide_secrets(text.rstrip()) + "\n")


# ---- hooks of the process


def install_hooks(path: Path) -> None:
    """sys.excepthook and threading.excepthook write to `path`. The window puts its own
    report over sys.excepthook later (qt.app.install_excepthook) and falls back to this one."""
    console = sys.__excepthook__

    def main_hook(exc_type, exc, tb) -> None:
        if not issubclass(exc_type, KeyboardInterrupt):
            write_error(path, exc_type, exc, tb, "main")
        if sys.stderr is not None:
            console(exc_type, exc, tb)

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread is not None else "?"
        write_error(path, args.exc_type, args.exc_value, args.exc_traceback, f"thread {name}")

    sys.excepthook = main_hook
    threading.excepthook = thread_hook


class RateLimit:
    """At most `n` per `per` seconds; `allow()` answers whether this one goes through and
    `dropped()` how many did not since the last that did."""

    def __init__(self, n: int, per: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.n, self.per, self.clock = n, per, clock
        self.times: deque[float] = deque()
        self._dropped = 0

    def allow(self) -> bool:
        now = self.clock()
        while self.times and now - self.times[0] >= self.per:
            self.times.popleft()
        if len(self.times) >= self.n:
            self._dropped += 1
            return False
        self.times.append(now)
        return True

    def dropped(self) -> int:
        n, self._dropped = self._dropped, 0
        return n


# ---- crash.log


def crash_header() -> str:
    return f"Daykeeper crash log{SEP}started {time.strftime('%Y-%m-%d %H:%M:%S')}{SEP}" + SEP.join(versions()) + "\n"


def crash_body(text: str) -> str:
    """What a run left in crash.log under its header; "" for a run that ended normally."""
    lines = text.splitlines()
    if lines and lines[0].startswith("Daykeeper crash log"):
        lines = lines[1:]
    if lines and lines[-1].strip() == CLEAN_EXIT:
        return ""
    return "\n".join(lines).strip()


def start_crash_log(folder: Path) -> bool:
    """Point faulthandler at folder/crash.log for this run. True when the previous run
    ended in a crash: what it left is kept as crash.log.1, so the next start answers False."""
    global _crash_file
    folder = Path(folder)
    path = folder / CRASH_NAME
    fresh = False
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if path.exists():
            text = _read_capped(path)
            body = crash_body(text)
            if body:
                kept = folder / CRASH_KEPT
                first = text.splitlines()[0]
                head = first + "\n" if first.startswith("Daykeeper crash log") else ""
                kept.write_text(head + body[-CRASH_LIMIT:] + "\n", encoding="utf-8")
                private(kept)
                fresh = True
        stop_crash_log()
        path.write_text(crash_header(), encoding="utf-8")
        private(path)
        _crash_file = path.open("a", encoding="utf-8")
        faulthandler.enable(_crash_file, all_threads=True)
    except OSError:
        return fresh
    return fresh


def _read_capped(path: Path) -> str:
    """The file, or its first line and its last CRASH_LIMIT bytes when it grew past that."""
    raw = path.read_bytes()
    if len(raw) > CRASH_LIMIT * 2:
        raw = raw[: raw.find(b"\n") + 1] + raw[-CRASH_LIMIT:]
    return raw.decode("utf-8", errors="replace")


def end_crash_log() -> None:
    """The run ends normally: crash.log says so, and the next start finds no crash."""
    if _crash_file is not None:
        try:
            _crash_file.write(CLEAN_EXIT + "\n")
            _crash_file.flush()
        except (OSError, ValueError):
            pass
    stop_crash_log()


def stop_crash_log() -> None:
    global _crash_file
    if _crash_file is not None:
        if faulthandler.is_enabled():
            faulthandler.disable()
        try:
            _crash_file.close()
        except OSError:
            pass
        _crash_file = None


def crash_file() -> IO[str] | None:
    return _crash_file


def last_crash(folder: Path) -> str:
    """The last crash kept, header included; "" when there is none."""
    try:
        return (Path(folder) / CRASH_KEPT).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def clear(folder: Path) -> None:
    """Delete errors.log, its older file and the last crash; the crash.log of this run stays
    (faulthandler holds it open)."""
    folder = Path(folder)
    with _lock:
        for name in (NAME, NAME + ".1", CRASH_KEPT):
            try:
                (folder / name).unlink(missing_ok=True)
            except OSError:
                pass


# ---- reading


@dataclass
class Entry:
    when: str
    where: str
    title: str  # the error: the last line of the traceback, or the message
    body: str  # the traceback or the message
    header: str  # the versions as written


def parse(text: str) -> list[Entry]:
    """The entries of an errors.log, oldest first. An entry of an older build has the time
    only in its header."""
    out: list[Entry] = []
    head: str | None = None
    lines: list[str] = []

    def close() -> None:
        if head is None:
            return
        parts = head[len(ENTRY):].split(SEP)
        body = "\n".join(lines).rstrip()
        last = next((ln.strip() for ln in reversed(body.splitlines()) if ln.strip()), "")
        out.append(Entry(parts[0].strip(), parts[1].strip() if len(parts) > 1 else "", last, body, SEP.join(parts[2:])))

    for line in text.splitlines():
        if line.startswith(ENTRY) and re.match(r"---- \d{4}-\d\d-\d\d", line):
            close()
            head, lines = line, []
        elif head is not None:
            lines.append(line)
    close()
    return out


def read_entries(path: Path) -> list[Entry]:
    """errors.log.1 and errors.log, newest first."""
    path = Path(path)
    text = ""
    for p in (path.with_name(path.name + ".1"), path):
        try:
            text += p.read_text(encoding="utf-8", errors="replace") + "\n"
        except OSError:
            pass
    return list(reversed(parse(text)))


# ---- scrubbing and the report

HIDDEN = "<hidden>"
_URL = re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.\-]*)://(?:[^\s/?#@'\"<>]*@)?([^\s/?#:'\"<>()\[\]]+)(?::\d+)?[^\s'\"<>()\[\]]*")
_EMAIL = re.compile(r"\b[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+\b")
_BEARER = re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=\-]{8,}")
_KEYED = re.compile(r"(?i)\b(authorization|private[-_]?token|access[-_]?token|api[-_]?key|password|passwd|secret|token)(\s*[:=]\s*)(?!(?:bearer|basic)\s)(\"[^\"]*\"|'[^']*'|[^\s,;&]+)")
_PREFIXED = re.compile(r"\b(?:glpat-|gl(?:dt|ptt|rt|cbt|imt|oas|soat|ft)-|ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|sk-|xox[abprsoe]-|AKIA)[A-Za-z0-9_\-]{6,}")
_HEX = re.compile(r"\b[0-9a-fA-F]{32,}\b")
_B64 = re.compile(r"(?<![A-Za-z0-9+/_\-])[A-Za-z0-9+/_\-]{32,}={0,2}(?![A-Za-z0-9+/_\-])")
_USERINFO = re.compile(r"(\b[a-zA-Z][a-zA-Z0-9+.\-]*://)[^\s/?#@'\"<>]*@")
_QUERY = re.compile(r"(\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s?#'\"<>]*)[?#][^\s'\"<>()\[\]]*")


def _looks_random(run: str) -> bool:
    """A base64 or token run, not a path or a long name: digits and letters mixed, and no
    piece between slashes long and plain enough to be a folder name."""
    if not (any(c.isdigit() for c in run) and any(c.isalpha() for c in run)):
        return False
    if "/" in run:
        pieces = [p for p in run.split("/") if p]
        return any(len(p) >= 32 for p in pieces) or run.endswith("=")
    return True


def _tokens(text: str) -> str:
    text = _PREFIXED.sub(HIDDEN, text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} {HIDDEN}", text)
    text = _KEYED.sub(lambda m: m.group(1) + m.group(2) + HIDDEN, text)
    text = _HEX.sub(HIDDEN, text)
    return _B64.sub(lambda m: HIDDEN if _looks_random(m.group(0)) else m.group(0), text)


def hide_secrets(text: str) -> str:
    """What is hidden before an entry is written: token-shaped strings, the user part and
    the query of a web address. Paths and hosts stay (the owner reads the log himself)."""
    text = _USERINFO.sub(r"\1", text)
    text = _QUERY.sub(r"\1", text)
    return _tokens(text)


def scrub(text: str, home: str | None = None, user: str | None = None) -> str:
    """The report as it may leave the computer: the home folder → ~, the user name →
    <user>, e-mails and tokens → <hidden>, a web address → its scheme and host."""
    home = os.path.expanduser("~") if home is None else home
    user = _user_name() if user is None else user
    if home and len(home) > 3:
        forms = {home, home.replace("\\", "/"), home.replace("/", "\\")}
        for form in sorted(forms, key=len, reverse=True):
            text = re.sub(re.escape(form), "~", text, flags=re.IGNORECASE)
    text = _URL.sub(lambda m: f"{m.group(1)}://{m.group(2)}", text)
    text = _EMAIL.sub(HIDDEN, text)
    text = _tokens(text)
    # someone else's home on the same machine, or a path written before the name changed
    text = re.sub(r"(?i)(\b[a-z]:[\\/]+(?:users|documents and settings)[\\/]+|(?<![\w.~])/users/|(?<![\w.~])/home/)([^\\/\s'\"<>]+)", lambda m: m.group(1) + "<user>", text)
    if user and len(user) >= 3:
        text = re.sub(rf"(?i)(?<![\w<]){re.escape(user)}(?![\w>])", "<user>", text)
    return text


def _user_name() -> str:
    for name in ("USERNAME", "USER", "LOGNAME"):
        if os.environ.get(name):
            return os.environ[name]
    return Path(os.path.expanduser("~")).name


def environment(scale: str = "", language: str = "") -> dict[str, str]:
    """The header fields of the report; the screen scale and the language come from the window."""
    return {
        "App": f"Daykeeper {__version__}",
        "OS": os_name(),
        "Python": platform.python_version(),
        "Qt": _versions.get("qt", "") or "—",
        "Screen scale": scale or "—",
        "Language": language or "—",
    }


def build_report(folder: Path, env: dict[str, str], n: int = REPORT_ENTRIES, home: str | None = None, user: str | None = None) -> str:
    """The error report of the folder's logs: `env` (environment()), the last `n` entries
    newest first and the last crash, scrubbed (see `scrub`)."""
    entries = read_entries(Path(folder) / NAME)
    lines = ["# Daykeeper error report", ""]
    lines += [f"- {k}: {v}" for k, v in env.items()]
    lines += [f"- Made: {time.strftime('%Y-%m-%d %H:%M')}", ""]
    shown = entries[:n]
    lines.append(f"## Errors ({len(shown)} of {len(entries)}, newest first)")
    lines.append("")
    if not shown:
        lines += ["None.", ""]
    for e in shown:
        lines.append(f"### {e.when} · {e.where or 'app'}" + (f" · {e.header}" if e.header else ""))
        lines += ["", "```", e.body, "```", ""]
    crash = last_crash(folder)
    lines.append("## Last crash")
    lines.append("")
    lines += ["```", crash, "```"] if crash else ["None."]
    return scrub("\n".join(lines).rstrip() + "\n", home, user)

