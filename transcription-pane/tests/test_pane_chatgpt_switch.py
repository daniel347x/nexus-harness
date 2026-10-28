"""Offline GPT native selection receipts and the desktop/Serve account routes.

Every native read, activation and executor is injected; only temporary SQLite state and
an ephemeral loopback Handler server are used. No login, credential store, quota endpoint,
OpenCode singleton, host restart or paid request is touched.
"""
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'transcription-pane'), str(ROOT / 'scripts')]
import chatgpt_accounts as ca
import chatgpt_switch as cs
from claude_reader import ReaderError
import oc_client
import server

NATIVE_ACTIVATE = cs._activate
ACCOUNTS = '/api/chatgpt/accounts'
SWITCH = '/api/chatgpt/account-switch'
ORIGIN, AUTHORITY, LOGIN = 'https://pane.example.ts.net', 'pane.example.ts.net', 'dan@example.test'


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setenv(ca.REGISTRY_ENV, str(tmp_path / 'missing-default.json'))
    unexpected = []

    def forbidden(*args, **kwargs):
        unexpected.append((args, kwargs))
        raise AssertionError('offline fixture reached an uninjected native transport')

    class NoSingleton:
        status = switch = forbidden

    monkeypatch.setattr(ca, '_public_integrations', forbidden)
    monkeypatch.setattr(cs, '_activate', forbidden)
    monkeypatch.setattr(oc_client, 'oc_get', forbidden)
    monkeypatch.setattr(oc_client, 'oc_call', forbidden)
    monkeypatch.setattr(oc_client, 'oc_raw', forbidden)
    monkeypatch.setattr(oc_client, '_password', forbidden)
    monkeypatch.setattr(server, '_chatgpt_switch', NoSingleton(), raising=False)
    yield
    assert unexpected == [], 'a caught transport exception must not hide a fixture leak'


class Native:
    """Mutable public-integration truth; extras are deliberate projection canaries."""
    def __init__(self, registry):
        self.registry = registry
        self.saved = [('cred_a', 'Personal'), ('cred_b', 'Work · 雪'), ('cred_c', 'Third')]
        self.selected = 'cred_a'
        self.reads, self.activations = [], []
        self.on_activate = None
        self.read_error = None

    def get(self, path, timeout=10):
        self.reads.append((path, timeout))
        assert path == '/api/integration', 'only the public provider list may be read'
        if self.read_error:
            raise self.read_error
        rows = [{'type': 'credential', 'id': key, 'label': label, 'method': 'oauth',
                 'value': {'accessToken': 'SECRET'}, 'privateHome': '/private/SECRET'}
                for key, label in self.saved]
        rows.sort(key=lambda row: row['id'] != self.selected)
        if self.selected is None:
            rows.insert(0, {'type': 'env', 'name': 'OPENAI_API_KEY', 'value': 'SECRET'})
        return [{'id': 'anthropic', 'connections': [
                    {'type': 'credential', 'id': 'cred_claude', 'label': 'Claude', 'value': 'SECRET'}]},
                {'id': 'openai', 'connections': rows}]

    def inventory(self):
        return ca.account_inventory(self.registry, get=self.get)

    def activate(self, credential_id):
        self.activations.append(credential_id)
        if self.on_activate:
            return self.on_activate(credential_id)
        self.selected = credential_id


class Rig:
    def __init__(self, tmp_path):
        self.registry = tmp_path / 'optional-accounts.json'
        self.path = tmp_path / 'receipts/switch.sqlite3'
        self.native = Native(self.registry)
        self.now = 1000.0
        self.jobs = []
        self.switch = self.new()

    def new(self, **overrides):
        fields = dict(state_path=self.path, get=self.native.get, activate=self.native.activate,
                      inventory=self.native.inventory, clock=lambda: self.now, start=self.jobs.append)
        fields.update(overrides)
        return cs.ChatGptSwitch(**fields)

    def finish(self):
        self.now += 1
        self.jobs.pop(0)()

    def receipt(self, operation_id):
        with sqlite3.connect(self.path) as db:
            row = db.execute('SELECT receipt FROM operations WHERE id=?', (operation_id,)).fetchone()
        return json.loads(row[0]) if row else None


