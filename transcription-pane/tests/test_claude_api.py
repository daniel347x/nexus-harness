import http.client
import json
import sys
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from claude_reader import ClaudeReader
from test_claude_reader import SESSION, record, uid, saturation_records


class ApiTests(unittest.TestCase):
    def test_audit_saturation_defaults_and_limited_prefix_are_whole_exact_http_pages(self):
        import claude_reader as cr
        rows = saturation_records()
        raw = "".join(json.dumps(r)+"\n" for r in rows).encode()
        self.assertGreater(len(raw), 5*1024*1024);self.assertLess(len(raw), 6*1024*1024)
        path=Path(self.temp.name)/(SESSION+'.jsonl');path.write_bytes(raw)
        for limit in (None, 16*1024*1024):
            with self.subTest(limit=limit):
                reader=ClaudeReader([{"id":"test", "root":self.temp.name}], **({"summary_limit":limit} if limit else {}))
                self.httpd.claude_reader=reader
                reader.sessions()  # configured-root discovery establishes inventory identity
                for _ in range(40):
                    index=reader._index("test",path)
                    if index.status in ("complete","index-limit"):break
                sid=reader._ref("session",("test",path));scope=reader._scope_ref(index,"main")
                address={"sessionRef":sid,"scopeRef":scope};seen=[];calls=[]
                for _ in range(10):
                    code,headers,page=self.request('/api/claude/events?'+urlencode(address))
                    self.assertEqual(code,200,page);self.assertEqual(headers['Cache-Control'],'no-store')
                    self.assertTrue(page['events']);self.assertLessEqual(len(page['events']),100)
                    if limit:
                        self.assertEqual(page['status'],'index-limit');self.assertIsNone(page['targetRef'])
                        marker=page['indexLimit'];self.assertIn('Later history not indexed',marker['message'])
                        self.assertEqual(marker['indexedRecords'],len(index.records))
                        self.assertEqual(marker['indexedBytes'],index.offset);self.assertEqual(marker['sourceBytes'],len(raw))
                        self.assertLessEqual(marker['summaryBytes'],limit)
                    else:
                        self.assertEqual(page['status'],'complete');self.assertIsNone(page['indexLimit'])
                    for e in page['events']:
                        seen.append(e['uuid']);calls.extend(e['calls'])
                        self.assertEqual(e['calls'][0]['inputBytes'],len(json.dumps(rows[len(seen)-1]['message']['content'][0]['input']).encode()))
                    if not page['hasMore']:
                        self.assertIsNone(page['nextCursor']);break
                    self.assertIsNotNone(page['nextCursor']);address['cursor']=page['nextCursor']
                self.assertEqual(seen,[uid(n) for n in range(1,len(index.records)+1)])
                self.assertEqual([c['id'] for c in calls],['call-'+str(n) for n in range(1,len(seen)+1)])
                if limit:
                    self.assertGreater(len(seen),200);self.assertLess(len(seen),400)
                    # Reload/repeated reads preserve useful prefix, not an empty loop.
                    self.assertEqual(len(self.request('/api/claude/events?'+urlencode({'sessionRef':sid,'scopeRef':scope}))[2]['events']),100)
                    # Ordinary poll/delta uses the SAME Handler summary adapter;
                    # exhausted prefix is not mistaken for complete source history.
                    code,_,delta=self.request('/api/claude/events?'+urlencode({'sessionRef':sid,'scopeRef':scope,'cursor':page['deltaCursor']}))
                    self.assertEqual(code,200,delta);self.assertEqual(delta['status'],'index-limit')
                    self.assertEqual(delta['events'],[]);self.assertIsNotNone(delta['indexLimit'])
                else:
                    self.assertEqual(len(seen),400)
                    for n in (0,228,399):
                        snap=reader.snapshot(calls[n]['callRef'],page['fileEpoch'])
                        expected=json.dumps(rows[n]['message']['content'][0]['input'])
                        self.assertEqual(snap['input'],expected);self.assertEqual(snap['inputBytes'],len(expected.encode()))

    def test_native_refine_context_full_witness_exact_prose_large_transcription_separate(self):
        path = Path(self.temp.name) / (SESSION + '.jsonl')
        text = 'native prose 🌈\n\n' * 12000 + 'TAIL'
        rows = [record(1, content=text),
                record(2, 1, kind='assistant', content=[{'type':'thinking','thinking':'NEVER THINKING'},
                    {'type':'text','text':'assistant prose'}, {'type':'tool_use','id':'c','name':'Read','input':{'secret':'NEVER INPUT'}}]),
                record(3, 2, content=[{'type':'tool_result','tool_use_id':'c','content':'NEVER OUTPUT'}], sourceToolAssistantUUID=uid(2))]
        path.write_text(''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in rows))
        self.httpd.claude_reader = ClaudeReader([{'id':'test','root':self.temp.name}])
        row = self.request('/api/claude/sessions')[2]['sessions'][0]
        page = self.request('/api/claude/events?'+urlencode({'sessionRef':row['sessionRef'],'scopeRef':row['scopes'][0]['scopeRef']}))[2]
        code, headers, ctx = self.request('/api/claude/context?'+urlencode({'targetRef':page['targetRef'],'targetRevision':17,'budgetBytes':400*1024}))
        self.assertEqual(code,200); self.assertEqual(headers['Cache-Control'],'no-store')
        self.assertEqual(ctx['targetRevision'],17)
        for key in ('targetRef','storeRef','sessionRef','scopeRef','leafRef','fileEpoch','leafUuid'):
            self.assertEqual(ctx[key],page['targetInfo'][key],key)
        self.assertEqual(ctx['text'],'[USER]\n'+text+'\n\n---\n\n[ASSISTANT]\nassistant prose')
        self.assertEqual(ctx['bytes'],len(ctx['text'].encode()))
        for excluded in ('NEVER THINKING','NEVER INPUT','NEVER OUTPUT'):
            self.assertNotIn(excluded,ctx['text'])
        path.write_text(json.dumps(record(1,content='replacement'))+'\n')
        self.assertEqual(self.request('/api/claude/context?'+urlencode({'targetRef':page['targetRef'],'targetRevision':17}))[0],409)

    def test_context_projection_source_replacement_inside_read_never_returns_old_success(self):
        row = self.request('/api/claude/sessions')[2]['sessions'][0]
        page = self.request('/api/claude/events?'+urlencode({'sessionRef':row['sessionRef'],'scopeRef':row['scopes'][0]['scopeRef']}))[2]
        original = self.httpd.claude_reader._read
        path = Path(self.temp.name)/(SESSION+'.jsonl')
        def after_read(index, rec):
            value = original(index, rec)
            other = path.with_suffix('.replacement')
            other.write_text(json.dumps(record(1,content='new source'))+'\n')
            other.replace(path)
            return value
        with patch.object(self.httpd.claude_reader,'_read',side_effect=after_read):
            code,_,body = self.request('/api/claude/context?'+urlencode({'targetRef':page['targetRef'],'targetRevision':0}))
        self.assertEqual(code,409); self.assertNotIn('text',body)

    def test_fixed_sessions_page_actual_assets_and_origin_no_preflight(self):
        expected = (Path(__file__).resolve().parents[1] / 'static' / 'sessions.html').read_bytes()
        for headers, code in (({}, 200), ({'Host':'foreign.example'},403),
                              ({'Origin':'http://127.0.0.1:9899'},403), ({'Sec-Fetch-Site':'cross-site'},403)):
            conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
            conn.request('GET', '/sessions', headers=headers)
            response = conn.getresponse(); body = response.read()
            self.assertEqual(response.status, code)
            self.assertEqual(response.getheader('Cache-Control'), 'no-store')
            self.assertIsNone(response.getheader('Access-Control-Allow-Origin'))
            if code == 200:
                self.assertEqual(body, expected)
                self.assertIn(b'/tool-preview-core.js', body)
                self.assertNotIn(b'tool-previews.js', body)
            conn.close()
        self.assertEqual(self.request('/sessions','POST')[0],405)
        self.assertEqual(self.request('/sessions','OPTIONS')[0],403)

    def test_production_history_http_sender_one_mailbox_existing_inspector_exact_raw(self):
        large = "🌈" * 50001 + "TAIL-END"
        path = Path(self.temp.name) / (SESSION + '.jsonl')
        rows = [record(1, kind='assistant', content=[{'type':'tool_use','id':'call','name':'mcp__workflowy__anchor',
                                                    'input':{'params':{'content':large,'target':'[ap:ABC123]'},'verb':'point.capture'}}]),
                record(2,1,content=[{'type':'tool_result','tool_use_id':'call','content':'output\n\tretained'}],sourceToolAssistantUUID=uid(1))]
        path.write_text(''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in rows))
        self.httpd.claude_reader = ClaudeReader([{'id':'test','root':self.temp.name}])
        # Load the actual history/inspector VM fixtures' helper prefix only, not a
        # second production UI or test-suite run. Both scripts execute real owners.
        program = r"""
const fs=require('node:fs'),assert=require('node:assert/strict'),path=require('node:path');
const dir=process.argv[1],base=process.argv[2];
function helper(file,marker){const text=fs.readFileSync(path.join(dir,file),'utf8');return new Function('require','__dirname','process',text.slice(0,text.indexOf(marker))+'\nreturn {fixture};')(require,dir,process).fixture;}
const history=helper('claude-sessions.test.cjs',"test('real page"),inspector=helper('tool-inspector.test.cjs',"test('native uses");
(async()=>{
 let inventory;
 const h=history(async(url,opts)=>{const response=await fetch(base+url,opts);if(url.startsWith('/api/claude/sessions'))inventory=await response.clone().json();return response;});
  await h.api.list(true);await h.api.browse(inventory.sessions[0]); // this fixture supplies stored native history, not a host facade
 assert.equal(h.api.state.accepted,null);assert.equal(h.api.state.rows.size,2);
 h.api.state.rows.values().next().value.calls[0].node.onclick();
 for(let i=0;i<100&&h.api.state.posting;i++)await new Promise(r=>setTimeout(r,10));
 assert.ok(h.api.state.accepted,'real same-origin Inspect ACK');
 const mail=await (await fetch(base+'/api/tool-selection')).json();assert.equal(mail.selection.backend,'claude');
 const raw=await (await fetch(base+'/api/tool-record?epoch='+mail.epoch+'&revision='+mail.revision)).json();
 assert.equal(raw.identity.callRef,h.api.state.accepted.callRef);assert.equal(raw.provenance,'recorded-jsonl');
 const p=inspector();p.mailbox(mail);p.response(raw);await p.api.pollInspection();
 assert.equal(p.api.state().snapshot.input,raw.snapshot.input);assert.equal(p.api.state().snapshot.output,'output\n\tretained');
 assert.ok(new TextEncoder().encode(raw.snapshot.input).length>200000);assert.equal(raw.snapshot.inputBytes,new TextEncoder().encode(raw.snapshot.input).length);await p.el('inspector-copy-input').onclick();assert.equal(p.copies[0],raw.snapshot.input);
 assert.equal(p.el('inspector-input').focusCount,0);assert.ok(!raw.part);assert.equal(h.messages.length,0);
 console.log('real history -> Handler -> one mailbox -> reader -> existing inspector: exact raw/copy/count');
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
        result = subprocess.run(['node','-e',program,str(Path(__file__).resolve().parent),f'http://127.0.0.1:{self.port}'],
                                text=True,capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_delta_completion_updates_original_call_summary_without_snapshots_or_bodies(self):
        path = Path(self.temp.name) / (SESSION + '.jsonl')
        first = record(1,kind='assistant',content=[{'type':'tool_use','id':'call','name':'Read','input':{'x':1}}])
        path.write_text(json.dumps(first)+'\n')
        self.httpd.claude_reader = ClaudeReader([{'id':'test','root':self.temp.name}])
        row = self.request('/api/claude/sessions')[2]['sessions'][0]
        args = {'sessionRef':row['sessionRef'],'scopeRef':row['scopes'][0]['scopeRef']}
        initial = self.request('/api/claude/events?'+urlencode(args))[2]
        self.assertEqual(initial['events'][0]['calls'][0]['status'],'pending-or-missing')
        output = 'secret raw output not in timeline' * 10000
        with path.open('a') as handle:
            handle.write(json.dumps(record(2,1,content=[{'type':'tool_result','tool_use_id':'call','content':output}],sourceToolAssistantUUID=uid(1)))+'\n')
        with patch.object(self.httpd.claude_reader,'snapshot',side_effect=AssertionError('no per-row snapshot')):
            code,_,delta = self.request('/api/claude/events?'+urlencode({**args,'cursor':initial['deltaCursor'],'fileEpoch':initial['fileEpoch']}))
        self.assertEqual(code,200)
        # The call's and its result's record times ride along (none recorded here), for the row's total time.
        self.assertEqual(delta['summaryUpdates'],[{'recordRef':initial['events'][0]['recordRef'],'block':0,'id':'call','status':'stored','outputBytes':len(output.encode()),
                                                   'time':None,'resultTime':None}])
        self.assertNotIn(output,json.dumps(delta))

    def test_append_inside_summary_adapter_window_cannot_change_frozen_delta_leaf(self):
        path = Path(self.temp.name) / (SESSION + '.jsonl')
        first = record(1,kind='assistant',content=[{'type':'tool_use','id':'call','name':'Read','input':{}}])
        path.write_text(json.dumps(first)+'\n')
        self.httpd.claude_reader = ClaudeReader([{'id':'test','root':self.temp.name}])
        row = self.request('/api/claude/sessions')[2]['sessions'][0]
        args = {'sessionRef':row['sessionRef'],'scopeRef':row['scopes'][0]['scopeRef']}
        initial = self.request('/api/claude/events?'+urlencode(args))[2]
        with path.open('a') as handle:
            handle.write(json.dumps(record(2,1,content=[{'type':'tool_result','tool_use_id':'call','content':'A'}],sourceToolAssistantUUID=uid(1)))+'\n')
        original = self.httpd.claude_reader.events
        def after_page(**query):
            response = original(**query)
            with path.open('a') as handle:
                handle.write(json.dumps(record(3,2,content=[{'type':'tool_result','tool_use_id':'call','content':'conflicting future'}],sourceToolAssistantUUID=uid(1)))+'\n')
            return response
        with patch.object(self.httpd.claude_reader,'events',side_effect=after_page):
            code,_,delta = self.request('/api/claude/events?'+urlencode({**args,'cursor':initial['deltaCursor']}))
        self.assertEqual(code,200)
        self.assertEqual(delta['summaryUpdates'][0]['status'],'stored')
        self.assertEqual(delta['summaryUpdates'][0]['outputBytes'],1)

    def test_real_api_shared_core_different_tail_unicode_order_and_raw_authority(self):
        previews = []
        core = Path(__file__).resolve().parents[2] / "chrome-extension/codenomad-session-watcher/tool-preview-core.js"
        program = "require('vm').runInThisContext(require('fs').readFileSync(process.argv[1],'utf8')); const data=JSON.parse(require('fs').readFileSync(0,'utf8')); console.log(opcToolPreview.preview(data,'mcp__workflowy__anchor',{descriptor:true}));"
        for tail in ("TAIL-A😀", "TAIL-B😀"):
            content = "🌈" * 200000 + tail
            inp = {"params": {"content": content, "middle": "x" * 500, "tieFirst": "aa", "tieSecond": "bb", "target": 1,
                              "nested": {"long": "q" * 129, "small": 0}}, "verb": "point.capture"}
            path = Path(self.temp.name) / (SESSION + ".jsonl")
            path.write_text(json.dumps(record(1, kind="assistant", content=[{"type":"tool_use", "id":"call", "name":"mcp__workflowy__anchor", "input":inp}])) + "\n")
            self.httpd.claude_reader = ClaudeReader([{"id":"test", "root":self.temp.name}])
            row = self.request('/api/claude/sessions')[2]['sessions'][0]
            status, headers, page = self.request('/api/claude/events?' + urlencode({"sessionRef":row['sessionRef'], "scopeRef":row['scopes'][0]['scopeRef']}))
            self.assertEqual(status, 200); self.assertEqual(headers['Cache-Control'], 'no-store')
            call = page['events'][0]['calls'][0]
            original = json.dumps(call['previewData'])
            output = subprocess.run(['node', '-e', program, str(core)], input=original, text=True, capture_output=True, timeout=10, check=True).stdout.strip()
            parsed = json.loads(output)
            self.assertEqual(list(parsed)[0], 'verb')
            self.assertEqual(list(parsed['params'])[:3], ['target', 'tieFirst', 'tieSecond'])
            self.assertLess(list(parsed['params']).index('middle'), list(parsed['params']).index('content'))
            self.assertEqual(list(parsed['params']['nested']), ['long', 'small'])
            self.assertEqual(parsed['params']['content'], content[:64] + '…' + content[-64:])
            self.assertEqual(json.dumps(call['previewData']), original)
            snapshot = self.httpd.claude_reader.snapshot(call['callRef'], page['fileEpoch'])
            self.assertEqual(json.loads(snapshot['input']), inp)
            self.assertEqual(call['inputBytes'], snapshot['inputBytes'])
            self.assertIsNone(call['outputBytes'])
            previews.append(output)
        self.assertNotEqual(previews[0], previews[1])

    def test_fixed_preview_core_route_is_exact_shared_bytes_and_origin_bounded(self):
        core = Path(__file__).resolve().parents[2] / "chrome-extension/codenomad-session-watcher/tool-preview-core.js"
        for headers, expected in (({}, 200), ({"Origin": "http://127.0.0.1:9899"}, 403),
                                  ({"Host": "foreign.example"}, 403), ({"Sec-Fetch-Site": "cross-site"}, 403)):
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            conn.request('GET', '/tool-preview-core.js', headers=headers)
            response = conn.getresponse(); body = response.read()
            self.assertEqual(response.status, expected)
            self.assertEqual(response.getheader('Cache-Control'), 'no-store')
            self.assertIsNone(response.getheader('Access-Control-Allow-Origin'))
            if expected == 200:
                self.assertEqual(body, core.read_bytes())
            conn.close()
        self.assertEqual(self.request('/tool-preview-core.js', 'POST')[0], 405)
        status, headers, _ = self.request('/tool-preview-core.js', 'OPTIONS', {"Origin":"https://foreign.example"})
        self.assertEqual(status, 403)
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.assertEqual(self.request('/tool-previews.js')[0], 404)  # no generic extension-file route

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        path = Path(self.temp.name) / (SESSION + ".jsonl")
        path.write_text(json.dumps(record(1)) + "\n")
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.claude_reader = ClaudeReader([{"id": "test", "root": self.temp.name}])
        self.mailbox_patch = patch.object(server, "_tools", server.ToolMailbox())
        self.mailbox_patch.start()
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_port

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        self.temp.cleanup()
        self.mailbox_patch.stop()

    def request(self, path, method="GET", headers=None, data=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, body=data, headers=headers or {})
        response = conn.getresponse()
        body = response.read()
        result = response.status, dict(response.getheaders()), json.loads(body)
        conn.close()
        return result

    def test_real_routes_and_schema_echo(self):
        status, headers, result = self.request("/api/claude/sessions")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        row = result["sessions"][0]
        status, _, page = self.request("/api/claude/events?" + urlencode({"sessionRef": row["sessionRef"], "scopeRef": row["scopes"][0]["scopeRef"]}))
        self.assertEqual(status, 200)
        status, _, context = self.request("/api/claude/context?" + urlencode({"targetRef": page["targetRef"], "targetRevision": 7}))
        self.assertEqual(status, 200)
        self.assertEqual(context["targetRevision"], 7)
        self.assertEqual(context["text"], "[USER]\nhello")
        self.assertEqual(context["backend"], "claude")
        self.assertEqual(context["schemaVersion"], 1)

    def test_events_http_page_carries_the_history_context_readout_and_source_progress(self):
        path = Path(self.temp.name) / (SESSION + ".jsonl")
        path.write_text(json.dumps(record(1)) + "\n" + json.dumps(record(2, kind="assistant", content="reply",
            timestamp="2026-10-14T19:13:03.797Z",
            message={"role": "assistant", "model": "claude-opus-5-5", "stop_reason": "end_turn", "usage": {
                "input_tokens": 2, "cache_read_input_tokens": 293869,
                "cache_creation_input_tokens": 375, "output_tokens": 1293}})) + "\n")
        row = self.request("/api/claude/sessions")[2]["sessions"][0]
        status, _, page = self.request("/api/claude/events?" + urlencode(
            {"sessionRef": row["sessionRef"], "scopeRef": row["scopes"][0]["scopeRef"]}))
        self.assertEqual(status, 200)
        readout = page["contextReadout"]
        self.assertEqual(readout["state"], "measured")
        self.assertEqual(readout["occupancy_tokens"], 294246)
        self.assertIsNone(readout["window_tokens"])  # tokens are shown even without a window
        self.assertIsNone(readout["fraction_used"])
        self.assertEqual(readout["source"], "windows-history")  # the fixture store is not native
        self.assertEqual(readout["identity"]["uuid"], SESSION)
        size = path.stat().st_size
        self.assertEqual(page["sourceProgress"]["indexedBytes"], size)
        self.assertEqual(page["sourceProgress"]["sourceBytes"], size)
        self.assertEqual(page["sourceProgress"]["kind"], "native-transcript")

    def test_target_revalidation_echoes_reader_owned_full_identity_and_labels_without_binding(self):
        row = self.request('/api/claude/sessions')[2]['sessions'][0]
        scope = next(s for s in row['scopes'] if s['label'] == 'main')
        page = self.request('/api/claude/events?' + urlencode({'sessionRef':row['sessionRef'], 'scopeRef':scope['scopeRef']}))[2]
        code, headers, validated = self.request('/api/claude/events?' + urlencode({'targetRef':page['targetRef']}))
        self.assertEqual(code, 200); self.assertEqual(headers['Cache-Control'], 'no-store')
        labels = validated['targetInfo']
        for key in ('sessionRef','scopeRef','leafRef','fileEpoch','targetRef'):
            self.assertEqual(labels[key], validated[key])
        self.assertEqual(labels['uuid'], row['uuid']); self.assertEqual(labels['title'], row['title'])
        self.assertEqual(labels['source'], row['source']); self.assertEqual(labels['scope'], 'main')
        self.assertIsNone(labels['bindingRef']); self.assertNotIn('path', labels)
        self.assertNotIn('events', labels); self.assertNotIn('text', labels)
        source = Path(self.temp.name) / (SESSION + '.jsonl')
        source.write_text(json.dumps(record(9, content='replacement')) + '\n')
        self.assertEqual(self.request('/api/claude/events?' + urlencode({'targetRef':page['targetRef']}))[0],409)

    def test_origin_host_site_and_no_cors(self):
        for headers in ({"Host": "evil:9911"}, {"Host": "127.0.0.1:9911"},
                        {"Origin": "http://127.0.0.1:9899"}, {"Sec-Fetch-Site": "cross-site"},
                        {"Origin": "https://example.com"}):
            status, response_headers, _ = self.request("/api/claude/sessions", headers=headers)
            self.assertEqual(status, 403)
            self.assertNotIn("Access-Control-Allow-Origin", response_headers)
        self.assertEqual(self.request("/api/claude/sessions", headers={"Origin": f"http://127.0.0.1:{self.port}"})[0], 200)
        self.assertEqual(self.request("/api/claude/sessions", "OPTIONS")[0], 403)

    def test_unknown_duplicate_empty_path_fields_and_methods(self):
        for suffix in ("?path=/etc/passwd", "?cursor=x&cursor=y", "?cursor=", "?workspace=x&unknown=x"):
            self.assertEqual(self.request("/api/claude/sessions" + suffix)[0], 400)
        self.assertEqual(self.request("/api/claude/context")[0], 400)
        self.assertEqual(self.request("/api/claude/sessions", "POST")[0], 405)
        self.assertEqual(self.request("/api/claude/not-real")[0], 404)

    def test_removed_cockpit_routes_absent_and_history_send_cannot_reach_opencode(self):
        # The terminal cockpit's arm/release/binding/delivery stay absent. U06's
        # independent Manager Send/control refuses malformed/unregistered input
        # and must never reach OpenCode Send or opencode_json.
        for route in ("arm", "release", "binding", "delivery"):
            with self.subTest(route=route):
                self.assertEqual(self.request("/api/claude/" + route)[0], 404)
                self.assertEqual(self.request("/api/claude/" + route, "POST")[0], 405)
                self.assertEqual(self.request("/api/claude/" + route, "POST",
                                 {"Content-Type": "application/json"}, json.dumps({"schemaVersion": 1}))[0], 405)
        self.assertEqual(self.request("/api/claude/delivery?deliveryId=operation-1")[0], 404)
        shaped = json.dumps({"text": "hello", "sessionId": "ses_abc123", "targetMode": "follow", "delivery": "queue"})
        with patch.object(server, "opencode_json", side_effect=AssertionError("Claude Send reached OpenCode")) as oc, \
             patch.object(server, "send_prompt", side_effect=AssertionError("Claude Send reached OpenCode Send")) as bridge:
            code, headers, refusal = self.request("/api/claude/send", "POST", {"Content-Type": "application/json"}, shaped)
        self.assertEqual(code, 400, refusal)
        self.assertEqual(refusal["status"], "schema")
        self.assertEqual(refusal["backend"], "claude")
        self.assertIn("schemaVersion", refusal["error"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual((oc.call_count, bridge.call_count), (0, 0))
        self.assertEqual(self.request("/api/claude/send")[0], 405)
        self.assertEqual(self.request("/api/claude/send", "POST", {}, "{}")[0], 403)
        self.assertEqual(self.request("/api/claude/send", "POST", {"Content-Type": "text/plain"}, "{}")[0], 403)
        self.assertEqual(self.request("/api/claude/send?extra=1", "POST", {"Content-Type": "application/json"}, "{}")[0], 403)
        for headers in ({"Content-Type": "application/json", "Origin": "http://127.0.0.1:9899"},
                        {"Content-Type": "application/json", "Host": "foreign.example"},
                        {"Content-Type": "application/json", "Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.request("/api/claude/send", "POST", headers, "{}")[0], 403)

    def native_click(self):
        path = Path(self.temp.name) / (SESSION + ".jsonl")
        # Lexical opaque payload stays raw through reader -> same mailbox -> HTTP.
        large = "🌈\n" * 25000 + "TAIL"
        first = json.dumps(record(1, kind="assistant", content=[{"type":"tool_use", "id":"call", "name":"mcp__workflowy__anchor", "input":"REPLACE"}]))
        first = first.replace('"REPLACE"', '{ "big":900719925474099312345,"n":-0,"n":1e+09,"doc":' + json.dumps(large) + ' }')
        second = record(2, 1, content=[{"type":"tool_result", "tool_use_id":"call", "content":"stub",
                                       "savedArtifact":{"relativePath":"out.txt", "completeness":"limited"}}], sourceToolAssistantUUID=uid(1))
        path.write_text(first + "\n" + json.dumps(second) + "\n")
        saved = path.parent / "tool-results/out.txt"; saved.parent.mkdir(); saved.write_text(large)
        row = self.request('/api/claude/sessions')[2]['sessions'][0]
        page = self.request('/api/claude/events?' + urlencode({"sessionRef":row['sessionRef'], "scopeRef":row['scopes'][0]['scopeRef']}))[2]
        d = {"schemaVersion":1, "backend":"claude", "callRef":page['events'][0]['calls'][0]['callRef'],
             "fileEpoch":page['fileEpoch'], "selectorId":"fixture-page", "sequence":1, "observedAt":int(server.time.time()*1000)}
        return d, large

    def test_native_inspect_same_mailbox_exact_raw_copy_counts_and_artifact(self):
        d, large = self.native_click()
        origin = f'http://127.0.0.1:{self.port}'
        status, headers, ack = self.request('/api/claude/inspect', 'POST', {"Content-Type":"application/json", "Origin":origin}, json.dumps(d))
        self.assertEqual(status, 200); self.assertTrue(ack['ok'])
        self.assertEqual(headers['Cache-Control'], 'no-store'); self.assertNotIn('Access-Control-Allow-Origin', headers)
        mail = self.request('/api/tool-selection', headers={"Origin":origin})[2]
        self.assertEqual(mail['revision'], ack['revision']); self.assertEqual(mail['selection'], ack['selection'])
        query = urlencode({k: mail[k] for k in ('epoch', 'revision')})
        status, headers, data = self.request('/api/tool-record?' + query, headers={"Origin":origin})
        self.assertEqual(status, 200); self.assertNotIn('part', data)
        raw = data['snapshot']
        self.assertIn('900719925474099312345,"n":-0,"n":1e+09', raw['input'])
        self.assertEqual(raw['inputBytes'], len(raw['input'].encode('utf-8')))
        self.assertEqual(raw['output'], 'stub'); self.assertEqual(raw['outputBytes'], 4)
        args = {k: raw[k] for k in ('artifactRef', 'callRef', 'fileEpoch')}
        self.assertEqual(self.request('/api/claude/artifact?' + urlencode(args))[2]['text'], large)
        self.assertEqual(self.request('/api/tool-record?' + query)[2]['snapshot']['output'], 'stub')
        for path in ('/api/tool-selection', '/api/tool-record?' + query):
            self.assertEqual(self.request(path, headers={"Host":"foreign.example"})[0], 403)
            self.assertEqual(self.request(path, headers={"Origin":"http://127.0.0.1:9899"})[0], 403)
        self.assertEqual(self.request('/api/tool-selection/clear', 'POST', {"Content-Type":"application/json", "Origin":origin}, json.dumps({k:mail[k] for k in ('epoch','revision')}))[0], 200)
        self.assertIsNone(server._tools.snapshot()['selection'])

    def test_native_inspect_origin_method_bounded_json_duplicate_and_unknown_fields(self):
        d, _ = self.native_click()
        for headers in ({"Origin":"http://127.0.0.1:9899"}, {"Host":"foreign.example"},
                        {"Origin":"https://foreign.example"}, {"Sec-Fetch-Site":"cross-site"}):
            status, headers, _ = self.request('/api/claude/inspect', 'POST', {"Content-Type":"application/json", **headers}, json.dumps(d))
            self.assertEqual(status, 403); self.assertNotIn('Access-Control-Allow-Origin', headers)
        for method in ('GET','HEAD','PUT','PATCH','DELETE'):
            if method == 'HEAD': continue  # HEAD has no JSON response body
            self.assertEqual(self.request('/api/claude/inspect', method)[0], 405)
        self.assertEqual(self.request('/api/claude/inspect', 'OPTIONS')[0], 403)
        for raw, code in ((json.dumps({**d,"toolName":"browser-label"}),400), (json.dumps(d)[:-1]+',"sequence":2}',400),
                          ('[]',400), ('x'*32769,413)):
            self.assertEqual(self.request('/api/claude/inspect','POST',{"Content-Type":"application/json"},raw)[0],code)
        self.assertEqual(self.request('/api/claude/inspect?unexpected=1','POST',{"Content-Type":"application/json"},json.dumps(d))[0],403)
        self.assertEqual(self.request('/api/claude/inspect','POST',{"Content-Type":"text/plain"},json.dumps(d))[0],403)
        self.assertEqual(server._tools.revision, 0)

    def test_http_diagnostics_delta_and_stale_epoch(self):
        path = Path(self.temp.name) / (SESSION + ".jsonl")
        path.write_text("not-json\n" * 205 + json.dumps(record(1)) + "\n")
        row = self.request("/api/claude/sessions")[2]["sessions"][0]
        address = {"sessionRef": row["sessionRef"], "scopeRef": row["scopes"][0]["scopeRef"]}
        page = self.request("/api/claude/events?" + urlencode(address))[2]
        target = page["targetRef"]
        count = len(page["diagnostics"])
        while page["hasMore"]:
            status, headers, page = self.request("/api/claude/events?" + urlencode({"targetRef": target, "cursor": page["nextCursor"]}))
            self.assertEqual(status, 200)
            self.assertEqual(headers["Cache-Control"], "no-store")
            count += len(page["diagnostics"])
        self.assertEqual(count, 205)
        with path.open("a") as f:
            f.write(json.dumps(record(2, 1, content="new 🌈")) + "\n")
        status, _, delta = self.request("/api/claude/events?" + urlencode({"targetRef": target, "cursor": page["deltaCursor"]}))
        self.assertEqual(status, 200)
        self.assertEqual(delta["change"], "append")
        self.assertEqual(delta["events"][0]["text"], "new 🌈")
        path.write_text(json.dumps(record(3, content="replacement")) + "\n")
        self.assertEqual(self.request("/api/claude/events?" + urlencode({"targetRef": target}))[0], 409)

    def test_actual_http_full_envelope_bound_and_outage(self):
        reader = self.httpd.claude_reader
        with patch.object(reader, "sessions", return_value={"text": "x" * (8 * 1024 * 1024)}):
            status, _, body = self.request("/api/claude/sessions")
            self.assertEqual(status, 413)
            self.assertEqual(body["status"], "response-limit")
            self.assertNotIn("text", body)
        with patch.object(reader, "sessions", side_effect=OSError("temporary outage")):
            status, _, body = self.request("/api/claude/sessions")
            self.assertEqual(status, 502)
            self.assertEqual(body["status"], "read-failure")

    def test_http_exact_envelope_limit_and_over_no_prefix(self):
        limit = 8 * 1024 * 1024
        for length, expected in ((limit - len(b'{"text":""}'), 200), (limit - len(b'{"text":""}') + 1, 413)):
            with self.subTest(length=length), patch.object(self.httpd.claude_reader, "sessions", return_value={"text": "x" * length}):
                status, headers, body = self.request("/api/claude/sessions")
                self.assertEqual(status, expected)
                self.assertEqual(headers["Cache-Control"], "no-store")
                if expected == 200:
                    self.assertEqual(body["text"], "x" * length)
                else:
                    self.assertNotIn("text", body)

    def test_http_artifact_exact_witness_and_context_schema(self):
        path = Path(self.temp.name) / (SESSION + ".jsonl")
        rows = [record(1, kind="assistant", content=[{"type": "tool_use", "id": "call", "name": "Read", "input": {}}]),
                record(2, 1, content=[{"type": "tool_result", "tool_use_id": "call", "content": "stub",
                                       "savedArtifact": {"relativePath": "out.txt", "completeness": "limited"}}], sourceToolAssistantUUID=uid(1))]
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        saved = path.parent / "tool-results" / "out.txt"
        saved.parent.mkdir()
        saved.write_text("saved 🌈\nfull text")
        row = self.request("/api/claude/sessions")[2]["sessions"][0]
        page = self.request("/api/claude/events?" + urlencode({"sessionRef": row["sessionRef"], "scopeRef": row["scopes"][0]["scopeRef"]}))[2]
        snap = self.httpd.claude_reader.snapshot(page["events"][0]["calls"][0]["callRef"], page["fileEpoch"])
        args = {k: snap[k] for k in ("artifactRef", "callRef", "fileEpoch")}
        status, headers, body = self.request("/api/claude/artifact?" + urlencode(args))
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(body["text"], saved.read_text())
        self.assertEqual(body["completeness"], "limited")
        for key in ("callRef", "fileEpoch"):
            self.assertEqual(self.request("/api/claude/artifact?" + urlencode({**args, key: "wrong"}))[0], 409)
        for key in args:
            self.assertEqual(self.request("/api/claude/artifact?" + urlencode({k: v for k, v in args.items() if k != key}))[0], 400)
        saved.unlink()
        self.assertEqual(self.request("/api/claude/artifact?" + urlencode(args))[0], 404)
        self.assertEqual(self.httpd.claude_reader.snapshot(args["callRef"], args["fileEpoch"])["output"], "stub")
        for bad in (-1, 0, 5119, 409601, "1.5", "true"):
            self.assertEqual(self.request("/api/claude/context?" + urlencode({"targetRef": page["targetRef"], "targetRevision": 1, "budgetBytes": bad}))[0], 400)
        path.write_text(json.dumps(record(3)) + "\n")
        self.assertEqual(self.request("/api/claude/artifact?" + urlencode(args))[0], 409)

    def test_http_scope_and_cursor_from_another_session_never_retargets(self):
        other = "00000000-0000-4000-8000-000000000002"
        (Path(self.temp.name) / (other + ".jsonl")).write_text(json.dumps(record(2, sessionId=other)) + "\n")
        rows = self.request("/api/claude/sessions")[2]["sessions"]
        self.assertEqual(len(rows), 2)
        address = {"sessionRef": rows[0]["sessionRef"], "scopeRef": rows[0]["scopes"][0]["scopeRef"]}
        page = self.request("/api/claude/events?" + urlencode(address))[2]
        wrong = {"sessionRef": rows[1]["sessionRef"], "scopeRef": rows[0]["scopes"][0]["scopeRef"]}
        self.assertEqual(self.request("/api/claude/events?" + urlencode(wrong))[0], 409)
        wrong = {"sessionRef": rows[1]["sessionRef"], "scopeRef": rows[1]["scopes"][0]["scopeRef"], "cursor": page["deltaCursor"]}
        self.assertEqual(self.request("/api/claude/events?" + urlencode(wrong))[0], 409)
        self.assertEqual(self.request("/api/claude/events?" + urlencode(address))[2]["sessionRef"], rows[0]["sessionRef"])

    def test_http_branch_choice_scope_mismatch_and_index_eviction(self):
        path = Path(self.temp.name) / (SESSION + ".jsonl")
        path.write_text("".join(json.dumps(r) + "\n" for r in (record(1), record(2, 1, content="A"), record(3, 1, content="B"))))
        row = self.request("/api/claude/sessions")[2]["sessions"][0]
        address = {"sessionRef": row["sessionRef"], "scopeRef": row["scopes"][0]["scopeRef"]}
        status, _, choices = self.request("/api/claude/events?" + urlencode(address))
        self.assertEqual(status, 200)
        self.assertEqual(choices["status"], "ambiguous")
        self.assertIsNone(choices["targetRef"])
        self.assertEqual(len(choices["branches"]), 2)
        status, _, chosen = self.request("/api/claude/events?" + urlencode({**address, "leafRef": choices["branches"][0]["leafRef"]}))
        self.assertEqual(status, 200)
        self.assertEqual([e["text"] for e in chosen["events"]], ["hello", "A"])
        self.assertEqual(self.request("/api/claude/events?" + urlencode({**address, "targetRef": chosen["targetRef"]}))[0], 400)
        self.httpd.claude_reader.files.clear()
        status, _, stale = self.request("/api/claude/events?" + urlencode({"targetRef": chosen["targetRef"]}))
        self.assertEqual(status, 409)
        self.assertEqual(stale["status"], "stale-epoch")


if __name__ == "__main__":
    unittest.main()
