import json
import os
import sys
import tempfile
import types
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import claude_reader as cr

SESSION = "00000000-0000-4000-8000-000000000001"


def uid(n):
    return str(uuid.UUID(int=n))


def record(n, parent=None, kind="user", content="hello", **extra):
    return {"sessionId": SESSION, "uuid": uid(n), "parentUuid": uid(parent) if parent else None,
            "type": kind, "version": "synthetic-v1", "isSidechain": False, "cwd": "/fixture",
            "origin": {"kind": "human"}, "message": {"role": kind, "content": content}, **extra}


def saturation_records(count=400):
    """Audit2's unscaled ~5.7MiB / 400-call descriptor-heavy history."""
    return [record(n, n-1 if n > 1 else None, "assistant", [{"type": "tool_use", "id": "call-"+str(n),
        "name": "mcp__workflowy__anchor", "input": {"verb": "point.capture",
        "params": {"field"+str(k): "x"*129 for k in range(100)}}}]) for n in range(1, count+1)]


class ReaderTests(unittest.TestCase):
    def test_snapshot_recorded_diff_is_separate_from_exact_raw_and_optional_failures(self):
        from test_tool_diff import claude_fixture
        from tool_diff import claude_diff
        f = claude_fixture()
        for mode in ("edit", "missing", "malformed", "pending", "error"):
            with self.subTest(mode=mode):
                rows = self.pair(f["input"], "raw output 雪\n", **({"is_error": True} if mode == "error" else {}))
                rows[0]["message"]["content"][0]["name"] = "Edit"
                meta = f["toolUseResult"] if mode != "malformed" else {"structuredPatch":"bad"}
                if mode != "missing": rows[1]["toolUseResult"] = meta
                if mode == "pending": rows = rows[:1]
                self.write(rows)
                self.reader = cr.ClaudeReader([{"id":"test", "root":str(self.root)}])
                snap = self.snapshot()
                self.assertEqual(snap["diff"], claude_diff("Edit",f["input"],meta if mode != "missing" else None,snap["status"]))
                self.assertEqual(snap["input"],json.dumps(f["input"],ensure_ascii=False))
                self.assertEqual(snap["inputBytes"],len(snap["input"].encode()))
                self.assertEqual(snap["output"],None if mode == "pending" else "raw output 雪\n")
                self.assertEqual(snap["outputBytes"],None if mode == "pending" else len("raw output 雪\n".encode()))
                if mode == "edit":
                    self.assertEqual(snap["diff"]["files"][0]["hunks"][0]["oldStart"],124)
                    self.assertTrue(snap["diff"]["complete"])
                else:
                    self.assertFalse(snap["diff"]["complete"])
                self.assertTrue(all("diff" not in c for e in self.events()["events"] for c in e["calls"]))

    def test_summary_cap_is_256mib_and_other_source_body_page_record_bounds_unchanged(self):
        self.assertEqual(cr.INDEX_SUMMARY_BYTES,256*1024*1024)
        self.assertEqual((cr.SOURCE_LIMIT,cr.BODY_LIMIT,cr.PAGE_LIMIT,cr.PAGE_EVENTS,cr.INDEX_RECORDS),
                         (16*1024*1024,8*1024*1024,8*1024*1024,100,100000))

    def test_saturation_during_append_retains_frozen_pages_and_refuses_stale_witnesses(self):
        self.reader = cr.ClaudeReader([{"id":"test", "root":str(self.root)}], summary_limit=16*1024*1024)
        rows = saturation_records()
        self.write(rows[:200])
        for _ in range(20):
            first = self.events()
            if first["status"] == "complete": break
        self.assertEqual(len(first["events"]), 100)
        held = first["events"][0]["calls"][0]
        self.assertEqual(self.reader.snapshot(held["callRef"], first["fileEpoch"])["inputBytes"], held["inputBytes"])
        with self.path.open("a") as stream:
            stream.write("".join(json.dumps(r)+"\n" for r in rows[200:]))
        for _ in range(30):
            current = self.reader.events(sessionRef=first["sessionRef"], scopeRef=first["scopeRef"])
            if current["status"] == "index-limit": break
        self.assertEqual(current["status"], "index-limit")
        self.assertGreater(current["indexLimit"]["indexedRecords"], 200)
        self.assertEqual(current["fileEpoch"], first["fileEpoch"])
        frozen = self.reader.events(sessionRef=first["sessionRef"], scopeRef=first["scopeRef"], cursor=first["nextCursor"])
        self.assertEqual([e["uuid"] for e in frozen["events"]], [uid(n) for n in range(101,201)])
        self.assertFalse(frozen["hasMore"])
        # Selection/full-context refuses incomplete indexes just as before.
        with self.assertRaises(cr.ReaderError): self.reader.snapshot(held["callRef"], first["fileEpoch"])
        with self.assertRaises(cr.ReaderError): self.reader.validate_target(first["targetRef"])
        for shape in ("rewrite", "truncate", "replace", "evict"):
            with self.subTest(shape=shape):
                if shape == "rewrite":
                    raw=self.path.read_bytes(); self.path.write_bytes(raw.replace(b'point.capture', b'point.extract', 1))
                elif shape == "truncate": self.path.write_bytes(self.path.read_bytes()[:100])
                elif shape == "replace":
                    replacement=self.path.with_suffix(".new");replacement.write_bytes(self.path.read_bytes());replacement.replace(self.path)
                else: self.reader.files.clear()
                # A same-size rewrite is detected by re-verifying the indexed prefix in
                # bounded steps; meanwhile the cursor gets "nothing new yet" (no events,
                # never stale content), then the refusal.
                with self.assertRaises(cr.ReaderError):
                    for _ in range(40):
                        waiting = self.reader.events(sessionRef=first["sessionRef"], scopeRef=first["scopeRef"], fileEpoch=first["fileEpoch"], cursor=first["nextCursor"])
                        self.assertEqual((waiting["events"], waiting["status"]), ([], "validating"))

    def test_preview_data_preserves_transport_edges_original_lengths_and_raw(self):
        values = []
        for tail in ("TAIL-A😀", "TAIL-B😀"):
            inp = {"params": {"content": "a" * 200000 + tail, "target": "[ap:ABC123]"}, "verb": "point.capture"}
            self.write(self.pair(inp, "PRIVATE-OUTPUT-NOT-IN-ROWS"))
            self.reader = cr.ClaudeReader([{"id": "test", "root": str(self.root)}])
            page = self.events()
            call = page["events"][0]["calls"][0]
            data = call["previewData"]
            self.assertEqual(data["status"], "available")
            self.assertNotIn("preview", call)  # no reachable arbitrary first512 fallback
            self.assertNotIn("PRIVATE-OUTPUT-NOT-IN-ROWS", json.dumps(page))
            self.assertLess(len(json.dumps(data).encode()), 4096)
            params = dict(dict(data["value"]["entries"])["params"]["entries"])
            self.assertEqual(params["content"]["head"], "a" * 128)
            self.assertTrue(params["content"]["tail"].endswith(tail))
            self.assertEqual(params["content"]["jsonCharacters"], len(inp["params"]["content"]) + 2)
            snap = self.reader.snapshot(call["callRef"], page["fileEpoch"])
            self.assertEqual(json.loads(snap["input"]), inp)
            self.assertEqual(call["inputBytes"], snap["inputBytes"])
            self.assertEqual(call["outputBytes"], len("PRIVATE-OUTPUT-NOT-IN-ROWS"))
            values.append(data)
        self.assertNotEqual(values[0], values[1])

    def test_preview_data_limits_duplicates_and_opaque_numbers_do_not_change_raw(self):
        for inp, reason in (([None] * (cr.PREVIEW_DATA_NODES + 1), "structure limit"),
                            ({str(i): "😀" * 200 for i in range(500)}, "byte limit"),
                            (2**80, "numeric precision")):
            self.write(self.pair(inp))
            self.reader = cr.ClaudeReader([{"id": "test", "root": str(self.root)}])
            page = self.events(); call = page["events"][0]["calls"][0]
            data = call["previewData"]
            self.assertEqual(data["status"], "unavailable")
            self.assertIn(reason, data["reason"])
            self.assertLess(len(json.dumps(data).encode()), cr.PREVIEW_DATA_LIMIT)
            snap = self.reader.snapshot(call["callRef"], page["fileEpoch"])
            self.assertEqual(json.loads(snap["input"]), inp)
        wire = json.dumps(self.pair({"a": 1})[0]).replace('{"a": 1}', '{"a":1,"a":2}')
        self.path.write_text(wire + "\n")
        self.reader = cr.ClaudeReader([{"id": "test", "root": str(self.root)}])
        page = self.events(); call = page["events"][0]["calls"][0]
        self.assertEqual(call["previewData"]["status"], "unavailable")
        self.assertEqual(self.reader.snapshot(call["callRef"], page["fileEpoch"])["input"], '{"a":1,"a":2}')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "project" / (SESSION + ".jsonl")
        self.path.parent.mkdir()
        self.reader = cr.ClaudeReader([{"id": "test", "root": str(self.root), "native": False}])

    def tearDown(self):
        self.temp.cleanup()

    def write(self, rows):
        self.path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))

    def address(self):
        for _ in range(20):
            rows = self.reader.sessions()["sessions"]
            if rows and rows[0]["status"] in ("complete", "incomplete-tail"):
                main = next(s for s in rows[0]["scopes"] if s["label"] == "main")
                return {"sessionRef": rows[0]["sessionRef"], "scopeRef": main["scopeRef"]}
        self.fail("index did not finish")

    def events(self):
        return self.reader.events(**self.address())

    def snapshot(self, rows=None):
        if rows:
            self.write(rows)
        page = self.events()
        calls = [c for row in page["events"] for c in row["calls"]]
        return self.reader.snapshot(calls[0]["callRef"], page["fileEpoch"])

    def pair(self, inp=False, out="", **extra):
        return [record(1, kind="assistant", content=[{"type": "tool_use", "id": "tool", "name": "same", "input": inp}]),
                record(2, 1, content=[{"type": "tool_result", "tool_use_id": "tool", "content": out, **extra}], sourceToolAssistantUUID=uid(1))]

    def test_lexical_payload_duplicates_and_numbers(self):
        wire = json.dumps(self.pair()[0])
        wire = wire.replace('"input": false', '"input": { "x":90071992547409931234567890,"x":-0,"e":1.230e+500,"s":"\\u0041" }')
        self.path.write_text(wire + "\n" + json.dumps(self.pair()[1]) + "\n")
        snap = self.snapshot()
        expected = '{ "x":90071992547409931234567890,"x":-0,"e":1.230e+500,"s":"\\u0041" }'
        self.assertEqual(snap["input"], expected)
        self.assertEqual(snap["inputBytes"], len(expected.encode()))

    def test_structural_duplicates_and_malformed_visible(self):
        self.path.write_text('{"type":"user","type":"assistant"}\nnot-json\n' + json.dumps(record(1)) + "\n")
        page = self.events()
        self.assertEqual(len(page["events"]), 1)
        self.assertEqual(len(page["diagnostics"]), 2)
        self.assertIn("duplicate structural", page["diagnostics"][0]["status"])

    def test_claude_code_paste_wrapper_and_new_bookkeeping_records(self):
        self.assertEqual(cr.unwrap_paste('\n\n<pasted_content id="f0ea">\nline one\n\nTail\n</pasted_content id="f0ea">\n'),
                         "line one\n\nTail")
        for unchanged in ("plain prompt", 'see <pasted_content id="a">\nx\n</pasted_content id="a">',
                          '<pasted_content id="a">\nx\n</pasted_content id="b">'):
            self.assertEqual(cr.unwrap_paste(unchanged), unchanged)
        row = record(1, content='\n\n<pasted_content id="f0ea">\nhello\nworld\n</pasted_content id="f0ea">\n')
        bookkeeping = [{"type": kind, "sessionId": SESSION} for kind in ("agent-name", "atis-latch", "cost-state")]
        self.path.write_text("".join(json.dumps(r) + "\n" for r in bookkeeping + [row]))
        page = self.reader.events(**self.address())
        self.assertEqual(page["diagnostics"], [])
        self.assertEqual([e.get("text") for e in page["events"] if e.get("kind") == "user"], ["hello\nworld"])

    def test_bookkeeping_only_file_is_an_empty_history_then_reselect_sees_the_prompt(self):
        path = self.path
        path.unlink(missing_ok=True)
        self.assertEqual(self.reader.sessions()["sessions"], [])  # a missing transcript is never invented
        bookkeeping = "".join(json.dumps({"type": k, "sessionId": SESSION}) + "\n"
                              for k in ("custom-title", "agent-name", "mode", "permission-mode", "atis-latch"))
        path.write_text(bookkeeping)
        address = self.address()
        empty = self.reader.events(**address)
        self.assertEqual(empty["events"], [])
        self.assertIsNone(empty["targetRef"])  # bookkeeping alone is an empty history, not a conversation
        with path.open("a") as stream:
            stream.write(json.dumps(record(1, content="hello")) + "\n")
        page = self.reader.events(**address)  # explicit reselect: a fresh snapshot, no cursor
        self.assertEqual([e["text"] for e in page["events"] if e.get("kind") == "user"], ["hello"])
        self.assertEqual([e["uuid"] for e in page["events"]], [uid(1)])

    def test_all_diagnostic_boundaries_page_even_without_main_leaf(self):
        # Unknown kinds that carry conversation identity stay diagnostics (bookkeeping is ignored).
        self.path.write_text("not-json\n" * 205 + '{"type":"future-metadata","message":{}}\n' * 5)
        address = self.address()
        cursor, seen, pages = None, [], 0
        while True:
            page = self.reader.events(**address, cursor=cursor)
            seen.extend(d["offset"] for d in page["diagnostics"])
            self.assertIsNone(page["targetRef"])
            self.assertEqual(page["events"], [])
            pages += 1
            self.assertLessEqual(pages, 3)
            if not page["hasMore"]:
                break
            cursor = page["nextCursor"]
        self.assertEqual(pages, 3)
        self.assertEqual(len(seen), 210)
        self.assertEqual(len(set(seen)), 210)

    def test_diagnostic_snapshot_freezes_append_and_delta_includes_new_boundary(self):
        self.path.write_text("not-json\n" * 205 + json.dumps(record(1)) + "\n")
        page = self.events()
        revision = page["snapshotRevision"]
        target = page["targetRef"]
        with self.path.open("a") as f:
            f.write("new-corrupt-boundary\n" + json.dumps(record(2, 1, content="appended")) + "\n")
        found = list(page["diagnostics"])
        while page["hasMore"]:
            page = self.reader.events(targetRef=target, cursor=page["nextCursor"])
            self.assertEqual(page["snapshotRevision"], revision)
            found.extend(page["diagnostics"])
        self.assertEqual(len(found), 205)
        delta = self.reader.events(targetRef=target, cursor=page["deltaCursor"])
        self.assertEqual(delta["change"], "append")
        self.assertEqual([r["text"] for r in delta["events"]], ["appended"])
        self.assertEqual(len(delta["diagnostics"]), 1)

    def test_unknown_version_excluded_context(self):
        self.write([record(1, version="future")])
        page = self.events()
        context = self.reader.context(page["targetRef"], 0)
        self.assertEqual(context["text"], "")
        self.assertIn("unsupported-version", str(context["exclusions"]))

    def test_provider_id_not_dedupe_replay_is(self):
        a = record(1, kind="assistant", content=[{"type": "text", "text": "A"}])
        b = record(2, 1, kind="assistant", content=[{"type": "text", "text": "B"}])
        a["message"]["id"] = b["message"]["id"] = "same-provider"
        self.write([a, a, b])
        page = self.events()
        self.assertEqual([r["text"] for r in page["events"]], ["A", "B"])
        self.assertEqual(page["events"][0]["replays"], 1)

    def test_conflicting_uuid_ambiguous(self):
        self.write([record(1), record(1, content="changed")])
        with self.assertRaises(cr.ReaderError) as error:
            self.events()
        self.assertEqual(error.exception.status, "ambiguous")

    def test_parallel_out_of_order_unique_id_join(self):
        self.write([record(1, kind="assistant", content=[{"type": "tool_use", "id": x, "name": "same", "input": x} for x in ("a", "b")]),
                    record(2, 1, content=[{"type": "tool_result", "tool_use_id": "b", "content": "B"}]),
                    record(3, 2, content=[{"type": "tool_result", "tool_use_id": "a", "content": "A"}])])
        page = self.events()
        calls = page["events"][0]["calls"]
        self.assertEqual([self.reader.snapshot(c["callRef"], page["fileEpoch"])["output"] for c in calls], ["A", "B"])

    def test_native_parallel_tool_side_chains_are_one_path_not_branches(self):
        """Observed 2.1.299 shape: one assistant record per tool_use block (chained), each
        tool_result parented on its own tool_use record, the turn continuing from one of
        them. Dan's long subagents opened EMPTY ("multiple descendant leaves")."""
        tool = lambda n, p, i: record(n, p, "assistant", [{"type": "tool_use", "id": i, "name": "Read", "input": {"f": i}}])
        done = lambda n, p, i: record(n, p, content=[{"type": "tool_result", "tool_use_id": i, "content": "out " + i}])
        rows = [record(1, content="go", timestamp="2026-10-15T01:02:03.000Z"), tool(2, 1, "a"), tool(3, 2, "b"), tool(4, 3, "c"),
                done(5, 4, "c"), done(6, 3, "b")]
        self.write(rows)
        page = self.events()
        self.assertEqual(page["status"], "complete")  # mid-batch: only result leaves so far
        self.assertEqual([e["uuid"] for e in page["events"]], [uid(n) for n in range(1, 7)])
        self.write(rows + [done(7, 2, "a"), record(8, 7, "assistant", [{"type": "text", "text": "all three read"}])])
        page = self.events()
        self.assertEqual((page["status"], len(page["branches"])), ("complete", 1))
        self.assertEqual([e["uuid"] for e in page["events"]], [uid(n) for n in range(1, 9)])
        self.assertEqual(sorted(c["status"] for e in page["events"] for c in e["calls"]), ["stored"] * 3)
        self.assertEqual(page["events"][0]["time"], 1792026123.0)
        self.assertEqual(self.reader.context(page["targetRef"], 0)["text"], "[USER]\ngo\n\n---\n\n[ASSISTANT]\nall three read")
        # Round 9a: the inspector's head row shows the call's own record time; none recorded = None.
        self.assertTrue(all(self.reader.snapshot(c["callRef"], page["fileEpoch"])["time"] is None
                            for e in page["events"] for c in e["calls"]))
        self.write(rows[:1] + [record(2, 1, "assistant", [{"type": "tool_use", "id": "t", "name": "Read", "input": {}}],
                                      timestamp="2026-10-15T01:02:09.000Z")])
        page = self.events()
        call = [c for e in page["events"] for c in e["calls"]][0]
        self.assertEqual(self.reader.snapshot(call["callRef"], page["fileEpoch"])["time"], 1792026129.0)
        self.assertIsNone(self.reader.snapshot(call["callRef"], page["fileEpoch"])["resultTime"])  # no result yet
        # Its result's own record time too, so the tool view can say how long the call took.
        self.write(rows[:1] + [record(2, 1, "assistant", [{"type": "tool_use", "id": "t", "name": "Read", "input": {}}],
                                      timestamp="2026-10-15T01:02:09.000Z"),
                               record(3, 2, content=[{"type": "tool_result", "tool_use_id": "t", "content": "x"}],
                                      timestamp="2026-10-15T01:03:32.500Z")])
        page = self.events()
        call = [c for e in page["events"] for c in e["calls"]][0]
        snap = self.reader.snapshot(call["callRef"], page["fileEpoch"])
        self.assertEqual((snap["time"], snap["resultTime"]), (1792026129.0, 1792026212.5))
        # The collapsed row carries the same two times, so it shows how long the call took without a snapshot read.
        self.assertEqual((call["time"], call["resultTime"]), (snap["time"], snap["resultTime"]))

    def test_reused_tool_id_across_branches_not_global_join(self):
        rows = [record(1), record(2, 1, "assistant", [{"type": "tool_use", "id": "same", "name": "tool", "input": "A"}]),
                record(3, 2, content=[{"type": "tool_result", "tool_use_id": "same", "content": "A result"}]),
                record(4, 1, "assistant", [{"type": "tool_use", "id": "same", "name": "tool", "input": "B"}]),
                record(5, 4, content=[{"type": "tool_result", "tool_use_id": "same", "content": "B result"}])]
        self.write(rows)
        address = self.address()
        branches = self.reader.events(**address)["branches"]
        for branch, expected in zip(branches, ("A result", "B result")):
            page = self.reader.events(**address, leafRef=branch["leafRef"])
            call = next(c for row in page["events"] for c in row["calls"])
            self.assertEqual(self.reader.snapshot(call["callRef"], page["fileEpoch"])["output"], expected)

    def test_mismatched_source_and_duplicate_result(self):
        rows = self.pair()
        rows[1]["sourceToolAssistantUUID"] = uid(99)
        self.assertEqual(self.snapshot(rows)["status"], "ambiguous")
        rows = self.pair()
        rows.append(record(3, 2, content=rows[1]["message"]["content"]))
        self.assertEqual(self.snapshot(rows)["status"], "ambiguous")

    def test_empty_falsy_null_missing_and_error(self):
        for inp, expected in ((False, "false"), (None, "null"), (0, "0"), ("", "")):
            with self.subTest(inp=inp):
                snap = self.snapshot(self.pair(inp, [], is_error=True))
                self.assertEqual(snap["input"], expected)
                self.assertEqual(snap["outputBytes"], 0)
                self.assertEqual(snap["status"], "error")
        rows = self.pair()
        del rows[0]["message"]["content"][0]["input"]
        del rows[1]["message"]["content"][0]["content"]
        snap = self.snapshot(rows)
        self.assertIsNone(snap["inputBytes"])
        self.assertIsNone(snap["outputBytes"])

    def test_result_blocks_order_raw_nontext(self):
        snap = self.snapshot(self.pair("x", [{"type": "text", "text": "one\n"}, {"type": "image", "data": "AA"}, {"type": "text", "text": "🌈"}]))
        expected = 'one\n\n\n{"type": "image", "data": "AA"}\n\n🌈'
        self.assertEqual(snap["output"], expected)
        self.assertEqual(snap["outputBytes"], len(expected.encode()))

    def test_large_prose_and_bodies_complete(self):
        for size in (25_000, 200_000):
            text = "🌈" * size
            snap = self.snapshot(self.pair(text, text))
            self.assertEqual(snap["input"], text)
            self.assertEqual(snap["output"], text)
            self.write([record(1, content=text)])
            self.assertEqual(self.events()["events"][0]["text"], text)

    def test_utf8_crlf_incomplete_tail_append(self):
        line = (json.dumps(record(1, content="é🌈"), ensure_ascii=False) + "\r\n").encode()
        self.path.write_bytes(line[:-3])
        rows = self.reader.sessions()["sessions"]
        self.assertEqual(rows[0]["status"], "incomplete-tail")
        with self.path.open("ab") as f:
            f.write(line[-3:])
        page = self.events()
        self.assertEqual(page["events"][0]["text"], "é🌈")

    def test_same_length_rewrite_replace_truncate_stale(self):
        for change in ("rewrite", "replace", "truncate"):
            with self.subTest(change=change):
                self.reader = cr.ClaudeReader([{"id": "test", "root": str(self.root), "native": False}])
                self.write(self.pair())
                page = self.events()
                cref = page["events"][0]["calls"][0]["callRef"]
                if change == "rewrite":
                    self.path.write_bytes(self.path.read_bytes().replace(b'same', b'evil'))
                elif change == "replace":
                    other = self.path.with_suffix(".new")
                    other.write_bytes(self.path.read_bytes())
                    other.replace(self.path)
                else:
                    self.path.write_bytes(b"")
                with self.assertRaises(cr.ReaderError) as error:
                    self.reader.snapshot(cref, page["fileEpoch"])
                self.assertEqual(error.exception.code, 409)

    def test_after_refresh_rewrite_before_record_open(self):
        self.write(self.pair())
        page = self.events()
        original = self.reader._read
        mutated = False

        def read(index, row):
            nonlocal mutated
            if not mutated:
                mutated = True
                self.path.write_bytes(self.path.read_bytes().replace(b'same', b'evil'))
            return original(index, row)
        with patch.object(self.reader, "_read", read), self.assertRaises(cr.ReaderError):
            self.reader.snapshot(page["events"][0]["calls"][0]["callRef"], page["fileEpoch"])

    def test_eviction_invalidates_epoch(self):
        self.write(self.pair())
        page = self.events()
        self.reader.files.clear()
        with self.assertRaises(cr.ReaderError):
            self.reader.snapshot(page["events"][0]["calls"][0]["callRef"], page["fileEpoch"])

    def test_multiple_leaves_explicit_selection_hidden_parent(self):
        control = {k: v for k, v in record(2, 1, "system").items() if k != "message"}
        self.write([record(1), control, record(3, 2, content="branch A"), record(4, 2, content="branch B")])
        address = self.address()
        page = self.reader.events(**address)
        self.assertEqual(page["status"], "ambiguous")
        self.assertEqual(len(page["branches"]), 2)
        chosen = self.reader.events(**address, leafRef=page["branches"][0]["leafRef"])
        self.assertEqual([row["uuid"] for row in chosen["events"]], [uid(1), uid(2), uid(3)])

    def test_context_excludes_tools_sidechain_unknown_origin(self):
        rows = [record(1, content="main"), record(2, 1, content="unknown", origin={"kind": "unknown"}),
                record(3, 2, kind="assistant", content=[{"type": "thinking", "thinking": "SECRET"}, {"type": "text", "text": "answer"}]),
                record(4, 3, content=[{"type": "tool_result", "tool_use_id": "x", "content": "TOOL"}]),
                record(5, content="AGENT", agentId="other", isSidechain=True)]
        self.write(rows)
        page = self.events()
        context = self.reader.context(page["targetRef"], 2)
        self.assertEqual(context["text"], "[USER]\nmain\n\n---\n\n[ASSISTANT]\nanswer")
        self.assertEqual(context["bytes"], len(context["text"].encode()))
        self.assertEqual(page["traces"], ["agent:other"])

    def test_budget_tail_utf8_and_invalid_budget(self):
        self.write([record(1, content="🌈" * 3000)])
        page = self.events()
        context = self.reader.context(page["targetRef"], 1, 5120)
        self.assertTrue(context["text"].startswith("[USER]\n…"))
        self.assertLessEqual(context["bytes"], 5120)
        for budget in (0, 5119, 409601, True):
            with self.assertRaises(cr.ReaderError):
                self.reader.context(page["targetRef"], 1, budget)

    def test_exhaustive_event_paging(self):
        self.write([record(i, i - 1 if i > 1 else None, content=str(i)) for i in range(1, 206)])
        address, all_rows, cursor = self.address(), [], None
        while True:
            page = self.reader.events(**address, cursor=cursor)
            all_rows.extend(page["events"])
            if not page["hasMore"]:
                break
            cursor = page["nextCursor"]
        self.assertEqual(len(all_rows), 205)
        self.assertEqual(len({r["recordRef"] for r in all_rows}), 205)

    def test_exhaustive_session_paging(self):
        for n in range(1, 122):
            session = uid(1000 + n)
            row = record(1, sessionId=session)
            (self.path.parent / (session + ".jsonl")).write_text(json.dumps(row) + "\n")
        all_rows, cursor = [], None
        while True:
            page = self.reader.sessions(cursor=cursor)
            all_rows.extend(page["sessions"])
            if not page["hasMore"]:
                break
            cursor = page["nextCursor"]
        self.assertEqual(len(all_rows), 121)

    def test_containment_symlink_special_and_browser_handle(self):
        self.write([record(1)])
        outside = self.root.parent / ("claude-outside-" + str(uuid.uuid4()))
        outside.write_text("secret")
        try:
            link = self.root / "link"
            link.symlink_to(outside)
            for path in (link, self.root / "../" / outside.name, self.root / "%2e%2e" / "x"):
                with self.assertRaises(cr.ReaderError):
                    cr.open_contained(self.root, path)
            fifo = self.root / "fifo"
            os.mkfifo(fifo)
            with self.assertRaises(cr.ReaderError):
                cr.open_contained(self.root, fifo)
            with self.assertRaises(cr.ReaderError):
                self.reader.events(sessionRef=str(self.path), scopeRef="no")
        finally:
            outside.unlink()

    def test_artifact_separate_stub_and_limited(self):
        rows = self.pair("x", "recorded stub", savedArtifact={"relativePath": "out.txt", "completeness": "limited"})
        self.write(rows)
        saved = self.path.parent / "tool-results"
        saved.mkdir()
        (saved / "out.txt").write_text("saved text")
        snap = self.snapshot()
        artifact = self.reader.artifact(snap["artifactRef"], snap["callRef"], snap["fileEpoch"])
        self.assertEqual(artifact["text"], "saved text")
        self.assertEqual(artifact["completeness"], "limited")
        self.assertEqual(self.reader.snapshot(snap["callRef"], snap["fileEpoch"])["output"], "recorded stub")
        (saved / "out.txt").unlink()
        with self.assertRaises(cr.ReaderError) as error:
            self.reader.artifact(snap["artifactRef"], snap["callRef"], snap["fileEpoch"])
        self.assertEqual(error.exception.code, 404)

    def test_body_limits_no_prefix(self):
        with patch.object(cr, "BODY_LIMIT", 32):
            with self.assertRaises(cr.ReaderError) as error:
                self.snapshot(self.pair("x" * 20, "y" * 20))
            self.assertEqual(error.exception.code, 413)
            self.write([record(1, content="z" * 33)])
            self.assertIsNone(self.events()["events"][0]["text"])

    def test_committed_golden_fixtures(self):
        fixtures = Path(__file__).parent / "fixtures/claude"
        for name in ("windows-2.1.185.jsonl", "synthetic-v1.jsonl"):
            with self.subTest(fixture=name):
                self.reader = cr.ClaudeReader([{"id": "test", "root": str(self.root), "native": False}])
                self.path.write_bytes((fixtures / name).read_bytes())
                page = self.events()
                calls = [c for row in page["events"] for c in row["calls"]]
                self.assertEqual(len(calls), 1)
                snap = self.reader.snapshot(calls[0]["callRef"], page["fileEpoch"])
                self.assertEqual(snap["status"], "stored")
                context = self.reader.context(page["targetRef"], 1)
                self.assertNotIn("Sanitized stored result", context["text"])
                self.assertNotIn("recorded stub", context["text"])

    def test_bounded_inventory_exhaustive_beyond_eviction_limit(self):
        for n in range(1, 30):
            session = uid(4000 + n)
            (self.path.parent / (session + ".jsonl")).write_text(json.dumps(record(1, sessionId=session)) + "\n")
        cursor, seen = None, []
        with patch.object(cr, "INVENTORY_FILES", 4), patch.object(cr, "SUMMARY_FILES_PER_PAGE", 3):
            for _ in range(40):
                page = self.reader.sessions(cursor=cursor)
                seen.extend(r["uuid"] for r in page["sessions"])
                self.assertLessEqual(len(self.reader.inventory), 4)
                if not page["hasMore"]:
                    break
                cursor = page["nextCursor"]
            else:
                self.fail("inventory continuation did not exhaust")
        self.assertEqual(len(seen), 29)
        self.assertEqual(len(set(seen)), 29)

    def test_two_inventory_readers_never_reset_each_others_cursor(self):
        # WPWZ14 (history-reader cursor collision): a fresh read by another reader (a second tab, the
        # sidebar's 10-second refresh beside a Reconnect) used to restart the one shared walk, so the
        # first reader's next page answered 409 "inventory scan restarted".
        for n in range(12):
            session = uid(5000 + n)
            (self.path.parent / (session + ".jsonl")).write_text(json.dumps(record(1, sessionId=session)) + "\n")
        def drain(page, seen):
            seen.extend(row["uuid"] for row in page["sessions"])
            return page["nextCursor"]
        first, second = [], []
        a = drain(self.reader.sessions(), first)
        b = drain(self.reader.sessions(), second)          # the second reader starts while the first pages
        while a or b:
            if a:
                a = drain(self.reader.sessions(cursor=a), first)
            if b:
                b = drain(self.reader.sessions(cursor=b), second)
        self.assertEqual(sorted(first), sorted(set(first)))
        self.assertEqual(sorted(first), sorted(second))
        self.assertEqual(len(first), 12)

    def test_inventory_cursor_of_an_evicted_scan_is_stale(self):
        for n in range(12):
            session = uid(5000 + n)
            (self.path.parent / (session + ".jsonl")).write_text(json.dumps(record(1, sessionId=session)) + "\n")
        cursor = self.reader.sessions()["nextCursor"]
        for _ in range(cr.INVENTORY_SCANS):
            self.reader.sessions()
        with self.assertRaises(cr.ReaderError) as error:
            self.reader.sessions(cursor=cursor)
        self.assertEqual(error.exception.status, "stale")

    def test_discovery_suspends_before_unreturned_summary_cap(self):
        for n in range(20):
            session = uid(9000 + n)
            (self.path.parent / (session + ".jsonl")).write_text(json.dumps(record(1, sessionId=session)) + "\n")
        scan = cr.InventoryScan()
        with patch.object(cr, "SUMMARY_FILES_PER_PAGE", 3):
            _, complete = self.reader._discover(scan)
        self.assertFalse(complete)
        self.assertLessEqual(len(scan.found), 3)

    def test_aggregate_summary_memory_and_cwd_bounded(self):
        self.reader = cr.ClaudeReader([{"id": "test", "root": str(self.root)}], summary_limit=8000)
        for n in range(12):
            session = uid(6000 + n)
            (self.path.parent / (session + ".jsonl")).write_text(json.dumps(record(1, sessionId=session)) + "\n")
        cursor = None
        while True:
            page = self.reader.sessions(cursor=cursor)
            self.assertLessEqual(sum(i.summary_bytes for i in self.reader.files.values()), 8000)
            if not page["hasMore"]:
                break
            cursor = page["nextCursor"]
        self.write([record(n, n - 1 if n > 1 else None, cwd="/cwd/" + str(n)) for n in range(1, 6)])
        self.reader.summary_limit = cr.INDEX_SUMMARY_BYTES
        reference = self.reader._ref("session", ("test", self.path))
        selected = self.reader._session(reference)
        self.assertEqual(len(selected.cwds), 2)
        for index in self.reader.files.values():
            self.assertLessEqual(len(index.cwds), 2)

    def test_unchanged_target_validation_does_not_reread_all_bodies(self):
        self.write([record(n, n - 1 if n > 1 else None) for n in range(1, 205)])
        page = self.events()
        with patch.object(self.reader, "_read", wraps=self.reader._read) as body:
            self.reader.validate_target(page["targetRef"])
            self.assertEqual(body.call_count, 0)

    def test_append_delta_snapshot_boundary_and_key_stability(self):
        self.write([record(1), record(2, 1)])
        page = self.events()
        old_epoch = page["fileEpoch"]
        with self.path.open("a") as f:
            f.write(json.dumps(record(3, 2, content="new")) + "\n")
        delta = self.reader.events(targetRef=page["targetRef"], cursor=page["deltaCursor"])
        self.assertEqual(delta["change"], "append")
        self.assertEqual([r["text"] for r in delta["events"]], ["new"])
        self.assertEqual(delta["fileEpoch"], old_epoch)
        self.assertNotEqual(delta["targetRef"], page["targetRef"])
        old = self.reader.events(targetRef=page["targetRef"])
        self.assertEqual(len(old["events"]), 2)  # old target never silently retargeted
        self.assertEqual(old["events"][0]["recordRef"], page["events"][0]["recordRef"])
        empty = self.reader.events(targetRef=delta["targetRef"], cursor=delta["deltaCursor"])
        self.assertEqual(empty["events"], [])

    def test_delta_new_branch_never_guesses_leaf(self):
        self.write([record(1)])
        page = self.events()
        with self.path.open("a") as f:
            f.write(json.dumps(record(2, 1)) + "\n" + json.dumps(record(3, 1)) + "\n")
        with self.assertRaises(cr.ReaderError) as error:
            self.reader.events(targetRef=page["targetRef"], cursor=page["deltaCursor"])
        self.assertEqual(error.exception.status, "ambiguous")

    def test_paged_snapshot_append_does_not_splice_new_leaf(self):
        self.write([record(n, n - 1 if n > 1 else None) for n in range(1, 103)])
        address = self.address()
        first = self.reader.events(**address)
        with self.path.open("a") as f:
            f.write(json.dumps(record(103, 102)) + "\n")
        second = self.reader.events(**address, cursor=first["nextCursor"])
        self.assertEqual([r["uuid"] for r in second["events"]], [uid(101), uid(102)])
        self.assertEqual(first["leafRef"], second["leafRef"])

    def test_events_carry_their_path_position_across_pages_and_appends(self):
        # Round 9b: positions let the viewer keep one contiguous run and never splice a gap.
        self.write([record(n, n - 1 if n > 1 else None) for n in range(1, 103)])
        address = self.address()
        first = self.reader.events(**address)
        second = self.reader.events(**address, cursor=first["nextCursor"])
        self.assertEqual([r["seq"] for r in first["events"] + second["events"]], list(range(102)))
        with self.path.open("a") as f:
            f.write(json.dumps(record(103, 102)) + "\n")
        delta = self.reader.events(**address, cursor=second["deltaCursor"])
        self.assertEqual([(r["seq"], r["uuid"]) for r in delta["events"]], [(102, uid(103))])

    def test_from_seq_starts_a_first_page_at_a_path_position(self):
        # FMPZO7: after a pane-server restart the viewer re-reads the rows on screen by position.
        self.write([record(n, n - 1 if n > 1 else None) for n in range(1, 103)])
        address = self.address()
        page = self.reader.events(**address, fromSeq=90)
        self.assertEqual([(r["seq"], r["uuid"]) for r in page["events"]], [(n, uid(n + 1)) for n in range(90, 102)])
        self.assertFalse(page["hasMore"])
        with self.path.open("a") as f:
            f.write(json.dumps(record(103, 102)) + "\n")
        delta = self.reader.events(**address, cursor=page["deltaCursor"])  # its cursors continue as usual
        self.assertEqual([(r["seq"], r["uuid"]) for r in delta["events"]], [(102, uid(103))])
        self.assertEqual(self.reader.events(**address, fromSeq=500)["events"], [])  # past the end: empty
        for bad in ({"fromSeq": -1}, {"fromSeq": True}, {"fromSeq": 3, "cursor": page["nextCursor"] or page["deltaCursor"]},
                    {"fromSeq": 3, "leafRef": page["leafRef"]}):
            with self.assertRaises(cr.ReaderError) as error:
                self.reader.events(**address, **bad)
            self.assertEqual((error.exception.code, error.exception.status), (400, "schema"), bad)

    def test_held_call_refresh_follows_only_unique_descendants(self):
        self.write(self.pair()[:1])
        page = self.events()
        call = page["events"][0]["calls"][0]["callRef"]
        self.assertEqual(self.reader.snapshot(call, page["fileEpoch"])["status"], "pending-or-missing")
        with self.path.open("a") as f:
            f.write(json.dumps(self.pair(out="completed")[1]) + "\n")
        self.assertEqual(self.reader.snapshot(call, page["fileEpoch"])["output"], "completed")

    def test_real_layout_subagent_is_separate_scope(self):
        self.write([record(1, content="main")])
        sub = self.path.parent / SESSION / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-other.jsonl").write_text(json.dumps(record(1, content="AGENT", agentId="other", isSidechain=True)) + "\n")
        page = self.reader.sessions()
        rows = page["sessions"]
        self.assertEqual(len(rows), 2)
        agent = next(r for r in rows if r["scopes"][0]["label"] != "main")
        events = self.reader.events(sessionRef=agent["sessionRef"], scopeRef=agent["scopes"][0]["scopeRef"])
        self.assertIsNone(events["targetRef"])
        self.assertEqual(events["events"][0]["text"], "AGENT")
        main = next(r for r in rows if r is not agent)
        events = self.reader.events(sessionRef=main["sessionRef"], scopeRef=main["scopes"][0]["scopeRef"])
        self.assertEqual(self.reader.context(events["targetRef"], 0)["text"], "[USER]\nmain")

    def test_oversize_line_continuation_preserves_next_framed_record(self):
        self.reader.scan_bytes = 64
        self.path.write_bytes(b'"' + b'x' * 900 + b'"\n' + (json.dumps(record(1)) + '\n').encode())
        with patch.object(cr, "SOURCE_LIMIT", 512):
            for _ in range(20):
                rows = self.reader.sessions()["sessions"]
                if rows[0]["status"] == "complete":
                    break
            else:
                self.fail("oversize framing did not continue")
            page = self.reader.events(sessionRef=rows[0]["sessionRef"], scopeRef=rows[0]["scopes"][0]["scopeRef"])
            self.assertEqual(page["events"][0]["text"], "hello")
            self.assertIn("source-limit", str(page["diagnostics"]))

    def test_internal_text_blocks_filtered_independently(self):
        self.write([record(1, content=[{"type": "text", "text": "genuine"},
                                     {"type": "text", "text": "<local-command-stdout>internal"},
                                     {"type": "tool_result", "tool_use_id": "orphan", "content": "TOOL"}])])
        page = self.events()
        context = self.reader.context(page["targetRef"], 0)
        self.assertEqual(context["text"], "[USER]\ngenuine")

    def test_uncommitted_large_summary_not_retained_past_limit(self):
        self.write(self.pair("large", "result"))
        self.reader.summary_limit = 64
        rows = self.reader.sessions()["sessions"]
        self.assertEqual(rows[0]["status"], "index-limit")
        index = next(iter(self.reader.files.values()))
        self.assertEqual(index.by_uuid, {})
        self.assertEqual(index.records, [])

    def test_actual_source_record_exact_16mib_and_over_framed_continuation(self):
        row = record(1, padding="")
        base = json.dumps(row) + "\n"
        wire = base.replace('"padding": ""', '"padding": "' + "x" * (cr.SOURCE_LIMIT - len(base.encode())) + '"')
        self.assertEqual(len(wire.encode()), 16 * 1024 * 1024)
        self.path.write_text(wire)
        self.assertEqual(self.events()["events"][0]["text"], "hello")
        self.reader = cr.ClaudeReader([{"id": "test", "root": str(self.root)}])
        self.path.write_text(wire[:-1] + " \n" + json.dumps(record(2, content="after oversized line")) + "\n")
        page = self.events()
        self.assertEqual([e["text"] for e in page["events"]], ["after oversized line"])
        self.assertIn("source-limit", str(page["diagnostics"]))

    def test_actual_combined_snapshot_exact_8mib_and_one_byte_over(self):
        size = cr.BODY_LIMIT // 2
        snap = self.snapshot(self.pair("x" * size, "y" * size))
        self.assertEqual(snap["inputBytes"] + snap["outputBytes"], 8 * 1024 * 1024)
        self.assertEqual(snap["input"], "x" * size)
        self.assertEqual(snap["output"], "y" * size)
        self.write(self.pair("x" * size, "y" * (size + 1)))
        # Refresh/rebuild the rewritten index, then select its new explicit epoch.
        self.reader = cr.ClaudeReader([{"id": "test", "root": str(self.root)}])
        with self.assertRaises(cr.ReaderError) as error:
            self.snapshot()
        self.assertEqual((error.exception.code, error.exception.status), (413, "body-limit"))

    def test_actual_prose_body_and_whole_page_limits_are_not_prefixes(self):
        for size in (cr.BODY_LIMIT, cr.BODY_LIMIT + 1):
            with self.subTest(size=size):
                self.write([record(1, content="z" * size)])
                self.reader = cr.ClaudeReader([{"id": "test", "root": str(self.root)}])
                page = self.events()
                row = page["events"][0]
                self.assertIsNone(row["text"])
                self.assertEqual(row["status"], "response-limit" if size == cr.BODY_LIMIT else "body-limit")
                self.assertLessEqual(len(json.dumps(page).encode()), cr.PAGE_LIMIT)
        self.write([record(n, n - 1 if n > 1 else None, content="x" * 100_000) for n in range(1, 102)])
        self.reader = cr.ClaudeReader([{"id": "test", "root": str(self.root)}], scan_bytes=32 * 1024 * 1024)
        address, cursor, seen = self.address(), None, []
        while True:
            page = self.reader.events(**address, cursor=cursor)
            self.assertLessEqual(len(page["events"]), 100)
            self.assertLessEqual(len(json.dumps(page).encode()), 8 * 1024 * 1024)
            self.assertTrue(all(e["text"] == "x" * 100_000 for e in page["events"]))
            seen.extend(e["uuid"] for e in page["events"])
            if not page["hasMore"]:
                break
            cursor = page["nextCursor"]
        self.assertEqual(len(set(seen)), 101)

    def test_actual_artifact_body_source_bounds_and_stub_independence(self):
        self.write(self.pair("in", "stub", savedArtifact={"relativePath": "out.txt", "completeness": "full"}))
        saved = self.path.parent / "tool-results"
        saved.mkdir()
        path = saved / "out.txt"
        snap = self.snapshot()
        path.write_bytes(b"x" * cr.BODY_LIMIT)
        art = self.reader.artifact(snap["artifactRef"], snap["callRef"], snap["fileEpoch"])
        self.assertEqual(art["bytes"], 8 * 1024 * 1024)
        self.assertEqual(art["text"], "x" * cr.BODY_LIMIT)
        for size, status in ((cr.BODY_LIMIT + 1, "body-limit"), (cr.SOURCE_LIMIT, "body-limit"),
                             (cr.SOURCE_LIMIT + 1, "source-limit")):
            with self.subTest(size=size):
                with path.open("wb") as f:
                    f.truncate(size)
                with self.assertRaises(cr.ReaderError) as error:
                    self.reader.artifact(snap["artifactRef"], snap["callRef"], snap["fileEpoch"])
                self.assertEqual((error.exception.code, error.exception.status), (413, status))
                self.assertEqual(self.reader.snapshot(snap["callRef"], snap["fileEpoch"])["output"], "stub")

    def test_artifact_replaced_with_symlink_at_actual_open_is_refused(self):
        self.write(self.pair("in", "stub", savedArtifact={"relativePath": "out.txt", "completeness": "full"}))
        saved = self.path.parent / "tool-results"
        saved.mkdir()
        path = saved / "out.txt"
        path.write_text("approved")
        outside = self.root / "unapproved.txt"
        outside.write_text("not an approved tool artifact")
        snap = self.snapshot()
        original = cr.os.open
        def replace_at_open(name, flags, *args, **kwargs):
            if name == "out.txt":
                path.unlink()
                path.symlink_to(outside)
            return original(name, flags, *args, **kwargs)
        with patch.object(cr.os, "open", replace_at_open), self.assertRaises(cr.ReaderError) as error:
            self.reader.artifact(snap["artifactRef"], snap["callRef"], snap["fileEpoch"])
        self.assertEqual(error.exception.code, 403)

    def test_fork_sessions_and_agents_reused_record_tool_ids_stay_separate(self):
        self.write(self.pair("main", "main result"))
        other = uid(8000)
        copy = [{**r, "sessionId": other} for r in self.pair("fork", "fork result")]
        (self.path.parent / (other + ".jsonl")).write_text("".join(json.dumps(r) + "\n" for r in copy))
        agent = [{**r, "agentId": "separate", "isSidechain": True} for r in self.pair("agent", "agent result")]
        with self.path.open("a") as f:
            f.write("".join(json.dumps(r) + "\n" for r in agent))
        sessions = self.reader.sessions()["sessions"]
        found = []
        for session in sessions:
            for scope in session["scopes"]:
                page = self.reader.events(sessionRef=session["sessionRef"], scopeRef=scope["scopeRef"])
                call = page["events"][0]["calls"][0]
                snap = self.reader.snapshot(call["callRef"], page["fileEpoch"])
                found.append((snap["input"], snap["output"]))
                if scope["label"] != "main":
                    self.assertIsNone(page["targetRef"])
        self.assertCountEqual(found, [("main", "main result"), ("fork", "fork result"), ("agent", "agent result")])

    def test_all_supported_provenance_exclusions_and_absent_origin(self):
        # Bash mode is recorded as <bash-input>; a typed `!` (or a path) is Dan's prose (audit, Unit A).
        excluded = [" /effort max", " !echo kept", "<command-name>/help</command-name>",
                    "<local-command-stdout>output", "<system-reminder>internal", "<bash-input>ls", "<bash-stdout>shell"]
        rows = [record(1, content="genuine")]
        for n, text in enumerate(excluded, 2):
            rows.append(record(n, n - 1, content=text))
        rows.append(record(9, 8, content="unknown provenance", origin={}))
        rows.append(record(10, 9, content="metadata", isMeta=True))
        missing = record(11, 10, content="absent origin")
        del missing["origin"]
        rows.append(missing)
        self.write(rows)
        context = self.reader.context(self.events()["targetRef"], 0)
        self.assertEqual(context["text"], "[USER]\ngenuine\n\n---\n\n[USER]\n !echo kept")
        self.assertEqual(len(context["exclusions"]), 9)

    def test_structural_routing_type_and_duplicate_matrix(self):
        cases = []
        for field, value in (("parentUuid", {}), ("parentUuid", 1), ("parentUuid", False),
                             ("uuid", 3), ("sessionId", []), ("isSidechain", 0),
                             ("origin", []), ("origin", {"kind": 1}), ("sourceToolAssistantUUID", "not-uuid")):
            cases.append(record(1, **{field: value}))
        cases += [record(1, message={"role": "assistant", "content": "wrong"}),
                  record(1, origin={"kind": "unknown"}, content=[{"type": "text", "text": 1}]),
                  record(1, origin={"kind": "unknown"}, content=[{"type": "text"}]),
                  record(1, content=[{"type": "tool_result", "tool_use_id": "tool", "is_error": 0}]),
                  record(1, content=[{"type": "tool_result", "tool_use_id": "tool", "content": [{"type": "text", "text": 1}]}]),
                  record(1, content=[{"type": "tool_result", "tool_use_id": "tool", "savedArtifact": {"relativePath": 1, "completeness": "full"}}])]
        for row in cases:
            with self.subTest(row=row):
                index = cr.FileIndex(self.path, self.root, SESSION, "test")
                summary = self.reader._summarize(index, json.dumps(row).encode(), 0)
                self.assertEqual(summary.kind, "diagnostic")
                self.assertTrue(summary.reason.startswith("malformed:"))
        # Duplicate routing keys at every traversed structural level, never payload keys.
        raw = json.dumps(record(1))
        for old, new in [('"type": "user"', '"type": "user", "type": "assistant"'),
                         ('"role": "user"', '"role": "user", "role": "assistant"'),
                         ('"kind": "human"', '"kind": "human", "kind": "unknown"')]:
            summary = self.reader._summarize(cr.FileIndex(self.path, self.root, SESSION, "test"), raw.replace(old, new).encode(), 0)
            self.assertEqual(summary.kind, "diagnostic")
        for content in ('[{"type":"text","type":"thinking","text":"x"}]',
                        '[{"type":"tool_result","tool_use_id":"tool","content":[{"type":"text","type":"image","text":"x"}]}]'):
            wire = raw.replace('"content": "hello"', '"content":' + content)
            summary = self.reader._summarize(cr.FileIndex(self.path, self.root, SESSION, "test"), wire.encode(), 0)
            self.assertEqual(summary.kind, "diagnostic")

    def test_orphan_missing_unknown_trace_and_missing_root_are_visible(self):
        self.write([record(1, content=[{"type": "tool_result", "tool_use_id": "orphan", "content": "stored orphan"}]),
                    record(2, 1, kind="assistant", content=[{"type": "tool_use", "id": "pending", "name": "same", "input": {}}])])
        page = self.events()
        self.assertEqual(page["events"][0]["orphanResults"], ["orphan"])
        call = page["events"][1]["calls"][0]
        snap = self.reader.snapshot(call["callRef"], page["fileEpoch"])
        self.assertEqual(snap["status"], "pending-or-missing")
        self.assertIsNone(snap["outputBytes"])
        bad = self.root / "project" / "unknown-owner" / "subagents" / "agent-x.jsonl"
        bad.parent.mkdir(parents=True)
        bad.write_text(json.dumps(record(3)) + "\n")
        diagnostics, cursor = [], None
        while True:
            result = self.reader.sessions(cursor=cursor)
            diagnostics += result["diagnostics"]
            if not result["hasMore"]:
                break
            cursor = result["nextCursor"]
        self.assertTrue(any(d["status"] == "unsupported-trace" for d in diagnostics))
        absent = cr.ClaudeReader([{"id": "absent", "root": str(self.root / "absent")}]).sessions()
        self.assertEqual(absent["sessions"], [])
        self.assertEqual(absent["diagnostics"][0]["status"], "no-history")

    def test_selected_source_outage_and_special_replacement_never_serves_cached_body(self):
        self.write(self.pair("input", "output"))
        page = self.events()
        call = page["events"][0]["calls"][0]["callRef"]
        self.path.unlink()
        with self.assertRaises(cr.ReaderError) as missing:
            self.reader.snapshot(call, page["fileEpoch"])
        self.assertEqual(missing.exception.code, 404)
        os.mkfifo(self.path)
        with self.assertRaises(cr.ReaderError) as special:
            self.reader.snapshot(call, page["fileEpoch"])
        self.assertEqual(special.exception.code, 403)

    def test_source_symlink_swap_inside_actual_record_open_is_refused(self):
        self.write(self.pair("input", "output"))
        page = self.events()
        call = page["events"][0]["calls"][0]["callRef"]
        outside = self.root / "other.jsonl"
        outside.write_bytes(self.path.read_bytes())
        original = cr.os.open
        swapped = False
        def replace_at_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if str(path) == self.path.name and not swapped:
                swapped = True
                self.path.unlink()
                self.path.symlink_to(outside)
            return original(path, flags, *args, **kwargs)
        with patch.object(cr.os, "open", side_effect=replace_at_open), self.assertRaises(cr.ReaderError) as blocked:
            self.reader.snapshot(call, page["fileEpoch"])
        self.assertTrue(swapped)
        self.assertEqual(blocked.exception.code, 403)

    def test_artifact_witness_missing_invalid_utf8_and_special_file(self):
        snap = self.snapshot(self.pair("input", "stub", savedArtifact={"relativePath": "out.txt", "completeness": "full"}))
        args = {k: snap[k] for k in ("artifactRef", "callRef", "fileEpoch")}
        for field in ("callRef", "fileEpoch"):
            with self.subTest(field=field), self.assertRaises(cr.ReaderError) as error:
                self.reader.artifact(**{**args, field: "wrong"})
            self.assertEqual(error.exception.code, 409)
        with self.assertRaises(cr.ReaderError) as error:
            self.reader.artifact(**args)
        self.assertEqual(error.exception.code, 404)
        path = self.path.parent / "tool-results" / "out.txt"
        path.parent.mkdir()
        path.write_bytes(b"\xff")
        with self.assertRaises(cr.ReaderError) as error:
            self.reader.artifact(**args)
        self.assertEqual(error.exception.status, "unsupported")
        path.unlink()
        os.mkfifo(path)
        with self.assertRaises(cr.ReaderError) as error:
            self.reader.artifact(**args)
        self.assertEqual(error.exception.code, 403)

    def test_native_artifact_reference_or_prose_path_never_opens_arbitrary_file(self):
        rows = self.pair("input", "cat /etc/passwd", savedArtifact={"relativePath": "out.txt", "completeness": "full"})
        rows = [{**r, "version": "2.1.185"} for r in rows]
        self.write(rows)
        snap = self.snapshot()
        self.assertIsNone(snap["artifactRef"])
        self.assertEqual(snap["output"], "cat /etc/passwd")
        self.assertIn("unsupported-artifact-reference", str(self.events()["diagnostics"]))


