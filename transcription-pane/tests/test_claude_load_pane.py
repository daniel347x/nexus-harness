"""Optional sidebar load facts reuse inventory health, never a Send/history dependency."""
import copy
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_universe_inventory as fixtures
from claude_live import ClaudeFacade
from claude_reader import ClaudeReader
from oc_history_settings import load
from oc_pins import SessionPins
from universe import UniverseEvidence


class LoadHost(fixtures.FakeHost):
    def __init__(self, records, health=None):
        super().__init__(records)
        self.health = health or {}
        self.health_error = False

    def call(self, method, **params):
        reply = super().call(method, **params)
        if method == 'health':
            if self.health_error:
                raise OSError('fixture health unavailable')
            reply.update(copy.deepcopy(self.health))
        return reply


class SidebarLoad(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        settings = replace(load(), pins_path=str(Path(self.scratch.name) / 'pins.json'))
        self.universe = UniverseEvidence(settings=settings, pins=SessionPins(settings.pins_path), ledger=lambda: {})

    def facade(self, host):
        return ClaudeFacade(ClaudeReader([{'id': 'fixture', 'root': self.scratch.name}]), host,
            universe_state=self.universe, clock=lambda: fixtures.NOW, spawn=lambda work: None)

    def test_inventory_reuses_one_health_reply_and_forwards_only_caption_facts(self):
        wanted = {'rows_behind_sets': 3, 'loop_lag_ms_max_60s': 2500}
        host = LoadHost([fixtures.record(1, days=1)], {'load': {
            **wanted, 'rows_behind_events': 1234, 'startup': {'bound_at': 10}, 'free_bytes': 999}})
        facade = self.facade(host)
        reply = facade.sessions(storeRef=facade.store_ref)
        self.assertEqual(reply['hostLoad'], wanted)
        self.assertEqual(len(reply['sessions']), 1)
        self.assertEqual(sum(method == 'health' for method, _ in host.calls), 1)
        host.health = {'load': {'rows_behind_sets': 0, 'loop_lag_ms_max_60s': 0}}
        self.assertEqual(facade.sessions(storeRef=facade.store_ref)['hostLoad'], host.health['load'])
        self.assertEqual(sum(method == 'health' for method, _ in host.calls), 2)
        self.assertFalse(any(method in ('send', 'steer', 'rows', 'replay') for method, _ in host.calls))

    def test_all_and_manager_inventories_observe_load_but_live_and_continuations_do_not_add_calls(self):
        host = LoadHost([fixtures.record(n, days=1, role='Manager', live=True) for n in range(1, 105)],
                        {'load': {'rows_behind_sets': 2, 'loop_lag_ms_max_60s': 0}})
        facade = self.facade(host)
        for params in ({'view': 'all'}, {'role': 'Manager'}):
            host.calls.clear()
            first = facade.sessions(storeRef=facade.store_ref, **params)
            self.assertEqual(first['hostLoad']['rows_behind_sets'], 2)
            self.assertEqual(sum(method == 'health' for method, _ in host.calls), 1)
            self.assertTrue(first['hasMore'])
            second = facade.sessions(cursor=first['nextCursor'], storeRef=facade.store_ref, **params)
            self.assertNotIn('hostLoad', second)  # A continuation cannot clear its first page's observation.
            self.assertEqual(sum(method == 'health' for method, _ in host.calls), 1)
        host.calls.clear()
        self.assertNotIn('hostLoad', facade.sessions(storeRef=facade.store_ref, live=True))
        self.assertEqual(sum(method == 'health' for method, _ in host.calls), 0)

    def test_old_missing_malformed_or_failed_health_keeps_inventory_and_no_caption(self):
        host = LoadHost([fixtures.record(1, days=1)])
        facade = self.facade(host)
        for health, error in (({}, False), ({'load': None}, False), ({'load': []}, False), ({}, True)):
            host.health, host.health_error = health, error
            for params in ({}, {'view': 'all'}, {'role': 'Manager'}):
                reply = facade.sessions(storeRef=facade.store_ref, **params)
                self.assertIsNone(reply['hostLoad'])
                self.assertEqual(len(reply['sessions']), 0 if params.get('role') == 'Manager' else 1)

    def test_health_observation_holds_neither_control_nor_history_lane(self):
        host = LoadHost([fixtures.record(1, days=1)], {'load': {'rows_behind_sets': 2}})
        facade = self.facade(host)
        entered, release = threading.Event(), threading.Event()
        original = host.call
        def held(method, **params):
            if method == 'health':
                entered.set()
                if not release.wait(3):
                    raise AssertionError('test did not release health')
            return original(method, **params)
        host.call = held
        replies, errors = [], []
        def inventory():
            try:
                replies.append(facade.sessions(storeRef=facade.store_ref))
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=inventory)
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            for lane in (facade.control_lock, facade.history_lock):
                self.assertTrue(lane.acquire(timeout=.1), 'caption health must not hold a work lane')
                lane.release()
        finally:
            release.set()
            thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(replies[0]['hostLoad']['rows_behind_sets'], 2)

    def test_concurrent_inventory_health_receipts_are_operation_local_not_a_shared_cache(self):
        host = LoadHost([fixtures.record(1, days=1)])
        facade = self.facade(host)
        barrier, original = threading.Barrier(2), host.call
        def observed(method, **params):
            reply = original(method, **params)
            if method == 'health':
                barrier.wait(timeout=3)
                reply['load'] = {'rows_behind_sets': int(threading.current_thread().name),
                                 'loop_lag_ms_max_60s': 0}
            return reply
        host.call = observed
        replies, errors = {}, []
        def inventory():
            try:
                replies[threading.current_thread().name] = facade.sessions(storeRef=facade.store_ref)['hostLoad']
            except BaseException as error:
                errors.append(error)
        threads = [threading.Thread(target=inventory, name=name) for name in ('2', '7')]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(4)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual({name: value['rows_behind_sets'] for name, value in replies.items()}, {'2': 2, '7': 7})


if __name__ == '__main__':
    unittest.main()
