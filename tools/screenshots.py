"""Screenshots of the app on a sample vault: the unlock screens, every tab, every settings
section. Made for the macOS runner of GitHub (`screenshots.yml`) and usable anywhere: each
window is drawn into a picture by Qt itself (QWidget.grab), so nothing is taken off the
screen, and with QT_QPA_PLATFORM=offscreen no window appears at all. Nothing of the
user's is read or written: the data, the settings and the Claude folder live in a
temporary folder, and the vault is opened as a preview copy (no hotkey, no watchers).

    python tools/screenshots.py <out folder> [light|dark] [en|ru]
"""
from __future__ import annotations

import os
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
for key in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
    os.environ.pop(key, None)
if sys.platform == "win32" and os.environ.get("QT_QPA_PLATFORM") == "offscreen":
    # offscreen looks for fonts in PySide6's own folder, which has none (tests/qt_helpers.py)
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from daykeeper.journal import today  # noqa: E402
from daykeeper.notes import NoteItem  # noqa: E402
from daykeeper.settings import write_settings  # noqa: E402
from daykeeper.tabs import TAB_IDS  # noqa: E402
from daykeeper.vault import Vault  # noqa: E402

OUT.mkdir(parents=True, exist_ok=True)
VAULT = TMP / "sample.pkv"
SIZE = os.environ.get("SHOT_SIZE", "1200x760")
write_settings(TMP / "settings.json", {"language": LANG, "theme": THEME, "geometry": f"{SIZE}+40+60"})


def pump(seconds: float = 1.2) -> None:
    end = time.time() + seconds
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.03)


def shot(win: QWidget, name: str) -> None:
    """The window as Qt draws it, at the screen's scale (Retina: twice the points)."""
    win.raise_()
    pump(0.6)
    path = OUT / f"{THEME}-{name}.png"
    picture = win.grab()
    picture.save(str(path))
    print(f"{path.name}: {picture.width()}x{picture.height()}", flush=True)


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
    v.notes.add(title="Release 2.0", items=[NoteItem("Check the build on macOS", True),
                                           NoteItem("A long checklist item that wraps onto the next line instead of running past the edge of the card")])
    v.routines.add(name="Backup", kind="script", target="/usr/local/bin/backup.sh")
    v.save()


def main() -> None:
    from daykeeper.qt import theme
    from daykeeper.qt.app import MainWindow
    from daykeeper.qt.dialogs.settings import sections

    app = QApplication(sys.argv[:1])
    app.setStyle(theme.make_style())
    theme.apply(app, theme.theme_named(THEME))

    win = MainWindow(VAULT, preview=True)  # no vault yet: the "new vault" screen
    win.show()
    pump()
    shot(win, "00-new-vault")
    win.close()
    pump(0.3)

    sample_vault()
    win = MainWindow(VAULT, preview=True)
    win.show()
    pump()
    shot(win, "01-unlock")
    win.unlock(PASSWORD)
    pump(2.0)
    for i, mode in enumerate((m for m in TAB_IDS if win.tab_on(m)), start=2):
        win.show_page(mode)
        pump(1.5)
        shot(win, f"{i:02d}-tab-{mode}")
    for i, (section, _text) in enumerate(sections(), start=20):
        win.settings_dialog(section)
        pump(1.5)
        settings = win._settings_win
        assert settings is not None
        shot(settings, f"{i:02d}-settings-{section}")
    win.close_settings()
    win.close()
    pump(0.3)


if __name__ == "__main__":
    main()
