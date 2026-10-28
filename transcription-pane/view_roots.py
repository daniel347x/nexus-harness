"""View's recent roots (the outer history): the roots View showed most recently, each kept with its own inner history
and Dan's note on why it mattered, in one JSON file on the pane server, so the phone and the desktop share them.

Storage: ~/.local/state/opencode-companion/transcription-pane/view-roots.json (0600 in a 0700 folder, atomic
replace). The browser owns what a root and an inner-history entry are (view-history.js cleans them on the way in);
this store only checks their shape and size, orders them (the most recently used first: shown, or dug into), keeps
at most `limit` of them plus up to NOTED_MAX older ones that carry a note (a note keeps a root), and answers each
change with the list. One root's inner history travels whole only when it is asked for (visit, open); the list
carries each root's card: its root, how deep its inner history goes, where it stands, its note.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time

ROOTS_PATH = Path.home() / '.local/state/opencode-companion/transcription-pane/view-roots.json'
LIMITS = (10, 20, 30, 50, 100)
DEFAULT_LIMIT = 30
NOTED_MAX = 100          # roots with a note kept beyond the limit
ENTRIES_MAX = 50         # view-history.js MAX_ENTRIES: one root's inner history, the root included
ROOT_BYTES = 96 * 1024   # view-history.js STORE_MAX: a root entry never outgrows its page's own store
ENTRIES_BYTES = 128 * 1024
NOTE_MAX = 4000
HANDLE_MAX = 200
BODY_LIMIT = 256 * 1024  # one POST
DEVICES = ('phone', 'desktop', 'page', 'framed')
# A root's key, as view-history.js rootKey makes it: how a handle is read and its identity (an Anchor Point by its
# core, a UUID by its first 8 hex), or a pick by its kind and a short hash of what was chosen.
KEY = re.compile(r'(?:(?:node|subtree|dock):(?:ap:[A-Z0-9]{3,16}|hex:[0-9a-f]{8})|pick:(?:dock|group|parcel|estate):[0-9a-z]{1,16})')
OPS = {'visit': {'key', 'root'}, 'open': {'key'}, 'save': {'key', 'entries', 'cursor'}, 'note': {'key', 'note'},
       'remove': {'key'}, 'limit': {'limit'}}
OPTIONAL = {'visit': {'device'}, 'open': {'device'}, 'save': {'device'}}
NAME_MAX, DESCRIPTION_MAX, DESCRIBE_MAX = 2048, 320, 12
HANDLE = re.compile(r'(?:\[ap:[A-Za-z0-9]{3,16}\]|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})?)')


def _middle(text, maximum):
    text = ' '.join(text.split())
    return text if len(text) <= maximum else text[:maximum // 2] + '…' + text[-((maximum - 1) // 2):]


def _description(note):
    """First meaningful note line; leave its words intact, drop only Markdown heading/quote decoration."""
    comment = False
    for line in note.splitlines():
        line = line.strip()
        if comment:
            comment = '-->' not in line
            continue
        if line.startswith('<!--'):
            comment = '-->' not in line
            continue
        if not line or re.match(r'^(?:↩|```|~~~|[-*_]{3,}\s*$)', line):
            continue
        if re.fullmatch(r'#{1,6}\s+(?:Task|Purpose|Result|Overview|Description|Contents|Summary|Context|Background|Introduction)\s*', line, re.I):
            continue
        return _middle(re.sub(r'^>\s?', '', re.sub(r'^#{1,6}\s+', '', line)), DESCRIPTION_MAX)
    return ''


def _target(entry):
    """Only a root that actually names ONE node/group; tag/symbol/lens roots aren't their source-map ancestor."""
    if isinstance(entry.get('handle'), str) and HANDLE.fullmatch(entry['handle']):
        return 'node', entry['handle']
    selection = entry.get('selection') or {}
    value = selection.get('value')
    if isinstance(value, str):
        return ('node', value) if HANDLE.fullmatch(value) else ('group', value) if selection.get('kind') != 'estate' else None
    if not isinstance(value, dict):
        return None
    for key in ('node', 'subtree', 'dock', 'group'):
        if isinstance(value.get(key), str):
            return ('group' if key == 'group' else 'node'), value[key]
    if value.get('kind') in ('node', 'dock', 'slice_group') and isinstance(value.get('ref'), str):
        return ('group' if value['kind'] == 'slice_group' else 'node'), value['ref']
    if isinstance(value.get('group_id'), str):
        return 'group', value['group_id']
    if value.get('kind') == 'raw_root' and isinstance(value.get('root_uuid'), str):
        return 'node', value['root_uuid']
    return None


