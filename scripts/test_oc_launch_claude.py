"""The Claude Code Conductor (overnight build U2a), offline: the backend-neutral registry binding (decision D4) and every
reader of it, oc_launch's Claude path end to end (reserve or adopt → launch with labels → verify before the ignition →
acknowledged → bind `execution:`), self-resume (a new id under a new number) against crash resume (the host resumes the
same id and the row stays bound), the one-active rule across both backends, and the old OpenCode rows and launches
unchanged. A fake Workflowy holds the root note, a scripted fake host stands in for the Claude host, and OpenCode is
patched: nothing reaches a live service."""
import asyncio
import functools
import io
import json
import re
from contextlib import redirect_stdout
from types import SimpleNamespace

import pytest

import oc_backend
import oc_binding
import oc_claude
import oc_common
import oc_doorbell
import oc_launch
import oc_quick
from oc_pins import SessionPins
import oc_seal
import oc_steer
from claude_headless import seat_prompt as sp
from test_oc_claude import FakeHost, eid_of, live

PROG = "[ap:PROG01]"
AUTH = "[ap:AUTH01]"
OLD_ROW = (
    "    - number: 1\n"
    "      opencode_session_id: \"ses_old1\"   # bound by oc_launch.py 2026-10-01 · title and model verified by GET · "
    "ignition delivered · model openai/gpt-6.1-sol variant high\n"
    "      entry_action: seeded\n"
    "      human_present: false\n"
    f"      authority: \"{AUTH}\"\n"
    "      state: stopped\n"
    "      report: null\n"
    "      briefs: []\n")


def root_note(rows: str = OLD_ROW) -> str:
    return ("A program root.\n\n```yaml\nprogram_interface:\n  schema_version: 1\n  seeded_by: human\n  seed: null\n"
            "  seal: null\n  sessions:\n" + rows + "```\n")


RESERVED_ROW = (
    "    - number: 2\n"
    "      opencode_session_id: null   # reserved by hand at seeding\n"
    "      entry_action: resume\n"
    "      human_present: false\n"
    f"      authority: \"{AUTH}\"\n"
    "      state: registered\n"
    "      report: null\n"
    "      briefs: []\n")


class FakeWorkflowy:
    """The program root as one note: read_note and exact single-hit search/replace edits, like node.edit."""

    def __init__(self, note: str):
        self.name = f"[name: not editable] {PROG} 🧪 PROGRAM: Claude Conductor test — scratch"
        self.note = note
        self.edits = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def read_note(self, handle):
        return self.name, self.note

    async def call(self, family, verb, params):
        assert (family, verb) == ("node", "edit")
        assert self.note.count(params["note_search"]) == 1
        self.note = self.note.replace(params["note_search"], params["note_replace"], 1)
        self.edits.append(params)
        return json.dumps({"success": True})


class ConductorHost(FakeHost):
    """FakeHost whose launch record also carries what a real host's does for a Conductor."""

    def m_launch(self, native_uuid, **p):
        super().m_launch(native_uuid, **p)
        eid = "cc-" + native_uuid
        self.records[eid].update(program=p.get("program"), context_target=p.get("context_target"), cwd=p.get("cwd"),
                                 agent=p.get("agent"))
        self.system_prompt = p.get("system_prompt")
        return dict(self.records[eid])


@pytest.fixture
def env(tmp_path, monkeypatch):
    """One fake Workflowy root, one fake Claude host, OpenCode with no sessions; the creation calls refuse unless a
    test installs them (a Claude launch must never create an OpenCode session)."""
    host = ConductorHost(tmp_path)
    wf = FakeWorkflowy(root_note())
    pins_path = tmp_path / 'private' / 'pins.json'
    config = tmp_path / 'history-settings.json'
    config.write_text(json.dumps({'pins_path': str(pins_path)}))
    monkeypatch.setenv('OC_SESSION_HISTORY_CONFIG', str(config))
    ns = SimpleNamespace(host=host, wf=wf, sessions=[], status={}, created=[], pins=SessionPins(pins_path))
    monkeypatch.setattr(oc_claude, "connect", lambda *a, **k: host)
    monkeypatch.setattr(oc_claude, "start", functools.partial(oc_claude.start, sleep=lambda s: None))
    monkeypatch.setattr(oc_launch, "Workflowy", lambda: wf)
    monkeypatch.setattr(oc_common, "Workflowy", lambda: wf)
    monkeypatch.setattr(oc_launch, "oc_sessions", lambda: ns.sessions)
    monkeypatch.setattr(oc_launch, "oc_status", lambda: (True, dict(ns.status)))
    monkeypatch.setattr(oc_common, "oc_sessions", lambda **k: ns.sessions)
    monkeypatch.setattr(oc_launch, "CONDUCTOR_CWD", str(tmp_path))

    def refuse(*a, **k):
        raise AssertionError("an OpenCode session call was made")
    for name in ("oc_create_session", "oc_prompt", "confirm_prompt", "oc_update_session", "oc_request"):
        monkeypatch.setattr(oc_launch, name, refuse)
    return ns


