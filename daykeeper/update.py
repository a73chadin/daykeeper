"""Updates: is a newer release out, and a download of it that is checked before it is used.

The check is one GET of GitHub's "latest release" (or the list of releases, when the user
asked for pre-releases too) with a plain User-Agent, at most once a day, off in the
settings with one switch. Nothing else leaves the computer.

The update itself happens only after the user asked for it. The release's SHA256SUMS.txt
is signed in CI with an Ed25519 key whose public half is in PUBLIC_KEYS below; the list is
read only when its signature holds, its first line names the release's own tag (an older
signed list replayed under a newer tag is refused), and the downloaded file is used only
when its SHA-256 is the one the list gives. Anything else is refused and the download
deleted.

Then: on Windows, an installed copy (the setup's uninstall entry points at this folder)
runs the new setup silently once this process has quit, which installs over it and starts
it again; a portable
folder is unpacked next to itself and swapped by a small script once this process has
quit, the old folder kept as `<name>.old` until the new one has started; on macOS the
same swap for the .app bundle. No Qt here: the window drives it (qt/dialogs/update.py)."""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import http.client
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath, PureWindowsPath

from . import __version__, tls
from .i18n import _

REPO = "a73chadin/daykeeper"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
LIST_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=20"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
# the public halves of the keys whose signature on SHA256SUMS.txt is accepted (base64 of
# the raw 32 bytes); a signature by any one of them holds. The first signs every release:
# its private half lives only in the repository's secrets. A spare key, whose private half
# is kept offline and never on GitHub, joins it here: a release signed with the spare
# reaches the copies already installed when the first key is lost or has to be replaced.
PUBLIC_KEYS: tuple[str, ...] = (
    "PfX6chME57winjkX63RGCzrV8Ey+owju5vhriuDzrdM=",
)
SUMS_NAME = "SHA256SUMS.txt"
# the first line of SHA256SUMS.txt, put there by CI before signing: "# daykeeper <tag>"
SUMS_HEADER = "# daykeeper "
SIG_NAME = SUMS_NAME + ".sig"
WINDOWS_ZIP = "Daykeeper-win64.zip"
MAC_ZIP = "Daykeeper-macos.zip"

CHECK_EVERY = 24 * 3600  # s; the automatic check, at most once a day
TIMEOUT = 20.0  # s, for each network read
SMALL_LIMIT = 4 * 1024 * 1024  # bytes; the API answer, the checksums and the signature
ASSET_LIMIT = 500 * 1024 * 1024  # bytes; a build is about 70 MB
CHUNK = 64 * 1024
STALE_TEMP = 3600  # s; a temp folder of an earlier update is swept after this

# keys of settings.json
SETTING_ON = "update_check"  # the daily check; on when missing
SETTING_PRE = "update_prereleases"  # offer pre-releases too; off when missing
SETTING_CHECKED = "update_checked_at"  # time of the last automatic or manual check
SETTING_TOLD = "update_told"  # the version the quiet note was shown for

TEMP_PREFIX = "daykeeper-update-"
OLD_SUFFIX = ".old"
STAGING_SUFFIX = ".update"
# a file prepare puts into the staging folder it makes: only such a folder is ever deleted
STAGING_MARK = ".daykeeper-update"
PROBE_PREFIX = ".daykeeper-probe-"

# the setup's AppId (packaging/daykeeper.iss); Inno Setup writes its uninstall entry, with
# the install folder, under HKCU\<UNINSTALL_KEY> for a per-user install
APP_ID = "{BEE0D65A-A989-4ED5-B3C9-34F1DC2BDC81}"
UNINSTALL_KEY = "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\" + APP_ID + "_is1"


class UpdateError(Exception):
    """Something the user is told in plain words; the update did not happen."""


class Cancelled(Exception):
    """The user pressed Cancel."""


# ---- versions


_VERSION = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")


def parse_version(text: str) -> tuple | None:
    """"2.0.10" / "v2.1.0-beta.1" → a key that sorts as the versions do: numbers as numbers,
    a release after its pre-releases, the parts of a pre-release compared one by one
    (numbers before words, numbers by value). None — not a version."""
    m = _VERSION.match(str(text).strip())
    if not m:
        return None
    nums = (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
    pre = m.group(4)
    if not pre:
        return (*nums, 1, ())
    parts = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split("."))
    return (*nums, 0, parts)


