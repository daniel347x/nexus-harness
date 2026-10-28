"""oc.sh telemetry on the program views' session rows: read-only, local, computed in this process.

    GET /api/telemetry?program=CORE          every session of one program ([ap:CORE])
    GET /api/telemetry?sid=ID[&sid=ID ...]   sessions outside any program (freestanding quick workers and helpers)

The numbers are scripts/oc_telemetry.py's own (pane_program / pane_session): an implementer job's phase boundaries and
⚑ flags exactly as `oc.sh telemetry` places them, and for any other role only its context at each job segment's first
and last model response and the wall clock. Local records only (OpenCode's database read-only, the Claude host database
and native transcripts), never a Workflowy call.

One worker thread does every computation, one at a time. Nothing is recomputed while its records are unchanged: before
answering, the worker probes the program's sessions (OpenCode rows / last seq / last update; a Claude transcript's inode
and size) and reuses the last answer when no probe moved. When one did, the program is recomputed: its implementer
candidates are classified again (their parsed messages come from the tool's incremental disk cache, so only new messages
are ever parsed) and any other session is read again only if its own probe moved. A request waits up to WAIT_SECONDS,
else it answers "computing" and the page asks again. Whether a session is live ("so far") is decided on every answer.
"""
from __future__ import annotations

import collections
import threading
import time

SCHEMA = 1
WAIT_SECONDS = 8.0          # a request waits this long for its computation (under the page's 12 s), then "computing"
INVENTORY_SECONDS = 5.0     # the local session inventory (who is in a program) is re-read at most this often
MEMO_KEYS = 256             # programs and sessions whose last answer is kept
BASIC_MEMO = 20000          # non-implementer sessions whose facts are kept by probe (pane_program's memo)
MAX_SIDS = 40               # sessions per ?sid= request


class Job:
    def __init__(self, key):
        self.key, self.done, self.result = key, threading.Event(), None


# @beacon[
#   id=opc-pane@PaneTelemetry,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=GET /api/telemetry: oc_telemetry's per-session numbers for the program views, computed in-process by one worker thread; a program is recomputed only when one of its sessions' probes (OpenCode rows/seq/update, Claude transcript size) moved, else its last answer is reused; live ("so far") is decided on every answer; a request waits up to 8 s, else answers "computing".,
# ]
class PaneTelemetry:
    def __init__(self, lib=None, clock=time.time, start=None):
        self._lib, self.clock = lib, clock
        self.start = start or (lambda work: threading.Thread(target=work, name="pane-telemetry", daemon=True).start())
        self.lock = threading.Lock()
        self.ready = threading.Condition(self.lock)
        self.queue: collections.deque = collections.deque()
        self.jobs: dict = {}                              # key → the Job pending or running for it
        self.memo: collections.OrderedDict = collections.OrderedDict()   # key → (signature, answer)
        self.worker = False
        # worker thread only (the OpenCode database connection belongs to the thread that opened it)
        self.basic: dict = {}
        self.inv, self.inv_at = None, 0.0

    @property
    def lib(self):
        if self._lib is None:
            import oc_telemetry  # scripts/ is on the server's path; stdlib only
            self._lib = oc_telemetry
        return self._lib

    # ---- requests
    def program(self, core: str) -> dict:
        prog = f"[ap:{core}]"
        job = self._submit(("p", prog))
        done = job.done.wait(WAIT_SECONDS)
        base = {"schemaVersion": SCHEMA, "backend": "telemetry", "program": prog, "asOf": self.clock()}
        if not done:
            return {**base, "status": "computing", "sessions": {}, "computing": ["program"]}
        r = job.result
        if r.get("error"):
            return {**base, "status": "unavailable", "error": r["error"], "sessions": {}}
        now = int(self.clock() * 1000)
        return {**base, "status": "complete", "computedAt": r["computedAt"], "rollup": r.get("rollup"),
                "problems": r.get("problems", []),
                "sessions": {sid: self.lib.mark_live(e, now) for sid, e in r["sessions"].items()}}

    def sessions(self, sids: list[str]) -> dict:
        jobs = [self._submit(("s", sid)) for sid in sids]
        deadline = time.monotonic() + WAIT_SECONDS
        for job in jobs:
            job.done.wait(max(0.0, deadline - time.monotonic()))
        now = int(self.clock() * 1000)
        out, computing, errors = {}, [], []
        for sid, job in zip(sids, jobs):
            r = job.result if job.done.is_set() else None
            if r is None:
                computing.append(sid)
            elif r.get("error"):
                errors.append({"sid": sid, "error": r["error"]})
            elif r.get("entry"):
                out[sid] = self.lib.mark_live(r["entry"], now)
        return {"schemaVersion": SCHEMA, "backend": "telemetry", "program": None, "asOf": self.clock(),
                "status": "complete" if not computing else "computing", "sessions": out, "computing": computing,
                "problems": errors}

    def _submit(self, key) -> Job:
        with self.lock:
            job = self.jobs.get(key)
            if job is None:
                job = self.jobs[key] = Job(key)
                self.queue.append(job)
                self.ready.notify()
            if not self.worker:
                self.worker = True
                self.start(self._run)
            return job

    # ---- the worker
    def _run(self):
        while True:
            with self.lock:
                while not self.queue:
                    self.ready.wait()
                job = self.queue.popleft()
            try:
                job.result = self._answer(job.key)
            except Exception as e:  # noqa: BLE001 — a failure is answered, never kept as the last answer
                job.result = {"error": (str(e) or type(e).__name__)[:300]}
            with self.lock:
                self.jobs.pop(job.key, None)
            job.done.set()

    def _inventory(self):
        if self.inv is None or time.monotonic() - self.inv_at >= INVENTORY_SECONDS:
            if self.inv is not None and self.inv.con is not None:
                self.inv.con.close()
            self.inv, self.inv_at = self.lib.Inventory(), time.monotonic()
        return self.inv

    def _answer(self, key) -> dict:
        lib, inv = self.lib, self._inventory()
        kind, name = key
        if kind == "p":
            sig = lib.program_signature(inv, name)
        else:
            s = inv.get(name)
            sig = (name, lib.probe(inv, s)) if s else None
        hit = self.memo.get(key)
        if hit is not None and sig is not None and hit[0] == sig:
            self.memo.move_to_end(key)
            return hit[1]
        if kind == "p":
            r = lib.pane_program(inv, name, True, self.basic)
            answer = {"sessions": r["sessions"], "rollup": r["rollup"], "problems": r["problems"]}
            if len(self.basic) > BASIC_MEMO:
                self.basic.clear()
        else:
            answer = {"entry": lib.pane_session(inv, name) if sig else None}
        answer["computedAt"] = self.clock()
        self.memo[key] = (sig, answer)
        while len(self.memo) > MEMO_KEYS:
            self.memo.popitem(last=False)
        return answer
