#!/usr/bin/env python3
"""Private row proofs on copies, or one read-only activation snapshot.

Replay and stored-rows sources share the pane's wire renderers, not a second projector.
Keep dumps and backup-API copies under /tmp/opencode/.
Each JSONL record is one (sid, scope): rows, main-only context, diagnostics,
witnesses, and the main scope's per-page subagent activity/registry.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile


REF_FIELDS = frozenset(('recordRef', 'callRef', 'leafRef', 'sessionRef', 'scopeRef',
                        'targetRef', 'fileEpoch', 'contextIdentity'))


class Normalizer:
    """One session's opaque references, numbered in first-encounter order."""

    def __init__(self, main_positions=None):
        self.refs = {}
        self.main_positions = main_positions or {}

    def value(self, value, key=None):
        if key == 'key' and isinstance(value, str):
            # Activity keys are a recordRef with an optional :text or :block:id suffix.
            record, separator, suffix = value.partition(':')
            return str(self.value(record, 'recordRef')) + separator + suffix
        if key == 'after':
            if value is None:
                return None
            return self.main_positions[value]  # an unproved anchor must fail the proof
        if key in REF_FIELDS and value is not None:
            return self.refs.setdefault(value, len(self.refs))
        if isinstance(value, dict):
            return {k: self.value(v, k) for k, v in value.items() if k != 'effort'}
        if isinstance(value, (list, tuple)):
            return [self.value(v) for v in value]
        return value


def checked_store_dir(directory):
    """Never let a copy-only command open the live database (including aliases)."""
    directory = Path(directory).expanduser().resolve()
    roots = {Path.home() / '.local/state/opencode-companion/claude-headless',
             Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) /
             'opencode-companion/claude-headless'}
    if any(directory.is_relative_to(root.resolve()) for root in roots):
        raise ValueError('--store must be a backup-API copy, never the live state root')
    return directory


def load_tree(tree):
    tree = Path(tree).resolve()
    sys.path[:0] = [str(tree / 'transcription-pane'), str(tree / 'scripts')]
    modules = [importlib.import_module(name) for name in
               ('claude_reader', 'claude_live', 'claude_headless.store')]
    if any(not Path(module.__file__).resolve().is_relative_to(tree) for module in modules):
        raise ValueError('reference-tree import was shadowed by an already imported module')
    return modules


def open_copy(store_type, directory, raw_root):
    class CopyStore(store_type):
        """Store read methods, never its writing constructor or startup migration."""

        def __init__(self):
            self.root = directory
            self.db = sqlite3.connect((directory / 'host.sqlite3').as_uri() + '?mode=ro', uri=True)
            self.db.row_factory = sqlite3.Row
            self.db.execute('BEGIN')
            self.identity = self.db.execute("SELECT value FROM meta WHERE key='identity'").fetchone()[0]
            self.dirty = {}

        def path(self, sid, generation, stream):
            if stream not in ('stdout', 'stderr', 'stdin'):
                raise ValueError('invalid raw stream')
            return raw_root / sid / f'g{generation:06d}' / (stream + '.bin')

    return CopyStore()


class StoreClient:
    """Read-only, in-process equivalent of the pane's host client; no Host startup."""

    def __init__(self, store):
        self.store = store

    def call(self, method, **params):
        sid = params.get('execution_id', '').removeprefix('cc-')
        if method == 'list':
            return self.store.sessions()
        if method == 'state':
            return self.store.get('sessions', sid)
        if method == 'replay':
            return self.store.replay(sid=sid, cursor=params.get('cursor'), limit=params.get('limit', 100))
        if method == 'raw':
            return self.store.raw(**params)
        if method == 'receipt':
            return self.store.get('receipts', sid, params['message_uuid'].removeprefix('cc-'))
        if method == 'incident':
            return self.store.get('incidents', sid, params['incident_id'])
        if method == 'incidents':
            return self.store.incidents(sid=sid or None,
                                        **{k: v for k, v in params.items() if k != 'execution_id'})
        raise ValueError('offline read client refuses method: ' + method)


