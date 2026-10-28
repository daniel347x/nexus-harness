"""Fix round Unit A, audit finding #9: the host's `list` answered every full record in one reply
(45 records = 570 KB; Managers 23-41 KB each), and every caller paid for it. Its summary mode
drops only the bulky detail fields; its filters narrow; a parameter it does not know is ignored,
never a refusal (callers re-filter, and an older host answered every full record anyway)."""
import asyncio

from claude_headless.host import LIST_SUMMARY_OMITS, list_view
from claude_headless.settlement import RECONNECT_POLICY
from test_claude_lifecycle import setup
from test_claude_headless_host import teardown


def rec(n, socket=None, **extra):
    return {'execution_id': f'cc-{n:08x}-0000-4000-8000-000000000000', 'native_uuid': f'{n:08x}', 'role': 'worker',
            'state': 'ready', 'owner': 'host', 'socket': socket, 'labels': {'title': f't{n}'},
            'pending_recovery': None, 'last_rate_limit_info': {'status': 'allowed'},
            'launch': {'argv': ['claude'] * 200, 'paths': {'prompt': '/p'}}, 'synthetic_results': [{'r': 'x' * 500}],
            'native_effective_settings': {'big': 'y' * 500}, 'tools': ['Bash'] * 100, 'mcp_servers': [{'n': 'wf'}],
            'result_identities': ['z'] * 50, **extra}


def test_summary_drops_only_the_bulky_detail_fields_and_says_so():
    rows = [rec(1), rec(2)]
    full = list_view(rows, {})
    assert full == rows                                           # no parameter: the full records, unchanged
    summary = list_view(rows, {'summary': True})
    for row in summary:
        assert row['summary'] is True
        assert not set(LIST_SUMMARY_OMITS) & set(row)
        for kept in ('execution_id', 'role', 'state', 'owner', 'socket', 'labels', 'pending_recovery',
                     'last_rate_limit_info'):
            assert kept in row                                    # routing, settlement and quota fields stay
    assert all('launch' in r for r in rows)                       # the stored records are untouched


def test_filters_narrow_by_execution_id_and_by_socket_core():
    rows = [rec(1, '[ap:AAAAAA]'), rec(2, 'workflowy [ap:AAAAAA] socket'), rec(3, '[ap:BBBBBB]'), rec(4)]
    assert [r['native_uuid'] for r in list_view(rows, {'socket': '[ap:AAAAAA]'})] == ['00000001', '00000002']
    assert [r['native_uuid'] for r in list_view(rows, {'execution_ids': [rows[2]['execution_id'], 'cc-nope']})] == ['00000003']


def test_an_unknown_or_odd_parameter_is_ignored_never_a_refusal():
    rows = [rec(1), rec(2)]
    assert list_view(rows, {'since': 5, 'mode': 'fast'}) == rows  # `live` is a mode now: test_claude_list_cache.py
    assert list_view(rows, {'summary': 'yes', 'socket': '', 'execution_ids': 'cc-1', 'live': 'yes',
                            'programs': 'AAAAAA'}) == rows


def test_the_running_host_answers_the_summary_mode(tmp_path):
    async def run():
        host, serving, session, _ = await setup(tmp_path)
        try:
            full = await host.dispatch({'method': 'list', 'params': {}})
            summary = await host.dispatch({'method': 'list', 'params': {'summary': True}})
            assert [r['execution_id'] for r in summary] == [r['execution_id'] for r in full]
            assert all(r['summary'] is True and r['reconnect_policy'] == RECONNECT_POLICY for r in summary)
            assert any('launch' in r for r in full) and not any('launch' in r for r in summary)
        finally:
            await teardown(host, serving)
    asyncio.run(run())
