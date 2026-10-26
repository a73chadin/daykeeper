import tempfile
import unittest
from pathlib import Path

from daykeeper.journal import today
from daykeeper.notes import COLOR_BG, Note, NoteItem, Notes, week_days, week_start
from daykeeper.vault import Vault


class NoteTest(unittest.TestCase):
    def test_items_progress_and_done(self):
        n = Note(title="Release")
        self.assertEqual(n.progress(), (0, 0))
        self.assertFalse(n.is_done())
        a = n.add_item("  build exe ")
        b = n.add_item("check login")
        self.assertIsNone(n.add_item("   "))
        self.assertEqual([i.text for i in n.items], ["build exe", "check login"])
        n.toggle(a)
        self.assertEqual(n.progress(), (1, 2))
        self.assertFalse(n.is_done())
        n.toggle(b, done=True)
        self.assertTrue(n.is_done())
        n.toggle(b)
        self.assertEqual(n.undone_items(), [b])
        n.remove_item(a)
        self.assertEqual(n.items, [b])

    def test_empty_and_matches(self):
        n = Note()
        self.assertTrue(n.is_empty())
        n.text = "Call the plumber about the kitchen tap"
        self.assertFalse(n.is_empty())
        self.assertTrue(n.matches("plumber"))
        self.assertTrue(n.matches(""))
        self.assertFalse(n.matches("jira"))
        n.add_item("APP-4161 retest")
        self.assertTrue(n.matches("app-4161"))

    def test_roundtrip_and_bad_color(self):
        n = Note(date="2026-10-12", title="t", text="x", pinned=True, color="green")
        n.add_item("a", done=True)
        d = n.to_dict()
        back = Note.from_dict(d)
        self.assertEqual(back.id, n.id)
        self.assertEqual(back.date, "2026-10-12")
        self.assertTrue(back.pinned)
        self.assertEqual(back.bg(), COLOR_BG["green"])
        self.assertEqual(back.items, [NoteItem("a", True)])
        d["color"] = "neon"
        self.assertEqual(Note.from_dict(d).color, "")
        self.assertEqual(Note.from_dict({}).date, today())


class NotesTest(unittest.TestCase):
    def setUp(self):
        self.ns = Notes()
        self.old_done = self.ns.add("2026-10-10", title="old done", created=1.0)
        self.old_done.add_item("x", done=True)
        self.old_open = self.ns.add("2026-10-10", title="old open", created=2.0)
        self.old_open.add_item("y")
        self.old_open.add_item("z", done=True)
        self.pinned = self.ns.add("2026-10-10", title="pinned", created=3.0, pinned=True)
        self.t1 = self.ns.add("2026-10-12", title="today's", created=4.0)

    def test_dates_and_day_sort(self):
        self.assertEqual(self.ns.dates(), ["2026-10-12", "2026-10-10"])
        order = [n.title for n in self.ns.for_day("2026-10-10")]
        self.assertEqual(order, ["pinned", "old open", "old done"])
        self.assertEqual(self.ns.day_progress("2026-10-10"), (2, 3))
        self.assertEqual(self.ns.day_progress("2026-10-12"), (0, 0))

    def test_search_and_unfinished(self):
        self.assertEqual([n.title for n in self.ns.search("old")], ["old open", "old done"])
        self.assertEqual([n.title for n in self.ns.unfinished()], ["old open"])
        self.assertEqual(len(self.ns.search("")), 4)

    def test_carry_over(self):
        self.assertIsNone(self.ns.carry_over(self.old_done))
        copy = self.ns.carry_over(self.old_open, "2026-10-13")
        self.assertIsNotNone(copy)
        self.assertEqual(copy.date, "2026-10-13")
        self.assertEqual(copy.title, "old open")
        self.assertEqual([(i.text, i.done) for i in copy.items], [("y", False)])
        self.assertEqual(self.old_open.progress(), (1, 2))  # the source is untouched
        self.assertIn("2026-10-13", self.ns.dates())

    def test_remove_get_drop_empty(self):
        self.assertIs(self.ns.get(self.t1.id), self.t1)
        self.ns.remove(self.t1)
        self.assertIsNone(self.ns.get(self.t1.id))
        self.ns.add("2026-10-13")
        self.ns.add("2026-10-13", text=" ")
        self.assertEqual(self.ns.drop_empty(), 2)
        self.assertEqual(len(self.ns.notes), 3)

    def test_from_list_skips_junk(self):
        ns = Notes.from_list([{"title": "ok"}, "junk", 5])
        self.assertEqual([n.title for n in ns.notes], ["ok"])


