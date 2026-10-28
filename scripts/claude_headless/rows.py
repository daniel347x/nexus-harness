"""One block-scoped display projection, independent of transport and storage.

Rows retain structure and raw witnesses, never prose bodies. The backend owns
registration, mutation stamps and the consumed-event watermark; the driver owns
replay order and the existing per-event diagnostic policy.
"""
from __future__ import annotations

import base64
import json
import math
import time
import uuid as uuid_module
from typing import Protocol

from .lexical import (BODY_LIMIT, SOURCE_LIMIT, SLASH_COMMAND, ReaderError, call_facts,
                      digest, parse_record, preview_data, projection, result_projection,
                      scalar, thinking_projection, unwrap_paste)


BLOCK_INDEX_LIMIT = 100_000
ROW_PAGE_EVENTS = 250


class RowsSource:
    """Store-shaped host reads with the pane's read-failure envelope."""
    def __init__(self, store):
        self.store = store

    def replay(self, **params):
        try:
            return self.store.replay(**params)
        except (OSError, RuntimeError, ValueError, AttributeError) as error:
            raise ReaderError(502, 'read-failure', 'host rows replay unavailable') from error

    def raw(self, **params):
        try:
            return self.store.raw(**params)
        except (OSError, RuntimeError, ValueError, AttributeError) as error:
            raise ReaderError(502, 'read-failure', 'host rows raw unavailable') from error


def _row_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


