"""Append-only bytes + FULL-synchronous SQLite index/projections, private to the host."""
import base64
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import uuid


FORGET_TABLES = ('events', 'receipts', 'permissions', 'controls', 'incidents', 'recoveries', 'results',
                 'rows', 'row_parts', 'row_keys', 'row_diagnostics', 'row_incidents', 'row_sets', 'stream_ends', 'task_inputs')
JUNK_TABLES = ('row_sets', 'rows', 'row_parts', 'row_keys', 'row_diagnostics', 'row_incidents')


def _task_run_key(task, run):
    """An index key, not the proof: preserve Python equality of JSON numbers (True == 1 == 1.0)."""
    def normalize(value):
        if isinstance(value, bool) or isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, list):
            return [normalize(v) for v in value]
        if isinstance(value, dict):
            return {k: normalize(v) for k, v in value.items()}
        return value
    return json.dumps(normalize([task, run]), sort_keys=True)


def _task_tool_key(tool):
    """Keep ordinary text keys; JSON-escaped lone surrogates need SQLite's binary binding instead."""
    try:
        tool.encode('utf-8')
    except UnicodeEncodeError:
        return tool.encode('utf-8', 'surrogatepass')
    return tool


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def private_dir(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or path.stat().st_uid != os.getuid():
        raise ValueError('state directory must be owned, not a symlink')
    path.chmod(0o700)
    sync_directory(path)
    sync_directory(path.parent)
    return path


def private_file(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    sync_directory(Path(path).parent)


# @beacon[
#   id=opc@RowSetCounters,
#   slice_labels=opc-rows,
#   kind=ast,
#   comment=Committed in-memory row-set metadata and exact event debt: one initial pending-range recount, then only changed sets and newly appended or projected index ranges; Store.batch publishes after commit and discards rollback touches, including bounded forget deletions.,
# ]
class RowSetCounters:
    """Small committed projections, never row-set bodies or history work on a health read.

    Each entry is (status, built_through, pending event count, event head). Global seqs
    bound index ranges; their difference is never used as an event count. Only the
    initial load (or a newly created set) counts its whole unprojected suffix.
    """
    def __init__(self, store):
        self.store = store
        self.states, self.built_through, self.unreadable = {}, {}, {}
        self.remaining_sessions = self.behind_sets = self.behind_events = 0
        for sid, body in store.db.execute("SELECT sid, body FROM row_sets WHERE sid!=''"):
            state = json.loads(body)
            built = state['built_through']
            head = self.head(sid)
            self.replace(sid, (state['status'], built, self.count(sid, built), head))

    def head(self, sid):
        return self.store.db.execute('SELECT COALESCE(MAX(seq),0) FROM events WHERE sid=?', (sid,)).fetchone()[0]

    def count(self, sid, after, through=None):
        sql, args = 'SELECT COUNT(*) FROM events WHERE sid=? AND seq>?', (sid, after)
        if through is not None:
            sql, args = sql + ' AND seq<=?', (*args, through)
        return self.store.db.execute(sql, args).fetchone()[0]

    def replace(self, sid, entry):
        previous = self.states.pop(sid, None)
        for value, sign in ((previous, -1), (entry, 1)):
            if value is not None:
                status, _, pending, _ = value
                behind = status in ('queued', 'running')
                self.remaining_sessions += sign * (status != 'complete')
                self.behind_sets += sign * behind
                self.behind_events += sign * pending * behind
        execution = 'cc-' + sid
        self.built_through.pop(execution, None)
        self.unreadable.pop(execution, None)
        if entry is not None:
            self.states[sid] = entry
            self.built_through[execution] = entry[1]
            if entry[0] == 'unreadable':
                self.unreadable[execution] = None

    def update(self, sids, deleted):
        """Read each touched set's final SQL state, so projection SAVEPOINT rollbacks count too."""
        for sid in sids:
            row = self.store.db.execute('SELECT body FROM row_sets WHERE sid=?', (sid,)).fetchone()
            if row is None:
                self.replace(sid, None)
                continue
            state = json.loads(row[0])
            built, previous = state['built_through'], self.states.get(sid)
            head = self.head(sid)
            if previous is None:
                pending = self.count(sid, built)
            else:
                _, old_built, pending, old_head = previous
                # A native read adds only its new range. A backfill page consumes only
                # its bounded prefix, never COUNTs the still-large suffix every page.
                if head > max(old_head, old_built):
                    pending += self.count(sid, max(old_head, old_built), head)
                pending -= deleted.get(sid, 0)
                if built > old_built:
                    pending -= self.count(sid, old_built, built)
                elif built < old_built:
                    pending += self.count(sid, built, old_built)
            self.replace(sid, (state['status'], built, pending, head))


# @beacon[
#   id=opc@ClaudeEventStore,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Private append-only native bytes with durable event cursors and exact send receipts; stream ends and task-input indexes commit with their events after raw fsync; old task generations back-fill only on lookup; independent alarms and observations retain exact obligations without session holds; batch() commits each native read once and only then updates session-record and row-health caches.,
# ]
class Store:
    def __init__(self, root):
        opened_at = time.monotonic()
        self.root = private_dir(root)
        self.db = sqlite3.connect(self.root / 'host.sqlite3')
        (self.root / 'host.sqlite3').chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        indexed = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='events_by_session'").fetchone()
        started = time.monotonic()
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS sessions(sid TEXT PRIMARY KEY, record TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS receipts(sid TEXT, mid TEXT, body TEXT NOT NULL,
          PRIMARY KEY(sid,mid));
        CREATE TABLE IF NOT EXISTS permissions(sid TEXT, generation INTEGER, rid TEXT,
          body TEXT NOT NULL, PRIMARY KEY(sid,generation,rid));
        CREATE TABLE IF NOT EXISTS controls(sid TEXT, rid TEXT, body TEXT NOT NULL,
          PRIMARY KEY(sid,rid));
        CREATE TABLE IF NOT EXISTS incidents(sid TEXT, rid TEXT, body TEXT NOT NULL,
          PRIMARY KEY(sid,rid));
        CREATE TABLE IF NOT EXISTS recoveries(sid TEXT, rid TEXT, body TEXT NOT NULL,
          PRIMARY KEY(sid,rid));
        CREATE TABLE IF NOT EXISTS quota_checks(sid TEXT, rid TEXT, body TEXT NOT NULL,
          PRIMARY KEY(sid,rid));
        CREATE TABLE IF NOT EXISTS observations(sid TEXT, rid TEXT, body TEXT NOT NULL,
          PRIMARY KEY(sid,rid));
        CREATE TABLE IF NOT EXISTS observer_recoveries(sid TEXT, rid TEXT, body TEXT NOT NULL,
          PRIMARY KEY(sid,rid));
        CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
          sid TEXT, generation INTEGER, time REAL, kind TEXT, detail TEXT,
          stream TEXT, offset INTEGER, length INTEGER);
        -- Every matching frame survives; coverage is one sentinel row, not a negative cache with an expiry.
        CREATE TABLE IF NOT EXISTS task_inputs(sid TEXT NOT NULL, generation INTEGER NOT NULL,
          kind TEXT NOT NULL, lookup_key TEXT NOT NULL, seq INTEGER NOT NULL,
          PRIMARY KEY(sid,generation,kind,lookup_key,seq));
        -- An older writer (or any append without parsed input indexing) cannot leave false complete coverage.
        -- The current writer restores the sentinel only after indexing the frame, in that same batch.
        CREATE TRIGGER IF NOT EXISTS task_inputs_uncovered AFTER INSERT ON events
          WHEN NEW.kind='native_frame' AND NEW.stream='stdout'
          BEGIN
            DELETE FROM task_inputs WHERE sid=NEW.sid AND generation=NEW.generation AND kind='coverage';
          END;
        -- One row per native result, written once: its identity (duplicate detection) and, for a background
        -- result (no input, so no receipt holds it), its summary. Never part of the session record.
        CREATE TABLE IF NOT EXISTS results(sid TEXT, identity TEXT, seq INTEGER, background INTEGER, body TEXT,
          PRIMARY KEY(sid,identity));
        CREATE INDEX IF NOT EXISTS results_by_seq ON results(sid, background, seq);
        -- One session's events without walking every session's (startup gap checks, replay/incidents by session).
        CREATE INDEX IF NOT EXISTS events_by_session ON events(sid);
        CREATE TABLE IF NOT EXISTS stream_ends(sid TEXT, generation INTEGER, stream TEXT, end INTEGER NOT NULL,
          PRIMARY KEY(sid, generation, stream));
        CREATE TABLE IF NOT EXISTS rows(sid TEXT, scope TEXT, seq INTEGER, ordinal INTEGER,
          origin TEXT, generation INTEGER, kind TEXT, uuid TEXT, changed INTEGER, body_changed INTEGER,
          body TEXT NOT NULL, PRIMARY KEY(sid, scope, seq));
        CREATE INDEX IF NOT EXISTS rows_by_origin ON rows(sid, origin);
        CREATE INDEX IF NOT EXISTS rows_by_changed ON rows(sid, scope, changed);
        CREATE INDEX IF NOT EXISTS rows_by_ordinal ON rows(sid, ordinal);
        CREATE TABLE IF NOT EXISTS row_parts(sid TEXT, origin TEXT, part INTEGER, body TEXT NOT NULL,
          PRIMARY KEY(sid, origin, part));
        CREATE TABLE IF NOT EXISTS row_keys(sid TEXT, kind TEXT, k TEXT, body TEXT NOT NULL,
          PRIMARY KEY(sid, kind, k));
        CREATE TABLE IF NOT EXISTS row_diagnostics(sid TEXT, scope TEXT, n INTEGER, body TEXT NOT NULL,
          PRIMARY KEY(sid, scope, n));
        CREATE TABLE IF NOT EXISTS row_incidents(sid TEXT, n INTEGER, body TEXT NOT NULL, PRIMARY KEY(sid, n));
        CREATE TABLE IF NOT EXISTS row_sets(sid TEXT PRIMARY KEY, body TEXT NOT NULL);
        ''')
        indexing = None if indexed else round(time.monotonic() - started, 3)
        if indexing is not None and not self.db.execute('SELECT EXISTS(SELECT 1 FROM events)').fetchone()[0]:
            indexing = None  # a new, empty store: nothing was migrated
        self.db.execute('INSERT OR IGNORE INTO meta VALUES (?,?)', ('identity', uuid.uuid4().hex))
        self.db.commit()
        self.identity = self.db.execute("SELECT value FROM meta WHERE key='identity'").fetchone()[0]
        self.depth = 0          # open batch() levels; writes commit only when the outermost one closes
        self.dirty = {}         # sid -> session record object written at that commit (once per batch)
        self.batch_high = 0     # highest event seq handed out in the open transaction
        self.seq_floor = 0      # after a rolled-back batch: no later event reuses a seq it handed out
        self.synced = set()     # raw files whose directory entry this process has fsynced
        self.rows_writer = None  # the host installs its synchronous append hook, never a second connection
        self._rows_counters = None
        self.rows_changed, self.rows_deleted = set(), {}  # touched sids / pending events deleted in this batch
        # sid -> (record, its committed JSON) in table order: the session records as the table holds them, filled by
        # the first cached_sessions() (the host's, at open) and changed only when a record write commits. None = not
        # filled yet, or dropped by a failed write or a rollback (the next read decodes the table again).
        self.session_cache = None
        migrate_at = time.monotonic()
        self.migrate(indexing)
        migrate_ms = (time.monotonic() - migrate_at) * 1000
        self.row_counters()  # one committed metadata/pending-range recount after startup requeue
        self.startup_timings = {'store_open_ms': round((time.monotonic() - opened_at) * 1000 - migrate_ms, 3),
                                'migrate_ms': round(migrate_ms, 3)}

    def row_counters(self):
        """The committed row-health projection, filled at open or after a failed cache update."""
        if self._rows_counters is None:
            self._rows_counters = RowSetCounters(self)
        return self._rows_counters

    # @beacon[
    #   id=opc@Store.migrate,
    #   slice_labels=opc-basic,
    #   kind=ast,
    #   comment=One-time store upgrade on open, journaled in meta (migration_unit2) and the service log: the events_by_session index and old records' result copies moved into results rows; a record it cannot read is left as it is, never a failed start.,
    # ]
    def migrate(self, indexing):
        """One-time store upgrades, journaled in ``meta`` and the service log: the events index (built on first open)
        and the result copies a session record used to carry (``result_identities``, ``synthetic_results``), moved
        into their own rows. On a copy of the live store (787K events, 72 records): about 0.6 s for the index, 16 ms
        for the result copies; a second open finds nothing to do."""
        self.migrate_rows()
        started, moved = time.monotonic(), 0
        rows = self.db.execute("SELECT sid, record FROM sessions WHERE record LIKE '%\"result_identities\"%' "
                               "OR record LIKE '%\"synthetic_results\"%'").fetchall()
        for sid, text in rows:
            try:
                record = json.loads(text)
                if not isinstance(record, dict) or not {'result_identities', 'synthetic_results'} & set(record):
                    continue
                for identity in record.pop('result_identities', None) or []:
                    self.db.execute('INSERT OR IGNORE INTO results VALUES (?,?,?,?,?)',
                                    (sid, str(identity), None, 0, None))
                for summary in record.pop('synthetic_results', None) or []:
                    cursor = str(summary.get('cursor') or '')
                    tail = cursor.rsplit(':', 1)[-1]
                    self.db.execute('INSERT OR IGNORE INTO results VALUES (?,?,?,?,?)',
                                    (sid, 'legacy-background:' + cursor, int(tail) if tail.isdigit() else None, 1,
                                     json.dumps(summary, ensure_ascii=False)))
            except (ValueError, TypeError, AttributeError):
                continue  # a record this cannot read stays as it is (reconstruction marks it); never a failed start
            self.db.execute('UPDATE sessions SET record=? WHERE sid=?', (json.dumps(record, ensure_ascii=False), sid))
            moved += 1
        if not moved and indexing is None:
            return
        done = {'at': time.time(), 'events_index_seconds': indexing, 'result_copies_moved_from_records': moved,
                'result_copies_seconds': round(time.monotonic() - started, 3)}
        self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('migration_unit2', json.dumps(done)))
        self.db.commit()
        print(f'claude host store: one-time migration {json.dumps(done)}', file=sys.stderr, flush=True)

    # @beacon[
    #   id=opc@Store.migrate_rows,
    #   slice_labels=opc-rows,opc-basic,
    #   kind=ast,
    #   comment=Startup metadata only: one indexed head per known sid requeues rows and isolates unusable sets; a primary-key event watermark detects older writers and invalidates only the derived stream-end cache before its lazy per-stream seed.,
    # ]
    def migrate_rows(self):
        """Requeue known logs without a history scan; distrust stream ends after an older writer.

        Every event from this version advances stream_ends_through in its own transaction. A larger
        event head therefore proves a writer that did not maintain the cache. Drop only those derived
        ends, never evidence: index_raw_gaps will seed each missing stream once. Deletion can lower the
        event head, so a watermark beyond it is still valid (SQLite never reuses committed event seqs).
        """
        from .rows import SqliteRows
        started = time.monotonic()
        registered = self.db.execute("SELECT value FROM meta WHERE key='row_schema'").fetchone()
        with self.batch():
            newest = self.db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
            through = self.db.execute("SELECT value FROM meta WHERE key='stream_ends_through'").fetchone()
            if through is None or not str(through[0]).isdigit() or int(through[0]) < newest:
                self.db.execute('DELETE FROM stream_ends')
                self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('stream_ends_through', str(newest)))
            queued = SqliteRows.requeue(self)
            if registered is None:
                done = {'at': time.time(), 'queued_sessions': queued,
                        'seed_seconds': round(time.monotonic() - started, 6)}
                self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('row_schema', '1'))
                self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('migration_rows', json.dumps(done)))
        if registered is None:
            print(f'claude host store: rows migration {json.dumps(done)}', file=sys.stderr, flush=True)

    # @beacon[
    #   id=opc@Store.batch,
    #   slice_labels=opc-basic,
    #   kind=ast,
    #   comment=One SQLite transaction per native read or writer step: events, receipts, projections and ONE record write per session; only a successful outer commit publishes session-record and row-health caches, rollback discards row touches and drops the record cache, and no later event reuses a rolled-back cursor.,
    # ]
    @contextmanager
    def batch(self):
        """Everything written inside is one transaction, committed when the outermost batch closes.

        Raw bytes are appended (and fsynced) by the caller before the index rows that point at them, so the
        ordering guarantee is unchanged; a crash before the commit leaves retained bytes that startup indexes as a
        ``raw_reconciliation_gap``. Session records are written once each, at the commit (``put('sessions')``
        inside a batch keeps the latest object), and enter the record cache (:meth:`cached_sessions`) only after it.
        Any exception rolls the whole transaction back and re-raises: the caller declares the durability failure,
        and the bytes of that read become a gap once storage works again. The rollback also drops the record cache,
        so the next read decodes the table as it now is. Cursors handed out inside a rolled-back batch are never
        reused (``seq_floor``). Row-health touches publish only after this commit; a
        rollback leaves their previously committed counters/maps untouched."""
        if self.depth:
            self.depth += 1
            try:
                yield
            finally:
                self.depth -= 1
            return
        self.depth = 1
        written = []
        try:
            yield
            for sid, record in self.dirty.items():
                text = json.dumps(record, ensure_ascii=False)
                self.db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?)', (sid, text))
                written.append((sid, text))
            self.db.commit()
        except BaseException:
            self.seq_floor = max(self.seq_floor, self.batch_high)
            self.session_cache = None
            try:
                self.db.rollback()
            except sqlite3.Error:
                pass
            raise
        else:
            self.cache_sessions(written)
            if self._rows_counters is not None:
                try:
                    self._rows_counters.update(self.rows_changed, self.rows_deleted)
                except Exception:
                    self._rows_counters = None  # committed SQL is truth; recount on the next read
        finally:
            self.depth, self.batch_high = 0, 0
            self.dirty.clear()
            self.rows_changed.clear()
            self.rows_deleted.clear()

    def commit(self):
        if not self.depth:
            self.db.commit()

    # @beacon[
    #   id=opc@Store.forget_batch,
    #   slice_labels=opc-basic,opc-rows,
    #   kind=ast,
    #   comment=Delete one exact sid with indexed 64-rowid steps and rowid-only outer DELETEs, at most 2000 rows and a 2 ms work budget per committed batch; bounded deleted-event witnesses keep row-health debt exact until row_sets itself is forgotten, then the worker yields.,
    # ]
    def forget_batch(self, table, sid, limit=2000):
        """One committed deletion slice; the caller yields before asking for another.

        Small indexed rowid steps with a 2 ms work budget leave room for FULL-sync commit inside the 20 ms
        slice target. Return the actual transaction time too, including that commit;
        the worker reduces its next row budget after a slow slice. No replay, second
        connection, history recounts, or transaction spanning an await.

        Keep sid equality in the LIMIT subquery, NOT redundantly on the outer
        DELETE: with a composite sid index SQLite otherwise scans all 13.48 M
        empty-sid diagnostics even when deleting a single selected rowid.
        """
        if table not in FORGET_TABLES:
            raise ValueError('unsupported forget table')
        if self.depth:
            raise RuntimeError('forget requires its own Store.batch')
        limit = min(max(int(limit), 1), 2000)
        started, deleted = time.monotonic(), 0
        with self.batch():
            while deleted < limit:
                step = min(64, limit - deleted)
                if sid and table == 'events' and self._rows_counters is not None:
                    previous = self._rows_counters.states.get(sid)
                    if previous is not None:
                        removed = self.db.execute('SELECT seq FROM events WHERE rowid IN '
                            '(SELECT rowid FROM events WHERE sid=? LIMIT ?)', (sid, step)).fetchall()
                        self.rows_deleted[sid] = self.rows_deleted.get(sid, 0) + sum(r[0] > previous[1] for r in removed)
                changed = self.db.execute(f'DELETE FROM {table} WHERE rowid IN '
                    f'(SELECT rowid FROM {table} WHERE sid=? LIMIT ?)', (sid, step)).rowcount
                if sid and table in ('events', 'row_sets') and changed:
                    self.rows_changed.add(sid)
                deleted += changed
                if changed < step or time.monotonic() - started >= .002:
                    break
        return deleted, (time.monotonic() - started) * 1000

    def get(self, table, sid, key=None):
        if table == 'sessions':
            if sid in self.dirty:
                return json.loads(json.dumps(self.dirty[sid], ensure_ascii=False))
            row = self.db.execute('SELECT record FROM sessions WHERE sid=?', (sid,)).fetchone()
        else:
            column = 'mid' if table == 'receipts' else 'rid'
            row = self.db.execute(f'SELECT body FROM {table} WHERE sid=? AND {column}=?',
                                  (sid, key)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, table, sid, body, key=None):
        if table == 'sessions' and self.depth:
            self.dirty[sid] = body  # written once, at the batch's commit
            return
        data = json.dumps(body, ensure_ascii=False)
        if table != 'sessions':
            self.db.execute(f'INSERT OR REPLACE INTO {table} VALUES (?,?,?)', (sid, key, data))
            self.commit()
            return
        try:
            self.db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?)', (sid, data))
            self.commit()
        except BaseException:
            self.session_cache = None  # what the table holds now is unknown here: the next read decodes it again
            raise
        self.cache_sessions([(sid, data)])

    def sessions(self):
        records = {row[0]: row[1] for row in self.db.execute('SELECT sid, record FROM sessions')}
        records.update({sid: json.dumps(record, ensure_ascii=False) for sid, record in self.dirty.items()})
        return [json.loads(text) for text in records.values()]

    # @beacon[
    #   id=opc@Store.cached_sessions,
    #   slice_labels=opc-basic,
    #   kind=ast,
    #   comment=The session records from memory (list, the orphan recheck, the five-hour notice's Manager): what sessions() would decode, in its order with the open batch overlaid, filled once and changed only by committed record writes; choose with the cached record, hand out Store.fresh copies.,
    # ]
    def cached_sessions(self):
        """Every session record as ``(sid, record, text)``: what :meth:`sessions` would decode, in the same order and
        with the open batch's records overlaid, from memory instead of decoding the whole table.

        ``record`` is the cache's own object (mid-batch, the writer's): choose with it, never change it or hand it
        out; ``Store.fresh(record, text)`` is the copy to hand out (``text``, its committed JSON, is None for a record
        the open batch has not written yet). The first call fills the cache (the host's, at open); after that only a
        committed record write changes it (:meth:`put`, :meth:`batch`), and a failed write or a rollback drops it so
        the next call decodes the table as it now is."""
        if self.session_cache is None:
            self.session_cache = {sid: (json.loads(text), text)
                                  for sid, text in self.db.execute('SELECT sid, record FROM sessions')}
        if not self.dirty:
            return [(sid, record, text) for sid, (record, text) in self.session_cache.items()]
        entries = dict(self.session_cache)
        entries.update({sid: (record, None) for sid, record in self.dirty.items()})
        return [(sid, record, text) for sid, (record, text) in entries.items()]

    @staticmethod
    def fresh(record, text=None):
        """A fresh deep copy of a record from :meth:`cached_sessions`: its committed JSON decoded again (a record the
        open batch holds: a JSON round trip, as :meth:`get` makes)."""
        return json.loads(text if text is not None else json.dumps(record, ensure_ascii=False))

    def cache_sessions(self, written):
        """Committed record writes enter the cache in commit order: INSERT OR REPLACE moves a row to the table's end,
        and so does this. Anything unexpected drops the cache rather than leave it unlike the table."""
        if self.session_cache is None:
            return
        try:
            for sid, text in written:
                self.session_cache.pop(sid, None)
                self.session_cache[sid] = (json.loads(text), text)
        except Exception:
            self.session_cache = None

    def result_seen(self, sid, identity):
        return self.db.execute('SELECT 1 FROM results WHERE sid=? AND identity=?', (sid, identity)).fetchone() is not None

    def put_result(self, sid, identity, cursor, background, summary=None):
        """Record one native result once: its identity first, then (a second call) the summary of a background
        result, which no receipt holds; a user-origin result's summary lives on its input's receipt."""
        body = json.dumps(summary, ensure_ascii=False) if background and summary is not None else None
        self.db.execute('INSERT INTO results VALUES (?,?,?,?,?) ON CONFLICT(sid, identity) DO UPDATE SET '
                        'body=excluded.body WHERE excluded.body IS NOT NULL',
                        (sid, identity, int(cursor.rsplit(':', 1)[1]), 1 if background else 0, body))
        self.commit()

    def last_background_result(self, sid):
        """The newest background result's summary (a wake-up turn's last word), or None."""
        row = self.db.execute('SELECT body FROM results WHERE sid=? AND background=1 AND body IS NOT NULL '
                              'ORDER BY seq DESC LIMIT 1', (sid,)).fetchone()
        return json.loads(row[0]) if row else None

    def receipts(self, sid):
        return [json.loads(row[0]) for row in self.db.execute('SELECT body FROM receipts WHERE sid=?', (sid,))]

    def records(self, table, sid=None):
        if table not in ('incidents', 'recoveries', 'controls', 'quota_checks', 'observations', 'observer_recoveries'):
            raise ValueError('unsupported projection table')
        sql, args = f'SELECT body FROM {table}', ()
        if sid is not None:
            sql, args = sql + ' WHERE sid=?', (sid,)
        return [json.loads(row[0]) for row in self.db.execute(sql, args)]

    def incidents(self, cursor=None, sid=None, limit=100):
        # The ONE feed uses ordinary durable cursors and therefore composes with replay/raw.
        seq = self.parse_cursor(cursor)
        limit = min(max(int(limit), 1), 500)
        sql, args = "SELECT * FROM events WHERE seq>? AND kind='incident'", [seq]
        if sid is not None:
            sql, args = sql + ' AND sid=?', args + [sid]
        rows = self.db.execute(sql + ' ORDER BY seq LIMIT ?', (*args, limit + 1)).fetchall()
        items = [{**json.loads(row['detail']), 'cursor': self.cursor(row['seq'])} for row in rows[:limit]]
        return {'incidents': items, 'cursor': items[-1]['cursor'] if items else self.cursor(seq),
                'has_more': len(rows) > limit, 'gap': False}

    def permission(self, sid, generation, rid, body=None):
        if body is not None:
            self.db.execute('INSERT INTO permissions VALUES (?,?,?,?)',
                            (sid, generation, rid, json.dumps(body, ensure_ascii=False)))
            self.commit()
        row = self.db.execute('SELECT body FROM permissions WHERE sid=? AND generation=? AND rid=?',
                              (sid, generation, rid)).fetchone()
        if row is None and body is None:
            # The same native request ID redelivered after resume gets its original
            # decision, not a second permission mutation in a new generation.
            row = self.db.execute('SELECT body FROM permissions WHERE sid=? AND rid=? '
                                  'ORDER BY generation DESC LIMIT 1', (sid, rid)).fetchone()
        return json.loads(row[0]) if row else None

    def path(self, sid, generation, stream):
        if stream not in ('stdout', 'stderr', 'stdin'):
            raise ValueError('invalid raw stream')
        return self.root / sid / f'g{generation:06d}' / (stream + '.bin')

    def task_inputs_complete(self, sid, generation):
        return self.db.execute("SELECT 1 FROM task_inputs WHERE sid=? AND generation=? "
            "AND kind='coverage' AND lookup_key='' AND seq=0", (sid, generation)).fetchone() is not None

    def mark_task_inputs_complete(self, sid, generation):
        """Inside the caller's batch: a fresh generation, or a successfully indexed whole generation."""
        self.db.execute("INSERT OR IGNORE INTO task_inputs VALUES (?,?,'coverage','',0)", (sid, generation))

    # @beacon[
    #   id=opc@Store.index_task_frame,
    #   slice_labels=opc-basic,
    #   kind=ast,
    #   comment=Index every scoped assistant tool-use and exact task/run lifecycle frame by seq in the frame's batch using SQLite-bindable identity keys; retain repeated ids and leave raw input and uniqueness proof to full_task_input.,
    # ]
    def index_task_frame(self, sid, generation, seq, event, detail):
        """Only candidate pointers: no scope filtering, input cropping, or one-frame-per-id replacement.

        Legacy events may have no type metadata. Match the old scan's metadata predicates as well as the
        raw event type, including its lifecycle treatment of every system frame with null type metadata.
        Key encoding never makes an otherwise JSON-valid tool id inadmissible.
        """
        detail = detail if isinstance(detail, dict) else {}
        typ, keys = detail.get('type'), []
        if event.get('type') == 'assistant' and typ in (None, 'assistant'):
            message = event.get('message')
            blocks = message.get('content') if isinstance(message, dict) else None
            if isinstance(blocks, list):
                keys.extend(('tool', _task_tool_key(block['id'])) for block in blocks if isinstance(block, dict)
                    and block.get('type') == 'tool_use' and isinstance(block.get('id'), str) and block['id'])
        if (event.get('type') == 'system' and (typ is None or typ == 'system' and detail.get('subtype') in
                ('task_started', 'task_updated', 'task_progress', 'task_notification')) and
                isinstance(event.get('tool_use_id'), str) and event['tool_use_id']):
            keys.append(('task', _task_run_key(event.get('task_id'), event.get('run_id'))))
        self.db.executemany('INSERT OR IGNORE INTO task_inputs VALUES (?,?,?,?,?)',
                           [(sid, generation, kind, key, seq) for kind, key in keys])

    # @beacon[
    #   id=opc@Store.backfill_task_inputs,
    #   slice_labels=opc-basic,
    #   kind=ast,
    #   comment=Lazy one-generation task-input back-fill: SQLite's original assistant/lifecycle metadata predicates select every frame pointer and coverage in one batch with no yield; a nested savepoint isolates failure without committing a caller's otherwise-empty batch.,
    # ]
    def backfill_task_inputs(self, sid, generation):
        """No startup sweep. A partial generation is replaced and marked only after the scan succeeds.

        The savepoint matters when task_event asks from inside a native read's batch: an index failure
        must leave that read intact so full_task_input can still answer through its original scan.
        Start the outer SQLite transaction if the caller has only deferred records so far; releasing
        an outermost SAVEPOINT would otherwise commit before Store.batch's owner decides to commit.
        """
        with self.batch():
            if not self.db.in_transaction:
                self.db.execute('BEGIN')
            self.db.execute('SAVEPOINT task_inputs_backfill')
            try:
                self.db.execute('DELETE FROM task_inputs WHERE sid=? AND generation=?', (sid, generation))
                rows = self.db.execute("SELECT *, json_extract(detail,'$.type') AS input_type, "
                    "json_extract(detail,'$.subtype') AS input_subtype FROM events "
                    "WHERE sid=? AND generation=? AND kind='native_frame' "
                    "AND stream='stdout' AND (json_extract(detail,'$.type') IS NULL OR "
                    "json_extract(detail,'$.type')='assistant' OR (json_extract(detail,'$.type')='system' AND "
                    "json_extract(detail,'$.subtype') IN "
                    "('task_started','task_updated','task_progress','task_notification'))) ORDER BY seq",
                    (sid, generation))
                with ExitStack() as opened:
                    stream = None
                    for row in rows:
                        if stream is None:
                            stream = opened.enter_context(self.path(sid, generation, 'stdout').open('rb'))
                        stream.seek(row['offset'])
                        event = json.loads(stream.read(row['length']))
                        self.index_task_frame(sid, generation, row['seq'], event,
                                              {'type': row['input_type'], 'subtype': row['input_subtype']})
                self.mark_task_inputs_complete(sid, generation)
            except BaseException:
                self.db.execute('ROLLBACK TO task_inputs_backfill')
                self.db.execute('RELEASE task_inputs_backfill')
                raise
            self.db.execute('RELEASE task_inputs_backfill')

    # @beacon[
    #   id=opc@Store.task_input_rows,
    #   slice_labels=opc-basic,
    #   kind=ast,
    #   comment=Exact tool-id or task/run frame lookup after lazy per-generation coverage; return every matching seq in order for the unchanged raw-input proof, or None to use today's scan if the index cannot be read or back-filled.,
    # ]
    def task_input_rows(self, sid, generation, kind, lookup):
        """An empty answer is authoritative only with coverage; None requests the original scan."""
        try:
            key = _task_run_key(*lookup) if kind == 'task' else _task_tool_key(lookup)
            if not self.task_inputs_complete(sid, generation):
                self.backfill_task_inputs(sid, generation)
            return self.db.execute('SELECT e.* FROM task_inputs AS i JOIN events AS e ON e.seq=i.seq '
                'WHERE i.sid=? AND i.generation=? AND i.kind=? AND i.lookup_key=? ORDER BY i.seq',
                (sid, generation, kind, key)).fetchall()
        except (sqlite3.Error, OSError, ValueError, TypeError, AttributeError, RecursionError):
            return None  # index availability never changes a task answer into a new refusal or unknown

    def append_bytes(self, sid, generation, stream, data):
        path = self.path(sid, generation, stream)
        fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'ab') as f:
            offset = f.tell()
            f.write(data)
            f.flush()
            os.fsync(f.fileno())  # the bytes and the file size, before any index row names them
        if path not in self.synced:
            # The file's directory entry needs one sync after the file appears; from then on the file's own fsync
            # makes each append durable.
            sync_directory(path.parent)
            self.synced.add(path)
        return offset

    # @beacon[
    #   id=opc@Store.event,
    #   slice_labels=opc-basic,opc-rows,
    #   kind=ast,
    #   comment=The one event insert: stream end, writer watermark and rows hook share the caller's batch and rollback; a row-health touch publishes exact per-session event debt only after commit, and a rolled-back cursor is never reused.,
    # ]
    def event(self, sid, generation, kind, detail=None, stream=None, offset=None, length=None, arrival_time=None,
              _frame=None, _task_frame=None):
        # CF-04: outside a read, the append and its rows share one batch too. Nested batches remain no-ops.
        with self.batch():
            task_frame = kind == 'native_frame' and stream == 'stdout' and _task_frame is not None
            covered = task_frame and self.task_inputs_complete(sid, generation)
            values = (sid, generation, time.time() if arrival_time is None else arrival_time,
                      kind, json.dumps(detail or {}, ensure_ascii=False), stream, offset, length)
            seq = None
            if self.seq_floor:
                # A rolled-back batch handed out cursors up to seq_floor: skip past them, so a cursor once handed out
                # never names a different event.
                if self.db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0] < self.seq_floor:
                    seq = self.seq_floor + 1
                self.seq_floor = 0
            cursor = self.db.execute('INSERT INTO events(seq,sid,generation,time,kind,detail,stream,offset,length) '
                                     'VALUES (?,?,?,?,?,?,?,?,?)', (seq, *values))
            self.batch_high = max(self.batch_high, cursor.lastrowid)
            if sid:
                self.rows_changed.add(sid)
            if stream is not None and offset is not None and length is not None:
                self.db.execute('INSERT INTO stream_ends(sid,generation,stream,end) VALUES (?,?,?,?) '
                                'ON CONFLICT(sid,generation,stream) DO UPDATE SET end=MAX(stream_ends.end,excluded.end)',
                                (sid, generation, stream, offset + length))
            self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',
                            ('stream_ends_through', str(cursor.lastrowid)))
            result = self.cursor(cursor.lastrowid)
            if task_frame:
                self.index_task_frame(sid, generation, cursor.lastrowid, _task_frame, detail)
                if covered:
                    self.mark_task_inputs_complete(sid, generation)
            if self.rows_writer is not None:
                self.rows_writer(sid, {result: _frame} if _frame is not None else None)
            return result

    def cursor(self, seq):
        return f'{self.identity}:{seq}'

    def parse_cursor(self, cursor):
        if cursor is None:
            return 0
        identity, seq = cursor.split(':', 1)
        if identity != self.identity or not seq.isdigit():
            raise ValueError('foreign or invalid durable cursor')
        seq = int(seq)
        last = self.db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
        if seq > last:
            raise ValueError('cursor ahead of durable log (gap)')
        return seq

    def replay(self, cursor=None, sid=None, limit=100):
        seq = self.parse_cursor(cursor)
        limit = min(max(int(limit), 1), 500)
        sql = 'SELECT * FROM events WHERE seq>?'
        args = [seq]
        if sid:
            sql += ' AND sid=?'
            args.append(sid)
        rows = self.db.execute(sql + ' ORDER BY seq LIMIT ?', (*args, limit + 1)).fetchall()
        result = []
        for row in rows[:limit]:
            item = dict(row)
            item['detail'] = json.loads(item['detail'])
            item['cursor'] = self.cursor(item.pop('seq'))
            result.append(item)
        return {'events': result, 'cursor': result[-1]['cursor'] if result else self.cursor(seq),
                'has_more': len(rows) > limit, 'gap': False}

    def raw(self, cursor, start=0, length=1024*1024):
        seq = self.parse_cursor(cursor)
        row = self.db.execute('SELECT * FROM events WHERE seq=?', (seq,)).fetchone()
        if not row or not row['stream']:
            raise ValueError('event has no raw bytes')
        start, length = int(start), min(max(int(length), 1), 4*1024*1024)
        if start < 0 or start > row['length']:
            raise ValueError('raw slice outside event')
        with self.path(row['sid'], row['generation'], row['stream']).open('rb') as f:
            f.seek(row['offset'] + start)
            data = f.read(min(length, row['length'] - start))
        if len(data) != min(length, row['length'] - start):
            raise OSError('durable raw record missing bytes')
        return {'base64': base64.b64encode(data).decode('ascii'), 'start': start,
                'next_start': start + len(data), 'total': row['length'],
                'has_more': start + len(data) < row['length']}

    # @beacon[
    #   id=opc@Store.raw_frames,
    #   slice_labels=opc-rows,
    #   kind=ast,
    #   comment=Read-only whole-frame batch: one log-head query and one open per stream; at most 4 MiB and 1000 cursors per call, with remaining continuations and explicit skips for the ordinary raw fallback.,
    # ]
    def raw_frames(self, cursors, budget=4*1024*1024):
        """Read one bounded slice of requested frames, never a limit on the caller's history.

        `frames` maps served cursors to exact base64 bytes and total length. `remaining`
        preserves the unprocessed suffix (byte/cursor budget); `skipped` names cursors
        needing ordinary paged raw reads. No writes, transaction or intervening await.
        """
        if not isinstance(cursors, list):
            raise ValueError('cursors must be a list')
        budget = min(max(int(budget), 1), 4*1024*1024)
        last = self.db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
        frames, skipped, remaining, streams, used = {}, [], cursors[1000:], {}, 0
        with ExitStack() as opened:
            for number, cursor in enumerate(cursors[:1000]):
                try:
                    identity, seq = cursor.split(':', 1)
                    if not seq.isdigit():
                        raise ValueError('invalid sequence')
                    seq = int(seq)
                except (AttributeError, ValueError):
                    skipped.append({'cursor': cursor, 'reason': 'invalid-cursor'})
                    continue
                if identity != self.identity:
                    skipped.append({'cursor': cursor, 'reason': 'foreign-cursor'})
                    continue
                if seq > last:
                    skipped.append({'cursor': cursor, 'reason': 'invalid-cursor'})
                    continue
                row = self.db.execute('SELECT sid,generation,stream,offset,length FROM events WHERE seq=?',
                                      (seq,)).fetchone()
                if not row or not row['stream']:
                    skipped.append({'cursor': cursor, 'reason': 'no-raw'})
                    continue
                total = row['length']
                if total > budget:
                    skipped.append({'cursor': cursor, 'reason': 'oversize'})
                    continue
                if used + total > budget:
                    remaining = cursors[number:]
                    break
                path = self.path(row['sid'], row['generation'], row['stream'])
                if path not in streams:
                    try:
                        streams[path] = opened.enter_context(path.open('rb'))
                    except OSError:
                        streams[path] = None
                stream = streams[path]
                if stream is None:
                    skipped.append({'cursor': cursor, 'reason': 'raw-unavailable'})
                    continue
                stream.seek(row['offset'])
                data = stream.read(total)
                used += len(data)  # short reads consume this call's I/O budget too
                if len(data) != total:
                    skipped.append({'cursor': cursor, 'reason': 'short-read'})
                    continue
                frames[cursor] = {'base64': base64.b64encode(data).decode('ascii'), 'total': total}
        return {'frames': frames, 'remaining': remaining, 'skipped': skipped}
