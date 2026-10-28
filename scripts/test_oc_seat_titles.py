"""U8: real socket names through every start path, with both backends and Workflowy fully offline."""
import asyncio
import json
from types import SimpleNamespace

import pytest

import oc_seat


CORPUS = [
    ("⚡ QUICK · IMPLEMENTER · U8 · seat session names · quick-sol61-fast-xhigh #prog-result",
     "quick-sol61-fast-xhigh", "quick", "implementer · U8 · seat session names"),
    ("⚡ QUICK · IMPLEMENTER · U7 · sidebar add-ons · quick-sol61-fast-xhigh",
     "quick-sol61-fast-xhigh", "quick", "implementer · U7 · sidebar add-ons"),
    ("✍️ Prompt · for the U8 implementer (seat session names) · recon C · Pane Usability Round #prog-recon",
     "sol61-xhigh", "fork", "implementer · U8 seat session names · recon C · Pane Usability Round"),
    ("✍️ Prompt · for findings auditor + adjudicator · general audit 1 · prompt author B · Pane Usability Round #prog-recon",
     "sol61-xhigh", "fork", "findings auditor + adjudicator · general audit 1 · prompt author B · Pane Usability Round"),
    ("🔧 Helper · U4 pre-fix evidence · for ses_eb7a5ff80ffeUV1n3NH9LANpDm",
     "quick-ds41-flash", "quick-helper", "helper · U4 pre-fix evidence"),
    ("🔧 Helper · title consumers sweep (boom/CLI/pane) · for cc-ca4dc56e-6e73-48d1-b521-268593ed642b",
     "quick-ds41-flash", "quick-helper", "helper · title consumers sweep boom/CLI/pane"),
    ("📁 Audit 1 · general · wave @62a475b · split #wfy-ns--local",
     "quick-sol61-fast-xhigh", "quick", "Audit 1 · general · wave 62a475b · split"),
    ("⚡ QUICK · FINDINGS_AUDITOR · 🔎 Findings · wave @62a475b · quick-sol61-fast-xhigh",
     "quick-sol61-fast-xhigh", "quick", "findings auditor · Findings · wave 62a475b"),
    ("⚖️ DISPATCHED · RESULT · stage 0 · contract [ap:W8HN1F] · opus55",
     "opus55", "dispatch-claude", "stage 0"),
]

PATHS = [
    ("quick", "⚡ quick worker"), ("quick-helper", "⚡ quick helper"),
    ("quick-claude", "⚡ quick worker"), ("quick-claude-helper", "⚡ quick helper"),
    ("dispatch", "worker"), ("dispatch-lead", "seat"),
    ("dispatch-claude", "worker"), ("dispatch-claude-lead", "seat"),
    ("fork", "seat"), ("fork-helper", "helper"), ("quick-fork", "⚡ quick fork worker"),
]


