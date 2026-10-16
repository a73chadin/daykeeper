"""An RGBA PNG from raw pixels (zlib only, no Pillow). Pure: the theme draws its rounded
shapes with it and the site icons are re-encoded with it, neither needs tkinter for that."""

from __future__ import annotations

import struct
import zlib


def png(width: int, height: int, rgba: bytearray) -> bytes:
    """An RGBA PNG from raw pixels."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    rows = b"".join(b"\x00" + bytes(rgba[y * width * 4 : (y + 1) * width * 4]) for y in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )
