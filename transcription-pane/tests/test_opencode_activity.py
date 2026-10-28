"""The fast OpenCode activity read: /api/session/active alone, every open sibling of the current turn, and a
fanout/deadline-bounded detail read that never turns a detail failure or a malformed busy map into idle."""
import http.client
import json
import sys
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server
from opencode_programs import OpenCodePrograms, Unavailable

SID_A = "ses_activityAA1"
SID_B = "ses_activityBB2"
NOW = 1_792_640_000.0


def tool(tool_id, name="shell", status="running", command="pytest -q", created=NOW - 30):
    state = {"status": status, "input": ({"command": command} if command else {})}
    if status in ("completed", "error"):
        state["output"] = "stored output"
    return {"type": "tool", "id": tool_id, "name": name, "state": state,
            "time": {"created": int(created * 1000),
                     **({"completed": int((created + 1) * 1000)} if status in ("completed", "error") else {})}}


def step(mid, tools, text="working", created=NOW - 31, settled=False):
    return {"id": mid, "type": "assistant",
            "content": [{"type": "text", "text": text}, *tools],
            "time": {"created": int(created * 1000),
                     **({"completed": int((created + 2) * 1000)} if settled else {})}}


def idle(mid="msg_idle", created=NOW - 20):
    return {"id": mid, "type": "idle", "time": {"created": int(created * 1000)}}


def user(mid="msg_user", created=NOW - 40):
    return {"id": mid, "type": "user", "text": "go", "time": {"created": int(created * 1000)}}


class FakeOpenCode:
    """A scripted OpenCode: one busy map, per-session newest-message pages and a per-path hook."""

    def __init__(self, active=(), pages=None, active_answer=None, fail_active=None):
        self.active = list(active)
        self.pages = dict(pages or {})
        self.active_answer = active_answer
        self.fail_active = fail_active
        self.calls = []
        self.hook = None

    def __call__(self, path, timeout, *, raw=False):
        self.calls.append(path)
        if self.hook:
            self.hook(path)
        if path == "/api/session/active":
            if self.fail_active:
                raise Unavailable(self.fail_active)
            answer = self.active_answer if self.active_answer is not None else {
                "data": {sid: {"type": "running"} for sid in self.active}}
        elif "/message?" in path:
            sid = path.split("/api/session/", 1)[1].split("/message", 1)[0]
            if sid not in self.pages:
                raise Unavailable("OpenCode has no such session")
            page = self.pages[sid]
            if isinstance(page, list) and "order=desc" in path:
                page = list(reversed(page))                    # the API's newest-first page
            answer = {"data": page}
        else:
            raise Unavailable("unexpected OpenCode path")
        return json.dumps(answer).encode() if raw else answer

    def active_reads(self):
        return [path for path in self.calls if path == "/api/session/active"]

    def detail_reads(self):
        return [path for path in self.calls if "/message?" in path]