# @beacon[
#   id=opc@SqliteRows,
#   slice_labels=opc-rows,
#   kind=ast,
#   comment=Durable RowBackend on the host connection and current batch: indexed keys, stable positions, raw witnesses and watermark; saves touch committed row-health counters, reload undoes rollback or ownership changes, and one persisted unreadable status/reason/time names projection failures or unusable startup metadata.,
# ]
class SqliteRows:
    def __init__(self, store, sid):
        self.store, self.sid = store, sid
        self.reload()

    @staticmethod
    def empty(status='complete'):
        return {'epoch': 'rows-' + uuid_module.uuid4().hex, 'status': status, 'built_through': 0,
                'generations': {}, 'agent_tasks': [], 'child_tasks': [], 'agent_info': [],
                'counts': {}, 'as_of': 0, 'revision': 0, 'diagnostics': {}, 'incidents': 0}

    def state(self):
        row = self.store.db.execute('SELECT body FROM row_sets WHERE sid=?', (self.sid,)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def tag(state):
        return (state['epoch'], state['status'], state['built_through']) if state else None

    def reload(self):
        stored = self.state()
        self.data = stored or self.empty()
        self.cache_tag = self.tag(stored)
        self.agent_tasks = {tuple(k): v for k, v in self.data['agent_tasks']}
        self.child_tasks = {tuple(k): v for k, v in self.data['child_tasks']}
        self.agent_info = {tuple(k): v for k, v in self.data['agent_info']}
        self._rows, self._accessed = {}, {}

    def sync(self):
        if self.tag(self.state()) != self.cache_tag:
            self.reload()

    def _writing(self):
        if not self.store.depth:
            raise RuntimeError('rows writes require Store.batch')

    def save(self):
        self._writing()
        self.data.update(agent_tasks=list(self.agent_tasks.items()), child_tasks=list(self.child_tasks.items()),
                         agent_info=list(self.agent_info.items()))
        self.store.db.execute('INSERT OR REPLACE INTO row_sets VALUES (?,?)', (self.sid, _row_json(self.data)))
        if self.sid:
            self.store.rows_changed.add(self.sid)
        self.cache_tag = self.tag(self.data)

    def mark_unreadable(self, error):
        """Persist this set's failure after its projection has been rolled back, in the caller's batch."""
        self.data.update(status='unreadable', reason=f'{type(error).__name__}: {str(error)[:200]}',
                         at=time.time())
        self.save()

    @property
    def built_through(self):
        seq = self.data['built_through']
        return self.store.cursor(seq) if seq else None

    @staticmethod
    def decode(body, store=None, sid=None):
        row = json.loads(body)
        # JSON object keys are strings; the projector's block maps have integer keys.
        for key in ('stream_types', 'final_blocks', 'thinking', 'text_bytes'):
            row[key] = {int(k): v for k, v in row[key].items()}
        if row.pop('_parts_external', False):
            if store is None or sid is None:
                raise ValueError('external row parts require the owning store and session')
            text_count = row.pop('_text_part_count')
            thinking_counts = {n: t.pop('_part_count') for n, t in row['thinking'].items() if '_part_count' in t}
            input_counts = {n: c.pop('_part_count') for n, c in enumerate(row['calls']) if '_part_count' in c}
            row['text_parts'] = []
            for n in thinking_counts:
                row['thinking'][n]['parts'] = []
            for n in input_counts:
                row['calls'][n].update(partial=[], partial_hashes={})
            if text_count or any(thinking_counts.values()) or any(input_counts.values()):
                for record in store.db.execute('SELECT body FROM row_parts WHERE sid=? AND origin=? ORDER BY part',
                                               (sid, row['origin'])):
                    for part in json.loads(record[0]):
                        if part[0] == 'text' and part[1][3] not in row['final_blocks']:
                            row['text_parts'].append(part[1])
                        elif part[0] == 'thinking' and part[1] in thinking_counts:
                            row['thinking'][part[1]]['parts'].append(part[2])
                        elif part[0] == 'input':
                            for n in input_counts:
                                call = row['calls'][n]
                                if call['block'] == part[1]:
                                    call['partial'].append(part[2])
                                    call['partial_hashes'][part[2]] = part[3]
            if (len(row['text_parts']) != text_count or
                    any(len(row['thinking'][n]['parts']) != count for n, count in thinking_counts.items()) or
                    any(len(row['calls'][n]['partial']) != count for n, count in input_counts.items())):
                raise ValueError('row part witnesses incomplete')
        return row

    @staticmethod
    def encode(row):
        # CF-03: metadata is small; streamed witness lists are append-only row_parts, not rewritten bodies.
        body = {**row, '_parts_external': True, '_text_part_count': len(row['text_parts'])}
        body.pop('text_parts')
        body['thinking'] = {}
        for n, summary in row['thinking'].items():
            body['thinking'][n] = {k: v for k, v in summary.items() if k != 'parts'}
            if 'parts' in summary:
                body['thinking'][n]['_part_count'] = len(summary['parts'])
        body['calls'] = []
        for call in row['calls']:
            item = dict(call)
            if call['input_path'] == 'stream':
                item.pop('partial', None)
                item.pop('partial_hashes', None)
                item['_part_count'] = len(call.get('partial', []))
            body['calls'].append(item)
        return _row_json(body)

    def row(self, origin):
        if origin not in self._rows:
            found = self.store.db.execute('SELECT body FROM rows WHERE sid=? AND origin=?',
                                          (self.sid, origin)).fetchone()
            if found is None:
                raise ValueError('row key names no row')
            self._rows[origin] = self.decode(found[0], self.store, self.sid)
        self._accessed[origin] = self._rows[origin]
        return self._rows[origin]

    def new_row(self, event, kind, scope, uuid=None):
        self._writing()
        counts = self.data['counts']
        seq = counts.get(scope, 0)
        stamp = event.get('time')
        row = {'origin': event['cursor'], 'generation': event['generation'], 'scope': scope,
               'seq': seq, 'ordinal': sum(counts.values()),
               'time': stamp if type(stamp) in (int, float) and math.isfinite(stamp) and stamp >= 0 else None,
               'kind': kind, 'uuid': uuid, 'frame': None, 'calls': [], 'text_parts': [],
               'changed': self.stamp, 'body_changed': self.stamp, 'prose_bytes': 0,
               'origin_uuid': uuid, 'status': 'partial', 'genuine': False,
               'stream_types': {}, 'final_blocks': {}, 'thinking': {}, 'text_bytes': {}}
        if scope != 'main':
            row['after_origin'] = self.last_anchor_origin()
        counts[scope] = seq + 1
        self._rows[row['origin']] = self._accessed[row['origin']] = row
        return row

    def touch(self, row, body=True):
        row['changed'] = self.stamp
        if body:
            row['body_changed'] = self.stamp
        self._accessed[row['origin']] = row

    def open_row(self, generation, scope):
        origin = self.data['generations'].get(str(generation), {}).get(scope, {}).get('open_origin')
        return self.row(origin) if origin else None

    def set_open(self, generation, scope, row_or_None):
        scopes = self.data['generations'].setdefault(str(generation), {})
        scopes[scope] = {'open_origin': row_or_None['origin'] if row_or_None else None}

    def _key(self, kind, key):
        row = self.store.db.execute('SELECT body FROM row_keys WHERE sid=? AND kind=? AND k=?',
                                   (self.sid, kind, _row_json(key))).fetchone()
        return json.loads(row[0]) if row else None

    def _put_key(self, kind, key, value):
        self._writing()
        self.store.db.execute('INSERT OR REPLACE INTO row_keys VALUES (?,?,?,?)',
                              (self.sid, kind, _row_json(key), _row_json(value)))

    def identity(self, generation, scope, kind, key):
        value = self._key('identity', (generation, scope, kind, key))
        return (value[0], self.row(value[1])) if value else None

    def set_identity(self, generation, scope, kind, key, fingerprint, row):
        self._put_key('identity', (generation, scope, kind, key), (fingerprint, row['origin']))

    def calls(self, generation, scope, call_id):
        result = []
        for origin, block, frame, path in self._key('call', (generation, scope, call_id)) or []:
            row = self.row(origin)
            call = next(c for c in row['calls'] if (c['block'], c['frame'], c['input_path']) == (block, frame, path))
            result.append((row, call))
        return result

    def add_call(self, generation, scope, call_id, row, call):
        self.replace_calls(generation, scope, call_id, self.calls(generation, scope, call_id) + [(row, call)])

    def replace_calls(self, generation, scope, call_id, entries):
        self._put_key('call', (generation, scope, call_id),
                      [(r['origin'], c['block'], c['frame'], c['input_path']) for r, c in entries])
        for row, call in entries:
            if call.get('peers') != len(entries):
                call['peers'] = len(entries)
                self.touch(row, body=False)
            call['results'] = list(self.results(generation, scope, call_id))

    def results(self, generation, scope, call_id):
        return self._key('result', (generation, scope, call_id)) or []

    def add_result(self, generation, scope, call_id, result):
        self._put_key('result', (generation, scope, call_id), self.results(generation, scope, call_id) + [result])
        for row, call in self.calls(generation, scope, call_id):
            call['results'].append(result)
            self.touch(row, body=False)

    def witness(self, kind, scope, uuid, fingerprint):
        self._put_key('witness', (kind, scope, uuid, fingerprint), True)

    def diagnostic(self, event, status):
        self._writing()
        scope = 'agent:' + str(event['detail']['parent_tool_use_id']) if event['detail'].get('parent_tool_use_id') else 'main'
        counts = self.data['diagnostics']
        n = counts.get(scope, 0)
        item = {'cursor': event['cursor'], 'status': status, 'generation': event['generation'],
                'revision': self.data['revision']}
        self.store.db.execute('INSERT INTO row_diagnostics VALUES (?,?,?,?)', (self.sid, scope, n, _row_json(item)))
        counts[scope] = n + 1

    def incident(self, event, item):
        self._writing()
        n = self.data['incidents']
        self.store.db.execute('INSERT INTO row_incidents VALUES (?,?,?)', (self.sid, n,
            _row_json({**item, 'cursor': event['cursor'], 'revision': self.data['revision']})))
        self.data['incidents'] = n + 1

    def receipt(self, message_uuid):
        return self.store.get('receipts', self.sid, message_uuid)

    def observe(self, event, fields=None, text=None):
        pass  # Activity remains a read-side pane observation, not a second projection.

    def last_anchor_origin(self):
        # The primary-key index supplies scope order, not a scan of the event log.
        row = self.store.db.execute("SELECT origin FROM rows WHERE sid=? AND scope='main' AND "
            "(kind='assistant' OR (kind='user' AND json_extract(body,'$.genuine'))) ORDER BY seq DESC LIMIT 1",
            (self.sid,)).fetchone()
        return row[0] if row else None

    def begin(self, event):
        self._writing()
        self.stamp = int(event['cursor'].rsplit(':', 1)[1])
        self.data['revision'] += 1
        # Only open rows survive in memory between events. All other keys use indexed SELECTs.
        opened = {item['open_origin'] for scopes in self.data['generations'].values() for item in scopes.values()}
        self._rows = {k: v for k, v in self._rows.items() if k in opened}
        self._accessed = {}

    def abandon(self, event):
        self.reload()

    def advance(self, event):
        self._writing()
        for row in self._accessed.values():
            # Keep each stream part's original witness, including parts superseded by a final.
            cursor = event['cursor']
            parts = [('text', p) for p in row['text_parts'][-1:] if p[0] == cursor]
            parts += [('thinking', n, p) for n, t in row['thinking'].items() for p in t.get('parts', [])[-1:] if p[0] == cursor]
            parts += [('input', c['block'], cursor, c.get('partial_hashes', {}).get(cursor))
                      for c in row['calls'] if cursor in c.get('partial_hashes', {})]
            if parts:
                self.store.db.execute('INSERT OR IGNORE INTO row_parts VALUES (?,?,?,?)',
                                      (self.sid, row['origin'], self.stamp, _row_json(parts)))
            self.store.db.execute('INSERT OR REPLACE INTO rows VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (self.sid, row['scope'], row['seq'], row['ordinal'], row['origin'], row['generation'], row['kind'],
                 row['uuid'], row['changed'], row['body_changed'], self.encode(row)))
        self.data.update(built_through=self.stamp, as_of=self.stamp)
        self.save()

    # @beacon[
    #   id=opc@SqliteRows.requeue,
    #   slice_labels=opc-rows,
    #   kind=ast,
    #   comment=Seed/requeue known sessions with one indexed event head per sid; no empty-sid, tombstone or eventless seed, unusable metadata becomes that set's persisted unreadable fact, and every replacement touches row-health counters for publication at the caller's commit.,
    # ]
    @classmethod
    def requeue(cls, store, sid=None):
        """Forward-only row ownership, without a global event scan. Return the number queued.

        Unusable derived metadata is replaced with the full empty unreadable shape, never a startup
        failure or a retry loop. Raw evidence and existing row witnesses are untouched. There is no
        automatic transition out of unreadable; a rebuild must first remove the damaged row set.
        """
        if not store.depth:
            raise RuntimeError('rows migration requires Store.batch')
        changed = 0
        known = [sid] if sid is not None else [row[0] for row in store.db.execute(
            "SELECT sid FROM sessions WHERE sid!='' UNION SELECT sid FROM row_sets WHERE sid!=''")]
        required = cls.empty().keys()
        for found_sid in known:
            if found_sid == '':
                continue  # the retained host-wide event is not a new empty-sid conversation after junk
            row = store.db.execute('SELECT body FROM row_sets WHERE sid=?', (found_sid,)).fetchone()
            data = None
            if row is not None:
                try:
                    data = json.loads(row[0])
                    if (not isinstance(data, dict) or not required <= data.keys()
                            or not isinstance(data['epoch'], str) or not data['epoch']
                            or data['status'] not in ('complete', 'queued', 'running', 'unreadable')
                            or any(type(data[key]) is not int or data[key] < 0
                                   for key in ('built_through', 'as_of', 'revision', 'incidents'))
                            or any(not isinstance(data[key], dict) for key in ('counts', 'generations', 'diagnostics'))):
                        raise ValueError('invalid row-set metadata')
                    # These are the small serialized maps reload() needs even for a rows metadata read.
                    for key in ('agent_tasks', 'child_tasks', 'agent_info'):
                        if not isinstance(data[key], list):
                            raise ValueError('invalid row-set maps')
                        dict((tuple(k), value) for k, value in data[key])
                except (ValueError, TypeError, KeyError, RecursionError):
                    data = cls.empty('unreadable')
                    data.update(reason='invalid row-set metadata', at=time.time())
                    store.db.execute('INSERT OR REPLACE INTO row_sets VALUES (?,?)', (found_sid, _row_json(data)))
                    store.rows_changed.add(found_sid)
                    continue
                if data['status'] == 'unreadable':
                    continue
            try:
                record = store.get('sessions', found_sid)
            except (ValueError, TypeError):
                record = None  # unreadable session records stay the reconstruction owner's concern, not a migration gate
            if isinstance(record, dict) and record.get('archived'):
                continue  # forget may already have removed both its events and set; never recreate them
            newest = store.db.execute('SELECT MAX(seq) FROM events WHERE sid=?', (found_sid,)).fetchone()[0]
            if newest is None:
                continue
            if data is None or (data['status'] == 'complete' and data['built_through'] < newest) or data['status'] == 'running':
                data = data if data is not None else cls.empty()
                data['status'] = 'queued'
                store.db.execute('INSERT OR REPLACE INTO row_sets VALUES (?,?)', (found_sid, _row_json(data)))
                store.rows_changed.add(found_sid)
                changed += 1
        return changed