def is_prerelease(text: str) -> bool:
    key = parse_version(text)
    return key is not None and key[3] == 0


def is_newer(candidate: str, current: str) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    return a is not None and b is not None and a > b


# ---- releases


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size: int = 0


@dataclass(frozen=True)
class Release:
    version: str  # the tag without its "v"
    tag: str
    notes: str = ""
    prerelease: bool = False
    page: str = ""
    assets: tuple[Asset, ...] = ()

    def asset(self, name: str) -> Asset | None:
        return next((a for a in self.assets if a.name == name), None)


def _release(item: object) -> Release | None:
    if not isinstance(item, dict) or item.get("draft"):
        return None
    tag = str(item.get("tag_name") or "")
    if parse_version(tag) is None:
        return None
    assets = []
    for a in item.get("assets") or []:
        if isinstance(a, dict) and a.get("name") and a.get("browser_download_url"):
            size = a.get("size")
            assets.append(Asset(str(a["name"]), str(a["browser_download_url"]), size if isinstance(size, int) else 0))
    return Release(version=tag[1:] if tag.startswith("v") else tag, tag=tag, notes=str(item.get("body") or ""),
                   prerelease=bool(item.get("prerelease")) or is_prerelease(tag), page=str(item.get("html_url") or ""),
                   assets=tuple(assets))


def releases_from_json(data: object) -> list[Release]:
    """The releases in an answer of the API: one object (latest) or a list of them."""
    items = data if isinstance(data, list) else [data]
    return [r for r in (_release(i) for i in items) if r is not None]


def pick(releases: Iterable[Release], current: str = __version__, prereleases: bool = False) -> Release | None:
    """The newest release newer than `current`; a pre-release only when asked for."""
    best: Release | None = None
    for r in releases:
        if r.prerelease and not prereleases:
            continue
        if is_newer(r.version, current) and (best is None or is_newer(r.version, best.version)):
            best = r
    return best


def notes_for(release: Release) -> str:
    """What the dialog shows: the release's own section of the changes when its text holds
    the whole CHANGELOG (a heading "## <version>"), else the text as it is."""
    text = release.notes.replace("\r\n", "\n").strip()
    m = re.search(rf"(?m)^## {re.escape(release.version)}(?=[ \t]|$).*$", text)
    if m:
        rest = text[m.start():]
        nxt = re.search(r"(?m)^## ", rest[3:])
        text = rest[: nxt.start() + 3].strip() if nxt else rest.strip()
    return text[:20000]


# ---- the settings


def check_enabled(settings: dict) -> bool:
    return settings.get(SETTING_ON, True) is not False


def prereleases_wanted(settings: dict) -> bool:
    return settings.get(SETTING_PRE) is True


def check_due(settings: dict, now: float) -> bool:
    """The automatic check is on and the last one was a day ago or more (or never, or a
    time in the future: a clock set back)."""
    if not check_enabled(settings):
        return False
    last = settings.get(SETTING_CHECKED)
    if not isinstance(last, (int, float)) or isinstance(last, bool):
        return True
    return not (0 <= now - last < CHECK_EVERY)


# ---- the network


def user_agent() -> str:
    return f"Daykeeper/{__version__}"


def _https_only(url: str) -> None:
    if not url.startswith("https://"):
        raise UpdateError(_("Refused an address that is not HTTPS: {url}").format(url=url))


MAX_REDIRECTS = 5  # a download goes from github.com to its file host: one hop, two at most