def args(**kw):
    base = dict(program=PROG, seeded=False, resume=True, brief=[], authority=AUTH, model="anthropic/claude-opus-5-5",
                self_resume=False, variant="xhigh", target=None, quick=False, human_present=False, name=None,
                force=False, dry_run=False)
    base.update(kw)
    return SimpleNamespace(**base)


def launch(**kw) -> tuple[int, str]:
    out = io.StringIO()
    with redirect_stdout(out):
        code = asyncio.run(oc_launch.run(args(**kw)))
    return code, out.getvalue()


def refused(**kw) -> tuple[object, str]:
    out = io.StringIO()
    with redirect_stdout(out), pytest.raises(SystemExit) as e:
        asyncio.run(oc_launch.run(args(**kw)))
    return e.value.code, out.getvalue()


def rows_of(note: str) -> dict:
    return {int(r["number"]): r for r in oc_common.registry_rows(note)}


# ------------------------------------------------------------------------------------ the binding (D4)

def test_row_binding_reads_both_kinds_and_old_rows_are_unchanged():
    B = oc_binding.row_binding
    assert B({"opencode_session_id": "ses_a", "state": "stopped"}) == ("opencode", "ses_a")
    assert B({"opencode_session_id": None, "state": "registered"}) is None
    assert B({"execution": {"backend": "claude", "id": eid_of(3)}}) == ("claude", eid_of(3))
    assert B({"opencode_session_id": None, "execution": {"backend": "claude", "id": eid_of(3)}}) == ("claude", eid_of(3))
    assert B({"execution": {"backend": "opencode", "id": "ses_b"}}) == ("opencode", "ses_b")
    assert B({"opencode_session_id": eid_of(4)}) == ("claude", eid_of(4))           # a cc- id hand-written there
    for bad in ({"execution": {"backend": "claude", "id": "ses_x"}}, {"execution": {"backend": "claude"}},
                {"execution": "cc-123"}, {"execution": {"backend": "claude", "id": eid_of(1)},
                                           "opencode_session_id": "ses_other"}):
        assert B(bad)[0] == "invalid" and oc_binding.row_session_id(bad) is None
        assert not oc_binding.is_reservation({**bad, "state": "registered"})          # bound to something: never free
    assert oc_binding.is_reservation({"opencode_session_id": None, "state": "registered"})
    assert oc_binding.is_reservation({"number": 3})                                   # state defaults to registered
    assert not oc_binding.is_reservation({"opencode_session_id": None, "state": "stopped"})   # legacy unknown id
    assert not oc_binding.is_reservation({"execution": {"backend": "claude", "id": eid_of(2)}, "state": "registered"})


def test_newest_live_row_sees_claude_rows_only_when_asked():
    rows = [{"number": 1, "opencode_session_id": "ses_a"},
            {"number": 2, "execution": {"backend": "claude", "id": eid_of(2)}},
            {"number": 3, "opencode_session_id": None}]
    by_id = {"ses_a": {}}
    assert oc_common.newest_live_row(rows, by_id)["number"] == 1                      # OpenCode-only callers
    assert oc_common.newest_live_row(rows, by_id, claude_live=lambda i: True)["number"] == 2
    assert oc_common.newest_live_row(rows, by_id, claude_live=lambda i: False)["number"] == 1


def test_claude_live_checker_never_calls_a_silent_host_gone(tmp_path, monkeypatch):
    host = FakeHost(tmp_path, live(2))
    monkeypatch.setattr(oc_claude, "connect", lambda *a, **k: host)
    check = oc_common.claude_live_checker()
    assert check(eid_of(2)) and not check(eid_of(9))
    def down(*a, **k):
        raise oc_backend.SeatError("❌ no Claude host", oc_backend.EXIT_NO_HOST)
    monkeypatch.setattr(oc_claude, "connect", down)
    sock = tmp_path / "host.sock"
    monkeypatch.setenv("OC_CLAUDE_HOST_SOCKET", str(sock))
    assert oc_common.claude_live_checker()(eid_of(9)) is False                         # no socket file: none can exist
    sock.write_text("")
    assert oc_common.claude_live_checker()(eid_of(9)) is True                          # a silent host: unknown, live


# ------------------------------------------------------------------------------------ the Claude path end to end

