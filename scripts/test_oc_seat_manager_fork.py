"""Socketless Manager forks, offline: title/kind proof, claim/age/settle, subagent guard and tripwire, room and
in-lock admission, exact Unit 2 notes, launch effort, grammar and bells. Fixtures/fakes only: no host, Workflowy, stdin or network."""
import inspect
import json
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

import oc_backend
import oc_claude
import oc_claude_usage
import oc_claude_view
import oc_common
import oc_doorbell
import oc_running
import oc_seat
import oc_telemetry

REAL_START = oc_claude.start
MGR = "cc-" + str(uuid.UUID(int=1))
NEW = "cc-" + str(uuid.UUID(int=2))
TITLE = "Fix X"
NOW = 1_800_000_000.0


def iso(seconds_ago=0, now=NOW):
    return datetime.fromtimestamp(now - seconds_ago, timezone.utc).isoformat().replace("+00:00", "Z")


def entry(kind, content, *, mid=None, uid=None, sidechain=False, timestamp=iso()):
    message = {"role": kind, "content": content}
    if mid:
        message["id"] = mid
    return {"type": kind, "uuid": uid or str(uuid.uuid4()), "isSidechain": sidechain,
            "timestamp": timestamp, "message": message}


def bash(command, tid, *, background=True):
    inputs = {"command": command}
    if background is not None:
        inputs["run_in_background"] = background
    return {"type": "tool_use", "id": tid, "name": "Bash", "input": inputs}


def answer(tid, text="ok"):
    return {"type": "tool_result", "tool_use_id": tid, "content": text}


def write(path, entries):
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    return str(path)


def call_cmd(title=TITLE, *, forklift=False, equals=False):
    return (f'bash /mnt/e/x/oc.sh seat quick --fork-self{" --forklift" if forklift else ""} '
            f'--title{"=" if equals else " "}"{title}"')


def one_call_manager(*calls, background=True):
    """The Manager read, then made one or several title-only calls in one message."""
    entries = [entry("user", "Dan: fork yourself and fix X"),
               entry("assistant", [{"type": "tool_use", "id": "t0", "name": "Read", "input": {}}], mid="msg_1"),
               entry("user", [answer("t0")])]
    for i, (command, tid) in enumerate(calls or [(call_cmd(), "toolu_1")]):
        entries.append(entry("assistant", [bash(command, tid, background=background)], mid="msg_2",
                             uid=f"call-entry-{i + 1}"))
    return entries


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def proof(path, title=TITLE, *, budget=0.5, clock=None, sleep=None, **kw):
    clock = clock or Clock()

    def nap(seconds):
        clock.sleep(seconds)
        if sleep:
            sleep(seconds)
    return oc_seat.manager_fork_call(path, title, budget=budget, clock=clock, sleep=nap,
                                      **{"now": NOW, **kw})


# ------------------------------------------------------------------------------------ exact P7 notes

BELLS = {
    "done": "It runs on in the Manager's background as its doorbell on you: when you finish, it rings the Manager and prints your final message.",
    "launch": "It runs on in the Manager's background as its doorbell: when you finish, it rings the Manager and prints your final message, with your program's launch line if you launched one.",
    "program": "It runs on in the Manager's background as its doorbell: when you finish, it rings the Manager and prints your final message if you launched nothing or a full program; for a quick program it then waits on that program's Conductor and rings when it stops.",
    "none": "It has returned, ringing nothing: the Manager reads your final message when it looks.",
    "foreground": "It has returned; the Manager rings its own doorbell on you.",
}

# Independently pinned to P7 + Unit 2 [ap:IGHNIN] (not assembled from implementation pieces).
PINNED_QUICK = '''You are a FORK (session {sid}) of {manager}, the Claude Manager conversation of Manager-Session-{session}, copied just after the call that started you. You are a fork of the Manager, doing one job for it. Everything above is that conversation, what Dan said and what the Manager read and did, and you hold it as your own. The call that created you is the `oc.sh seat quick --fork-self … --title "{title}"` call above (tool call {tool_use_id}): it took effect, and you are its result. {bell} Never run it, or any `--fork-self`, again. Your copy may show that call as stopped, interrupted or answered with "No response requested.": that is your copy, not the Manager's. Your job is what was asked for just before that call: Dan's request as the conversation up to and including that call states it, the Manager's own words about this fork included; if that call was one of several made together, yours is the part titled "{title}", and only that. Anything in the conversation after that call (the Manager's reply, Dan's next words, other calls and forks) belongs to the Manager, not to you. There is no socket and no job note, and nothing else is coming: begin now. You are now a QUICK WORKER (role worker, Claude Code, {model} at {effort}); your own id is {sid}, and `context_status` measures you. Your working rules are the quick worker's: read {QUICK_RULES} now; where it differs from the Manager instructions in your system prompt, it and this message win. Where it speaks of your socket, this message replaces it: your prompt is that request, and there is nothing to read first; write your result, and any PARTIAL, to a local Anchor Point in this session's Dock (`🍴 Result · {title}`, `ap_id: auto`); Anchor Points are encouraged, consolidated there. An account for Dan, if one was asked for, ends that note; your helpers' sockets go in this session's Dock, the one this conversation captures into (its number is {session} by the shared stamp; where they differ, the Dock this conversation uses and its number win). None of the Manager's duties are yours: you do not talk to Dan, create no session Dock or sub-session, edit no Residue, Board or .oc_config.json, curate and hand off nothing, launch no program (only a forklift does: if the request was to launch one, say so in your final message and stop), and steer, wait on, seal, resume, cancel or restart nothing the request does not name. Never pass {manager} to any script. Leave the Agent and SendMessage tools unused; start quick helpers with `oc.sh seat quick … --parent-seat {sid} --no-wait`. {cwd} is not your project: use absolute paths in the repository the request names. In that result Anchor Point put the outcome (done, partial or failed), what you did and found, the evidence, the files and commits, and anything the Manager must decide or do. End with a short final message, under 20,000 characters: its first line is the result's AP-ID, then `Worker: {sid} · fork of Manager-Session-{session} ({manager})` and the outcome. The doorbell prints that message; the Manager reads the Anchor Point and reports it to Dan.'''

