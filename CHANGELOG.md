# Changes

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
