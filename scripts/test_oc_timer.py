"""Offline only: the duration parser and the refusal path; nothing sleeps."""
import pytest

import oc_timer


@pytest.mark.parametrize("text,seconds", [
    ("90", 90), ("90s", 90), ("30m", 1800), ("1h", 3600), ("1h30m", 5400), ("1h0m30s", 3630),
    ("2h", 7200), (" 5S ", 5), ("120m", 7200),
])
def test_parse_duration_accepts(text, seconds):
    assert oc_timer.parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "abc", "1d", "30 m", "m", "1.5h", "-5", "0", "0s", "30m1h"])
def test_parse_duration_rejects(text):
    with pytest.raises(ValueError):
        oc_timer.parse_duration(text)


@pytest.mark.parametrize("text", ["2h1s", "3h", "121m", "7201"])
def test_parse_duration_refuses_over_ceiling(text):
    with pytest.raises(ValueError, match="re-arm"):
        oc_timer.parse_duration(text)


def test_main_refuses_without_sleeping(monkeypatch, capsys):
    monkeypatch.setattr(oc_timer.time, "sleep", lambda s: pytest.fail("slept"))
    assert oc_timer.main(["3h"]) == 2
    assert "2-hour" in capsys.readouterr().err


def test_main_prints_one_line(monkeypatch, capsys):
    slept = []
    monkeypatch.setattr(oc_timer.time, "sleep", slept.append)
    assert oc_timer.main(["1h30m", "--label", "MCI check"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert slept == [5400] and len(out) == 1
    assert out[0].startswith("⏰ timer 1h30m elapsed · MCI check · ")