def _keep_metadata(previous, fresh):
    """A visit/save may arrive from a page opened before enrichment: never erase already-known metadata."""
    if previous and not fresh.get('name') and previous.get('name'):
        fresh['name'] = previous['name']
    if previous and fresh.get('metadataVersion') != 1 and previous.get('metadataVersion') == 1:
        for key in ('name', 'description', 'metadataVersion'):
            if key in previous:
                fresh[key] = previous[key]
    return fresh


class Missing(LookupError):
    """The root asked for is not (or no longer) in the list."""


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def _root(value):
    """A root entry: an object, a pick (its selection) or a handle, no bigger than its page's own store."""
    if not isinstance(value, dict):
        raise ValueError('a root is an object')
    picked = isinstance(value.get('selection'), dict)
    handle = value.get('handle')
    if not picked and not (isinstance(handle, str) and handle.strip() and len(handle) <= HANDLE_MAX):
        raise ValueError('a root is a pick (selection) or a handle')
    if _size(value) > ROOT_BYTES:
        raise ValueError('a root entry is too large')
    return copy.deepcopy(value)


def _entries(value):
    """One inner history: the root first, then the handles opened from it (at most ENTRIES_MAX, bounded in size)."""
    if not isinstance(value, list) or not 1 <= len(value) <= ENTRIES_MAX:
        raise ValueError(f'an inner history is 1 to {ENTRIES_MAX} entries, the root first')
    out = [_root(value[0])]
    for entry in value[1:]:
        handle = entry.get('handle') if isinstance(entry, dict) else None
        if not (isinstance(handle, str) and handle.strip() and len(handle) <= HANDLE_MAX):
            raise ValueError('an inner-history entry is an object with its handle')
        out.append(copy.deepcopy(entry))
    if _size(out) > ENTRIES_BYTES:
        raise ValueError('an inner history is too large')
    return out


def _brief(root):
    """A root for its card: what view-history.js rootText reads (a socket's or program's active card, its last two
    lines), so the list stays small; the whole root comes back with visit and open."""
    out = copy.deepcopy(root)
    position = out.get('position')
    if isinstance(position, dict) and isinstance(position.get('cards'), list) and position['cards']:
        cards = position['cards']
        card = next((c for c in cards if isinstance(c, dict) and c.get('active') is True), cards[0])
        if isinstance(card, dict):
            card = dict(card)
            if isinstance(card.get('chain'), list):
                card['chain'] = card['chain'][-2:]
            position['cards'] = [card]
    return out


def _used(root):
    return max(root.get('visited') or 0, root.get('saved') or 0)


