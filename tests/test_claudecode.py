"""Wiring into Claude Code: the hook entry in settings.json and the block in CLAUDE.md.
Temporary folders only; the real ~/.claude is never touched."""

import json
import tempfile
import unittest
from pathlib import Path

from daykeeper import claudecode as cc

CMD = "C:/Users/me/AppData/Local/Daykeeper/bin/daykeeper-cli.exe hook"


class HookEntryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "settings.json"

    def test_connect_keeps_everything_else(self):
        other = {"matcher": "Edit", "hooks": [{"type": "command", "command": "node lint.mjs"}]}
        self.path.write_text(json.dumps({"model": "opus", "hooks": {"PostToolUse": [other], "Stop": [{"hooks": []}]}}), encoding="utf-8")
        cc.connect(self.path, CMD)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["model"], "opus")
        self.assertEqual(data["hooks"]["Stop"], [{"hooks": []}])
        self.assertEqual(data["hooks"]["PostToolUse"][0], other)
        self.assertEqual(data["hooks"]["PostToolUse"][1]["matcher"], cc.MATCHER)
        self.assertEqual(cc.state(self.path, CMD), cc.ON)
        self.assertTrue((self.path.parent / ("settings.json" + cc.BACKUP_SUFFIX)).exists())

    def test_connect_twice_and_replace_the_old_script(self):
        old = {"hooks": [{"type": "command", "command": 'python "/path/to/repo/hooks/journal-inbox.py"', "timeout": 15}]}
        self.path.write_text(json.dumps({"hooks": {"PostToolUse": [old]}}), encoding="utf-8")
        self.assertEqual(cc.state(self.path, CMD), cc.STALE)
        cc.connect(self.path, CMD)
        cc.connect(self.path, CMD)
        self.assertEqual(cc.our_commands(json.loads(self.path.read_text(encoding="utf-8"))), [CMD])

    def test_a_shared_group_keeps_the_other_hook(self):
        group = {"hooks": [{"type": "command", "command": "node other.mjs"}, {"type": "command", "command": CMD}]}
        self.path.write_text(json.dumps({"hooks": {"PostToolUse": [group]}}), encoding="utf-8")
        cc.disconnect(self.path)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["hooks"]["PostToolUse"], [{"hooks": [{"type": "command", "command": "node other.mjs"}]}])

    def test_disconnect_leaves_no_empty_shells(self):
        cc.connect(self.path, CMD)
        cc.disconnect(self.path)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {})
        self.assertEqual(cc.state(self.path, CMD), cc.OFF)

    def test_a_broken_file_is_not_written(self):
        self.path.write_text('{"model": "opus",}', encoding="utf-8")
        with self.assertRaises(cc.SettingsBroken):
            cc.connect(self.path, CMD)
        self.assertEqual(self.path.read_text(encoding="utf-8"), '{"model": "opus",}')
        self.assertEqual(cc.state(self.path, CMD), cc.OFF)

    def test_a_missing_file_is_created(self):
        cc.connect(self.path, CMD)
        self.assertEqual(cc.state(self.path, CMD), cc.ON)


class CodexTest(unittest.TestCase):
    def test_codex_files_follow_codex_home(self):
        env = {"CODEX_HOME": "D:/codex-home"}
        self.assertEqual(cc.settings_file(env, cc.CODEX), Path("D:/codex-home") / "hooks.json")
        self.assertEqual(cc.instructions_file(env, cc.CODEX), Path("D:/codex-home") / "AGENTS.md")
        self.assertEqual(cc.settings_file({}, cc.CODEX), Path.home() / ".codex" / "hooks.json")

    def test_connect_writes_the_same_hook_shape_into_hooks_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hooks.json"
            cc.connect(path, CMD)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["hooks"]["PostToolUse"][0]["matcher"], cc.MATCHER)
            self.assertEqual(cc.state(path, CMD), cc.ON)

    def test_a_codex_shell_call_is_read(self):
        from daykeeper.hookrules import classify
        from daykeeper.settings import Links
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "turn_id": "t1",
                   "tool_input": {"command": ["bash", "-lc", "glab mr merge 44"]}, "tool_response": "Merged"}
        self.assertEqual(classify(payload, Links(gitlab_url="https://gl.example.com", gitlab_project="g/r")), [("mr-merged", "!44")])


