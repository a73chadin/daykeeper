"""Claude Code PostToolUse hook from the source tree: the same as `daykeeper-cli hook`
(daykeeper/cli.py). The app's "Claude Code" settings wire it up; by hand, in
`~/.claude/settings.json`, hooks → PostToolUse:
    {"matcher": "Bash|PowerShell|mcp__.*",
     "hooks": [{"type": "command", "command": "python \\"<repository path>/hooks/journal-inbox.py\\"", "timeout": 15}]}"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daykeeper.cli import hook_main  # noqa: E402

if __name__ == "__main__":
    sys.exit(hook_main())
