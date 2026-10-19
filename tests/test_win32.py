"""The Windows calls answer sensibly everywhere: real values on Windows, nothing elsewhere,
and never an exception."""
import re
import sys
import unittest

from daykeeper import win32


class Win32Test(unittest.TestCase):
    def test_nothing_breaks_with_a_dead_handle(self):
        self.assertFalse(win32.caption_colour(0, "#111827", "#FFFFFF"))

    def test_colorref(self):
        self.assertEqual(win32._colorref("#010203"), 0x030201)
        self.assertEqual(win32._colorref("FFFFFF"), 0xFFFFFF)

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_windows_answers(self):
        self.assertRegex(win32.oem_codepage(), r"cp\d+")
        self.assertIn(win32.ui_language_is_russian(), (True, False))
        self.assertGreaterEqual(win32.text_scale(), 1.0)
        self.assertTrue(win32.IS_WINDOWS)

    @unittest.skipIf(sys.platform == "win32", "elsewhere")
    def test_elsewhere_answers_nothing(self):
        self.assertIsNone(win32.oem_codepage())
        self.assertIsNone(win32.ui_language_is_russian())
        self.assertEqual(win32.text_scale(), 1.0)
        self.assertFalse(win32.IS_WINDOWS)

    def test_the_helper_and_the_pure_modules_need_neither_qt_nor_tkinter(self):
        """The hook, daykeeper-cli and inbox.py run on a system Python without Qt, tkinter or
        cryptography: each of them blocked, the helper's entry points still import."""
        import subprocess
        from pathlib import Path

        code = (
            "import sys; [sys.modules.__setitem__(m, None) for m in ('tkinter', 'PySide6', 'shiboken6', 'cryptography')]; "
            "import daykeeper.journal, daykeeper.settings, daykeeper.i18n, daykeeper.win32, daykeeper.layout, daykeeper.palette, daykeeper.pngimage, "
            "daykeeper.cli, cli_main, inbox"
        )
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(re.fullmatch(r"", out.stdout))


if __name__ == "__main__":
    unittest.main()
