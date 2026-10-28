"""The shared pure row builders retain live display fields without live augmentation."""
import copy
import sys
import tempfile
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from opencode_programs import opencode_row, program_rows
from claude_live import ClaudeFacade, host_row_fields
from claude_reader import ClaudeReader


NOW = 1_792_090_000.0


def session(sid, **fields):
    return {'id': sid, 'title': sid, 'time': {'created': NOW * 1000}, **fields}


class OpenCodeRowSeam(unittest.TestCase):
    def test_live_rows_are_the_pure_fields_plus_busy_without_mutating_sources(self):
        sessions = [
            session('ses_manager001', agent='manager', title='Manager-Session-222'),
            session('ses_quick00001', metadata={'kind': 'seat', 'quick': True, 'socket': '[ap:SOCKET]'}),
            session('ses_program001', metadata={'kind': 'conductor', 'program': '[ap:5CKYAT]'},
                    cost=0.123456, model={'providerID': 'openai', 'id': 'gpt-6.1'}),
            session('ses_child00001', parentID='ses_program001'),
        ]
        by_id = {s['id']: s for s in sessions}
        original = copy.deepcopy(by_id)
        for row in program_rows(sessions, {'ses_child00001'}, NOW):
            self.assertEqual(opencode_row(by_id[row['id']], by_id),
                             {key: value for key, value in row.items() if key != 'busy'})
        self.assertEqual(by_id, original)

    def test_unattributed_and_old_sessions_still_have_pure_rows(self):
        stray = session('ses_stray00001', title='ordinary session')
        old = session('ses_old0000001', title='[ap:5CKYAT]-001 · old', time={'created': (NOW - 90 * 86400) * 1000})
        by_id = {s['id']: s for s in (stray, old)}
        self.assertIsNone(opencode_row(stray, by_id).get('program'))
        self.assertEqual(opencode_row(old, by_id)['program'], '5CKYAT')
        self.assertEqual({r['id'] for r in program_rows([stray, old], set(), NOW)}, set(by_id))

    def test_plain_children_follow_the_chain_and_missing_or_cyclic_chains_are_unattributed(self):
        root = session('ses_program001', title='[ap:5CKYAT]-002 · program')
        child = session('ses_child00001', parentID=root['id'])
        grandchild = session('ses_grand00001', parentID=child['id'])
        by_id = {s['id']: s for s in (grandchild, child, root)}
        row = opencode_row(grandchild, by_id)
        self.assertEqual((row['kind'], row['parentId'], row['program']), ('subagent', child['id'], '5CKYAT'))
        self.assertIsNone(opencode_row(grandchild, {grandchild['id']: grandchild}).get('program'))
        cycle = {child['id']: {**child, 'parentID': grandchild['id']}, grandchild['id']: grandchild}
        self.assertIsNone(opencode_row(grandchild, cycle).get('program'))

    def test_parked_warm_forks_only_return_none_and_plain_child_titles_keep_join_semantics(self):
        root = session('ses_program001', title='[ap:5CKYAT]-001 · program')
        warm = session('ses_warm000001', title='[ap:5CKYAT]-001 ▸ warm · parked')
        stamped = session('ses_warm000002', metadata={'kind': 'warm'}, parentID=root['id'])
        child = {**warm, 'parentID': root['id']}
        by_id = {s['id']: s for s in (root, child, stamped)}
        self.assertIsNone(opencode_row(warm, by_id))
        self.assertIsNone(opencode_row(stamped, by_id))
        self.assertEqual(opencode_row(child, by_id)['kind'], 'subagent')

    def test_seed_then_join_order_stays_stable_for_equal_or_missing_creation_stamps(self):
        grandchild = session('ses_grand00001', parentID='ses_child00001', time={})
        child = session('ses_child00001', parentID='ses_program001', time={})
        second = session('ses_program002', title='[ap:OTHER1]-001 · second', time={})
        root = session('ses_program001', title='[ap:5CKYAT]-001 · first', time={})
        rows = program_rows([grandchild, child, second, root], set(), NOW)
        self.assertEqual([r['id'] for r in rows], [second['id'], root['id'], child['id'], grandchild['id']])


class HostRowSeam(unittest.TestCase):
    def record(self, **fields):
        return {'execution_id': 'cc-01234567-89ab-cdef-0123-456789abcdef',
                'native_uuid': '01234567-89ab-cdef-0123-456789abcdef', 'generation': 1,
                'role': 'worker', 'cwd': '/fixture', 'state': 'ready', 'owner': 'owned', **fields}

    def test_host_summary_fields_and_existing_label_fallback_are_pure(self):
        record = self.record(program='[ap:5CKYAT]', socket='[ap:SOCKET]',
                             labels={'title': '  own seat title  ', 'kind': 'seat', 'quick': True,
                                     'manager_session': '222'})
        original = copy.deepcopy(record)
        fields = host_row_fields(record)
        self.assertEqual(fields, {'uuid': record['native_uuid'], 'title': 'own seat title',
            'cwd': '/fixture', 'role': 'worker', 'executionId': record['execution_id'], 'generation': 1,
            'displayRole': 'quick-worker', 'roleSource': 'host', 'socket': '[ap:SOCKET]', 'managerSession': '222',
            'lineage': {'program': '5CKYAT', 'kind': 'seat', 'quick': True,
                        'managerSession': '222', 'title': 'own seat title'}})
        self.assertEqual(record, original)
        self.assertTrue(set(fields).isdisjoint({'liveState', 'capabilities', 'createdAt', 'targetRef', 'fileEpoch'}))

    def test_chosen_label_and_cwd_socket_guards_are_the_existing_rules(self):
        record = self.record(label=' Chosen label ', title='ignored', cwd=42, socket='s' * 129)
        fields = host_row_fields(record)
        self.assertEqual((fields['title'], fields['sessionLabel'], fields['cwd']),
                         ('Chosen label', 'Chosen label', 'unknown/label-limit'))
        self.assertNotIn('socket', fields)
        self.assertNotIn('sessionLabel', host_row_fields(self.record()))

    def test_facade_calls_the_shared_builder_and_retains_only_existing_public_fields(self):
        record = self.record(label='Chosen label', socket='[ap:SOCKET]')

        class Host:
            def call(self, method, **params):
                if method == 'list':
                    return [copy.deepcopy(record)]
                if method == 'health':
                    return {'implemented': []}
                raise AssertionError(method)

        with tempfile.TemporaryDirectory() as root:
            facade = ClaudeFacade(ClaudeReader([{'id': 'fixture', 'root': root}]), Host())
            row = facade.sessions(storeRef=facade.store_ref)['sessions'][0]
            pure = host_row_fields(record)
            for key, value in pure.items():
                if key != 'sessionLabel':
                    self.assertEqual(row[key], value)
            self.assertEqual(facade._inventory_record(record)['sessionLabel'], 'Chosen label')
            self.assertNotIn('sessionLabel', row, 'the old API did not expose the summary-only launch-label marker')
            self.assertTrue({'liveState', 'capabilities', 'createdAt', 'targetRef', 'fileEpoch'} <= set(row))


if __name__ == '__main__':
    unittest.main()
