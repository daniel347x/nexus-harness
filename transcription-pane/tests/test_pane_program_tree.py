"""Round 7 sidebar: the program tree's edge data from the host's immutable launch labels (ClaudeFacade._lineage),
OpenCode's program rows and glance (opencode_programs, a scripted fake OpenCode: no live service), and the two
read-only /api/opencode routes (same-origin GET, bounded, OpenCode down = an honest `unavailable`)."""
import http.client
import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import opencode_programs
import server
from claude_live import ClaudeFacade
from claude_reader import ClaudeReader
from opencode_programs import OpenCodePrograms, Unavailable, program_rows
from test_claude_lockout_batch_a import FleetHost, host_record

CORE = 'UHZREM'
LEAD = 'cc-b5e93288-0040-4e15-8d21-e9b107a3e450'
COND = 'cc-44b3fc58-2503-4b76-8a39-f6b379756814'
NOW = 1_792_090_000.0


def record(role, labels, program=f'[ap:{CORE}]', **extra):
    return {'role': role, 'program': program, 'labels': labels, **extra}


class Lineage(unittest.TestCase):
    def test_a_conductor_carries_its_program_number_and_kind(self):
        got = ClaudeFacade._lineage(record('conductor', {
            'title': f'[ap:{CORE}]-002 · ⚡ x', 'kind': 'conductor', 'registry_n': 2, 'program_core': CORE,
            'entry_action': 'resume', 'quick_program': True, 'manager_session': '183'}))
        self.assertEqual(got, {'program': CORE, 'kind': 'conductor', 'conductorN': 2, 'quickProgram': True,
                               'entryAction': 'resume', 'managerSession': '183', 'title': f'[ap:{CORE}]-002 · ⚡ x'})

    def test_seats_and_helpers_carry_their_edges_from_labels(self):
        lead = ClaudeFacade._lineage(record('worker', {'kind': 'seat', 'fresh': True, 'conductor_id': COND, 'conductor_n': 1}))
        self.assertEqual((lead['kind'], lead['conductorN'], lead['conductorId'], lead['seatRole']), ('seat', 1, COND, 'lead'))
        helper = ClaudeFacade._lineage(record('helper', {'kind': 'helper', 'quick': True, 'parent_seat': LEAD}, parent=LEAD))
        self.assertEqual((helper['kind'], helper['parentSeat'], helper['quick']), ('helper', LEAD, True))
        # oc_quick's worker before its Conductor (older launch): no number; the title prefix [ap:CORE] has none either.
        early = ClaudeFacade._lineage(record('worker', {'kind': 'seat', 'quick': True, 'title': f'[ap:{CORE}] ▸ ⚡ quick worker · t'}))
        self.assertNotIn('conductorN', early)
        # A number lost from the labels is read from the title prefix, and says so.
        titled = ClaudeFacade._lineage(record('worker', {'kind': 'seat', 'title': f'[ap:{CORE}]-003 ▸ ⚡ quick worker · t'}))
        self.assertEqual((titled['conductorN'], titled['numberSource']), (3, 'title'))

    def test_malformed_values_drop_out_and_non_program_sessions_have_none(self):
        got = ClaudeFacade._lineage(record('worker', {'kind': 'seat', 'conductor_n': '1', 'conductor_id': 'cc-short',
                                                       'parent_seat': 'not an id', 'manager_session': '18x',
                                                       'seat_role': 'boss', 'quick': 'yes'}))
        self.assertEqual(got, {'program': CORE, 'kind': 'seat'})
        self.assertIsNone(ClaudeFacade._lineage(record('Manager', {}, program=None)))
        self.assertIsNone(ClaudeFacade._lineage(record('worker', {'kind': 'seat'}, program='[ap:bad]')))
        self.assertIsNone(ClaudeFacade._lineage({'role': 'worker', 'labels': 'not a dict'}))

    def test_a_freestanding_quick_worker_or_helper_carries_its_edges_without_a_program(self):
        worker = ClaudeFacade._lineage(record('worker', {'kind': 'seat', 'quick': True, 'manager_session': '193',
                                                          'title': '▸ ⚡ quick worker · t'}, program=None))
        self.assertEqual(worker, {'kind': 'seat', 'quick': True, 'managerSession': '193', 'title': '▸ ⚡ quick worker · t'})
        helper = ClaudeFacade._lineage(record('helper', {'kind': 'helper', 'quick': True, 'parent_seat': 'ses_fswork1xxx'},
                                              program=None, parent='ses_fswork1xxx'))
        self.assertEqual(helper, {'kind': 'helper', 'parentSeat': 'ses_fswork1xxx', 'quick': True})
        # Only a quick seat with no program at all: a Manager, an unlabelled worker or a malformed program has none.
        self.assertIsNone(ClaudeFacade._lineage(record('Manager', {'kind': 'seat', 'quick': True}, program=None)))
        self.assertIsNone(ClaudeFacade._lineage(record('worker', {'kind': 'seat'}, program=None)))
        self.assertIsNone(ClaudeFacade._lineage(record('worker', {'kind': 'seat', 'quick': 'yes'}, program=None)))
        self.assertIsNone(ClaudeFacade._lineage(record('worker', {'kind': 'seat', 'quick': True}, program='[ap:bad]')))
        self.assertIsNone(ClaudeFacade._lineage(record('worker', {'kind': 'seat', 'quick': True, 'program_core': 'x'}, program=None)))

    def test_the_inventory_row_carries_the_lineage(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        seat = host_record('bbbbbbbb-2222-4222-8222-222222222222', role='worker', cwd='/fixture/quick-worker')
        seat.update(program=f'[ap:{CORE}]', labels={'kind': 'seat', 'quick': True, 'conductor_n': 1, 'manager_session': '184'})
        manager = host_record('aaaaaaaa-1111-4111-8111-111111111111', cwd='/fixture/manager-sessions')
        lone = host_record('cccccccc-3333-4333-8333-333333333333', role='worker', cwd='/fixture/quick-worker')
        lone.update(socket='[ap:SOCKCC]', labels={'kind': 'seat', 'quick': True, 'manager_session': '193'})
        facade = ClaudeFacade(ClaudeReader([{'id': 'fixture', 'root': temp.name}]), FleetHost([manager, seat, lone]))
        rows = {r['uuid'][:8]: r for r in facade.sessions()['sessions']}
        self.assertEqual(rows['bbbbbbbb']['lineage'], {'program': CORE, 'kind': 'seat', 'conductorN': 1, 'quick': True,
                                                       'managerSession': '184'})
        self.assertNotIn('lineage', rows['aaaaaaaa'])
        self.assertEqual((rows['cccccccc']['lineage'], rows['cccccccc']['socket']),
                         ({'kind': 'seat', 'quick': True, 'managerSession': '193'}, '[ap:SOCKCC]'))


def oc_session(sid, title, metadata=None, parent=None, created=NOW - 3600, **extra):
    s = {'id': sid, 'title': title, 'metadata': metadata, 'agent': 'quick-ds41-flash', 'cost': 0.0123,
         'model': {'providerID': 'openrouter', 'id': 'deepseek/deepseek-v4.1-flash', 'variant': 'max'},
         'time': {'created': created * 1000, 'updated': created * 1000, 'idle': created * 1000 + 5000},
         'outcome': 'succeeded', **extra}
    if parent:
        s['parentID'] = parent
    return s


SESSIONS = [
    oc_session('ses_cond0000001', '[ap:PGBMNF]-021 · Phrase Signal Lab'),                    # title-only Conductor
    oc_session('ses_child000001', 'Build batch (@quick subagent)', parent='ses_cond0000001'),   # its subagent worker
    oc_session('ses_helper00001', f'[ap:{CORE}]-001 ▸ ⚡ quick helper · [ap:WB93JF]',
               {'kind': 'helper', 'quick': True, 'program': f'[ap:{CORE}]', 'parent_seat': LEAD,
                'conductor_sid': COND, 'conductor_n': 1, 'manager_session': '183'}),
    oc_session('ses_seat0000001', f'[ap:{CORE}]-001 ▸ worker · recheck',
               {'kind': 'seat', 'fresh': True, 'seat_role': 'worker', 'program': f'[ap:{CORE}]', 'conductor_n': 1}),
    oc_session('ses_newcond0001', '[ap:NEWPRG]-001 · ⚡ labelled',
               {'kind': 'conductor', 'program': '[ap:NEWPRG]', 'conductor_n': 1, 'quick_program': True}),
    oc_session('ses_warm0000001', f'[ap:{CORE}]-001 ▸ warm · parked', {'kind': 'warm', 'program': f'[ap:{CORE}]'}),
    oc_session('ses_manager0001', 'Manager-Session-180'),                                       # no program: not listed
    oc_session('ses_old00000001', '[ap:OLDPRG]-001 · ancient', created=NOW - 90 * 86400),       # outside the window
]


class ProgramRows(unittest.TestCase):
    def test_stored_conductor_titles_stay_whole_beyond_300_and_4096_characters(self):
        for size in (350, 5000):
            title = '[ap:LONG01]-001 · ' + ('A long program name 雪 — ' * size)[:size] + ' complete tail 雪'
            self.assertGreater(len(title), 300)
            for metadata in (None, {'kind': 'conductor', 'program': '[ap:LONG01]', 'conductor_n': 1}):
                with self.subTest(size=size, metadata=metadata):
                    stored = oc_session('ses_longcond001', title, metadata, agent='a' * 100,
                                        model={'providerID': 'p', 'id': 'm' * 250}, outcome='o' * 60)
                    row = opencode_programs.opencode_row(stored, {stored['id']: stored})
                    self.assertEqual((row['kind'], row['program'], row['title']), ('conductor', 'LONG01', title))
                    self.assertEqual(program_rows([stored], set(), NOW)[0]['title'], title)
                    self.assertEqual(stored['title'], title, 'never rewrite the stored source')
                    self.assertEqual((len(row['agent']), len(row['model']), len(row['outcome'])), (80, 200, 40),
                                     'unrelated display-field limits are unchanged')

    def test_metadata_first_title_fallback_children_by_parent_only_parked_warm_sessions_dropped(self):
        rows = {r['id']: r for r in program_rows(SESSIONS, {'ses_seat0000001'}, NOW)}
        self.assertEqual(set(rows), {'ses_cond0000001', 'ses_child000001', 'ses_helper00001', 'ses_seat0000001',
                                     'ses_newcond0001', 'ses_manager0001', 'ses_old00000001'})
        old = rows['ses_cond0000001']
        self.assertEqual((old['kind'], old['program'], old['conductorN'], old['numberSource']), ('conductor', 'PGBMNF', 21, 'title'))
        self.assertEqual((rows['ses_child000001']['kind'], rows['ses_child000001']['parentId']), ('subagent', 'ses_cond0000001'))
        helper = rows['ses_helper00001']
        self.assertEqual((helper['kind'], helper['parentSeat'], helper['conductorId'], helper['conductorN']), ('helper', LEAD, COND, 1))
        seat = rows['ses_seat0000001']
        self.assertEqual((seat['seatRole'], seat['busy'], seat['model'], seat['cost']),
                         ('worker', True, 'openrouter/deepseek/deepseek-v4.1-flash#max', 0.0123))
        self.assertEqual(rows['ses_newcond0001']['quickProgram'], True)
        self.assertEqual(seat['createdAt'], NOW - 3600)        # seconds, like the Claude rows
        self.assertFalse(any('metadata' in r or 'password' in json.dumps(r) for r in rows.values()))

    def test_freestanding_quick_workers_and_helpers_are_listed_from_metadata_alone_each_row_with_its_socket(self):
        quick = {'quick': True, 'program': None, 'manager_session': '193'}
        sessions = SESSIONS + [
            oc_session('ses_fswork1xxx', '▸ ⚡ quick worker · Research', {**quick, 'kind': 'seat', 'socket': '[ap:SOCKOC]'}),
            oc_session('ses_fshelp1xxx', '▸ ⚡ quick helper · sweep',                    # a Claude lead's OpenCode helper
                       {**quick, 'kind': 'helper', 'socket': '[ap:SOCKH1]', 'parent_seat': LEAD}),
            oc_session('ses_fsfire1xxx', '▸ ⚡ quick helper · fired', {**quick, 'kind': 'helper', 'parent_seat': 'ses_fswork1xxx'},
                       parent='ses_fswork1xxx'),                                            # a helper started as its lead's child
            oc_session('ses_fschild1xx', 'explore (@general subagent)', parent='ses_fswork1xxx'),   # the worker's own subagent
            oc_session('ses_fsfork1xxx', 'Manager-Session-193 ▸ ⚡ quick fork worker · fork',
                       {**quick, 'kind': 'seat', 'fork': True, 'socket': '[ap:SOCKFK]'}),
            # Not freestanding quick work: no quick stamp, a warm base, a malformed program, a quick-looking title alone.
            oc_session('ses_fsplain1xx', '▸ ⚡ quick worker · plain', {'kind': 'seat', 'program': None}),
            oc_session('ses_fswarm1xxx', 'warm', {**quick, 'kind': 'warm'}),
            oc_session('ses_fsbadprog1', '[ap:bad] ▸ ⚡ quick worker · x', {**quick, 'kind': 'seat', 'program': '[ap:bad]'}),
            oc_session('ses_fstitle1xx', '▸ ⚡ quick worker · no metadata'),
        ]
        rows = {r['id']: r for r in program_rows(sessions, {'ses_fshelp1xxx'}, NOW)}
        self.assertEqual({sid for sid in rows if sid.startswith('ses_fs')},
                          {'ses_fswork1xxx', 'ses_fshelp1xxx', 'ses_fsfire1xxx', 'ses_fschild1xx', 'ses_fsfork1xxx',
                           'ses_fsplain1xx', 'ses_fsbadprog1', 'ses_fstitle1xx'})
        worker = rows['ses_fswork1xxx']
        self.assertEqual({k: worker[k] for k in ('kind', 'quick', 'managerSession', 'socket', 'directory') if k in worker},
                         {'kind': 'seat', 'quick': True, 'managerSession': '193', 'socket': '[ap:SOCKOC]'})
        self.assertNotIn('program', worker)
        helper = rows['ses_fshelp1xxx']
        self.assertEqual((helper['kind'], helper['parentSeat'], helper['socket'], helper['busy']), ('helper', LEAD, '[ap:SOCKH1]', True))
        fired = rows['ses_fsfire1xxx']
        self.assertEqual((fired['kind'], fired['parentId'], fired['parentSeat']), ('helper', 'ses_fswork1xxx', 'ses_fswork1xxx'))
        child = rows['ses_fschild1xx']
        self.assertEqual(child, {**child, 'kind': 'subagent', 'parentId': 'ses_fswork1xxx'})
        self.assertNotIn('program', child)
        self.assertEqual((rows['ses_fsfork1xxx']['kind'], rows['ses_fsfork1xxx']['socket']), ('seat', '[ap:SOCKFK]'))
        self.assertTrue(all('conductorN' not in rows[sid] and 'numberSource' not in rows[sid] for sid in rows if sid.startswith('ses_fs')))
        # Program rows carry their socket too (display only), and nothing else changed for them.
        self.assertEqual(program_rows([oc_session('ses_seat0000001', f'[ap:{CORE}]-001 ▸ worker · x',
                                                  {'kind': 'seat', 'program': f'[ap:{CORE}]', 'socket': '[ap:SOCKPR]'})],
                                      set(), NOW)[0]['socket'], '[ap:SOCKPR]')
        self.assertNotIn('socket', rows['ses_seat0000001'])
        long = program_rows([oc_session('ses_fslong1xxx', 't', {**quick, 'kind': 'seat', 'socket': 'x' * 129})], set(), NOW)
        self.assertNotIn('socket', long[0])


class FakeOpenCode:
    def __init__(self, sessions=(), busy=(), messages=(), fail=None, active_answer=None):
        self.sessions, self.busy, self.messages, self.fail, self.calls = list(sessions), set(busy), list(messages), fail, []
        self.active_answer = active_answer

    def __call__(self, path, timeout, *, raw=False):
        self.calls.append(path)
        if self.fail:
            raise Unavailable(self.fail)
        answer = self.answer(path)
        return json.dumps(answer).encode() if raw else answer   # raw=True: the response's exact bytes

    def answer(self, path):
        if path.startswith('/api/session?'):
            return {'data': self.sessions, 'cursor': {}}
        if path == '/api/session/active':
            return self.active_answer if self.active_answer is not None else {'data': {sid: {'type': 'busy'} for sid in self.busy}}
        if path.endswith('/message?order=desc&limit=24'):
            return {'data': self.messages}
        sid = path.rsplit('/', 1)[1]
        return {'data': next((s for s in self.sessions if s['id'] == sid), None)}


class Reader(unittest.TestCase):
    def test_programs_are_cached_briefly_and_an_outage_is_unavailable_never_empty(self):
        clock = [NOW]
        fake = FakeOpenCode(SESSIONS)
        reader = OpenCodePrograms(request=fake, clock=lambda: clock[0], spawn=lambda work: work())
        first = reader.programs()
        self.assertEqual((first['status'], len(first['sessions'])), ('complete', 7))
        reader.programs()
        self.assertEqual(len([c for c in fake.calls if c.startswith('/api/session?')]), 1)   # cached
        clock[0] += 9
        fake.fail = 'OpenCode unreachable: refused'
        down = reader.programs()
        self.assertEqual((down['status'], down['error'], down['sessions']), ('unavailable', 'OpenCode unreachable: refused', []))

    def test_a_malformed_busy_map_is_unavailable_not_an_idle_list(self):
        for answer in ({'data': []}, {'data': 'not a map'}, []):
            with self.subTest(answer=answer):
                got = OpenCodePrograms(request=FakeOpenCode(SESSIONS, active_answer=answer)).programs()
                self.assertEqual((got['status'], got['sessions']), ('unavailable', []))
                self.assertIn('unexpected shape', got['error'])

    def test_a_glance_preserves_long_stored_conductor_titles_without_changing_other_field_limits(self):
        for size in (350, 5000):
            title = '[ap:LONG01]-001 · ' + ('A long program name 雪 — ' * size)[:size] + ' complete tail 雪'
            with self.subTest(size=size):
                stored = oc_session('ses_longcond001', title, agent='a' * 100,
                                    model={'providerID': 'p', 'id': 'm' * 250}, outcome='o' * 60)
                got = OpenCodePrograms(request=FakeOpenCode([stored])).glance(stored['id'])
                self.assertEqual((got['status'], got['title']), ('complete', title))
                self.assertEqual(stored['title'], title, 'a glance never rewrites its source')
                self.assertEqual((len(got['agent']), len(got['model']), len(got['outcome'])), (80, 200, 40))

    def test_a_glance_is_text_only_newest_five_clipped_and_needs_an_exact_id(self):
        messages = [{'type': 'assistant', 'time': {'created': (NOW - i) * 1000},
                     'content': [{'type': 'reasoning', 'text': 'secret thinking'}, {'type': 'tool', 'name': 'bash'},
                                 {'type': 'text', 'text': ('y' * 2000) if i == 0 else f'answer {i}'}]} for i in range(8)]
        messages.insert(3, {'type': 'user', 'time': {'created': NOW * 1000}, 'text': 'the prompt'})
        fake = FakeOpenCode(SESSIONS, busy={'ses_seat0000001'}, messages=messages)
        got = OpenCodePrograms(request=fake).glance('ses_seat0000001')
        self.assertEqual((got['status'], got['busy'], got['outcome']), ('complete', True, 'succeeded'))
        texts = [m['text'] for m in got['messages']]
        self.assertEqual(len(texts), 5)
        self.assertEqual(texts[-1], 'y' * 1200 + '…')                          # newest last, clipped
        self.assertIn('the prompt', texts)
        self.assertFalse(any('secret thinking' in t or 'bash' in t for t in texts))
        self.assertEqual(got['messages'][-1]['chars'], 2000)
        for bad in ('ses_x', '../api/credential', 'cc-44b3fc58-2503-4b76-8a39-f6b379756814', None):
            with self.assertRaises(ValueError):
                OpenCodePrograms(request=fake).glance(bad)
        fake.fail = 'OpenCode GET /api/session: HTTP 500'
        self.assertEqual(OpenCodePrograms(request=fake).glance('ses_seat0000001')['status'], 'unavailable')


class ForkLabels(unittest.TestCase):
    def test_only_a_forks_own_launch_labels_and_message_forms_count(self):
        forked = opencode_programs._forked
        self.assertTrue(forked({'kind': 'seat', 'seat_base': 'sb-1cac6454e703', 'warm_sid': 'ses_w'}))
        self.assertTrue(forked({'kind': 'seat', 'quick': True, 'fork': True, 'forked_from': 'ses_m'}))
        self.assertFalse(forked({'kind': 'seat', 'quick': True}))                 # a fresh quick worker
        self.assertFalse(forked({'kind': 'warm', 'seat_base': 'sb-1cac6454e703'}))  # a parked base is not a fork
        self.assertFalse(forked(None))
        prompt = opencode_programs._fork_launch_prompt('ses_seat0000001')
        self.assertTrue(prompt.search('You are a LEAD SEAT (session ses_seat0000001), forked from seat base sb-abc for program [ap:UHZREM]. Everything above'))
        self.assertTrue(prompt.search('You are a HELPER (session ses_seat0000001), forked from seat base sb-abc for program [ap:UHZREM].'))
        self.assertTrue(prompt.search('You are no longer the Manager. You are a QUICK WORKER (session ses_seat0000001) for program [ap:UHZREM], forked from a Manager session'))
        # A fresh seat that says it has no base, a fresh quick worker's prompt, and an ancestor's copied launch never match.
        self.assertFalse(prompt.search('You are a LEAD SEAT (session ses_seat0000001) in program [ap:UHZREM], started fresh: there is no seat base'))
        self.assertFalse(prompt.search('You are a QUICK WORKER (session ses_seat0000001). Your socket is [ap:X]: its note is your prompt.'))
        self.assertFalse(prompt.search('You are a LEAD SEAT (session ses_ancestor00001), forked from seat base sb-abc for program [ap:UHZREM].'))


class TurnTimes(unittest.TestCase):
    def test_first_and_last_turn_come_from_the_session_messages_markers_skipped_cached_briefly(self):
        ms = lambda s: int(s * 1000)
        messages = [{'type': 'user', 'time': {'created': ms(NOW - 600)}, 'text': 'go'},
                    {'type': 'assistant', 'time': {'created': ms(NOW - 590), 'streamed': ms(NOW - 585), 'completed': ms(NOW - 580)}},
                    {'type': 'model-switched', 'time': {'created': ms(NOW - 570)}},
                    {'type': 'assistant', 'time': {'created': ms(NOW - 300), 'streamed': ms(NOW - 200)}},   # still running
                    {'type': 'idle', 'time': {'created': ms(NOW - 100)}}]
        state = {'messages': messages, 'session': {'id': 'ses_seat0000001', 'time': {'created': ms(NOW - 600)},
                                                   'metadata': {'kind': 'seat'}}}
        calls = []

        def fake(path, timeout):
            calls.append(path)
            if path == '/api/session/ses_seat0000001':
                return {'data': state['session']}
            order = 'asc' if 'order=asc' in path else 'desc'
            page = state['messages'] if order == 'asc' else list(reversed(state['messages']))
            return {'data': page}
        clock = [NOW]
        reader = OpenCodePrograms(request=fake, clock=lambda: clock[0])
        got = reader.turns('ses_seat0000001')
        self.assertEqual((got['status'], got['firstAt'], got['lastAt']), ('complete', NOW - 600, NOW - 200))
        self.assertEqual(calls, ['/api/session/ses_seat0000001',
                                 '/api/session/ses_seat0000001/message?order=asc&limit=20',
                                 '/api/session/ses_seat0000001/message?order=desc&limit=20'])
        reader.turns('ses_seat0000001')
        self.assertEqual(len(calls), 3)                                              # cached
        clock[0] += 9
        messages[:] = [{'type': 'idle', 'time': {'created': ms(NOW)}}]                # no turn recorded: unknown, never a guess
        got = reader.turns('ses_seat0000001')
        self.assertEqual((got['firstAt'], got['lastAt']), (None, None))
        for bad in ('ses_x', '../api/credential', None):
            with self.assertRaises(ValueError):
                reader.turns(bad)
        self.assertEqual(OpenCodePrograms(request=FakeOpenCode(fail='OpenCode unreachable')).turns('ses_seat0000001')['status'],
                         'unavailable')

    def test_a_fork_first_turn_is_its_own_launch_or_creation_never_the_copied_history(self):
        ms = lambda s: int(s * 1000)
        base = [{'type': 'user', 'time': {'created': ms(NOW - 90000)}, 'text': '\u2550\u2550 BASE PACKAGE \u00b7 seat-base \u2550\u2550'},
                {'type': 'assistant', 'time': {'created': ms(NOW - 89990)}}]
        launch = {'type': 'user', 'time': {'created': ms(NOW - 500)},
                  'text': 'You are a LEAD SEAT (session ses_seat0000001), forked from seat base sb-abc for program [ap:UHZREM]. '
                          'Everything above this message is the seat base: shared material, a snapshot.'}
        own = [{'type': 'assistant', 'time': {'created': ms(NOW - 490), 'completed': ms(NOW - 480)}}]
        state = {'messages': base + [launch] + own,
                 'session': {'id': 'ses_seat0000001', 'time': {'created': ms(NOW - 505)},
                             'metadata': {'kind': 'seat', 'seat_base': 'sb-abc', 'warm_sid': 'ses_warm000001'}}}
        calls = []

        def fake(path, timeout):
            calls.append(path)
            if path == '/api/session/ses_seat0000001':
                if state['session'] is None:
                    raise Unavailable('OpenCode GET /api/session: HTTP 500')
                return {'data': state['session']}
            order = 'asc' if 'order=asc' in path else 'desc'
            page = state['messages'] if order == 'asc' else list(reversed(state['messages']))
            return {'data': page}
        clock = [NOW]
        reader = OpenCodePrograms(request=fake, clock=lambda: clock[0])
        got = reader.turns('ses_seat0000001')
        self.assertEqual((got['firstAt'], got['lastAt']), (NOW - 500, NOW - 480))     # its own launch message, never the copy
        # The copied conversation alone (an ancestor's own launch text names another session): a labelled
        # fork's own work starts at its creation, never 90000 s of copied base history.
        clock[0] += 9
        state['messages'] = [{'type': 'user', 'time': {'created': ms(NOW - 90000)},
                              'text': 'You are a LEAD SEAT (session ses_ancestor00001), forked from seat base sb-zzz for program [ap:UHZREM].'}] + base * 10
        got = reader.turns('ses_seat0000001')
        self.assertEqual((got['firstAt'], got['lastAt']), (NOW - 505, None))        # the copied last turn is not its own
        # Missing provenance keeps today's answer (the oldest stamp), never a guess.
        clock[0] += 9
        state['session'] = {'id': 'ses_seat0000001', 'time': {'created': ms(NOW - 505)}}
        got = reader.turns('ses_seat0000001')
        self.assertEqual(got['firstAt'], NOW - 90000)
        # An unreadable session object (no creation, no labels): the same legacy answer, never an outage.
        clock[0] += 9
        state['session'] = None
        got = reader.turns('ses_seat0000001')
        self.assertEqual((got['status'], got['firstAt']), ('complete', NOW - 90000))
        # A just-launched labelled fork with no turn yet: its creation is a known first, never a guess.
        clock[0] += 9
        state['messages'] = []
        state['session'] = {'id': 'ses_seat0000001', 'time': {'created': ms(NOW - 505)}, 'metadata': {'kind': 'seat', 'seat_base': 'sb-abc'}}
        got = reader.turns('ses_seat0000001')
        self.assertEqual((got['firstAt'], got['lastAt']), (NOW - 505, None))


class Routes(unittest.TestCase):
    def setUp(self):
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.httpd.opencode_programs = OpenCodePrograms(request=FakeOpenCode(SESSIONS), clock=lambda: NOW)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close(); self.thread.join()

    def request(self, route, method='GET', headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_address[1], timeout=15)
        connection.request(method, route, headers=headers or {})
        response = connection.getresponse()
        reply = json.loads(response.read())
        connection.close()
        return response.status, reply

    def test_same_origin_get_only_with_a_validated_id(self):
        status, reply = self.request('/api/opencode/programs')
        self.assertEqual((status, reply['backend'], reply['status'], len(reply['sessions'])), (200, 'opencode', 'complete', 7))
        status, reply = self.request('/api/opencode/programs?view=month')
        self.assertEqual((status, reply['backend']), (200, 'opencode'))
        status, reply = self.request('/api/opencode/glance?id=ses_seat0000001')
        self.assertEqual((status, reply['id']), (200, 'ses_seat0000001'))
        status, reply = self.request('/api/opencode/turns?id=ses_seat0000001')
        self.assertEqual((status, reply['id']), (200, 'ses_seat0000001'))
        for route, method, headers, code in (
                ('/api/opencode/programs', 'GET', {'Origin': 'http://evil.example'}, 403),
                ('/api/opencode/programs', 'POST', {}, 405),
                ('/api/opencode/programs?x=1', 'GET', {}, 400),
                ('/api/opencode/programs?view=day', 'GET', {}, 400),
                ('/api/opencode/glance?id=../credential', 'GET', {}, 400),
                ('/api/opencode/turns?id=../credential', 'GET', {}, 400),
                ('/api/opencode/turns', 'GET', {}, 400),
                ('/api/opencode/glance?id=ses_seat0000001&id=ses_seat0000002', 'GET', {}, 400),
                ('/api/opencode/credential', 'GET', {}, 404),
                ('/api/opencode/programs', 'OPTIONS', {}, 403)):
            status, _ = self.request(route, method, headers)
            self.assertEqual(status, code, (route, method, headers))


if __name__ == '__main__':
    unittest.main()