class VaultNotesTest(unittest.TestCase):
    def test_notes_survive_save_and_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v.pkv"
            v = Vault.create(path, "master-pass-1")
            n = v.notes.add(title="plan", color="blue")
            n.add_item("one")
            n.add_item("two", done=True)
            v.save()
            v2 = Vault.open(path, "master-pass-1")
            self.assertEqual(len(v2.notes.notes), 1)
            got = v2.notes.notes[0]
            self.assertEqual(got.title, "plan")
            self.assertEqual(got.color, "blue")
            self.assertEqual(got.progress(), (1, 2))


if __name__ == "__main__":
    unittest.main()


class ChecklistOrderTest(unittest.TestCase):
    def test_move_item_stays_within_its_state(self):
        n = Note()
        a, b, c = n.add_item("a"), n.add_item("b"), n.add_item("c")
        n.toggle(b)
        self.assertEqual(n.open_items(), [a, c])
        self.assertTrue(n.move_item(c, -1))
        self.assertEqual(n.open_items(), [c, a])
        self.assertEqual(n.done_items(), [b])
        self.assertFalse(n.move_item(c, -1))
        self.assertFalse(n.move_item(b, +1))

    def test_clear_done(self):
        n = Note()
        n.add_item("a")
        n.toggle(n.add_item("b"))
        self.assertEqual(n.clear_done(), 1)
        self.assertEqual([i.text for i in n.items], ["a"])
        self.assertEqual(n.clear_done(), 0)


