"""Modal dialogs in the app's look: the window itself (centred on its parent, Return and
Escape), a one-field question, a code or password prompt, and the message boxes with
the app's title filled in."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from collections.abc import Callable

from . import palette
from .i18n import _

BULLET = "•"


class Dialog(tk.Toplevel):
    """A modal window over `parent` with a padded `box` to build in. `keys(ok)` binds
    Return to `ok` and Escape to closing; `editor_buttons` makes the Delete / Save /
    Cancel row of an editor."""

    closes_on_lock = True  # App.close_child_windows

    def __init__(self, parent: tk.Misc, title: str):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=palette.BG)
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)
        self.box = ttk.Frame(self, padding=24)
        self.box.pack()
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_reqwidth()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def keys(self, ok: Callable | None = None, escape: bool = True) -> None:
        if ok is not None:
            self.bind("<Return>", ok)
        if escape:
            self.bind("<Escape>", lambda _e: self.destroy())

    def editor_buttons(self, row: int, columnspan: int, pady: tuple[int, int], save: Callable, delete: Callable | None = None) -> ttk.Frame:
        """Delete (when given), Save and Cancel in the bottom-right corner of the box."""
        btns = ttk.Frame(self.box)
        btns.grid(row=row, column=0, columnspan=columnspan, sticky="e", pady=pady)
        if delete is not None:
            ttk.Button(btns, text=_("Delete"), style="Danger.TButton", command=delete).pack(side="left", padx=(0, 24))
        ttk.Button(btns, text=_("Save"), style="Primary.TButton", command=save).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text=_("Cancel"), command=self.destroy).pack(side="left")
        return btns


def ask_text(parent: tk.Misc, title: str, prompt: str, initial: str = "") -> str | None:
    """A small dialog with one field. None — cancelled."""
    dlg = Dialog(parent, title)
    box = dlg.box
    result: list[str | None] = [None]
    ttk.Label(box, text=prompt).grid(row=0, column=0, sticky="w")
    var = tk.StringVar(value=initial)
    entry = ttk.Entry(box, textvariable=var, width=34)
    entry.grid(row=1, column=0, pady=(6, 14), sticky="ew")
    entry.select_range(0, "end")

    def ok(_e=None):
        result[0] = var.get()
        dlg.destroy()

    row = ttk.Frame(box)
    row.grid(row=2, column=0, sticky="ew")
    ttk.Button(row, text=_("Cancel"), command=dlg.destroy).pack(side="right")
    ttk.Button(row, text=_("Done"), style="Primary.TButton", command=ok).pack(side="right", padx=(0, 8))
    dlg.keys(ok)
    entry.focus_set()
    parent.wait_window(dlg)
    return result[0]


def ask_secret(parent: tk.Misc, title: str, prompt: str, button: str, accept: Callable[[str], bool | str], then: Callable[[], None]) -> None:
    """A code or password prompt: `accept(code)` is True when it is right, else the text to
    show (and the field is emptied); `then()` runs once the dialog has closed on a right one."""
    dlg = Dialog(parent, title)
    box = dlg.box
    ttk.Label(box, text=prompt).grid(row=0, column=0, sticky="w", padx=(0, 12))
    code = ttk.Entry(box, show=BULLET, width=26)
    code.grid(row=0, column=1)
    err = ttk.Label(box, text="", style="Error.TLabel")
    err.grid(row=1, column=0, columnspan=2)

    def submit(_e=None):
        result = accept(code.get())
        if result is not True:
            err.config(text=result)
            code.delete(0, "end")
            return
        dlg.destroy()
        then()

    ttk.Button(box, text=button, style="Primary.TButton", command=submit).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
    dlg.keys(submit)
    code.focus_set()


# ---- message boxes with the app's title

def ask_yes_no(parent: tk.Misc, text: str) -> bool:
    return messagebox.askyesno(palette.APP_TITLE, text, parent=parent)


def show_info(parent: tk.Misc, text: str) -> None:
    messagebox.showinfo(palette.APP_TITLE, text, parent=parent)


def show_warning(parent: tk.Misc, text: str) -> None:
    messagebox.showwarning(palette.APP_TITLE, text, parent=parent)


def show_error(parent: tk.Misc, text: str) -> None:
    messagebox.showerror(palette.APP_TITLE, text, parent=parent)
