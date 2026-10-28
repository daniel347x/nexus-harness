#!/usr/bin/env python3
"""Thin operator launcher for the pane-independent native Manager seat (U04).

Composes the body-only Manager prompt for the persistent headless Claude host
(:mod:`claude_headless.manager`) and asks an already-running host to launch or resume one native
Manager session over the existing exact client contract. It never sends a message: a turn is a
separate, explicit host ``send``, so a session can be launched and left waiting for Dan's first
move.

  launch   --model M --effort E [--uuid U] [--cwd DIR] [--agent A] [--program P]
           [--socket-handle S] [--parent P] [--workflowy-url URL]
           composes the prompt and requests one launch with role ``Manager``; a fresh canonical UUID
           is allocated unless an exact one is supplied
  resume   --uuid U
           resumes the host-owned UUID with the host's exact stored prompt, choices and provenance
           read from host state; nothing is recomposed and nothing is sent
  prepare  [--out FILE] [--strict]
           composes/checks the prompt, publishes the stable readable preview and prints hashes and
           warnings (DOCTRINE DRIFT, residual OpenCode text; --strict refuses either); no host call,
           no process, no inference

Start the host first (``bash …/scripts/claude_host.sh start``); the full contract, hashes and
limitations are in ``docs/claude-headless-manager.md``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid as uuid_module

from claude_headless.manager import compose_manager_prompt, publish_manager_prompt

MANAGER_CWD = "/mnt/e/__daniel347x/__Dan_Root/__repos/manager-sessions"
"""The Manager sessions folder — the working directory the interim WSL Manager already uses."""

MANAGER_ROLE = "Manager"
"""The only host role this launcher launches or resumes: the native Manager seat."""


def exact_uuid(value: str) -> str:
    """A caller-supplied exact canonical UUID (or ``cc-UUID``); anything else is refused."""
    text = value[3:] if value.startswith("cc-") else value
    try:
        canonical = str(uuid_module.UUID(text))
    except (ValueError, AttributeError, TypeError) as error:
        raise ValueError(f"--uuid must be an exact canonical UUID or cc-UUID: {value!r}") from error
    if canonical != text:
        raise ValueError(
            f"--uuid must be exact and canonical, not a prefix or another spelling: {value!r}")
    return canonical


def _prompt_facts(prompt: dict) -> dict:
    return {
        "source_path": prompt["source_path"],
        "source_sha256": prompt["source_sha256"],
        "prompt_sha256": prompt["prompt_sha256"],
        "bytes": prompt["bytes"],
        "warnings": prompt["warnings"],
        "drifted": prompt.get("drifted") or [],
    }


def prepared_prompt(args: argparse.Namespace) -> tuple[dict, str]:
    """Compose/check/publish before any launch or restart touches the host. Doctrine drift is a loud
    warning here (the drifted override is skipped), never a refusal; --strict still refuses it."""
    prompt = compose_manager_prompt(strict=getattr(args, 'strict', False))
    for warning in prompt['warnings']:
        print(f'WARNING: {warning}', file=sys.stderr)
    return prompt, publish_manager_prompt(prompt['text'])


def drift_labels(prompt: dict) -> dict | None:
    """Launch labels naming the overrides a doctrine drift skipped, so the launch record carries them; None (no
    labels at all, as before) when nothing drifted."""
    drifted = prompt.get("drifted") or []
    return {"prompt_drift": ", ".join(drifted)[:2000]} if drifted else None


def build_launch_params(prompt_text: str, *, native_uuid: str, cwd: str, model: str, effort: str,
                        agent: str | None = None, program: str | None = None,
                        socket_handle: str | None = None, parent: str | None = None,
                        workflowy_url: str | None = None, labels: dict | None = None) -> dict:
    """The exact one host ``launch`` request for a fresh native Manager session; sends nothing."""
    params = {
        "native_uuid": native_uuid,
        "cwd": str(cwd),
        "model": model,
        "effort": effort,
        "system_prompt": prompt_text,
        "resume": False,
        "role": MANAGER_ROLE,
        "agent": agent,
        "program": program,
        "socket_handle": socket_handle,
        "parent": parent,
        "workflowy_url": workflowy_url,
    }
    if labels:
        params["labels"] = labels
    return params


# @beacon[
#   id=opc@claude_manager_stored_prompt,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Reads the host's stored prompt snapshot for a resume and verifies it against the recorded hash before any host call.,
# ]
def stored_prompt(record) -> str:
    """Read the host's stored prompt snapshot for a resume and verify it against the recorded hash.

    Only a native ``Manager`` record may be resumed from here. A missing field, an unreadable
    snapshot or a hash mismatch refuses: the host requires the original launch's exact prompt, so a
    recomposed or substituted text must never be handed to it.
    """
    if not isinstance(record, dict):
        raise ValueError("host state is not a session record")
    if record.get("backend") != "claude":
        raise ValueError("refusing to resume: not a native Claude session record")
    if record.get("role") != MANAGER_ROLE:
        raise ValueError(
            f"refusing to resume: role {record.get('role')!r} is not the native Manager seat")
    for key in ("native_uuid", "cwd", "requested_model", "requested_effort", "prompt_sha256"):
        if not record.get(key):
            raise ValueError(f"refusing to resume: the record has no {key}")
    paths = (record.get("launch") or {}).get("paths") or {}
    if paths.get("prompt"):
        prompt_path = Path(paths["prompt"])
    elif record.get("run_dir"):
        prompt_path = Path(record["run_dir"]) / "prompt.md"
    else:
        raise ValueError("refusing to resume: the record names no stored prompt snapshot")
    try:
        data = prompt_path.read_bytes()
    except OSError as error:
        raise RuntimeError(f"cannot read the stored prompt snapshot {prompt_path}: {error}") from error
    if hashlib.sha256(data).hexdigest() != record["prompt_sha256"]:
        raise RuntimeError(
            "stored prompt does not match the record's prompt hash; refusing to resume with a "
            "changed or substituted prompt")
    return data.decode("utf-8")


def build_resume_params(record: dict, prompt_text: str) -> dict:
    """The host's exact stored choices for the resume; nothing recomposed and no new defaults."""
    return {
        "native_uuid": record["native_uuid"],
        "cwd": record["cwd"],
        "model": record["requested_model"],
        "effort": record["requested_effort"],
        "system_prompt": prompt_text,
        "resume": True,
        "role": record["role"],
        "agent": record.get("agent"),
        "program": record.get("program"),
        "socket_handle": record.get("socket"),
        "parent": record.get("parent"),
    }


