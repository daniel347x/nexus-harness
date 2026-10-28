"""Guard: no exact-version gate on an external tool in non-test code.

Claude Code stays on automatic updates by Dan's ruling (``claude_version.sh rollback`` is the safety
net), and OpenCode and CodeNomad update too. So nothing in this repository may depend on ONE exact
version of an external tool to keep working: **a tool update must never silently switch a feature
off.** Check the capability the code relies on instead (the field or shape it needs is present and
well formed), failing closed per record or per process, never per version; where no such check is
possible, keep working and warn loudly and visibly (a pane notice, host health or log).

The scan covers every tracked non-test source file (Python, JavaScript, TypeScript, shell) and fails
on a version literal (``"2.1.299"``, ``'v2.0.24'``, ``"2.1.299 (Claude Code)"``), a version-prefix
literal (``startswith("2.1.")``), the same in a regular expression (``r"2\\.1\\.299"``), a shell
comparison or ``case`` arm against a bare version, a Python version-tuple comparison, or a named
version allowlist (``SUPPORTED_VERSIONS = ...``). Comments are not scanned: saying which version
something was observed on is fine. The few legitimate literals are in ALLOWED, each with its reason;
an entry that no longer matches anything fails too, so the list stays small and true.
"""
from __future__ import annotations

import io
import re
import subprocess
import tokenize
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE = {".py", ".js", ".cjs", ".mjs", ".ts", ".sh"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", "tmp", "scratch", "packages",
             ".codenomad", "tests", "fixtures", "vendor"}

# (path relative to the repository, the literal) -> why it is not a version gate.
ALLOWED = {
    ("scripts/oc_usage.py", "2.1.300"):
        "fallback User-Agent for the native OAuth usage endpoint when CLI version discovery fails; "
        "never compared and never gates tool capability or account selection",
    ("scripts/claude_context_mcp.py", "1.0.0"):
        "our own MCP server's version, reported in serverInfo; nothing compares it",
    ("chrome-extension/codenomad-session-watcher/session-focus.js", "0.20.1"):
        "names the CodeNomad build the selectors were read from; used only in the visible failure "
        "message ('CodeNomad may have changed'), never compared",
}

_VERSION = r"v?\d+\.\d+\.\d+(?!\.?\d)"
LITERAL_RULES = (
    ("version literal", re.compile(r"\s*\^?" + _VERSION)),
    ("version-prefix literal", re.compile(r"\s*\^?v?\d+\.\d+\.\Z")),
    ("version regular expression", re.compile(r"\s*\^?v?\d+\\\.\d+\\\.\d+")),
)
CODE_RULES = (
    ("named version allowlist", re.compile(
        r"\b\w*(?:supported|qualified|pinned|tested|allowed|compatible|known_?good|verified)\w*versions?\b",
        re.I)),
    ("version tuple comparison", re.compile(
        r"(?<!sys\.)\bversion\w*\s*(?:[<>]=?|[=!]=)\s*\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\)", re.I)),
)
SHELL_RULES = (
    ("shell version comparison", re.compile(r"(?:\s=|==|!=)\s*" + _VERSION)),
    ("shell case arm on a version", re.compile(r"^\s*" + _VERSION + r"[\s|*]*\)")),
)


def tracked_sources():
    """Every tracked non-test source file (filesystem walk when git is unavailable)."""
    try:
        listed = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True,
                                check=True, timeout=60).stdout.decode("utf-8").split("\0")
        paths = [ROOT / p for p in listed if p]
    except (OSError, subprocess.SubprocessError):
        paths = [p for p in ROOT.rglob("*") if p.is_file()]
    for path in sorted(paths):
        rel = path.relative_to(ROOT)
        name = rel.name
        if (path.suffix not in CODE or set(rel.parts[:-1]) & SKIP_DIRS or name.startswith("test_")
                or name == "conftest.py" or re.search(r"\.test\.[cm]?js\Z", name) or not path.is_file()):
            continue
        yield rel.as_posix(), path.read_text(encoding="utf-8", errors="replace")


def python_parts(text):
    """(line number, literal value) for every string, and (line number, code with strings/comments blanked)."""
    literals, code = [], {}
    lines = text.splitlines()
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return [], list(enumerate(lines, 1))
    blank = {n: list(line) for n, line in enumerate(lines, 1)}
    middle = getattr(tokenize, "FSTRING_MIDDLE", None)
    for token in tokens:
        if token.type in (tokenize.STRING, middle):
            raw = token.string
            body = re.sub(r"\A[rRbBuUfF]{0,2}('''|\"\"\"|'|\")", "", raw) if token.type == tokenize.STRING else raw
            body = re.sub(r"('''|\"\"\"|'|\")\Z", "", body) if token.type == tokenize.STRING else body
            literals.append((token.start[0], body))
        if token.type in (tokenize.STRING, tokenize.COMMENT, middle):
            (r0, c0), (r1, c1) = token.start, token.end
            for row in range(r0, r1 + 1):
                if row in blank:
                    start, end = (c0 if row == r0 else 0), (c1 if row == r1 else len(blank[row]))
                    blank[row][start:end] = " " * (end - start)
    for n, chars in blank.items():
        code[n] = "".join(chars)
    return literals, sorted(code.items())


