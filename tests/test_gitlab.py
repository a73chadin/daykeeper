"""GitLab watcher: snapshots from a fake API and the events between them."""
import json
import unittest
import urllib.parse

from daykeeper.gitlab import FULL_EVERY, RULES, CheckResult, GitLabClient, GitLabError, GitLabSettings, check as gitlab_check, closed_states, diff_snapshots, take_snapshot
from daykeeper.integrations import Integrations
from daykeeper.locale_ru import RU

ME = {"id": 7, "username": "me"}


def note(author, resolvable=True, resolved=False, system=False, at="2026-10-14T10:00:00Z"):
    return {"author": {"username": author}, "resolvable": resolvable, "resolved": resolved, "system": system, "created_at": at}


class FakeGitLab:
    """Answers the few endpoints the watcher uses from an in-memory model."""

    def __init__(self):
        self.mrs = {}  # (pid, iid) → dict
        self.approvals = {}
        self.discussions = {}
        self.approval_events = {}  # pid → [(iid, username)]
        self.pipelines = {}  # pid → [pipeline dicts]
        self.calls = []

    def add_mr(self, pid, iid, author, reviewers=(), title="Fix", state="opened", blocking=True, pipeline="success", project="grp/app"):
        self.mrs[(pid, iid)] = {
            "project_id": pid, "iid": iid, "title": title, "state": state, "web_url": f"https://gl/x/-/merge_requests/{iid}",
            "author": {"username": author}, "reviewers": [{"username": r} for r in reviewers], "sha": f"sha{iid}", "user_notes_count": 0,
            "references": {"full": f"{project}!{iid}"}, "blocking_discussions_resolved": blocking, "head_pipeline": {"status": pipeline},
        }
        self.approvals.setdefault((pid, iid), [])
        self.discussions.setdefault((pid, iid), [])

    def __call__(self, url, headers):
        self.calls.append(url)
        if headers.get("PRIVATE-TOKEN") != "tok":
            return 401, b"{}"
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.replace("/api/v4", "")
        q = dict(urllib.parse.parse_qsl(parsed.query))
        if path == "/user":
            return 200, json.dumps(ME).encode()
        if path == "/merge_requests":
            if q.get("page", "1") != "1":
                return 200, b"[]"
            out = [m for m in self.mrs.values() if m["state"] == q.get("state", "opened")]
            if "author_id" in q:
                out = [m for m in out if m["author"]["username"] == "me"]
            if "reviewer_id" in q:
                out = [m for m in out if any(r["username"] == "me" for r in m["reviewers"])]
            return 200, json.dumps(out).encode()
        parts = path.strip("/").split("/")  # projects/<pid>/merge_requests/<iid>[/x]
        if len(parts) == 3:  # projects/<pid>/events | pipelines
            pid, first = int(parts[1]), q.get("page", "1") == "1"
            if parts[2] == "events":
                out = [{"author": {"username": u}, "target_iid": i, "action_name": "approved"} for i, u in self.approval_events.get(pid, [])]
            else:
                out = self.pipelines.get(pid, [])
            return 200, json.dumps(out if first else []).encode()
        pid, iid = int(parts[1]), int(parts[3])
        if len(parts) == 4:
            return 200, json.dumps(self.mrs[(pid, iid)]).encode()
        if parts[4] == "approvals":
            return 200, json.dumps({"approved_by": [{"user": {"username": u}} for u in self.approvals[(pid, iid)]]}).encode()
        if parts[4] == "discussions":
            return 200, json.dumps(self.discussions[(pid, iid)] if q.get("page", "1") == "1" else []).encode()
        return 404, b"{}"