def test_claude_conductor_launch_reserves_launches_verifies_ignites_and_binds(env):
    before_old_row = env.wf.note[env.wf.note.index("    - number: 1"):env.wf.note.index("    - number: 1") + len(OLD_ROW)]
    code, out = launch(target=0.4, name="scratch")
    assert code == 0, out
    rec = next(iter(env.host.records.values()))
    eid = rec["execution_id"]
    p = env.host.launched
    assert (p["role"], p["cwd"], p["model"], p["effort"], p["agent"], p["program"], p["context_target"]) == (
        "conductor", oc_launch.CONDUCTOR_CWD, "claude-opus-5-5", "xhigh", "conductor", PROG, 0.4)
    assert p["socket_handle"] is None and p["parent"] is None
    labels = p["labels"]
    assert labels["title"] == f"{PROG}-002 · scratch" and labels["kind"] == "conductor" and labels["registry_n"] == 2
    assert labels["model_ref"] == "anthropic/claude-opus-5-5#xhigh" and labels["quick_program"] is False
    assert env.host.system_prompt == sp.compose_conductor_prompt()["text"]            # the composed replace-mode prompt
    assert env.host.sent == [oc_launch.compose_ignition(PROG, True, 2, False, [])]     # the same fixed ignition
    rows = rows_of(env.wf.note)
    assert oc_binding.row_binding(rows[2]) == ("claude", eid)
    assert rows[2].get("opencode_session_id") is None and rows[2]["state"] == "registered"
    line = oc_launch.binding_line(env.wf.note, 2)
    assert line.endswith("model anthropic/claude-opus-5-5 variant xhigh") and "execution: {backend: claude" in line
    assert oc_launch.model_from_binding(line) == ("anthropic/claude-opus-5-5", "xhigh")
    assert before_old_row in env.wf.note                                               # the old row, byte for byte
    assert oc_quick.printed_sid(out) == eid and f"sid: {eid}" in out
    assert "VERIFY title ok" in out and "ignition acknowledged" in out and "Claude Code on Max" in out
    assert len(env.wf.edits) == 2                                                      # reserve, bind


@pytest.mark.parametrize("explicit", [False, True])
def test_claude_conductor_title_keeps_the_complete_program_label(env, explicit):
    label = "Millions of events: keep the Claude host and the Manager App fast as the stores grow"
    env.wf.name = f"[name: not editable] {PROG} 📦 PROGRAM: {label} — implementation details #prog-root"
    code, out = launch(name=label if explicit else None)
    assert code == 0, out
    assert env.host.launched["labels"]["title"] == f"{PROG}-002 · {label}"


def test_launched_by_names_whoever_runs_the_launch(env, monkeypatch):
    """A forklift (its oc.sh quick runs oc_launch with the inherited env) is found by this label (UnitSpec P8)."""
    monkeypatch.setattr(oc_doorbell, "nearest_runtime", lambda env: "claude")
    L, cc = oc_launch.launched_by, eid_of(5)
    assert L({"CC_CONTEXT_EXECUTION_ID": cc, "OPENCODE_SESSION_ID": "ses_x"}) == cc
    assert L({"CC_CONTEXT_EXECUTION_ID": "cc-1234", "OPENCODE_SESSION_ID": "ses_x"}) == "ses_x"
    assert L({"OPENCODE_SESSION_ID": "msg_x"}) is None and L({}) is None
    monkeypatch.setenv("CC_CONTEXT_EXECUTION_ID", cc)
    assert launch()[0] == 0
    assert env.host.launched["labels"]["launched_by"] == cc
    assert oc_launch.opencode_conductor_metadata(program=PROG, n=2, quick=True, target=None)["launched_by"] == cc
    monkeypatch.delenv("CC_CONTEXT_EXECUTION_ID")
    monkeypatch.setenv("OPENCODE_SESSION_ID", "ses_sol")
    assert oc_launch.opencode_conductor_metadata(program=PROG, n=2, quick=True, target=None)["launched_by"] == "ses_sol"
    monkeypatch.delenv("OPENCODE_SESSION_ID")
    assert oc_launch.claude_conductor_labels(title="t", program=PROG, n=3, entry_action="resume", quick=False,
                                             choice={"model_ref": "m", "effort": "high"},
                                              composed={"kind": "k", "prompt_sha256": "0" * 64})["launched_by"] is None


@pytest.mark.parametrize("runtime", ["opencode", "claude"])
def test_forklift_launch_provenance_uses_its_own_runtime_when_both_ids_were_inherited(env, monkeypatch, runtime):
    cc, sid = eid_of(5), "ses_native_forklift"
    monkeypatch.setenv("CC_CONTEXT_EXECUTION_ID", cc)
    monkeypatch.setenv("OPENCODE_SESSION_ID", sid)
    monkeypatch.setattr(oc_doorbell, "nearest_runtime", lambda env: runtime)
    expected = sid if runtime == "opencode" else cc
    assert oc_launch.launched_by() == expected
    assert oc_launch.opencode_conductor_metadata(program=PROG, n=2, quick=True, target=None)["launched_by"] == expected
    assert oc_launch.claude_conductor_labels(title="t", program=PROG, n=3, entry_action="seeded", quick=True,
                choice={"model_ref": "m", "effort": "high"},
                composed={"kind": "k", "prompt_sha256": "0" * 64})["launched_by"] == expected


def test_quick_program_reservation_is_adopted_and_bound_by_a_claude_conductor(env):
    env.wf.note = oc_quick.root_note_text(
        root=PROG, job="[ap:JOB001]", parent="ad469515", route="fresh", map_handles=[], worker="quick-opus55-high",
        fork_from=None, before=None, continuations=2, authority=AUTH, state="running", worker_sid=eid_of(7),
        record=None, now="2026-10-15 12:00", day="2026-10-15")
    code, out = launch(seeded=True, resume=False, quick=True, variant="high")
    assert code == 0, out
    assert "ADOPTING reserved row -001" in out and len(env.wf.edits) == 1               # no new row: only the bind
    assert env.host.sent[0].startswith("You are the CONDUCTOR. Read [ap:XUWU84] and follow it. Quick program at")
    rows = rows_of(env.wf.note)
    assert list(rows) == [1] and oc_binding.row_backend(rows[1]) == "claude"
    assert env.host.launched["labels"]["quick_program"] is True


