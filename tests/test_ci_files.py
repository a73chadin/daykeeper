"""The build and CI files as the October 2026 audit and the 2.0 switch left them: tests
on every push, the dependency audit, the provenance step, one Python version, one spec,
the macOS bundle's version. Read as text — the workflows cannot run here, but a line that goes
missing should fail a test, not a release."""
from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


class CiFilesTest(unittest.TestCase):
    def test_tests_run_on_every_push_and_pull_request(self):
        """M7: lint, types, the audit and the suite on push and pull_request, Linux and Windows,
        the window's tests without a screen."""
        text = _read(".github/workflows/test.yml")
        self.assertRegex(text, r"on:\s*\n\s*push:\s*\n\s*pull_request:")
        for needle in ("python -m ruff check .", "python -m mypy", "pip_audit --require-hashes -r requirements-build.txt", "QT_QPA_PLATFORM: offscreen", "windows-latest", "ubuntu-latest", "unittest discover tests"):
            self.assertIn(needle, text, needle)
        self.assertNotIn("xvfb", text, "no Tk window to show on a virtual screen any more")
        self.assertNotIn("@v", re.sub(r"#.*", "", text), "actions are pinned to commit hashes, not tags")

    def test_release_publishes_provenance_and_the_mac_bundle_carries_its_version(self):
        """M7 (provenance), L27 (Info.plist version and minimum system, now in the spec)."""
        text = _read(".github/workflows/build.yml")
        self.assertIn("actions/attest-build-provenance@", text)
        self.assertIn("attestations: write", text)
        self.assertIn("id-token: write", text)
        for build in (text, _read("build.sh")):
            self.assertIn("codesign --force --deep --sign - dist/Daykeeper.app", build)
        spec = _read("packaging/daykeeper.spec")
        for needle in ("CFBundleShortVersionString", "CFBundleVersion", '"LSMinimumSystemVersion": "14.0"', "from daykeeper import __version__", '__version__.split("-")[0]'):
            self.assertIn(needle, spec, needle)

    def test_one_python_version_everywhere(self):
        """M7: 3.13 in CI, in pyproject and in the local build script."""
        for wf in ("build.yml", "test.yml", "screenshots.yml"):
            versions = set(re.findall(r'python-version:\s*"([\d.]+)"', _read(f".github/workflows/{wf}")))
            self.assertEqual(versions, {"3.13"}, wf)
        config = tomllib.loads(_read("pyproject.toml"))
        self.assertEqual(config["tool"]["mypy"]["python_version"], "3.13")
        self.assertEqual(config["tool"]["ruff"]["target-version"], "py313")
        self.assertIn("py -3.13 -m venv .venv", _read("build.bat"))
        self.assertIn("Python 3.13", _read("README.md"))

    def test_one_version_and_one_set_of_dependencies(self):
        """2.0: pyproject, the package and requirements.txt say the same; PySide6 and shiboken6
        are pinned to one version, in the build set too."""
        import daykeeper

        project = tomllib.loads(_read("pyproject.toml"))["project"]
        self.assertEqual(project["version"], daykeeper.__version__)
        pins = [x.strip() for x in _read("requirements.txt").splitlines() if x.strip() and not x.startswith("#")]
        self.assertEqual(sorted(project["dependencies"]), sorted(pins))
        build = _read("requirements-build.txt")
        for pin in pins:
            self.assertIn(pin + " \\", build, pin)
        qt = {x.split("==")[1] for x in pins if x.startswith(("PySide6", "shiboken6"))}
        self.assertEqual(len(qt), 1, "PySide6 and shiboken6 go together")
        self.assertNotIn("tkinter", _read("requirements.txt") + build)

    def test_the_pure_modules_are_under_mypy(self):
        """L16: the modules the audit added to the checked set stay there; the window too."""
        files = tomllib.loads(_read("pyproject.toml"))["tool"]["mypy"]["files"]
        for name in ("undo", "revisions", "export", "markdown", "pngimage", "i18n", "autostart", "runner", "claudecode", "win32", "clipboard", "jobs", "layout", "tabs", "fieldicons"):
            self.assertIn(f"daykeeper/{name}.py", files, name)
        for entry in ("daykeeper/qt", "main.py", "cli_main.py", "inbox.py"):
            self.assertIn(entry, files, entry)
        self.assertGreaterEqual(len(files), 50)

    def test_every_build_carries_the_icon(self):
        """The file icon of the exe and the bundle, and the assets folder for the window icon."""
        spec = _read("packaging/daykeeper.spec")
        for needle in ('"icon.icns" if MAC else "icon.ico"', '(os.path.join(ROOT, "assets"), "assets")'):
            self.assertIn(needle, spec, needle)
        self.assertIn("--icon assets\\icon.ico", _read("build.bat"), "the console helper")
        self.assertIn("--icon assets/icon.ico", _read(".github/workflows/build.yml"), "the console helper")

    def test_a_suffixed_tag_is_a_pre_release(self):
        """v2.0.1-beta.1 goes out marked as a pre-release and never as the latest; the macOS
        bundle keeps a numeric version (the spec cuts the suffix)."""
        workflow = _read(".github/workflows/build.yml")
        self.assertIn("prerelease: ${{ contains(github.ref_name, '-') }}", workflow)
        self.assertIn("make_latest: ${{ !contains(github.ref_name, '-') }}", workflow)

    def test_one_build_from_one_spec(self):
        """2.0: the Tk build is gone. Windows and macOS build packaging/daykeeper.spec into the
        Daykeeper folder / Daykeeper.app under their size ceilings; the release keeps the
        file names the README links to; the console helper leaves Qt out."""
        workflow = _read(".github/workflows/build.yml")
        self.assertEqual(workflow.count("packaging/daykeeper.spec"), 2, "Windows and macOS")
        self.assertIn("packaging/check_size.py dist/Daykeeper 95", workflow)
        self.assertIn("packaging/check_size.py dist/Daykeeper.app 90", workflow)
        release = workflow[workflow.index("  release:"):]
        for name in ("Daykeeper-win64.zip", "Daykeeper-macos.zip", "SHA256SUMS.txt"):
            self.assertIn(f"out/{name}", release, name)
        for gone in ("Daykeeper.exe", "Daykeeper-qt", "main_qt.py", "daykeeper-qt.spec"):
            self.assertNotIn(gone, workflow, gone)
        self.assertIn(r"packaging\daykeeper.spec", _read("build.bat"))
        self.assertIn("packaging/daykeeper.spec", _read("build.sh"))
        self.assertFalse((ROOT / "build_qt.bat").exists())
        self.assertFalse((ROOT / "main_qt.py").exists())
        for text in (workflow, _read("build.bat"), _read("build.sh")):
            self.assertNotRegex(text, r"PyInstaller[^\n]* main\.py", "the app is built from the spec only")
            for line in (x for x in text.splitlines() if "PyInstaller" in x and "cli_main.py" in x):
                self.assertIn("--exclude-module PySide6", line, line)
        spec = _read("packaging/daykeeper.spec")
        for needle in ('"main.py"', 'name="Daykeeper"', 'name="Daykeeper.app"', 'bundle_identifier="local.daykeeper"', "licenses", "THIRD_PARTY.md"):
            self.assertIn(needle, spec, needle)
        for name, size in (("LGPL-3.0.txt", 5000), ("GPL-3.0.txt", 5000), ("Python-LICENSE.txt", 5000), ("cffi-LICENSE.txt", 500)):
            self.assertTrue((ROOT / "licenses" / name).stat().st_size > size, name)
            self.assertIn(f"licenses/{name}", _read("THIRD_PARTY.md"), name)
        self.assertIn("Lesser General Public License", _read("THIRD_PARTY.md"))
        readme = _read("README.md")
        links = re.findall(r"releases/latest/download/([\w.-]+)", readme)
        self.assertTrue(links, "the README links to the latest downloads")
        for link in links:
            self.assertIn(f"out/{link}", release, f"the README links to {link}, which the release must carry")

    def test_the_window_tests_run_in_ci(self):
        """Both test jobs install the pinned build set, which brings PySide6."""
        text = _read(".github/workflows/test.yml")
        for job in ("  tests-linux:", "  tests-windows:"):
            part = text[text.index(job):]
            self.assertIn("--require-hashes -r requirements-build.txt", part, job)
        self.assertIn("PySide6-Essentials==", _read("requirements-build.txt"))

    def test_dev_tools_are_pinned(self):
        text = _read("requirements-dev.txt")
        self.assertRegex(text, r"(?m)^pip-audit==\d", msg="pip-audit pinned")
        self.assertRegex(text, r"(?m)^hypothesis==\d")


if __name__ == "__main__":
    unittest.main()