def host_client(args: argparse.Namespace):
    """Open the local host client; imported lazily so this module imports on any platform."""
    from claude_headless.client import Client
    return Client(args.socket_path) if args.socket_path else Client()


def run_launch(client, args: argparse.Namespace) -> dict:
    prompt, latest = prepared_prompt(args)
    native_uuid = exact_uuid(args.uuid) if args.uuid else str(uuid_module.uuid4())
    record = client.call("launch", **build_launch_params(
        prompt["text"], native_uuid=native_uuid, cwd=args.cwd or MANAGER_CWD, model=args.model,
        effort=args.effort, agent=args.agent, program=args.program,
        socket_handle=args.socket_handle, parent=args.parent, workflowy_url=args.workflowy_url,
        labels=drift_labels(prompt)))
    return {"status": "launch_requested", "sent": False, **_prompt_facts(prompt),
            "latest_prompt_path": latest, "record": record}


def run_resume(client, args: argparse.Namespace) -> dict:
    native_uuid = exact_uuid(args.uuid)
    record = client.call("state", execution_id="cc-" + native_uuid)
    prompt_text = stored_prompt(record)
    latest = publish_manager_prompt(prompt_text)
    resumed = client.call("launch", **build_resume_params(record, prompt_text))
    return {"status": "resume_requested", "sent": False,
            "prompt_sha256": record["prompt_sha256"], "latest_prompt_path": latest, "record": resumed}


def run_prepare(args: argparse.Namespace) -> dict:
    prompt, latest = prepared_prompt(args)
    data = prompt["text"].encode("utf-8")
    out = args.out
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        if os.name == "posix":
            out.chmod(0o600)
    return {"status": "prepared", **_prompt_facts(prompt), "out": str(out) if out else None,
            "latest_prompt_path": latest}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--socket-path", type=Path, default=None,
                        help="operator override for the host socket; default is the host's own")
    commands = parser.add_subparsers(dest="command", required=True)

    launch = commands.add_parser(
        "launch", help="compose the Manager prompt and request one host launch; sends nothing")
    launch.add_argument("--model", required=True,
                        help="explicit native model (requested, not proof of what is effective)")
    launch.add_argument("--effort", required=True, help="explicit native effort level")
    launch.add_argument('--strict', action='store_true', help='refuse residual OpenCode-only text')
    launch.add_argument("--uuid", default=None,
                        help="exact canonical session UUID (or cc-UUID); default allocates a fresh one")
    launch.add_argument("--cwd", default=None,
                        help=f"absolute working directory; default {MANAGER_CWD}")
    launch.add_argument("--agent", default=None, help="optional provenance: the launching agent/seat")
    launch.add_argument("--program", default=None, help="optional provenance: the program AP-ID")
    launch.add_argument("--socket-handle", default=None,
                        help="optional provenance: the seat socket handle")
    launch.add_argument("--parent", default=None, help="optional provenance: the parent seat")
    launch.add_argument("--workflowy-url", default=None,
                        help="operator Workflowy MCP endpoint override")

    resume = commands.add_parser(
        "resume", help="resume the host-owned UUID with its exact stored prompt/config/choices")
    resume.add_argument("--uuid", required=True,
                        help="the host-owned exact canonical UUID (or cc-UUID)")

    prepare = commands.add_parser(
        "prepare", help="compose/check/publish the prompt and print hashes/warnings; no host call")
    prepare.add_argument("--out", type=Path, default=None,
                         help="optional path to write the composed prompt")
    prepare.add_argument('--strict', action='store_true', help='refuse residual OpenCode-only text')
    return parser


# @beacon[
#   id=opc@claude_manager_headless_main,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Composes or resumes exactly one native Manager session through the host client; a turn stays a separate explicit send.,
# ]
def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            payload = run_prepare(args)
        else:
            client = host_client(args)
            payload = run_launch(client, args) if args.command == "launch" else run_resume(client, args)
    except KeyboardInterrupt:
        return 130
    except OSError as error:
        print(f"{type(error).__name__}: host transport failed; is the host running and its socket "
              f"reachable? ({error})", file=sys.stderr)
        return 1
    except Exception as error:  # the operator wants the exact reason, then a plain exit 1
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
