# Operations Runbook

**Status:** v1 (M5) · **Audience:** operators running `magicite serve` against a real
project registry. For design rationale see `docs/02-architecture.md` (storage),
`docs/03-learning-model.md` (decay/forgetting), `docs/06-trust-governance-lifecycle.md`
(trust/governance) and the implementation spec (`.spectra/changes/magicite-v1-implementation/spec.md`).
This document is operational detail on top of those, not a restatement of them.

---

## 1. Review mode vs. autonomous mode (AC-027)

Every R3 tool (`nucleate`, `sharpen`, `promote`, `archive`) is **approval-gated by
default** ("review mode"). Calling one of these tools:

1. Creates an `approval` row (state `proposed`), durably mirrored to
   `.spectra/approvals/<id>.json`.
2. Returns immediately with `requires_approval: true` and the proposal's `evidence`
   (for `promote`) or patch (for `sharpen`) — **the engram is not mutated**.

**Autonomous mode** (`MAGICITE_AUTONOMOUS=1`, or `[governance] autonomous = true` in
`magicite.toml`) makes a passing proposal execute immediately: the same approval row
is decided (`decided_by: "autonomous-mode"`) and executed in the same call. Two
transitions are **never** auto-executed, even under autonomous mode, because spec
§5.1 marks them "manual, always":

- `archived → probation` (revival) — always requires a separate, explicit decision.
- Any transition whose evidence bar is not cleared — `promote()` returns
  `transition_denied` outright (AC-016); there is nothing to auto-execute.

Autonomous mode is a blast-radius decision, not a default: enable it only for a
registry you trust end-to-end (e.g. a CI job re-running against a known-good
corpus), never for a registry that ingests imported/third-party content unattended.

**Known v1 gap — no "resume from approved" worker.** The frozen 16-tool surface has
no dedicated "approve" or "execute" tool. In review mode, a proposal simply sits in
`proposed` state after `.spectra/approvals/<id>.json` is written. To execute a
reviewed proposal today:

1. Inspect the JSON file, decide, and edit `state` to `"approved"` (and set
   `decided_by`/`decided_at`) by hand, or via a small operator script calling
   `core.approvals.decide()` directly against the registry's DB.
2. Re-run the *same* R3 tool call with autonomous mode temporarily enabled for that
   one operation, **or** apply the change manually (e.g. `sharpen`'s patch) and run
   `magicite sync` to re-index it.

A first-class "resume approved proposals" tool/worker is a reasonable M6+ addition;
it is out of scope for this milestone (see the M5 delta for the full reasoning).

---

## 2. Injection scan and quarantine (AC-028)

`register()` runs `engram.lint.injection_scan()` on **every** ingested engram —
native `.egr.md` and SKILL.md imports alike (SKILL.md conversion funnels through the
same `_ingest_one` ingestion path). Three triggers, each sufficient on its own:

| Signal | What it means |
|---|---|
| `has_exec_blocks` | The body contains a fenced code block (any language). Magicite never executes it — quarantine is the gate before a host ever sees it. |
| `over_broad_triggers` | The declared positive triggers match more than 30% of a stock probe-query set (`engram.lint.DEFAULT_PROBE_QUERIES`, or a caller-supplied set) — a sign of routing-hijack engineering. |
| `suspicious_pitfalls` | The Pitfalls section contains recognizable prompt-injection phrasing (e.g. "ignore previous instructions"). |

A flagged engram is recorded with `verification_status: quarantined` — this is
**independent of `status`** and excludes it from routing regardless of lifecycle
stage (`routable := status ∈ {nascent,...} AND verification_status == 'verified'`).
`promote()` re-runs the same scan on every call (spec §5.1's "any → quarantined" is
unconditional on the current status), so content edited on disk *after*
registration to add an exec block is still caught the next time anyone tries to
advance it.

**Manual review of a quarantined engram:** inspect the file directly (the exec
block/trigger set/pitfall text are all in the frontmatter and body, human-readable),
decide whether it is legitimate, and either delete/rewrite the offending content and
`register()` again, or (if the flag was a false positive, e.g. legitimately broad
triggers for a genuinely general-purpose skill) manually set
`trust.verification_status` is **not** the fix — the server never reads that field
(see §4). The only path back to `verified` is content that no longer trips the scan.

---

## 3. Trust: verification_status is server-assigned, never file-authored

`register()` computes `verification_status` itself, from `origin` +
strict-lint-cleanliness + the injection scan — **it never reads
`trust.verification_status` out of the file's own frontmatter.** An engram that
declares `verification_status: verified` in its YAML gets no credit for the claim;
the value below is what the server actually persists:

| origin | lint clean | scan clean | → verification_status |
|---|---|---|---|
| any | any | **no** | `quarantined` (always wins) |
| `imported` / `distilled` | any | yes | `pending` (manual review path only) |
| `authored` / `sharpened` | **no** | yes | `pending` (defensive; strict-lint failures don't reach this point in practice) |
| `authored` / `sharpened` | yes | yes | `verified` |

This closes an adversarial-import scenario: a planted skill that ships with a
forged `trust.verification_status: verified` line becomes routable **only** if it
is genuinely `authored`/`sharpened` origin, passes strict lint, and has no
injection-scan hit — the same bar any legitimately-authored engram has to clear, not
a bar the file's own content can assert its way past.

---

## 4. Idempotency cache: keyed by tool, real TTL

`eph_idempotency` is keyed on **`(tool, request_id)`**, not `request_id` alone.
Reusing a `request_id` across two *different* tools (e.g. calling `checkpoint()`
and `consolidate()` with the same caller-chosen id) is not treated as a replay of
either — each tool gets its own row. Rows expire `idempotency_ttl_s` (default 24h,
`magicite.toml [signals] idempotency_ttl_s`) after creation and are purged by Dream
phase 3 (`purge_retention`), same cadence as `eph_event`/`eph_tag` retention.

---

## 5. Hardened hot-path connection: PRAGMA/ATTACH denied

The hot-path (ephemeral) SQLite connection denies `PRAGMA` and `ATTACH` statements
unconditionally, in addition to the existing non-`eph_`-table write DENY matrix
(spec §6.2 G1). No tool exposes either today — this is a defense-in-depth measure,
not a response to a reachable exploit — but it removes an unrecoverable failure
mode: a `PRAGMA user_version=0` from any hot-path code path would desync the
migration runner's bookkeeping from the real schema, and the next `run_migrations()`
call would attempt to re-run `CREATE TABLE ...` against an already-populated
database and fail outright (`OperationalError: table ... already exists`),
permanently blocking server boot. As defense in depth beyond the authorizer,
`storage/migrations/001_init.sql` is now written entirely with `CREATE TABLE IF NOT
EXISTS` / `CREATE INDEX IF NOT EXISTS`, so even an out-of-band `user_version` reset
(via any future code path, not just the ones this fix denies) self-heals on the next
migration run instead of bricking boot.

---

## 6. Rollback and archive recovery

Everything in `docs/06-trust-governance-lifecycle.md` §Rollback Paths still applies
verbatim (`git checkout` + `magicite sync`, single-engram rollback, restore-from-archive
+ `magicite sync`). Two operational notes M5 adds:

- **Archived engrams survive a plain `sync()`.** An archived engram's DB row points
  at `.spectra/archive/<date>-<name>.egr.md`, outside the scanned
  `.spectra/engrams/` tree — `sync()` no longer treats that as "the file vanished"
  and deletes the row (a data-integrity fix landed alongside M5; see the M5 delta).
  Its index entry, edges and journal history persist across ordinary re-syncs.
- **Restoring a file from `.spectra/archive/` does not, by itself, restore
  routability.** The restored file still declares `status: archived` in its own
  `plasticity:` block (that is what was checkpointed when it was archived) — copying
  it back into `.spectra/engrams/` and running `sync()` re-indexes it faithfully,
  still `archived`. Actual revival is the explicit `archived → probation` FSM
  transition (spec §5.1: "≥3 new successful sessions after a revival request... manual,
  always") via `promote()`, which never auto-executes even under autonomous mode.

---

## 7. Auto-archival now requires genuine prior standing

`archive_below_floor` (Dream's own decay-floor auto-archive, AC-033) requires **both**
`(success_count + failure_count) >= 3` **and** `peak_storage_strength >=
floor_archived` — the high-water mark the engram's `storage_strength` has ever
reached. Evidence count alone does not prove an engram ever *had* standing to decay
away from: a skill honestly praised a few times whose S is still climbing *toward*
the floor (never having crossed it) is a novice, not a decayed veteran, and is never
auto-archived on that basis alone. `peak_storage_strength` is maintained
automatically (on every ingest and every Dream potentiation step) and is not a
tunable.

---

## 8. Concurrent-writer safety (R7 and beyond)

Every durable-write entry point — `register()`, `sync()`, `export()`,
`sharpen()`'s execution, `archive()`'s execution, and Dream's own `run()`/
`checkpoint()` — now acquires the **same** cross-process `WriterLease` (spec §4.2)
before writing: `fcntl.flock(<runtime>/dream.lock)` first, then an atomic, TTL-guarded
`writer_lease` DB row. A second writer (a concurrent `sync()`, a second `magicite
serve` process, or an in-flight Dream run) is turned away with a `busy` error
*before touching a single row*, rather than silently interleaving with — and
clobbering — a run that is mid-flight. This closes a real data-loss path: prior to
this fix, `register()`/`sync()`/`export()` held only the in-process logical lease
(G2), not the cross-process one Dream already used, so a concurrent `sync()` could
re-ingest a stale on-disk file over commits Dream had already made in memory but not
yet checkpointed, and Dream would then checkpoint the clobbered state and report
success.

**R7 — lock semantics degrade on non-local filesystems.** `fcntl.flock()` is
unreliable (or a no-op) on NFS/CIFS-mounted registries; this is *why* the writer
lease is two-layered (spec §4.2, Assumption A3): the DB `writer_lease` row
(TTL/heartbeat-guarded, `INSERT ... ON CONFLICT DO UPDATE ... WHERE expires_at <
:now`) is the portable fallback that still holds even when the flock layer does not.
Operational guidance:

- Prefer a local (ext4/xfs/btrfs/APFS/NTFS-via-WSL2) filesystem for
  `.spectra/` in any deployment where more than one Magicite process (or more than
  one `magicite dream --once`/CLI invocation) can plausibly run concurrently against
  the same registry.
- On NFS/CIFS, the DB-row layer is authoritative; a stale lease is reclaimable once
  its TTL (60s default, `writer_lease.expires_at`) has passed, so a crashed holder on
  a network filesystem does not permanently wedge the writer path — but throughput
  under contention will be worse than local disk (every acquisition is a real round
  trip to the DB row, not just a fast local flock).
- `magicite doctor` (M7) is the intended place to detect and warn about a
  non-local `.spectra/` mount; it is not yet implemented in v1 (see the M7 story).

---

## 9. Autonomous CLI

`magicite dream --once --autonomous` sets `cfg.autonomous = True` for that single
CLI invocation. In v1, Dream's own phases never call `nucleate`/`sharpen`/`promote`/
`archive` directly (the only auto-lifecycle-transition Dream performs is the
decay-floor archive, which has no approval gate to begin with — spec §5.1: "auto on
decay floor"), so this flag currently has no observable effect from the CLI alone;
it exists so that a future Dream phase which *does* create R3 proposals inherits the
same governance switch the MCP tools already honor, without a second flag to wire up
later.

---

## 10. Quick reference — environment variables

| Variable | Effect |
|---|---|
| `MAGICITE_AUTONOMOUS` | `1`/`true` enables autonomous mode (§1). Default off. |
| `MAGICITE_HOOK_TOKEN` | Shared secret for Tier-2 signal provenance (docs/05). Unset ⇒ Tier 2 unreachable. |
| `MAGICITE_COMMIT_DB` | `1` disables the auto-`.gitignore` for `skill-graph.db*` (CR-2). |
| `MAGICITE_EMBEDDING_PROVIDER` | `fastembed` (default) \| `ollama` \| `hashing` (deterministic, offline, tests). |
| `MAGICITE_EMBEDDING_OFFLINE` | `1` refuses any network fetch inside `fastembed_provider` at runtime. |
| `MAGICITE_PROJECT_ROOT` | Overrides the registry project root (`--project-root` CLI flag wins if both given). |

`magicite.toml` tunables relevant to this document: `[dream] idempotency_ttl_s`
(§4), `[plasticity] floor_archived` (§7, the peak-based gate compares against this
same value), `[governance] autonomous` (§1, TOML equivalent of the env var above).

---

## 11. The `magicite-bench` harness, standing KPIs, and ablations (M6)

`magicite-bench` is a separate console script (`eval/bench.py`), not one of the
16 MCP tools — it is docs/07's offline evaluation harness (AC-029), run by an
operator or CI job against a project's own registry, never called from inside a
live `serve` process.

```sh
magicite-bench --project-root . --queries .spectra/bench/queries.jsonl \
  --baseline b --baseline d
```

Emits Hit@1/3/5, MRR and Plan F1 for whichever of the four docs/07 baselines
(`a` native lexical, `b` dense embedding, `c` embedding+structural-graph, `d` the
real `route()`) are requested (`--baseline`, repeatable; default: all four).
`--sync/--no-sync` (default on) runs `sync()` first so embeddings/communities are
current. This is the harness that makes H-BODY/H-SCALE/H-COMPOSE/H-LEARN
falsifiable (risk R10) — it reports whatever the numbers actually are, including a
baseline ordering that inverts the docs/07 hypothesis (this happened on the
toy registry with the `hashing` embedder in Vivi's own M6 verification run; see the
M6 delivery report for the exact figures and the honest reading of them).

`eval/ablations.py` ships three switches (spec §7.3's "ship as config switches"
list): `no_decay` (zeroes `lambda_r_per_day`/`lambda_s_per_day` for the bench run),
`no_communities` (`cfg.ablation_no_communities = true`, also a real
`core/router.py::route()` switch — set it in `magicite.toml`'s `[routing]` table
to run a live server with community reranking disabled), and `no_tag_capture`
(simulated entirely offline inside `eval/ablations.py` — it never touches the live
two-phase-commit path, so P0 is unaffected regardless of any config).

`introspect(include_health=true)` (`obs/kpi.py`) reports the standing KPIs docs/07
names: silent-engram % (target <10%, ">20% ⇒ routing cues are systematically
poor"), skill-fitness distribution (S_node histogram), black-hole hub / top-5
traffic-share concentration, and the honest cold-start signal — `registry_size`
below ~50 skills (risk R9) is called out explicitly rather than buried.
`flag_dead(window_days, limit)` is now implemented (the last `not_implemented` tool
body in the 16-tool surface): it lists engrams never routed, or not routed within
`window_days`, ordered worst-first.

---

## 12. Session-suppression hardening (`session_end_tag_grace_s`)

`session_end(session_id)` accepts a caller-supplied `session_id` with no
authentication (spec §3.3 forbids server-side session minting) — any caller that
names a session_id, including one it does not own, can end it. Before M6, that
call could pull a not-yet-captured tag's expiry forward to "now" unconditionally,
which let a same-instant `session_end(<id>)` call (a stranger's, or a race with the
owner's own) silently make a subsequent `signal_outcome()` capture 0.

`cfg.session_end_tag_grace_s` (default `60.0`, `magicite.toml`
`[signals] session_end_tag_grace_s`) bounds this: `session_end()` may only pull a
tag's expiry forward once it is already at least this many seconds old (measured
from its immutable `set_at`). This closes the realistic same-turn
`signal_use() → signal_outcome()` race without adding caller identity — it bounds
the effect, not the principal, and does not claim to eliminate a sufficiently
patient, repeated attacker (the same "bounded, not eliminated" posture R1 already
takes elsewhere). Set to `0` to restore the pre-M6 behaviour.
