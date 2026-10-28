"""Request-local, stdlib-only Streamable HTTP transport for NEXUS reads.

Lookup stays in oc_view; this module never constructs its SDK Workflowy client.
The pane server establishes the scripts import path before importing this module.
"""
import http.client
import json
import socket
import urllib.error
import urllib.request

from oc_view import _mcp_url, _token

WIRE_LIMIT = 16 * 1024 * 1024
PROTOCOL_VERSION = "2025-11-25"


class GlimpseError(Exception):
    """Credential-free transport or daemon failure suitable for the pane.

    ``error`` keeps a daemon fault's structured ``{code, message}`` (redacted) when the reply carried one.
    """

    def __init__(self, message, error=None):
        super().__init__(message)
        self.error = error


def _fault(reply):
    """A daemon operation fault: ``{"success": false, ...}`` or ``{"status": "error", ...}``."""
    return isinstance(reply, dict) and (reply.get("success") is False or reply.get("status") == "error")


def _row_envelope(reply):
    """glimpse.run / anchor.dock.render's include_rows reply: {markdown, rows, render_spec?}."""
    return (isinstance(reply, dict) and isinstance(reply.get("markdown"), str) and isinstance(reply.get("rows"), list)
            and (reply.get("render_spec") is None or isinstance(reply["render_spec"], dict)))


class _SessionLost(Exception):
    pass


