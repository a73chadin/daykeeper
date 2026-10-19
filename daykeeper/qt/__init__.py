"""The Qt (PySide6) window of Daykeeper (the Tk window it replaced went in 2.0).

Everything that imports PySide6 lives in this package and nowhere else: the core, the
hook and `daykeeper-cli` must start without Qt (tests/test_qt_package.py keeps it so)."""
