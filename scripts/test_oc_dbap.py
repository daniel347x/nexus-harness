"""Offline transport stubs only: these tests never contact a host or database."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import oc_dbap


LAUNCHER = Path(__file__).with_name("oc.sh")
SQL = b"-- quotes and psql syntax stay literal\r\nSELECT 'it''s $HOME \\ text';\r\n\\pset pager off\n"
STUB = r'''
import hashlib, json, os, pathlib, sys, time
kind = pathlib.Path(sys.argv[0]).name
root = pathlib.Path(os.environ["STUB_ROOT"])
args = sys.argv[1:]
remote = args[-1].split(":", 1)[-1] if kind == "scp" else args[-1].split()[-1]
target = root / pathlib.Path(remote).name
if kind == "scp":
    content = pathlib.Path(args[-2]).read_bytes()
    event = {"kind": kind, "remote": remote, "sha256": hashlib.sha256(content).hexdigest(), "args": args}
else:
    content = target.read_bytes()  # fails loudly if execute beat transfer
    event = {"kind": kind, "remote": remote, "sha256": hashlib.sha256(content).hexdigest(), "args": args}
with (root / "events").open("a") as f:
    f.write(json.dumps(event) + "\n")
if os.environ.get("STUB_FAIL") == kind:
    print(kind + " failed")
    sys.exit(7)
if os.environ.get("STUB_HANG") == kind:
    print("partial " + kind, flush=True)
    time.sleep(10)
if kind == "scp":
    target.write_bytes(content)
else:
    print("SQL SUBMITTED")
    sys.stdout.flush()
    sys.stdout.buffer.write(content)
    sys.stdout.buffer.flush()
    print("\nOUTPUT")
    print("ERROR: stub SQL error" if os.environ.get("STUB_SQL_ERROR") else "query result")
'''


@pytest.fixture
def harness(tmp_path):
    binaries = tmp_path / "bin"
    binaries.mkdir()
    remote = tmp_path / "remote"
    remote.mkdir()
    for name in ("scp", "ssh"):
        path = binaries / name
        path.write_text(f"#!{sys.executable}\n" + STUB)
        path.chmod(0o700)
    env = dict(os.environ, PATH=f"{binaries}:{os.environ['PATH']}", STUB_ROOT=str(remote),
               OC_PY=sys.executable, HOME=str(tmp_path / "no-config"))
    local = tmp_path / "retained files ' $"

    def run(data=SQL, *args, **changes):
        return subprocess.run(["bash", str(LAUNCHER), "dbap", "--local-dir", str(local), *args],
                              input=data, capture_output=True, env=dict(env, **changes), timeout=15)

    def events():
        path = remote / "events"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    return run, events, local, tmp_path


def test_order_bytes_audit_and_noninteractive_options(harness):
    run, events, local, _ = harness
    result = run()
    assert result.returncode == 0, result.stderr
    rows = events()
    assert [row["kind"] for row in rows] == ["scp", "ssh"]
    assert rows[0]["remote"] == rows[1]["remote"]
    assert rows[0]["sha256"] == rows[1]["sha256"] == hashlib.sha256(SQL).hexdigest()
    directory, = local.iterdir()
    query, = directory.glob("*.sql")
    assert query.read_bytes() == SQL
    audit = json.loads((directory / "audit.json").read_text())
    assert audit["phase"] == "completed" and audit["sql_status"] == "unverified"
    assert (directory / "output.log").read_bytes() == result.stdout
    assert b"SQL success is NOT verified" in result.stderr
    for row in rows:
        assert "BatchMode=yes" in row["args"] and "StrictHostKeyChecking=yes" in row["args"]
        assert "ConnectTimeout=10" in row["args"]
    assert "-B" in rows[0]["args"]
    assert "-n" in rows[1]["args"] and "-T" in rows[1]["args"]


def test_existing_file_snapshot_and_ambiguity(harness):
    run, events, local, tmp = harness
    source = tmp / "input ' $ file.sql"
    source.write_bytes(SQL)
    assert run(b"", "--file", str(source)).returncode == 0
    query, = local.glob("*/*.sql")
    assert query.read_bytes() == source.read_bytes() == SQL
    assert run(SQL, "--file", str(source)).returncode != 0
    assert len(events()) == 2


def test_quoted_heredoc_in_one_shell_call(harness):
    _, _, local, tmp = harness
    env = dict(os.environ, PATH=f"{tmp / 'bin'}:{os.environ['PATH']}",
               STUB_ROOT=str(tmp / "remote"), OC_PY=sys.executable)
    import shlex
    sql = "SELECT '$HOME', 'it''s quoted', '\\path';\n"
    command = (f"bash {shlex.quote(str(LAUNCHER))} dbap --local-dir {shlex.quote(str(local))} <<'SQL'\n"
               + sql + "SQL\n")
    result = subprocess.run(["bash", "-c", command], capture_output=True, env=env, timeout=15)
    assert result.returncode == 0, result.stderr
    query, = local.glob("*/*.sql")
    assert query.read_bytes() == sql.encode()


@pytest.mark.parametrize("data", [b"", b" \t\r\n", b"SELECT \x00", b"\xff"])
def test_invalid_sql_never_transfers(harness, data):
    run, events, local, _ = harness
    assert run(data).returncode != 0
    assert events() == [] and not local.exists()


@pytest.mark.parametrize("flag,value", [("--run-timeout", "0"), ("--transfer-timeout", "-1"),
                                        ("--run-timeout", "nan"), ("--run-timeout", "inf")])
def test_invalid_timeout(harness, flag, value):
    run, events, _, _ = harness
    assert run(SQL, flag, value).returncode == 2
    assert not events()


def test_missing_file_and_write_failure(harness):
    run, events, local, tmp = harness
    assert run(b"", "--file", str(tmp / "absent.sql")).returncode != 0
    local.write_text("not a directory")
    assert run().returncode != 0
    assert not events()


def test_fsync_failure_prevents_transport(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(SQL)))
    monkeypatch.setattr(oc_dbap.os, "fsync", lambda _: (_ for _ in ()).throw(OSError("write failed")))
    monkeypatch.setattr(oc_dbap.subprocess, "run", lambda *a, **k: pytest.fail("transport after failed write"))
    old_umask = os.umask(0o077)
    try:
        assert oc_dbap.main(["--local-dir", str(tmp_path)]) == 1
    finally:
        os.umask(old_umask)
    assert "write failed" in capsys.readouterr().err


@pytest.mark.parametrize("kind,expected", [("scp", ["scp"]), ("ssh", ["scp", "ssh"])])
def test_failed_transport(harness, kind, expected):
    run, events, local, _ = harness
    result = run(STUB_FAIL=kind)
    assert result.returncode != 0
    assert [row["kind"] for row in events()] == expected
    audit, = local.glob("*/audit.json")
    assert json.loads(audit.read_text())["transport_returncode"] == 7
    assert (b"before any retry" in result.stderr) == (kind == "ssh")


@pytest.mark.parametrize("kind,flag,expected", [("scp", "--transfer-timeout", ["scp"]),
                                               ("ssh", "--run-timeout", ["scp", "ssh"])])
def test_timeout_retains_partial_output(harness, kind, flag, expected):
    run, events, _, _ = harness
    result = run(SQL, flag, "0.3", STUB_HANG=kind)
    assert result.returncode == 124
    assert [row["kind"] for row in events()] == expected
    assert ("partial " + kind).encode() in result.stdout
    assert (b"does not cancel SQL" in result.stderr) == (kind == "ssh")


def test_sql_error_rc0_is_not_reported_as_success(harness):
    run, _, local, _ = harness
    result = run(STUB_SQL_ERROR="1")
    assert result.returncode == 0  # documented completion only, deliberately no error heuristic
    assert b"ERROR: stub SQL error" in result.stdout
    assert b"SQL success is NOT verified" in result.stderr
    audit, = local.glob("*/audit.json")
    assert json.loads(audit.read_text())["sql_status"] == "unverified"


def test_simultaneous_invocations_cannot_share_sql(harness):
    run, events, local, _ = harness
    queries = [b"SELECT 'one';", b"SELECT 'two';"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, queries))
    assert all(result.returncode == 0 for result in results)
    rows = events()
    paths = {row["remote"] for row in rows}
    assert len(paths) == 2
    for path in paths:
        pair = [row for row in rows if row["remote"] == path]
        assert [row["kind"] for row in pair] == ["scp", "ssh"]
        assert pair[0]["sha256"] == pair[1]["sha256"]
    assert sorted(path.read_bytes() for path in local.glob("*/*.sql")) == sorted(queries)