@pytest.fixture
def runtime(monkeypatch):
    """Record starts without touching a daemon, a host store or an OpenCode session."""
    seen = {"titles": [], "reads": [], "sent": [], "name": "⚡ QUICK · IMPLEMENTER · U8 · seat session names",
            "note": "dispatch:\n  role: IMPLEMENTER\n", "ancestors": "", "sessions": [], "claude": [],
            "history_reads": []}

    def name(socket):
        seen["reads"].append(socket)
        if isinstance(seen["name"], Exception):
            raise seen["name"]
        return seen["name"]

    def no_workflowy(*a, **k):
        raise AssertionError("a test reached the live Workflowy adapter")

    def create(**kw):
        seen["titles"].append(kw["title"])
        return 200, {"id": "ses_test"}

    def fork(source, title, **kw):
        seen["titles"].append(title)
        return "ses_test"

    def claude(**kw):
        seen["titles"].append(kw["labels"]["title"])
        return {"message_uuid": "m123456789", "acknowledged": True}

    monkeypatch.setattr(oc_seat, "socket_name", name, raising=False)  # also runs against the pre-U8 archive
    monkeypatch.setattr(oc_seat, "program_title_source", lambda s: (name(s), seen["note"], seen["ancestors"]))
    monkeypatch.setattr(oc_seat, "Workflowy", no_workflowy)
    monkeypatch.setattr(oc_seat, "agent_model", lambda a: "openai/gpt-6.1-sol")
    monkeypatch.setattr(oc_seat, "agent_variant", lambda a: "xhigh")
    monkeypatch.setattr(oc_seat, "cast_agent", lambda a: None)
    monkeypatch.setattr(oc_seat.oc_backend, "claude_launch_choice", lambda **k: {
        "model": "claude-opus-5-5", "effort": "high", "model_ref": "anthropic/claude-opus-5-5#high"})
    monkeypatch.setattr(oc_seat, "guard_socket", lambda *a, **k: None)
    monkeypatch.setattr(oc_seat, "busy_seats_on_socket", lambda *a: (True, []))
    real_number = oc_seat.SocketRunNumber
    monkeypatch.setattr(oc_seat, "SocketRunNumber", lambda socket: real_number(socket, spawn=lambda work: work()))

    def history(backend):
        seen["history_reads"].append(backend)
        if isinstance(seen[backend], Exception):
            raise seen[backend]
        return seen[backend]

    monkeypatch.setattr(oc_seat, "oc_sessions", lambda: history("sessions"))
    monkeypatch.setattr(oc_seat.oc_claude, "sessions_on_socket", lambda socket: history("claude"))
    monkeypatch.setattr(oc_seat, "validate_quick_runtime", lambda *a, **k: None)
    monkeypatch.setattr(oc_seat, "validate_cast_runtime", lambda *a, **k: None)
    monkeypatch.setattr(oc_seat, "wait_quick_mcp", lambda: None)
    monkeypatch.setattr(oc_seat, "manager_session", lambda: "190")
    monkeypatch.setattr(oc_seat, "conductor_of", lambda p: ("ses_cond", 8) if p else None)
    monkeypatch.setattr(oc_seat, "quick_parent", lambda p: (p, "[ap:PROG]"))
    monkeypatch.setattr(oc_seat, "resolve_sid", lambda p: p)
    monkeypatch.setattr(oc_seat, "session_of", lambda p: {"agent": "sol61-xhigh", "model": {"providerID": "openai", "id": "gpt-6.1-sol"}})
    monkeypatch.setattr(oc_seat, "oc_messages", lambda p: [{"id": "msg_before"}])
    monkeypatch.setattr(oc_seat, "oc_create_session", create)
    monkeypatch.setattr(oc_seat, "fork_session", fork)
    monkeypatch.setattr(oc_seat, "send", lambda *a: seen["sent"].append(a))
    monkeypatch.setattr(oc_seat.oc_claude, "new_execution_id", lambda: "cc-00000000-0000-0000-0000-000000000008")
    monkeypatch.setattr(oc_seat.oc_claude, "start", claude)
    composed = {"kind": "test", "text": "offline prompt", "prompt_sha256": "a" * 64}
    monkeypatch.setattr(oc_seat, "compose_quick_prompt", lambda a: composed)
    monkeypatch.setattr(oc_seat, "compose_cast_prompt", lambda a: composed)
    seen["monkeypatch"] = monkeypatch
    return seen


def start_path(runtime, path, *, title=None, agent=None, program="[ap:PROG]"):
    """Drive the actual starters, not a second implementation of their title construction."""
    kw = {"socket": "[ap:SOCKET]", "program": program, "title": title}
    runtime["monkeypatch"].setattr(oc_seat, "seat_backend", lambda *a: "claude" if "claude" in path else "opencode")
    if path.startswith("quick") and path != "quick-fork":
        return oc_seat.start_quick(agent=agent or ("quick-opus55-high" if "claude" in path else "quick-ds41-flash"),
                                   helper="helper" in path, **kw)
    if path.startswith("dispatch"):
        return oc_seat.start_dispatch(agent=agent or ("opus55" if "claude" in path else "sol61-xhigh"),
                                      lead="lead" in path, **kw)
    if path == "quick-fork":
        return oc_seat.start_quick_fork(source="ses_manager", before="msg_before", **kw)
    base = {"id": "ses_base", "time": {}, "metadata": {"role": "helper" if "helper" in path else "lead",
            "agent": agent or "sol61-xhigh", "model": "openai/gpt-6.1-sol", "program": program}}
    runtime["monkeypatch"].setattr(oc_seat, "base_session", lambda b: base)
    return oc_seat.cmd_fork(SimpleNamespace(base="sb-test", socket=kw["socket"], title=title, parent_seat=None,
                                           no_wait=True, timeout=1))


