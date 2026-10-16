"""Screenshots of the app on a sample vault: the unlock screens, every tab, every settings
section. Made for the macOS runner of GitHub (`screenshots.yml`), where the screen is
taken by `screencapture`; nothing of the user's is read or written — the data, the
settings and the Claude folder live in a temporary folder.

    python tools/screenshots.py <out folder> [light|dark] [en|ru]
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = Path(sys.argv[1]).resolve()
THEME = sys.argv[2] if len(sys.argv) > 2 else "light"
LANG = sys.argv[3] if len(sys.argv) > 3 else "en"
PASSWORD = "sample-master-1"
TMP = Path(tempfile.mkdtemp(prefix="daykeeper-shots-"))
os.environ["CLAUDE_CONFIG_DIR"] = str(TMP / "claude")
for key in ("HOME", "APPDATA", "LOCALAPPDATA"):
    os.environ[key] = str(TMP / key.lower())

from daykeeper import palette  # noqa: E402
from daykeeper.journal import today  # noqa: E402
from daykeeper.notes import NoteItem  # noqa: E402
from daykeeper.settings import write_settings  # noqa: E402
from daykeeper.vault import Vault  # noqa: E402

palette.apply(THEME)
from daykeeper.app import App  # noqa: E402
from daykeeper.settings_view import sections  # noqa: E402

OUT.mkdir(parents=True, exist_ok=True)
VAULT = TMP / "sample.pkv"
write_settings(TMP / "settings.json", {"language": LANG, "theme": THEME, "geometry": "1200x760+40+60"})


def pump(win, seconds: float = 1.2) -> None:
    end = time.time() + seconds
    while time.time() < end:
        win.update()
        time.sleep(0.03)


def shot(win, name: str) -> None:
    """The window's rectangle off the screen; Tk and screencapture both count in points."""
    win.lift()
    pump(win, 0.6)
    x, y, w, h = win.winfo_rootx(), win.winfo_rooty(), win.winfo_width(), win.winfo_height()
    path = OUT / f"{THEME}-{name}.png"
    if sys.platform == "darwin":
        subprocess.run(["screencapture", "-x", "-R", f"{x},{y},{w},{h}", str(path)], check=True)
    print(f"{path.name}: {w}x{h} at {x},{y}", flush=True)


def sample_vault() -> None:
    v = Vault.create(VAULT, PASSWORD)
    for name, login, url in (("GitHub", "alice", "https://github.com"), ("Mail", "alice@example.com", "https://mail.example.com"),
                             ("Tracker", "alice", "https://tracker.example.com")):
        e = v.add_entry(name)
        e.fields[0].value = login
        e.fields[1].value = "correct-horse-battery"
        v.bookmarks.add(name, url, entry_id=e.id)
    day = v.journal.get(today(), create=True)
    assert day is not None
    day.add("ok", "APP-101")
    day.add("failed", "APP-102")
    day.add_activity("review", "!42")
    v.notes.add(title="Release 1.1", items=[NoteItem("Check the build on macOS", True),
                                           NoteItem("A long checklist item that wraps onto the next line instead of running past the edge of the card")])
    v.routines.add(name="Backup", kind="script", target="/usr/local/bin/backup.sh")
    v.save()


def main() -> None:
    app = App(VAULT, preview=True)  # no vault yet: the "new vault" screen
    pump(app)
    shot(app, "00-new-vault")
    app.destroy()

    sample_vault()
    app = App(VAULT, preview=True)
    pump(app)
    shot(app, "01-unlock")
    app.unlock(PASSWORD)
    pump(app, 2.0)
    for i, mode in enumerate(app.tab_buttons, start=2):
        app.show_page(mode)
        pump(app, 1.5)
        shot(app, f"{i:02d}-tab-{mode}")
    for i, (section, _text) in enumerate(sections(), start=20):
        app.settings_dialog(section)
        pump(app, 1.5)
        win = app._settings_win
        assert win is not None
        shot(win, f"{i:02d}-settings-{section}")
    app.close_settings()
    app.destroy()


if __name__ == "__main__":
    main()
