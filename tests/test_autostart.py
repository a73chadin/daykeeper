"""Start with the system: the entry per platform and its refresh. A dictionary stands in for
the registry, temporary folders for ~/Library and ~/.config; the real ones are not touched."""

import os
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

    def test_a_launch_agent_under_an_earlier_label_is_taken_over(self):
        """A plist written under an earlier label still reads as the entry; writing or
        turning off the entry removes it, so the app never starts twice."""
        with tempfile.TemporaryDirectory() as tmp:
            agents = Path(tmp) / "Library" / "LaunchAgents"
            agents.mkdir(parents=True)
            older = agents / "org.example.daykeeper.plist"
            older.write_text(au.plist(["/Applications/Daykeeper.app/Contents/MacOS/Daykeeper", au.MINIMIZED]), encoding="utf-8")
            unrelated = agents / "org.example.other.plist"
            unrelated.write_text("<plist/>", encoding="utf-8")
            mac = au.default_store("darwin", Path(tmp))
            self.assertTrue(au.is_on(mac))
            self.assertTrue(au.is_minimized(mac))
            au.turn_on(["/Applications/Daykeeper.app/Contents/MacOS/Daykeeper"], mac)
            self.assertFalse(older.exists())
            self.assertTrue((agents / f"{au.AGENT_ID}.plist").exists())
            older.write_text("<plist/>", encoding="utf-8")
            au.turn_off(mac)
            self.assertFalse(au.is_on(mac))
            self.assertFalse(older.exists())
            self.assertTrue(unrelated.exists(), "another program's agent is not touched")

    def test_the_desktop_exec_is_escaped(self):
        """L4: %, backslash, `, $ and " in an Exec= argument as the Desktop Entry spec wants
        them, and the program read back from the file is the path that went in."""
        odd = r'/opt/a "b" $HOME `x` 100% back\slash/Daykeeper'
        text = au.desktop_entry([odd, au.MINIMIZED])
        line = next(x for x in text.splitlines() if x.startswith("Exec="))
        self.assertEqual(line, r'Exec="/opt/a \\"b\\" \\$HOME \\`x\\` 100%% back\\\\slash/Daykeeper" --minimized')
        self.assertEqual(au.exec_arg("/opt/plain/Daykeeper"), "/opt/plain/Daykeeper")
        self.assertEqual(au.exec_arg("50%"), "50%%")
        self.assertEqual(au.entry_program(text), odd)

    def test_the_program_of_each_entry_kind(self):
        self.assertEqual(au.entry_program(au.plist(["/Applications/D & K.app/Contents/MacOS/Daykeeper", au.MINIMIZED])),
                         "/Applications/D & K.app/Contents/MacOS/Daykeeper")
        self.assertEqual(au.entry_program(r'"C:\Program Files\Daykeeper\Daykeeper.exe" --minimized'), r"C:\Program Files\Daykeeper\Daykeeper.exe")
        self.assertEqual(au.entry_program(r"C:\Apps\Daykeeper.exe --minimized"), r"C:\Apps\Daykeeper.exe")


