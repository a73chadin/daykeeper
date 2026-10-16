"""Before the window: the colour theme from settings.json. Imports only what does not take
colours from the palette (settings), so every module imported after it sees the theme."""

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


def apply_theme(argv: list[str]) -> None:
    palette.apply(theme_for(argv))
