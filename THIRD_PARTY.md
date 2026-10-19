# Third-party software

The builds of Daykeeper (the `Daykeeper` folder of `Daykeeper-win64.zip`, `Daykeeper.app`)
carry the software below. Its license texts are in [`licenses/`](licenses/), which the
build copies next to the program (`_internal/licenses/` on Windows, inside the bundle on
macOS), together with this file.

## Qt and Qt for Python (PySide6)

Daykeeper's window uses [Qt](https://www.qt.io/) and
[Qt for Python (PySide6, shiboken6)](https://doc.qt.io/qtforpython-6/), version 6.12,
© The Qt Company Ltd. and other contributors, under the
[GNU Lesser General Public License version 3](licenses/LGPL-3.0.txt), which builds on the
[GNU General Public License version 3](licenses/GPL-3.0.txt).

- Qt's libraries ship as separate files in the build folder, unmodified. You may replace
  them with your own build of the same Qt version; Daykeeper does not check them.
- The source code of Qt and of Qt for Python is available from
  [download.qt.io](https://download.qt.io/official_releases/) and
  [code.qt.io](https://code.qt.io/cgit/pyside/pyside-setup.git/) (the exact version is in
  `requirements-build.txt`).
- Only QtCore, QtGui and QtWidgets and the plugins they need are shipped
  (`packaging/qt_filter.py` lists what is left out).

## Python

The build carries the Python interpreter and its standard library, under the
[Python Software Foundation License](licenses/Python-LICENSE.txt). The same file lists the
libraries the Windows build of Python links (OpenSSL, libffi, bzip2, Zstandard and
others) with their licenses.

## cryptography and cffi

The vault is encrypted with [cryptography](https://cryptography.io/) (Apache 2.0 / BSD; its
license texts ship in the build under `cryptography-<version>.dist-info/licenses/`), which
uses [cffi](https://cffi.readthedocs.io/) (MIT,
[licenses/cffi-LICENSE.txt](licenses/cffi-LICENSE.txt)).

## PyInstaller

The builds are made with [PyInstaller](https://pyinstaller.org/); its bootloader, part of
the program file, is GPL-2.0 with an exception that allows it in programs under any
license.
