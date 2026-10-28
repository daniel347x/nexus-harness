"""Guarded NEXUS boundary, with mock tools and exact live-file fixtures only."""
import contextlib
import datetime
import http.client
import io
import json
import pathlib
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from browser_scratch import scratch_dir

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import server
from glimpse_client import GlimpseError

RENDER = "/api/glimpse/render"
CATALOG = "/api/glimpse/catalog"
REFRESH = "/api/glimpse/cache-refresh"
DOCKS = "/api/glimpse/docks"
ANCESTORS = "/api/glimpse/ancestors"
ASSETS = ("glimpse-md-render.js", "glimpse-md-render.css", "marked.min.js", "marked-katex-init.js",
          "katex-overrides.css", "katex/katex.min.js", "katex/katex.min.css", "katex/fonts/KaTeX_Main-Regular.woff2")
# The daemon's real include_rows replies (S1's code, in-process MCP) and the pre-S1 daemon's refusals.
ROWS_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "glimpse-rows"
ENVELOPES = json.loads((ROWS_FIXTURES / "envelopes.json").read_text(encoding="utf-8"))["records"]
PRE_S1 = json.loads((ROWS_FIXTURES / "pre_s1_forbidden.json").read_text(encoding="utf-8"))


def daemon_fault(reply):
    """GlimpseError as GlimpseClient raises it for a structured daemon fault."""
    error = reply["error"]
    return GlimpseError(str(error), {"code": error["code"], "message": error["message"]})


class FakeClient:
    def __init__(self):
        self.calls = []
        self.markdown = ' \n# 雪\n<div>raw</div>\n"quoted"\n(empty)\n '
        self.failure = None
        self.script = []  # replies (or exceptions) for the next render calls, in order
        self.groups = {"success": True, "slice_groups": [{"id": "grp-b", "name": "Category | B [2]"}, {"id": "grp-a", "name": "A"}]}
        self.docks = {"success": True, "docks": [{"id": "uuid-b", "dock_handle": "uuid-b", "name": "⭐ B #dock--fav"}, {"id": "uuid-a", "name": "A #dock"}]}

    def call(self, family, verb, params):
        self.calls.append((family, verb, json.loads(json.dumps(params))))
        if self.script:
            reply = self.script.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        if self.failure and self.failure(family, verb, params):
            raise GlimpseError("No live Workflowy tab connected; cannot read browser storage")
        if (family, verb) == ("slice_group", "list"):
            return json.dumps(self.groups)
        if (family, verb) == ("anchor", "dock.list"):
            return self.docks
        return self.markdown


