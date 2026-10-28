"""The shared session-id resolver (oc_common.match_session_id / resolve_session_id), Manager-Session-110 ([ap:V0616D])."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oc_common as c  # noqa: E402

REAL = "ses_f0179ef0affe1pm5PwWok7XJND"
IDS = [REAL,
       "ses_f02f70debffejYM3ek7raDw0Zk", "ses_f02f76dc0ffeblNS95fqrK2NTB", "ses_f02f64661ffekmKFLKPc6Hho74",
       "ses_eff4d315dffeMFhW46UB5R4Kgt"]


@pytest.mark.parametrize("handle,how", [
    (REAL, "exact"),
    ("ses_f0179ef0", "prefix"),
    ("f0179ef0", "prefix"),                                # bare segment, no ses_
    ("ses_f0179ef0affe1pm5Pwok7XJND", "corrected"),        # the incident: one character dropped
    ("ses_f0179ef0affe1pm5PwWok7XJNE", "corrected"),       # substitution
    ("ses_f0179ef0affe1pm5PwWko7XJND", "corrected"),       # transposition
    (f"`{REAL}`", "exact"),                                 # stray backticks
])
def test_resolves(handle, how):
    sid, got, _ = c.match_session_id(handle, IDS)
    assert (sid, got) == (REAL, how)


def test_family_prefix_is_ambiguous():
    assert c.match_session_id("ses_f02f", IDS)[1] == "ambiguous"


def test_sibling_prefix_with_a_different_tail_is_never_guessed():
    # a deleted session's id would share its siblings' timestamp prefix; it must NOT map onto a sibling
    assert c.match_session_id("ses_f02f70debffeZZZZZZZZZZZZZZ", IDS)[:2] == (None, "none")


def test_destructive_callers_get_a_suggestion_not_a_correction():
    sid, how, near = c.match_session_id("ses_f0179ef0affe1pm5Pwok7XJND", IDS, fuzzy=False)
    assert (sid, how, near) == (None, "suggest", [REAL])


def test_resolve_session_id_messages(capsys):
    sessions = [{"id": i, "title": f"t{i[-4:]}"} for i in IDS]
    assert c.resolve_sid("ses_f0179ef0affe1pm5Pwok7XJND", sessions) == REAL
    assert "session id corrected" in capsys.readouterr().err
    with pytest.raises(SystemExit) as e:
        c.resolve_sid("ses_123456789012", sessions)
    assert "Closest" in str(e.value)
