"""The Manager App's Claude account switch, offline: the CLI status reading, the one durable
operation (waiting → switching → active / refused / failed), idempotence across a lost reply or a
pane restart, and the two same-origin HTTP routes.

No native login is touched: the status reader and the restart process are injected, and a synthetic
state directory under a temporary HOME keeps every write away from real state.
"""
import contextlib
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import urllib.error
import urllib.request
import pytest
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import claude_accounts
import server


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class FakeProc:
    def __init__(self, pid=4242):
        self.pid = pid
        self.code = None

    def poll(self):
        return self.code


class Machine:
    """The mutable facts a switch reads: the CLI status, the child, the log and the clock."""

    def __init__(self, labels=('personal', 'work'), selected='personal', epoch=4):
        self.clock = Clock()
        self.labels = list(labels)
        self.selected, self.epoch = selected, epoch
        self.proc = None
        self.alive = False
        self.spawns = []
        self.reads = 0
        self.receipts = {}

    def status(self):
        """The actual shape of scripts/claude_headless/accounts.py::status()."""
        self.reads += 1
        accounts = []
        for label in self.labels:
            accounts.append({'label': label,
                             'config_dir': '/home/daniel347x/.claude-work' if label == 'work' else None,
                             'identity': 'a1b2c3d4e5f6a1b2c3d4e5f6', 'email': label + '@example.test',
                             'login_at': 1792899593.0,
                             'auth': {'loggedIn': True, 'authMethod': 'claude.ai',
                                      'subscriptionType': 'max', 'email': label + '@example.test',
                                      'accessToken': 'never-a-real-token'}})
        return {'active': {'label': self.selected,
                           'config_dir': '/home/daniel347x/.claude-work' if self.selected == 'work' else None,
                           'identity': 'deadbeefdeadbeefdeadbeef', 'email': self.selected + '@example.test',
                           'login_at': 1792899593.0, 'epoch': self.epoch},
                'accounts': accounts}

    def spawn(self, record):
        self.spawns.append(record)
        self.proc = FakeProc()
        self.alive = True
        return self.proc

    def is_alive(self, pid, witness):
        return bool(pid) and bool(self.proc) and self.alive and pid == self.proc.pid

    def finish(self, code):
        self.alive = False
        if self.proc is not None:
            self.proc.code = code
        if self.spawns and code in (0, 1, 3):
            record = self.spawns[-1]
            self.receipts[record['id']] = self.receipt(record, code)

    def receipt(self, record, code=0, **changes):
        return {'schema': 'claude-restart/1', 'operation_id': record['id'],
            'request': {'account': record['account']}, 'phase': {0: 'service_ready', 1: 'recovery_required', 3: 'waiting'}[code],
            'requested_transition': {0: 'succeeded', 1: 'failed', 3: 'refused'}[code],
            'direction': 'continue_destination', 'effects': [],
            'service': {'state': {0: 'ready', 1: 'failed', 3: 'unchecked'}[code],
                        'account': {'label': self.selected, 'epoch': self.epoch}},
            'manager': {'state': 'pending'}, 'notice': {'status': 'scheduled'},
            'errors': [] if code == 0 else [{'message': 'no host signal; unsettled work retained' if code == 3 else
                                            'destination selected but replacement startup failed'}],
            'commands': {'recover': 'bash scripts/claude_restart.sh --recover ' + record['id']}, **changes}


class SwitchFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.machine = Machine()

    def tearDown(self):
        self.temp.cleanup()

    def switch(self, **fields):
        machine = self.machine
        switch = claude_accounts.AccountSwitch(
            state_path=self.machine_dir() / 'switch.json', log_path=self.machine_dir() / 'switch.log',
            run_status=fields.pop('run_status', machine.status), spawn=fields.pop('spawn', machine.spawn),
            read_operation=fields.pop('read_operation', lambda operation_id: machine.receipts.get(operation_id)),
            clock=machine.clock, alive=fields.pop('alive', machine.is_alive), **fields)
        return switch

    def machine_dir(self):
        path = self.root / 'state'
        path.mkdir(parents=True, exist_ok=True)
        return path

    def switch_call(self, switch, account, operation_id):
        answer, code = switch.switch(account, operation_id)
        return answer, code

    def attempt(self, switch, code, line=1):
        with open(switch.log_path, 'a', encoding='utf-8') as log:
            log.write(f'=== attempt {line} 12:00:0{line} exit {code}\n')


class StatusReadingTests(SwitchFixture):
    def test_canonical_status_keeps_labels_identity_and_epoch_only(self):
        reading = claude_accounts.public_status(self.machine.status())
        self.assertEqual(reading['selected'], {'label': 'personal', 'epoch': 4, 'identity': {
            'email': 'personal@example.test', 'plan': 'max', 'loggedIn': True}})
        self.assertEqual(reading['accounts'][1]['identity'],
                         {'email': 'work@example.test', 'plan': 'max', 'loggedIn': True})
        self.assertEqual([row['label'] for row in reading['accounts']], ['personal', 'work'])
        self.assertEqual([row['active'] for row in reading['accounts']], [True, False])
        self.assertNotIn('never-a-real-token', json.dumps(reading))

    def test_login_folder_paths_and_secrets_never_reach_a_browser(self):
        payload = {'selected': {'label': 'work', 'epoch': '7',
                                'identity': {'email': 'w@example.test', 'plan': 'max',
                                             'accessToken': 'secret', 'refreshToken': 'secret'}},
                   'accounts': [{'label': 'personal', 'configDir': '/home/daniel347x/.claude',
                                 'folder': '/home/daniel347x/.claude', 'enrolled': False},
                                {'label': 'work', 'config_dir': '/home/daniel347x/.claude-work'}]}
        reading = claude_accounts.public_status(payload)
        text = json.dumps(reading)
        self.assertEqual(reading['selected']['epoch'], 7)
        self.assertNotIn('/home/daniel347x', text)
        self.assertNotIn('secret', text)
        self.assertFalse(next(row for row in reading['accounts'] if row['label'] == 'personal')['enrolled'])

    def test_alias_shapes_are_tolerated(self):
        reading = claude_accounts.public_status({'current': 'work', 'labels': {'personal': {}, 'work': {}}})
        self.assertEqual(reading['selected']['label'], 'work')
        self.assertIsNone(reading['selected']['epoch'])
        self.assertEqual([row['label'] for row in reading['accounts']], ['personal', 'work'])

    def test_a_status_that_cannot_name_the_selected_label_is_unusable(self):
        self.assertIsNone(claude_accounts.public_status({'accounts': [{'label': 'personal'}]}))
        self.assertIsNone(claude_accounts.public_status(['work']))
        self.assertIsNone(claude_accounts.public_status({}))

    def test_status_reports_an_uninstalled_command_without_running_anything(self):
        switch = self.switch(run_status=None)
        with patch.object(claude_accounts, 'STATUS_SCRIPT', self.root / 'missing.py'):
            reading = switch.status(force=True)
        self.assertFalse(reading['installed'])
        self.assertFalse(reading['available'])
        self.assertEqual(reading['status'], 'unavailable')


