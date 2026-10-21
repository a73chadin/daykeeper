# Daykeeper Multitool

A personal multitool for your working day on Windows and macOS, all in one encrypted file
under one master password:

- **Passwords** — logins, API tokens, keys and cards, with expiry reminders and 2FA codes;
- **Bookmarks** — tiles with links, tied to password entries for one-click login;
- **Journal** — what was verified, failed and filed, turned into a standup report, and
  the same over a week or a month;
- **Notes** — cards by day with checklists and attached Markdown files;
- **Routine** — quick-launch scripts, commands, links and folders, on a schedule or on an
  event of an integration;
- **Integrations** — GitLab and Jira notifications about your merge requests, issues and
  watched branches;
- **Calendar** — own events and reminders, a month that gathers what every tab holds for
  a day, meetings from an `.ics` file.

Around the tabs: a quick window from anywhere on the computer, a morning digest, a
clipboard history kept encrypted, text templates, and sending a note or a merge request to
the AI assistant you already use (Claude Code, Codex).

Everything stays on your computer. The program file and the data folder keep the short
name, Daykeeper.

UI in English and Russian, light or dark (Settings → General; the theme switches at once).
Day-to-day usage and troubleshooting — [PLAYBOOK.md](PLAYBOOK.md); what changed between
releases — [CHANGELOG.md](CHANGELOG.md); how to report a weakness —
[SECURITY.md](SECURITY.md).

## System requirements

- **Windows 10 version 1809 or later, or Windows 11** (x64).
- **macOS 14 Sonoma or later** (Apple Silicon: M1 and later).
- Linux: from source only, not tested on every desktop.

The window is built on Qt 6 (PySide6, under the LGPL — see
[THIRD_PARTY.md](THIRD_PARTY.md)); these are the systems Qt 6.12 supports. Older systems
(Windows 7 / 8.1, Windows 10 before 1809, macOS 13 and older) ran the 1.x versions; the
1.x builds are no longer published, so such a system stays on the copy it already has.

## Run

Ready builds are on the [Releases](../../releases) page: an installer or a folder that
runs as it is on Windows, a disk image or a zip on macOS.

- Windows, installer — `Daykeeper-<version>-setup.exe`: for your user only, asks for no
  administrator rights, installs into `%LOCALAPPDATA%\Programs\Daykeeper` and adds a
  Start-menu entry; a desktop icon and the start with the system are boxes to tick on
  the way. A newer setup installs over the older one (a running Daykeeper is closed
  first: the setup asks). Remove it in Settings → Apps → Installed apps (or "Add or
  remove programs"): the uninstaller never touches your data — the vault, its copies and
  `settings.json` stay in `%APPDATA%\Daykeeper`, and its last message says so.
- Windows, portable — [`Daykeeper-win64.zip`](../../releases/latest/download/Daykeeper-win64.zip):
  unpack anywhere and start `Daykeeper\Daykeeper.exe` (the window is up in well under a
  second).
- macOS — `Daykeeper-<version>.dmg`: open it and drag Daykeeper onto Applications; or
  [`Daykeeper-macos.zip`](../../releases/latest/download/Daykeeper-macos.zip), unpack and
  drag `Daykeeper.app` to Applications.

**The first start: the builds are not code-signed.** Signing needs a certificate (a
Windows code-signing certificate, an Apple Developer ID) the project does not have, so the
systems warn once:

- Windows SmartScreen shows "Windows protected your PC" for the setup or for
  `Daykeeper.exe` from the zip: "More info" → "Run anyway". The setup, if Windows asks
  about it, names an unknown publisher; it asks for no administrator rights either way.
- macOS Gatekeeper stops the first start ("cannot be opened", or "damaged" for a
  download it distrusts). Once, either way:

  1. Through the settings: double-click the app and close the warning, then System
     Settings → Privacy & Security → Security → "Open Anyway" next to Daykeeper, confirm
     with the Mac's password or Touch ID and press "Open". (Right-click → Open no longer
     does this since macOS 15.)
  2. In Terminal, also when macOS says the app is damaged or "Open Anyway" is not there —
     this removes the "downloaded from the internet" mark:

     ```
     xattr -dr com.apple.quarantine /Applications/Daykeeper.app
     ```

  An update made by the app itself is not downloaded through a browser, carries no such
  mark and starts without the warning.

Whether a file really is the one the release built is a separate question from these
warnings: see "Updates" below and "Check a download" further on.

Settings → General → Start opens the app when you sign in (per user: the Run key on
Windows, a LaunchAgent on macOS, ~/.config/autostart elsewhere), optionally minimized;
it still asks for the master password. The entry always points at the copy of the app
that turned it on. A built app takes the entry over quietly at start only when the copy
the entry points at is no longer there (moved, or deleted after an update), and only when
it does not itself run from the temporary folder or from Downloads; an entry whose
Daykeeper is still on disk stays as it is.

**Updates.** Daykeeper asks GitHub once a day whether a newer release is out (Settings →
General → About: "Check for updates once a day", on by default; "Check for updates" asks
at once). That is one request to GitHub's API with the plain User-Agent
`Daykeeper/<version>` — nothing about you or your data, no other address. Pre-releases
(`2.1.0-beta.1`) are offered only when "Offer test versions" is ticked. A newer version
shows as a quiet note and as a line in About: "Version X is out — Update…". Nothing is
downloaded until you press it, read the release notes and press "Update now":

