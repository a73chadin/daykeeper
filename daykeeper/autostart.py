"""Start with the system: an entry that opens the app when the user signs in.

Windows — a value in HKCU\\...\\CurrentVersion\\Run (per user, no admin rights);
macOS — a LaunchAgent plist in ~/Library/LaunchAgents; elsewhere — a .desktop file in
~/.config/autostart. One small store per system with get / put / remove, so the logic is
unit-tested on a dictionary and on temporary folders. No tkinter here."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from xml.sax.saxutils import escape

NAME = "Daykeeper"
MINIMIZED = "--minimized"  # the app starts as an icon in the taskbar: the unlock screen waits there
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AGENT_ID = "ru.achadin.daykeeper"


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


def plist(args: list[str]) -> str:
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


def desktop_entry(args: list[str]) -> str:
    return f"[Desktop Entry]\nType=Application\nName={NAME}\nExec={command_line(args)}\nX-GNOME-Autostart-enabled=true\n"


def default_store(platform: str = sys.platform, home: Path | None = None):
    home = home or Path.home()
    if platform == "win32":
        return WindowsRun()
    if platform == "darwin":
        return FileEntry(home / "Library" / "LaunchAgents" / f"{AGENT_ID}.plist", plist)
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


def refresh(args: list[str], store=None) -> bool:
    """At start: an entry that points at another copy (the app was moved or updated in a new
    place) is rewritten to this one. True — it was rewritten. An entry that starts this very
    program is left as it is, even when its arguments differ: the user may have edited them."""
    store = store or default_store()
    current = store.get()
    if current is None or current == _render(store, args):
        return False
    program = args[0].lower() if os.name == "nt" else args[0]
    if program in (current.lower() if os.name == "nt" else current):
        return False
    store.put(args)
    return True
