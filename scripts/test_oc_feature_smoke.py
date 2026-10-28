"""Inert tests for the OPT-IN finished-feature smoke (`oc_feature_smoke.py`) and its `oc_cache_check.main` delegation.

Nothing here contacts OpenCode, Workflowy, a provider or the payload proxy: a fake world stands in for the CLI
(`Smoke._transport`), the read-only client calls (`oc_client.oc_session/oc_messages/oc_status/oc_inbox/oc_delete/
oc_request`) and the tap/proxy log files under a temporary state directory. The fake copies a base history into each
fork exactly like the native fork does (fresh message ids only), and each essential check has a witness test that the
check FAILS when the evidence it claims to verify is broken.
"""
import json
import sys
import time
import uuid

import oc_cache_check
import oc_feature_smoke as smoke

PROGRAM = "[ap:PROG1]"
AGENT = "sol61-xhigh"
MODEL = "openai/gpt-6.1-sol-fast#xhigh"
SOCKETS = {"prepass": "[ap:PP01]", "f1": "[ap:F101]", "f2": "[ap:F201]",
           "extension": "[ap:EX01]", "f3": "[ap:F301]"}
MARKERS = {"prepass": "PPS-PREPASS-OK", "f1": "PPS-F1-OK", "f2": "PPS-F2-OK",
           "extension": "PPS-EXT-OK", "f3": "PPS-F3-OK"}
MATERIAL = {"handles": ["[ap:READ1]", "[ap:READ2]"], "expect": ["ALPHA-READ", "BETA-READ"],
            "extension_expect": ["GAMMA-EXT"], "extension_handles": ["[ap:READ3]"]}
BASE_INPUT = 18_200
F1_TOKEN = {"input": 500, "output": 60, "reasoning": 10, "cache": {"read": 18_000, "write": 0}}
F2_TOKEN = {"input": 500, "output": 60, "reasoning": 10, "cache": {"read": 0, "write": 0}}
F3_TOKEN = {"input": 500, "output": 60, "reasoning": 10, "cache": {"read": 22_000, "write": 0}}
EXT_TOKEN = {"input": 300, "output": 40, "reasoning": 10, "cache": {"read": 23_000, "write": 0}}
BASE_A_TOKEN = {"input": 8_000, "output": 60, "reasoning": 0, "cache": {"read": 0, "write": 0}}
BASE_B_TOKEN = {"input": 200, "output": 40, "reasoning": 10, "cache": {"read": 18_000, "write": 0}}
LONG_A = "ALPHA-READ " + "a" * 1_200
LONG_B = "BETA-READ " + "b" * 1_200
LONG_EXT = "GAMMA-EXT " + "g" * 1_200


# ------------------------------------------------------------------------------------ fake rows

def user(mid, created, text):
    return {"id": mid, "type": "user", "time": {"created": created}, "text": text}


def assistant(mid, created, completed, tokens, calls=(), text=""):
    content = []
    for name, inp, out, *overrides in calls:
        state = {"status": "completed", "input": inp, "content": [{"type": "text", "text": out}]}
        if overrides:
            state.update(overrides[0])
        content.append({"type": "tool", "name": name, "state": state})
    if text:
        content.append({"type": "text", "text": text})
    return {"id": mid, "type": "assistant", "time": {"created": created, "completed": completed},
            "tokens": tokens, "cost": 0.0, "content": content}


def idle(mid, created):
    return {"id": mid, "type": "idle", "time": {"created": created}}


def read_call(handle, output):
    return ("workflowy_node", {"verb": "read", "params": {"node_handle": handle}},
            f"[name: not editable] {handle} Fixture note\n\n{output}")


def write_call(socket, marker):
    # node.edit diff=none returns this documented metadata header, not arbitrary success text.
    result = {"success": True, "no_op": False, "note_changed": True,
              "node_id": str(uuid.uuid5(uuid.NAMESPACE_URL, socket)), "note_chars": len(marker)}
    return ("workflowy_node", {"verb": "edit", "params": {"node_handle": socket, "note": marker, "diff": "none"}},
            json.dumps(result))


def total(tokens):
    return tokens["input"] + tokens["cache"]["read"] + tokens["cache"]["write"]


def stamp(ms):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ms / 1000))


def local_stamp(ms):
    return time.strftime("%Y%m%dT%H%M%S", time.localtime(ms / 1000)) + f"{int(ms) % 1000:03d}"


