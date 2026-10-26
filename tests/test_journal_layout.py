"""Journal layouts: the ready-made ones, editing, storage, and everything that follows the
layout — the day, the report, the inbox words, the hook and the instructions for Claude."""

from dataclasses import replace
import json
import tempfile
import unittest
from pathlib import Path

from daykeeper import claudecode, cli
from daykeeper import journal_layout as jl
from daykeeper.settings import Links
from daykeeper.journal import configure as configure_links, links
from daykeeper.journal import Day, Journal, append_inbox, build_report, merge_inbox, parse_activity_lines, read_inbox


class LayoutCase(unittest.TestCase):
    """Runs with a layout in place and puts the tester's one back afterwards."""

    layout = "qa"

    def setUp(self):
        jl.configure(jl.preset(self.layout))
        self.addCleanup(jl.configure, jl.preset("qa"))
        self.addCleanup(configure_links, links())  # other tests leave tracker links set: keys stay plain here
        configure_links(Links())


class PresetTest(unittest.TestCase):
    def test_every_preset_round_trips_through_settings(self):
        for pid in jl.PRESETS:
            layout = jl.preset(pid)
            back = jl.Layout.from_settings(json.loads(json.dumps(layout.to_settings())))
            self.assertEqual(back.to_settings(), layout.to_settings(), pid)

    def test_no_settings_is_the_tester(self):
        self.assertEqual(jl.Layout.from_settings({}).preset, "qa")
        self.assertEqual(jl.Layout.from_settings({"journal": {"columns": [], "kinds": []}}).preset, "qa")

    def test_broken_rows_are_skipped(self):
        raw = {"journal": {"columns": [{"id": "a", "label": "A"}, {"id": "a", "label": "dup"}, {"label": "no id"}, "x"],
                           "kinds": [{"id": "k", "label": "K", "role": "weird", "extra": 1}]}}
        layout = jl.Layout.from_settings(raw)
        self.assertEqual([c.id for c in layout.columns], ["a"])
        self.assertEqual(layout.kinds[0].role, jl.ROLE_PLAIN)

    def test_words_resolve_with_the_old_aliases(self):
        qa = jl.preset("qa")
        self.assertEqual(qa.resolve("pass"), ("column", "ok"))
        self.assertEqual(qa.resolve("defect"), ("column", "bugs"))
        self.assertEqual(qa.resolve("mr-open"), ("activity", "mr_open"))
        self.assertIsNone(jl.preset("pm").resolve("pass"))
        self.assertEqual(jl.preset("pm").resolve("meeting"), ("activity", "meetings"))

    def test_slug_and_new_ids(self):
        self.assertEqual(jl.slug("Созвоны"), "sozvony")
        self.assertEqual(jl.slug("Code review!"), "code-review")
        layout = jl.preset("qa")
        self.assertEqual(jl.new_id("Pass", jl.taken_words(layout)), "pass-2", "a word the inbox already uses is not reused")
        self.assertEqual(jl.new_id("???", set()), "item")


class TesterReportUnchangedTest(LayoutCase):
    def test_the_tester_report_keeps_its_shape(self):
        day = Day("2026-10-13", columns={"ok": ["APP-1"], "failed": ["APP-2"]}, plans="RFT")
        day.add_activity("review", "!42")
        text = build_report(day)
        self.assertTrue(text.startswith("1. tested tasks, reviewed MRs\nMR reviews: !42\n\nTested successfully:\nAPP-1\n"), text)
        self.assertTrue(text.endswith("2. RFT\n\n3. None"))


