"""Shared lexical JSON spans and display policies; stdlib only."""
import hashlib
import json
import re
from dataclasses import dataclass, field

SOURCE_LIMIT = 16 * 1024 * 1024
BODY_LIMIT = 8 * 1024 * 1024
PREVIEW_DATA_LIMIT = 64 * 1024
PREVIEW_DATA_NODES = 2048
PREVIEW_DATA_DEPTH = 32

# Claude Code 2.1.2xx stores a pasted prompt as <pasted_content id=X>\nTEXT\n</pasted_content id=X>
# (and its UserPromptSubmit hook reports it so); the human's prompt is TEXT.
PASTE_WRAPPER = re.compile(r'\s*<pasted_content id="([^"\n]{1,64})">\n(.*)\n</pasted_content id="\1">\s*', re.S)


def unwrap_paste(text):
    """The prompt as pasted: unwraps a prompt that is exactly one Claude Code paste wrapper, else unchanged."""
    match = PASTE_WRAPPER.fullmatch(text) if isinstance(text, str) else None
    return match.group(2) if match else text


# A user message that is a slash command (`/compact`, `/model opus`), never a path like `/mnt/e/…`.
SLASH_COMMAND = re.compile(r"/[A-Za-z][A-Za-z0-9_:.-]*(?=\s|$)")

MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@\[\]-]{0,127}\Z")
EFFORT_LEVEL = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")

def call_facts(model, effort, per_turn, api_error=False):
    """(states a call, model, thinking level) as ONE assistant record records them; display only.

    Claude Code (2.1.296+) writes on every assistant record the model that answered
    (`message.model`), the request's `output_config.effort` (`effort`) and the per-turn
    effort it sent (`perTurnEffort`); its own per-turn level reads `perTurnEffort ?? effort`.
    A synthetic or API-error record states no call. Two different levels on one record are
    no certain level, and a level never stands without its model: None, never a default.
    """
    if api_error or model == "<synthetic>":
        return False, None, None
    if not isinstance(model, str) or not MODEL_ID.fullmatch(model):
        return True, None, None
    levels = {value for value in (effort, per_turn) if value is not None}
    level = next(iter(levels)) if len(levels) == 1 else None
    return True, model, level if isinstance(level, str) and EFFORT_LEVEL.fullmatch(level) else None


class ReaderError(Exception):
    def __init__(self, code, status, message):
        super().__init__(message)
        self.code, self.status = code, status


def digest(data):
    return hashlib.sha256(data).hexdigest()


@dataclass
class Span:
    start: int
    end: int
    kind: str
    scalar: object = None
    children: list = field(default_factory=list)

    def members(self):
        if self.kind != "object":
            raise ValueError("expected object")
        result = {}
        for key, value in self.children:
            if key in result:
                raise ValueError("duplicate structural field: " + key)
            result[key] = value
        return result