def session_dump(facade, execution, generation):
    """Walk public pages for every scope, then normalise without hiding wire fields."""
    session = facade._ref('session', (execution, generation))
    pages_by_scope = {}
    todo = ['main']
    while todo:
        scope = todo.pop(0)
        address = {'sessionRef': session,
                   'scopeRef': facade._ref('scope', (execution, generation, scope))}
        pages, cursor = [], None
        while True:
            page = facade.events(**address, **({'cursor': cursor} if cursor else {}))
            pages.append(page)
            if not page['hasMore']:
                break
            next_cursor = page['nextCursor']
            if not next_cursor or next_cursor == cursor:
                raise ValueError('copy proof page made no progress')
            cursor = next_cursor
        pages_by_scope[scope] = pages
        if scope == 'main':
            todo.extend(sorted(page['traces']))
    main_positions = {row['recordRef']: row['seq'] for page in pages_by_scope['main']
                      for row in page['events'] + page['eventUpdates']}
    normal = Normalizer(main_positions)
    context = facade.context(pages_by_scope['main'][-1]['targetRef'], 1, 400 * 1024)['text']
    idx = facade.indexes[execution]
    result = []
    for scope, pages in pages_by_scope.items():
        rows = {}
        diagnostics = []
        for page in pages:
            for row in page['events'] + page['eventUpdates']:
                rows[row['seq']] = row
            diagnostics.extend(page['diagnostics'])
        if list(sorted(rows)) != list(range(len(rows))):
            raise ValueError('copy proof rows are not dense')
        result.append(normal.value({
            'sid': execution.removeprefix('cc-'), 'scope': scope,
            'rows': [rows[n] for n in sorted(rows)],
            'context': context if scope == 'main' else '',
            'diagnostics': diagnostics,
            'witnesses': sorted((w for w in idx.content_witnesses if w[1] == scope),
                                key=lambda w: json.dumps(w)),
            'activityPages': [{'subagentActivity': page['subagentActivity'],
                               'subagents': page['subagents']} for page in pages] if scope == 'main' else [],
        }))
    return result


def stored_facade(live, history, store, **kwargs):
    """Offline-only view of stored structure through the unchanged pane wire/body code.

    No replay, RowProjector or projection driver is invoked. Activity groups are
    rendered by the same _subagent_activity from persisted rows and agent_info.
    This is a proof adapter, not the pane's rows-first implementation (U3).
    """
    from claude_headless.rows import SqliteRows

    class StoredFacade(live.ClaudeFacade):
        def _refresh(self, idx, until=None):
            if getattr(idx, '_stored_loaded', False):
                return
            sid = idx.execution.removeprefix('cc-')
            backend = SqliteRows(store, sid)
            state = backend.state()
            newest = store.db.execute('SELECT COALESCE(MAX(seq),0) FROM events WHERE sid=?', (sid,)).fetchone()[0]
            if state is None or state['status'] != 'complete' or state['built_through'] != newest:
                raise ValueError('stored row set is absent, building or behind its log')
            idx.epoch = state['epoch']
            idx.cursor = backend.built_through
            idx.revision = newest
            idx.rows = [SqliteRows.decode(r[0], store, sid) for r in store.db.execute(
                'SELECT body FROM rows WHERE sid=? ORDER BY ordinal', (sid,))]
            idx.by_origin = {row['origin']: row for row in idx.rows}
            idx.diagnostics = [json.loads(r[0]) for r in store.db.execute(
                "SELECT body FROM row_diagnostics WHERE sid=? ORDER BY json_extract(body,'$.revision'),scope,n", (sid,))]
            idx.incidents = [json.loads(r[0]) for r in store.db.execute(
                'SELECT body FROM row_incidents WHERE sid=? ORDER BY n', (sid,))]
            idx.content_witnesses = {tuple(json.loads(r[0])) for r in store.db.execute(
                "SELECT k FROM row_keys WHERE sid=? AND kind='witness'", (sid,))}
            idx.agent_tasks, idx.child_tasks, idx.agent_info = backend.agent_tasks, backend.child_tasks, backend.agent_info
            idx.status = 'complete'
            idx._stored_loaded = True

    return StoredFacade(history, StoreClient(store), **kwargs)


