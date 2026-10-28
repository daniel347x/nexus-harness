"""Expanded sidebar rows' glances: the Claude glance (claude_glance.ClaudeGlance: a transcript's tail only, bounded and
incremental, never the history reader's lock), the shared message rule (opencode_programs.glance_messages) and the
running-or-latest tool call of both backends (the Claude transcript's calls, opencode_conversation.glance_call), plus
the read-only GET /api/claude/glance route."""
import http.client
import json
import sys
import tempfile
import threading
import time
import unittest
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_glance
import server
from claude_glance import ClaudeGlance
from claude_reader import ClaudeReader
from opencode_programs import GLANCE_MESSAGES, GLANCE_TEXT, OpenCodePrograms, glance_messages
from test_pane_program_tree import NOW, SESSIONS, FakeOpenCode

SESSION = "00000000-0000-4000-8000-0000000000a1"


def uid(n):
    return str(uuid.UUID(int=n))


def stamp(seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(seconds)) + ".000Z"


def prompt(n, text, **extra):
    """A human prompt as the conversation view admits it (origin human)."""
    return {"sessionId": SESSION, "uuid": uid(n), "parentUuid": None, "type": "user", "version": "2.1.300",
            "isSidechain": False, "timestamp": stamp(NOW + n), "origin": {"kind": "human"},
            "message": {"role": "user", "content": text}, **extra}


def said(n, blocks, step=None, **extra):
    return {"sessionId": SESSION, "uuid": uid(n), "parentUuid": uid(n - 1), "type": "assistant", "version": "2.1.300",
            "isSidechain": False, "timestamp": stamp(NOW + n),
            "message": {"role": "assistant", "id": step or "msg_%d" % n, "model": "claude-opus-5-5", "content": blocks},
            **extra}


def text(n, words, step=None, **extra):
    return said(n, [{"type": "text", "text": words}], step, **extra)


def call(n, call_id, name="Bash", inp=None, step=None):
    return said(n, [{"type": "tool_use", "id": call_id, "name": name, "input": inp or {"command": "ls -la"}}], step)


def result(n, call_id, error=False, body="ok"):
    return {"sessionId": SESSION, "uuid": uid(n), "parentUuid": uid(n - 1), "type": "user", "version": "2.1.300",
            "isSidechain": False, "timestamp": stamp(NOW + n), "sourceToolAssistantUUID": uid(n - 1),
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call_id, "is_error": error,
                                                     "content": body}]}}


class ClaudeGlanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "-mnt-e-repos-quick-worker" / (SESSION + ".jsonl")
        self.path.parent.mkdir()
        self.reader = ClaudeReader([{"id": "native", "root": str(self.root), "native": True}])
        self.glance = ClaudeGlance(self.reader)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, rows, mode="w", end="\n"):
        with self.path.open(mode, encoding="utf-8") as out:
            out.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows[:-1]))
            if rows:
                out.write(json.dumps(rows[-1], ensure_ascii=False) + end)

    def test_messages_are_the_conversation_views_prose_under_the_opencode_glance_rule(self):
        rows = [prompt(1, "the job: build the glance"),
                said(2, [{"type": "thinking", "thinking": "private", "signature": "s"}]),
                text(3, "first answer")]
        rows += [text(4 + i, "answer %d" % i) for i in range(6)]
        rows += [text(10, "part one", step="msg_split"), text(11, "part two", step="msg_split"),
                 prompt(12, "<system-reminder>not Dan</system-reminder>"),
                 prompt(13, "meta text", isMeta=True),
                 text(14, "a subagent's words", agentId="a1b2"),
                 result(15, "toolu_x"),
                 prompt(16, "a later prompt from Dan"),
                 text(17, "y" * 2000)]
        self.write(rows)
        with self.path.open("a") as out:   # an API request copy repeats prose: never a message
            out.write(json.dumps({"type": "api-request-blob", "message": {"content": [{"type": "text", "text": "copy"}]}}) + "\n")
        got = self.glance.glance(SESSION)
        self.assertEqual(got["status"], "complete")
        texts = [m["text"] for m in got["messages"]]
        self.assertEqual(len(texts), GLANCE_MESSAGES)
        self.assertEqual(texts, ["answer 4", "answer 5", "part one\n\npart two", "a later prompt from Dan",
                                 "y" * GLANCE_TEXT + "…"])          # oldest first; one step's two records are one message
        self.assertEqual([m["role"] for m in got["messages"]][-2:], ["user", "assistant"])
        self.assertEqual(got["messages"][-1]["chars"], 2000)
        self.assertEqual((got["model"], got["lastAt"]), ("claude-opus-5-5", NOW + 17))
        self.assertIsNone(got["call"])
        self.assertFalse(got["partial"])
        # The same rule the OpenCode glance applies (opencode_programs.glance_messages).
        self.assertEqual(got["messages"], glance_messages(
            (m["role"], m["time"], m["text"], m["chars"]) for m in reversed(got["messages"])))

    def test_the_running_call_then_its_result_by_growth_and_the_newest_step_decides(self):
        self.write([prompt(1, "go"), text(2, "running ls"), call(3, "toolu_a", inp={"command": "ls -la /tmp"})])
        got = self.glance.glance(SESSION)
        c = got["call"]
        self.assertEqual((c["name"], c["status"], c["open"], c["time"], c["resultTime"]),
                         ("Bash", "pending-or-missing", True, NOW + 3, None))
        self.assertEqual(c["previewData"]["status"], "available")
        self.assertEqual(dict(c["previewData"]["value"]["entries"])["command"]["head"], "ls -la /tmp")
        self.write([result(4, "toolu_a")], mode="a")
        got = self.glance.glance(SESSION)
        self.assertEqual(got["read"]["mode"], "growth")
        c = got["call"]
        self.assertEqual((c["status"], c["open"], c["time"], c["resultTime"]), ("stored", False, NOW + 3, NOW + 4))
        # Two calls in one step: the one still without a result is shown, even when it came first.
        self.write([call(5, "toolu_b", name="Read", inp={"file_path": "/x"}, step="msg_par"),
                    call(6, "toolu_c", name="Grep", inp={"pattern": "y"}, step="msg_par"), result(7, "toolu_c")], mode="a")
        c = self.glance.glance(SESSION)["call"]
        self.assertEqual((c["name"], c["open"]), ("Read", True))
        self.write([result(8, "toolu_b", error=True)], mode="a")
        c = self.glance.glance(SESSION)["call"]
        self.assertEqual((c["name"], c["status"], c["open"], c["resultTime"]), ("Grep", "stored", False, NOW + 7))
        self.write([call(9, "toolu_d", name="Edit", inp={"file_path": "/z"}), result(10, "toolu_d", error=True)], mode="a")
        c = self.glance.glance(SESSION)["call"]
        self.assertEqual((c["name"], c["status"], c["open"]), ("Edit", "error", False))
        self.assertEqual(self.glance.glance(SESSION)["read"]["mode"], "unchanged")

    def test_only_the_tail_is_read_within_its_bounds(self):
        filler = "z" * 3000
        rows = [prompt(1, "early prompt"), text(2, "early answer")]
        for i in range(40):   # big tool results between the early messages and the end
            rows += [call(3 + 2 * i, "toolu_%d" % i), result(4 + 2 * i, "toolu_%d" % i, body=filler)]
        rows += [text(100, "the latest answer")]
        self.write(rows)
        size = self.path.stat().st_size
        with patch.object(claude_glance, "TAIL_STEP", 8192), patch.object(claude_glance, "TAIL_LIMIT", 32768):
            got = self.glance.glance(SESSION)
        self.assertLessEqual(got["read"]["bytes"], 32768)
        self.assertLess(got["read"]["window"], size)
        self.assertEqual([m["text"] for m in got["messages"]], ["the latest answer"])
        self.assertTrue(got["partial"])                      # fewer messages, and the window did not reach the start
        self.assertEqual(got["call"]["name"], "Bash")
        # Unbounded by the patch, the same file is read back to its start (it is small) and finds the early ones.
        fresh = ClaudeGlance(self.reader).glance(SESSION)
        self.assertEqual([m["text"] for m in fresh["messages"]], ["early prompt", "early answer", "the latest answer"])
        self.assertFalse(fresh["partial"])
        # A first read also stops reading further back after READ_SECONDS.
        with patch.object(claude_glance, "TAIL_STEP", 4096), patch.object(claude_glance, "READ_SECONDS", -1):
            quick = ClaudeGlance(self.reader).glance(SESSION)
        self.assertLessEqual(quick["read"]["bytes"], 4096)

    def test_an_incomplete_final_record_waits_and_a_rewrite_or_large_growth_reads_a_fresh_tail(self):
        self.write([prompt(1, "go"), text(2, "first"), text(3, "half written")], end="")
        got = self.glance.glance(SESSION)
        self.assertEqual([m["text"] for m in got["messages"]], ["go", "first"])
        with self.path.open("a") as out:
            out.write("\n")
        got = self.glance.glance(SESSION)
        self.assertEqual((got["read"]["mode"], got["messages"][-1]["text"]), ("growth", "half written"))
        raw = self.path.read_bytes()
        self.path.write_bytes(raw.replace(b"first", b"FIRST"))   # same size, changed bytes: the witness sees it
        got = self.glance.glance(SESSION)
        self.assertEqual((got["read"]["mode"], [m["text"] for m in got["messages"]][1]), ("tail", "FIRST"))
        with patch.object(claude_glance, "TAIL_LIMIT", 64):
            self.write([text(4, "grew a lot")], mode="a")
            got = self.glance.glance(SESSION)
        self.assertEqual(got["read"]["mode"], "tail")

    def test_it_never_waits_on_the_history_readers_lock_and_refuses_bad_input(self):
        self.write([prompt(1, "go"), text(2, "done")])
        held, release = threading.Event(), threading.Event()

        def hold():
            with self.reader.lock:   # e.g. the inventory's turn-fact pass
                held.set()
                release.wait(10)
        thread = threading.Thread(target=hold)
        thread.start()
        held.wait(5)
        try:
            started = time.monotonic()
            got = self.glance.glance(SESSION, "/mnt/e/repos/quick-worker")
            self.assertLess(time.monotonic() - started, 2)
            self.assertEqual(got["messages"][-1]["text"], "done")
        finally:
            release.set()
            thread.join()
        missing = self.glance.glance(uid(77))
        self.assertEqual((missing["status"], missing["uuid"]), ("unavailable", uid(77)))
        for bad in ("cc-" + SESSION, "../" + SESSION, None, SESSION.upper()):
            with self.assertRaises(ValueError):
                self.glance.glance(bad)