class Reader:
    """A facade-shaped reader: only the live Manager inventory the switch reads."""

    def __init__(self, rows):
        self.rows, self.fields = rows, None

    def sessions(self, **fields):
        self.fields = fields
        return {'sessions': self.rows}


class OperationTests(SwitchFixture):
    def test_cancel_waiting_is_durable_idempotent_and_replay_does_not_spawn(self):
        switch = self.switch()
        first = switch.switch('work', 'op-00000001')[0]['operation']
        self.assertTrue(first['canCancel'])
        cancelled = switch.cancel('op-00000001')['operation']
        self.assertEqual(cancelled['phase'], 'cancelled')
        self.assertFalse(cancelled['canCancel'])
        self.assertEqual(self.machine.selected, 'personal')
        restarted = self.switch()
        self.assertEqual(restarted.operation()['operation']['phase'], 'cancelled')
        self.assertEqual(restarted.cancel('op-00000001')['operation']['phase'], 'cancelled')
        self.assertEqual(restarted.switch('work', 'op-00000001')[0]['operation']['phase'], 'cancelled')
        self.assertEqual(len(self.machine.spawns), 1)
        # Controls unlock immediately, without waiting for the old helper's next observation.
        restarted.switch('work', 'op-00000002')
        self.assertEqual(len(self.machine.spawns), 2)
        self.assertTrue(restarted.operation()['operation']['canCancel'])

    def test_cancel_gate_survives_crash_before_operation_receipt_save(self):
        switch = self.switch()
        switch.switch('work', 'op-00000001')
        record = switch._read_record()
        claude_accounts.RestartControl(record['controlPath']).cancel()
        self.assertEqual(self.switch().operation()['operation']['phase'], 'cancelled')

    def test_stopping_gate_immediately_disables_cancel_before_log_or_selector_changes(self):
        switch = self.switch()
        switch.switch('work', 'op-00000001')
        claude_accounts.RestartControl(switch._read_record()['controlPath']).begin_stopping()
        operation = switch.operation()['operation']
        self.assertEqual(operation['phase'], 'switching')
        self.assertFalse(operation['canCancel'])
        with self.assertRaises(claude_accounts.AccountError) as caught:
            switch.cancel('op-00000001')
        self.assertEqual((caught.exception.code, caught.exception.status), (409, 'too-late'))
        self.assertEqual(self.machine.selected, 'personal')

    def test_cancel_rejects_unknown_or_malformed_id(self):
        switch = self.switch()
        for identifier, code in [('short', 400), ('op-00000009', 404)]:
            with self.assertRaises(claude_accounts.AccountError) as caught:
                switch.cancel(identifier)
            self.assertEqual(caught.exception.code, code)

    def test_missing_legacy_gate_never_grants_log_based_cancellation(self):
        switch = self.switch()
        switch.switch('work', 'op-00000001')
        record = switch._read_record()
        record.pop('controlPath')
        switch._save(record)
        self.assertFalse(switch.operation()['operation']['canCancel'])
        with self.assertRaises(claude_accounts.AccountError):
            switch.cancel('op-00000001')

    def test_switch_starts_the_existing_guarded_restart_and_waits(self):
        switch = self.switch()
        answer, code = self.switch_call(switch, 'work', 'op-00000001')
        operation = answer['operation']
        self.assertEqual(code, 202)
        self.assertEqual(operation['phase'], 'waiting')
        self.assertEqual(operation['account'], 'work')
        self.assertEqual(len(self.machine.spawns), 1)
        record = self.machine.spawns[0]
        self.assertEqual(record['account'], 'work')
        self.assertEqual(record['fromAccount'], 'personal')
        self.assertEqual(record['epochBefore'], 4)
        self.assertIsNone(record['managerUuid'])
        self.assertTrue(switch.state_path.is_file())
        self.assertEqual(os.stat(switch.state_path).st_mode & 0o777, 0o600)
        self.attempt(switch, 3)
        self.assertEqual(switch.operation()['operation']['phase'], 'waiting')

    def test_the_label_change_makes_it_switching_then_active(self):
        switch = self.switch()
        switch.switch('work', 'op-00000001')
        self.attempt(switch, 3)
        self.attempt(switch, 0, line=2)
        self.assertEqual(switch.operation()['operation']['phase'], 'waiting')  # log/exit alone is not service evidence
        self.machine.selected, self.machine.epoch = 'work', 5
        self.machine.finish(0)
        self.machine.clock.now += 5
        operation = switch.operation()['operation']
        self.assertEqual(operation['phase'], 'active')
        self.assertIsNotNone(operation['finishedAt'])
        # A terminal phase is written down: a later flip back does not rewrite history.
        self.machine.selected = 'personal'
        self.assertEqual(switch.operation()['operation']['phase'], 'active')

    def test_a_refusal_leaves_the_account_alone_and_says_so(self):
        switch = self.switch()
        switch.switch('work', 'op-00000001')
        self.attempt(switch, 3)
        self.machine.finish(3)
        self.machine.clock.now += 5
        operation = switch.operation()['operation']
        self.assertEqual(operation['phase'], 'refused')
        self.assertIn('no host signal', operation['detail'])
        self.assertFalse(switch.status(force=True)['selected']['label'] == 'work')

    def test_a_failure_after_the_signal_is_reported_with_its_exit(self):
        switch = self.switch()
        switch.switch('work', 'op-00000001')
        self.attempt(switch, 1)
        self.machine.finish(1)
        self.machine.clock.now += 5
        operation = switch.operation()['operation']
        self.assertEqual(operation['phase'], 'recovery_required')
        self.assertIn('replacement startup failed', operation['detail'])

    def test_a_rejected_restart_command_is_a_refusal_not_a_switch(self):
        switch = self.switch()
        switch.switch('work', 'op-00000001')
        self.attempt(switch, 2)
        self.machine.finish(2)
        self.machine.clock.now += 5
        operation = switch.operation()['operation']
        self.assertEqual(operation['phase'], 'refused')
        self.assertIn('nothing was signalled', operation['detail'])

    def test_the_same_operation_id_never_starts_a_second_restart(self):
        switch = self.switch()
        first, code = switch.switch('work', 'op-00000001')
        second, again = switch.switch('work', 'op-00000001')
        self.assertEqual((code, again), (202, 200))
        self.assertEqual(first['operation']['id'], second['operation']['id'])
        self.assertEqual(len(self.machine.spawns), 1)

    def test_a_second_id_while_one_is_running_is_refused_without_spawning(self):
        switch = self.switch()
        switch.switch('work', 'op-00000001')
        with self.assertRaises(claude_accounts.AccountError) as caught:
            switch.switch('personal', 'op-00000002')
        self.assertEqual(caught.exception.code, 409)
        self.assertEqual(len(self.machine.spawns), 1)

    def test_switching_to_the_active_label_does_nothing(self):
        switch = self.switch()
        body, code = switch.switch('personal', 'op-00000001')
        self.assertEqual(code, 200)
        self.assertTrue(body['operation']['alreadyActive'])
        self.assertEqual(body['operation']['phase'], 'active')
        self.assertEqual(body['operation']['service']['state'], 'unchecked')
        self.assertIn('already selected', body['operation']['detail'])
        self.assertEqual(self.machine.spawns, [])

    def test_an_unenrolled_label_is_refused_before_any_process(self):
        self.machine.labels = ['personal']
        switch = self.switch()
        with self.assertRaises(claude_accounts.AccountError) as caught:
            switch.switch('work', 'op-00000001')
        self.assertEqual(caught.exception.code, 409)
        self.assertIn('enroll', str(caught.exception))
        self.assertEqual(self.machine.spawns, [])

    def test_unknown_labels_and_bad_operation_ids_are_schema_errors(self):
        switch = self.switch()
        for account, identifier, code in (('work', 'short', 400), ('root', 'op-00000001', 400),
                                          ('work', 'op 1', 400)):
            with self.subTest(account=account, identifier=identifier):
                with self.assertRaises(claude_accounts.AccountError) as caught:
                    switch.switch(account, identifier)
                self.assertEqual(caught.exception.code, code)

    def test_an_unavailable_status_refuses_the_switch(self):
        def broken():
            raise claude_accounts.AccountError(503, 'unavailable', 'not installed')
        switch = self.switch(run_status=broken)
        with self.assertRaises(claude_accounts.AccountError) as caught:
            switch.switch('work', 'op-00000001')
        self.assertEqual(caught.exception.code, 503)
        self.assertEqual(self.machine.spawns, [])

    def test_the_one_live_manager_is_resumed(self):
        reader = Reader([{'uuid': '11111111-1111-4111-8111-111111111111', 'capabilities': {'nativeInput': True}}])
        switch = self.switch(reader=reader)
        switch.switch('work', 'op-00000001')
        self.assertEqual(self.machine.spawns[0]['managerUuid'], '11111111-1111-4111-8111-111111111111')
        self.assertEqual(reader.fields, {'role': 'Manager'})

    def test_newest_registered_manager_is_resumed_even_when_halted(self):
        reader = Reader([{'uuid': '11111111-1111-4111-8111-111111111111', 'createdAt': 100,
                          'capabilities': {'nativeInput': True}},
                         {'uuid': '22222222-2222-4222-8222-222222222222', 'createdAt': 200,
                          'state': 'exited', 'capabilities': {'nativeInput': False}}])
        switch = self.switch(reader=reader)
        switch.switch('work', 'op-00000001')
        self.assertEqual(self.machine.spawns[0]['managerUuid'], '22222222-2222-4222-8222-222222222222')

    def test_selector_changed_but_failed_host_start_is_not_active(self):
        switch = self.switch()
        switch.switch('work', 'op-00000001')
        self.machine.selected, self.machine.epoch = 'work', 5
        self.machine.finish(1)
        self.machine.clock.now += 5
        self.assertEqual(switch.operation()['operation']['phase'], 'recovery_required')

    def test_selector_changed_without_success_receipt_is_not_active(self):
        switch = self.switch()
        switch.switch('work', 'op-00000001')
        self.machine.selected, self.machine.epoch = 'work', 5
        self.machine.alive = False
        self.machine.clock.now += 5
        self.assertEqual(switch.operation()['operation']['phase'], 'unknown')

    def test_old_operation_ids_remain_receipts_after_later_switches(self):
        switch = self.switch()
        switch.switch('work', 'op-00000001')
        self.machine.selected, self.machine.epoch = 'work', 5
        self.machine.finish(0)
        self.machine.clock.now += 5
        self.assertEqual(switch.operation()['operation']['phase'], 'active')
        switch.switch('personal', 'op-00000002')
        self.machine.selected, self.machine.epoch = 'personal', 6
        self.machine.finish(0)
        self.machine.clock.now += 5
        self.assertEqual(switch.operation()['operation']['phase'], 'active')
        receipt, code = switch.switch('work', 'op-00000001')
        self.assertEqual(code, 200)
        self.assertEqual(receipt['operation']['account'], 'work')
        self.assertEqual(len(self.machine.spawns), 2)
        self.assertEqual(switch.operation()['operation']['id'], 'op-00000002')
        self.assertEqual(switch.operation('op-00000001')['operation']['account'], 'work')

    def test_one_operation_id_cannot_be_rebound_to_another_account(self):
        switch = self.switch()
        switch.switch('work', 'op-00000001')
        with self.assertRaises(claude_accounts.AccountError) as caught:
            switch.switch('personal', 'op-00000001')
        self.assertEqual(caught.exception.code, 409)
        self.assertEqual(len(self.machine.spawns), 1)

    def test_a_pane_restart_reads_the_same_operation(self):
        switch = self.switch()
        started = switch.switch('work', 'op-00000001')[0]['operation']
        self.attempt(switch, 3)
        # A fresh module instance (the pane server restarted) sees the same durable record and no new spawn.
        restarted = self.switch()
        fresh = restarted.operation()['operation']
        self.assertEqual(fresh['id'], started['id'])
        self.assertEqual(fresh['phase'], 'waiting')
        self.assertEqual(len(self.machine.spawns), 1)

    def test_a_corrupt_record_is_ignored_not_trusted(self):
        switch = self.switch()
        switch.state_path.write_text('{not json', encoding='utf-8')
        self.assertEqual(switch.operation()['status'], 'none')
        switch.switch('work', 'op-00000001')
        self.assertEqual(switch.operation()['operation']['phase'], 'waiting')

    def test_unknown_operation_id_is_not_found(self):
        switch = self.switch()
        switch.switch('work', 'op-00000001')
        with self.assertRaises(claude_accounts.AccountError) as caught:
            switch.operation('op-00000009')
        self.assertEqual(caught.exception.code, 404)

    def test_recovery_receipt_survives_reload_and_never_claims_old_restore_as_target_active(self):
        switch = self.switch()
        switch.switch('work', 'op-00000001')
        record = switch._read_record()
        record.update(pid=None, procStart=None)  # pane died after spawn, before saving its PID
        switch._save(record)
        self.assertEqual(self.switch().operation()['operation']['phase'], 'unknown')
        self.machine.receipts[record['id']] = self.machine.receipt(record, 1, phase='starting', direction='restore_old',
            service={'state': 'starting', 'account': {'label': 'personal', 'epoch': 4}})
        self.assertEqual(self.switch().operation()['operation']['phase'], 'starting')
        self.machine.receipts[record['id']]['service']['state'] = 'ready'
        restored = self.switch().operation()['operation']
        self.assertEqual(restored['phase'], 'restored')
        self.assertIn('Switch failed; personal service restored', restored['detail'])
        self.assertIn('Manager pending', restored['detail'])
        self.assertEqual(self.switch().status(force=True)['selected']['label'], 'personal')
        second = self.switch()
        second.switch('work', 'op-00000002')
        newer = second._read_record()
        self.machine.selected, self.machine.epoch = 'work', 5
        self.machine.receipts[newer['id']] = self.machine.receipt(newer, 0, phase='starting', requested_transition='pending',
            service={'state': 'starting', 'account': {'label': 'work', 'epoch': 5}})
        self.assertEqual(self.switch().operation()['operation']['phase'], 'starting')
        self.machine.receipts[newer['id']] = self.machine.receipt(newer)
        self.assertEqual(self.switch().operation()['operation']['phase'], 'active')
        self.assertEqual(self.switch().operation(record['id'])['operation']['phase'], 'restored')
        today = self.switch().status(force=True)
        self.assertEqual(today['selected']['label'], 'work')
        self.assertEqual(today['operation']['id'], newer['id'])
        self.assertEqual(len(self.machine.spawns), 2)  # GET/reload never advances, recovers or spawns


