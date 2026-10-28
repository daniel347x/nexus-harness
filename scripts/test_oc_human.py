"""Targeted offline contracts for oc.sh human: lookup, parcel, flags and both destinations."""
import contextlib
import io
import json
from pathlib import Path
import sys
import shutil
import subprocess
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oc_human as human
import oc_view

UUID = "e2c3652b-bea0-4fd7-bb16-8581d987c60b"
OTHER = "248484f8-35cb-4d98-aba3-615777308572"
ROWS = [
    "[adee3c89-5cf4-4c77-8887-a7c08b28f5cf] [WG-1       ] ⦿ Manager Sessions ▸3",
    "[7f8e8517-303f-4cfc-965b-e6cd98b18825] [WG-1.1     ] ⦿ Manager-Session-223 #wfy-ns--local ▸1",
    f"[{UUID}] [WG-1.1.1   ] • [ap:5CKYAT] 📦 PROGRAM · Millions of events &amp; more #wfy-ns--local #prog-root ▸43",
    f"[{OTHER}] [WG-1.2     ] • [ap:WOFFA4] 🍴 PROGRAM · Pre-seeded sessions #prog-root ▸23",
    "[c5d8e05b-142f-4aa4-850a-98e01fc9cd40] [WG-1.3] • Not a program #prog-root-extra ▸0",
]
PROGRAM = human.Program(UUID, "📦 PROGRAM · Millions of events", "[ap:5CKYAT]", "Manager-Session-223")


class Shape(unittest.TestCase):
    def test_inventory_titles_handles_and_manager(self):
        programs = human.programs_from_preview(ROWS)
        self.assertEqual(len(programs), 2)
        self.assertEqual(programs[0].title, "📦 PROGRAM · Millions of events & more")
        self.assertEqual(programs[0].handle, "[ap:5CKYAT]")
        self.assertEqual(programs[0].manager, "Manager-Session-223")
        self.assertEqual(programs[1].manager, "—")

    def test_manager_subsession_and_html(self):
        rows = list(ROWS)
        rows[1] = rows[1].replace("Manager-Session-223", "Manager-Session-223-a")
        rows[2] = rows[2].replace("#prog-root", "#<u>prog-root</u>")
        self.assertEqual(human.programs_from_preview(rows)[0].manager, "Manager-Session-223-a")

    def test_program_without_ap_uses_uuid(self):
        self.assertEqual(human.program(UUID, "PROGRAM · Example #prog-root").handle, UUID)

    def test_unrecognized_inventory_is_not_silently_partial(self):
        with self.assertRaisesRegex(RuntimeError, "inventory"):
            human.programs_from_preview(["format changed"])

    def test_parcel_carries_named_group_semantics(self):
        self.assertEqual(human.parcel(UUID), {
            "kind": "lightning_rod", "root_uuid": UUID,
            "match_scope": "name",
            "lightning": {"mode": "render_tags", "match_scope": "name", "tags": ["prog-human", "fog-dan"], "postfix_policy": "slice"},
            "sort_by": "modified", "sort_direction": "asc",
        })

    def test_flags(self):
        defaults = human.parser().parse_args(["Millions of events"])
        self.assertTrue(defaults.view)
        self.assertFalse(defaults.modal)
        self.assertIsNone(defaults.tab)
        custom = human.parser().parse_args(["MCI", "--no-view", "--modal", "--tab", "HUMAN-TEST"])
        self.assertFalse(custom.view)
        self.assertTrue(custom.modal)
        self.assertEqual(custom.tab, "HUMAN-TEST")
        self.assertTrue(human.parser().parse_args(["MCI", "--no-view", "--view"]).view)

    def test_visit_is_a_full_body_named_pick_with_stable_key(self):
        value = human.parcel(UUID)
        visit = human.view_visit(PROGRAM, value)
        self.assertEqual(visit["op"], "visit")
        self.assertRegex(visit["key"], r"^pick:parcel:[0-9a-z]+$")
        root = visit["root"]
        self.assertEqual(root["id"], 1)  # required by View's cleanEntry on opening a recent root
        self.assertEqual(root["selection"]["value"], value)
        self.assertTrue(root["selection"]["body"])  # phone opens the pages, not just headings
        self.assertIn("Millions of events", root["name"])
        renamed = human.Program(UUID, "Renamed program", PROGRAM.handle)
        self.assertEqual(human.view_visit(renamed, value)["key"], visit["key"])

    @unittest.skipUnless(shutil.which("node"), "Node is needed to compare the browser's actual root key")
    def test_view_key_matches_actual_browser_owner(self):
        source = Path(__file__).resolve().parents[1] / "transcription-pane/static/view-history.js"
        value = human.parcel(UUID)
        visit = human.view_visit(PROGRAM, value)
        script = "require('vm').runInThisContext(require('fs').readFileSync(process.argv[1], 'utf8') + '\\n;globalThis.h=opcViewHistory'); console.log(h.rootKey(JSON.parse(process.argv[2])));"
        reply = subprocess.run(["node", "-e", script, str(source), json.dumps(visit["root"])],
                               check=True, capture_output=True, text=True, timeout=10)
        self.assertEqual(reply.stdout.strip(), visit["key"])

    def test_view_uses_existing_endpoint(self):
        response = Mock()
        response.__enter__ = Mock(return_value=io.StringIO(json.dumps({"opened": {"key": "example"}})))
        response.__exit__ = Mock(return_value=False)
        with patch.object(human.urllib.request, "urlopen", return_value=response) as open_url:
            human.push_view(PROGRAM, human.parcel(UUID))
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, human.PANE + "/api/view/roots")
        self.assertEqual(request.method, "POST")
        self.assertEqual(json.loads(request.data), human.view_visit(PROGRAM, human.parcel(UUID)))


