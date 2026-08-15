---
eidolon: ramza
kind: decision
id: A1-REVISED
version: 1.0.0
created_at: 2026-08-14
change_id: magicite-v1-implementation
supersedes: "A1 (spec.md §Scope → Assumptions table; spec.yaml stack.framework_assumption)"
status: recorded
disposition: "within declared variance — no formal spec amendment; recorded as an A1 revision"
authorizing_verdict: "FORGE (Reasoner) — 'Magicite MCP SDK framework path (A1 re-adjudication)', 2026-08-14, 2 passes, gate PASS, confidence 88%, requires_checker false"
authorizing_verdict_path: "scratchpad/framework-verdict.md (session-local; ephemeral — the decisive content is reproduced below so this record stands alone)"
evidence_path: "scratchpad/mcp2-dossier.md (session-local; executed PoC, unpacked wheels, measured diff)"
implementing_commit: 2d12134
implemented_by: vivi
recorded_by: ramza
appendonly: true
---

# A1-REVISED — the MCP framework path

> **Append-only.** This record is written once. If A1 is revised again, write `A1-REVISED-2.md`
> and mark this record superseded; do not edit it in place.

---

## 1. The decision

> **A1-REVISED (2026-08-14).** A1 originally resolved "FastMCP" to `mcp.server.fastmcp.FastMCP` on
> the official SDK's 1.x line — a reading chosen, as `spec.md` §Open-assumptions records, without
> network access to verify either candidate package's metadata API. Execution-verified evidence has
> now superseded that reading: `mcp` 2.0.0 hard-removed `mcp.server.fastmcp` with no shim, the 1.x
> line is maintenance-mode with a critical-fixes-only bar demonstrated in practice (a real
> FastMCP-construction defect closed with its ready-made fix declined) and zero releases since
> 2.0.0, and the standalone `fastmcp` 3.4.7 hard-pins `mcp<2.0` — so adopting it would *inherit* the
> 1.x line rather than escape it, at 68 packages, a D-Bus secret-service stack, and an outbound
> pypi.org call at startup that contradicts Magicite's offline-container posture. Magicite therefore
> adopts `mcp>=2.0,<3.0` and drives the low-level `mcp.server.lowlevel.Server` directly, registering
> `on_list_tools` / `on_call_tool` as public constructor kwargs; the 2.x high-level `MCPServer` was
> rejected because it reproduces FastMCP's `extra="ignore"` argument model verbatim and can satisfy
> neither the hand-authored-schema requirement nor AC-005 at the top level — the original reason
> Magicite bypassed FastMCP holds unchanged against its successor. The change is a strict improvement
> on every axis the spec cares about: protocol coverage becomes a superset (all four handshake-era
> revisions retained, plus `2026-07-28`), AC-002 moves from Magicite-discipline to framework-enforced
> fd-level stdout diversion that survives native code and subprocess children, the private-attribute
> reach-through `app._mcp_server…` that motivated Risk R6 is replaced by a supported public API, and
> the resolved dependency set shrinks by one package with an identical native/compiled set and no
> `torch`. Measured blast radius is `src/magicite/mcp/app.py` (+28/−18), six lines in one test, and
> one line in `pyproject.toml` — inside the boundary A1 itself declared — with `TOOL_REGISTRY`,
> `schemas.py`, and all six `bind_*.py` modules untouched, which is INV-1 doing precisely the job it
> was specified to do.

*(§1 is FORGE's adjudicated text, reproduced as written. Everything below is RAMZA's governance
record of how it was landed.)*

---

## 2. Why this is a revision and not a spec amendment

FORGE's governance ruling — **within declared variance; no formal spec amendment blocks work** —
rests on the spec's own handoff block, not on convenience:

| Evidence | Bearing |
|---|---|
| `spec.yaml handoff.verdict`: *"VALIDATE — human validation wanted on the eight CR resolutions … and on assumption A1 before implementation starts"* | RAMZA explicitly left A1 open pending a validation step that had not yet been performed. |
| `spec.yaml handoff.not_vivi_s_call`: `[the 16-tool surface, the Tier A/B/C split, the P0 guard mechanism, any section-9 resolution]` | **A1 is not on the frozen not-negotiable list.** |
| `spec.md` §Confidence item 2 (as emitted) | Named the exact uncertainty — no network to verify either package's metadata API — and named R6 as the compensating control. |
| A1's own declared bound: *"the adapter module (`src/magicite/mcp/`) is the only thing that changes"* | The executed change landed inside that bound. |

Revising A1 on evidence is the spec working as designed. Four confirming checks, all mechanical:

1. **Blast radius contained.** Commit `2d12134` touches `pyproject.toml`, `uv.lock`,
   `src/magicite/mcp/app.py`, and three files under `tests/acceptance/` — every one already inside
   `declared_scope`. FORGE's VC-14 tripwire (any edit to `core/`, `engram/`, `storage/`,
   `embeddings/`, or `mcp/{registry,schemas,bind_*}.py`) did **not** fire; had it fired, the ruling
   would have inverted to "formal amendment required".
2. **Frozen artifact untouched.** `acceptance-criteria.md` is byte-identical, sha256
   `7bd3d1842de8d0d55c00fc4803e52e012885a26094cc928b7801af556eada448`. It contains no framework
   reference at all — verified mechanically, not asserted — which is why no criterion text could
   have needed to change. `plan-state.json amendments[]` remains empty: the criteria have never been
   amended, and this record does not amend them.
3. **Normative content untouched.** INV-1 is framework-neutral by wording ("the MCP framework") and
   is *reinforced* by this change. INV-2/3/4, the 16-tool surface, the Tier A/B/C split, the P0
   guard mechanism, all nine VG commands, the milestone decomposition, and all eight §9 CR
   resolutions are unaffected.
4. **Direction of travel.** The change retires R6's mechanism and hardens AC-002's *enforcement
   class* without altering AC-002's *text*. A revision that only tightens the spec's own risk
   posture does not warrant a re-plan.

`[CONSTRAINT]` `spec.yaml`'s `stack:` block is annotated *"fixed upstream; not reopened by this
spec."* That annotation bound RAMZA's planning process; it is overridden here by RAMZA's own
explicit deferral of A1 to a validation step. Because `spec.yaml`'s header forbids divergence from
`spec.md`, the two were patched together in this same change-set — patching one alone would have
created exactly the divergence that rule exists to prevent.

---

## 3. What was verified before the change was accepted

FORGE set fourteen mechanically-checkable verification conditions (VC-1 … VC-14). **All fourteen
were run; thirteen passed and one is a recorded, non-silent deferral.** 84 tests pass.

| VC | Subject | Result |
|---|---|---|
| VC-1 | `initialize` across all four handshake-era revisions (`2024-11-05`, `2025-03-26`, `2025-06-18`, `2025-11-25`), each followed by a 16-tool `tools/list` | pass |
| VC-2 | No modern-era (`2026-07-28`) leakage into a handshake `initialize` reply | pass |
| VC-3 | Real-client end-to-end (`ClientSession` + `stdio_client`), register → route → introspect | pass |
| VC-4 | AC-002 at three escape levels from inside a real tool handler: `print(flush=True)`, raw `os.write(1, …)`, subprocess child | pass |
| VC-5 | fd 0 diverted to the null device has no side effect on any Magicite path | pass |
| VC-6 | AC-005 end-to-end: unknown top-level field rejected with the Magicite error envelope; all 16 advertised schemas carry `additionalProperties: false` | pass |
| VC-7 | Manifest/metadata projection intact: 16 tools, `_meta` + `annotations` complete, VG-4 command unchanged | pass |
| VC-8 | INV-1 preserved — MCP imports appear only in `src/magicite/mcp/app.py` | pass |
| VC-9 | Zero private-SDK access — `_mcp_server` and `validate_input` return no matches in `src/` | pass |
| VC-10 | Supply chain: `uv.lock` regenerated, `mcp==2.0.0` + `mcp-types==2.0.0`, AC-031 (`torch` absent) holds, native set unchanged | pass |
| VC-11 | New transitive imports (`truststore`, `opentelemetry.trace`) survive the hardened image | **deferred to M7 — no Dockerfile exists yet (M7 deliverable). Recorded as an open item against VG-9; not silently dropped.** |
| VC-12 | No outbound network at startup | pass |
| VC-13 | VG-1 … VG-5 green; `acceptance-criteria.md` still hashes to `7bd3d184…` | pass |
| VC-14 | Blast-radius tripwire — nothing outside `{src/magicite/mcp/app.py, pyproject.toml, uv.lock, tests/*}` | pass (did not fire) |