# @beacon[
#   id=opc-pane@ViewRoots,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=One shared Recent-roots store: private atomic JSON with inner histories, cursors, notes and use order intact. Visit/open/save/note/remove/limit retain their existing rules; describe enriches only missing root name/description/version in bounded cache-only batches outside the store lock, then merges against current identities without resurrecting removed roots. Older pages cannot erase enriched metadata; GET never waits for the daemon.,
# ]
class ViewRoots:
    """Root keys -> {entries, cursor, note, device, created, visited, saved, noted}; the list plus its limit."""

    def __init__(self, path=None, clock=time.time):
        self.path = Path(path if path is not None else ROOTS_PATH)
        self.clock = clock
        self.lock = threading.Lock()
        self._cache, self._stamp = None, None

    def _read(self):
        try:
            stat = self.path.stat()
        except OSError:
            self._cache, self._stamp = {'limit': DEFAULT_LIMIT, 'roots': []}, None
            return self._cache
        stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp != self._stamp or self._cache is None:
            data = {'limit': DEFAULT_LIMIT, 'roots': []}
            try:
                raw = json.loads(self.path.read_text(encoding='utf-8'))
                if isinstance(raw, dict):
                    if raw.get('limit') in LIMITS:
                        data['limit'] = raw['limit']
                    for root in raw.get('roots') or []:
                        try:
                            if not (isinstance(root, dict) and isinstance(root.get('key'), str) and KEY.fullmatch(root['key'])):
                                continue
                            entries = _entries(root.get('entries'))
                            cursor = root.get('cursor')
                            data['roots'].append({
                                'key': root['key'], 'entries': entries,
                                'cursor': cursor if type(cursor) is int and 0 <= cursor < len(entries) else 0,
                                'note': root['note'] if isinstance(root.get('note'), str) else '',
                                'device': root['device'] if root.get('device') in DEVICES else None,
                                **{k: root[k] for k in ('created', 'visited', 'saved', 'noted') if type(root.get(k)) in (int, float)}})
                        except ValueError:
                            continue
            except (OSError, ValueError):
                pass
            self._cache, self._stamp = data, stamp
        return self._cache

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        body = json.dumps({'schemaVersion': 1, **data}, ensure_ascii=False, indent=1).encode('utf-8')
        fd, temp = tempfile.mkstemp(prefix='.view-roots-', dir=str(self.path.parent))
        try:
            with os.fdopen(fd, 'wb') as target:
                os.fchmod(target.fileno(), 0o600)
                target.write(body)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temp, self.path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        self._stamp = None

    @staticmethod
    def _keep(data):
        """The most recently used first; past the limit, only roots with a note (up to NOTED_MAX)."""
        ordered = sorted(data['roots'], key=_used, reverse=True)
        kept = ordered[:data['limit']] + [r for r in ordered[data['limit']:] if r['note']][:NOTED_MAX]
        data['roots'] = sorted(kept, key=_used, reverse=True)

    def _reply(self, data, opened=None):
        def card(root):
            entries, cursor = root['entries'], root['cursor']
            at = entries[cursor] if cursor else None
            return {'key': root['key'], 'root': _brief(entries[0]), 'count': len(entries), 'cursor': cursor,
                    'at': {k: at[k] for k in ('handle', 'how', 'name') if k in at} if at else None,
                    'note': root['note'], 'device': root.get('device'),
                    **{k: root[k] for k in ('created', 'visited', 'saved', 'noted') if k in root}}
        out = {'schemaVersion': 1, 'limit': data['limit'], 'limits': list(LIMITS), 'notedMax': NOTED_MAX,
               'roots': [card(r) for r in sorted(data['roots'], key=_used, reverse=True)]}
        if opened is not None:
            out['opened'] = {'key': opened['key'], 'entries': copy.deepcopy(opened['entries']), 'cursor': opened['cursor'],
                             'note': opened['note']}
        return out

    def read(self):
        with self.lock:
            return self._reply(self._read())

    # @beacon[
    #   id=opc-pane@ViewRoots.describe,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Metadata-only backfill for up to 12 listed roots: take identities under the store lock, resolve distinct roots from cache/config outside it, then reread and merge only name/description/version. Keeps concurrent notes/history/use order, never resurrects removals, and persists successful reads once.,
    # ]
    def describe(self, args, read_metadata):
        """Backfill old roots once, fail-soft; resolver reads node cache/group config only, never live Workflowy."""
        if not isinstance(args, dict) or set(args) != {'schemaVersion', 'op', 'keys'} or type(args.get('schemaVersion')) is not int or args['schemaVersion'] != 1 or args.get('op') != 'describe':
            raise ValueError('describe takes exactly schemaVersion 1, op and keys')
        keys = args['keys']
        if not isinstance(keys, list) or not 1 <= len(keys) <= DESCRIBE_MAX or any(not isinstance(k, str) or not KEY.fullmatch(k) for k in keys) or len(set(keys)) != len(keys):
            raise ValueError(f'describe takes 1 to {DESCRIBE_MAX} distinct listed root keys')
        with self.lock:
            pending = [(r['key'], copy.deepcopy(r['entries'][0])) for r in self._read()['roots']
                       if r['key'] in keys and r['entries'][0].get('metadataVersion') != 1]
        changes, memo = [], {}
        for key, entry in pending:
            target = _target(entry)
            try:
                if target not in memo:
                    memo[target] = None
                    memo[target] = read_metadata(*target) if target else ('', '')
                name, note = memo[target]
                if not isinstance(name, str) or not isinstance(note, str):
                    continue
                metadata = {'description': _description(note), 'metadataVersion': 1}
                if name.strip():
                    metadata['name'] = _middle(name, NAME_MAX)
                changes.append((key, target, metadata))
            except Exception:
                # Cosmetic metadata must never refuse a root, erase old text or prevent later roots from succeeding.
                continue
        with self.lock:
            self._stamp = None
            data = copy.deepcopy(self._read())
            changed = False
            for key, target, metadata in changes:
                found = next((r for r in data['roots'] if r['key'] == key), None)
                if found and found['entries'][0].get('metadataVersion') != 1 and _target(found['entries'][0]) == target:
                    found['entries'][0].update(metadata)
                    changed = True
            if changed:
                self._write(data)
                self._cache, self._stamp = data, None
            return self._reply(data)

    def apply(self, args):
        """One change from a View page: {schemaVersion 1, op, …} → the list (and, for visit/open, that root's whole
        inner history). ValueError for a malformed change; Missing when open or note names a root no longer listed."""
        if not isinstance(args, dict) or type(args.get('schemaVersion')) is not int or args['schemaVersion'] != 1:
            raise ValueError('a change needs schemaVersion 1')
        op = args.get('op')
        if op not in OPS:
            raise ValueError('op is visit, open, save, note, remove or limit')
        fields = set(args) - {'schemaVersion', 'op'}
        if not OPS[op] <= fields or fields - OPS[op] - OPTIONAL.get(op, set()):
            raise ValueError(op + ' takes exactly ' + ', '.join(sorted(OPS[op] | OPTIONAL.get(op, set()))))
        key = args.get('key')
        if 'key' in OPS[op] and not (isinstance(key, str) and KEY.fullmatch(key)):
            raise ValueError('a root key is required')
        device = args.get('device')
        if device is not None and device not in DEVICES:
            raise ValueError('device is phone, desktop, page or framed')
        root = _root(args['root']) if op == 'visit' else None
        entries = _entries(args['entries']) if op == 'save' else None
        cursor = args.get('cursor')
        if op == 'save' and not (type(cursor) is int and 0 <= cursor < len(entries)):
            raise ValueError('cursor is an entry of that inner history')
        note = args.get('note')
        if op == 'note' and not (isinstance(note, str) and len(note.strip()) <= NOTE_MAX):
            raise ValueError(f'a note is text of at most {NOTE_MAX} characters')
        if op == 'limit' and args['limit'] not in LIMITS:
            raise ValueError('limit is one of ' + ', '.join(map(str, LIMITS)))
        with self.lock:
            # Re-read on a change, so another page's completed replace is kept.
            self._stamp = None
            data = copy.deepcopy(self._read())
            now = self.clock()
            found = next((r for r in data['roots'] if r['key'] == key), None) if key else None
            opened = None
            if op == 'visit':
                if found:
                    found['entries'][0] = _keep_metadata(found['entries'][0], root)
                else:
                    found = {'key': key, 'entries': [root], 'cursor': 0, 'note': '', 'created': now}
                    data['roots'].append(found)
                found['visited'] = now
                found['device'] = device or found.get('device')
                opened = found
            elif op == 'open':
                if not found:
                    raise Missing('that root is no longer in the list')
                found['visited'] = now
                found['device'] = device or found.get('device')
                opened = found
            elif op == 'save':
                if not found:
                    found = {'key': key, 'note': '', 'created': now, 'visited': now}
                    data['roots'].append(found)
                elif found.get('entries'):
                    entries[0] = _keep_metadata(found['entries'][0], entries[0])
                found.update(entries=entries, cursor=cursor, saved=now)
                found['device'] = device or found.get('device')
            elif op == 'note':
                if not found:
                    raise Missing('that root is no longer in the list')
                found['note'] = note.strip()
                found['noted'] = now
            elif op == 'remove':
                data['roots'] = [r for r in data['roots'] if r['key'] != key]
            else:
                data['limit'] = args['limit']
            self._keep(data)
            if opened is not None and opened not in data['roots']:
                opened = None  # a visit can outlive its own eviction only by a race too narrow to matter; say nothing
            self._write(data)
            self._cache, self._stamp = data, None
            return self._reply(data, opened)