# @beacon[
#   id=opc-pane@GlimpseClient,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Request-local stdlib MCP JSON/SSE reads for explicit View requests using oc_view lookup; retries session loss once except cache refresh. Returns literal Markdown or requested include_rows envelopes; daemon faults retain their structured code and own safe message, never a dict repr. No inventory lookups, pooling or background traffic.,
# ]
class GlimpseClient:
    """Initialize once per explicit View request/batch; no pool, polling or background traffic."""

    def __init__(self, *, url=None, token=None, timeout=30):
        try:
            self.url = _mcp_url() if url is None else url
            self._secret = _token() if token is None else token
        except (OSError, ValueError):
            raise GlimpseError("Cannot read Workflowy connection settings/token file") from None
        if not self._secret:
            raise GlimpseError("Workflowy bearer token missing; check WORKFLOWY_BROKER_TOKEN or oc_view's broker_token file setting")
        self.timeout = timeout
        self.session = None
        self.protocol = PROTOCOL_VERSION
        self._ready = False
        self._id = 0

    def safe_error(self, message):
        # Daemon errors can quote transport data. Never surface our bearer value.
        return str(message).replace(self._secret, "[redacted]")

    def _daemon_error(self, message, reply):
        """GlimpseError for a failed tool reply, keeping a structured error code/message when present.

        The error's text is the fault's own message (never a dict's repr, which repeats it inside its legacy result);
        a reply without one keeps the text it came with.
        """
        error = reply.get("error") if isinstance(reply, dict) else None
        structured = ({"code": error["code"], "message": self.safe_error(error.get("message", ""))}
                      if isinstance(error, dict) and isinstance(error.get("code"), str) else None)
        if not isinstance(error, dict) and isinstance(message, str) and message.lstrip().startswith("{"):
            try:
                twin = json.loads(message)  # an isError reply's JSON text: its message names the fault
            except ValueError:
                twin = None
            error = twin.get("error") if _fault(twin) else None
        if isinstance(error, dict) and isinstance(error.get("message"), str) and error["message"].strip():
            message = error["message"]
        elif isinstance(message, dict):
            message = message.get("message") if isinstance(message.get("message"), str) and message["message"].strip() else json.dumps(message)
        return GlimpseError(self.safe_error(message), structured)

    def _rpc(self, method, params, *, notification=False):
        self._id += 1
        request_id = self._id
        body = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            body["id"] = request_id
        headers = {"Authorization": "Bearer " + self._secret,
                   "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if self._ready:
            headers["MCP-Protocol-Version"] = self.protocol
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(self.url, json.dumps(body).encode(), headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                if response.headers.get("Mcp-Session-Id"):
                    self.session = response.headers["Mcp-Session-Id"]
                if notification:
                    return None
                if response.headers.get_content_type() == "text/event-stream":
                    result = self._sse(response, request_id)
                else:
                    raw = response.read(WIRE_LIMIT + 1)
                    if len(raw) > WIRE_LIMIT:
                        raise GlimpseError("Workflowy reply exceeds 16 MiB wire limit")
                    length = response.headers.get("Content-Length")
                    if length is not None and len(raw) != int(length):
                        raise GlimpseError("Incomplete Workflowy MCP reply")
                    result = self._matching(json.loads(raw), request_id)
                    if result is None:
                        raise GlimpseError("Workflowy reply has no matching JSON-RPC result")
                return result
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            if code == 404:
                raise _SessionLost() from None
            raise GlimpseError(f"Workflowy MCP HTTP {code}") from None
        except (socket.timeout, TimeoutError):
            raise GlimpseError("Workflowy MCP timed out") from None
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            raise GlimpseError("Cannot reach Workflowy MCP; check oc_view connection settings") from None
        except (ValueError, UnicodeError):
            raise GlimpseError("Malformed or incomplete Workflowy MCP reply") from None

    def _matching(self, message, request_id):
        if not isinstance(message, dict) or message.get("id") != request_id:
            return None
        if "error" in message:
            error = message["error"]
            text = error.get("message", "JSON-RPC error") if isinstance(error, dict) else "JSON-RPC error"
            raise GlimpseError(self.safe_error(text))
        if "result" not in message:
            raise GlimpseError("Workflowy JSON-RPC reply lacks result")
        return message["result"]

    def _sse(self, response, request_id):
        data, total = [], 0
        while True:
            line = response.readline(WIRE_LIMIT + 1)
            total += len(line)
            if total > WIRE_LIMIT:
                raise GlimpseError("Workflowy reply exceeds 16 MiB wire limit")
            if not line:
                raise GlimpseError("Workflowy SSE ended before its matching result")
            line = line.decode("utf-8").rstrip("\r\n")
            if not line:
                if data:
                    message = json.loads("\n".join(data))
                    data = []
                    result = self._matching(message, request_id)
                    if result is not None:
                        return result  # Do not wait for EOF on an open event stream.
            elif line.startswith("data:"):
                data.append(line[5:].removeprefix(" "))

    def _initialize(self):
        result = self._rpc("initialize", {"protocolVersion": PROTOCOL_VERSION,
                           "capabilities": {}, "clientInfo": {"name": "pane-glimpse", "version": "1"}})
        if not isinstance(result, dict) or not isinstance(result.get("protocolVersion"), str):
            raise GlimpseError("Workflowy initialize reply lacks protocolVersion")
        self.protocol = result["protocolVersion"]
        self._ready = True
        self._rpc("notifications/initialized", {}, notification=True)

    def call(self, family, verb, params):
        """Return literal tool text or structured data; never JSON-quote Markdown.

        Only when ``include_rows`` was asked for is a row envelope returned: the structured content, or
        else its JSON text twin decoded; other text (even JSON-looking Markdown) stays literal.
        Only the Handler selects tools. Cache refresh has no replay, even on 404:
        its reply may have been lost after the daemon completed the attempt.
        """
        rows_asked = isinstance(params, dict) and params.get("include_rows") is True
        for attempt in range(2):
            try:
                if not self._ready:
                    self._initialize()
                result = self._rpc("tools/call", {"name": family, "arguments": {"verb": verb, "params": params}})
                if not isinstance(result, dict):
                    raise GlimpseError("Malformed Workflowy tool result")
                content = result.get("content", [])
                if not isinstance(content, list):
                    raise GlimpseError("Malformed Workflowy tool content")
                texts = [part["text"] for part in content
                         if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)]
                structured = result.get("structuredContent")
                if isinstance(structured, dict) and set(structured) == {"result"}:
                    structured = structured["result"]
                if rows_asked and not isinstance(structured, dict) and texts:
                    try:
                        twin = json.loads("\n".join(texts))
                    except ValueError:
                        twin = None
                    if _row_envelope(twin) or (_fault(twin) and isinstance(twin.get("error"), dict)):
                        structured = twin
                if result.get("isError"):
                    raise self._daemon_error("\n".join(texts) or "Workflowy tool failed", structured)
                if _fault(structured):
                    raise self._daemon_error(structured.get("error") or "Workflowy tool failed", structured)
                if rows_asked and _row_envelope(structured):
                    return structured
                if texts:
                    text = "\n".join(texts)
                    if (family, verb) == ("system", "cache.refresh"):
                        try:
                            status = json.loads(text)
                        except ValueError:
                            status = None
                        if isinstance(status, dict) and (status.get("success") is False or status.get("error")):
                            raise GlimpseError(self.safe_error(status.get("error") or "Workflowy cache refresh failed"))
                    return text
                if "structuredContent" in result:
                    structured = result["structuredContent"]
                    return structured["result"] if isinstance(structured, dict) and set(structured) == {"result"} else structured
                raise GlimpseError("Workflowy tool reply contains no text or structured result")
            except _SessionLost:
                self.session, self._ready = None, False
                if attempt or (family, verb) == ("system", "cache.refresh"):
                    raise GlimpseError("Workflowy MCP session lost; request not replayed" if verb == "cache.refresh"
                                       else "Workflowy MCP session lost after one reinitialization") from None