class IntegrationContractTests(SwitchFixture):
    """The exact commands this pane hands the lead's scripts, pinned offline."""

    def test_the_status_command_is_the_documented_one(self):
        script = self.root / 'oc_claude_accounts.py'
        script.write_text('', encoding='utf-8')
        switch = self.switch(run_status=None)
        payload = json.dumps(self.machine.status())
        with patch.object(claude_accounts, 'STATUS_SCRIPT', script), \
                patch.object(claude_accounts.subprocess, 'run',
                             return_value=SimpleNamespace(returncode=0, stdout=payload, stderr='')) as run:
            reading = switch.status(force=True)
        self.assertTrue(reading['available'])
        self.assertEqual(run.call_args.args[0], ['bash', str(claude_accounts.SCRIPTS / 'oc.sh'),
                                                 'claude_accounts', 'status', '--json'])

    def test_the_spawn_command_is_the_existing_guarded_restart(self):
        switch = self.switch()
        record = {'id': 'switch-fixture', 'account': 'work', 'managerUuid': '11111111-1111-4111-8111-111111111111'}
        with patch.object(claude_accounts.subprocess, 'Popen') as popen:
            popen.return_value.pid = 1234
            switch._spawn(record)
        args = popen.call_args.args[0]
        self.assertEqual(args[:3], [sys.executable, '-X', 'utf8'])
        self.assertEqual(args[3], str(claude_accounts.RESTART_WRAPPER))
        self.assertEqual(args[4], '--_worker')
        self.assertEqual(args[args.index('--account') + 1], 'work')
        self.assertIn('--close-idle', args)
        self.assertEqual(args[args.index('--operation-id') + 1], 'switch-fixture')
        self.assertEqual(args[args.index('--resume') + 1], record['managerUuid'])
        self.assertTrue(popen.call_args.kwargs['start_new_session'])
        self.assertEqual(popen.call_args.kwargs['stdin'], claude_accounts.subprocess.DEVNULL)

    def test_the_restart_wrapper_passes_the_account_and_resume_through(self):
        sys.path.insert(0, str(claude_accounts.SCRIPTS))
        import oc_claude_restart
        seen = {}

        def fake_worker(script, args, timeout, interval):
            seen.update(script=script, args=args, timeout=timeout, interval=interval)
            return 0

        argv = ['oc_claude_restart.py', '--_worker', '--timeout', '3600', '--interval', '15',
                '--account', 'work', '--close-idle', '--resume', '11111111-1111-4111-8111-111111111111']
        with patch.object(oc_claude_restart, 'worker', fake_worker), patch.object(sys, 'argv', argv):
            code = oc_claude_restart.main()
        self.assertEqual(code, 0)
        self.assertEqual(seen['args'][:-2], ['--account', 'work', '--close-idle',
                                        '--resume', '11111111-1111-4111-8111-111111111111'])
        self.assertEqual(seen['args'][-2], '--operation-id')
        self.assertEqual((seen['timeout'], seen['interval']), (3600, 15))
        self.assertTrue(seen['script'].endswith('claude_restart.sh'))


