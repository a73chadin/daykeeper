"""Colours, fonts and the shown name of the app — the one place they are defined.

Pure (no Qt): the Qt theme (daykeeper/qt/theme.py) builds its two Theme objects from here,
so a colour changed here changes everywhere at once.

Two themes. The light one is what the names hold; DARK lists the names the dark theme
gives other colours. The names themselves never change while the app runs."""

from __future__ import annotations

import sys

APP_TITLE = "Daykeeper Multitool"  # shown name; the exe, the bundle and the data folder stay “Daykeeper”

_MAC = sys.platform == "darwin"
FONT = "Segoe UI" if sys.platform == "win32" else ("Helvetica Neue" if _MAC else "DejaVu Sans")
MONO = "Consolas" if sys.platform == "win32" else ("Menlo" if _MAC else "DejaVu Sans Mono")

WHITE = "#FFFFFF"  # the light surface: fields, menus, tool tips' text; not white in the dark theme
ON_ACCENT = "#111827"  # text and ticks on an accent fill, in both themes: ink on amber, as on the icon
ON_RAIL = "#FFFFFF"  # text on the dark rail under the pointer, in both themes
BG = "#F3F4F8"  # window and unlock screen background
SIDEBAR = "#F7F8FB"  # left column with the list
CARD = "#FFFFFF"  # entry card
FIELD = "#FFFFFF"
BORDER = "#E3E6ED"
TEXT = "#111827"
MUTED = "#667085"  # 4.7:1 on the window background (#6B7280 was 4.4:1)
DISABLED_TEXT = "#6E7684"  # a greyed-out menu item (4.5:1 on white; #B8BDC7 was 1.9:1)
ACCENT = "#F59E0B"  # amber, the colour of the icon; never text — too light on white (LINK and ACCENT_INK are)
LINK = "#B45309"  # link-like text on a card: a darker amber, 5:1 on white; lighter in the dark theme
ACCENT_HOVER = "#D97706"
ACCENT_PRESSED = "#B45309"
ACCENT_DISABLED = "#FDE68A"
ACCENT_SOFT = "#FEF3C7"
ACCENT_INK = "#92400E"  # text on a soft accent background
DANGER = "#DC2626"
DANGER_SOFT = "#FEE2E2"  # a danger button under the pointer
DANGER_PRESSED = "#FECACA"
SUCCESS = "#15803D"  # 4.5:1 on white (#16A34A was 3.3:1)
HOVER = "#F1F3F8"
PRESSED = "#E6E9F0"
CHECK_BORDER = "#C3C8D3"
SCROLL = "#D3D8E1"
SCROLL_HOVER = "#A3ABB9"
RAIL = "#111827"  # the navigation rail on the left
RAIL_HOVER = "#1F2937"
RAIL_ACTIVE = "#F59E0B"  # the active tab: the amber pill with ink text
RAIL_TEXT = "#9CA3AF"
CRED_BG = "#F6F7F9"  # the login and password rows on a bookmark tile
CONSOLE_BG = "#0F172A"  # the output window of a routine
CONSOLE_TEXT = "#E2E8F0"
TILE = "#FFFFFF"  # a bookmark or routine tile, a read event
RAISED = "#FFFFFF"  # the face of a raised button
RAISED_HOVER = "#F7F8FB"  # ... under the pointer
DANGER_HOVER = "#FEF2F2"
CODE_BG = "#F1F3F6"  # `code` in a Markdown file
CODE_TEXT = "#B42318"
CODEBLOCK_BG = "#F6F8FA"
SHADE_INK = "#8A93A6"  # the soft shadow over a pinned bar, at its darkest

THEMES = ("light", "dark")

DARK = {
    "WHITE": "#262B33",
    "BG": "#16181D",
    "SIDEBAR": "#1B1E24",
    "CARD": "#1F232A",
    "FIELD": "#262B33",
    "BORDER": "#343A46",
    "TEXT": "#E7E9EE",
    "MUTED": "#9AA3B2",
    "DISABLED_TEXT": "#8E97A7",
    "ACCENT": "#F59E0B",
    "LINK": "#FBBF24",
    "ACCENT_HOVER": "#FBBF24",
    "ACCENT_PRESSED": "#D97706",
    "ACCENT_DISABLED": "#6B5313",
    "ACCENT_SOFT": "#3A2E12",
    "ACCENT_INK": "#FCD34D",
    "DANGER": "#F87171",
    "DANGER_SOFT": "#3D2226",
    "DANGER_PRESSED": "#4C262B",
    "SUCCESS": "#4ADE80",
    "HOVER": "#2A2F38",
    "PRESSED": "#323844",
    "CHECK_BORDER": "#4B5362",
    "SCROLL": "#3A404C",
    "SCROLL_HOVER": "#596273",
    "RAIL": "#0E1014",
    "RAIL_HOVER": "#20242B",
    "RAIL_ACTIVE": "#F59E0B",
    "RAIL_TEXT": "#9CA3AF",
    "CRED_BG": "#2A2F38",
    "CONSOLE_BG": "#0B0E14",
    "TILE": "#252A32",
    "RAISED": "#2C313A",
    "RAISED_HOVER": "#333944",
    "DANGER_HOVER": "#3D2226",
    "CODE_BG": "#2C313A",
    "CODE_TEXT": "#FCA5A5",
    "CODEBLOCK_BG": "#232830",
    "SHADE_INK": "#000000",
}


def mix(a: str, b: str, t: float) -> str:
    """`a` laid over `b` at strength `t` (0 — b, 1 — a)."""
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x * t + y * (1 - t)):02X}" for x, y in zip(ca, cb, strict=True))
