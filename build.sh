#!/bin/sh
# Build the macOS app bundle (dist/Daykeeper.app) or, on Linux, a single binary
# (dist/Daykeeper), then print its SHA256.
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
    .venv/bin/python -m pip install -q --require-hashes -r requirements-build.txt
fi
if [ "$(uname)" = "Darwin" ]; then
    .venv/bin/python -m PyInstaller --noconfirm --clean --windowed --noupx --name Daykeeper \
        --osx-bundle-identifier ru.achadin.daykeeper main.py
    ditto -c -k --sequesterRsrc --keepParent dist/Daykeeper.app dist/Daykeeper-macos.zip
    echo "Done: $(pwd)/dist/Daykeeper.app"
    shasum -a 256 dist/Daykeeper-macos.zip
else
    .venv/bin/python -m PyInstaller --noconfirm --clean --onefile --noconsole --noupx --name Daykeeper main.py
    echo "Done: $(pwd)/dist/Daykeeper"
    sha256sum dist/Daykeeper
fi
