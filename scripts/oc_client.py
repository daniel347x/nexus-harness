"""oc_client.py — the one client of the OpenCode server API, shared by every oc_* script.

The server is OpenCode's background service (`opencode service`, 127.0.0.1:4096 in WSL; Windows reaches it through
WSL's localhost forwarding). Every route is under /api, replies are wrapped in {"data": …}, lists are paged with
{"cursor": {"next": …}}, and errors are {"_tag", "message"}. The service always has a password: Basic auth, user
`opencode`. It is read once per process from OPENCODE_PASSWORD (or OPENCODE_SERVER_PASSWORD), else from the service
config, ~/.config/opencode/service.json in WSL: read directly on Linux, through `wsl.exe` on Windows.

A session's history is one flat list of messages typed by `type`: user · assistant · synthetic · system · shell ·
skill · compaction · agent-switched · model-switched · location-switched · idle. An assistant message holds
`content` items (text · reasoning · tool), `agent`, `model {id, providerID, variant}`, `cost`, `tokens {input, output,
reasoning, cache {read, write}}`, `finish`, `error`. The msg_* helpers read them, so no script parses raw shapes.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

OPENCODE_BASE = os.environ.get("OPENCODE_BASE", "http://127.0.0.1:4096").rstrip("/")
SERVICE_CONFIG = "~/.config/opencode/service.json"
PAGE = 200

_auth: str | None = None


class OpenCodeError(RuntimeError):
    def __init__(self, status: int, tag: str, message: str):
        super().__init__(f"HTTP {status} {tag}: {message}")
        self.status, self.tag, self.message = status, tag, message


def _password() -> str:
    pw = os.environ.get("OPENCODE_PASSWORD") or os.environ.get("OPENCODE_SERVER_PASSWORD")
    if pw:
        return pw
    if os.name != "nt":
        with open(os.path.expanduser(SERVICE_CONFIG), encoding="utf-8") as f:
            return json.load(f)["password"]
    out = subprocess.run(["wsl.exe", "-e", "sh", "-c", 'cat "$HOME/.config/opencode/service.json"'],
                         capture_output=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError("cannot read the OpenCode service password from WSL ~/.config/opencode/service.json: "
                           + out.stderr.decode("utf-8", "replace").strip()[:300])
    return json.loads(out.stdout.decode("utf-8"))["password"]


def auth_header() -> str:
    global _auth
    if _auth is None:
        _auth = "Basic " + base64.b64encode(f"opencode:{_password()}".encode()).decode()
    return _auth


def _unwrap(parsed: Any) -> Any:
    return parsed["data"] if isinstance(parsed, dict) and "data" in parsed else parsed


def oc_raw(method: str, path: str, body: Any = None, timeout: int = 60) -> tuple[int, Any]:
    """(status, parsed JSON — still wrapped — or text). Never raises on HTTP errors."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json", "Authorization": auth_header()}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(OPENCODE_BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    if not raw:
        return status, None
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw.decode("utf-8", "replace")


def oc_request(method: str, path: str, body: Any = None, timeout: int = 60) -> tuple[int, Any]:
    """(status, unwrapped data, or the server's error object/text). Never raises on HTTP errors."""
    status, parsed = oc_raw(method, path, body, timeout)
    return status, (_unwrap(parsed) if status < 400 else parsed)


def oc_call(method: str, path: str, body: Any = None, timeout: int = 60) -> Any:
    """The unwrapped data; raises OpenCodeError on an HTTP error."""
    status, parsed = oc_raw(method, path, body, timeout)
    if status >= 400:
        tag = parsed.get("_tag", "") if isinstance(parsed, dict) else ""
        msg = parsed.get("message", "") if isinstance(parsed, dict) else str(parsed or "")
        raise OpenCodeError(status, tag, msg[:500])
    return _unwrap(parsed)


def oc_get(path: str, timeout: int = 60) -> Any:
    return oc_call("GET", path, None, timeout)


def _query(params: dict[str, Any] | None) -> str:
    items = {k: v for k, v in (params or {}).items() if v is not None}
    return ("?" + urllib.parse.urlencode(items)) if items else ""


def oc_list(path: str, params: dict[str, Any] | None = None, timeout: int = 60, max_items: int | None = None,
            deadline: float | None = None) -> list:
    """Every item of a paged list (following cursor.next), or the first max_items. A cursor already encodes the
    order and the filters, so a follow-up page sends only the cursor and the page size.
    Optional monotonic deadline bounds paging; each request gets at most the remaining time."""
    items: list = []
    first = dict(params or {})
    first.setdefault("limit", PAGE)
    query = _query(first)
    while True:
        remaining = timeout if deadline is None else min(timeout, deadline - time.monotonic())
        if remaining <= 0:
            raise TimeoutError("OpenCode list deadline exceeded")
        status, parsed = oc_raw("GET", path + query, None, remaining)
        if status >= 400:
            tag = parsed.get("_tag", "") if isinstance(parsed, dict) else ""
            raise OpenCodeError(status, tag, (parsed.get("message", "") if isinstance(parsed, dict) else str(parsed))[:500])
        page = (parsed or {}).get("data") or []
        items.extend(page)
        if max_items is not None and len(items) >= max_items:
            return items[:max_items]
        cursor = ((parsed or {}).get("cursor") or {}).get("next")
        if not cursor or len(page) < int(first["limit"]):
            return items
        query = _query({"cursor": cursor, "limit": first["limit"]})


# ----------------------------------------------------------------------------- sessions


def oc_info() -> dict:
    return oc_get("/api/info", timeout=20)


def oc_sessions(**filters: Any) -> list[dict]:
    """Sessions across every project, newest updated first (filters: parentID, directory, project, search)."""
    return oc_list("/api/session", filters, timeout=120)


def oc_session(sid: str) -> dict:
    return oc_get(f"/api/session/{sid}")


def oc_children(parent_sid: str) -> list[dict]:
    """Direct children (workers) of a session, newest updated first."""
    kids = oc_list("/api/session", {"parentID": parent_sid})
    return sorted(kids, key=lambda s: (s.get("time") or {}).get("updated", 0), reverse=True)


def oc_status() -> tuple[bool, dict[str, str]]:
    """(ok, {session_id: "busy"}) — the server lists only running sessions: absent == idle."""
    try:
        d = oc_get("/api/session/active", timeout=20) or {}
    except Exception as e:  # noqa: BLE001
        print(f"  (note: /api/session/active unavailable: {str(e)[:160]})")
        return False, {}
    return True, {sid: "busy" for sid in (d if isinstance(d, dict) else {})}


def oc_messages(sid: str, type: str | None = None) -> list[dict]:
    """The whole history of one session, oldest first. The list index is a stable pin (append-only)."""
    return oc_list(f"/api/session/{sid}/message", {"order": "asc", "type": type}, timeout=120)


def oc_recent_messages(sid: str, n: int = 20, type: str | None = None) -> list[dict]:
    """The newest `n` messages of one session (optionally one type), newest first, without paging the whole history."""
    return oc_list(f"/api/session/{sid}/message", {"order": "desc", "type": type, "limit": n},
                   timeout=60, max_items=n)


def oc_inbox(sid: str) -> list[dict]:
    """Prompts accepted but not yet taken into the history, in delivery order. A prompt keeps its `msg_` id from the
    inbox into the history, so an id found here is pending and an id found in the history is delivered."""
    return oc_get(f"/api/session/{sid}/inbox") or []


# @beacon[
#   id=oc_client@prompt_evidence,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Exact-id prompt evidence: inbox first then session-scoped paged history until that id or exhaustion, never a newest-N window or an assumed individual-message endpoint. Returns pending/delivered/missing plus unchanged native body for exact comparison; read failures stay errors.,
# ]
def prompt_evidence(sid: str, mid: str) -> tuple[str, dict | None]:
    """Exact native id in inbox or fully paged session history, with its unchanged body.

    Inbox-before-history cannot miss an atomic promotion. A native history user has no
    sessionID: its provenance is this server-held session-scoped read, not a fabricated
    field. The public protocol has a message-list endpoint, not an individual-message GET.
    Missing means actual exhaustion (or a gone session), never a newest-50 cutoff.
    """
    try:
        inbox = oc_inbox(sid)
    except OpenCodeError as error:
        if error.status != 404:
            raise
        return "missing", None
    if not isinstance(inbox, list):
        raise OpenCodeError(502, "read-failure", "OpenCode session inbox has an unexpected shape")
    pending = next((i for i in inbox if isinstance(i, dict) and i.get("id") == mid), None)
    if pending is not None:
        return "pending", pending
    query, seen = {"order": "desc", "limit": PAGE}, set()
    while True:
        status, parsed = oc_raw("GET", f"/api/session/{sid}/message" + _query(query))
        if status == 404:
            return "missing", None
        if status >= 400:
            tag = parsed.get("_tag", "") if isinstance(parsed, dict) else ""
            msg = parsed.get("message", "") if isinstance(parsed, dict) else str(parsed or "")
            raise OpenCodeError(status, tag, msg[:500])
        page = parsed.get("data") if isinstance(parsed, dict) else None
        if not isinstance(page, list) or any(not isinstance(m, dict) or not isinstance(m.get("id"), str) for m in page):
            raise OpenCodeError(502, "read-failure", "OpenCode history page has an unexpected shape")
        item = next((m for m in page if m["id"] == mid), None)
        if item is not None:
            return "delivered", item
        cursor = (parsed.get("cursor") or {}).get("next")
        if not cursor:
            return "missing", None
        if cursor in seen:
            raise OpenCodeError(502, "read-failure", "OpenCode receipt history cursor did not advance")
        seen.add(cursor)
        query = {"cursor": cursor, "limit": PAGE}


# @beacon[
#   id=oc_client@confirm_prompt,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Where a sent prompt is, by its msg_ id: delivered (found by exact id in paged history) / pending (inbox at the next step boundary) / missing (exhausted); shared by oc_steer and oc_launch, with unchanged public states/detail strings.,
# ]
def confirm_prompt(sid: str, mid: str, wait_s: float = 4.0) -> tuple[str, str]:
    """Where a sent prompt is, by its msg_ id, waiting up to wait_s for it to be taken in → (state, detail):
    "delivered" (exact id in paged history) · "pending" (inbox, taken at its next step boundary)
    · "missing" (exhausted). Inbox-first reads cannot miss a promotion; there is no newest-N cutoff."""
    deadline = time.time() + wait_s
    while True:
        state, _item = prompt_evidence(sid, mid)
        if state == "delivered":
            return "delivered", f"{mid} is in the session's history"
        if time.time() >= deadline:
            if state == "pending":
                return "pending", f"{mid} is pending in the session's inbox (taken at its next step boundary)"
            return "missing", f"{mid} is in neither the session's history nor its inbox"
        time.sleep(1.0)


def oc_create_session(*, title: str, agent: str | None = None, model: dict | None = None, parent: str | None = None,
                      metadata: dict | None = None, directory: str | None = None,
                      permissions: list | None = None, id: str | None = None) -> tuple[int, Any]:
    """Create a session. Always pass `model`: a session's agent does NOT supply it, and a session without one runs on
    the server's default (the config's `model`, else the first text model in the catalog). `id` is a caller-chosen
    native `ses_…` id for launch idempotency: the server returns an existing session with the same id."""
    body: dict[str, Any] = {"title": title}
    if agent:
        body["agent"] = agent
    if model:
        body["model"] = model
    if parent:
        body["parentID"] = parent
    if metadata is not None:
        body["metadata"] = metadata
    if directory:
        body["location"] = {"directory": directory}
    if permissions is not None:
        body["permissions"] = permissions
    if id:
        body["id"] = id
    return oc_request("POST", "/api/session", body)


def oc_update_session(sid: str, *, title: str | None = None, metadata: dict | None = None) -> tuple[int, Any]:
    body: dict[str, Any] = {}
    if title is not None:
        body["title"] = title
    if metadata is not None:
        body["metadata"] = metadata
    return oc_request("PATCH", f"/api/session/{sid}", body)


def oc_prompt(sid: str, text: str, delivery: str | None = None, resume: bool | None = None,
              *, id: str | None = None) -> tuple[int, Any]:
    """Queue a prompt (always asynchronous); 200 → the inbox item {id: msg_…, payload, delivery}.
    delivery: "steer" (the default: taken at the session's next step boundary, e.g. when a running tool call returns)
    | "queue" (taken only after the current turn ends). An idle session is woken unless resume is False. `id` is a
    caller-chosen native `msg_…` id; the first admission wins upstream without comparing the body, so compare the
    returned item before acknowledging anything."""
    body: dict[str, Any] = {"text": text}
    if delivery:
        body["delivery"] = delivery
    if resume is not None:
        body["resume"] = resume
    if id:
        body["id"] = id
    return oc_request("POST", f"/api/session/{sid}/prompt", body)


def oc_wait(sid: str, timeout: int = 3600) -> tuple[int, Any]:
    """Block until the session is idle (204), or until `timeout` seconds pass (raises TimeoutError)."""
    return oc_request("POST", f"/api/experimental/session/{sid}/wait", None, timeout=timeout)


def oc_interrupt(sid: str) -> tuple[int, Any]:
    return oc_request("POST", f"/api/session/{sid}/interrupt")


def oc_fork(sid: str, before: str | None = None) -> tuple[int, Any]:
    return oc_request("POST", f"/api/session/{sid}/fork", {"before": before} if before else {})


def oc_delete(sid: str) -> tuple[int, Any]:
    """Deletes the session AND its children."""
    return oc_request("DELETE", f"/api/session/{sid}")


def oc_switch_agent(sid: str, agent: str) -> tuple[int, Any]:
    return oc_request("POST", f"/api/session/{sid}/agent", {"agent": agent})


def oc_switch_model(sid: str, model: dict) -> tuple[int, Any]:
    return oc_request("POST", f"/api/session/{sid}/model", {"model": model})


def oc_mcp() -> list[dict]:
    return oc_get("/api/mcp", timeout=30) or []


def oc_models() -> list[dict]:
    return oc_get("/api/model", timeout=60) or []


# ----------------------------------------------------------------------------- models


def parse_model_ref(spec: str) -> dict:
    """'provider/model[#variant]' → {"providerID", "id"[, "variant"]}. The first '/' splits the provider."""
    spec = spec.strip()
    base, _, variant = spec.partition("#")
    provider, sep, model = base.partition("/")
    if not sep or not provider or not model:
        raise ValueError(f"model must be provider/model[#variant], got {spec!r}")
    ref = {"providerID": provider, "id": model}
    if variant:
        ref["variant"] = variant
    return ref


def model_ref_str(ref: Any) -> str:
    if not isinstance(ref, dict):
        return str(ref or "?")
    s = f"{ref.get('providerID', '?')}/{ref.get('id', '?')}"
    return s + (f"#{ref['variant']}" if ref.get("variant") else "")


# ----------------------------------------------------------------------------- messages


def msg_type(m: dict) -> str:
    return str(m.get("type") or "?")


def msg_created(m: dict) -> int:
    return int((m.get("time") or {}).get("created") or 0)


def msg_text(m: dict) -> str:
    """The readable text of a message: a user/synthetic/system message's text, an assistant's text items, a shell
    command and its output, a compaction summary."""
    t = msg_type(m)
    if t == "assistant":
        return "\n".join(c.get("text", "") for c in (m.get("content") or [])
                         if c.get("type") == "text" and c.get("text"))
    if t == "shell":
        out = m.get("output")
        out = out.get("output", "") if isinstance(out, dict) else (out or "")
        return f"$ {m.get('command', '')}\n{out}".rstrip()
    if t == "compaction":
        return str(m.get("summary") or "")
    text = m.get("text")
    if isinstance(text, str):
        return text
    payload = m.get("payload")
    return str(payload.get("text", "")) if isinstance(payload, dict) else ""


def msg_reasoning(m: dict) -> str:
    return "\n".join(c.get("text", "") for c in (m.get("content") or [])
                     if c.get("type") == "reasoning" and c.get("text"))


def tool_output_text(state: dict) -> str:
    parts = state.get("content") or []
    if isinstance(parts, str):
        return parts
    return "\n".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text")


def msg_tools(m: dict) -> list[dict]:
    """An assistant message's tool calls: [{name, status, input, output, error, metadata}]."""
    out = []
    for c in (m.get("content") or []):
        if c.get("type") != "tool":
            continue
        st = c.get("state") or {}
        err = st.get("error")
        out.append({
            "name": c.get("name") or "?",
            "status": st.get("status") or "?",
            "input": st.get("input"),
            "output": tool_output_text(st),
            "error": (err.get("message") if isinstance(err, dict) else err) or "",
            "metadata": st.get("metadata") or {},
        })
    return out


def msg_cost(m: dict) -> float:
    c = m.get("cost")
    return float(c) if isinstance(c, (int, float)) else 0.0


def msg_tokens(m: dict) -> dict:
    t = m.get("tokens") or {}
    cache = t.get("cache") or {}
    return {"input": t.get("input", 0) or 0, "output": t.get("output", 0) or 0,
            "reasoning": t.get("reasoning", 0) or 0,
            "cache": {"read": cache.get("read", 0) or 0, "write": cache.get("write", 0) or 0}}


def msg_error(m: dict) -> str:
    e = m.get("error")
    if not e:
        return ""
    return str(e.get("message") or e) if isinstance(e, dict) else str(e)


def who(m: dict) -> str:
    """'user' · 'assistant (agent)' · the message type for the rest."""
    t = msg_type(m)
    if t == "assistant":
        return f"assistant ({m.get('agent')})" if m.get("agent") else "assistant"
    return t