class World:
    """A tiny in-memory OpenCode + `oc_seat.sh`, with knobs for the mutation witnesses."""

    def __init__(self, tmp_path, **knobs):
        self.tmp, self.t, self.seq = tmp_path, 1_700_000_000_000, 0
        self.sessions, self.messages, self.deleted, self.calls = {}, {}, set(), []
        self.base_id = "ses_base0000000000000000000001"
        self.jobs, self.delete_codes, self.readback_codes = {}, {}, {}
        self.resumed = False
        self.knobs = knobs
        self.socket_notes = {socket: f"Your assignment: write {MARKERS[job]} into {socket}. F1-ASSIGNMENT"
                             for job, socket in SOCKETS.items()}
        self.tap_rows, self.proxy_rows = [], []
        self.write_state_logs()

    # -- time / ids ----------------------------------------------------------------

    def now(self):
        self.t += 1_000
        return self.t

    def mid(self):
        self.seq += 1
        return f"msg_{self.seq:04d}"

    def write_state_logs(self):
        tap = self.tmp / "state" / "opencode-companion" / "openai-usage.jsonl"
        proxy = self.tmp / "state" / "opencode-payload-proxy" / "requests.jsonl"
        tap.parent.mkdir(parents=True, exist_ok=True)
        proxy.parent.mkdir(parents=True, exist_ok=True)
        tap.write_text("".join(json.dumps(row) + "\n" for row in self.tap_rows), encoding="utf-8")
        proxy.write_text("".join(json.dumps(row) + "\n" for row in self.proxy_rows), encoding="utf-8")

    # -- sessions and history -------------------------------------------------------

    def socket_call(self, job, *, write=False):
        """Model actual note delivery/write state and real result shapes, including failed operations with no effect."""
        socket, marker = SOCKETS[job], MARKERS[job]
        failure = self.knobs.get("socket_failure")
        if failure and failure[:2] == (job, "write" if write else "read"):
            mode = failure[2]
            call = write_call(socket, marker) if write else read_call(socket, self.socket_notes[socket])
            status = "error" if mode == "native-error" else "running" if mode == "incomplete" else "completed"
            out = json.dumps({"success": False, "operation": "node.edit" if write else "node.read",
                              "error": {"code": "fixture_failed", "message": "No operation/effect"}})
            if mode == "unknown":
                out = "updated" if write else ""
            elif mode == "no-write":
                out = json.dumps({"success": True, "no_op": True, "note_changed": False,
                                  "node_id": str(uuid.uuid5(uuid.NAMESPACE_URL, socket)), "note_chars": 0})
            return (*call[:2], out, {"status": status, "error": "fixture native failure" if status == "error" else ""})
        if not write:
            return read_call(socket, self.socket_notes[socket])
        self.socket_notes[socket] = marker
        return write_call(socket, marker)

    def refresh(self, sid):
        session, messages = self.sessions[sid], self.messages[sid]
        own, _copied = smoke.split_session_turns(session, messages)
        sums = {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}}
        for message in own:
            for key in ("input", "output", "reasoning"):
                sums[key] += message["tokens"].get(key, 0)
            for key in ("read", "write"):
                sums["cache"][key] += (message["tokens"].get("cache") or {}).get(key, 0)
        session["tokens"], session["cost"] = sums, 0.0

    def base_session(self):
        self.sessions[self.base_id] = {
            "id": self.base_id, "agent": AGENT,
            "model": {"providerID": "openai", "id": "gpt-6.1-sol-fast", "variant": "xhigh"},
            "metadata": {"kind": "seat", "fresh": True, "seat_role": "lead", "pre_pass": True, "program": PROGRAM,
                         "socket": SOCKETS["prepass"], "agent": AGENT, "model": "openai/gpt-6.1-sol-fast",
                         "variant": "xhigh", "manager_session": "1"},
            "location": {"directory": "/home/daniel347x"}, "time": {"created": self.now(), "updated": self.now()},
            "cost": 0.0, "tokens": {}, "title": "prepass",
        }
        a_out, b_out = ("tiny", "tiny") if self.knobs.get("shallow_base") else (LONG_A, LONG_B)
        answer = self.knobs.get("base_answer", "Read both. ALPHA-READ BETA-READ")
        self.messages[self.base_id] = [
            user(self.mid(), self.now(), f"Read {MATERIAL['handles'][0]} and {MATERIAL['handles'][1]}."),
            assistant(self.mid(), self.now(), self.now(), BASE_A_TOKEN,
                      calls=[read_call(MATERIAL["handles"][0], a_out),
                              self.socket_call("prepass")]),
            assistant(self.mid(), self.now(), self.now(), BASE_B_TOKEN,
                      calls=[read_call(MATERIAL["handles"][1], b_out),
                              self.socket_call("prepass", write=True)],
                      text=answer),
            idle(self.mid(), self.now()),
        ]
        self.refresh(self.base_id)
        self.jobs["prepass"] = self.base_id
        return self.base_id

    def framing(self, job, sid, base, socket):
        override = self.knobs.get("framing")
        if callable(override):
            return override(job, sid, base, socket)
        return (f"You are a FORK (session {sid}) of {base} in program {PROGRAM}. Everything above this message is "
                f"{base}'s conversation: its reading and its work, carried into this session. Treat it as already "
                f"read, and do not continue the base's own task. The base's socket {SOCKETS['prepass']} is not yours "
                f"to write. Your job is the note of {socket}: read it in full now and follow it exactly. It is your "
                "assignment and your authority, and it says where your report goes.")

    def job_history(self, job, sid, base, socket, created):
        tokens = {"f1": F1_TOKEN, "f2": F2_TOKEN, "f3": F3_TOKEN}[job]
        if job == "f1" and "f1_read" in self.knobs:
            tokens = {**tokens, "cache": {**tokens["cache"], "read": self.knobs["f1_read"]}}
        phrases = list(dict.fromkeys(MATERIAL["expect"]
                                     + (MATERIAL["extension_expect"] if job == "f3" else [])))
        if job == "f3" and self.knobs.get("f3_answer") == "extension_only":
            phrases = MATERIAL["extension_expect"]
        calls = [] if self.knobs.get("skip_socket_read") == job else [self.socket_call(job)]
        if self.knobs.get("mention_write") == job:
            calls.append(("workflowy_node", {"verb": "edit",
                                             "params": {"node_handle": "[ap:OTHER]",
                                                        "note": f"write {MARKERS[job]} into {socket}"}}, "updated"))
        elif self.knobs.get("foreign_write") == job:
            other = "f2" if job == "f1" else "f1"
            calls.append(self.socket_call(other, write=True))
        else:
            calls.append(self.socket_call(job, write=True))
        if self.knobs.get("foreign_tool") == job:
            tool = self.knobs.get("foreign_tool_name", "bash")
            calls.append((tool, {"command": "touch /tmp/not-ours"}, "done"))
        return [user(self.mid(), created + 1, self.framing(job, sid, base, socket)),
                assistant(self.mid(), created + 2, created + 3, tokens, calls=calls,
                          text="Recalled: " + " ".join(phrases)),
                idle(self.mid(), created + 4)]

    def make_fork(self, job, base, socket, variant):
        src = self.sessions[base]
        created = max(m["time"]["created"] for m in self.messages[base]) + 10
        sid = f"ses_{job}00000000000000000000000001"
        copied = json.loads(json.dumps(self.messages[base]))
        for message in copied:                    # the native fork's ONLY new field: the fresh outer message id
            message["id"] = self.mid()
        copied_count = len(copied)                 # freeze BEFORE extending this same list with the own turn
        self.messages[sid] = copied
        self.sessions[sid] = {
            "id": sid, "agent": src["agent"], "model": {**src["model"], "variant": variant},
            "location": dict(src["location"]),
            "metadata": {"kind": "seat", "seat_role": "lead", "role": "lead", "fresh": False, "pre_pass": False,
                         "quick": False, "seat_base": base, "warm_sid": base, "program": PROGRAM, "socket": socket,
                         "agent": src["agent"], "model": "openai/gpt-6.1-sol-fast", "variant": variant,
                         "parent_seat": None, "context_target": None, "manager_session": "1"},
            "fork": {"sessionID": base,
                     "boundary": {"type": "through", "messageID": self.messages[base][-1]["id"]}},
            "time": {"created": created, "updated": created}, "cost": 0.0, "tokens": {}, "title": job,
        }
        self.messages[sid].extend(self.job_history(job, sid, base, socket, created))
        self.jobs[job] = sid
        self.refresh(sid)
        if callable(self.knobs.get("mutate_fork")):
            self.knobs["mutate_fork"](job, self.messages[sid], copied_count)
            self.refresh(sid)
        if self.knobs.get("taps", True):
            self.add_tap(job)
        if job == "f2" and self.knobs.get("effort_evidence", True):
            self.add_effort_evidence("f2", after=self.knobs.get("effort_after", "high"),
                                     folded=self.knobs.get("effort_folded", 1),
                                     offset_s=self.knobs.get("effort_offset_s", 0.0))
            if self.knobs.get("extra_effort_row"):
                self.add_effort_evidence("f2", after=self.knobs.get("extra_effort_after", "high"),
                                         offset_s=self.knobs.get("extra_effort_offset_s", 60.0))
        return sid

    # -- tap / proxy fixtures -------------------------------------------------------

    def find_new_call(self, sid):
        own, _copied = smoke.split_session_turns(self.sessions[sid], self.messages[sid])
        return own[0]

    def add_tap(self, job):
        sid, new = self.jobs[job], self.find_new_call(self.jobs[job])
        self.tap_rows.append({"time": stamp(new["time"]["completed"]), "session": sid, "status": 200,
                              "reasoning": {"effort": self.sessions[sid]["model"]["variant"]},
                              "usage": {"input_tokens": total(new["tokens"]),
                                        "cached_tokens": new["tokens"]["cache"]["read"]}})
        self.write_state_logs()

    def add_effort_evidence(self, job, *, after="high", before="xhigh", folded=1, offset_s=0.0):
        sid = self.jobs[job]
        new = self.find_new_call(sid)
        created, completed = new["time"]["created"], new["time"]["completed"]
        start = created + int(offset_s * 1000)
        self.proxy_rows.append({"ts": local_stamp(start), "session": sid, "path": "/openai/v1/responses",
                                "route": "openai", "model": "gpt-6.1-sol", "status": 200,
                                "ms": max(0, completed - start), "effort_updates_folded": folded,
                                "effort_before": before, "effort_after": after})
        self.write_state_logs()

    # -- the CLI --------------------------------------------------------------------

    def transport(self, argv, timeout):
        args = list(argv[2:])
        self.calls.append(args)
        verb = args[0]
        if verb == "dispatch":
            sid = self.base_session()
            code = self.knobs.get("dispatch_exit", 0)
            return code, (f"🚀 LEAD SEAT {sid} · {AGENT} (openai/gpt-6.1-sol-fast · xhigh) · fresh, no seat base · "
                          f"socket {SOCKETS['prepass']} · PRE-PASS\n"), ""
        if verb == "fork":
            base = args[args.index("--base") + 1]
            socket = args[args.index("--socket") + 1]
            job = next(k for k, v in SOCKETS.items() if v == socket and k in ("f1", "f2", "f3"))
            variant = args[args.index("--variant") + 1] if "--variant" in args else "xhigh"
            sid = self.make_fork(job, base, socket, variant)
            code = 1 if self.knobs.get("fork_exit") == job else 0
            return code, (f"🚀 SEAT {sid} · forked from {base} ({AGENT}|openai/gpt-6.1-sol-fast · {variant}) · "
                          f"socket {socket}\n   --no-wait: `oc_seat wait {sid} --max 540` until it exits 0\n"), ""
        if verb == "resume":
            base = args[1]
            self.resumed = True
            socket = args[args.index("--socket") + 1]
            assert "--extend" in args
            if callable(self.knobs.get("mutate_old_fork")):
                self.knobs["mutate_old_fork"](self.messages[self.jobs["f1"]])
            created = self.now()
            self.messages[base].extend([
                user(self.mid(), created, f"FOLLOW-UP. Read {SOCKETS['extension']} and follow it."),
                assistant(self.mid(), created + 1, created + 2, EXT_TOKEN,
                          calls=[self.socket_call("extension"),
                                 read_call(MATERIAL["extension_handles"][0], LONG_EXT),
                                 self.socket_call("extension", write=True)],
                          text="Extension read. GAMMA-EXT"),
                idle(self.mid(), created + 3)])
            self.refresh(base)
            return 0, (f"🧮 {base} · seat · {AGENT}|openai/gpt-6.1-sol-fast · context 1,000 / 872,000 tokens = 0%\n"
                       f"📨 FOLLOW-UP sent to {base} · socket {socket} · xhigh · clause [ap:BQP053]\n"), ""
        if verb == "wait":
            sid = args[1]
            if self.knobs.get("running") == "extension" and self.resumed and sid == self.base_id:
                return 2, f"⏳ {sid} still running after 60s\n", ""
            if callable(self.knobs.get("on_wait")):
                self.knobs["on_wait"](self, sid)
            return 0, self.wait_text(sid), ""
        if verb == "info":
            return 0, self.info_text(args[1]), ""
        raise AssertionError(f"unexpected CLI call {args}")

    # -- rendered CLI text ----------------------------------------------------------

    def account_line(self, sid):
        session, messages = self.sessions[sid], self.messages[sid]
        own, copied = smoke.split_session_turns(session, messages)
        base = next(m for m in reversed(copied) if (m.get("time") or {}).get("completed"))
        new = own[0]
        tokens, bt = new["tokens"], base["tokens"]
        read, write, inp, new_total = (tokens["cache"]["read"], tokens["cache"]["write"],
                                      tokens["input"], total(tokens))
        base_total = total(bt)
        state = ("CACHE HIT" if read >= 0.9 * base_total else
                 "CACHE MISS" if read < 0.1 * base_total else "PARTIAL HIT")
        return (f"🍴 fork of {session['fork']['sessionID']} · first NEW call {new['id']}@"
                f"{new['time']['created']}→{new['time']['completed']} · read={read:,} write={write:,} "
                f"input={inp:,} total={new_total:,} · base_input={base_total} (copied call {base['id']}@"
                f"{base['time']['created']}→{base['time']['completed']}) · new-input cached "
                f"{100 * read / new_total:.1f}% · base-input coverage {100 * read / base_total:.1f}% · {state}")

    def wait_text(self, sid):
        session, messages = self.sessions[sid], self.messages[sid]
        own, copied = smoke.split_session_turns(session, messages)
        lines = [f"✅ {sid} idle · seat · {len(own)} turn(s) · $0.0000 · outcome succeeded"]
        if copied:
            lines.append(f"   excludes $0.0000 copied history ({len(copied)} turn(s)); charged in the source session(s)")
        for k, message in enumerate(own):
            tokens = message["tokens"]
            lines.append(f"    turn {k:>2}: read={tokens['cache']['read']:>8,}  write={tokens['cache']['write']:>7,}  "
                         f"out={tokens['output']:>6,}  $0.0000")
        if session.get("fork"):
            line = self.account_line(sid)
            if callable(self.knobs.get("wait_account")):
                line = self.knobs["wait_account"](line, sid)
            lines.append(line)
        lines += ["-" * 96, smoke.c.msg_text(own[-1])[-1500:]]
        return "\n".join(lines) + "\n"

    def info_text(self, sid):
        session = self.sessions[sid]
        md = session["metadata"]
        lines = [f"ℹ️  {sid} · idle · {session.get('title')}",
                 f"   kind={md.get('kind')} · socket={md.get('socket')} · parent=None · "
                 f"model=openai/gpt-6.1-sol-fast#{session['model']['variant']} · messages={len(self.messages[sid])}"]
        if session.get("fork"):
            line = self.account_line(sid)
            if callable(self.knobs.get("info_account")):
                line = self.knobs["info_account"](line, sid)
            lines.append(line)
        last = [m for m in self.messages[sid] if m["type"] == "assistant"][-1]
        lines += ["-" * 96, smoke.c.msg_text(last)[-1500:]]
        return "\n".join(lines) + "\n"

    # -- oc_client seams ------------------------------------------------------------

    def session(self, sid):
        if sid in self.deleted:
            raise smoke.c.OpenCodeError(404, "NotFound", "session not found")
        return self.sessions[sid]

    def status(self):
        return True, {}

    def delete(self, sid):
        code = self.delete_codes.get(sid, 204)
        if code < 400:
            self.deleted.add(sid)
        return code, None

    def request(self, method, path, body=None, timeout=60):
        sid = path.rsplit("/", 1)[-1]
        if sid in self.readback_codes:
            return self.readback_codes[sid], {}
        return (404, {}) if sid in self.deleted else (200, self.sessions.get(sid))

    def install(self, monkeypatch):
        monkeypatch.setattr(smoke.Smoke, "_transport", self.transport)
        monkeypatch.setattr(smoke.c, "oc_session", self.session)
        monkeypatch.setattr(smoke.c, "oc_messages", lambda sid: self.messages[sid])
        monkeypatch.setattr(smoke.c, "oc_status", self.status)
        monkeypatch.setattr(smoke.c, "oc_inbox", lambda sid: [])
        monkeypatch.setattr(smoke.c, "oc_delete", self.delete)
        monkeypatch.setattr(smoke.c, "oc_request", self.request)


