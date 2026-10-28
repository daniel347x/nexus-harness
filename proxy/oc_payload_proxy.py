#!/usr/bin/env python3
"""oc_payload_proxy.py — the PAYLOAD EXTENSION FOR OPENCODE: a local, transparent, rewriting proxy
between OpenCode and EVERY model provider (Anthropic, OpenRouter, OpenAI, Google, xAI, Moonshot). Stdlib only (runs
on WSL's python3). Docs: [ap:NN279Q] (this proxy) ·
[ap:MCC7MG] (the package manager it serves). ⚠️ When something misbehaves, check Dan's TypingMind Payload Extension
(docs + source) first — it has solved the same class of problems (key order, cache hygiene, headers, keep-alive).

Why it exists: OpenCode hardcodes parts of the request it sends to Anthropic that its plugin hooks cannot
change (first case: `cache_control: {type:"ephemeral"}` with no `ttl`, assigned AFTER conversion, so the
1-hour prompt-cache TTL the package system [ap:AOHAS5] needs could not be requested from a plugin —
verified 2026-09-25 05:17: a base expired between 5 and 12 minutes with the plugin installed). A proxy is
the one place that sees the FINAL bytes. It is also the instrument: every request's shape (blocks,
breakpoints, sizes, key order) is logged, so no more guessing at what OpenCode sends.

Wire-up: each provider's `options.baseURL` in config/opencode.json (installed by the restart script) points here.
Anthropic is unprefixed (http://127.0.0.1:4097/v1, the original wiring); every other provider carries its ROUTES
prefix, which the proxy strips before forwarding (openrouter → http://127.0.0.1:4097/openrouter/api/v1, openai →
…/openai/v1, google → …/google/v1beta, xai → …/xai/v1, moonshotai → …/moonshotai/v1). Headers (API keys etc.)
pass through untouched; responses, including SSE streams, are relayed byte-for-byte.

REAL 429 HANDLING (Manager-Session-110, Dan's ruling in [ap:F29OSR]): OpenCode's own retry gives a 429 about one
minute (5 tries, 30 s cap, hard-coded in session/retry.ts). This proxy retries BEFORE OpenCode sees anything: a
retryable status (429, 408, 502, 503, 504, 529) or a connection failure, arriving before any response byte has been
relayed, is retried with exponential backoff (5 s, doubling, each wait capped at 5 min, 10 retries ≈ 25 minutes;
retry-after / retry-after-ms honoured). Only after the last retry does the error reach OpenCode. Safe because no byte
has been sent to the client yet; the request body is buffered. OpenCode's headerTimeout must exceed the total backoff
(config/opencode.json sets one hour for every provider). If OpenCode hangs up mid-wait, the retries stop.
Retry state per OpenCode session (the x-opencode-session-id header) is served at GET /_proxy/status[?session=ses_…], which
`oc_seat wait` prints so a conductor never mistakes a backoff for a hang.

FEATURES (each independently switchable; defaults in FEATURES below, overridden by proxy/features.json):
  cache_ttl           "5m" (default since Manager-Session-064) | "1h" | "off": every cache_control block in
                      /v1/messages bodies gets that explicit "ttl" (uniform, so Anthropic's mixed-TTL ordering rule never
                      applies; OUR setting, not OpenCode's). 5m writes cost 1.25× base input, 1h writes 2×; reads are the
                      same. "1h" served the talk-to-a-warm-seat idea (Manager-Session-054); Dan returned it to 5 minutes
                      once runs became sequenced seats (Manager-Session-064). "off" forwards OpenCode's blocks untouched.
                      Legacy key `cache_ttl_1h: true|false` is still honoured (= "1h" | "5m"). The package scripts read
                      the same file (oc_pack.proxy_cache_ttl) to price cache writes.
  manager_cache_ttl   "1h" (default) | "5m" | "off": the exception to cache_ttl for MANAGER SESSIONS. A request whose
                      system prompt contains `manager_marker` (default "# CONVERSATION CONTINUITY AGENT", the first line
                      of agents/agent/manager.md) gets this TTL instead. Dan steps away from a Manager Session for
                      minutes or an hour; five minutes would make every break a full cache miss. Programs call
                      constantly, so they keep cache_ttl. "off" = no exception (the Manager gets cache_ttl too).
  conductor_cache_ttl "1h" (default) | "5m" | "off": the same exception for CONDUCTORS on Anthropic models. A request
                      whose system prompt contains `conductor_marker` (default: the opening sentence of
                      agents/agent/conductor.md) gets this TTL. A conductor waits 10-60 minutes for each worker, so
                      at 5 minutes every worker return would rewrite its whole cache; workers and seats, which call
                      constantly, keep cache_ttl. A Manager request takes manager_cache_ttl first.
  log_summary         one JSON line per request in $XDG_STATE/opencode-payload-proxy/requests.jsonl:
                      time, path, model, stream, system/tools/messages counts, every breakpoint (where, ttl),
                      per-block char sizes of the LAST user message, status, latency. Rotates at 20 MB.
  dump_bodies         (off) also write each full request body to dumps/<ts>.json — for deep debugging only.
  fold_effort_updates (on) OpenAI /v1/responses: remove OpenCode V2's `configuration_update` input items (a mid-session
                      thinking-level change, which the ChatGPT plan rejects with a 400 that breaks the session for good)
                      and set the top-level reasoning.effort to the last one's level (transform_responses_body).
  retry               {enabled, max_retries, initial_s, factor, max_wait_s, max_retry_after_s, statuses}: the
                      429 backoff above. features.json may override any key (e.g. {"retry": {"max_retries": 12}}).

Never modifies a body it cannot parse; on any transform error the ORIGINAL bytes are forwarded. If the proxy
is down, Anthropic calls fail loudly in OpenCode (connection refused) — nothing silent.

Run:   python3 proxy/oc_payload_proxy.py [--port 4097] [--upstream https://api.anthropic.com]   (--upstream = the
       unprefixed route; tests also override ROUTES)
       (normally via proxy/oc_payload_proxy.sh start|stop|status|log — detached, pid file, log file)
"""
from __future__ import annotations