class OpenCodeGlanceCall(unittest.TestCase):
    def messages(self, status, settled=False):
        ms = lambda s: int(s * 1000)
        tool = {"id": "prt_tool1", "type": "tool", "name": "bash",
                "state": {"status": status, "input": {"command": "pytest -q", "description": "tests"},
                          **({"output": "ok"} if status == "completed" else {})},
                "time": {"created": ms(NOW - 30), **({"completed": ms(NOW - 5)} if status == "completed" else {})}}
        step = {"id": "msg_new", "type": "assistant", "time": {"created": ms(NOW - 31), **({"completed": ms(NOW - 4)} if settled else {})},
                "content": [{"type": "text", "text": "running the tests"}, tool]}
        older = {"id": "msg_old", "type": "assistant", "time": {"created": ms(NOW - 90), "completed": ms(NOW - 80)}, "finish": "tool-calls",
                 "content": [{"id": "prt_old", "type": "tool", "name": "read", "state": {"status": "completed", "input": {"filePath": "/a"}, "output": "x"},
                              "time": {"created": ms(NOW - 89), "completed": ms(NOW - 85)}}]}
        return [step, older]

    def test_the_glance_carries_the_running_or_latest_call_as_its_conversation_row_has_it(self):
        fake = FakeOpenCode(SESSIONS, busy={'ses_seat0000001'}, messages=self.messages("running"))
        got = OpenCodePrograms(request=fake).glance('ses_seat0000001')
        c = got["call"]
        self.assertEqual((c["name"], c["open"], c["time"], c["resultTime"]), ("bash", True, NOW - 30, None))
        self.assertEqual(dict(c["previewData"]["value"]["entries"])["command"]["head"], "pytest -q")
        self.assertEqual([m["text"] for m in got["messages"]], ["running the tests"])
        fake.messages = self.messages("completed", settled=True)
        c = OpenCodePrograms(request=fake).glance('ses_seat0000001')["call"]
        self.assertEqual((c["status"], c["open"], c["time"], c["resultTime"]), ("stored", False, NOW - 30, NOW - 5))
        fake.messages = [m for m in self.messages("running") if m["id"] == "msg_old"]
        self.assertEqual(OpenCodePrograms(request=fake).glance('ses_seat0000001')["call"]["name"], "read")
        fake.messages = [{"id": "msg_u", "type": "user", "text": "hi", "time": {"created": int(NOW * 1000)}}]
        self.assertIsNone(OpenCodePrograms(request=fake).glance('ses_seat0000001')["call"])

    def test_the_glance_lists_every_open_sibling_and_keeps_the_legacy_call(self):
        ms = lambda s: int(s * 1000)
        first = {"id": "prt_parallel_a", "type": "tool", "name": "shell",
                 "state": {"status": "running", "input": {"command": "long parallel shell"}},
                 "time": {"created": ms(NOW - 30)}}
        second = {"id": "prt_parallel_b", "type": "tool", "name": "read",
                  "state": {"status": "running", "input": {"filePath": "/x"}},
                  "time": {"created": ms(NOW - 29)}}
        step = {"id": "msg_parallel", "type": "assistant", "time": {"created": ms(NOW - 31)},
                "content": [{"type": "text", "text": "running the tests"}, first, second]}
        fake = FakeOpenCode(SESSIONS, busy={'ses_seat0000001'}, messages=[step])
        got = OpenCodePrograms(request=fake).glance('ses_seat0000001')
        self.assertEqual((got["busy"], got["toolStatus"]), (True, "complete"))
        self.assertEqual([call["id"] for call in got["runningCalls"]], ["prt_parallel_a", "prt_parallel_b"])
        self.assertEqual(got["runningCalls"][0]["command"], "long parallel shell")
        self.assertEqual(got["call"]["name"], "read")            # legacy: the last open sibling
        # The last sibling finishing first leaves the earlier long call in runningCalls (and in the legacy call).
        second["state"] = {"status": "completed", "input": {}, "output": "done"}
        got = OpenCodePrograms(request=fake).glance('ses_seat0000001')
        self.assertEqual([call["id"] for call in got["runningCalls"]], ["prt_parallel_a"])
        self.assertEqual(got["call"]["name"], "shell")
        # Busy is required for a running claim; the legacy call remains for compatibility.
        fake.busy = set()
        got = OpenCodePrograms(request=fake).glance('ses_seat0000001')
        self.assertEqual((got["busy"], got["runningCalls"]), (False, []))
        self.assertEqual(got["call"]["name"], "shell")
        # A malformed busy map makes the whole glance unavailable, never idle.
        fake.active_answer = {"data": []}
        self.assertEqual(OpenCodePrograms(request=fake).glance('ses_seat0000001')["status"], "unavailable")