# @beacon[
#   id=opc@backfill_rows_page,
#   slice_labels=opc-rows,
#   kind=ast,
#   comment=One resumable background slice on the host connection: at most 250 events and the caller's elapsed-work budget, with at least one event of progress; a SAVEPOINT isolates failed projection and persists unreadable in the same batch, skipped thereafter; rows and watermark commit together.,
# ]
def backfill_rows_page(store, sid, backend=None, *, stop=None):
    """Commit one event-boundary slice, or roll its projection back and persist unreadable.

    Missing/complete/unreadable sets are never recreated or projected. The caller rechecks
    tombstones before every page and supplies its elapsed-work stop predicate.
    """
    backend = backend or SqliteRows(store, sid)
    backend.sync()
    if backend.cache_tag is None or backend.data['status'] not in ('queued', 'running'):
        return backend
    with store.batch():
        backend.data['status'] = 'running'
        backend.save()  # Starts the SQLite transaction before the nested SAVEPOINT.
        store.db.execute('SAVEPOINT rows_page')
        try:
            complete = project_through(RowsSource(store), sid, RowProjector(backend),
                                       limit=ROW_PAGE_EVENTS, stop=stop, ensure_progress=True)
        except Exception as error:
            store.db.execute('ROLLBACK TO rows_page')
            store.db.execute('RELEASE rows_page')
            backend.reload()  # SQLite rollback does not undo the backend's Python mutations.
            backend.mark_unreadable(error)  # A storage/rollback/mark failure still escapes Store.batch.
        else:
            store.db.execute('RELEASE rows_page')
            if complete:
                backend.data['status'] = 'complete'
                backend.save()
    return backend