def test_self_resume_seats_a_new_claude_id_under_a_new_number_crash_resume_keeps_the_same(env, monkeypatch):
    assert launch(target=0.4)[0] == 0
    first = rows_of(env.wf.note)[2]
    eid2 = oc_binding.row_session_id(first)
    # crash resume: the HOST resumes the same conversation (same id, next generation); the registry is untouched
    note_before = env.wf.note
    env.host.call("resume", execution_id=eid2)
    assert env.host.records[eid2]["generation"] == 2 and env.wf.note == note_before
    assert asyncio.run(oc_common.current_conductor(PROG)) == (eid2, 2)
    # the caller is busy in its own turn (it runs the command); self-resume reads model, level and target from its row
    env.host.records[eid2].update(state="ready", setting_control_pending=False, native_session_state="running")
    code, out = launch(self_resume=True, model=None, variant=None, authority=None)
    assert code == 0, out
    rows = rows_of(env.wf.note)
    eid3 = oc_binding.row_session_id(rows[3])
    assert eid3.startswith("cc-") and eid3 != eid2 and oc_binding.row_backend(rows[3]) == "claude"
    assert rows[2]["state"] == "stopped" and "handed off to -003" in env.wf.note
    assert oc_binding.row_session_id(rows[2]) == eid2                                 # the predecessor stays bound
    p = env.host.launched
    assert (p["model"], p["effort"], p["context_target"]) == ("claude-opus-5-5", "xhigh", 0.4)
    assert rows[3]["authority"] == AUTH
    assert asyncio.run(oc_common.current_conductor(PROG)) == (eid3, 3)


def test_self_resume_falls_back_to_the_host_record_when_the_comment_is_gone(env):
    assert launch()[0] == 0
    eid2 = oc_binding.row_session_id(rows_of(env.wf.note)[2])
    env.wf.note = re.sub(r"(execution: \{[^}]*\})[^\n]*", r"\1", env.wf.note)       # a hand-trimmed comment
    code, out = launch(self_resume=True, model=None, variant=None, authority=None)
    assert code == 0, out
    assert (env.host.launched["model"], env.host.launched["effort"]) == ("claude-opus-5-5", "xhigh")
    assert oc_binding.row_session_id(rows_of(env.wf.note)[3]) != eid2


# ------------------------------------------------------------------------------------ one active Conductor

def test_a_busy_or_recovering_claude_conductor_refuses_a_second_on_either_backend(env, monkeypatch):
    assert launch()[0] == 0
    eid2 = oc_binding.row_session_id(rows_of(env.wf.note)[2])
    rec = env.host.records[eid2]
    rec.update(state="ready", setting_control_pending=False, pending_message_uuids=[], native_session_state="idle",
               background_tasks={"t1": {"description": "oc_seat.sh wait"}})        # waiting in background: busy
    note = env.wf.note
    launched_before = len(env.host.records)
    code, out = refused(model="openai/gpt-6.1-sol", variant="high")
    assert "Claude Conductor" in out and "still BUSY" in out and env.wf.note == note
    rec.update(background_tasks={}, pending_recovery="rec-1")                          # a crash recovery is scheduled
    code, out = refused()
    assert "still BUSY" in out and env.wf.note == note and len(env.host.records) == launched_before
    rec.update(pending_recovery=None, owner="unknown", state="ownership_unknown")    # the host lost sight of it
    code, out = refused()
    assert "still BUSY" in out and env.wf.note == note


def test_a_busy_opencode_conductor_refuses_a_claude_launch_and_nothing_is_written(env):
    env.wf.note = root_note(OLD_ROW.replace("state: stopped", "state: running"))
    env.sessions = [{"id": "ses_old1", "title": f"{PROG}-001 · x"}]
    env.status = {"ses_old1": "busy"}
    note = env.wf.note
    code, out = refused()
    assert "ses_old1 is still BUSY" in out and env.wf.note == note and not env.host.records


def test_a_silent_host_makes_a_claude_row_unknown_and_refuses_an_opencode_launch(env, monkeypatch, tmp_path):
    assert launch()[0] == 0
    note = env.wf.note
    def down(*a, **k):
        raise oc_backend.SeatError("❌ no answer", oc_backend.EXIT_UNKNOWN)
    monkeypatch.setattr(oc_claude, "connect", down)
    sock = tmp_path / "live.sock"
    sock.write_text("")
    monkeypatch.setenv("OC_CLAUDE_HOST_SOCKET", str(sock))
    code, out = refused(model="openai/gpt-6.1-sol", variant="high")
    assert "UNKNOWN whether it still runs" in out and env.wf.note == note


def test_a_title_already_on_the_host_is_out_of_sync(env):
    env.host.records[eid_of(5)] = live(5, labels={"title": f"{PROG}-002 · stray", "kind": "conductor"},
                                       role="conductor")
    code, _ = refused()
    assert "Claude host are OUT OF SYNC" in str(code) and not env.wf.edits