@pytest.fixture
def rig(tmp_path):
    fixture = Rig(tmp_path)
    try:
        yield fixture
    finally:
        # Queued fixtures own a real flock until their inert executor finishes.
        while fixture.jobs:
            fixture.finish()


def test_status_reads_current_native_inventory_each_time_without_selecting(rig):
    first = rig.switch.status()
    assert first['schemaVersion'] == 1 and first['backend'] == 'chatgpt-accounts'
    assert first['operation'] is None and first['mapping'] == 'missing'
    assert first['active']['credential_id'] == 'cred_a'
    rig.native.selected = 'cred_c'
    second = rig.switch.status()
    assert second['active']['credential_id'] == 'cred_c'
    assert len(rig.native.reads) == 2 and rig.native.activations == [] and rig.jobs == []
    assert 'SECRET' not in json.dumps(second) and '/private/' not in json.dumps(second)


def test_pending_and_attempting_are_durable_before_the_one_native_activation(rig):
    body, code = rig.switch.switch('cred_b', 'op-one', 'cred_a')
    assert code == 202 and body['operation']['phase'] == 'pending'
    assert body['operation']['attempted'] is False
    assert rig.receipt('op-one') == body['operation']
    assert os.stat(rig.path).st_mode & 0o777 == 0o600
    assert rig.native.activations == [] and len(rig.jobs) == 1
    observed = []

    def activate(key):
        receipt = rig.receipt('op-one')
        observed.append((receipt['phase'], receipt['attempted'], receipt['fromCredentialId']))
        assert rig.switch.status('op-one')['operation']['phase'] == 'attempting'
        rig.native.selected = key

    rig.native.on_activate = activate
    rig.finish()
    done = rig.switch.status('op-one')['operation']
    assert observed == [('attempting', True, 'cred_a')]
    assert done['phase'] == 'confirmed' and done['activeAtFinish']['credential_id'] == 'cred_b'
    assert done['finishedAt'] == 1001.0 and rig.native.activations == ['cred_b']
    assert 'in-flight' in done['detail'] and 'New requests' in done['detail']


def test_lost_reply_repeat_and_reload_never_replay_a_native_activation(rig):
    rig.switch.switch('cred_b', 'lost-reply', 'cred_a')  # Deliberately discard the first response.
    pending, code = rig.new().switch('cred_b', 'lost-reply', 'cred_a')
    assert code == 202 and pending['operation']['phase'] == 'pending' and len(rig.jobs) == 1
    rig.finish()
    original = rig.new().status('lost-reply')['operation']
    for _ in range(3):
        repeated, code = rig.new().switch('cred_b', 'lost-reply', 'cred_a')
        assert code == 200 and repeated['operation'] == original
    assert rig.native.activations == ['cred_b'] and rig.jobs == []


@pytest.mark.parametrize('credential_id, expected', [('cred_c', 'cred_a'), ('cred_b', None), ('cred_b', 'cred_c')])
def test_an_operation_id_cannot_be_rebound_to_any_other_intent(rig, credential_id, expected):
    rig.switch.switch('cred_b', 'immutable', 'cred_a')
    before = rig.receipt('immutable')
    with pytest.raises(ReaderError) as caught:
        rig.new().switch(credential_id, 'immutable', expected)
    assert (caught.value.code, caught.value.status) == (409, 'intent')
    assert rig.receipt('immutable') == before and len(rig.jobs) == 1
    assert rig.native.activations == []