1. The vault is saved (an update never starts with unsaved changes) and copied to the
   `backup` folder, as before any save.
2. `SHA256SUMS.txt` and `SHA256SUMS.txt.sig` are downloaded and the signature is checked
   with the public key built into the app; then the file for your copy — the setup for an
   installed copy, `Daykeeper-win64.zip` for a portable folder, `Daykeeper-macos.zip` on
   macOS — and its SHA-256 is checked against the signed list. Any mismatch: the update is
   refused, says why, and the download is deleted. "Cancel" stops it at any point.
3. Installed: the setup runs silently, closes the app, installs over it and starts the
   new version. Portable folder: the new one is unpacked next to it as `Daykeeper.update`;
   once the app has quit a small script renames the old folder to `Daykeeper.old`, puts
   the new one in its place and starts it; the new one deletes `Daykeeper.old` when it has
   started. macOS: the same for `Daykeeper.app` (it must lie in a folder you can write to,
   such as Applications).

A run from the source tree and a preview window never update themselves; on Linux an
update is taken by hand.

*How a download is trusted.* Every release's `SHA256SUMS.txt` is signed in CI with an
Ed25519 key (`tools/sign_release.py`); the private half exists only as a secret of the
repository, the public half is in `daykeeper/update.py` (`PUBLIC_KEYS`, which can also
hold a spare key kept offline), and CI checks the new signature against it before
publishing. A file that was swapped on the release page or on the way does not match the
signed list, a list that was swapped does not match the signature, and the list's signed
first line names its release, so an older release cannot be offered as a newer one. The same checks by hand: PLAYBOOK, section 9.

**Coming from 1.x.** 2.0 opens the vault, `settings.json` and the journal inbox of 1.x
as they are. After that, do not open the vault with a 1.x build again: 1.x does not know
the calendar, the clipboard history with its switch and limits, the Jira settings, the
watched GitLab branches and the date of the last token check, the routines' event
triggers and project filters, and the hashes of accepted scripts, and drops all of it
when it saves.

From source (Python 3.13 — the version the builds and CI use, `pyproject.toml`;
`build.bat` makes its venv with it; PySide6 comes from `requirements.txt`):

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt      # macOS / Linux: .venv/bin/pip
.venv\Scripts\python main.py                        # macOS / Linux: .venv/bin/python main.py
```

`main.py [vault file] [--minimized] [--preview]`: `--minimized` starts in the taskbar (the
start with the system passes it), `--preview` opens a copy of a vault for a look and
deletes the copy when the window closes. Linux: the same command; the suite runs there in
CI without a screen (Qt's offscreen platform); there is no Linux build on the Releases page.

Build it yourself — `build.bat` on Windows builds the console helper
`dist\daykeeper-cli\` and then the app as a folder, `dist\Daykeeper\` (zipped to
`dist\Daykeeper-win64.zip`), which carries the helper inside; `build.sh` on macOS builds
`dist/Daykeeper.app` (zipped to `dist/Daykeeper-macos.zip`). Both build from one
PyInstaller spec, `packaging/daykeeper.spec`, install the build tools from
`requirements-build.txt` (exact versions with hashes), check the size of the result
(`packaging/check_size.py`: 95 MB for the Windows folder, 90 MB for the bundle) and print
its SHA256. The build leaves out the parts of Qt the app does not use
(`packaging/qt_filter.py`) and is a folder, not one file: the LGPL wants Qt's libraries as
files of their own, and a folder starts three times faster. The icon (`assets/icon.ico`,
`.icns`, `.png` — the file icon and the window icon) is drawn by `tools/make_icon.py`;
rerun it only after changing the design (needs Pillow).

Releases are built by GitHub Actions: a tag like `v2.1.0` (it must match the version in
`daykeeper/__init__.py`) runs the tests and builds on Windows and macOS, then attaches the
two zips, the setup (`packaging/daykeeper.iss`, Inno Setup), the disk image (`hdiutil`),
`SHA256SUMS.txt` and its signature `SHA256SUMS.txt.sig` to the release (a tag with
a suffix — `v2.1.0-beta.1`, `v2.1.0-rc.1` — makes a pre-release: still in development,
marked so on GitHub and never offered as the latest) (`.github/workflows/build.yml`,
actions pinned to commit hashes). Check a download:

```
certutil -hashfile Daykeeper-win64.zip SHA256    # Windows
shasum -a 256 Daykeeper-macos.zip                # macOS
gh attestation verify Daykeeper-win64.zip --owner a73chadin   # provenance: which workflow run built it
```

and compare with `SHA256SUMS.txt`; whether that list itself is the signed one — PLAYBOOK,
section 9.

The release also publishes a signed provenance statement for each file (GitHub's build
attestation): `gh attestation verify` proves the file came out of this repository's
workflow at a given commit. The binaries themselves are not code-signed — that needs a
certificate (a Windows code-signing certificate, an Apple Developer ID) that the project
does not have; until it does, Windows SmartScreen and macOS Gatekeeper will warn as
described above. `build.bat` also builds the setup when Inno Setup's `iscc` is on the
PATH, and `build.sh` the disk image.

The vault file lives in the data folder — `%APPDATA%\Daykeeper\vault.pkv` on Windows,
`~/Library/Application Support/Daykeeper/vault.pkv` on macOS; another path can be passed
as the first argument. Before a save the previous version is copied to `backup\` next
to it (the latest 20 and the last one of each of the past 14 days). A data folder left by
the previous name, PassKeeper, is renamed on the first start.

Shortcuts are Ctrl+letter on Windows and Cmd+letter on macOS; tooltips show the one for
your system. `Ctrl+1…7` and `Ctrl+Tab` switch the tabs. Settings → Tabs hides tabs and
changes their order. On macOS the app has the usual menu bar (the app menu with About and
Settings, File, Edit, Window).

## Passwords

- Entry kinds: login and password, API token, SSH key / certificate, bank card, note,
  other. Each kind starts with its own set of fields ("+ New entry"); the kind can be
  changed in the card, the fields are kept.
- An entry holds any number of "name — value" fields. "+ Add field" offers presets:
  token, login, password, site, mail, phone, valid until, PIN, 2FA key, private key —
  or a custom one. A field can be locked: the value is shown as dots and does not take
  part in search. So a login with a password and a token live in one entry.
- Per-field buttons: show, copy, generate a password, lock, delete. A secret value is in
  its field only while it is shown or typed into; otherwise the field holds nothing.
- Manual order: ▲ ▼ under the list, `Alt+↑` / `Alt+↓` or the right-click menu. A new
  entry goes to the end.
- Lists: the "List" switcher above the search, the gear next to it creates, renames and
  deletes a list (its entries move to the main list). Move an entry — the "List" field
  in the card or right-click → "Move to list". Search covers all lists and says where
  the hit lives.
- **Keys that run out.** A date in a "valid until" / "expires" field, or a certificate in
  an open field, is read (`daykeeper/expiry.py`; a secret field is never opened for this).
  The list shows a badge, the card a coloured "Expires in 3 days" line, the top bar a
  count with the list behind it, and a note pops up once a day after the unlock. How many
  days ahead to warn — Settings → Security (0 — off); "Do not remind" hides one key.
  The GitLab check puts its own token's last day on the token entry by itself.
- **2FA codes.** A "2FA key" field (an `otpauth://totp/…` address, as a QR code holds it,
  or the bare base32 key) shows the current six-digit code under it with the seconds left;
  copying it is a secret copy. A bookmark tied to the entry gets a shield that copies the
  code. TOTP only (`daykeeper/totp.py`); HOTP and Steam codes are not supported.
