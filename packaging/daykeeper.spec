# -*- mode: python -*-
# Daykeeper as a folder (the LGPL needs Qt's libraries as files of their own, and a folder
# starts three times faster than one file): dist/Daykeeper/ with Daykeeper.exe, and on
# macOS dist/Daykeeper.app. Build: python -m PyInstaller --noconfirm --clean packaging/daykeeper.spec
# (build.bat / build.sh do it). It carries the console helper when dist/daykeeper-cli/ is
# there (build it first), the assets, and the license texts of Qt / Qt for Python.
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
sys.path.insert(0, SPECPATH)
from qt_filter import QT_EXCLUDE_MODULES, filt  # noqa: E402

MAC = sys.platform == "darwin"
datas = [(os.path.join(ROOT, "assets"), "assets"), (os.path.join(ROOT, "licenses"), "licenses"), (os.path.join(ROOT, "THIRD_PARTY.md"), ".")]
helper = os.path.join(ROOT, "dist", "daykeeper-cli")
if os.path.isdir(helper) and not MAC:
    datas.append((helper, "daykeeper-cli"))

a = Analysis([os.path.join(ROOT, "main.py")], pathex=[ROOT], datas=datas, excludes=QT_EXCLUDE_MODULES, noarchive=False)
a.binaries = filt(a.binaries)
a.datas = filt(a.datas)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Daykeeper", console=False, upx=False,
          icon=os.path.join(ROOT, "assets", "icon.icns" if MAC else "icon.ico"))
coll = COLLECT(exe, a.binaries, a.datas, name="Daykeeper", upx=False)
if MAC:
    sys.path.insert(0, ROOT)
    from daykeeper import __version__  # noqa: E402

    version = __version__.split("-")[0]  # macOS takes digits and dots only: 2.0.1-beta.1 -> 2.0.1
    # the version (PyInstaller writes 0.0.0 by itself) and the minimum system: macOS 14,
    # the oldest the PySide6 6.12 wheels run on
    app = BUNDLE(coll, name="Daykeeper.app", icon=os.path.join(ROOT, "assets", "icon.icns"), bundle_identifier="local.daykeeper",
                 version=version, info_plist={"CFBundleShortVersionString": version, "CFBundleVersion": version, "LSMinimumSystemVersion": "14.0",
                                              "NSHighResolutionCapable": True})
