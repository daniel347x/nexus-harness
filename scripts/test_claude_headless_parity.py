"""Launch-local Manager persona/tools regressions, without inference or account changes."""
import json

import pytest

from claude_headless import launch, manager
from test_claude_headless_launch import build, pair


def test_manager_persona_tools_and_settings(tmp_path):
    result = build(tmp_path, role='Manager', environ={
        'WORKFLOWY_MCP_BEARER': 'inert', 'ENABLE_TOOL_SEARCH': 'true',
        'CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH': '1',
        'CLAUDE_CODE_DISABLE_BACKGROUND_TASKS': '1'})
    argv = result['argv']
    assert '--system-prompt-file' in argv and '--append-system-prompt-file' not in argv
    assert pair(argv, '--tools').split(',') == [
        'Bash', 'Read', 'Edit', 'Write', 'Grep', 'Glob', 'WebFetch', 'WebSearch', 'TaskStop', 'Agent', 'SendMessage']
    assert '--disable-slash-commands' in argv
    assert '--allowedTools' not in argv
    assert result['env']['ENABLE_TOOL_SEARCH'] == 'false'
    assert result['env']['CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH'] == '16384'
    assert result['profile']['attribution'] == {'commit': '', 'pr': ''}
    assert result['profile']['includeGitInstructions'] is False
    assert 'CLAUDE_CODE_DISABLE_BACKGROUND_TASKS' not in result['env']


@pytest.mark.parametrize('role', ['Manager', 'conductor', 'worker', 'helper', 'base'])
def test_every_role_keeps_edit_and_web_tools_without_permission_preapproval(tmp_path, role):
    result = build(tmp_path, role=role)
    tools = pair(result['argv'], '--tools').split(',')
    assert {'Edit', 'Write', 'WebFetch', 'WebSearch'} <= set(tools)
    assert '--allowedTools' not in result['argv']
    if role != 'Manager':
        assert 'Agent' not in tools and 'Monitor' not in tools


def test_policy_can_switch_back_per_role_and_resume_keeps_original(tmp_path, monkeypatch):
    config = tmp_path / 'roles.json'
    config.write_text(json.dumps({'default': {
        'system_prompt_mode': 'append', 'tools': 'default', 'disable_skills': False},
        'Manager': {'system_prompt_mode': 'replace', 'tools': ['Bash'], 'disable_skills': True}}))
    monkeypatch.setattr(launch, 'ROLE_POLICY_PATH', config)
    old, new, fresh = [tmp_path / name for name in ('old', 'new', 'fresh')]
    for p in (old, new, fresh):
        p.mkdir()
    first = build(old, role='Manager')
    (old / 'launch.json').write_text(json.dumps(first['safe']))
    config.write_text(json.dumps({'default': {
        'system_prompt_mode': 'append', 'tools': 'default', 'disable_skills': False}}))
    second = build(new, role='Manager', resume=True, snapshot_source=str(old))
    assert second['safe']['role_policy'] == first['safe']['role_policy']
    assert '--system-prompt-file' in second['argv']
    assert '--append-system-prompt-file' in build(fresh, role='Manager')['argv']


def test_pre_policy_resume_retains_append_default(tmp_path):
    old, new = tmp_path / 'old', tmp_path / 'new'
    old.mkdir(); new.mkdir()
    first = build(old, role='Manager')
    first['safe'].pop('role_policy')
    (old / 'launch.json').write_text(json.dumps(first['safe']))
    assert '--append-system-prompt-file' in build(
        new, role='Manager', resume=True, snapshot_source=str(old))['argv']


@pytest.mark.parametrize('policy', [
    {}, {'system_prompt_mode': 'bad', 'tools': ['Bash'], 'disable_skills': False},
    {'system_prompt_mode': 'replace', 'tools': ['Bash', 'Bash'], 'disable_skills': False},
])
def test_invalid_policy_refuses_before_snapshots(tmp_path, monkeypatch, policy):
    config = tmp_path / 'roles.json'
    config.write_text(json.dumps({'default': policy}))
    monkeypatch.setattr(launch, 'ROLE_POLICY_PATH', config)
    with pytest.raises(ValueError):
        build(tmp_path, role='Manager')
    assert not (tmp_path / 'prompt.md').exists()


def test_runtime_no_missing_subagent_or_fork_route():
    text = manager.compose_manager_prompt()['text']
    assert 'There is no OpenCode `subagent` tool in Claude Code' in text
    assert 'oc.sh seat quick --helper --no-wait' in text
    assert 'subagent_type: "manager-quick"' in text
    assert 'Never use another agent type' in text
    assert 'run_in_background: true' in text
    assert '`--fork-from` is OpenCode\'s: a Claude conversation cannot be forked into OpenCode' in text
    assert 'oc.sh seat quick --fork-self [--forklift] [--doorbell none|launch|program] [--variant V] --title "‹what›"' in text
    for words in ('zero preparation', 'back to Dan in under 15 s', 'any check the forklift can do, you do not',
                  'Manager for that one', 'both providers', 'network / modem trouble', 'no other existing AP',
                  'slightly discouraged but encouraged when of clear value', 'AP-ID', 'read the result Anchor Point'):
        assert words in text
    assert 'The one exception takes no id' in text
    assert 'automatic and idle compaction are disabled' in text
    assert 'appended per-run prompt' not in text
