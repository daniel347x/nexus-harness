"""Program *Millions of events* U1.2 ([ap:EEMJY8] A1): the host answers `list` from an in-memory copy of its session
records instead of decoding every record on every call. After every write the host makes (committed or rolled back,
inside or outside a batch) the copy is exactly what the table holds, in the table's order; readers get copies,
never the cached objects; and the new `live` / `programs` modes never hide a row the pane keeps."""
import asyncio
import json
from pathlib import Path
import sqlite3
import sys
import time
import uuid

import pytest

from claude_headless.host import list_kept_regardless_of_age, list_live, list_program, list_view
from claude_headless.settlement import RECONNECT_POLICY
from claude_headless.store import Store
from test_claude_headless_host import teardown, until
from test_claude_lifecycle import setup


def pane():
    """The pane's own rules (transcription-pane/claude_live.py), never a copy of them."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'transcription-pane'))
    from claude_live import ClaudeFacade
    return ClaudeFacade


def test_live_keeps_every_row_the_pane_keeps_and_programs_use_the_panes_lineage_rule():
    facade, now = pane(), 1_800_000_000
    matrix = []
    for state in ('ready', 'starting', 'exited', 'exited_draining', 'ownership_unknown', 'launch_failed', 'collected',
                  None, 'odd'):
        for owner in ('host', 'unknown', 'draining', 'gone', None):
            for exit_code in (None, 0, 1):
                for extra in ({}, {'pending_message_uuids': ['m']}, {'pending_recovery': 'r'}):
                    for age in (3600, 30 * 86400):  # inside the default window and far outside it
                        n = len(matrix)
                        matrix.append({'execution_id': f'cc-{n:08x}', 'native_uuid': f'{n:08x}', 'state': state,
                                       'owner': owner, 'exit_code': exit_code, 'last_activity_at': now - age,
                                       **extra})
    live = list_view(matrix, {'live': True}, now=now)
    assert live == [r for r in matrix if not facade._ended(r) or list_kept_regardless_of_age(r)]
    assert len(live) < len(list_view(matrix, {}, now=now))  # recent ended rows are in the window, never in live
    assert all(r in live for r in matrix if not facade._ended(r))  # a superset of the pane's live rows, at any age
    assert all(list_live(r) == (r in live) for r in matrix)

    shapes = [{'program': '[ap:AAAAAA]'}, {'program': ' [ap:AAAAAA] '}, {'program': '[ap:aaaaaa]'},
              {'program': 'AAAAAA'}, {'labels': {'program_core': 'AAAAAA'}}, {'labels': {'program_core': 'aaaaaa'}},
              {'program': '[ap:BBBBBB]', 'labels': {'program_core': 'AAAAAA'}}, {'program': 7},
              {'labels': 'AAAAAA'}, {'program': '[ap:AAAAAAA]'}, {'labels': {'program_core': 'AAAAAA', 'quick': True}},
              {}, {'program': '', 'labels': {'quick': True}}]
    rows = [{'execution_id': f'cc-p{n:07x}', 'native_uuid': f'p{n:07x}', 'role': 'worker', 'state': 'exited',
             'owner': 'gone', 'last_activity_at': now - 90 * 86400, **shape} for n, shape in enumerate(shapes)]
    for row in rows:
        assert list_program(row) == (facade._lineage(row) or {}).get('program'), row
    members = [r for r in rows if list_program(r) == 'AAAAAA']
    assert len(members) == 4 and not list_view(rows, {}, now=now)  # all ninety days old: out of the window
    assert list_view(rows, {'programs': ['AAAAAA']}, now=now) == members  # every member, at any age
    assert list_view(rows, {'programs': ['[ap:AAAAAA]', 'nope', 3]}, now=now) == members
    assert list_view(rows, {'programs': []}, now=now) == []  # like execution_ids: [], an empty selection

    # The selectors together answer every record any of them names (in the records' order); socket still narrows.
    both = rows + matrix
    pick = {'execution_ids': [matrix[0]['execution_id']], 'programs': ['BBBBBB'], 'live': True}
    union = [r for r in both if r['execution_id'] == matrix[0]['execution_id'] or list_program(r) == 'BBBBBB'
             or list_live(r)]
    assert list_view(both, pick, now=now) == union
    assert list_view(both, {**pick, 'all': True, 'window_days': 1}, now=now) == union  # selectors win, as by id did
    socketed = [{**r, 'socket': 'x [ap:SOCKET] y'} if n % 3 == 0 else r for n, r in enumerate(both)]
    assert list_view(socketed, {**pick, 'socket': '[ap:SOCKET]'}, now=now) == \
        [r for r in list_view(socketed, pick, now=now) if r.get('socket')]
    # Malformed selector values are ignored: the default window applies, never a refusal.
    for odd in ({'live': 'yes'}, {'live': 1}, {'programs': 'AAAAAA'}, {'programs': None}, {'execution_ids': 'cc-1'}):
        assert list_view(both, odd, now=now) == list_view(both, {}, now=now)


# ---------------------------------------------------------------------------- the cache against the table, step by step

NOW = time.time()


def stored(n, **extra):
    """A record of another session, shaped like the live host's."""
    sid = str(uuid.UUID(int=n))
    return {'native_uuid': sid, 'execution_id': 'cc-' + sid, 'generation': 1, 'role': 'worker', 'state': 'exited',
            'owner': 'gone', 'exit_code': 0, 'cwd': '/tmp', 'labels': {'title': f'w{n}', 'kind': 'worker'},
            'last_activity_at': NOW - 3600, 'first_launched_at': NOW - 7200, 'launch': {'argv': ['claude'] * 20},
            'tools': ['Bash'] * 10, **extra}