def quick_root(env):
    env.wf.note = oc_quick.root_note_text(
        root=PROG, job="[ap:JOB001]", parent="ad469515", route="fresh", map_handles=[], worker="quick-opus55-high",
        fork_from=None, before=None, continuations=2, authority=AUTH, state="running", worker_sid=eid_of(7),
        record=None, now="2026-10-15 12:00", day="2026-10-15")


def test_oc_quicks_worker_carrying_001_before_its_conductor_is_never_a_clash(env):
    """oc_quick starts its worker (titled `[ap:CORE]-001 ▸ ⚡ quick worker · …`, conductor_n=1) BEFORE the Conductor:
    on either backend that worker is not a second Conductor for row 001."""
    quick_root(env)
    env.host.records[eid_of(7)] = live(7, labels={"title": f"{PROG}-001 ▸ ⚡ quick worker · [ap:JOB001]", "kind": "seat",
                                                  "quick": True, "conductor_n": 1})
    code, out = launch(seeded=True, resume=False, quick=True, variant="high")
    assert code == 0, out
    assert "ADOPTING reserved row -001" in out and oc_binding.row_backend(rows_of(env.wf.note)[1]) == "claude"
    quick_root(env)                                                                    # the same with an OpenCode worker
    env.host.records.clear()
    env.host.sent.clear()
    env.sessions = [{"id": "ses_qw1", "title": f"{PROG}-001 ▸ ⚡ quick worker · [ap:JOB001]",
                     "metadata": {"kind": "seat", "quick": True, "program": PROG, "conductor_n": 1}}]
    code, out = launch(seeded=True, resume=False, quick=True, variant="high")
    assert code == 0, out
    assert "ADOPTING reserved row -001" in out and "already carries" not in out


@pytest.mark.parametrize("where", ["claude", "opencode", "opencode-untitled-kind"])
def test_a_real_conductor_carrying_the_prefix_still_refuses(env, where):
    quick_root(env)
    if where == "claude":
        env.host.records[eid_of(5)] = live(5, labels={"title": f"{PROG}-001 · stray", "kind": "conductor"},
                                           role="conductor")
    else:                                          # an old OpenCode Conductor carries no metadata: the title decides
        meta = {"kind": "conductor", "program": PROG, "conductor_n": 1} if where == "opencode" else {}
        env.sessions = [{"id": "ses_c1", "title": f"{PROG}-001 · stray", "metadata": meta}]
    note = env.wf.note
    code, out = refused(seeded=True, resume=False, quick=True, variant="high")
    assert "OUT OF SYNC" in str(code) and "already carries" in out and env.wf.note == note and not env.wf.edits


def test_conductor_like_counts_conductors_and_unlabelled_sessions_only():
    assert oc_launch.conductor_like("conductor") and oc_launch.conductor_like(None)
    assert oc_launch.conductor_like(None, "conductor") and oc_launch.conductor_like("seat", "conductor")
    assert not any(oc_launch.conductor_like(k, "worker") for k in ("seat", "worker", "helper", "warm"))


def test_two_reservations_or_an_unreadable_binding_refuse(env):
    env.wf.note = root_note(OLD_ROW.replace("ses_old1", "null").replace("state: stopped", "state: registered")
                            + RESERVED_ROW)
    code, out = refused()
    assert "more than one reservation" in str(code) and "no session bound" in out
    env.wf.note = root_note(OLD_ROW + RESERVED_ROW.replace("opencode_session_id: null   # reserved by hand at seeding",
                                                           "execution: {backend: claude}"))
    code, out = refused()
    assert "OUT OF SYNC" in str(code) and "unreadable binding" in out and not env.wf.edits


# ------------------------------------------------------------------------------------ before anything is written

def test_dry_run_and_the_admission_checks_write_nothing(env, monkeypatch):
    code, out = launch(dry_run=True)
    assert code == 0 and "DRY RUN" in out and not env.wf.edits and not env.host.records
    assert "backend: CLAUDE CODE on Max" in out and "conductor composer" in out and '"kind": "conductor"' in out
    code, out = refused(variant=None)                                                   # a Claude effort is required
    assert code == oc_backend.EXIT_ERROR and "no thinking level" in out and not env.wf.edits
    frame = {"status": "allowed", "unifiedWindows": {"five_hour": {"utilization": .95, "resetsAt": 4_000_000_000}}}
    env.host.records[eid_of(8)] = live(8, last_rate_limit_info=frame, last_rate_limit_cursor="id:9")
    code, _ = refused()
    assert code == oc_backend.EXIT_QUOTA and not env.wf.edits
    code, out = launch(dry_run=True)                                                    # a dry run reports it instead
    assert code == 0 and "a real launch would refuse now" in out
    # Doctrine drift (finding #2) never refuses: the dry run goes ahead, and the label would carry the skipped override.
    monkeypatch.setattr(sp, "CONDUCTOR_OVERRIDES", (("x", "no such doctrine text", "y"),))
    env.host.records.pop(eid_of(8))
    code, out = launch(dry_run=True)
    assert code == 0 and "DRY RUN" in out and '"prompt_drift": "conductor:x"' in out and not env.wf.edits