def test_historical_receipts_do_not_claim_the_current_external_selection(rig):
    rig.switch.switch('cred_b', 'first', 'cred_a')
    rig.finish()
    historical = rig.receipt('first')
    rig.switch.switch('cred_a', 'second', 'cred_b')
    rig.finish()
    rig.native.selected = 'cred_c'  # An external CLI selection, not another app operation.
    body = rig.new().status('first')
    assert body['active']['credential_id'] == 'cred_c'
    assert body['operation'] == historical and historical['activeAtFinish']['credential_id'] == 'cred_b'
    again, code = rig.new().switch('cred_b', 'first', 'cred_a')
    assert code == 200 and again['operation'] == historical
    assert again['active']['credential_id'] == 'cred_c'
    assert rig.native.activations == ['cred_b', 'cred_a']


@pytest.mark.parametrize('mapping', ['missing', 'invalid', 'unmapped', 'mapped-without-auth'])
def test_optional_quota_enrollment_cannot_block_a_saved_native_switch(rig, mapping):
    if mapping == 'invalid':
        rig.registry.write_text('{', encoding='utf-8')
    elif mapping == 'unmapped':
        rig.registry.write_text('{}', encoding='utf-8')
    elif mapping == 'mapped-without-auth':
        rig.registry.write_text(json.dumps({'Work': {
            'codex_home': str(rig.path.parent / 'no-codex-login'), 'opencode_credential_id': 'cred_b'}}))
    rig.switch.switch('cred_b', 'quota-independent', 'cred_a')
    rig.finish()
    assert rig.switch.status('quota-independent')['operation']['phase'] == 'confirmed'
    assert rig.native.activations == ['cred_b']


def test_unknown_active_is_not_a_new_switch_refusal(rig):
    rig.native.selected = None
    rig.switch.switch('cred_b', 'unknown-active', 'cred_a')
    rig.finish()
    done = rig.switch.status('unknown-active')['operation']
    assert done['phase'] == 'confirmed' and done['fromCredentialId'] is None
    assert rig.native.activations == ['cred_b']


def test_already_active_is_confirmed_without_a_native_post(rig):
    rig.switch.switch('cred_a', 'already-active', 'older-expectation')
    rig.finish()
    done = rig.switch.status('already-active')['operation']
    assert done['phase'] == 'confirmed' and done['attempted'] is False
    assert 'already active' in done['detail'] and rig.native.activations == []


def test_inventory_is_rechecked_immediately_before_activation(rig):
    rig.switch.switch('cred_b', 'removed-target', 'cred_a')
    rig.native.saved = [item for item in rig.native.saved if item[0] != 'cred_b']
    rig.finish()
    done = rig.switch.status('removed-target')['operation']
    assert done['phase'] == 'failed' and done['attempted'] is False
    assert rig.native.activations == []


@pytest.mark.parametrize('credential_id', ['cred_claude', 'not-saved', '/api/credential/cred_b/activate'])
def test_arbitrary_or_other_provider_targets_cannot_select_anything(rig, credential_id):
    rig.switch.switch(credential_id, 'not-openai')
    rig.finish()
    done = rig.switch.status('not-openai')['operation']
    assert done['phase'] == 'failed' and done['attempted'] is False
    assert rig.native.activations == [] and rig.native.selected == 'cred_a'


def test_fresh_expected_active_conflict_is_not_attempted_or_replayed(rig):
    rig.switch.switch('cred_b', 'view-conflict', 'cred_a')
    rig.native.selected = 'cred_c'
    rig.finish()
    done = rig.switch.status('view-conflict')['operation']
    assert done['phase'] == 'conflict' and done['attempted'] is False
    rig.native.selected = 'cred_a'
    assert rig.new().switch('cred_b', 'view-conflict', 'cred_a')[0]['operation'] == done
    assert rig.native.activations == [] and rig.jobs == []


def test_two_adapters_share_the_execution_lock_and_a_conflict_receipt_is_terminal(rig):
    rig.switch.switch('cred_b', 'owner', 'cred_a')
    other, code = rig.new().switch('cred_c', 'competing', 'cred_a')
    assert code == 200 and other['operation']['phase'] == 'conflict'
    assert other['operation']['attempted'] is False and len(rig.jobs) == 1
    assert rig.new().status('owner')['operation']['phase'] == 'pending'
    rig.finish()
    repeated, _ = rig.new().switch('cred_c', 'competing', 'cred_a')
    assert repeated['operation'] == other['operation']
    assert rig.jobs == [] and rig.native.activations == ['cred_b']
    rig.new().switch('cred_c', 'new-deliberate-operation', 'cred_b')
    rig.finish()
    assert rig.native.activations == ['cred_b', 'cred_c']


