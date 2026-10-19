"""Start with the system: an entry that opens the app when the user signs in.

Windows — a value in HKCU\\...\\CurrentVersion\\Run (per user, no admin rights);
macOS — a LaunchAgent plist in ~/Library/LaunchAgents; elsewhere — a .desktop file in
~/.config/autostart. One small store per system with get / put / remove, so the logic is
unit-tested on a dictionary and on temporary folders. No tkinter here."""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

NAME = "Daykeeper"
MINIMIZED = "--minimized"  # the app starts as an icon in the taskbar: the unlock screen waits there
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AGENT_ID = "local.daykeeper"


def launch_args(minimized: bool, vault: Path | None = None, default_vault: Path | None = None) -> list[str]:
    """What starts this copy of the app: the exe of a built app, pythonw + main.py from the
    source tree; the vault path when it is not the default one."""
    if getattr(sys, "frozen", False):
        args = [sys.executable]
    else:
        python = sys.executable
        if os.name == "nt":
            python = python.replace("python.exe", "pythonw.exe")  # no console window at sign-in
        args = [python, str(Path(__file__).resolve().parents[1] / "main.py")]
    if vault is not None and default_vault is not None and Path(vault).resolve() != Path(default_vault).resolve():
        args.append(str(vault))
    if minimized:
        args.append(MINIMIZED)
    return args


def command_line(args: list[str]) -> str:
    return " ".join(f'"{a}"' if (" " in a or not a) else a for a in args)


# ---- stores


class WindowsRun:
    """The per-user Run key."""

    def __init__(self, key: str = RUN_KEY, name: str = NAME):
        import winreg

        self.winreg, self.key, self.name = winreg, key, name

    def get(self) -> str | None:
        try:
            with self.winreg.OpenKey(self.winreg.HKEY_CURRENT_USER, self.key) as k:
                value, _kind = self.winreg.QueryValueEx(k, self.name)
                return str(value)
        except OSError:
            return None

    def put(self, args: list[str]) -> None:
        with self.winreg.CreateKey(self.winreg.HKEY_CURRENT_USER, self.key) as k:
            self.winreg.SetValueEx(k, self.name, 0, self.winreg.REG_SZ, command_line(args))

    def remove(self) -> None:
        try:
            with self.winreg.OpenKey(self.winreg.HKEY_CURRENT_USER, self.key, 0, self.winreg.KEY_SET_VALUE) as k:
                self.winreg.DeleteValue(k, self.name)
        except FileNotFoundError:
            pass


