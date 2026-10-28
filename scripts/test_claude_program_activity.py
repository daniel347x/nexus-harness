"""Program *Millions of events* U2.2 ([ap:UHKPV1] §7 U2.2) / wave-1 ruling R5: the read-only `program_activity` verb.

The pane's cold list needs to know which programs had activity since it last looked, and it must get that without
unpacking every session record (the cliff is server-side). So the host answers one row per program core -- its newest
moment in milliseconds and how many sessions it has -- from the in-memory record copy (`Store.cached_sessions`):
no SQL, no JSON decode, no copy, no liveness rule, no cap, no expiry, no refusal. A program's newest member decides
the program's `last_at` (one comparison per program); `sessions` counts every member, whatever its age, so a program
that is working now is answered whole.
"""
import asyncio
import os
import uuid

import pytest

from claude_headless.host import Host, list_program, list_recency, program_activity
from claude_headless.store import Store


def cached(record, text='{}'):
    """One `Store.cached_sessions` row: (sid, the record as the cache holds it, its committed JSON)."""
    return (record.get('native_uuid') or str(uuid.uuid4()), record, text)


def core(n, program='[ap:AAAAAA]', **extra):
    """A stored record carrying program core AAAAAA (n selects its own session)."""
    record = {'native_uuid': f'{n:08x}', 'execution_id': f'cc-{n:08x}', 'role': 'worker', 'state': 'exited',
              'owner': 'gone', 'exit_code': 0, **extra}
    if program is not None:
        record['program'] = program
    return record


# ------------------------------------------------------------------------------------------------ the grouping itself

def test_cores_are_list_programs_rule_and_a_program_has_one_row():
    shapes = [
        ({'program': '[ap:AAAAAA]'}, 'AAAAAA'),
        ({'program': ' [ap:BBBBBB] '}, 'BBBBBB'),                      # surrounding whitespace is fine
        ({'program': '[ap:aaaaaa]'}, None),                            # six capitals or digits only
        ({'program': 'AAAAAA'}, None),                                 # the program field must be the AP handle
        ({'labels': {'program_core': 'CCCCCC'}}, 'CCCCCC'),
        ({'labels': {'program_core': 'cccccc'}}, None),
        ({'program': '[ap:DDDDDD]', 'labels': {'program_core': 'AAAAAA'}}, 'DDDDDD'),  # program wins, as in list
        ({'program': 7}, None),
        ({'labels': 'CCCCCC'}, None),
        ({'program': '[ap:AAAAAAA]'}, None),
        ({}, None),
        ({'program': '', 'labels': {'quick': True}}, None),            # no core at all: skipped, never a row
    ]
    records = [{'native_uuid': f'{n:08x}', 'last_activity_at': 1_800_000_000, **shape}
               for n, (shape, _) in enumerate(shapes)]
    for record, (_, wanted) in zip(records, shapes):
        assert list_program(record) == wanted, record                 # the rule the answer is built on, not a copy
    answer = program_activity([cached(r) for r in records], {}, now=1_800_000_500)
    assert answer == {'programs': [{'core': 'AAAAAA', 'last_at': 1_800_000_000_000, 'sessions': 1},
                                   {'core': 'BBBBBB', 'last_at': 1_800_000_000_000, 'sessions': 1},
                                   {'core': 'CCCCCC', 'last_at': 1_800_000_000_000, 'sessions': 1},
                                   {'core': 'DDDDDD', 'last_at': 1_800_000_000_000, 'sessions': 1}],
                     'as_of': 1_800_000_500_000}
    assert [r['core'] for r in answer['programs']] == sorted(r['core'] for r in answer['programs'])  # ties by core


