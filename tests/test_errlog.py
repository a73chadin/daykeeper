"""errors.log and crash.log without Qt: the scrubber of the report, the report itself, the
hooks of the process (a thread's error, faulthandler) and the rate limit."""
from __future__ import annotations

import faulthandler
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from daykeeper import __version__, errlog

ROOT = Path(__file__).resolve().parent.parent
HOME = r"C:\Users\Sam"
# token-shaped samples are joined from pieces, so no scanner takes the source for a leak
GITLAB_SAMPLE = "glpat-" + "Ab12Cd34Ef56Gh78Ij90"


def scrub(text: str) -> str:
    return errlog.scrub(text, home=HOME, user="sam")


class ScrubTest(unittest.TestCase):
    def test_home_folder_and_user_name(self):
        self.assertEqual(scrub(r'File "C:\Users\Sam\proj\a.py", line 3'), r'File "~\proj\a.py", line 3')
        self.assertEqual(scrub("c:/users/sam/proj/b.py"), "~/proj/b.py")
        self.assertEqual(scrub("/Users/sam/Library/x and /home/kim/y"), "/Users/<user>/Library/x and /home/<user>/y")
        self.assertEqual(scrub(r"D:\Users\kim\z.txt"), r"D:\Users\<user>\z.txt")
        self.assertEqual(scrub("Sam opened it, then sam closed it"), "<user> opened it, then <user> closed it")
        self.assertEqual(scrub("samples and assam stay"), "samples and assam stay", "only the whole word")

    def test_email(self):
        self.assertEqual(scrub("from jo.doe+tag@example.org today"), "from <hidden> today")

    def test_each_token_shape(self):
        samples = {
            GITLAB_SAMPLE: "GitLab",
            "ghp_" + "Ab12Cd34Ef56Gh78Ij90Kl12Mn34Op56Qr78": "GitHub classic",
            "github_pat_" + "11ABCDEFG0_abcdefghijklmnop123": "GitHub fine-grained",
            "sk-" + "proj-Ab12Cd34Ef56Gh78": "API key",
            "xoxb-" + "1234567890-abcdefghij": "chat bot",
            "0123456789abcdef0123456789abcdef0123": "long hex",
            "QWxhZGRpbjpvcGVuIHNlc2FtZQ1234567890abcdEFGH==": "base64",
        }
        for token, what in samples.items():
            with self.subTest(what):
                out = scrub(f"value {token} end")
                self.assertEqual(out, "value <hidden> end")
        self.assertEqual(scrub("Authorization: Bearer abc.DEF123.ghi456jkl"), "Authorization: Bearer <hidden>")
        self.assertEqual(scrub('password: "hunter22" token=abc123def'), "password: <hidden> token=<hidden>")

    def test_urls_keep_scheme_and_host(self):
        self.assertEqual(scrub("GET https://git.example.com/group/proj/-/merge_requests/5?private_token=x#top failed"), "GET https://git.example.com failed")
        self.assertEqual(scrub("see http://user:pw@host.example.net:8080/a/b"), "see http://host.example.net")
        self.assertEqual(scrub("ftp://files.example.org"), "ftp://files.example.org")

    def test_code_paths_and_names_stay(self):
        line = 'File "~/venv/Lib/site-packages/PySide6/QtWidgets/x.py", line 12, in test_every_event_handler_is_guarded'
        self.assertEqual(scrub(line), line)

    def test_written_entries_hide_tokens_but_keep_paths(self):
        out = errlog.hide_secrets("GET https://user:pw@git.example.com/a/b?private_token=abc at C:\\Users\\Sam\\x " + GITLAB_SAMPLE)
        self.assertEqual(out, "GET https://git.example.com/a/b at C:\\Users\\Sam\\x <hidden>")


class ReportTest(unittest.TestCase):
    def test_header_fields_and_scrubbed_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            log = folder / errlog.NAME
            for n in range(3):
                try:
                    raise ValueError(f"bad {n} for sam@example.org at {HOME}\\notes.md token {GITLAB_SAMPLE}")
                except ValueError as exc:
                    self.assertTrue(errlog.write_error(log, type(exc), exc, exc.__traceback__, "window"))
            (folder / errlog.CRASH_KEPT).write_text("Daykeeper crash log | started 2026-01-02 03:04:05\nFatal Python error: Segmentation fault\n", encoding="utf-8")
            env = errlog.environment("150%", "en")
            report = errlog.build_report(folder, env, n=2, home=HOME, user="sam")
        for field in ("- App: Daykeeper " + __version__, "- OS: ", "- Python: ", "- Qt: ", "- Screen scale: 150%", "- Language: en"):
            self.assertIn(field, report)
        self.assertIn("## Errors (2 of 3, newest first)", report)
        self.assertLess(report.index("bad 2"), report.index("bad 1"), "newest first")
        self.assertNotIn("bad 0", report, "only the last n")
        self.assertIn("ValueError: bad 2 for <hidden> at ~\\notes.md token <hidden>", report)
        self.assertNotIn("example.org", report)
        self.assertIn("Fatal Python error: Segmentation fault", report)
        self.assertIn("· window · Daykeeper", report, "the entry's header names where and the versions")

    def test_entries_parse_newest_first_including_an_older_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / errlog.NAME
            log.write_text("---- 2026-01-01 10:00:00\nTraceback (most recent call last):\nKeyError: 'x'\n", encoding="utf-8")
            errlog.write_message(log, "Qt warning", "QPainter::begin: Paint device returned engine == 0")
            entries = errlog.read_entries(log)
        self.assertEqual([e.where for e in entries], ["Qt warning", ""])
        self.assertEqual(entries[1].title, "KeyError: 'x'")
        self.assertIn("Daykeeper " + __version__, entries[0].header)

    def test_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for name in (errlog.NAME, errlog.NAME + ".1", errlog.CRASH_KEPT):
                (folder / name).write_text("x", encoding="utf-8")
            errlog.clear(folder)
            self.assertEqual(list(folder.iterdir()), [])