class FileEntry:
    """A file that the system reads at sign-in: a LaunchAgent plist or a .desktop entry."""

    def __init__(self, path: Path, render):
        self.path, self.render = path, render

    def get(self) -> str | None:
        try:
            return self.path.read_text(encoding="utf-8")
        except OSError:
            return None

    def put(self, args: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.render(args), encoding="utf-8")

    def remove(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


class LaunchAgent(FileEntry):
    """The LaunchAgent plist, which also finds one written under an earlier label (a file
    `<something>.daykeeper.plist` beside it): it is read as the entry until the entry is
    written again, and then it goes, so the app never starts twice at sign-in."""

    def _older(self) -> list[Path]:
        try:
            found = sorted(self.path.parent.glob("*.daykeeper.plist"))
        except OSError:
            return []
        return [p for p in found if p.name != self.path.name]

    def get(self) -> str | None:
        current = super().get()
        if current is not None:
            return current
        for older in self._older():
            try:
                return older.read_text(encoding="utf-8")
            except OSError:
                continue
        return None

    def _drop_older(self) -> None:
        for older in self._older():
            try:
                older.unlink()
            except FileNotFoundError:
                pass

    def put(self, args: list[str]) -> None:
        super().put(args)
        self._drop_older()

    def remove(self) -> None:
        super().remove()
        self._drop_older()


def plist(args: list[str]) -> str:
    from xml.sax.saxutils import escape  # here, not at the top: it cost 70 ms of every start for this one line

    items = "".join(f"\n        <string>{escape(a)}</string>" for a in args)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{AGENT_ID}</string>
    <key>ProgramArguments</key>
    <array>{items}
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
"""


# the characters the Desktop Entry spec reserves in an Exec argument: with one of them the
# argument goes in double quotes
EXEC_RESERVED = frozenset(" \t\n\"'\\><~|&;$*?#()`")


def exec_arg(arg: str) -> str:
    """One argument of Exec= as the Desktop Entry spec wants it: % doubled (a field code
    otherwise); in double quotes when it holds a reserved character, with ", `, $ and \\
    escaped there by a backslash; then every backslash doubled again, since the file's own
    string escapes are read before the quoting."""
    arg = arg.replace("%", "%%")
    if not arg or any(c in EXEC_RESERVED for c in arg):
        arg = '"' + re.sub(r'(["`$\\])', r"\\\1", arg) + '"'
    return arg.replace("\\", "\\\\")


def desktop_entry(args: list[str]) -> str:
    exec_line = " ".join(exec_arg(a) for a in args)
    return f"[Desktop Entry]\nType=Application\nName={NAME}\nExec={exec_line}\nX-GNOME-Autostart-enabled=true\n"


def default_store(platform: str = sys.platform, home: Path | None = None):
    home = home or Path.home()
    if platform == "win32":
        return WindowsRun()
    if platform == "darwin":
        return LaunchAgent(home / "Library" / "LaunchAgents" / f"{AGENT_ID}.plist", plist)
    config = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    return FileEntry(config / "autostart" / "daykeeper.desktop", desktop_entry)


# ---- what the settings ask


def _render(store, args: list[str]) -> str:
    return store.render(args) if isinstance(store, FileEntry) else command_line(args)


def is_on(store=None) -> bool:
    return (store or default_store()).get() is not None


def is_minimized(store=None) -> bool:
    current = (store or default_store()).get()
    return bool(current) and MINIMIZED in current


def turn_on(args: list[str], store=None) -> None:
    (store or default_store()).put(args)


def turn_off(store=None) -> None:
    (store or default_store()).remove()


def _first_arg(line: str, backslash_escapes: bool) -> str:
    """The first argument of a command line: up to the closing quote when it is quoted (a
    backslash escapes the next character in a .desktop Exec=), else up to the first space."""
    line = line.strip()
    if not line.startswith('"'):
        return line.split(None, 1)[0] if line else ""
    out, i = [], 1
    while i < len(line) and line[i] != '"':
        if backslash_escapes and line[i] == "\\" and i + 1 < len(line):
            i += 1
        out.append(line[i])
        i += 1
    return "".join(out)


def entry_program(text: str) -> str | None:
    """The program an entry starts: the first of ProgramArguments of a LaunchAgent, the first
    argument of Exec= of a .desktop file, the first one of a Run value. None — not readable."""
    if "<plist" in text:
        from xml.sax.saxutils import unescape

        m = re.search(r"<key>ProgramArguments</key>\s*<array>\s*<string>(.*?)</string>", text, re.S)
        return unescape(m.group(1)) if m else None
    if text.startswith("[Desktop Entry]"):
        m = re.search(r"^Exec=(.*)$", text, re.M)
        if not m:
            return None
        value = re.sub(r"\\(.)", r"\1", m.group(1))  # the file's string escapes come first
        return _first_arg(value, backslash_escapes=True).replace("%%", "%") or None
    return _first_arg(text, backslash_escapes=False) or None


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def is_daykeeper_build(program: str) -> bool:
    """The entry's program is still there and is a build of Daykeeper (Daykeeper.exe, the
    binary inside Daykeeper.app): a copy the user kept on purpose, not a moved one."""
    path = Path(program)
    return path.name.lower().startswith(NAME.lower()) and path.is_file()


def _under(path: Path, folder: Path) -> bool:
    """`path` lies inside `folder`, as written or with links resolved (macOS keeps the
    temporary folder behind /var -> /private/var); case-blind on Windows."""
    for p in {path, path.resolve()}:
        for f in {folder, folder.resolve()}:
            if Path(os.path.normcase(str(p))).is_relative_to(os.path.normcase(str(f))):
                return True
    return False


def is_throwaway(program: str, home: Path | None = None) -> bool:
    """A copy started from the temporary folder or from Downloads: a try-out of an unpacked
    archive, which must not take the start with the system from the copy the user keeps."""
    path = Path(os.path.abspath(program))
    return _under(path, Path(tempfile.gettempdir())) or _under(path, (home or Path.home()) / "Downloads")


def refresh(args: list[str], store=None) -> bool:
    """At start: an entry that points at a copy of Daykeeper that is no longer there (the app
    was moved, or updated in a new place) is rewritten to this one. True — it was rewritten.
    Left as they are: an entry that starts this very program, even when its arguments differ
    (the user may have edited them), and one whose program is still on disk and is a build
    of Daykeeper (a second copy the user keeps)."""
    store = store or default_store()
    current = store.get()
    if current is None or current == _render(store, args):
        return False
    program = entry_program(current)
    if program is None:
        mine = args[0].lower() if os.name == "nt" else args[0]
        if mine in (current.lower() if os.name == "nt" else current):
            return False
    elif _same_path(program, args[0]) or is_daykeeper_build(program):
        return False
    store.put(args)
    return True