def _child_switch(state_path, registry, pipe):
    """A second OS process holds the real execution lock, using only inert native IO."""
    native, jobs = Native(registry), []
    switch = cs.ChatGptSwitch(state_path=state_path, get=native.get, activate=native.activate,
                             inventory=native.inventory, clock=lambda: 1000.0, start=jobs.append)
    try:
        body, code = switch.switch('cred_b', 'child-owner', 'cred_a')
        pipe.send((code, body['operation']['phase']))
        if pipe.recv() == 'finish':
            jobs.pop(0)()
            pipe.send((switch.status('child-owner')['operation']['phase'], native.activations))
    finally:
        pipe.close()


def test_execution_lock_serializes_separate_processes_without_recovering_a_live_intent(rig):
    context = multiprocessing.get_context('fork')  # The production adapter itself requires POSIX flock.
    parent_pipe, child_pipe = context.Pipe()
    process = context.Process(target=_child_switch, args=(rig.path, rig.registry, child_pipe))
    process.start()
    child_pipe.close()
    try:
        assert parent_pipe.poll(5), 'inert child did not publish its pending receipt'
        assert parent_pipe.recv() == (202, 'pending')
        assert rig.new().status('child-owner')['operation']['phase'] == 'pending'
        competitor, _ = rig.switch.switch('cred_c', 'parent-competitor', 'cred_a')
        assert competitor['operation']['phase'] == 'conflict'
        assert competitor['operation']['attempted'] is False
        assert rig.native.activations == [] and rig.jobs == []
        parent_pipe.send('finish')
        assert parent_pipe.poll(5), 'inert child did not retain its completion'
        assert parent_pipe.recv() == ('confirmed', ['cred_b'])
        process.join(timeout=5)
        assert process.exitcode == 0
        # Readback in this process remains fresh native truth, not the other process's receipt.
        result = rig.new().status('child-owner')
        assert result['active']['credential_id'] == 'cred_a'
        assert result['operation']['phase'] == 'confirmed'
        assert result['operation']['activeAtFinish']['credential_id'] == 'cred_b'
    finally:
        parent_pipe.close()
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)


@pytest.mark.parametrize('phase, attempted', [('pending', False), ('attempting', True)])
def test_abandoned_durable_intents_recover_as_uncertain_without_replay(rig, phase, attempted):
    old = {'id': 'abandoned', 'credentialId': 'cred_b', 'expectedActiveCredentialId': 'cred_a',
           'phase': phase, 'attempted': attempted, 'startedAt': 900.0, 'updatedAt': 900.0,
           'finishedAt': None, 'detail': 'fixture abandoned before outcome'}
    with rig.switch._db() as db:
        rig.switch._save(db, old)
    rig.native.selected = 'cred_b'
    recovered = rig.new().status('abandoned')
    assert recovered['active']['credential_id'] == 'cred_b'
    assert recovered['operation']['phase'] == 'uncertain'
    assert recovered['operation']['attempted'] is attempted
    assert 'No selection was replayed' in recovered['operation']['detail']
    assert rig.new().switch('cred_b', 'abandoned', 'cred_a')[0]['operation'] == recovered['operation']
    assert rig.native.activations == [] and rig.jobs == []


