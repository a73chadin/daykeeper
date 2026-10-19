"""Two-language UI. Code uses English keys via `_()`; the Russian catalog lives in
`locale_ru.py`. The language is chosen in the app (File → Language) and stored in
settings.json; without a setting the system UI language decides."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Mapping

from . import win32
from .locale_ru import RU

LANGUAGES: list[tuple[str, str]] = [("en", "English"), ("ru", "Russian")]  # (code, name key)
CATALOGS: dict[str, dict[str, str]] = {"ru": RU}

MAC = sys.platform == "darwin"
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")  # UI keys
MONTHS_SHORT = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")  # UI keys

_lang = "en"


def set_language(code: str) -> None:
    global _lang
    _lang = code if code in dict(LANGUAGES) else "en"


def language() -> str:
    return _lang


def _(key: str) -> str:
    """Translate a UI string; unknown keys and English fall through unchanged."""
    return CATALOGS.get(_lang, {}).get(key, key)


def variants(key: str) -> set[str]:
    """The key in every language — for parsing labels a user typed by hand."""
    out = {key}
    for catalog in CATALOGS.values():
        if key in catalog:
            out.add(catalog[key])
    return out


def hotkeys(text: str) -> str:
    """Shortcut labels for this system: “Ctrl+S” in the catalog becomes “Cmd+S” on macOS
    (Qt draws it as ⌘S in menus)."""
    return text.replace("Ctrl+", "Cmd+") if MAC else text


def fmt_day(date: str) -> str:
    """"2026-10-14" → "Wed 14.10.2026" with the weekday in the UI language; other text as is."""
    try:
        t = time.strptime(date, "%Y-%m-%d")
    except ValueError:
        return date
    return f"{_(WEEKDAYS[t.tm_wday])} {t.tm_mday:02d}.{t.tm_mon:02d}.{t.tm_year}"


def fmt_month(year: int, month: int, with_year: bool = True) -> str:
    """(2026, 10) → "Oct 2026" with the month in the UI language; "Oct" without the year."""
    name = _(MONTHS_SHORT[month - 1])
    return f"{name} {year}" if with_year else name


def fmt_time(ts: float) -> str:
    """A timestamp as "14.10.2026 09:30" in local time."""
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(ts))


def language_from_tag(tag: str) -> str:
    """"ru_RU.UTF-8" / "ru-RU" / "ru" → "ru"; anything else → "en"."""
    return "ru" if tag.strip().lower().startswith("ru") else "en"


def language_from_env(env: Mapping[str, str] = os.environ) -> str:
    """POSIX locale variables, in the order the C library consults them."""
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if env.get(name):
            return language_from_tag(env[name])
    return "en"


def detect_system_language() -> str:
    """Russian system UI → "ru", anything else → "en". Windows asks the UI language,
    macOS the user's locale (apps launched from Finder carry no LANG), others the env."""
    if win32.IS_WINDOWS:
        return "ru" if win32.ui_language_is_russian() else "en"
    if MAC:
        try:
            out = subprocess.run(["defaults", "read", "-g", "AppleLocale"], capture_output=True, text=True, timeout=3, check=False)  # noqa: S603, S607
            if out.returncode == 0 and out.stdout.strip():
                return language_from_tag(out.stdout)
        except (OSError, subprocess.SubprocessError):
            pass
    return language_from_env()