# @beacon[
#   id=opc-pane@parse_record,
#   slice_labels=opc-pane,opc-rows,
#   kind=ast,
#   comment=Validate native JSON with lexical spans; only structural objects reject duplicate keys, opaque payloads keep their original tokens.,
# ]
def parse_record(raw):
    """A bounded JSON grammar, not a regex payload extractor or numeric round trip."""
    text = raw.decode("utf-8", errors="strict")
    pos, tokens = 0, 0
    decoder = json.JSONDecoder()

    def ws():
        nonlocal pos
        while pos < len(text) and text[pos] in " \t\r\n":
            pos += 1

    def value(depth=0):
        nonlocal pos, tokens
        tokens += 1
        if tokens > 500_000 or depth > 128:
            raise ValueError("JSON parser limit")
        ws()
        start = pos
        if pos >= len(text):
            raise ValueError("incomplete JSON value")
        c = text[pos]
        if c in "{[":
            pos += 1
            items = []
            closing = "}" if c == "{" else "]"
            ws()
            if pos < len(text) and text[pos] == closing:
                pos += 1
                return Span(start, pos, "object" if c == "{" else "array", children=items)
            while True:
                if c == "{":
                    key = value(depth + 1)
                    if key.kind != "string":
                        raise ValueError("object key is not a string")
                    ws()
                    if pos >= len(text) or text[pos] != ":":
                        raise ValueError("missing colon")
                    pos += 1
                    items.append((key.scalar, value(depth + 1)))
                else:
                    items.append(value(depth + 1))
                ws()
                if pos >= len(text):
                    raise ValueError("unclosed container")
                sep = text[pos]
                pos += 1
                if sep == closing:
                    break
                if sep != ",":
                    raise ValueError("invalid separator")
            return Span(start, pos, "object" if c == "{" else "array", children=items)
        if c == '"':
            scalar, pos = decoder.raw_decode(text, pos)
            # Reject lone surrogates: these cannot be a UTF-8 Copy snapshot.
            scalar.encode("utf-8", errors="strict")
            return Span(start, pos, "string", scalar)
        for token, scalar in (("true", True), ("false", False), ("null", None)):
            if text.startswith(token, pos):
                pos += len(token)
                return Span(start, pos, token, scalar)
        match = re.match(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", text[pos:])
        if not match:
            raise ValueError("invalid JSON token")
        pos += len(match[0])
        return Span(start, pos, "number")

    root = value()
    ws()
    if pos != len(text):
        raise ValueError("trailing JSON data")
    root.members()
    return text, root


def scalar(fields, key, kind=None, default=None):
    node = fields.get(key)
    if node is None:
        return default
    if kind and node.kind != kind:
        raise ValueError("invalid structural field: " + key)
    return node.scalar


def projection(text, node):
    if node is None:
        return None
    return node.scalar if node.kind == "string" else text[node.start:node.end]


# @beacon[
#   id=opc-pane@preview_data,
#   slice_labels=opc-pane,opc-rows,opc-codenomad-tools,
#   kind=ast,
#   comment=Bounded structural input transport facts and original string lengths/Unicode edges; only shared JS renders/sorts/crops, raw selected snapshots stay unchanged.,
# ]
def preview_data(text, span):
    """Transport facts only; the shared JS core owns crop/sort/render."""
    nodes, used = 0, 0

    def charge(value):
        nonlocal used
        used += len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if used > PREVIEW_DATA_LIMIT:
            raise ValueError("descriptor byte limit")

    def project(value, depth=0):
        nonlocal nodes
        nodes += 1
        if nodes > PREVIEW_DATA_NODES or depth > PREVIEW_DATA_DEPTH:
            raise ValueError("descriptor structure limit")
        if value.kind == "string":
            original = value.scalar
            # JSON.stringify string code-point length without a serialized copy.
            length = 2 + sum(2 if c in '\\"\b\t\n\f\r' else 6 if ord(c) < 32 else 1 for c in original)
            result = {"kind": "string", "characters": len(original), "jsonCharacters": length,
                      "head": original[:128], "tail": original[-128:]}
        elif value.kind == "number":
            lexeme = text[value.start:value.end]
            if len(lexeme) > 128 or (re.fullmatch(r"-?[0-9]+", lexeme) and abs(int(lexeme)) > 2**53 - 1):
                raise ValueError("opaque numeric precision")
            result = {"kind": "number", "lexeme": lexeme}
        elif value.kind in ("null", "true", "false"):
            result = {"kind": value.kind}
        elif value.kind == "array":
            charge({"kind": "array", "items": []})
            return {"kind": "array", "items": [project(child, depth + 1) for child in value.children]}
        elif value.kind == "object":
            members = value.members()  # ambiguous payload duplicates remain raw, not previewable
            charge({"kind": "object", "entries": []})
            entries = []
            for key, child in members.items():
                if len(key.encode("utf-8")) > 4096:
                    raise ValueError("descriptor key limit")
                charge([key, None])
                entries.append([key, project(child, depth + 1)])
            return {"kind": "object", "entries": entries}
        else:
            raise ValueError("unsupported preview value")
        charge(result)
        return result

    try:
        if span is None:
            return {"schemaVersion": 1, "status": "unavailable", "reason": "input missing"}
        result = {"schemaVersion": 1, "status": "available", "value": project(span)}
        if len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > PREVIEW_DATA_LIMIT:
            raise ValueError("descriptor byte limit")
        return result
    except (ValueError, UnicodeError, RecursionError) as error:
        return {"schemaVersion": 1, "status": "unavailable", "reason": str(error)[:128]}


def result_projection(text, node):
    if node is None:
        return None
    if node.kind != "array":
        return projection(text, node)
    pieces = []
    for block in node.children:
        fields = block.members() if block.kind == "object" else {}
        if scalar(fields, "type", "string") == "text":
            pieces.append(scalar(fields, "text", "string"))
        else:
            pieces.append(text[block.start:block.end])
    return "\n\n".join(pieces)


# @beacon[
#   id=opc-pane@thinking_projection,
#   slice_labels=opc-pane,opc-rows,
#   kind=ast,
#   comment=Display-only indexed thinking summaries with whole-body budgets; signatures and redacted data never enter text or context.,
# ]
def thinking_projection(block, number, remaining, *, streaming=False):
    typ = scalar(block, "type", "string")
    value = scalar(block, "thinking", "string", default="") if typ == "thinking" else ""
    size = len(value.encode("utf-8"))
    availability = ("redacted" if typ == "redacted_thinking" else
                    "body-limit" if size > remaining else "stored" if value else "omitted")
    return {"blockIndex": number, "text": value if availability == "stored" else "",
            "textBytes": size, "availability": availability, "streaming": streaming}, (size if availability == "stored" else 0)