@pytest.mark.parametrize("name,agent,path,hint", CORPUS)
def test_real_socket_names_say_what_the_seat_is_doing(runtime, name, agent, path, hint):
    runtime["name"] = "[ap:SOCKET] " + name
    role = "PROMPT AUTHOR" if name.startswith("✍️ Prompt") else "IMPLEMENTER"
    if "FINDINGS_AUDITOR" in name:
        role = "FINDINGS AUDITOR"
    elif name.startswith("📁 Audit"):
        role = "ADJUDICATOR"
    runtime["note"] = f"dispatch:\n  role: {role.replace(' ', '_')}\n"
    start_path(runtime, path, agent=agent)
    marker = dict(PATHS)[path]
    shown = "ADJUDICATOR (AUDIT VERDICT)" if role == "ADJUDICATOR" else role
    own_role = "" if "helper" in path else f"{shown} · "
    assert runtime["titles"] == [f"[ap:PROG]-008 ▸ {marker} · [ap:SOCKET] · {own_role}{hint}"]
    assert runtime["reads"] == ["[ap:SOCKET]"]
    tail = runtime["titles"][0].split("[ap:SOCKET]", 1)[1]
    for forbidden in ("@", "▸", "#", "[ap:", "ses_", "cc-", agent):
        assert forbidden not in tail


@pytest.mark.parametrize("path,marker", PATHS)
def test_every_start_path_uses_one_derivation_without_changing_its_prefix(runtime, path, marker):
    start_path(runtime, path)
    role = "" if "helper" in path else "IMPLEMENTER · "
    assert runtime["titles"] == [f"[ap:PROG]-008 ▸ {marker} · [ap:SOCKET] · {role}implementer · U8 · seat session names"]
    assert runtime["reads"] == ["[ap:SOCKET]"]


@pytest.mark.parametrize("path,marker", PATHS)
@pytest.mark.parametrize("title", ["my explicit title @unchanged", ""])
def test_explicit_detail_cannot_suppress_program_socket_or_role_but_helpers_are_unchanged(runtime, path, marker, title):
    start_path(runtime, path, title=title)
    if "helper" in path:
        assert runtime["titles"] == [f"[ap:PROG]-008 ▸ {marker} · {title or '[ap:SOCKET]'}"]
        assert runtime["reads"] == []
    else:
        detail = f" · {title}" if title else ""
        assert runtime["titles"] == [f"[ap:PROG]-008 ▸ {marker} · [ap:SOCKET] · IMPLEMENTER{detail}"]
        assert runtime["reads"] == ["[ap:SOCKET]"]


@pytest.mark.parametrize("path,marker", PATHS)
@pytest.mark.parametrize("name", [RuntimeError("offline\nwith a second line"), None, "", "⚡ QUICK · HELPER · #prog-result"])
def test_failed_or_empty_socket_names_warn_once_and_still_start_with_the_old_title(runtime, capsys, path, marker, name):
    runtime["name"] = name
    if "helper" not in path:
        runtime["monkeypatch"].setattr(oc_seat, "program_title_source", lambda s: (_ for _ in ()).throw(ValueError("unavailable")))
    start_path(runtime, path)
    role = "" if "helper" in path else (" · QUICK" if path.startswith("quick") else " · WORKER")
    assert runtime["titles"] == [f"[ap:PROG]-008 ▸ {marker} · [ap:SOCKET]{role}"]
    assert runtime["reads"] == (["[ap:SOCKET]"] if "helper" in path else [])
    warnings = [line for line in capsys.readouterr().out.splitlines() if "socket title lookup" in line]
    assert len(warnings) == 1


@pytest.mark.parametrize("path,marker", [("quick", "⚡ quick worker"), ("quick-claude", "⚡ quick worker"),
                                         ("fork", "seat"), ("quick-fork", "⚡ quick fork worker")])
def test_unnumbered_and_manager_fork_prefixes_stay_as_before(runtime, path, marker):
    start_path(runtime, path, program=None)
    prefix = "Manager-Session-190 " if path == "quick-fork" else ""
    assert runtime["titles"] == [f"{prefix}▸ {marker} · [ap:SOCKET] · implementer · U8 · seat session names"]