class _Answer:
    """An answer being read and the connection it came on: closing it closes both."""

    def __init__(self, resp, conn):
        self.resp, self.conn, self.headers = resp, conn, resp.headers

    def read(self, n: int = -1) -> bytes:
        return self.resp.read(n)

    def close(self) -> None:
        self.resp.close()
        self.conn.close()

    def __enter__(self) -> _Answer:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class Http:
    """GET over HTTPS with a timeout and a plain User-Agent, nothing else in the request.
    `get` reads a small answer whole; `stream` gives (size or 0, chunks). Tests put a fake
    with the same two methods in its place.

    http.client, not urllib: urllib asks the server to close the connection after the
    answer (`Connection: close`), and on some networks GitHub's answer then stalls halfway
    — the check timed out every other time while the same request without it never did."""

    def __init__(self, timeout: float = TIMEOUT):
        self.timeout = timeout

    def _open(self, url: str, accept: str) -> _Answer:
        from urllib.parse import urljoin, urlsplit

        for _hop in range(MAX_REDIRECTS + 1):
            _https_only(url)  # a redirect to plain HTTP is refused like a first address
            parts = urlsplit(url)
            conn = http.client.HTTPSConnection(parts.hostname or "", parts.port or 443, timeout=self.timeout, context=tls.context())
            try:
                conn.request("GET", (parts.path or "/") + (f"?{parts.query}" if parts.query else ""), headers={"User-Agent": user_agent(), "Accept": accept})
                resp = conn.getresponse()
            except Exception as exc:  # noqa: BLE001 — every network failure is one plain message
                conn.close()
                raise UpdateError(str(exc) or type(exc).__name__) from exc
            if resp.status in (301, 302, 303, 307, 308) and resp.getheader("Location"):
                url = urljoin(url, resp.getheader("Location") or "")
                conn.close()
                continue
            if resp.status != 200:
                conn.close()
                raise UpdateError(f"HTTP {resp.status}")
            return _Answer(resp, conn)
        raise UpdateError(_("Too many redirects"))

    def get(self, url: str, limit: int = SMALL_LIMIT, accept: str = "application/octet-stream") -> bytes:
        with self._open(url, accept) as resp:
            try:
                body = resp.read(limit + 1)
            except (OSError, ValueError, http.client.HTTPException) as exc:
                raise UpdateError(str(exc) or type(exc).__name__) from exc
        if len(body) > limit:
            raise UpdateError(_("The answer from the server is larger than expected"))
        return body

    def stream(self, url: str) -> tuple[int, Iterator[bytes]]:
        resp = self._open(url, "application/octet-stream")
        try:
            size = int(resp.headers.get("Content-Length") or 0)
        except ValueError:
            size = 0

        def chunks() -> Iterator[bytes]:
            with resp:
                while True:
                    try:
                        block = resp.read(CHUNK)
                    except (OSError, ValueError, http.client.HTTPException) as exc:
                        raise UpdateError(str(exc) or type(exc).__name__) from exc
                    if not block:
                        return
                    yield block

        return size, chunks()


def check(http: Http | None = None, current: str = __version__, prereleases: bool = False) -> Release | None:
    """One GET: the release to offer, or None when this one is the newest."""
    http = http or Http()
    body = http.get(LIST_URL if prereleases else LATEST_URL, accept="application/vnd.github+json")
    try:
        data = json.loads(body.decode("utf-8"))
    except ValueError as exc:
        raise UpdateError(_("The answer from GitHub is not readable")) from exc
    return pick(releases_from_json(data), current, prereleases)


# ---- where this copy runs from, and what replaces it


INSTALLED, PORTABLE, MAC_APP = "installed", "portable", "app"