def test_a_programs_last_at_is_its_newest_member_in_milliseconds():
    # list_recency is the newest of four stamps (epoch seconds); a malformed stamp is not a moment.
    records = [core(1, last_activity_at=1_700_000_000, last_input_at=1_700_000_100),
               core(2, first_launched_at=1_700_000_200, context_generation_started_at='not a moment',
                    last_activity_at=1_700_000_050),
               core(3, program=None, labels={'program_core': 'BBBBBB', 'title': 'w3'}, last_activity_at=1_700_000_300)]
    answer = program_activity([cached(r) for r in records], {}, now=1_700_000_400)
    assert answer['as_of'] == 1_700_000_400_000
    assert answer['programs'] == [
        {'core': 'BBBBBB', 'last_at': 1_700_000_300_000, 'sessions': 1},
        {'core': 'AAAAAA', 'last_at': 1_700_000_200_000, 'sessions': 2}]   # newest of both members, both counted
    assert answer['programs'][1]['last_at'] == int(list_recency(records[1]) * 1000)


def test_since_bounds_each_program_at_its_newest_moment():
    rows = [cached({'native_uuid': '00000001', 'program': '[ap:AAAAAA]', 'last_activity_at': 1_800_000_000}),
            cached({'native_uuid': '00000002', 'labels': {'program_core': 'BBBBBB'},
                    'last_activity_at': 1_800_000_001}),
            cached({'native_uuid': '00000003', 'labels': {'program_core': 'CCCCCC'}})]  # no stamp at all

    def answer(since):
        return program_activity(rows, {'since': since}, now=1_800_000_010)['programs']

    assert [r['core'] for r in answer(1_800_000_000_000)] == ['BBBBBB', 'AAAAAA']
    assert [r['core'] for r in answer(1_800_000_001_000)] == ['BBBBBB']  # the boundary itself is kept
    assert answer(1_800_000_001_001) == []  # undated is not evidence of activity inside the horizon
    # An absent or malformed since is 0 (every program with a known moment), never a refusal.
    for odd in ({}, {'since': None}, {'since': 'soon'}, {'since': True}, {'since': float('nan')},
                {'since': float('inf')}, {'since': -5}):
        assert program_activity(rows, odd, now=1_800_000_010)['programs'] == answer(0), odd
    assert [r['core'] for r in answer(0)] == ['BBBBBB', 'AAAAAA']


def test_a_program_is_answered_by_its_newest_member_and_counted_whole():
    # The old member does not hide the program, and it is not dropped from the count either: the pane gets the unit.
    rows = [cached({'native_uuid': '00000001', 'program': '[ap:AAAAAA]', 'last_activity_at': 1_700_000_000}),
            cached({'native_uuid': '00000002', 'program': '[ap:AAAAAA]', 'last_activity_at': 1_800_000_000})]
    assert program_activity(rows, {'since': 1_750_000_000_000}, now=1_800_000_001)['programs'] == [
        {'core': 'AAAAAA', 'last_at': 1_800_000_000_000, 'sessions': 2}]


def test_liveness_is_not_a_reason_to_answer():
    # This verb reports activity, not busyness: a live-but-old session is old here (the pane's `live` read is the
    # other question). Nothing is kept regardless of age.
    rows = [cached({'native_uuid': '00000001', 'program': '[ap:AAAAAA]', 'state': 'ready', 'owner': 'host',
                    'pending_message_uuids': ['m'], 'last_activity_at': 1_700_000_000})]
    assert program_activity(rows, {'since': 1_750_000_000_000}, now=1_800_000_001)['programs'] == []


def test_nothing_recorded_and_nothing_with_a_core_is_an_empty_answer():
    assert program_activity([], {}, now=1_800_000_000.0) == {'programs': [], 'as_of': 1_800_000_000_000}
    assert program_activity([], {'since': 1_800_000_000_000}, now=1_800_000_000.0)['programs'] == []
    uncored = [cached({'native_uuid': f'{n:08x}', 'role': 'worker', 'last_activity_at': 1_800_000_000})
               for n in range(4)]
    assert program_activity(uncored, {}, now=1_800_000_000.0) == {'programs': [], 'as_of': 1_800_000_000_000}


# ---------------------------------------------------------------------------- the host's own path, and what it may cost

class CacheOnly:
    """A store stand-in with exactly what `program_activity` may read: the cached records. Anything else -- a SQL
    query, `Store.get`, `records`, `sessions`, a copy through `Store.fresh` -- fails loudly instead."""

    def __init__(self, entries):
        self.entries, self.calls = entries, 0

    def cached_sessions(self):
        self.calls += 1
        return self.entries