@pytest.mark.parametrize('mode, expected_phase', [
    ('external-change', 'changed'), ('no-selection', 'changed'),
    ('transport-failure', 'failed'), ('reply-lost-after-selection', 'confirmed'),
    ('readback-unknown', 'uncertain'), ('readback-exception', 'uncertain'),
])
def test_native_outcomes_use_public_readback_not_the_transport_reply(rig, mode, expected_phase):
    class HttpFailure(Exception):
        status = 503

    def activate(key):
        if mode == 'external-change':
            rig.native.selected = 'cred_c'
        elif mode == 'transport-failure':
            raise HttpFailure('SECRET transport headers and token')
        elif mode == 'reply-lost-after-selection':
            rig.native.selected = key
            raise TimeoutError('SECRET reply lost')
        elif mode == 'readback-unknown':
            rig.native.selected = None
        elif mode == 'readback-exception':
            rig.native.read_error = RuntimeError('SECRET readback body')

    rig.native.on_activate = activate
    rig.switch.switch('cred_b', 'outcome', 'cred_a')
    rig.finish()
    rig.native.read_error = None
    result = rig.new().status('outcome')
    done = result['operation']
    assert done['phase'] == expected_phase and done['attempted'] is True
    assert 'SECRET' not in json.dumps(result)
    if mode == 'transport-failure':
        assert 'HTTP 503' in done['detail']
    if mode == 'reply-lost-after-selection':
        assert 'TimeoutError' in done['nativeProblem']
    rig.native.selected = 'cred_b'
    assert rig.new().status('outcome')['operation'] == done, 'fresh truth cannot rewrite a terminal receipt'
    assert rig.new().switch('cred_b', 'outcome', 'cred_a')[0]['operation'] == done
    assert rig.native.activations == ['cred_b'] and rig.jobs == []


def test_a_readback_that_raises_outside_the_inventory_adapter_is_also_uncertain(rig):
    reads = []

    def inventory():
        reads.append(None)
        if len(reads) == 3:  # initial response, preflight, then post-attempt readback
            raise RuntimeError('SECRET inventory exception')
        return rig.native.inventory()

    switch = rig.new(inventory=inventory)
    switch.switch('cred_b', 'raised-readback', 'cred_a')
    rig.finish()
    done = switch.status('raised-readback')['operation']
    assert done['phase'] == 'uncertain' and done['attempted'] is True
    assert 'SECRET' not in json.dumps(done) and rig.native.activations == ['cred_b']


def test_unavailable_preflight_fails_without_trying_and_a_repeat_does_not_retry(rig):
    rig.switch.switch('cred_b', 'unavailable', 'cred_a')
    rig.native.read_error = TimeoutError('SECRET connection settings')
    rig.finish()
    rig.native.read_error = None
    done = rig.new().status('unavailable')['operation']
    assert done['phase'] == 'failed' and done['attempted'] is False
    assert 'TimeoutError' in done['detail'] and 'SECRET' not in json.dumps(done)
    assert rig.new().switch('cred_b', 'unavailable', 'cred_a')[0]['operation'] == done
    assert rig.native.activations == [] and rig.jobs == []


def test_executor_start_failure_is_durable_and_releases_the_lock(rig):
    def fail_start(work):
        raise RuntimeError('SECRET executor failure')

    body, code = rig.new(start=fail_start).switch('cred_b', 'failed-start', 'cred_a')
    assert code == 200 and body['operation']['phase'] == 'failed'
    assert body['operation']['attempted'] is False and 'SECRET' not in json.dumps(body)
    assert rig.new().switch('cred_b', 'failed-start', 'cred_a')[0]['operation'] == body['operation']
    rig.switch.switch('cred_b', 'deliberate-retry', 'cred_a')
    rig.finish()
    assert rig.native.activations == ['cred_b']


@pytest.mark.parametrize('operation_id', ['', 'with space', '../path', 'x' * 129, None, True, 12])
def test_operation_ids_are_validated_before_creating_an_intent(rig, operation_id):
    with pytest.raises(ReaderError) as caught:
        rig.switch.switch('cred_b', operation_id)
    assert caught.value.code == 400 and rig.jobs == [] and rig.native.activations == []
    assert not rig.path.exists()


@pytest.mark.parametrize('credential_id, expected', [(None, None), ('', None), (42, None),
                                                    ('cred_b', True), ('cred_b', {}), ('cred_b', 12)])
