"""Lock settings: idle lock of the whole app and a separate guard on the Passwords tab.

The settings live inside the encrypted vault (key `security`), not in settings.json:
whoever sits at an unlocked computer must not be able to switch the locks off by editing
a plain file. The PIN is kept as an Argon2id hash with its own salt. Pure (no tkinter)."""

from __future__ import annotations

import hmac
import math
import secrets
from dataclasses import dataclass

from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

GUARD_OFF = "off"  # the master password at start is enough
GUARD_MASTER = "master"  # the Passwords tab asks for the master password again
GUARD_PIN = "pin"  # the Passwords tab asks for a PIN
GUARDS = (GUARD_OFF, GUARD_MASTER, GUARD_PIN)

IDLE_CHOICES = (0, 1, 3, 5, 10, 15, 30, 60)  # minutes; 0 — never
PIN_MIN, PIN_MAX = 4, 12


def _pin_hash(pin: str, salt: bytes) -> bytes:
    # The vault is already decrypted when the PIN is checked, so the hash only guards
    # the UI; modest parameters keep the check instant.
    return Argon2id(salt=salt, length=32, iterations=2, lanes=1, memory_cost=19 * 1024).derive(pin.encode("utf-8"))


def pin_problem(pin: str) -> str:
    """Why a PIN cannot be used ("" — it can): digits only, 4–12 of them."""
    if not pin.isdigit():
        return "digits"
    if not PIN_MIN <= len(pin) <= PIN_MAX:
        return "length"
    return ""


@dataclass
class SecurityPrefs:
    app_lock_min: int = 0  # lock the whole app after this many idle minutes; 0 — never
    lock_on_minimize: bool = False  # lock the whole app when its window is minimized
    vault_guard: str = GUARD_OFF
    vault_lock_min: int = 5  # the Passwords tab locks again after this many idle minutes; 0 — only by hand
    pin_salt: str = ""
    pin_hash: str = ""

    @property
    def has_pin(self) -> bool:
        return bool(self.pin_salt and self.pin_hash)

    def set_pin(self, pin: str) -> None:
        if pin_problem(pin):
            raise ValueError(pin_problem(pin))
        salt = secrets.token_bytes(16)
        self.pin_salt, self.pin_hash = salt.hex(), _pin_hash(pin, salt).hex()

    def clear_pin(self) -> None:
        self.pin_salt = self.pin_hash = ""
        if self.vault_guard == GUARD_PIN:
            self.vault_guard = GUARD_OFF

    def check_pin(self, pin: str) -> bool:
        if not self.has_pin or not pin:
            return False
        return hmac.compare_digest(_pin_hash(pin, bytes.fromhex(self.pin_salt)), bytes.fromhex(self.pin_hash))

    @property
    def guarded(self) -> bool:
        """Does the Passwords tab ask for a code of its own."""
        return self.vault_guard == GUARD_MASTER or (self.vault_guard == GUARD_PIN and self.has_pin)

    def to_dict(self) -> dict:
        return {
            "app_lock_min": self.app_lock_min, "lock_on_minimize": self.lock_on_minimize,
            "vault_guard": self.vault_guard, "vault_lock_min": self.vault_lock_min,
            "pin_salt": self.pin_salt, "pin_hash": self.pin_hash,
        }

    @classmethod
    def from_dict(cls, d) -> SecurityPrefs:
        if not isinstance(d, dict):
            return cls()

        def minutes(key: str, default: int) -> int:
            try:
                value = int(d.get(key, default))
            except (TypeError, ValueError):
                return default
            return value if 0 <= value <= 24 * 60 else default

        guard = str(d.get("vault_guard", GUARD_OFF))
        return cls(
            app_lock_min=minutes("app_lock_min", 0),
            lock_on_minimize=bool(d.get("lock_on_minimize", False)),
            vault_guard=guard if guard in GUARDS else GUARD_OFF,
            vault_lock_min=minutes("vault_lock_min", 5),
            pin_salt=str(d.get("pin_salt", "")),
            pin_hash=str(d.get("pin_hash", "")),
        )


class IdleClock:
    """When the user last did something, and which locks are due. Times are in seconds
    from any monotonic clock; the app feeds it from time.monotonic()."""

    def __init__(self, now: float):
        self.last = now

    def touch(self, now: float) -> None:
        self.last = now

    def idle(self, now: float) -> float:
        return max(0.0, now - self.last)

    def due(self, now: float, minutes: int) -> bool:
        return minutes > 0 and self.idle(now) >= minutes * 60


# the passwords people pick first; a master password among them is guessed in seconds
COMMON_PASSWORDS = {
    "password", "password1", "passw0rd", "qwerty", "qwerty123", "qwertyuiop", "12345678", "123456789",
    "1234567890", "11111111", "00000000", "87654321", "iloveyou", "admin123", "letmein1", "welcome1",
    "football", "baseball", "sunshine", "princess", "dragon12", "monkey12", "abc12345", "trustno1",
    "пароль", "пароль123", "йцукенгш", "qwertyui", "asdfghjk", "zxcvbnm1", "1q2w3e4r", "1qaz2wsx",
}
WEAK, FAIR, GOOD, STRONG = 0, 1, 2, 3


def password_strength(pw: str) -> int:
    """A rough guess of how hard a master password is to guess: WEAK … STRONG. Counts the
    kinds of characters and the length, and knows the passwords people pick first and
    one character repeated. Not a promise, a nudge toward a longer password."""
    if len(pw) < 8 or pw.lower() in COMMON_PASSWORDS or len(set(pw)) < 4:
        return WEAK
    pool = 0
    pool += 26 if any("a" <= c <= "z" for c in pw) else 0
    pool += 26 if any("A" <= c <= "Z" for c in pw) else 0
    pool += 10 if any(c.isdigit() for c in pw) else 0
    pool += 33 if any(c.isalpha() and not c.isascii() for c in pw) else 0  # another alphabet
    pool += 33 if any(not c.isalnum() for c in pw) else 0  # spaces and signs
    bits = len(pw) * math.log2(max(pool, 2))
    words = len(pw.split())
    if words >= 4 and len(pw) >= 20:  # a phrase of words: long, and easy to remember
        return STRONG
    return FAIR if bits < 60 else GOOD if bits < 90 else STRONG


FREE_TRIES = 3  # wrong codes before the pause starts
LOCK_AFTER = 10  # wrong codes in a row that lock the whole app (the master password is needed)
MAX_PAUSE = 60  # seconds


class Attempts:
    """Wrong codes in a row on the PIN and master password prompts of an open app. A PIN
    of 4 digits is checked in 26 ms: without a limit anyone at the unlocked computer goes
    through all of them in minutes. After FREE_TRIES the prompt waits 1, 2, 4… s; after
    LOCK_AFTER the app locks. One counter for every prompt, so switching prompts does not
    reset it."""

    def __init__(self):
        self.failed = 0
        self.until = 0.0

    def wait(self, now: float) -> float:
        """Seconds before the next try is taken; 0 — now."""
        return max(0.0, self.until - now)

    def fail(self, now: float) -> bool:
        """A wrong code. True — that was one too many: lock the app."""
        self.failed += 1
        if self.failed >= FREE_TRIES:
            self.until = now + min(MAX_PAUSE, 2 ** (self.failed - FREE_TRIES))
        return self.failed >= LOCK_AFTER

    def ok(self) -> None:
        self.failed, self.until = 0, 0.0