class AttachmentTest(unittest.TestCase):
    def test_attach_once_and_detach(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "Plan.md"
            f.write_text("# plan", encoding="utf-8")
            n = Note()
            att = n.attach(f)
            self.assertIsNotNone(att)
            self.assertIsNone(n.attach(str(f)))
            self.assertEqual((att.name, att.suffix, att.exists(), att.is_text()), ("Plan.md", ".md", True, True))
            self.assertFalse(n.is_empty())
            self.assertTrue(n.matches("plan.md"))
            n.detach(att)
            self.assertEqual(n.attachments, [])
            self.assertTrue(n.is_empty())

    def test_missing_file_is_kept_but_reported(self):
        n = Note()
        att = n.attach("Z:/nowhere/x.pdf")
        self.assertFalse(att.exists())
        self.assertFalse(att.is_text())

    def test_round_trip_and_older_files(self):
        n = Note(title="t", color="purple", span=2, hide_done=True)
        n.attach("C:/docs/a.md")
        back = Note.from_dict(n.to_dict())
        self.assertEqual((back.color, back.wide, back.hide_done), ("purple", True, True))
        self.assertEqual([a.path for a in back.attachments], [n.attachments[0].path])
        old = Note.from_dict({"title": "old", "color": "nonsense"})
        self.assertEqual((old.color, old.wide, old.hide_done, old.attachments), ("", False, False, []))

    def test_with_files_and_carry_over_keeps_files(self):
        notes = Notes()
        a = notes.add("2026-01-01", title="spec")
        a.attach("C:/docs/spec.md")
        a.add_item("read")
        notes.add("2026-01-02", title="plain")
        self.assertEqual(notes.with_files(), [a])
        copy = notes.carry_over(a, "2026-01-03")
        self.assertEqual([x.path for x in copy.attachments], [x.path for x in a.attachments])
        self.assertIsNot(copy.attachments[0], a.attachments[0])


class WeekPlansTest(unittest.TestCase):
    def test_week_start_and_days(self):
        self.assertEqual(week_start("2026-10-29"), "2026-10-26")
        self.assertEqual(week_start("2026-10-26"), "2026-10-26")
        self.assertEqual(week_start("2026-11-01"), "2026-10-26", "Sunday ends the week")
        self.assertEqual(week_start("not a date"), "not a date")
        days = week_days("2026-12-30")
        self.assertEqual((days[0], days[-1], len(days)), ("2026-12-28", "2027-01-03", 7), "a week across the new year")

    def test_a_plan_belongs_to_the_week_not_to_a_day(self):
        ns = Notes()
        plan = ns.add("2026-10-29", week=True, title="week plan")
        plan.add_item("ship 2.3")
        day = ns.add("2026-10-26", title="monday")
        day.add_item("x", done=True)
        self.assertEqual(plan.date, "2026-10-26", "kept on the Monday")
        self.assertEqual(ns.for_day("2026-10-26"), [day])
        self.assertEqual(ns.dates(), ["2026-10-26"])
        self.assertEqual(ns.for_week("2026-11-01"), [plan])
        self.assertEqual(ns.day_progress("2026-10-26"), (1, 1), "a plan does not count in its Monday")
        self.assertEqual(ns.week_progress("2026-10-28"), (0, 1))
        ns.add("2026-11-03", title="next week")
        self.assertEqual(ns.weeks(), ["2026-11-02", "2026-10-26"])
        self.assertEqual(ns.unfinished(), [plan])
        self.assertEqual([n.title for n in ns.search("")], ["next week", "week plan", "monday"], "a week's plans before its days")

    def test_round_trip_and_carry_over_to_this_week(self):
        ns = Notes()
        plan = ns.add("2026-10-12", week=True, title="old plan")
        plan.add_item("left over")
        back = Note.from_dict(plan.to_dict())
        self.assertTrue(back.week)
        self.assertFalse(Note.from_dict({"title": "older file"}).week)
        self.assertEqual(Note.from_dict({"date": "2026-10-14", "week": True}).date, "2026-10-12", "a plan sits on its Monday")
        copy = ns.carry_over(plan)
        self.assertTrue(copy.week)
        self.assertEqual(copy.date, week_start(today()))
        self.assertEqual([i.text for i in copy.items], ["left over"])


class FlowLayoutTest(unittest.TestCase):
    def test_columns_follow_the_space(self):
        from daykeeper.layout import column_width

        self.assertEqual(column_width(1000, gap=10, minimum=300), (3, (1000 - 20) / 3))
        self.assertEqual(column_width(200, gap=10, minimum=300)[0], 1)

    def test_cards_flow_left_to_right_and_wrap(self):
        from daykeeper.layout import flow_layout

        spots, total = flow_layout([(300, 100), (300, 180), (300, 50)], 700, gap=10)
        self.assertEqual(spots, [(0, 0), (310, 0), (0, 190)], "a row is as tall as its tallest card")
        self.assertEqual(total, 240)
        self.assertEqual(flow_layout([(900, 40)], 700, gap=10), ([(0, 0)], 40), "too wide still starts the row")
        self.assertEqual(flow_layout([], 700), ([], 0))

    def test_pixel_sizes(self):
        spots, _ = __import__("daykeeper.layout", fromlist=["x"]).flow_layout([(301, 10), (301, 10)], 613, gap=10)
        self.assertEqual(spots[1], (311, 0), "one pixel wider still fits — no jump to columns")

    def test_size_is_kept_and_old_files_still_read(self):
        n = Note(title="t", span=3, width=517, height=233)
        back = Note.from_dict(n.to_dict())
        self.assertEqual((back.span, back.width, back.height), (3, 517, 233))
        self.assertEqual(Note.from_dict({"title": "x", "width": -5, "height": 10**6}).width, 0)
        self.assertEqual(Note.from_dict({"title": "x", "height": 10**6}).height, 4000)
        self.assertTrue(n.to_dict()["wide"], "an older version still sees a wide card")
        self.assertEqual(Note.from_dict({"title": "old", "wide": True}).span, 2)
        self.assertEqual(Note.from_dict({"title": "x", "span": 99}).span, 4)
