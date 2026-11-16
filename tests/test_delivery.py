"""Delivery must be acknowledged by a successful vault save, including duplicates."""
import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from daykeeper import cli
from daykeeper.journal import append_inbox, prune_receipts, today
from daykeeper.session import Session
from daykeeper.vault import Vault
from tests.helpers import reset_globals


class DeliveryTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.session = Session(Path(self.tmp.name) / "vault.pkv")
        self.session.unlock("delivery-test-master")
        self.token = "a" * 32

    def tearDown(self):
        self.session.lock()
        self.tmp.cleanup()
        reset_globals()

    def send(self, status="manual", items=None, origin=""):
        return append_inbox(self.session.inbox_path, status, items or ["APP-1"], origin=origin, receipt=self.token)

    def confirm(self, count=1):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return cli.wait_for_receipts(self.session.inbox_path, self.token, count, 0)

    def test_saved_batch_and_duplicate_are_confirmed(self):
        self.send(items=["APP-1", "APP-2", "APP-1"])
        self.assertEqual(self.session.pull_inbox(), (2, 3))
        persisted = Vault.open(self.session.vault_path, "delivery-test-master")
        try:
            self.assertEqual(len(persisted.journal.get(today()).activities), 2)
        finally:
            persisted.forget()
        self.assertEqual(self.confirm(3), 0)

    def test_failed_save_has_no_receipt_and_retry_works(self):
        self.send()
        with patch.object(self.session.vault, "save", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.session.pull_inbox()
        self.assertEqual(self.confirm(), 2)
        self.session.pull_inbox()
        self.assertEqual(self.confirm(), 0)

    def test_filtered_event_is_rejected(self):
        self.session.write_settings({"gitlab_url": "https://work.example"})
        self.send(origin="personal.example")
        self.session.pull_inbox()
        self.assertEqual(self.confirm(), 2)

    def test_cleanup_failure_does_not_change_confirmed_delivery(self):
        self.send()
        self.session.pull_inbox()
        with patch.object(Path, "unlink", side_effect=PermissionError("busy")):
            self.assertEqual(self.confirm(), 0)

    def test_only_old_receipts_are_pruned(self):
        self.send()
        self.session.pull_inbox()
        folder = self.session.inbox_path.with_name(self.session.inbox_path.name + "-receipts")
        old = folder / (self.token + "-0.json")
        fresh = folder / ("b" * 32 + "-0.json")
        unrelated = folder / "user.json"
        fresh.write_text("{}", encoding="utf-8")
        unrelated.write_text("{}", encoding="utf-8")
        then = time.time() - 8 * 86400
        os.utime(old, (then, then))
        os.utime(unrelated, (then, then))
        prune_receipts(self.session.inbox_path)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue(unrelated.exists())

    def test_empty_inbox_without_receipt_is_not_success(self):
        self.assertFalse(self.session.inbox_path.exists())
        self.assertEqual(self.confirm(), 2)

    def test_locked_app_does_not_confirm(self):
        self.send()
        self.session.lock()
        self.assertIsNone(self.session.pull_inbox())
        self.assertEqual(self.confirm(), 2)

    def test_cli_waits_by_default_for_both_case_events(self):
        for status in ("manual", "auto"):
            with patch.object(cli, "wait_for_receipts", return_value=2) as wait, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.inbox_main([status, "APP-1", "--work", "--inbox", str(self.session.inbox_path)]), 2)
            self.assertEqual(wait.call_args.args[-1], 10)
        rows = [json.loads(line) for line in self.session.inbox_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len({row["receipt"] for row in rows}), 2)

    def test_coverage_sends_both_even_if_first_confirmation_fails(self):
        with patch.object(cli, "inbox_main", side_effect=[2, 0]) as send:
            self.assertEqual(cli.main(["coverage", "APP-1"]), 2)
        self.assertEqual([call.args[0] for call in send.call_args_list], [["manual", "APP-1"], ["auto", "APP-1"]])

    def test_coverage_confirms_both_events_on_the_requested_day(self):
        with patch.object(cli, "_data_dir", None), patch.object(cli.time, "sleep", side_effect=lambda _seconds: self.session.pull_inbox()), contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(["--data", self.tmp.name, "coverage", "APP-1", "--work", "--date", "2026-01-02"])
        self.assertEqual(code, 0)
        persisted = Vault.open(self.session.vault_path, "delivery-test-master")
        try:
            activities = persisted.journal.get("2026-01-02").activities
            self.assertEqual({a["kind"] for a in activities}, {"manual", "auto"})
            self.assertTrue(all(a["text"] == "APP-1" for a in activities))
            self.assertIsNone(persisted.journal.get(today()))
        finally:
            persisted.forget()


if __name__ == "__main__":
    unittest.main()
