"""Entry point: python main.py [path_to_vault_file] [--preview] [--minimized].

`--minimized` starts in the taskbar (the start with the system passes it), `--preview`
opens a copy of the vault for a look and deletes it on close. The colour theme comes from
the settings of the vault (boot.theme_for); it is an object put on the application."""

import os
import sys

# a plugin path from the environment would load a foreign Qt plugin into the process that
# holds the vault: gone before Qt reads it, and the plugins taken from PySide6's own folder
for _name in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
    os.environ.pop(_name, None)


def main() -> int:
    import threading
    from pathlib import Path

    from PySide6.QtCore import QCoreApplication, qVersion
    from PySide6.QtWidgets import QApplication

    from daykeeper import autostart, errlog
    from daykeeper.boot import theme_for
    from daykeeper.session import remove_preview_copy
    from daykeeper.vault import default_vault_path

    argv = sys.argv[1:]
    flags = {"--preview", autostart.MINIMIZED}
    args = [a for a in argv if a not in flags]
    preview = "--preview" in argv
    # errors before the window and in threads go to errors.log, a segfault or an abort to
    # crash.log (a preview copy's folder is deleted with it: no crash.log held open there)
    vault_path = Path(args[0]) if args else default_vault_path()
    log = errlog.log_path_for(vault_path)
    errlog.set_qt_version(qVersion())
    errlog.install_hooks(log)
    crashed = False if preview else errlog.start_crash_log(vault_path.parent)

    QCoreApplication.setLibraryPaths([plugin_folder()])
    app = QApplication(sys.argv[:1])
    from daykeeper.qt.errors import install_qt_messages

    install_qt_messages(log)

    from daykeeper.qt import theme

    app.setStyle(theme.make_style())  # Fusion on Windows and macOS alike, our check boxes; the style sheet goes over it
    from daykeeper.qt.app import MainWindow, install_excepthook

    theme.apply(app, theme.theme_named(theme_for(argv)))
    window = MainWindow(Path(args[0]) if args else None, preview=preview)
    install_excepthook(window)
    # logging off or shutting down: Windows asks every program before the windows go (Qt's
    # commitData); what is typed is saved then, whether or not a close event follows
    def save_for_logoff(_manager) -> None:
        window.flush_edits()
        window.save_now()

    app.commitDataRequest.connect(save_for_logoff)
    window.show_as_saved(minimized=autostart.MINIMIZED in argv)
    if crashed:
        window.tell_crash()
    if not preview:
        refresh_autostart(window.vault_path)
    code = app.exec()
    errlog.end_crash_log()
    if "--preview" in argv:
        remove_preview_copy(window.vault_path)  # the duplicate made for the look goes with the window
    # a site icon or a GitLab check may still be waiting on the network; the window is gone
    # and everything is saved, so the process does not wait for them
    if any(t.is_alive() and not t.daemon for t in threading.enumerate() if t is not threading.main_thread()):
        os._exit(code)
    return code


def plugin_folder() -> str:
    """Qt's plugins inside PySide6, as Qt's own qt.conf there says: PySide6/plugins on
    Windows, PySide6/Qt/plugins on macOS and Linux (a fixed "plugins" left macOS without
    its platform plugin, and the window could not start there)."""
    from pathlib import Path

    import PySide6
    from PySide6.QtCore import QLibraryInfo

    folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)
    return folder if Path(folder).is_dir() else str(Path(PySide6.__file__).resolve().parent / "plugins")


def refresh_autostart(vault_path, store=None) -> bool:
    """A start with the system left by a copy of Daykeeper that is gone (moved, updated in a
    new place, or a 1.x one-file exe since deleted) now starts this one. Only a built app
    does it, only when the start with the system is on, and not from the temporary folder or
    Downloads: an absent entry stays absent, a try-out of an unpacked archive leaves the
    kept copy's entry alone, and a run from the source tree never points the user's sign-in
    at a checkout. An entry whose Daykeeper is still on disk is kept (autostart.refresh).
    True — it was rewritten."""
    from daykeeper import autostart
    from daykeeper.vault import default_vault_path

    if not getattr(sys, "frozen", False) or autostart.is_throwaway(sys.executable):
        return False
    try:
        return autostart.refresh(autostart.launch_args(autostart.is_minimized(store), vault_path, default_vault_path()), store)
    except OSError:
        return False


if __name__ == "__main__":
    sys.exit(main())