PINNED_FORKLIFT = '''You are a FORK (session {sid}) of {manager}, the Claude Manager conversation of Manager-Session-{session}, copied just after the call that started you. You are a fork of the Manager, doing one job for it. Everything above is that conversation, what Dan said and what the Manager read and did, and you hold it as your own. The call that created you is the `oc.sh seat quick --fork-self --forklift … --title "{title}"` call above (tool call {tool_use_id}): it took effect, and you are its result. {bell} Never run it, or any `--fork-self`, again. Your copy may show that call as stopped, interrupted or answered with "No response requested.": that is your copy, not the Manager's. You are now the Manager's FORKLIFT: a quick worker that prepares and launches, for the Manager, the one program asked for just before that call, as the conversation up to and including that call states it (Dan's request, his go and the decisions made with him, the Manager's own words about this forklift included); if that call was one of several made together, yours is the one titled "{title}". Anything in the conversation after that call (the Manager's reply, Dan's next words, other calls and forks) belongs to the Manager, not to you. There is no socket and no job note, and nothing else is coming: begin now. You run as role worker, Claude Code, {model} at {effort}; your own id is {sid}, and `context_status` measures you. Your working rules are the quick worker's: read {QUICK_RULES} now; where it differs from the Manager instructions in your system prompt, it and this message win, except that you are the Manager for this one job: the Program Interface's Manager obligations bind you for that one seed, and you follow the Manager's launch doctrine (⚡ QUICK PROGRAM [ap:XUWU84] *For the Manager* for a quick program; the seeding reads for a full one), reusing what this conversation already holds. Quickly do every preparatory check a Manager would before launching: quota on both providers (`oc.sh usage`), network / modem trouble, Claude host and OpenCode health, running programs that touch the same repository or files, the Project Maps, Residue and the Board for conflicts or anything urgent. These are quick reads in your own background time, reusing what the conversation holds, then launch. A real blocker means do not launch: end at once with the reason so the doorbell rings. Minor issues and soft signals are not a launch gate; mention them in your final message. Where the rules speak of your socket, this message replaces it: your prompt is that request; your result is your final message; your helpers' sockets go in this session's Dock. You write the program's own records (a quick program's job note; a full program's root, SkyView, StageConfiguration, Brief and Board row) exactly as the Manager would, under this session's Dock, the one this conversation captures into, with that session's number (it is {session} by the shared stamp; where they differ, the Dock this conversation uses and its number win). New Anchor Points beyond those records are allowed, slightly discouraged but encouraged when of clear value: only local Anchor Points in this session's Dock. Each opens with `Created by forklift "{title}" ({sid}).` so curation picks it up; list every Anchor Point you create in your final message. Edit no other existing Anchor Point: your writes remain your own program's records and its one Board-row insert. Residue, curation, steering other programs and the rest of the Board stay the primary Manager's. Launch exactly that program, once, in the foreground: `oc.sh quick … --parent "‹that Dock›" --session ‹its number›` (Bash `timeout` 300000), or, for a full program, its seed under that Dock, `oc.sh preflight`, then `oc.sh launch … --seeded`. Skip QUICK PROGRAM's background launch and its doorbell step: you answer no one, and the Manager's call is the doorbell. A quick program's `oc.sh quick` adds its own Board row. For a full program, once it is launched, add its Board row yourself: one `node.edit` search/replace into `## Programs` of the Board ([ap:QHUADA]), never a whole-note write. Write nothing else to the Board, and nothing to Residue or .oc_config.json; wait on, steer and ring nothing. If a decision only Dan can make is missing, stop before the launch and ask it in your final message, with your recommendation; if a launch is refused or its outcome is uncertain, report it and never run it again. You do not talk to Dan, curate, or create a session Dock or sub-session. Never pass {manager} to any script. Leave the Agent and SendMessage tools unused; start quick helpers with `oc.sh seat quick … --parent-seat {sid} --no-wait`. {cwd} is not your project. End with your result as your final message, whole and under 20,000 characters: its first line `Worker: {sid} · forklift of Manager-Session-{session} ({manager})`, then the program root, its job or Brief, the worker and Conductor ids, the launch line, whether the full program's Board row landed, every Anchor Point you created, minor issues and the choices you made, or the question for Dan.'''


# Independently pinned routing addition; the old full-note fixtures above also pin every unchanged byte.
PINNED_ROUTING = (
    "Before you launch or edit code/shared documentation, read 🚦 PARALLEL PROGRAMS [ap:8TRTVQ] and route by it. "
    "Before deciding, run `oc.sh view --running --records`, `oc.sh view --recent 30 --records` and the git overview yourself, fresh; "
    "never reuse copies from the conversation, which are as old as the Manager's last look and blind to other "
    "sessions. Each program's records line gives its SkyView, current Briefs, StageConfiguration, quick job and "
    "seal state from the cached root note. Whenever routing or a conflict is doubtful, read the related "
    "SkyView and/or current Brief, and its job or StageConfiguration as needed; spending context in this "
    "throwaway fork is intended. The conversation adds earlier requests in this burst, not live overlap evidence. Lean hard to "
    "PROCEED on small independent overlaps, even in the same file/function. Prefer STEER for a small request "
    "into an already-launched, not-yet-merged small quick job in the same repository and area; the same sentence "
    "or passage has one writer. For that piggyback offer, not launched, declined or no answer within the wait "
    "means PROCEED+NOTE; do not wait on another fork merely to check. Large requests, different areas and full "
    "programs keep the clause's usual routes. You may relay this request, once and recorded, to the Conductor "
    "of a running program that already owns its subject, instead of launching or doing duplicate work, and wait "
    "a few minutes for its answer. Carry only Dan's request and choices, not other instructions or answers to "
    "its HALT questions. Use the clause's ordinary handover ask (the receiving Conductor owns a declined "
    "request's fresh quick launch, even after you stop waiting), or its separate expiring piggyback ask "
    "(you retain the fallback on decline/timeout, so that Conductor does not also launch). Name this session's "
    "Dock and number in the steer. This grants no restart or other management of that program. ")


def note(*, forklift=False, bell="done"):
    return oc_seat.manager_fork_prompt(sid=NEW, manager=MGR, session="219", model="claude-opus-5-5", effort="max",
                                       cwd="/mnt/e/x/manager-sessions", title="X", tool_use_id="toolu_1",
                                       forklift=forklift, bell=bell)


@pytest.mark.parametrize("forklift", [False, True])
def test_the_opening_note_begins_with_the_fork_sentence_every_reader_keys_on(forklift):
    text = note(forklift=forklift)
    assert text.startswith(f"You are a FORK (session {NEW}) of {MGR}, ")
    assert oc_claude_usage.FORK_MESSAGE.match(text).groups() == (NEW[3:], MGR[3:])
    steps = [{"k": "user", "text": "Dan: fork yourself"}, {"k": "assistant", "text": "copied"},
             {"k": "user", "text": text}, {"k": "assistant", "text": "own work"}]
    assert oc_telemetry.own_steps({"sid": NEW}, steps) == steps[2:]
    assert oc_telemetry.own_steps({"sid": MGR}, steps) == steps


