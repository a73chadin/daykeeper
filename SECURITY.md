# Security

Daykeeper keeps passwords and tokens, so a weakness in it matters. Please report one
privately, not in a public issue: on GitHub open the repository's **Security** tab →
**Report a vulnerability**. Say what you found, how to reproduce it and what it lets an
attacker do. You get an answer within a week.

What is in scope: the encryption of the vault and its copies, the master password and PIN
checks, anything that leaks a secret into a plain file, the clipboard, the log or the
network, the Claude Code hook and the helper, the build and the release files.

What the app does not protect against, and says so in the README: someone who can read
the memory of your running session or who has your unlocked computer — while the vault is
open, the secrets are in memory.

Only the latest release gets fixes.
