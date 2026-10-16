# Daykeeper Multitool — playbook

How to use the app day by day and what to do when something goes wrong. The technical
description is in [README.md](README.md).

## 1. What it is and where it lives

Daykeeper Multitool is a personal keeper of passwords, tokens and keys plus bookmarks,
a verification journal, notes, a daily routine of quick-launch scripts and GitLab
notifications. One encrypted file, one master password, everything on your computer. The
only network traffic is what you switch on: the GitLab checks, the site icons of bookmarks
and the links you open.

| What | Where |
|---|---|
| Program | `Daykeeper.exe` (Windows) or `Daykeeper.app` (macOS) from the Releases page, or from `dist\` after `build.bat` / `build.sh` |
| Vault (passwords, bookmarks, journal, notes, routines, integrations, lock settings — encrypted) | `<data folder>\vault.pkv` |
| Automatic copies (the latest 20 and the last one of each of the past 14 days, also encrypted) | `<data folder>\backup\` |
| Copies kept beside the vault: a conflict, before an import, before a restore (never deleted) | `<data folder>\vault-conflict-…pkv` and the like |
| Settings: window, language, theme, tabs, journal layout, links (not secret) | `<data folder>\settings.json` |
| Journal inbox from sessions (not secret, temporary) | `<data folder>\inbox.jsonl` |
| Safety hook log | `<data folder>\hook-log.jsonl` |
| Errors of the window (a traceback, no field values) | `<data folder>\errors.log` |
| Sources and tests | the repository folder |

The data folder is `%APPDATA%\Daykeeper` on Windows (`C:\Users\<user name>\AppData\Roaming\Daykeeper`)
and `~/Library/Application Support/Daykeeper` on macOS; Settings (⚙) → Data → "Open vault
folder" opens it. A folder left by the previous name, `PassKeeper`, is renamed to
`Daykeeper` on the first start.

Everything the old app menu held is in two places now: the buttons next to the logo
(⚙ settings, ⏻ lock the app, 🔒 lock the passwords, ↶ ↷ undo and redo) and the settings
window.

## 2. First start and unlock

1. Start the app. If there is no vault yet, it asks you to invent a master password
   (at least 8 characters) and repeat it. The line under the fields says how hard the
   password is to guess; a phrase of four unrelated words is long and easy to remember.
   The eye next to the field shows what you type.
2. From then on every start asks for the master password once. Lock by hand — `Ctrl+L`
   or ⏻; idle locks and a separate code for the Passwords tab are in Settings → Security
   (see 6e).
3. A forgotten master password cannot be recovered: the file is encrypted with it.
   Change the password knowing the old one: Settings → Master password.
4. The language link under the unlock button switches English and Russian; later —
   Settings → General. The theme (light or dark) is there too; it takes effect after a
   restart.

## 3. Passwords

- "+ New entry" offers a kind: login and password, API token, SSH key / certificate,
  bank card, note, other. The kind only decides the starting fields.
- The card: the name on top, below it the kind and the list, then the fields and a note.
  Everything saves itself a second after an edit; `Ctrl+S` saves at once.
- A field: "+ Add field" → a preset or a custom one. The lock makes the value secret:
  it is shown as dots and is not searched. The eye shows the value, "Copy" puts it into
  the clipboard for 30 seconds, the die generates a password.
- Order: ▲ ▼ under the list, `Alt+↑` / `Alt+↓`, or right-click → "Up / Down".
- Lists: the switcher above the search. ⋯ next to it creates, renames and deletes a list
  (entries go to the main list). Move an entry — the "List" field in the card or
  right-click → "Move to list".
- Search (`Ctrl+F`) looks through all lists and tells where the hit lives; picking a hit
  switches to its list.

## 4. Import from a .env file

Settings → Data → "Import from a .env file…". Lines `KEY=VALUE` (comments, `export`,
quotes are handled). Before writing the app shows a plan: which lists and entries appear,
which fields are added — without values. The main list is not touched; an entry with the
same name in the same list gets the new fields, a field with the same key gets the new
value. Keys with `TOKEN`, `KEY`, `SECRET`, `PASSWORD` and the like become secret fields.
Own rules for other prefixes go into `settings.json` (see README, "Import from .env").

## 5. Export, import and copies

Settings → Data:

- **"Export to a readable file…"** (`Ctrl+E`). The app asks for the master password
  again, then writes a text file (to read), CSV (for Excel) or **everything as JSON**:
  every tab, to keep or to move to another computer. The file is NOT encrypted: delete it
  as soon as it is no longer needed.
- **"Import everything from an export…"** replaces every tab with what a JSON export
  holds, after the master password. The vault as it was is kept beside it first. The lock
  settings are neither exported nor imported.
- **Copies.** The list of the automatic copies and the copies kept beside the vault.
  "Open" shows a copy in a separate window (on a duplicate: whatever you do there changes
  neither the copy nor the vault). "Restore…" makes a copy the vault: the vault as it is
  now is kept beside it first, then the app locks — open it with the password of that
  copy.

## 6. Journal

The "Journal" tab. On the left — days (the numbers are the counts of the columns, the tip
over the list names them), on the right — the day card. The columns and the kinds of
activity come from the journal layout: Settings → Journal (a tester, a developer, an
analyst or a plain diary to start from, then rename, hide or add your own).

1. "1. Done" — one line. Empty — the report builds it itself from the columns and the
   activity, the grey hint shows what will go in.
2. "Activity" — one line per kind: `MR reviews: !42, !43`, `MRs opened: !45`; any
   other line goes under the note kind.
3. The columns — one item per line. A tracker link pasted in becomes a key.
4. "2. Plans" and "3. Absence / extra info" (can be switched off in the layout).

The "Add" line with the quick buttons and the activity menu adds quickly; several keys —
with spaces or commas. The buttons under the day ("Build report", "Links…", "Set up the
journal…", "Pick up from sessions") stay at the bottom; the day above them scrolls.

"Build report" (`Ctrl+R`) assembles the standup text and copies it to the clipboard. Keys
and MR numbers become links by the addresses from "Links…" (tracker, GitLab, own
repository). Without them they stay text.

## 6a. Notes: to-do lists by day

The "Notes" tab. On the left — search and days ("done / total" items), on the right —
cards of the day. "+ Note" adds an empty card, "+ Checklist" — a card with the cursor
in the item line; `Ctrl+N` on this tab also adds a note.

- A card: a title, text, a checklist and files. Items are added from the "+ Add item"
  row (the plus or Enter; the cursor stays there for the next one). A tick moves the
  item under "Done (n)" — click the header to fold it, "Clear" removes done items. The
  bar above the list shows progress, done cards go to the bottom of the day.
- In an item: `Alt+↑/↓` moves it, Backspace in an empty item deletes it, right-click —
  the item menu.
- 📌 pins the card to the top of the day, 🎨 picks one of nine colours, ⋯ — the card
  settings: wide card, fold done items, attach files, clear done items, carry over,
  delete (a non-empty card asks first).
- 📎 attaches files (Markdown first, any file works). A click opens a Markdown or text
  file in the built-in viewer; ticking a task box there writes to the file; "Edit" opens
  the raw text, "Save" writes it. A link in such a file to a program (`.exe`, `.bat`,
  `.ps1`…) is not started: its folder opens instead. ✕ detaches the file, the file itself
  stays on disk. A missing file shows ⚠.
- "⏳ Unfinished" gathers cards with undone items across all days, "📎 With files" —
  cards with files. A card of a past day has "⤴ to today", which copies the undone items
  into a new card for today (the old card stays as it was).
- Search looks through titles, texts, items and file names of all days.

## 6b. Bookmarks

"+ Bookmark" opens the editor: a title, the address, a group, a password entry to tie,
an icon (emoji or letters) and a colour. Typing an address ties the bookmark to the entry
with the same host by itself. On a password entry, right-click → "Make a bookmark…".

On a tile: a click opens the address; 👤 copies the login; 🔑 copies the password (👁
shows it for ten seconds); "↗ + 🔑" opens the address and copies the password. When the
passwords are locked, 🔑 asks for the code first. ⋯ — edit, move, delete. From the
keyboard: `Tab` reaches a tile, `Enter` opens it, `Shift+Enter` opens it and copies the
password, the menu key (or `Shift+F10`) opens its menu.

A bookmark opens web addresses only (`http`, `https`, `mailto`); a file or a program is
started from the Routine tab.

## 6c. Routine

"+ Routine": a name, the type (script, command, link, folder, file), what to start,
arguments and a working folder for scripts. "Open in a separate console window" — for
scripts that ask questions. "Start by itself": each time the vault is unlocked and/or at
a time (`09:30`) on ticked weekdays.

▶ runs a tile; scripts show the output window (Stop, Run again). Stop ends the script
together with everything it started. Automatic runs are quiet: the left column shows
today's schedule and the run log (double-click — the output); a failed automatic run pops
up in the corner. From the keyboard: `Tab` to a tile, `Enter` runs it, the menu key opens
its menu.

## 6d. Integrations: GitLab

1. Make a token in GitLab (User settings → Access tokens) with the `read_api` scope.
2. Save it in a password entry (a hidden field).
3. On the "Integrations" tab: the GitLab address, "Token from entry", how often to check,
   the rules to report; tick "Watch". "Check now" checks at once.
4. After the first check "Projects" lists the projects with your open MRs. Tick the ones
   to watch; none ticked — all of them are watched.

The first check remembers the state and reports nothing. After that each change matching
a rule goes to the feed on the tab, the tab button shows the number of unread events, a
pop-up appears in the corner (click — opens the MR) and the taskbar button flashes on
Windows. Checks run only while the vault is open.

## 6e. Locks

Settings → Security (asks for the master password first):

- **Whole app** — lock after N idle minutes, and/or when the window is minimized.
- **Passwords tab** — no extra code (the master password at start is enough), the
  master password again, or a PIN (4–12 digits, "Set PIN…"). The tab locks again after
  N idle minutes or by the 🔒 button; the guard also hides passwords on bookmark tiles.
  Journal, notes and the other tabs stay open.

Wrong codes are counted on every prompt of an open app (the PIN, the master password
for the settings and the export): after three the prompt waits 1, 2, 4… seconds, after
ten the whole app locks and wants the master password. A lock also closes every window
that shows what the vault holds: the settings, an opened file, the output of a run.

## 7. Where the journal fills itself from

Two mechanisms, both write to the inbox (`inbox.jsonl`); the app picks the file up on
unlock and once a minute, files items by day, skips duplicates and deletes the file. A
broken line in it is skipped.

**A rule for AI sessions.** After a verdict on a task, a filed defect, a review, an opened
or merged MR, written test cases or autotests the session runs:

```
python inbox.py pass APP-1234
python inbox.py fail APP-2938
python inbox.py bug  APP-4127
python inbox.py review !42
python inbox.py mr-open !45
python inbox.py mr-merged !44
python inbox.py manual APP-101
python inbox.py auto APP-101
python inbox.py note "nightly run 1042 triage"
```

Other work that is neither a verification nor an MR (a nightly run triage, a skill edit,
a test refactoring, VM setup) arrives as one `note "one sentence"` at the end of the
piece of work; in the day it sits under the note kind in "Activity".

Settings → AI assistants connects Claude Code and Codex in one click: the hook, the
helper and the instructions for the sessions. The same commands can be run by hand from
any terminal; `--date 2026-10-12` — when the event belongs to another day.

**Links in the report.** Settings → Report links (or "Journal → Links…"): the tracker
address, the GitLab address and the own repository `group/repo`. Without them issue keys
and MR numbers stay text in the report. The hook reads the same three values.

**Safety hook.** It runs after every command of a session and recognises by itself:
`glab mr create` → MR opened, `glab mr merge` → merged, `glab mr approve` or a thread in
an MR → review, an issue created in the tracker → new bug, a comment starting with
"Tested on …" → passed / failed, a transition to Test failed. A review counts only for
someone else's MR: the hook learns the author through `glab` (within 8 seconds) and
remembers it in `hook-cache.json`; replies in threads of an own MR are not reviews. Own
rules are added in Settings → AI assistants; text a rule catches that looks like a token
or a key is never written. What the hook caught goes to `hook-log.jsonl` (Settings → AI
assistants → "Recent triggers").

If something extra lands in the journal, just delete the line in the day editor.

## 8. Shortcuts

| Keys | Action |
|---|---|
| `Ctrl+1` … `Ctrl+6` | The tabs in their order |
| `Ctrl+Tab` / `Ctrl+Shift+Tab` | The next / previous tab |
| `Ctrl+N` | New thing of the open tab: entry, bookmark, note or routine |
| `Ctrl+F` | Search (passwords, bookmarks or notes, by tab) |
| `Ctrl+S` | Save now |
| `Ctrl+Z` / `Ctrl+Y` | Undo / redo: inside a field — the typing, elsewhere — the last change |
| `Ctrl+E` | Export |
| `Ctrl+R` | Build the standup report |
| `Ctrl+L` | Lock |
| `Alt+↑` / `Alt+↓` | Move the entry up / down |
| `Delete` (in the list) | Delete the entry |
| `Enter` on a tile | Open the bookmark / run the routine |
| Menu key or `Shift+F10` on a tile | Its menu |

On macOS `Cmd` stands for `Ctrl`. Shortcuts work in any keyboard layout.

## 9. When something goes wrong

**Version 1.0 says "Not a vault file".** Since 1.1 the vault is written in a new form
that 1.0 cannot read (on purpose: it would have shown the passwords empty). Use 1.1 or
later; the file as it was before the first start of 1.1 is in `backup\`.

**"Wrong password".** The master password is wrong, or the file is damaged. Try again;
then Settings → Data → Copies → "Restore…" on the latest copy (or put a file from
`backup\` in place of `vault.pkv` by hand while the app is closed).

**"The vault file was changed by another window or program".** Two windows of the app
(or a sync tool) wrote the same file. Nothing is lost: the other save stays in the file,
and what you changed in this window is offered as a separate copy when you close or lock
it. Open that copy from Settings → Data → Copies and carry over what you need.

**Something did not react to a click.** The error is in `errors.log` in the data folder
(a short dialog says so the first time). Send the last block of it with the report.

**The app does not start or crashes.** Run from source: `python main.py` — the error
shows in the console. The most frequent cause is a missing `cryptography` package:
`pip install -r requirements.txt`.

**The window opened off screen.** Delete `geometry` and `zoomed` from `settings.json`
or the whole file; it will be created anew.

**The journal does not fill itself.** Settings → AI assistants → "Recent triggers": every
trigger is there. Empty — reconnect the assistant there. The inbox is read on unlock and
once a minute; "Journal → Pick up from sessions" reads it at once.

**Keys in the report are not links.** Fill in Settings → Report links.

**Forgot the PIN of the Passwords tab.** The master password still opens everything:
Settings → Security (asks for the master password) → "Ask for the master password" or
"Set PIN…".

**"Too many wrong tries".** Wait the seconds the prompt names. After ten wrong codes in a
row the app locks: unlock it with the master password, which also clears the count.

**GitLab: "Check failed".** `401` — the token is wrong or expired; `403` — it lacks
`read_api`; a network error — the address is wrong or GitLab is unreachable (VPN). The
token entry must have the token in a hidden field.

**A routine does not start.** "Not found" — the script moved; edit the tile. A `.py`
script needs Python on the PATH (`py` on Windows); a `.ps1` one runs with the execution
policy bypassed for that run only. The output window shows the error text.

**The clipboard history still shows a password.** It was copied by another app or by
`Ctrl+C` from a shown field; the copy buttons of the app put secrets with the "not in
history" mark (Windows only).

**The build fails with "permission denied".** The app is running: close it, then run
`build.bat` again.

**macOS says the app is damaged or from an unidentified developer.** The bundle is not
signed, so macOS stops its first start. Once, either way: double-click the app, close the
warning, then System Settings → Privacy & Security → Security → "Open Anyway" next to
Daykeeper and confirm (right-click → Open no longer does this since macOS 15); or, also
when it says "damaged", remove the quarantine flag in Terminal:
`xattr -dr com.apple.quarantine /Applications/Daykeeper.app`.

## 10. What is and is not protected

- The vault and its copies are encrypted with AES-256-GCM, the key comes from the master
  password through Argon2id. A tampered file does not open; a forged header cannot make
  the key derivation take more than four times the usual work.
- Bookmarks, routines, integration settings and the lock settings are inside the
  encrypted vault too, so the locks and the auto-start list cannot be changed without the
  master password.
- Settings, the inbox, the hook log and `errors.log` are plain text but hold no secrets:
  only dates, issue keys, MR numbers, addresses and tracebacks. On macOS and Linux they
  are readable only by you.
- Changing the master password re-encrypts the copies made under the old one and
  deletes older automatic ones; a deleted entry may still live in a copy until it
  rotates out.
- Secrets stay sealed in memory too and are opened only while shown or copied; a lock
  wipes the keys. What was shown may stay in memory until it is overwritten. An exported
  file is plain text — delete it; in the CSV, values starting with `=`, `+`, `-` or `@` get an
  apostrophe so spreadsheets do not run them.
- Network: the GitLab checks (only when "Watch" is on), the site icons of bookmarks (can
  be switched off in Settings → General), the hook asking GitLab who the author of an MR
  is through your own `glab`, and links you open.