class DeveloperTest(LayoutCase):
    layout = "dev"

    def test_report_and_summary(self):
        day = Day("2026-10-13", columns={"done": ["APP-1"], "fixed": ["APP-9"]})
        day.add_activity("mr_open", "!45")
        day.add_activity("review", "!42")
        day.add_activity("note", "pairing on the release")
        self.assertEqual(day.auto_summary(), "development, bug fixes, code review, pairing on the release")
        text = build_report(day)
        self.assertIn("MRs opened: !45\nMR reviews: !42\n\nTasks done:\nAPP-1\n\nIn progress:\n\nBugs fixed:\nAPP-9", text)

    def test_activity_lines_round_trip(self):
        got = parse_activity_lines("MRs opened: !1, !2\nsome note")
        self.assertEqual(got, [{"kind": "mr_open", "text": "!1"}, {"kind": "mr_open", "text": "!2"}, {"kind": "note", "text": "some note"}])

    def test_the_inbox_takes_the_layout_words(self):
        with tempfile.TemporaryDirectory() as tmp:
            inbox = Path(tmp) / "inbox.jsonl"
            self.assertEqual(append_inbox(inbox, "done", ["APP-1"], origin=""), 1)
            self.assertEqual(append_inbox(inbox, "progress", ["APP-2"], origin=""), 1)
            with self.assertRaises(ValueError):
                append_inbox(inbox, "nonsense", ["APP-3"], origin="")  # a word no layout has
            journal = Journal()
            merge_inbox(journal, read_inbox(inbox))
            day = next(iter(journal.days.values()))
            self.assertEqual((day.section("done"), day.section("progress")), (["APP-1"], ["APP-2"]))


class PlainDiaryTest(LayoutCase):
    layout = "simple"

    def test_no_absence_line(self):
        day = Day("2026-10-13", columns={"done": ["wrote the plan"]}, plans="review it")
        text = build_report(day)
        self.assertTrue(text.endswith("2. review it"))
        self.assertNotIn("3.", text)


class HiddenAndOldItemsTest(LayoutCase):
    def test_hidden_kinds_keep_their_items(self):
        layout = jl.preset("qa")
        layout.kind("manual").hidden = True
        jl.configure(layout)
        day = Day("2026-10-13", activities=[{"kind": "manual", "text": "APP-1"}, {"kind": "review", "text": "!5"}])
        self.assertEqual(day.activity_lines(), "MR reviews: !5")
        self.assertEqual(day.hidden_activities(), [{"kind": "manual", "text": "APP-1"}])

    def test_a_day_written_before_the_layouts(self):
        old = {"date": "2026-10-01", "ok": ["APP-1"], "failed": [], "bugs": ["APP-9"], "activities": []}
        day = Day.from_dict(old)
        self.assertEqual((day.section("ok"), day.section("bugs")), (["APP-1"], ["APP-9"]))
        self.assertNotIn("ok", Day.from_dict(json.loads(json.dumps(day.to_dict()))).to_dict().keys() - {"columns", "date", "summary", "activities", "plans", "other"})

    def test_an_alias_for_a_column_the_layout_lacks_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            inbox = Path(tmp) / "inbox.jsonl"
            append_inbox(inbox, "pass", ["APP-1"], origin="")
            jl.configure(jl.preset("pm"))
            journal = Journal()
            merge_inbox(journal, read_inbox(inbox))
            self.assertEqual(next(iter(journal.days.values())).columns, {"ok": ["APP-1"]})


