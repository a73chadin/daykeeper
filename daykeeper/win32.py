"""Calls into Windows (ctypes), the only place they live apart from the clipboard.

Every function does nothing and answers None / False on another system or when the
call is not there (an older Windows, no DWM), so callers need no platform checks of
their own. A window is addressed by its handle (`int(widget.winId())` in Qt); nothing
here imports Qt."""

from __future__ import annotations

import ctypes
import sys

IS_WINDOWS = sys.platform == "win32"

_DWMWA_CAPTION_COLOR, _DWMWA_TEXT_COLOR = 35, 36
_LANG_RUSSIAN = 0x19

_FAILURES = (OSError, AttributeError, ValueError, TypeError)


def _colorref(colour: str) -> int:
    """"#RRGGBB" as the COLORREF Windows wants (0x00BBGGRR)."""
    c = colour.lstrip("#")
    return int(c[0:2], 16) | (int(c[2:4], 16) << 8) | (int(c[4:6], 16) << 16)


def oem_codepage() -> str | None:
    """The console code page, as a Python codec name ("cp866"); None elsewhere."""
    if not IS_WINDOWS:
        return None
    try:
        return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    except _FAILURES:
        return None


def text_scale() -> float:
    """Windows' "Text size" (Settings → Accessibility), 1.0 to 2.25; 1.0 elsewhere or when
    it is not set. Programs get it only by reading it: a font of a given size is not made
    bigger by the system."""
    if not IS_WINDOWS:
        return 1.0
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Accessibility") as key:
            value = int(winreg.QueryValueEx(key, "TextScaleFactor")[0])
    except (OSError, ValueError, TypeError):
        return 1.0
    return min(2.25, max(1.0, value / 100))


def ui_language_is_russian() -> bool | None:
    """Whether the Windows UI language is Russian; None elsewhere or when unknown."""
    if not IS_WINDOWS:
        return None
    try:
        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
    except _FAILURES:
        return None
    return (lang_id & 0x3FF) == _LANG_RUSSIAN


def caption_colour(hwnd: int, background: str, text: str) -> bool:
    """Paint the Windows 11 title bar of a window; False where it is not supported."""
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        for attr, colour in ((_DWMWA_CAPTION_COLOR, background), (_DWMWA_TEXT_COLOR, text)):
            value = ctypes.c_int(_colorref(colour))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))
        return True
    except _FAILURES:  # not Windows 11 or no DWM
        return False


def _declare() -> None:
    """Argument and result types for every call above, once at import. Without them ctypes
    passes a window handle above 2^31 as a negative 32-bit int on x64, and the call
    silently does nothing for such a window (the caption colours). A function an older
    Windows does not have is skipped; its caller already falls back."""
    if not IS_WINDOWS:
        return
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    signatures: list[tuple] = [
        (kernel32, "GetOEMCP", [], wintypes.UINT),
        (kernel32, "GetUserDefaultUILanguage", [], ctypes.c_ushort),
    ]
    try:
        signatures.append((ctypes.windll.dwmapi, "DwmSetWindowAttribute", [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD], ctypes.c_long))
    except _FAILURES:
        pass
    for dll, name, args, result in signatures:
        try:
            fn = getattr(dll, name)
            fn.argtypes, fn.restype = args, result
        except _FAILURES:
            pass


_declare()
