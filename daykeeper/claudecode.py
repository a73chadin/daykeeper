"""Wiring Daykeeper into AI coding assistants — Claude Code and Codex (see Agent) — without
touching files by hand.

- The hook: our entry in `~/.claude/settings.json` or `~/.codex/hooks.json` (hooks →
  PostToolUse). Connect adds it, disconnect removes it; everything else in the file stays
  as it was. A copy of the file is kept before the first change; a file that is not valid
  JSON is never written.
- The helper: the built app carries `daykeeper-cli.exe` and copies it to a fixed folder, so
  the hook keeps working when the app itself is moved.
- The instructions: a block in `~/.claude/CLAUDE.md` or `~/.codex/AGENTS.md` between our
  markers, telling the assistant to write the journal on its own. Inserted again — the
  block is replaced, not duplicated.

No tkinter here: the logic is unit-tested on temporary folders."""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from .hookrules import MATCHER
from .i18n import _
from .journal_layout import ROLE_MR, ROLE_NOTE, ROLE_TESTS, current as current_layout
from .settings import app_dir

HELPER_NAME = "daykeeper-cli.exe" if os.name == "nt" else "daykeeper-cli"
OUR_MARKS = ("daykeeper-cli", "journal-inbox.py", "cli_main.py")  # a hook command with one of these is ours
BEGIN, END = "<!-- daykeeper:begin -->", "<!-- daykeeper:end -->"
BACKUP_SUFFIX = ".before-daykeeper"
HOOK_TIMEOUT = 15

ON, OFF, STALE = "on", "off", "stale"


class SettingsBroken(ValueError):
    """The assistant's hooks file is not valid JSON: we do not write over it."""


@dataclass(frozen=True)
class Agent:
    """An AI coding assistant Daykeeper can wire into. Claude Code and Codex read the same
    hook shape (hooks → PostToolUse → matcher + command, the tool call as JSON on stdin)
    and keep personal instructions in a Markdown file of their folder."""

    id: str
    name: str
    env: str  # the variable that moves the assistant's folder
    folder: str  # under the home folder by default
    hooks_name: str  # the JSON file holding the hooks
    instructions_name: str
    trust_note: str = ""  # UI key: what the user still does in the assistant itself

    def dir(self, env=os.environ) -> Path:
        return Path(env.get(self.env) or Path.home() / self.folder)


CLAUDE = Agent("claude", "Claude Code", "CLAUDE_CONFIG_DIR", ".claude", "settings.json", "CLAUDE.md")
CODEX = Agent("codex", "Codex", "CODEX_HOME", ".codex", "hooks.json", "AGENTS.md",
              "Codex runs a new hook only after you trust it: type /hooks in Codex once and allow it.")
AGENTS = (CLAUDE, CODEX)


def claude_dir(env=os.environ) -> Path:
    return CLAUDE.dir(env)


def settings_file(env=os.environ, agent: Agent = CLAUDE) -> Path:
    """The file our hook entry goes into: Claude Code's settings.json, Codex's hooks.json."""
    return agent.dir(env) / agent.hooks_name


def instructions_file(env=os.environ, agent: Agent = CLAUDE) -> Path:
    return agent.dir(env) / agent.instructions_name


# ---- the helper

HELPER_FOLDER = "daykeeper-cli"  # a folder build: it starts in ~0.2 s, a one-file exe in ~0.65 s
STAMP = "build-id.txt"  # written by build.bat: the installed copy is replaced when it differs


def helper_dir() -> Path:
    """%LOCALAPPDATA%/Daykeeper/bin on Windows; the data folder's bin elsewhere."""
    local = os.environ.get("LOCALAPPDATA")
    return (Path(local) / "Daykeeper" if local else app_dir()) / "bin"


def bundled_helper() -> Path | None:
    """The helper folder inside the built app; None when running from the source tree."""
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    path = Path(base) / HELPER_FOLDER
    return path if (path / HELPER_NAME).exists() else None


def source_tree() -> Path:
    return Path(__file__).resolve().parents[1]


def helper_command(data_dir: Path | None = None) -> list[str]:
    """How to start the helper: the installed exe for the built app, the source script
    under this Python otherwise, plus `--data` when the vault is not in the default folder
    (the hook must read the settings and fill the inbox of the vault the app opens)."""
    if getattr(sys, "frozen", False):
        parts = [str(helper_dir() / HELPER_FOLDER / HELPER_NAME)]
    else:
        parts = [sys.executable.replace("pythonw.exe", "python.exe"), str(source_tree() / "cli_main.py")]
    if data_dir is not None and Path(data_dir).resolve() != app_dir().resolve():
        parts += ["--data", str(data_dir)]
    return parts


def helper_available() -> bool:
    """The source tree always has the helper; a built app only when it carries one (the
    Windows build does; the macOS bundle does not yet)."""
    return not getattr(sys, "frozen", False) or bundled_helper() is not None