class CommandTest(unittest.TestCase):
    def test_quoting(self):
        self.assertEqual(cc.hook_command(["C:\\Users\\me\\bin\\daykeeper-cli.exe"]), "C:/Users/me/bin/daykeeper-cli.exe hook")
        self.assertEqual(cc.hook_command(["C:\\Users\\Ann Lee\\daykeeper-cli.exe"]), '"C:/Users/Ann Lee/daykeeper-cli.exe" hook')


class InstallHelperTest(unittest.TestCase):
    def test_copied_once_per_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, bin_dir = Path(tmp) / "bundle", Path(tmp) / "bin"
            src.mkdir()
            (src / cc.HELPER_NAME).write_text("v1", encoding="utf-8")
            (src / cc.STAMP).write_text("build-1", encoding="utf-8")
            self.assertTrue(cc.install_helper(src, bin_dir))
            installed = bin_dir / cc.HELPER_FOLDER / cc.HELPER_NAME
            self.assertEqual(installed.read_text(encoding="utf-8"), "v1")
            installed.write_text("touched", encoding="utf-8")
            cc.install_helper(src, bin_dir)
            self.assertEqual(installed.read_text(encoding="utf-8"), "touched", "the same build is not copied again")
            (src / cc.HELPER_NAME).write_text("v2", encoding="utf-8")
            (src / cc.STAMP).write_text("build-2", encoding="utf-8")
            cc.install_helper(src, bin_dir)
            self.assertEqual(installed.read_text(encoding="utf-8"), "v2")
            self.assertEqual(sorted(p.name for p in bin_dir.iterdir()), [cc.HELPER_FOLDER])


class InstructionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "CLAUDE.md"

    def test_text_follows_the_options(self):
        text = cc.instructions(cc.TeachOptions(statuses=("pass", "note"), note_language="Russian"), ["C:\\bin\\daykeeper-cli.exe"])
        self.assertIn("C:/bin/daykeeper-cli.exe pass APP-1234", text)
        self.assertIn("in Russian", text)
        self.assertNotIn("mr-open", text)
        self.assertNotIn("PowerShell", text)
        spaced = cc.instructions(cc.TeachOptions(statuses=("pass",)), ["C:\\Ann Lee\\daykeeper-cli.exe"])
        self.assertIn("PowerShell", spaced)
        self.assertNotIn("note", spaced.split("```")[1])

    def test_insert_replaces_and_remove_restores(self):
        self.path.write_text("# Mine\n\nKeep me.\n", encoding="utf-8")
        cc.insert_block("first", self.path)
        cc.insert_block("second", self.path)
        text = self.path.read_text(encoding="utf-8")
        self.assertEqual(text.count(cc.BEGIN), 1)
        self.assertIn("second", text)
        self.assertTrue(text.startswith("# Mine\n\nKeep me.\n"))
        self.assertEqual(cc.current_block(self.path), "second\n")
        self.assertFalse(cc.mentions_journal_elsewhere(self.path))
        self.assertTrue(cc.remove_block(self.path))
        self.assertEqual(self.path.read_text(encoding="utf-8"), "# Mine\n\nKeep me.\n")

    def test_an_older_hand_written_section_is_noticed(self):
        self.path.write_text("Run python /path/to/repo/inbox.py pass KEY\n", encoding="utf-8")
        self.assertTrue(cc.mentions_journal_elsewhere(self.path))
        self.assertIsNone(cc.current_block(self.path))

    def test_a_new_file(self):
        cc.insert_block("block", self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), f"{cc.BEGIN}\nblock\n{cc.END}\n")


if __name__ == "__main__":
    unittest.main()