@pytest.mark.parametrize("forklift", [False, True])
def test_the_opening_note_says_what_the_fork_is_and_is_not(forklift):
    text = note(forklift=forklift)
    for words in ("You are a fork of the Manager, doing one job for it.", "Manager-Session-219", "(tool call toolu_1)",
                  "Never run it, or any `--fork-self`, again.", "There is no socket and no job note",
                  "your result is your final message" if forklift else "write your result, and any PARTIAL, to a local Anchor Point",
                  f"read {oc_seat.QUICK_RULES} now", "you do not talk to Dan" if not forklift else "You do not talk to Dan",
                  f"Never pass {MGR} to any script.", "Leave the Agent and SendMessage tools unused",
                  f"--parent-seat {NEW} --no-wait", "/mnt/e/x/manager-sessions is not your project",
                  "anything" if not forklift else "the Program Interface's Manager obligations"):
        assert words in text, words
    assert ("launch no program" in text) is not forklift
    assert "You are no longer the Manager" not in text
    if forklift:
        for words in ("you are the Manager for this one job", "both providers (`oc.sh usage`)",
                      "network / modem trouble", "Claude host and OpenCode health", "same repository or files",
                      "Project Maps, Residue and the Board", "A real blocker means do not launch",
                      "Minor issues and soft signals are not a launch gate", "only local Anchor Points",
                      "slightly discouraged but encouraged when of clear value",
                      f'Created by forklift "X" ({NEW}).', "list every Anchor Point you create",
                      "Edit no other existing Anchor Point", "its one Board-row insert"):
            assert words in text
    else:
        for words in ("🍴 Result · X", "ap_id: auto", "Anchor Points are encouraged, consolidated there",
                      "its first line is the result's AP-ID", "Manager reads the Anchor Point and reports it to Dan"):
            assert words in text


@pytest.mark.parametrize("forklift", [False, True])
@pytest.mark.parametrize("bell", list(BELLS))
def test_every_opening_note_is_pinned_word_for_word_without_a_socket(forklift, bell):
    text = note(forklift=forklift, bell=bell)
    template = PINNED_FORKLIFT if forklift else PINNED_QUICK
    changes = (
        (
            ("), reusing what this conversation already holds. Quickly do every preparatory check",
             "). Reuse doctrine and material already read, not live overlap observations. " + PINNED_ROUTING +
             "Quickly do every preparatory check"),
            ("These are quick reads in your own background time, reusing what the conversation holds, then launch.",
             "These are quick reads in your own background time; launch when your route calls for it."),
            ("and its one Board-row insert. Residue, curation, steering other programs and the rest of the Board stay "
             "the primary Manager's. Launch exactly that program, once, in the foreground:",
             "and its one Board-row insert, plus the clause's one recorded relay of this request. Residue, curation, "
             "other steering and the rest of the Board stay the primary Manager's. If the route calls for a launch, "
             "launch exactly that program, once, in the foreground:"),
            ("; wait on, steer and ring nothing. If a decision only Dan can make is missing",
             "; apart from the clause's recorded relay and brief wait for its answer, wait on, steer and ring nothing. "
             "If a decision only Dan can make is missing"),
            ("({manager})`, then the program root",
             "({manager})`, then the clause's ROUTE line and one-sentence comparison, then the program root"),
            ("or the question for Dan.",
             "or the steer receipt, named wait event or question for Dan. STEERED, WAITING or ASK DAN with no launch "
             "rings exit 4 normally; the Manager relays it in one line without redoing your reconnaissance."),
        ) if forklift else (
            ("and steer, wait on, seal, resume, cancel or restart nothing the request does not name. Never pass",
             "and otherwise steer, wait on, seal, resume, cancel or restart nothing the request does not name. " +
             PINNED_ROUTING + "For you, PROCEED means doing your assigned job, not launching a program. Never pass"),
            ("its first line is the result's AP-ID, then `Worker:",
             "its first line is the result's AP-ID, then the clause's ROUTE line and one-sentence comparison, then `Worker:"),
        )
    )
    for old, new in changes:
        assert template.count(old) == 1
        template = template.replace(old, new, 1)
    expected = template.format(sid=NEW, manager=MGR, session="219", model="claude-opus-5-5", effort="max",
                               cwd="/mnt/e/x/manager-sessions", title="X", tool_use_id="toolu_1",
                               QUICK_RULES=oc_seat.QUICK_RULES, bell=BELLS[bell])
    assert text == expected
    assert text.startswith(f"You are a FORK (session {NEW}) of {MGR}, ")
    for forbidden in ("{socket}", "[ap:JOB]", "Your job is the note of", "## Result", "## PARTIAL"):
        assert forbidden not in text
    source = inspect.getsource(oc_seat.manager_fork_prompt)
    for forbidden in ('Your job is the note of', 'mode="socket"'):
        assert forbidden not in source and forbidden not in text  # DoD 9 scoped by [ap:6VFMYB]


@pytest.mark.parametrize("forklift", [False, True])
def test_overlap_routing_reads_live_and_has_only_the_recorded_request_exception(forklift):
    text = note(forklift=forklift)
    assert text.count(PINNED_ROUTING) == 1
    assert "reusing what the conversation holds" not in text
    for words in ("[ap:8TRTVQ]", "oc.sh view --running --records", "oc.sh view --recent 30 --records", "git overview yourself, fresh",
                  "records line gives its SkyView", "current Briefs, StageConfiguration, quick job", "seal state from the cached root note",
                  "Whenever routing or a conflict is doubtful", "SkyView and/or current Brief", "throwaway fork is intended",
                  "blind to other sessions", "Lean hard to PROCEED", "Prefer STEER for a small request",
                  "already-launched, not-yet-merged small quick job", "or passage has one writer",
                  "do not wait on another fork merely to check", "once and recorded", "its HALT questions",
                  "ordinary handover ask", "separate expiring piggyback ask", "does not also launch",
                  "ROUTE line and one-sentence comparison"):
        assert words in text
    if forklift:
        assert "If the route calls for a launch, launch exactly that program, once" in text
        assert "STEERED, WAITING or ASK DAN with no launch rings exit 4 normally" in text


# ------------------------------------------------------------------------------------ proof

@pytest.mark.parametrize("equals", [False, True])
@pytest.mark.parametrize("title", [TITLE, "recon: is 'scout' ambiguous (no edits)"])
def test_a_background_one_call_is_proved_by_its_title(tmp_path, equals, title):
    path = write(tmp_path / "m.jsonl", one_call_manager((call_cmd(title, equals=equals) + " | tail -n 5", "toolu_1")))
    assert proof(path, title) == ("call-entry-1", "toolu_1", True)
    path = write(tmp_path / "f.jsonl", one_call_manager(background=None))
    assert proof(path) == ("call-entry-1", "toolu_1", False)


def test_a_subagent_sidechain_agent_prompt_or_request_blob_never_proves_the_call(tmp_path):
    sub = tmp_path / "m" / "subagents"
    sub.mkdir(parents=True)
    write(sub / "agent-1.jsonl", [entry("assistant", [bash(call_cmd(), "s1")], sidechain=True)])
    base = one_call_manager()[:3]
    cases = {"subagent file": base,
             "sidechain line": base + [entry("assistant", [bash(call_cmd(), "s2")], sidechain=True)],
             "Agent prompt": base + [entry("assistant", [{"type": "tool_use", "id": "a1", "name": "Agent", "input": {
                 "subagent_type": "manager-quick", "prompt": call_cmd()}}])],
             "request blob": base + [{"type": "api-request-blob", "uuid": "b1", "isSidechain": False, "message": {
                 "role": "assistant", "content": [bash(call_cmd(), "b1")]}}]}
    for entries in cases.values():
        with pytest.raises(SystemExit, match=r"no Bash call holding `--fork-self` and exactly this title.*Nothing started"):
            proof(write(tmp_path / "m.jsonl", entries))


