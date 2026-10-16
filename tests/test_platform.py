"""Per-system choices that must not depend on the machine running the tests: the data
folder and the language detection helpers."""

import unittest
from pathlib import Path

from daykeeper.i18n import hotkeys, language_from_env, language_from_tag
from daykeeper.settings import data_base_dir


class DataDirTest(unittest.TestCase):
    HOME = Path("/Users/alice")

    def test_windows_uses_appdata(self):
        self.assertEqual(data_base_dir("win32", {"APPDATA": r"C:\Users\alice\AppData\Roaming"}, self.HOME), Path(r"C:\Users\alice\AppData\Roaming"))

    def test_windows_without_appdata_falls_back_to_home(self):
        self.assertEqual(data_base_dir("win32", {}, self.HOME), self.HOME)

    def test_macos_uses_application_support(self):
        self.assertEqual(data_base_dir("darwin", {"APPDATA": "ignored"}, self.HOME), self.HOME / "Library" / "Application Support")

    def test_linux_uses_xdg_or_local_share(self):
        self.assertEqual(data_base_dir("linux", {"XDG_DATA_HOME": "/data"}, self.HOME), Path("/data"))
        self.assertEqual(data_base_dir("linux", {}, self.HOME), self.HOME / ".local" / "share")


class LanguageTest(unittest.TestCase):
    def test_tag(self):
        for tag in ("ru", "ru_RU", "ru_RU.UTF-8", "ru-RU", "RU_ru\n"):
            self.assertEqual(language_from_tag(tag), "ru", tag)
        for tag in ("en_US.UTF-8", "C", "", "de_DE"):
            self.assertEqual(language_from_tag(tag), "en", tag)

    def test_env_order(self):
        self.assertEqual(language_from_env({"LANG": "en_US", "LC_ALL": "ru_RU"}), "ru")
        self.assertEqual(language_from_env({"LANG": "ru_RU", "LC_MESSAGES": "en_US"}), "en")
        self.assertEqual(language_from_env({"LANG": "ru_RU.UTF-8"}), "ru")
        self.assertEqual(language_from_env({}), "en")

    def test_hotkeys_keep_catalog_text_outside_macos(self):
        text = "Save\tCtrl+S"
        self.assertIn(hotkeys(text), (text, "Save\tCmd+S"))


if __name__ == "__main__":
    unittest.main()