def session_ids(store, selection):
    all_sids = {row[0] for row in store.db.execute('SELECT sid FROM sessions UNION SELECT sid FROM events')}
    sids = all_sids if selection == 'all' else set(selection.split(','))
    if not sids <= all_sids:
        raise ValueError('requested session absent from snapshot')
    return sorted(sids)


# @beacon[
#   id=opc@claude_rows_compare.write_dump,
#   slice_labels=opc-rows,
#   kind=ast,
#   comment=Walks all public scope pages and context through one replay or stored-source facade per session using the caller's existing read snapshot; records failures without publishing previews.,
# ]
def write_dump(reader, live, store, sids, source, out, unreadable=None):
    """Render one source using an already-open snapshot; never open another connection."""
    failures = 0
    previous_budget = live.DRAIN_BUDGET_SECONDS
    try:
        # Proof only: no session may be cut by a live viewer request's observer budget.
        live.DRAIN_BUDGET_SECONDS = 24 * 60 * 60
        total_rows, scopes = 0, 0
        out = Path(out)
        with out.open('w', encoding='utf-8') as output:
            out.chmod(0o600)
            for sid in sids:
                try:
                    state = store.get('sessions', sid)
                    if not state:
                        raise ValueError('events have no session record')
                    history = reader.ClaudeReader([])
                    history.host_turn_ledger = lambda *a, **kw: None
                    options = {'summary_limit': 2**60}
                    facade = (stored_facade(live, history, store, **options) if source == 'rows' else
                              live.ClaudeFacade(history, StoreClient(store), **options))
                    facade._deadline = float('inf')  # offline, not a live observer request
                    records = session_dump(facade, 'cc-' + sid, state['generation'])
                    count = sum(len(record['rows']) for record in records)
                    for record in records:
                        output.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n')
                    total_rows += count
                    scopes += len(records)
                    print(f'{sid}: scopes={len(records)} rows={count}', flush=True)
                except Exception as error:
                    failures += 1
                    # Name the failed session, not its private raw bytes or preview values.
                    output.write(json.dumps({'sid': sid, 'scope': 'main', 'error': type(error).__name__}) + '\n')
                    if unreadable is not None:
                        unreadable[sid] = source + '-unreadable:' + type(error).__name__
                        print(f'{sid}: UNREADABLE {type(error).__name__}', file=sys.stderr, flush=True)
                    else:
                        print(f'{sid}: FAILED {type(error).__name__}: {error}', file=sys.stderr, flush=True)
        print(f'source={source} sessions={len(sids)} scopes={scopes} rows={total_rows} failures={failures}')
        return int(bool(failures))
    finally:
        live.DRAIN_BUDGET_SECONDS = previous_budget


def dump(args):
    directory = checked_store_dir(args.store)  # before importing or opening any Store
    tree = args.reference_tree or Path(__file__).resolve().parents[1]
    reader, live, storage = load_tree(tree)
    store = open_copy(storage.Store, directory, Path(args.raw_root).expanduser().resolve())
    try:
        return write_dump(reader, live, store, session_ids(store, args.sessions), args.source, args.out)
    finally:
        store.db.rollback()
        store.db.close()


