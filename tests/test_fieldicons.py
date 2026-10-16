import unittest

from daykeeper.fieldicons import PLAIN, SECRET, icon_for, summary
from daykeeper.i18n import variants
from daykeeper.vault import Field


class FieldIconsTest(unittest.TestCase):
    def test_presets_in_any_language(self):
        for name in variants("login"):
            self.assertEqual(icon_for(name, False), "👤")
        self.assertEqual(icon_for("  PASSWORD ", True), "🔑")

    def test_unknown_fields(self):
        self.assertEqual(icon_for("bucket", False), PLAIN)
        self.assertEqual(icon_for("bucket", True), SECRET)

    def test_summary_skips_empty_fields_and_repeats(self):
        fields = [Field("login", "me"), Field("password", "x", True), Field("site", ""), Field("mail", "a@b"), Field("login", "you")]
        icons, names = summary(fields)
        self.assertEqual(icons, "👤 🔑 ✉")
        self.assertEqual(names, ["login", "password", "mail", "login"])


if __name__ == "__main__":
    unittest.main()