import argparse, email.utils, json, os, select, socket, ssl, sys, threading, time
from http.client import HTTPSConnection, HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "opencode-payload-proxy"
LOG_PATH = STATE_DIR / "requests.jsonl"
DUMP_DIR = STATE_DIR / "dumps"
LOG_ROTATE_BYTES = 20 * 1024 * 1024

RETRY_DEFAULTS = {"enabled": True, "max_retries": 10, "initial_s": 5.0, "factor": 2.0, "max_wait_s": 300.0,
                  "max_retry_after_s": 900.0, "statuses": [408, 429, 502, 503, 504, 529]}
FEATURES = {"cache_ttl": "5m", "manager_cache_ttl": "1h", "manager_marker": "# CONVERSATION CONTINUITY AGENT",
            "conductor_cache_ttl": "1h",
            "conductor_marker": "You are the CONDUCTOR of a program in Dan's Program Conductor harness",
            "log_summary": True, "dump_bodies": False, "fold_effort_updates": True, "retry": dict(RETRY_DEFAULTS)}
try:
    _loaded = json.loads((HERE / "features.json").read_text(encoding="utf-8"))
    if "cache_ttl_1h" in _loaded and "cache_ttl" not in _loaded:      # legacy switch
        _loaded["cache_ttl"] = "1h" if _loaded.pop("cache_ttl_1h") else "5m"
    _retry = _loaded.pop("retry", None)
    FEATURES.update(_loaded)
    if isinstance(_retry, dict):
        FEATURES["retry"].update(_retry)
except FileNotFoundError:
    pass
except Exception as e:  # noqa: BLE001
    print(f"[proxy] features.json unreadable ({e}); using defaults", file=sys.stderr)
if FEATURES.get("cache_ttl") not in ("5m", "1h", "off"):
    print(f"[proxy] cache_ttl {FEATURES.get('cache_ttl')!r} not one of 5m|1h|off; using 5m", file=sys.stderr)
    FEATURES["cache_ttl"] = "5m"
if FEATURES.get("manager_cache_ttl") not in ("5m", "1h", "off"):
    print(f"[proxy] manager_cache_ttl {FEATURES.get('manager_cache_ttl')!r} not one of 5m|1h|off; using 1h", file=sys.stderr)
    FEATURES["manager_cache_ttl"] = "1h"
if FEATURES.get("conductor_cache_ttl") not in ("5m", "1h", "off"):
    print(f"[proxy] conductor_cache_ttl {FEATURES.get('conductor_cache_ttl')!r} not one of 5m|1h|off; using 1h", file=sys.stderr)
    FEATURES["conductor_cache_ttl"] = "1h"

HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
              "transfer-encoding", "upgrade", "host", "content-length"}

# Path prefix → upstream origin. A request to /<prefix>/rest is forwarded to <origin>/rest; any other path goes to
# DEFAULT_UPSTREAM (Anthropic, the original unprefixed wiring). Keep in step with the baseURLs in config/opencode.json.
ROUTES = {
    "anthropic": "https://api.anthropic.com",
    "openrouter": "https://openrouter.ai",
    "openai": "https://api.openai.com",
    "google": "https://generativelanguage.googleapis.com",
    "xai": "https://api.x.ai",
    "moonshotai": "https://api.moonshot.ai",
}
DEFAULT_UPSTREAM = "https://api.anthropic.com"
UPSTREAM_TIMEOUT_S = 3600      # socket read timeout; hidden-thinking models can be silent for many minutes


def route(path: str) -> tuple[str, str, str]:
    """(route name, upstream origin, upstream path) for an incoming request path."""
    seg = path.lstrip("/").split("/", 1)
    if seg and seg[0] in ROUTES:
        rest = "/" + (seg[1] if len(seg) > 1 else "")
        return seg[0], ROUTES[seg[0]], rest
    return "anthropic", DEFAULT_UPSTREAM, path


def backoff_s(retry_no: int, cfg: dict, retry_after) -> float:
    """Wait before retry number `retry_no` (1-based): initial × factor^(n-1), capped; a server retry-after wins when
    longer (itself capped at max_retry_after_s)."""
    w = min(float(cfg["initial_s"]) * float(cfg["factor"]) ** (retry_no - 1), float(cfg["max_wait_s"]))
    if retry_after is not None:
        w = max(w, min(retry_after, float(cfg["max_retry_after_s"])))
    return w


def parse_retry_after(headers) -> float | None:
    """Seconds from retry-after-ms / retry-after (seconds or HTTP date); None when absent or unreadable."""
    h = {k.lower(): v for k, v in headers}
    try:
        if h.get("retry-after-ms"):
            return max(0.0, float(h["retry-after-ms"]) / 1000)
        ra = h.get("retry-after")
        if ra:
            try:
                return max(0.0, float(ra))
            except ValueError:
                dt = email.utils.parsedate_to_datetime(ra)
                return max(0.0, dt.timestamp() - time.time())
    except Exception:  # noqa: BLE001
        return None
    return None


# ------------------------------------------------------------------------------------ retry state (for oc_seat wait)

_state_lock = threading.Lock()
RETRY_STATE: dict[str, dict] = {}     # key: OpenCode session id (x-opencode-session-id) or "-"
STATE_KEEP_S = 3600                   # resolved entries linger an hour so a late `wait` still sees what happened


def set_state(key: str, **fields) -> None:
    with _state_lock:
        cur = RETRY_STATE.setdefault(key, {})
        cur.update(fields, updated=time.time())
        cutoff = time.time() - STATE_KEEP_S
        for k in [k for k, v in RETRY_STATE.items() if v.get("phase") != "backoff" and v.get("updated", 0) < cutoff]:
            RETRY_STATE.pop(k, None)


def status_payload(session: str | None) -> dict:
    now = time.time()
    with _state_lock:
        items = {k: dict(v) for k, v in RETRY_STATE.items() if session is None or k == session}
    for v in items.values():
        if v.get("next_try_at"):
            v["next_in_s"] = max(0, round(v["next_try_at"] - now))
    return {"now": now, "retry": FEATURES["retry"], "sessions": items}

# ------------------------------------------------------------------------------------ transforms

# @beacon[
#   id=auto-beacon@_walk_cache_control-n2is,
#   role=_walk_cache_control,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
def _walk_cache_control(obj, path: str, found: list[dict], rewrite) -> None:
    """Find every cache_control dict; when `rewrite` is a TTL string ("5m" | "1h") set it explicitly. Records (where,
    ttl) for the log."""
    if isinstance(obj, dict):
        cc = obj.get("cache_control")
        if isinstance(cc, dict) and cc.get("type") == "ephemeral":
            if rewrite:
                cc["ttl"] = rewrite
            found.append({"where": path, "ttl": cc.get("ttl", "5m")})
        for k, v in obj.items():
            if k != "cache_control":
                _walk_cache_control(v, f"{path}.{k}", found, rewrite)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _walk_cache_control(v, f"{path}[{i}]", found, rewrite)