# ------------------------------------------------------------------------------------ harness

def manifest(tmp_path, **overrides):
    data = {"schema": "pre-seeded-feature-smoke/1", "label": "helper-smoke", "program": PROGRAM, "agent": AGENT,
            "model": MODEL, "changed_level": "high", "sockets": dict(SOCKETS), "markers": dict(MARKERS),
            "socket_expect": {"f1": "F1-ASSIGNMENT"}, "material": dict(MATERIAL),
            "reading": {"min_tool_calls": 2, "min_tool_output_chars": 1500},
            "timeouts_s": {"cli": 30, "wait": 60}}
    data.update(overrides)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def run(tmp_path, monkeypatch, **knobs):
    world = World(tmp_path, **knobs)
    world.install(monkeypatch)
    loaded = smoke.load_manifest(manifest(tmp_path, **knobs.pop("manifest_overrides", {})))
    engine = smoke.Smoke(loaded, receipt_dir=tmp_path / "receipts", state_dir=tmp_path / "state", wait_max=60)
    return world, engine, engine.run()


def check(engine, cid):
    return next(c for c in engine.checks if c["id"] == cid)


def failed(engine):
    return [c["id"] for c in engine.checks if c["required"] and c["ok"] is not True]


# ------------------------------------------------------------------------------------ the happy journey