@pytest.mark.parametrize("role", ["SPEC_AUTHOR", "strictness-pass", "PROMPT_AUTHOR", "lead seat"])
def test_role_words_become_plain_words_without_inventing_roles(runtime, role):
    runtime["name"] = f"⚡ QUICK · {role} · U5 · writing requirements"
    runtime["note"] = f"session_role: {role.replace(' ', '_')}"
    start_path(runtime, "quick")
    plain = role.lower().replace("_", " ").replace("-", " ")
    assert runtime["titles"][0].endswith(f"[ap:SOCKET] · {plain.upper()} · {plain} · U5 · writing requirements")


def test_phrase_is_twelve_words_and_a_single_huge_word_cannot_block_host_launch(runtime):
    runtime["name"] = "Helper · " + " ".join(f"word{i}" for i in range(30))
    start_path(runtime, "quick-claude")
    assert runtime["titles"][0].endswith("helper · " + " ".join(f"word{i}" for i in range(12)))
    runtime["name"] = "Helper · " + "x" * 5000
    start_path(runtime, "quick-claude")
    assert runtime["titles"][-1].endswith("helper · " + "x" * 119 + "…")
    assert len(runtime["titles"][-1]) < 300


@pytest.mark.parametrize("reply", ["not json", "{}", '{"match_count":0}', '{"match_count":2,"name":"wrong"}',
                                   '{"match_count":1,"name":null}', '{"success":false,"error":{}}'])
def test_unusable_resolve_responses_warn_and_do_not_prevent_a_start(monkeypatch, capsys, reply):
    # Use the real adapter here; fake only its transport, so bad JSON cannot accidentally look like a task name.
    calls = []

    class FakeWorkflowy:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def call(self, family, verb, params):
            calls.append((family, verb, params))
            return reply

    monkeypatch.setattr(oc_seat, "Workflowy", FakeWorkflowy)
    assert oc_seat.socket_title("[ap:SOCKET]", None, agent="a") == "[ap:SOCKET]"
    assert calls == [("node", "resolve", {"node_handle": "[ap:SOCKET]"})]
    assert capsys.readouterr().out.count("socket title lookup") == 1


def test_socket_lookup_reads_only_the_name_once_and_passes_the_handle_directly(monkeypatch):
    calls = []

    class FakeWorkflowy:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def call(self, family, verb, params):
            calls.append((family, verb, params))
            return json.dumps({"match_count": 1, "uuid": "test", "name": "[ap:SOCKET] Helper · title consumers sweep"})

    monkeypatch.setattr(oc_seat, "Workflowy", FakeWorkflowy)
    assert oc_seat.socket_title("[ap:SOCKET]", None, agent="agent") == "[ap:SOCKET] · helper · title consumers sweep"
    assert calls == [("node", "resolve", {"node_handle": "[ap:SOCKET]"})]


@pytest.mark.parametrize("backend", ["opencode", "claude"])
def test_boom_worker_roster_keeps_the_task_phrase_readable_on_both_backends(monkeypatch, capsys, backend):
    """The old 70-character roster crop left only 'Fi…' of this real findings-auditor task phrase."""
    import oc_claude_view
    import oc_view
    parent = "ses_parent0001" if backend == "opencode" else "cc-00000000-0000-0000-0000-000000000001"
    child = "ses_helper0002" if backend == "opencode" else "cc-00000000-0000-0000-0000-000000000002"
    title = "[ap:Q2M7XJ]-003 ▸ ⚡ quick helper · [ap:SOCKET] · findings auditor · Findings · wave 62a475b"
    records = [{"id": parent, "title": "[ap:Q2M7XJ]-003 ▸ seat · [ap:PARENT]", "agent": "sol61-xhigh",
                "time": {}, "metadata": {"kind": "seat", "program": "[ap:Q2M7XJ]"}},
               {"id": child, "title": title, "agent": "quick-sol61-fast-xhigh", "time": {},
                "metadata": {"kind": "helper", "quick": True, "parent_seat": parent,
                             "program": "[ap:Q2M7XJ]", "socket": "[ap:SOCKET]"}}]
    if backend == "claude":
        for record in records:
            record.update(backend="claude", claude={"phase": "done", "reasons": []})
    monkeypatch.setattr(oc_view, "oc_sessions", lambda: records if backend == "opencode" else [])
    monkeypatch.setattr(oc_view, "oc_status", lambda: (True, {}))
    monkeypatch.setattr(oc_view, "history", lambda sid: [])
    monkeypatch.setattr(oc_claude_view, "inventory", lambda: {
        "ok": True, "sessions": records if backend == "claude" else [], "status": {}, "note": "offline host"})
    args = SimpleNamespace(program=[], title=[], sid=[parent], recent=0, bytes=5000, full=False, detail=False,
                           conductors=3, finished=5, lines=10, lines_explicit=False, tab="BOOM", modal="navigator",
                           tags=None, workers="all", no_modal=True, no_workflowy=True, show_tools=False)
    assert asyncio.run(oc_view.run_selected(args)) == 0
    roster = next(line for line in capsys.readouterr().out.splitlines() if f"· {child} ·" in line)
    assert title in roster