class RateLimitTest(unittest.TestCase):
    def test_at_most_n_a_minute(self):
        now = [0.0]
        limit = errlog.RateLimit(3, 60, clock=lambda: now[0])
        self.assertEqual([limit.allow() for _i in range(5)], [True, True, True, False, False])
        self.assertEqual(limit.dropped(), 2)
        now[0] = 61
        self.assertTrue(limit.allow())
        self.assertEqual(limit.dropped(), 0)


class HooksTest(unittest.TestCase):
    def setUp(self) -> None:
        self.saved = sys.excepthook, threading.excepthook

    def tearDown(self) -> None:
        sys.excepthook, threading.excepthook = self.saved

    def test_a_thread_exception_reaches_errors_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / errlog.NAME
            errlog.install_hooks(log)

            def work() -> None:
                raise RuntimeError("from a worker")

            t = threading.Thread(target=work, name="worker-1")
            t.start()
            t.join()
            sys.excepthook, threading.excepthook = self.saved
            text = log.read_text(encoding="utf-8")
        self.assertIn("| thread worker-1 | Daykeeper", text)
        self.assertIn("RuntimeError: from a worker", text)
        self.assertIn('in work\n    raise RuntimeError("from a worker")', text, "the code line, no locals")

    def test_sys_excepthook_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / errlog.NAME
            errlog.install_hooks(log)
            hook = sys.excepthook
            sys.excepthook, threading.excepthook = self.saved
            stderr, sys.stderr = sys.stderr, None  # type: ignore[assignment]
            try:
                hook(OSError, OSError("disk"), None)
            finally:
                sys.stderr = stderr
            self.assertIn("OSError: disk", log.read_text(encoding="utf-8"))


class CrashLogTest(unittest.TestCase):
    def tearDown(self) -> None:
        errlog.stop_crash_log()

    def test_faulthandler_points_at_crash_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(errlog.start_crash_log(Path(tmp)), "nothing before")
            self.assertTrue(faulthandler.is_enabled())
            f = errlog.crash_file()
            self.assertIsNotNone(f)
            assert f is not None
            self.assertEqual(Path(f.name), Path(tmp) / errlog.CRASH_NAME)
            head = (Path(tmp) / errlog.CRASH_NAME).read_text(encoding="utf-8")
            self.assertTrue(head.startswith("Daykeeper crash log | started "))
            self.assertIn(__version__, head)
            errlog.end_crash_log()
            self.assertFalse(faulthandler.is_enabled())
            self.assertFalse(errlog.start_crash_log(Path(tmp)), "a clean exit is no crash")
            errlog.stop_crash_log()

    @unittest.skipIf(sys.platform == "darwin" and os.environ.get("DAYKEEPER_CRASH_TESTS") != "1",
                     "a real abort leaves a system crash report in ~/Library/Logs/DiagnosticReports: on macOS only with DAYKEEPER_CRASH_TESTS=1")
    def test_a_child_that_aborts_leaves_a_crash_found_once(self):
        child = (
            "import sys, faulthandler\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            "if sys.platform == 'win32':\n"
            "    import msvcrt\n"
            "    msvcrt.SetErrorMode(0x0001 | 0x0002)  # no system dialog for the crash\n"
            "from pathlib import Path\n"
            "from daykeeper import errlog\n"
            "errlog.start_crash_log(Path(sys.argv[1]))\n"
            "faulthandler._sigabrt()\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            run = subprocess.run([sys.executable, "-c", child, tmp], capture_output=True, timeout=60, env={**os.environ, "PYTHONFAULTHANDLER": ""})  # noqa: S603
            self.assertNotEqual(run.returncode, 0)
            dump = (Path(tmp) / errlog.CRASH_NAME).read_text(encoding="utf-8", errors="replace")
            self.assertIn("Fatal Python error", dump)
            self.assertTrue(errlog.start_crash_log(Path(tmp)), "the next start finds it")
            self.assertIn("Fatal Python error", errlog.last_crash(Path(tmp)))
            self.assertTrue(errlog.last_crash(Path(tmp)).startswith("Daykeeper crash log"))
            errlog.stop_crash_log()
            self.assertFalse(errlog.start_crash_log(Path(tmp)), "and says so once")
            errlog.stop_crash_log()


if __name__ == "__main__":
    unittest.main()