- Import from a variables file (Settings → Data → "Import from a .env file…"): `KEY=VALUE`
  lines are filed into the lists "Environments" (by prefix: `DEV_`, `STAGE_`, `PROD_EU_`…),
  "Integrations" (well-known services: `TELEGRAM_*`, `JIRA_*`, `GITHUB_*`…) and
  "Miscellaneous" (by the first word of the key). A plan without values is shown
  before writing. The main list is untouched; an entry with the same name gets the new
  fields. Own rules go first — in `settings.json`:

  ```
  "env_import": [{"prefix": "STRIPE_", "list": "Payments", "entry": "Stripe"}]
  ```
- Import from another password manager (Settings → Data → "Import from KeePass /
  Bitwarden…"): a KeePass 2.x XML or CSV export, a Bitwarden JSON (not encrypted) or CSV
  export (`daykeeper/importers.py`). The format is told by the content; the plan — counts
  only — is shown before anything is written; the vault as it was is kept beside it first,
  and the export file, which lies on disk in the open, is deleted afterwards unless you
  say no.
- Export (Settings → Data, `Ctrl+E`): the entries as text for reading or CSV for Excel,
  or everything as JSON (see "Data" below). The file is NOT encrypted; the app asks for
  the master password again.
- The clipboard is cleared 30 seconds after copying. On Windows a copied secret is
  marked so that the clipboard history (Win+V) and cloud sync do not keep it.
- Autosave one second after an edit. `Ctrl+S` saves at once.
- Locks (Settings → Security): the whole app after N idle minutes or when the window is
  minimized; a separate guard on the Passwords tab — the master password again or a PIN
  — that locks it again after N idle minutes or by the "🔒 Lock passwords" button. "No
  extra code" leaves just the master password at start. `Ctrl+L` locks the app by hand.
  Wrong codes are counted on every prompt: after three the prompt waits 1, 2, 4… s,
  after ten the whole app locks. A locked tab takes its list and card off the screen;
  nothing behind the guard is reachable from the keyboard.
- Search by name, note, field names and open values. Locked values are not searched.
- Change the master password in Settings → Master password; the current one is asked
  first, and a new one gets a hint of how hard it is to guess. Copies made under the old
  password are re-encrypted, older automatic ones are deleted.
- Right-click → "Make a bookmark…" turns an entry into a bookmark tile.

Shortcuts: `Ctrl+N` new entry (login and password), `Ctrl+F` search, `Ctrl+E` export,
`Delete` in the list — delete. Without the mouse: `Ctrl+F`, type, `Enter` (the first hit
is on the card), `Ctrl+Shift+C` copies its password, `Ctrl+B` its login; `↓` walks from
the search into the list, `Esc` clears the search. The list is a model over the vault:
10 000 entries open in a fraction of a second.

## Bookmarks

Tiles with links to the services you use, in groups (the gear above the group list
creates, renames and deletes them). A tile has an avatar — the first letter, two letters
or an emoji, in one of eight colours, or the site's own icon and colour (Settings →
General, "Load site icons and colours") — a title and the host. A click opens the address.

