"""Prove the Tier-2 trust boundary against a live server (AC-D5).

``core/signals.py::assign_tier`` decides a call's tier from exactly one
input: a constant-time comparison of the caller's ``adapter_token`` against
the server's own ``MAGICITE_HOOK_TOKEN``. This script asserts that
end-to-end over real MCP rather than in a unit test, because the claim that
matters to an operator is "a client cannot talk its way into Tier 2", and
that claim is about the deployed server, not about a function.

Four cases, two servers:

  server WITH a token   + the matching secret        -> tier 2
  server WITH a token   + a wrong secret             -> tier 1
  server WITH a token   + the string "hook_verified" -> tier 1
  server WITHOUT a token + the matching secret       -> tier 1

    uv run python scripts/dogfood_tier_probe.py
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dogfood_session import Server  # noqa: E402

REAL_TOKEN = "dogfood-probe-" + uuid.uuid4().hex


class TokenServer(Server):
    """A Server whose subprocess environment we control."""

    def __init__(self, project_root: Path, hook_token: str | None) -> None:
        env = dict(os.environ)
        env["MAGICITE_EMBEDDING_OFFLINE"] = "1"
        if hook_token:
            env["MAGICITE_HOOK_TOKEN"] = hook_token
        else:
            env.pop("MAGICITE_HOOK_TOKEN", None)
        self.proc = subprocess.Popen(
            ["uv", "run", "magicite", "serve", "--project-root", str(project_root)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            bufsize=1,
            cwd=str(project_root),
        )
        self._id = 0
        self.transcript = []


def _tier(srv: Server, skill: str, token: str | None) -> int:
    args: dict = {"skill_ids": [skill], "session_id": f"tier-probe-{uuid.uuid4().hex[:8]}"}
    if token is not None:
        args["adapter_token"] = token
    return srv.call("signal_use", args)["signal_tier"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project-root", type=Path, default=Path.cwd())
    ap.add_argument("--skill", default="magicite-route-and-signal-loop")
    args = ap.parse_args()

    failures: list[str] = []

    def check(label: str, got: int, want: int) -> None:
        ok = got == want
        print(f"  [{'ok  ' if ok else 'FAIL'}] {label:<52} tier={got} (want {want})")
        if not ok:
            failures.append(label)

    print("server configured WITH a hook token:")
    srv = TokenServer(args.project_root, REAL_TOKEN)
    try:
        srv.initialize()
        check("matching secret", _tier(srv, args.skill, REAL_TOKEN), 2)
        check("wrong secret", _tier(srv, args.skill, "not-the-secret"), 1)
        check('the literal string "hook_verified"', _tier(srv, args.skill, "hook_verified"), 1)
        check("no token at all", _tier(srv, args.skill, None), 1)
    finally:
        srv.close()

    print("\nserver configured WITHOUT a hook token:")
    srv = TokenServer(args.project_root, None)
    try:
        srv.initialize()
        check("the real secret (tier 2 unreachable)", _tier(srv, args.skill, REAL_TOKEN), 1)
    finally:
        srv.close()

    if failures:
        print(f"\n{len(failures)} tier assertion(s) failed", file=sys.stderr)
        return 1
    print("\nTier-2 boundary holds: only the server's own secret earns tier 2.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
