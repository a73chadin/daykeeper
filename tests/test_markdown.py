"""The Markdown reader of the file viewer."""
import unittest

from daykeeper.markdown import Span, parse, parse_inline, toggle_task


def kinds(text: str) -> list[str]:
    return [b.kind for b in parse(text)]


class InlineTest(unittest.TestCase):
    def test_plain_text_is_one_span(self):
        self.assertEqual(parse_inline("just text"), [Span("just text")])

    def test_bold_italic_code_strike(self):
        spans = parse_inline("a **b** *c* `d` ~~e~~")
        styled = {s.text: s.styles for s in spans if s.styles}
        self.assertEqual(styled["b"], {"bold"})
        self.assertEqual(styled["c"], {"italic"})
        self.assertEqual(styled["d"], {"code"})
        self.assertEqual(styled["e"], {"strike"})

    def test_nested_bold_and_italic(self):
        spans = parse_inline("**bold _both_ here**")
        self.assertIn(Span("both", frozenset({"bold", "italic"})), spans)

    def test_links(self):
        spans = parse_inline("see [docs](https://example.com/a) or https://example.com/b.")
        links = [(s.text, s.href) for s in spans if "link" in s.styles]
        self.assertEqual(links, [("docs", "https://example.com/a"), ("https://example.com/b", "https://example.com/b")])

    def test_code_keeps_markup_inside(self):
        spans = parse_inline("`**not bold**`")
        self.assertEqual(spans, [Span("**not bold**", frozenset({"code"}))])

    def test_snake_case_is_not_italic(self):
        self.assertEqual(parse_inline("my_var_name"), [Span("my_var_name")])


class BlockTest(unittest.TestCase):
    def test_headings_and_paragraphs(self):
        blocks = parse("# Title\n\nline one\nline two\n\n## Sub")
        self.assertEqual([b.kind for b in blocks], ["heading", "blank", "para", "blank", "heading"])
        self.assertEqual(blocks[0].level, 1)
        self.assertEqual(blocks[2].text, "line one line two")
        self.assertEqual(blocks[-1].level, 2)

    def test_lists_and_tasks(self):
        blocks = parse("- one\n  - nested\n1. first\n- [ ] todo\n- [x] done")
        self.assertEqual([b.kind for b in blocks], ["item", "item", "item", "task", "task"])
        self.assertEqual(blocks[1].level, 1)
        self.assertEqual(blocks[2].marker, "1.")
        self.assertEqual((blocks[3].checked, blocks[3].line), (False, 3))
        self.assertEqual((blocks[4].checked, blocks[4].text), (True, "done"))

    def test_code_fence_keeps_its_text(self):
        blocks = parse("```python\n# not a heading\n- not an item\n```\nafter")
        self.assertEqual(blocks[0].kind, "code")
        self.assertEqual(blocks[0].raw, "# not a heading\n- not an item")
        self.assertEqual(blocks[1].text, "after")

    def test_unclosed_fence_runs_to_the_end(self):
        self.assertEqual(kinds("```\ncode"), ["code"])

    def test_quote_rule_table(self):
        self.assertEqual(kinds("> quoted\n\n---\n\n| a | b |\n|---|---|\n| 1 | 2 |"), ["quote", "blank", "rule", "blank", "table"])

    def test_crlf_input(self):
        self.assertEqual(kinds("# A\r\n- b\r\n"), ["heading", "item"])


class ToggleTaskTest(unittest.TestCase):
    def test_flips_only_the_given_line(self):
        src = "- [ ] a\n- [x] b\ntext"
        self.assertEqual(toggle_task(src, 0), "- [x] a\n- [x] b\ntext")
        self.assertEqual(toggle_task(src, 1), "- [ ] a\n- [ ] b\ntext")
        self.assertEqual(toggle_task(src, 2), src)
        self.assertEqual(toggle_task(src, 99), src)

    def test_keeps_windows_line_endings(self):
        self.assertEqual(toggle_task("- [ ] a\r\n- [ ] b", 1), "- [ ] a\r\n- [x] b")


if __name__ == "__main__":
    unittest.main()


class TableTest(unittest.TestCase):
    def test_columns_are_aligned_and_the_rule_kept(self):
        from daykeeper.markdown import table_lines

        lines = table_lines("| Step | Owner |\n|---|:---:|\n| build exe | me |")
        self.assertEqual(lines, ["Step      │ Owner", "──────────┼──────", "build exe │ me"])

    def test_ragged_rows_are_padded(self):
        from daykeeper.markdown import table_lines

        self.assertEqual(table_lines("|a|b|c|\n|x|"), ["a │ b │ c", "x │   │"])
