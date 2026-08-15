"""Drive every Magicite MCP tool against Magicite's own registry (AC-D4).

This is the dogfooding proof: it speaks real JSON-RPC over stdio to
``magicite serve`` -- the same surface an MCP host uses -- rather than
importing ``magicite.core`` and calling functions directly. If the server
cannot answer, this script fails, which is the entire point.

It exercises the full 16-tool surface in a realistic order: inspect the
registry, route a real question, load the winning body, report use and
outcome, close the session, consolidate, checkpoint, export, and then the
four approval-gated R3 proposal tools. Review mode is the default, so the
R3 calls produce proposals and mutate nothing.

    uv run python scripts/dogfood_session.py [--out transcript.json]

Exit code 0 iff every call returned a non-error result.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "2025-11-25"

#: Realistic questions a maintainer of this repository would actually ask,
#: paired with the engram that should win. Phrased as a person would type
#: them, not as keyword bags -- routing on invented keywords would test the
#: query rewriter rather than the registry.
PROBES: list[tuple[str, str]] = [
    (
        "the magicite container dies with a permission error before the handshake finishes",
        "magicite-container-privilege-boundary",
    ),
    (
        "I edited an engram file but route still gives me the old procedure",
        "magicite-rebuild-skill-index",
    ),
    (
        "is it fair for the README to say we beat plain embedding search",
        "magicite-honest-claim-scope",
    ),
    (
        "what should an agent call after route returns its candidates",
        "magicite-route-and-signal-loop",
    ),
]


class Server:
    """A line-delimited JSON-RPC client for one ``magicite serve`` process."""

    def __init__(self, project_root: Path) -> None:
        env = dict(os.environ)
        env.setdefault("MAGICITE_EMBEDDING_OFFLINE", "1")
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
        self.transcript: list[dict[str, Any]] = []

    def _send(self, method: str, params: dict | None = None, *, want_id: bool = True) -> Any:
        assert self.proc.stdin is not None and self.proc.stdout is not None
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if want_id:
            self._id += 1
            msg["id"] = self._id
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        if not want_id:
            return None
        # Skip any server-initiated notification that arrives before our reply.
        while True:
            line = self.proc.stdout.readline()
            if not line:
                stderr = self.proc.stderr.read() if self.proc.stderr else ""
                raise RuntimeError(f"server closed stdout during {method}\n{stderr}")
            resp = json.loads(line)
            if resp.get("id") == self._id:
                return resp

    def initialize(self) -> dict:
        resp = self._send(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "magicite-dogfood", "version": "1.0.0"},
            },
        )
        self._send("notifications/initialized", want_id=False)
        return resp

    def list_tools(self) -> list[str]:
        resp = self._send("tools/list", {})
        return [t["name"] for t in resp["result"]["tools"]]

    def call(self, name: str, arguments: dict) -> dict:
        resp = self._send("tools/call", {"name": name, "arguments": arguments})
        record = {"tool": name, "arguments": arguments, "response": resp}
        self.transcript.append(record)
        if "error" in resp:
            raise RuntimeError(f"{name} returned an error: {resp['error']}")
        if resp.get("result", {}).get("isError"):
            raise RuntimeError(f"{name} reported isError: {resp['result']}")
        return self.payload(resp)

    @staticmethod
    def payload(resp: dict) -> dict:
        """Unwrap the structured tool result, falling back to parsing the text block."""
        result = resp.get("result", {})
        if isinstance(result.get("structuredContent"), dict):
            return result["structuredContent"]
        for block in result.get("content", []):
            if block.get("type") == "text":
                try:
                    return json.loads(block["text"])
                except json.JSONDecodeError:
                    return {"text": block["text"]}
        return result

    def close(self) -> None:
        if self.proc.stdin and not self.proc.stdin.closed:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project-root", type=Path, default=Path.cwd())
    ap.add_argument("--out", type=Path, default=None, help="write the JSON-RPC transcript here")
    args = ap.parse_args()

    session_id = f"dogfood-{uuid.uuid4().hex[:12]}"
    srv = Server(args.project_root)
    hits = 0
    try:
        init = srv.initialize()
        server_name = init["result"]["serverInfo"]["name"]
        tools = srv.list_tools()
        print(f"handshake ok: {server_name}, {len(tools)} tools advertised")
        if len(tools) != 16:
            print(f"FAIL: expected the frozen 16-tool surface, saw {len(tools)}", file=sys.stderr)
            return 1

        print("\n-- introspect --")
        health = srv.call("introspect", {"include_health": True})
        print(json.dumps(health, indent=2)[:600])

        print("\n-- route / load / signal cycles --")
        for query, expected in PROBES:
            routed = srv.call("route", {"query": query, "k": 3, "session_id": session_id})
            names = [c.get("name") for c in routed.get("candidates", [])]
            top = names[0] if names else None
            mark = "HIT " if top == expected else ("top3" if expected in names else "MISS")
            if expected in names:
                hits += 1
            print(f"  [{mark}] {query[:58]:<58} -> {names}")
            if not names:
                continue

            body = srv.call("load_skill_body", {"name": top})
            proc_chars = len(body.get("procedure") or "")
            has_exec = body.get("exec_blocks_present")

            skill_id = routed["candidates"][0].get("id") or top
            srv.call("signal_use", {"skill_ids": [skill_id], "session_id": session_id})
            # salience is confidence in the valence reading -- this is a
            # scripted probe with a known expected answer, so the read is
            # confident when it matched and hedged when it did not.
            matched = top == expected
            srv.call(
                "signal_outcome",
                {
                    "valence": 1.0 if matched else -0.5,
                    "salience": 0.9 if matched else 0.4,
                    "skill_ids": [skill_id],
                    "session_id": session_id,
                },
            )
            print(
                f"         loaded {proc_chars} chars of procedure "
                f"(exec_blocks_present={has_exec}), signalled use + outcome"
            )

        print("\n-- close the loop --")
        srv.call("session_end", {"session_id": session_id, "reason": "dogfood probe complete"})
        cons = srv.call("consolidate", {"manual_trigger": True})
        print(f"  consolidate: {json.dumps(cons)[:200]}")
        ckpt = srv.call("checkpoint", {})
        print(f"  checkpoint:  {json.dumps(ckpt)[:200]}")
        dead = srv.call("flag_dead", {"window_days": 30, "limit": 10})
        print(f"  flag_dead:   {json.dumps(dead)[:200]}")
        synced = srv.call("sync", {})
        print(f"  sync:        {json.dumps(synced)[:200]}")

        # export refuses any out_dir resolving outside project_root, so the
        # scratch directory has to live inside the tree, not in /tmp.
        with tempfile.TemporaryDirectory(dir=args.project_root) as tmp:
            # min_status accepts only "consolidated" or "promoted": SKILL.md
            # shims are a compile target for *settled* skills, so a freshly
            # authored all-nascent registry legitimately exports nothing.
            exported = srv.call("export", {"out_dir": tmp, "min_status": "consolidated"})
            written = len(list(Path(tmp).rglob("*.md")))
            print(f"  export:      {json.dumps(exported)[:160]} ({written} files on disk)")

        print("\n-- R3 proposal tools (review mode: proposals only, no mutation) --")
        target = PROBES[0][1]
        for name, params in (
            ("nucleate", {"min_support": 2}),
            ("sharpen", {"name": target, "proposed_changes": {"pitfalls": ["(x1) dogfood probe"]}}),
            ("promote", {"name": target}),
            ("archive", {"name": target, "reason": "dogfood probe, not intended for approval"}),
        ):
            out = srv.call(name, params)
            gated = out.get("requires_approval")
            print(f"  {name:<10} requires_approval={gated} {json.dumps(out)[:140]}")

        print(f"\nself-route: {hits}/{len(PROBES)} probes found their expected engram in top-3")
    finally:
        if args.out:
            args.out.write_text(json.dumps(srv.transcript, indent=2), encoding="utf-8")
            print(f"transcript written to {args.out}")
        srv.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