@pytest.mark.parametrize("role", [
    "SPEC_AUTHOR", "SPEC_AUDITOR", "IMPLEMENTER", "DOCUMENTATION_MAINTAINER", "AUDIT_DESIGNER",
    "FOCUSED_AUDITOR", "WHOLE_UNIT_AUDITOR", "CLOSURE_AUDITOR", "VERDICT_REVIEWER",
    "AUDIT_CHALLENGE_REVIEWER", "CONSENSUS_PARTICIPANT", "CONSOLIDATOR", "CONSOLIDATION_AUDITOR",
    "PROSE_WORKER", "PROSE_REVIEWER", "RECON", "PROMPT_AUTHOR", "FINDINGS_AUDITOR", "ADJUDICATOR",
    "NAVIGATOR", "PRISM", "FAN_IN", "COMPARATOR", "FUTURE_ROLE",
])
def test_every_roster_identifier_is_its_marker_without_a_parallel_runtime_roster(role):
    assert oc_seat.title_role("Task", f"```yaml\ndispatch:\n  role: {role} # own assignment\n```") == role.replace("_", " ")


@pytest.mark.parametrize("name", [
    "✍️ Prompt · for N05's strictness pass, then N06's and N05's implementers · prompt author A⁵ · opus55-xhigh",
    "✍️ Prompt · for N06b's implementer, then N05's implementer · prompt author A⁶ · opus55-xhigh",
])
def test_prompt_authors_are_not_the_workers_they_frame(runtime, name):
    runtime["name"] = name
    runtime["note"] = "```yaml\ndispatch:\n  role: PROMPT_AUTHOR\n  prompt_for:\n    - socket: '[ap:TARGET]'\n      dispatch:\n        role: IMPLEMENTER\n```"
    start_path(runtime, "dispatch-claude-lead", agent="opus55-xhigh")
    tail = runtime["titles"][0].split("[ap:SOCKET]", 1)[1]
    assert tail.startswith(" · PROMPT AUTHOR · ")
    assert tail.endswith(oc_seat.socket_title_hint(name, "opus55-xhigh"))


def test_nested_target_dispatch_and_guarded_historical_tail_are_not_own_roles():
    assert oc_seat.title_role("ordinary job", "prompt_for:\n  - dispatch:\n      role: IMPLEMENTER\n") is None
    assert oc_seat.title_role("ordinary job", "plain prompt\n... | omitted lines\ndispatch:\n  role: RECON\n") is None
    assert oc_seat.title_role("ordinary job", "dispatch:\n  prompt_for:\n    dispatch:\n      role: IMPLEMENTER\n") is None


@pytest.mark.parametrize("indent", [" ", "    ", "\t"])
def test_own_dispatch_role_is_not_tied_to_two_space_yaml_formatting(indent):
    note = f'dispatch:\n{indent}agent: a\n{indent}role: "PROMPT_AUTHOR" # own role\n{indent}prompt_for:\n{indent}{indent}dispatch:\n{indent}{indent}{indent}role: IMPLEMENTER\n'
    assert oc_seat.title_role("Task", note) == "PROMPT AUTHOR"