def test_a_claude_conductor_launches_while_opencode_is_down_unless_an_opencode_row_may_still_run(env, monkeypatch):
    """Finding #5: a Claude Conductor does not need OpenCode. With OpenCode down it launches with a warning, unless an
    OpenCode-bound registry row not marked stopped might still be running (the one-active rule needs OpenCode)."""
    def down(**_):
        raise OSError("connection refused")
    monkeypatch.setattr(oc_launch, "oc_sessions", down)
    code, out = launch(dry_run=True)
    assert code == 0 and "OpenCode is unavailable" in out and "DRY RUN" in out
    env.wf.note = env.wf.note.replace("state: stopped", "state: running", 1)     # the old OpenCode row may still run
    code, out = refused()
    assert "UNKNOWN whether it still runs (OpenCode is unavailable" in out and not env.wf.edits


def test_a_launch_record_that_does_not_verify_is_never_ignited(env, monkeypatch):
    real = env.host.m_launch

    def wrong_title(native_uuid, **p):
        rec = real(native_uuid, **p)
        env.host.records[rec["execution_id"]]["labels"] = {**p["labels"], "title": "something else"}
        return dict(env.host.records[rec["execution_id"]])
    monkeypatch.setattr(env.host, "m_launch", wrong_title)
    code, out = launch()
    assert code == 2 and "NOT VERIFIED" in out and env.host.sent == []
    assert oc_binding.is_reservation(rows_of(env.wf.note)[2])                          # the row stays a reservation


def test_the_switch_sends_an_anthropic_conductor_to_opencode_deliberately(env, monkeypatch):
    monkeypatch.setenv("OC_CLAUDE_BACKEND", "opencode")
    code, out = launch(dry_run=True)
    assert code == 0 and "POST /api/session body" in out and "CLAUDE CODE" not in out


# ------------------------------------------------------------------------------------ the OpenCode path, unchanged

@pytest.mark.parametrize("label", ["Claude Conductor test", "Keep every meaningful word in this long program label, including the last word"])
def test_the_opencode_launch_still_binds_opencode_session_id(env, monkeypatch, label):
    env.wf.name = f"[name: not editable] {PROG} 🧪 PROGRAM: {label} — scratch #prog-root"
    calls = []
    monkeypatch.setattr(oc_launch, "oc_create_session",
                        lambda **k: (calls.append(k), (200, {"id": "ses_new2", "title": k["title"]}))[1])
    monkeypatch.setattr(oc_launch, "oc_request", lambda m, path: (200, {
        "id": "ses_new2", "title": calls[0]["title"], "agent": "conductor",
        "model": {"providerID": "openai", "id": "gpt-6.1-sol", "variant": "high"}}))
    monkeypatch.setattr(oc_launch, "oc_prompt", lambda sid, text: (200, {"id": "msg_1"}))
    monkeypatch.setattr(oc_launch, "confirm_prompt", lambda sid, mid: ("delivered", "in the history"))
    code, out = launch(model="openai/gpt-6.1-sol", variant="high")
    assert code == 0, out
    assert calls[0]["title"] == f"{PROG}-002 · {label}"
    line = oc_launch.binding_line(env.wf.note, 2)
    assert re.search(r'opencode_session_id: "ses_new2"   # bound by oc_launch\.py \S+ · title and model verified by GET · '
                     r"ignition delivered · model openai/gpt-6\.1-sol variant high$", line)
    assert "execution" not in env.wf.note and not env.host.records
    assert oc_quick.printed_sid(out) == "ses_new2"
    md = calls[0]["metadata"]                       # labelled like a Claude Conductor (the pane places it by label)
    assert (md["kind"], md["program"], md["conductor_n"], md["quick_program"]) == ("conductor", PROG, 2, False)
    assert isinstance(md["manager_session"], str) and "context_target" not in md


# ------------------------------------------------------------------------------------ birth-only program favorites

def birth_backend(env, monkeypatch, backend, before_start=lambda: None):
    """Fake either session boundary, retaining the real reservation/star/bind owners."""
    if backend == 'claude':
        start = oc_claude.start

        def checked_start(**kwargs):
            before_start()
            return start(**kwargs)
        monkeypatch.setattr(oc_claude, 'start', checked_start)
        return {}

    def create(**kwargs):
        before_start()
        sid = f'ses_birth{len(env.created) + 1}'
        record = {'id': sid, **kwargs}
        env.created.append(record)
        return 200, record

    monkeypatch.setattr(oc_launch, 'oc_create_session', create)
    monkeypatch.setattr(oc_launch, 'oc_request', lambda method, path: (200, next(
        (r for r in env.created if r['id'] == path.rsplit('/', 1)[-1]), {'id': 'ses_old1', 'metadata': {}})))
    monkeypatch.setattr(oc_launch, 'oc_prompt', lambda *a: (200, {'id': 'msg_birth'}))
    monkeypatch.setattr(oc_launch, 'confirm_prompt', lambda *a: ('delivered', 'in history'))
    return {'model': 'openai/gpt-6.1-sol', 'variant': 'high'}


