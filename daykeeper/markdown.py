"""A small Markdown reader for the built-in file viewer.

Covers what working notes use: headings, paragraphs, bullet and numbered lists, task
items (`- [ ]` / `- [x]`), quotes, fenced code, tables, horizontal rules, and inline
bold, italic, strike-through, code and links. Pure (no tkinter), so it is unit-tested;
`md_view.py` turns the blocks into a Tk Text widget.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Span:
    text: str
    styles: frozenset[str] = frozenset()  # bold, italic, strike, code, link
    href: str = ""


@dataclass
class Block:
    kind: str  # heading, para, item, task, quote, code, table, rule, blank
    spans: list[Span] = field(default_factory=list)
    level: int = 0  # heading level 1–6; list nesting depth for items
    marker: str = ""  # "•" or "3." for list items
    checked: bool = False  # task items
    line: int = -1  # source line of a task item (0-based), to toggle it in the file
    raw: str = ""  # code and table blocks: the text as is

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans) if self.spans else self.raw


_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_RULE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_ITEM = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
_TASK = re.compile(r"^\[([ xX])\]\s+(.*)$")
_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
_TABLE = re.compile(r"^\s*\|.*\|\s*$")

_INLINE = re.compile(
    r"(?P<code>`+)(?P<code_text>.+?)(?P=code)"
    r"|\[(?P<link_text>[^\]]+)\]\((?P<link_href>[^)\s]+)(?:\s+\"[^\"]*\")?\)"
    r"|<(?P<auto>https?://[^>\s]+)>"
    r"|(?P<bare>https?://[^\s<>()\[\]]+[^\s<>()\[\].,;:!?\"'])"
    r"|(?P<bold>\*\*|__)(?P<bold_text>.+?)(?P=bold)"
    r"|~~(?P<strike_text>.+?)~~"
    r"|(?<![\w*])\*(?P<em_text>[^*\s](?:[^*]*[^*\s])?)\*(?!\*)"
    r"|(?<![\w_])_(?P<em2_text>[^_\s](?:[^_]*[^_\s])?)_(?![\w_])"
)


def parse_inline(text: str, styles: frozenset[str] = frozenset()) -> list[Span]:
    """Inline markup → spans. Bold, italic and strike nest; code and links do not."""
    spans: list[Span] = []
    pos = 0
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            spans.append(Span(text[pos:m.start()], styles))
        if m.group("code_text") is not None:
            spans.append(Span(m.group("code_text").strip(), styles | {"code"}))
        elif m.group("link_text") is not None:
            spans.append(Span(m.group("link_text"), styles | {"link"}, m.group("link_href")))
        elif m.group("auto") is not None:
            spans.append(Span(m.group("auto"), styles | {"link"}, m.group("auto")))
        elif m.group("bare") is not None:
            spans.append(Span(m.group("bare"), styles | {"link"}, m.group("bare")))
        elif m.group("bold_text") is not None:
            spans.extend(parse_inline(m.group("bold_text"), styles | {"bold"}))
        elif m.group("strike_text") is not None:
            spans.extend(parse_inline(m.group("strike_text"), styles | {"strike"}))
        else:
            inner = m.group("em_text") if m.group("em_text") is not None else m.group("em2_text")
            spans.extend(parse_inline(inner, styles | {"italic"}))
        pos = m.end()
    if pos < len(text):
        spans.append(Span(text[pos:], styles))
    return spans


def parse(text: str) -> list[Block]:
    """Markdown text → blocks in reading order. Consecutive plain lines join into one
    paragraph; a blank line between blocks becomes a `blank` block (one at most)."""
    blocks: list[Block] = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    para: list[str] = []

    def flush_para() -> None:
        if para:
            blocks.append(Block("para", parse_inline(" ".join(p.strip() for p in para))))
            para.clear()

    def blank() -> None:
        if blocks and blocks[-1].kind != "blank":
            blocks.append(Block("blank"))

    i = 0
    while i < len(lines):
        line = lines[i]
        fence = _FENCE.match(line)
        if fence:
            flush_para()
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(fence.group(1)):
                body.append(lines[i])
                i += 1
            blocks.append(Block("code", raw="\n".join(body)))
            i += 1  # the closing fence (or the end of the file)
            continue
        if not line.strip():
            flush_para()
            blank()
            i += 1
            continue
        heading = _HEADING.match(line)
        if heading:
            flush_para()
            blocks.append(Block("heading", parse_inline(heading.group(2)), level=len(heading.group(1))))
        elif _RULE.match(line):
            flush_para()
            blocks.append(Block("rule"))
        elif _TABLE.match(line):
            flush_para()
            rows: list[str] = []
            while i < len(lines) and _TABLE.match(lines[i]):
                rows.append(lines[i].strip())
                i += 1
            blocks.append(Block("table", raw="\n".join(rows)))
            continue
        elif (quote := _QUOTE.match(line)) is not None:
            flush_para()
            blocks.append(Block("quote", parse_inline(quote.group(1))))
        elif (item := _ITEM.match(line)) is not None:
            flush_para()
            indent = len(item.group(1).expandtabs(4)) // 2
            marker = item.group(2)
            body_text = item.group(3)
            task = _TASK.match(body_text)
            if task is not None and not marker[0].isdigit():
                blocks.append(Block("task", parse_inline(task.group(2)), level=indent, checked=task.group(1) != " ", line=i))
            else:
                shown = marker if marker[0].isdigit() else "•"
                blocks.append(Block("item", parse_inline(body_text), level=indent, marker=shown))
        else:
            para.append(line)
        i += 1
    flush_para()
    while blocks and blocks[-1].kind == "blank":
        blocks.pop()
    return blocks


def toggle_task(text: str, line: int) -> str:
    """Flip `[ ]` ↔ `[x]` on a task line of the source; other lines and line endings stay."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(newline)
    if not 0 <= line < len(lines):
        return text
    m = re.match(r"^(\s*[-*+]\s+\[)([ xX])(\].*)$", lines[line])
    if m is None:
        return text
    lines[line] = m.group(1) + (" " if m.group(2) != " " else "x") + m.group(3)
    return newline.join(lines)


def table_lines(raw: str) -> list[str]:
    """A pipe table → aligned plain-text lines for a monospace font: cells padded to the
    column width, columns split by “│”, the `|---|` row drawn as a rule under the header.
    The first line is the header."""
    rows: list[list[str]] = []
    rule_after: int | None = None
    for line in raw.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c) and any(cells):
            rule_after = len(rows)
            continue
        rows.append(cells)
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    sizes = [max(len(r[i]) for r in rows) for i in range(width)]
    out = [" │ ".join(cell.ljust(sizes[i]) for i, cell in enumerate(r)).rstrip() for r in rows]
    if rule_after:
        out.insert(rule_after, "─┼─".join("─" * s for s in sizes))
    return out