class ActivityReader(unittest.TestCase):
    def reader(self, fake, clock=None, spawn=None):
        clock = clock or (lambda: NOW)
        return OpenCodePrograms(request=fake, clock=clock, spawn=spawn or (lambda work: work()))

    def test_notifications_do_not_hide_a_long_wait_beyond_the_first_page(self):
        paths = []
        def request(path, timeout, *, raw=False):
            paths.append(path)
            if path == "/api/session/active":
                return {"data": {SID_A: {"type": "running"}}}
            answer = ({"data": [user("notification" + str(i)) for i in range(4)], "cursor": {"next": "older"}}
                      if "cursor=older" not in path else
                      {"data": [step("still-running", [tool("wait", command="bash oc_seat.sh wait " + SID_B)])]})
            return json.dumps(answer).encode()
        got = self.reader(request).activity()
        self.assertEqual(got["sessions"][0]["runningCalls"][0]["id"], "wait")
        self.assertEqual(len(paths), 3)

    def test_active_detail_has_no_first_64_session_cutoff(self):
        ids = ["ses_active%04d" % i for i in range(70)]
        fake = FakeOpenCode(active=ids, pages={sid: [idle()] for sid in ids})
        got = self.reader(fake).activity()
        self.assertEqual(len(got["sessions"]), 70)
        self.assertTrue(all(row["toolStatus"] == "complete" for row in got["sessions"]))

    def test_all_open_siblings_survive_the_last_one_finishing(self):
        first = tool("call_shell", command="long parallel shell")
        second = tool("call_read", name="read", command=None, created=NOW - 29)
        fake = FakeOpenCode(active={SID_A}, pages={SID_A: [user(), step("msg_new", [first, second])]})
        clock = [NOW]
        reader = self.reader(fake, clock=lambda: clock[0])
        got = reader.activity()
        self.assertEqual((got["status"], got["backend"], got["schemaVersion"]), ("complete", "opencode", 1))
        self.assertEqual(got["active"], [SID_A])
        self.assertEqual(got["sessions"][0]["toolStatus"], "complete")
        calls = got["sessions"][0]["runningCalls"]
        self.assertEqual([call["id"] for call in calls], ["call_shell", "call_read"])
        shell = calls[0]
        self.assertEqual({key: shell[key] for key in ("name", "status", "time", "resultTime", "outputBytes")},
                         {"name": "shell", "status": "pending-or-missing", "time": NOW - 30,
                          "resultTime": None, "outputBytes": None})
        self.assertEqual(shell["command"], "long parallel shell")
        self.assertEqual(shell["previewData"]["status"], "available")
        self.assertNotIn("output", shell)
        self.assertNotIn("command", calls[1])                  # the read call has no shell command
        # The LAST sibling finishes first: the earlier long call must still be the one reported.
        second["state"] = {"status": "completed", "input": {}, "output": "done"}
        clock[0] += 2
        reader.activity()                                      # starts the refresh; still serves the last answer
        refreshed = reader.activity()
        self.assertEqual([call["id"] for call in refreshed["sessions"][0]["runningCalls"]], ["call_shell"])

    def test_busy_false_unknown_and_malformed_are_never_fabricated_idle(self):
        page = [user(), step("msg_new", [tool("call_a")])]
        # An empty busy map is a complete, truthful idle map (no detail reads at all).
        fake = FakeOpenCode(active=set(), pages={SID_A: page})
        got = self.reader(fake).activity()
        self.assertEqual((got["status"], got["active"], got["sessions"]), ("complete", [], []))
        self.assertEqual(fake.detail_reads(), [])
        # Busy without an open call is busy with nothing running, never a false call.
        fake = FakeOpenCode(active={SID_A}, pages={SID_A: [user(), step("msg_new", [])]})
        got = self.reader(fake).activity()
        self.assertEqual((got["active"], got["sessions"][0]["runningCalls"]), ([SID_A], []))
        # A malformed /api/session/active body is unavailable: no active list and no sessions at all.
        for answer in ({"data": []}, {"data": "not a map"}, []):
            fake = FakeOpenCode(active={SID_A}, pages={SID_A: page}, active_answer=answer)
            got = self.reader(fake).activity()
            self.assertEqual((got["status"], got.get("error"), "active" in got, "sessions" in got),
                             ("unavailable", "OpenCode session activity has an unexpected shape", False, False))
        down = self.reader(FakeOpenCode(fail_active="OpenCode unreachable")).activity()
        self.assertEqual((down["status"], down["error"]), ("unavailable", "OpenCode unreachable"))

    def test_idle_boundary_drops_a_stale_part_and_a_later_turn_reports_only_its_own(self):
        stale = step("msg_stale", [tool("call_stale")], created=NOW - 60)
        current = [user(created=NOW - 20), step("msg_current", [tool("call_current")], created=NOW - 10)]
        fake = FakeOpenCode(active={SID_A}, pages={SID_A: [stale, idle(created=NOW - 50), *current]})
        got = self.reader(fake).activity()
        self.assertEqual([call["id"] for call in got["sessions"][0]["runningCalls"]], ["call_current"])
        # A busy session whose newest page ends at its idle receipt has nothing running now.
        fake = FakeOpenCode(active={SID_A}, pages={SID_A: [stale, idle()]})
        got = self.reader(fake).activity()
        self.assertEqual(got["sessions"][0], {"id": SID_A, "toolStatus": "complete", "runningCalls": []})

    def test_detail_failure_keeps_the_busy_map_and_names_unavailable(self):
        fake = FakeOpenCode(active={SID_A, SID_B},
                            pages={SID_A: [user(), step("msg_new", [tool("call_a")])]})
        got = self.reader(fake).activity()
        self.assertEqual(got["status"], "complete")
        self.assertEqual(got["active"], [SID_A, SID_B])
        rows = {row["id"]: row for row in got["sessions"]}
        self.assertEqual([call["id"] for call in rows[SID_A]["runningCalls"]], ["call_a"])
        self.assertEqual((rows[SID_B]["toolStatus"], rows[SID_B]["runningCalls"]), ("unavailable", []))
        self.assertIn("no such session", rows[SID_B]["error"])

    def test_cache_single_flight_refreshing_and_stale_disclosure(self):
        fake = FakeOpenCode(active={SID_A}, pages={SID_A: [user(), step("msg_new", [tool("call_a")])]})
        clock = [NOW]
        started = []
        reader = self.reader(fake, clock=lambda: clock[0], spawn=lambda work: started.append(work))
        first = reader.activity()
        self.assertEqual(first["active"], [SID_A])
        self.assertEqual(len(fake.active_reads()), 1)
        # Inside the ~1 s cache: no second upstream read.
        reader.activity()
        self.assertEqual(len(fake.active_reads()), 1)
        # Past it: the last good answer is served at once while ONE refresh is in flight, and repeated calls join it.
        clock[0] += 2
        served = reader.activity()
        self.assertTrue(served["refreshing"])
        self.assertEqual(served["observedAt"], first["observedAt"])
        reader.activity()
        self.assertEqual(len(started), 1)
        self.assertEqual(len(fake.active_reads()), 1)
        started[0]()
        self.assertEqual(len(fake.active_reads()), 2)
        after = reader.activity()
        self.assertFalse(after.get("refreshing"))
        self.assertEqual(after["active"], [SID_A])
        # A failed refresh keeps the last good active map, names the failure and marks it stale.
        clock[0] += 2
        fake.fail_active = "OpenCode unreachable"
        before = fake.active_reads()
        reader.activity()
        started[-1]()
        later = reader.activity()
        self.assertEqual((later["status"], later["stale"], later["error"]), ("complete", True, "OpenCode unreachable"))
        self.assertEqual(later["active"], [SID_A])
        self.assertEqual(len(fake.active_reads()), len(before) + 1)

    def test_an_in_flight_first_read_is_shared_not_duplicated(self):
        entered, release = threading.Event(), threading.Event()
        fake = FakeOpenCode(active={SID_A}, pages={SID_A: [user(), step("msg_new", [tool("call_a")])]})

        def hook(path):
            if path == "/api/session/active":
                entered.set()
                release.wait(5)
        fake.hook = hook
        reader = self.reader(fake, spawn=lambda work: work())
        answers = []
        threads = [threading.Thread(target=lambda: answers.append(reader.activity())) for _ in range(2)]
        threads[0].start()
        self.assertTrue(entered.wait(2))
        threads[1].start()
        time.sleep(0.1)                                        # the second request joins the in-flight first read
        release.set()
        for thread in threads:
            thread.join(5)
        self.assertEqual(len(fake.active_reads()), 1)
        self.assertEqual([answer["status"] for answer in answers], ["complete", "complete"])
        self.assertEqual([answer["active"] for answer in answers], [[SID_A], [SID_A]])

    def test_first_read_is_bounded_and_malformed_detail_never_hides_the_busy_fact(self):
        fake = FakeOpenCode(active={SID_A}, pages={SID_A: "not a page"})
        got = self.reader(fake).activity()
        self.assertEqual(got["status"], "complete")
        self.assertEqual(got["active"], [SID_A])
        self.assertEqual(got["sessions"][0]["toolStatus"], "unavailable")

    def test_detail_reads_fan_out_across_active_sessions(self):
        entered = threading.Barrier(2, timeout=5)
        fake = FakeOpenCode(active={SID_A, SID_B},
                            pages={SID_A: [user(), step("msg_a", [tool("call_a")])],
                                   SID_B: [user(), step("msg_b", [tool("call_b")])]})

        def hook(path):
            if "/message?" in path:
                entered.wait()                                 # serial reads would never pass this together
        fake.hook = hook
        got = self.reader(fake).activity()
        self.assertEqual(got["status"], "complete")
        self.assertEqual([row["toolStatus"] for row in got["sessions"]], ["complete", "complete"])

    def test_a_blocked_detail_read_is_named_unavailable_at_the_deadline(self):
        import opencode_programs as module

        class Slow(FakeOpenCode):
            def __call__(self, path, timeout, *, raw=False):
                if "/message?" in path:
                    time.sleep(0.5)
                return super().__call__(path, timeout, raw=raw)
        fake = Slow(active={SID_A}, pages={SID_A: [user(), step("msg_a", [tool("call_a")])]})
        with patch.object(module, "ACTIVITY_ACTIVE_TIMEOUT", 0.2), \
                patch.object(module, "ACTIVITY_CALL_TIMEOUT", 0.05), \
                patch.object(module, "ACTIVITY_TOTAL", 0.3):
            started = time.monotonic()
            got = self.reader(fake).activity()
            self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(got["status"], "complete")
        self.assertEqual(got["active"], [SID_A])
        self.assertEqual(got["sessions"][0]["toolStatus"], "unavailable")
        self.assertIn("deadline", got["sessions"][0]["error"])


