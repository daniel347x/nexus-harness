"""The one settings loader for session history, the live universe and trimming.

The repository file overrides DEFAULTS; OC_SESSION_HISTORY_CONFIG selects a file
instead. Bad files/values never block work. Problems are retained for history status
to report, not printed by readers. Paths expand against HOME at each load.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path


DEFAULTS = {
    'view_window_days': 7,
    'view_wide_window_days': 30,
    'idle_unsealed_days': 90,
    'universe_idle_scan_minutes': 10,
    'sweep_idle_hours': 24,
    'sweep_every_hours': 24,
    'sweep_start_delay_minutes': 10,
    'sweep_check_minutes': 15,
    'export_grain': 'month',
    'export_dir': '/mnt/e/__daniel347x/__Files/SessionHistoryArchives',
    'originals_dir': 'full-originals',
    'archive_path': '~/.local/state/opencode-companion/history/archive.sqlite3',
    'seal_ledger_path': '~/.local/state/opencode-companion/program-seals.jsonl',
    'pins_path': '~/.local/state/opencode-companion/transcription-pane/session-pins.json',
    'trim_sealed_days': 7,
    'trim_enabled': False,
}
CONFIG_PATH = Path(__file__).resolve().parents[1] / 'config' / 'session-history.json'
_PATH_KEYS = ('export_dir', 'originals_dir', 'archive_path', 'seal_ledger_path', 'pins_path')


@dataclass(frozen=True)
class Settings:
    """Loaded flat settings and diagnostics for the status command (no reader output)."""

    view_window_days: int | float
    view_wide_window_days: int | float
    idle_unsealed_days: int | float
    universe_idle_scan_minutes: int | float
    sweep_idle_hours: int | float
    sweep_every_hours: int | float
    sweep_start_delay_minutes: int | float
    sweep_check_minutes: int | float
    export_grain: str
    export_dir: str
    originals_dir: str
    archive_path: str
    seal_ledger_path: str
    pins_path: str
    trim_sealed_days: int | float
    trim_enabled: bool
    problems: tuple[str, ...] = ()


# @beacon[
#   id=opc-history@oc_history_settings.load,
#   slice_labels=opc-history,
#   kind=ast,
#   comment=The one flat history-settings reader: DEFAULTS plus the repository or env-selected JSON file; unknown keys ignored and wrong types defaulted with retained status diagnostics; paths expand against the current HOME, a relative originals_dir lands under export_dir (so it follows the backup folder), and no value range blocks work.,
# ]
def load() -> Settings:
    """Load settings without caching, printing, range gates or startup failures.

    Durations accept JSON numbers (not Booleans); switches and strings retain their
    own types. An alternate file replaces the repository override, not DEFAULTS.
    A relative originals_dir is a folder under export_dir; an absolute one stands alone.
    """
    values, problems = dict(DEFAULTS), []
    path = Path(os.path.expanduser(os.environ.get('OC_SESSION_HISTORY_CONFIG', str(CONFIG_PATH))))
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError) as error:
        data = {}
        problems.append(f'{path}: {error}')
    if not isinstance(data, dict):
        problems.append(f'{path}: expected a JSON object; using defaults')
        data = {}
    for key, default in DEFAULTS.items():
        if key not in data:
            continue
        value = data[key]
        valid = type(value) in (int, float) if type(default) is int else type(value) is type(default)
        if valid:
            values[key] = value
        else:
            problems.append(f'{key}: wrong type {type(value).__name__}; using default {default!r}')
    for key in _PATH_KEYS:
        values[key] = os.path.expanduser(values[key])
    values['originals_dir'] = os.path.join(values['export_dir'], values['originals_dir'])
    return Settings(**values, problems=tuple(problems))
