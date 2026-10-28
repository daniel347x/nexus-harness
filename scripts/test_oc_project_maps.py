"""Offline Project Map inference and both CLI defaults: exact repository roots, no location-specific constants."""
import asyncio
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import oc_common
import oc_quick
import oc_seat


LOCATION = "[ap:MAPHOME]"
A, B = "[ap:MAP001]", "[ap:MAP002]"
UUID_A, UUID_B = "aaaaaaaa-1111-1111-1111-111111111111", "bbbbbbbb-2222-2222-2222-222222222222"
MOUNT = "/mnt/z/project-map-fixture"


@pytest.fixture
def repos(tmp_path, monkeypatch):
    """Use real tmp_path directories/.git walks, mounted lexically at a fixture WSL drive (no system mount writes)."""
    for name in ("repo", "other", "space repo"):
        root = tmp_path / name
        (root / "src").mkdir(parents=True)
        (root / ".git").mkdir()
        (root / "src" / "module.py").write_text("pass\n")
    # A worktree's .git is a file, and its nearest repository wins over its enclosing checkout.
    (tmp_path / "repo" / "nested").mkdir()
    (tmp_path / "repo" / "nested" / ".git").write_text("gitdir: /fixture/worktree\n")
    exists, is_dir = Path.exists, Path.is_dir

    def translated(path):
        s = str(path)
        return tmp_path / s[len(MOUNT):].lstrip("/") if s == MOUNT or s.startswith(MOUNT + "/") else path

    monkeypatch.setattr(Path, "exists", lambda p: exists(translated(p)))
    monkeypatch.setattr(Path, "is_dir", lambda p: is_dir(translated(p)))
    return SimpleNamespace(a=MOUNT + "/repo", b=MOUNT + "/other", spaced=MOUNT + "/space repo")


class WorkflowyFixture:
    def __init__(self, maps=(), *, config=None, job="", error=None):
        self.maps = list(maps)
        self.config = {"project_maps_location": LOCATION} if config is None else config
        self.job, self.error, self.calls = job, error, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def children(self, handle):
        self.calls.append(("children", handle))
        assert handle == LOCATION
        return [{"uuid": row[0], "name": "fixture map"} for row in self.maps]

    async def read_note(self, handle):
        self.calls.append(("read_note", handle))
        return "socket", self.job

    async def call(self, family, verb, params):
        self.calls.append((family, verb, params))
        if (family, verb) == ("system", "config.get"):
            if self.error:
                raise self.error
            return json.dumps(self.config)
        if (family, verb) == ("node", "search"):
            assert params["is_regex"] is True and params["context_lines"] == 0 and params["max_matches"] == 1
            body = next(row[2] for row in self.maps if row[0] == params["root_handle"])
            hits = [line for line in body.splitlines() if re.search(params["query"], line, re.I)]
            text = f"Matched 1 lines:\n1 | {hits[0]}\n" if hits else ""
            return text + f"Pagination: offset=0; returned_matches={int(bool(hits))}; has_more=false; next_offset=null."
        if (family, verb) == ("node", "resolve"):
            handle = params["node_handle"]
            if handle == "[ap:PARENT]":
                return json.dumps({"match_count": 1, "uuid": "parent-uuid"})
            row = next((row for row in self.maps if handle in row[:2]), None)
            return json.dumps({"match_count": 1, "uuid": row[0], "ap_id": row[1]} if row else {"match_count": 0})
        raise AssertionError((family, verb, params))


def infer(wf, text):
    return asyncio.run(oc_seat.infer_maps(text, wf=wf))


def test_one_map_and_one_outcome_line(repos, capsys):
    wf = WorkflowyFixture([(UUID_A, A, "Repository: `" + repos.a + "`")])
    assert infer(wf, f"Change `{repos.a}/src/module.py`.") == [A]
    assert capsys.readouterr().out.splitlines() == [
        f"🗺️ Project Map {A} added (the job names {repos.a}); --no-map to omit"]


