"""U06 caller allocated native controls on durable Store/Host, no native inference."""
import asyncio
import json
import os
import uuid
from types import SimpleNamespace

import pytest
from claude_headless.host import Host, NativeSession
from test_claude_headless_host import setup, teardown, until


def test_actual_http_native_stdin_echo_lost_reply_and_busy_duplicate(tmp_path):
    from http.client import HTTPConnection
    import sys
    import threading
    from http.server import ThreadingHTTPServer
    from pathlib import Path
    from urllib.parse import urlencode
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'transcription-pane'))
    import server
    from claude_live import ClaudeFacade
    from claude_reader import ClaudeReader
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        session.update(role='Manager')
        history_root = tmp_path/'history'; history_root.mkdir()
        facade = ClaudeFacade(ClaudeReader([{'id':'fixture','root':str(history_root)}]), client)
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        httpd.claude_reader = facade
        thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
        port = httpd.server_address[1]
        def http(path, body=None, lost=False):
            c = HTTPConnection('127.0.0.1', port, timeout=10)
            c.request('POST' if body else 'GET', path,
                body=json.dumps(body, ensure_ascii=False).encode() if body else None,
                headers={'Content-Type':'application/json'} if body else {})
            if lost:
                c.close(); return
            response = c.getresponse(); result = response.status, json.loads(response.read()); c.close()
            return result
        try:
            code, inv = await asyncio.to_thread(http, '/api/claude/sessions')
            assert code == 200
            target = inv['sessions'][0]
            code, page = await asyncio.to_thread(http, '/api/claude/events?'+urlencode({
                'sessionRef':target['sessionRef'],'scopeRef':target['scopes'][0]['scopeRef']}))
            assert code == 200
            identity = {k:page['targetInfo'][k] for k in ('targetRef','executionId','generation')}
            body = {'schemaVersion':1,'backend':'claude',**identity,'messageUuid':str(uuid.uuid4()),
                'original':' \r\n🌈雪\x00\t'*20000+'TAIL ', 'submitted':'🌈雪\n'*20000+'TAIL'}
            code, reply = await asyncio.to_thread(http, '/api/claude/send', body)
            assert code == 200 and reply['receipt']['original'] == body['original']
            await until(lambda: host.store.get('receipts', session.sid, body['messageUuid']).get('completed'))
            code, reply = await asyncio.to_thread(http, '/api/claude/receipt?'+urlencode({
                **identity,'messageUuid':body['messageUuid']}))
            assert code == 200
            receipt = reply['receipt']
            assert receipt['original'] == body['original'] and receipt['submitted'] == body['submitted']
            assert all(receipt[k] for k in ('accepted','written','acknowledged','started','completed'))
            stdin = [json.loads(line) for line in host.store.path(session.sid,1,'stdin').read_bytes().splitlines()]
            assert [r['message']['content'] for r in stdin if r['type']=='user'] == [body['submitted']]
            # Drop the HTTP observer before its response, while the one native input stays owned.
            held = {**body,'messageUuid':str(uuid.uuid4()),'original':' original hold ','submitted':'hold'}
            await asyncio.to_thread(http, '/api/claude/send', held, True)
            await until(lambda: (host.store.get('receipts', session.sid, held['messageUuid']) or {}).get('acknowledged'))
            assert (await asyncio.to_thread(http, '/api/claude/receipt?'+urlencode({
                **identity,'messageUuid':held['messageUuid']})))[1]['receipt']['acknowledged']
            assert (await asyncio.to_thread(http, '/api/claude/send', held))[0] == 200
            assert (await asyncio.to_thread(http, '/api/claude/send', {**body,'messageUuid':str(uuid.uuid4())}))[0] == 409
            stdin = [json.loads(line) for line in host.store.path(session.sid,1,'stdin').read_bytes().splitlines()]
            assert sum(r['type']=='user' for r in stdin) == 2
            control = {'schemaVersion':1,'backend':'claude',**identity,'requestId':str(uuid.uuid4()),'operation':'interrupt'}
            assert (await asyncio.to_thread(http, '/api/claude/control', control))[0] == 200
            await until(lambda: not session.pending)
        finally:
            await asyncio.to_thread(httpd.shutdown)
            httpd.server_close(); thread.join()
            await teardown(host, serving)
    asyncio.run(scenario())