def registered_install_dir() -> str | None:
    """The folder the setup's uninstall entry under HKCU names (InstallLocation); None —
    no entry (never installed by this user, or not Windows). Only read."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            value = winreg.QueryValueEx(key, "InstallLocation")[0]
    except (ImportError, OSError):
        return None
    return value if isinstance(value, str) and value.strip() else None


def _same_folder(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def install_kind(platform: str = sys.platform, executable: str | Path = sys.executable, registered: Callable[[], str | None] | None = None) -> str:
    """How this built copy was put on the computer: "installed" (Windows, by the setup:
    its uninstaller lies beside the exe and its uninstall entry names this folder),
    "portable" (Windows, an unpacked folder), "app" (a macOS bundle); "" — something an
    update cannot replace (Linux, a lone binary)."""
    exe = Path(executable)
    if platform == "win32":
        if any(exe.parent.glob("unins*.exe")):
            location = (registered or registered_install_dir)()
            if location and _same_folder(location, exe.parent):
                return INSTALLED
        return PORTABLE
    if platform == "darwin":
        return MAC_APP if app_location(platform, exe) is not None else ""
    return ""


def app_location(platform: str = sys.platform, executable: str | Path = sys.executable) -> Path | None:
    """What an update replaces: the folder that holds Daykeeper.exe, or the .app bundle
    (the exe is Daykeeper.app/Contents/MacOS/Daykeeper)."""
    exe = Path(executable)
    if platform == "darwin":
        bundle = exe.parent.parent.parent
        return bundle if bundle.suffix == ".app" and exe.parent.name == "MacOS" else None
    return exe.parent


def setup_name(version: str) -> str:
    return f"Daykeeper-{version}-setup.exe"


def asset_name(version: str, platform: str, kind: str) -> str | None:
    """The file of the release that updates this copy."""
    if platform == "win32":
        return setup_name(version) if kind == INSTALLED else WINDOWS_ZIP
    if platform == "darwin" and kind == MAC_APP:
        return MAC_ZIP
    return None


def why_not(frozen: bool, preview: bool, platform: str = sys.platform, executable: str | Path = sys.executable) -> str:
    """Why this copy must not update itself; "" — it may."""
    if preview:
        return "preview"
    if not frozen:
        return "source"
    kind = install_kind(platform, executable)
    if not kind:
        return "platform"
    if platform == "darwin":
        app = app_location(platform, executable)
        if app is None or "/AppTranslocation/" in app.as_posix():
            return "translocated"  # macOS runs a quarantined download from a hidden read-only copy
    if kind != INSTALLED and not writable((app_location(platform, executable) or Path(executable).parent).parent):
        return "readonly"
    return ""


def writable(folder: Path) -> bool:
    """A folder can be made in `folder` and removed again: what the swap needs next to the
    app (os.access answers by the mode bits and misses ACLs and read-only volumes)."""
    try:
        probe = tempfile.mkdtemp(prefix=PROBE_PREFIX, dir=folder)
    except OSError:
        return False
    with contextlib.suppress(OSError):
        os.rmdir(probe)
    return True


# ---- checking what was downloaded


def verify_signature(data: bytes, sig_text: bytes | str, public_keys: str | Sequence[str] | None = None) -> None:
    """The signature (base64 of 64 bytes) over `data` holds for one of the keys (the
    release keys, PUBLIC_KEYS, when none are given); else UpdateError."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        raw = sig_text.encode("ascii") if isinstance(sig_text, str) else bytes(sig_text)
        sig = base64.b64decode(raw.strip(), validate=True)
    except (ValueError, binascii.Error, UnicodeEncodeError) as exc:
        raise UpdateError(_("The signature of the checksums is not readable")) from exc
    if len(sig) != 64:
        raise UpdateError(_("The signature of the checksums is not readable"))
    keys = (public_keys,) if isinstance(public_keys, str) else tuple(public_keys or PUBLIC_KEYS)
    for key in keys:
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(key)).verify(sig, data)
            return
        except InvalidSignature:
            continue
    raise UpdateError(_("The signature of the checksums does not match: the list was not made by the release build"))


def sums_tag(text: str) -> str | None:
    """The tag the signed list was made for: its first line, "# daykeeper <tag>"; None — the
    line is missing (a list of an older build, or not one of ours)."""
    first = text.split("\n", 1)[0].strip()
    if not first.startswith(SUMS_HEADER):
        return None
    return first[len(SUMS_HEADER):].strip() or None


def parse_sums(text: str) -> dict[str, str]:
    """`sha256sum` output: "<64 hex>  <name>" (or " *<name>") per line → {name: hex}."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"^([0-9a-fA-F]{64}) [ *](.+)$", line.strip())
        if m:
            out[m.group(2).strip()] = m.group(1).lower()
    return out


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def verify_asset(path: Path, name: str, sums: dict[str, str]) -> None:
    expected = sums.get(name)
    if expected is None:
        raise UpdateError(_("{name} is not in the signed list of checksums").format(name=name))
    if sha256_file(path) != expected:
        raise UpdateError(_("{name} is not the file the release published: its checksum differs").format(name=name))


# ---- the download


@dataclass
class Progress:
    """Written by the download thread, read by the window's timer."""

    stage: str = ""  # "sums", "download", "unpack"
    done: int = 0
    total: int = 0