class StartRepairTest(unittest.TestCase):
    """main.refresh_autostart: at start a built app takes over an entry of another build of
    Daykeeper (the 1.x one-file exe, an older folder), and does nothing else."""

    EXE = r"C:\Apps\Daykeeper\Daykeeper.exe"

    def setUp(self):
        import main

        self.refresh = main.refresh_autostart
        self.tmp = tempfile.TemporaryDirectory()
        self.default = Path(self.tmp.name) / "vault.pkv"
        patches = [mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(sys, "executable", self.EXE),
                   mock.patch("daykeeper.vault.default_vault_path", return_value=self.default)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_an_entry_of_another_build_now_starts_this_exe(self):
        store = FakeRun()
        store.value = r"C:\Users\me\Downloads\Daykeeper.exe --minimized"
        self.assertTrue(self.refresh(self.default, store))
        self.assertEqual(store.value, au.command_line([self.EXE, au.MINIMIZED]), "the current exe, minimized as it was")

    def test_off_stays_off(self):
        store = FakeRun()
        self.assertFalse(self.refresh(self.default, store))
        self.assertIsNone(store.value)

    def test_an_entry_of_this_exe_is_left_as_it_is(self):
        store = FakeRun()
        store.value = f'"{self.EXE}" --minimized --edited-by-hand'
        self.assertFalse(self.refresh(self.default, store))
        self.assertEqual(store.value, f'"{self.EXE}" --minimized --edited-by-hand')

    def test_a_run_from_the_source_tree_never_takes_the_entry(self):
        store = FakeRun()
        store.value = r"C:\Old\Daykeeper.exe"
        with mock.patch.object(sys, "frozen", False):
            self.assertFalse(self.refresh(self.default, store))
        self.assertEqual(store.value, r"C:\Old\Daykeeper.exe")

    def test_an_entry_of_a_daykeeper_still_on_disk_is_kept(self):
        """M2: a second copy of Daykeeper the user keeps elsewhere owns its entry; only a
        moved or removed copy gets repaired."""
        kept = Path(self.tmp.name) / "Kept" / "Daykeeper.exe"
        kept.parent.mkdir()
        kept.write_bytes(b"")
        store = FakeRun()
        store.value = au.command_line([str(kept), au.MINIMIZED])
        self.assertFalse(self.refresh(self.default, store))
        self.assertEqual(store.value, au.command_line([str(kept), au.MINIMIZED]))
        kept.unlink()
        self.assertTrue(self.refresh(self.default, store), "the copy is gone: the entry is taken over")
        self.assertEqual(store.value, au.command_line([self.EXE, au.MINIMIZED]))

    def test_another_program_on_disk_is_not_a_daykeeper(self):
        other = Path(self.tmp.name) / "launcher.exe"
        other.write_bytes(b"")
        store = FakeRun()
        store.value = au.command_line([str(other)])
        self.assertTrue(self.refresh(self.default, store), "an existing file that is not a Daykeeper build is no reason to keep it")

    def test_a_try_out_from_temp_or_downloads_never_takes_the_entry(self):
        """M2: a build started from the temporary folder or from Downloads leaves the entry."""
        home = Path(os.path.abspath(os.sep + "fake-home"))
        for exe in (str(Path(tempfile.gettempdir()) / "unzipped" / "Daykeeper" / "Daykeeper.exe"),
                    str(home / "Downloads" / "Daykeeper-win64" / "Daykeeper" / "Daykeeper.exe")):
            store = FakeRun()
            store.value = r"C:\Gone\Daykeeper.exe"
            with mock.patch.object(sys, "executable", exe), mock.patch.object(Path, "home", return_value=home):
                self.assertFalse(self.refresh(self.default, store), exe)
            self.assertEqual(store.value, r"C:\Gone\Daykeeper.exe")

    def test_throwaway_places(self):
        home = Path(os.path.abspath(os.sep + "fake-home"))  # not under the temporary folder
        self.assertTrue(au.is_throwaway(str(Path(tempfile.gettempdir()) / "x" / "Daykeeper.exe"), home))
        self.assertTrue(au.is_throwaway(str(home / "Downloads" / "Daykeeper.exe"), home))
        self.assertFalse(au.is_throwaway(str(home / "Apps" / "Daykeeper.exe"), home))
        self.assertFalse(au.is_throwaway(str(home / "Downloads-old" / "Daykeeper.exe"), home))

    def test_a_store_that_fails_is_quiet(self):
        store = mock.Mock()
        store.get.side_effect = OSError("denied")
        self.assertFalse(self.refresh(self.default, store))


@unittest.skipUnless(sys.platform == "win32" and os.environ.get("DAYKEEPER_REGISTRY_TESTS") == "1",
                     "writes a throwaway key in the real registry: Windows only, and only with DAYKEEPER_REGISTRY_TESTS=1")
class WindowsRunTest(unittest.TestCase):
    """A throwaway key under HKCU\\Software, removed afterwards; the real Run key is not used.
    It still writes to the real registry, so it runs only when asked for."""

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
