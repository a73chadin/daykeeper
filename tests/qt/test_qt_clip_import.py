"""The clipboard history of the Qt window (the watch, the top-bar button, the history
window, the settings) and the import from KeePass / Bitwarden (Settings → Data).

The real clipboard is never touched: the window's Qt clipboard is the fake of
open_window, and the watch's Windows functions (change counter, the private mark, the
text) are fakes put on the watch here."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import reset_globals
from tests.qt_helpers import close_window, open_window, pump, qt_app, require_qt, type_text

require_qt()

import shiboken6  # noqa: E402

from daykeeper import importers  # noqa: E402
from daykeeper.qt.dialogs import import_other  # noqa: E402

PASSWORD = "test-master-1"
TOKEN = "glpat-demoTokenDemo0000"  # the shape of a token, not a real one


def setUpModule():
    qt_app()


class FakeSystem:
    """The Windows side of the clipboard as the watch sees it."""

    def __init__(self):
        self.seq = 1
        self.text: str | None = None
        self.marked = False
        self.reads = 0

    def copy(self, text: str, marked: bool = False) -> None:
        self.seq += 1
        self.text, self.marked = text, marked

    def read(self) -> str | None:
        self.reads += 1
        return self.text


class ClipHistoryTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.win = open_window(self.tmp.name, password=PASSWORD)
        self.win.background_jobs = True  # open_window makes a preview copy, which records nothing
        self.sys = FakeSystem()
        watch = self.win.clip
        watch.windows = True
        watch.sequence_number = lambda: self.sys.seq
        watch.excluded = lambda: self.sys.marked
        watch.read_system = self.sys.read
        self.now = 1_760_000_000.0
        watch.clock = lambda: self.now

    def tearDown(self):
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()

    def turn_on(self, skip_secrets: bool = True, limit: int = 20) -> None:
        prefs = self.win.vault.security
        prefs.clip_history, prefs.clip_skip_secrets, prefs.clip_limit = True, skip_secrets, limit
        self.win.clip.refresh()

    def texts(self) -> list[str]:
        v = self.win.vault
        return [i.text(v.clip_sealer) for i in v.clipboard.items]

    def test_a_preview_copy_records_nothing(self):
        self.win.background_jobs = False
        self.turn_on()
        self.assertFalse(self.win.clip._ticker.running)
        self.sys.copy("hello")
        self.win.clip.tick()
        self.assertEqual(self.texts(), [])

    def test_off_by_default_records_nothing(self):
        self.assertFalse(self.win.vault.security.clip_history)
        self.assertFalse(self.win.clip._ticker.running)
        self.assertTrue(self.win.main_screen.clip_btn.isHidden())
        self.sys.copy("hello")
        self.win.clip.tick()
        self.assertEqual(self.texts(), [])
        self.assertEqual(self.sys.reads, 0, "the clipboard is not even read while the history is off")

    def test_records_a_changed_text_and_reads_only_on_a_change(self):
        self.sys.copy("there before the history was on")
        self.turn_on()
        self.assertTrue(self.win.clip._ticker.running)
        self.assertFalse(self.win.main_screen.clip_btn.isHidden())
        self.win.clip.tick()
        self.assertEqual(self.texts(), [], "what was on the clipboard before counts as seen")
        self.sys.copy("first copied text")
        self.win.clip.tick()
        self.assertEqual(self.texts(), ["first copied text"])
        self.assertTrue(self.win._save.pending, "saved soon")
        reads = self.sys.reads
        self.win.clip.tick()
        self.assertEqual(self.sys.reads, reads, "the counter did not move: nothing read")
        self.assertFalse(self.win.history.can_undo or self.win.history.capture(), "not an undo step")

    def test_skips_marked_own_secret_and_token_looking(self):
        self.turn_on(skip_secrets=True)
        self.sys.copy("from another password manager", marked=True)
        self.win.clip.tick()
        self.sys.copy(TOKEN)
        self.win.clip.tick()
        self.win.copy_to_clipboard("demo-pass-1", secret=True)  # Daykeeper's own secret copy
        self.sys.copy("demo-pass-1")  # as if the mark were lost (macOS has none)
        self.win.clip.tick()
        self.assertEqual(self.texts(), [])
        self.win.vault.security.clip_skip_secrets = False
        self.sys.copy(TOKEN)
        self.win.clip.tick()
        self.assertEqual(self.texts(), [TOKEN], "recorded once that setting is off")

    def test_the_slow_way_reads_the_qt_clipboard(self):
        watch = self.win.clip
        watch.windows = False
        self.turn_on()
        self.win.fake_clip.text = "plain text from a browser"
        watch.tick()
        self.assertEqual(self.texts(), [], "once a second: the first tick waits")
        watch.tick()
        self.assertEqual(self.texts(), ["plain text from a browser"])
        self.win.copy_to_clipboard("demo-pass-2", secret=True)
        watch.tick()
        watch.tick()
        self.assertEqual(self.texts(), ["plain text from a browser"], "its own secret copy is known by its hash")
        self.assertEqual(self.sys.reads, 0, "no Windows call on the slow way")

    def test_nothing_while_locked_or_paused(self):
        self.turn_on()
        self.win.clip.set_paused(True)
        self.sys.copy("during the pause")
        self.win.clip.tick()
        self.win.clip.set_paused(False)
        self.win.clip.tick()
        self.assertEqual(self.texts(), [], "what was copied during the pause is not picked up after it")
        vault = self.win.vault
        self.win.lock()
        self.assertFalse(self.win.clip._ticker.running)
        self.sys.copy("while locked")
        self.win.clip.tick()
        self.assertEqual(vault.clipboard.items, [])
        self.win.unlock(PASSWORD)
        pump()
        self.assertTrue(self.win.clip._ticker.running)
        self.win.clip.tick()
        self.assertEqual(self.texts(), [], "copied while locked: not recorded after the unlock either")

    def test_the_window_copies_back_pins_deletes_and_clears(self):
        self.turn_on(skip_secrets=False)
        for text in ("one", "two\nsecond line", TOKEN):
            self.sys.copy(text)
            self.win.clip.tick()
            self.now += 60
        dlg = self.win.show_clip_history()
        pump()
        rows = dlg.rows()
        self.assertEqual([r.title.text() for r in rows], [TOKEN[:4] + "••••••••", "two", "one"], "newest first; a token is masked")
        self.assertIn("15 chars", rows[1].meta.text())
        dlg.copy_back(rows[2].item)
        self.assertEqual(self.win.fake_clip.text, "one")
        self.sys.copy("one")
        self.win.clip.tick()
        self.assertEqual(len(self.win.vault.clipboard.items), 3, "a text put back is not recorded again")
        self.win._clip_wipe.cancel()
        dlg.copy_back(rows[0].item)
        self.assertEqual(self.win.fake_clip.text, TOKEN)
        self.assertTrue(self.win._clip_wipe.pending, "a token-looking text goes back as a secret copy, wiped later")
        self.win._clip_wipe.cancel()
        type_text(dlg.search, "two")
        self.assertEqual(len(dlg.rows()), 1)
        dlg.search.clear()
        dlg.toggle_pin(dlg.rows()[2].item)
        self.assertTrue(self.win.vault.clipboard.items[2].pinned)
        dlg.delete(dlg.rows()[1].item)
        self.assertEqual(self.texts(), [TOKEN, "one"])
        dlg.clear_all()
        self.assertEqual(self.texts(), ["one"], "the pinned text stays")
        self.win.lock()
        pump()
        self.assertFalse(shiboken6.isValid(dlg) and dlg.isVisible(), "closes on lock")

    def guard_down(self) -> None:
        from daykeeper.security import GUARD_PIN

        prefs = self.win.vault.security
        prefs.vault_guard = GUARD_PIN
        prefs.set_pin("2468")
        self.win.security.lock_secrets()

    def test_a_token_goes_back_only_after_the_guard_and_a_refusal_hides_nothing(self):
        self.turn_on(skip_secrets=False)
        self.sys.copy(TOKEN)
        self.win.clip.tick()
        dlg = self.win.show_clip_history()
        pump()
        row = dlg.rows()[0]
        self.guard_down()
        with mock.patch("daykeeper.qt.lock.ask_secret") as prompt:
            dlg.copy_back(row.item)
        prompt.assert_called_once()
        self.assertIsNone(self.win.fake_clip.text, "nothing copied before the code")
        self.assertIsNone(self.win.clip._ignore, "a refused copy marks nothing")
        self.now += 60
        self.win.vault.clipboard.clear(keep_pinned=False)
        self.sys.copy(TOKEN)  # the user copies the same text elsewhere: it is recorded
        self.win.clip.tick()
        self.assertEqual(self.texts(), [TOKEN])
        accept, then = prompt.call_args.args[4], prompt.call_args.args[5]
        self.assertIs(accept("2468"), True)
        then()
        self.assertEqual(self.win.fake_clip.text, TOKEN)
        self.assertTrue(self.win._clip_wipe.pending, "a secret copy, wiped later")
        self.win._clip_wipe.cancel()

    def test_the_history_window_asks_the_guard_first(self):
        self.turn_on()
        self.guard_down()
        with mock.patch("daykeeper.qt.lock.ask_secret") as prompt:
            self.assertIsNone(self.win.show_clip_history())
        prompt.assert_called_once()
        accept, then = prompt.call_args.args[4], prompt.call_args.args[5]
        self.assertIs(accept("2468"), True)
        then()
        pump()
        dlg = self.win.clip.dialog
        self.assertTrue(dlg is not None and dlg.isVisible(), "opened after the code")

    def test_settings_switch_it_on_and_off(self):
        self.win.settings_dialog("security")
        pump()
        sw = self.win._settings_win
        gate = sw.pages["security"].gate
        type_text(gate["password"], PASSWORD)
        gate["submit"]()
        parts = sw.pages["security"].parts
        parts["clip_on"].setChecked(True)
        parts["clip_limit"].setCurrentIndex(2)
        parts["clip_skip"].setChecked(False)
        parts["save"]()
        prefs = self.win.vault.security
        self.assertEqual((prefs.clip_history, prefs.clip_limit, prefs.clip_skip_secrets), (True, 100, False))
        self.assertFalse(self.win.main_screen.clip_btn.isHidden())
        self.sys.copy("kept")
        self.win.clip.tick()
        self.assertEqual(self.texts(), ["kept"])
        parts["clip_on"].setChecked(False)
        parts["save"]()
        self.assertFalse(self.win.clip._ticker.running)
        self.assertTrue(self.win.main_screen.clip_btn.isHidden())
        self.assertEqual(self.win.vault.clipboard.items, [], "switched off: forgotten")


KEEPASS_XML = """<?xml version="1.0" encoding="utf-8"?>
<KeePassFile>
  <Root>
    <Group>
      <Name>Database</Name>
      <Entry>
        <String><Key>Title</Key><Value>Demo mail</Value></String>
        <String><Key>UserName</Key><Value>demo@example.com</Value></String>
        <String><Key>Password</Key><Value ProtectInMemory="True">demo-pass-1</Value></String>
      </Entry>
      <Group>
        <Name>Work</Name>
        <Entry>
          <String><Key>Title</Key><Value>Demo wiki</Value></String>
          <String><Key>Password</Key><Value ProtectInMemory="True">demo-pass-2</Value></String>
        </Entry>
      </Group>
    </Group>
  </Root>