@dataclass
class Prepared:
    """A verified update, ready to be put in place."""

    kind: str
    version: str
    setup: Path | None = None  # Windows installed: the verified setup exe (in `workdir`)
    staging: Path | None = None  # portable / app: the folder next to the app that holds the new copy
    new_copy: Path | None = None  # staging/Daykeeper or staging/Daykeeper.app
    workdir: Path | None = None


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled


def _release_url(release: Release, name: str) -> str:
    asset = release.asset(name)
    if asset is None:
        raise UpdateError(_("The release has no {name}").format(name=name))
    if not asset.url.startswith(DOWNLOAD_PREFIX):
        raise UpdateError(_("{name} is not on the project's release page").format(name=name))
    return asset.url


def download(http: Http, url: str, dest: Path, progress: Progress | None = None, cancel: threading.Event | None = None, limit: int = ASSET_LIMIT) -> Path:
    size, chunks = http.stream(url)
    if size > limit:
        raise UpdateError(_("The download is larger than expected"))
    if progress is not None:
        progress.done, progress.total = 0, size
    got = 0
    with open(dest, "wb") as fh:
        for block in chunks:
            _check_cancel(cancel)
            got += len(block)
            if got > limit:
                raise UpdateError(_("The download is larger than expected"))
            fh.write(block)
            if progress is not None:
                progress.done = got
    _check_cancel(cancel)
    return dest


def unpack(archive: Path, dest: Path, platform: str = sys.platform) -> None:
    """A zip of the release into `dest`. macOS: ditto, which keeps the bundle's links and
    modes (zipfile drops them, and the app would not start)."""
    dest.mkdir(parents=True, exist_ok=True)
    if platform == "darwin":
        subprocess.run(["/usr/bin/ditto", "-x", "-k", str(archive), str(dest)], check=True, capture_output=True)  # noqa: S603 — fixed program
        return
    import zipfile

    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            target = (dest / info.filename).resolve()
            if not target.is_relative_to(dest.resolve()):
                raise UpdateError(_("The archive holds a path outside its folder"))
        zf.extractall(dest)


def app_marker(platform: str) -> str:
    """The file inside a folder that makes it a Daykeeper build."""
    return "Contents/MacOS/Daykeeper" if platform == "darwin" else "Daykeeper.exe"


def is_build(folder: Path, platform: str) -> bool:
    return (folder / app_marker(platform)).is_file()


def is_our_staging(folder: Path) -> bool:
    return (folder / STAGING_MARK).is_file()