def check(fake, old, rules=None):
    rules = rules or GitLabSettings()
    client = GitLabClient("https://gl", "tok", fetch=fake)
    new = take_snapshot(client, ME, rules, old)
    gone = {k: v for k, v in old.items() if k not in new}
    events = diff_snapshots(old, new, rules, closed_states(client, gone)) if old else []
    return new, events


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.gl = FakeGitLab()
        self.gl.add_mr(1, 10, "me", title="My feature", blocking=False)
        self.gl.discussions[(1, 10)] = [{"notes": [note("bob")]}]
        self.gl.add_mr(1, 20, "ann", reviewers=["me"], title="Ann's fix")
        self.state, events = check(self.gl, {})
        self.assertEqual(events, [], "the first look is a baseline")

    def test_snapshot_shape(self):
        mine, review = self.state["grp/app!10"], self.state["grp/app!20"]
        self.assertEqual((mine["role"], mine["approved_by"], mine["blocking_resolved"], mine["threads"]), ("author", [], False, 0))
        self.assertEqual((review["role"], review["reviewer"], review["my_threads"], review["replies"]), ("reviewer", True, 0, 0))

    def test_approval_resolution_and_pipeline(self):
        self.gl.approvals[(1, 10)] = ["bob"]
        self.gl.mrs[(1, 10)]["blocking_discussions_resolved"] = True
        self.gl.mrs[(1, 10)]["head_pipeline"] = {"status": "failed"}
        _new, events = check(self.gl, self.state)
        self.assertEqual(sorted(e.kind for e in events), ["approved", "pipeline", "resolved"])
        approved = next(e for e in events if e.kind == "approved")
        self.assertEqual(approved.args, {"user": "bob", "mr": "grp/app!10"})
        self.assertEqual(approved.url, "https://gl/x/-/merge_requests/10")

    def test_merged(self):
        self.gl.mrs[(1, 10)]["state"] = "merged"
        new, events = check(self.gl, self.state)
        self.assertNotIn("grp/app!10", new)
        self.assertEqual([e.kind for e in events], ["merged"])

    def test_closed_is_not_merged(self):
        self.gl.mrs[(1, 10)]["state"] = "closed"
        _new, events = check(self.gl, self.state)
        self.assertEqual(events, [])

    def test_review_requested_my_threads_and_replies(self):
        self.gl.add_mr(2, 30, "kim", reviewers=["me"], title="New one")
        self.gl.discussions[(1, 20)] = [
            {"notes": [note("me", resolved=True), note("ann", resolved=True, at="2026-10-14T11:00:00Z")]},
            {"notes": [note("me"), note("ann", system=True)]},
            {"notes": [note("zed"), note("ann")]},  # not my thread
        ]
        _new, events = check(self.gl, self.state)
        kinds = {e.kind: e for e in events}
        self.assertEqual(set(kinds), {"review_requested", "my_threads_resolved", "replies"})
        self.assertEqual(kinds["review_requested"].args["author"], "kim")
        self.assertEqual(kinds["my_threads_resolved"].args["n"], 1)
        self.assertEqual(kinds["replies"].args["user"], "ann")

    def test_rules_switched_off_are_silent_and_not_fetched(self):
        rules = GitLabSettings(rules={k: False for k in RULES})
        self.gl.calls.clear()
        self.gl.approvals[(1, 10)] = ["bob"]
        _new, events = check(self.gl, self.state, rules)
        self.assertEqual(events, [])
        self.assertFalse(any("approvals" in c or "discussions" in c for c in self.gl.calls))