def test_the_full_journey_passes_and_records_its_evidence(tmp_path, monkeypatch):
    world, engine, code = run(tmp_path, monkeypatch)
    assert code == 0, failed(engine)
    assert failed(engine) == []
    dispatch = next(c for c in world.calls if c[0] == "dispatch")
    assert "--lead" in dispatch and "--pre-pass" in dispatch
    assert dispatch[dispatch.index("--model") + 1] == MODEL
    assert dispatch[dispatch.index("--socket") + 1] == SOCKETS["prepass"]
    assert dispatch[dispatch.index("--program") + 1] == PROGRAM
    forks = [c for c in world.calls if c[0] == "fork"]
    assert [c[c.index("--socket") + 1] for c in forks] == [SOCKETS["f1"], SOCKETS["f2"], SOCKETS["f3"]]
    assert [c[c.index("--base") + 1] for c in forks] == [world.base_id] * 3     # every fork is direct from the base
    assert "--variant" not in forks[0] and "--variant" not in forks[2]           # same-level F1/F3
    assert forks[1][forks[1].index("--variant") + 1] == "high"                   # changed-level F2
    resume_at = world.calls.index(next(c for c in world.calls if c[0] == "resume"))
    assert resume_at < world.calls.index(forks[2])                               # completed extension before F3
    assert "--extend" in world.calls[resume_at]
    assert world.calls[resume_at][world.calls[resume_at].index("--socket") + 1] == SOCKETS["extension"]
    # counters
    assert check(engine, "f1.hit_observed")["ok"] is True
    assert check(engine, "f3.hit_observed")["ok"] is True
    assert engine.observations["f2"]["account"]["state"] == smoke.STATE_MISS    # allowed
    assert check(engine, "f2.state_recorded")["ok"] is True
    # both views equal and the frozen recomputation agrees for every fork
    for job in ("f1", "f2", "f3"):
        assert check(engine, f"{job}.wait_info_equal")["ok"] is True, check(engine, f"{job}.wait_info_equal")
        assert check(engine, f"{job}.account_matches_history")["ok"] is True
        assert check(engine, f"{job}.copied_history")["ok"] is True
        assert check(engine, f"{job}.cost_partition")["ok"] is True
        assert check(engine, f"{job}.socket_read")["ok"] is True
        assert check(engine, f"{job}.marker_write")["ok"] is True
        assert check(engine, f"{job}.only_assigned_writes")["ok"] is True
        assert check(engine, f"{job}.framing_conversation")["ok"] is True
        assert check(engine, f"{job}.recall")["ok"] is True
        assert check(engine, f"{job}.no_material_reread")["ok"] is True
        assert check(engine, f"{job}.native_identity")["ok"] is True
        assert check(engine, f"{job}.no_inherited_flags")["ok"] is True
    assert check(engine, "prepass.no_fork_account")["ok"] is True
    assert engine.observations["f3"]["expected"]["copied_assistants"] == 3       # extension inherited by F3
    assert engine.observations["f1"]["expected"]["copied_assistants"] == 2       # F1 predates it
    assert check(engine, "extension.identity_preserved")["ok"] is True
    assert check(engine, "f1.old_fork_immutable")["ok"] is True
    assert check(engine, "f2.old_comparison_frozen")["ok"] is True
    # post-proxy effort is the proof; the pre-proxy tap fact is only recorded
    assert check(engine, "f2.effective_effort")["ok"] is True
    assert engine.observations["f2"]["effective_effort"]["effort_after"] == "high"
    assert engine.observations["f2"]["pre_proxy_effort"] == {"effort": "high"}
    assert check(engine, "f1.tap_counters")["ok"] is True
    # exact-id cleanup with a 404 readback for every created session
    assert engine.cleanup["unresolved"] == []
    assert {d["id"] for d in engine.cleanup["deleted"]} == {world.jobs[j] for j in ("prepass", "f1", "f2", "f3")}
    assert all(check(engine, f"{job}.cleanup_exact_id")["ok"] for job in ("prepass", "f1", "f2", "f3"))
    receipt = json.loads(next((tmp_path / "receipts").glob("*.json")).read_text())
    assert receipt["run"]["exit"] == 0 and receipt["counts"]["required_failed"] == 0
    assert receipt["pin"]["commit"] and receipt["pin"]["files"]["scripts/oc_feature_smoke.py"]
    assert all(row["log"] for row in receipt["commands"])
    assert (tmp_path / "receipts" / receipt["commands"][0]["log"]).exists()


