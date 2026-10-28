#!/usr/bin/env python3
"""oc_binding.py — the ONE reader of a program registry row's binding (decision D4, overnight build U2a [ap:0OP1FK]).

A registry row ([ap:O2QNOF] §6) is bound to the session that runs it in one of two ways: the legacy
`opencode_session_id: "ses_…"` (every OpenCode Conductor; old rows untouched), or the additive
`execution: {backend: claude, id: "cc-…"}` that oc_launch writes IN PLACE of the null sid line when it binds a Claude
Code Conductor. Every script reads a binding through row_binding / row_session_id, never through the raw keys, so a
Claude row is neither mistaken for a reservation nor silently skipped by a liveness or busy check.

Stdlib only and import-free (no oc_* module), so the viewers can use it without import cycles; oc_common re-exports it.
"""
from __future__ import annotations

import json
import re

_CC_ID = re.compile(r"cc-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_UNSET = (None, "", "null", "~")


# @beacon[
#   id=oc_binding@row_binding,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=D4: the ONE reader of a registry row's binding → ('claude', cc-…) from the additive `execution`, ('opencode', ses_…) from the legacy opencode_session_id, None when unbound; a malformed or conflicting binding is ('invalid', …): never a reservation, never live.,
# ]
def row_binding(row: dict) -> tuple[str, str] | None:
    """(backend, id) of the session a registry row is bound to, or None when it has no binding of either kind.
    `execution: {backend, id}` is read first (backend `claude` with a `cc-<uuid>` id, or `opencode` with a `ses_…` id);
    else the legacy `opencode_session_id` (a `cc-…` id written there by hand still reads as Claude). A present but unreadable `execution`, or one that contradicts a non-null
    `opencode_session_id`, is ('invalid', its text): bound to something no script can read, so it is never a reservation
    and never live (oc_launch refuses it as out of sync)."""
    legacy = row.get("opencode_session_id")
    legacy = None if legacy in _UNSET else str(legacy).strip()
    ex = row.get("execution")
    if ex in _UNSET:
        if legacy and _CC_ID.fullmatch(legacy):
            return "claude", legacy        # a cc- id hand-written into the legacy key is still a Claude session
        return ("opencode", legacy) if legacy else None
    if isinstance(ex, dict):
        backend = str(ex.get("backend") or "").strip().lower()
        sid = str(ex.get("id") or "").strip()
        ok = ((backend == "claude" and _CC_ID.fullmatch(sid)) or (backend == "opencode" and sid.startswith("ses_")))
        if ok and (legacy is None or legacy == sid):
            return backend, sid
    return "invalid", json.dumps(ex, ensure_ascii=False, default=str)


def row_session_id(row: dict) -> str | None:
    """The id a registry row is bound to (`ses_…` or `cc-…`), whatever runs it; None when unbound or unreadable."""
    b = row_binding(row)
    return b[1] if b and b[0] != "invalid" else None


def row_backend(row: dict) -> str | None:
    """'opencode' · 'claude' · 'invalid' for a bound row; None for an unbound one."""
    b = row_binding(row)
    return b[0] if b else None


def is_reservation(row: dict) -> bool:
    """A RESERVATION ([ap:O2QNOF] §6): no binding of either kind AND state `registered` (a legacy row whose id is
    simply unknown is `stopped`, never a reservation)."""
    return row_binding(row) is None and str(row.get("state") or "registered").strip().lower() == "registered"