def table(store):
    """A fresh decode of the table, exactly as Store.sessions() reads it (same query, so the same order)."""
    return [(sid, json.loads(text)) for sid, text in store.db.execute('SELECT sid, record FROM sessions')]


def old_list(host, params):
    """The list before the cache: every record decoded and completed, then windowed."""
    rows = {r.get('native_uuid'): {**r, 'reconnect_policy': RECONNECT_POLICY, **host.attention_fields(
        r.get('native_uuid'), host.store.get('sessions', r.get('native_uuid')) or {})} for r in host.store.sessions()}
    rows.update({sid: host.state(sid) for sid in host.sessions})
    return list_view(list(rows.values()), params, now=host.supervisor.clock())


def old_manager(host):
    records = {r['native_uuid']: r for r in host.store.sessions() if r.get('role') == 'Manager'}
    records.update({sid: s.record for sid, s in host.sessions.items() if s.record.get('role') == 'Manager'})
    return max(records.values(), key=lambda r: (r.get('last_input_at') or r.get('first_launched_at') or 0,
                                                r.get('first_launched_at') or 0, r['native_uuid'])) if records else None


def orphans_handed(host):
    """The records recheck_orphans hands to recheck_orphan (which is held still here)."""
    seen, real = [], host.recheck_orphan
    host.recheck_orphan = seen.append
    try:
        host.recheck_orphans()
    finally:
        host.recheck_orphan = real
    return seen


async def agree(host, params_list):
    """The cache, list in every mode, current_manager and recheck_orphans all equal a fresh decode of the table."""
    store = host.store
    cached = store.cached_sessions()
    assert [record for _, record, _ in cached] == store.sessions()  # content and order, any open batch overlaid
    if not store.depth:
        assert [(sid, record) for sid, record, _ in cached] == table(store)
        assert all(json.loads(text) == record for _, record, text in cached)
    for params in params_list:
        got = await host.dispatch({'method': 'list', 'params': params})
        want = old_list(host, params)
        assert json.dumps(got) == json.dumps(want), params  # the same rows, the same order, byte for byte
    assert host.supervisor.quota_wake.current_manager() == old_manager(host)
    assert orphans_handed(host) == [r for r in store.sessions() if r.get('state') == 'ownership_unknown'
                                    and r.get('native_uuid') not in host.sessions]