A tile can be tied to a password entry: it then shows the entry's login (click — copy)
and copies its password; "↗ + 🔑" opens the address and copies the password in one go;
👁 shows the password for ten seconds; an entry with a 2FA key adds the shield, which
copies the code of the moment (`Ctrl+Enter` on the selected tile does the same). When the
Passwords tab is guarded and locked, the password row and the shield ask for the code
first. The editor ties a new bookmark to the entry with the same host by itself. Deleting
an entry unties its bookmarks. A bookmark opens web addresses only (`http`, `https`,
`mailto`). From the keyboard: `Tab` to a tile, `Enter` opens, `Shift+Enter` opens and
copies the password, the menu key opens its menu. The tiles are painted by one widget
(`daykeeper/qt/widgets/tiles.py`): a tab of 300 tiles opens in well under a second. A
site's icon is decoded by the app's own small decoder (`daykeeper/siteicon.py`), never by
Qt's image loaders. Stored in the encrypted file (key `bookmarks`); model —
`daykeeper/bookmarks.py`, tests — `tests/test_bookmarks.py`.

## Journal

**Its shape is yours** (Settings → Journal, `daykeeper/journal_layout.py`): ready-made
layouts — tester (verified / failed / new bug, MR reviews and test cases; the default),
developer (tasks done / in progress / bugs fixed, MRs and code review), analyst or manager
(tasks, documents, decisions, meetings), plain diary (done / in the way) — and then any
column or kind of activity can be renamed, hidden, moved, added or removed. A kind has a
role: MR numbers, test cases (one shared "New tests" line), a list, or notes that go into
line 1. Each has a word for line 1 of the report and a command word for `daykeeper-cli`
(`pass`, `review`, or one made from the label — «Созвоны» → `sozvony`). The layout lives in
`settings.json` (key `journal`), so the helper, the hook and "Teach Claude" follow it.
Nothing written is deleted: a hidden or removed column keeps its items in the vault.

The "Journal" tab next to the passwords. A day is a card: "1. Done" (one line), three
columns "Tested successfully / Test failed / New bug" (one issue key per line, tracker
links turn into keys), "2. Plans", "3. Absence / extra info" ("None" by default). Stored
inside the same encrypted file. The buttons under the day stay at the bottom of the pane;
only the day above them scrolls.

- "Build report" (`Ctrl+R`) assembles the standup text and copies it to the clipboard:
  issue keys and MR numbers expand into tracker and GitLab links.
- **A week, a month, any dates** (`Ctrl+Shift+R`, `daykeeper/stats.py`): counts per column
  and per kind of activity of the layout in use, a heat map of the days (a click opens the
  day), a row per week and the most frequent notes; the same as a Markdown table to copy
  or save. A renamed or added column is counted as it is.
- ⋯ → "Links…" — the tracker address (Jira: key `APP-1` → `<address>/browse/APP-1`), the
  GitLab address and the own repository (`group/repo`: its MRs are written short as
  `!42`, others as a full link). Stored in `settings.json` next to the vault
  (`jira_url`, `gitlab_url`, `gitlab_project`). Empty — keys and numbers stay text.
- The "Add" line with the buttons "Passed / Failed / Bug" and the MR menu adds by hand;
  several keys can be typed with spaces or commas. The columns and the "Activity"
  block are also edited directly (a line like `MR reviews: !42, !43`). When Jira is
  connected and its settings name a transition for a verdict, a key added to "passed" or
  "failed" offers that move in a corner note ("APP-1 → Done?"); only a click sends it.
- **Text templates** (Settings → Templates, `daykeeper/templates.py`): a verification
  comment, a defect report, an MR description to start from, and your own. Placeholders
  are filled from where the template is used — the selected line of the journal, a note
  card, the quick window: `{key}` `{key_url}`, `{mr}` `{mr_url}`, `{today}`,
  `{date:%d.%m.%Y}`, `{me}`, `{clipboard}`, `{note_title}` `{note_items}`; `{?Environment}`
  asks in one small form before the text is made. The text goes to the clipboard, and
  the task link opens when the template says so.
- "Activity" — MR reviews, opened and merged MRs, new manual test cases and autotests,
  miscellaneous. In the report it goes as lines under "1.". An empty "1. Done" line is
  built by itself from the phrases of the day's columns and kinds, such as "tested tasks,
  reviewed MRs, new manual/autotests".
