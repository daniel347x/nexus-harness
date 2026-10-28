"""A §3 History archive for tests and measurements, built from MasterSpec [ap:UHKPV1] §3.3's DDL by the tests.

Never imports the archiver (scripts/oc_history.py): rows are written as §3.2–§3.5 describe them. `Archive` builds
small archives for route tests (one transaction per session, as the archiver writes); `build_realistic` builds a
full-size one from real-shaped snapshots (U2.0's host records and OpenCode infos, copied to scratch) for the
latency measurement. Leg 2's browser and node checks can reuse both:

    python3 transcription-pane/tests/history_fixture.py OUT.sqlite3 --hosts host_sessions.jsonl --opencode opencode_live.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random
import sqlite3
import sys
import time
import zlib

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parent), str(HERE.parent.parent / 'scripts')):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# §3.3, normative (column order free; every timestamp epoch milliseconds).
DDL = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE sessions (
  key TEXT PRIMARY KEY, unit_key TEXT NOT NULL, backend TEXT NOT NULL, id TEXT NOT NULL, native_uuid TEXT,
  program_core TEXT, parent_key TEXT, kind TEXT NOT NULL, role TEXT, title TEXT, chosen_name TEXT, socket TEXT,
  manager_session TEXT, cwd TEXT, created_at INTEGER NOT NULL, first_at INTEGER, last_at INTEGER NOT NULL,
  close_reason TEXT, outcome TEXT, cost_usd REAL, tokens_json TEXT, models_json TEXT NOT NULL,
  levels_json TEXT NOT NULL, turns INTEGER NOT NULL, turn_facts_json TEXT NOT NULL, telemetry_json TEXT,
  source_json TEXT NOT NULL, notes_json TEXT, raw_bytes INTEGER NOT NULL, witness TEXT NOT NULL,
  body_sha256 TEXT NOT NULL, body_bytes INTEGER NOT NULL, archived_at INTEGER NOT NULL, pruned_at INTEGER,
  archive_version INTEGER NOT NULL, UNIQUE (backend, id));
CREATE INDEX sessions_unit ON sessions (unit_key, last_at);
CREATE INDEX sessions_program ON sessions (program_core);
CREATE INDEX sessions_last ON sessions (last_at);
CREATE TABLE bodies (key TEXT PRIMARY KEY REFERENCES sessions(key), codec TEXT NOT NULL CHECK (codec = 'zlib-json'),
  body BLOB NOT NULL);
CREATE TABLE units (
  unit_key TEXT PRIMARY KEY, kind TEXT NOT NULL, program_core TEXT, first_at INTEGER NOT NULL,
  last_at INTEGER NOT NULL, sessions INTEGER NOT NULL, cost_usd REAL, backends_json TEXT NOT NULL,
  sealed_at INTEGER, seal TEXT, telemetry_json TEXT, archived_at INTEGER NOT NULL);
CREATE INDEX units_order ON units (last_at DESC, unit_key DESC);
"""