# ------------------------------------------------------------------------------------ mutation witnesses

def test_a_mutated_copied_tool_payload_fails_copied_history(tmp_path, monkeypatch):
    def mutate(job, messages, _copied):
        if job == "f1":
            for part in messages[1]["content"]:
                if part.get("type") == "tool":
                    part["state"]["content"][0]["text"] = "REDACTED"
    _world, engine, code = run(tmp_path, monkeypatch, mutate_fork=mutate)
    assert code == 1
    assert check(engine, "f1.copied_history")["ok"] is False
    assert "f1.copied_history" in failed(engine)


def test_a_mutated_copied_timestamp_fails_copied_history(tmp_path, monkeypatch):
    def mutate(job, messages, _copied):
        if job == "f1":
            messages[0]["time"]["created"] = 1
    _world, engine, code = run(tmp_path, monkeypatch, mutate_fork=mutate)
    assert code == 1
    assert check(engine, "f1.copied_history")["ok"] is False


def test_wait_and_info_must_show_the_same_account(tmp_path, monkeypatch):
    _world, engine, code = run(tmp_path, monkeypatch,
                               info_account=lambda line, sid: line.replace("read=18,000", "read=1"))
    assert code == 1
    assert check(engine, "f1.wait_info_equal")["ok"] is False
    assert check(engine, "f1.account_matches_history")["ok"] is True     # wait still matches the history
    assert engine.observations["f1"]["wait_info_diffs"]


def test_an_account_disagreeing_with_the_history_fails_recomputation(tmp_path, monkeypatch):
    _world, engine, code = run(tmp_path, monkeypatch,
                               wait_account=lambda line, sid: line.replace("base_input=18200", "base_input=999"))
    assert code == 1
    assert check(engine, "f1.wait_info_equal")["ok"] is False
    assert check(engine, "f1.account_matches_history")["ok"] is False


def test_f1_miss_is_reported_and_never_retried_into_a_pass(tmp_path, monkeypatch):
    world, engine, code = run(tmp_path, monkeypatch, f1_read=0)
    assert code == 1
    assert engine.observations["f1"]["account"]["state"] == smoke.STATE_MISS
    assert check(engine, "f1.hit_observed")["ok"] is False
    assert len([c for c in world.calls if c[0] == "fork"]) == 3                   # no refork
    assert len([c for c in world.calls if c[0] == "wait" and c[1] == world.jobs["f1"]]) == 1   # no re-wait


def test_changed_level_effort_needs_post_proxy_evidence(tmp_path, monkeypatch):
    _world, engine, code = run(tmp_path, monkeypatch, effort_evidence=False)
    # the tap still says high, but a pre-proxy top-level effort is not proof of the effective effort
    assert code == 1
    assert check(engine, "f2.effective_effort")["ok"] is None
    assert engine.observations["f2"]["effective_effort"]["verified"] is None
    assert engine.observations["f2"]["pre_proxy_effort"] == {"effort": "high"}
    assert "no successful /openai/v1/responses proxy row overlaps" in \
        engine.observations["f2"]["effective_effort"]["reason"]


def test_missing_tap_rows_are_a_stated_limit_not_an_invented_pass(tmp_path, monkeypatch):
    _world, engine, code = run(tmp_path, monkeypatch, taps=False)
    assert code == 0, failed(engine)
    assert engine.observations["f1"]["tap"] is None
    assert any("no OpenAI usage-tap row" in line for line in engine.limits)


