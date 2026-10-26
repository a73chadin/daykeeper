# Changes

## Not released yet

**Notes**
- The days of a week stand side by side as columns, as many as the width holds (four on
  a usual screen), the rest in the next row; the week's plans stay above them at full
  width. A day's title drops the year, which the week's title already has.
- "⤴ to today" is offered only on a past day (and "⤴ to this week" on a past week): a
  card planned for a later day waits for its day.

## 2.3.0 — plans of the week

**Notes**
- Plans of the week. Each week stands on the left above its days ("📅 Week 26.10 – 01.11",
  done / total items of its plans). A click shows the week's plans and, under them, each
  of its seven days with its cards, so the plan of the week and of the days are on one
  screen. "+ Plan" adds a plan of the week, "+ Note" beside a day adds a card on that
  day, and the week stays on screen. "+ Note" on the left adds a plan when a week is
  shown. A plan of a past week has "⤴ to this week". Plans are found by search and
  gathered in "Unfinished" like any card. A vault with plans opened by an older build
  shows each plan on the Monday of its week.
- Two cards of half the width no longer fall into separate rows when the scroll bar
  takes an odd number of pixels.

## 2.2.1 — the update check no longer times out

**Updates**
- The check for a new version and the download no longer stall: they asked GitHub to
  close the connection after the answer, and on some networks the answer then stopped
  halfway — "Could not check for updates: The read operation timed out" every other time.
  A copy that still has this (2.2.0 and older) may need "Check for updates" pressed twice,
  or the setup downloaded from the releases page.
- An error under About takes the whole width instead of a word or two a line.

**Build**
- certifi 2026.10.19 (the CA list of the macOS build), after an advisory against
  2026.10.18.

## 2.2.0 — call transcripts from Downloads, note themes in the report

**Notes**
- A call's transcript downloaded to Downloads is offered as a note: a notice for a minute,
  a click opens "From a call…" on the file. It is told by its look — subtitles, or lines
  of speech with time codes in order — so it works with any meeting service that lets
  you download the transcript as text. On by default; Settings → General → Calls turns it
  off or names another folder. Files that were there before are never offered.
- A transcript whose first line names the meeting in quotes gives the card that name
  instead of "Call <date>".
- "From a call…" reads a transcript saved in UTF-16 or in the Windows Cyrillic code page,
  not only UTF-8.

**Journal**
- Note themes (Settings → Journal → Report): a note that names a theme of the list goes
  into line 1 of the report as that theme, once a day and in the order of the list; other
  notes go there as written. `inbox.py themes` lists them; "Teach Claude" puts them into
  the instructions; a note that is not a theme gets a hint on the console.
- A task failed and then passed the same day shows only as passed (and the same for any
  columns that share a report phrase, like "in progress" and "done").

## 2.1.0 — an installer and signed updates

**Install**
- Windows: `Daykeeper-<version>-setup.exe` (Inno Setup, `packaging/daykeeper.iss`) — for
  your user only, no administrator rights, into `%LOCALAPPDATA%\Programs\Daykeeper`;
  a Start-menu entry, an optional desktop icon, an optional start with the system (the
  same Run value the app writes), an uninstall entry in Settings → Apps. A newer setup
  installs over the older one and closes a running copy first (it asks). The uninstaller
  removes the start with the system only when it starts this copy, and never touches the
  data folder — its last message says so. The zip stays for a portable folder.
- macOS: `Daykeeper-<version>.dmg` with the app and a link to Applications; the zip stays.
- The builds are still not code-signed: the README says what SmartScreen and Gatekeeper
  show at the first start and how to get past it.

**Updates**
- Once a day (on by default; Settings → General → About → "Check for updates once a day")
  the app asks GitHub which release is the latest — one request with the User-Agent
  `Daykeeper/<version>`, nothing else. A newer version is a quiet note and a line in
  About: "Version X is out — Update…"; "Check for updates" asks at once. Pre-releases
  only when "Offer test versions" is ticked.
- "Update…" shows the release notes; only "Update now" downloads. The release's
  `SHA256SUMS.txt` must carry a valid Ed25519 signature (`SHA256SUMS.txt.sig`, made in CI
  by `tools/sign_release.py`) for the key built into the app, and the downloaded file
  must have the SHA-256 the list gives; otherwise the update is refused and the download
  deleted. Cancel works at any point.