class RouteTests(unittest.TestCase):
    def setUp(self):
        # Unique fixtures outside every checkout and separate from live config.
        self.temp = tempfile.TemporaryDirectory(dir=scratch_dir('fixtures/glimpse-routes'))
        self.checkout = pathlib.Path(self.temp.name)
        self.assets = self.checkout / "workflowy-glimpse-cache"
        self.assets.mkdir()
        for name in ASSETS:
            path = self.assets / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("/*! marked v15.0.12 */\n" + name + "\n雪").encode() if name == "marked.min.js" else b"fixture\x00\xff\r\n" + name.encode())
        self.client = FakeClient()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.glimpse_client_factory = lambda: self.client
        self.httpd.nexus_checkout = self.checkout
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path=RENDER, *, method="POST", data=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=10)
        body = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
        all_headers = {"Content-Type": "application/json", **(headers or {})}
        connection.request(method, path, body, all_headers)
        response = connection.getresponse()
        raw = response.read()
        code, rh = response.status, dict(response.getheaders())
        connection.close()
        return code, rh, json.loads(raw) if "application/json" in rh.get("Content-Type", "") else raw

    def render(self, value, options=None, kind="parcel"):
        data = {"kind": kind, "value": value}
        if options is not None:
            data["options"] = options
        return self.request(data=data)

    def test_parcels_all_go_to_existing_run_unchanged(self):
        lens = {"kind": "merge", "operation": "union", "inputs": [{"kind": "group", "group_handle": "G"}, {"kind": "subtree", "root_handle": "abcd1234"}]}
        # 'operation' is part of the read-only LensSpec set algebra, not a tool write.
        values = ["Category | G", "[ap:POINT1]", {"group": "G"}, {"node": "abcd1234"}, {"subtree": "abcd1234"},
                  {"tags": ["opc-pane"], "root": "abcd1234"}, {"symbols": ["Handler"], "root": "abcd1234"},
                  {"kind": "raw_root", "root_uuid": "abcd1234", "content_format": "raw_text", "focus": {"mode": "row", "row_id": "R"}},
                  {"spec": {"kind": "lightning_rod", "lightning": {"mode": "render_search", "root_uuid": "abcd1234", "query": "needle"}}},
                  {"spec": {"kind": "lightning_rod", "lightning": {"mode": "render_search", "root_uuid": "abcd1234", "query": "modal"}}},
                  {"spec": {"kind": "lightning_rod", "lightning": {"mode": "render_search", "root_uuid": "abcd1234", "query": '{"output_file":"literal search text"}'}}},
                  {"lens": lens}, {"spec": {"lightning": {"mode": "render_lens", "lens": lens}}}]
        for value in values:
            with self.subTest(value=value):
                self.client.calls.clear()
                code, headers, result = self.render(value)
                self.assertEqual(code, 200, result)
                self.assertEqual(self.client.calls, [("glimpse", "run", {"render_spec": value, "as_markdown": True})])
                self.assertEqual(result["markdown"], self.client.markdown)
                self.assertEqual(result["bytes"], len(self.client.markdown.encode()))
                self.assertEqual(result["request"], {"kind": "parcel", "value": value, "options": {}})
                datetime.datetime.fromisoformat(result["renderedAt"].replace("Z", "+00:00"))
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_dock_short_long_and_json_string_use_anchor(self):
        for value in ({"dock": "abcd1234"}, {"dock": "abcd1234", "descend": True},
                      {"kind": "dock", "ref": "abcd1234", "descend": True}, '{"dock":"abcd1234","descend":true}'):
            with self.subTest(value=value):
                options = {"headings_only": False, "sort_by": "modified", "sort_direction": "desc", "recent": "newest 4", "recent_by": "created", "ancestor_notes": True}
                self.assertEqual(self.render(value, options)[0], 200)
                parsed = json.loads(value) if isinstance(value, str) else value
                self.assertEqual(self.client.calls[-1], ("anchor", "dock.render", {"dock_handle": "abcd1234", "as_markdown": True,
                                 "descend_policy": "all" if parsed.get("descend") else "stop_at_child_anchors", **options}))

    def test_named_and_formal_estates_keep_daemon_headers(self):
        self.client.markdown = "# 🏛️ ESTATE\n> render_scope: kept\n"
        for value in ("[ap:ESTATE1]", {"spec": {"kind": "raw_root", "root_uuid": "abcd1234"}},
                      '[{"node":"abcd1234"},{"dock":"ffff1234"}]'):
            with self.subTest(value=value):
                code, _, result = self.render(value, kind="estate")
                self.assertEqual(code, 200, result)
                self.assertEqual(result["markdown"], self.client.markdown)
                self.assertEqual(self.client.calls[-1], ("glimpse", "estate", {"handle": value, "mode": "output", "as_markdown": True}))

    def test_no_tab_is_explained_but_raw_has_no_catalog_precondition(self):
        self.client.failure = lambda family, verb, p: verb in ("list", "dock.list") or "group" in p.get("render_spec", {})
        code, _, result = self.request(CATALOG, method="GET")
        self.assertEqual(code, 200)
        self.assertEqual(len(result["errors"]), 2)
        for value in ({"node": "abcd1234"}, {"subtree": "abcd1234"}):
            self.client.calls.clear()
            self.assertEqual(self.render(value)[0], 200)
            self.assertEqual([c[:2] for c in self.client.calls], [("glimpse", "run")])
        code, _, result = self.render({"group": "G"})
        self.assertEqual(code, 502)
        self.assertIn(server.NO_GLIMPSE_TAB, result["error"])

    def test_catalog_order_names_partial_failure_and_exact_envelopes(self):
        code, _, result = self.request(CATALOG, method="GET")
        self.assertEqual(code, 200, result)
        self.assertEqual(result["groups"], [{"handle": "grp-b", "name": "Category | B [2]"}, {"handle": "grp-a", "name": "A"}])
        self.assertEqual(result["docks"], [{"handle": "uuid-b", "name": "⭐ B #dock--fav", "depth": 0}, {"handle": "uuid-a", "name": "A #dock", "depth": 0}])
        self.assertEqual(self.client.calls, [("slice_group", "list", {"detail": "full"}), ("anchor", "dock.list", {"detail": "full", "favorites_only": True})])
        self.client.groups = {"success": False, "error": "No live Workflowy tab connected"}
        result = self.request(CATALOG, method="GET")[2]
        self.assertEqual(result["groups"], [])
        self.assertEqual(len(result["docks"]), 2)
        self.assertEqual(result["errors"][0]["source"], "groups")

    def test_catalog_docks_carry_their_depth_among_the_listed_docks(self):
        # dock.list's visual (depth-first) order with each row's ancestor path ending in its own name: a Dock nests under
        # the nearest earlier listed Dock whose path is a proper prefix of its own; plain ancestors in between add nothing.
        root, maps = "🗡️ Workflowy Root", "🛰️ Project Maps #wfy-ns--fav "
        rows = [("maps", [root, maps]),
                ("nexus", [root, maps, "[ap:ECOT1S] 🗺️ NEXUS map #wfy-ns--fav"]),
                ("companion", [root, maps, "🗺️ COMPANION map #wfy-ns--fav"]),
                ("pane", [root, maps, "🗺️ COMPANION map #wfy-ns--fav", "🗺️ PANE map #wfy-ns--fav"]),
                ("deep", [root, "📜 COMPONENTS", "MARKDOWN", "Architecture #wfy-ns--fav"]),
                ("deeper", [root, "📜 COMPONENTS", "MARKDOWN", "Architecture #wfy-ns--fav", "🔥 OpenCode", "🎙️ MANAGER APP"]),
                ("same-name", [root, "📜 COMPONENTS", "MARKDOWN", "Architecture #wfy-ns--fav "]),
                ("pathless", None),
                ("after", [root, "WORK #wfy-ns--fav"])]
        self.client.docks = {"success": True, "docks": [{"id": i, "name": i, **({"path": path} if path else {})} for i, path in rows]}
        result = self.request(CATALOG, method="GET")[2]
        self.assertEqual([(d["handle"], d["depth"]) for d in result["docks"]],
                         [("maps", 0), ("nexus", 1), ("companion", 1), ("pane", 2), ("deep", 0), ("deeper", 1), ("same-name", 0), ("pathless", 0), ("after", 0)])
        self.assertEqual(server.dock_depths([]), [])

    def test_row_envelope_status_lines_are_notices_never_markdown(self):
        envelope = dict(ENVELOPES["node_parcel"]["structuredContent"])
        decorated = {**envelope, "archive_warning": "⚠️⚠️⚠️ ARCHIVED NODE · a.json\n⚠️⚠️⚠️ ARCHIVED NODE · b.json",
                     "archived": [{"handle": "x"}],
                     "resolved_corrections": [{"short": "abcd1234", "name": "Doc", "supplied": "abcd12345"}, "not a record"],
                     "archives_left_out": {"count": 2, "line": "📦 2 archived subtree(s) under this scope were left out"}}
        self.client.script = [decorated]
        code, _, result = self.render({"node": "abcd1234"}, {"include_rows": True})
        self.assertEqual(code, 200, result)
        self.assertEqual(result["notices"], ["⚠️⚠️⚠️ ARCHIVED NODE · a.json", "⚠️⚠️⚠️ ARCHIVED NODE · b.json",
                                             "resolved: abcd1234 Doc (corrected from abcd12345)",
                                             "📦 2 archived subtree(s) under this scope were left out"])
        self.assertEqual(result["markdown"], envelope["markdown"])
        self.assertEqual(result["rows"], envelope["rows"])
        self.assertEqual(result["archivesLeftOut"], 2, "the footer's count: View offers include_archives from it")
        self.client.script = [envelope]
        self.assertNotIn("notices", self.render({"node": "abcd1234"}, {"include_rows": True})[2])
        self.client.script = [{**envelope, "archives_left_out": {"count": 0, "line": " "}, "resolved_corrections": "x"}]
        result = self.render({"node": "abcd1234"}, {"include_rows": True})[2]
        self.assertNotIn("notices", result)
        self.assertNotIn("archivesLeftOut", result)

    def test_include_archives_is_a_boolean_option_forwarded_to_every_render(self):
        on = {"include_archives": True}
        self.assertEqual(self.render({"node": "abcd1234"}, on)[0], 200)
        self.assertEqual(self.client.calls[-1], ("glimpse", "run", {"render_spec": {"node": "abcd1234"}, "as_markdown": True, "include_archives": True}))
        self.assertEqual(self.render({"dock": "abcd1234"}, {**on, "include_rows": True})[0], 200)
        self.assertEqual(self.client.calls[-1], ("anchor", "dock.render", {"dock_handle": "abcd1234", "as_markdown": True,
                                                                         "descend_policy": "stop_at_child_anchors", "include_archives": True, "include_rows": True}))
        self.assertEqual(self.render("[ap:ESTATE1]", on, kind="estate")[0], 200)
        self.assertEqual(self.client.calls[-1], ("glimpse", "estate", {"handle": "[ap:ESTATE1]", "mode": "output", "as_markdown": True, "include_archives": True}))
        self.client.calls.clear()
        for bad in ({"include_archives": "yes"}, {"include_archives": 1}, {"include_archives": None}):
            self.assertEqual(self.render({"node": "abcd1234"}, bad)[0], 400)
        self.assertEqual(self.client.calls, [])

    def test_docks_route_reads_all_and_local_inventories(self):
        self.client.docks = {"success": True, "docks": [
            {"id": "fav", "name": "Fav", "is_favorite": True, "is_local": False, "path": ["Root", "Fav #d--fav"]},
            {"id": "loc", "name": "Loc", "is_favorite": False, "is_local": True, "path": ["Root", "Fav #d--fav", "Loc #d--local"]},
            {"id": "plain", "name": "Plain", "path": ["Root", "Plain #d"]}]}
        for data, params in (({}, {"detail": "full", "favorites_only": True}),
                             ({"all": True}, {"detail": "full", "favorites_only": False}),
                             ({"local": True}, {"detail": "full", "favorites_only": True, "include_local": True}),
                             ({"all": True, "local": True}, {"detail": "full", "favorites_only": False, "include_local": True}),
                             ({"all": False, "local": False}, {"detail": "full", "favorites_only": True})):
            with self.subTest(data=data):
                self.client.calls.clear()
                code, _, result = self.request(DOCKS, data=data)
                self.assertEqual(code, 200, result)
                self.assertEqual(self.client.calls, [("anchor", "dock.list", params)])
                self.assertEqual((result["all"], result["local"]), (data.get("all", False), data.get("local", False)))
                self.assertEqual(result["errors"], [])
                self.assertEqual(result["docks"], [{"handle": "fav", "name": "Fav", "favorite": True, "local": False, "depth": 0},
                                                   {"handle": "loc", "name": "Loc", "favorite": False, "local": True, "depth": 1},
                                                   {"handle": "plain", "name": "Plain", "depth": 0}])
        self.client.calls.clear()
        for bad in ({"all": 1}, {"favorites_only": False}, [], {"local": "true"}):
            self.assertEqual(self.request(DOCKS, data=bad)[0], 400)
        self.assertEqual(self.request(DOCKS, method="GET")[0], 405)
        self.assertEqual(self.request(DOCKS + "?all=1", data={})[0], 400)
        self.assertEqual(self.client.calls, [])
        self.client.failure = lambda family, verb, p: verb == "dock.list"
        code, _, result = self.request(DOCKS, data={"all": True})
        self.assertEqual(code, 200)
        self.assertEqual(result["docks"], [])
        self.assertEqual(result["errors"][0]["source"], "docks")
        self.assertIn(server.NO_GLIMPSE_TAB, result["errors"][0]["error"])

    def test_ancestors_route_returns_the_daemons_f3_markdown(self):
        self.client.markdown = "Key: ⚓ Dock\n\n# ⦿ 🗡️ Workflowy Root\n## ⦿ Shelf ⚓ ⭐\n### • [ap:LEAF01] Leaf\n\n→ `f149bfe4-68b2-460a-9a6c-b9acaafd80bd`"
        code, headers, result = self.request(ANCESTORS, data={"handle": " [ap:LEAF01] "})
        self.assertEqual(code, 200, result)
        self.assertEqual(self.client.calls, [("node", "ancestors", {"node_handle": "[ap:LEAF01]", "uuids": "leaf"})])
        self.assertEqual(result["markdown"], self.client.markdown)
        self.assertEqual(result["bytes"], len(self.client.markdown.encode()))
        self.assertEqual((result["handle"], result["uuids"]), ("[ap:LEAF01]", "leaf"))
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.request(ANCESTORS, data={"handle": "f149bfe4", "uuids": "every-level"})[0], 200)
        self.assertEqual(self.client.calls[-1], ("node", "ancestors", {"node_handle": "f149bfe4", "uuids": "every-level"}))
        self.client.calls.clear()
        for bad in ({}, {"handle": ""}, {"handle": "a\nb"}, {"handle": "x" * 201}, {"handle": "abcd1234", "uuids": "all"},
                    {"handle": "abcd1234", "metrics": False}, {"handle": 3}):
            with self.subTest(bad=bad):
                self.assertEqual(self.request(ANCESTORS, data=bad)[0], 400)
        self.assertEqual(self.request(ANCESTORS, method="GET")[0], 405)
        self.assertEqual(self.client.calls, [])
        self.client.script = [GlimpseError("Node not found: abcd1234")]
        code, _, result = self.request(ANCESTORS, data={"handle": "abcd1234"})
        self.assertEqual((code, result["status"], result["error"]), (502, "daemon", "Node not found: abcd1234"))
        self.client.script = [{"not": "markdown"}]
        self.assertEqual(self.request(ANCESTORS, data={"handle": "abcd1234"})[0], 502)

    def test_refresh_is_explicit_throttled_including_failure(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                self.httpd.glimpse_refresh_attempt = None
                self.client.calls.clear()
                self.client.failure = (lambda f, v, p: v == "cache.refresh") if failure else None
                with patch("server.time.monotonic", return_value=100):
                    code, _, result = self.request(REFRESH, data={})
                self.assertEqual(code, 502 if failure else 200)
                self.assertIn("attemptedAt", result)
                self.assertEqual(self.client.calls, [("system", "cache.refresh", {})])
                with patch("server.time.monotonic", return_value=101):
                    code, _, result = self.request(REFRESH, data={})
                self.assertEqual(code, 429)
                self.assertEqual(result["retryAfterSeconds"], 29)
                self.assertIn("30 seconds", result["error"])
                self.assertEqual(len(self.client.calls), 1)
                with patch("server.time.monotonic", return_value=130):
                    self.request(REFRESH, data={})
                self.assertEqual(len(self.client.calls), 2)

    def test_no_hidden_refresh_in_repeated_render_or_catalog(self):
        for _ in range(3):
            self.assertEqual(self.render({"node": "abcd1234"})[0], 200)
            self.assertEqual(self.request(CATALOG, method="GET")[0], 200)
        self.assertNotIn(("system", "cache.refresh"), [c[:2] for c in self.client.calls])

    def test_write_directives_nested_json_and_modal_rejected_without_calls(self):
        values = [{"node": "abcd1234", "output_file": "x"}, {"node": "abcd1234", "push_to_modal": False}, "modal", {},
                  {"spec": {"root_uuid": "abcd1234", "focus": {"output_file": "x"}}},
                  '{"spec":{"root_uuid":"abcd1234","lightning":{"push_to_modal":true}}}',
                  {"spec": {"root_uuid": "abcd1234", "lightning": {"mode": "tabs"}}},
                  {"spec": {"root_uuid": "abcd1234", "lightning": {"verb": "delete"}}},
                  {"spec": {"root_uuid": "abcd1234", "lightning": {"operation": "delete"}}},
                  {"kind": "dom"}, {"kind": "raw_root"}, {"node": ""}, {"tags": ["x"]}]
        for value in values:
            with self.subTest(value=value):
                self.client.calls.clear()
                code, _, result = self.render(value)
                self.assertEqual(code, 400, result)
                self.assertEqual(self.client.calls, [])
        self.assertEqual(self.request(REFRESH, data={"force": True})[0], 400)

    def test_wrong_options_schema_methods_and_content_type(self):
        for options in ({"unknown": True}, {"headings_only": 1}, {"sort_direction": "desc"}, {"sort_by": "none", "sort_direction": "asc"}, [], {"recent": 3}):
            self.assertEqual(self.render({"node": "abcd1234"}, options)[0], 400)
        self.assertEqual(self.request(data={"kind": "parcel", "value": {"node": "abcd1234"}, "tool": "node"})[0], 400)
        for path, method in ((RENDER, "GET"), (CATALOG, "POST"), (REFRESH, "GET")):
            self.assertEqual(self.request(path, method=method, data={})[0], 405)
        self.assertEqual(self.request(RENDER + "?refresh=1", data={})[0], 400)
        self.assertEqual(self.request(data={}, headers={"Content-Type": "text/plain"})[0], 400)
        self.assertEqual(self.client.calls, [])

    def test_guard_before_client_and_file_reads_and_no_preflight(self):
        for path, method, data in ((RENDER, "POST", {"kind": "parcel", "value": {"node": "abcd1234"}}),
                                   (CATALOG, "GET", None), (REFRESH, "POST", {}), (DOCKS, "POST", {"all": True}),
                                   (ANCESTORS, "POST", {"handle": "abcd1234"}),
                                   ("/nexus-md/marked.min.js", "GET", None), ("/glimpse.html", "GET", None),
                                   ("/glimpse.js", "GET", None), ("/glimpse.css", "GET", None)):
            with self.subTest(path=path):
                with patch.object(pathlib.Path, "read_bytes", side_effect=AssertionError("unguarded file read")):
                    self.assertEqual(self.request(path, method=method, data=data, headers={"Origin": "https://foreign.invalid"})[0], 403)
                code, headers, _ = self.request(path, method="OPTIONS")
                self.assertEqual(code, 403)
                self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(self.client.calls, [])

    def test_assets_exact_bytes_mime_and_no_copy_fallback(self):
        for name in ASSETS:
            with self.subTest(name=name):
                code, headers, data = self.request("/nexus-md/" + name, method="GET")
                self.assertEqual(code, 200)
                self.assertEqual(data, (self.assets / name).read_bytes())
                self.assertEqual(headers["Cache-Control"], "no-store")
                if name.endswith(".js"):
                    self.assertIn("javascript", headers["Content-Type"])
                elif name.endswith(".css"):
                    self.assertIn("text/css", headers["Content-Type"])
                else:
                    self.assertIn("font/woff2", headers["Content-Type"])
        marked = self.assets / "marked.min.js"
        self.assertIn(b"v15.0.12", self.request("/nexus-md/marked.min.js", method="GET")[2])
        marked.write_bytes(b"changed-live-source")
        self.assertEqual(self.request("/nexus-md/marked.min.js", method="GET")[2], b"changed-live-source")
        marked.unlink()
        code, _, result = self.request("/nexus-md/marked.min.js", method="GET")
        self.assertEqual(code, 404)
        self.assertIn(str(self.checkout), result["error"])
        self.assertIn("set nexus_checkout in ~/.config/opencode-companion/pane-glimpse.json, then restart pane", result["error"])

    def test_asset_allowlist_nested_font_and_symlink_escape(self):
        (self.assets / "private.txt").write_text("not served")
        (self.checkout / "outside").write_text("outside")
        (self.assets / "glimpse-md-render.js").unlink()
        (self.assets / "glimpse-md-render.js").symlink_to(self.checkout / "outside")
        for name in ("private.txt", "../outside", "katex/fonts/../katex.min.js", "katex/fonts/sub/a.woff2", "katex/fonts/%2e%2e", "glimpse-md-render.js"):
            self.assertEqual(self.request("/nexus-md/" + name, method="GET")[0], 404, name)

    def test_invalid_startup_setting_disables_only_renderer_files(self):
        reason = (f"{self.checkout / 'pane-glimpse.json'}: invalid pane glimpse configuration; "
                  "expected only nexus_checkout with an absolute path")
        phone_before = self.request("/m", method="GET")
        self.httpd.nexus_checkout_error = reason
        # Both the configured checkout and a would-be default contain real fixture bytes.
        with patch.object(server, "DEFAULT_NEXUS_CHECKOUT", self.checkout):
            for checkout in (self.checkout, None):
                self.httpd.nexus_checkout = checkout
                for path, method in (("marked.min.js", "GET"),
                                     ("katex/fonts/KaTeX_Main-Regular.woff2", "GET"),
                                     ("not-allowlisted", "GET"), ("marked.min.js", "POST")):
                    with self.subTest(checkout=checkout, path=path, method=method):
                        code, _, result = self.request("/nexus-md/" + path, method=method)
                        self.assertGreaterEqual(code, 400)
                        self.assertIsInstance(result, dict)
                        self.assertEqual(result["schemaVersion"], 1)
                        self.assertTrue(result["status"])
                        self.assertIn(reason, result["error"])
                        self.assertIn("fix it and restart the pane", result["error"])
                        # The scratch path itself contains "fixtures"; require only the named error, never asset bytes.
                        self.assertEqual(result["error"], reason + "; fix it and restart the pane")
            self.assertEqual(self.request("/nexus-md/marked.min.js", method="GET",
                                          headers={"Origin": "https://foreign.invalid"})[0], 403)
        self.assertEqual(phone_before[0], 200)
        self.assertEqual(self.request("/m", method="GET"), phone_before)

    def test_large_document_not_cropped_or_refused_below_existing_limits(self):
        self.client.markdown = "雪" * (1024 * 1024)
        code, _, result = self.render({"node": "abcd1234"})
        self.assertEqual(code, 200)
        self.assertEqual(result["markdown"], self.client.markdown)
        self.assertEqual(result["bytes"], 3 * 1024 * 1024)
        self.client.markdown = "x" * (9 * 1024 * 1024)
        code, _, result = self.render({"node": "abcd1234"})
        self.assertEqual(code, 200)
        self.assertEqual(result["markdown"], self.client.markdown)
        self.client.markdown = "x" * (16 * 1024 * 1024)
        self.assertEqual(self.render({"node": "abcd1234"})[0], 413)

    def test_actual_b1_checkout_bytes_when_present(self):
        actual = pathlib.Path("/mnt/e/__daniel347x/__Dan_Root/__repos/workflowy-nexus-mcp-tools-wt-paneviews")
        if not (actual / "workflowy-glimpse-cache/glimpse-md-render.js").is_file():
            self.skipTest("B1 renderer not delivered yet")
        self.httpd.nexus_checkout = actual
        for name in ASSETS:
            code, _, data = self.request("/nexus-md/" + name, method="GET")
            self.assertEqual(code, 200)
            self.assertEqual(data, (actual / "workflowy-glimpse-cache" / name).read_bytes())
        self.assertIn(b"v15.0.12", self.request("/nexus-md/marked.min.js", method="GET")[2])

    # ---- the row list (UnitSpec S §§5.1, 6.3, 6.4): include_rows, the envelope and the old-daemon retry

    def test_include_rows_is_a_boolean_option_for_parcels_only(self):
        for flag in (True, False):
            with self.subTest(flag=flag):
                self.client.calls.clear()
                code, _, result = self.render({"node": "abcd1234"}, {"include_rows": flag})
                self.assertEqual(code, 200, result)
                self.assertEqual(self.client.calls, [("glimpse", "run", {"render_spec": {"node": "abcd1234"}, "as_markdown": True, "include_rows": flag})])
                self.assertEqual(result["request"]["options"], {"include_rows": flag})
        self.client.calls.clear()
        for options in ({"include_rows": 1}, {"include_rows": "true"}, {"include_rows": None}):
            self.assertEqual(self.render({"node": "abcd1234"}, options)[0], 400, options)
        code, _, result = self.render("[ap:ESTATE1]", {"include_rows": True}, kind="estate")
        self.assertEqual(code, 400)
        self.assertIn("never an estate", result["error"])
        self.assertEqual(self.client.calls, [])

    def test_run_and_dock_forward_the_real_envelope_unchanged(self):
        for name, value, expected in (
                ("anchor_dock", {"dock": "20000000"}, ("anchor", "dock.render", {"dock_handle": "20000000", "as_markdown": True,
                                                                                "descend_policy": "stop_at_child_anchors", "include_rows": True})),
                ("slice_group", {"group": "Architecture"}, ("glimpse", "run", {"render_spec": {"group": "Architecture"}, "as_markdown": True, "include_rows": True})),
                ("raw_root_created_asc", {"subtree": "10000000"}, ("glimpse", "run", {"render_spec": {"subtree": "10000000"}, "as_markdown": True, "include_rows": True}))):
            with self.subTest(name=name):
                envelope = ENVELOPES[name]["structuredContent"]
                self.client.calls.clear()
                self.client.script = [json.loads(json.dumps(envelope))]
                code, _, result = self.render(value, {"include_rows": True})
                self.assertEqual(code, 200, result)
                self.assertEqual(self.client.calls, [expected])
                self.assertEqual(result["markdown"], envelope["markdown"])
                self.assertEqual(result["bytes"], len(envelope["markdown"].encode("utf-8")))  # Markdown only, never the rows
                self.assertEqual(result["rows"], envelope["rows"])
                self.assertEqual(result["render_spec"], envelope["render_spec"])
                self.assertEqual(set(result), {"schemaVersion", "markdown", "bytes", "renderedAt", "request", "rows", "render_spec"})

    def test_empty_rows_and_missing_spec_are_kept_as_given(self):
        self.client.script = [{"markdown": "(empty)", "rows": []}]
        result = self.render({"node": "abcd1234"}, {"include_rows": True})[2]
        self.assertEqual((result["markdown"], result["rows"]), ("(empty)", []))
        self.assertNotIn("render_spec", result)
        self.client.script = [{"markdown": "# x", "rows": [], "render_spec": None}]
        result = self.render({"node": "abcd1234"}, {"include_rows": True})[2]
        self.assertEqual(result["rows"], [])
        self.assertNotIn("render_spec", result)

    def test_plain_markdown_reply_is_document_only_without_warning(self):
        # An old daemon or an old shape: Markdown text, even with include_rows asked, and even if it looks like JSON.
        for markdown in (self.client.markdown, '{"markdown": "x", "rows": []}'):
            with self.subTest(markdown=markdown):
                self.client.script = [markdown]
                code, _, result = self.render({"node": "abcd1234"}, {"include_rows": True})
                self.assertEqual(code, 200, result)
                self.assertEqual(result["markdown"], markdown)
                self.assertNotIn("rows", result)
                self.assertNotIn("render_spec", result)
                self.assertNotIn("warning", json.dumps(result).lower())
        # A row envelope that was not asked for is not taken as Markdown.
        self.client.script = [ENVELOPES["node_parcel"]["structuredContent"]]
        self.assertEqual(self.render({"node": "abcd1234"})[0], 502)

    def test_malformed_envelope_is_an_error_not_a_document(self):
        for reply in ({"markdown": "# x", "rows": "nope"}, {"markdown": 3, "rows": []}, {"markdown": "# x", "rows": [], "render_spec": "x"}, {"rows": []}):
            with self.subTest(reply=reply):
                self.client.script = [reply]
                code, _, result = self.render({"node": "abcd1234"}, {"include_rows": True})
                self.assertEqual(code, 502, result)
                self.assertEqual(result["status"], "daemon")

    def test_pre_s1_daemon_refusal_retries_once_without_only_include_rows(self):
        for refusal, value, family, verb in ((PRE_S1["forbidden_run"], {"group": "Architecture"}, "glimpse", "run"),
                                             (PRE_S1["forbidden_dock"], {"dock": "abcd1234", "recursive": False}, "anchor", "dock.render")):
            with self.subTest(operation=refusal["operation"]):
                options = {"headings_only": True, "sort_by": "created", "sort_direction": "asc", "include_rows": True}
                self.client.calls.clear()
                self.client.script = [daemon_fault(refusal), "# Old daemon\n\nbody"]
                code, _, result = self.render(value, options)
                self.assertEqual(code, 200, result)
                self.assertEqual(len(self.client.calls), 2)
                first, second = self.client.calls
                self.assertEqual(first[:2], (family, verb))
                self.assertEqual(second[:2], (family, verb))
                self.assertIs(first[2]["include_rows"], True)
                self.assertEqual(second[2], {k: v for k, v in first[2].items() if k != "include_rows"})
                self.assertEqual(result["markdown"], "# Old daemon\n\nbody")
                self.assertNotIn("rows", result)
                self.assertNotIn("render_spec", result)
                self.assertEqual(result["request"]["options"], options)  # Refresh repeats the same request

    def test_retry_has_no_capability_memory_old_then_new_daemon(self):
        self.client.script = [daemon_fault(PRE_S1["forbidden_run"]), "# old"]
        self.assertNotIn("rows", self.render({"node": "abcd1234"}, {"include_rows": True})[2])
        envelope = ENVELOPES["node_parcel"]["structuredContent"]
        self.client.calls.clear()
        self.client.script = [envelope]
        result = self.render({"node": "abcd1234"}, {"include_rows": True})[2]
        self.assertEqual(result["rows"], envelope["rows"])
        self.assertEqual(len(self.client.calls), 1)
        self.assertIs(self.client.calls[0][2]["include_rows"], True)

    def test_other_errors_are_not_retried(self):
        stale = ENVELOPES["stale_focus"]["structuredContent"]
        unknown = ENVELOPES["unknown_parameter"]["structuredContent"]
        cases = (daemon_fault(stale), daemon_fault(unknown),
                 GlimpseError("forbidden parameter 'include_rows'; allowed parameters: []"),  # text only, no structured code
                 GlimpseError("x", {"code": "forbidden_parameter", "message": "forbidden parameter 'output_file'; allowed parameters: []"}),
                 GlimpseError("No live Workflowy tab connected; cannot read browser storage"))
        for error in cases:
            with self.subTest(error=str(error)[:60]):
                self.client.calls.clear()
                self.client.script = [error, "# must not be fetched"]
                code, _, result = self.render({"node": "abcd1234"}, {"include_rows": True})
                self.assertEqual(code, 502, result)
                self.assertEqual(len(self.client.calls), 1)
                self.assertNotIn("markdown", result)
        self.client.script = []
        # The stale-focus message stays visible, exactly as the daemon said it.
        self.client.script = [daemon_fault(stale)]
        result = self.render({"node": "abcd1234"}, {"include_rows": True})[2]
        self.assertIn("could not be resolved occurrence-exactly", result["error"])
        # Without include_rows a forbidden_parameter is never retried either.
        self.client.calls.clear()
        self.client.script = [daemon_fault(PRE_S1["forbidden_run"]), "# no"]
        self.assertEqual(self.render({"node": "abcd1234"})[0], 502)
        self.assertEqual(len(self.client.calls), 1)

    def test_serialized_spec_with_a_row_focus_round_trips_to_run(self):
        # The daemon's own echoed blobs (all 19 serializer fields) with only focus replaced, as a View tap sends them.
        for name in ("anchor_dock", "slice_group", "raw_root_created_asc", "node_parcel", "tags_parcel"):
            with self.subTest(name=name):
                envelope = ENVELOPES[name]["structuredContent"]
                spec = json.loads(json.dumps(envelope["render_spec"]))
                self.assertEqual(len(spec), 19)
                row = envelope["rows"][-1]
                spec["focus"] = {"mode": "row", "uuid": row["uuid"], "ancestor_path": row["ancestor_path"], "occurrence": row["occurrence"]}
                self.client.calls.clear()
                self.client.script = [envelope]
                code, _, result = self.render(spec, {"include_rows": True})
                self.assertEqual(code, 200, result)
                self.assertEqual(self.client.calls, [("glimpse", "run", {"render_spec": spec, "as_markdown": True, "include_rows": True})])
                self.assertEqual(result["request"]["value"], spec)
        tap = ENVELOPES["anchor_dock_tap"]["params"]["render_spec"]
        self.client.script = [ENVELOPES["anchor_dock_tap"]["structuredContent"]]
        self.assertEqual(self.render(tap, {"include_rows": True})[0], 200)
        self.assertEqual(self.client.calls[-1][2]["render_spec"]["focus"]["occurrence"], 0)

    def test_serialized_spec_keeps_read_only_directive_rejection(self):
        spec = json.loads(json.dumps(ENVELOPES["anchor_dock"]["structuredContent"]["render_spec"]))
        for extra in ({"output_file": "x"}, {"push_to_modal": True}, {"target_tab": 1}, {"kind": "live_dom"}, {"verb": "delete"}, {"unknown_field": 1}):
            with self.subTest(extra=extra):
                self.client.calls.clear()
                self.assertEqual(self.render({**spec, **extra}, {"include_rows": True})[0], 400)
                self.assertEqual(self.client.calls, [])

    def test_wire_limit_counts_rows_too(self):
        row = {"uuid": "u", "ancestor_path": [], "occurrence": 0, "depth": 0, "name": "x" * 1024}
        self.client.script = [{"markdown": "# small", "rows": [row] * (17 * 1024)}]
        code, _, result = self.render({"node": "abcd1234"}, {"include_rows": True})
        self.assertEqual(code, 413)
        self.assertIn("16 MiB", result["error"])


class ConfigTests(unittest.TestCase):
    def test_main_starts_with_invalid_glimpse_settings(self):
        scratch = scratch_dir('fixtures/glimpse-config')
        with tempfile.TemporaryDirectory(dir=scratch) as temp:
            home = pathlib.Path(temp)
            path = home / ".config/opencode-companion/pane-glimpse.json"
            path.parent.mkdir(parents=True)
            for text in ('{', '[]', '{}', '{"nexus_checkout":null}', '{"nexus_checkout":""}',
                         '{"nexus_checkout":"relative"}', '{"nexus_checkout":"/a","extra":"PRIVATE-CONTENTS"}'):
                with self.subTest(text=text):
                    path.write_text(text)
                    stderr = io.StringIO()
                    with patch.object(pathlib.Path, "home", return_value=home), \
                            patch.object(server, "ThreadingHTTPServer") as constructor, \
                            contextlib.redirect_stderr(stderr):
                        result = server.main()
                    constructor.assert_called_once_with((server.HOST, server.PORT), server.Handler)
                    self.assertNotEqual(result, 1)
                    httpd = constructor.return_value
                    httpd.serve_forever.assert_called_once_with()
                    reason = (f"{path}: invalid pane glimpse configuration; "
                              "expected only nexus_checkout with an absolute path")
                    self.assertEqual(httpd.nexus_checkout_error, reason)
                    self.assertIsNone(httpd.nexus_checkout)
                    self.assertEqual(stderr.getvalue(), reason + "\n")
                    self.assertNotIn("PRIVATE-CONTENTS", stderr.getvalue())

    def test_missing_valid_and_invalid_settings(self):
        scratch = scratch_dir('fixtures/glimpse-config')
        with tempfile.TemporaryDirectory(dir=scratch) as temp:
            path = pathlib.Path(temp) / "pane-glimpse.json"
            self.assertEqual(server.load_glimpse_settings(path), server.DEFAULT_NEXUS_CHECKOUT)
            path.write_text(json.dumps({"nexus_checkout": temp}))
            self.assertEqual(server.load_glimpse_settings(path), pathlib.Path(temp).resolve())
            for text in ("", "{}", "[]", '{"nexus_checkout":"relative"}', '{"nexus_checkout":null}', '{"nexus_checkout":"/a","nexus_checkout":"/b"}', '{"nexus_checkout":"/a","extra":1}'):
                path.write_text(text)
                with self.assertRaises(ValueError) as caught:
                    server.load_glimpse_settings(path)
                self.assertIn(str(path), str(caught.exception))


if __name__ == "__main__":
    unittest.main()
