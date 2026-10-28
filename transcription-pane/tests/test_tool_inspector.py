"""Persistent inspector contract: offline unit tests + ephemeral authenticated HTTP fixtures.

Never uses live OpenCode, installed service credentials, browser actions or paid requests.
"""
import copy
import email.message
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import browser_scratch as scratch_paths

spec = importlib.util.spec_from_file_location("tool_inspector_pane", pathlib.Path(__file__).resolve().parents[1] / "server.py")
pane = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pane)

ORIGIN = "http://127.0.0.1:9899"
PANE_ORIGIN = "http://127.0.0.1:9911"
SESSION_PATH = "/api/session/ses_fixture"
MESSAGE_PATH = SESSION_PATH + "/message/msg_fixture"


def selection(**extra):
    result = {
        "origin": ORIGIN, "instanceId": "instance-fixture", "sessionId": "ses_fixture",
        "messageId": "msg_fixture", "partId": "part_fixture", "selectorId": "selector_fixture",
        "sequence": 1, "observedAt": 1000000, "toolName": "workflowy_node",
        "sessionTitle": "selected session", "workspaceName": "offline workspace",
        "association": {"sessionCreated": 100, "directory": "/fixture/project",
                        "messageCreated": 200, "toolName": "workflowy_node", "callId": "call_fixture"},
    }
    result.update(extra)
    return result


def records():
    part = {"id": "part_fixture", "type": "tool", "name": "workflowy_node", "callID": "call_fixture",
            "state": {"status": "completed", "input": {"verb": "read", "params": {"node_handle": "fixture"}},
                      "output": "saved output", "metadata": {"provenance": "unchanged"}}}
    session = {"id": "ses_fixture", "time": {"created": 100}, "location": {"directory": "/fixture/project"}}
    message = {"id": "msg_fixture", "sessionID": "ses_fixture", "time": {"created": 200, "completed": 300},
               "content": [{"id": "other_part", "type": "text", "text": "not returned"}, part]}
    return session, message