def test_the_newest_of_two_matching_calls_is_chosen(tmp_path):
    path = write(tmp_path / "m.jsonl", one_call_manager((call_cmd(), "toolu_1"), (call_cmd(), "toolu_2")))
    assert proof(path)[:2] == ("call-entry-2", "toolu_2")


def test_a_late_flush_is_found_on_a_later_poll(tmp_path):
    entries = one_call_manager()
    path = write(tmp_path / "m.jsonl", entries[:-1])
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            write(Path(path), entries)
    assert proof(path, budget=5, sleep=sleep) == ("call-entry-1", "toolu_1", True)
    assert sleeps == [oc_seat.FORK_CALL_POLL_S] * 2


def test_a_tail_with_no_main_chain_tool_call_is_refused_at_once(tmp_path):
    path = write(tmp_path / "m.jsonl", [entry("user", "hi"), entry("assistant", [{"type": "text", "text": "Hi."}])])
    with pytest.raises(SystemExit, match="not in the form this script reads.*Nothing started"):
        proof(path, budget=120, sleep=lambda s: pytest.fail("polled"))
    with pytest.raises(SystemExit, match="transcript is not on disk"):
        proof(str(tmp_path / "missing.jsonl"))


def test_the_check_widens_its_window_past_a_large_sibling_result(tmp_path, monkeypatch):
    monkeypatch.setattr(oc_seat, "FORK_TAIL_WINDOWS", (200, 1 << 20))
    path = write(tmp_path / "m.jsonl", one_call_manager() + [entry("user", [answer("sibling", "x" * 5000)])])
    assert proof(path) == ("call-entry-1", "toolu_1", True)


def test_a_call_that_already_made_a_fork_is_skipped_so_two_identical_calls_are_two_calls(tmp_path):
    entries = one_call_manager((call_cmd(), "toolu_1"), (call_cmd(), "toolu_2"))
    path = write(tmp_path / "m.jsonl", entries)
    reads = []

    def claimed():
        reads.append(1)
        return {"toolu_2"}
    assert proof(path, claimed=claimed)[:2] == ("call-entry-1", "toolu_1")
    assert len(reads) == 1
    later = entry("assistant", [bash(call_cmd(), "toolu_3")], mid="msg_2", uid="call-entry-3")
    sleeps = []

    def sleep(_):
        sleeps.append(1)
        if len(sleeps) == 3:
            write(Path(path), entries + [later])
    assert proof(path, budget=5, sleep=sleep, claimed=lambda: {"toolu_1", "toolu_2"})[:2] == ("call-entry-3", "toolu_3")
    with pytest.raises(SystemExit, match="already made a fork"):
        proof(path, claimed=lambda: {"toolu_1", "toolu_2", "toolu_3"})


def test_a_matching_call_from_before_this_process_is_ignored(tmp_path):
    old = {**one_call_manager()[-1], "timestamp": iso(oc_seat.FORK_CALL_MAX_AGE_S + 60)}
    path = write(tmp_path / "m.jsonl", one_call_manager()[:3] + [old])
    with pytest.raises(SystemExit, match="no Bash call holding"):
        proof(path)
    fresh = {**one_call_manager()[-1], "timestamp": iso(1), "uuid": "call-entry-new"}
    assert proof(write(Path(path), one_call_manager()[:3] + [old, fresh]))[0] == "call-entry-new"
    for stamp in (None, "not-a-timestamp"):
        undated = {**old, "timestamp": stamp}
        assert proof(write(Path(path), one_call_manager()[:3] + [undated]))[0] == old["uuid"]


@pytest.mark.parametrize("have,want", [("fix", "fix the parser"), ("fix the parser", "fix")])
def test_a_title_never_matches_a_substring_either_way(tmp_path, have, want):
    with pytest.raises(SystemExit, match="no Bash call holding"):
        proof(write(tmp_path / "m.jsonl", one_call_manager((call_cmd(have), "t1"))), want)


@pytest.mark.parametrize("command,title", [('oc.sh quick --title "Fix X"', TITLE),
                                         ('oc.sh quick --title "read --fork-self docs"', "read --fork-self docs")])
def test_the_fork_flag_must_be_a_word_not_a_title_fragment(tmp_path, command, title):
    with pytest.raises(SystemExit, match="no Bash call holding"):
        proof(write(tmp_path / "m.jsonl", one_call_manager((command, "t1"))), title)


@pytest.mark.parametrize("forklift", [False, True])
def test_the_forklift_kind_must_agree_on_both_sides(tmp_path, forklift):
    with pytest.raises(SystemExit, match="no Bash call holding"):
        proof(write(tmp_path / "m.jsonl", one_call_manager((call_cmd(forklift=not forklift), "t1"))), forklift=forklift)


def test_an_unsplittable_command_never_matches_and_is_not_an_error(tmp_path):
    with pytest.raises(SystemExit, match="no Bash call holding.*Nothing started"):
        proof(write(tmp_path / "m.jsonl", one_call_manager((call_cmd() + ' "unclosed', "t1"))))


def subcall(path, *, ago=0, timestamp=None):
    folder = Path(path).with_suffix("") / "subagents"
    folder.mkdir(parents=True, exist_ok=True)
    sub = entry("assistant", [bash(call_cmd(), "sub1")], uid="sub-entry", sidechain=True,
                timestamp=iso(ago) if timestamp is None else timestamp)
    write(folder / "agent-x.jsonl", [sub])


def test_the_subagent_guard_refuses_an_older_unclaimed_manager_call_but_not_a_newer_one(tmp_path):
    calls = one_call_manager()
    calls[-1]["timestamp"] = iso(60)
    path = write(tmp_path / "m.jsonl", calls + [entry("user", [answer("toolu_1", "refused")])])
    subcall(path, ago=1)
    with pytest.raises(SystemExit, match="a subagent of this Manager made this call.*Nothing started"):
        proof(path)
    calls[-1]["timestamp"] = iso(0)
    assert proof(write(Path(path), calls)) == ("call-entry-1", "toolu_1", True)


@pytest.mark.parametrize("which", ["main missing", "sub missing", "sub invalid"])
def test_the_subagent_guard_refuses_incomparable_entry_times(tmp_path, which):
    calls = one_call_manager()
    if which == "main missing":
        calls[-1].pop("timestamp")
    path = write(tmp_path / "m.jsonl", calls)
    subcall(path)
    subpath = Path(path).with_suffix("") / "subagents" / "agent-x.jsonl"
    if which != "main missing":
        sub = json.loads(subpath.read_text())
        sub["timestamp"] = None if which == "sub missing" else "unparseable"
        write(subpath, [sub])
    with pytest.raises(SystemExit, match="a subagent of this Manager made this call"):
        proof(path)


