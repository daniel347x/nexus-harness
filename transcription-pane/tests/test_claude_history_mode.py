"""Whole-history Refine mode: uncropped eligible prose and JS-parity block boundaries."""
import http.client
import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from claude_reader import ClaudeReader, ReaderError
from claude_live import ClaudeFacade
from test_claude_live import InertHost
from test_claude_reader import SESSION, record, uid

SEPARATOR = "\n\n---\n\n"


def utf16_len(text):
    """JavaScript String.length: UTF-16 code units, not Python code points."""
    return len(text.encode("utf-16-le")) // 2


def js_slice(text, start, end):
    """JavaScript text.slice(start, end) over the same UTF-16 code units."""
    return text.encode("utf-16-le")[2 * start:2 * end].decode("utf-16-le")


def bulk(prefix, repeats=1200):
    # 68,000 bytes per message; six of them exceed the retired 400 KiB context ceiling.
    return prefix + " " + ("x" * 64 + "🌈") * repeats


def native_rows(count=6):
    rows, parent = [], None
    for number in range(1, count + 1):
        kind = "user" if number % 2 else "assistant"
        body = bulk("PART-" + str(number))
        rows.append(record(number, parent, kind, body if kind == "user" else [{"type": "text", "text": body}]))
        parent = number
    return rows


class NativeHistoryModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "project" / (SESSION + ".jsonl")
        self.path.parent.mkdir()
        self.reader = ClaudeReader([{"id": "test", "root": str(self.root), "native": False}])

    def tearDown(self):
        self.temp.cleanup()

    def write(self, rows):
        self.path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                             encoding="utf-8")

    def page(self):
        for _ in range(30):
            sessions = self.reader.sessions()["sessions"]
            if sessions and sessions[0]["status"] in ("complete", "incomplete-tail"):
                main = next(s for s in sessions[0]["scopes"] if s["label"] == "main")
                page = self.reader.events(sessionRef=sessions[0]["sessionRef"], scopeRef=main["scopeRef"])
                self.assertIsNotNone(page["targetRef"])
                return page
        self.fail("index did not finish")

    def test_whole_history_uncropped_beyond_400kib_with_utf16_boundaries(self):
        rows = native_rows()
        self.write(rows)
        page = self.page()
        whole = self.reader.context(page["targetRef"], 1, historyMode="whole")
        self.assertEqual(whole["historyMode"], "whole")
        self.assertGreater(whole["bytes"], 400 * 1024)
        self.assertEqual((whole["turns"], whole["users"], len(whole["historyBoundaries"])), (6, 3, 6))
        expected = []
        for number in range(1, 7):
            label = "[USER]" if number % 2 else "[ASSISTANT]"
            expected.append(label + "\n" + bulk("PART-" + str(number)))
        self.assertEqual(whole["text"], SEPARATOR.join(expected))
        self.assertEqual([b["id"] for b in whole["historyBoundaries"]],
                         [uid(number) for number in range(1, 7)])
        self.assertEqual([b["end"] for b in whole["historyBoundaries"]],
                         [utf16_len(SEPARATOR.join(expected[:number])) for number in range(1, 7)])
        # The lead's core consumes text.slice(previousEnd, end): exactly one block each time.
        previous = 0
        for index, boundary in enumerate(whole["historyBoundaries"]):
            self.assertEqual(js_slice(whole["text"], previous, boundary["end"]),
                             (SEPARATOR if index else "") + expected[index])
            previous = boundary["end"]
        self.assertEqual(previous, utf16_len(whole["text"]))  # JS text.length parity
        self.assertNotEqual(previous, len(whole["text"]))  # astral chars prove UTF-16, not code points

    def test_budgeted_default_and_explicit_keep_current_crop_and_boundary(self):
        self.write(native_rows())
        page = self.page()
        default = self.reader.context(page["targetRef"], 1)
        explicit = self.reader.context(page["targetRef"], 1, historyMode="budgeted")
        fields = ("text", "bytes", "turns", "users", "historyBoundaries", "historyMode", "exclusions")
        self.assertEqual({key: default[key] for key in fields}, {key: explicit[key] for key in fields})
        self.assertEqual((default["historyMode"], default["turns"]), ("budgeted", 1))
        self.assertLessEqual(default["bytes"], 50 * 1024)
        self.assertEqual(default["historyBoundaries"], [{"id": uid(6), "end": utf16_len(default["text"])}])
        self.assertTrue(default["text"].startswith("[ASSISTANT]\n…"))
        # A larger budget still crops the oldest included block; its id names its record.
        cropped = self.reader.context(page["targetRef"], 1, 200 * 1024)
        self.assertLessEqual(cropped["bytes"], 200 * 1024)
        self.assertEqual(cropped["turns"], 3)
        self.assertEqual(cropped["historyBoundaries"][0]["id"], uid(4))
        self.assertTrue(js_slice(cropped["text"], 0, cropped["historyBoundaries"][0]["end"]).startswith("[ASSISTANT]\n…"))
        self.assertEqual(cropped["historyBoundaries"][-1]["end"], utf16_len(cropped["text"]))

    def test_whole_and_budgeted_extract_the_same_eligible_rows_under_budget(self):
        self.write([record(1, content="alpha 🌈"),
                    record(2, 1, kind="assistant", content=[{"type": "text", "text": "beta\n\ngamma"}]),
                    record(3, 2, content=[{"type": "tool_result", "tool_use_id": "t", "content": "TOOL"}]),
                    record(4, 3, kind="assistant", content=[{"type": "thinking", "thinking": "SECRET"},
                                                            {"type": "text", "text": "delta"}])])
        page = self.page()
        whole = self.reader.context(page["targetRef"], 1, historyMode="whole")
        budgeted = self.reader.context(page["targetRef"], 1)
        self.assertEqual(whole["text"], SEPARATOR.join(["[USER]\nalpha 🌈",
                                                       "[ASSISTANT]\nbeta\n\ngamma", "[ASSISTANT]\ndelta"]))
        self.assertEqual(whole["text"], budgeted["text"])
        self.assertEqual(whole["historyBoundaries"], budgeted["historyBoundaries"])
        self.assertEqual([b["id"] for b in whole["historyBoundaries"]], [uid(1), uid(2), uid(4)])
        self.assertNotIn("TOOL", whole["text"])
        self.assertNotIn("SECRET", whole["text"])
        self.assertEqual(whole["exclusions"], budgeted["exclusions"])
        self.assertIn(uid(3), str(whole["exclusions"]))

    def test_empty_history_has_no_boundaries(self):
        self.write([record(1, version="future")])  # one excluded record: no eligible prose
        page = self.page()
        for mode in ("budgeted", "whole"):
            with self.subTest(mode=mode):
                context = self.reader.context(page["targetRef"], 1, historyMode=mode)
                self.assertEqual((context["text"], context["turns"], context["historyBoundaries"]), ("", 0, []))
                self.assertEqual(context["historyMode"], mode)

    def test_whole_mode_does_not_consult_the_budget_value(self):
        self.write([record(1, content="whole 🌈")])
        page = self.page()
        expected = self.reader.context(page["targetRef"], 1, historyMode="whole")
        for budget in (None, 0, 1, 401 * 1024):
            with self.subTest(budget=budget):
                result = self.reader.context(page["targetRef"], 1, budget, historyMode="whole")
                self.assertEqual(result["text"], expected["text"])
                self.assertEqual(result["historyBoundaries"], expected["historyBoundaries"])

    def test_invalid_history_mode_is_a_schema_error(self):
        self.write([record(1)])
        page = self.page()
        for value in ("all", "", None, 1, ("whole",)):
            with self.subTest(value=value):
                with self.assertRaises(ReaderError) as error:
                    self.reader.context(page["targetRef"], 1, historyMode=value)
                self.assertEqual((error.exception.code, error.exception.status), (400, "schema"))


class HostHistoryModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.host = InertHost()
        self.reader = ClaudeFacade(ClaudeReader([{"id": "fixture", "root": self.temp.name}]), self.host)
        self.number = 0

    def tearDown(self):
        self.temp.cleanup()

    def add_message(self, text):
        self.number += 1
        if self.number % 2:
            self.host.user(text, uuid="u" + str(self.number))
        else:
            self.host.frame("assistant", [{"type": "text", "text": text}],
                            "a" + str(self.number), message_id="p" + str(self.number))

    def page(self):
        sessions = self.reader.sessions()["sessions"]
        row = sessions[0]
        main = next(scope for scope in row["scopes"] if scope["label"] == "main")
        page = self.reader.events(sessionRef=row["sessionRef"], scopeRef=main["scopeRef"])
        self.assertIsNotNone(page["targetRef"])
        return page

    def test_host_whole_history_beyond_400kib_and_budgeted_parity(self):
        for number in range(1, 7):
            self.add_message(bulk("HOST-" + str(number)))
        page = self.page()
        whole = self.reader.context(page["targetRef"], 1, historyMode="whole")
        self.assertEqual(whole["historyMode"], "whole")
        self.assertGreater(whole["bytes"], 400 * 1024)
        self.assertEqual((whole["turns"], whole["users"], len(whole["historyBoundaries"])), (6, 3, 6))
        expected = []
        for number in range(1, 7):
            label = "[USER]" if number % 2 else "[ASSISTANT]"
            expected.append(label + "\n" + bulk("HOST-" + str(number)))
        self.assertEqual(whole["text"], SEPARATOR.join(expected))
        self.assertEqual([b["id"] for b in whole["historyBoundaries"]],
                         ["u1", "a2", "u3", "a4", "u5", "a6"])
        self.assertEqual(whole["historyBoundaries"][-1]["end"], utf16_len(whole["text"]))
        previous = 0
        for index, boundary in enumerate(whole["historyBoundaries"]):
            self.assertEqual(js_slice(whole["text"], previous, boundary["end"]),
                             (SEPARATOR if index else "") + expected[index])
            previous = boundary["end"]
        default = self.reader.context(page["targetRef"], 1)
        explicit = self.reader.context(page["targetRef"], 1, historyMode="budgeted")
        fields = ("text", "bytes", "turns", "users", "historyBoundaries", "historyMode", "exclusions")
        self.assertEqual({key: default[key] for key in fields}, {key: explicit[key] for key in fields})
        self.assertEqual(default["historyMode"], "budgeted")
        self.assertLess(default["bytes"], whole["bytes"])
        self.assertTrue(default["text"].startswith("[ASSISTANT]\n…"))
        self.assertEqual(default["historyBoundaries"][0]["id"], "a6")

    def test_host_whole_mode_does_not_consult_the_budget_value(self):
        self.add_message("whole host 🌈")
        page = self.page()
        expected = self.reader.context(page["targetRef"], 1, historyMode="whole")
        for budget in (None, 0, 1, 401 * 1024):
            with self.subTest(budget=budget):
                result = self.reader.context(page["targetRef"], 1, budget, historyMode="whole")
                self.assertEqual(result["text"], expected["text"])
                self.assertEqual(result["historyBoundaries"], expected["historyBoundaries"])


