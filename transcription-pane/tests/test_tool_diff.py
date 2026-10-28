"""Offline Diff v1 evidence, not input-only previews or filesystem reconstruction."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tool_diff import claude_diff, opencode_diff, recorded_value, SHELL_REASON
from claude_reader import parse_record

FIXTURES = Path(__file__).parent / "fixtures/tool-diff"


def claude_fixture():
    return json.loads((FIXTURES / "claude-history.json").read_text())


def opencode_fixture():
    return json.loads((FIXTURES / "opencode-state.json").read_text())


class DiffTests(unittest.TestCase):
    def test_claude_edit_and_compatible_multiedit_true_starts_prefixes_and_update(self):
        f = claude_fixture()
        for name in ("Edit", "MultiEdit", "Write"):
            meta = {**f["toolUseResult"], **({"type": "update"} if name == "Write" else {})}
            diff = claude_diff(name, f["input"], meta, "stored")
            self.assertEqual(diff, {"source": "claude-structured-patch", "complete": True, "files": [{
                "path": "/fixture/code.py", "status": "modified", "additions": 1, "deletions": 1,
                "hunks": f["toolUseResult"]["structuredPatch"]}]})
        self.assertEqual(diff["files"][0]["hunks"][0]["oldStart"], 124)

    def test_claude_create_empty_and_no_final_newline(self):
        for content, lines in (("", []), ("雪\n", ["+雪"]), ("a\u2028b", ["+a\u2028b", "\\ No newline at end of file"])):
            meta = {**claude_fixture()["create"], "content": content}
            d = claude_diff("Write", {}, meta, "stored")
            self.assertTrue(d["complete"])
            h = d["files"][0]["hunks"][0]
            self.assertEqual(h, {"oldStart": 0, "oldLines": 0, "newStart": 1,
                                 "newLines": int(bool(content)), "lines": lines})
            self.assertEqual(d["files"][0]["status"], "added")

    def test_opencode_multifile_multihunk_omitted_counts_empty_sides_and_markers(self):
        f = opencode_fixture(); before = copy.deepcopy(f)
        d = opencode_diff(f["toolName"], f["state"])
        self.assertEqual(f, before)
        self.assertEqual((d["source"], d["complete"], len(d["files"])), ("opencode-unified-patch", True, 3))
        first, added, deleted = d["files"]
        self.assertEqual(first, {"path": "/fixture/code.py", "status": "modified", "additions": 2, "deletions": 2,
            "hunks": [{"oldStart":124,"oldLines":3,"newStart":124,"newLines":3,"lines":[" before","-old","+new 雪"," after"]},
                      {"oldStart":900,"oldLines":1,"newStart":900,"newLines":1,
                       "lines":["-tail","\\ No newline at end of file","+TAIL","\\ No newline at end of file"]}]})
        self.assertEqual(added["hunks"][0], {"oldStart":0,"oldLines":0,"newStart":1,"newLines":1,"lines":["+hello"]})
        self.assertEqual((deleted["status"],deleted["hunks"][0]["newStart"],deleted["hunks"][0]["newLines"]), ("deleted",0,0))

    def test_recorded_rename_and_empty_file(self):
        state = opencode_fixture()["state"]
        for status, patch in (("modified",""),("added","Index: new\n===\n--- new\n+++ new\n"),("deleted","--- new\n+++ new\n")):
            state["metadata"]["files"] = [{"file":"new", "oldPath":"old", "status":status, "patch":patch, "additions":0,"deletions":0}]
            d = opencode_diff("apply_patch", state)
            self.assertEqual(d["files"], [{"path":"new","oldPath":"old","status":status,"additions":0,"deletions":0,"hunks":[]}])
            self.assertTrue(d["complete"])

    def test_write_is_not_an_empty_old_file_claim(self):
        d = opencode_diff("write", {"status":"completed","input":{"path":"file","content":"new 雪\n"}})
        self.assertEqual((d["source"],d["complete"],d["reason"]),
                         ("recorded-new-content",False,"new content; previous content not recorded"))
        self.assertEqual(d["files"][0], {"path":"file","status":"modified","additions":1,"deletions":0,
            "hunks":[{"oldStart":0,"oldLines":0,"newStart":1,"newLines":1,"lines":["+new 雪"]}]})

    def test_missing_pending_error_and_unrelated_tools_never_invent_applied_edits(self):
        for status in ("pending-or-missing", "error", "ambiguous"):
            d = claude_diff("Edit", claude_fixture()["input"], claude_fixture()["toolUseResult"], status)
            self.assertEqual((d["files"], d["complete"], d["source"]), ([], False, "unavailable"))
        for status in ("pending", "streaming", "running", "error", None):
            state = {**opencode_fixture()["state"], "status":status}
            self.assertEqual(opencode_diff("patch", state)["files"], [])
        self.assertIsNone(claude_diff("Read", {}, None, "stored"))
        self.assertIsNone(opencode_diff("workflowy_node", {}))
        for name in ("Bash", "shell"):
            self.assertEqual(claude_diff(name, {}, None, "stored"), {"source":"unavailable","complete":False,"files":[],"reason":SHELL_REASON})
            self.assertEqual(opencode_diff(name, {}), claude_diff(name, {}, None, "stored"))

    def test_malformed_optional_metadata_and_unknown_aggregate_multiedit(self):
        for meta in (None, [], {"files":[]}, {"filePath":"p","structuredPatch":"bad"},
                     {"filePath":"p","structuredPatch":[{"oldStart":True}]}):
            d = claude_diff("MultiEdit", {}, meta, "stored")
            self.assertFalse(d["complete"]); self.assertTrue(d["reason"])
        for state in (None, {}, {"status":"completed","metadata":[]},
                      {"status":"completed","metadata":{"files":[None,{}, {"file":"p","status":"modified","patch":False}]}}):
            d = opencode_diff("edit", state)
            self.assertFalse(d["complete"]); self.assertTrue(d["reason"])
        text, root = parse_record(b'{"metadata":{"structuredPatch":[],"structuredPatch":[1]}}')
        span = root.members()["metadata"]
        self.assertIsNone(recorded_value(text, span))
        self.assertFalse(claude_diff("Edit", {}, recorded_value(text, span), "stored")["complete"])

    def test_mixed_good_bad_files_and_count_mismatch_retain_good_evidence(self):
        state = opencode_fixture()["state"]
        state["metadata"]["files"] += [{"file":"broken","status":"modified","patch":"@@ bad @@\n+bad\n","additions":1,"deletions":0}, None]
        state["metadata"]["files"][0]["additions"] = 20
        d = opencode_diff("patch", state)
        self.assertFalse(d["complete"])
        self.assertEqual(len(d["files"]),3)
        self.assertEqual(d["files"][0]["additions"],20)
        self.assertIn("disagrees",d["reason"]); self.assertIn("header",d["reason"])
        self.assertEqual(d["files"][0]["hunks"][0]["oldStart"],124)

    def test_malformed_counts_prefixes_and_headers_are_incomplete(self):
        for patch in ("@@ -5,2 +5 @@\n-old\n+new\n", "@@ -x +5 @@\n+a", "@@ -1 +1 @@\nbad\n", "@@ -0 +1 @@\n-old\n+new\n", "@@ -"+"1"*5000+" +5 @@\n-old\n+new\n"):
            state = opencode_fixture()["state"]
            state["metadata"]["files"][0]["patch"] = patch
            d = opencode_diff("edit",state)
            self.assertFalse(d["complete"])
            self.assertEqual(len(d["files"]),2)

    def test_large_diff_has_no_separate_ceiling_or_crop(self):
        count = 10001
        meta = {"type":"create", "filePath":"big", "content":"雪\n"*count}
        d = claude_diff("Write", {}, meta, "stored")
        self.assertTrue(d["complete"])
        self.assertEqual((d["files"][0]["additions"],len(d["files"][0]["hunks"][0]["lines"])),(count,count))


if __name__ == "__main__":
    unittest.main()
