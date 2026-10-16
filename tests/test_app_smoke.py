"""The whole window on a temporary vault: unlock, every tab, one item on each, save, lock.
Skipped without a display. The safety net for the refactoring: it goes through the public
entry points of the window and checks the file that comes out."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from daykeeper.journal import today
from daykeeper.security import GUARD_MASTER
from daykeeper.settings import write_settings
from daykeeper.vault import Vault
from tests.helpers import has_display, reset_globals

PASSWORD = "smoke-master-1"
TABS = ("vault", "bookmarks", "journal", "notes", "routines", "integrations")


@unittest.skipUnless(has_display(), "no display")
class AppSmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "smoke.pkv"
        write_settings(self.path.parent / "settings.json", {"language": "en", "geometry": "1200x760+20+20"})
        from daykeeper.app import App

        self.app = App(self.path, preview=True)
        self.app.withdraw()
        self.errors: list[str] = []
        self.app.report_callback_exception = lambda *exc: self.errors.append(repr(exc[1]))

    def tearDown(self) -> None:
        try:
            self.app.destroy()
        finally:
            self.tmp.cleanup()
            reset_globals()

    def pump(self, n: int = 3) -> None:
        """Let the window run its pending jobs (a tab built in slices finishes between repaints)."""
        for _ in range(n):
            self.app.update()
            self.app.after(5)
        self.app.update()

    def close_toplevels(self) -> int:
        n = 0
        for w in self.app.winfo_children():
            if w.winfo_class() == "Toplevel":
                w.destroy()
                n += 1
        self.app.update()
        return n

    def test_unlock_every_tab_save_and_lock(self) -> None:
        app = self.app
        app.unlock(PASSWORD)
        self.pump()
        self.assertIsNotNone(app.vault, "the vault was created and opened")
        self.assertTrue(self.path.exists())

        for tab in TABS:
            app.show_page(tab)
            self.pump()
        self.assertEqual(self.errors, [])

        app.show_page("vault")
        app.pages["vault"].new_item()
        app.pages["vault"].name_var.set("Smoke entry")
        self.pump()

        app.show_page("notes")
        app.pages["notes"].new_item()
        self.pump()
        self.assertEqual(len(app.vault.notes.notes), 1)

        app.show_page("journal")
        app.pages["journal"]._add_today()
        app.pages["journal"].jquick_var.set("APP-1")
        app.pages["journal"]._quick_add("ok")
        self.pump()

        app.show_page("bookmarks")
        app.vault.bookmarks.add("Example", "example.com")
        app.session.touch("bookmarks")
        app.show_page("vault")
        app.show_page("bookmarks")
        self.pump()
        app.pages["bookmarks"].new_item()
        self.pump()
        self.assertEqual(self.close_toplevels(), 1, "the bookmark editor opened")

        app.show_page("routines")
        app.vault.routines.add(name="Example", kind="url", target="https://example.com")
        app.session.touch("routines")
        app.show_page("vault")
        app.show_page("routines")
        self.pump()
        app.pages["routines"].new_item()
        self.pump()
        self.assertEqual(self.close_toplevels(), 1, "the routine editor opened")

        app.show_page("integrations")
        self.pump()
        app.settings_dialog()
        self.pump()
        self.assertEqual(self.close_toplevels(), 1, "the settings window opened")
        self.assertEqual(self.errors, [])

        app.save_now()
        saved = Vault.open(self.path, PASSWORD)
        self.assertEqual([e.name for e in saved.entries], ["Smoke entry"])
        self.assertEqual(len(saved.notes.notes), 1)
        self.assertEqual([b.title for b in saved.bookmarks.items], ["Example"])
        self.assertEqual([r.name for r in saved.routines.ordered()], ["Example"])
        self.assertEqual(saved.journal.get(today()).section("ok"), ["APP-1"])

        out = self.path.parent / "export.txt"
        self.assertFalse(app.session.export("txt", out, "not-the-password"))
        self.assertFalse(out.exists(), "a wrong master password exports nothing")
        self.assertTrue(app.session.export("txt", out, PASSWORD))
        self.assertIn("Smoke entry", out.read_text(encoding="utf-8"))

        app.vault.security.vault_guard = GUARD_MASTER
        app.show_page("vault")
        app.security.lock_secrets()
        self.pump()
        self.assertFalse(app.security.secrets_open(), "the Passwords tab is behind its guard")
        app.show_page("bookmarks")
        app.show_page("vault")
        app.security.unlock_secrets()
        self.pump()
        self.assertTrue(app.security.secrets_open())
        self.assertEqual(self.errors, [])

        app.lock()
        self.pump()
        self.assertIsNone(app.vault)
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
