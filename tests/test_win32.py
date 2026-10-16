"""The Windows calls answer sensibly everywhere: real values on Windows, nothing elsewhere,
and never an exception."""
import re
import sys
import unittest

from daykeeper import win32


class Win32Test(unittest.TestCase):
    def test_nothing_breaks_with_a_dead_handle(self):
        self.assertFalse(win32.caption_colour(0, "#111827", "#FFFFFF"))
        self.assertFalse(win32.round_corners(0, "#E3E6ED"))
        self.assertFalse(win32.flash_window(0))
        self.assertFalse(win32.set_redraw(0, True))
        self.assertFalse(win32.redraw_now(0))
        self.assertEqual(win32.window_handle(0), 0)

    def test_colorref(self):
        self.assertEqual(win32._colorref("#010203"), 0x030201)
        self.assertEqual(win32._colorref("FFFFFF"), 0xFFFFFF)

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_windows_answers(self):
        self.assertRegex(win32.oem_codepage(), r"cp\d+")
        screen = win32.virtual_screen()
        self.assertEqual(len(screen), 4)
        self.assertGreater(screen[2], 0)
        self.assertGreater(screen[3], 0)
        self.assertIn(win32.ui_language_is_russian(), (True, False))
        area = win32.work_area_at(screen[0] + 10, screen[1] + 10)
        self.assertEqual(len(area), 4)
        self.assertLess(area[0], area[2])
        self.assertTrue(win32.IS_WINDOWS)

    @unittest.skipIf(sys.platform == "win32", "elsewhere")
    def test_elsewhere_answers_nothing(self):
        self.assertIsNone(win32.oem_codepage())
        self.assertIsNone(win32.virtual_screen())
        self.assertIsNone(win32.ui_language_is_russian())
        self.assertIsNone(win32.work_area_at(0, 0))
        self.assertFalse(win32.enable_dpi_awareness())
        self.assertFalse(win32.IS_WINDOWS)

    def test_pure_modules_do_not_import_tkinter(self):
        """The hook and inbox.py run on a system Python without tkinter or cryptography."""
        import subprocess

        code = (
            "import sys; sys.modules['tkinter'] = None; sys.modules['cryptography'] = None; "
            "import daykeeper.journal, daykeeper.settings, daykeeper.i18n, daykeeper.win32, daykeeper.layout, daykeeper.palette, daykeeper.pngimage"
        )
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(re.fullmatch(r"", out.stdout))


if __name__ == "__main__":
    unittest.main()