def aside_name(path: Path, now: float | None = None) -> Path:
    """A free name next to `path` for a folder that is in the way but is not ours."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(time.time() if now is None else now))
    n = 1
    while True:
        target = path.with_name(f"{path.name}.{stamp}-{n}")
        if not target.exists() and not target.is_symlink():
            return target
        n += 1


def staging_for[P: PurePath](app: P) -> P:
    return app.with_name(app.name + STAGING_SUFFIX)


def old_for[P: PurePath](app: P) -> P:
    return app.with_name(app.name + OLD_SUFFIX)


def prepare(release: Release, platform: str, executable: str | Path, http: Http | None = None, progress: Progress | None = None,
            cancel: threading.Event | None = None, workdir: Path | None = None, unpacker: Callable[[Path, Path, str], None] = unpack) -> Prepared:
    """Download and check everything an update needs, on a thread. On any failure,
    cancel included, what was downloaded or unpacked is deleted before the error goes up."""
    http = http or Http()
    kind = install_kind(platform, executable)
    name = asset_name(release.version, platform, kind)
    if name is None:
        raise UpdateError(_("This system is updated by hand"))
    work = workdir or Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
    staging: Path | None = None
    progress = progress if progress is not None else Progress()
    try:
        progress.stage = "sums"
        sums_bytes = http.get(_release_url(release, SUMS_NAME))
        sig = http.get(_release_url(release, SIG_NAME))
        verify_signature(sums_bytes, sig)
        sums_text = sums_bytes.decode("utf-8", "replace")
        signed_for = sums_tag(sums_text)
        if signed_for is None:
            raise UpdateError(_("The signed list of checksums does not name its version, so it cannot be told from an older release's list"))
        if signed_for != release.tag:
            raise UpdateError(_("The signed list of checksums is for {signed}, not for {tag}: an older release offered as a new one").format(signed=signed_for, tag=release.tag))
        sums = parse_sums(sums_text)
        if name not in sums:
            raise UpdateError(_("{name} is not in the signed list of checksums").format(name=name))
        _check_cancel(cancel)
        progress.stage = "download"
        file = download(http, _release_url(release, name), work / name, progress, cancel)
        verify_asset(file, name, sums)
        if kind == INSTALLED:
            return Prepared(kind, release.version, setup=file, workdir=work)
        progress.stage = "unpack"
        app = app_location(platform, executable)
        if app is None:
            raise UpdateError(_("This system is updated by hand"))
        staging = staging_for(app)
        if staging.exists() or staging.is_symlink():
            if is_our_staging(staging):
                shutil.rmtree(staging)
            else:
                staging.rename(aside_name(staging))  # not ours: kept, under another name
        staging.mkdir()
        (staging / STAGING_MARK).write_bytes(b"")
        unpacker(file, staging, platform)
        new_copy = staging / ("Daykeeper.app" if kind == MAC_APP else "Daykeeper")
        inside = new_copy / ("Contents/MacOS/Daykeeper" if kind == MAC_APP else "Daykeeper.exe")
        if not inside.is_file():
            raise UpdateError(_("The archive does not hold the app"))
        _check_cancel(cancel)
        shutil.rmtree(work, ignore_errors=True)  # the zip is unpacked: only the new copy stays
        return Prepared(kind, release.version, staging=staging, new_copy=new_copy)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        raise


# ---- putting it in place (after this process has quit)


SETUP_SWITCHES = ("/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/FORCECLOSEAPPLICATIONS", "/LAUNCH=1")


def setup_command(setup: Path) -> list[str]:
    """The installer, silent (the user confirmed already): it closes a copy that still runs
    from the folder (by force when it does not close when asked), installs over it and
    starts it again (LAUNCH=1, its [Run] entry)."""
    return [str(setup), *SETUP_SWITCHES]


def _ps(text: str | PurePath) -> str:
    """A PowerShell single-quoted literal: nothing in it is expanded."""
    return "'" + str(text).replace("'", "''") + "'"


def windows_setup_script(pid: int, running_exe: Path, setup: Path, log: Path) -> str:
    """PowerShell that waits for this process to end, then runs the setup (setup_command):
    the app's files are free by then. When the app does not quit, nothing is run; when the
    setup cannot start, the old app is started again."""
    args = " ".join(setup_command(setup)[1:])
    return f"""$ErrorActionPreference = 'Stop'
$log = {_ps(log)}
$exe = {_ps(running_exe)}
$setup = {_ps(setup)}
function Say([string]$text) {{ try {{ Add-Content -LiteralPath $log -Value ((Get-Date -Format s) + ' ' + $text) -Encoding UTF8 }} catch {{ }} }}
Say 'waiting for the app to quit before the setup'
$p = Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue
if ($p -and $p.Path -and ($p.Path -ieq $exe)) {{
    if (-not $p.WaitForExit(120000)) {{ Say 'the app did not quit in two minutes: the setup was not run'; exit 1 }}
}}
try {{
    $info = New-Object System.Diagnostics.ProcessStartInfo $setup
    $info.Arguments = {_ps(args)}
    $info.UseShellExecute = $false
    $s = [System.Diagnostics.Process]::Start($info)
    Say ('started the setup, pid ' + $s.Id)
}} catch {{
    Say ('failed: ' + $_)
    if (Test-Path -LiteralPath $exe) {{
        $info = New-Object System.Diagnostics.ProcessStartInfo $exe
        $info.WorkingDirectory = [System.IO.Path]::GetDirectoryName($exe)
        $info.UseShellExecute = $false
        [void][System.Diagnostics.Process]::Start($info)
        Say 'started the old app again'
    }}
}}
"""


def windows_swap_script(pid: int, running_exe: Path, app: Path, new_copy: Path, staging: Path, log: Path) -> str:
    """PowerShell that waits for this process to end, puts the old folder aside as
    `<name>.old`, moves the new copy into its place and starts it. When a step fails, the
    old folder goes back and the old app starts: the user is never left without one.
    Every path is a single-quoted literal and used with -LiteralPath (spaces, brackets)."""
    old = old_for(app)
    return f"""$ErrorActionPreference = 'Stop'
