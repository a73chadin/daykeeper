"""Start with the system: the entry per platform and its refresh. A dictionary stands in for
the registry, temporary folders for ~/Library and ~/.config; the real ones are not touched."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from daykeeper import autostart as au


class FakeRun:
    def __init__(self):
        self.value = None

    def get(self):
        return self.value

    def put(self, args):
        self.value = au.command_line(args)

    def remove(self):
        self.value = None


class LaunchArgsTest(unittest.TestCase):
    def test_built_app(self):
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(sys, "executable", r"C:\Apps\Day Keeper\Daykeeper.exe"):
            args = au.launch_args(True, Path("C:/v/vault.pkv"), Path("C:/v/vault.pkv"))
            self.assertEqual(args, [r"C:\Apps\Day Keeper\Daykeeper.exe", au.MINIMIZED])
            self.assertEqual(au.command_line(args), r'"C:\Apps\Day Keeper\Daykeeper.exe" --minimized')
            self.assertIn("D:/other.pkv", [a.replace("\\", "/") for a in au.launch_args(False, Path("D:/other.pkv"), Path("C:/v/vault.pkv"))])

    def test_source_tree(self):
        args = au.launch_args(False)
        self.assertTrue(args[1].endswith("main.py"))
        self.assertNotIn(au.MINIMIZED, args)


class StoreTest(unittest.TestCase):
    def test_on_off_and_minimized(self):
        store = FakeRun()
        self.assertFalse(au.is_on(store))
        au.turn_on(["C:/D.exe", au.MINIMIZED], store)
        self.assertTrue(au.is_on(store))
        self.assertTrue(au.is_minimized(store))
        au.turn_off(store)
        self.assertFalse(au.is_on(store))
        au.turn_off(store)  # twice is fine

    def test_refresh_rewrites_only_a_stale_entry(self):
        store = FakeRun()
        self.assertFalse(au.refresh(["C:/new/D.exe"], store), "off stays off")
        self.assertIsNone(store.value)
        au.turn_on(["C:/old/D.exe"], store)
        self.assertTrue(au.refresh(["C:/new/D.exe"], store))
        self.assertEqual(store.value, "C:/new/D.exe")
        self.assertFalse(au.refresh(["C:/new/D.exe"], store))

    def test_macos_and_linux_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            mac = au.default_store("darwin", Path(tmp))
            au.turn_on(["/Applications/Daykeeper.app/Contents/MacOS/Daykeeper", au.MINIMIZED], mac)
            text = (Path(tmp) / "Library" / "LaunchAgents" / f"{au.AGENT_ID}.plist").read_text(encoding="utf-8")
            self.assertIn("<string>--minimized</string>", text)
            self.assertIn("<true/>", text)
            self.assertTrue(au.is_minimized(mac))
            with mock.patch.dict("os.environ", {"XDG_CONFIG_HOME": ""}):
                linux = au.default_store("linux", Path(tmp))
            au.turn_on(["/opt/day keeper/Daykeeper"], linux)
            entry = (Path(tmp) / ".config" / "autostart" / "daykeeper.desktop").read_text(encoding="utf-8")
            self.assertIn('Exec="/opt/day keeper/Daykeeper"', entry)
            au.turn_off(linux)
            self.assertFalse(au.is_on(linux))
            self.assertFalse(au.refresh(["/x"], linux))


@unittest.skipUnless(sys.platform == "win32", "the Run key is Windows-only")
class WindowsRunTest(unittest.TestCase):
    """A throwaway key under HKCU\\Software, removed afterwards; the real Run key is not used."""

    KEY = r"Software\DaykeeperAutostartTest"

    def tearDown(self):
        import winreg
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, self.KEY)
        except OSError:
            pass

    def test_value_round_trip(self):
        store = au.WindowsRun(self.KEY)
        self.assertIsNone(store.get())
        store.put([r"C:\Program Files\Daykeeper.exe", au.MINIMIZED])
        self.assertEqual(store.get(), r'"C:\Program Files\Daykeeper.exe" --minimized')
        store.remove()
        self.assertIsNone(store.get())


if __name__ == "__main__":
    unittest.main()