- Before anything is replaced the vault is saved (an update never starts with unsaved
  changes) and copied to `backup`. An installed copy runs the new setup silently and
  starts again; a portable folder or a macOS bundle is unpacked next to itself and
  swapped by a small script after the app quits, the old one kept as `Daykeeper.old`
  until the new one has started. A run from source and a preview window never update.

**Build**
- The release adds the setup, the disk image and `SHA256SUMS.txt.sig`; all are in the
  checksums and in the provenance attestations. A tag must match the version in
  `daykeeper/__init__.py`. `build.bat` builds the setup when `iscc` is on the PATH,
  `build.sh` the disk image.

## 2.0.0 — one window, on Qt

> **⚠ After 2.0, do not open the vault with a 1.x build.** 2.0 reads the vault, the
> settings and the journal inbox of 1.x as they are, but 1.x does not know the calendar,
> the clipboard history with its switch and limits, the Jira settings, the watched
> GitLab branches and the date of the last token check, the routines' event triggers and
> project filters, and the hashes of accepted scripts: when a 1.x build saves the vault,
> they are gone. Keep a copy (Settings → Data) before the first start of 2.0 if you may
> want to go back; the automatic copies in `backup\` are made by both.

**System requirements.** Windows 10 version 1809 or later, or Windows 11 (x64); macOS 14
Sonoma or later (Apple Silicon). Windows 7 / 8.1, older Windows 10 and macOS 13 and older
ran 1.x and cannot run 2.0. The 1.x releases are no longer published.

**The window**
- The app has one window, on Qt (PySide6 6.12, Qt Widgets); the Tk window and everything
  that only it used are gone from the repository. `main.py` starts the Qt window (it was
  `main_qt.py`); `--minimized`, `--preview` and the vault path work as before, and the
  theme still comes from the settings — now it also switches at once, without a restart.
- Every tab builds in tens of milliseconds: 10 000 password entries open in about 0.2 s,
  a busy day of notes in under 0.1 s, a tab of 300 bookmark tiles in 0.05 s.
- A secret value of a password entry is in its field only while it is shown or typed
  into. A locked Passwords tab takes its list and card off the screen; nothing behind the
  guard is reachable from the keyboard, and windows that name entries close with it.
- A Calendar tab (`Ctrl+7`): own events with repeats and reminders, a month that gathers
  the journal, the notes, the expiring keys, the routines and the integration events by
  day, meetings imported from an `.ics` file.
- macOS: the usual menu bar (the app menu with About and Settings, File, Edit, Window).

**New**
- Keys that run out: dates in "valid until" / "expires" fields and certificates in open
  fields are read; a badge in the list, a line on the card, a count on the top bar with
  its list, a note once a day. The GitLab check puts its token's last day on its entry.
- 2FA codes: a "2FA key" field shows the current TOTP code; a tied bookmark tile copies it
  with its shield (`Ctrl+Enter`), behind the Passwords guard.
- The morning digest (☀, `Ctrl+D`, and once a day after the first unlock): plans, open note
  items, MRs to review, keys that run out, the night's pipelines and failed routines.
- The quick window: `Ctrl+Shift+Space` from anywhere on Windows, `Ctrl+K` in the app —
  find and copy, open, run; a line like `pass APP-12` files a journal entry.
- The clipboard history (off until switched on in Settings → Security), sealed with the
  vault's key; Daykeeper's own secrets, privately marked text and token-like text are
  never recorded.
- The journal over a week, a month or any dates (`Ctrl+Shift+R`): counts, a heat map, a
  table by weeks, the same as Markdown.
- Text templates with placeholders and questions (Settings → Templates): a verification
  comment, a defect report, an MR description and your own.
- Routines on an event: a GitLab or Jira event can start a routine, which gets the event in
  `DAYKEEPER_*` variables; a storm of events is limited.
- Jira: a watcher next to GitLab (Cloud with an API token, a server of your own with a
  personal access token), its events in the shared feed, and the verdict transition
  offered after a key goes to "passed" or "failed" in the journal.
- GitLab: watched branches — the newest pipeline failed or is green again, new commits.
- Import from KeePass (XML, CSV) and Bitwarden (JSON, CSV), with a plan before writing
  and the export file deleted afterwards.
- Send to AI: a note card, one of its items or an MR of the feed becomes an assignment for
  Claude Code or Codex, shown in full before it leaves; in a console window in the
  project's folder or as a quiet run whose answer comes back into the app. Daykeeper calls
  no model itself and keeps no key for one.
- MR preview: an MR of the feed as one Markdown page (description, files, diffs) with
  Review, Fixes after review and Brief.
- From a call: a transcript or a recording (transcribed on the computer by whisper.cpp)
  becomes a note card with a summary, notes and tasks.
- The Error log window (Settings → General → About): `errors.log` and `crash.log`, and a
  report scrubbed of paths, names, e-mails, tokens and addresses, shown exactly as it is
  sent. A crash of the process is caught in `crash.log` and named at the next start.

**Start with the system**
- The Qt window owns the start with the system now (the same "Daykeeper" Run entry on
  Windows, a LaunchAgent on macOS). Turning it on writes the exe that runs now; at start a
  built app quietly takes over an entry whose copy of Daykeeper is gone (moved, or a 1.x
  one-file exe since deleted), when the start with the system is on. An entry whose
  Daykeeper is still on disk is kept, and a copy run from the temporary folder or from
  Downloads takes nothing over. A preview copy shows the setting greyed out.
- The macOS LaunchAgent and the app's bundle id are `local.daykeeper`; a LaunchAgent left
  under the earlier label is read as the entry and replaced when the entry is written.
- The Linux `.desktop` entry quotes and escapes its Exec= line as the Desktop Entry spec
  asks.

**Builds**
- One build from one spec, `packaging/daykeeper.spec`: `Daykeeper-win64.zip` (the
  `Daykeeper` folder, about 71 MB unpacked) and `Daykeeper-macos.zip` (`Daykeeper.app`).
  The one-file `Daykeeper.exe` is not built any more: the LGPL wants Qt's libraries as
  files of their own, and the folder starts three times faster. `build_qt.bat` is gone;
  `build.bat` / `build.sh` build the app.
- Qt and Qt for Python are used under the LGPL-3.0: THIRD_PARTY.md and `licenses/`, both
  inside the build. tkinter is no longer needed anywhere; the console helper leaves Qt out.
- CI: the window's tests run on Linux, Windows and macOS without a screen (Qt's offscreen
  platform) instead of under Xvfb; `tools/screenshots.py` draws the Qt window into
  pictures.

## 1.1.2-beta.2 — pre-release: the fixes of the audit of 16 October 2026 and the Notes tab speed-up

> **Pre-release, still in development.** Covered by the automated tests and a scripted
> run of every screen, but not yet used day to day. Keep a copy of the vault
> (Settings → Data) before trying it; 1.1.1 stays the latest stable release.

Every finding of the October 2026 code audit, each with a test and a probe, and the
Notes tab drawn on a canvas. beta.1 (16 October) carried everything below but the
**Speed** part and the folder build.

**Look**
- The restart after a theme switch failed in the one-file Windows build: the new copy
  inherited the old one's `_PYI_*` variables and ran from its temp folder, which the old
  copy could then not remove ("Failed to remove temporary directory"). The restart now
  starts a fresh copy (`PYINSTALLER_RESET_ENVIRONMENT`).
- A plain note, a script routine and the border and title of a tile under the pointer take
  the app's accent; they were still blue.
- The app has an icon: an amber tile with the ink letter D and a small key in its counter.
  It is the file icon of the Windows exe and the macOS bundle and the icon of every window
  (before, the exe showed the PyInstaller default and the windows Tk's feather). The
  picture is drawn by `tools/make_icon.py` into `assets/`.
- The colours follow the icon: the accent is amber with ink text on it (buttons, the active
  tab, checks) in both themes, and the logo on the unlock screen and in the tab bar is the
  icon itself (`assets/logo/`, one file per size the window shows it at); link-like text is a
  darker amber that keeps 4.5:1 on a card. Text is never drawn in the accent itself any more
  (it was in three places), and a rail tab under the pointer stays white.

**Speed**
- The Notes tab opens about twenty times faster on a busy day (16 cards with 120
  checklist items: 0.35 s instead of 7.5 s on screen; a change to the day shows in 0.2 s
  instead of 5.9 s; coming back to the tab takes 0.07 s instead of 0.6 s; a resize of the
  window 0.06 s instead of 1.5 s). A card is now one canvas: the title, the text, the
  check boxes, the items, the “+” row, the files and the footer are drawn on it. Before, a
  card was about twenty Tk windows (a frame, an entry, a text box and a label per
  item...), each laid out four times over on a tab build — and on Windows every Tk
  widget is a window of its own. A field becomes a real one only while it is edited: a
  click on the title, the text, an item or the “+” row opens the editor there (the
  cursor lands where you clicked); Enter or a typed key opens the picked field from the
  keyboard (Tab reaches a card, ↑/↓ pick a field); Tab and Shift+Tab move between the
  fields of a card and on to the next card; Esc, Enter or a click elsewhere close the
  editor. The text of a card shows its first eight lines; the editor holds all of it.
  Everything else works as before: the check boxes, the “✕”, Backspace on an empty item,
  Alt+↑/↓, the “Done (n)” fold, the files, the menus, the drag of an edge.
- Typing in a long note no longer re-lays the whole text out on every key: the field's
  height is set only when it really changes (a tenth of a second per key on a note of
  30 KB in long paragraphs). And the editor of a card belongs to the frame around the
  scrolling area, placed over the card rather than made inside it: inside the scroll
  canvas every key made Tk recompute the canvas item, ten times the cost (7 ms instead
  of 0.7 on a 10 KB paragraph). A key in a note now costs what it costs in a bare Tk
  text widget: nothing anywhere but at the start of a long paragraph, where Tk re-wraps
  the paragraph (about 10 ms on screen for 10 KB of text without a line break, 25 ms
  for 30 KB — Tk's known weak spot, the same in a bare widget).
- Start: the app's own imports take 40 ms less (`urllib`, `xml.sax`, `concurrent.futures`,
  `tempfile` and `shutil` are loaded when first used). The Windows build also comes as a
  folder, `Daykeeper-win64.zip`: it opens its window in about 0.7 s, where the one-file
  exe unpacks itself on every start (1.5–2.7 s, more while the antivirus looks at it).

**Security**
- The PIN / master-password guard over the Passwords tab was only a picture: `Ctrl+F`,
  `Tab` and `Space` reached the search, the list and the Copy button under it, and the card
  kept the plain password in its fields. Now a locked tab takes its list and card off the
  screen and empties the card; every shortcut of the tab asks for the code first; a secret
  cannot be copied while the guard is down; an undo while locked keeps the new tab locked.
- Automatic routine runs (on unlock, at a time of day) start only the script the user
  accepted: saving the tile or running it by hand records a hash of the file (and of what
  a shortcut points to); a changed or never-accepted script does not start by itself and
  says so in a corner note, a run by hand asks before accepting the new version. The
  editor warns when a script sits on a network share, in Downloads or outside the profile
  folder. `.ps1` scripts still run with `-ExecutionPolicy Bypass`: the tile's hash now
  authenticates the file's content, so the bypass is scoped to exactly the file the user
  accepted, and the tile's tooltip says so.
- A user hook rule with a backtracking regex (`^(a+)+$`) made every assistant tool call
  wait the full 15 s hook timeout. Patterns are now bounded: 500 characters, no nested
  repetition, no repeated choice with alike options, no two unbounded repeats in a row
  (the editor refuses them with a plain message); the search covers the first 16 KB of a
  command or answer; a longer text runs the regex in a child process killed after 1.5 s,
  and the skipped rule is named under "Recent triggers". A worker thread with a deadline,
  as the report proposed, cannot do it — CPython's `re` holds the GIL for the whole search.
- The unlock screen waits after wrong master passwords the way the PIN prompt does.
- A link in an attached document opens with the system only when it is a known kind of
  document; programs, scripts, Office files with macros, disk images and unknown types are
  shown in their folder (an allow-list instead of a block list).
- Site-icon fetches no longer follow a redirect from a public site into a loopback,
  link-local or private address.
- A new master password also brings a new field key for the sealed values.
- The hook reads a `@file` named in a command only when the file lies under the folder the
  command ran in. The console helper refuses a note or an item that looks like a token.
- The hook command written into the assistant's settings is quoted for the shell whenever
  the path holds anything but plain characters (`$`, a backtick, a quote, `;` …).
- The wipe timer of the clipboard keeps a hash of the copied secret, not the secret.
- A copied password cannot be read through a toast after the lock: toasts close with it.
- Argon2id runs six passes instead of three (about 0.1 s here); the header bound stays at
  twelve, so a forged header costs no more than before. Older files open unchanged.

**Data**
- A lone surrogate in any text (a Windows file name can carry one) made every autosave
  fail with `UnicodeEncodeError` until the window was closed. The vault and the sealed
  values carry such text through unchanged; readable exports, the inbox and `settings.json`
  write U+FFFD in its place; a save that still cannot serialise shows the error instead of
  dying silently.
- The journal inbox is moved aside before it is read, so a line the hook appends while the
  app reads and saves goes into a fresh inbox and is picked up next time instead of being
  deleted unread; a claim left by a crash is picked up as well.
- A synced replacement of the vault file with the same size and time (OneDrive, iCloud,
  `copy2`) is detected: the stamp carries the nonce of the file's header.
- A damaged sealed value is kept as it is and the card says "unreadable value"; before, it
  was silently emptied and lost at the next save.
- A copy is kept before a list or a day is deleted and at the first save after an unlock
  (undo dies with the lock), whatever the backup cadence.
- A hand-edited export with a list or a word where a number belongs imports (the field
  takes its default) instead of failing with an uncaught `TypeError`.
- `settings.json` is flushed to disk before the rename; a `vault.pkv.tmp` left by a crash is
  removed at the next open; the assistant's `settings.json` is edited under a lock file and
  re-read right before the write, so an edit Claude Code makes meanwhile is kept.
- The temporary duplicate of a copy opened for a look is removed when its window closes;
  folders a crashed preview left behind are swept at the next Open.

**Undo**
- The history keeps per-object snapshots (one entry, one day, one note, one tile) and only
  what changed in a step, under a budget of 20 MB: a step after one edit is a few hundred
  bytes instead of the whole domain (474 KB at 1 000 entries, 4.8 MB at 10 000), and a
  hundred steps no longer cost up to half a gigabyte.

**Interface**
- Passwords: `Enter` in the search puts the first hit on the card, `↓` walks into the
  list, `Esc` clears the search; `Ctrl+Shift+C` copies the password of the entry on the
  card, `Ctrl+B` its login — find and copy without the mouse.
- Journal: the verdict columns sit right under the summary (they were below the fold in a
  760 px window); "Links…" and "Set up the journal…" moved into a ⋯ menu; "Pick up from
  sessions" is "Read the inbox now"; the quick button reads "✗ Провал" in Russian.
- Routine: a tile is drawn on one canvas (as bookmark tiles are): a hundred routines open
  in 0.3 s instead of 3.3 s. The run state, the chips and the actions are redrawn in place.
- Plain words instead of team jargon: "tested tasks" in the auto-summary (was "RFT"),
  "Out of office / other" (was "Absence / extra info"). The error dialog says what to do
  with `errors.log`.
- Contrast: disabled text, the success colour, link text in the dark theme and the active
  tab reach 4.5:1.

**Build and CI**
- Tests, lint, types and a dependency audit (`pip-audit`) run on every push and pull
  request — Dependabot's included — on Linux under Xvfb and on Windows (`test.yml`).
- The release publishes a signed build-provenance attestation for each file
  (`gh attestation verify`). The binaries themselves stay unsigned: that needs a code-signing
  certificate the project does not have.
- One Python for the build, the tests and CI: 3.13 (`build.bat` makes its venv with it; the
  README says so).
- The macOS bundle carries the app's version and `LSMinimumSystemVersion` (it said 0.0.0).
- Linux: the Bookmarks and Routine tabs no longer crash on the first tile (the Windows-only
  `<App>` keysym is bound where Tk has it; `<Menu>` on X11); the suite runs under Xvfb.
- 17 Windows API calls declare their argument and result types (a handle above 2^31 was
  passed truncated); every timer stops with the window (`invalid command name` noise in the
  tests); 16 more pure modules are under mypy.

## 1.1.1

- macOS: a copied password is now wiped from the system clipboard after 30 seconds as
  well; before, only the app's own copy was cleared and the password stayed there.
- macOS: text at the size other Mac apps use (13 for body text); 1.1.0 drew it larger.
- The arrows and the bin under the Passwords list are narrower and stay on one line with
  "+ New entry" in a window from about 1000 px wide; the sidebar is wide enough for them.
- The strength hint and the errors under a new master password wrap at the field's width:
  the unlock panel no longer widens while one types.
- The long heading of the activities on the Journal tab wraps inside the card instead of
  being cut at its right edge in a narrow window.
- Tests: on macOS the window tests no longer hide their window (Tk 8.6 there never returns
  from an update over a hidden window that was never drawn); the whole suite runs on a Mac.

## 1.1.0

**Data**
- The full export: every tab as readable JSON, and the import of it back (Settings →
  Data). The vault as it was is kept beside it first; the lock settings stay.
- Copies: Settings → Data lists the automatic copies and the copies kept beside the
  vault; each opens in a separate window or becomes the vault.
- A conflict (another window saved the file) no longer loses what was changed here: on
  close or lock it is offered as a separate copy. The inbox no longer pops an error every
  minute while the conflict lasts.
- A broken line in the journal inbox is skipped instead of stopping the inbox for good.
- An error in a click or a timer goes to `errors.log` and a short dialog.
- Hand-edited `settings.json` with a wrong type no longer breaks the Journal tab or the hook.

**Security**
- Passwords and tokens are sealed a second time inside the vault with a field key of
  their own: they are plain text only while shown or copied — not in the content decrypted
  at unlock, not in every autosave, not in the undo history. A lock wipes both keys. The
  file gets a new mark (`PKV2`): version 1.0 refuses it instead of opening it with empty
  passwords; a file of 1.0 is converted on the first unlock, its old form stays in `backup`.
- Wrong codes are counted: after three the prompt waits, after ten the app locks.
- A lock closes every window that shows vault data: an opened file, the output of a run.
  An attached file being edited is saved first.
- The GitLab token does not follow a redirect to another port of the same host.
- A bookmark opens web addresses only; a link in an attached document never starts a program.
- A rule of the hook does not write text that looks like a token into the journal.
- Stop ends a routine together with the scripts it started.
- The start-with-the-system entry is no longer rewritten when the user edited its arguments.
- A forged vault header can ask for four times the usual key work at most (was 1 GiB and 64 passes).

**Speed**
- Bookmark tiles are drawn on one canvas each: the tab opens 5–8 times faster, an undo
  that touches bookmarks too.
- The tabs are made on their first visit; the master password is checked off the window's thread.
- The process ends with the window even while site icons are still being fetched.
- Typing a name in the Passwords list changes only that row; the search waits for a pause.
- Integrations opens up to six times faster: the section is drawn once instead of three
  times, and the notifications show the newest 20 with “Show more” below.
- Settings → Journal opens twice as fast: its lists are measured in one layout pass.
- A GitLab check asks about each MR only once an hour. In between it asks a project for
  its new approvals and pipelines with one request each, and reads the threads of an MR
  only when its count of notes moved or your threads in it are still open. A quiet check of
  20 open MRs takes about 6 requests instead of 60–80.

**Usability**
- A dark theme (Settings → General; takes effect after a restart).
- Integrations → GitLab → Projects: tick the projects to watch; none ticked — all of them.
- Long text wraps instead of running past the edge: checklist items in notes and the
  lines of the journal report (the copied report keeps its lines as they are).
- A narrow or short window keeps everything in view: the sidebar buttons stay at the
  bottom, the Passwords sidebar narrows and a field's name goes above its value, the rows
  of buttons in the Journal and the GitLab settings wrap onto the next line.
- Sharp on every monitor: moved to a monitor of another scale, the window draws itself
  again at that scale instead of being stretched by Windows.
- `Ctrl+1…6` and `Ctrl+Tab` switch tabs; bookmark and routine tiles work from the keyboard.
- The eye inside the password field on the unlock screen shows the password; a new master
  password gets a strength hint.
- Settings → General → About: the version and the releases page.
- The import and export dialogs return to the settings they were started from.
- Own rules for the .env import in `settings.json`.

**Under the hood**
- ruff and mypy in CI, `requirements-dev.txt`, dependabot, `SECURITY.md`, this file.
- `requirements.txt` pins the same `cryptography` as the build; builds run without UPX.

## 1.0.0

The first release: passwords, bookmarks, the verification journal, notes, the routine and
GitLab notifications in one encrypted file; the Claude Code hook and the journal helper;
Windows exe and macOS app.