@pytest.mark.parametrize("tool", ["Agent", "Task"])
def test_the_tripwire_refuses_a_subagent_call_without_its_folder(tmp_path, tool):
    calls = one_call_manager()
    calls.insert(1, entry("assistant", [{"type": "tool_use", "id": "a1", "name": tool, "input": {"prompt": "x"}}]))
    with pytest.raises(SystemExit, match="the subagent transcripts are not where this script reads them.*Nothing started"):
        proof(write(tmp_path / "m.jsonl", calls))


def test_no_subagents_folder_and_no_agent_call_is_proved_even_after_taskstop(tmp_path):
    calls = one_call_manager()
    assert proof(write(tmp_path / "m.jsonl", calls)) == ("call-entry-1", "toolu_1", True)
    calls.insert(1, entry("assistant", [{"type": "tool_use", "id": "stop", "name": "TaskStop", "input": {}}]))  # R1
    assert proof(write(tmp_path / "m.jsonl", calls)) == ("call-entry-1", "toolu_1", True)


def test_a_read_error_mid_poll_is_a_named_refusal_not_a_traceback(tmp_path, monkeypatch):
    path = write(tmp_path / "m.jsonl", one_call_manager()[:-1])
    original, reads = Path.open, []

    def open_(self, *a, **k):
        if str(self) == path:
            reads.append(1)
            if len(reads) == 2:
                raise PermissionError("fixture read denied")
        return original(self, *a, **k)
    monkeypatch.setattr(Path, "open", open_)
    with pytest.raises(SystemExit, match="the Manager's transcript could not be read.*fixture read denied.*Nothing started"):
        proof(path)
    assert len(reads) == 2


def test_a_subagent_read_error_is_also_a_named_refusal(tmp_path, monkeypatch):
    path = write(tmp_path / "m.jsonl", one_call_manager())
    subcall(path, ago=10)
    original = Path.open

    def open_(self, *a, **k):
        if self.name == "agent-x.jsonl":
            raise PermissionError("subagent read denied")
        return original(self, *a, **k)
    monkeypatch.setattr(Path, "open", open_)
    with pytest.raises(SystemExit, match="the Manager's transcript could not be read.*subagent read denied.*Nothing started"):
        proof(path)


def test_the_settle_waits_for_the_retrys_own_foreground_entry_on_the_third_poll(tmp_path):
    old = {**one_call_manager()[-1], "timestamp": iso(60)}
    new = entry("assistant", [bash(call_cmd(), "retry", background=False)], uid="retry-entry")
    path = write(tmp_path / "m.jsonl", [old])
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        if len(sleeps) == 2:
            write(Path(path), [old, new])
    assert proof(path, budget=120, sleep=sleep) == ("retry-entry", "retry", False)
    assert sleeps == [0.25, 0.25]


@pytest.mark.parametrize("stamp", [iso(60), None])
def test_the_settle_uses_the_older_or_undated_match_after_five_seconds(tmp_path, stamp):
    old = {**one_call_manager()[-1], "timestamp": stamp}
    clock = Clock()
    assert proof(write(tmp_path / "m.jsonl", [old]), budget=120, clock=clock) == ("call-entry-1", "toolu_1", True)
    assert clock.t == 5


def test_the_settle_never_exceeds_the_proofs_remaining_budget(tmp_path):
    old = {**one_call_manager()[-1], "timestamp": iso(60)}
    clock = Clock()
    assert proof(write(tmp_path / "m.jsonl", [old]), budget=0.5, clock=clock)[1] == "toolu_1"
    assert clock.t == 0.5


def test_a_fresh_entry_is_proved_on_the_first_poll_without_settling(tmp_path):
    fresh = {**one_call_manager()[-1], "timestamp": iso(1)}
    assert proof(write(tmp_path / "m.jsonl", [fresh]), budget=120,
                 sleep=lambda s: pytest.fail("a fresh match settled"))[1] == "toolu_1"


def test_a_subagent_entry_appearing_during_the_settle_is_guarded(tmp_path):
    old = {**one_call_manager()[-1], "timestamp": iso(60)}
    path = write(tmp_path / "m.jsonl", [old])
    clock = Clock()

    def sleep(s):
        if clock.t == 0.5:
            subcall(path)
    with pytest.raises(SystemExit, match="a subagent of this Manager made this call.*Nothing started"):
        proof(path, budget=120, clock=clock, sleep=sleep)
    assert clock.t == 5


def test_a_refusal_names_the_fallback_chain():
    for forklift in (False, True):
        text = str(oc_seat.fork_call_refusal("why", forklift=forklift))
        assert "why. Nothing started. Tell Dan in a line and fall back: a fresh quick worker" in text
        assert "with the context written into its socket" in text
        assert ("or launch the program yourself" in text) is forklift
        assert "two-step" not in text and "--fork-self --socket" not in text


# ------------------------------------------------------------------------------------ launch/admission/grammar

class Host:
    def __init__(self, record, *, health=None, ctx=None, rows=()):
        self.health = {"seat_fork": 1, "manager_fork": 1} if health is None else health
        self.record, self.rows, self.states, self.state_reads = record, list(rows), {}, []
        self.ctx = {"ok": True, "fraction_used": 0.30} if ctx is None else ctx
        self.socket_path = Path("/nonexistent/host.sock")

    def call(self, method, **params):
        if method == "state":
            eid = params["execution_id"]
            self.state_reads.append(eid)
            return self.record if eid == MGR else self.states.get(eid, next(
                (r for r in self.rows if r.get("execution_id") == eid), {}))
        if method == "list":
            return self.rows
        if method == "get_context_usage":
            return self.ctx
        raise AssertionError(method)


class NoStdin:
    def read(self, *a, **k):
        pytest.fail("the fork path read stdin")

    def fileno(self):
        pytest.fail("the fork path inspected stdin")

    def isatty(self):
        pytest.fail("the fork path inspected stdin")


@pytest.fixture
def forking(tmp_path, monkeypatch):
    record = {"execution_id": MGR, "role": "Manager", "cwd": str(tmp_path), "generation": 3,
              "requested_model": "claude-opus-5-5", "requested_effort": "max",
              "native_transcript_path_expected": write(tmp_path / "m.jsonl", one_call_manager())}
    state = {"host": Host(record), "started": []}
    monkeypatch.setattr(oc_claude, "connect", lambda path=None: state["host"])
    monkeypatch.setattr(oc_seat, "manager_session", lambda: "219")
    monkeypatch.setattr(oc_seat.time, "time", lambda: NOW)
    monkeypatch.setattr(sys, "stdin", NoStdin())

    def forbidden(*a, **k):
        pytest.fail("a Manager fork constructed Workflowy or used a socket")
    monkeypatch.setattr(oc_seat, "Workflowy", forbidden)
    monkeypatch.setattr(oc_common, "Workflowy", forbidden)
    monkeypatch.setattr(oc_seat, "guard_socket", forbidden)
    monkeypatch.setattr(oc_seat, "SocketRunNumber", forbidden)

    def start(**kw):
        kw["precheck"](kw["host"])
        state["started"].append(kw)
        kw["on_id"](kw["execution_id"], {})
        return {"message_uuid": "abcdef0123", "acknowledged": True}
    monkeypatch.setattr(oc_claude, "start", start)
    state["env"] = {"CC_CONTEXT_EXECUTION_ID": MGR, "CC_CONTEXT_BINDING": "the-binding",
                    "CC_CONTEXT_HOST_SOCKET": "/nonexistent/host.sock"}
    for key, value in state["env"].items():
        monkeypatch.setenv(key, value)
    return state


