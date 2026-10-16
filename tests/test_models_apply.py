"""The results of background work applied to the models — what used to sit inside Tk
callbacks: a site's answer on a bookmark, a GitLab check on the integrations, a run and
the editor's fields on a routine, and gitlab.check with a fake server."""
import base64
import json
import unittest
import urllib.parse
from datetime import datetime

from daykeeper.bookmarks import Bookmark, Bookmarks
from daykeeper.gitlab import GitLabError, GitLabSettings, check
from daykeeper.integrations import Integrations
from daykeeper.routines import Routine, Routines, Schedule


class BookmarkSiteInfoTest(unittest.TestCase):
    def test_icon_colour_and_stamp_land_on_the_bookmark(self):
        bm = Bookmarks().add("Site", "https://site.example.com")
        self.assertTrue(bm.apply_site_info(bm.url, b"PNGDATA", "#112233", b"small", 26, now=1000.0))
        self.assertEqual(bm.favicon, base64.b64encode(b"PNGDATA").decode("ascii"))
        self.assertEqual(bm.small_icon_at(26), base64.b64encode(b"small").decode("ascii"))
        self.assertEqual(bm.site_colour, "#112233")
        self.assertEqual(bm.site_checked, 1000.0)
        self.assertFalse(bm.needs_site_info(now=1000.0 + 60))

    def test_an_answer_for_the_old_address_is_dropped(self):
        bm = Bookmarks().add("Site", "https://old.example.com")
        self.assertFalse(bm.apply_site_info("https://other.example.com", b"PNG", "#112233", b"s", 26, now=5.0))
        self.assertEqual((bm.favicon, bm.site_colour, bm.site_checked), ("", "", 0.0))

    def test_nothing_from_the_site_still_stamps_the_check(self):
        bm = Bookmarks().add("Site", "https://site.example.com")
        self.assertTrue(bm.apply_site_info(bm.url, None, "", b"", 26, now=7.0))
        self.assertEqual((bm.favicon, bm.site_colour, bm.site_checked), ("", "", 7.0))

    def test_small_icon_only_for_the_same_big_icon(self):
        bm = Bookmark(url="https://s.example.com", favicon="AAAA")
        self.assertFalse(bm.apply_small_icon("BBBB", b"small", 26), "the icon changed meanwhile")
        self.assertFalse(bm.apply_small_icon("AAAA", b"", 26), "could not be shrunk")
        self.assertTrue(bm.apply_small_icon("AAAA", b"small", 26))
        self.assertEqual(bm.favicon_small_px, 26)


class IntegrationsApplyCheckTest(unittest.TestCase):
    def test_first_check_is_the_baseline_second_brings_events(self):
        from daykeeper.gitlab import CheckResult, Event

        integ = Integrations()
        first = integ.apply_check(CheckResult("me", {"a!1": {"role": "author"}}, []), now=100.0)
        self.assertTrue(first)
        self.assertEqual((integ.gitlab.username, integ.gitlab_checked, integ.feed), ("me", 100.0, []))
        ev = Event("approved", "a!1", "Fix", "https://gl/1", "@{user} approved {mr}", {"user": "bob", "mr": "a!1"})
        second = integ.apply_check(CheckResult("me", {"a!1": {"role": "author", "approved_by": ["bob"]}}, [ev]), now=200.0)
        self.assertFalse(second)
        self.assertEqual([e.kind for e in integ.feed], ["approved"])
        self.assertEqual(integ.unread(), 1)
        self.assertEqual(integ.gitlab_checked, 200.0)
        integ.note_failed_check(300.0)
        self.assertEqual(integ.gitlab_checked, 300.0)
        self.assertEqual(len(integ.feed), 1, "a failed check changes nothing else")


