"""Offline model/prompt mocks only: never connects to the live OpenCode service."""
import copy
import importlib.util
import json
import pathlib
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("pane_server", pathlib.Path(__file__).resolve().parents[1] / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

SID = "ses_fixture"
MODEL = {"providerID": "openai", "id": "gpt-6.1-sol", "variant": "medium"}


class ThinkingBridgeTests(unittest.TestCase):
    def setUp(self):
        self.clock = patch.object(server.time, "time", return_value=1000)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.reset()

    def reset(self):
        server._active = {"sessionId": None, "title": None, "thinking": None, "ts": 0, "revision": 0, "watcherId": None}
        server._observation = (0, None, 0)
        server.time.time.return_value = 1000
        self.model = copy.deepcopy(MODEL)
        self.calls = []
        self.busy = False
        self.fail_switch = False
        self.ignore_switch = False
        self.hook = lambda method, path: None
        self.catalog = [{"providerID": "openai", "id": "gpt-6.1-sol", "variants": [{"id": "medium"}, {"id": "xhigh"}]}]

    def report(self, variant="xhigh", **extra):
        d = {"sessionId": SID, "title": "fixture", "thinking": {"providerID": "openai", "id": "gpt-6.1-sol", "variant": variant},
             "watcherId": "fixture-watcher", "sequence": 1, "observedAt": 1000000}
        d.update(extra)
        return server.accept_report(d)

    def request(self, method, path, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        self.hook(method, path)
        if method == "GET" and path.startswith("/api/model"):
            return copy.deepcopy(self.catalog)
        if path == "/api/session/active":
            return {SID: {}} if self.busy else {}
        if method == "GET":
            return {"model": copy.deepcopy(self.model), "location": {"directory": "/fixture/project"}}
        if path.endswith("/model"):
            if self.fail_switch:
                raise OSError("fixture switch refused")
            if not self.ignore_switch:
                self.model = copy.deepcopy(body["model"])
            return None  # native switch is HTTP 204
        if path.endswith("/prompt"):
            return {"id": "fixture-inbox-id"}
        self.fail("unexpected request")

    def send(self, **extra):
        d = {"sessionId": SID, "targetMode": "follow", "watcherRevision": server._active["revision"], "text": "first\n\nsecond"}
        d.update(extra)
        return server.send_prompt(d, self.request)

    def writes(self, suffix):
        return [c for c in self.calls if c[0] == "POST" and c[1].endswith(suffix)]

    def test_fresh_xhigh_switch_precedes_prompt_preserves_identity_and_paragraphs(self):
        self.report()
        result = self.send()
        self.assertEqual(self.writes("/model")[0][2], {"model": {"providerID": "openai", "id": "gpt-6.1-sol", "variant": "xhigh"}})
        self.assertEqual(self.writes("/prompt")[0][2], {"text": "first\n\nsecond"})
        self.assertLess(self.calls.index(self.writes("/model")[0]), self.calls.index(self.writes("/prompt")[0]))
        self.assertEqual(result["thinking"], "thinking xhigh")
        self.assertTrue(any(c[1] == "/api/model?location%5Bdirectory%5D=%2Ffixture%2Fproject" for c in self.calls))

    def test_medium_and_explicit_default(self):
        for variant in ("medium", None):
            with self.subTest(variant=variant):
                self.reset()
                self.model["variant"] = "xhigh"
                self.report(variant)
                self.send()
                expected = {"providerID": "openai", "id": "gpt-6.1-sol"}
                if variant is not None:
                    expected["variant"] = variant
                self.assertEqual(self.writes("/model")[0][2]["model"], expected)

    def test_missing_selector_not_default(self):
        self.report(thinking=None)
        self.assertIn("selector unavailable", self.send()["thinking"])
        self.assertFalse(self.writes("/model"))
        self.assertEqual(self.model, MODEL)

    def test_stale_manual_and_legacy_reports_preserve_level(self):
        for scenario in ("stale", "manual", "legacy"):
            with self.subTest(scenario=scenario):
                self.reset()
                if scenario == "legacy":
                    server.accept_report({"sessionId": SID, "title": "legacy"})
                else:
                    self.report(observedAt=950000 if scenario == "stale" else 1000000)
                self.send(targetMode="manual" if scenario == "manual" else "follow")
                self.assertFalse(self.writes("/model"))
                self.assertTrue(self.writes("/prompt"))

    def test_missing_report_manual_send_does_not_apply_level(self):
        self.send(targetMode="manual")
        self.assertFalse(self.writes("/model"))

    def test_no_report_follow_fails_not_cross_session(self):
        with self.assertRaisesRegex(server.SendError, "target/selection changed"):
            self.send()
        self.assertFalse(self.calls)

    def test_session_mismatch_fails(self):
        self.report(sessionId="ses_other")
        with self.assertRaisesRegex(server.SendError, "target/selection changed"):
            self.send()
        self.assertFalse(self.calls)

    def test_model_mismatch_fails_without_any_write(self):
        self.report()
        self.model["id"] = "another-model"
        with self.assertRaisesRegex(server.SendError, "selector model differs"):
            self.send()
        self.assertFalse(self.writes("/model") or self.writes("/prompt"))

    def test_catalog_rejects_arbitrary_variant_and_missing_model(self):
        for scenario in ("unsupported", "missing"):
            with self.subTest(scenario=scenario):
                self.reset()
                self.report("arbitrary" if scenario == "unsupported" else "xhigh")
                if scenario == "missing": self.catalog = []
                with self.assertRaises(server.SendError): self.send()
                self.assertFalse(self.writes("/model") or self.writes("/prompt"))

    def test_failed_or_unacknowledged_switch_never_prompts(self):
        for scenario in ("error", "no-op"):
            with self.subTest(scenario=scenario):
                self.reset()
                self.report()
                self.fail_switch = scenario == "error"
                self.ignore_switch = scenario == "no-op"
                with self.assertRaises(server.SendError): self.send()
                self.assertFalse(self.writes("/prompt"))

    def test_queue_ignores_selector_even_with_mismatched_model_and_busy_generation(self):
        self.report()
        self.model["id"] = "another-model"
        self.busy = True
        result = self.send(delivery="queue")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.writes("/prompt")[0][2], {"text": "first\n\nsecond", "delivery": "queue", "resume": False})
        self.assertIn("selector not applied", result["thinking"])

    def test_session_became_busy_never_switches(self):
        self.report()
        self.busy = True
        with self.assertRaisesRegex(server.SendError, "became busy"): self.send()
        self.assertFalse(self.writes("/model") or self.writes("/prompt"))

    def test_report_changed_during_catalog_or_after_switch_aborts_prompt(self):
        for point in ("/api/model", "/model"):
            with self.subTest(point=point):
                self.reset()
                self.report()
                def change(method, path):
                    if (point == "/api/model" and path.startswith(point)) or (point == "/model" and method == "POST" and path.endswith(point)):
                        self.report("medium", observedAt=1000001, sequence=2)
                self.hook = change
                with self.assertRaisesRegex(server.SendError, "target/selection changed"): self.send()
                self.assertFalse(self.writes("/prompt"))

    def test_model_changed_during_catalog_aborts_before_switch(self):
        self.report()
        def change(method, path):
            if path.startswith("/api/model"): self.model["id"] = "another-model"
        self.hook = change
        with self.assertRaisesRegex(server.SendError, "model changed"): self.send()
        self.assertFalse(self.writes("/model") or self.writes("/prompt"))

    def test_heartbeat_same_choice_keeps_revision_and_updates_freshness(self):
        self.report()
        revision = server._active["revision"]
        self.report(observedAt=1000001, sequence=2)
        self.assertEqual(server._active["revision"], revision)

    def test_out_of_order_same_watcher_and_other_tab_cannot_overwrite(self):
        self.report()
        self.report("medium", observedAt=1000002, sequence=3)
        self.assertFalse(self.report(observedAt=1000001, sequence=2))
        self.assertFalse(self.report(observedAt=999999, watcherId="older-tab"))
        self.assertEqual(server._active["thinking"]["variant"], "medium")

    def test_delayed_report_uses_observation_not_arrival_for_freshness(self):
        self.report(observedAt=950000)
        self.assertEqual(server.active_report()["age_s"], 50)
        self.send()
        self.assertFalse(self.writes("/model"))

    def test_no_visible_session_invalidates_choice_retains_stale_target(self):
        self.report()
        revision = server._active["revision"]
        self.report(sessionId=None, thinking=None, observedAt=1000001, sequence=2)
        self.assertEqual(server._active["sessionId"], SID)
        self.assertIsNone(server._active["thinking"])
        self.assertIsNone(server.active_report()["age_s"])
        self.assertGreater(server._active["revision"], revision)

    def test_expiration_during_network_wait_aborts_before_switch(self):
        self.report()
        def expire(method, path):
            if path == "/api/session/active": server.time.time.return_value = 1046
        self.hook = expire
        with self.assertRaisesRegex(server.SendError, "expired"): self.send()
        self.assertFalse(self.writes("/model") or self.writes("/prompt"))

    def test_already_selected_level_does_not_need_a_switch(self):
        self.report("medium")
        self.send()
        self.assertFalse(self.writes("/model"))
        self.assertTrue(self.writes("/prompt"))

    def test_absent_variant_field_is_invalid_not_default(self):
        with self.assertRaisesRegex(ValueError, "invalid thinking selection"):
            self.report(thinking={"providerID": "openai", "id": "gpt-6.1-sol"})

    def test_catalog_or_busy_status_failure_does_not_switch_or_prompt(self):
        for failed_path in ("/api/model", "/api/session/active"):
            with self.subTest(path=failed_path):
                self.reset()
                self.report()
                def fail(method, path):
                    if path.startswith(failed_path): raise server.SendError("fixture unavailable")
                self.hook = fail
                with self.assertRaisesRegex(server.SendError, "fixture unavailable"): self.send()
                self.assertFalse(self.writes("/model") or self.writes("/prompt"))

    def handler(self, path, headers, body):
        handler = object.__new__(server.Handler)
        handler.command = "POST"
        handler.path = path
        handler.headers = headers
        handler._body = lambda: json.dumps(body).encode("utf-8")
        handler._json = lambda code, obj, cors=False: (code, obj, cors)
        return handler

    def test_pane_route_wiring_and_origin_boundary(self):
        d = {"text": "fixture"}
        good = self.handler("/api/send", {"Content-Type": "application/json", "Origin": "http://127.0.0.1:9911"}, d)
        with patch.object(server, "send_prompt", return_value={"data": {"id": "fixture"}, "thinking": "thinking medium"}) as send:
            code, result, cors = good._route()
            self.assertEqual(code, 200)
            self.assertFalse(cors)
            send.assert_called_once_with(d)
            for headers in ({"Content-Type": "text/plain"}, {"Content-Type": "application/json", "Origin": "http://127.0.0.1:9899"}):
                self.assertEqual(self.handler("/api/send", headers, d)._route()[0], 403)
            self.assertEqual(send.call_count, 1)
        with patch.object(server, "send_prompt", side_effect=server.SendError("switch failed; no prompt")):
            self.assertEqual(good._route()[0], 409)
        self.assertEqual(good.do_OPTIONS()[0], 403)

    def test_report_route_restricts_cross_site_origin(self):
        d = {"sessionId": SID}
        bad = self.handler("/api/active-session", {"Origin": "https://unrelated.example"}, d)
        self.assertEqual(bad._route()[0], 403)
        self.assertIsNone(server._active["sessionId"])
        good = self.handler("/api/active-session", {"Origin": "http://127.0.0.1:9899"}, d)
        self.assertEqual(good._route()[0], 200)
        self.assertEqual(server._active["sessionId"], SID)

    def test_native_response_unwrapping_and_204_without_live_credentials(self):
        from unittest.mock import MagicMock
        with patch.object(server, "auth_header", return_value="fixture-only"), patch.object(server.urllib.request, "urlopen") as open_mock:
            response = MagicMock()
            open_mock.return_value.__enter__.return_value = response
            response.read.return_value = b'{"data":[{"id":"fixture","variants":[{"id":"xhigh"}]}],"location":{"directory":"/fixture"}}'
            self.assertEqual(server.opencode_json("GET", "/api/model")[0]["id"], "fixture")
            response.read.return_value = b""
            self.assertIsNone(server.opencode_json("POST", "/api/session/ses_fixture/model", {"model": MODEL}))
            req = open_mock.call_args.args[0]
            self.assertEqual(json.loads(req.data), {"model": MODEL})
            self.assertEqual(req.get_header("Authorization"), "fixture-only")


if __name__ == "__main__":
    unittest.main()
