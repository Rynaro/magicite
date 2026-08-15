"""Append-only event ledger writer -- the Tier-0 passive-inference capture
path (spec §3.3: "Tier-0 passive-inference capture path (docs/05 D3),
concretely: every tool invocation appends an ``eph_event`` row with
``signal_tier=0`` and its arguments digest").

This is the mechanism that closes GAP-003 (docs/05 §Tier-1 Fallback for
Hookless Hosts / FINDING-012): plasticity is never dead on a host with no
hooks and an agent that never calls ``signal_use``/``signal_outcome``,
because every tool call -- ``route`` above all, but every other tool too --
is *itself* server-observable evidence, independent of any caller
cooperation. Dream's replay phase (``core/dream.py``, M4) mines these rows
for exposure, co-retrieval, implicit-negative (routed but never
``signal_use``d within the session window) and idle-gap signals.

Deliberately thin: it wraps ``storage.ephemeral.append_event`` rather than
introducing a second way to write ``eph_event`` (spec's own instruction for
that table: "there is exactly one place that knows the ``eph_*`` schema
shapes", ``storage/ephemeral.py``'s module docstring) -- this module's job is
just the *policy* of "call it for every tool invocation, with a digest, at
Tier 0", not a new storage shape.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from magicite.storage import ephemeral as ephemeral_mod

#: Tier-0 (inferred): guaranteed on any host, never claimed by a caller
#: (spec §3.3, docs/05 D3). Every generic per-call ledger row is stamped
#: with this tier; a tool's own *additional*, more specific bookkeeping
#: (e.g. ``route``'s candidate-set event, ``core/router.py``) may carry a
#: richer payload but is still Tier 0 for the same reason.
PASSIVE_INFERENCE_TIER = 0


def args_digest(arguments: dict[str, Any]) -> str:
    """A stable sha256 over the call's arguments -- "its arguments digest"
    per spec, not the raw payload (bounded row size; the raw arguments
    already live in whatever tool-specific event a handler writes itself,
    e.g. ``route``'s ``candidate_ids`` payload)."""
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record_tool_call(
    conn: sqlite3.Connection,
    *,
    session_id: str | None,
    tool: str,
    arguments: dict[str, Any],
) -> None:
    """Append the generic Tier-0 ledger row for one successful tool
    invocation. Called once per call from ``mcp/app.py::dispatch_call`` --
    never on an idempotency-cache replay (replaying a cached response is not
    a fresh observation of usage) and never on a raised error (an error is
    not evidence the tool was actually applied)."""
    ephemeral_mod.append_event(
        conn,
        session_id=session_id,
        tool=tool,
        signal_tier=PASSIVE_INFERENCE_TIER,
        engram_id=None,
        payload={"args_sha256": args_digest(arguments)},
    )