def test_the_cache_is_the_table_after_every_host_write(tmp_path):
    async def run():
        host, serving, session, _ = await setup(tmp_path)
        store = host.store
        try:
            assert store.session_cache is not None  # filled at open, before anything reads it
            a1 = stored(1, program='[ap:AAAAAA]', socket='x [ap:SOCKET] y')
            a2 = stored(2, labels={'program_core': 'AAAAAA', 'title': 'w2'}, last_activity_at=NOW - 30 * 86400,
                        first_launched_at=NOW - 31 * 86400)
            b1 = stored(3, program='[ap:BBBBBB]', role='helper', state='ready', owner='host', exit_code=None,
                        socket='[ap:SOCKET]')
            orphan = stored(4, state='ownership_unknown', owner='unknown', exit_code=None, pid=999999,
                            process_witness={'pid': 999999, 'start_ticks': '1', 'boot_id': 'gone'})
            manager = stored(5, role='Manager', last_input_at=NOW + 1000)  # the newest Manager: a stored one
            held = stored(6, pending_message_uuids=['m'], last_activity_at=NOW - 30 * 86400,
                          first_launched_at=NOW - 31 * 86400)
            modes = [{}, {'all': True}, {'summary': True}, {'all': True, 'summary': True}, {'live': True},
                     {'live': True, 'summary': True}, {'programs': ['AAAAAA']}, {'programs': ['[ap:BBBBBB]']},
                     {'execution_ids': [a2['execution_id'], session.record['execution_id'], 'cc-nope']},
                     {'execution_ids': []}, {'socket': '[ap:SOCKET]', 'all': True}, {'window_days': 1}]

            # 1 · writes outside a batch: each commits at once and is cached at once
            for record in (a1, a2, b1, orphan, manager, held):
                store.put('sessions', record['native_uuid'], record)
            await agree(host, modes)
            assert host.supervisor.quota_wake.current_manager()['native_uuid'] == manager['native_uuid']

            # 2 · writes inside a batch: seen mid-batch as before, cached only at the commit, in the table's new order
            n1 = stored(7, program='[ap:AAAAAA]')
            with store.batch():
                store.put('sessions', b1['native_uuid'], {**b1, 'state': 'exited', 'owner': 'gone', 'exit_code': 0})
                store.put('sessions', n1['native_uuid'], n1)
                await agree(host, modes)
                assert store.session_cache[b1['native_uuid']][0]['state'] == 'ready'  # not committed yet
            await agree(host, modes)
            assert [sid for sid, _ in table(store)][-2:] == [b1['native_uuid'], n1['native_uuid']]

            # 3 · a batch that raises: rolled back whole, and none of it reaches the cache
            n2 = stored(8)
            with pytest.raises(RuntimeError):
                with store.batch():
                    store.put('sessions', a1['native_uuid'], {**a1, 'state': 'ready', 'owner': 'host'})
                    store.put('sessions', n2['native_uuid'], n2)
                    store.event(n2['native_uuid'], 1, 'fixture')
                    raise RuntimeError('fixture')
            await agree(host, modes)
            assert n2['native_uuid'] not in dict(table(store)) and dict(table(store))[a1['native_uuid']] == a1

            # 4 · a write whose commit fails: the row is pending in the open transaction (the host's own reads see
            # it, as before); a later rollback takes it away again
            real_commit = store.commit
            def failing_commit():
                raise sqlite3.OperationalError('fixture: disk I/O error')
            store.commit = failing_commit
            try:
                with pytest.raises(sqlite3.OperationalError):
                    store.put('sessions', n2['native_uuid'], n2)
            finally:
                store.commit = real_commit
            await agree(host, modes)
            with pytest.raises(RuntimeError):
                with store.batch():
                    store.event(n2['native_uuid'], 1, 'fixture')
                    raise RuntimeError('fixture')
            await agree(host, modes)
            assert n2['native_uuid'] not in dict(table(store))

            # 5 · readers change what they were given: nothing they hold is the cache's own object
            for row in await host.dispatch({'method': 'list', 'params': {'all': True}}):
                row['state'] = 'mutated'
                if isinstance(row.get('labels'), dict):
                    row['labels']['title'] = 'mutated'
            winner = host.supervisor.quota_wake.current_manager()
            winner['labels']['title'], winner['state'] = 'mutated', 'mutated'
            for record in orphans_handed(host):
                record.update(owner='gone', state='exited')
            for _, record, text in store.cached_sessions():
                copy = Store.fresh(record, text)
                copy['state'] = 'mutated'
                if isinstance(copy.get('labels'), dict):
                    copy['labels']['title'] = 'mutated'
            await agree(host, modes)

            # 6 · the orphan recheck: a failed write leaves the orphan as it was; then it is proved gone
            real_event = store.event
            def failing_event(sid, generation, kind, *args, **kwargs):
                if kind == 'orphan_owner_proved_gone':
                    raise sqlite3.OperationalError('fixture: disk full')
                return real_event(sid, generation, kind, *args, **kwargs)
            store.event = failing_event
            try:
                host.recheck_orphans()  # recheck_orphan updates its record first; the write then fails
            finally:
                store.event = real_event
            assert dict(table(store))[orphan['native_uuid']]['state'] == 'ownership_unknown'
            await agree(host, modes)
            host.recheck_orphans()
            proved = dict(table(store))[orphan['native_uuid']]
            assert proved['owner'] == 'gone' and proved['state'] == 'exited' and proved['session_directory_missing']
            assert orphan['execution_id'] not in [r['execution_id'] for r in
                                                  await host.dispatch({'method': 'list', 'params': {'live': True}})]
            await agree(host, modes)

            # 7 · a live session ends and is released: list now reads its stored record from the cache
            assert session.record['execution_id'] in [r['execution_id'] for r in
                                                      await host.dispatch({'method': 'list', 'params': {'live': True}})]
            await host.dispatch({'method': 'close_input', 'params': {'execution_id': session.sid, 'generation': 1}})
            await until(lambda: session.sid not in host.sessions)
            await agree(host, modes)
            ended = next(r for r in await host.dispatch({'method': 'list', 'params': {}})
                         if r['execution_id'] == session.record['execution_id'])
            assert ended['owner'] == 'gone' and ended == old_list(host, {'execution_ids': [ended['execution_id']]})[0]
        finally:
            await teardown(host, serving)
    asyncio.run(run())


