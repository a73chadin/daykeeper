"""Drop a verification result into the Daykeeper journal inbox (without opening the vault).

    python inbox.py pass APP-1234 APP-5678
    python inbox.py fail APP-2938
    python inbox.py bug  APP-4127 --note "duplicate of APP-4100"
    python inbox.py pass https://tracker.example.com/browse/APP-1 --date 2026-10-12
    python inbox.py review !42          # reviewed an MR
    python inbox.py mr-open !45         # opened an MR
    python inbox.py mr-merged !44       # merged an MR
    python inbox.py manual APP-101       # wrote manual test cases for the task
    python inbox.py auto APP-101         # wrote autotests for the task
    python inbox.py note "nightly run 1042 triage"
    python inbox.py themes               # the themes a note may name, one per line

Statuses: pass — tested successfully, fail — Test failed, bug — a newly filed bug;
review / mr-open / mr-merged — an MR (number, !number or link), manual / auto — test
cases for a task, note — any text. Activity goes into the report as lines under "1.".
With themes set (Settings → Journal → Report), a note that names one goes into line 1 as
that theme, once a day and in the order of the list; any other note goes there as written.
Only work is filed: an item written inside a git repository whose remote is not on the
tracker's or GitLab's host (a personal project) is dropped; --work / --personal decide
by hand.
Inbox file: inbox.jsonl in the data folder (%APPDATA%\\Daykeeper on Windows,
~/Library/Application Support/Daykeeper on macOS; or --inbox). The app picks it up when
the vault is opened and once a minute, filing the items by day."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from daykeeper.cli import inbox_main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(inbox_main())