@pytest.mark.parametrize('backend', ['claude', 'opencode'])
@pytest.mark.parametrize('adopt', [False, True])
def test_full_seeded_birth_stars_after_first_reservation_before_session_start(env, monkeypatch, backend, adopt):
    env.wf.note = root_note(oc_launch.compose_row(1, 'seeded', False, AUTH, [], '    ') if adopt else '')

    def check_star():
        assert env.pins.get('program:PROG01') is not None
        assert oc_launch.BIRTH_STAR_MARKER in oc_launch.binding_line(env.wf.note, 1)

    kw = birth_backend(env, monkeypatch, backend, check_star)
    code, out = launch(seeded=True, resume=False, **kw)
    assert code == 0, out
    assert out.count('★ starred [ap:PROG01] (Favorite programs)') == 1
    assert set(env.pins.all()) == {'program:PROG01'}
    assert ('ADOPTING reserved row -001' in out) is adopt


@pytest.mark.parametrize('backend', ['claude', 'opencode'])
@pytest.mark.parametrize('adopt', [False, True])
def test_first_seeded_dry_run_would_star_but_writes_nothing(env, monkeypatch, backend, adopt):
    env.wf.note = root_note(oc_launch.compose_row(1, 'seeded', False, AUTH, [], '    ') if adopt else '')
    before = env.wf.note
    kw = birth_backend(env, monkeypatch, backend)
    code, out = launch(seeded=True, resume=False, dry_run=True, **kw)
    assert code == 0 and '★ would star [ap:PROG01] (Favorite programs)' in out
    assert env.wf.note == before and not env.wf.edits and not env.host.records and not env.created
    assert not env.pins.path.exists() and not env.pins.path.parent.exists()


@pytest.mark.parametrize('backend', ['claude', 'opencode'])
@pytest.mark.parametrize('mode', ['resume', 'self-resume', 'second-seeded', 'quick', 'promoted', 'promoted-unbound', 'sealed'])
def test_nonbirth_launches_do_not_restar_manual_unstar(env, monkeypatch, backend, mode):
    if mode in ('quick', 'promoted', 'promoted-unbound'):
        quick_root(env)
    if mode in ('promoted', 'promoted-unbound'):
        env.wf.note = env.wf.note.replace('profile: quick', 'profile: minimal').replace('quick_state:', 'quick_history:')
    if mode == 'promoted':
        env.wf.note = env.wf.note.replace('opencode_session_id: null', 'opencode_session_id: "ses_old1"')
    if mode == 'sealed':
        env.wf.note = root_note('').replace('seal: null', 'seal: "[ap:SEAL01]"')
    env.pins.set('program:PROG01')
    env.pins.set('program:PROG01', False)  # Dan removed it: never infer birth from pin absence.
    before = env.pins.path.read_bytes()
    kw = birth_backend(env, monkeypatch, backend)
    code, out = launch(seeded=mode not in ('resume', 'self-resume'), resume=mode == 'resume',
                       self_resume=mode == 'self-resume', target=0.4, **kw)
    assert code == 0, out
    assert '★' not in out and 'was not starred' not in out
    assert env.pins.all() == {} and env.pins.path.read_bytes() == before


@pytest.mark.parametrize('mode', ['resume', 'seeded-with-self-resume'])
def test_even_an_empty_registry_resume_is_not_a_birth(env, monkeypatch, mode):
    env.wf.note = root_note('') if mode == 'resume' else root_note()
    kw = birth_backend(env, monkeypatch, 'opencode')
    code, out = launch(seeded=mode != 'resume', resume=mode == 'resume', self_resume=mode != 'resume', target=0.4, **kw)
    assert code == 0 and '★' not in out and not env.pins.path.exists()


@pytest.mark.parametrize('backend', ['claude', 'opencode'])
@pytest.mark.parametrize('adopt', [False, True])
def test_unwritable_store_warns_once_and_full_launch_still_succeeds(env, monkeypatch, backend, adopt):
    import oc_pins
    env.wf.note = root_note(oc_launch.compose_row(1, 'seeded', False, AUTH, [], '    ') if adopt else '')
    kw = birth_backend(env, monkeypatch, backend)

    def fail_replace(*args):
        raise PermissionError('store is read-only')
    monkeypatch.setattr(oc_pins.os, 'replace', fail_replace)
    code, out = launch(seeded=True, resume=False, **kw)
    assert code == 0, out
    assert out.count('⚠️ program [ap:PROG01] was not starred') == 1 and 'store is read-only' in out
    assert not env.pins.path.exists() and oc_binding.row_binding(rows_of(env.wf.note)[1]) is not None