def canonical(turns) -> bytes:
    """§3.4's canonical body bytes (what body_sha256 hashes)."""
    return json.dumps(turns, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def user(seq, ts, text, origin=None) -> dict:
    turn = {'seq': seq, 'role': 'user', 'ts': ts, 'text': text}
    if origin:
        turn['origin'] = origin
    return turn


def assistant(seq, ts, text, tools=(), model='claude-opus-5-5', effort='high') -> dict:
    turn = {'seq': seq, 'role': 'assistant', 'ts': ts, 'text': text}
    if tools:
        turn['tools'] = list(tools)
    if model:
        turn['model'] = model
    if effort:
        turn['effort'] = effort
    return turn


def tool(name, value, status='ok', child=None) -> dict:
    """§3.4's tool entry: the first 120 code points of the input as compact JSON."""
    return {'name': name, 'status': status,
            'preview': json.dumps(value, ensure_ascii=False, separators=(',', ':'))[:120], 'child': child}


def host_record(uuid, *, role='worker', program=None, labels=None, state='exited', generation=1,
                cwd='/mnt/e/__daniel347x/__Dan_Root/__repos/quick-worker', **extra) -> dict:
    """A host `list` summary row (the source_json of a host session)."""
    return {'execution_id': 'cc-' + uuid, 'native_uuid': uuid, 'backend': 'claude', 'generation': generation,
            'role': role, 'program': program, 'socket': None, 'parent': None, 'cwd': cwd, 'state': state,
            'owner': 'gone', 'labels': labels or {}, 'summary': True, **extra}


def opencode_info(sid, *, title, parent=None, metadata=None, created=1_792_000_000_000, updated=None,
                  agent='quick-ds41-flash', directory='/mnt/e/__daniel347x/__Dan_Root/__repos/quick-worker') -> dict:
    """An OpenCode session info object (the source_json of an OpenCode session)."""
    info = {'id': sid, 'projectID': 'fixture', 'agent': agent,
            'model': {'id': 'deepseek/deepseek-v4.1-flash-1017', 'providerID': 'openrouter', 'variant': 'max'},
            'cost': 0.0125, 'time': {'created': created, 'updated': updated or created + 60_000}, 'title': title,
            'location': {'directory': directory}}
    if parent:
        info['parentID'] = parent
    if metadata is not None:
        info['metadata'] = metadata
    return info


def turn_facts(turns) -> dict:
    """turn_facts_json: the live turnFacts shape, in seconds."""
    stamps = [t['ts'] for t in turns if isinstance(t.get('ts'), int)]
    models = list(dict.fromkeys(t['model'] for t in turns if t.get('model')))
    levels = list(dict.fromkeys(t['effort'] for t in turns if t.get('effort')))
    facts = {'status': 'complete', 'records': len(turns), 'models': models, 'levels': levels,
             'modelsComplete': True, 'levelsComplete': True}
    if stamps:
        facts.update(firstAt=min(stamps) / 1000, lastAt=max(stamps) / 1000)
    return facts


def session_row(key, *, turns=(), source=None, unit_key=None, kind=None, program_core=None, parent_key=None,
                title=None, chosen_name=None, created_at=None, last_at=None, cwd=None, role=None, telemetry=None,
                archived_at=None, pruned_at=None, archive_version=1, cost_usd=None, body=None, now=None):
    """(sessions row dict, bodies row tuple) for one archived conversation."""
    backend, sid = key.split(':', 1)
    turns = list(turns)
    raw = canonical(turns)
    stamps = [t['ts'] for t in turns if isinstance(t.get('ts'), int)]
    now = now or int(time.time() * 1000)
    created_at = created_at if created_at is not None else (min(stamps) if stamps else now - 3_600_000)
    last_at = last_at if last_at is not None else (max(stamps) if stamps else created_at)
    facts = turn_facts(turns)
    row = {'key': key, 'unit_key': unit_key or (f'program:{program_core}' if program_core else 'session:' + key),
           'backend': backend, 'id': sid,
           'native_uuid': sid[3:39] if backend == 'claude' else None, 'program_core': program_core,
           'parent_key': parent_key, 'kind': kind or ('subagent' if '/agent-' in sid else 'session'), 'role': role,
           'title': title, 'chosen_name': chosen_name, 'socket': None, 'manager_session': None, 'cwd': cwd,
           'created_at': created_at, 'first_at': min(stamps) if stamps else None, 'last_at': last_at,
           'close_reason': None, 'outcome': None, 'cost_usd': cost_usd, 'tokens_json': None,
           'models_json': json.dumps(facts['models']), 'levels_json': json.dumps(facts['levels']), 'turns': len(turns),
           'turn_facts_json': json.dumps(facts), 'telemetry_json': json.dumps(telemetry) if telemetry is not None else None,
           'source_json': json.dumps(source if source is not None else {'native': True, 'cwd': cwd}, ensure_ascii=False),
           'notes_json': None, 'raw_bytes': len(raw) * 3,
           'witness': json.dumps({'kind': f'{backend}-fixture', 'size': len(raw)}, sort_keys=True, separators=(',', ':')),
           'body_sha256': hashlib.sha256(raw).hexdigest(), 'body_bytes': 0, 'archived_at': archived_at or now,
           'pruned_at': pruned_at, 'archive_version': archive_version}
    blob = zlib.compress(raw, 6) if body is None else body
    row['body_bytes'] = len(blob)
    return row, (key, 'zlib-json', blob)


def _insert(db, row, body):
    db.execute(f"INSERT OR REPLACE INTO sessions ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
    db.execute('INSERT OR REPLACE INTO bodies (key, codec, body) VALUES (?, ?, ?)', body)


def _units(db, unit_keys=None, sealed=None, telemetry=None, now=None):
    """Recompute units rows from their members (§3.3: maintained in each session's transaction)."""
    sealed, telemetry = sealed or {}, telemetry or {}
    where = '' if unit_keys is None else f"WHERE unit_key IN ({','.join('?' * len(unit_keys))})"
    for unit_key, first, last, count, cost, backends, archived in db.execute(
            'SELECT unit_key, MIN(created_at), MAX(last_at), COUNT(*), SUM(cost_usd), '
            f'GROUP_CONCAT(DISTINCT backend), MAX(archived_at) FROM sessions {where} GROUP BY unit_key',
            list(unit_keys or ())).fetchall():
        old = db.execute('SELECT sealed_at, seal, telemetry_json FROM units WHERE unit_key = ?', (unit_key,)).fetchone()
        core = unit_key[8:] if unit_key.startswith('program:') else None
        stamp = sealed.get(unit_key, old[0] if old else None)
        tele = telemetry.get(unit_key)
        db.execute('INSERT OR REPLACE INTO units VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                   (unit_key, 'program' if core else 'session', core, first, last, count, cost,
                    json.dumps(sorted(backends.split(','))), stamp,
                    ('[ap:SEAL' + core[:2] + ']') if stamp and core else (old[1] if old else None),
                    json.dumps(tele) if tele is not None else (old[2] if old else None), archived or now))


class Archive:
    """A small §3 archive in WAL mode (the archiver's journal mode); each add() is one transaction."""

    def __init__(self, path, *, wal=True, meta=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(str(self.path), isolation_level=None)
        if wal:
            self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript(DDL)
        values = {'schema_version': '1', 'archive_version': '1', 'created_at': str(int(time.time() * 1000)), **(meta or {})}
        self.db.executemany('INSERT INTO meta VALUES (?, ?)', list(values.items()))

    def add(self, key, *, sealed_at=None, unit_telemetry=None, **fields) -> dict:
        row, body = session_row(key, **fields)
        self.db.execute('BEGIN')
        _insert(self.db, row, body)
        _units(self.db, [row['unit_key']], {row['unit_key']: sealed_at} if sealed_at else None,
               {row['unit_key']: unit_telemetry} if unit_telemetry is not None else None)
        self.db.execute('COMMIT')
        return row

    def execute(self, sql, args=()):
        return self.db.execute(sql, args)

    def close(self):
        self.db.close()


# ---- the full-size fixture (measurement; Leg 2's browser runs)

_WORDS = None


def _text(rng, chars):
    """Readable-looking prose of about `chars` characters (compresses like text, ~3x under zlib)."""
    global _WORDS
    if _WORDS is None:
        letters = 'etaoinshrdlucmfwypvbgkqjxz'
        seeded = random.Random(1)
        _WORDS = [''.join(seeded.choices(letters, weights=range(26, 0, -1), k=seeded.randint(2, 9))) for _ in range(3000)]
    words = rng.choices(_WORDS, weights=[1 / (i + 1) for i in range(len(_WORDS))], k=max(1, chars // 6))
    return ' '.join(words)[:chars]


def _tools(rng, n, children=()):
    out = []
    for i in range(n):
        name = rng.choice(('Bash', 'Read', 'Edit', 'Grep', 'mcp__workflowy__node', 'Write'))
        value = ({'command': 'cd /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion && ' + _text(rng, rng.randint(20, 300)),
                  'description': _text(rng, 30)} if name == 'Bash' else
                 {'verb': 'read', 'params': {'node_handle': '[ap:%06d]' % rng.randint(0, 999999)}} if name.startswith('mcp')
                 else {'file_path': '/mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/' + _text(rng, 40).replace(' ', '/'),
                       'old_string': _text(rng, rng.randint(10, 400))})
        out.append(tool(name, value, rng.choice(('ok', 'ok', 'ok', 'error', 'unknown')),
                        children[i] if i < len(children) else None))
    return out


def conversation(rng, start_ms, *, compressed=None, turns=None, model='claude-opus-5-5', children=()):
    """Synthetic turns: until the body reaches ~`compressed` bytes after zlib, or exactly `turns` turns."""
    out, size, ts = [], 0, start_ms
    target = (compressed or 0) * 3
    while (len(out) < turns) if turns is not None else (size < target or len(out) < 2):
        ts += rng.randint(2_000, 400_000)
        if len(out) % 2 == 0:
            text = _text(rng, rng.randint(200, 3000))
            out.append(user(len(out), ts, text, 'task-notification' if rng.random() < 0.03 else None))
        else:
            text = _text(rng, rng.randint(60, 4000)) if rng.random() < 0.8 else ''
            out.append(assistant(len(out), ts, text, _tools(rng, rng.choice((0, 0, 1, 2, 3, 4)), children), model,
                                 rng.choice(('high', 'max', 'medium'))))
            children = ()
        size += len(out[-1]['text']) + 60 + sum(len(t['preview']) + 50 for t in out[-1].get('tools', ()))
    return out


def build_realistic(path, hosts, opencode, *, seed=7, natives=91, subagents=49, large_turns=6000) -> dict:
    """A full-size archive from real-shaped snapshots: every host record and OpenCode session, plus synthetic
    unmanaged transcripts and subagents, bodies 5–21 kB compressed and one very large conversation."""
    from claude_headless.host import LIST_SUMMARY_OMITS, list_program
    from opencode_programs import SESSION_ID, opencode_row
    rng, now = random.Random(seed), int(time.time() * 1000)
    records = [json.loads(line) for line in Path(hosts).read_text(encoding='utf-8').splitlines() if line.strip()]
    infos = json.loads(Path(opencode).read_text(encoding='utf-8'))
    infos = infos['sessions'] if isinstance(infos, dict) else infos
    path = Path(path)
    if path.exists():
        raise FileExistsError(path)
    archive = Archive(path)
    db = archive.db
    db.execute('PRAGMA synchronous=OFF')
    db.execute('BEGIN')
    rows, managers = [], []
    for record in records:
        uuid_ = record.get('native_uuid')
        if not isinstance(uuid_, str):
            continue
        source = {**{k: v for k, v in record.items() if k not in LIST_SUMMARY_OMITS}, 'summary': True}
        last = int(float(record.get('last_activity_at') or now / 1000) * 1000)
        core = list_program(record)
        key = 'claude:cc-' + uuid_
        chosen = f'Manager-Session-{100 + len(managers)}' if record.get('role') == 'Manager' and rng.random() < 0.7 else None
        rows.append(dict(key=key, source=source, program_core=core, role=record.get('role'), chosen_name=chosen,
                         cwd=record.get('cwd'), last=last))
        if record.get('role') == 'Manager':
            managers.append(len(rows) - 1)
    by_id = {s['id']: s for s in infos if isinstance(s, dict) and isinstance(s.get('id'), str) and SESSION_ID.fullmatch(s['id'])}
    attributed = {sid: opencode_row(s, by_id) for sid, s in by_id.items()}
    for sid, info in by_id.items():
        root, seen = info, {sid}
        while root.get('parentID') in by_id and root['parentID'] not in seen:
            seen.add(root['parentID'])
            root = by_id[root['parentID']]
        core = (attributed[sid] or {}).get('program') or (attributed[root['id']] or {}).get('program')
        times = info.get('time') if isinstance(info.get('time'), dict) else {}
        parent = info.get('parentID')
        rows.append(dict(key='opencode:' + sid, source=info, program_core=core, role=(attributed[sid] or {}).get('kind'),
                         cwd=(info.get('location') or {}).get('directory'), created=times.get('created'),
                         last=times.get('updated') or times.get('created') or now,
                         unit=None if core else 'session:opencode:' + root['id'],
                         parent_key='opencode:' + parent if isinstance(parent, str) and parent in by_id else None))
    folders = ('/mnt/e/__daniel347x/__Dan_Root/__repos/quick-worker', '/mnt/e/__daniel347x/__Dan_Root/__repos/manager-sessions',
               '/mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion')
    for i in range(natives):
        uuid_ = '%08x-0000-4000-8000-%012x' % (rng.getrandbits(32), i)
        rows.append(dict(key='claude:cc-' + uuid_, source={'native': True, 'cwd': folders[i % 3]}, cwd=folders[i % 3],
                         title=f'Native transcript {i}', last=now - rng.randint(1, 60) * 86_400_000))
    host_rows = [r for r in rows if r['key'].startswith('claude:') and 'execution_id' in r['source']]
    for i in range(subagents):
        parent = rng.choice(host_rows)
        agent = '%016x' % rng.getrandbits(64)
        unit = f"program:{parent['program_core']}" if parent['program_core'] else 'session:' + parent['key']
        rows.append(dict(key=f"{parent['key']}/agent-{agent}", kind='subagent', parent_key=parent['key'], unit=unit,
                         source={'parentUuid': parent['key'][10:], 'agentId': agent, 'agentType': 'general-purpose',
                                 'description': _text(rng, 40)}, cwd=parent['cwd'], last=parent['last'],
                         title=parent['key'][10:18] + ' ▸ general-purpose'))
    large = rows[managers[0]] if managers else rows[0]
    sizes = []
    for r in rows:
        last = int(r['last'])
        if r is large:
            turns = conversation(rng, last - large_turns * 200_000, turns=large_turns)
        else:
            turns = conversation(rng, last - rng.randint(60_000, 172_800_000), compressed=rng.randint(5_000, 21_000))
        created = r.get('created') or turns[0]['ts']
        last = max(last, turns[-1]['ts'], created)
        telemetry = {'sessionId': r['key'].split(':', 1)[1], 'runs': [{'mark': 'first', 'context': [50.1, 57.2, 427.0],
                                                                       'minutes': rng.randint(1, 90)}]}
        row, body = session_row(r['key'], turns=turns, source=r['source'], unit_key=r.get('unit'), kind=r.get('kind'),
                                program_core=r.get('program_core'), parent_key=r.get('parent_key'), title=r.get('title'),
                                chosen_name=r.get('chosen_name'), created_at=int(created), last_at=last, cwd=r.get('cwd'),
                                role=r.get('role'), telemetry=telemetry, cost_usd=round(rng.random() * 3, 4), now=now)
        _insert(db, row, body)
        sizes.append(row['body_bytes'])
    program_units = [u for (u,) in db.execute("SELECT DISTINCT unit_key FROM sessions WHERE unit_key LIKE 'program:%'")]
    sealed = {u: now - rng.randint(1, 90) * 86_400_000 for u in program_units if rng.random() < 0.6}
    _units(db, None, sealed, {u: {'program': '[ap:%s]' % u[8:], 'sessions': {}} for u in program_units}, now)
    db.execute("INSERT OR REPLACE INTO meta VALUES ('last_sweep_at', ?)", (str(now),))
    db.execute('COMMIT')
    archive.close()
    with sqlite3.connect(str(path)) as check:
        count, units = check.execute('SELECT (SELECT COUNT(*) FROM sessions), (SELECT COUNT(*) FROM units)').fetchone()
        largest = check.execute('SELECT unit_key, sessions FROM units ORDER BY sessions DESC LIMIT 1').fetchone()
    sizes.sort()
    return {'path': str(path), 'sessions': count, 'units': units, 'fileBytes': path.stat().st_size,
            'largestUnit': {'unit': largest[0], 'sessions': largest[1]},
            'largestConversation': {'key': large['key'], 'turns': large_turns,
                                    'compressedBytes': max(sizes), 'canonicalBytes': None},
            'bodyBytes': {'min': sizes[0], 'median': sizes[len(sizes) // 2], 'p95': sizes[int(len(sizes) * 0.95)],
                          'max': sizes[-1], 'total': sum(sizes)}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('out')
    parser.add_argument('--hosts', required=True)
    parser.add_argument('--opencode', required=True)
    parser.add_argument('--large-turns', type=int, default=6000)
    args = parser.parse_args()
    print(json.dumps(build_realistic(args.out, args.hosts, args.opencode, large_turns=args.large_turns), indent=1))