class RoutineRunsTest(unittest.TestCase):
    def test_started_and_finished(self):
        r = Routine(name="x", kind="script", target="x.py")
        r.mark_started(50.0)
        self.assertEqual((r.last_run, r.last_code), (50.0, None))
        r.mark_finished(2)
        self.assertEqual(r.last_code, 2)
        r.mark_started(60.0, code=0)
        self.assertEqual((r.last_run, r.last_code), (60.0, 0))

    def test_upsert_waits_for_tomorrow_when_the_time_has_passed(self):
        routines = Routines()
        now = datetime(2026, 10, 14, 12, 0)  # a Wednesday, noon
        fields = dict(name="n", kind="script", target="x.py", args="", cwd="", terminal=False, icon="", schedule=Schedule(at="09:30", days=[2]))
        r = routines.upsert(None, fields, now)
        self.assertEqual(r.last_run, now.timestamp(), "09:30 has passed: it must not fire today")
        self.assertEqual(routines.due(now), [])
        later = routines.upsert(None, dict(fields, name="l", schedule=Schedule(at="18:00", days=[2])), now)
        self.assertEqual(later.last_run, 0.0, "18:00 is still ahead")
        self.assertEqual(len(routines.items), 2)

    def test_upsert_changes_fields_and_resets_only_when_the_time_moved(self):
        routines = Routines()
        now = datetime(2026, 10, 14, 12, 0)
        r = routines.add(name="n", kind="script", target="x.py", schedule=Schedule(at="18:00", days=[2]), last_run=1.0)
        same = routines.upsert(r, dict(name="renamed", schedule=Schedule(at="18:00", days=[2])), now)
        self.assertIs(same, r)
        self.assertEqual((r.name, r.last_run), ("renamed", 1.0), "the time did not move: last_run stays")
        routines.upsert(r, dict(name="renamed", schedule=Schedule(at="09:00", days=[2])), now)
        self.assertEqual(r.last_run, now.timestamp(), "moved to a time already past: waits for tomorrow")


ME = {"id": 7, "username": "me"}


def fake_server(mrs: list[dict], token: str = "tok"):
    """Answers the few endpoints `check` uses; `calls` records the paths asked."""
    calls: list[str] = []

    def fetch(url, headers):
        calls.append(url)
        if headers.get("PRIVATE-TOKEN") != token:
            return 401, b"{}"
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.replace("/api/v4", "")
        q = dict(urllib.parse.parse_qsl(parsed.query))
        if path == "/user":
            return 200, json.dumps(ME).encode()
        if path == "/merge_requests":
            if q.get("page", "1") != "1":
                return 200, b"[]"
            out = [m for m in mrs if m["state"] == q.get("state", "opened")]
            if "author_id" in q:
                out = [m for m in out if m["author"]["username"] == "me"]
            if "reviewer_id" in q:
                out = [m for m in out if any(r["username"] == "me" for r in m["reviewers"])]
            return 200, json.dumps(out).encode()
        parts = path.strip("/").split("/")
        iid = int(parts[3])
        mr = next(m for m in mrs if m["iid"] == iid)
        if len(parts) == 4:
            return 200, json.dumps(mr).encode()
        if parts[4] == "approvals":
            return 200, json.dumps({"approved_by": [{"user": {"username": u}} for u in mr.get("approvals", [])]}).encode()
        if parts[4] == "discussions":
            return 200, b"[]"
        return 404, b"{}"

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def mr(iid: int, approvals=()):
    return {
        "project_id": 5, "iid": iid, "title": f"MR {iid}", "state": "opened", "web_url": f"https://gl/x/-/merge_requests/{iid}",
        "author": {"username": "me"}, "reviewers": [], "references": {"full": f"grp/app!{iid}"},
        "blocking_discussions_resolved": True, "head_pipeline": {"status": "success"}, "approvals": list(approvals),
    }


class GitLabCheckTest(unittest.TestCase):
    def test_first_check_then_an_approval(self):
        mrs = [mr(1)]
        result = check("https://gl", "tok", GitLabSettings(), {}, fetch=fake_server(mrs))
        self.assertEqual(result.username, "me")
        self.assertEqual(list(result.snapshot), ["grp/app!1"])
        self.assertEqual(result.events, [], "the first look is the baseline")
        mrs[0]["approvals"] = ["bob"]
        again = check("https://gl", "tok", GitLabSettings(), result.snapshot, fetch=fake_server(mrs))
        self.assertEqual([(e.kind, e.args.get("user")) for e in again.events], [("approved", "bob")])
        integ = Integrations()
        self.assertTrue(integ.apply_check(result, 1.0))
        self.assertFalse(integ.apply_check(again, 2.0))
        self.assertEqual(integ.unread(), 1)

    def test_a_refusal_raises(self):
        with self.assertRaises(GitLabError):
            check("https://gl", "wrong", GitLabSettings(), {}, fetch=fake_server([mr(1)]))


if __name__ == "__main__":
    unittest.main()