def test_two_maps_in_job_order_not_location_order_and_deduplicated(repos, capsys):
    windows = "Z:\\project-map-fixture\\other"
    wf = WorkflowyFixture([(UUID_A, A, repos.a), (UUID_B, B, windows)])
    text = f"First `{windows}\\missing\\file.py`, then `{repos.a}/src/module.py`, and `{repos.b}` again."
    assert infer(wf, text) == [B, A]
    line = capsys.readouterr().out.splitlines()
    assert line == [f"🗺️ Project Map {B}, {A} added (the job names {repos.b}, {repos.a}); --no-map to omit"]
    assert sum(call[:2] == ("node", "search") for call in wf.calls) == 2


@pytest.mark.parametrize("suffix", ["/scripts/x", "-old", "_old", ".old", "\\scripts\\x"])
@pytest.mark.parametrize("windows", [False, True])
def test_map_requires_repository_root_not_a_subpath_or_similar_name(repos, capsys, suffix, windows):
    root = "Z:\\project-map-fixture\\repo" if windows else repos.a
    wf = WorkflowyFixture([(UUID_A, A, root + suffix)])
    assert infer(wf, f"Work in `{repos.a}`") == []
    assert capsys.readouterr().out.splitlines() == [
        f"🗺️ no Project Map added (no map under the location names {repos.a})"]


@pytest.mark.parametrize("form", ["wsl", "windows", "windows-forward", "windows-mixed"])
def test_map_root_forms_are_equivalent_and_segments_are_regex_escaped(repos, form):
    forms = {"wsl": repos.a, "windows": "Z:\\project-map-fixture\\repo",
             "windows-forward": "z:/project-map-fixture/repo", "windows-mixed": "Z:\\project-map-fixture/repo"}
    wf = WorkflowyFixture([(UUID_A, A, "`" + forms[form] + "`.")])
    assert infer(wf, f"Work in `{repos.a}/missing/path.py`") == [A]
    query = next(call[2]["query"] for call in wf.calls if call[:2] == ("node", "search"))
    assert query.endswith(r"(?![\w\-/\\]|\.\w)")
    assert re.escape("project-map-fixture") in query


@pytest.mark.parametrize("path", ["src/module.py", "missing/file.py", "nested/missing.py"])
def test_nearest_existing_ancestor_and_nearest_git_folder_or_file(repos, path):
    root = repos.a + ("/nested" if path.startswith("nested/") else "")
    wf = WorkflowyFixture([(UUID_A, A, root)])
    assert infer(wf, f"Change `{repos.a}/{path}`") == [A]


def test_directory_spaces_and_unquoted_trailing_prose(repos):
    wf = WorkflowyFixture([(UUID_A, A, repos.spaced)])
    assert infer(wf, f"Work in {repos.spaced} for this job.") == [A]


def test_map_without_ap_id_keeps_uuid_and_alias_nodes_collapse(repos):
    wf = WorkflowyFixture([(UUID_A, "no anchor point available", repos.a), (UUID_A, None, repos.a)])
    assert infer(wf, f"`{repos.a}`") == [UUID_A]


@pytest.mark.parametrize("config", [{}, {"project_maps_location": None}, {"project_maps_location": ""},
                                   {"project_maps_location": "  "}, {"project_maps_location": 7}])
def test_no_location_is_nonblocking_and_skips_job_and_map_reads(config, capsys):
    wf = WorkflowyFixture(config=config)
    assert asyncio.run(oc_seat.infer_maps(socket="[ap:JOB]", wf=wf)) == []
    assert capsys.readouterr().out == "🗺️ no Project Map added (no location set)\n"
    assert len(wf.calls) == 1


def test_config_error_and_no_tab_never_raise(capsys):
    wf = WorkflowyFixture(error=RuntimeError("no Workflowy browser tab is connected\ntry later"))
    assert infer(wf, "anything") == []
    assert capsys.readouterr().out.splitlines() == [
        "🗺️ no Project Map added (location unreadable: no Workflowy browser tab is connected try later)"]


def test_connection_failure_never_blocks_start(monkeypatch, capsys):
    def unavailable():
        raise RuntimeError("offline")
    monkeypatch.setattr(oc_seat, "Workflowy", unavailable)
    assert asyncio.run(oc_seat.infer_maps("anything")) == []
    assert capsys.readouterr().out == "🗺️ no Project Map added (location unreadable: offline)\n"