MEASURED = {"input_tokens": 2, "cache_read_input_tokens": 293869,
            "cache_creation_input_tokens": 375, "output_tokens": 1293}
MEASURED_AT = "2026-10-14T19:13:03.797Z"


def measured_epoch():
    return datetime.fromisoformat("2026-10-14T19:13:03.797+00:00").timestamp()


class ContextReadoutTests(unittest.TestCase):
    """The pane's bounded last-call context readout: the shared witness scan and report."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "project" / (SESSION + ".jsonl")
        self.path.parent.mkdir()
        self.reader = cr.ClaudeReader([{"id": "test", "root": str(self.root), "native": True}])

    def tearDown(self):
        self.temp.cleanup()

    def write(self, rows):
        self.path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))

    def assistant(self, n, usage, timestamp, **extra):
        return record(n, kind="assistant", content="reply", timestamp=timestamp,
                      message={"role": "assistant", "model": "claude-opus-5-5",
                               "stop_reason": "end_turn", "usage": usage}, **extra)

    def address(self):
        rows = self.reader.sessions()["sessions"]
        main = next(s for s in rows[0]["scopes"] if s["label"] == "main")
        return {"sessionRef": rows[0]["sessionRef"], "scopeRef": main["scopeRef"]}

    def test_the_shared_witness_scan_supplies_the_last_completed_call(self):
        older = self.assistant(1, {"input_tokens": 5, "cache_read_input_tokens": 100,
                                   "cache_creation_input_tokens": 0, "output_tokens": 9},
                               "2026-10-14T18:00:00.000Z")
        sidechain = self.assistant(4, {"input_tokens": 9, "cache_read_input_tokens": 9,
                                       "cache_creation_input_tokens": 9, "output_tokens": 9},
                                   "2026-10-14T19:20:00.000Z", isSidechain=True)
        self.write([older, record(2), self.assistant(3, MEASURED, MEASURED_AT), sidechain])
        report = self.reader.transcript_context(self.root, self.path, uuid=SESSION, record={"role": "Manager"})
        self.assertEqual(report["state"], "measured")
        self.assertEqual(report["occupancy_tokens"], 294246)  # output 1293 is never occupancy
        self.assertIsNone(report["window_tokens"])            # no window observed: tokens still shown
        self.assertIsNone(report["fraction_used"])
        self.assertEqual(report["timestamp"], MEASURED_AT)
        self.assertAlmostEqual(report["measured_at"], measured_epoch(), places=3)
        self.assertIn("as of the previous model call", report["label"])
        self.assertEqual(report["report_source"], "shared")
        self.assertEqual(report["source"], "native-transcript")
        self.assertEqual(report["model"], "claude-opus-5-5")
        self.assertEqual(report["identity"], {"execution_id": None, "generation": None,
                                              "uuid": SESSION, "role": "Manager"})
        # A sidechain or subagent-lineage record is never the session's context, and another
        # session's records never answer this readout.
        self.write([self.assistant(1, MEASURED, MEASURED_AT, isSidechain=True),
                    self.assistant(2, MEASURED, MEASURED_AT, parent_tool_use_id="toolu_fixture")])
        self.assertEqual(self.reader.transcript_context(self.root, self.path, uuid=SESSION)["state"], "unavailable")
        self.write([self.assistant(1, MEASURED, MEASURED_AT)])
        self.assertEqual(self.reader.transcript_context(self.root, self.path, uuid=uid(7))["state"], "unavailable")

    def test_no_completed_call_or_missing_shared_module_is_an_honest_unknown(self):
        self.write([record(1)])
        report = self.reader.transcript_context(self.root, self.path, uuid=SESSION, record={"role": "Manager"})
        self.assertEqual((report["state"], report["reason"]),
                         ("unavailable", "no completed model call in the transcript tail yet"))
        self.assertIsNone(report["occupancy_tokens"])
        # A certifying stop_reason and all three nonnegative usage keys are required.
        self.write([record(1, kind="assistant", content="reply",
                           message={"role": "assistant", "model": "claude-opus-5-5", "stop_reason": None,
                                    "usage": MEASURED}, timestamp=MEASURED_AT)])
        self.assertEqual(self.reader.transcript_context(self.root, self.path, uuid=SESSION)["state"], "unavailable")
        self.write([record(1, kind="assistant", content="reply",
                           message={"role": "assistant", "model": "claude-opus-5-5", "stop_reason": "end_turn",
                                    "usage": {"input_tokens": 2, "output_tokens": 5}}, timestamp=MEASURED_AT)])
        self.assertEqual(self.reader.transcript_context(self.root, self.path, uuid=SESSION)["state"], "unavailable")
        # The shared module is required; a missing one is an explicit unknown, never arithmetic.
        self.write([self.assistant(1, MEASURED, MEASURED_AT)])
        with patch.dict(sys.modules, {"claude_headless": None, "claude_headless.context": None}):
            report = self.reader.transcript_context(self.root, self.path, uuid=SESSION)
        self.assertEqual((report["state"], report["reason"]),
                         ("unavailable", "shared context report unavailable"))

    def test_generation_cutoff_refuses_a_previous_generations_call(self):
        self.write([self.assistant(1, MEASURED, MEASURED_AT)])
        self.assertEqual(self.reader.transcript_context(self.root, self.path, uuid=SESSION,
                                                        not_before="2026-10-14T19:00:00Z")["state"], "measured")
        report = self.reader.transcript_context(self.root, self.path, uuid=SESSION,
                                                not_before=measured_epoch() + 1)
        self.assertEqual((report["state"], report["reason"]),
                         ("unavailable", "no completed model call yet in this generation"))

    def test_the_witness_is_cached_but_the_report_is_recomposed_every_read(self):
        self.write([self.assistant(1, MEASURED, MEASURED_AT)])
        module = cr._shared_context()
        self.assertIsNotNone(module)
        reads, real = [], module.latest_transcript_call
        def counted(*args, **kwargs):
            reads.append(1)
            return real(*args, **kwargs)
        reader = cr.ClaudeReader([{"id": "fresh", "root": str(self.root), "native": True}])
        with patch.object(module, "latest_transcript_call", counted):
            first = reader.transcript_context(self.root, self.path, uuid=SESSION, record={"role": "Manager"},
                                              windows={"claude-opus-5-5": 1000000}, now=measured_epoch() + 10)
            second = reader.transcript_context(self.root, self.path, uuid=SESSION, record={"role": "worker"},
                                               windows={"claude-opus-5-5": 500000}, now=measured_epoch() + 610)
            self.assertEqual(len(reads), 1)  # unchanged file: the raw witness is read once
            self.path.write_text(self.path.read_text() + json.dumps(self.assistant(2, {
                "input_tokens": 2, "cache_read_input_tokens": 296879, "cache_creation_input_tokens": 1994,
                "output_tokens": 5}, "2026-10-14T19:16:04.917Z")) + "\n")
            third = reader.transcript_context(self.root, self.path, uuid=SESSION, now=measured_epoch() + 700)
            self.assertEqual(len(reads), 2)  # a changed file is reread
        # The report is recomposed every read: window, role target and age are never stale.
        self.assertEqual((first["window_tokens"], first["fraction_used"]), (1000000, 0.294246))
        self.assertEqual(second["window_tokens"], 500000)
        self.assertEqual(first["occupancy_tokens"], second["occupancy_tokens"])
        self.assertNotEqual(first["label"], second["label"])  # 10s vs 610s old
        self.assertNotEqual(first["target"]["fraction"], second["target"]["fraction"])
        self.assertEqual(third["occupancy_tokens"], 298875)
        self.assertAlmostEqual(third["measured_at"],
                               datetime.fromisoformat("2026-10-14T19:16:04.917+00:00").timestamp(), places=3)

    def test_transcript_path_scans_configured_roots_and_never_trusts_cwd_alone(self):
        self.write([record(1)])
        self.assertEqual(self.reader.transcript_path(SESSION), ("test", self.path))
        self.assertEqual(self.reader.transcript_path(SESSION, cwd="/fixture"), ("test", self.path))
        self.assertEqual(self.reader.transcript_path("not-a-uuid"), (None, None))
        self.assertEqual(self.reader.context_readout_for_uuid(uid(3))["reason"],
                         "no native transcript found for this session yet")

    def test_events_carry_the_measured_readout_and_the_source_progress(self):
        self.write([record(1), self.assistant(2, MEASURED, MEASURED_AT)])
        page = self.reader.events(**self.address())
        readout = page["contextReadout"]
        self.assertEqual(readout["occupancy_tokens"], 294246)
        self.assertIsNone(readout["window_tokens"])  # a stored transcript has no raw stream window
        self.assertIsNone(readout["fraction_used"])
        self.assertEqual(readout["timestamp"], MEASURED_AT)
        self.assertAlmostEqual(readout["measured_at"], measured_epoch(), places=3)
        self.assertEqual(readout["identity"]["uuid"], SESSION)
        self.assertEqual(readout["identity"]["role"], "history")
        self.assertEqual(readout["occupancy_basis"], "input + cache_read + cache_creation (no output)")
        size = self.path.stat().st_size
        self.assertEqual(page["sourceProgress"], {"indexedBytes": size, "indexedRecords": 2,
                                                  "sourceBytes": size, "status": "complete",
                                                  "kind": "native-transcript"})


if __name__ == "__main__":
    unittest.main()
