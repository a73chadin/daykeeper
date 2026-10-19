#!/bin/sh
# Build the app from packaging/daykeeper.spec: on macOS the bundle dist/Daykeeper.app
# (zipped to dist/Daykeeper-macos.zip), elsewhere the folder dist/Daykeeper/; then print
# the SHA256. The spec puts the version and the minimum system (macOS 14) into Info.plist.
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
    .venv/bin/python -m pip install -q --require-hashes -r requirements-build.txt
fi
.venv/bin/python -m PyInstaller --noconfirm --clean --distpath dist --workpath build/Daykeeper packaging/daykeeper.spec
if [ "$(uname)" = "Darwin" ]; then
    .venv/bin/python packaging/check_size.py dist/Daykeeper.app 90
    # an ad-hoc signature over the whole bundle (no certificate: see README)
    codesign --force --deep --sign - dist/Daykeeper.app
    ditto -c -k --sequesterRsrc --keepParent dist/Daykeeper.app dist/Daykeeper-macos.zip
    echo "Done: $(pwd)/dist/Daykeeper.app"
    shasum -a 256 dist/Daykeeper-macos.zip
else
    .venv/bin/python packaging/check_size.py dist/Daykeeper 95
    echo "Done: $(pwd)/dist/Daykeeper"
fi
