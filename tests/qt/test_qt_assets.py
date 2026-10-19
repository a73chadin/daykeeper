"""The pictures of assets/: found in the source tree and inside a built app, absent
without a crash, and every file the builds need is there."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

from tests.qt_helpers import qt_app, require_qt

require_qt()

from daykeeper.qt import theme  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def setUpModule():
    qt_app()


class AssetLookupTest(unittest.TestCase):
    def test_the_icon_is_found_in_the_source_tree(self) -> None:
        path = theme.asset("icon.png")
        self.assertEqual(path, ROOT / "assets" / "icon.png")
        self.assertTrue(path is not None and path.is_file())

    def test_a_built_app_looks_next_to_its_unpacked_files(self) -> None:
        """PyInstaller puts the `assets` data folder under sys._MEIPASS."""
        with mock.patch.object(sys, "_MEIPASS", str(ROOT), create=True):
            self.assertEqual(theme.asset("icon.png"), ROOT / "assets" / "icon.png")
        with mock.patch.object(sys, "_MEIPASS", str(ROOT / "nowhere"), create=True):
            self.assertIsNone(theme.asset("icon.png"), "no icon there: None, not a path to nothing")
            self.assertTrue(theme.window_icon().isNull(), "the window opens all the same, without an icon")

    def test_every_asset_the_builds_need_is_there(self) -> None:
        for name in ("icon.png", "icon.ico", "icon.icns"):
            self.assertTrue((ROOT / "assets" / name).is_file(), name)
        ico = (ROOT / "assets" / "icon.ico").read_bytes()
        self.assertEqual(ico[:4], b"\x00\x00\x01\x00", "an ICO header")
        self.assertGreaterEqual(int.from_bytes(ico[4:6], "little"), 9, "16 to 256 px inside")
        self.assertEqual((ROOT / "assets" / "icon.icns").read_bytes()[:4], b"icns")


if __name__ == "__main__":
    unittest.main()