# @beacon[
#   id=auto-beacon@transform_messages_body-zd5o,
#   role=transform_messages_body,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
def transform_messages_body(body: bytes) -> tuple[bytes, dict]:
    """Returns (new_body, summary). Only for /v1/messages JSON. Never raises to the caller."""
    summary: dict = {}
    try:
        j = json.loads(body.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return body, {"parse": "not-json"}
    if not isinstance(j, dict):
        return body, {"parse": "not-object"}
    rewrite = FEATURES.get("cache_ttl") if FEATURES.get("cache_ttl") in ("5m", "1h") else None
    sysv = j.get("system")
    system_text = ("\n".join(b.get("text", "") for b in sysv if isinstance(b, dict)) if isinstance(sysv, list)
                   else (sysv if isinstance(sysv, str) else ""))
    marker = FEATURES.get("manager_marker") or ""
    is_manager = bool(marker) and marker in system_text
    if is_manager and FEATURES.get("manager_cache_ttl") in ("5m", "1h"):
        rewrite = FEATURES["manager_cache_ttl"]
    cmarker = FEATURES.get("conductor_marker") or ""
    is_conductor = not is_manager and bool(cmarker) and cmarker in system_text
    if is_conductor and FEATURES.get("conductor_cache_ttl") in ("5m", "1h"):
        rewrite = FEATURES["conductor_cache_ttl"]
    breakpoints: list[dict] = []
    _walk_cache_control(j.get("system"), "system", breakpoints, rewrite)
    _walk_cache_control(j.get("tools"), "tools", breakpoints, rewrite)
    _walk_cache_control(j.get("messages"), "messages", breakpoints, rewrite)
    msgs = j.get("messages") or []
    last_user = next((m for m in reversed(msgs) if isinstance(m, dict) and m.get("role") == "user"), None)
    def block_sizes(m):
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, str):
            return [{"type": "text", "chars": len(c)}]
        out = []
        for b in c or []:
            if isinstance(b, dict):
                t = b.get("type"); s = b.get("text") if t == "text" else json.dumps(b.get("input") or b.get("content") or "", ensure_ascii=False)
                out.append({"type": t, "chars": len(s) if isinstance(s, str) else 0, "cc": "cache_control" in b})
        return out
    summary = {
        "model": j.get("model"), "stream": bool(j.get("stream")), "max_tokens": j.get("max_tokens"),
        "system_blocks": (len(sysv) if isinstance(sysv, list) else (1 if sysv else 0)),
        "system_chars": (sum(len(b.get("text", "")) for b in sysv if isinstance(b, dict)) if isinstance(sysv, list) else len(sysv or "")),
        "tools": len(j.get("tools") or []), "messages": len(msgs),
        "breakpoints": breakpoints, "ttl_rewritten": rewrite or False, "manager": is_manager, "conductor": is_conductor,
        "last_user_blocks": block_sizes(last_user) if last_user else [],
        "top_level_keys": list(j.keys()),
    }
    if rewrite and breakpoints:
        body = json.dumps(j, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return body, summary

# @beacon[
#   id=auto-beacon@transform_responses_body-k3e7,
#   role=transform_responses_body,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=ChatGPT plan: fold OpenCode V2's configuration_update input items into top-level reasoning.effort,
# ]
def transform_responses_body(body: bytes) -> tuple[bytes, dict]:
    """OpenAI /v1/responses: fold `configuration_update` input items into the top-level `reasoning.effort`.

    Why: OpenCode V2 turns a same-model thinking-level change (a `model-switched` history event where only the
    variant differs) into an input item `{"type": "configuration_update", "reasoning": {"effort": …}}` and sets the
    top-level effort to the level BEFORE the change, to keep the prompt cache frozen. The ChatGPT plan's token-sharing
    route rejects that item (HTTP 400 `subscription_sharing_unsupported_capability`), and since the event stays in the
    history, every later turn re-sends it and the session is broken for good. Here every such item is removed and the
    top-level effort becomes the LAST item's effort, which is the session's current level (V2 already drops all the
    items itself whenever the last one disagrees with the requested level). Cost: one cache miss on the turn where the
    level changes. No item → the body is forwarded byte-for-byte; OpenCode sets the effort, never this proxy.
    Never raises to the caller."""
    if b"configuration_update" not in body:          # the common case: no parse, no change
        return body, {}
    try:
        j = json.loads(body.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return body, {"parse": "not-json"}
    items = j.get("input") if isinstance(j, dict) else None
    if not isinstance(items, list):
        return body, {}
    updates = [it for it in items if isinstance(it, dict) and it.get("type") == "configuration_update"]
    if not updates:
        return body, {}
    effort = next((u["reasoning"]["effort"] for u in reversed(updates)
                   if isinstance(u.get("reasoning"), dict) and isinstance(u["reasoning"].get("effort"), str)), None)
    j["input"] = [it for it in items if not (isinstance(it, dict) and it.get("type") == "configuration_update")]
    before = (j.get("reasoning") or {}).get("effort") if isinstance(j.get("reasoning"), dict) else None
    if effort is not None:
        reasoning = j.get("reasoning") if isinstance(j.get("reasoning"), dict) else {}
        reasoning["effort"] = effort
        j["reasoning"] = reasoning
    summary = {"effort_updates_folded": len(updates), "effort_before": before, "effort_after": effort}
    return json.dumps(j, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), summary

# ------------------------------------------------------------------------------------ logging

_log_lock = threading.Lock()

def log_line(rec: dict) -> None:
    if not FEATURES.get("log_summary"):
        return
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with _log_lock:
            if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_ROTATE_BYTES:
                LOG_PATH.replace(LOG_PATH.with_suffix(".jsonl.1"))
            with LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        print(f"[proxy] log failed: {e}", file=sys.stderr)

def dump_body(ts: str, body: bytes) -> None:
    if not FEATURES.get("dump_bodies"):
        return
    try:
        DUMP_DIR.mkdir(parents=True, exist_ok=True)
        (DUMP_DIR / f"{ts}.json").write_bytes(body)
    except Exception as e:  # noqa: BLE001
        print(f"[proxy] dump failed: {e}", file=sys.stderr)

# ------------------------------------------------------------------------------------ the relay

# @beacon[
#   id=auto-beacon@Relay-8h6p,
#   role=Relay,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
class Relay(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "oc-payload-proxy/2.0"

    def log_message(self, fmt, *args):  # quiet the default stderr access log; we keep our own
        pass

    # -- helpers ------------------------------------------------------------------------------------------------

    def _client_gone(self) -> bool:
        """True when OpenCode has hung up (aborted the call): a readable socket that yields EOF on a peek."""
        try:
            r, _, _ = select.select([self.connection], [], [], 0)
            if not r:
                return False
            return self.connection.recv(1, socket.MSG_PEEK) == b""
        except Exception:  # noqa: BLE001
            return True

    def _send_plain(self, code: int, reason: str, payload: bytes, ctype: str = "text/plain") -> None:
        self.send_response(code, reason); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload))); self.send_header("Connection", "close")
        self.end_headers(); self.wfile.write(payload)

    def _status_endpoint(self) -> None:
        q = parse_qs(urlsplit(self.path).query)
        payload = json.dumps(status_payload((q.get("session") or [None])[0]), ensure_ascii=False).encode("utf-8")
        self._send_plain(200, "OK", payload, "application/json")
        self.close_connection = True

    # -- the forward, with backoff --------------------------------------------------------------------------------

    def _forward(self):
        if self.command == "GET" and self.path.split("?")[0] == "/_proxy/status":
            return self._status_endpoint()
        t0 = time.time(); ts = time.strftime("%Y%m%dT%H%M%S") + f"{int((t0 % 1) * 1000):03d}"
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        name, origin, up_path = route(self.path)
        upstream = urlsplit(origin)
        summary: dict = {"route": name}
        if name == "anthropic" and self.command == "POST" and up_path.startswith("/v1/messages") and body:
            dump_body(ts, body)
            body, more = transform_messages_body(body)
            summary.update(more)
        elif (name == "openai" and self.command == "POST" and up_path.startswith("/v1/responses") and body
              and FEATURES.get("fold_effort_updates") and not self.headers.get("Content-Encoding")):
            body, more = transform_responses_body(body)
            summary.update(more)
        if body and "model" not in summary:
            try:
                summary["model"] = json.loads(body.decode("utf-8")).get("model")
            except Exception:  # noqa: BLE001
                pass
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
        headers["Host"] = upstream.netloc
        if body:
            headers["Content-Length"] = str(len(body))
        sess = self.headers.get("x-opencode-session-id") or "-"
        cfg = FEATURES["retry"]
        retryable = set(int(s) for s in cfg.get("statuses") or [])
        max_retries = int(cfg.get("max_retries", 0)) if cfg.get("enabled") else 0
        conn_cls = HTTPSConnection if upstream.scheme == "https" else HTTPConnection
        status = None; attempt = 0; conn = None; history: list = []
        try:
            while True:
                attempt += 1
                conn = conn_cls(upstream.hostname, upstream.port, timeout=UPSTREAM_TIMEOUT_S,
                                **({"context": ssl.create_default_context()} if upstream.scheme == "https" else {}))
                err = None; resp = None
                try:
                    conn.request(self.command, up_path, body=body or None, headers=headers)
                    resp = conn.getresponse()
                except Exception as e:  # noqa: BLE001  (connection refused / reset / DNS / TLS, before any byte)
                    err = e
                code = resp.status if resp is not None else None
                failed = err is not None or code in retryable
                if failed and attempt <= max_retries:
                    retry_after = parse_retry_after(resp.getheaders()) if resp is not None else None
                    detail = repr(err) if err is not None else (resp.read(2000).decode("utf-8", "replace") if resp else "")
                    conn.close()
                    wait = backoff_s(attempt, cfg, retry_after)
                    history.append({"status": code, "error": repr(err) if err else None, "wait_s": round(wait, 1)})
                    set_state(sess, phase="backoff", route=name, model=summary.get("model"), attempt=attempt,
                              max_retries=max_retries, last_status=code, last_error=(detail or "")[:300],
                              next_try_at=time.time() + wait, started_at=t0)
                    print(f"[proxy] {name} {summary.get('model')} session={sess} → {code or repr(err)}; retry "
                          f"{attempt}/{max_retries} in {wait:.0f}s", flush=True)
                    deadline = time.time() + wait
                    while time.time() < deadline:
                        if self._client_gone():
                            summary["client_gone"] = True
                            set_state(sess, phase="abandoned", next_try_at=None)
                            return
                        time.sleep(min(1.0, max(0.0, deadline - time.time())))
                    continue
                if err is not None:
                    raise err
                break
            status = resp.status
            if history:
                set_state(sess, phase=("recovered" if status not in retryable else "gave_up"), route=name,
                          model=summary.get("model"), attempt=attempt, max_retries=max_retries,
                          last_status=status, next_try_at=None)
            self.send_response(resp.status, resp.reason)
            for k, v in resp.getheaders():
                if k.lower() in HOP_BY_HOP or k.lower() == "content-length":
                    continue
                self.send_header(k, v)
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            while True:
                chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(4096)   # as data arrives → SSE flows
                if not chunk:
                    break
                self.wfile.write(f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n"); self.wfile.flush()
        except Exception as e:  # noqa: BLE001
            summary["relay_error"] = repr(e)
            if history:
                set_state(sess, phase="gave_up", last_error=repr(e)[:300], next_try_at=None)
            try:
                if status is None:
                    self._send_plain(502, "proxy relay failed", f"oc_payload_proxy: upstream relay failed: {e!r}".encode())
            except Exception:  # noqa: BLE001
                pass
        finally:
            if conn is not None:
                conn.close()
            self.close_connection = True
            rec = {"ts": ts, "method": self.command, "path": self.path.split("?")[0], "status": status,
                   "ms": int((time.time() - t0) * 1000), "req_bytes": len(body), "session": sess}
            if history:
                rec["retries"] = history
            rec.update(summary)
            log_line(rec)

    do_POST = _forward
    do_GET = _forward
    do_DELETE = _forward
    do_PUT = _forward

# @beacon[
#   id=auto-beacon@main-8x8g,
#   role=main,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=int(os.environ.get("OC_PROXY_PORT", "4097")))
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--upstream", default=os.environ.get("OC_PROXY_UPSTREAM", "https://api.anthropic.com"))
    a = ap.parse_args()
    global DEFAULT_UPSTREAM
    DEFAULT_UPSTREAM = a.upstream.rstrip("/")
    srv = ThreadingHTTPServer((a.bind, a.port), Relay)
    srv.daemon_threads = True
    print(f"[proxy] oc_payload_proxy listening on http://{a.bind}:{a.port} → default {DEFAULT_UPSTREAM} + routes "
          f"{sorted(ROUTES)}  features={FEATURES}  log={LOG_PATH}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