**Carry-forward:** VC-11 must be asserted under VG-9 at M7. It is the only unclosed condition and it
is the reason this record exists rather than a bare commit message.

---

## 4. What changed in the plan artifacts

Landed in the same change-set as the migration, per FORGE §8.3.

| Artifact | Change |
|---|---|
| `decisions/A1-REVISED.md` | **new** — this record |
| `spec.md` | seven located patches (§Scope bullet, the A1 assumption row, the `mcp/app.py` tree comment, the `pyproject` dependency-floor snippet, the decorator/metadata prose, the §Confidence open-assumption item, the R6 risk row) plus an errata banner on the reading contract. The original A1 text and the original §Confidence item are struck through, not deleted — superseded, not erased. |
| `spec.yaml` | `stack.framework`, `stack.framework_assumption`, the `stack:` block annotation, risk R6 (retitled, P1 → P2, mitigation extended), a new `errata:` block, and refreshed `artifacts[]` hashes |
| `spec.envelope.json` | integrity tag re-stamped to the new `spec.md` hash (§5) |
| `plan-state.json` | one appended `errata[]` entry; `amendments[]` and `criteria_sha256` untouched |
| `acceptance-criteria.md` | **untouched, byte-identical** — the tamper anchor |

### 4.1 `spec.md` hash transition

```
old sha256  92372ad65a206ede166a64ae9019347d647f54e5160ed5b8da891d412a75c77b   (88404 bytes)
new sha256  9fca1c088916cda23de4a815ce335b497c25d58cb7d1f0cc2d7a9350aac7baac   (90934 bytes)
```

`spec.yaml artifacts[0].sha256` was updated to the new value. `artifacts[1]`
(`acceptance-criteria.md`, `7bd3d184…`) was left exactly as emitted and re-verified against the file
on disk.

---

## 5. The ECL envelope re-stamp, and why it is not a laundering of tamper evidence

`spec.envelope.json` carries an ECL v2.0 integrity tag computed over `spec.md`'s bytes. Patching
`spec.md` necessarily invalidates it. Two options existed, and the choice is deliberate:

- **Leave the tag stale.** Every future `ramza-verify-emit` run would report an integrity mismatch —
  a *false* tamper alarm on a change that was authorized, adjudicated, and recorded. Tamper signals
  that are known-broken stop being read, which destroys the signal exactly when a real tamper event
  needs it.
- **Re-stamp and record.** The tag tracks the current payload, and the old → new transition, the
  reason, and the authorizing verdict are written into three places that survive the session: this
  record, `plan-state.json errata[]`, and an `x_ramza_amendment` vendor extension on the envelope
  itself.