- **Inbox from sessions.** Scripts and AI sessions drop results into
  `inbox.jsonl` next to the vault without opening the app. The command is the console
  helper: `daykeeper-cli.exe` in the built app (put into `%LOCALAPPDATA%\Daykeeper\bin\daykeeper-cli\`
  when Claude Code is connected), `python inbox.py` in the source tree:

  ```
  daykeeper-cli pass APP-1234 APP-5678
  daykeeper-cli fail APP-2938
  daykeeper-cli bug  APP-4127 --date 2026-10-12
  daykeeper-cli review !42
  daykeeper-cli mr-open !45
  daykeeper-cli mr-merged !44
  daykeeper-cli manual APP-101
  daykeeper-cli auto APP-101
  daykeeper-cli note "nightly run 1042 triage"
  daykeeper-cli --data D:\vaults pass APP-1      # a vault outside the default folder
  ```

  The app picks the file up when the vault is opened, when the file changes and once a
  minute, files the items by day (duplicates are skipped) and deletes the file.
  "Read the inbox now" under the day in the Journal — by hand. The helper and `inbox.py` need neither Qt
  nor `cryptography`: they start in a fraction of a second on any Python.
- **Claude Code and Codex** (Settings → AI assistants). Everything is done from the app, no
  file is edited by hand; each assistant is connected and taught on its own (Codex: the hook
  goes into `~/.codex/hooks.json`, the instructions into `~/.codex/AGENTS.md`, and Codex asks
  to trust a new hook once with `/hooks`):
  - "Connect" puts the helper in place and adds a PostToolUse hook to
    `~/.claude/settings.json` (only for `Bash`, `PowerShell` and MCP tools). The rest of
    the file stays as it was; a copy `settings.json.before-daykeeper` is kept before the
    first change; a file with a JSON error is not touched. "Disconnect" removes the entry.
    An older entry (the source-tree script, a moved app) shows as "connect again".
  - "What counts as an event" — ready-made sets: GitLab (`glab`: MR opened, merged,
    approved, a new thread in someone else's MR — the author is asked through `glab` and
    cached in `hook-cache.json`), GitHub (`gh pr create / merge / review`), the issue
    tracker (a new issue, a move to Test failed; REST and the Atlassian tools), verdict
    comments ("Tested on …" → pass, or fail when it says so).
  - "Own rules" — “if the command (the answer) has … — write …”: a piece of text or a
    regular expression, an optional success word in the answer, the journal line kind,
    and what to write (the issue key, the MR, the matched text or a fixed text).
  - "Where it works" — every project, or only some folders; and folders never to log.
  - "Test a command" — paste a command and its answer, see what would be written and by
    which rule. "Recent triggers" — the last events with the rule that found them, from
    `hook-log.jsonl` (no commands or answers are logged: they can hold tokens).
  - "Teach Claude to keep the journal…" — a block for `~/.claude/CLAUDE.md` (which kinds,
    the note language, the text can be edited) between `<!-- daykeeper:begin/end -->`
    markers: inserted again, it replaces itself; "Remove" takes it out.
  - The rules live in `settings.json` next to the vault, key `hook`. Code:
    `daykeeper/hookrules.py` (matching), `daykeeper/cli.py` (the helper),
    `daykeeper/claudecode.py` (the files), `daykeeper/qt/dialogs/settings_claude.py`
    (the section).

**Undo and redo** — `Ctrl+Z` / `Ctrl+Y` (or `Ctrl+Shift+Z`) and ↶ ↷ in the top bar: the
last changes to passwords, the journal, notes, bookmarks, routines and the calendar (100
steps or 20 MB of kept state, whichever comes first; changes 0.7 s apart are one step). A
step keeps only the objects that changed — one entry, one day, one tile — so the history
stays small on a large vault. Inside a text field `Ctrl+Z` undoes the typing. Items from
the inbox, a routine's run, the script a routine accepted and a bookmark's fetched icon are
not undone (`daykeeper/undo.py`).

## Notes

The "Notes" tab — cards by day (`daykeeper/notes.py`, view —
`daykeeper/qt/pages/notes.py`): a title, text, a checklist and attached files.

- Checklist items are added from the "+ Add item" row at the bottom of the list: Enter
  adds and keeps typing. A tick moves the item under "Done (n)", which folds; "Clear"
  removes done items. A progress bar sits above the list. `Alt+↑/↓` moves an item,
  Backspace in an empty item removes it, right-click opens the item menu.
- "⋯" holds the card settings: a wide card over two columns, folding of done items,
  nine colours with an accent stripe, files, carrying undone items over to today,
  deletion, "Send to AI…". 📌 pins a card to the top of the day.
- 📎 attaches files by path (the vault keeps the link, so the file is always its current
  version). Markdown and text open in a built-in viewer: headings, lists, task boxes
  that tick in the file, quotes, code, tables, links; "Edit" switches to the raw text and
  saves it back. Other files open in the system app. "📎 With files" on the left lists
  every card with files.
- **From a call…** (🎙 on the left): a transcript (`.txt`, `.md`, `.vtt`, `.srt`) or a
  recording of a call becomes a card of today with a summary, notes and tasks, the
  transcript attached. A recording is transcribed on your computer by whisper.cpp
  (`whisper-cli` and its model, from the settings or the PATH; ffmpeg turns mp3 / m4a /
  ogg / webm into the WAV it reads), the summary is written by Claude Code over the quiet
  channel (see "Send to AI"); every item can be unticked or edited before the card is
  made. Without Claude Code the card holds the title and the transcript.
  `daykeeper/calls.py`, the window `daykeeper/qt/dialogs/calls.py`.
- **A downloaded transcript is offered.** While the app is unlocked it looks at Downloads
  (Settings → General → Calls: on by default, another folder can be set): a `.txt`,
  `.vtt` or `.srt` that comes there and reads as a call — subtitles, or lines of speech
  with time codes going forward, as a meeting service's "download the transcript" gives —
  gets a notice for a minute; a click opens "From a call…" on that file. Only files that
  came after the first look are offered, each once; only the first 64 KB of a new text
  file are read to tell, and nothing leaves the computer until the click.
  When the transcript's first line names the meeting in quotes, the card takes that name.
- Search across all days covers titles, text, items and file names; "Unfinished" lists
  every card with open items.

A card is one painted widget: a field becomes a real editor only while it is edited, so a
busy day opens in a few hundredths of a second. Stored in the same encrypted file (key
`notes`). Tests — `tests/test_notes.py`, `tests/test_markdown.py`, `tests/qt/test_qt_notes.py`.

## Routine

The "Routine" tab — quick-launch tiles (`daykeeper/routines.py`, view —
`daykeeper/qt/pages/routines.py`). A tile starts a script (`.py`, `.ps1`, `.bat`/`.cmd`,
`.sh` or a program, with arguments and a working folder), a shell command, a link, a
folder or a file. ▶ runs it: scripts and commands run in the background, the tile shows
how it ended and the output window is a click away (the live log, Stop — it ends the
script with everything it started — and Run again); "Open in a console" is for
interactive scripts. `Tab` reaches a tile, `Enter` runs it.
A `.ps1` script always runs with `-ExecutionPolicy Bypass`: adding a tile is the
consent to run that script, whatever the machine-wide PowerShell policy says — and the
consent is to the file as it is, not to the path: saving the tile or running it by hand
records a hash of the script (and of what a shortcut points to), and an automatic run
starts only that version. A changed or never-accepted script waits for a run by hand,
which asks before accepting the new version; the editor warns when the script sits on a
network share, in Downloads or outside the profile folder.

A routine can start by itself each time the vault is unlocked, or at a time of day on
chosen weekdays — once a day; if the app was closed at that time, it runs when the app
is opened later that day. **Or on an event** ("Also run on an event" in the editor): an
MR merged, a watched pipeline failed, an issue moved to a watched status — any rule of
the GitLab and Jira watchers, optionally only for some projects. The run gets the event as
environment variables (`DAYKEEPER_EVENT`, `DAYKEEPER_SOURCE`, `DAYKEEPER_TITLE`,
`DAYKEEPER_URL`, `DAYKEEPER_PROJECT`, `DAYKEEPER_REF`, `DAYKEEPER_TEXT`); a link, a script
or a program also gets `{title}`-like braces of its arguments filled, each value as one
argument. A shell command never gets the values typed into its line (an MR title is
anyone's text): it reads the variables. A storm of events starts a limited number of runs,
and the run log says how many were cut.

Automatic runs are quiet: the result goes to the run log on the left (double-click shows
the output), a failure also pops up in the corner of the screen. Routines live in the
encrypted, authenticated file, so nothing can be added to the auto-start list without the
master password. Tests — `tests/test_routines.py`, `tests/qt/test_qt_routines_events.py`.

## Integrations

The "Integrations" tab: GitLab and Jira, each checked in the background every N minutes
(2–60) while the vault is open. Their events share one feed on the tab (unread ones are
counted on the tab button), pop up in the corner of the screen (a click opens the MR or
the issue), and the taskbar button asks for attention. The first check of each only
remembers the state.

**GitLab**: the address, a password entry whose secret field holds a personal access
token with the `read_api` scope; "Watch" turns it on. Rules:

- an approval on my MR, all threads on my MR resolved, its pipeline failed, it was merged;
- I was asked to review an MR; my threads were resolved or answered in an MR I review;
- **watched branches** ("+ Add a branch…": a project, a branch, optionally only the
  pipelines started by a schedule or by a push): the newest pipeline failed, is green again, or new
  commits landed — whoever started them.

"Projects" lists the projects with my open MRs after the first check; ticked ones are the
only ones watched, none ticked — all. A check asks about every MR once an hour; in
between, new approvals and pipelines come with one request a project, and the threads of
an MR are read only when its count of notes changed or my threads in it are still open.
Once a day the check also asks the token's last day and puts it on the token entry's
"valid until" field, so the expiry reminders cover it.

An MR in the feed has its actions by my part in it: **Preview** shows it as one Markdown
page in the built-in viewer (the header, the description, the files with the lines added
and removed, each diff; a big MR is cut, the rest is left to the browser; read only, web
links only, `daykeeper/mr_preview.py`), and **Review**, **Fixes after review** and
**Brief** hand it to an AI assistant (see "Send to AI"; the brief comes back above the
diff).

**Jira** (`daykeeper/jira.py`): the address, the e-mail (Jira Cloud) and an entry with the
token — an API token for `*.atlassian.net` (Basic with the e-mail), a personal access
token for a server of your own (Bearer); a JQL filter (by default my issues, the ones I
reported and the ones I watch) and the watched statuses. Rules: an issue moved to a
watched status, a bug assigned to me, a new comment in my issue, my issue sent back to
work. Verdict transitions — which transition to offer after "passed" and after "failed" in
the journal (see "Journal"). A check asks only for what was updated lately.

Watcher logic — `daykeeper/gitlab.py` and `daykeeper/jira.py`, tests against a fake API —
`tests/test_gitlab.py`, `tests/test_pipelines.py`, `tests/test_jira.py`; nothing in the
tests touches the network.

## Calendar

The "Calendar" tab (`daykeeper/calendar_model.py`, view — `daykeeper/qt/pages/calendar.py`):
a month on one grid. A day shows its own events, the keys that run out and the routines
with a time as chips, and beside the number how many journal items, open note items and
integration events it has; a click shows them in the panel on the left, where "Open the
journal day" and "Notes of the day" lead on.

- "+ Event": a title, a date, a time or all day, a repeat (weekly, monthly, yearly, until a
  date), a reminder (minutes, hours or days before), a note and a link. The reminder pops
  up once, never while the vault is locked.
- "⋯ → Import .ics…": meetings from a calendar file (Outlook, Google and the like). The
  file is only read as text, nothing in it is fetched or run. Weekly and daily repeats,
  `UNTIL` / `COUNT`, a reminder before the start and a moved occurrence are kept;
  importing the same file again updates its events instead of adding copies. A time with
  a time zone is taken as written (the zone is not converted).

Stored in the encrypted file (key `calendar`). A new tab: a vault saved by 1.x loses it
(see "Coming from 1.x").

## Around the tabs

- **Quick window** — `Ctrl+Shift+Space` from anywhere on Windows (Settings → Quick access
  changes the keys), `Ctrl+K` inside the app on every system. Type to find an entry, a
  bookmark, a recent note, a routine, a template or a command (`/lock`, `/digest`,
  `/today`, `/clip`, `/settings`): `Enter` does what the result is for (copies a password
  — behind the Passwords guard — opens a bookmark, runs a routine), `→` or `Tab` lists its
  other actions, `Esc` hides it. A line that reads as a journal entry — `pass APP-12
  APP-13`, `APP-12 verified`, `!7 opened yesterday`, `note nightly run triage`, with the
  words of your journal layout in English or Russian — shows what would be filed, and
  `Enter` files it. No secret value is in its index (`daykeeper/quick.py`).
- **Morning digest** — once a day after the first unlock (Settings → General), and any
  time by ☀ on the top bar or `Ctrl+D`: the plans of the journal, open note items, MRs
  waiting for my review, keys that run out this week, what happened at night (watched
  pipelines, failed routines) and the unread notifications. A line opens what it is
  about (`daykeeper/digest.py`).
- **Clipboard history** — off until switched on (Settings → Security): what you copy
  while the vault is open is kept, sealed with the vault's key, so a text copied a while
  ago can be put back (the top-bar button, `/clip`). Never recorded: the passwords
  Daykeeper copies, what other password managers mark as private, and — when the setting
  is on — text that looks like a token or a key; nothing while the vault is locked or the
  history is paused. Pinned texts stay when it is cleared or full; switching it off forgets
  everything (`daykeeper/cliphistory.py`).
- **Send to AI** — a note card or one of its items ("⋯ → Send to AI…"), and an MR of the
  feed, become an assignment for Claude Code or Codex. Daykeeper calls no model itself and
  keeps no key for one: the dialog shows the whole text — exactly what leaves the
  encrypted vault — which is written to `<data folder>\assignments\` and handed over
  either in a console window in the project's folder (`terminal`) or to `claude -p` whose
  answer comes back into the app (`quiet`; "Save to note" keeps it). Settings → Send to AI
  holds the assistant and channel to start with, the model of the quiet channel, the
  project map (a GitLab path → the folder of its checkout), the assignment templates and
  the clean-up of old assignment files. A lock stops the quiet runs and forgets their
  answers (`daykeeper/assistants.py`, `daykeeper/assistant_runs.py`).
- **Error log** — Settings → General → About → "Error log…" (on macOS also in the app
  menu): what `errors.log` and `crash.log` next to the vault hold, newest first, and the
  report to send — the versions, the last errors and the last crash, scrubbed of the home
  folder, the user name, e-mails, tokens and every web address but its host. What "Copy
  report" and "Save report…" hand over is exactly what the window shows. After a crash
  the next start says so once (`daykeeper/errlog.py`).

## Languages

English and Russian. The language is picked in Settings → General (or the link on the
unlock screen) and stored in `settings.json`; without a setting the system language
decides (Windows UI language, macOS locale, `LANG` elsewhere). Code and comments are
English; the Russian catalog is `daykeeper/locale_ru.py`. Report headings follow the UI
language.

## Data: export, import, copies

Settings → Data:

- **Everything as JSON** — the export dialog's third format: every tab (entries, journal,
  notes, bookmarks, routines, integrations, the calendar) as readable JSON, to keep or to
  move. Not the lock settings: a PIN hash means nothing outside, and an import must not
  switch locks off. Not the clipboard history either: it stays sealed in the vault.
- **Import everything from an export** replaces every tab with such a file after the master
  password. The vault as it was is kept beside it first (`vault-before-import-<time>.pkv`).
- **Import from KeePass / Bitwarden** — see "Passwords".
- **Copies** — the automatic ones from `backup\` and the ones kept beside the vault: a
  conflict, before an import, before a restore. "Open" shows a copy in a second window, on
  a duplicate in a temporary folder, so looking changes nothing; "Restore…" makes it the
  vault (the current one is kept beside it first) and locks the app.
- **A conflict** — another window or a sync tool saved the vault file: nothing is written
  over it; what was changed here is offered as `vault-conflict-<time>.pkv` on close or lock.
- An error in a click, a timer, a thread or Qt itself goes to `errors.log` next to the
  vault (the traceback, no field values) and a short note; a crash of the process to
  `crash.log`. See "Error log" above.

## Security

- The master password becomes a key through Argon2id (64 MiB, 6 iterations, about 0.1 s
  on a desktop of 2026; files written with 3 open as before), on a background thread: the
  window keeps painting while it works. Wrong passwords on the unlock screen wait like
  wrong PIN codes do.
- The value of every secret field is sealed a second time (AES-256-GCM) with a field key
  that lives inside the encrypted content. In memory it stays sealed and is opened only
  for the moment it is shown or copied, so the content decrypted at unlock, each autosave
  and the undo history hold no secret in the clear. The clipboard history is sealed with
  the same key. A lock zeroes the master key and the field key. The file is marked
  `PKV2`; version 1.0 refuses it rather than open it with empty passwords; a `PKV1` file is
  converted on the first unlock (the old one stays in `backup`).
- The content is encrypted with AES-256-GCM. A tampered or damaged file does not open.
  The Argon2 parameters in the file header are checked before a key is derived and may be
  at most four times the defaults, so a forged header cannot exhaust memory or freeze the
  window.
- Everything — entries, journal, notes, bookmarks, routines, the calendar, the clipboard
  history, integration settings and the lock settings — is inside the encrypted file;
  `settings.json` holds only the window, the language, the report links, the journal
  layout, the hook rules, the templates and the AI settings (nothing secret).
- Writes are atomic: a temporary file first, then a rename. On macOS and Linux the data
  folder is `0700` and the files are `0600`.
- Idle locks, the Passwords tab guard with its count of wrong codes, the private clipboard
  and re-encrypted copies — see "Passwords" above. A lock closes every window that shows
  vault data (the settings, an opened file, the output of a run, the digest, the AI
  answers).
- The GitLab and Jira tokens go only over https and only to the server the user typed — a
  redirect to another host or another port of it stops the request.
- A link in an attached document never starts a program (its folder opens); a rule of the
  hook never writes text that looks like a token or a key into the journal.
- The CSV export puts an apostrophe before values starting with `=`, `+`, `-` or `@`, so
  spreadsheets show them as text instead of running them as formulas.
- The Qt libraries are separate files of the build and are not checked at start (the LGPL
  lets the user replace them); the app clears `QT_PLUGIN_PATH` before Qt starts, so a
  plugin folder from the environment is not loaded.
- The update check is one request a day to GitHub's API (it can be switched off); an
  update is downloaded only after a click and used only when the signature of
  `SHA256SUMS.txt` holds for the key built into the app and the file's SHA-256 is the
  one listed (see "Updates" above).
- A forgotten master password cannot be recovered.

Limitation: Python cannot wipe strings from memory. A secret that was shown or copied,
and the entry open on the card, may stay in memory after the lock until it is overwritten;
the keys are wiped, the rest of the secrets never were plain. Someone who can read the
memory of the running app while it is unlocked can read the vault — as with any password
manager.

## Code map

`daykeeper/` is two layers. The core, without Qt — the models (`vault`, `journal`,
`notes`, `bookmarks`, `routines`, `integrations`, `calendar_model`, `cliphistory`,
`security`, `settings`), the GitLab and Jira clients (`gitlab`, `jira`), the site icons
(`siteicon`, `pngimage`), the Markdown parser, the import and export (`export`,
`envimport`, `importers`), `expiry`, `totp`, `digest`, `stats`, `templates`, `quick`,
`assistants`, `calls`, `mr_preview`, `revisions` (what changed since a tab was drawn),
`layout` (geometry maths), `tabs`, `palette` (colours and fonts, the light and dark
themes), `boot` (the theme from the settings before the window), `errlog` (`errors.log`),
`i18n` with `locale_ru`; and the services — `session` (the vault's life: open, save, lock,
the inbox, the export), `jobs` (a thread pool read on the window's thread), `runner` (a
routine's subprocess), `assistant_runs`, `hub` (the event hub), `hotkey`, `clipboard`,
`autostart`, `update` (the check for a new release, the signed download and the swap
scripts), `win32` (every call into Windows). The console helper (`cli`, `cli_main.py`,
`inbox.py`) imports only `settings`, `i18n`, `journal` and `hookrules`.

The window — `daykeeper/qt/`, the only package that imports PySide6: `app` (the main
window, the top bar, the shortcuts, the macOS menu), `unlock` (the unlock screen), `lock`
(the locks), `theme` (the two themes as objects, the style sheet), `icons` (vector
icons), `timers`, `jobs`, `errors` (every event handler is guarded), `pages/` (one module
per tab) and `dialogs/` (the settings sections, the data dialogs, the viewer, the quick
window, the digest, the clipboard history, the AI dialogs, the error log, the update; the calendar's
event editor is `event_editor` in `pages/calendar.py`) and `widgets/`. A tab reaches the window only through the methods of its
`shell`; a tab is made on its first visit. `main.py` starts it.

## Tests

```
.venv\Scripts\python -m unittest discover tests
```

The core is tested without a window. The window's tests (`tests/qt/`) run without a
screen on Qt's offscreen platform — every tab, the settings, the locks and the guard, a
full smoke run on a temporary vault (`tests/qt/test_qt_full_smoke.py`); none of them
touches the real clipboard, the autostart entry, the network or the assistants' files.
`tests/test_qt_package.py` holds the borders: PySide6 only inside `daykeeper/qt`, the
helper never reaching it, nothing importing tkinter. `tests/bench_qt_tabs.py` and
`tests/bench_qt_shell.py` print the speed of the window against its targets.
`tools/screenshots.py <folder> [light|dark] [en|ru]` draws every tab and settings section
of a sample vault into pictures (`.github/workflows/screenshots.yml` runs it on macOS).

Lint and types — `ruff` over everything, `mypy` over the package and the entry points
(`pyproject.toml`); CI runs both before the tests:

```
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m mypy
```

## License

MIT, see [LICENSE](LICENSE). The build carries Qt and Qt for Python under the LGPL-3.0 —
see [THIRD_PARTY.md](THIRD_PARTY.md) and `licenses/`.
