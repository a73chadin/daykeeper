"""Before the window: the colour theme from settings.json, read without the vault (only
settings and the palette's theme names), so the window is built in it from the start."""

from __future__ import annotations

from pathlib import Path

from . import palette
from .settings import app_dir, read_settings, settings_path_for


def theme_for(argv: list[str]) -> str:
    """The theme the settings of the vault in `argv` (or the default vault) ask for."""
    args = [a for a in argv if not a.startswith("--")]
    vault = Path(args[0]) if args else app_dir() / "vault.pkv"
    name = str(read_settings(settings_path_for(vault)).get("theme") or "light")
    return name if name in palette.THEMES else "light"