class GlanceRoute(unittest.TestCase):
    def setUp(self):
        self.calls = []
        test = self

        class Glance:
            def glance(self, uuid, cwd=None):
                test.calls.append((uuid, cwd))
                if uuid != SESSION:
                    raise ValueError("an exact session UUID is required")
                return {"schemaVersion": 1, "backend": "claude", "status": "complete", "uuid": uuid, "messages": [], "call": None}
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.httpd.claude_glance = Glance()
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close(); self.thread.join()

    def request(self, route, method='GET', headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_address[1], timeout=15)
        connection.request(method, route, headers=headers or {})
        response = connection.getresponse()
        reply = json.loads(response.read())
        connection.close()
        return response.status, reply

    def test_same_origin_get_with_exactly_one_uuid_and_an_optional_folder_hint(self):
        status, reply = self.request('/api/claude/glance?uuid=' + SESSION + '&cwd=%2Fmnt%2Fe%2Frepos')
        self.assertEqual((status, reply['uuid'], reply['backend']), (200, SESSION, 'claude'))
        self.assertEqual(self.calls[-1], (SESSION, '/mnt/e/repos'))
        for route, method, headers, code in (
                ('/api/claude/glance?uuid=' + SESSION, 'GET', {'Origin': 'http://evil.example'}, 403),
                ('/api/claude/glance?uuid=' + SESSION, 'POST', {}, 405),
                ('/api/claude/glance', 'GET', {}, 400),
                ('/api/claude/glance?uuid=../credential', 'GET', {}, 400),
                ('/api/claude/glance?uuid=' + SESSION + '&uuid=' + SESSION, 'GET', {}, 400),
                ('/api/claude/glance?uuid=' + SESSION + '&path=/etc', 'GET', {}, 400)):
            status, _ = self.request(route, method, headers)
            self.assertEqual(status, code, (route, method, headers))


if __name__ == '__main__':
    unittest.main()