</KeePassFile>
"""

BITWARDEN = {
    "encrypted": False,
    "folders": [{"id": "f1", "name": "Work"}],
    "items": [
        {"id": "i1", "folderId": "f1", "type": 1, "name": "Demo wiki",
         "login": {"username": "demo", "password": "demo-pass-2", "uris": [{"uri": "https://wiki.example.com"}]}},
        {"id": "i2", "folderId": None, "type": 2, "name": "Demo note", "notes": "plain words", "secureNote": {"type": 0}},
    ],
}


class ImportOtherTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.win = open_window(self.tmp.name, password=PASSWORD)
        self.folder = Path(self.tmp.name)

    def tearDown(self):
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()

    def write(self, name: str, text: str) -> Path:
        path = self.folder / "exports" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def entry(self, name: str):
        return next(e for e in self.win.vault.entries if e.name == name)

    def copies(self) -> list[Path]:
        return [p for p, kind in self.win.session.copies() if kind == "import"]

    def test_keepass_xml_into_the_vault_backup_and_file_deleted(self):
        before = len(self.win.vault.entries)
        source = self.write("kp.xml", KEEPASS_XML)
        dlg = import_other.import_other_dialog(self.win, source=str(source))
        self.assertIn("entries: +2", dlg.parts["summary"].text())
        self.assertTrue(dlg.parts["merge"].isHidden(), "no matches: no choice")
        self.assertTrue(dlg.parts["remove"].isChecked(), "delete the export: on by default")
        dlg.parts["apply"]()
        self.assertEqual(len(self.win.vault.entries), before + 2)
        self.assertIn("Work", self.win.vault.groups)
        self.assertEqual(self.entry("Demo wiki").group, "Work")
        self.assertEqual(len(self.copies()), 1, "the vault as it was is kept beside it")
        self.assertFalse(source.exists(), "the export is deleted")
        self.assertIn("entries: 2, fields: 3", self.win.main_screen.status.text())
        pump(800)
        self.assertTrue(self.win.history.can_undo, "an import is one undo step")

    def test_bitwarden_json_skip_versus_merge(self):
        self.win.vault.add_group("Work")
        wiki = self.win.vault.add_entry("Demo wiki", group="Work")
        wiki.fields = []
        source = self.write("bw.json", json.dumps(BITWARDEN))
        dlg = import_other.import_other_dialog(self.win, source=str(source))
        self.assertFalse(dlg.parts["merge"].isHidden(), "a match: the choice is offered")
        self.assertIn("skipped 1", dlg.parts["summary"].text())
        dlg.parts["merge"].setChecked(True)
        self.assertIn("extended 1", dlg.parts["summary"].text())
        dlg.parts["remove"].setChecked(False)
        dlg.parts["apply"]()
        values = {f.key: f.value for f in self.entry("Demo wiki").fields}
        self.assertIn("demo-pass-2", values.values(), "merge: the missing fields were added")
        self.assertEqual(sum(e.name == "Demo wiki" for e in self.win.vault.entries), 1, "not duplicated")
        self.assertTrue(source.exists(), "kept when unticked")

    def assert_wiped(self, dlg, plan, items, apply_fn) -> None:
        self.assertEqual((plan.new, plan.matches), ([], []))
        self.assertTrue(all(not i.fields and not i.note for i in items), "no plain value left on an item")
        self.assertNotIn("plan", dlg.parts)
        self.assertNotIn("apply", dlg.parts)
        for cell in apply_fn.__closure__ or ():
            value = cell.cell_contents
            if isinstance(value, dict):
                self.assertEqual(value, {}, "the state is emptied")
            if isinstance(value, list):
                self.assertFalse([x for x in value if isinstance(x, importers.Imported) and x.fields])

    def captured(self, dlg):
        plan = dlg.parts["plan"]()
        items = plan.new + [m.item for m in plan.matches]
        self.assertTrue(any(i.fields for i in items))
        return plan, items, dlg.parts["apply"]

    def test_after_import_no_plain_value_is_kept(self):
        self.win.vault.add_group("Work")
        self.win.vault.add_entry("Demo wiki", group="Work").fields = []
        source = self.write("bw.json", json.dumps(BITWARDEN))
        dlg = import_other.import_other_dialog(self.win, source=str(source))
        dlg.parts["merge"].setChecked(True)
        plan, items, apply_fn = self.captured(dlg)
        apply_fn()
        self.assertIn("demo-pass-2", [f.value for f in self.entry("Demo wiki").fields], "imported first")
        self.assert_wiped(dlg, plan, items, apply_fn)
        apply_fn()  # Enter once more after the end does nothing
        self.assertEqual(sum(e.name == "Demo note" for e in self.win.vault.entries), 1)

    def test_after_cancel_no_plain_value_is_kept(self):
        before = len(self.win.vault.entries)
        source = self.write("kp.xml", KEEPASS_XML)
        dlg = import_other.import_other_dialog(self.win, source=str(source))
        plan, items, apply_fn = self.captured(dlg)
        dlg.reject()
        self.assert_wiped(dlg, plan, items, apply_fn)
        self.assertEqual(len(self.win.vault.entries), before)
        self.assertTrue(source.exists(), "cancel deletes nothing")

    def test_bitwarden_skip_leaves_the_match_alone(self):
        self.win.vault.add_group("Work")
        wiki = self.win.vault.add_entry("Demo wiki", group="Work")
        wiki.fields = []
        source = self.write("bw.json", json.dumps(BITWARDEN))
        dlg = import_other.import_other_dialog(self.win, source=str(source))
        dlg.parts["apply"]()
        self.assertEqual(self.entry("Demo wiki").fields, [])
        self.assertEqual(self.entry("Demo note").note, "plain words")

    def test_an_oversized_file_is_refused_before_reading(self):
        source = self.write("big.json", "{}")
        with mock.patch.object(importers, "MAX_BYTES", 1), mock.patch.object(import_other, "show_error") as told, \
                mock.patch.object(Path, "read_bytes", side_effect=AssertionError("read")) as read:
            dlg = import_other.import_other_dialog(self.win, source=str(source))
        self.assertIsNone(dlg)
        read.assert_not_called()
        self.assertIn("larger than", told.call_args[0][1])

    def test_a_foreign_file_is_told_as_is(self):
        source = self.write("x.xml", "<html></html>")
        with mock.patch.object(import_other, "show_error") as told:
            self.assertIsNone(import_other.import_other_dialog(self.win, source=str(source)))
        self.assertIn("not a KeePass or Bitwarden export", told.call_args[0][1])
        source = self.write("enc.json", json.dumps({"encrypted": True, "items": []}))
        with mock.patch.object(import_other, "show_error") as told:
            self.assertIsNone(import_other.import_other_dialog(self.win, source=str(source)))
        self.assertIn("encrypted", told.call_args[0][1])

    def test_the_data_section_has_the_button(self):
        self.win.settings_dialog("data")
        pump()
        from PySide6.QtWidgets import QPushButton

        names = [b.text() for b in self.win._settings_win.pages["data"].findChildren(QPushButton)]
        self.assertIn("Import from KeePass or Bitwarden…", names)


if __name__ == "__main__":
    unittest.main()
