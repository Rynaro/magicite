#!/usr/bin/env python3
"""Claude Code Tier-2 signal hook for Magicite (docs/adapters/claude-code.md).

Fires ``signal_use`` or ``signal_outcome`` against the project's Magicite
server with the host's ``adapter_token``, which is the only input that can
earn a call Tier 2 (``core/signals.py::assign_tier``, constant-time compare).

**Inert by default.** If ``MAGICITE_HOOK_TOKEN`` is unset the script exits
immediately, before importing anything expensive or touching the disk, so
wiring it into ``settings.json`` costs nothing until an operator opts in.
Tier-1 self-report and Tier-0 passive inference keep working either way --
this hook only raises fidelity, it is never required.

**What a hook can and cannot verify.** An outcome is externally observable
from the host (a command's exit code, whether the turn ended in a
correction), so ``signal_outcome`` is genuinely hook-verifiable. *Which
routed skill the agent actually applied* is not observable from the host --
only the agent knows that -- so ``signal_use`` is only sent here when the
agent recorded its choice in the correlation file below. Absent that, use
stays Tier-1 self-report by design rather than being guessed at Tier 2.

Correlation file: ``.spectra/runtime/hook-current-skill`` -- one engram id or
name per line, written by the agent (or a wrapper) after it decides which
routed skill to follow, and consumed and cleared by the outcome hook.

Usage (from settings.json):
    magicite-signal.py use
    magicite-signal.py outcome
"""

from __future__ import annotations

import os
import sys

# Fast bail: no token means Tier 2 is categorically unreachable, so there is
# nothing this hook can add. Kept above every other import on purpose --
# this is the path taken on every tool call in an unconfigured project.
_TOKEN = os.environ.get("MAGICITE_HOOK_TOKEN")
if not _TOKEN:
    sys.exit(0)

import json  # noqa: E402
import subprocess  # noqa: E402
from pathlib import Path  # noqa: E402

PROTOCOL_VERSION = "2025-11-25"
CORRELATION_FILE = Path(".spectra/runtime/hook-current-skill")
#: A hook read of valence is a heuristic, never a clean oracle, so salience
#: stays modest: salience is confidence in the *valence read*, and inflating
#: it would spray retroactive credit across every skill tagged this session.
SALIENCE = 0.5


def _project_root() -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())


def _read_skill_ids() -> list[str]:
    path = _project_root() / CORRELATION_FILE
    if not path.exists():
        return []
    ids = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return ids


def _clear_skill_ids() -> None:
    path = _project_root() / CORRELATION_FILE
    if path.exists():
        path.unlink()


def _infer_valence(payload: dict) -> float:
    """docs/05 valence inference, restricted to what a hook can actually see."""
    for key in ("exit_code", "exitCode", "status_code"):
        if key in payload:
            try:
                return 1.0 if int(payload[key]) == 0 else -1.0
            except (TypeError, ValueError):
                pass
    tool_response = payload.get("tool_response")
    if isinstance(tool_response, dict):
        if tool_response.get("isError") or tool_response.get("error"):
            return -1.0
        if tool_response:
            return 1.0
    return 0.0


def _call(root: Path, tool: str, arguments: dict) -> dict | None:
    """One short-lived stdio MCP session. Failures are swallowed: a hook must
    never break the host's turn, and a dropped signal degrades to Tier 1."""
    env = dict(os.environ)
    env.setdefault("MAGICITE_EMBEDDING_OFFLINE", "1")
    try:
        proc = subprocess.Popen(
            ["magicite", "serve", "--project-root", str(root)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            text=True,
            cwd=str(root),
        )
    except (FileNotFoundError, OSError):
        return None

    def send(msg: dict) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()

    try:
        send({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "magicite-cc-hook", "version": "1.0.0"},
            },
        })
        assert proc.stdout is not None
        proc.stdout.readline()
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        })
        while True:
            line = proc.stdout.readline()
            if not line:
                return None
            resp = json.loads(line)
            if resp.get("id") == 2:
                return resp
    except Exception:
        return None
    finally:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "outcome"
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}

    root = _project_root()
    session_id = payload.get("session_id")
    skill_ids = _read_skill_ids()

    if mode == "use":
        # Only fire when the agent told us which skill it chose. Guessing
        # here would manufacture hook-verified evidence for a skill that may
        # never have been applied.
        if not skill_ids:
            return 0
        args = {"skill_ids": skill_ids, "adapter_token": _TOKEN}
        if session_id:
            args["session_id"] = session_id
        _call(root, "signal_use", args)
        return 0

    valence = _infer_valence(payload)
    if valence == 0.0:
        return 0  # a neutral read is not evidence; docs/05 says skip the call
    args = {"valence": valence, "salience": SALIENCE, "adapter_token": _TOKEN}
    if skill_ids:
        args["skill_ids"] = skill_ids
    if session_id:
        args["session_id"] = session_id
    _call(root, "signal_outcome", args)
    _clear_skill_ids()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
