"""The checklist of a note card on one canvas (notes_view.ItemList): the items are canvas
items, not widgets; a click opens an editor over the row; the editor's keys move between
items; ticking, removing, adding and folding redraw the lists. Skipped without a display.
Key events need the focus, which a hidden window does not get, so the keyboard paths are
driven through the methods the bindings call."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from daykeeper.journal import today
from daykeeper.settings import write_settings
from daykeeper.widgets import WrapLine
from tests.helpers import has_display, hide, reset_globals

PASSWORD = "checklist-master-1"


@unittest.skipUnless(has_display(), "no display")
class NotesChecklistTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "notes.pkv"
        write_settings(self.path.parent / "settings.json", {"language": "en", "geometry": "1200x760+20+20"})
        from daykeeper.app import App

        self.app = App(self.path, preview=True)
        hide(self.app)
        self.errors: list[str] = []
        self.app.report_callback_exception = lambda *exc: self.errors.append(repr(exc[1]))
        self.app.unlock(PASSWORD)
        self.note = self.app.vault.notes.add(today(), title="list", text="t")
        for i in range(4):
            self.note.add_item(f"item {i}" + (" and a long tail that wraps onto a second line of the card for sure" if i == 1 else ""))
        self.note.toggle(self.note.items[3])
        self.app.session.touch("notes")
        self.app.show_page("notes")
        self.app.finish_chunks("notes")
        self.pump()
        self.page = self.app.pages["notes"]
        self.card = self.page._ncards[self.note.id]
        self.open, self.done = self.card.open_list, self.card.done_list

    def tearDown(self) -> None:
        try:
            self.app.destroy()
        finally:
            self.tmp.cleanup()
            reset_globals()

    def pump(self) -> None:
        self.app.update_idletasks()
        self.app.update()

    def row_middle(self, lst, i: int) -> int:
        y0, y1 = lst.rows[i]
        return (y0 + y1) // 2

    def test_items_are_canvas_items_not_widgets(self):
        self.assertEqual(len(self.open.rows), 3)
        self.assertEqual(len(self.done.rows), 1)
        self.assertEqual([w for w in self.open.winfo_children()], [], "no widget per item")
        self.assertEqual(self.open.itemcget("t1", "text"), self.note.items[1].text)
        first, second = (y1 - y0 for y0, y1 in self.open.rows[:2])
        self.assertGreater(second, first, "a long item wraps and its row is taller")
        self.assertEqual(self.errors, [])

    def test_a_click_opens_the_editor_and_typing_edits_the_item(self):
        self.open.event_generate("<Button-1>", x=60, y=self.row_middle(self.open, 0))
        self.pump()
        editor = self.open.editor
        self.assertIsInstance(editor, WrapLine)
        self.assertEqual(self.open.editing, 0)
        self.assertEqual(str(self.open.itemcget("t0", "state")), "hidden", "the canvas text hides under the editor")
        editor.insert("end", "!")
        self.pump()
        self.assertEqual(self.note.items[0].text, "item 0!")
        self.open.close()
        self.pump()
        self.assertIsNone(self.open.editor)
        self.assertEqual(self.open.itemcget("t0", "text"), "item 0!")
        self.assertEqual(self.errors, [])

    def test_tab_moves_the_editor_between_items_and_past_the_last_to_the_plus_row(self):
        self.open.edit(0)
        self.open._neighbour(+1)
        self.assertEqual(self.open.editing, 1)
        self.assertTrue(self.open.editor.value().startswith("item 1"))
        self.open._neighbour(-1)
        self.assertEqual(self.open.editing, 0)
        self.open._neighbour(+1)
        self.open._neighbour(+1)
        self.open._neighbour(+1)  # past the last item
        self.assertIsNone(self.open.editor)
        self.assertEqual(self.errors, [])

    def test_the_check_box_ticks_and_the_cross_removes(self):
        self.open.event_generate("<Button-1>", x=8, y=self.row_middle(self.open, 2))
        self.pump()
        self.assertTrue(self.note.items[2].done)
        self.assertEqual((len(self.open.rows), len(self.done.rows)), (2, 2))
        x = self.open.winfo_width() - 5
        self.open.event_generate("<Motion>", x=x, y=self.row_middle(self.open, 1))
        self.open.event_generate("<Button-1>", x=x, y=self.row_middle(self.open, 1))
        self.pump()
        self.assertEqual([i.text for i in self.note.open_items()], ["item 0"])
        self.assertEqual(len(self.open.rows), 1)
        self.assertEqual(self.errors, [])

    def test_adding_backspace_and_moving_redraw_the_lists(self):
        self.card.add_var.set("new one")
        self.page._note_add_item(self.card, self.note, self.card.add_var)
        self.pump()
        self.assertEqual([i.text for i in self.note.open_items()][-1], "new one")
        self.assertEqual(len(self.open.rows), 4)
        self.open.edit(3)
        self.open.editor.delete("1.0", "end")
        self.pump()
        self.page._note_backspace_item(self.card, self.note, self.note.open_items()[3], self.open.editor)
        self.pump()
        self.assertEqual(len(self.note.open_items()), 3, "the emptied item is gone")
        self.assertEqual(self.open.editing, 2, "the one above is being edited")
        self.open.close()
        self.open.edit(0)
        first = self.note.open_items()[0]
        self.page._note_move_item(self.card, self.note, first, +1)
        self.pump()
        self.assertIs(self.note.open_items()[1], first)
        self.assertEqual(self.open.editing, 1, "the moved item keeps its editor")
        self.open.close()
        self.assertEqual(self.errors, [])

    def test_folding_hides_the_done_list_and_the_card_grows_with_items(self):
        self.page._note_fold_done(self.card, self.note)
        self.pump()
        self.assertFalse(self.done.winfo_ismapped())
        self.assertEqual(self.done.rows, [])
        self.page._note_fold_done(self.card, self.note)
        self.pump()
        self.assertTrue(self.done.winfo_ismapped())
        self.assertEqual(len(self.done.rows), 1)
        before = self.card.winfo_reqheight()
        for i in range(5):
            self.note.add_item(f"more {i}")
        self.page._render_checklist(self.card, self.note)
        self.pump()
        self.assertGreater(self.card.winfo_reqheight(), before)
        self.assertEqual(self.errors, [])

    def test_keyboard_pick_and_edit(self):
        self.open._select(0)
        self.assertEqual(self.open.sel, 0)
        self.assertTrue(self.open.find_withtag("s0"), "the picked row has a ring")
        self.open._select(1)
        self.open.edit(self.open.sel)
        self.assertEqual(self.open.editing, 1)
        self.open.close()
        self.assertEqual(self.errors, [])

    def test_the_text_grows_only_when_its_height_changes(self):
        text = self.card.text_widget
        text.insert("end", "\nmore\nlines")
        self.page._grow_text(text)
        self.assertEqual(int(text.cget("height")), 4)
        seen: list[str] = []
        original = text.config

        def spy(*args, **kw):
            seen.append(repr(kw))
            return original(*args, **kw)

        text.config = spy  # type: ignore[method-assign]
        self.page._grow_text(text)
        self.assertEqual(seen, [], "the same height is not set again: Tk would lay the whole text out anew")


if __name__ == "__main__":
    unittest.main()
