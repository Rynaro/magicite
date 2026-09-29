# S11 — MCP and CLI contract integration and conformance

## Assignment

Status: ready to assign subject to dependency gates. Priority: P0 V1 release scope.
Build after: S03, S04, S07, S08, S09, S12, S13.
Merge after: S03, S04, S07, S08, S09, S12, S13.
Executor: reasoning-capable implementation agent; follow repository Vivi workflow. This packet itself is specs only.
Work bound: one independently reviewed slice; split into smaller PRs at the listed action boundaries if needed. No elapsed-day deadline; completion is evidence-gated, per user direction.

Read [shared contracts](../contracts.md), [evaluation](../evaluation.md), [release gates](../release-gates.md) and [dispatch plan](../plan.json) before coding. Shared-file ownership in C9 overrides broad owned-path patterns below.

## Story and current evidence

As an integrating host, I want stable schemas, safe body loading and predictable retry/cancellation behavior.

TOOL_REGISTRY owns schemas at mcp/registry.py:60-83; strict dispatch/idempotency at mcp/app.py:327-440 and structured/text parity at :536-540 (H). Async handler calls synchronous dispatch at :549-550 (H).

## Scope and contract

C4, C5, C8, C10. Single owner for all public binding/CLI edits. Preserve names and legacy fields via adapters where semantics remain valid; use versioned/explicit output contract for breaking shape changes. Reject unsupported requested versions before work. New trust/migration/evidence/backup operations are CLI-first; 16 MCP names remain unless amended.

Owned implementation surfaces (future work, not modified by this packet):

- `src/magicite/mcp/*`
- `src/magicite/__main__.py`
- `tests/unit/mcp/*`
- `tests/acceptance/test_stdio_handshake.py`
- `tests/acceptance/test_tool_manifest.py`
- `tests/fixtures/protocol-v1/*`

Out of scope: unrelated refactors, changing another slice's contract or thresholds, claiming unrun verification, host execution inside the runtime, publishing a release without release authority.

## Action plan

1. Generate strict input/output schemas and protocol snapshots from registry; add optional context/version selection without widening permissions.
2. Wire domain APIs from upstream slices; project new RouteDecision/Plan into supported legacy fields with documented limitations.
3. Recheck eligibility and expected content/snapshot digest on load_skill_body; deliver preserved full procedure prose safely.
4. Define cancellation before dispatch/no effect, during pure compute/abort, before durable commit/abort-or-status, after commit/return operation status; test retry idempotence.
5. Provide a standalone conformance probe and pinned real-host transcript format; run exact supported host/SDK/protocol matrix.

## Acceptance Criteria

### AC-S11-01 (event-driven)
GIVEN a decision whose body or trust policy changed
WHEN load_skill_body runs
THEN the call SHALL return stale_decision without the body
VERIFY: new tests/unit/mcp/test_v1_retrieval.py::test_stale_body_denied

### AC-S11-02 (event-driven)
GIVEN a valid v1 tool response
WHEN text and structured payloads are decoded
THEN both payloads SHALL represent identical data
VERIFY: extend tests/unit/mcp/test_dispatch_call.py::test_v1_payload_parity

### AC-S11-03 (event-driven)
GIVEN cancellation around durable commit followed by retry
WHEN the client reconnects
THEN the durable operation SHALL have at most one effect
VERIFY: new tests/acceptance/test_stdio_cancellation.py::test_commit_boundary

### AC-S11-04 (event-driven)
GIVEN each advertised host/SDK/protocol row
WHEN the installed conformance probe runs
THEN every required probe SHALL pass with an archived transcript
VERIFY: new tests/acceptance/test_v1_conformance.py and host evidence manifest

## Verification and handoff

VERIFY targets above name expected future tests/gates; they are not claims that these tests exist or passed at spec time. Extend the cited existing tests where appropriate. Run focused tests first, then repository lint/type and affected integration suites; preserve current hot-path, writer lease, rebuild and idempotency invariants. Record actual commands, dependency/model pins and outputs. Empirical/release gates require their own artifacts beyond unit tests.

Deliver: implementation branch/base SHA, dependency SHAs, owned-path diff, contract fixtures, criterion-to-test/result mapping, migration/rollback notes, and any unpassed criterion. Do not mark complete merely because tests for the happy path pass. Integration owner applies shared-file patches in dependency order. A contract change requires an explicit spec/criteria amendment before merging dependent work.

## Failure and rollback

Keep protocol adapters during the deprecation window. Unsupported features return explicit capability errors. Cancellation never undoes an already acknowledged durable operation.