def test_job_that_names_no_repository(capsys):
    wf = WorkflowyFixture()
    assert infer(wf, "Write prose about a relative/path; /tmp/no-repository is not a drive path.") == []
    assert capsys.readouterr().out == "🗺️ no Project Map added (the job names no repository)\n"
    assert len(wf.calls) == 1


def test_socket_note_is_the_direct_seat_job(repos, monkeypatch, capsys):
    wf = WorkflowyFixture([(UUID_A, A, repos.a)], job=f"Change `{repos.a}/src/module.py`.")
    monkeypatch.setattr(oc_seat, "Workflowy", lambda: wf)
    assert asyncio.run(oc_seat.infer_maps(socket="[ap:JOB]")) == [A]
    assert ("read_note", "[ap:JOB]") in wf.calls
    assert len(capsys.readouterr().out.splitlines()) == 1


@pytest.fixture
def seat_cli(monkeypatch):
    starts, defaults, inferred, resolved = [], [], [], []
    monkeypatch.setattr(oc_seat, "start_quick", lambda **kw: starts.append(kw) or "ses_fixture")
    monkeypatch.setattr(oc_seat, "program_quick_maps", lambda p: defaults.append(p) or [])
    monkeypatch.setattr(oc_seat, "resolve_maps", lambda handles: resolved.extend(handles) or [A, B])

    async def inference(*args, **kwargs):
        inferred.append((args, kwargs))
        return [A]
    monkeypatch.setattr(oc_seat, "infer_maps", inference)

    def cli(*flags):
        monkeypatch.setattr(sys, "argv", ["oc_seat.py", "quick", "--agent", "quick-ds41-flash", "--socket",
                                         "[ap:JOB]", "--no-wait", *flags])
        return oc_seat.main()
    return SimpleNamespace(cli=cli, starts=starts, defaults=defaults, inferred=inferred, resolved=resolved)


def test_fresh_seat_accepts_explicit_repeatable_maps_without_fork(seat_cli):
    assert seat_cli.cli("--map", "fixture-a", "--map", "fixture-b") == 0
    assert seat_cli.starts[-1]["map_handles"] == [A, B]
    assert seat_cli.resolved == ["fixture-a", "fixture-b"]
    assert seat_cli.defaults == seat_cli.inferred == []


def test_program_default_reaches_fresh_seat(seat_cli, monkeypatch):
    monkeypatch.setattr(oc_seat, "program_quick_maps", lambda p: [A, B] if p == "[ap:PROG]" else [])
    assert seat_cli.cli("--program", "[ap:PROG]") == 0
    assert seat_cli.starts[-1]["map_handles"] == [A, B]
    assert seat_cli.inferred == []


def test_fresh_seat_infers_only_without_explicit_or_program_maps(seat_cli):
    assert seat_cli.cli() == 0
    assert seat_cli.starts[-1]["map_handles"] == [A]
    assert seat_cli.inferred == [((), {"socket": "[ap:JOB]"})]


def test_unreadable_program_default_falls_back_without_refusing(seat_cli, monkeypatch):
    def unreadable(program):
        raise RuntimeError("offline")
    monkeypatch.setattr(oc_seat, "program_quick_maps", unreadable)
    assert seat_cli.cli("--program", "[ap:PROG]") == 0
    assert seat_cli.starts[-1]["map_handles"] == [A]


def test_no_map_skips_program_defaults_and_inference(seat_cli):
    assert seat_cli.cli("--program", "[ap:PROG]", "--no-map") == 0
    assert seat_cli.starts[-1]["map_handles"] == []
    assert seat_cli.defaults == seat_cli.inferred == seat_cli.resolved == []


@pytest.mark.parametrize("flags", [("--parent-seat", "ses_lead"), ("--helper",)])
def test_helpers_get_no_default_or_inferred_maps(seat_cli, flags):
    assert seat_cli.cli("--program", "[ap:PROG]", *flags) == 0
    assert seat_cli.starts[-1]["map_handles"] == []
    assert seat_cli.defaults == seat_cli.inferred == []
    assert seat_cli.cli(*flags, "--map", "fixture-a", "fixture-b") == 0
    assert seat_cli.starts[-1]["map_handles"] == [A, B]