def script_parts(text, shell=False):
    """The same split for JavaScript/TypeScript (and, with shell=True, shell): strings, comments, code."""
    literals, out, line, i, n = [], [], 1, 0, len(text)
    while i < n:
        ch = text[i]
        if shell and ch == "#" and (i == 0 or text[i - 1] in " \t\n;"):
            while i < n and text[i] != "\n":
                out.append(" "); i += 1
            continue
        if not shell and text.startswith("//", i) and (i == 0 or text[i - 1] != ":"):
            while i < n and text[i] != "\n":
                out.append(" "); i += 1
            continue
        if not shell and text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            chunk = text[i:end]
            out.append("".join("\n" if c == "\n" else " " for c in chunk))
            line += chunk.count("\n"); i = end
            continue
        if ch in "'\"`":
            start_line, j, body = line, i + 1, []
            while j < n and text[j] != ch:
                if text[j] == "\\" and not (shell and ch == "'"):
                    body.append(text[j:j + 2]); j += 2; continue
                if text[j] == "\n":
                    if ch != "`" and not shell:
                        break
                    line += 1
                body.append(text[j]); j += 1
            literals.append((start_line, "".join(body)))
            out.append(ch + " " * (j - i - 1) + (ch if j < n else ""))
            i = j + 1
            continue
        if ch == "\n":
            line += 1
        out.append(ch); i += 1
    return literals, list(enumerate("".join(out).split("\n"), 1))


def violations(rel, text):
    """Every version gate this module recognises in one source file: (rule, line, offending text)."""
    if rel.endswith(".py"):
        literals, code = python_parts(text)
        shell = False
    else:
        shell = rel.endswith(".sh")
        literals, code = script_parts(text, shell=shell)
    found = []
    for number, value in literals:
        for rule, pattern in LITERAL_RULES:
            if pattern.match(value):
                found.append((rule, number, value.strip()[:80]))
                break
    for number, line in code:
        for rule, pattern in CODE_RULES + (SHELL_RULES if shell else ()):
            match = pattern.search(line)
            if match:
                found.append((rule, number, match.group(0).strip()[:80]))
    return found


WHY = ("A tool update must never silently switch a feature off. Claude Code auto-updates "
       "(claude_version.sh rollback is the safety net), and OpenCode and CodeNomad update too, so no code "
       "may gate on one exact external-tool version. Replace each hit with a check of the capability the "
       "code actually relies on (the field or shape it needs is present and well formed), failing closed per "
       "record or per process, never per version; if no such check is possible, keep working and warn "
       "loudly and visibly. A literal that only describes (never compared, never gating) goes into ALLOWED "
       "in scripts/test_no_version_gates.py with its reason.")


class NoVersionGates(unittest.TestCase):
    def test_no_exact_version_gate_on_an_external_tool_in_non_test_code(self):
        hits, used = [], set()
        for rel, text in tracked_sources():
            for rule, number, value in violations(rel, text):
                key = next((k for k in ALLOWED if k[0] == rel and k[1] == value), None)
                if key:
                    used.add(key)
                else:
                    hits.append(f"  {rel}:{number}: {rule}: {value}")
        self.assertFalse(hits, "\n" + WHY + "\n" + "\n".join(hits))
        stale = sorted(set(ALLOWED) - used)
        self.assertFalse(stale, f"ALLOWED entries no longer match anything; remove them: {stale}")

    def test_the_scanner_catches_each_form_of_version_gate_and_ignores_comments_and_addresses(self):
        bad_python = '\n'.join((
            'SUPPORTED_VERSIONS = {"2.1.185", "synthetic-v1"}',
            'if version == "2.1.299": pass',
            "ok = cli in ('2.1.299 (Claude Code)',)",
            'QUALIFIED_CLI_VERSION = "2.1.300"',
            'old = v.startswith("2.1.")',
            'm = re.match(r"^2\\.1\\.299", v)',
            'if version_tuple >= (2, 1, 300): pass',
            'clone = "v2.0.24"',
        ))
        found = violations("x.py", bad_python)
        self.assertEqual(sorted({n for _, n, _ in found}), list(range(1, 9)), found)
        bad_js = "if (info.version === '0.20.1') stop();\nconst knownGoodVersions = [];\nok = v.startsWith(`2.0.`);"
        self.assertEqual(sorted({n for _, n, _ in violations("x.js", bad_js)}), [1, 2, 3])
        bad_sh = '[ "$v" = 2.1.299 ] && exit\ncase "$v" in\n  2.1.299) exit 1 ;;\nesac\n[ "$v" == "2.0.24" ]'
        self.assertEqual(sorted({n for _, n, _ in violations("x.sh", bad_sh)}), [1, 3, 5])
        fine_python = '\n'.join((
            '# Native 2.1.299 emits one assistant record per block; "2.1.300" agrees.',
            'HOST = "127.0.0.1"',
            'URL = "http://127.0.0.1:4096"',
            'if sys.version_info >= (3, 10): pass',
            'raise Refused(f"{v!r} is not a version number like 2.1.300")',
            'VERSION_SHAPE = re.compile(r"\\d+\\.\\d+\\.\\d+")',
            'record.version = scalar(fields, "version", "string")',
        ))
        self.assertEqual(violations("x.py", fine_python), [])
        fine_js = "// Read-only CodeNomad 0.20.1 adapter. 'v2.0.24' in a comment\nconst PANE = 'http://127.0.0.1:9911/api';\n/* 2.1.299 */ x();"
        self.assertEqual(violations("x.js", fine_js), [])
        fine_sh = '# rollback to 2.1.299 = old\ncurl -s "http://127.0.0.1:$PORT/"  # 2.1.300\nver="$(claude --version)"'
        self.assertEqual(violations("x.sh", fine_sh), [])


if __name__ == "__main__":
    unittest.main()