def set_call(forking, *, title=TITLE, forklift=False, background=True):
    path = Path(forking["host"].record["native_transcript_path_expected"])
    write(path, one_call_manager((call_cmd(title, forklift=forklift), "toolu_1"), background=background))


def test_the_one_call_forks_a_titled_labelled_quick_fork_worker_and_a_forklift(forking, capsys):
    for forklift in (False, True):
        set_call(forking, title="X", forklift=forklift)
        fork = oc_seat.start_manager_fork(title="X", forklift=forklift, environ=forking["env"])
        kw = forking["started"][-1]
        assert fork == (kw["execution_id"], "toolu_1", True) and kw["socket"] is None
        assert kw["fork_from"] == MGR and kw["fork_self_binding"] == "the-binding"
        assert kw["role"] == "worker" and kw["cwd"] == forking["host"].record["cwd"] and kw["system_prompt"] is None
        assert (kw["model"], kw["effort"]) == ("claude-opus-5-5", "max")
        assert all(kw.get(k) is None for k in ("program", "parent", "agent"))
        labels = kw["labels"]
        kind = "🚜 forklift" if forklift else "⚡ quick fork worker"
        assert labels["title"] == f"Manager-Session-219 ▸ {kind} · X"
        assert {k: labels[k] for k in ("kind", "quick", "forked_from", "fork_source", "fork_point", "fork_entry",
                                       "manager_session", "composer", "variant", "fork_call", "fork_mode")} == {
            "kind": "fork", "quick": True, "forked_from": MGR, "fork_source": "manager", "fork_point": "call",
            "fork_entry": "call-entry-1", "manager_session": "219", "composer": "manager", "variant": "max",
            "fork_call": "toolu_1", "fork_mode": "background"}
        assert (labels.get("forklift") is True) is forklift and "job" not in labels and "socket" not in labels
        assert kw["prompt"] == oc_seat.manager_fork_prompt(
            sid=fork.eid, manager=MGR, session="219", model="claude-opus-5-5", effort="max", cwd=kw["cwd"],
            title="X", tool_use_id="toolu_1", forklift=forklift, bell="launch" if forklift else "done")
    out = capsys.readouterr().out
    assert "· X" in out and "socket" not in out and "the-binding" not in out


def test_a_subagent_caller_is_refused(forking):
    forking["host"].record["role"] = "worker"
    with pytest.raises(SystemExit, match="works only from a Claude Manager.*fork never forks.*Nothing started"):
        oc_seat.start_manager_fork(title=TITLE, environ=forking["env"])
    assert forking["started"] == []


@pytest.mark.parametrize("forklift", [False, True])
def test_fork_self_variant_sets_only_the_forks_launch_effort(forking, monkeypatch, forklift):
    set_call(forking, forklift=forklift)
    before = dict(forking["host"].record)
    monkeypatch.setattr(sys, "argv", quick_argv(*(["--forklift"] if forklift else []),
                                              "--variant", "high", "--doorbell", "none"))
    assert oc_seat.main() == 0
    kw = forking["started"][-1]
    assert (kw["model"], kw["effort"]) == ("claude-opus-5-5", "high")
    assert kw["labels"]["variant"] == "high" and " at high" in kw["prompt"]
    assert kw["fork_from"] == MGR and kw["system_prompt"] is None
    assert forking["host"].record == before  # same native fork path; no setting change on the Manager


@pytest.mark.parametrize("code", [1, 5, 6, 7, 8])
def test_a_pre_announcement_unknown_start_names_its_id_without_claiming_nothing_started(
        forking, monkeypatch, capsys, code):
    seen = []

    def fail(**kw):
        seen.append(kw["execution_id"])
        raise oc_backend.SeatError("fixture start error", code)
    monkeypatch.setattr(oc_claude, "new_execution_id", lambda: NEW)
    monkeypatch.setattr(oc_claude, "start", fail)
    monkeypatch.setattr(sys, "argv", quick_argv())
    assert oc_seat.main() == code
    assert seen == [NEW] and forking["started"] == []
    captured = capsys.readouterr()
    output = captured.out + captured.err
    if code == oc_backend.EXIT_UNKNOWN:
        assert f"⚠️ the launch outcome is unknown: check oc.sh seat info {NEW} before calling again." in captured.out
        assert "Nothing started" not in output
    else:
        assert "   Nothing started.\n" in captured.out
        assert "the launch outcome is unknown" not in output


def test_a_failed_first_input_names_the_fork_and_releases_only_its_live_slot(forking, tmp_path, monkeypatch, capsys):
    from test_oc_claude import ForkStartClock, ForkStartHost
    host = ForkStartHost(tmp_path, races=2)
    host.records = {MGR: forking["host"].record}
    host.context = {"ok": True, "fraction_used": 0.30}
    forking["host"] = host
    timer = ForkStartClock()

    def start(**kw):
        return REAL_START(**kw, ready_budget=4, poll=0.25, clock=timer, sleep=timer.sleep)
    monkeypatch.setattr(oc_claude, "start", start)
    monkeypatch.setattr(oc_claude, "new_execution_id", lambda: NEW)
    monkeypatch.setattr(sys, "argv", quick_argv())
    assert oc_seat.main() == 1
    captured = capsys.readouterr()
    assert f"🍴 QUICK FORK WORKER {NEW}" in captured.out
    assert "the Manager is working" not in captured.err and "the fork is working on its own turn" in captured.err
    assert NEW in captured.err and "nothing was delivered; its live-fork slot was released" in captured.err
    assert "Nothing started" not in captured.out + captured.err
    assert host.sent == [] and host.methods().count("send") == 2 and host.methods().count("launch") == 1
    assert oc_seat.live_manager_forks(host, MGR) == []
    assert oc_seat.claimed_fork_calls(host) == {"toolu_1"}  # release is not permission to repeat this launch


def test_what_a_manager_fork_refuses(forking, capsys):
    def refused(match, env=None, error=SystemExit):
        with pytest.raises(error, match=match) as e:
            oc_seat.start_manager_fork(title=TITLE, environ=forking["env"] if env is None else env)
        assert "Nothing started" in str(e.value) and forking["started"] == []
    refused("runs only inside a Claude Code Manager", env={})
    host = forking["host"]
    host.health = {"seat_fork": 1}
    refused("predates Manager forks", error=oc_backend.SeatError)
    host.health = {"seat_fork": 1, "manager_fork": 1}
    host.ctx = {"ok": True, "fraction_used": 0.61}
    refused(r"at 61% of its window, past the 55% fork limit.*fresh quick worker")
    host.ctx = {"ok": True, "fraction_used": 0.20}
    busy = {"execution_id": "cc-x", "forked_from": MGR, "state": "ready", "owner": "host",
            "native_session_state": "running", "labels": {"title": "a fork"}}
    host.rows = [busy] * oc_seat.MAX_LIVE_MANAGER_FORKS
    assert oc_seat.MAX_LIVE_MANAGER_FORKS == 12
    refused("12 forks of this Manager are still live")
    host.rows = [{**busy, "native_session_state": "idle", "primary_turn_open": False,
                  "last_input_uuid": "m1", "first_launched_at": NOW - 10}] * 3
    host.ctx = {"ok": False, "reason": "no completed model call yet"}
    oc_seat.start_manager_fork(title=TITLE, environ=forking["env"])
    assert "context reading is unknown" in capsys.readouterr().out