def test_legacy_prompt_socket_shape_does_not_mistake_target_strictness_for_its_role():
    assert oc_seat.title_role("✍️ Prompt · for strictness pass · N05", "") == "PROMPT AUTHOR"
    assert oc_seat.title_role("⚡ QUICK · strictness pass · UnitSpec N05", "dispatch:\n  role: SPEC_AUTHOR\n") == "STRICTNESS"
    assert oc_seat.title_role("⚖️ DISPATCHED · RESULT · stage 0 · N06a", "") == "IMPLEMENTER"
    assert oc_seat.title_role("ordinary job", "session_role: PROSE_WORKER\n") == "PROSE WORKER"
    assert oc_seat.title_role("ordinary job", "dispatch:\n  role: WHOLE_SESSION_AUDITOR\n") == "WHOLE UNIT AUDITOR"


@pytest.mark.parametrize("role", ["IMPLEMENTER", "FOCUSED_AUDITOR", "FINDINGS_AUDITOR", "ADJUDICATOR", "CONSOLIDATOR"])
def test_chain_placement_marks_all_round_roles_not_just_the_implementer(runtime, role):
    runtime["note"] = f"dispatch:\n  role: {role}\n"
    runtime["ancestors"] = "# ⦿ Program\n## ⦿ 📁 Implementation\n### ⦿ [ap:CHAIN] 📁 Chain 2 · charter [ap:WO] ▸4\n#### • socket"
    start_path(runtime, "quick-claude")
    shown = "ADJUDICATOR (AUDIT VERDICT)" if role == "ADJUDICATOR" else role.replace('_', ' ')
    assert f"[ap:SOCKET] · REMEDIATION · {shown} · " in runtime["titles"][0]


def test_adjudicator_title_says_it_is_an_audit(runtime):
    runtime["note"] = "session_role: ADJUDICATOR\n"
    start_path(runtime, "quick")
    assert "[ap:SOCKET] · ADJUDICATOR (AUDIT VERDICT) · " in runtime["titles"][0]


def test_general_audit_is_not_a_chain_and_task_words_do_not_manufacture_ancestry(runtime):
    runtime["name"] = "📁 Audit 2 · whole-unit · late · N06"
    runtime["note"] = "dispatch:\n  role: WHOLE_UNIT_AUDITOR\n"
    runtime["ancestors"] = "# ⦿ Program\n## ⦿ 📁 Implementation\n### • Audit 2 · after Chain 1"
    start_path(runtime, "quick")
    assert " · WHOLE UNIT AUDITOR · " in runtime["titles"][0]
    assert "REMEDIATION" not in runtime["titles"][0]


def test_unknown_quick_role_is_honest_explicit_detail_survives_and_socket_is_once(runtime, capsys):
    runtime["note"] = "Your untyped custom task"
    runtime["name"] = "ordinary job"
    start_path(runtime, "quick", title="[ap:SOCKET] · keep every word @42")
    assert runtime["titles"][0].endswith("[ap:SOCKET] · QUICK · keep every word @42")
    assert runtime["titles"][0].count("[ap:SOCKET]") == 1
    assert "role for [ap:SOCKET] unknown" not in capsys.readouterr().out   # a quick job with no role is normal


def test_unknown_dispatched_role_says_worker_and_warns(runtime, capsys):
    runtime["note"] = "Your untyped custom task"
    runtime["name"] = "ordinary job"
    start_path(runtime, "dispatch")
    assert "[ap:SOCKET] · WORKER" in runtime["titles"][0]
    assert "role for [ap:SOCKET] unknown" in capsys.readouterr().out


def test_program_source_is_bounded_no_resolve_or_full_prompt_and_context_failure_does_not_refuse(monkeypatch, capsys):
    calls = []

    class FakeWorkflowy:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def call(self, family, verb, params):
            calls.append((family, verb, params))
            if verb == "ancestors":
                raise RuntimeError("offline ancestry")
            return "[name: not editable] [ap:SOCKET] ⚖️ RESULT · stage 0 · N06a\n[note: guarded]\ndispatch:\n  role: IMPLEMENTER\n... | omitted\n  role: RECON"

    monkeypatch.setattr(oc_seat, "Workflowy", FakeWorkflowy)
    result = oc_seat.socket_title("[ap:SOCKET]", "custom detail", agent="a", program_worker=True)
    assert result == "[ap:SOCKET] · IMPLEMENTER · custom detail"
    assert calls == [("node", "read", {"node_handle": "[ap:SOCKET]", "max_lines": 80}),
                     ("node", "ancestors", {"node_handle": "[ap:SOCKET]"})]
    assert "remediation context unknown" in capsys.readouterr().out


