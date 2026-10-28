"""Previous completed model-call input usage, shared by the bound tool and pane.

This is a measured historical boundary, NOT exact occupancy inside a tool call.
Never sum turn/cumulative usage or add output tokens to that input boundary.
"""
import hashlib
import hmac
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from .controls import await_control, model_matches

NATIVE_TOOL = 'mcp__context__context_status'
INPUT_KEYS = ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')

RUNTIME = f"""\
## Native context binding
Whenever your instructions say `context_status`, call **`{NATIVE_TOOL}` with no arguments**.
It is bound to this launch, not a session/model supplied by you. It reports measured input
tokens **as of the previous completed model call**, with its timestamp and age, then applies
the shared role profile or launch target. It is not exact tool-time occupancy: the pending
tool result and subsequent input are not included. UNKNOWN means no completed model call
yet in this generation, not zero. A number remains usable while another call/tool is in flight.
Call before large reads, at landing/handoff decisions and whenever instructed. No output reserve
is subtracted. Targets are goals: finish the thing in hand, then land or hand off as advised.
Never substitute cumulative usage, byte ratios or another conversation's transcript.
Every role (Manager, Conductor, worker, helper) measures this way, by Dan's ruling.
Auto-compaction stays off; silence never kills.
"""


def validate_target(value):
    if value is not None and (type(value) not in (int, float) or not math.isfinite(value)
                              or not 0 < value <= 1):
        raise ValueError('context_target must be a finite fraction in (0,1]')
    return value


def binding_matches(record, binding):
    expected = record.get('context_binding_sha256')
    return (isinstance(binding, str) and isinstance(expected, str)
            and hmac.compare_digest(hashlib.sha256(binding.encode()).hexdigest(), expected))


def timestamp_seconds(value):
    if type(value) in (int, float) and math.isfinite(value):
        return float(value)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is not None:
                return parsed.timestamp()
        except ValueError:
            pass
    return None


def input_tokens(usage):
    if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0 for k in INPUT_KEYS):
        return None
    return sum(usage[k] for k in INPUT_KEYS)


def unavailable(record, state, reason, provenance=None):
    return {'ok': False, 'state': state, 'execution_id': record.get('execution_id'),
        'generation': record.get('generation'), 'role': record.get('role'),
        'occupancy_tokens': None, 'window_tokens': None, 'fraction_used': None,
        'target': None, 'fresh': False, 'provenance': provenance,
        'advice': 'UNKNOWN: ' + reason, 'reason': reason}


# @beacon[
#   id=opc@previous_call_report,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Reports one completed model call's measured input with as-of age and shared role targets; never cumulative or exact tool-time occupancy.,
# ]
def previous_call_report(record, usage, measured_at, window=None, source='native assistant usage', now=None):
    """Pure presentation/target arithmetic used by both observation surfaces."""
    from oc_common import context_profile, profile_target
    used = input_tokens(usage)
    if used is None:
        return unavailable(record, 'unknown', 'no completed model call yet in this generation')
    stamp = timestamp_seconds(measured_at)
    now = time.time() if now is None else now
    age = max(0, now - stamp) if stamp is not None else None
    as_of = datetime.fromtimestamp(stamp, timezone.utc).isoformat().replace('+00:00', 'Z') if stamp is not None else None
    age_label = f'{age:.0f}s old' if age is not None else 'age unavailable'
    label = f'measured: as of the previous model call ({as_of or "timestamp unavailable"}, {age_label})'
    model = record.get('context_model') or (record.get('controls_effective') or {}).get('model') or record.get('actual_model')
    profile = context_profile(model or '')
    worker = record.get('role') in ('worker', 'helper', 'base')
    window = window if type(window) is int and window > 0 else None
    target = None
    advice = 'Measured input available; native window not yet available. Use the as-of number under supervision.'
    if window is not None:
        override = validate_target(record.get('context_target'))
        fraction = override if override is not None else profile_target(profile, window, worker=worker)
        target_tokens = math.floor(window * fraction + .5)
        crossed = used >= target_tokens
        target = {'fraction': fraction, 'tokens': target_tokens, 'crossed': crossed,
            'source': 'launch context_target' if override is not None else 'profile land_by' if worker else 'profile handoff',
            'headroom_tokens': max(0, target_tokens-used)}
        action = 'LAND' if worker else 'HANDOFF'
        advice = (f'{action}: take on no new work; finish the current thing, then write output/PARTIAL.'
                  if crossed else 'OK: below target as of the previous model call; check again before large loads or new work.')
    return {'ok': True, 'state': 'measured', 'execution_id': record.get('execution_id'),
        'generation': record.get('generation'), 'role': record.get('role'), 'model': model,
        'profile_role': 'worker' if worker else 'primary', 'profile': profile,
        'profile_authority': 'plugins/context-status.ts via oc_common.context_profile/profile_target',
        'profile_fallback': bool(profile.get('fallback')), 'occupancy_tokens': used,
        'window_tokens': window, 'fraction_used': used/window if window else None,
        'remaining_tokens': max(0, window-used) if window else None, 'output_reserve_tokens': 0,
        'target': target, 'fresh': False, 'as_of': as_of, 'age_seconds': age, 'label': label,
        'advice': advice, 'provenance': {'mechanism': source, 'measured_at': as_of,
            'timestamp_basis': 'native record timestamp or host completed-call arrival', 'age_seconds': age,
            'generation': record.get('generation')},
        'measurement_scope': 'Input of the previous completed model call; subsequent tools/input and output are not included.',
        'count_basis': 'ONE model call input_tokens + cache_read_input_tokens + cache_creation_input_tokens',
        'native_api_usage_snapshot': {key: usage[key] for key in INPUT_KEYS},
        # Dan's ruling D2: seats land on this same previous-call reading.
        'unattended_roles_enabled': True}