class Undecodable:
    """The committed JSON a `Store.fresh` copy would decode: reading it at all is the failed proof (`json.loads`
    refuses a non-string, and every string conversion says why)."""

    def __str__(self):
        raise AssertionError('the answer decoded a record'
                             ' (it may read Store.cached_sessions, never re-decode it)')


def host_at(tmp_path, moment=1_800_000_000.0):
    """A real Host (its store, its dispatch) with a fixed clock; no serve(), no process, no socket."""
    return Host(tmp_path / 'state', tmp_path / 's.sock', clock=lambda: moment)


def close_host(host):
    host.store.db.close()
    os.close(host.host_lock)


def test_the_host_answers_program_activity(tmp_path):
    async def run():
        host = host_at(tmp_path)
        try:
            records = [{'native_uuid': str(uuid.UUID(int=n)), 'execution_id': 'cc-' + str(uuid.UUID(int=n)),
                        'role': 'worker', 'state': 'exited', 'owner': 'gone',
                        'program': '[ap:AAAAAA]' if n <= 2 else '[ap:BBBBBB]',
                        'last_activity_at': 1_700_000_000 + n * 100}
                       for n in range(1, 5)]
            records.append({'native_uuid': str(uuid.UUID(int=9)), 'role': 'worker', 'labels': {'quick': True},
                            'last_activity_at': 1_800_000_000})
            for record in records:
                host.store.put('sessions', record['native_uuid'], record)
            answer = await host.dispatch({'method': 'program_activity',
                                          'params': {'since': 1_700_000_000_000}})
            assert answer == {'programs': [{'core': 'BBBBBB', 'last_at': 1_700_000_400_000, 'sessions': 2},
                                           {'core': 'AAAAAA', 'last_at': 1_700_000_200_000, 'sessions': 2}],
                              'as_of': 1_800_000_000_000}
            assert await host.dispatch({'method': 'program_activity',
                                        'params': {'since': 1_700_000_300_000}}) == {
                'programs': [{'core': 'BBBBBB', 'last_at': 1_700_000_400_000, 'sessions': 2}],
                'as_of': 1_800_000_000_000}
            health = await host.dispatch({'method': 'health', 'params': {}})
            assert 'program_activity' in health['implemented']
        finally:
            close_host(host)
    asyncio.run(run())


def test_the_answer_reads_only_the_cached_records_no_sql_no_decode_no_copy(tmp_path, monkeypatch):
    async def run():
        host = host_at(tmp_path)
        try:
            host.store.put('sessions', '00000001', core(1, last_activity_at=1_800_000_000))
            monkeypatch.setattr(Store, 'fresh', staticmethod(
                lambda *args, **kwargs: pytest.fail('program_activity copied a record')))
            # A cache row as the host holds it: one value JSON could never hold, and text no decoder accepts. Reading
            # either through json.dumps/json.loads (Store.fresh) fails, so the answer must not decode or copy.
            opaque = {'native_uuid': '00000002', 'program': '[ap:AAAAAA]', 'last_activity_at': 1_800_000_050,
                      'labels': {'program_core': 'AAAAAA', 'marker': {1, 2}}}
            stub = CacheOnly([*host.store.cached_sessions(), ('00000002', opaque, Undecodable())])
            real_store, host.store = host.store, stub
            try:
                answer = await host.dispatch({'method': 'program_activity', 'params': {}})
            finally:
                host.store = real_store
            assert stub.calls == 1                                   # one pass over the cache, nothing else
            assert answer == {'programs': [{'core': 'AAAAAA', 'last_at': 1_800_000_050_000, 'sessions': 2}],
                              'as_of': 1_800_000_000_000}
            assert opaque == {'native_uuid': '00000002', 'program': '[ap:AAAAAA]', 'last_activity_at': 1_800_000_050,
                              'labels': {'program_core': 'AAAAAA', 'marker': {1, 2}}}  # read, never changed
        finally:
            close_host(host)
    asyncio.run(run())