def rows_eligibility(store, sid):
    """Name sessions the pane cannot serve from rows in THIS snapshot, including pruned raw."""
    if not store.get('sessions', sid):
        return 'session-record-unavailable'
    # Check indexed stdout extents read-only, including unfinished sets. A pruned file is
    # reported as unreadable rather than disguising it as a still-running backfill.
    for generation, extent in store.db.execute(
            "SELECT generation,MAX(offset+length) FROM events WHERE sid=? "
            "AND kind='native_frame' AND stream='stdout' GROUP BY generation", (sid,)):
        try:
            with store.path(sid, generation, 'stdout').open('rb') as raw:
                if extent is not None and os.fstat(raw.fileno()).st_size < extent:
                    return 'raw-unreadable:short-stdout'
        except OSError as error:
            return 'raw-unreadable:' + type(error).__name__
    row = store.db.execute('SELECT body FROM row_sets WHERE sid=?', (sid,)).fetchone()
    if row is None:
        return 'rows-absent'
    try:
        state = json.loads(row[0])
        status, built = state['status'], state['built_through']
    except (ValueError, KeyError, TypeError):
        return 'rows-state-unreadable'
    if status != 'complete':
        return 'rows-' + str(status)
    newest = store.db.execute('SELECT COALESCE(MAX(seq),0) FROM events WHERE sid=?', (sid,)).fetchone()[0]
    if built != newest:
        return 'rows-behind'
    return None


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def omit_unreadable(path, unreadable):
    """Remove the same explicitly named sessions from BOTH private dumps, never mismatched values."""
    filtered = path.with_suffix('.filtered')
    with path.open(encoding='utf-8') as source, filtered.open('w', encoding='utf-8') as output:
        filtered.chmod(0o600)
        for line in source:
            if json.loads(line)['sid'] not in unreadable:
                output.write(line)
    filtered.replace(path)


# @beacon[
#   id=opc@claude_rows_compare.live_read_only,
#   slice_labels=opc-rows,
#   kind=ast,
#   comment=Activation proof in one mode=ro snapshot without Store startup: names exclusions and returns exit 3 for render/state/absent/behind failures health cannot name unless comparison already failed, then rolls back.,
# ]
def live_read_only(args):
    """Read-only activation snapshot; unnamed render/row-state exclusions fail with exit 3."""
    raw_root = Path(args.raw_root).expanduser().resolve()
    directory = Path(args.store).expanduser().resolve() if args.store else raw_root
    scratch = Path('/tmp/opencode').resolve()
    if args.out_dir:
        out_dir = Path(args.out_dir).expanduser().resolve()
        if not out_dir.is_relative_to(scratch):
            raise ValueError('--out-dir must be private scratch under /tmp/opencode/')
        out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    else:
        out_dir = Path(tempfile.mkdtemp(prefix='claude-rows-activation-', dir=scratch))
    reader, live, storage = load_tree(Path(__file__).resolve().parents[1])
    # open_copy bypasses Store.__init__, opens mode=ro and begins BEFORE its first SELECT.
    # Both sources get this same Store-shaped read adapter and connection.
    store = open_copy(storage.Store, directory, raw_root)
    try:
        sids = session_ids(store, args.sessions)
        included, excluded = [], []
        for sid in sids:
            reason = rows_eligibility(store, sid)
            if reason is None:
                included.append(sid)
            else:
                excluded.append({'sid': sid, 'reason': reason})
                print(f'EXCLUDED {sid}: {reason}', flush=True)
        replay, rows = out_dir / 'replay.jsonl', out_dir / 'rows.jsonl'
        unreadable = {}
        write_dump(reader, live, store, included, 'replay', replay, unreadable)
        write_dump(reader, live, store, included, 'rows', rows, unreadable)
        if unreadable:
            for sid, reason in sorted(unreadable.items()):
                excluded.append({'sid': sid, 'reason': reason})
                print(f'EXCLUDED {sid}: {reason}', flush=True)
            included = [sid for sid in included if sid not in unreadable]
            for path in (replay, rows):
                omit_unreadable(path, unreadable)
        result = compare(replay, rows) if included else 1
        unnamed = [item for item in excluded if
            item['reason'].startswith(('replay-unreadable:', 'rows-unreadable:')) or
            item['reason'] in ('rows-state-unreadable', 'rows-behind', 'rows-absent')]
        if unnamed:
            print('FAILED: exclusions health does not name: ' + json.dumps(unnamed, sort_keys=True), flush=True)
            if result == 0:
                result = 3
        report = {'requested_sessions': len(sids), 'compared_sessions': len(included),
                  'excluded': excluded, 'replay_sha256': file_sha256(replay),
                  'rows_sha256': file_sha256(rows), 'exit': result}
        report_path = out_dir / 'snapshot-report.json'
        with report_path.open('w', encoding='utf-8') as output:
            report_path.chmod(0o600)
            json.dump(report, output, indent=2)
            output.write('\n')
        print('SNAPSHOT ' + json.dumps(report, sort_keys=True), flush=True)
        print(f'private outputs: {out_dir}', flush=True)
        return result
    finally:
        store.db.rollback()
        store.db.close()