Tamper-evidence exists to detect **unrecorded** change. A recorded, authorized amendment with a
preserved hash chain extends the audit chain rather than breaking it — which is the same stance
`ramza-freeze --amend` takes for the criteria anchor ("freeze = tamper-EVIDENCE, not immutability:
`--amend` is a first-class, hash-chained, reasoned operation"). The envelope re-stamp applies that
stance to the ECL tag.

**Mechanism used.** There is no canonical `ramza-*` amendment command for the ECL envelope.
`ramza-freeze --amend` amends only `plan-state.json criteria_sha256` — i.e. the
`acceptance-criteria.md` anchor, which must **not** move — so it was deliberately not used. The
re-stamp was therefore a targeted `jq` rewrite of `artifact.sha256`, `artifact.size_bytes` and
`integrity.value`, plus an appended `x_ramza_amendment` extension, verified afterwards by the
canonical emission gate `ramza-verify-emit --spec spec.md --envelope spec.envelope.json`, which
recomputes the digest over the payload bytes and would fail on any mismatch.

**Left as sent, on purpose.** The envelope's `objective` and `context_delta.summary` still read
"FastMCP", and `trace.ts` still carries the original emission timestamp. Those fields are the
message as it was actually sent to Vivi; retroactively editing prose in a delivered ECL message
would be rewriting history rather than amending it. The `x_ramza_amendment` extension carries the
correction and points at `spec.md` §Scope as the authoritative stack statement.
`x_ramza_acceptance_criteria` is byte-unchanged.

---

## 6. Deliberately not changed

- **`acceptance-criteria.md`** — byte-identical, sha256 unchanged. Not re-frozen, not amended.
- **AC-002's text.** Its *enforcement class* strengthened (Magicite discipline → framework-enforced
  fd-level diversion); its *statement* and VG-5's command are identical. That is an evidence
  upgrade, not a criterion change.
- **The 16-tool surface, INV-1 … INV-4, the Tier A/B/C split, the P0 guard mechanism (G1/G2/G3), all
  nine VG commands, the M0–M7 decomposition, and all eight §9 CR resolutions.**
- **The confidence score (84.75 → VALIDATE), the complexity score (10/12), the explore scores, and
  the phase walk.** Not re-run and not re-scored; this is an assumption resolution inside a declared
  boundary, not a re-plan. `spec.md` §Confidence now says so in the discharged item itself.
- **`handoff.verdict`'s wording** in `spec.yaml`. It records what RAMZA said at emission
  ("… and on assumption A1 before implementation starts"). Its A1 clause is discharged by this
  record, but the sentence stays as-sent.

---

## 7. Reversal conditions still live

Carried forward from FORGE §10. These are not closed by this record.

| ID | Condition | Consequence |
|---|---|---|
| RC-3 | By M7, `mcp` 2.0.x has received **zero** patch releases **and** ≥1 open defect on the stdio + `tools/list` + `tools/call` path affects Magicite | Re-evaluate the **pinning** strategy (exact-pin, vendor a patch, carry a local fix) — **not** the framework choice. 1.x is not a refuge: it is equally frozen with a stricter bar. |
| RC-4 | A stable `fastmcp` 4.x on the `mcp` 2.x base ships **and** Magicite later needs the served HTTP/OAuth profile (v1 `out_of_scope`) | Reopen the standalone-`fastmcp` option **for the served adapter only**, never for stdio. |
| RC-5 | Upstream publishes a 1.x EOL date, or un-freezes 1.x and backports `2026-07-28` | **No change either way.** |
| — | VC-11 unresolved at M7 | VG-9 cannot be signed off without it; assert `import truststore, opentelemetry.trace` inside the hardened image. |

RC-1 (handshake regression) and RC-2 (blast-radius tripwire) were the pre-acceptance reversal
triggers; both were tested and neither fired, so they are discharged rather than carried.

Residual flags inherited from the dossier: `[GAP-3]` (`truststore` in a hardened image → VC-11, M7)
and the untested fd-diversion edge cases (`multiprocessing` spawn, a second concurrent
`stdio_server()`), which become live only when the in-process Dream worker lands at **M4** and
should be re-checked there.

---

## 8. Open item for the human

A1 was flagged in the handoff for **human** validation, and its original resolution was made by the
orchestrator rather than by RAMZA's analysis. This record is therefore surfaced for confirmation,
not merely filed. It is a **notification, not a blocker**: the change is reversible by `git revert`,
the evidence is execution-verified, and the suite is green.

The parallel handoff item — *"human validation wanted on the eight CR resolutions (CR-3 and CR-4
change what v1 does)"* — remains **open and unaffected** by this record.
