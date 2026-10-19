"""Geometry without widgets: how cards and tiles share a width, where a window may sit.
Pure (no tkinter), so it is unit-tested with plain numbers."""

from __future__ import annotations

import re

CARD_MIN_WIDTH = 340  # a note card column; the number of columns follows the window width
CARD_GAP = 14
TILE_MIN_WIDTH = 270  # a bookmark or routine tile
TILE_MARGIN = 48  # what a tile grid keeps for its padding before it counts columns
DEFAULT_GEOMETRY = "1180x720"


def column_width(avail: int, gap: int = CARD_GAP, minimum: int = CARD_MIN_WIDTH) -> tuple[int, float]:
    """(the number of columns, the width of one) for the space there is."""
    cols = max(1, (avail + gap) // (minimum + gap))
    return cols, (avail - gap * (cols - 1)) / cols


def flow_layout(sizes: list[tuple[int, int]], avail: int, gap: int = CARD_GAP) -> tuple[list[tuple[int, int]], int]:
    """Place cards of the given (width, height) left to right, starting a new row when the
    next one does not fit; a row is as tall as its tallest card. ([(x, y)], total height)."""
    out: list[tuple[int, int]] = []
    x = y = row_h = 0
    for w, h in sizes:
        if x and x + w > avail:
            x, y, row_h = 0, y + row_h + gap, 0
        out.append((x, y))
        x += w + gap
        row_h = max(row_h, h)
    return out, (y + row_h if sizes else 0)


def tile_columns(width: int, minimum: int = TILE_MIN_WIDTH, margin: int = TILE_MARGIN) -> int:
    """How many tiles of at least `minimum` fit in a grid `width` wide; never fewer than one."""
    return max(1, (width - margin) // minimum)


def parse_geometry(geometry: str, default: str = DEFAULT_GEOMETRY) -> tuple[str, str]:
    """"WxH+X+Y" → ("WxH", "X+Y"); a size that does not look like one gives `default`,
    a missing position gives ""."""
    size, _sep, pos = geometry.partition("+")
    if not re.fullmatch(r"\d{3,5}x\d{3,5}", size):
        size = default
    return size, pos


def on_screen(geometry: str, screen: tuple[int, int, int, int] | None) -> bool:
    """The top-left corner of "WxH+X+Y" lands on the screen (otherwise the window is lost).
    `screen` is (left, top, width, height) of all monitors; None — unknown, then only a
    corner with non-negative coordinates counts as on screen."""
    m = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", geometry)
    if not m:
        return False
    x, y = int(m.group(3)), int(m.group(4))
    if screen is None:
        return x >= 0 and y >= 0
    left, top, width, height = screen
    return left - 50 <= x < left + width - 100 and top - 50 <= y < top + height - 100