def _stamp(folder: Path) -> str:
    try:
        return (folder / STAMP).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def install_helper(src: Path | None = None, bin_dir: Path | None = None) -> bool:
    """Copy the bundled helper folder into place when it is missing or of another build.
    The copy goes next to it first and is swapped in, so a half copy is never used; a
    folder held by a running hook stays until the next try. True — the helper is in place."""
    src = src or bundled_helper()
    if src is None:
        return True  # the source tree: nothing to install
    bin_dir = bin_dir or helper_dir()
    dst = bin_dir / HELPER_FOLDER
    if (dst / HELPER_NAME).exists() and _stamp(dst) == _stamp(src) != "":
        return True
    new, old = bin_dir / (HELPER_FOLDER + ".new"), bin_dir / (HELPER_FOLDER + ".old")
    try:
        for leftover in (new, old):
            shutil.rmtree(leftover, ignore_errors=True)
        shutil.copytree(src, new)
        if dst.exists():
            os.replace(dst, old)
        os.replace(new, dst)
        shutil.rmtree(old, ignore_errors=True)
        return True
    except OSError:
        if old.exists() and not dst.exists():
            try:
                os.replace(old, dst)
            except OSError:
                pass
        return (dst / HELPER_NAME).exists()


def quote(path: str) -> str:
    return f'"{path}"' if any(c in path for c in " &()") else path


def hook_command(parts: list[str] | None = None) -> str:
    parts = parts or helper_command()
    return " ".join(quote(p.replace("\\", "/")) for p in parts) + " hook"


# ---- Claude Code settings.json


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise SettingsBroken(str(exc)) from exc
    if not isinstance(data, dict):
        raise SettingsBroken("not an object")
    return data


def _is_ours(hook: dict) -> bool:
    return isinstance(hook, dict) and any(mark in str(hook.get("command", "")) for mark in OUR_MARKS)


def our_commands(data: dict) -> list[str]:
    groups = (data.get("hooks") or {}).get("PostToolUse") or []
    return [h.get("command", "") for g in groups if isinstance(g, dict) for h in g.get("hooks") or [] if _is_ours(h)]


def state(path: Path | None = None, command: str | None = None) -> str:
    """ON — our hook with the current command; STALE — ours, but an old command (moved app,
    the earlier python script); OFF — none, or the file cannot be read."""
    try:
        found = our_commands(_read(path or settings_file()))
    except (SettingsBroken, OSError):
        return OFF
    if not found:
        return OFF
    return ON if found == [command or hook_command()] else STALE


