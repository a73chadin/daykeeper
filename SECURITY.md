# Security

Daykeeper keeps passwords and tokens, so a weakness in it matters. Please report one
privately, not in a public issue: on GitHub open the repository's **Security** tab →
**Report a vulnerability**. Say what you found, how to reproduce it and what it lets an
attacker do. You get an answer within a week.

What is in scope: the encryption of the vault and its copies, the master password and PIN
checks, the guard over the Passwords tab, anything that leaks a secret into a plain file,
the clipboard or its history, the logs and the error report, the network or an assignment
sent to an AI assistant, the Claude Code / Codex hook and the helper, the build and the
release files.

The window is Qt (PySide6), shipped under the LGPL-3.0 as separate libraries in the build
folder (see [THIRD_PARTY.md](THIRD_PARTY.md)). The LGPL lets you replace them, so the app
does not verify them at start: whoever can write into the app's folder can change what
runs, as with any program. A weakness in Qt itself is fixed by a release on a newer
PySide6; report it to the Qt project as well.

What the app does not protect against, and says so in the README: someone who can read
the memory of your running session or who has your unlocked computer — while the vault is
open, the secrets are in memory.

Only the latest release gets fixes. The 1.x versions (the Tk window) get none any more.