@pytest.mark.parametrize('adopt', [False, True])
def test_failed_unbound_first_launch_retry_cannot_undo_manual_unstar(env, monkeypatch, adopt):
    env.wf.note = root_note(oc_launch.compose_row(1, 'seeded', False, AUTH, [], '    ') if adopt else '')
    monkeypatch.setattr(oc_launch, 'oc_create_session', lambda **k: (503, {'error': 'failed to start'}))
    code, out = launch(seeded=True, resume=False, model='openai/gpt-6.1-sol', variant='high')
    assert code == 1 and '★ starred' in out
    assert oc_binding.is_reservation(rows_of(env.wf.note)[1])
    assert oc_launch.BIRTH_STAR_MARKER in oc_launch.binding_line(env.wf.note, 1)
    env.pins.set('program:PROG01', False)
    before = env.pins.path.read_bytes()
    kw = birth_backend(env, monkeypatch, 'opencode')
    code, out = launch(seeded=True, resume=False, **kw)
    assert code == 0 and '★' not in out and 'was not starred' not in out
    assert env.pins.path.read_bytes() == before and env.pins.all() == {}


def test_failed_birth_claim_warns_but_adopted_launch_still_succeeds(env, monkeypatch):
    env.wf.note = root_note(oc_launch.compose_row(1, 'seeded', False, AUTH, [], '    '))
    call = env.wf.call

    async def fail_claim(family, verb, params):
        if oc_launch.BIRTH_STAR_MARKER in params.get('note_replace', ''):
            return json.dumps({'success': False, 'error': 'claim failed'})
        return await call(family, verb, params)

    monkeypatch.setattr(env.wf, 'call', fail_claim)
    code, out = launch(seeded=True, resume=False)
    assert code == 0 and out.count('⚠️ program [ap:PROG01] was not starred') == 1
    assert not env.pins.path.exists()


def test_birth_pin_key_uses_canonical_core_even_for_a_lowercase_launch_handle(env):
    env.wf.note = root_note('')
    code, out = launch(program='[ap:prog01]', seeded=True, resume=False)
    assert code == 0 and '★ starred [ap:PROG01]' in out
    assert set(env.pins.all()) == {'program:PROG01'}


# ------------------------------------------------------------------------------------ the other readers

def test_oc_steer_targets_a_claude_current_conductor_and_never_falls_back(env, monkeypatch):
    assert launch()[0] == 0
    eid2 = oc_binding.row_session_id(rows_of(env.wf.note)[2])
    monkeypatch.setattr(oc_steer, "Workflowy", lambda: env.wf)
    env.sessions = [{"id": "ses_old1", "title": f"{PROG}-001 · x"}]                    # the older OpenCode row is live
    target, _, _, row = asyncio.run(oc_steer.resolve_conductor(PROG, None, env.sessions))
    assert (target["backend"], target["id"], int(row["number"])) == ("claude", eid2, 2)   # U2b: steered, never -001
    with pytest.raises(SystemExit) as e:                                                # an id the host does not know
        asyncio.run(oc_steer.resolve_conductor(PROG, eid_of(1), env.sessions))
    assert "no Claude session" in str(e.value.code) and "nothing sent" in str(e.value.code)


def test_oc_seal_quiescence_asks_the_host_about_a_claude_conductor(env, monkeypatch):
    assert launch()[0] == 0
    rows = oc_common.registry_rows(env.wf.note)
    eid2 = oc_binding.row_session_id(rows_of(env.wf.note)[2])
    monkeypatch.setattr(oc_seal, "oc_sessions", lambda: [])
    monkeypatch.setattr(oc_seal, "oc_status", lambda: (True, {}))
    rec = env.host.records[eid2]
    rec.update(state="ready", setting_control_pending=False, native_session_state="running")
    clear, notes, blockers = oc_seal.quiescence(rows, PROG)
    assert not clear and any(eid2 in b and "Claude Code" in b for b in blockers)
    rec.update(native_session_state="idle", pending_message_uuids=[])
    clear, notes, blockers = oc_seal.quiescence(rows, PROG)
    assert clear and any(eid2 in n for n in notes)


def test_wait_shows_a_conductors_last_word_from_its_background_wake_up(tmp_path):
    """A Claude Conductor ends its first turn waiting in the background; its doorbell turn has no input of its own, so
    its result is a host `synthetic_result`. `wait` shows the ignition's result and then that last word, labelled."""
    from test_oc_claude import done_receipt
    host = FakeHost(tmp_path, live(6, role="conductor"))
    eid = eid_of(6)
    done_receipt(host, eid, text="I'm ending my turn until the waiter wakes me.")
    host.records[eid].update(last_result_cursor="g1:900",
                             synthetic_results=[{"cursor": "g1:500", "result": "an older wake-up"},
                                                {"cursor": "g1:900", "result": "The worker's Result is in; done."}])
    out = []
    assert oc_claude.wait(eid, host=host, out=out.append, sleep=lambda s: None) == oc_backend.EXIT_DONE
    text = "\n".join(out)
    assert text.index("I'm ending my turn") < text.index("after a background-task wake-up") < text.index(
        "The worker's Result is in; done.") and "an older wake-up" not in text
    host.records[eid]["last_result_cursor"] = "g1:950"                                # a newer input-tied result
    out.clear()
    oc_claude.wait(eid, host=host, out=out.append, sleep=lambda s: None)
    assert "background-task wake-up" not in "\n".join(out)