class HistoryModeApiTests(unittest.TestCase):
    """The pane route forwards historyMode to both readers without a schema bump."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        native = Path(self.temp.name) / (SESSION + ".jsonl")
        native.write_text("".join(json.dumps(row) + "\n" for row in (
            record(1, content="A" * 30000),
            record(2, 1, kind="assistant", content=[{"type": "text", "text": "B" * 30000}]))))
        self.host = InertHost()
        self.host.user("H" * 30000, uuid="hu1")
        self.host.frame("assistant", [{"type": "text", "text": "I" * 30000}], "ha2", message_id="hp")
        self.reader = ClaudeFacade(ClaudeReader([{"id": "fixture", "root": self.temp.name}]), self.host)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.claude_reader = self.reader
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, query):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        connection.request("GET", "/api/claude/context?" + urlencode(query))
        response = connection.getresponse()
        body = json.loads(response.read())
        connection.close()
        return response.status, body

    def native_page(self):
        reader = self.reader.history
        for _ in range(30):
            sessions = reader.sessions()["sessions"]
            if sessions and sessions[0]["status"] in ("complete", "incomplete-tail"):
                main = next(s for s in sessions[0]["scopes"] if s["label"] == "main")
                page = reader.events(sessionRef=sessions[0]["sessionRef"], scopeRef=main["scopeRef"])
                self.assertIsNotNone(page["targetRef"])
                return page
        self.fail("native index did not finish")

    def host_page(self):
        sessions = self.reader.sessions()["sessions"]
        row = sessions[0]
        main = next(scope for scope in row["scopes"] if scope["label"] == "main")
        return self.reader.events(sessionRef=row["sessionRef"], scopeRef=main["scopeRef"])

    def test_api_forwards_whole_and_budgeted_for_native_and_host_targets(self):
        native = self.native_page()
        query = {"targetRef": native["targetRef"], "targetRevision": 1, "budgetBytes": 5 * 1024}
        status, default = self.request(query)
        self.assertEqual(status, 200, default)
        self.assertEqual((default["historyMode"], default["turns"]), ("budgeted", 1))
        self.assertLessEqual(default["bytes"], 5 * 1024)
        code, whole = self.request({**query, "historyMode": "whole"})
        self.assertEqual(code, 200, whole)
        self.assertEqual(whole["historyMode"], "whole")
        self.assertGreater(whole["bytes"], 50 * 1024)  # the explicit whole request ignores the 5 KiB budget
        self.assertEqual(whole["turns"], 2)
        self.assertEqual(len(whole["historyBoundaries"]), 2)
        self.assertEqual(whole["historyBoundaries"][-1]["end"], utf16_len(whole["text"]))
        self.assertEqual(self.request({**query, "historyMode": "all"})[0], 400)
        host = self.host_page()
        status, host_whole = self.request({"targetRef": host["targetRef"], "targetRevision": 1,
                                           "historyMode": "whole"})
        self.assertEqual(status, 200, host_whole)
        self.assertEqual(host_whole["historyMode"], "whole")
        self.assertEqual([b["id"] for b in host_whole["historyBoundaries"]], ["hu1", "ha2"])
        self.assertGreater(host_whole["bytes"], 50 * 1024)


if __name__ == "__main__":
    unittest.main()