class SwitchAndBackTest(LayoutCase):
    """Tester → developer for a while → tester: nothing written before or during is lost."""

    def test_nothing_is_lost(self):
        day = Day("2026-10-13", columns={"ok": ["APP-1"], "bugs": ["APP-9"]})
        day.add_activity("manual", "APP-3")
        day.add_activity("review", "!42")
        with tempfile.TemporaryDirectory() as tmp:
            inbox = Path(tmp) / "inbox.jsonl"
            jl.configure(jl.preset("dev"))
            # the editor rewrites the activity field from what it shows, as the Journal tab does
            day.activities = parse_activity_lines(day.activity_lines() + "\nMRs opened: !45") + day.hidden_activities()
            day.columns["done"] = ["APP-50"]
            for status, item in (("pass", "APP-2"), ("manual", "APP-4"), ("done", "APP-51")):
                self.assertEqual(append_inbox(inbox, status, [item], date="2026-10-13", origin=""), 1, status)
            journal = Journal([day])
            merge_inbox(journal, read_inbox(inbox))
            saved = Day.from_dict(json.loads(json.dumps(journal.days["2026-10-13"].to_dict())))
            jl.configure(jl.preset("qa"))
            self.assertEqual(saved.section("ok"), ["APP-1", "APP-2"])
            self.assertEqual(saved.section("bugs"), ["APP-9"])
            kinds = {(a["kind"], a["text"]) for a in saved.activities}
            self.assertLessEqual({("manual", "APP-3"), ("manual", "APP-4"), ("review", "!42"), ("mr_open", "!45")}, kinds)
            self.assertIn("Manual cases: APP-3, APP-4", saved.activity_lines())
            self.assertEqual(saved.section("done"), ["APP-50", "APP-51"], "the developer's column waits for its layout")


class FollowersTest(LayoutCase):
    layout = "pm"

    def test_the_hook_keeps_another_layouts_line_and_skips_an_unknown_word(self):
        with tempfile.TemporaryDirectory() as tmp:
            inbox, log = Path(tmp) / "inbox.jsonl", Path(tmp) / "log.jsonl"
            settings = jl.preset("pm").to_settings()
            payload = {"tool_name": "Bash", "tool_input": {"command": "glab mr merge 44"}, "tool_response": "Merged"}
            self.assertEqual(len(cli.run_hook(payload, settings, inbox, log)), 1, "the tester's MR line is kept, hidden")
            self.assertEqual([(i.kind, i.item) for i in read_inbox(inbox)], [("mr_merged", "!44")])
            settings["hook"] = {"rules": [{"name": "x", "status": "nonsense", "pattern": "deploy", "take": "text", "text": "d"}]}
            cli.run_hook({"tool_name": "Bash", "tool_input": {"command": "deploy"}, "tool_response": ""}, settings, inbox, log)
            self.assertEqual(cli.read_log(log)[0]["skipped"], "layout")

    def test_instructions_list_the_layout_words(self):
        text = claudecode.instructions(claudecode.TeachOptions(), ["C:/bin/daykeeper-cli.exe"])
        for word in ("task", "doc", "decision", "meeting", "note"):
            self.assertIn(f"daykeeper-cli.exe {word} ", text)
        self.assertNotIn(" pass ", text)
        self.assertIn("`note` — one per notable piece", text)