class LightCheckTest(unittest.TestCase):
    """Between full checks: approvals and pipelines once a project, threads only where the
    count of notes moved, nothing about unwatched projects — and no event is lost."""

    def setUp(self):
        self.gl = FakeGitLab()
        for i in range(10):
            self.gl.add_mr(1, 100 + i, "me", blocking=False)
            self.gl.add_mr(1, 200 + i, "ann", reviewers=["me"])
        self.gl.discussions[(1, 200)] = [{"notes": [note("me")]}]  # my thread, still open
        self.state = gitlab_check("https://gl", "tok", GitLabSettings(), {}, self.gl).snapshot

    def light(self, settings=None):
        self.gl.calls.clear()
        result = gitlab_check("https://gl", "tok", settings or GitLabSettings(), self.state, self.gl, since=1_000_000.0)
        return result, {e.kind: e for e in result.events}

    def test_a_quiet_check_asks_a_handful_of_times(self):
        result, events = self.light()
        self.assertEqual(events, {})
        self.assertFalse(result.full)
        asked = [c.split("/api/v4")[1].split("?")[0] for c in self.gl.calls]
        # me, two lists, approvals and pipelines of the project, the one MR with my open thread
        self.assertEqual(len(asked), 6, asked)
        self.assertEqual(sum("/projects/1/merge_requests/" in a for a in asked), 1)

    def test_nothing_is_lost(self):
        self.gl.approval_events[1] = [(101, "bob")]
        self.gl.pipelines[1] = [{"id": 5, "sha": "sha102", "ref": "feature", "status": "failed"}]
        self.gl.mrs[(1, 103)]["blocking_discussions_resolved"] = True
        self.gl.discussions[(1, 103)] = [{"notes": [note("bob", resolved=True)]}]
        self.gl.discussions[(1, 200)] = [{"notes": [note("me", resolved=True)]}]  # resolving moves no count
        self.gl.discussions[(1, 201)] = [{"notes": [note("me"), note("ann")]}]
        self.gl.mrs[(1, 201)]["user_notes_count"] = 2
        _result, events = self.light()
        self.assertEqual(set(events), {"approved", "pipeline", "resolved", "my_threads_resolved", "replies"})
        self.assertEqual(events["approved"].mr, "grp/app!101")
        self.assertEqual(events["pipeline"].mr, "grp/app!102")

    def test_a_pipeline_of_merged_results_counts_by_its_ref(self):
        self.gl.pipelines[1] = [{"id": 9, "sha": "other", "ref": "refs/merge-requests/104/merge", "status": "failed"}]
        _result, events = self.light()
        self.assertEqual(events["pipeline"].mr, "grp/app!104")

    def test_an_approval_seen_again_is_not_news(self):
        self.gl.approval_events[1] = [(101, "bob")]
        result, _events = self.light()
        self.state = result.snapshot
        _result, events = self.light()
        self.assertEqual(events, {})

    def test_only_the_chosen_projects(self):
        self.gl.add_mr(2, 300, "me", project="grp/other")
        self.gl.add_mr(2, 301, "kim", reviewers=["me"], project="grp/other")
        result, events = self.light(GitLabSettings(projects=["grp/app"]))
        self.assertEqual(result.projects, ["grp/app", "grp/other"], "every project is offered in the settings")
        self.assertFalse(any(k.startswith("grp/other") for k in result.snapshot))
        self.assertEqual(events, {}, "a review in an unwatched project says nothing")
        self.assertFalse(any("/projects/2/" in c for c in self.gl.calls))
        self.state = result.snapshot
        self.gl.mrs[(1, 100)]["state"] = "merged"
        result, events = self.light(GitLabSettings(projects=["grp/other"]))
        self.assertNotIn("merged", events, "a project switched off: its MRs did not close")
        self.assertFalse(any("/projects/1/merge_requests/" in c for c in self.gl.calls))

    def test_when_a_full_check_is_due(self):
        integ = Integrations()
        self.assertEqual(integ.since(5000.0), 0.0, "the first check is full")
        integ.gitlab_state = {"grp/app!1": {}}
        integ.apply_check(CheckResult("me", {"grp/app!1": {}}, [], ["grp/app"], started=4000.0, full=True), 4010.0)
        self.assertEqual(integ.since(5000.0), 4000.0)
        integ.apply_check(CheckResult("me", {"grp/app!1": {}}, [], ["grp/app"], started=4500.0, full=False), 4510.0)
        self.assertEqual((integ.since(5000.0), integ.gitlab_full), (4500.0, 4000.0))
        self.assertEqual(integ.since(4000.0 + FULL_EVERY), 0.0, "once an hour a full one")
        back = Integrations.from_dict(integ.to_dict())
        self.assertEqual((back.gitlab_synced, back.gitlab_full, back.gitlab_projects), (4500.0, 4000.0, ["grp/app"]))
        self.assertEqual(GitLabSettings.from_dict({"projects": ["a/b", 3, ""]}).projects, ["a/b"])


class ClientTest(unittest.TestCase):
    def test_bad_token(self):
        client = GitLabClient("https://gl", "wrong", fetch=FakeGitLab())
        with self.assertRaises(GitLabError) as ctx:
            client.me()
        self.assertIn("401", str(ctx.exception))

    def test_pagination_stops_on_a_short_page(self):
        pages = {1: list(range(100)), 2: list(range(100, 130))}

        def fetch(url, headers):
            page = int(dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))["page"])
            return 200, json.dumps(pages.get(page, [])).encode()

        self.assertEqual(len(GitLabClient("https://gl", "t", fetch=fetch).get_all("/x")), 130)


