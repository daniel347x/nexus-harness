"""The snapshot's advisory 🌳 Git worktrees section, against a throwaway repository: offline, nothing removed."""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oc_sessions as S


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def commit(cwd, name, text):
    (Path(cwd) / name).write_text(text)
    git(cwd, "add", name)
    git(cwd, "commit", "-q", "-m", f"add {name}")


@pytest.fixture
def repos(tmp_path, monkeypatch):
    """root/proj (main) with five linked worktrees: landed+clean, landed+dirty, unique work, rebased, detached."""
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@t")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@t")
    p = tmp_path / "proj"
    p.mkdir()
    git(p, "init", "-q", "-b", "main")
    commit(p, "a.txt", "a\n")
    wt = {k: tmp_path / f"proj-wt-{k}" for k in ("landed", "dirty", "unique", "rebased", "detached")}
    git(p, "worktree", "add", "-q", "-b", "landed", str(wt["landed"]))
    git(p, "worktree", "add", "-q", "-b", "dirty", str(wt["dirty"]))
    (wt["dirty"] / "scratch.txt").write_text("x\n")
    git(p, "worktree", "add", "-q", "-b", "unique", str(wt["unique"]))
    commit(wt["unique"], "u.txt", "u\n")
    git(p, "worktree", "add", "-q", "-b", "rebased", str(wt["rebased"]))
    commit(wt["rebased"], "r.txt", "r\n")
    commit(p, "m.txt", "m\n")                                      # main moves on, then takes r.txt by cherry-pick
    rsha = subprocess.run(["git", "-C", str(wt["rebased"]), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    git(p, "cherry-pick", rsha)
    git(p, "worktree", "add", "-q", "--detach", str(wt["detached"]), "main")
    return tmp_path, wt


def by_branch(rows):
    return {r["branch"].split()[0] if r["branch"].startswith("detached") else r["branch"]: r for r in rows}


def test_rows_classify_each_worktree(repos):
    root, wt = repos
    sessions = [{"id": "ses_owner1", "title": "glare job", "location": {"directory": str(wt["dirty"])}}]
    rows, errors = S.worktree_rows(sessions, repos=("proj",), root=str(root))
    assert errors == [] and len(rows) == 5                          # the primary checkout is never a row
    r = by_branch(rows)
    assert r["landed"]["landed"] == "in main" and r["landed"]["dirty"] == "clean"
    assert r["landed"]["command"] == f'git -C "{root}/proj" worktree remove "{wt["landed"]}"'
    assert r["landed"]["owner"] == "UNKNOWN"
    assert r["dirty"]["in_main"] and r["dirty"]["dirty"] == "1 untracked path(s)" and r["dirty"]["command"] is None
    assert r["dirty"]["owner"].startswith("`ses_owner1` glare job")
    assert r["unique"]["landed"] == "not in main: 1 unique commit(s)" and r["unique"]["command"] is None
    assert r["rebased"]["landed"].startswith("in main by patch") and r["rebased"]["command"]
    assert r["detached"]["in_main"] and r["detached"]["command"]


def test_advisory_only_nothing_removed(repos):
    root, wt = repos
    rows, errors = S.worktree_rows([], repos=("proj",), root=str(root))
    tree = S.worktrees_tree(rows, errors)
    assert all(p.is_dir() for p in wt.values())                     # shown, never removed
    assert tree["name"] == "🌳 Git worktrees · 5 linked · 3 landed and clean"
    assert "never removed by this script" in tree["note"]
    assert tree["note"].count("worktree remove") == 4               # 3 commands + the note's own mention
    assert set(S.etchable(tree)) == {"name", "note"}


def test_prunable_checkout_gets_no_command(repos):
    root, wt = repos
    (wt["landed"] / ".git").unlink()                                # a half-removed checkout: git calls it prunable
    r = by_branch(S.worktree_rows([], repos=("proj",), root=str(root))[0])
    assert r["landed"]["dirty"].startswith("prunable") and r["landed"]["command"] is None


def test_missing_repo_is_reported_not_fatal(tmp_path):
    rows, errors = S.worktree_rows([], repos=("nope",), root=str(tmp_path))
    assert rows == [] and errors and errors[0].startswith("nope: git worktree list failed")
    assert "## Not read" in S.worktrees_tree(rows, errors)["note"]
    assert S.worktrees_tree([], []) is None


def test_parse_worktree_list():
    text = ("worktree /r/p\nHEAD aaa\nbranch refs/heads/main\n\n"
            "worktree /r/p-wt\nHEAD bbb\ndetached\nlocked busy\n\n"
            "worktree /r/p-x\nHEAD ccc\nbranch refs/heads/exp/x\nprunable gitdir file points to non-existent location\n")
    ws = S.parse_worktree_list(text)
    assert [w["worktree"] for w in ws] == ["/r/p", "/r/p-wt", "/r/p-x"]
    assert ws[1]["branch"] is None and "detached" in ws[1] and ws[1]["locked"] == "busy"
    assert ws[2]["branch"] == "exp/x" and "prunable" in ws[2]