class RowBackend(Protocol):
    """Mutable row/key storage and event hooks shared by memory and durable rows.

    Keys are (generation, scope, kind, identity) or (generation, scope, call_id).
    identity() returns (fingerprint, row) or None. calls() returns registered
    (row, call) pairs; call entries carry their own peers and results for reads.
    begin/abandon/advance preserve the original event accounting, including the
    one-revision rollback on a failed 409/502 read. No hook commits a transaction.
    """
    sid: str
    agent_tasks: dict
    child_tasks: dict
    agent_info: dict

    @property
    def built_through(self) -> str | None: ...
    def new_row(self, event, kind, scope, uuid=None) -> dict: ...
    def touch(self, row, body=True): ...
    def open_row(self, generation, scope): ...
    def set_open(self, generation, scope, row_or_None): ...
    def identity(self, generation, scope, kind, key): ...
    def set_identity(self, generation, scope, kind, key, fingerprint, row): ...
    def calls(self, generation, scope, call_id) -> list: ...
    def add_call(self, generation, scope, call_id, row, call): ...
    def replace_calls(self, generation, scope, call_id, entries): ...
    def results(self, generation, scope, call_id) -> list: ...
    def add_result(self, generation, scope, call_id, result): ...
    def witness(self, kind, scope, uuid, fingerprint): ...
    def diagnostic(self, event, status): ...
    def incident(self, event, item): ...
    def receipt(self, message_uuid) -> dict | None: ...
    def observe(self, event, fields=None, text=None): ...
    def last_anchor_origin(self) -> str | None: ...
    def begin(self, event): ...
    def abandon(self, event): ...
    def advance(self, event): ...


# @beacon[
#   id=opc@MemoryRows,
#   slice_labels=opc-rows,
#   kind=ast,
#   comment=The in-memory row backend over the existing index maps; owns all projection mutations, call peers/results, anchor origins and unchanged per-event accounting.,
# ]
class MemoryRows(RowBackend):
    def __init__(self, idx, *, receipt=None, observe=None):
        self.idx = idx
        self.sid = idx.execution[3:]
        self.agent_tasks, self.child_tasks, self.agent_info = idx.agent_tasks, idx.child_tasks, idx.agent_info
        self._receipt, self._observe = receipt, observe

    @property
    def built_through(self):
        return self.idx.cursor

    def new_row(self, event, kind, scope, uuid=None):
        idx = self.idx
        stamp = event.get('time')
        row = {'origin': event['cursor'], 'generation': event['generation'], 'scope': scope,
               'time': stamp if type(stamp) in (int, float) and math.isfinite(stamp) and stamp >= 0 else None,
               'ordinal': len(idx.rows),
               'kind': kind, 'uuid': uuid, 'frame': None, 'calls': [], 'text_parts': [],
               'changed': idx.revision, 'body_changed': idx.revision, 'prose_bytes': 0,
               'origin_uuid': uuid, 'status': 'partial', 'genuine': False,
               'stream_types': {}, 'final_blocks': {}, 'thinking': {}, 'text_bytes': {}}
        if scope != 'main':
            row['after_origin'] = self.last_anchor_origin()
        idx.rows.append(row)
        idx.by_origin[row['origin']] = row
        idx.dirty[row['origin']] = row
        return row

    def touch(self, row, body=True):
        row['changed'] = self.idx.revision
        self.idx.dirty[row['origin']] = row
        if body:
            row['body_changed'] = self.idx.revision

    def open_row(self, generation, scope):
        return self.idx.active.get((generation, scope))

    def set_open(self, generation, scope, row_or_None):
        if row_or_None is None:
            self.idx.active.pop((generation, scope), None)
        else:
            self.idx.active[(generation, scope)] = row_or_None

    def identity(self, generation, scope, kind, key):
        return self.idx.identities.get((generation, scope, kind, key))

    def set_identity(self, generation, scope, kind, key, fingerprint, row):
        self.idx.identities[(generation, scope, kind, key)] = (fingerprint, row)

    def calls(self, generation, scope, call_id):
        return self.idx.calls_by_id.get((generation, scope, call_id), [])

    def add_call(self, generation, scope, call_id, row, call):
        entries = self.calls(generation, scope, call_id) + [(row, call)]
        self.replace_calls(generation, scope, call_id, entries)

    def replace_calls(self, generation, scope, call_id, entries):
        self.idx.calls_by_id[(generation, scope, call_id)] = entries
        for row, call in entries:
            if call.get('peers') != len(entries):
                call['peers'] = len(entries)
                self.touch(row, body=False)
            call['results'] = list(self.results(generation, scope, call_id))

    def results(self, generation, scope, call_id):
        return self.idx.results.get((generation, scope, call_id), [])

    def add_result(self, generation, scope, call_id, result):
        self.idx.results.setdefault((generation, scope, call_id), []).append(result)
        for row, call in self.calls(generation, scope, call_id):
            call['results'].append(result)
            self.touch(row, body=False)

    def witness(self, kind, scope, uuid, fingerprint):
        self.idx.content_witnesses.add((kind, scope, uuid, fingerprint))

    def diagnostic(self, event, status):
        self.idx.diagnostics.append({'cursor': event['cursor'], 'status': status,
                                     'generation': event['generation'], 'revision': self.idx.revision})

    def incident(self, event, item):
        self.idx.incidents.append({**item, 'cursor': event['cursor'], 'revision': self.idx.revision})

    def receipt(self, message_uuid):
        return self._receipt(message_uuid) if self._receipt else None

    def observe(self, event, fields=None, text=None):
        if self._observe:
            self._observe(event, fields, text)

    def last_anchor_origin(self):
        for row in reversed(self.idx.rows):
            if row['scope'] == 'main' and (row['kind'] == 'assistant' or
                    (row['kind'] == 'user' and row.get('genuine'))):
                return row['origin']
        return None

    def begin(self, event):
        self.idx.revision += 1
        self.idx.dirty.clear()

    def abandon(self, event):
        self.idx.revision -= 1

    def advance(self, event):
        idx = self.idx
        # Charge retained row deltas, not every rewritten copy of a live row.
        # The event allowance covers other cursor/identity/diagnostic metadata.
        idx.summary_bytes += 256
        for row in idx.dirty.values():
            retained = len(repr({k: v for k, v in row.items() if k != '_summary_size'}).encode())
            idx.summary_bytes += retained - row.get('_summary_size', 0)
            row['_summary_size'] = retained
        idx.cursor = event['cursor']
        idx.indexed_bytes += event.get('length') or 0