def test_tap_full_input_includes_a_supplied_cache_write_without_inference(tmp_path, monkeypatch):
    engine = smoke.Smoke(smoke.load_manifest(manifest(tmp_path)), receipt_dir=tmp_path / "receipts",
                         state_dir=tmp_path / "state", wait_max=60)
    for job in smoke.FORK_JOBS:
        engine.sessions[job] = {"id": f"ses_{job}"}
        engine.observations[job] = {"expected": {"new": {"time": {"created": 1000, "completed": 2000},
            "input": 100, "read": 900, "write": 50, "total": 1050}}}
    rows = [{"session": f"ses_{job}", "status": 200, "time": "1970-01-01T00:00:02Z",
             "usage": {"input_tokens": 1050, "cached_tokens": 900}} for job in smoke.FORK_JOBS]
    monkeypatch.setattr(engine, "_log_rows", lambda path, ids: rows if path.name == "openai-usage.jsonl" else [])
    engine.corroborate()
    assert all(check(engine, f"{job}.tap_counters")["ok"] for job in smoke.FORK_JOBS)
    assert engine.observations["f1"]["tap"]["api_write"] == 50
    assert engine.observations["f1"]["tap"]["api_full_input"] == 1050


def test_cleanup_failure_is_unresolved_and_never_called_cleaned(tmp_path, monkeypatch):
    def prep(world):
        world.delete_codes[world.jobs["f2"]] = 500
        world.readback_codes[world.jobs["f2"]] = 200
    world = World(tmp_path)
    world.install(monkeypatch)
    engine = smoke.Smoke(smoke.load_manifest(manifest(tmp_path)), receipt_dir=tmp_path / "receipts",
                         state_dir=tmp_path / "state", wait_max=60)
    original = world.make_fork

    def make(job, base, socket, variant):
        sid = original(job, base, socket, variant)
        if job == "f2":
            prep(world)
        return sid
    world.make_fork = make
    code = engine.run()
    assert code == 1
    assert check(engine, "f2.cleanup_exact_id")["ok"] is False
    assert engine.cleanup["unresolved"] and engine.cleanup["unresolved"][0]["name"] == "f2"
    assert all(d["cleaned"] for d in engine.cleanup["deleted"] if d["name"] != "f2")
    receipt = json.loads(next((tmp_path / "receipts").glob("*.json")).read_text())
    assert receipt["cleanup"]["unresolved"]


def test_an_unfinished_extension_stops_before_f3_and_keeps_the_ids(tmp_path, monkeypatch):
    world, engine, code = run(tmp_path, monkeypatch, running="extension")
    assert code == 2
    assert engine.sessions["prepass"]["id"] == world.base_id
    assert engine.sessions["f1"]["id"] and engine.sessions["f2"]["id"]
    assert "f3" not in engine.sessions
    assert not any(c[0] == "fork" and c[c.index("--socket") + 1] == SOCKETS["f3"] for c in world.calls)
    assert "still running" in engine.error
    receipt = json.loads(next((tmp_path / "receipts").glob("*.json")).read_text())
    assert receipt["run"]["exit"] == 2 and receipt["sessions"]["f1"]["id"]
    assert {d["name"] for d in engine.cleanup["deleted"]} == {"f1", "f2", "prepass"}


def test_a_failed_dispatch_announcement_keeps_the_id_and_cleans_it_up(tmp_path, monkeypatch):
    world, engine, code = run(tmp_path, monkeypatch, dispatch_exit=1)
    assert code == 2
    assert engine.sessions["prepass"]["id"] == world.base_id
    assert "after announcing" in engine.error
    assert engine.cleanup["deleted"][0]["id"] == world.base_id and engine.cleanup["deleted"][0]["cleaned"]
    receipt = json.loads(next((tmp_path / "receipts").glob("*.json")).read_text())
    assert receipt["sessions"]["prepass"]["id"] == world.base_id and receipt["run"]["exit"] == 2


def test_a_failed_fork_announcement_keeps_the_id_and_stops_before_the_job_runs(tmp_path, monkeypatch):
    world, engine, code = run(tmp_path, monkeypatch, fork_exit="f1")
    assert code == 2
    assert engine.sessions["f1"]["id"] == world.jobs["f1"]
    assert "f2" not in engine.sessions and "f3" not in engine.sessions
    assert not any(c[0] == "wait" and c[1] == world.jobs["f1"] for c in world.calls)   # no job assumed started
    assert {d["name"] for d in engine.cleanup["deleted"]} == {"prepass", "f1"}
    assert all(d["cleaned"] for d in engine.cleanup["deleted"])


def test_an_invalid_manifest_creates_nothing(tmp_path, monkeypatch):
    world = World(tmp_path)
    world.install(monkeypatch)
    duplicated = manifest(tmp_path, sockets={**SOCKETS, "f2": SOCKETS["f1"]})
    assert smoke.main(manifest=str(duplicated), receipt_dir=str(tmp_path / "r"),
                      state_dir=str(tmp_path / "s")) == 2
    assert world.calls == []


def test_sb_snapshot_framing_is_rejected(tmp_path, monkeypatch):
    def sb(job, sid, base, socket):
        return (f"You are a LEAD SEAT (session {sid}), forked from seat base {base} for program {PROGRAM}. "
                "Everything above is the seat base: shared material, a snapshot, opened by its map. Reply ACK.")
    _world, engine, code = run(tmp_path, monkeypatch, framing=sb)
    assert code == 1
    assert check(engine, "f1.framing_conversation")["ok"] is False
    assert check(engine, "f1.framing_named")["ok"] is False


def test_a_foreign_socket_or_marker_write_is_rejected(tmp_path, monkeypatch):
    _world, engine, code = run(tmp_path, monkeypatch, foreign_write="f1")
    assert code == 1
    assert check(engine, "f1.only_assigned_writes")["ok"] is False
    assert check(engine, "f1.marker_write")["ok"] is False


def test_a_write_that_merely_mentions_the_socket_in_its_note_is_not_obedience(tmp_path, monkeypatch):
    _world, engine, code = run(tmp_path, monkeypatch, mention_write="f1")
    assert code == 1
    assert check(engine, "f1.marker_write")["ok"] is False      # its TARGET is another node, not the owned socket
    assert "unassigned/untargeted" in check(engine, "f1.marker_write")["detail"]


def test_an_f3_answer_with_only_extension_phrases_fails(tmp_path, monkeypatch):
    _world, engine, code = run(tmp_path, monkeypatch, f3_answer="extension_only")
    assert code == 1
    assert check(engine, "f3.recall")["ok"] is False
    assert "ALPHA-READ" in check(engine, "f3.recall")["detail"]