def test_a_narrow_list_copies_and_completes_only_the_rows_it_answers(tmp_path, monkeypatch):
    async def run():
        host, serving, session, _ = await setup(tmp_path)
        try:
            for n in range(1, 201):
                host.store.put('sessions', str(uuid.UUID(int=n)),
                               stored(n, program='[ap:AAAAAA]' if n <= 3 else '[ap:BBBBBB]'))
            copies, completed = [], []
            real_fresh, real_attention = Store.fresh, host.attention_fields
            monkeypatch.setattr(Store, 'fresh', staticmethod(lambda record, text=None: copies.append(1) or
                                                             real_fresh(record, text)))
            monkeypatch.setattr(host, 'attention_fields', lambda sid, record: completed.append(sid) or
                                real_attention(sid, record))
            for params, rows in (({'execution_ids': []}, 0), ({'execution_ids': [stored(7)['execution_id']]}, 1),
                                 ({'programs': ['AAAAAA']}, 3), ({'live': True}, 1)):
                copies.clear()
                completed.clear()
                answer = await host.dispatch({'method': 'list', 'params': params})
                assert len(answer) == rows, params
                stored_rows = rows - (1 if params.get('live') else 0)  # the live session is its own state() row
                assert len(copies) == stored_rows, params           # one copy per stored row answered, not 201
                # attention for each stored row answered, plus state() of each live session (always, as before)
                assert len(completed) == stored_rows + len(host.sessions), params
        finally:
            await teardown(host, serving)
    asyncio.run(run())
