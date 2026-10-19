"""The shared plumbing of the new features in the data: the calendar and the clipboard
history in the vault, the Jira state in the integrations, the clipboard settings in the
lock settings."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from daykeeper.cliphistory import LIMITS
from daykeeper.gitlab import FULL_EVERY, CheckResult, Event
from daykeeper.integrations import Integrations
from daykeeper.jira import JiraSettings
from daykeeper.revisions import DOMAINS
from daykeeper.security import SecurityPrefs
from daykeeper.undo import History
from daykeeper.vault import UNDO_DOMAINS, Vault


class VaultCalendarClipboardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "v.pkv"

    def tearDown(self):
        self.tmp.cleanup()

    def test_both_keys_are_kept_in_the_file(self):
        v = Vault.create(self.path, "master-pass-1")
        v.calendar.add("Standup", "2026-10-19", time="10:00", remind_min=10)
        self.assertTrue(v.clipboard.add("copied text", v.clip_sealer, 1.0))
        v.save()
        v2 = Vault.open(self.path, "master-pass-1")
        self.assertEqual([e.title for e in v2.calendar.events], ["Standup"])
        self.assertEqual(v2.clipboard.items[0].text(v2.clip_sealer), "copied text")
        raw = json.loads(v2._payload())
        self.assertNotIn("copied text", json.dumps(raw["clipboard"]), "ciphertext only")

    def test_an_old_file_without_the_keys_opens(self):
        v = Vault.create(self.path, "master-pass-1")
        data = json.loads(v._payload())
        del data["calendar"], data["clipboard"]
        v._load(data)
        self.assertEqual(v.calendar.events, [])
        self.assertEqual(v.clipboard.items, [])

    def test_the_sealer_is_gone_once_locked(self):
        v = Vault.create(self.path, "master-pass-1")
        self.assertIsNotNone(v.clip_sealer)
        v.clipboard.add("x1", v.clip_sealer, 1.0)
        v.forget()
        self.assertIsNone(v.clip_sealer)
        self.assertFalse(v.clipboard.add("x2", v.clip_sealer, 2.0), "nothing is recorded while locked")

    def test_calendar_is_exported_the_clipboard_is_not(self):
        v = Vault.create(self.path, "master-pass-1")
        v.calendar.add("Release", "2026-11-01")
        v.clipboard.add("private", v.clip_sealer, 1.0)
        doc = v.export_data()
        self.assertEqual([e["title"] for e in doc["data"]["calendar"]], ["Release"])
        self.assertNotIn("clipboard", doc["data"])
        other = Vault.create(Path(self.tmp.name) / "o.pkv", "master-pass-2")
        other.clipboard.add("mine", other.clip_sealer, 1.0)
        other.import_data(doc)
        self.assertEqual([e.title for e in other.calendar.events], ["Release"])
        self.assertEqual(other.clipboard.items[0].text(other.clip_sealer), "mine", "the import leaves the history alone")

    def test_a_new_password_reseals_the_history(self):
        v = Vault.create(self.path, "master-pass-1")
        v.clipboard.add("one", v.clip_sealer, 1.0)
        v.clipboard.add("two", v.clip_sealer, 2.0)
        v.clipboard.pin(1)
        old_blob = v.clipboard.items[0].sealed
        v.change_password("master-pass-2")
        self.assertNotEqual(v.clipboard.items[0].sealed, old_blob)
        v2 = Vault.open(self.path, "master-pass-2")
        self.assertEqual([i.text(v2.clip_sealer) for i in v2.clipboard.items], ["two", "one"])
        self.assertTrue(v2.clipboard.items[1].pinned)

    def test_calendar_is_an_undo_domain_the_clipboard_is_not(self):
        self.assertIn("calendar", UNDO_DOMAINS)
        self.assertNotIn("clipboard", UNDO_DOMAINS)
        self.assertIn("calendar", DOMAINS)
        v = Vault.create(self.path, "master-pass-1")
        h = History(v.object_states, UNDO_DOMAINS)
        h.reset()
        e = v.calendar.add("Meeting", "2026-10-20", time="09:00")
        h.capture()
        e.title = "Moved meeting"
        h.capture()
        e.notified = "2026-10-20"  # the reminder was shown: the clock, not an edit
        h.capture()
        self.assertEqual(h.undo(v.set_object_state), {"calendar"})
        restored = v.calendar.get(e.id)
        self.assertEqual(restored.title, "Meeting")
        self.assertEqual(restored.notified, "2026-10-20", "an undo does not show a reminder again")
        self.assertEqual(h.undo(v.set_object_state), {"calendar"})
        self.assertEqual(v.calendar.events, [])
        v.clipboard.add("not undone", v.clip_sealer, 1.0)
        h.capture()
        self.assertEqual(h.undo(v.set_object_state), set(), "a copied text is no undo step")

    def test_calendar_domain_state_round_trip(self):
        v = Vault.create(self.path, "master-pass-1")
        v.calendar.add("A", "2026-10-21", notified="2026-10-21")
        state = v.domain_state("calendar")
        self.assertNotIn("notified", state)
        v.calendar.events[0].title = "B"
        v.set_domain_state("calendar", state)
        self.assertEqual(v.calendar.events[0].title, "A")
        self.assertEqual(v.calendar.events[0].notified, "2026-10-21")


class JiraStateTest(unittest.TestCase):
    def _result(self, events=(), started=100.0, full=True):
        return CheckResult("ann", {"APP-1": {"status": "Open"}}, list(events), [], started, full=full)

    def test_round_trip_and_tolerance(self):
        integ = Integrations()
        integ.jira = JiraSettings(url="https://jira.example.com", enabled=True)
        integ.jira_state = {"APP-1": {"status": "Open"}}
        integ.jira_checked, integ.jira_synced, integ.jira_full = 3.0, 2.0, 1.0
        back = Integrations.from_dict(json.loads(json.dumps(integ.to_dict())))
        self.assertEqual(back.jira.url, "https://jira.example.com")
        self.assertTrue(back.jira.enabled)
        self.assertEqual(back.jira_state, {"APP-1": {"status": "Open"}})
        self.assertEqual((back.jira_checked, back.jira_synced, back.jira_full), (3.0, 2.0, 1.0))
        old = Integrations.from_dict({"gitlab_checked": 5})  # a file from before Jira
        self.assertEqual(old.jira_state, {})
        self.assertFalse(old.jira.enabled)
        bad = Integrations.from_dict({"jira": "x", "jira_state": [1], "jira_checked": "soon", "jira_full": 10**400})
        self.assertEqual((bad.jira_state, bad.jira_checked, bad.jira_full), ({}, 0.0, 0.0))
        self.assertEqual(bad.jira.url, "")

    def test_first_check_is_a_baseline_then_events_go_to_the_shared_feed(self):
        integ = Integrations()
        integ.add_events([Event("merged", "g/r!1", "MR", "", "Merged", ts=1.0)])
        self.assertEqual(integ.jira_since(200.0), 0.0, "no snapshot yet: a full check")
        self.assertTrue(integ.apply_jira_check(self._result(started=100.0), 150.0))
        self.assertEqual((integ.jira_checked, integ.jira_synced, integ.jira_full, integ.jira.username), (150.0, 100.0, 100.0, "ann"))
        self.assertEqual(integ.jira_since(200.0), 100.0, "a light check looks back to the last start")
        self.assertEqual(integ.jira_since(100.0 + FULL_EVERY), 0.0, "once an hour a full one")
        ev = Event("status", "APP-1", "Issue", "", "{key} moved", {"key": "APP-1"}, ts=2.0, source="jira")
        self.assertFalse(integ.apply_jira_check(self._result([ev], started=300.0, full=False), 310.0))
        self.assertEqual(integ.jira_full, 100.0, "a light check keeps the time of the full one")
        self.assertEqual([e.source for e in integ.feed], ["jira", "gitlab"])
        integ.note_failed_jira_check(999.0)
        self.assertEqual(integ.jira_checked, 999.0)
        self.assertEqual(integ.gitlab_checked, 0.0, "Jira does not move the GitLab clock")


class ClipPrefsTest(unittest.TestCase):
    def test_defaults_round_trip_and_tolerance(self):
        p = SecurityPrefs()
        self.assertEqual((p.clip_history, p.clip_limit, p.clip_skip_secrets), (False, 50, True))
        self.assertIn(p.clip_limit, LIMITS)
        p.clip_history, p.clip_limit, p.clip_skip_secrets = True, 100, False
        back = SecurityPrefs.from_dict(json.loads(json.dumps(p.to_dict())))
        self.assertEqual((back.clip_history, back.clip_limit, back.clip_skip_secrets), (True, 100, False))
        old = SecurityPrefs.from_dict({"app_lock_min": 5})
        self.assertEqual((old.clip_history, old.clip_limit, old.clip_skip_secrets), (False, 50, True))
        bad = SecurityPrefs.from_dict({"clip_history": "yes", "clip_limit": 7, "clip_skip_secrets": 0})
        self.assertEqual((bad.clip_history, bad.clip_limit), (False, 50))
        self.assertTrue(bad.clip_skip_secrets, "only an explicit false turns the filter off")
        self.assertEqual(SecurityPrefs.from_dict({"clip_limit": True}).clip_limit, 50)


if __name__ == "__main__":
    unittest.main()