class Lookup(unittest.IsolatedAsyncioTestCase):
    async def test_literal_case_insensitive_fragment(self):
        wf = Mock(call=AsyncMock(return_value=json.dumps({"success": True, "preview_tree": ROWS})))
        found = await human.lookup(wf, "mILLIONS of EVENTS")
        self.assertEqual([p.uuid for p in found], [UUID])
        self.assertEqual(wf.call.call_count, 1)
        self.assertEqual(wf.call.call_args.args[2]["tags"], ["prog-root"])
        self.assertNotIn("tag_match", wf.call.call_args.args[2])  # daemon v1 supports prefix matching only
        self.assertEqual(await human.lookup(wf, "*"), [])  # fragment, not a smart query or regex

    async def test_multiple_matches_are_all_returned(self):
        wf = Mock(call=AsyncMock(return_value=json.dumps({"preview_tree": ROWS})))
        self.assertEqual(len(await human.lookup(wf, "PROGRAM")), 2)

    async def test_each_handle_reads_only_the_root(self):
        for handle in ("[ap:5CKYAT]", UUID, UUID[:8]):
            wf = Mock(call=AsyncMock(return_value=json.dumps({"root": {"id": UUID, "name": "[ap:5CKYAT] Program #prog-root"}})))
            self.assertEqual((await human.lookup(wf, handle))[0].uuid, UUID)
            self.assertEqual(wf.call.call_count, 1)
            family, verb, params = wf.call.call_args.args
            self.assertEqual((family, verb), ("node", "scry"))
            self.assertEqual(params["depth"], 0)
            self.assertEqual(params["root_handle"], handle)

    async def test_domain_failure_is_an_error_not_zero_matches(self):
        wf = Mock(call=AsyncMock(return_value='{"success":false,"error":{"message":"unavailable"}}'))
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            await human.lookup(wf, "MCI")