class ScratchPathsTests(unittest.TestCase):
    """The shared fixture/browser scratch contract, without launching Chrome."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=scratch_paths.scratch_dir('fixtures'))
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name) / 'override'
        env = patch.dict(os.environ, OC_SCRATCH=str(self.root))
        env.start()
        self.addCleanup(env.stop)

    def test_external_default_override_parts_and_receipt_containment(self):
        self.assertEqual(scratch_paths.DEFAULT_ROOT.parent.name, 'oc-scratch')
        self.assertEqual(scratch_paths.scratch_root(), self.root)
        for part in ('profiles', 'receipts', 'fixtures/inspector'):
            self.assertTrue(scratch_paths.scratch_dir(part).is_dir())
        receipt = scratch_paths.receipt_dir(self.root / 'receipts' / 'check')
        self.assertTrue(receipt.is_dir())
        with self.assertRaises(ValueError):
            scratch_paths.receipt_dir(pathlib.Path(self.temp.name) / 'outside')

    def test_profile_cleanup_success_and_failure_preserve_receipts(self):
        receipt = scratch_paths.receipt_dir(self.root / 'receipts' / 'check') / 'result.json'
        receipt.write_text('{}')
        with scratch_paths.chrome_profile('chrome-check-') as profile:
            self.assertEqual(profile.parent, self.root / 'profiles')
            (profile / 'Chrome state').write_text('disposable')
        self.assertFalse(profile.exists())
        with self.assertRaisesRegex(RuntimeError, 'suite failed'):
            with scratch_paths.chrome_profile('chrome-shot-') as failed:
                raise RuntimeError('suite failed')
        self.assertFalse(failed.exists())
        self.assertEqual(receipt.read_text(), '{}')

    def test_profile_removal_retries_a_windows_lock(self):
        with patch.object(scratch_paths.shutil, 'rmtree', side_effect=[PermissionError('locked'), None]) as remove, \
                patch.object(scratch_paths.time, 'sleep') as sleep:
            self.assertTrue(scratch_paths.remove_profile(self.root / 'profiles' / 'locked'))
        self.assertEqual(remove.call_count, 2)
        sleep.assert_called_once_with(0.1)

    def test_locked_profile_is_left_for_next_sweep_without_masking_failure(self):
        with patch.object(scratch_paths.shutil, 'rmtree', side_effect=PermissionError('locked')) as remove, \
                patch.object(scratch_paths.time, 'sleep'), patch('sys.stderr', new=io.StringIO()) as stderr:
            with self.assertRaisesRegex(RuntimeError, 'original failure'):
                with scratch_paths.chrome_profile('chrome-locked-') as profile:
                    raise RuntimeError('original failure')
        self.assertEqual(remove.call_count, 6)
        self.assertTrue(profile.is_dir())
        self.assertIn('left for the next sweep', stderr.getvalue())

    def test_startup_sweep_only_removes_old_profiles(self):
        profiles = scratch_paths.scratch_dir('profiles')
        stale = profiles / 'chrome-crashed'
        fresh = profiles / 'chrome-running'
        stale.mkdir()
        fresh.mkdir()
        os.utime(stale, (1, 1))
        receipt = scratch_paths.receipt_dir(self.root / 'receipts' / 'old') / 'result.json'
        receipt.write_text('{}')
        os.utime(receipt.parent, (1, 1))
        scratch_paths.sweep_profiles()
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.is_dir())
        self.assertTrue(receipt.is_file())

    def test_all_five_runners_cleanup_profiles_on_browser_timeout(self):
        import capture_pane_shots
        import capture_phone_shots
        import run_browser_checks
        import run_browser_suite
        import run_diff_view_browser
        for runner, args in (
                (run_browser_checks, ['--receipt-dir', str(self.root / 'receipts/checks'), '--suite', 'tool-inspector']),
                (run_browser_suite, ['--receipt-dir', str(self.root / 'receipts/suite'), 'tool-inspector']),
                (capture_phone_shots, ['--out', str(self.root / 'receipts/phone'), '--shots', 'chat']),
                (capture_pane_shots, ['--out', str(self.root / 'receipts/pane'), '--shots', 'collapsed']),
                (run_diff_view_browser, [])):
            with self.subTest(runner=runner.__name__), \
                    patch('sys.argv', [runner.__name__, *args]), \
                    patch.object(runner.http.server, 'ThreadingHTTPServer') as server, \
                    patch.object(runner.threading, 'Thread'), patch.object(runner.time, 'sleep'), \
                    patch.object(runner.subprocess, 'run', side_effect=subprocess.TimeoutExpired('chrome', 1)):
                with self.assertRaises(subprocess.TimeoutExpired):
                    runner.main()
                self.assertEqual(list((self.root / 'profiles').iterdir()), [])
                server.return_value.shutdown.assert_called_once()
                server.return_value.server_close.assert_called_once()


class ClaudeMailboxTests(unittest.TestCase):
    """Native calls use the real reader, not fake OpenCode parts or a second mailbox."""
    def setUp(self):
        from claude_reader import ClaudeReader
        from test_claude_reader import SESSION, record, uid
        scratch = scratch_paths.scratch_dir('fixtures/tool-inspector')
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.path = pathlib.Path(self.temp.name) / (SESSION + ".jsonl")
        self.rows = [record(1, kind="assistant", content=[{"type":"tool_use", "id":"call", "name":"mcp__workflowy__node", "input":{"verb":"read", "params":{}}}]),
                     record(2, 1, content=[{"type":"tool_result", "tool_use_id":"call", "content":"result"}], sourceToolAssistantUUID=uid(1))]
        self.path.write_text("".join(json.dumps(r) + "\n" for r in self.rows))
        self.reader = ClaudeReader([{"id":"test", "root":self.temp.name}])
        row = self.reader.sessions()["sessions"][0]
        self.page = self.reader.events(sessionRef=row["sessionRef"], scopeRef=row["scopes"][0]["scopeRef"])
        self.box = pane.ToolMailbox()
        self.click = {"schemaVersion":1, "backend":"claude", "callRef":self.page["events"][0]["calls"][0]["callRef"],
                      "fileEpoch":self.page["fileEpoch"], "selectorId":"native-page", "sequence":1, "observedAt":1000000}
        clock = patch.object(pane.time, "time", return_value=1000)
        clock.start(); self.addCleanup(clock.stop)

    def read(self):
        mail = self.box.snapshot()
        return self.box.read_record(mail["epoch"], mail["revision"], claude_reader=self.reader)

    def test_native_click_delayed_before_server_validation_cannot_reopen_close(self):
        self.box.accept_claude(self.click, self.reader)
        queued = {**self.click, "sequence":2, "observedAt":1000001}
        with patch.object(pane.time, "time", return_value=1000.002):
            self.box.clear(self.box.epoch, self.box.revision)
            with self.assertRaises(pane.ReaderError) as error:
                self.box.accept_claude(queued, self.reader)
            self.assertEqual(error.exception.code, 409)
            self.assertIsNone(self.box.selection)
            self.box.accept_claude({**queued, "sequence":3, "observedAt":1000003}, self.reader)
            self.assertEqual(self.box.selection["sequence"], 3)

    def test_native_click_and_legacy_opencode_share_one_revision_and_watermark(self):
        ack = self.box.accept_claude(self.click, self.reader)
        self.assertEqual(ack["revision"], 1)
        native = self.read()
        self.assertEqual(native["backend"], "claude")
        self.assertNotIn("part", native)
        self.assertEqual(native["snapshot"]["output"], "result")
        self.assertEqual(native["identity"], ack["selection"]["identity"])
        self.assertEqual(native["identity"]["blockIndex"], 0)
        self.assertNotIn("sessionId", native["selection"])
        self.assertNotIn("input", json.dumps(self.box.snapshot()))
        self.box.accept(selection(observedAt=1000001))
        self.assertEqual(self.box.revision, 2)
        with self.assertRaises(pane.ReaderError):
            self.box.accept_claude(self.click, self.reader)
        self.box.accept_claude({**self.click, "observedAt":1000002, "sequence":2}, self.reader)
        self.assertEqual(self.box.revision, 3)
        self.box.accept_claude({**self.click, "observedAt":1000002, "sequence":3}, self.reader)
        self.assertEqual(self.box.revision, 4)  # same deliberate call is still a new click

    def test_native_schema_refs_and_stale_page_never_commit(self):
        for changes in ({"schemaVersion":True}, {"backend":"opencode"}, {"input":"not allowed"},
                        {"fileEpoch":"wrong"}, {"callRef":self.page["targetRef"]},
                        {"sequence":True}, {"observedAt":1006000}, {"selectorId":"bad/page"}):
            with self.subTest(changes=changes), self.assertRaises(pane.ReaderError):
                self.box.accept_claude({**self.click, **changes}, self.reader)
        self.assertEqual(self.box.revision, 0)
        self.path.write_text(json.dumps(self.rows[0]).replace('"read"', '"edit"') + "\n")
        with self.assertRaises(pane.ReaderError):
            self.box.accept_claude(self.click, self.reader)
        self.assertIsNone(self.box.selection)

    def test_close_or_another_backend_inside_validation_prevents_late_commit(self):
        for replacement in ("close", "opencode"):
            with self.subTest(replacement=replacement):
                self.box = pane.ToolMailbox(); self.box.accept_claude(self.click, self.reader)
                original = self.reader._read
                def during(index, record):
                    value = original(index, record)
                    if replacement == "close": self.box.clear(self.box.epoch, self.box.revision)
                    else: self.box.accept(selection(observedAt=1000002))
                    return value
                with patch.object(self.reader, "_read", side_effect=during), self.assertRaises(pane.ToolError):
                    self.box.accept_claude({**self.click, "sequence":2, "observedAt":1000001}, self.reader)
                self.assertEqual(self.box.revision, 2)
                self.assertEqual(self.box.selection is None, replacement == "close")
                if self.box.selection: self.assertEqual(self.box.selection["sessionId"], "ses_fixture")

    def test_newer_native_validation_wins_and_close_cannot_be_replayed(self):
        original = self.reader._read
        def during(index, record):
            value = original(index, record)
            with patch.object(self.reader, "_read", side_effect=original):
                self.box.accept_claude({**self.click, "observedAt":1000001, "sequence":2}, self.reader)
            return value
        with patch.object(self.reader, "_read", side_effect=during), self.assertRaises(pane.ToolError):
            self.box.accept_claude(self.click, self.reader)
        self.assertEqual(self.box.selection["sequence"], 2)
        self.box.clear(self.box.epoch, self.box.revision)
        with self.assertRaises(pane.ReaderError):
            self.box.accept_claude({**self.click, "observedAt":1000001, "sequence":2}, self.reader)
        self.assertIsNone(self.box.selection)

    def test_delayed_snapshot_close_replacement_and_source_rewrite_are_rejected(self):
        for action in ("close", "opencode", "rewrite"):
            self.box = pane.ToolMailbox(); self.box.accept_claude(self.click, self.reader)
            original = self.reader.snapshot
            def during(*args):
                value = original(*args)
                if action == "close": self.box.clear(self.box.epoch, self.box.revision)
                elif action == "opencode": self.box.accept(selection(observedAt=1000001))
                else: self.path.write_text(json.dumps(self.rows[0]).replace('"read"', '"edit"') + "\n")
                return value
            with self.subTest(action=action), patch.object(self.reader, "snapshot", side_effect=during), self.assertRaises((pane.ToolError, pane.ReaderError)):
                self.read()
            if action == "close": self.assertIsNone(self.box.selection)
            if action == "opencode": self.assertEqual(self.box.selection["sessionId"], "ses_fixture")

    def test_pending_is_unknown_not_live_empty_or_fake_raw(self):
        self.path.write_text(json.dumps(self.rows[0]) + "\n")
        from claude_reader import ClaudeReader
        self.reader = ClaudeReader([{"id":"test", "root":self.temp.name}])
        row = self.reader.sessions()["sessions"][0]
        page = self.reader.events(sessionRef=row["sessionRef"], scopeRef=row["scopes"][0]["scopeRef"])
        self.click.update(callRef=page["events"][0]["calls"][0]["callRef"], fileEpoch=page["fileEpoch"])
        self.box.accept_claude(self.click, self.reader)
        raw = self.read()["snapshot"]
        self.assertEqual(raw["status"], "pending-or-missing")
        self.assertIsNone(raw["output"]); self.assertIsNone(raw["outputBytes"])
        self.assertNotIn("live", raw)

    def test_real_read_lock_serializes_native_reads_but_not_close(self):
        self.box.accept_claude(self.click, self.reader)
        entered, release, second = threading.Event(), threading.Event(), threading.Event()
        original = self.reader.snapshot
        def blocked(*args):
            entered.set(); release.wait(timeout=3); return original(*args)
        errors = []
        def read(mark=None):
            try:
                mail = self.box.snapshot()
                if mark: mark.set()
                self.box.read_record(mail["epoch"], mail["revision"], claude_reader=self.reader)
            except (pane.ToolError, pane.ReaderError) as e: errors.append(e.code)
        with patch.object(self.reader, "snapshot", side_effect=blocked):
            first = threading.Thread(target=read); first.start(); self.assertTrue(entered.wait(2))
            other = threading.Thread(target=read, args=(second,)); other.start(); self.assertTrue(second.wait(2))
            self.box.clear(self.box.epoch, self.box.revision)  # no waiting for blocked reader
            release.set(); first.join(3); other.join(3)
        self.assertFalse(first.is_alive() or other.is_alive())
        self.assertEqual(sorted(errors), [409, 409])  # both captured revision1 before Close

    def test_all_three_admissions_share_watermark_revision_and_unchanged_read(self):
        from test_opencode_conversation import SID, Wire
        from opencode_conversation import OpenCodeConversation
        wire=Wire(); reader=OpenCodeConversation(wire.request)
        page=reader.events(id=SID,fromSeq=0)
        call=next(r["calls"][0] for r in page["events"] if r["calls"])
        click={"schemaVersion":1,"backend":"opencode","id":SID,"messageId":call["messageId"],
               "partIndex":call["partIndex"],"callRef":call["callRef"],"fileEpoch":page["fileEpoch"],
               "selectorId":"oc-viewer","sequence":1,"observedAt":1000001}
        self.box.accept_claude(self.click,self.reader)
        self.box.accept_opencode(click,reader)
        self.assertEqual(self.box.revision,2)
        self.assertEqual(self.box.selection["backend"],"opencode")
        self.assertIsNone(self.box.selection["association"]["callId"])
        def request(path):
            data=wire.request(path,10,raw=True).decode()
            body=pane.wire_members(data)["data"]
            return pane.WireRecord(json.loads(body),body) if "/message/" in path else json.loads(body)
        read=self.box.read_record(self.box.epoch,self.box.revision,request)
        self.assertEqual(read["part"]["id"],"prt_real")
        self.box.accept(selection(observedAt=1000002))
        self.assertEqual(self.box.revision,3)
        with self.assertRaises(pane.ReaderError): self.box.accept_opencode(click,reader)
        self.box.accept_opencode({**click,"observedAt":1000003,"sequence":2},reader)
        self.assertEqual(self.box.revision,4)
        with self.assertRaises(pane.ReaderError): self.box.accept_claude(self.click,self.reader)
        self.box.accept_claude({**self.click,"observedAt":1000004,"sequence":2},self.reader)
        self.assertEqual(self.box.revision,5)
        self.assertEqual(self.box.selection["backend"],"claude")

    def test_opencode_close_newer_click_during_validation_and_cleared_at(self):
        from test_opencode_conversation import SID, Wire
        from opencode_conversation import OpenCodeConversation
        for action in ("close","codenomad","claude","opencode"):
            with self.subTest(action=action):
                wire=Wire(); reader=OpenCodeConversation(wire.request)
                page=reader.events(id=SID,fromSeq=0); call=next(r["calls"][0] for r in page["events"] if r["calls"])
                click={"schemaVersion":1,"backend":"opencode","id":SID,"messageId":call["messageId"],
                       "partIndex":call["partIndex"],"callRef":call["callRef"],"fileEpoch":page["fileEpoch"],
                       "selectorId":"oc-viewer","sequence":1,"observedAt":1000001}
                self.box=pane.ToolMailbox(); self.box.accept_claude(self.click,self.reader)
                original=reader.witness
                def during(**kwargs):
                    value=original(**kwargs)
                    if action=="close": self.box.clear(self.box.epoch,self.box.revision)
                    elif action=="codenomad": self.box.accept(selection(observedAt=1000002))
                    elif action=="claude": self.box.accept_claude({**self.click,"observedAt":1000002,"sequence":2},self.reader)
                    else:
                        with patch.object(reader,"witness",side_effect=original):
                            self.box.accept_opencode({**click,"observedAt":1000002,"sequence":2},reader)
                    return value
                with patch.object(reader,"witness",side_effect=during), self.assertRaises(pane.ToolError):
                    self.box.accept_opencode(click,reader)
                self.assertEqual(self.box.revision,2)
                if action=="close": self.assertIsNone(self.box.selection)
                else: self.assertEqual(self.box.selection["observedAt"],1000002)
        with patch.object(pane.time,"time",return_value=1000.004):
            self.box.clear(self.box.epoch,self.box.revision)
            with self.assertRaises(pane.ReaderError): self.box.accept_opencode({**click,"observedAt":1000003},reader)
            self.assertIsNone(self.box.selection)


class MailboxTests(unittest.TestCase):
    def test_mailbox_normalizes_diff_beside_part_and_keeps_optional_errors_local(self):
        from test_tool_diff import opencode_fixture
        from tool_diff import opencode_diff
        f = opencode_fixture()
        part = self.message['content'][1]
        part.update(name='patch',state=f['state'])
        chosen = selection(toolName='patch')
        chosen['association']['toolName'] = 'patch'
        self.box.accept(chosen)
        for malformed in (False,True):
            if malformed: part['state']['metadata']['files'].append({'file':'bad','patch':'@@ wrong'})
            before = copy.deepcopy(part)
            result = self.read()
            self.assertEqual(result['part'],before)
            self.assertEqual(result.wire,json.dumps(before,ensure_ascii=False,separators=(',',':')))
            self.assertEqual(result['diff'],opencode_diff('patch',part['state']))
            self.assertEqual(result['diff']['complete'],not malformed)
            self.assertEqual(result['diff']['files'][0]['hunks'][0]['oldStart'],124)
        self.assertEqual(self.calls,[SESSION_PATH,MESSAGE_PATH,MESSAGE_PATH])

    def setUp(self):
        clock = patch.object(pane.time, "time", return_value=1000)
        clock.start()
        self.addCleanup(clock.stop)
        self.box = pane.ToolMailbox()
        self.session, self.message = records()
        self.calls = []
        self.hook = lambda path: None

    def request(self, path):
        self.calls.append(path)
        self.hook(path)
        if path == SESSION_PATH:
            return copy.deepcopy(self.session)
        if path == MESSAGE_PATH:
            return copy.deepcopy(self.message)
        self.fail("unexpected path " + path)

    def read(self):
        snap = self.box.snapshot()
        return self.box.read_record(snap["epoch"], snap["revision"], self.request)

    def expect_error(self, code, function, *args):
        with self.assertRaises(pane.ToolError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, code)
        return str(caught.exception)

    def test_epoch_and_selection_are_memory_only_separate_from_active_report(self):
        self.assertNotEqual(self.box.epoch, pane.ToolMailbox().epoch)
        self.assertEqual(self.box.snapshot()["selection"], None)
        self.box.accept(selection())
        before = self.box.snapshot()
        pane.accept_report({"sessionId": "ses_send_target", "title": "Send is unrelated"})
        self.assertEqual(self.box.snapshot(), before)
        self.assertNotIn("part", before)
        self.assertNotIn("state", json.dumps(before))

    def test_every_deliberate_same_identity_click_advances_revision(self):
        self.box.accept(selection())
        self.box.accept(selection(sequence=2))
        self.box.accept(selection(observedAt=1000001, sequence=3))
        self.assertEqual(self.box.revision, 3)

    def test_ordering_equal_timestamp_and_different_selectors(self):
        self.box.accept(selection())
        for change in ({}, {"sequence": 0}, {"sequence": 1, "observedAt": 999999},
                       {"sequence": 9, "selectorId": "another_selector"}):
            expected = 400 if change.get("sequence") == 0 else 409
            self.expect_error(expected, self.box.accept, selection(**change))
        self.box.accept(selection(selectorId="another_selector", observedAt=1000001))
        self.assertEqual(self.box.revision, 2)
        self.expect_error(409, self.box.accept, selection(sequence=999))
        self.assertEqual(self.box.snapshot()["selection"]["selectorId"], "another_selector")

    def test_close_compare_and_clear_retains_watermark_and_next_click_reopens(self):
        self.box.accept(selection())
        self.expect_error(409, self.box.clear, "old-epoch", 1)
        self.expect_error(409, self.box.clear, self.box.epoch, 0)
        snap = self.box.clear(self.box.epoch, 1)
        self.assertEqual(snap["revision"], 2)
        self.assertIsNone(snap["selection"])
        self.expect_error(409, self.box.accept, selection())
        self.expect_error(404, self.box.read_record, self.box.epoch, 2, self.request)
        self.assertFalse(self.calls)
        self.box.accept(selection(sequence=2))
        self.assertEqual(self.box.revision, 3)
        self.assertIsNotNone(self.box.selection)

    def test_snapshot_and_accept_have_no_mutable_aliases(self):
        original = selection()
        self.box.accept(original)
        original["association"]["directory"] = "wrong"
        snap = self.box.snapshot()
        snap["selection"]["association"]["directory"] = "also wrong"
        self.assertEqual(self.box.snapshot()["selection"]["association"]["directory"], "/fixture/project")

    def test_safe_ids_observation_and_payload_schema(self):
        for field in ("instanceId", "sessionId", "messageId", "partId", "selectorId"):
            for value in (None, "", "x" * 129, "../bad", "bad/segment", "a?b", "a%2Fb", "x\n", "汉字", True):
                with self.subTest(field=field, value=value):
                    self.expect_error(400, self.box.accept, selection(**{field: value}))
        for change in ({"sessionId": "not_a_session"}, {"sessionId": "ses_"}, {"origin": "http://localhost:9899/"},
                       {"sequence": True}, {"sequence": 1.5}, {"observedAt": True},
                       {"observedAt": 1006000}, {"observedAt": 0}, {"state": {"input": "forbidden"}},
                       {"sourceError": ""}, {"toolName": ""}, {"workspaceName": "x" * 4097},
                       {"sessionTitle": "\ud800"}):
            with self.subTest(change=change):
                self.expect_error(400, self.box.accept, selection(**change))
        self.assertEqual(self.box.revision, 0)

    def test_selection_serialized_bytes_bound_not_characters(self):
        # Individually valid text fields but >32 KiB UTF-8 together.
        d = selection(sessionTitle="汉" * 4096, workspaceName="汉" * 4096, sourceError="汉" * 4096)
        self.expect_error(413, self.box.accept, d)

    def test_witness_schema_requires_immutable_fields(self):
        for changed in ({"sessionCreated": True}, {"sessionCreated": 1.5}, {"messageCreated": 0},
                        {"directory": ""}, {"callId": "bad/id"}, {"toolName": ""}, {"url": "http://evil"}):
            d = selection()
            d["association"].update(changed)
            self.expect_error(400, self.box.accept, d)
        d = selection()
        del d["association"]["callId"]
        self.expect_error(400, self.box.accept, d)

    def test_missing_witness_or_source_error_never_falls_back_to_send_target(self):
        for changed in ({"association": None}, {"sourceError": "remote backend cannot be associated"}):
            self.box = pane.ToolMailbox()
            self.box.accept(selection(**changed))
            self.expect_error(409, self.read)
            self.assertFalse(self.calls)

    def test_selected_part_only_full_provenance_and_session_check_once_per_revision(self):
        self.box.accept(selection())
        first = self.read()
        second = self.read()
        self.assertEqual(self.calls, [SESSION_PATH, MESSAGE_PATH, MESSAGE_PATH])
        self.assertEqual(first["part"], self.message["content"][1])
        self.assertEqual(first["selection"], selection())
        self.assertEqual(first["fetchedAt"], 1000000)
        self.assertTrue(second["messageCompleted"])
        self.assertNotIn("not returned", json.dumps(first))
        self.box.accept(selection(sequence=2))
        self.read()
        self.assertEqual(self.calls.count(SESSION_PATH), 2)

    def test_association_failure_is_cached_only_for_this_revision(self):
        self.box.accept(selection())
        self.session["location"]["directory"] = "/another/project"
        self.expect_error(409, self.read)
        self.expect_error(409, self.read)
        self.assertEqual(self.calls, [SESSION_PATH])
        self.box.accept(selection(sequence=2))
        self.session["location"]["directory"] = "/fixture/project"
        self.read()
        self.assertEqual(self.calls, [SESSION_PATH, SESSION_PATH, MESSAGE_PATH])

    def test_transient_association_outage_can_recover_on_refresh(self):
        self.box.accept(selection())
        def offline(path):
            if path == SESSION_PATH:
                raise pane.ToolError(502, "offline")
        self.hook = offline
        self.expect_error(502, self.read)
        self.assertIsNone(self.box.association_cache)
        self.hook = lambda path: None
        self.assertEqual(self.read()["part"], self.message["content"][1])
        self.assertEqual(self.calls, [SESSION_PATH, SESSION_PATH, MESSAGE_PATH])

    def test_all_session_witness_mismatches_refuse_message_read(self):
        for change in ({"id": "ses_other"}, {"time": {"created": 101}}, {"time": None},
                       {"time": {"created": True}}, {"location": None},
                       {"location": {"directory": "/other"}}):
            self.box = pane.ToolMailbox()
            self.box.accept(selection())
            self.session, self.message = records()
            self.session.update(change)
            self.calls = []
            self.expect_error(409, self.read)
            self.expect_error(409, self.read)
            self.assertEqual(self.calls, [SESSION_PATH])

    def test_message_identity_content_part_and_tool_witness_mismatches(self):
        changes = [("id", "msg_other"), ("sessionID", "ses_other"), ("sessionID", None),
                   ("time", {"created": 201}), ("time", None), ("content", None)]
        for key, value in changes:
            self.box = pane.ToolMailbox()
            self.box.accept(selection())
            self.session, self.message = records()
            self.message[key] = value
            self.expect_error(409, self.read)
        for key, value in (("type", "text"), ("name", "another_tool"), ("callID", "another_call")):
            self.box = pane.ToolMailbox()
            self.box.accept(selection())
            self.session, self.message = records()
            self.message["content"][1][key] = value
            self.expect_error(409, self.read)

    def test_missing_or_duplicate_part_is_not_guessed(self):
        self.box.accept(selection())
        self.message["content"] = []
        self.expect_error(404, self.read)
        self.session, self.message = records()
        self.message["content"].append(copy.deepcopy(self.message["content"][1]))
        self.expect_error(409, self.read)

    def test_optional_session_id_and_null_call_id_are_valid(self):
        d = selection(toolName=None)
        d["association"]["callId"] = None
        self.box.accept(d)
        del self.message["sessionID"]
        del self.message["content"][1]["callID"]
        self.assertEqual(self.read()["part"]["name"], "workflowy_node")

    def test_stale_before_read_never_fetches(self):
        self.box.accept(selection())
        self.expect_error(409, self.box.read_record, "old-epoch", 1, self.request)
        self.expect_error(409, self.box.read_record, self.box.epoch, 0, self.request)
        self.assertFalse(self.calls)

    def test_stale_during_session_or_message_fetch_discards_result_and_cache(self):
        for point in (SESSION_PATH, MESSAGE_PATH):
            self.box = pane.ToolMailbox()
            self.box.accept(selection())
            def change(path):
                if path == point:
                    self.box.accept(selection(sequence=2))
            self.hook = change
            self.expect_error(409, self.read)
            self.assertIsNone(self.box.association_cache)
            self.hook = lambda path: None
            self.read()

    def test_close_during_read_does_not_block_and_stale_error_wins(self):
        self.box.accept(selection())
        def close(path):
            if path == MESSAGE_PATH:
                self.box.clear(self.box.epoch, 1)
                raise pane.ToolError(502, "offline")
        self.hook = close
        self.assertIn("stale", self.expect_error(409, self.read))
        self.assertIsNone(self.box.selection)

    def test_running_pending_empty_long_unicode_and_saved_stub_are_unmodified(self):
        self.box.accept(selection())
        states = [{"status": "pending", "input": ""},
                  {"status": "streaming", "input": '{"partial"'},
                  {"status": "running", "input": {"text": "x" * 12000 + "TAIL"}},
                  {"status": "completed", "input": {}, "output": ""},
                  {"status": "completed", "input": {"text": "汉字 👩🏽‍💻 e\u0301\n" * 15000},
                   "output": "🌈\n" * 15000 + "TAIL"},
                  {"status": "completed", "input": {}, "output": "Output saved to file /fixture/original.txt (stub)"}]
        for state in states:
            with self.subTest(status=state["status"]):
                self.message["content"][1]["state"] = state
                self.message["time"].pop("completed", None)
                result = self.read()
                self.assertEqual(result["part"]["state"], state)
                self.assertFalse(result["messageCompleted"])

    def test_selected_part_size_fail_not_crop(self):
        self.box.accept(selection())
        self.message["content"][1]["state"]["output"] = "x" * pane.TOOL_PART_LIMIT
        self.assertIn("not cropped", self.expect_error(413, self.read))
        self.message["content"][1]["state"]["output"] = "汉" * (pane.TOOL_PART_LIMIT // 3 + 1)
        self.expect_error(413, self.read)


class InspectorHTTPTests(unittest.TestCase):
    def test_final_mailbox_wire_bound_includes_diff_and_preserves_part_splice(self):
        # Exercise the serializer's existing 16 MiB allowance using a small injected
        # threshold, not a new production limit or an expensive fixture body.
        part = self.message['content'][1]
        wire = json.dumps(part,separators=(',',':'))
        held = pane.WireRecord({'selection':{'backend':'opencode'},'part':part,'diff':{'reason':'雪'*100}},wire)
        sent = []
        fake = type('Capture',(),{'_send':lambda _,code,body,**kw:sent.append((code,body))})()
        pane.Handler._json(fake,200,held)
        self.assertEqual(pane.wire_members(sent[0][1].decode())['part'],wire)
        size = len(sent[0][1])
        with patch.object(pane,'TOOL_UPSTREAM_LIMIT',size): pane.Handler._json(fake,200,held)
        with patch.object(pane,'TOOL_UPSTREAM_LIMIT',size-1), self.assertRaises(pane.ToolError) as caught:
            pane.Handler._json(fake,200,held)
        self.assertEqual(caught.exception.code,413)
        self.assertIn('not cropped',str(caught.exception))

    @classmethod
    def setUpClass(cls):
        cls.received = []
        cls.responses = {}
        cls.block_path = None
        cls.entered = threading.Event()
        cls.release = threading.Event()

        class OfflineOpenCode(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                cls.received.append((self.path, self.headers.get("Authorization")))
                if self.path == cls.block_path:
                    cls.entered.set()
                    cls.release.wait(timeout=5)
                code, body, headers = cls.responses.get(self.path, (404, b'{"error":"missing"}', {}))
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # a bounded pane read intentionally closes oversized responses

        def start(handler):
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            def cleanup():
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
            cls.addClassCleanup(cleanup)
            return f"http://127.0.0.1:{server.server_port}"

        cls.upstream = start(OfflineOpenCode)
        cls.base_patch = patch.object(pane, "OPENCODE_BASE", cls.upstream)
        cls.auth_patch = patch.object(pane, "auth_header", return_value="Basic offline-inspector-fixture-only")
        cls.base_patch.start()
        cls.auth_patch.start()
        cls.addClassCleanup(cls.base_patch.stop)
        cls.addClassCleanup(cls.auth_patch.stop)
        cls.base = start(pane.Handler)

    def setUp(self):
        pane._tools = pane.ToolMailbox()
        type(self).received.clear()
        type(self).responses.clear()
        type(self).block_path = None
        type(self).entered.clear()
        type(self).release.clear()
        self.session, self.message = records()
        self.respond(SESSION_PATH, self.session)
        self.respond(MESSAGE_PATH, self.message)

    def respond(self, path, obj, code=200, headers=None, raw=None):
        body = raw if raw is not None else json.dumps({"data": obj}, ensure_ascii=False).encode("utf-8")
        type(self).responses[path] = (code, body, headers or {})

    def call(self, path, method="GET", data=None, origin=None, ctype=None, headers=None):
        h = dict(headers or {})
        if origin is not None:
            h["Origin"] = origin
        if ctype:
            h["Content-Type"] = ctype
        if isinstance(data, dict):
            data = json.dumps(data, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as e:
            response = e
        with response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None, response.headers

    def select(self, **extra):
        return self.call("/api/tool-selection", "POST", selection(**extra), ORIGIN, "text/plain;charset=UTF-8")

    def read(self, epoch=None, revision=None, origin=None):
        snap = pane._tools.snapshot()
        return self.call("/api/tool-record?epoch=" + (epoch or snap["epoch"]) + "&revision=" +
                         str(snap["revision"] if revision is None else revision), origin=origin)

    def test_http_ack_cors_is_exact_and_mailbox_record_have_none(self):
        code, ack, headers = self.select()
        self.assertEqual((code, ack), (200, {"ok": True}))
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), ORIGIN)
        self.assertEqual(headers.get("Vary"), "Origin")
        for code, result, h in (self.call("/api/tool-selection"), self.read()):
            self.assertEqual(code, 200)
            self.assertIsNone(h.get("Access-Control-Allow-Origin"))
            self.assertEqual(h.get("Cache-Control"), "no-store")
        self.assertEqual(self.received, [(SESSION_PATH, "Basic offline-inspector-fixture-only"),
                                         (MESSAGE_PATH, "Basic offline-inspector-fixture-only")])
        self.assertNotIn("offline-inspector-fixture-only", json.dumps(result))

    def test_http_origin_required_exact_and_payload_matches(self):
        for origin in (None, "null", PANE_ORIGIN, "https://evil.example", ORIGIN + "/", "http://127.0.0.1:98990"):
            code, obj, headers = self.call("/api/tool-selection", "POST", selection(), origin, "text/plain")
            self.assertEqual(code, 403)
            self.assertEqual(set(obj), {"error"})
            self.assertIsNone(headers.get("Access-Control-Allow-Origin"))
        code, _, _ = self.select(origin="http://localhost:9899")
        self.assertEqual(code, 400)  # payload origin differs from Origin header
        code, _, headers = self.call("/api/tool-selection", "POST", selection(origin="http://localhost:9899"),
                                     "http://localhost:9899", "text/plain")
        self.assertEqual(code, 200)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "http://localhost:9899")

    def test_http_content_type_json_and_size_validation(self):
        for ctype in ("application/json", "application/x-www-form-urlencoded", "text/html"):
            self.assertEqual(self.call("/api/tool-selection", "POST", selection(), ORIGIN, ctype)[0], 403)
        for raw in (b"", b"{broken", b"[]", b'{"origin":NaN}', b'{"origin":"a","origin":"b"}', b"\xff"):
            self.assertEqual(self.call("/api/tool-selection", "POST", raw, ORIGIN, "text/plain")[0], 400)
        self.assertEqual(self.call("/api/tool-selection", "POST", b"x" * (32 * 1024 + 1), ORIGIN, "text/plain")[0], 413)
        self.assertEqual(pane._tools.revision, 0)

    def test_http_method_preflight_and_cross_origin_read_clear_are_denied(self):
        for path in ("/api/tool-selection", "/api/tool-record", "/api/tool-selection/clear"):
            for method in ("PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
                code, _, headers = self.call(path, method, origin=PANE_ORIGIN)
                self.assertIn(code, (403, 405))
                self.assertIsNone(headers.get("Access-Control-Allow-Origin"))
        self.select()
        for origin in (ORIGIN, "http://localhost:9899", "https://evil.example", "null"):
            for result in (self.call("/api/tool-selection", origin=origin), self.read(origin=origin),
                           self.call("/api/tool-selection/clear", "POST", {"epoch": pane._tools.epoch, "revision": 1},
                                     origin, "application/json")):
                self.assertEqual(result[0], 403)
                self.assertIsNone(result[2].get("Access-Control-Allow-Origin"))
        self.assertEqual(self.call("/api/tool-selection", headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(pane._tools.revision, 1)

    def test_http_query_validation_and_stale_do_not_contact_upstream(self):
        self.select()
        for query in ("", "?epoch=x", "?epoch=x&revision=-1", "?epoch=x&revision=true",
                      "?epoch=x&epoch=y&revision=1", "?epoch=x&revision=1&url=http://evil"):
            self.assertEqual(self.call("/api/tool-record" + query)[0], 400)
        self.assertEqual(self.read(epoch="old-epoch")[0], 409)
        self.assertEqual(self.read(revision=0)[0], 409)
        self.assertFalse(self.received)

    def test_http_close_heartbeat_replay_and_later_click(self):
        self.select()
        self.assertEqual(self.call("/api/tool-selection/clear", "POST", {"epoch": pane._tools.epoch, "revision": 0},
                                   PANE_ORIGIN, "application/json")[0], 409)
        self.assertEqual(self.call("/api/tool-selection/clear", "POST", {"epoch": pane._tools.epoch, "revision": 1},
                                   PANE_ORIGIN, "text/plain")[0], 403)
        code, obj, _ = self.call("/api/tool-selection/clear", "POST", {"epoch": pane._tools.epoch, "revision": 1},
                                PANE_ORIGIN, "application/json")
        self.assertEqual((code, obj["revision"], obj["selection"]), (200, 2, None))
        self.call("/api/active-session", "POST", {"sessionId": "ses_elsewhere", "title": "heartbeat"}, ORIGIN, "text/plain")
        self.assertIsNone(self.call("/api/tool-selection")[1]["selection"])
        self.assertEqual(self.select()[0], 409)
        self.assertEqual(self.read()[0], 404)
        self.assertEqual(self.select(sequence=2)[0], 200)
        self.assertEqual(self.read()[0], 200)

    def test_http_full_empty_long_unicode_and_stub_provenance(self):
        self.select()
        for text in ("", "x" * 12000 + "TAIL", "汉字 👩🏽‍💻 e\u0301\n" * 10000 + "TAIL",
                     "Output too large. Full output saved to /fixture/tool-output (stored stub)."):
            self.message["content"][1]["state"].update(input={"text": text}, output=text)
            self.respond(MESSAGE_PATH, self.message)
            code, result, _ = self.read()
            self.assertEqual(code, 200)
            self.assertEqual(result["part"], self.message["content"][1])
            self.assertNotIn("content", result)
        self.assertEqual([p for p, _ in self.received].count(SESSION_PATH), 1)

    def test_http_preserves_exact_selected_part_wire_once_for_shared_row_and_copy_sizes(self):
        self.select()
        part_wire = (r'{ "id":"part_fixture","type":"tool","name":"workflowy_node","callID":"call_fixture",'
                     r'"state": {"status":"completed","input":{"n":900719925474099312345,"exp":1.23456789e+300,"z":-0,"s":"\u0061\n汉😀"},'
                     r'"content":[{"type":"text","text":"doc\n😀"},{"type":"image","n":900719925474099312345}]}}')
        message_wire = ('{"data":{"id":"msg_fixture","sessionID":"ses_fixture","time":{"created":200,"completed":300},'
                        '"content":[{"id":"other_part","type":"text","text":"unrelated"},' + part_wire + ']}}')
        self.respond(MESSAGE_PATH, None, raw=message_wire.encode("utf-8"))
        snap = pane._tools.snapshot()
        with urllib.request.urlopen(self.base + "/api/tool-record?epoch=" + snap["epoch"] + "&revision=1", timeout=5) as response:
            wire = response.read().decode("utf-8")
        self.assertEqual(pane.wire_members(wire)["part"], part_wire)
        self.assertEqual(wire.count('900719925474099312345'), 2)  # each occurrence once, no escaped duplicate body
        self.assertNotIn("unrelated", wire)
        self.assertNotIn("offline-inspector-fixture-only", wire)
        self.assertEqual(json.loads(wire)["selection"]["association"], selection()["association"])

    def test_shared_browser_snapshot_script_is_one_fixed_file_and_no_general_escape(self):
        with urllib.request.urlopen(self.base + "/tool-record-text.js", timeout=5) as response:
            actual = response.read()
            self.assertIn("javascript", response.headers["Content-Type"])
        expected = pathlib.Path(pane.__file__).resolve().parent.parent / "chrome-extension/codenomad-session-watcher/tool-record-text.js"
        self.assertEqual(actual, expected.read_bytes())
        self.assertEqual(self.call("/../server.py")[0], 404)

    def test_http_message_envelope_missing_and_backend_failures_visible(self):
        self.select()
        cases = [(200, b"{}", 409), (200, b'{"data":[]}', 409), (200, b"", 409),
                 (200, b'{"data":{"id":"wrong"}}', 409), (200, b'{"data":NaN}', 409),
                 (404, b"private raw upstream body", 404), (401, b"private auth error", 502),
                 (503, b"private backend error", 502)]
        for code, raw, expected in cases:
            self.respond(MESSAGE_PATH, None, code=code, raw=raw)
            status, result, _ = self.read()
            self.assertEqual(status, expected)
            self.assertEqual(set(result), {"error"})
            self.assertNotIn("private", result["error"])

    def test_http_redirect_is_not_followed_or_used_as_remote_routing(self):
        self.select()
        self.respond(MESSAGE_PATH, None, code=302, headers={"Location": self.upstream + "/remote-record"}, raw=b"")
        self.assertEqual(self.read()[0], 409)
        self.assertFalse(any(p == "/remote-record" for p, _ in self.received))

    def test_http_offline_and_auth_failure_are_502_without_credentials(self):
        self.select()
        original_open = urllib.request.OpenerDirector.open
        def offline(opener, request, *args, **kwargs):
            if request.full_url.startswith(self.upstream):
                raise urllib.error.URLError("offline")
            return original_open(opener, request, *args, **kwargs)
        with patch.object(pane.urllib.request.OpenerDirector, "open", autospec=True, side_effect=offline):
            code, obj, _ = self.read()
        self.assertEqual(code, 502)
        self.assertNotIn("offline-inspector-fixture-only", obj["error"])
        # A deliberate new click is needed after revision-bound association failure.
        self.select(sequence=2)
        with patch.object(pane, "auth_header", side_effect=OSError("private credential error")):
            self.assertEqual(self.read()[0], 502)

    def test_http_oversized_part_and_upstream_refused_not_cropped(self):
        self.select()
        self.message["content"][1]["state"]["output"] = "x" * pane.TOOL_PART_LIMIT
        self.respond(MESSAGE_PATH, self.message)
        self.assertEqual(self.read()[0], 413)
        self.respond(MESSAGE_PATH, None, raw=b"x" * (pane.TOOL_UPSTREAM_LIMIT + 1))
        self.assertEqual(self.read()[0], 413)

    def test_http_stale_after_inflight_read_and_close_are_discarded(self):
        self.select()
        type(self).block_path = MESSAGE_PATH
        answer = []
        worker = threading.Thread(target=lambda: answer.append(self.read()), daemon=True)
        worker.start()
        self.assertTrue(type(self).entered.wait(timeout=3))
        code, _, _ = self.call("/api/tool-selection/clear", "POST", {"epoch": pane._tools.epoch, "revision": 1},
                               PANE_ORIGIN, "application/json")
        self.assertEqual(code, 200)
        type(self).release.set()
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(answer[0][0], 409)
        self.assertNotIn("part", answer[0][1])

    def test_http_concurrent_record_reads_serialize_and_verify_session_once(self):
        self.select()
        type(self).block_path = SESSION_PATH
        answers = []
        workers = [threading.Thread(target=lambda: answers.append(self.read()), daemon=True) for _ in range(2)]
        workers[0].start()
        self.assertTrue(type(self).entered.wait(timeout=3))
        workers[1].start()
        type(self).release.set()
        for worker in workers:
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
        self.assertEqual([answer[0] for answer in answers], [200, 200])
        self.assertEqual([p for p, _ in self.received], [SESSION_PATH, MESSAGE_PATH, MESSAGE_PATH])

    def test_http_transport_timeout_and_bounded_read_without_length_header(self):
        class Response:
            headers = {}
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self, n):
                self.read_bound = n
                return b"x" * n
        response = Response()
        with patch.object(pane.urllib.request.OpenerDirector, "open", return_value=response) as opened:
            with self.assertRaises(pane.ToolError) as caught:
                pane.tool_backend_json(MESSAGE_PATH)
        self.assertEqual(caught.exception.code, 413)
        self.assertEqual(response.read_bound, pane.TOOL_UPSTREAM_LIMIT + 1)
        self.assertEqual(opened.call_args.kwargs["timeout"], 10)
        req = opened.call_args.args[0]
        self.assertEqual(req.full_url, self.upstream + MESSAGE_PATH)
        self.assertEqual(req.get_header("Authorization"), "Basic offline-inspector-fixture-only")

    def test_bounded_body_reader_rejects_bad_lengths_and_transfer_encoding(self):
        handler = object.__new__(pane.Handler)
        for headers in ({}, {"Content-Length": "-1"}, {"Content-Length": "bad"},
                        {"Content-Length": "1" * 5000},
                        {"Content-Length": "10", "Transfer-Encoding": "chunked"}):
            handler.headers = headers
            handler.rfile = io.BytesIO(b"{}")
            with self.assertRaises(pane.ToolError) as caught:
                handler._bounded_json(32 * 1024)
            self.assertEqual(caught.exception.code, 400)
        handler.headers = {"Content-Length": "999999"}
        with self.assertRaises(pane.ToolError) as caught:
            handler._bounded_json(32 * 1024)
        self.assertEqual(caught.exception.code, 413)
        handler.headers = email.message.Message()
        handler.headers.add_header("Content-Length", "2")
        handler.headers.add_header("Content-Length", "20")
        with self.assertRaises(pane.ToolError) as caught:
            handler._bounded_json(32 * 1024)
        self.assertEqual(caught.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