@pytest.mark.parametrize('gate_phase', ['cancelled', 'stopping'])
def test_pane_projects_existing_gate_facts_even_with_canonical_row(tmp_path, monkeypatch, gate_phase):
    from claude_headless import accounts, host as host_module, restart
    from claude_headless.restart_state import read_receipt, read_operations
    from test_claude_accounts import native
    from test_claude_observer_switch import ObserverAwareHarness, invoke
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    native(home)
    native(home, home / '.claude-work', 'work@example.test')
    accounts._write(accounts.registry_path(), {'emails': {
        'personal': 'personal@example.test', 'work': 'work@example.test'}})
    monkeypatch.setattr(accounts, 'resolve_account', lambda label: {
        **accounts._folder_identity(home / '.claude-work', accounts._read(accounts.registry_path())), 'label': label})
    h = ObserverAwareHarness(tmp_path / 'host', hold_at='prepare' if gate_phase == 'stopping' else 'inventory')
    monkeypatch.setattr(host_module, 'witness', h.witness)
    machine = Machine()
    monkeypatch.setattr(claude_accounts, '_proc_start', lambda pid: 'inert-worker-start')
    switch = claude_accounts.AccountSwitch(state_path=tmp_path / 'switch.json', log_path=tmp_path / 'switch.log',
        run_status=machine.status, spawn=machine.spawn, alive=machine.is_alive, clock=machine.clock,
        read_operation=lambda operation: read_receipt(h.state_root, operation, public=True))
    switch.switch('work', 'switch-one')
    record = switch._read_record()
    gate = claude_accounts.RestartControl(record['controlPath'])
    expected = 'cancelled' if gate_phase == 'cancelled' else 'switching'
    projections = []
    def inspect():
        before = read_operations(h.state_root)
        for reader in (switch.operation, lambda: switch.status(force=True)):
            projected = reader()['operation']
            assert projected['id'] == 'switch-one' and projected['phase'] == expected
            assert not projected['canCancel'] and projected['service']['state'] == 'unchecked'
            projections.append(projected)
        assert read_operations(h.state_root) == before and len(machine.spawns) == 1
    if gate_phase == 'stopping':
        before_effects = restart.RestartLifecycle.before_effects
        def admitted(lifecycle):
            before_effects(lifecycle)
            inspect()  # canonical pending row, stopping admission, but no host stop/effect yet
            with pytest.raises(claude_accounts.AccountError) as error:
                switch.cancel(record['id'])
            assert (error.value.code, error.value.status) == (409, 'too-late')
        monkeypatch.setattr(restart.RestartLifecycle, 'before_effects', admitted)
    try:
        with pytest.raises(restart.RestartRefused):
            invoke(h, control=gate)
        if gate_phase == 'cancelled':
            gate.cancel()  # won gate write with no pane receipt rewrite, real canonical refusal still present
            inspect()
        assert len(projections) == 2 and not h.kills and h.starts == 0
        assert 'prepare_observer_switch' in h.implemented
    finally:
        h.close()