class Destinations(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, argv, matches, view_error=None):
        client = AsyncMock()
        out, err = io.StringIO(), io.StringIO()
        with patch.object(human, "WorkflowyHTTP", return_value=client), \
                patch.object(human, "lookup", AsyncMock(return_value=matches)), \
                patch.object(human, "push_view", side_effect=view_error) as view, \
                patch.object(human, "push_modal", AsyncMock(return_value="modal pushed")) as modal, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = await human.run(human.parser().parse_args(argv))
        return code, out.getvalue(), err.getvalue(), view, modal

    async def test_default_prints_only_one_json_line_and_publishes_view(self):
        code, out, err, view, modal = await self.invoke(["Millions"], [PROGRAM])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), human.parcel(UUID))
        self.assertEqual(len(out.splitlines()), 1)
        view.assert_called_once()
        modal.assert_not_called()
        self.assertIn("Recent roots", err)

    async def test_no_view_and_custom_modal_tab(self):
        code, _, _, view, modal = await self.invoke(["Millions", "--no-view", "--modal", "--tab", "HUMAN-TEST"], [PROGRAM])
        self.assertEqual(code, 0)
        view.assert_not_called()
        self.assertEqual(modal.call_args.args[1:], ([UUID], human.TAGS, "HUMAN-TEST", "tags"))
        self.assertEqual(modal.call_args.kwargs, {"sort_by": "modified"})

    async def test_ambiguous_and_missing_names_push_neither(self):
        for matches in ([], [PROGRAM, human.Program(OTHER, "Another", "[ap:WOFFA4]")]):
            code, out, err, view, modal = await self.invoke(["PROGRAM", "--modal"], matches)
            self.assertEqual(code, 2)
            self.assertEqual(out, "")
            view.assert_not_called()
            modal.assert_not_called()
            if matches:
                self.assertIn("Manager-Session-223", err)
                self.assertIn("[ap:WOFFA4]", err)

    async def test_view_failure_keeps_print_and_still_attempts_modal(self):
        code, out, err, _, modal = await self.invoke(["Millions", "--modal"], [PROGRAM], OSError("pane down"))
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out), human.parcel(UUID))
        self.assertIn("pane down", err)
        modal.assert_awaited_once()

    async def test_reused_modal_renderer_default_and_modified_order(self):
        for sort in (None, "modified"):
            wf = Mock(call=AsyncMock(return_value='{"success":true}'))
            await oc_view.push_modal(wf, [UUID], human.TAGS, "HUMAN-TEST", "tags", **({"sort_by": sort} if sort else {}))
            family, verb, params = wf.call.call_args.args
            self.assertEqual((family, verb), ("lightning_rod", "render_tags"))
            self.assertEqual(params["postfix_policy"], "slice")
            self.assertEqual(params["sort_by"], sort or "created")
            self.assertEqual(params["sort_direction"], "asc")
            self.assertTrue(params["push_to_modal"])
            self.assertEqual(params["target_tab"], "HUMAN-TEST")

    async def test_modal_domain_failure_is_not_reported_as_success(self):
        wf = Mock(call=AsyncMock(return_value='{"success":false,"error":{"message":"missing tab"}}'))
        with self.assertRaisesRegex(RuntimeError, "missing tab"):
            await oc_view.push_modal(wf, [UUID], human.TAGS, "HUMAN-TEST", "tags", sort_by="modified")

    async def test_modal_without_tab_uses_daemon_default(self):
        wf = Mock(call=AsyncMock(return_value='{"success":true}'))
        await oc_view.push_modal(wf, [UUID], human.TAGS, None, "tags", sort_by="modified")
        self.assertNotIn("target_tab", wf.call.call_args.args[2])

    async def test_existing_boom_navigator_fallback_keeps_created_order(self):
        wf = Mock(call=AsyncMock(side_effect=['{"empty_selection":true}', '{"success":true}']))
        note = await oc_view.push_modal(wf, [UUID], [], "BOOM")
        self.assertIn("NO NAVIGATOR ACCOUNT", note)
        probe, push = [call.args[2] for call in wf.call.call_args_list]
        self.assertTrue(probe["headings_only"])
        self.assertNotIn("push_to_modal", probe)
        self.assertTrue(push["push_to_modal"])
        self.assertEqual(push["target_tab"], "BOOM")
        self.assertEqual(push["sort_by"], "created")
        self.assertEqual(push["lens"]["tags"], ["prog-human"])

    async def test_multi_program_modified_order_uses_same_lens_owner(self):
        wf = Mock(call=AsyncMock(return_value='{"success":true}'))
        await oc_view.push_modal(wf, [UUID, OTHER], human.TAGS, "HUMAN-TEST", "tags", sort_by="modified")
        family, verb, params = wf.call.call_args.args
        self.assertEqual((family, verb), ("slice_group", "render"))
        self.assertEqual(params["sort_by"], "modified")
        self.assertEqual(params["lens"]["operation"], "union")


if __name__ == "__main__":
    unittest.main()
