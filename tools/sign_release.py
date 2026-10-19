"""Sign the release's SHA256SUMS.txt for the app's update check (daykeeper/update.py).

    python tools/sign_release.py out/SHA256SUMS.txt

reads the private key from the environment variable DAYKEEPER_SIGNING_KEY (base64 of the
raw 32-byte Ed25519 private key; CI passes the repository secret of that name), signs the
exact bytes of the file and writes `<file>.sig`: base64 of the 64-byte signature, one
line. Then it checks the signature with the public key the app carries, so a secret that
does not match the app fails the release here, not on every user's computer.

The key never leaves this process: it is not printed, not logged and not written
anywhere; an error names what is wrong with it, never its value. A missing or unreadable
key fails with exit code 2."""

from __future__ import annotations

import base64
import binascii
import os
import sys
from collections.abc import Mapping
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ENV = "DAYKEEPER_SIGNING_KEY"


class KeyProblem(Exception):
    """The key is missing or unreadable; the message never holds the key."""


def load_key(env: Mapping[str, str] = os.environ):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    text = (env.get(ENV) or "").strip()
    if not text:
        raise KeyProblem(f"{ENV} is not set")
    try:
        raw = base64.b64decode(text, validate=True)
    except (ValueError, binascii.Error):
        raise KeyProblem(f"{ENV} is not base64") from None
    if len(raw) != 32:
        raise KeyProblem(f"{ENV} must hold 32 bytes, it holds {len(raw)}")
    return Ed25519PrivateKey.from_private_bytes(raw)


def sign(data: bytes, key) -> str:
    return base64.b64encode(key.sign(data)).decode("ascii")


def main(argv: list[str], env: Mapping[str, str] = os.environ, public_key: str | None = None) -> int:
    from daykeeper import update

    if len(argv) != 2:
        print("usage: python tools/sign_release.py <SHA256SUMS.txt>", file=sys.stderr)
        return 2
    sums = Path(argv[1])
    try:
        key = load_key(env)
    except KeyProblem as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    data = sums.read_bytes()
    signature = sign(data, key)
    del key
    sig_path = sums.with_name(sums.name + ".sig")
    sig_path.write_text(signature + "\n", encoding="ascii", newline="\n")
    try:
        update.verify_signature(data, signature, public_key)
    except update.UpdateError:
        sig_path.unlink()
        print("error: the signature does not verify with the public key in daykeeper/update.py: the secret and the app's key are not a pair", file=sys.stderr)
        return 1
    print(f"signed {sums.name} ({len(data)} bytes) -> {sig_path.name}; verified with the app's public key")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