def test_switch_intent_types_are_not_coerced(rig, credential_id, expected):
    with pytest.raises(ReaderError) as caught:
        rig.switch.switch(credential_id, 'bad-types', expected)
    assert caught.value.code == 400 and rig.native.activations == [] and rig.jobs == []


def test_native_transport_is_one_fixed_post_via_the_shared_client(monkeypatch):
    calls = []
    monkeypatch.setattr(oc_client, 'oc_call', lambda *a, **k: calls.append((a, k)))
    NATIVE_ACTIVATE('cred_a/odd ?#')
    assert calls == [(('POST', '/api/credential/cred_a%2Fodd%20%3F%23/activate'), {'timeout': 15})]


class Http:
    def __init__(self, rig):
        self.rig = rig
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.httpd.chatgpt_switch = rig.switch
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=lambda: self.httpd.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        assert not self.thread.is_alive()

    def remote(self, *, origin=ORIGIN, host=AUTHORITY, login=LOGIN, site=None):
        return ([('Host', host)] if host is not None else []) + (
            [('Origin', origin)] if origin is not None else []) + (
            [('Tailscale-User-Login', login)] if login is not None else []) + (
            [('Sec-Fetch-Site', site)] if site is not None else [])

    def enable(self):
        self.httpd.remote_origin_policy = server.RemoteOriginPolicy(ORIGIN, AUTHORITY, LOGIN)

    def request(self, route, *, method='GET', body=None, raw=None, headers=None,
                content_type='application/json', add_length=True):
        if raw is None and body is not None:
            raw = json.dumps(body).encode('utf-8')
        connection = HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            connection.putrequest(method, route, skip_host=True)
            for name, value in (headers if headers is not None else [('Host', f'127.0.0.1:{self.port}')]):
                connection.putheader(name, value)
            if raw is not None:
                if content_type is not None:
                    connection.putheader('Content-Type', content_type)
                if add_length:
                    connection.putheader('Content-Length', str(len(raw)))
            connection.endheaders(raw)
            response = connection.getresponse()
            payload = response.read()
            return response.status, dict(response.getheaders()), json.loads(payload) if payload else None
        finally:
            connection.close()


@pytest.fixture
def http(rig):
    fixture = Http(rig)
    try:
        yield fixture
    finally:
        fixture.close()


def intent(**changes):
    return {'schemaVersion': 1, 'backend': 'chatgpt', 'credentialId': 'cred_b',
            'operationId': 'http-operation', 'expectedActiveCredentialId': 'cred_a', **changes}


def guarded_operations():
    return [(ACCOUNTS, 'GET', None), (SWITCH, 'GET', None), (SWITCH, 'POST', intent())]


def test_http_inventory_and_switch_receipt_survive_lost_reply_reload_and_external_change(http, rig):
    code, headers, body = http.request(ACCOUNTS)
    assert code == 200 and body['active']['credential_id'] == 'cred_a' and body['operation'] is None
    assert body['mapping'] == 'missing' and 'SECRET' not in json.dumps(body)
    assert 'Access-Control-Allow-Origin' not in headers
    code, _, pending = http.request(SWITCH, method='POST', body=intent())
    assert code == 202 and pending['operation']['phase'] == 'pending'
    assert http.request(SWITCH, method='POST', body=intent())[0] == 202
    assert len(rig.jobs) == 1
    rig.finish()
    http.httpd.chatgpt_switch = rig.new()
    completed = http.request(SWITCH + '?operationId=http-operation')[2]['operation']
    assert completed['phase'] == 'confirmed'
    rig.native.selected = 'cred_c'
    for route in (ACCOUNTS, SWITCH, SWITCH + '?operationId=http-operation'):
        code, _, fresh = http.request(route)
        assert code == 200 and fresh['operation'] == completed
        assert fresh['active']['credential_id'] == 'cred_c'
    code, _, repeated = http.request(SWITCH, method='POST', body=intent())
    assert code == 200 and repeated['operation'] == completed
    assert rig.native.activations == ['cred_b'] and rig.jobs == []