$log = {_ps(log)}
$app = {_ps(app)}
$old = {_ps(old)}
$new = {_ps(new_copy)}
$staging = {_ps(staging)}
$exeName = 'Daykeeper.exe'
$mark = '{STAGING_MARK}'
function Say([string]$text) {{ try {{ Add-Content -LiteralPath $log -Value ((Get-Date -Format s) + ' ' + $text) -Encoding UTF8 }} catch {{ }} }}
function Start-App([string]$folder) {{
    $info = New-Object System.Diagnostics.ProcessStartInfo (Join-Path $folder $exeName)
    $info.WorkingDirectory = $folder
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $p = [System.Diagnostics.Process]::Start($info)
    Say ('started ' + $info.FileName + ' pid ' + $p.Id)
}}
function Aside([string]$path) {{
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $n = 0
    do {{ $n++; $target = $path + '.' + $stamp + '-' + $n }} while (Test-Path -LiteralPath $target)
    return $target
}}
Say 'waiting for the app to quit'
try {{
    $p = Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue
    if ($p -and $p.Path -and ($p.Path -ieq {_ps(running_exe)})) {{
        if (-not $p.WaitForExit(120000)) {{ throw 'the app did not quit in two minutes' }}
    }}
    if (Test-Path -LiteralPath $old) {{
        if (Test-Path -LiteralPath (Join-Path $old $exeName) -PathType Leaf) {{ Remove-Item -LiteralPath $old -Recurse -Force }}
        else {{
            $aside = Aside $old
            Move-Item -LiteralPath $old -Destination $aside
            Say ('kept a folder that is not Daykeeper as ' + $aside)
        }}
    }}
    $moved = $false
    for ($i = 0; $i -lt 40 -and -not $moved; $i++) {{
        try {{ Move-Item -LiteralPath $app -Destination $old; $moved = $true }} catch {{ Start-Sleep -Milliseconds 500 }}
    }}
    if (-not $moved) {{ throw 'the app folder is still in use' }}
    try {{ Move-Item -LiteralPath $new -Destination $app }}
    catch {{ Move-Item -LiteralPath $old -Destination $app; throw }}
    if (Test-Path -LiteralPath (Join-Path $staging $mark) -PathType Leaf) {{ Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue }}
    Say 'swapped'
    Start-App $app
}} catch {{
    Say ('failed: ' + $_)
    if (Test-Path -LiteralPath (Join-Path $app $exeName)) {{ Start-App $app }}
}}
"""


def powershell_command(script: str, system_root: str | None = None) -> list[str]:
    """PowerShell running `script` given inline (-EncodedCommand: UTF-16 in base64, so
    no quoting can break it, and no script file for an execution policy to stop)."""
    root = system_root or os.environ.get("SYSTEMROOT") or r"C:\Windows"
    exe = str(PureWindowsPath(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
    return [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode("ascii")]


def mac_swap_script(pid: int, app: PurePath, new_copy: PurePath, staging: PurePath, log: PurePath, launch: Sequence[str] = ("/usr/bin/open",),
                    running_exe: PurePath | None = None) -> str:
    """sh that does on macOS what windows_swap_script does: wait (only while the process
    is still the app: a reused process number is not waited for), `<name>.old`, move,
    start (with `open`), and the old bundle back when a step fails. A `<name>.old` that is
    not a Daykeeper bundle is kept under another name, never deleted."""
    q = shlex.quote
    start = " ".join(q(a) for a in launch)
    exe = running_exe if running_exe is not None else app / "Contents" / "MacOS" / "Daykeeper"
    return f"""LOG={q(str(log))}