def _without_ours(data: dict) -> dict:
    hooks = data.get("hooks")
    if not isinstance(hooks, dict) or not isinstance(hooks.get("PostToolUse"), list):
        return data
    kept = []
    for group in hooks["PostToolUse"]:
        if isinstance(group, dict) and isinstance(group.get("hooks"), list):
            rest = [h for h in group["hooks"] if not _is_ours(h)]
            if not rest:
                continue
            group = {**group, "hooks": rest}
        kept.append(group)
    if kept:
        hooks["PostToolUse"] = kept
    else:
        del hooks["PostToolUse"]
        if not hooks:
            del data["hooks"]
    return data


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)  # the user's file as it was before we ever touched it
    tmp = path.with_name(path.name + ".daykeeper-tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def connect(path: Path | None = None, command: str | None = None) -> None:
    """Add our hook (an older entry of ours is replaced). Raises SettingsBroken, OSError."""
    path = path or settings_file()
    data = _without_ours(_read(path))
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise SettingsBroken("hooks is not an object")
    hooks.setdefault("PostToolUse", []).append(
        {"matcher": MATCHER, "hooks": [{"type": "command", "command": command or hook_command(), "timeout": HOOK_TIMEOUT}]}
    )
    _write(path, data)


def disconnect(path: Path | None = None) -> None:
    path = path or settings_file()
    data = _read(path)
    if our_commands(data):
        _write(path, _without_ours(data))


# ---- CLAUDE.md


@dataclass
class TeachOptions:
    statuses: tuple[str, ...] | None = None  # command words; None — every line of the journal layout
    note_language: str = "English"  # what language Claude writes note text in

    def words(self) -> tuple[str, ...]:
        return self.statuses if self.statuses is not None else tuple(w for w, _l in current_layout().statuses())


def cli_for_text(parts: list[str] | None = None) -> str:
    """The helper as Claude should type it: forward slashes, quoted only when needed."""
    return " ".join(quote(p.replace("\\", "/")) for p in (parts or helper_command()))


_EXAMPLES = {
    "pass": ("pass APP-1234", "verified successfully"),
    "fail": ("fail APP-1234", "verification failed (Test failed)"),
    "bug": ("bug APP-4127", "a new defect I filed"),
    "review": ("review !42", "reviewed SOMEONE ELSE's MR (posted a review or a verdict)"),
    "mr-open": ("mr-open !45", "opened an MR"),
    "mr-merged": ("mr-merged !44", "merged an MR"),
    "manual": ("manual APP-101", "wrote manual test cases for a task"),
    "auto": ("auto APP-101", "wrote autotests for a task"),
    "note": ('note "nightly run 1042 triage"', "other notable work, one sentence"),
}


def example(word: str) -> tuple[str, str]:
    """(arguments, comment) for one command word: the tester's words have hand-written
    examples, any other line of the layout gets one from its kind."""
    layout = current_layout()
    resolved = layout.resolve(word)
    if resolved is None:
        return f'{word} "…"', word
    where, ident = resolved
    if word in _EXAMPLES and ident in ("ok", "failed", "bugs", "review", "mr_open", "mr_merged", "manual", "auto", "note"):
        return _EXAMPLES[word]
    if where == "column":
        return f"{word} APP-1234", f"{layout.column(ident).label} (issue keys or short items)"
    kind = layout.kind(ident)
    sample = {ROLE_MR: "!42", ROLE_TESTS: "APP-101", ROLE_NOTE: '"what was done, one sentence"'}.get(kind.role, '"…"')
    return f"{word} {sample}", kind.label


def note_words(words: tuple[str, ...]) -> list[str]:
    layout = current_layout()
    out = []
    for w in words:
        r = layout.resolve(w)
        if r and r[0] == "activity" and layout.role(r[1]) == ROLE_NOTE:
            out.append(w)
    return out


def instructions(options: TeachOptions | None = None, parts: list[str] | None = None) -> str:
    """The CLAUDE.md block (without the markers). English: it is read by Claude."""
    options = options or TeachOptions()
    cli = cli_for_text(parts)
    ps_note = "\nIn PowerShell put `&` before a quoted path." if cli.startswith('"') else ""
    words = options.words()
    examples = [example(w) for w in words]
    width = max((len(a) for a, _c in examples), default=0)
    lines = [f"{cli} {a.ljust(width)}   # {c}" for a, c in examples]
    parts_ = [
        "# Work journal (Daykeeper)",
        "",
        "Write the outcome of finished work into my Daykeeper journal with one command. It",
        "opens nothing and only appends a line to the journal inbox:",
        "",
        "```",
        *lines,
        "```",
        ps_note.strip(),
        "",
        "When: right after the result exists — a verdict on a task, a defect filed, a review",
        "posted, an MR opened or merged, test cases or autotests written. Several keys — space",
        "separated. Unfinished work (blocked, postponed) is not written. The app drops",
        "duplicates, and a hook already catches some of these on its own, so run the command",
        "anyway.",
    ]
    notes = note_words(words)
    if notes:
        parts_ += [
            "",
            f"`{notes[0]}` — one per notable piece of work that is not covered above (a run triaged, a",
            "skill or instruction changed, tests refactored, a plan prepared), at the end of that",
            f"piece, one sentence in {options.note_language}, no keys or MR numbers. Not for answers",
            "to questions or tiny edits.",
        ]
    parts_ += [
        "",
        "Only work goes into the journal: items written inside a personal repository are",
        "dropped by the app.",
    ]
    return "\n".join(p for p in parts_ if p is not None).replace("\n\n\n", "\n\n").strip() + "\n"


def current_block(path: Path | None = None) -> str | None:
    """Our block as it is in CLAUDE.md now; None — not there."""
    try:
        text = (path or instructions_file()).read_text(encoding="utf-8-sig")
    except OSError:
        return None
    start, end = text.find(BEGIN), text.find(END)
    if start < 0 or end < start:
        return None
    return text[start + len(BEGIN) : end].strip("\n") + "\n"


def mentions_journal_elsewhere(path: Path | None = None) -> bool:
    """CLAUDE.md speaks of the journal outside our block (an older hand-written section)."""
    try:
        text = (path or instructions_file()).read_text(encoding="utf-8-sig")
    except OSError:
        return False
    start, end = text.find(BEGIN), text.find(END)
    if start >= 0 and end > start:
        text = text[:start] + text[end + len(END) :]
    return any(word in text for word in ("inbox.py", "daykeeper-cli", "Daykeeper"))


def insert_block(block: str, path: Path | None = None) -> None:
    """Put the block between our markers: replace it if there, else append at the end."""
    path = path or instructions_file()
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        text = ""
    wrapped = f"{BEGIN}\n{block.strip()}\n{END}"
    start, end = text.find(BEGIN), text.find(END)
    if start >= 0 and end > start:
        text = text[:start] + wrapped + text[end + len(END) :]
    else:
        text = (text.rstrip("\n") + "\n\n" if text.strip() else "") + wrapped + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)
    tmp = path.with_name(path.name + ".daykeeper-tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def remove_block(path: Path | None = None) -> bool:
    path = path or instructions_file()
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return False
    start, end = text.find(BEGIN), text.find(END)
    if start < 0 or end < start:
        return False
    text = (text[:start].rstrip("\n") + "\n" + text[end + len(END) :].lstrip("\n")).strip("\n")
    tmp = path.with_name(path.name + ".daykeeper-tmp")
    tmp.write_text(text + "\n" if text else "", encoding="utf-8")
    os.replace(tmp, path)
    return True


def state_text(value: str) -> str:
    return {ON: _("Connected"), STALE: _("Connected with an old path — connect again"), OFF: _("Not connected")}[value]