def test_begin_control_caller_id_native_readback_and_duplicate(tmp_path):
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        session.update(role='Manager')
        rid = str(uuid.uuid4())
        params = dict(execution_id=row['execution_id'], generation=1, request_id=rid,
            operation='set_model', model='sonnet', authority='fixture deliberate control')
        try:
            first = await client.acall('begin_control', **params)
            assert first['request_id'] == rid
            await until(lambda: host.store.get('controls', session.sid, rid).get('applied'))
            receipt = await client.acall('control_receipt', execution_id=row['execution_id'], request_id=rid)
            assert receipt['state'] == 'applied'
            assert receipt['effective']['model'] == 'claude-sonnet-5-5'
            assert receipt['effective']['effort'] == 'low'
            before = host.store.path(session.sid, 1, 'stdin').read_bytes()
            duplicate = await client.acall('begin_control', **params)
            assert duplicate['applied']
            assert host.store.path(session.sid, 1, 'stdin').read_bytes() == before
            with pytest.raises(Exception, match='different operation'):
                await client.acall('begin_control', **{**params, 'model': 'opus'})
            with pytest.raises(Exception, match='generation'):
                await client.acall('begin_control', **{**params, 'generation': True})
            session.update(role='worker')
            with pytest.raises(Exception, match='Manager-only'):
                await client.acall('begin_control', **{**params, 'request_id': str(uuid.uuid4())})
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_pending_control_detached_observer_and_strict_generation(tmp_path):
    async def scenario():
        host = Host(tmp_path/'state', tmp_path/'host.sock')
        sid, rid = str(uuid.uuid4()), str(uuid.uuid4())
        record = {'native_uuid': sid, 'execution_id': 'cc-'+sid, 'generation': 1,
            'role': 'Manager', 'state': 'ready', 'requested_model': 'haiku', 'requested_effort': 'low',
            'controls_effective': {'model': 'claude-haiku-5-5', 'effort': 'low'}}
        session = NativeSession(host, record, SimpleNamespace(returncode=None), None)
        (host.root/sid/'g000001').mkdir(parents=True)
        host.sessions[sid] = session
        params = dict(execution_id='cc-'+sid, generation=1, request_id=rid, operation='apply_effort',
                      effort='high', authority='fixture explicit')
        try:
            first = await host.begin_control(**params)
            assert first['state'] == 'pending'
            assert session.record['setting_control_pending']
            assert first['caller_request']['params']['effort'] == 'high'
            assert len(session.control_tasks) == 1
            duplicate = await host.begin_control(**params)
            assert duplicate['request_id'] == rid and session.writes.qsize() == 1
            with pytest.raises(RuntimeError, match='busy'):
                await host.begin_control(**{**params, 'request_id': str(uuid.uuid4())})
            # A native response later, with no observer/client still awaiting it.
            def respond(request, payload):
                event = {'type': 'control_response', 'response': {'subtype': 'success',
                    'request_id': request, 'response': payload}}
                session.project(event, json.dumps(event), session.event('fixture_reply'))
            respond(rid, {})
            await until(lambda: session.writes.qsize() == 2)
            readbacks = [r for r in host.store.records('controls', sid) if r.get('subtype') == 'get_settings']
            assert len(readbacks) == 1
            respond(readbacks[0]['request_id'], {'applied': {'model': 'claude-haiku-5-5', 'effort': 'high'}})
            await until(lambda: host.store.get('controls', sid, rid).get('applied'))
            assert not session.record['setting_control_pending']
            assert not session.control_tasks
        finally:
            for task in list(session.control_tasks):
                task.cancel()
            await asyncio.gather(*session.control_tasks, return_exceptions=True)
            host.store.db.close(); os.close(host.host_lock)
    asyncio.run(scenario())


def test_control_intent_exception_returns_existing_uncertain_id_without_second_write(tmp_path):
    async def scenario():
        host = Host(tmp_path/'state', tmp_path/'host.sock')
        sid, rid = str(uuid.uuid4()), str(uuid.uuid4())
        record = {'native_uuid':sid, 'execution_id':'cc-'+sid, 'generation':1,
            'role':'Manager', 'state':'ready', 'requested_model':'haiku', 'requested_effort':'low',
            'controls_effective':{'model':'claude-haiku-5-5', 'effort':'low'}}
        session = NativeSession(host, record, SimpleNamespace(returncode=None), None)
        (host.root/sid/'g000001').mkdir(parents=True)
        host.sessions[sid] = session
        original = session.enqueue
        def failed_after_intent(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('fixture observer failed after durable native intent')
        session.enqueue = failed_after_intent
        params = dict(execution_id='cc-'+sid,generation=1,request_id=rid,operation='apply_effort',
            effort='high',authority='fixture explicit')
        try:
            result = await host.begin_control(**params)
            assert result['request_id'] == rid and result['state'] == 'uncertain'
            assert result['caller_request']['params']['effort'] == 'high'
            assert session.writes.qsize() == 1
            repeated = await host.begin_control(**params)
            assert repeated['state'] == 'uncertain' and session.writes.qsize() == 1
            with pytest.raises(ValueError,match='different operation'):
                await host.begin_control(**{**params,'effort':'low'})
        finally:
            await asyncio.gather(*session.control_tasks,return_exceptions=True)
            host.store.db.close(); os.close(host.host_lock)
    asyncio.run(scenario())
