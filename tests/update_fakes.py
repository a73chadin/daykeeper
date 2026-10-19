"""Fakes for the update tests: a release whose checksums are signed with a key made in the
test (never the release key), and a network that serves it from memory. Nothing here goes
to the network."""
from __future__ import annotations

import base64
import hashlib
import io
import threading
import zipfile
from collections.abc import Callable

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from daykeeper import update


class SigningPair:
    """An Ed25519 pair made for one test; `public` is base64 of the raw 32 bytes, as each
    of the app's PUBLIC_KEYS is."""

    def __init__(self) -> None:
        self.private = Ed25519PrivateKey.generate()
        raw = self.private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.public = base64.b64encode(raw).decode("ascii")

    def private_b64(self) -> str:
        raw = self.private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
        return base64.b64encode(raw).decode("ascii")

    def sign(self, data: bytes) -> bytes:
        return base64.b64encode(self.private.sign(data)) + b"\n"


def zip_of(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def sums_of(files: dict[str, bytes], tag: str | None = None) -> bytes:
    """SHA256SUMS.txt as CI makes it: the line "# daykeeper <tag>" (none when `tag` is
    None), then `sha256sum`'s lines."""
    head = f"{update.SUMS_HEADER}{tag}\n" if tag is not None else ""
    return (head + "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in files.items())).encode("ascii")


class FakeHttp:
    """Serves `files` by URL; records every URL asked. `gate` (an Event) holds a stream in
    the middle until it is set, so a test can press Cancel during a download."""

    def __init__(self, files: dict[str, bytes] | None = None, api: object = None) -> None:
        self.files = dict(files or {})
        self.api = api
        self.asked: list[str] = []
        self.gate: threading.Event | None = None
        self.in_stream = threading.Event()
        self.fail_with: Exception | None = None

    def get(self, url: str, limit: int = update.SMALL_LIMIT, accept: str = "") -> bytes:
        self.asked.append(url)
        if self.fail_with is not None:
            raise self.fail_with
        if url.startswith("https://api.github.com/"):
            import json

            return json.dumps(self.api).encode("utf-8")
        if url not in self.files:
            raise update.UpdateError("HTTP 404")
        return self.files[url]

    def stream(self, url: str):
        self.asked.append(url)
        if url not in self.files:
            raise update.UpdateError("HTTP 404")
        data = self.files[url]

        def chunks():
            half = len(data) // 2
            yield data[:half]
            self.in_stream.set()
            if self.gate is not None:
                self.gate.wait(10)
            yield data[half:]

        return len(data), chunks()


def release_json(version: str, names: list[str], prerelease: bool = False, body: str = "") -> dict:
    tag = "v" + version
    return {
        "tag_name": tag, "name": tag, "draft": False, "prerelease": prerelease, "body": body,
        "html_url": f"https://github.com/{update.REPO}/releases/tag/{tag}",
        "assets": [{"name": n, "browser_download_url": f"{update.DOWNLOAD_PREFIX}{tag}/{n}", "size": 1} for n in names],
    }


OWN_TAG = "own"


def signed_release(key: SigningPair, version: str, assets: dict[str, bytes], body: str = "", tamper: Callable[[dict[str, bytes]], None] | None = None,
                   sign_with: SigningPair | None = None, with_sig: bool = True, sums_tag: str | None = OWN_TAG) -> tuple[update.Release, FakeHttp]:
    """A release of `assets` with SHA256SUMS.txt (and its .sig, signed by `sign_with` or
    `key`), and a FakeHttp serving it. `tamper(served)` changes what is served after the
    sums were made (a swapped file). The list names the release's own tag unless
    `sums_tag` names another, or None: no version line."""
    sums = sums_of(assets, "v" + version if sums_tag == OWN_TAG else sums_tag)
    served = dict(assets)
    served[update.SUMS_NAME] = sums
    if with_sig:
        served[update.SIG_NAME] = (sign_with or key).sign(sums)
    if tamper is not None:
        tamper(served)
    data = release_json(version, list(served), body=body)
    release = update.releases_from_json(data)[0]
    tag = "v" + version
    http = FakeHttp({f"{update.DOWNLOAD_PREFIX}{tag}/{n}": b for n, b in served.items()}, api=data)
    return release, http