# @beacon[
#   id=opc@latest_transcript_call,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Reads an exact session's completed assistant usage backwards without indexing conversation bodies; bounded and generation-cutoff aware.,
# ]
def latest_transcript_call(path, native_uuid, not_before=None, max_bytes=16*1024*1024, *, opener=None, agent_id=None):
    """Read-only bounded tail. An incomplete final JSONL line is never a call.

    A stop_reason certifies a final native assistant record. Per-block duplicate
    records carry the same usage; choose ONE, never add them. Caller owns path
    containment (an opener can supply its verified handle); UUID and optional
    launch cutoff are checked here as well. With `agent_id`, the file is that native
    subagent's own transcript: its sidechain records (sessionId = the parent's UUID,
    agentId = this subagent) are the subagent's own calls; anything else is skipped.
    """
    cutoff = timestamp_seconds(not_before)
    try:
        with (opener() if opener is not None else Path(path).open('rb')) as stream:
            size = stream.seek(0, 2)
            position, budget, tail = size, max_bytes, b''
            first = True
            while position > 0 and budget > 0:
                count = min(position, budget, 65536)
                position -= count
                budget -= count
                stream.seek(position)
                block = stream.read(count)
                if first:
                    newline = block.rfind(b'\n')
                    if newline < 0:
                        continue  # discard incomplete trailing row, even across chunks
                    block = block[:newline+1]
                    first = False
                pieces = (block + tail).split(b'\n')
                tail = pieces[0]
                complete = pieces[1:]
                if position == 0:
                    complete.insert(0, tail)
                    tail = b''
                for raw in reversed(complete):
                    try:
                        row = json.loads(raw)
                    except (ValueError, UnicodeError, RecursionError):
                        continue
                    if not isinstance(row, dict) or row.get('type') != 'assistant' or row.get('sessionId') != native_uuid:
                        continue
                    stamp = timestamp_seconds(row.get('timestamp'))
                    if cutoff is not None and (stamp is None or stamp < cutoff):
                        continue
                    message = row.get('message') or {}
                    own = (row.get('isSidechain') is True and row.get('agentId') == agent_id) if agent_id else not row.get('isSidechain')
                    if (not isinstance(message, dict) or not message.get('stop_reason')
                            or not own or row.get('parent_tool_use_id')
                            or input_tokens(message.get('usage')) is None):
                        continue
                    return {'usage': message['usage'], 'measured_at': row.get('timestamp'),
                        'model': message.get('model'), 'message_id': message.get('id'),
                        'source': 'native transcript completed assistant usage'}
    except OSError:
        pass
    return None


def normalize(record, native, provenance):
    """Compatibility normalizer: native total/category/tail estimates are diagnostic only."""
    call = record.get('last_completed_model_call') or {}
    usage = call.get('usage')
    if call.get('generation') != record.get('generation'):
        usage = None
    native = native if isinstance(native, dict) else {}
    window = native.get('rawMaxTokens')
    if native.get('autocompactSource') != 'auto':
        window = None
    result = previous_call_report({**record, 'context_model': call.get('model')}, usage,
        call.get('measured_at'), window, call.get('source', 'host completed model call'))
    result['provenance'] = {**(result.get('provenance') or {}), **provenance}
    result['window_source'] = 'native get_context_usage rawMaxTokens (not totalTokens)'
    return result


# @beacon[
#   id=opc@read_native_context,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Returns the last completed call's input even while tools run; native controls supply only an uncached window, never a cleanliness gate.,
# ]
async def read_native_context(session):
    record = session.record
    call = record.get('last_completed_model_call') or {}
    if call.get('generation') != record['generation']:
        call = {}
    if input_tokens(call.get('usage')) is None:
        cutoff = record.get('context_generation_started_at')
        if cutoff is not None and record.get('native_transcript_path_expected'):
            call = latest_transcript_call(record['native_transcript_path_expected'], session.sid, cutoff) or {}
    if input_tokens(call.get('usage')) is None:
        return unavailable(record, 'unknown', 'no completed model call yet in this generation')
    model = call.get('model') or (record.get('controls_effective') or {}).get('model')
    windows = record.get('context_windows') or {}
    window = windows.get(model)
    if window is None and session.process.returncode is None and not record.get('stdout_eof') and not session.infrastructure_error:
        control = session.control('get_context_usage', {'detail': 'summary'})
        reply = await await_control(session, control['request_id'])
        host = getattr(session, 'host', None)
        if host is not None and host.sessions.get(session.sid) is not session:
            return unavailable(record, 'stale', 'launch generation changed while reading its window')
        native = ((reply or {}).get('response') or {}).get('response') or {}
        if (isinstance(native, dict) and native.get('autocompactSource') == 'auto'
                and model_matches(model, native.get('model'))):
            candidate = native.get('rawMaxTokens')
            if type(candidate) is int and candidate > 0:
                window = candidate
                session.update(context_windows={**windows, model: window})
    latest = record.get('last_completed_model_call') or {}
    if latest.get('generation') == record['generation'] and input_tokens(latest.get('usage')) is not None:
        call = latest
        latest_model = call.get('model') or model
        if latest_model != model:
            window = (record.get('context_windows') or {}).get(latest_model)
        model = latest_model
    result = previous_call_report({**record, 'context_model': model}, call['usage'],
        call.get('measured_at'), window, call.get('source', 'host completed model call'))
    result['provenance']['assistant_usage_cursor'] = call.get('cursor')
    result['window_source'] = 'native result modelUsage.contextWindow or get_context_usage.rawMaxTokens'
    return result