@pytest.mark.parametrize("live", [8, 11])
def test_a_burst_can_start_the_ninth_through_twelfth_manager_fork(forking, live):
    busy = {"execution_id": "cc-x", "forked_from": MGR, "state": "ready", "owner": "host",
            "native_session_state": "running", "labels": {"title": "another fork"}}
    forking["host"].rows = [busy] * live
    oc_seat.start_manager_fork(title=TITLE, environ=forking["env"])
    assert len(forking["started"]) == 1


def test_the_subagent_guard_starts_no_fork_even_beside_a_refused_main_chain_call(forking):
    path = Path(forking["host"].record["native_transcript_path_expected"])
    calls = one_call_manager()
    calls[-1]["timestamp"] = iso(1)
    write(path, calls + [entry("user", [answer("toolu_1", "Nothing started")])])
    subcall(path)
    with pytest.raises(SystemExit, match="a subagent of this Manager made this call.*Nothing started"):
        oc_seat.start_manager_fork(title=TITLE, environ=forking["env"])
    assert forking["started"] == []


def test_a_call_that_already_made_a_fork_is_refused_inside_the_lock(forking, monkeypatch, capsys):
    monkeypatch.setattr(oc_seat, "claimed_fork_calls", lambda host: set())
    forking["host"].rows = [{"execution_id": "cc-other", "forked_from": MGR, "state": "ready", "owner": "host",
                             "native_session_state": "idle", "last_input_uuid": "m1", "first_launched_at": NOW - 10,
                             "labels": {"fork_call": "toolu_1"}}]
    with pytest.raises(SystemExit, match="this call already made a fork: cc-other.*Nothing started"):
        oc_seat.start_manager_fork(title=TITLE, environ=forking["env"])
    assert forking["started"] == [] and "was created but no fork started" not in capsys.readouterr().out


def test_the_precheck_counts_twelve_live_forks_inside_the_start_lock_before_the_quota_guard(forking, monkeypatch):
    order = []

    @contextmanager
    def lock(host, budget=120.0):
        order.append("lock")
        try:
            yield
        finally:
            order.append("unlock")

    class Admitted(Exception):
        pass

    def quota(*a, **k):
        order.append("quota")
        raise Admitted()
    monkeypatch.setattr(oc_seat, "claimed_fork_calls", lambda host: set())  # isolate the precheck's list, not proof's
    monkeypatch.setattr(oc_claude, "start", REAL_START)
    monkeypatch.setattr(oc_claude, "start_lock", lock)
    monkeypatch.setattr(oc_claude, "quota_guard", quota)
    host = forking["host"]
    plain = host.call
    host.call = lambda method, **p: (order.append(method) if method == "list" else None, plain(method, **p))[1]
    busy = {"execution_id": "cc-x", "forked_from": MGR, "state": "ready", "owner": "host",
            "native_session_state": "running", "labels": {"title": "a fork"}}
    host.rows = [busy] * 11
    with pytest.raises(Admitted):
        oc_seat.start_manager_fork(title=TITLE, environ=forking["env"])
    assert order == ["lock", "list", "quota", "unlock"]
    order.clear()
    host.rows = [busy] * 12
    with pytest.raises(SystemExit, match="12 forks of this Manager are still live \\(at most 12\\)"):
        oc_seat.start_manager_fork(title=TITLE, environ=forking["env"])
    assert order == ["lock", "list", "unlock"]


def test_a_fork_between_launch_and_first_message_keeps_its_place(forking):
    host = forking["host"]
    busy = {"execution_id": "cc-busy", "forked_from": MGR, "state": "ready", "owner": "host",
            "native_session_state": "running", "labels": {}}
    unsent = {**busy, "execution_id": "cc-unsent", "native_session_state": "idle", "last_input_uuid": None,
              "first_launched_at": NOW - 10}
    host.rows = [busy] * 11 + [unsent]
    with pytest.raises(SystemExit, match="12 forks of this Manager are still live.*"):
        oc_seat.start_manager_fork(title=TITLE, environ=forking["env"])
    assert forking["started"] == []
    unsent["first_launched_at"] = NOW - oc_claude.UNSENT_GRACE - 100
    oc_seat.start_manager_fork(title=TITLE, environ=forking["env"])
    assert len(forking["started"]) == 1


def test_missing_summary_fields_read_state_for_this_managers_forks_only(forking):
    host = forking["host"]
    own = {"execution_id": "cc-own", "forked_from": MGR, "state": "ready", "owner": "host",
           "native_session_state": "idle", "labels": {}}
    other = {**own, "execution_id": "cc-other", "forked_from": "cc-another-manager"}
    host.states["cc-own"] = {**own, "last_input_uuid": None, "first_launched_at": NOW - 10}
    host.rows, host.state_reads = [own, other], []
    assert [r["execution_id"] for r in oc_seat.live_manager_forks(host, MGR)] == ["cc-own"]
    assert host.state_reads == ["cc-own"]


NO_SOCKET_FORM = ('❌ --fork-self has no socket form: the fork is this conversation, and its job is what was just asked. '
                  'Run oc.sh seat quick --fork-self [--forklift] --title "‹what›" in the background '
                  '(Bash run_in_background: true, timeout: 3600000): the call is its doorbell. '
                  'A Manager meeting this refusal has an old system prompt, started before the merge; '
                  'a fresh Manager Session has the new one. Nothing started.')


def quick_argv(*extra, title=TITLE):
    return ["oc_seat.py", "quick", "--fork-self", *([] if title is None else ["--title", title]), *extra]


def test_fork_self_takes_no_id_and_no_seat_flags(monkeypatch):
    for extra in (["--agent", "quick-opus55-high"], ["--fork-from", MGR], ["--before", "msg_x"],
                  ["--map", "[ap:MAP001]"], ["--no-map"], ["--program", "[ap:PROG01]"],
                  ["--parent-seat", "ses_helper"], ["--parent", "[ap:DOCK01]"], ["--helper"],
                  ["--model", "anthropic/claude-opus-5-5"], ["--target", "0.4"],
                  ["--no-wait"], ["--timeout", "9"], ["--force"]):
        monkeypatch.setattr(sys, "argv", quick_argv(*extra))
        with pytest.raises(SystemExit, match="--fork-self takes only.*Nothing started"):
            oc_seat.main()