def scope_for(fields):
    parent = scalar(fields, 'parent_tool_use_id')
    if parent is not None and not isinstance(parent, str):
        raise ValueError('invalid parent tool identity')
    return 'agent:' + parent if parent else 'main'


def permitted(text, user=False):
    # A message of Dan's that starts with a path (`/mnt/e/…`) or `!` is prose; only a
    # slash-command-shaped first word (`/compact`) is a command, not conversation.
    value = text.lstrip()
    return not value.startswith(('<command-', '<local-command-', '<system-reminder>',
                                 '<bash-input>', '<bash-stdout>')) and not (user and SLASH_COMMAND.match(value))


def frame_prose(fields, content, row):
    if scalar(fields, 'isMeta', default=False):
        return ''
    if content.kind == 'string':
        value = content.scalar
    else:
        value = '\n\n'.join(scalar(b.members(), 'text', 'string') for b in content.children
            if scalar(b.members(), 'type', 'string') == 'text' and
            permitted(scalar(b.members(), 'text', 'string'), row['kind'] == 'user'))
    if row['kind'] == 'user':
        return unwrap_paste(value) if row['genuine'] and permitted(value, True) else ''
    return value if permitted(value) else ''


# @beacon[
#   id=opc@join_status,
#   slice_labels=opc-rows,
#   kind=ast,
#   comment=Pure call-entry join status from its registered peer count and retained result witnesses; no rendering-time lookup into an index.,
# ]
def join_status(call):
    outputs = call['results']
    if call['peers'] != 1 or len(outputs) > 1:
        return 'ambiguous', None
    if call['input_path'] == 'stream':
        return 'not-final-fragment', None
    if not outputs:
        return 'pending-or-missing', None
    return ('error' if outputs[0]['error'] else 'stored'), outputs[0]


# @beacon[
#   id=opc@read_frame,
#   slice_labels=opc-rows,
#   kind=ast,
#   comment=One lexical parse of admitted native bytes from witnessed raw slices or the current read's in-hand frame bytes; no parsed-frame cache.,
# ]
def read_frame(cursor, store, frames=None):
    if frames is not None and cursor in frames:
        raw = frames[cursor]
        if len(raw) > SOURCE_LIMIT:
            raise ReaderError(413, 'source-limit', 'native frame exceeds 16 MiB reader limit; raw retained by host')
        return parse_record(raw)
    chunks, start, total = [], 0, None
    while True:
        page = store.raw(cursor=cursor, start=start, length=4 * 1024 * 1024)
        if type(page.get('total')) is not int or page['total'] > SOURCE_LIMIT:
            raise ReaderError(413, 'source-limit', 'native frame exceeds 16 MiB reader limit; raw retained by host')
        if total is not None and page['total'] != total:
            raise ReaderError(409, 'gap', 'raw frame size changed')
        total = page['total']
        chunk = base64.b64decode(page['base64'], validate=True)
        if page.get('start') != start or page.get('next_start') != start + len(chunk):
            raise ReaderError(409, 'gap', 'raw slice witness mismatch')
        chunks.append(chunk)
        start += len(chunk)
        if not page['has_more']:
            if start != total:
                raise ReaderError(409, 'gap', 'raw frame incomplete')
            break
        if not chunk:
            raise ReaderError(409, 'gap', 'raw slice made no progress')
    return parse_record(b''.join(chunks))


# @beacon[
#   id=opc@project_through,
#   slice_labels=opc-rows,
#   kind=ast,
#   comment=The one ordered replay driver over every event kind; owns gap checks and diagnostic/abandon policy, count limits and page/event stop predicates; background elapsed-work slices opt into at least one event of progress without changing viewer stop boundaries.,
# ]
def project_through(store, sid, projector, frames=None, *, limit=None, stop=None, ensure_progress=False):
    """Consume past built_through, returning True only on a complete replay page.

    stop(event) runs before each event; stop(None) before each page. The distinction
    lets the existing viewer retain its exact >= page / > event budget boundary.
    limit counts consumed events, not frames or rows. A stalled page returns False.
    ensure_progress defers a stop until one event has advanced, even if reading the
    page or that event alone exceeds a background time budget. Viewer stops keep
    their existing exact boundaries by default; a zero count limit still stops.
    """
    backend, consumed = projector.backend, 0
    while True:
        if ((limit is not None and consumed >= limit) or
                (stop and stop(None) and (consumed or not ensure_progress))):
            return False
        size = min(500, limit - consumed) if limit is not None else 500
        page = store.replay(cursor=backend.built_through, sid=sid, limit=size)
        if page.get('gap'):
            raise ReaderError(409, 'gap', 'durable replay gap; reselect')
        for event in page['events']:
            if ((limit is not None and consumed >= limit) or
                    (stop and stop(event) and (consumed or not ensure_progress))):
                return False
            backend.begin(event)
            try:
                projector.project(event, lambda: read_frame(event['cursor'], store, frames))
            except ReaderError as error:
                if error.code in (409, 502):
                    backend.abandon(event)
                    raise
                backend.diagnostic(event, error.status)
            except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as error:
                backend.diagnostic(event, 'unsupported:' + str(error)[:160])
            backend.advance(event)
            consumed += 1
        if not page['has_more']:
            return True
        if not page['events']:
            return False