class HttpTests(SwitchFixture):
    def setUp(self):
        super().setUp()
        self.accts = self.switch()
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.httpd.claude_accounts = self.accts
        self.httpd.usage_readings = server.UsageReadings(
            run_account=lambda label: {'claude': {'ok': True, 'windows': [{'label': 'five-hour', 'percent': 23}],
                                                     'account': {'label': label, 'identity': label + '-secret'}},
                                      'chatgpt': None},
            label_reader=lambda label: {'label': label, 'identity': label + '-secret'},
            start=lambda work: work())
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        super().tearDown()

    def call(self, route, *, method='GET', body=None, headers=None):
        raw = json.dumps(body).encode('utf-8') if body is not None else None
        data = None
        if raw is not None:
            data = raw
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}{route}', data=data, method=method,
                                         headers=headers or {})
        if data is not None:
            request.add_header('Content-Type', 'application/json')
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b'{}')

    def wire(self, route, *, method='GET', body=None, host, origin, login=None, site=None):
        raw = json.dumps(body).encode('utf-8') if body is not None else None
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        connection.putrequest(method, route, skip_host=True)
        connection.putheader('Host', host)
        if origin:
            connection.putheader('Origin', origin)
        if login:
            connection.putheader('Tailscale-User-Login', login)
        if site:
            connection.putheader('Sec-Fetch-Site', site)
        if raw is not None:
            connection.putheader('Content-Type', 'application/json')
            connection.putheader('Content-Length', str(len(raw)))
        connection.endheaders(raw)
        response = connection.getresponse()
        return response.status, json.loads(response.read() or b'{}')

    def test_status_route_reports_labels_without_paths_or_secrets(self):
        code, body = self.call('/api/claude/accounts')
        self.assertEqual(code, 200)
        self.assertTrue(body['available'])
        self.assertEqual(body['selected']['label'], 'personal')
        self.assertEqual([row['label'] for row in body['accounts']], ['personal', 'work'])
        self.assertNotIn('/home/', json.dumps(body))
        self.assertNotIn('a1b2c3d4e5f6', json.dumps(body))
        self.assertNotIn('never-a-real-token', json.dumps(body))
        self.assertIsNone(body['operation'])

    def test_switch_post_and_its_operation_read(self):
        code, body = self.call('/api/claude/account-switch', method='POST',
                               body={'schemaVersion': 1, 'backend': 'claude', 'account': 'work',
                                     'operationId': 'op-00000001'})
        self.assertEqual(code, 202)
        self.assertEqual(body['operation']['phase'], 'waiting')
        code, body = self.call('/api/claude/account-switch?operationId=op-00000001')
        self.assertEqual(code, 200)
        self.assertEqual(body['operation']['id'], 'op-00000001')
        code, body = self.call('/api/claude/accounts')
        self.assertEqual(body['operation']['id'], 'op-00000001')
        code, body = self.call('/api/claude/account-switch?operationId=op-00000009')
        self.assertEqual(code, 404)

    def test_a_repeated_post_is_the_same_operation(self):
        payload = {'schemaVersion': 1, 'backend': 'claude', 'account': 'work', 'operationId': 'op-00000001'}
        self.assertEqual(self.call('/api/claude/account-switch', method='POST', body=payload)[0], 202)
        self.assertEqual(self.call('/api/claude/account-switch', method='POST', body=payload)[0], 200)
        self.assertEqual(len(self.machine.spawns), 1)

    def test_named_usage_route_reads_without_switching_or_exposing_private_identity(self):
        code, body = self.call('/api/usage?account=work&refresh=1')
        self.assertEqual(code, 200)
        self.assertEqual(body['account'], 'work')
        self.assertEqual(body['claude']['account']['label'], 'work')
        self.assertNotIn('work-secret', json.dumps(body))
        self.assertNotIn('chatgpt', body)
        self.assertEqual(self.machine.selected, 'personal')
        self.assertEqual(self.machine.spawns, [])
        for query in ('account=root', 'account=work&account=personal', 'account=', 'refresh=0',
                      'account=work&path=x', 'refresh=1&refresh=1'):
            self.assertEqual(self.call('/api/usage?' + query)[0], 400)

    def test_cancel_route_schema_origin_reload_and_too_late(self):
        self.accts.switch('work', 'op-00000001')
        route = '/api/claude/account-switch/cancel'
        args = {'schemaVersion': 1, 'backend': 'claude', 'operationId': 'op-00000001'}
        self.assertEqual(self.call(route, method='POST', body=args, headers={'Origin': 'https://evil.example'})[0], 403)
        for bad in ({**args, 'account': 'work'}, {**args, 'backend': 'claude-accounts'}, {**args, 'schemaVersion': True}):
            self.assertEqual(self.call(route, method='POST', body=bad)[0], 400)
        self.assertEqual(self.call(route)[0], 405)
        self.assertEqual(self.call(route, method='POST', body=args)[1]['operation']['phase'], 'cancelled')
        self.httpd.claude_accounts = self.switch()
        self.assertEqual(self.call('/api/claude/accounts')[1]['operation']['phase'], 'cancelled')
        self.httpd.claude_accounts.switch('work', 'op-00000002')
        record = self.httpd.claude_accounts._read_record()
        claude_accounts.RestartControl(record['controlPath']).begin_stopping()
        self.assertEqual(self.call(route, method='POST', body={**args, 'operationId': 'op-00000002'})[0], 409)

    def test_schema_and_body_guards(self):
        cases = [
            {'schemaVersion': 1, 'backend': 'claude', 'account': 'root', 'operationId': 'op-00000001'},
            {'schemaVersion': 1, 'backend': 'claude', 'account': 'work'},
            {'schemaVersion': 1, 'backend': 'claude', 'account': 'work', 'operationId': 'op-00000001', 'path': '/x'},
            {'schemaVersion': 2, 'backend': 'claude', 'account': 'work', 'operationId': 'op-00000001'},
            {'schemaVersion': 1, 'backend': 'openai', 'account': 'work', 'operationId': 'op-00000001'},
        ]
        for body in cases:
            with self.subTest(body=body):
                code, answer = self.call('/api/claude/account-switch', method='POST', body=body)
                self.assertEqual(code, 400)
                self.assertIn('error', answer)
        self.assertEqual(self.machine.spawns, [])

    def test_a_wrong_content_type_is_refused(self):
        raw = json.dumps({'schemaVersion': 1, 'backend': 'claude', 'account': 'work',
                          'operationId': 'op-00000001'}).encode('utf-8')
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}/api/claude/account-switch',
                                         data=raw, method='POST',
                                         headers={'Content-Type': 'text/plain'})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(caught.exception.code, 403)
        self.assertEqual(self.machine.spawns, [])

    def test_a_cross_origin_switch_is_refused(self):
        code, body = self.call('/api/claude/account-switch', method='POST',
                               body={'schemaVersion': 1, 'backend': 'claude', 'account': 'work',
                                     'operationId': 'op-00000001'},
                               headers={'Origin': 'https://evil.example'})
        self.assertEqual(code, 403)
        self.assertEqual(self.machine.spawns, [])

    def test_the_phone_origin_policy_reaches_the_same_routes(self):
        self.httpd.remote_origin_policy = server.RemoteOriginPolicy(
            'https://pane.example.ts.net', 'pane.example.ts.net', 'dan@example.test')
        code, body = self.wire('/api/claude/accounts', host='pane.example.ts.net',
                               origin='https://pane.example.ts.net', login='dan@example.test', site='same-origin')
        self.assertEqual(code, 200)
        self.assertEqual(body['selected']['label'], 'personal')
        code, body = self.wire('/api/claude/account-switch', method='POST',
                               body={'schemaVersion': 1, 'backend': 'claude', 'account': 'work',
                                     'operationId': 'op-00000001'},
                               host='pane.example.ts.net', origin='https://pane.example.ts.net',
                               login='dan@example.test')
        self.assertEqual(code, 202)
        self.assertEqual(body['operation']['phase'], 'waiting')
        # The same route from another identity or origin is refused.
        for headers in ({'login': 'someone@example.test'}, {'origin': 'https://elsewhere.example'},
                        {'site': 'cross-site'}):
            with self.subTest(**headers):
                code, _ = self.wire('/api/claude/accounts', host='pane.example.ts.net',
                                    origin=headers.get('origin', 'https://pane.example.ts.net'),
                                    login=headers.get('login', 'dan@example.test'), site=headers.get('site'))
                self.assertEqual(code, 403)