def old_oc_run(sid, created, title="legacy task", socket="[ap:SOCKET]", kind="seat", conductor=1):
    return {"id": sid, "time": {"created": created * 1000}, "title": title,
            "metadata": {"socket": socket, "kind": kind, "conductor_n": conductor}}


def old_claude_run(sid, created, title="legacy task", socket="[ap:SOCKET]", kind="seat", conductor=1):
    return {"id": sid, "created": created, "title": title, "socket": socket, "kind": kind,
            "labels": {"conductor_n": conductor}}


@pytest.mark.parametrize("path,marker", PATHS)
def test_seven_legacy_runs_number_the_next_fresh_run_but_never_helpers(runtime, path, marker):
    runtime["sessions"] = [old_oc_run(f"ses_{n}", n, conductor=15 + n // 3) for n in range(1, 5)]
    runtime["claude"] = [old_claude_run(f"cc-{n}", n, conductor=17) for n in range(5, 8)]
    start_path(runtime, path)
    role = "" if "helper" in path else "IMPLEMENTER · "
    suffix = "" if "helper" in path else " · #8"
    assert runtime["titles"] == [f"[ap:PROG]-008 ▸ {marker} · [ap:SOCKET] · {role}implementer · U8 · seat session names{suffix}"]
    assert runtime["history_reads"] == ([] if "helper" in path else ["sessions", "claude"])


@pytest.mark.parametrize("path", ["quick", "quick-claude", "quick-fork", "fork"])
def test_programless_runs_gain_only_the_number_and_explicit_title_bytes_survive(runtime, path):
    runtime["sessions"] = [old_oc_run("ses_old", 1)]
    detail = "Keep  ·  every @word #99"
    start_path(runtime, path, program=None, title=detail)
    assert runtime["titles"][0].endswith(" · " + detail + " · #2")


def test_number_uses_highest_stamp_not_history_count_or_conductor_number():
    oc = [old_oc_run("ses_last", 3, "task · #27", conductor=3)]
    cc = [old_claude_run("cc-old", 2, "task · #24", conductor=2)]
    assert oc_seat.next_socket_run_number("[ap:SOCKET]", oc, cc) == 28
    # Removing old runs cannot lower a surviving high stamp; a new Conductor starts no new series.
    assert oc_seat.next_socket_run_number("[ap:SOCKET]", oc, []) == 28


def test_new_cross_backend_continuations_keep_their_stored_title_after_legacy_history_disappears(runtime):
    runtime["sessions"] = [old_oc_run(f"ses_{n}", n) for n in range(1, 8)]
    start_path(runtime, "quick")
    first = runtime["titles"][-1]
    assert first.endswith(" · #8")
    runtime["sessions"] = [old_oc_run("ses_new", 8, first, conductor=8)]
    start_path(runtime, "quick-claude")
    assert runtime["titles"] == [first, first.removesuffix(" · #8") + " · #9"]
    runtime["claude"] = [old_claude_run("cc-new", 9, runtime["titles"][-1], conductor=9)]
    runtime["sessions"] = []
    start_path(runtime, "quick")
    assert runtime["titles"][0] == first
    assert runtime["titles"][-1].endswith(" · #10")


@pytest.mark.parametrize("stamped_backend", ["opencode", "claude"])
@pytest.mark.parametrize("legacy_after", [False, True])
def test_legacy_runs_advance_at_creation_position_with_mixed_timestamp_units(stamped_backend, legacy_after):
    oc_time, cc_time = (1, 2) if (stamped_backend == "opencode") == legacy_after else (2, 1)
    oc = [old_oc_run("ses_z", oc_time, "task · #7" if stamped_backend == "opencode" else "task")]
    cc = [old_claude_run("cc-a", cc_time, "task · #7" if stamped_backend == "claude" else "task")]
    assert oc_seat.next_socket_run_number("[ap:SOCKET]", oc, cc) == (9 if legacy_after else 8)


def test_number_is_per_socket_deduplicated_and_only_recognises_the_stamped_suffix():
    first = old_oc_run("ses_a", 1, "round #99")
    oc = [old_oc_run("ses_other", 2, "other · #9000", socket="[ap:OTHER1]"), first, first,
          old_oc_run("ses_helper", 3, "helper · #999", kind="helper"),
          old_oc_run("ses_base", 4, "warm · #888", kind="warm")]
    cc = [old_claude_run("cc-other", 2, "other · #9000", socket="[ap:OTHER2]")]
    assert oc_seat.next_socket_run_number("[ap:SOCKET]", oc, cc) == 2
    assert oc_seat.next_socket_run_number("[ap:NEWONE]", oc, cc) == 1
    # Use the same AP-core equivalence as the existing socket listing.
    assert oc_seat.next_socket_run_number("SOCKET", oc, cc) == 2


def test_creation_ties_and_inventory_order_are_deterministic():
    oc = [old_oc_run("ses_b", 1, "task · #7"), old_oc_run("ses_a", 1)]
    assert oc_seat.next_socket_run_number("[ap:SOCKET]", oc, []) == 8
    assert oc_seat.next_socket_run_number("[ap:SOCKET]", oc[::-1], []) == 8


@pytest.mark.parametrize("backend", ["sessions", "claude"])
@pytest.mark.parametrize("path", ["quick", "quick-claude", "dispatch", "dispatch-claude", "fork", "quick-fork"])
def test_either_unreadable_history_warns_but_launches_with_the_old_title(runtime, capsys, backend, path):
    runtime["sessions"] = [old_oc_run("ses_old", 1, "task · #7")]
    runtime["claude"] = [old_claude_run("cc-old", 2, "task · #8")]
    runtime[backend] = OSError("unavailable")
    start_path(runtime, path)
    assert runtime["titles"][0].endswith("[ap:SOCKET] · IMPLEMENTER · implementer · U8 · seat session names")
    assert len(runtime["sent"]) == (0 if "claude" in path else 1)  # the fake Claude start acknowledged its prompt
    assert "launching unnumbered" in capsys.readouterr().out


def test_pending_numbering_never_waits_and_late_completion_cannot_rename_a_stored_title(monkeypatch, capsys):
    queued = []
    monkeypatch.setattr(oc_seat, "oc_sessions", lambda: [old_oc_run("ses_old", 1)])
    monkeypatch.setattr(oc_seat.oc_claude, "sessions_on_socket", lambda socket: [])
    sequence = oc_seat.SocketRunNumber("[ap:SOCKET]", spawn=queued.append)
    monkeypatch.setattr(sequence.ready, "wait", lambda *a, **k: pytest.fail("numbering waited on launch"))
    stored = sequence.stamp("original title")
    assert stored == "original title"
    assert "histories still being read; launching unnumbered" in capsys.readouterr().out
    queued[0]()
    assert sequence.ready.is_set() and sequence.number == 2
    assert stored == "original title"  # no late update, rename or background write


def test_numbering_prefetch_is_daemonised_and_failure_to_spawn_does_not_refuse(monkeypatch, capsys):
    threads = []

    class Thread:
        def __init__(self, **kw):
            threads.append(kw)

        def start(self):
            raise RuntimeError("thread unavailable")

    monkeypatch.setattr(oc_seat.threading, "Thread", Thread)
    sequence = oc_seat.SocketRunNumber("[ap:SOCKET]")
    assert threads[0]["daemon"] is True
    assert sequence.stamp("old title") == "old title"
    assert "history read could not start (RuntimeError); launching unnumbered" in capsys.readouterr().out


def test_all_time_claude_reader_is_used_not_the_lossy_socket_listing_wrapper(monkeypatch):
    calls = []
    monkeypatch.setattr(oc_seat, "oc_sessions", lambda: [])
    monkeypatch.setattr(oc_seat, "claude_sessions_on_socket", lambda socket: pytest.fail("lossy history wrapper"))

    def read(socket):
        calls.append(socket)
        return [old_claude_run("cc-old", 1, "task · #7")]

    monkeypatch.setattr(oc_seat.oc_claude, "sessions_on_socket", read)
    sequence = oc_seat.SocketRunNumber("[ap:SOCKET]", spawn=lambda work: work())
    assert sequence.stamp("old title") == "old title · #8"
    assert calls == ["[ap:SOCKET]"]