def test_http_changed_intent_is_409_without_a_second_job(http, rig):
    http.request(SWITCH, method='POST', body=intent())
    for changes in ({'credentialId': 'cred_c'}, {'expectedActiveCredentialId': None}):
        code, _, body = http.request(SWITCH, method='POST', body=intent(**changes))
        assert code == 409 and body['status'] == 'intent'
    assert len(rig.jobs) == 1 and rig.native.activations == []


def test_http_other_provider_target_fails_without_native_activation(http, rig):
    code, _, body = http.request(SWITCH, method='POST', body=intent(credentialId='cred_claude'))
    assert code == 202 and body['operation']['phase'] == 'pending'
    rig.finish()
    code, _, body = http.request(SWITCH + '?operationId=http-operation')
    assert code == 200 and body['operation']['phase'] == 'failed'
    assert body['operation']['attempted'] is False and rig.native.activations == []


def test_http_strict_query_and_method_contracts_stop_before_native_reads(http, rig):
    for route in (ACCOUNTS + '?refresh=1', ACCOUNTS + '?operationId=x', ACCOUNTS + '?x=1',
                  SWITCH + '?x=1', SWITCH + '?operationId=', SWITCH + '?operationId=x&operationId=x',
                  SWITCH + '?operationId=x&other=1', SWITCH + '?operationId', SWITCH + '?operationId=bad%20id'):
        assert http.request(route)[0] == 400, route
    # status validates operationId after a fresh public inventory; schema parsing does not.
    reads_after_queries = len(rig.native.reads)
    for method in ('POST', 'PUT', 'PATCH', 'DELETE', 'HEAD'):
        assert http.request(ACCOUNTS, method=method, body=intent() if method == 'POST' else None)[0] == 405
    for method in ('PUT', 'PATCH', 'DELETE', 'HEAD'):
        assert http.request(SWITCH, method=method)[0] == 405
    assert http.request(SWITCH + '?operationId=x', method='POST', body=intent())[0] == 400
    for route in (ACCOUNTS, SWITCH):
        code, headers, _ = http.request(route, method='OPTIONS')
        assert code == 403 and 'Access-Control-Allow-Origin' not in headers
    assert len(rig.native.reads) == reads_after_queries
    assert rig.native.activations == [] and rig.jobs == []


@pytest.mark.parametrize('changes', [
    {'schemaVersion': True}, {'schemaVersion': 2}, {'schemaVersion': '1'},
    {'backend': 'openai'}, {'backend': 'claude'}, {'backend': 'chatgpt-accounts'},
    {'credentialId': ''}, {'credentialId': None}, {'credentialId': 12},
    {'operationId': ''}, {'operationId': 'with space'}, {'operationId': True},
    {'expectedActiveCredentialId': True}, {'expectedActiveCredentialId': 12},
    {'path': '/api/credential'}, {'provider': 'anthropic'}, {'command': 'restart'},
])
def test_http_post_schema_is_exact_and_does_not_coerce_values(http, rig, changes):
    code, _, body = http.request(SWITCH, method='POST', body=intent(**changes))
    assert code == 400 and 'error' in body
    assert rig.native.reads == [] and rig.native.activations == [] and rig.jobs == []
    assert not rig.path.exists()


def test_http_all_post_fields_are_required_even_nullable_expected_active(http, rig):
    for field in intent():
        body = intent()
        del body[field]
        assert http.request(SWITCH, method='POST', body=body)[0] == 400
    for body in ([], 'switch', 1, False):
        assert http.request(SWITCH, method='POST', body=body)[0] == 400
    assert rig.native.reads == [] and rig.native.activations == [] and rig.jobs == []