# @beacon[
#   id=opc@RowProjector,
#   slice_labels=opc-rows,
#   kind=ast,
#   comment=The single block-witnessed stream/final and native-task row interpreter over a backend; preserves the existing lexical parsing, receipt proof, replay aliases and diagnostic behaviour.,
# ]
class RowProjector:
    def __init__(self, backend):
        self.backend = backend

    @staticmethod
    def _note_model(row, message):
        """The model a native frame says answered this message (display only; never a guess).

        Every frame of one message must name the same model: two different names leave none."""
        node = message.get('model')
        stated, model, _ = call_facts(node.scalar if node is not None and node.kind == 'string' else None, None, None)
        if not stated:
            return
        if row.get('model', model) != model:
            row['model_conflict'] = True
        row['model'] = model

    def project(self, event, frame):
        backend = self.backend
        kind = event['kind']
        backend.observe(event)
        if kind == 'incident':
            # Full permission requests remain private host receipts/raw, never a bar-page payload.
            item = {k: event['detail'][k] for k in ('incident_id', 'execution_id', 'generation', 'time',
                    'category', 'handler', 'source_cursor', 'message_uuids', 'task_acceptance', 'escalation')
                    if k in event['detail']}
            backend.incident(event, item)
            return
        if kind in ('raw_reconciliation_gap', 'unsupported_frame', 'malformed_frame', 'unknown_frame',
                    'protocol_attention', 'receipt_mismatch'):
            backend.diagnostic(event, kind)
            return
        if kind != 'native_frame' or event.get('stream') != 'stdout':
            return
        typ = event['detail'].get('type')
        if typ not in ('user', 'assistant', 'stream_event', 'result', 'system', 'tool_progress',
                       'command_lifecycle', 'control_request', 'control_response'):
            return
        text, root = frame()
        fields = root.members()
        typ, uuid = scalar(fields, 'type', 'string'), scalar(fields, 'uuid', 'string')
        session = scalar(fields, 'session_id', 'string')
        if session is not None and session != backend.sid:
            raise ValueError('foreign native session in host feed')
        subtype = scalar(fields, 'subtype', 'string')
        if typ == 'tool_progress' or (typ == 'system' and subtype in ('task_started', 'task_progress', 'task_notification')):
            scope, gen = scope_for(fields), event['generation']
            task = scalar(fields, 'task_id', 'string') or scalar(fields, 'tool_use_id', 'string')
            if not task or len(task) > 256:
                raise ValueError('invalid native task progress identity')
            # Subagent attribution from native identities only: a local_agent task names the
            # primary Agent call (tool_use_id); a task its subagent starts says
            # owned_by_subagent + parent_task_id (the agent's task_id). Its later
            # notification carries only task_id, so the mapping is remembered here.
            if subtype == 'task_started' and scalar(fields, 'task_type', 'string') == 'local_agent':
                agent_call = scalar(fields, 'tool_use_id', 'string')
                if agent_call and len(agent_call) <= 256:
                    backend.agent_tasks[(gen, task)] = agent_call
                    info = backend.agent_info.setdefault((gen, agent_call), {'index': len(backend.agent_info)})
                    info.update(agentId=task, state='running', started=event.get('time'),
                        description=(scalar(fields, 'description', 'string') or '')[:300] or None,
                        subagentType=(scalar(fields, 'subagent_type', 'string') or '')[:120] or None)
            if subtype == 'task_notification' and (gen, task) in backend.agent_tasks:
                info = backend.agent_info.get((gen, backend.agent_tasks[(gen, task)]))
                status = scalar(fields, 'status', 'string')
                if info is not None and status in ('completed', 'failed', 'stopped', 'killed'):
                    info.update(state=status, finished=event.get('time'))
            if scalar(fields, 'owned_by_subagent', default=False) is True:
                owner = backend.agent_tasks.get((gen, scalar(fields, 'parent_task_id', 'string')))
                if not owner:
                    return  # Never attribute a child's own nested task to the primary session.
                backend.child_tasks[(gen, task)] = owner
            key = (gen, scope, 'native-task', task)
            if (gen, task) in backend.child_tasks:
                scope = 'agent:' + backend.child_tasks[(gen, task)]
                key = (gen, scope, 'native-task', task)
            elif (gen, task) in backend.agent_tasks and typ == 'system':
                # The agent's own start/finish become entries of its activity group; its
                # per-tool progress repeats the forwarded child calls and is not a row.
                if subtype == 'task_progress':
                    return
                scope = 'agent:' + backend.agent_tasks[(gen, task)]
                key = (gen, scope, 'native-task', task + ':' + subtype)
            prior = backend.identity(*key)
            row = prior[1] if prior else backend.new_row(event, 'task', scope, 'task:'+key[3])
            row.update(frame=event['cursor'], frame_hash=digest(text.encode()),
                       status=subtype or 'tool_progress', origin_hash=row.get('origin_hash') or digest(text.encode()))
            backend.set_identity(*key, None, row)
            backend.touch(row)
            return
        if typ not in ('user', 'assistant', 'result'):
            backend.observe(event, fields, text)
        if typ not in ('user', 'assistant', 'stream_event', 'result'):
            return
        scope, gen = scope_for(fields), event['generation']
        stream_key = (gen, scope)
        if typ == 'stream_event':
            stream = fields['event'].members()
            sub = scalar(stream, 'type', 'string')
            if sub == 'message_start':
                message = stream['message'].members()
                if backend.open_row(*stream_key) is not None:
                    backend.diagnostic(event, 'overlapping-message-stream')
                row = backend.new_row(event, 'assistant', scope)
                row['message_id'] = scalar(message, 'id', 'string')
                self._note_model(row, message)
                row['start_hash'] = digest(text.encode())
                row['origin_hash'] = row['start_hash']
                backend.set_open(*stream_key, row)
            else:
                row = backend.open_row(*stream_key)
                if row is None:
                    backend.diagnostic(event, 'unpaired-partial-stream')
                    return
                number = scalar(stream, 'index')
                # Numeric lexical spans have no coerced scalar in the shared parser.
                if 'index' in stream:
                    number = json.loads(projection(text, stream['index']))
                    if type(number) is not int or not 0 <= number < BLOCK_INDEX_LIMIT:
                        raise ValueError('invalid stream block index')
                if sub == 'content_block_start':
                    block = stream['content_block'].members()
                    btyp = scalar(block, 'type', 'string')
                    if number is None or number in row['stream_types']:
                        raise ValueError('conflicting partial content block')
                    if btyp not in ('text', 'thinking', 'redacted_thinking', 'tool_use'):
                        raise ValueError('unsupported partial content block')
                    row['stream_types'][number] = btyp
                    row['current_block'] = number
                    row['status'] = 'partial'
                    if btyp == 'tool_use':
                        callid, name = scalar(block, 'id', 'string'), scalar(block, 'name', 'string')
                        if not callid or not name or len(callid) > 256 or len(name) > 256:
                            raise ValueError('conflicting partial tool block')
                        row['calls'].append({'block': number, 'id': callid, 'name': name,
                            'frame': event['cursor'], 'partial': [], 'partial_hashes': {}, 'inputBytes': None,
                            'previewData': {'schemaVersion': 1, 'status': 'unavailable', 'reason': 'input streaming'},
                            'status': 'not-final-fragment', 'input_path': 'stream'})
                        backend.add_call(gen, scope, callid, row, row['calls'][-1])
                    elif btyp == 'text':
                        value = scalar(block, 'text', 'string', default='')
                        row['text_bytes'][number] = len(value.encode())
                        if value:
                            row['text_parts'].append((event['cursor'], 'start', digest(text.encode()), number))
                            row['prose_bytes'] += len(value.encode())
                    else:
                        summary, _ = thinking_projection(block, number, BODY_LIMIT, streaming=True)
                        row['thinking'][number] = {'type': btyp, 'bytes': summary['textBytes'],
                            'parts': [(event['cursor'], 'start', digest(text.encode()))], 'stopped': False}
                elif sub == 'content_block_delta':
                    delta = stream['delta'].members()
                    dtyp = scalar(delta, 'type', 'string')
                    expected = {'text_delta': 'text', 'thinking_delta': 'thinking',
                                'signature_delta': 'thinking', 'input_json_delta': 'tool_use'}.get(dtyp)
                    if expected is None or row['stream_types'].get(number) != expected or number in row['final_blocks']:
                        raise ValueError('unpaired content delta')
                    if dtyp == 'text_delta':
                        value = scalar(delta, 'text', 'string')
                        if value is None:
                            raise ValueError('missing text delta')
                        row['text_parts'].append((event['cursor'], 'delta', digest(text.encode()), number))
                        row['prose_bytes'] += len(value.encode())
                        row['text_bytes'][number] += len(value.encode())
                    elif dtyp == 'thinking_delta':
                        value = scalar(delta, 'thinking', 'string')
                        if value is None:
                            raise ValueError('missing thinking delta')
                        summary = row['thinking'][number]
                        summary['parts'].append((event['cursor'], 'delta', digest(text.encode())))
                        summary['bytes'] += len(value.encode())
                    elif dtyp == 'input_json_delta':
                        fragment = scalar(delta, 'partial_json', 'string')
                        calls = [c for c in row['calls'] if c['block'] == number]
                        if len(calls) != 1 or fragment is None:
                            raise ValueError('unpaired input fragment')
                        call = calls[0]
                        call['partial'].append(event['cursor'])
                        call['partial_hashes'][event['cursor']] = digest(text.encode())
                        call['inputBytes'] = (call['inputBytes'] or 0) + len(fragment.encode())
                        # A bounded parseable fragment can already drive the ordinary preview.
                        # Never retain an unbounded growing tool body in the summary index.
                        if call['inputBytes'] <= 64 * 1024:
                            value = call.get('preview_fragment', '') + fragment
                            call['preview_fragment'] = value
                            try:
                                preview_text, preview_root = parse_record(value.encode())
                                call['previewData'] = preview_data(preview_text, preview_root)
                            except (ValueError, UnicodeError, RecursionError):
                                pass
                        else:
                            call.pop('preview_fragment', None)
                            call['previewData'] = {'schemaVersion': 1, 'status': 'unavailable',
                                                   'reason': 'partial preview limit; raw fragment available on Inspect'}
                elif sub == 'content_block_stop':
                    if number not in row['stream_types']:
                        raise ValueError('unpaired block stop')
                    if number in row['thinking']:
                        row['thinking'][number]['stopped'] = True
                elif sub == 'message_stop':
                    row['stream_stopped'] = True
                    if all(n in row['final_blocks'] for n in row['stream_types']):
                        backend.set_open(*stream_key, None)
                backend.touch(row)
            return
        if typ == 'result':
            # Visible result/error/denial receipt, never an assistant context message.
            key = (gen, scope, typ, uuid or event['cursor'])
            fingerprint = digest(text.encode())
            previous = backend.identity(*key)
            if previous:
                if previous[0] != fingerprint:
                    backend.diagnostic(event, 'conflicting-result-identity')
                return
            backend.observe(event, fields, text)
            row = backend.new_row(event, 'result', scope, uuid)
            row.update(frame=event['cursor'], frame_hash=fingerprint, origin_hash=fingerprint,
                       status='result-receipt; task acceptance unjudged')
            backend.set_identity(*key, fingerprint, row)
            return
        message = fields['message'].members()
        if scalar(message, 'role', 'string') != typ:
            raise ValueError('native role mismatch')
        content = message.get('content')
        if content is None or content.kind not in ('string', 'array'):
            raise ValueError('native content unsupported')
        message_id = scalar(message, 'id', 'string')
        key = (gen, scope, typ, uuid) if uuid else (gen, scope, typ, event['cursor'])
        fingerprint = digest(projection(text, content).encode())
        previous = backend.identity(*key)
        if previous:
            if previous[0] == fingerprint and previous[1].get('message_id') == message_id:
                previous[1]['replays'] = previous[1].get('replays', 0) + 1
                return
            backend.diagnostic(event, 'conflicting-event-identity')
            return
        backend.observe(event, fields, text)
        genuine = False
        if typ == 'user' and scope == 'main' and scalar(fields, 'isReplay', default=False) and uuid:
            # Read proof before mutating summaries: an interrupted receipt read must
            # not leave a half-created row which duplicates on reconnect.
            receipt = backend.receipt(uuid)
            external = content.scalar if content.kind == 'string' else (
                scalar(content.children[0].members(), 'text', 'string') if len(content.children) == 1 and
                scalar(content.children[0].members(), 'type', 'string') == 'text' else None)
            genuine = bool(receipt and not receipt.get('recovery_id') and external is not None and
                           receipt.get('submitted') == external)
            # A resume echo can repeat an earlier *input*, not submit a new one.
            # Its durable receipt's original generation plus UUID/exact content
            # proves lineage. Never apply this to provider IDs or bare same text.
            original_generation = receipt.get('generation') if receipt else None
            prior = backend.identity(original_generation, scope, 'user', uuid)
            if original_generation != gen and prior and prior[0] == fingerprint and receipt.get('submitted') == external:
                prior_row = prior[1]
                prior_row['replays'] = prior_row.get('replays', 0) + 1
                prior_row.setdefault('replay_generations', []).append(gen)
                backend.set_identity(*key, fingerprint, prior_row)
                backend.touch(prior_row, body=False)
                return
        row = backend.open_row(*stream_key) if typ == 'assistant' else None
        blocks = content.children if content.kind == 'array' else []
        # Native 2.1.299 emits a final assistant frame for EACH block before its
        # block_stop, not one full final message. Only the active scoped lifecycle
        # and observed block type/tool identity authorize that join.
        mapping = list(range(len(blocks)))
        conflict = bool(row and (not message_id or row.get('message_id') != message_id))
        if row and not conflict:
            if len(blocks) == 1:
                btyp = scalar(blocks[0].members(), 'type', 'string')
                candidates = [n for n, t in row['stream_types'].items()
                              if t == btyp and n not in row['final_blocks']]
                current = row.get('current_block')
                if current in candidates:
                    mapping = [current]
                elif len(candidates) == 1:
                    mapping = candidates
                else:
                    conflict = True
            elif any(row['stream_types'].get(n) != scalar(b.members(), 'type', 'string')
                     for n, b in enumerate(blocks)):
                conflict = True
            if not conflict:
                for source_block, block in enumerate(blocks):
                    b = block.members()
                    if scalar(b, 'type', 'string') == 'tool_use' and not any(
                            (c['block'], c['id'], c['name']) == (mapping[source_block],
                                scalar(b, 'id', 'string'), scalar(b, 'name', 'string')) for c in row['calls']):
                        conflict = True
                        break
        if conflict:
            backend.diagnostic(event, 'partial-final-identity-conflict')
            row = None
            mapping = list(range(len(blocks)))
        final_calls = []
        final_thinking = {}
        finals = {}
        for source_block, block in enumerate(blocks):
            number = mapping[source_block]
            b = block.members()
            btyp = scalar(b, 'type', 'string')
            finals[number] = {'type': btyp, 'frame': event['cursor'],
                'frame_hash': digest(text.encode()), 'source_block': source_block,
                'genuine': typ == 'assistant' and scope == 'main' and not fields.get('origin')
                           and not scalar(fields, 'isMeta', default=False)}
            if scalar(b, 'type', 'string') == 'tool_use':
                callid, name = scalar(b, 'id', 'string'), scalar(b, 'name', 'string')
                if typ != 'assistant' or not callid or not name or len(callid) > 256 or len(name) > 256:
                    raise ValueError('invalid final tool use')
                inp = projection(text, b.get('input'))
                final_calls.append({'block': number, 'id': callid, 'name': name,
                    'frame': event['cursor'], 'partial': [], 'input_path': 'final',
                    'frame_hash': digest(text.encode()), 'source_block': source_block,
                    'inputBytes': len(inp.encode()) if inp is not None else None,
                    'previewData': preview_data(text, b.get('input')),
                    'status': 'pending-or-missing'})
            elif scalar(b, 'type', 'string') == 'tool_result':
                callid = scalar(b, 'tool_use_id', 'string')
                if typ != 'user' or not callid:
                    raise ValueError('invalid model-facing result')
                out = result_projection(text, b.get('content'))
                result = {'frame': event['cursor'], 'block': source_block, 'uuid': uuid,
                    'frame_hash': digest(text.encode()),
                    'outputBytes': len(out.encode()) if out is not None else None,
                    'error': scalar(b, 'is_error', default=False)}
                backend.add_result(gen, scope, callid, result)
            elif btyp in ('thinking', 'redacted_thinking'):
                summary, _ = thinking_projection(b, number, BODY_LIMIT)
                final_thinking[number] = {'type': btyp, 'bytes': summary['textBytes'], 'stopped': True}
            elif btyp == 'text':
                value = scalar(b, 'text', 'string')
                if value is None:
                    raise ValueError('missing final text')
                finals[number]['bytes'] = len(value.encode())
            else:
                raise ValueError('unsupported-native-block:' + str(scalar(b, 'type', 'string')))
        # Stream->final joins need current stream lifecycle + generation/scope/message
        # and all observed block/tool IDs. Provider message.id alone never deduplicates.
        if row and any(c['block'] in finals and not any(
                (c['block'], c['id'], c['name']) == (f['block'], f['id'], f['name'])
                for f in final_calls) for c in row['calls']):
            backend.diagnostic(event, 'partial-final-identity-conflict')
            row = None
        if row is None:
            row = backend.new_row(event, typ, scope, uuid)
        for oldcall in [c for c in row['calls'] if c['block'] in finals]:
            k = (gen, scope, oldcall['id'])
            backend.replace_calls(*k, [(r, c) for r, c in backend.calls(*k) if r is not row])
        row.update(frame=event['cursor'], uuid=uuid, message_id=message_id,
                   frame_hash=digest(text.encode()), status='stored',
                   calls=[c for c in row['calls'] if c['block'] not in finals] + final_calls,
                   text_parts=[p for p in row['text_parts'] if p[3] not in finals], content_hash=fingerprint)
        if typ == 'assistant':
            self._note_model(row, message)
        row['final_blocks'].update(finals)
        row['thinking'].update(final_thinking)
        if row['stream_types'] and any(n not in row['final_blocks'] for n in row['stream_types']):
            row['status'] = 'partial'
        elif row.get('stream_stopped'):
            backend.set_open(*stream_key, None)
        if row['origin'] == event['cursor']:
            row['origin_hash'] = row['frame_hash']
        for call in final_calls:
            backend.add_call(gen, scope, call['id'], row, call)
        if typ == 'assistant':
            row['genuine'] = (scope == 'main' and not fields.get('origin') and
                not scalar(fields, 'isMeta', default=False) and all(b['genuine'] for b in row['final_blocks'].values()))
        else:
            row['genuine'] = genuine
        # Summaries retain byte counts, not prose bodies. Tool-only bars never reread
        # their full frame on paging; deliberately selected input/output is separate.
        if row['stream_types']:
            for n, b in finals.items():
                if b['type'] == 'text':
                    row['text_bytes'][n] = b['bytes']
            row['prose_bytes'] = sum(row['text_bytes'].values()) + max(0, len(row['text_bytes']) - 1) * 2
        else:
            prose = frame_prose(fields, content, row)
            row['prose_bytes'] = len(prose.encode())
        backend.set_identity(*key, fingerprint, row)
        backend.witness(typ, scope, uuid, fingerprint)
        backend.touch(row)
