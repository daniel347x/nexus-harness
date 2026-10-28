"""Independent stored-history layouts, never controller promotion or live readiness."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import claude_reader as cr

FIXTURES = Path(__file__).parent / "fixtures/claude"
SESSION = "00000000-0000-4000-8000-000000000001"


class VersionTests(unittest.TestCase):
    def page(self, rows):
        self.path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        self.reader = cr.ClaudeReader([{"id": "native", "root": self.temp.name, "native": True}])
        session = self.reader.sessions()["sessions"][0]
        self.assertFalse(session["capabilities"]["nativeInput"])
        return self.reader.events(sessionRef=session["sessionRef"], scopeRef=session["scopes"][0]["scopeRef"])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / (SESSION + ".jsonl")

    def test_native_296_independent_history_context_and_raw_pair(self):
        self.check_fixture("native-2.1.296.jsonl", "2.1.296", 8)

    def test_actual_headless_299_independent_history_context_and_raw_pair(self):
        self.check_fixture("headless-2.1.299.jsonl", "2.1.299", 5)

    def test_fixture_provenance_hashes_and_separate_exact_versions(self):
        manifest = json.loads((FIXTURES / "provenance.json").read_text())
        for name, version in (("native-2.1.296.jsonl", "2.1.296"), ("headless-2.1.299.jsonl", "2.1.299")):
            with self.subTest(fixture=name):
                evidence = manifest[name]
                self.assertEqual(evidence["cliVersion"], version)
                self.assertEqual(hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest(), evidence["fixtureSha256"])
                self.assertEqual(len(evidence["sourceSha256"]), 64)
                self.assertIn("NOT original bytes", evidence["sanitization"])
        self.assertNotEqual(manifest["native-2.1.296.jsonl"]["sourceSha256"], manifest["headless-2.1.299.jsonl"]["sourceSha256"])

    def test_no_private_version_promotion_or_checkpoint_interface_remains(self):
        reader = cr.ClaudeReader([])
        for name in ("qualified_sources", "managed_sources", "owned_scope", "probe_version",
                     "qualify_native_source", "adopt_qualified_source", "adopt_managed_source",
                     "restrict_owned_scope", "operation_checkpoint", "native_checkpoint", "native_fixture"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(reader, name))
        with self.assertRaises(TypeError):
            cr.ClaudeReader([], probe_version="2.1.300")

    def check_fixture(self, name, version, count):
        rows = [json.loads(line) for line in (FIXTURES / name).read_text().splitlines()]
        self.assertEqual({r["version"] for r in rows}, {version})
        page = self.page(rows)
        self.assertEqual(page["diagnostics"], [])
        self.assertEqual(len(page["events"]), count)
        self.assertFalse(page["hasMore"])
        self.assertEqual(page["events"][0]["text"], "Sanitized user 🌈\n\nTail")
        context = self.reader.context(page["targetRef"], 4)
        self.assertEqual(context["text"], "[USER]\nSanitized user 🌈\n\nTail\n\n---\n\n[ASSISTANT]\nSanitized assistant answer")
        calls = [c for event in page["events"] for c in event["calls"]]
        self.assertEqual(len(calls), 1)
        snapshot = self.reader.snapshot(calls[0]["callRef"], page["fileEpoch"])
        self.assertEqual(snapshot["status"], "stored")
        self.assertEqual(snapshot["input"], '{"verb": "ping", "params": {}}')
        self.assertEqual(snapshot["output"], "Sanitized stored result\n\tTail")
        self.assertEqual(snapshot["outputBytes"], len(snapshot["output"].encode()))
        self.assertEqual(len({r["uuid"] for r in page["events"]}), count)

    def test_headless_origin_absent_requires_all_observed_external_sdk_fields_on_any_version(self):
        row = json.loads((FIXTURES / "headless-2.1.299.jsonl").read_text().splitlines()[0])
        # The provenance fields are the capability, not the release: the same SDK prompt is
        # Dan's (or a Conductor's) message on 2.1.300 and on a release nobody has seen yet.
        for version in ("2.1.299", "2.1.300", "2.2.0", "3.0.0-beta.1"):
            with self.subTest(version=version):
                page = self.page([{**row, "version": version}])
                self.assertEqual(page["diagnostics"], [])
                self.assertEqual(page["events"][0]["text"], "Sanitized user 🌈\n\nTail")
                self.assertEqual(self.reader.context(page["targetRef"], 1)["text"], "[USER]\nSanitized user 🌈\n\nTail")
        for key, value in (("promptSource", "queued"),
                           ("turnOrigin", "unknown"), ("entrypoint", "cli"), ("userType", "internal"),
                           ("origin", {"kind": "unknown"}), ("isMeta", True),
                           ("sourceToolAssistantUUID", row["uuid"])):
            with self.subTest(key=key, value=value):
                page = self.page([{**row, key: value}])
                self.assertEqual(self.reader.context(page["targetRef"], 1)["text"], "")
        for key in ("promptSource", "turnOrigin", "entrypoint", "userType"):
            with self.subTest(missing=key):
                changed = {k: v for k, v in row.items() if k != key}
                page = self.page([changed])
                self.assertEqual(self.reader.context(page["targetRef"], 1)["text"], "")

    def test_any_release_is_read_by_shape_and_only_the_departing_record_is_refused(self):
        rows = [json.loads(line) for line in (FIXTURES / "native-2.1.296.jsonl").read_text().splitlines()]
        expected = "[USER]\nSanitized user 🌈\n\nTail\n\n---\n\n[ASSISTANT]\nSanitized assistant answer"
        # An ordinary Claude Code update (2.1.300 is installed) or a future release: the same
        # history, prose and Refine context, no diagnostic. Never a version allowlist.
        for version in ("2.1.297", "2.1.300", "2.2.0", "3.0.0", "3.0.0-beta.1"):
            with self.subTest(version=version):
                page = self.page([{**r, "version": version} for r in rows])
                self.assertEqual(page["diagnostics"], [])
                self.assertTrue(all(e["status"] in ("stored", "metadata") for e in page["events"]))
                self.assertEqual(self.reader.context(page["targetRef"], 1)["text"], expected)
        # A missing or malformed version string is refused, per record, visibly.
        for version in ("future", "2.1", "", "2.1.300\nforged"):
            with self.subTest(malformed=version):
                page = self.page([{**r, "version": version} for r in rows])
                self.assertTrue(page["diagnostics"])
                self.assertTrue(all(d["status"].startswith("unsupported-version:") for d in page["diagnostics"]))
                self.assertEqual(self.reader.context(page["targetRef"], 1)["text"], "")
        page = self.page([{k: v for k, v in r.items() if k != "version"} for r in rows])
        self.assertIn("unsupported-version:missing", str(page["diagnostics"]))
        # A future release's new block kind refuses that record alone; the rest still reads.
        changed = {**rows[0], "version": "3.0.0",
                   "message": {"role": "user", "content": [{"type": "future-prose", "text": "unknown"}]}}
        page = self.page([changed])
        self.assertIn("unsupported-block", str(page["diagnostics"]))
        self.assertEqual(self.reader.context(page["targetRef"], 1)["text"], "")
        page = self.page([changed] + [{**r, "version": "3.0.0"} for r in rows[1:]])
        self.assertEqual([d["status"] for d in page["diagnostics"]], ["unsupported-block:future-prose"])
        self.assertIn("[ASSISTANT]\nSanitized assistant answer", self.reader.context(page["targetRef"], 1)["text"])
