"""Diff v1: pure normalization of evidence already held by snapshot readers.

No current-file reconstruction: starts, context and prefixes belong to the record.
Optional malformed evidence labels only the diff, never the raw snapshot.
"""
import json
import re

SHELL_REASON = "Shell command; no structured edit diff recorded (it may have edited files)."
HEADER = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:.*)\Z")
COORDS = ("oldStart", "oldLines", "newStart", "newLines")


def recorded_value(text, span):
    """Decode an optional lexical payload, rejecting ambiguous duplicate members locally."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate diff metadata member")
            result[key] = value
        return result
    try:
        return json.loads(text[span.start:span.end], object_pairs_hook=pairs) if span is not None else None
    except (ValueError, TypeError, RecursionError):
        return None


def _result(files=(), source="unavailable", reasons=()):
    value = {"files": list(files), "source": source, "complete": not reasons}
    if reasons:
        value["reason"] = "; ".join(dict.fromkeys(reasons))
    return value


def _unavailable(reason):
    return _result(reasons=[reason])


def _integer(value):
    return type(value) is int and value >= 0


def _counts(lines):
    return sum(s.startswith("+") for s in lines), sum(s.startswith("-") for s in lines)


def _valid_hunk(hunk):
    if not isinstance(hunk, dict) or not all(_integer(hunk.get(k)) for k in COORDS):
        return False
    lines = hunk.get("lines")
    if not isinstance(lines, list) or not all(isinstance(s, str) and
            (s.startswith((" ", "+", "-")) or s == "\\ No newline at end of file") for s in lines):
        return False
    additions, deletions = _counts(lines)
    context = sum(s.startswith(" ") for s in lines)
    return (context + deletions == hunk["oldLines"] and context + additions == hunk["newLines"] and
            (not hunk["oldLines"] or hunk["oldStart"] > 0) and
            (not hunk["newLines"] or hunk["newStart"] > 0))


def _file(path, status, hunks):
    additions, deletions = _counts([line for h in hunks for line in h["lines"]])
    return {"path": path, "status": status, "additions": additions, "deletions": deletions, "hunks": hunks}


def _new_content(path, content, status):
    # Split physical LF lines only; retain CRs, Unicode separators and a missing final LF.
    lines = content.split("\n")
    if lines[-1] == "":
        lines.pop()
    hunk = {"oldStart": 0, "oldLines": 0, "newStart": 1, "newLines": len(lines),
            "lines": ["+" + line for line in lines]}
    if content and not content.endswith("\n"):
        hunk["lines"].append("\\ No newline at end of file")
    return _file(path, status, [hunk])


# @beacon[
#   id=opc-pane@claude_diff,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Normalize recorded Claude structuredPatch or creation content without reconstructing history; unavailable evidence never alters raw snapshots.,
# ]
def claude_diff(tool_name, input_value, result_metadata, status):
    """Claude history and host feed share the same recorded structuredPatch shape."""
    name = tool_name.lower() if isinstance(tool_name, str) else ""
    if name in ("bash", "shell"):
        return _unavailable(SHELL_REASON)
    if name not in ("edit", "multiedit", "write"):
        return None
    if status != "stored":
        return _unavailable("No applied diff recorded: call status " + str(status))
    meta = result_metadata
    if not isinstance(meta, dict):
        return _unavailable("Missing or malformed recorded structuredPatch metadata (aggregate MultiEdit shapes are unverified)")
    path = meta.get("filePath")
    if not isinstance(path, str) or not path:
        return _unavailable("Missing or malformed recorded filePath")
    if meta.get("type") == "create":
        content = meta.get("content")
        if not isinstance(content, str):
            return _unavailable("Missing or malformed recorded creation content")
        reasons = (["Malformed recorded creation structuredPatch"] if
                   "structuredPatch" in meta and meta["structuredPatch"] != [] else [])
        return _result([_new_content(path, content, "added")], "claude-structured-patch", reasons)
    patches = meta.get("structuredPatch")
    if not isinstance(patches, list):
        return _unavailable("Missing or malformed recorded structuredPatch (aggregate MultiEdit shapes are unverified)")
    hunks, reasons = [], []
    for h in patches:
        if _valid_hunk(h):
            hunks.append({**{k: h[k] for k in COORDS}, "lines": list(h["lines"])})
        else:
            reasons.append("Malformed recorded structuredPatch hunk or line counts")
    if not hunks and not reasons and not (meta.get("oldString") == meta.get("newString") and
                                           isinstance(meta.get("oldString"), str)):
        reasons.append("No recorded update hunks")
    return _result([_file(path, "modified", hunks)] if hunks or not reasons else [],
                   "claude-structured-patch", reasons)


def _unified(patch):
    """Parse one files[] patch; headers/preamble never become source lines."""
    hunks, reasons, current = [], [], None
    def finish():
        if current is not None:
            if _valid_hunk(current):
                hunks.append(current)
            else:
                reasons.append("Malformed unified hunk line counts or prefixes")
    # LF is the wire patch delimiter; do not split source Unicode line separators.
    lines = patch.split("\n")
    if lines[-1] == "":
        lines.pop()
    for line in lines:
        match = HEADER.fullmatch(line)
        if match:
            finish()
            a, b, c, d = match.groups()
            try:
                current = dict(zip(COORDS, (int(a), int(b) if b is not None else 1,
                                            int(c), int(d) if d is not None else 1)))
                current["lines"] = []
            except ValueError:
                current = None
                reasons.append("Malformed unified hunk integer")
        elif line.startswith("@@"):
            finish()
            current = None
            reasons.append("Malformed unified hunk header")
        elif current is not None:
            current["lines"].append(line)
        elif not (line.startswith(("Index:", "===", "---", "+++", "diff ", "index ",
                                   "new file mode ", "deleted file mode ", "rename ", "similarity index ")) or not line):
            reasons.append("Unrecognized unified patch evidence outside hunks")
    finish()
    return hunks, reasons


# @beacon[
#   id=opc-pane@opencode_diff,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Normalize all recorded OpenCode files patches for direct and mailbox snapshots; write content is explicitly not a recorded before/after.,
# ]
def opencode_diff(tool_name, state):
    """One unified parser for both OpenCode read paths; optional evidence is local."""
    name = tool_name.lower() if isinstance(tool_name, str) else ""
    if name in ("bash", "shell"):
        return _unavailable(SHELL_REASON)
    if name not in ("edit", "patch", "apply_patch", "write"):
        return None
    if not isinstance(state, dict):
        return _unavailable("Missing or malformed recorded tool state")
    if state.get("status") != "completed" or "error" in state:
        return _unavailable("No applied diff recorded: call status " + str(state.get("status", "missing")))
    if name == "write":
        inp = state.get("input")
        if not isinstance(inp, dict) or not isinstance(inp.get("content"), str):
            return _unavailable("Missing or malformed recorded write content")
        path = inp.get("path", inp.get("filePath", inp.get("file_path")))
        if not isinstance(path, str) or not path:
            return _unavailable("Missing or malformed recorded write path")
        return _result([_new_content(path, inp["content"], "modified")], "recorded-new-content",
                       ["new content; previous content not recorded"])
    meta = state.get("metadata")
    entries = meta.get("files") if isinstance(meta, dict) else None
    if not isinstance(entries, list) or not entries:
        return _unavailable("Missing or malformed recorded metadata.files patches")
    files, reasons = [], []
    for entry in entries:
        if not isinstance(entry, dict):
            reasons.append("Malformed metadata.files entry")
            continue
        path, status, patch = entry.get("file"), entry.get("status"), entry.get("patch")
        if not isinstance(path, str) or not path or status not in ("added", "modified", "deleted") or not isinstance(patch, str):
            reasons.append("Missing or malformed recorded file path, status or patch")
            continue
        hunks, errors = _unified(patch)
        reasons.extend(path + ": " + error for error in errors)
        value = _file(path, status, hunks)
        for key in ("additions", "deletions"):
            count = entry.get(key)
            if not _integer(count):
                reasons.append(path + ": missing or malformed recorded " + key)
            else:
                if count != value[key]:
                    reasons.append(path + ": recorded " + key + " disagrees with hunk counts")
                value[key] = count
        if "oldPath" in entry:
            if isinstance(entry["oldPath"], str) and entry["oldPath"]:
                value["oldPath"] = entry["oldPath"]
            else:
                reasons.append(path + ": malformed recorded oldPath")
        if hunks or not errors:
            files.append(value)
    return _result(files, "opencode-unified-patch", reasons)