def test_seat_map_and_no_map_are_exclusive(seat_cli):
    with pytest.raises(SystemExit) as exc:
        seat_cli.cli("--map", A, "--no-map")
    assert exc.value.code == 2
    assert seat_cli.starts == []


@pytest.mark.parametrize("route", ["fresh", "fork", "scouted"])
@pytest.mark.parametrize("omit", [False, True])
def test_quick_program_infers_before_writes_records_maps_and_passes_fresh_route(repos, tmp_path, monkeypatch,
                                                                            capsys, route, omit):
    job = tmp_path / "job.md"
    job.write_text(f"## Task\nChange `{repos.a}/src/module.py`.\n")
    wf = WorkflowyFixture([(UUID_A, A, repos.a)])
    monkeypatch.setattr(oc_quick, "Workflowy", lambda: wf)
    monkeypatch.setattr(oc_quick, "load_config", lambda: {})
    monkeypatch.setattr(oc_quick, "check_runtimes", lambda *a, **kw: None)
    starts, notes, events = [], [], []

    async def home(*args):
        events.append("write")
        config_calls = [call for call in wf.calls if call[:2] == ("system", "config.get")]
        assert len(config_calls) == (0 if omit else 1)
        return "[ap:ROOT01]", "root-uuid", "[ap:JOB001]"

    async def write(root, note, *args):
        notes.append(note)
        return True

    monkeypatch.setattr(oc_quick, "create_home", home)
    monkeypatch.setattr(oc_quick, "write_root_and_board", write)
    monkeypatch.setattr(oc_seat, "start_quick", lambda **kw: starts.append(kw) or "ses_worker")
    monkeypatch.setattr(oc_seat, "start_quick_fork", lambda **kw: starts.append(kw) or "ses_worker")
    monkeypatch.setattr(oc_seat, "refuse_claude_fork", lambda *args: None)
    monkeypatch.setattr(oc_quick, "resolve_sid", lambda sid: sid)
    monkeypatch.setattr(oc_quick, "oc_messages", lambda sid: [{"id": "msg_boundary"}])
    monkeypatch.setattr(oc_quick.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0,
                         stdout="sid: cc-12345678-1234-1234-1234-123456789abc", stderr=""))
    flags = ["--fork-from", "ses_mgr", "--before", "msg_boundary"] if route == "fork" else []
    if omit:
        flags += ["--no-map"]
    monkeypatch.setattr(sys, "argv", ["oc_quick.py", "--parent", "[ap:PARENT]", "--title", "fixture", "--job",
                                     str(job), "--route", route, "--authority", "Dan: go", "--board", "", *flags])
    assert oc_quick.main() == 0
    expected = [] if omit else [A]
    policy, _ = oc_common.quick_policy(notes[0])
    assert policy["maps"] == expected and policy["map"] == (None if omit else A)
    assert events == ["write"]
    assert starts == [] if route == "scouted" else starts[-1]["map_handles"] == expected
    assert ("🗺️ Project Map" in capsys.readouterr().out) is not omit


def test_quick_program_dry_run_shows_inferred_maps_without_writes(repos, tmp_path, monkeypatch, capsys):
    job = tmp_path / "job.md"
    job.write_text(f"Work in `{repos.a}`")
    wf = WorkflowyFixture([(UUID_A, A, repos.a)])
    monkeypatch.setattr(oc_quick, "Workflowy", lambda: wf)
    monkeypatch.setattr(oc_quick, "load_config", lambda: {})
    monkeypatch.setattr(oc_quick, "check_runtimes", lambda *args, **kwargs: None)
    monkeypatch.setattr(sys, "argv", ["oc_quick.py", "--parent", "[ap:PARENT]", "--title", "fixture", "--job",
                                     str(job), "--route", "scouted", "--authority", "Dan: go", "--board", "", "--dry-run"])
    assert oc_quick.main() == 0
    out = capsys.readouterr().out
    assert oc_common.quick_policy(out)[0]["maps"] == [A]
    assert f"maps {A} · continuations" in out and "DRY RUN — nothing written" in out
    assert not any(call[:2] in (("node", "edit"), ("anchor", "dock.create")) for call in wf.calls)