def differences(a, b, path=''):
    if type(a) is not type(b):
        yield path
    elif isinstance(a, dict):
        for key in sorted(a.keys() | b.keys()):
            child = f'{path}/{key}'
            if key not in a or key not in b:
                yield child
            else:
                yield from differences(a[key], b[key], child)
    elif isinstance(a, list):
        if len(a) != len(b):
            yield f'{path}/length'
        for n, (left, right) in enumerate(zip(a, b)):
            yield from differences(left, right, f'{path}/{n}')
    elif a != b:
        yield path


def compare(left, right):
    def records(path):
        result = {}
        with Path(path).open(encoding='utf-8') as source:
            for line in source:
                record = json.loads(line)
                key = (record['sid'], record['scope'])
                if key in result or 'error' in record:
                    raise ValueError('duplicate or failed session/scope in dump')
                result[key] = record
        return result
    a, b = records(left), records(right)
    mismatches = rows = 0
    for sid, scope in sorted(a.keys() | b.keys()):
        key = (sid, scope)
        row_count = len(a.get(key, {}).get('rows', []))
        print(f'{sid}/{scope}: rows={row_count}/{len(b.get(key, {}).get("rows", []))}')
        rows += row_count
        for path in differences(a.get(key), b.get(key), f'{sid}/{scope}'):
            if mismatches < 20:
                print('MISMATCH ' + path)  # paths only; never publish private values
            mismatches += 1
    print(f'sessions={len({sid for sid, scope in a.keys() | b.keys()})} '
          f'scopes={len(a.keys() | b.keys())} rows={rows} mismatches={mismatches}')
    return int(bool(mismatches))


# @beacon[
#   id=opc@claude_rows_compare.main,
#   slice_labels=opc-rows,
#   kind=ast,
#   comment=Copy proofs and the single-snapshot read-only activation check; private JSONL dumps compare every scope and main-page agent placement without publishing bodies.,
# ]
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-read-only', action='store_true')
    parser.add_argument('--store', help='activation snapshot DB directory; defaults to --raw-root')
    parser.add_argument('--raw-root', help='activation snapshot raw state directory')
    parser.add_argument('--sessions', default='all')
    parser.add_argument('--out-dir', help='activation dumps/report under /tmp/opencode/ (private)')
    commands = parser.add_subparsers(dest='command')
    proof = commands.add_parser('dump')
    proof.add_argument('--store', required=True)
    proof.add_argument('--raw-root', required=True)
    proof.add_argument('--source', choices=['replay', 'rows'], required=True)
    proof.add_argument('--out', required=True)
    proof.add_argument('--sessions', default='all')
    proof.add_argument('--reference-tree', type=Path)
    equal = commands.add_parser('compare')
    equal.add_argument('a')
    equal.add_argument('b')
    args = parser.parse_args(argv)
    if args.live_read_only:
        if args.command or not args.raw_root:
            parser.error('--live-read-only needs --raw-root and no subcommand')
    elif args.command is None or args.out_dir:
        parser.error('choose dump, compare, or --live-read-only')
    try:
        if args.live_read_only:
            return live_read_only(args)
        return dump(args) if args.command == 'dump' else compare(args.a, args.b)
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f'row proof failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