def test_a_shell_call_is_shown_unverified_not_silently_read_only(tmp_path, monkeypatch):
    _world, engine, code = run(tmp_path, monkeypatch, foreign_tool="f1", foreign_tool_name="bash")
    assert code == 1
    assert check(engine, "f1.no_unverified_tool_calls")["ok"] is None
    assert "UNVERIFIED" in check(engine, "f1.no_unverified_tool_calls")["detail"]
    assert check(engine, "f1.no_unverified_tool_calls")["facts"]["unverified"] == ["bash"]


def test_a_file_edit_is_a_foreign_write_not_an_ignored_call(tmp_path, monkeypatch):
    _world, engine, code = run(tmp_path, monkeypatch, foreign_tool="f1", foreign_tool_name="edit")
    assert code == 1
    assert check(engine, "f1.only_assigned_writes")["ok"] is False
    assert "FILE-WRITING" in check(engine, "f1.only_assigned_writes")["detail"]


def test_a_known_read_only_tool_keeps_the_write_proof_green(tmp_path, monkeypatch):
    _world, engine, code = run(tmp_path, monkeypatch, foreign_tool="f1", foreign_tool_name="context_status")
    assert code == 0, failed(engine)
    assert check(engine, "f1.no_unverified_tool_calls")["ok"] is True


def test_a_later_correct_effort_cannot_substitute_for_a_wrong_first_request(tmp_path, monkeypatch):
    _world, engine, code = run(tmp_path, monkeypatch, effort_after="xhigh", extra_effort_row=True,
                               extra_effort_after="high", extra_effort_offset_s=60.0)
    assert code == 1
    assert check(engine, "f2.effective_effort")["ok"] is False
    assert "cannot substitute" in check(engine, "f2.effective_effort")["detail"]


def test_an_effort_row_outside_the_first_call_window_is_unverified(tmp_path, monkeypatch):
    _world, engine, code = run(tmp_path, monkeypatch, effort_offset_s=60.0)
    assert code == 1
    assert check(engine, "f2.effective_effort")["ok"] is None
    assert "overlaps the first NEW call window" in engine.observations["f2"]["effective_effort"]["reason"]


def test_the_manifest_must_state_the_reading_scale(tmp_path, monkeypatch):
    world = World(tmp_path)
    world.install(monkeypatch)
    data = json.loads(manifest(tmp_path).read_text())
    data.pop("reading")
    path = tmp_path / "no-scale.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert smoke.main(manifest=str(path), receipt_dir=str(tmp_path / "r"),
                      state_dir=str(tmp_path / "s")) == 2
    assert world.calls == []


def test_a_job_that_never_reads_its_socket_fails(tmp_path, monkeypatch):
    _world, engine, code = run(tmp_path, monkeypatch, skip_socket_read="f1")
    assert code == 1
    assert check(engine, "f1.socket_read")["ok"] is False


def test_a_prepass_answer_that_does_not_report_its_reading_fails(tmp_path, monkeypatch):
    _world, engine, code = run(tmp_path, monkeypatch, base_answer="I looked at some nodes and stopped.")
    assert code == 1
    assert check(engine, "prepass.reported_reading")["ok"] is False


def test_a_shallow_base_reading_is_not_a_pass(tmp_path, monkeypatch):
    _world, engine, code = run(tmp_path, monkeypatch, shallow_base=True)
    assert code == 1
    assert check(engine, "prepass.reading_scale")["ok"] is False
    assert check(engine, "prepass.material_content")["ok"] is False


def test_a_source_mutation_after_a_fork_is_detected(tmp_path, monkeypatch):
    def on_wait(world, sid):
        if sid == world.jobs.get("f1"):
            world.messages[world.base_id].append(user(world.mid(), world.now(), "extra source message"))
    _world, engine, code = run(tmp_path, monkeypatch, on_wait=on_wait)
    assert code == 1
    assert check(engine, "f1.source_unchanged")["ok"] is False


def test_the_base_extension_must_not_touch_an_earlier_fork(tmp_path, monkeypatch):
    def mutate(messages):
        for part in messages[1]["content"]:
            if part.get("type") == "tool":
                part["state"]["content"][0]["text"] = "CHANGED"
    _world, engine, code = run(tmp_path, monkeypatch, mutate_old_fork=mutate)
    assert code == 1
    assert check(engine, "f1.old_fork_immutable")["ok"] is False


# ------------------------------------------------------------------------------------ entry-point delegation

def test_oc_cache_check_delegates_the_feature_smoke(tmp_path, monkeypatch):
    called = {}

    def fake_main(**kwargs):
        called.update(kwargs)
        return 7
    monkeypatch.setattr(smoke, "main", fake_main)
    m = manifest(tmp_path)
    monkeypatch.setattr(sys, "argv", ["oc_cache_check.py", "--feature-smoke", "--manifest", str(m),
                                      "--wait-max", "5"])
    assert oc_cache_check.main() == 7
    assert called == {"manifest": str(m), "receipt_dir": None, "state_dir": None, "wait_max": 5.0}


def test_feature_smoke_without_a_manifest_is_an_argument_error(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["oc_cache_check.py", "--feature-smoke"])
    try:
        oc_cache_check.main()
    except SystemExit as e:
        assert e.code == 2
    else:                                                    # pragma: no cover
        raise AssertionError("argparse did not refuse a missing --manifest")


def test_simple_turn_mode_still_runs_unchanged(tmp_path, monkeypatch):
    sessions = {"ses_simple": [assistant("m1", 1, 2, {"input": 10, "output": 1, "reasoning": 0,
                                                      "cache": {"read": 100, "write": 0}}),
                               assistant("m2", 3, 4, {"input": 10, "output": 1, "reasoning": 0,
                                                      "cache": {"read": 100, "write": 0}})]}
    deleted = []

    monkeypatch.setattr(oc_cache_check.c, "oc_create_session", lambda **kw: (200, {"id": "ses_simple"}))
    monkeypatch.setattr(oc_cache_check.c, "oc_prompt", lambda sid, text: {})
    monkeypatch.setattr(oc_cache_check.c, "oc_wait", lambda sid, timeout=0: (204, None))
    monkeypatch.setattr(oc_cache_check.c, "oc_messages", lambda sid: sessions[sid])

    def fork(sid):
        sessions["ses_fork"] = [assistant("m3", 5, 6, {"input": 10, "output": 1, "reasoning": 0,
                                                       "cache": {"read": 100, "write": 0}})]
        return 200, {"id": "ses_fork"}
    monkeypatch.setattr(oc_cache_check.c, "oc_fork", fork)
    monkeypatch.setattr(oc_cache_check.c, "oc_delete", lambda sid: deleted.append(sid) or (204, None))
    monkeypatch.setattr(sys, "argv", ["oc_cache_check.py", "--turns", "2", "--forks", "1"])
    assert oc_cache_check.main() == 0
    assert deleted == ["ses_fork", "ses_simple"]