def test_http_bounded_strict_json_and_content_type_do_not_reach_native_io(http, rig):
    duplicate = json.dumps(intent())[:-1] + ',"operationId":"different"}'
    for raw in (b'{', b'null', b'', b'\xff', duplicate.encode(), b'{"x":NaN}'):
        assert http.request(SWITCH, method='POST', raw=raw)[0] == 400
    for content_type in ('text/plain', None):
        assert http.request(SWITCH, method='POST', body=intent(), content_type=content_type)[0] == 400
    assert http.request(SWITCH, method='POST', body=intent(), add_length=False)[0] == 400
    assert http.request(SWITCH, method='POST', raw=b' ' * 4097)[0] == 413
    host = [('Host', f'127.0.0.1:{http.port}')]
    assert http.request(SWITCH, method='POST', body=intent(), headers=host + [('Content-Type', 'application/json')])[0] == 400
    assert http.request(SWITCH, method='POST', body=intent(), headers=host + [('Content-Length', '1')])[0] == 400
    assert http.request(SWITCH, method='POST', body=intent(), headers=host + [('Transfer-Encoding', 'chunked')])[0] == 400
    assert rig.native.reads == [] and rig.native.activations == [] and rig.jobs == []


def assert_origin_refused(http, rig, headers):
    before = (len(rig.native.reads), len(rig.native.activations), len(rig.jobs))
    for route, method, data in guarded_operations():
        code, response_headers, body = http.request(route, method=method, body=data, headers=headers)
        assert code == 403 and body['status'] == 'origin'
        assert 'Access-Control-Allow-Origin' not in response_headers
    assert (len(rig.native.reads), len(rig.native.activations), len(rig.jobs)) == before
    assert not rig.path.exists(), 'an origin refusal must not even create a receipt database'


def test_remote_off_and_loopback_cross_origin_refuse_before_all_native_io(http, rig):
    assert_origin_refused(http, rig, http.remote())
    assert_origin_refused(http, rig, [('Host', f'127.0.0.1:{http.port}'), ('Origin', 'https://evil.invalid')])
    assert_origin_refused(http, rig, [('Host', f'127.0.0.1:{http.port}'), ('Sec-Fetch-Site', 'cross-site')])


def test_serve_allowed_sites_and_navigation_use_the_same_native_switch(http, rig):
    http.enable()
    for index, site in enumerate((None, 'same-origin', 'none')):
        for origin in (None, ORIGIN):
            code, headers, body = http.request(ACCOUNTS, headers=http.remote(origin=origin, site=site))
            assert code == 200 and body['available'] is True
            assert 'Access-Control-Allow-Origin' not in headers
        rig.native.selected = 'cred_a'
        code, headers, body = http.request(SWITCH, method='POST',
            body=intent(operationId=f'phone-{index}', expectedActiveCredentialId=None if index == 0 else 'cred_a'),
            headers=http.remote(site=site))
        assert code == 202 and body['operation']['phase'] == 'pending'
        assert 'Access-Control-Allow-Origin' not in headers
        rig.finish()
        code, _, body = http.request(SWITCH + f'?operationId=phone-{index}', headers=http.remote(site=site))
        assert code == 200 and body['operation']['phase'] == 'confirmed'
    assert rig.native.activations == ['cred_b'] * 3


def test_serve_missing_foreign_duplicate_and_fetch_site_headers_never_reach_native_io(http, rig):
    http.enable()
    cases = [
        http.remote(host=None), http.remote(host='foreign.example.ts.net'),
        http.remote(login=None), http.remote(login='someone@example.test'),
        http.remote(origin='https://evil.invalid'), http.remote(origin='http://' + AUTHORITY),
        http.remote(site='cross-site'), http.remote(site='same-site'),
        http.remote(origin=None, site='cross-site'),
        http.remote() + [('Host', AUTHORITY)], http.remote() + [('Origin', ORIGIN)],
        http.remote() + [('Tailscale-User-Login', LOGIN)],
        [('Host', 'foreign.invalid'), ('X-Forwarded-Host', AUTHORITY),
         ('X-Forwarded-Proto', 'https'), ('Tailscale-User-Login', LOGIN)],
    ]
    for headers in cases:
        assert_origin_refused(http, rig, headers)