APP={q(str(app))}
OLD={q(str(old_for(app)))}
NEW={q(str(new_copy))}
STAGING={q(str(staging))}
EXE={q(str(exe))}
exec >>"$LOG" 2>&1
echo "$(date) waiting for the app to quit"
COMM=$(ps -p {int(pid)} -o comm= 2>/dev/null)
if [ -n "$COMM" ] && [ "$COMM" != "$EXE" ] && [ "$(basename -- "$COMM")" != "$(basename -- "$EXE")" ]; then
    echo "process {int(pid)} is not the app ($COMM): not waiting for it"
else
    i=0
    while kill -0 {int(pid)} 2>/dev/null; do
        sleep 0.5
        i=$((i + 1))
        if [ "$i" -gt 240 ]; then echo "the app did not quit"; exit 1; fi
    done
fi
if [ -e "$OLD" ] || [ -L "$OLD" ]; then
    if [ -f "$OLD/Contents/MacOS/Daykeeper" ]; then
        rm -rf "$OLD"
    else
        ASIDE="$OLD.$(date +%Y%m%d-%H%M%S).$$"
        if ! mv "$OLD" "$ASIDE"; then echo "could not move aside a folder that is not Daykeeper"; {start} "$APP"; exit 1; fi
        echo "kept a folder that is not Daykeeper as $ASIDE"
    fi
fi
if ! mv "$APP" "$OLD"; then echo "could not move the app aside"; {start} "$APP"; exit 1; fi
if ! mv "$NEW" "$APP"; then echo "could not move the new app in"; mv "$OLD" "$APP"; {start} "$APP"; exit 1; fi
if [ -f "$STAGING/{STAGING_MARK}" ]; then rm -rf "$STAGING"; fi
echo "swapped"
{start} "$APP"
"""


def swap_log(tempdir: str | None = None) -> Path:
    return Path(tempdir or tempfile.gettempdir()) / "Daykeeper-update.log"


def apply_command(prepared: Prepared, platform: str, executable: str | Path, pid: int | None = None, log: Path | None = None) -> list[str]:
    """What starts the replacement once this process has quit."""
    pid = os.getpid() if pid is None else pid
    log = log or swap_log()
    if prepared.kind == INSTALLED and prepared.setup is not None:
        return powershell_command(windows_setup_script(pid, Path(executable), prepared.setup, log))
    app = app_location(platform, executable)
    if app is None or prepared.new_copy is None or prepared.staging is None:
        raise UpdateError(_("Nothing to put in place"))
    if prepared.kind == PORTABLE:
        return powershell_command(windows_swap_script(pid, Path(executable), app, prepared.new_copy, prepared.staging, log))
    return ["/bin/sh", "-c", mac_swap_script(pid, app, prepared.new_copy, prepared.staging, log, running_exe=Path(executable))]


def launch_detached(command: list[str], platform: str = sys.platform) -> subprocess.Popen:
    """Start `command` so that it outlives this process, with no console window."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("_PYI_")}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    if platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return subprocess.Popen(command, creationflags=flags, close_fds=True, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # noqa: S603 — our own setup or script
    else:
        return subprocess.Popen(command, start_new_session=True, close_fds=True, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # noqa: S603 — our own script


# ---- what an update leaves behind, swept at the next start


def leftovers(platform: str = sys.platform, executable: str | Path = sys.executable, tempdir: str | None = None, now: float | None = None) -> list[Path]:
    """The old copy kept by a swap (`<name>.old`, when it holds a Daykeeper build), a
    staging folder no update is using (when an update made it), and temp folders of
    earlier downloads."""
    now = time.time() if now is None else now
    out: list[Path] = []
    app = app_location(platform, executable)
    if app is not None:
        old = old_for(app)
        if is_build(old, platform):
            out.append(old)
        staging = staging_for(app)
        if staging.is_dir() and is_our_staging(staging):
            out.append(staging)
    try:
        for p in Path(tempdir or tempfile.gettempdir()).glob(TEMP_PREFIX + "*"):
            try:
                if p.is_dir() and now - p.stat().st_mtime > STALE_TEMP:
                    out.append(p)
            except OSError:
                continue
    except OSError:
        pass
    return out


def sweep(paths: Iterable[Path]) -> int:
    n = 0
    for p in paths:
        shutil.rmtree(p, ignore_errors=True)
        n += not p.exists()
    return n