# ------------------------------------------------------------------------------------ pure account parsing/recompute

def test_parse_account_reads_the_spec_line():
    text = ("🍴 fork of ses_base123 · first NEW call msg_9@1793133567803→1793133572259 · read=22,400 write=0 "
            "input=529 total=22,929 · base_input=22566 (copied call msg_7@1793133560000→1793133560900) · "
            "new-input cached 97.7% · base-input coverage 99.3% · CACHE HIT")
    account = smoke.parse_account(text)
    assert account["present"] is True
    assert (account["source"], account["first_new_id"], account["base_call_id"]) == (
        "ses_base123", "msg_9", "msg_7")
    assert (account["read"], account["write"], account["input"], account["total"]) == (22400, 0, 529, 22929)
    assert account["base_input"] == 22566
    assert (account["new_input_cached"], account["base_input_coverage"]) == (97.7, 99.3)
    assert account["state"] == smoke.STATE_HIT


def test_the_parser_matches_the_real_oc_seat_account_formatter():
    import oc_seat
    account = {"source": "ses_base", "state": "CACHE HIT",
               "base": {"id": "msg_1", "time": {"created": 1, "completed": 2}, "total": 1000},
               "new": {"id": "msg_2", "time": {"created": 3, "completed": 4}, "read": 900, "write": 0,
                       "input": 50, "total": 950},
               "new_input_cached": 94.7, "base_input_coverage": 90.0}
    parsed = smoke.parse_account(oc_seat.format_fork_cache(account))
    assert parsed["present"] is True and parsed["source"] == "ses_base"
    assert (parsed["read"], parsed["write"], parsed["input"], parsed["total"], parsed["base_input"]) == (
        900, 0, 50, 950, 1000)
    assert parsed["first_new_id"] == "msg_2" and parsed["base_call_id"] == "msg_1"
    assert parsed["state"] == "CACHE HIT"
    assert (parsed["new_input_cached"], parsed["base_input_coverage"]) == (94.7, 90.0)
    pending = oc_seat.format_fork_cache({"source": "ses_base", "state": "UNAVAILABLE",
                                         "reason": "copied base_input is zero",
                                         "base": {"id": "msg_1", "time": {}, "total": None},
                                         "new": {"id": "msg_2", "time": {}}})
    assert smoke.parse_account(pending)["state"] == "UNAVAILABLE"
    assert "zero" in pending


def test_parse_account_keeps_pending_and_unavailable_reasons():
    pending = smoke.parse_account("🍴 fork of ses_a · first NEW call ?@?→? · read=None write=None input=None "
                                  "total=None · base_input=100 (copied call msg_1@1→2) · PENDING: no first NEW "
                                  "assistant call yet")
    assert pending["present"] and pending["state"] == smoke.STATE_PENDING
    assert pending["read"] is None and "no first NEW" in pending["reason"]
    unavailable = smoke.parse_account("🍴 fork of ses_a · first NEW call msg_2@1→2 · read=0 write=0 input=10 "
                                      "total=10 · base_input=? (copied call msg_1@1→2) · CACHE MISS")
    assert unavailable["state"] == smoke.STATE_MISS and unavailable["base_input"] is None


def test_parse_account_never_uses_a_per_turn_read_line():
    text = "    turn  0: read=  22,400  write=      0  out=    56  $0.0000\nno account here\n"
    assert smoke.parse_account(text)["present"] is False


def _session(created=100, source="ses_base"):
    return {"time": {"created": created}, "fork": {"sessionID": source}, "metadata": {}}


def _asst(mid, created, tokens, completed=True):
    row = {"id": mid, "type": "assistant", "time": {"created": created}}
    if completed:
        row["time"]["completed"] = created + 1
    if tokens is not None:
        row["tokens"] = tokens
    return row


def test_expected_account_reports_unavailable_missing_counters_without_percentages():
    messages = [_asst("b", 10, {"input": 100, "cache": {"read": 0, "write": 0}}),
                _asst("n", 200, {"input": 10, "cache": {"read": 50, "write": 0}})]
    messages[1]["tokens"]["cache"]["read"] = "many"
    account = smoke.expected_account(_session(), messages)
    assert account["state"] == smoke.STATE_UNAVAILABLE
    assert account["new_input_cached"] is None and account["base_input_coverage"] is None
    assert "first NEW call" in account["reason"]


def test_expected_account_does_not_hide_coverage_above_100(tmp_path):
    messages = [_asst("b", 10, {"input": 1000, "cache": {"read": 0, "write": 0}}),
                _asst("n", 200, {"input": 10, "cache": {"read": 1500, "write": 0}})]
    account = smoke.expected_account(_session(), messages)
    assert account["state"] == smoke.STATE_HIT
    assert account["base_input_coverage"] > 100


def test_expected_account_is_pending_without_its_own_call():
    messages = [_asst("b", 10, {"input": 100, "cache": {"read": 0, "write": 0}})]
    account = smoke.expected_account(_session(), messages)
    assert account["state"] == smoke.STATE_PENDING and account["new"] is None


def test_expected_account_refuses_a_zero_base_denominator():
    messages = [_asst("b", 10, {"input": 0, "cache": {"read": 0, "write": 0}}),
                _asst("n", 200, {"input": 10, "cache": {"read": 0, "write": 0}})]
    account = smoke.expected_account(_session(), messages)
    assert account["state"] == smoke.STATE_UNAVAILABLE and "zero" in account["reason"]
    assert account["base_input_coverage"] is None


def test_expected_account_rejects_negative_and_nonfinite_counters():
    for bad in (-1, float("inf")):
        messages = [_asst("b", 10, {"input": 100, "cache": {"read": 0, "write": 0}}),
                    _asst("n", 200, {"input": 10, "cache": {"read": bad, "write": 0}})]
        assert smoke.expected_account(_session(), messages)["state"] == smoke.STATE_UNAVAILABLE
