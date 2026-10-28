"""The one loader's defaults, override precedence and nonblocking diagnostics."""
from dataclasses import asdict
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import oc_history_settings as settings


def expanded_defaults(home):
    values = {key: value.replace('~/', str(home) + '/') if isinstance(value, str) else value
              for key, value in settings.DEFAULTS.items()}
    values['originals_dir'] = values['export_dir'] + '/' + values['originals_dir']
    return values


def test_defaults_and_repository_file(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv('OC_SESSION_HISTORY_CONFIG', raising=False)
    assert json.loads(settings.CONFIG_PATH.read_text()) == settings.DEFAULTS
    loaded = asdict(settings.load())
    assert loaded.pop('problems') == ()
    assert loaded == expanded_defaults(tmp_path)
    assert len(loaded) == 16
    assert loaded['originals_dir'] == '/mnt/e/__daniel347x/__Files/SessionHistoryArchives/full-originals'


@pytest.mark.parametrize('value,expected', [(None, '{export}/full-originals'), ('kept', '{export}/kept'),
                                            ('~/elsewhere', '{home}/elsewhere'), ('/absolute/originals', '/absolute/originals')])
def test_originals_follow_export_dir_unless_absolute(tmp_path, monkeypatch, value, expected):
    config = tmp_path / 'settings.json'
    config.write_text(json.dumps({'export_dir': '~/exports', **({'originals_dir': value} if value else {})}))
    monkeypatch.setenv('OC_SESSION_HISTORY_CONFIG', str(config))
    monkeypatch.setenv('HOME', str(tmp_path))
    loaded = settings.load()
    assert loaded.originals_dir == expected.format(export=tmp_path / 'exports', home=tmp_path)
    assert not loaded.problems


def test_repository_override(tmp_path, monkeypatch):
    config = tmp_path / 'repository.json'
    config.write_text(json.dumps({'view_window_days': 12, 'trim_enabled': True}))
    monkeypatch.delenv('OC_SESSION_HISTORY_CONFIG', raising=False)
    monkeypatch.setattr(settings, 'CONFIG_PATH', config)
    assert settings.load().view_window_days == 12
    assert settings.load().trim_enabled is True
    assert settings.load().idle_unsealed_days == settings.DEFAULTS['idle_unsealed_days']


def test_env_file_replaces_repository_override(tmp_path, monkeypatch):
    repository, alternate = tmp_path / 'repository.json', tmp_path / 'alternate.json'
    repository.write_text(json.dumps({'view_window_days': 25}))
    alternate.write_text(json.dumps({'sweep_every_hours': 3, 'archive_path': '~/archive.sqlite3'}))
    monkeypatch.setattr(settings, 'CONFIG_PATH', repository)
    monkeypatch.setenv('OC_SESSION_HISTORY_CONFIG', str(alternate))
    monkeypatch.setenv('HOME', str(tmp_path))
    loaded = settings.load()
    assert loaded.view_window_days == settings.DEFAULTS['view_window_days']
    assert loaded.sweep_every_hours == 3
    assert loaded.archive_path == str(tmp_path / 'archive.sqlite3')


def test_unknown_key_ignored_and_no_range_gate(tmp_path, monkeypatch):
    config = tmp_path / 'settings.json'
    config.write_text(json.dumps({'view_window_dayz': 100, 'view_window_days': -1,
                                  'sweep_every_hours': 0.25, 'export_grain': 'custom'}))
    monkeypatch.setenv('OC_SESSION_HISTORY_CONFIG', str(config))
    loaded = settings.load()
    assert loaded.view_window_days == -1
    assert loaded.sweep_every_hours == 0.25
    assert loaded.export_grain == 'custom'
    assert not loaded.problems
    assert not hasattr(loaded, 'view_window_dayz')


@pytest.mark.parametrize('key,value', [('view_window_days', '7'), ('sweep_idle_hours', True),
                                     ('trim_enabled', 1), ('pins_path', None), ('export_grain', [])])
def test_wrong_type_defaults_and_records_problem(tmp_path, monkeypatch, capsys, key, value):
    config = tmp_path / 'settings.json'
    config.write_text(json.dumps({key: value}))
    monkeypatch.setenv('OC_SESSION_HISTORY_CONFIG', str(config))
    monkeypatch.setenv('HOME', str(tmp_path))
    loaded = settings.load()
    assert getattr(loaded, key) == expanded_defaults(tmp_path)[key]
    assert len(loaded.problems) == 1 and key in loaded.problems[0]
    assert capsys.readouterr().out == ''


@pytest.mark.parametrize('contents', [None, '{bad json', '[]'])
def test_missing_or_malformed_file_means_defaults(tmp_path, monkeypatch, contents):
    config = tmp_path / 'settings.json'
    if contents is not None:
        config.write_text(contents)
    monkeypatch.setenv('OC_SESSION_HISTORY_CONFIG', str(config))
    monkeypatch.setenv('HOME', str(tmp_path))
    values = asdict(settings.load())
    values.pop('problems')
    assert values == expanded_defaults(tmp_path)


def test_unreadable_file_means_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    with patch.object(Path, 'read_text', side_effect=PermissionError('unreadable')):
        values = asdict(settings.load())
    assert values.pop('problems')
    assert values == expanded_defaults(tmp_path)


def test_home_expands_at_load_not_import(tmp_path, monkeypatch):
    monkeypatch.setenv('OC_SESSION_HISTORY_CONFIG', str(tmp_path / 'absent.json'))
    for home in (tmp_path / 'one', tmp_path / 'two'):
        monkeypatch.setenv('HOME', str(home))
        assert settings.load().pins_path.startswith(str(home) + '/')
