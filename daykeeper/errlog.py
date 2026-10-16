"""errors.log next to the vault: what went wrong in a click or a timer of the window. A built
app has no console, so without it an error is just "nothing happened". The traceback only:
no field values, no locals. Owner-only, kept under LIMIT by one older file. No tkinter."""

from __future__ import annotations

import time
import traceback
from pathlib import Path

from .settings import private

NAME = "errors.log"
LIMIT = 256 * 1024  # bytes; then errors.log becomes errors.log.1 and a new one starts


def log_path_for(vault_path: Path) -> Path:
    return Path(vault_path).parent / NAME


def write_error(path: Path, exc_type, exc, tb) -> bool:
    """Append one error with its traceback; False when the file could not be written."""
    text = f"---- {time.strftime('%Y-%m-%d %H:%M:%S')}\n" + "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > LIMIT:
            path.replace(path.with_name(path.name + ".1"))
        new = not path.exists()
        with path.open("a", encoding="utf-8") as f:
            f.write(text)
        if new:
            private(path)
    except OSError:
        return False
    return True