class ActivityRoute(unittest.TestCase):
    class Stub:
        def __init__(self):
            self.calls = 0
            self.reply = {"schemaVersion": 1, "backend": "opencode", "status": "complete",
                          "observedAt": NOW, "active": [SID_A], "sessions": []}

        def activity(self):
            self.calls += 1
            return self.reply

    def setUp(self):
        self.stub = self.Stub()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.opencode_programs = self.stub
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(5)

    def request(self, route, method="GET", headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        connection.request(method, route, headers=headers or {})
        response = connection.getresponse()
        reply = json.loads(response.read())
        connection.close()
        return response.status, reply, response.getheader("Cache-Control")

    def test_same_origin_get_no_query_and_no_cross_origin_or_mutating_route(self):
        origin = "http://127.0.0.1:" + str(self.httpd.server_address[1])
        status, reply, cache = self.request("/api/opencode/activity", headers={"Origin": origin})
        self.assertEqual((status, reply, cache), (200, self.stub.reply, "no-store"))
        self.assertEqual(self.stub.calls, 1)
        for route, method, headers, code in (
                ("/api/opencode/activity?x=1", "GET", {"Origin": origin}, 400),
                ("/api/opencode/activity", "POST", {"Origin": origin}, 405),
                ("/api/opencode/activity", "GET", {"Origin": "http://evil.example"}, 403),
                ("/api/opencode/activity", "OPTIONS", {"Origin": origin}, 403)):
            with self.subTest(route=route, method=method):
                self.assertEqual(self.request(route, method, headers)[0], code)
        self.assertEqual(self.stub.calls, 1)                    # refused before any OpenCode read

    def test_a_lone_surrogate_in_a_running_command_is_serialized_safely(self):
        self.stub.reply = {"schemaVersion": 1, "backend": "opencode", "status": "complete",
                           "observedAt": NOW, "active": [SID_A],
                           "sessions": [{"id": SID_A, "toolStatus": "complete",
                                         "runningCalls": [{"name": "shell", "status": "pending-or-missing",
                                                           "command": "echo \ud83d"}]}]}
        origin = "http://127.0.0.1:" + str(self.httpd.server_address[1])
        status, reply, _ = self.request("/api/opencode/activity", headers={"Origin": origin})
        self.assertEqual(status, 200)
        self.assertEqual(reply["sessions"][0]["runningCalls"][0]["command"], "echo \ud83d")


if __name__ == "__main__":
    unittest.main()