def test_the_one_call_grammar_refuses_socket_first_blank_title_and_wrong_bell_without_reading_stdin(monkeypatch):
    monkeypatch.setattr(sys, "stdin", NoStdin())
    monkeypatch.setattr(oc_seat, "start_manager_fork", lambda **k: pytest.fail("a refused call reached the fork"))
    monkeypatch.setattr(sys, "argv", quick_argv("--socket", "[ap:OLD001]", "--agent", "quick-opus55-high"))
    with pytest.raises(SystemExit) as e:
        oc_seat.main()
    assert str(e.value) == NO_SOCKET_FORM
    for title in (None, "", "   "):
        monkeypatch.setattr(sys, "argv", quick_argv(title=title))
        with pytest.raises(SystemExit, match="needs --title.*Nothing started"):
            oc_seat.main()
    for bell in ("launch", "program"):
        monkeypatch.setattr(sys, "argv", quick_argv("--doorbell", bell))
        with pytest.raises(SystemExit, match="a quick fork worker's call rings when it finishes; --doorbell none.*Nothing started"):
            oc_seat.main()
    monkeypatch.setattr(sys, "argv", ["oc_seat.py", "quick", "--agent", "quick-opus55-high"])
    with pytest.raises(SystemExit) as e:
        oc_seat.main()
    assert e.value.code == 2  # other routes still require --socket, as argparse did


def test_the_boom_groups_a_manager_fork_under_its_manager():
    manager = {"execution_id": MGR, "role": "Manager", "labels": None}
    fork = {"execution_id": NEW, "role": "worker", "forked_from": MGR, "socket": None,
            "labels": {"title": "Manager-Session-219 ▸ ⚡ quick fork worker · x", "kind": "fork",
                       "quick": True, "manager_session": "219", "forked_from": MGR}}
    sessions = [oc_claude_view.session_of(manager), oc_claude_view.session_of(fork)]
    assert sessions[1]["metadata"]["forked_from"] == MGR
    owner = oc_running.RunningOwners(sessions).owner(NEW)
    assert (owner.kind, owner.identity, owner.evidence) == ("manager", MGR, "fork source")
    assert not oc_running.is_manager_session(sessions[1])


# ------------------------------------------------------------------------------------ the call is its bell

@pytest.mark.parametrize("forklift", [False, True])
@pytest.mark.parametrize("background", [False, True])
def test_doorbell_none_starts_and_returns_without_a_bell_or_wait(forking, monkeypatch, capsys, forklift, background):
    set_call(forking, forklift=forklift, background=background)

    def never(*a, **k):
        pytest.fail("--doorbell none armed or waited")
    monkeypatch.setattr(oc_doorbell, "identify_caller", never)
    monkeypatch.setattr(oc_doorbell, "arm", never)
    monkeypatch.setattr(oc_seat, "wait_any", never)
    monkeypatch.setattr(oc_seat, "forklift_wait", never)
    monkeypatch.setattr(sys, "argv", quick_argv(*(["--forklift"] if forklift else []), "--doorbell", "none"))
    assert oc_seat.main() == 0
    kw = forking["started"][-1]
    eid = kw["execution_id"]
    kind = "🚜 FORKLIFT" if forklift else "🍴 QUICK FORK WORKER"
    out = capsys.readouterr().out
    assert f"{kind} {eid} · {TITLE} started · no doorbell (--doorbell none): read its final message: " \
           f"oc.sh seat info {eid} --tail 20000" in out
    assert "foreground" not in out and BELLS["none"] in kw["prompt"] and kw["socket"] is None


@pytest.mark.parametrize("extra", [(), ("--forklift",), ("--forklift", "--doorbell", "program")])
def test_a_foreground_one_call_forks_and_returns_with_the_doorbell_line(forking, monkeypatch, capsys, extra):
    forklift = "--forklift" in extra
    set_call(forking, forklift=forklift, background=None)

    def never(*a, **k):
        pytest.fail("a foreground call armed or waited")
    monkeypatch.setattr(oc_seat, "wait_any", never)
    monkeypatch.setattr(oc_doorbell, "identify_caller", never)
    monkeypatch.setattr(sys, "argv", quick_argv(*extra))
    assert oc_seat.main() == 0
    kw = forking["started"][-1]
    out = capsys.readouterr().out
    assert "⚠️ this call ran in the foreground: no automatic doorbell; ring it with oc.sh seat wait " in out
    assert ("--tail 20000" in out) is not forklift
    assert ("--forklift" in out) is forklift
    assert ("--doorbell program" in out) is ("program" in extra)
    assert kw["labels"]["fork_mode"] == "foreground" and BELLS["foreground"] in kw["prompt"]


class Bell:
    def __init__(self, log, target):
        self.log, self.target, self.lines, self.refused = log, target, [], False

    def handed_over(self):
        return None

    def disarm(self):
        self.log.append(("disarm", self.target))


@pytest.mark.parametrize("code", [2, 5])
def test_the_background_call_is_its_quick_fork_workers_doorbell(forking, monkeypatch, capsys, code):
    caller = oc_doorbell.Caller(MGR, "claude", 1.0, "Manager-Session-219")
    monkeypatch.setattr(oc_doorbell, "identify_caller", lambda label: (caller, None))
    log, waits = [], []
    monkeypatch.setattr(oc_doorbell, "arm", lambda target, who, **k: (log.append(("arm", target)), Bell(log, target))[1])

    def wait(sid, max_s, superseded=None, tail=None):
        waits.append((sid, max_s, superseded, tail))
        return code
    monkeypatch.setattr(oc_seat, "wait_any", wait)
    monkeypatch.setattr(sys, "argv", quick_argv("--max", "60"))
    assert oc_seat.main() == code
    eid = forking["started"][-1]["execution_id"]
    assert log == [("arm", eid), ("disarm", eid)]
    assert waits[0][:2] == (eid, 60) and waits[0][2] is not None and waits[0][3] == oc_seat.FORK_RESULT_TAIL == 20000
    assert f"re-arm its doorbell (in the background): `oc.sh seat wait {eid} --max 60 --tail 20000`" in capsys.readouterr().out


@pytest.mark.parametrize("doorbell", [None, "program"])
def test_the_background_forklift_passes_the_caller_and_bell_to_its_waiter(forking, monkeypatch, doorbell):
    set_call(forking, forklift=True)
    caller = oc_doorbell.Caller(MGR, "claude", 1.0, "Manager-Session-219")
    monkeypatch.setattr(oc_doorbell, "identify_caller", lambda label: (caller, None))
    seen = []
    monkeypatch.setattr(oc_seat, "forklift_wait", lambda fork, **k: (seen.append((fork, k)), 2)[1])
    monkeypatch.setattr(sys, "argv", quick_argv("--forklift", *([] if doorbell is None else ["--doorbell", doorbell])))
    assert oc_seat.main() == 2
    assert seen == [(forking["started"][-1]["execution_id"], {"doorbell": doorbell, "max_s": 3000, "caller": caller})]
    assert BELLS[doorbell or "launch"] in forking["started"][-1]["prompt"]