class ThemesTest(LayoutCase):
    """Notes that name a theme of the list go into line 1 as that theme, once and in the
    order of the list; other notes go there as written."""

    THEMES = ["переезд на Zest", "чистка дублей тестовой базы", "тестирование UAT"]

    def setUp(self):
        super().setUp()
        layout = jl.preset("qa")
        layout.themes = list(self.THEMES)
        jl.configure(layout)

    def test_line_one_names_each_theme_once_in_the_list_order(self):
        day = Day("2026-10-20", columns={"ok": ["APP-1"]})
        for text in ("тестирование UAT", "Переезд на Zest.", "переезд на zest", "разбор зависаний поиска", "  чистка дублей тестовой базы "):
            day.add_activity("note", text)
        day.add_activity("review", "!42")
        first = build_report(day).splitlines()[0]
        self.assertEqual(first, "1. tested tasks, reviewed MRs, переезд на Zest, чистка дублей тестовой базы, тестирование UAT, разбор зависаний поиска")

    def test_a_theme_carries_what_the_work_was_about(self):
        jl.configure(replace(jl.current(), themes=["переезд на Zest — перенос кейсов и CLI", "тестирование UAT"]))
        day = Day("2026-10-20")
        for text in ("тестирование UAT: релиз 2.4", "Переезд на Zest", "переезд на zest: стампы в тестах.", "тестирование UAT: Релиз 2.4", "тестирование UAT: смоук прода", "разбор зависаний"):
            day.add_activity("note", text)
        self.assertEqual(day.auto_summary(), "переезд на Zest: стампы в тестах; тестирование UAT: релиз 2.4, смоук прода; разбор зависаний")
        day = Day("2026-10-20")
        day.add_activity("note", "переезд на Zest")  # no subject: the commas as before, the hint stays out
        day.add_activity("note", "тестирование UAT")
        self.assertEqual(day.auto_summary(), "переезд на Zest, тестирование UAT")
        self.assertEqual(jl.split_note("разбор: чего-то", jl.current().themes), None)
        self.assertEqual(jl.clean_themes("a — one\nA — two\nb"), ["a — one", "b"])

    def test_without_themes_notes_go_as_written_each_once(self):
        jl.configure(jl.preset("qa"))
        day = Day("2026-10-20")
        for text in ("night triage", "Night triage", "skills"):
            day.add_activity("note", text)
        self.assertEqual(day.auto_summary(), "night triage, skills")

    def test_themes_round_trip_and_are_cleaned(self):
        layout = jl.Layout.from_settings({"journal": {**jl.preset("qa").to_settings()["journal"], "themes": [" a  b ", "A B", "", None, 3, "c"]}})
        self.assertEqual(layout.themes, ["a b", "3", "c"])
        self.assertEqual(jl.Layout.from_settings(layout.to_settings()).themes, ["a b", "3", "c"])
        self.assertEqual(jl.copy(layout).themes, layout.themes)
        self.assertIsNot(jl.copy(layout).themes, layout.themes)
        self.assertEqual(jl.clean_themes("x\n\n y \nX"), ["x", "y"])

    def test_the_last_verdict_of_the_day_wins(self):
        day = Day("2026-10-20")
        day.add("failed", "APP-7")
        day.add("ok", "APP-7")
        self.assertEqual((day.columns["ok"], day.columns["failed"]), (["APP-7"], []))
        day.add("bugs", "APP-7")  # another phrase: not a verdict of the same thing
        self.assertEqual(day.columns["ok"], ["APP-7"])

    def test_the_inbox_lists_the_themes_and_warns_of_a_stray_note(self):
        import contextlib
        import io

        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault.pkv"
            (Path(tmp) / "settings.json").write_text(json.dumps({"journal": {**jl.preset("qa").to_settings()["journal"], "themes": self.THEMES}}, ensure_ascii=False), encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            original = cli.vault_path
            cli.vault_path = lambda: vault
            self.addCleanup(setattr, cli, "vault_path", original)
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(cli.inbox_main(["themes"]), 0)
                self.assertEqual(cli.inbox_main(["note", "тестирование UAT", "--work"]), 0)
                self.assertEqual(err.getvalue(), "")
                self.assertEqual(cli.inbox_main(["note", "тестирование UAT: релиз 2.4", "--work"]), 0)
                self.assertEqual(err.getvalue(), "")
                self.assertEqual(cli.inbox_main(["note", "что-то своё", "--work"]), 0)
                self.assertIn("not a theme", err.getvalue())
                self.assertEqual(cli.inbox_main(["note", "тестирование UAT: релиз и смоук прода с новой учёткой без чата", "--work"]), 0)
            self.assertIn("переезд на Zest\nчистка дублей тестовой базы\nтестирование UAT\n", out.getvalue())
            self.assertIn("longer than 6 words", err.getvalue())
            self.assertEqual(len(read_inbox(Path(tmp) / "inbox.jsonl")), 4)

    def test_the_instructions_name_the_themes(self):
        text = claudecode.instructions(claudecode.TeachOptions(), ["C:/bin/daykeeper-cli.exe"])
        self.assertIn("- переезд на Zest\n- чистка дублей тестовой базы\n- тестирование UAT\n", text)
        self.assertIn("one of these themes, word for word, then a colon", text)


if __name__ == "__main__":
    unittest.main()