class TokenExpiryTest(unittest.TestCase):
    """GET /personal_access_tokens/self, from fakes only."""

    @staticmethod
    def answering(status: int, body: object):
        def fetch(url, headers):
            if url.endswith("/personal_access_tokens/self"):
                return status, json.dumps(body).encode()
            return FakeGitLab()(url, headers)

        return fetch

    def client(self, status: int, body: object) -> GitLabClient:
        return GitLabClient("https://gitlab.example.com", "tok", fetch=self.answering(status, body))

    def test_the_last_day_of_the_token(self):
        self.assertEqual(self.client(200, {"id": 1, "expires_at": "2026-12-31"}).token_expiry(), "2026-12-31")
        self.assertEqual(self.client(200, {"id": 1, "expires_at": None}).token_expiry(), "", "never expires")

    def test_silent_when_the_server_does_not_say(self):
        self.assertIsNone(self.client(403, {"message": "403 Forbidden"}).token_expiry(), "a token without read_api")
        self.assertIsNone(self.client(404, {"message": "404 Not Found"}).token_expiry(), "an older GitLab")
        self.assertIsNone(self.client(200, {"id": 1}).token_expiry())
        self.assertIsNone(self.client(200, {"expires_at": "soon"}).token_expiry())

    def test_the_check_asks_only_when_told(self):
        fetch = self.answering(200, {"expires_at": "2026-12-31"})
        plain = gitlab_check("https://gitlab.example.com", "tok", GitLabSettings(), {}, fetch=fetch)
        self.assertEqual((plain.token_asked, plain.token_expires), (False, None))
        asked = gitlab_check("https://gitlab.example.com", "tok", GitLabSettings(), {}, fetch=fetch, ask_expiry=True)
        self.assertEqual((asked.token_asked, asked.token_expires), (True, "2026-12-31"))
        refused = gitlab_check("https://gitlab.example.com", "tok", GitLabSettings(), {}, fetch=self.answering(403, {}), ask_expiry=True)
        self.assertEqual((refused.token_asked, refused.token_expires), (True, None), "the check itself still works")

    def test_once_a_day(self):
        integ = Integrations()
        self.assertTrue(integ.token_expiry_due(1_000_000.0))
        integ.apply_check(CheckResult("me", {}, [], token_asked=True, token_expires="2026-12-31"), 1_000_000.0)
        self.assertFalse(integ.token_expiry_due(1_000_000.0 + 3600))
        self.assertTrue(integ.token_expiry_due(1_000_000.0 + 24 * 3600))
        integ.apply_check(CheckResult("me", {}, []), 2_000_000.0)
        self.assertEqual(integ.gitlab_token_checked, 1_000_000.0, "a check that did not ask keeps the day")
        self.assertEqual(Integrations.from_dict(integ.to_dict()).gitlab_token_checked, 1_000_000.0)


class StoreTest(unittest.TestCase):
    def test_settings_and_feed_round_trip(self):
        integ = Integrations()
        integ.gitlab.url = "https://gl"
        integ.gitlab.rules["merged"] = False
        integ.gitlab_state = {"a!1": {"role": "author"}}
        _new, _ev = None, None
        from daykeeper.gitlab import Event

        integ.add_events([Event("approved", "a!1", "t", "u", "x"), Event("merged", "a!2", "t", "u", "y")])
        self.assertEqual(integ.unread(), 2)
        back = Integrations.from_dict(integ.to_dict())
        self.assertEqual((back.gitlab.url, back.gitlab.rule("merged"), back.gitlab_state), ("https://gl", False, {"a!1": {"role": "author"}}))
        back.mark_all_read()
        self.assertEqual(back.unread(), 0)
        self.assertEqual(GitLabSettings.from_dict({"interval_min": 7}).interval_min, 5)


class CatalogTest(unittest.TestCase):
    def test_rule_labels_and_event_texts_are_translated(self):
        texts = [label for label, _on in RULES.values()] + [
            "Review requested: {mr} by @{author}", "@{user} approved {mr}", "All threads are resolved on {mr}",
            "The pipeline failed on {mr}", "{n} of your threads resolved on {mr}", "@{user} replied in your threads on {mr}",
            "{mr} was merged", "PROJECTS", "None ticked — all projects are watched.", "Only the ticked projects are watched.",
            "The projects with your open merge requests appear here after the first check.",
        ]
        self.assertEqual([t for t in texts if t not in RU], [])


if __name__ == "__main__":
    unittest.main()