class NamedUsageTests(unittest.TestCase):
    def cache(self, **fields):
        return server.UsageReadings(
            account_reader=lambda: {'identity': 'selected-personal', 'epoch': 4},
            label_reader=fields.pop('label_reader', lambda label: {'identity': label + '-private', 'epoch': 4}),
            **fields)

    @staticmethod
    def reading(label, percent):
        return {'claude': {'ok': True, 'account': {'label': label, 'identity': label + '-private', 'epoch': 4},
                           'windows': [{'label': 'five-hour', 'percent': percent, 'resets_at': '2026-10-26T00:00:00Z'}]},
                'chatgpt': None}

    def test_both_view_caches_are_labelled_separate_and_age_independently(self):
        now, seen, jobs = [1000.0], [], []
        def named(label):
            seen.append(label)
            return self.reading(label, 10 if label == 'personal' else 85)
        cache = self.cache(run_account=named, run=lambda: self.fail('view cannot read default/GPT'),
                           start=jobs.append, clock=lambda: now[0])
        self.assertEqual(cache.snapshot(account='work')['status'], 'loading')
        self.assertEqual(seen, [])
        jobs.pop(0)()
        work = cache.snapshot(account='work')
        self.assertEqual((work['account'], work['claude']['windows'][0]['percent']), ('work', 85))
        self.assertNotIn('private', json.dumps(work))
        now[0] += 7
        cache.snapshot(account='personal')
        jobs.pop(0)()
        self.assertEqual(cache.snapshot(account='personal')['claude']['windows'][0]['percent'], 10)
        self.assertEqual(cache.snapshot(account='work')['ageSeconds'], 7)
        self.assertEqual(seen, ['work', 'personal'])

    def test_named_read_command_uses_account_flag_and_never_default_provider(self):
        cache = self.cache(start=lambda work: work())
        with patch.object(server.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout=json.dumps(self.reading('work', 85)))) as run:
            self.assertEqual(cache.snapshot(account='work')['status'], 'ready')
        self.assertEqual(run.call_args.args[0], [sys.executable, str(cache.SCRIPT), '--account', 'work', '--json'])

    def test_unknown_and_failed_named_readings_never_borrow_other_account(self):
        cache = self.cache(run_account=lambda label: {'claude': {'ok': False, 'error': 'not signed in'}, 'chatgpt': None},
                           start=lambda work: work())
        body = cache.snapshot(account='work')
        self.assertFalse(body['claude']['ok'])
        self.assertEqual(body['claude']['account']['label'], 'work')
        self.assertNotIn('windows', body['claude'])
        bad = self.cache(run_account=lambda label: self.reading('personal', 99), start=lambda work: work())
        self.assertNotIn('claude', bad.snapshot(account='work'))
        self.assertIn('another account', bad.snapshot(account='work')['error'])

    def test_named_resolution_stays_off_the_foreground_read_path(self):
        jobs = []
        cache = self.cache(run_account=lambda label: self.reading(label, 12), start=jobs.append,
                           label_reader=lambda label: self.fail('must not resolve native auth before queued job'))
        self.assertEqual(cache.snapshot(account='work')['status'], 'loading')
        self.assertEqual(len(jobs), 1)

    def test_changed_named_login_clears_old_reading_and_rejects_inflight_identity(self):
        identity, now, jobs = ['work-private'], [1000.0], []
        def run(label):
            reading = self.reading(label, 85)
            identity[0] = 'renewed-work-private'
            return reading
        cache = self.cache(run_account=run, start=jobs.append, clock=lambda: now[0],
                           label_reader=lambda label: {'identity': identity[0], 'epoch': 4})
        cache.snapshot(account='work')
        jobs.pop(0)()
        self.assertNotIn('claude', cache.snapshot(account='work'))
        # A subsequent fresh reading may publish only the identity actually resolved for this label.
        def own(label):
            value = self.reading(label, 12)
            value['claude']['account']['identity'] = identity[0]
            return value
        cache.run_account = own
        now[0] += 30
        cache.snapshot(account='work')
        jobs.pop(0)()
        self.assertEqual(cache.snapshot(account='work')['claude']['windows'][0]['percent'], 12)

    def test_default_and_named_view_of_the_active_label_share_one_reading(self):
        now, jobs, seen = [1000.0], [], []

        def named(label):
            seen.append(label)
            return self.reading(label, 42)

        def gpt():
            return {'chatgpt': {'ok': True, 'windows': [{'label': 'weekly', 'percent': 10}]}}

        cache = server.UsageReadings(
            account_reader=lambda: {'label': 'personal', 'identity': 'personal-private', 'epoch': 4},
            run_account=named, run_chatgpt=gpt, clock=lambda: now[0], start=jobs.append)
        cache.snapshot(force=True)
        self.assertEqual(len(jobs), 2, 'one active-account read plus one independent GPT read')
        jobs.pop(0)()
        jobs.pop(0)()
        default = cache.snapshot()
        self.assertEqual((default['claude']['windows'][0]['percent'], default['chatgpt']['windows'][0]['percent']), (42, 10))
        self.assertNotIn('account', default)
        self.assertIsNone(cache.selected.reading, 'the active label is the one account cache, not a second reader')
        before = len(jobs)
        named_view = cache.snapshot(account='personal')
        self.assertEqual((named_view['account'], named_view['claude']['windows'][0]['percent']), ('personal', 42))
        self.assertEqual(len(jobs), before, 'the named view of the active label is the same cached reading')
        self.assertEqual(seen, ['personal'], 'the active account endpoint is read once, not once per view')
        self.assertEqual(named_view['ageSeconds'], default['ageSeconds'])

    def test_provider_backoff_blocks_even_a_deliberate_read_and_keeps_stale_windows(self):
        now, jobs = [1000.0], []

        def limited(label):
            return {'claude': {'ok': False, 'stale': True,
                               'windows': [{'label': 'five-hour', 'percent': 77, 'resets_at': '2026-10-26T00:00:00Z'}],
                               'error': 'HTTP 429', 'note': 'Claude quota refresh is waiting for the provider\'s retry time; last reading kept.',
                               'rate_limited': True, 'retry_at': now[0] + 600,
                               'observed_at': now[0] - 400, 'age_seconds': 400,
                               'account': {'label': label, 'identity': label + '-private', 'epoch': 4}},
                    'claude_frame_line': None}

        cache = server.UsageReadings(
            account_reader=lambda: {'label': 'personal', 'identity': 'personal-private', 'epoch': 4},
            run_account=limited, run_chatgpt=lambda: {'chatgpt': {'ok': True, 'windows': [], 'age_seconds': 0}},
            clock=lambda: now[0], start=jobs.append)
        cache.snapshot(force=True)
        jobs.pop(0)()
        jobs.pop(0)()
        body = cache.snapshot(force=True)
        self.assertFalse(body['claude']['ok'])
        self.assertTrue(body['claude']['stale'])
        self.assertEqual(body['claude']['windows'][0]['percent'], 77, 'the last good windows stay visible')
        self.assertAlmostEqual(body['ageSeconds'], 400.0, 'the provider age rides beside the pane age')
        self.assertEqual(body['retryAt'], now[0] + 600)
        self.assertIn('retry time', body['claude']['note'])
        self.assertEqual(jobs, [], 'a known retry_at stops the script even when Dan asks for a refresh')
        now[0] += 31
        cache.snapshot(force=True)
        self.assertEqual(len(jobs), 1, 'GPT is not held back by the Claude retry gate')
        jobs.pop(0)()
        still = cache.snapshot(force=True)
        self.assertEqual(still['claude']['windows'][0]['percent'], 77)
        self.assertGreater(still['retryAt'], now[0])
        now[0] += 601
        cache.snapshot(force=True)
        self.assertEqual(len(jobs), 2, 'after the gate the due providers read again')

    def test_each_deliberate_read_asks_the_cli_for_one_provider_and_never_selects(self):
        now, jobs = [1000.0], []

        def scripted(argv, **kwargs):
            provider = argv[argv.index('--provider') + 1] if '--provider' in argv else None
            if provider == 'chatgpt':
                payload = {'chatgpt': {'ok': True, 'windows': []}}
            elif '--account' in argv:
                label = argv[argv.index('--account') + 1]
                payload = {'claude': {'ok': True, 'windows': [], 'account': {'label': label, 'identity': label + '-private'}},
                           'claude_frame_line': None}
            else:
                payload = {'claude': {'ok': True, 'windows': [], 'account': {'label': 'default', 'identity': 'x'}},
                           'claude_frame_line': None}
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr='')

        cache = server.UsageReadings(
            account_reader=lambda: {'label': 'default', 'identity': 'x', 'epoch': 1},
            label_reader=lambda label: {'label': label, 'identity': label + '-private', 'epoch': 1},
            clock=lambda: now[0], start=jobs.append)
        cache.snapshot(force=True)
        with patch.object(server.subprocess, 'run', side_effect=scripted) as run:
            jobs.pop(0)()
            jobs.pop(0)()
        self.assertEqual(sorted(c.args[0] for c in run.call_args_list),
                         sorted([[sys.executable, str(cache.SCRIPT), '--provider', 'chatgpt', '--json'],
                                 [sys.executable, str(cache.SCRIPT), '--provider', 'claude', '--refresh', '--json']]))
        cache.snapshot(account='work', force=True)
        with patch.object(server.subprocess, 'run', side_effect=scripted) as run:
            jobs.pop(0)()
        self.assertEqual(run.call_args.args[0],
                          [sys.executable, str(cache.SCRIPT), '--account', 'work', '--refresh', '--json'])

    def test_short_provider_retry_resumes_before_normal_ttl_without_another_manual_click(self):
        now, jobs = [1000.0], []
        reading = self.reading('personal', 42)
        reading['claude'].update(ok=False, stale=True, rate_limited=True, retry_at=1075,
                                 observed_at=1000, age_seconds=30)
        cache = server.UsageReadings(
            account_reader=lambda: {'label': 'personal', 'identity': 'personal-private', 'epoch': 4},
            run_account=lambda label: reading, clock=lambda: now[0], start=jobs.append)
        now[0] = 1030
        cache.snapshot(account='personal', force=True)
        jobs.pop(0)()
        now[0] = 1074
        cache.snapshot(account='personal')
        self.assertEqual(jobs, [])
        now[0] = 1075
        cache.snapshot(account='personal')
        self.assertEqual(len(jobs), 1, 'retry_at, not the ordinary 120s TTL, ends the wait')

    def test_the_active_label_reads_the_selector_not_a_second_named_resolution(self):
        now, jobs = [1000.0], []

        def scripted(argv, **kwargs):
            return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps(
                {'claude': {'ok': True, 'windows': [], 'account': {'label': 'personal', 'identity': 'personal-private'}},
                 'claude_frame_line': None}))

        cache = server.UsageReadings(account_reader=lambda: {'label': 'personal', 'identity': 'personal-private', 'epoch': 4},
                                     clock=lambda: now[0], start=jobs.append)
        cache.snapshot(force=True)
        with patch.object(server.subprocess, 'run', side_effect=scripted) as run:
            jobs.pop(0)()  # the active label's one Claude cache; GPT stays queued
        self.assertEqual(run.call_args.args[0],
                         [sys.executable, str(cache.SCRIPT), '--provider', 'claude', '--refresh', '--json'])
        self.assertEqual(cache.snapshot(account='personal')['claude']['account'], {'label': 'personal'})

    def test_login_change_during_refresh_also_drops_an_existing_cached_reading(self):
        identity, now, jobs = ['work-private'], [1000.0], []
        cache = self.cache(run_account=lambda label: self.reading(label, 85), start=jobs.append,
                           clock=lambda: now[0], label_reader=lambda label: {'identity': identity[0], 'epoch': 4})
        cache.snapshot(account='work')
        jobs.pop(0)()
        self.assertEqual(cache.snapshot(account='work')['claude']['windows'][0]['percent'], 85)
        def changed(label):
            identity[0] = 'different-login'
            return self.reading(label, 99)
        cache.run_account = changed
        now[0] += 120
        cache.snapshot(account='work')
        jobs.pop(0)()
        self.assertNotIn('claude', cache.snapshot(account='work'))


if __name__ == '__main__':
    unittest.main()
