---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-08-14
change_id: magicite-v1-implementation
esl_tier: full
maker: vivi
checker: kupo
plan_state: .spectra/changes/magicite-v1-implementation/plan-state.json
criteria_file: .spectra/changes/magicite-v1-implementation/acceptance-criteria.md
corpus: docs/01-vision-and-hypotheses.md .. docs/07-evaluation-and-observability.md (normative)
---

# Magicite v1 — Implementation Spec

> **Reading contract.** `docs/01–07` are normative for *behaviour*; this spec is
> normative for *construction*. Where the corpus said "details are normative,
> examples are illustrative" (docs/README.md:99), this spec turns the normative
> details into file paths, DDL, signatures, and guards. Seven corpus tensions had
> to be resolved unilaterally — every one is recorded in §9 with its rationale, and
> none reopens a decision the corpus actually made.

---

## Scope

**Intent class:** CHANGE (greenfield implementation of an already-specified design).

**In:**

- A Python 3.11+ MCP server, `magicite`, speaking **stdio** via **FastMCP**, exposing the
  **16-tool v1 surface** (§3), backed by embedded **SQLite (WAL)** and a **local embedding model**.
- The `.egr.md` v0.2 file store (docs/04), the SQLite skill graph as a **rebuildable index** (§2),
  and the `sync()` rebuild procedure that proves the invariant.
- The three-tier plasticity state split (Tier A durable node / Tier B durable edge / Tier C
  ephemeral), the Tier-0/1/2 signal-fidelity ladder, and the **Dream** consolidation worker (§4).
- The engram lifecycle FSM with approval gates, unified `register()` incl. SKILL.md
  ingestion and export (§5).
- Decay/forgetting semantics and the mechanical **P0 enforcement point** — the code paths that
  may *not* write learned state (§6).
- Test strategy and the acceptance checks Kupo runs (§7).
- Packaging: hardened Docker image (`ghcr.io/rynaro/magicite`, sibling-MCP pattern) plus
  `pip install magicite` for development (§8).

**Out (explicitly, v1):**

- Served profile: HTTP/Streamable transport, OAuth/OIDC, PostgreSQL, multi-tenancy, K8s
  (docs/02 §"v2+: Served (Deferred)").
- CRDT / multi-writer merge of learned weights (docs/06 §"Why Not CRDT-Style Merge").
- GNN representation layer (docs/README Known Gaps).
- Fine-grained per-signal-tier telemetry as a standing product KPI (docs/README Known Gaps);
  v1 logs the raw rows and computes yield **offline** in the bench tool only.
- Any generative-model call inside the server process (see CR-3, §9).
- Any R4/R5 tool: no shell, no arbitrary SQL, no URL fetch, no engram execution (docs/02 §R5 prohibition).

**Deferred (designed-for, not built):** `rollback-consolidation` tool (docs/06 names it
"hypothetical"), remote registry sync, host adapters beyond Claude Code, NDCG metric,
LLM-judge rubric provider (interface reserved, `rubric_provider=host`).

**Assumptions (with risk-if-wrong):**

| # | Assumption | Risk if wrong |
|---|---|---|
| A1 | The official MCP Python SDK's `mcp.server.fastmcp.FastMCP` is "FastMCP" for the user's stack decision (sibling `atlas-aci` already floors `mcp>=1.2.0`). | If the standalone `fastmcp>=2` package was meant, the adapter module (`src/magicite/mcp/`) is the only thing that changes — tool bodies are framework-free by construction (§1). |
| A2 | A ~130MB ONNX embedding model may be baked into the runtime image. | If image size is capped harder, `MAGICITE_EMBEDDING_PROVIDER=ollama` or `hashing` ships instead; no engine change (CR-6). |
| A3 | The registry lives on a local POSIX filesystem (flock + SQLite WAL semantics hold). | On NFS/CIFS the file lock degrades; the DB `writer_lease` row (§4) is the portable fallback and the documented mitigation. |
| A4 | Vivi implements against Python 3.12 in CI even though 3.11 is the floor. | None material; `requires-python = ">=3.11"` is tested on 3.11 and 3.12 in the matrix. |

**Complexity (`ramza-score --rubric complexity`):** 10/12 → `human_loop`
(scope 3, ambiguity 2, dependencies 2, risk 3). This is why the change is ESL tier **full** with
Kupo as an independent checker: no milestone may self-certify.

---

## Approach

**Selected hypothesis: H-C — layered core library + thin transport adapter + in-process,
lease-arbitrated single-writer worker** (`ramza-score --rubric explore` = **85/100, elite**;
alternatives and their scores in §Rejected Alternatives).

Four structural commitments follow, and everything else in this spec is downstream of them:

1. **The engine is a library; MCP is an adapter.** `magicite.core` (engine, storage, engram,
   embeddings) never imports the MCP framework. `magicite.mcp` is a ~300-line shim that binds
   16 tool contracts to `core` calls. This is docs/02's D2 verdict made structural: the served
   profile later becomes a second adapter, never a second engine.
2. **Writers are a *place*, not a rule.** All durable mutation happens inside a `WriterExecutor`
   holding a lease; the hot path physically cannot write durable state because its SQLite
   connection installs an **authorizer callback** that DENYs writes to every non-`eph_` table
   (§6). P0 stops being a discipline and becomes an API error.
3. **Files are the source of truth; the DB is a cache.** Tier A + Tier B live in `.egr.md`;
   the graph DB is reconstructable from the registry alone (§2). `sync()` is not a repair tool,
   it is the definition of the invariant.
4. **The surface is frozen at M0.** All 16 tools are registered with full input/output schemas
   in the walking skeleton; unimplemented bodies return a typed `not_implemented` error. Kupo can
   diff the tool manifest from milestone 0 onward, and Vivi never renegotiates a signature mid-build.

**Tool-call contract shape (all 16, uniformly):** strict pydantic models with
`model_config = ConfigDict(extra="forbid")` on input *and* output (docs/02 discipline 2:
unknown-field rejection), an explicit `session_id` parameter rather than connection state
(discipline 1), risk-class + side-effect + signal-tier metadata attached at registration
(discipline 3), and a `request_id` idempotency key on every write tool (discipline 4).

---

## 1. Repository & Package Layout

Greenfield repository, `src/` layout, `uv` + `hatchling` (identical to the sibling
`atlas-aci` MCP so the release/Trivy workflow transfers).

```
magicite/
  pyproject.toml            uv.lock            README.md   LICENSE (Apache-2.0)
  Dockerfile                Dockerfile.dev     .dockerignore
  .github/workflows/ci.yml  .github/workflows/release.yml
  docs/                     ← the normative corpus (already present)
  src/magicite/
    __init__.py             __main__.py           # click CLI: serve|sync|dream|export|tools|doctor|fetch-model
    config.py                                     # Config dataclass + TOML/env resolution
    errors.py                                     # MagiciteError taxonomy + error codes
    mcp/
      __init__.py  app.py                         # FastMCP instance + lifespan
      registry.py                                 # @magicite_tool decorator, TOOL_REGISTRY, manifest
      schemas.py                                  # pydantic in/out models (extra="forbid")
      bind_retrieval.py bind_signals.py bind_registry.py bind_lifecycle.py bind_dream.py bind_inspect.py
    core/
      __init__.py
      router.py             # seed -> PPR -> rerank -> topological plan expansion
      activation.py         # sparse PPR power iteration (numpy)
      communities.py        # CommunityDetector protocol: leiden | label_propagation
      composition.py        # needs/composes DAG closure + Kahn sort + cycle guard
      plasticity.py         # dw rules: metaplastic saturation, spacing, recency, tier weights
      decay.py              # lazy R/S decay + materialisation policy
      lifecycle.py          # status FSM: transitions + guards
      fitness.py            # docs/07 gate functions (pure, side-effect free)
      dream.py              # 7-phase consolidation orchestrator
      distill.py            # frequent-path detection -> nucleation candidates (proposal only)
      audit.py              # silent engrams, hub PageRank, coverage gaps, orphans
      approvals.py          # docs/06 approval state machine
      signals.py            # tag set/capture, co-activation, per-session caps, tier assignment
      session.py            # session registry + TTL window
    storage/
      __init__.py  db.py                          # connect(), PRAGMAs, migrations runner
      authorizer.py                               # hot-path SQLite authorizer (P0 enforcement)
      lease.py                                    # flock + writer_lease row, heartbeat
      durable.py                                  # ONLY module allowed to write non-eph_ tables
      ephemeral.py                                # eph_* CRUD (hot path)
      queries.py                                  # read models for route/introspect/flag_dead
      migrations/001_init.sql 002_...sql
    engram/
      __init__.py  model.py                       # pydantic v0.2 frontmatter models
      parser.py     writer.py                     # ruamel round-trip read / atomic write
      lint.py                                     # strict + import lint profiles
      skillmd.py                                  # SKILL.md import + export renderers
      ids.py                                      # egr_<8-hex> content-hash ids
      schema/engram-0.2.schema.json               # JSON Schema, shipped as package data
    embeddings/
      __init__.py  base.py                        # Embedder protocol
      fastembed_provider.py  ollama_provider.py  hashing_provider.py  cache.py
    obs/
      logging.py                                  # structlog -> stderr ONLY (stdio safety)
      events.py                                   # append-only event ledger writer
      kpi.py                                      # standing KPI computation (docs/07)
    eval/
      __init__.py  bench.py                       # baselines a-d runner
      ablations.py  metrics.py                    # Hit@k, MRR, Plan F1
  tests/
    unit/         (mirrors src tree 1:1)
    integration/  test_route_end_to_end.py test_dream_cycle.py test_register_import.py
    acceptance/   test_stdio_handshake.py test_tool_manifest.py test_rebuild_invariant.py
                  test_p0_hot_path.py test_dream_idempotent.py test_docker_smoke.py
    fixtures/toy-registry/            # 7 .egr.md engrams + 3 SKILL.md imports + 40 labelled queries
    fixtures/traces/                  # recorded session traces for replay tests
    conftest.py                       # tmp registry factory, frozen clock, hashing embedder
```

**Dependency floor** (`pyproject.toml`):

```toml
requires-python = ">=3.11"
dependencies = [
  "mcp>=1.12.0",          # provides mcp.server.fastmcp.FastMCP
  "pydantic>=2.6",
  "ruamel.yaml>=0.18",    # comment/order-preserving round-trip of .egr.md frontmatter
  "jsonschema>=4.21",
  "numpy>=1.26",
  "click>=8.1",
  "structlog>=24.1",
  "fastembed>=0.4",       # ONNX local embeddings, no torch
]
[project.optional-dependencies]
leiden = ["python-igraph>=0.11", "leidenalg>=0.10"]
dev    = ["pytest>=8", "pytest-asyncio>=0.23", "ruff>=0.4", "mypy>=1.10", "pytest-cov>=5"]
[project.scripts]
magicite = "magicite.__main__:cli"
```

`torch` is forbidden as a direct or transitive dependency (image-size guard, asserted in CI).
`sqlite3` is stdlib. Leiden is an **extra**, never a hard dependency (Risk R5, §Risks).

**Data layout on disk** (per docs/02 §Registry Scope, extended with the three dirs the corpus
implies but never names):

```
<project_root>/.spectra/
  engrams/           # *.egr.md  — the registry; git-committable; source of truth (Tier A+B)
  engrams/skill-graph.db          # rebuildable index (+ -wal, -shm); gitignored by default (CR-2)
  archive/           # <YYYY-MM-DD>-<name>.egr.md — never deleted (docs/03 forgetting policy)
  approvals/         # <approval_id>.json — durable pending R3 requests (docs/06)
  runtime/           # dream.lock, audit reports, consolidation logs — always gitignored
  magicite.toml      # tunables (eta, lambdas, thresholds, weights) — git-committable
```

`register()` writes a `.gitignore` into `.spectra/engrams/` on first run containing
`skill-graph.db*` — opt out with `MAGICITE_COMMIT_DB=1`.

---

## 2. Storage Schema and the Rebuildable-Index Invariant

### 2.1 Connection policy

```sql
PRAGMA journal_mode = WAL;      -- docs/02: concurrent readers + hot-path writer
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;    -- WAL + NORMAL is crash-safe for a rebuildable cache
PRAGMA busy_timeout = 5000;
```

Two connection factories, and only two (§6): `ephemeral_connection()` (authorizer-restricted,
used by the hot path) and `writer_connection()` (requires a held lease).

### 2.2 DDL — durable mirror (rebuildable from `.egr.md`)

```sql
CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE engram (
  id                  TEXT PRIMARY KEY,            -- egr_<8 hex>, content hash of identity+routing
  name                TEXT NOT NULL UNIQUE,        -- == filename stem, [a-z0-9-]{1,64}
  path                TEXT NOT NULL,               -- registry-relative
  spec_version        TEXT NOT NULL,               -- 'engram/0.2'
  version             INTEGER NOT NULL DEFAULT 1,
  origin              TEXT NOT NULL,               -- authored|imported|distilled|sharpened
  verification_status TEXT NOT NULL,               -- pending|verified|quarantined
  status              TEXT NOT NULL,               -- draft|nascent|probation|consolidated|promoted|archived
  intent_does         TEXT NOT NULL,
  intent_use_when     TEXT NOT NULL,
  intent_not_when     TEXT,
  -- Tier A mirror (authoritative copy lives in the file; refreshed by sync, written by Dream)
  storage_strength    REAL NOT NULL DEFAULT 0.0,
  s_decayed_at        TEXT NOT NULL,               -- anchor for lazy S decay
  exposure_count      INTEGER NOT NULL DEFAULT 0,  -- value at last checkpoint (see eph_bookkeeping)
  success_count       INTEGER NOT NULL DEFAULT 0,
  failure_count       INTEGER NOT NULL DEFAULT 0,
  excitability        REAL NOT NULL DEFAULT 0.05,
  last_applied        TEXT, last_checkpoint TEXT,
  embedding_model     TEXT, embedding_ref TEXT, embedding_refreshed_at TEXT,
  has_exec_blocks     INTEGER NOT NULL DEFAULT 0,  -- docs/06 injection surface flag
  identity_sha256     TEXT NOT NULL,               -- hash of identity+routing blocks; drift only (CR-8)
  content_sha256      TEXT NOT NULL,               -- whole-file digest -> dirty detection
  body_sha256         TEXT NOT NULL,               -- body-only digest -> embedding staleness
  file_mtime_ns       INTEGER NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX engram_status_idx ON engram(status, verification_status);

CREATE TABLE engram_step (
  engram_id TEXT NOT NULL REFERENCES engram(id) ON DELETE CASCADE,
  step_no INTEGER NOT NULL, text TEXT NOT NULL,
  ok_count INTEGER NOT NULL DEFAULT 0, total_count INTEGER NOT NULL DEFAULT 0,
  fault_class TEXT,                                -- e.g. GLOBAL_PINNING_BREAKS_SIBLINGS
  PRIMARY KEY (engram_id, step_no)
);

CREATE TABLE engram_trigger (
  engram_id TEXT NOT NULL REFERENCES engram(id) ON DELETE CASCADE,
  polarity TEXT NOT NULL CHECK (polarity IN ('positive','negative')),
  ord INTEGER NOT NULL, text TEXT NOT NULL,
  PRIMARY KEY (engram_id, polarity, ord)
);

CREATE TABLE edge (                                -- Tier B + declared composition edges
  src_id           TEXT NOT NULL REFERENCES engram(id) ON DELETE CASCADE,
  dst_name         TEXT NOT NULL,                  -- name, not id: dangling targets are legal
  dst_id           TEXT REFERENCES engram(id) ON DELETE SET NULL,
  type             TEXT NOT NULL CHECK (type IN
                     ('co_activation','composes','depends_on','similar_to','inhibits')),
  storage_strength REAL NOT NULL DEFAULT 0.0,      -- S_edge
  s_decayed_at     TEXT NOT NULL,
  evidence_count   INTEGER NOT NULL DEFAULT 0,
  provenance       TEXT NOT NULL CHECK (provenance IN ('declared','learned','distilled','derived')),
  first_observed   TEXT NOT NULL, last_updated TEXT,
  below_prune_runs INTEGER NOT NULL DEFAULT 0,     -- docs/03: prune after >=3 consecutive runs
  dangling         INTEGER NOT NULL DEFAULT 0,     -- 1 => inert, excluded from routing
  PRIMARY KEY (src_id, dst_name, type)
);
CREATE INDEX edge_dst_idx ON edge(dst_id, type);

CREATE TABLE context_node (                        -- docs/03 Class C row 15: renamed astroengrams
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL  -- project|toolchain|error_class
);
CREATE TABLE engram_context (
  engram_id TEXT NOT NULL REFERENCES engram(id) ON DELETE CASCADE,
  context_id TEXT NOT NULL REFERENCES context_node(id) ON DELETE CASCADE,
  weight REAL NOT NULL DEFAULT 1.0,
  PRIMARY KEY (engram_id, context_id)
);

CREATE TABLE engram_community (                    -- derived index; rebuilt, never checkpointed
  engram_id TEXT PRIMARY KEY REFERENCES engram(id) ON DELETE CASCADE,
  community_id INTEGER NOT NULL, algo TEXT NOT NULL, computed_at TEXT NOT NULL
);

CREATE TABLE engram_journal (                      -- mirror of the file's provenance journal
  engram_id TEXT NOT NULL REFERENCES engram(id) ON DELETE CASCADE,
  version INTEGER NOT NULL, ts TEXT NOT NULL, author TEXT NOT NULL,
  event TEXT NOT NULL, note TEXT, signal_tier TEXT, base_version INTEGER,
  PRIMARY KEY (engram_id, version, ts)
);
```

### 2.3 DDL — Tier C (ephemeral; lost on rebuild, by design)

```sql
CREATE TABLE eph_session (
  session_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
  ended_at TEXT, host TEXT, adapter_verified INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE eph_bookkeeping (                     -- CR-1: hot-path counters, checkpointed to Tier A
  engram_id TEXT PRIMARY KEY, exposure_delta INTEGER NOT NULL DEFAULT 0,
  last_activated TEXT, route_returns INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE eph_retrieval (                       -- R: retrieval strength, fast decay
  engram_id TEXT PRIMARY KEY, r REAL NOT NULL DEFAULT 0.0, r_decayed_at TEXT NOT NULL
);

CREATE TABLE eph_tag (                             -- synaptic tags, two-phase commit
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL, subject_kind TEXT NOT NULL CHECK (subject_kind IN ('node','edge')),
  engram_id TEXT, edge_src TEXT, edge_dst TEXT, edge_type TEXT,
  signal_tier INTEGER NOT NULL CHECK (signal_tier IN (0,1,2)),
  set_at TEXT NOT NULL, expires_at TEXT NOT NULL,
  captured_at TEXT, capture_valence REAL, capture_salience REAL, capture_weight REAL,
  capped INTEGER NOT NULL DEFAULT 0, consumed_run_id TEXT
);
CREATE INDEX eph_tag_live_idx ON eph_tag(session_id, expires_at, captured_at);

CREATE TABLE eph_candidate_edge (                  -- sub-threshold edges (Tier C)
  src_id TEXT NOT NULL, dst_id TEXT NOT NULL, type TEXT NOT NULL,
  pending_dw REAL NOT NULL DEFAULT 0.0, evidence_count INTEGER NOT NULL DEFAULT 0,
  first_observed TEXT NOT NULL, last_updated TEXT NOT NULL,
  PRIMARY KEY (src_id, dst_id, type)
);

CREATE TABLE eph_embedding (
  engram_id TEXT NOT NULL, model TEXT NOT NULL, dim INTEGER NOT NULL,
  vec BLOB NOT NULL,                               -- float32 little-endian, L2-normalised
  source_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY (engram_id, model)
);

CREATE TABLE eph_event (                           -- episodic ledger = Dream input + audit trail
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, session_id TEXT,
  tool TEXT NOT NULL, signal_tier INTEGER, engram_id TEXT,
  valence REAL, salience REAL, payload_json TEXT NOT NULL
);
CREATE INDEX eph_event_ts_idx ON eph_event(id, ts);

CREATE TABLE eph_idempotency (                     -- docs/02 discipline 4
  request_id TEXT PRIMARY KEY, tool TEXT NOT NULL, args_sha256 TEXT NOT NULL,
  response_json TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL
);
```

### 2.4 DDL — operational (not learned state, not checkpointed)

```sql
CREATE TABLE writer_lease (
  id INTEGER PRIMARY KEY CHECK (id = 1), holder TEXT NOT NULL, pid INTEGER NOT NULL,
  acquired_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL, expires_at TEXT NOT NULL
);
CREATE TABLE consolidation_run (
  id TEXT PRIMARY KEY, trigger TEXT NOT NULL,      -- manual|session_end|cli|idle
  state TEXT NOT NULL,                             -- queued|running|succeeded|failed
  phase TEXT, started_at TEXT, finished_at TEXT,
  watermark_event_id INTEGER NOT NULL DEFAULT 0, stats_json TEXT, error TEXT
);
CREATE TABLE approval (
  id TEXT PRIMARY KEY, op TEXT NOT NULL, target_name TEXT NOT NULL, payload_json TEXT NOT NULL,
  state TEXT NOT NULL,                             -- proposed|approved|rejected|executed|failed
  proposed_by TEXT NOT NULL, proposed_at TEXT NOT NULL,
  decided_by TEXT, decided_at TEXT, reason TEXT, executed_run_id TEXT
);
```

### 2.5 The `.egr.md` file-store contract

- **One file per skill**, `<registry>/<name>.egr.md`, `name` == filename stem.
- **Read** through `engram/parser.py`: split frontmatter/body on the first `---` fence pair;
  frontmatter parsed with `ruamel.yaml` round-trip (comments and key order preserved);
  body parsed into `Procedure | Pitfalls | Examples | Provenance` sections plus fenced
  `exec` blocks, which are captured as **inert text** and never evaluated.
- **Validate** against `engram/schema/engram-0.2.schema.json` (JSON Schema 2020-12) —
  the schema is the machine-readable form of docs/04's frontmatter spec and ships as package data.
- **Write** only through `engram/writer.py::atomic_write()`:
  `tmp = path.with_suffix(".egr.md.tmp")` → write → `fsync(file)` → `os.replace(tmp, path)` →
  `fsync(dir)`. Never partial, never in place.
- **Determinism:** the writer renders floats to 4 decimals, sorts the `synapses:` list by
  `(type, target)`, emits LF endings and no trailing whitespace. Two checkpoints of identical
  state produce byte-identical files (AC-021) — this is what makes git diffs reviewable.
- **Ownership:** every field of `plasticity:` and `synapses:` is Dream-only (§6). `identity`,
  `intent`, `triggers`, composition and body sections are author-owned; the server rewrites
  them only via the approval-gated `sharpen`/`register` paths.

### 2.6 `sync()` — the rebuild procedure (the invariant's proof)

```
sync():
  1. acquire writer lease (fail fast if held: {"busy": true, "holder": ...})
  2. scan <registry>/*.egr.md ; for each: parse -> validate -> lint(profile=strict)
  3. upsert engram, engram_step, engram_trigger, engram_context, engram_journal
     (Tier A values copied verbatim from the file — the file wins, always)
  4. upsert edge rows from: declared composition (needs/composes/inhibits/affinity,
     provenance='declared') and the synapses: block (provenance from the file)
  5. delete durable rows whose file vanished; report them in `removed`
  6. resolve dst_name -> dst_id; unresolved => dangling=1 (inert, docs/03 dangling targets)
  7. re-embed where eph_embedding.source_sha256 != engram.body_sha256 (or row absent)
  8. rebuild derived similar_to edges (top-m cosine kNN, provenance='derived', DB-only)
  9. recompute communities (leiden if available, else label_propagation)
 10. write schema_meta['last_sync'], release lease
 returns {synced, removed, validation_errors[], dangling[], detector, consolidation_scheduled}
```

**Invariant (AC-009/AC-010):** for any registry `Rg`,
`durable_projection(state_after(sync(Rg)))` is byte-equal whether the DB existed before or was
deleted; only Tier C (R, tags, candidate edges, cached embeddings, event ledger) is lost, which
docs/03 defines as "semantically equivalent to a period of disuse". The acceptance test deletes
`skill-graph.db*`, re-runs `sync()`, and diffs the projection.

---

## 3. MCP Surface — 16 Tools

### 3.1 Registration mechanism

```python
# magicite/mcp/registry.py
@magicite_tool(
    risk="R0",                       # R0..R3 (R4/R5 forbidden in v1)
    side_effect="none",              # none | ephemeral | filesystem | durable | batch
    signal_tier=0,                   # 0|1|2|None
    idempotent=True,
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def route(...): ...
```

The decorator (a) registers the callable on the FastMCP app, (b) records the metadata row in
`TOOL_REGISTRY`, (c) wraps the body with: strict input validation, idempotency replay,
structured event logging, error mapping. Metadata is exposed three ways: MCP tool
`annotations`, the `_meta` field when the pinned SDK supports it, and always via
`magicite tools` / the `magicite://tools` resource — so risk classes survive framework drift (Risk R6).

**Universal parameters.** `session_id: str | None` on every tool that participates in a session
(docs/02 discipline 1: no connection state). `request_id: str | None` on every write tool:
replaying a `request_id` with the same `args_sha256` returns the stored response verbatim;
replaying it with different args raises `idempotency_key_conflict` (docs/02 discipline 4).

**Error taxonomy** (`errors.py`): `not_found`, `invalid_input`, `lint_failed`,
`transition_denied`, `approval_required`, `busy`, `quarantined`, `not_implemented`,
`idempotency_key_conflict`, `path_outside_project`. Every error carries
`{code, message, hint, retryable: bool}`.

### 3.2 The inventory

Corpus provenance for the count is recorded as **CR-5** (§9): docs/05 heads 13 tools, docs/04
adds `nucleate`, docs/05 names `load_skill_body` in the progressive-disclosure section, and the
session-end trigger docs/03 requires needs a tool on hookless hosts. v1 ships exactly these 16.

| # | Tool | Risk | Side effect | Signal tier | Idempotent |
|---|---|---|---|---|---|
| 1 | `route` | R0 | none (Tier-C bookkeeping) | 0 | yes (counters are ledger-only) |
| 2 | `load_skill_body` | R0 | none | 0 | yes |
| 3 | `introspect` | R0 | none | — | yes |
| 4 | `flag_dead` | R0 | none | — | yes |
| 5 | `signal_use` | R1 | ephemeral | 1 or 2 | yes (`request_id`) |
| 6 | `signal_outcome` | R1 | ephemeral | 1 or 2 | yes (`request_id`) |
| 7 | `session_end` | R1 | ephemeral + enqueue | 1 | yes |
| 8 | `register` | R2 | filesystem + durable index | — | yes (per content sha) |
| 9 | `sync` | R2 | durable index | — | yes |
| 10 | `checkpoint` | R2 | filesystem | — | yes |
| 11 | `export` | R2 | filesystem | — | yes |
| 12 | `consolidate` | R3 | batch | — | yes (dedups to running run) |
| 13 | `nucleate` | R3 | proposal | — | yes |
| 14 | `sharpen` | R3 | proposal → durable | — | yes |
| 15 | `promote` | R3 | proposal → durable | — | yes |
| 16 | `archive` | R3 | proposal → durable | — | yes |

### 3.3 Signatures and schemas

Return types are pydantic models; `extra="forbid"` on both directions. Only fields that differ
from or refine docs/05 are annotated.

```python
# ── 1. route ── R0, Tier-0 capture path
def route(query: str,                       # 1..500 chars
          context: RouteContext | None = None,   # {project_tag?, recent_failures[]?, user_prefs[]?}
          k: int = 5,                       # 1..20
          session_id: str | None = None) -> RouteResult

class Candidate(BaseModel):                 # L1 metadata ONLY (~30-100 tokens each)
    rank: int; id: str; name: str
    intent_does: str                        # truncated to 200 chars
    intent_use_when: str                    # truncated to 200 chars
    score: float; status: str; exposure_count: int
    body_ref: str                           # project-relative path for host-side read
    signal_tier_0: bool = True
class RouteResult(BaseModel):
    candidates: list[Candidate]
    composition_plan: list[str]             # topologically ordered names
    plan_confidence: float
    instructions: str                       # the Tier-1 self-report prompt (docs/05 verbatim)
    session_id: str                         # server-minted when the caller omitted it
    registry_size: int                      # docs/07 honest-limit hint (<50 => "native matching is fine")
    unresolved_context: list[str] = []      # context strings that matched no context node
```

**Session resolution rule (one rule, every tool, docs/02 discipline 1).** An explicit
`session_id` is used verbatim and its `eph_session` row is upserted. An omitted `session_id`
causes the server to mint a fresh UUIDv4, return it, and treat the call as its own single-call
session: it can therefore never co-activate with an earlier call. Sessions expire at
`session_ttl` (3h) of inactivity; a call naming an expired session starts a new one under the
same id and records a `session_resumed_after_expiry` event. No tool ever depends on connection
state.

`route` algorithm (`core/router.py`), fully specified so no improvisation is needed:

```
1. q      = embed(query)                                     # L2-normalised
2. seeds  = top_m cosine over eph_embedding, m = clamp(5k, 25, 200), filtered to routable
            routable := status in (nascent, probation, consolidated, promoted)
                        AND verification_status = 'verified' AND NOT dangling-only
3. p      = softmax(cos_sim(seeds) / temperature=0.07)       # personalisation vector
4. a      = PPR(p, W, restart=0.15, max_iter=20, tol=1e-4)   # W row-normalised, positive edges
            W_ij = S_edge_ij * type_gain[type]               # co_activation .8, composes 1.0,
                                                             # depends_on 1.0, similar_to .6
5. inhibition: for every edge (j -> i, type='inhibits') with a_j > 0:
            a_i *= (1 - S_edge_ji * inhib_gain=0.7)          # docs/01 negative intent
6. score_i = w_a*a_i + w_s*cos_i + w_r*R_i + w_e*excitability_i   # .45/.30/.15/.10 (magicite.toml)
7. hub penalty: if usage_pagerank_i > p95: score_i *= (1 - hub_penalty=0.15)   # docs/03 black holes
7b. context conditioning (uses the `context` argument; docs/03 context nodes):
    project_tag      -> resolve to a context_node; engrams linked via engram_context get
                        score_i *= (1 + context_gain=0.20 * weight)
    recent_failures  -> resolve each error-class string to a context_node the same way;
                        engrams whose pitfalls declare that fault_class get the same boost
    user_prefs       -> entries of the form "-<name>" hard-exclude that engram from candidates;
                        plain names get score_i *= (1 + pref_gain=0.10)
    unresolvable context strings are ignored and echoed back in `unresolved_context[]`
8. community rerank (H-SCALE): group by engram_community, community_score = max + 0.25*mean,
   keep the top-2 communities, then take the global top-k within them
9. plan = composition.expand(winner): closure over resolved needs/composes edges,
   Kahn topological sort, cycle => break the weakest edge + warn, depth<=5, size<=8
10. plan_confidence = mean(S_edge over plan edges) * (resolved_deps / declared_deps)
11. bookkeeping (Tier C ONLY): eph_bookkeeping.exposure_delta += 1, last_activated = now,
    eph_event row (tool='route', signal_tier=0). R and S are NOT touched (Principle 1).
```

Latency budget (docs/07): embed ≤50ms + activation ≤30ms + assembly ≤20ms → **<100ms p95 at 10³ nodes**;
asserted by `tests/integration/test_route_latency.py` on a generated 1000-node registry.

```python
# ── 2. load_skill_body ── R0 (hosts without filesystem access; progressive disclosure)
def load_skill_body(name: str, level: Literal["L2","L3"] = "L2",
                    max_bytes: int = 8192) -> SkillBody
# L2 = Procedure + Pitfalls; L3 = + Examples + Provenance + exec blocks (returned inert,
# with exec_blocks_present=true and a host-executes-not-server warning). Truncation is explicit:
# {truncated: bool, next_offset: int|null}. Never returned unsolicited by route().

# ── 3. introspect ── R0
def introspect(skill_id: str | None = None, consolidation_id: str | None = None,
               include_health: bool = False) -> IntrospectResult
# skill_id  -> skill{...}, outbound_edges[], inbound_edges[], history[], silent_engram_flag,
#              tier_state{S_node, S_effective_now, R, live_tags, pending_dw}
# consolidation_id -> run{state, phase, stats, audit_report}
# neither   -> registry summary {counts by status, detector, last_sync, last_consolidation,
#              registry_size, embedding_model, autonomous_mode}
# include_health -> the docs/07 standing KPIs (fitness histogram, hub share, silent %)

# ── 4. flag_dead ── R0
def flag_dead(window_days: int = 30, limit: int = 50) -> FlagDeadResult
# candidates[]{id,name,last_routed,retrieval_strength,reason}, recommendation, silent_pct

# ── 5. signal_use ── R1, Tier-1 (Tier-2 when the adapter token matches)
def signal_use(skill_ids: list[str],                 # ids or names, 1..20
               session_id: str | None = None,
               adapter_token: str | None = None,     # host hook adapters only
               request_id: str | None = None) -> SignalUseResult
# 1. resolve + routable check; unknown id => invalid_input (never silently ignored)
# 2. tier = 2 if adapter_token == MAGICITE_HOOK_TOKEN else 1        <- assigned server-side
# 3. tag each skill: expires_at = now + session_ttl (default 3h, magicite.toml)
# 4. R nudge (Tier C): R <- min(1, R + eta_R*(1-R)), eta_R=0.15     <- Principle 1: application, not listing
# 5. co-activation: every pair of skills with live tags in this session
#    -> eph_candidate_edge(type='co_activation') upsert + edge tag
# 6. per-skill-per-session cap = 3; extra calls return capped=true and set no new tags
# returns {tagged[], co_activation_candidates[], expires_at, signal_tier, capped[], note}

# ── 6. signal_outcome ── R1, Tier-1/2
def signal_outcome(valence: float,                   # -1..1
                   salience: float | None = None,    # 0..1, default 0.5
                   skill_ids: list[str] | None = None,
                   session_id: str | None = None,
                   adapter_token: str | None = None,
                   request_id: str | None = None) -> SignalOutcomeResult
# credit set (docs/03 rule 2, behavioural tagging):
#   skill_ids given            -> exactly those (recency-weighted)
#   |valence| > theta_salience -> every live tag in the session window     (retroactive credit)
#   otherwise                  -> nothing captured; captured=0 + explanatory note
# capture_weight = salience * exp(-dt / tau_credit=1800s)     <- recency weighting
# writes: eph_tag.captured_at/valence/salience/weight + eph_event. NO weight math here.
# returns {captured, skills_credited[], signal_tier, consolidation_scheduled, note}

# ── 7. session_end ── R1
def session_end(session_id: str, reason: str | None = None,
                request_id: str | None = None) -> SessionEndResult
# closes the session, expires its tags (retained for the next Dream run), writes the trace
# summary event, and enqueues Dream when dream.on_session_end (default true) and
# now - last_run >= dream.min_interval_s (default 300); otherwise sets pending_work.
# returns {session_id, closed, tags_expired, captured_pending, dream_run_id|null, enqueued}

# ── 8. register ── R2  (unified ingestion, docs/04 FINDING-010)
def register(path: str, format: Literal["auto","egr","skill"] = "auto",
             request_id: str | None = None) -> RegisterResult
# see §5.3 for the full pipeline. returns {ingested, registered[{id,name,origin,status,
# verification_status,warnings[]}], validation_errors[], skipped_unchanged, consolidation_scheduled}

# ── 9. sync ── R2   (see §2.6)     ── 10. checkpoint ── R2  (see §4.5)
def sync(request_id: str | None = None) -> SyncResult
def checkpoint(request_id: str | None = None) -> CheckpointResult
# checkpoint() = Dream phase 7 in isolation: {checkpointed, modified_engrams[], write_ratio, timestamp}

# ── 11. export ── R2
def export(out_dir: str, min_status: Literal["consolidated","promoted"] = "consolidated",
           request_id: str | None = None) -> ExportResult
# renders skills/<name>/SKILL.md shims (stats stripped, docs/04 compile-target section);
# out_dir must resolve inside the project root else path_outside_project.

# ── 12. consolidate ── R3
def consolidate(manual_trigger: bool = False, request_id: str | None = None) -> ConsolidateResult
# enqueue-only, fast. If a run is queued/running, returns THAT consolidation_id (idempotent).
# returns {consolidation_id, enqueued, status, estimated_start, note}

# ── 13. nucleate ── R3 (proposal-only; the server never generates prose — CR-3)
def nucleate(trace_ids: list[str] | None = None, min_support: int = 5,
             request_id: str | None = None) -> NucleateResult
# returns {candidates[{proposal_id, path_names[], support, mean_valence, trace_ir,
#          draft_skeleton}], approval_ids[], note: "draft the .egr.md and call register()"}

# ── 14. sharpen ── R3     ── 15. promote ── R3     ── 16. archive ── R3
def sharpen(name: str, proposed_changes: SharpenChanges | None = None,
            request_id: str | None = None) -> SharpenResult
def promote(name: str, request_id: str | None = None) -> PromoteResult
def archive(name: str, reason: str | None = None, request_id: str | None = None) -> ArchiveResult
# All three follow the docs/06 approval machine: they create an `approval` row in state
# `proposed` and return {approval_id, state, requires_approval: true, evidence{...}} unless
# (a) autonomous mode is on, or (b) promote()'s evidence gate passes automatically
# (docs/06: promote is evidence-gated, auto when S/pass-rate/sessions clear the bar).
# Execution ALWAYS happens inside the writer executor, never inline in the tool call (§6).
```

**Tier-0 passive-inference capture path (docs/05 D3), concretely:** every tool invocation
appends an `eph_event` row with `signal_tier=0` and its arguments digest; `route` additionally
increments `eph_bookkeeping` and records the returned candidate set. Dream's replay phase mines
these Tier-0 rows for exposure, co-retrieval, implicit-negative (routed but never `signal_use`d
within the session window) and idle-gap signals. Tier-0 evidence may move **R and bookkeeping
only** — a hard rule enforced by `plasticity.apply()` refusing any `dS` with `tier == 0` (AC-014).

---

## 4. Dream Consolidation Worker

### 4.1 Trigger model

| Trigger | Path | Default |
|---|---|---|
| Explicit tool call | `consolidate(manual_trigger=?)` → enqueue | always on |
| Session end | `session_end()` → enqueue if `dream.on_session_end` and `now - last_run >= dream.min_interval_s` | on, 300s floor |
| CLI / cron / container run | `magicite dream --once` runs the same orchestrator inline | operator choice |
| Idle poll | background task, `dream.idle_poll_s` | **0 (off) in v1** |

Queue semantics: at most one `queued` and one `running` run. `consolidate()` while a run is
queued/running returns that run's id (idempotent, docs/05 "Idempotent"). `manual_trigger=true`
only reorders the queue; it never runs a second writer.

### 4.2 Single-writer locking

`storage/lease.py::WriterLease` acquires, in order:

1. `fcntl.flock(<runtime>/dream.lock, LOCK_EX | LOCK_NB)` — fast, same-host.
2. `INSERT OR REPLACE INTO writer_lease` guarded by `expires_at < now` — portable across
   containers/bind mounts where flock is unreliable (Assumption A3).

Heartbeat every 10s; TTL 60s; a lease whose `expires_at` has passed is reclaimable with a
`lease_stolen` warning event. **Every durable write asserts `lease.held_by_me()`** — the
assertion lives in `storage/durable.py`, not in the callers.

### 4.3 What each phase reads and writes, per tier

| # | Phase | Reads | Writes | Tier touched |
|---|---|---|---|---|
| 1 | Replay | `eph_event`, `eph_tag` (captured), `eph_session` since watermark | in-memory TraceIR | — |
| 2 | Potentiate | TraceIR, `edge`, `engram`, `eph_candidate_edge` | `edge.storage_strength`, `engram.storage_strength`, success/failure counts, `engram_step` stats, promotion of candidate → `edge` | A + B (DB rows) |
| 3 | Decay | `engram`, `edge`, `eph_retrieval`, `eph_tag` | decayed R (materialised), decayed S (lazy anchors), deletes expired tags/events past retention | A + B + C |
| 4 | Renormalise | all `edge` | scaled `storage_strength` (ratios preserved) | B |
| 5 | Distil | TraceIR frequent paths | `approval` rows (`op='nucleate'`) + candidates payload | — (proposals only) |
| 6 | Audit | whole graph | `runtime/audit-<run>.json`, flags on `engram` (silent/hub) | reports only |
| 7 | Checkpoint | dirty set | **`.egr.md` files** (Tier A `plasticity:` + Tier B `synapses:` + journal), `engram.last_checkpoint`, `exposure_count += eph_bookkeeping.exposure_delta` (then zeroed) | A + B (files) |

Phase 2 update rules (`core/plasticity.py`, exactly docs/03 §Key Update Rules):

```
eta_eff = eta * (1 - w / w_max)                      # metaplastic saturation
        * tier_weight[tier]                          # 0 -> 0.0 (S untouched), 1 -> 0.6, 2 -> 1.0
        * (1 - exp(-dt_since_last_update / tau_spacing))   # spacing effect
dw      = eta_eff * mean_outcome * capture_weight    # capture_weight carries recency + salience
commit if |dw| > theta_consolidate (0.01)
prune:  S_edge < theta_prune (0.10) for >=3 consecutive runs -> archive row, drop from synapses
```

Defaults (all in `magicite.toml`, all ablation-switchable): `eta=0.08`, `w_max=1.0`,
`tau_spacing=6h`, `lambda_R=0.1/day`, `lambda_S=0.01/day`, `theta_salience=0.7`,
`theta_synapse=0.35`, `theta_consolidate_status=0.6`, `floor_archived=0.2`, `epsilon_write=0.05`.

### 4.4 Run-level idempotency and reversibility

A run is a pure function of `(durable state, events in (watermark, now])`. Re-running with an
unchanged watermark and no new events writes **zero** files (AC-020). Phases 1–6 are
transactional in the DB; phase 7 is atomic per file. A crash mid-checkpoint leaves a prefix of
files updated and the run marked `failed` at phase 7 — the next run re-derives the same dirty
set and completes it, because dirtiness is computed from state, not from a queue.
Rollback stays exactly as docs/06 defines it: `git checkout` the registry (or restore from
`.spectra/archive/`) then `magicite sync`.

### 4.5 The `synapses:` checkpoint procedure

```
dirty(e) := |dS_node| > epsilon_write OR status changed OR synapse-set changed
            OR embedding refreshed OR outcome counts changed OR step stats changed
for e in dirty:
    doc  = ruamel.round_trip_load(read(e.path))         # preserves comments + key order
    doc['plasticity'] = {storage_strength, exposure_count, outcome{success,failure},
                         last_applied, excitability, last_checkpoint, status}
    syn  = [edge for edge in edges(src=e)
            if edge.provenance != 'derived'                        # kNN edges never persist
            and (edge.provenance == 'declared'
                 or (edge.storage_strength >= theta_synapse and edge.evidence_count >= 3))]
    doc['synapses'] = sorted(render(syn), key=lambda s: (s['type'], s['target']))
    body.append_journal(version=e.version, author='dream-worker', event='consolidated',
                        note=..., signal_tier=dominant_tier, base_version=e.version)
    atomic_write(e.path, doc, body)
emit metric checkpoint_write_ratio = len(dirty)/len(registry)   # docs/03 target < 0.05; warn above
```

Symmetric `co_activation` edges are written in both directions in the same run (docs/03
"Bidirectional Mirror"); Dream being the only writer is what keeps them consistent. Dangling
targets are written through verbatim and stay inert until the target registers.

---

## 5. Engram Lifecycle FSM and Unified `register()`

### 5.1 States and transitions

`status ∈ {draft, nascent, probation, consolidated, promoted, archived}` (docs/04 §Lifecycle),
orthogonal to `verification_status ∈ {pending, verified, quarantined}` (docs/06 §Trust Model).
**Routable := `status ∈ {nascent, probation, consolidated, promoted}` AND
`verification_status = 'verified'`.** `draft`, `archived` and anything `pending`/`quarantined`
are excluded from routing, always.

| From → To | Guard (`core/fitness.py`) | Trigger | Approval (default `--review`) |
|---|---|---|---|
| — → nascent | ingestion succeeded | `register`, distillation | none |
| nascent → probation | `reconstruction_ok∨n/a` ∧ `rubric ≥ 8/12` ∧ `injection_scan_clean` | Dream, `promote` | auto if guard passes, else manual |
| nascent/probation → draft | guard failed | Dream, `promote` | none (demotion is always allowed) |
| probation → consolidated | `S ≥ 0.6` ∧ `pass_rate ≥ 0.9` ∧ `sessions ≥ 3` ∧ no valence < −0.7 in last 5 | Dream, `promote` | auto if guard passes, else manual |
| consolidated → promoted | `S ≥ 0.85` ∧ `pass_rate ≥ 0.98` ∧ `distinct_sessions ≥ 10` ∧ no evidence decay | Dream, `promote` | auto if guard passes, else **explicit** manual |
| any(≠draft) → archived | `S_effective < 0.2` (auto) or operator call | Dream, `archive` | auto on decay floor; manual for explicit archive |
| archived → probation | ≥3 new successful sessions after a revival request | `promote` | manual, always |
| pending → verified | review approved (imports, distilled) | `promote`/approval | manual, always |
| any → quarantined | injection-scan hit or operator flag | `register`, operator | none (safety direction is free) |

Implementation: `core/lifecycle.py::apply(engram, to, actor, evidence) -> Transition`
raises `TransitionDenied(reason, unmet[])`. `storage/durable.py::set_status()` calls it first
and is the **only** writer of `engram.status`; nothing else assigns that column (AC-016).
Every transition appends an `engram_journal` row and, at the next checkpoint, a provenance-journal
entry in the file.

### 5.2 Approval machinery (docs/06)

`core/approvals.py` implements `proposed → approved → executed → succeeded|failed`, plus
`rejected` and re-review. Approvals are durable **outside** the rebuildable DB: each row is
mirrored to `.spectra/approvals/<id>.json` and reloaded on `sync()`. R3 tools create proposals;
the **writer executor** consumes them. `MAGICITE_AUTONOMOUS=1` (or `magicite dream --autonomous`)
auto-approves R3 proposals and records `decided_by='autonomous-mode'` — the audit trail never
loses the fact that no human looked (docs/06 §Autonomous Mode).

### 5.3 `register()` pipeline (unified; closes FINDING-010)

```
1. resolve path; must be inside project_root after symlink resolution      -> path_outside_project
2. discover: format=egr -> **/*.egr.md ; format=skill -> **/SKILL.md ; auto -> both
3. per file:
   a. native .egr.md  -> parse -> JSON-Schema validate -> lint(profile='strict')
   b. SKILL.md        -> skillmd.to_engram():
        intent.does      <- description (first sentence(s), <=200 chars)
        intent.use_when  <- "Use when:" line, else the description tail, else "general purpose"
        intent.not_when  <- "Not when:" line, else literal "unspecified — requires review"
        triggers.positive<- dedup(name tokens + description key phrases + use_when)  (target >=3)
        triggers.negative<- [] (recorded as a lint warning, not an error)
        body sections    <- heading match; unmatched body -> Procedure verbatim
        origin='imported', status='nascent', verification_status='pending'
        provenance_journal += {event:'imported', note:'migrated from SKILL.md', author:<actor>}
      then lint(profile='import')
4. lint outcome:
     strict profile  : any violation -> hard error, nothing written (docs/04 "format discipline
                       is the learning signal")
     import profile  : violations become warnings; engram lands status='draft',
                       verification_status='pending', NOT routable, listed in `warnings[]`  (CR-4)
5. injection scan (docs/06 §Injection Surfaces): over-broad triggers (matching >30% of a
   stock query probe set), exec blocks present, suspicious imperative text in pitfalls
   -> has_exec_blocks=1 and/or verification_status='quarantined'
6. write: imports are rendered to <registry>/<name>.egr.md (atomic); native files already inside
   the registry are indexed in place, native files outside are copied in
7. index (DB upsert), embed, wire declared edges, resolve dangling
8. plasticity defaults for new engrams: S=0, counts=0, excitability=0.05, last_checkpoint=now
9. idempotency: identical content_sha256 -> skipped_unchanged (no write, no version bump);
   changed content with an unchanged `version` -> warning `version_not_bumped`
```

### 5.4 `sharpen()` execution semantics

`sharpen` never edits a file inline. It creates an `approval` row (`op='sharpen'`) whose payload
is the proposed patch, and the **writer executor** applies it:

```
1. lease acquired; engram re-read from disk (the file, not the DB, is the base)
2. patch applied: proposed_changes.procedures -> named step bodies; .triggers -> appended to
   triggers.positive (deduped); .pitfalls -> appended to the Pitfalls section
3. lint(profile='strict') re-run on the patched document
      fail  -> approval state 'failed', file untouched, reason returned; nothing is half-applied
4. version += 1; provenance_journal += {event:'sharpened', author:<actor>, base_version:<old>,
   summary_of_change:<diff summary>}
5. atomic_write; re-embed (body_sha256 changed); identity_sha256 recomputed for drift, `id` unchanged (CR-8)
6. plasticity/synapses blocks are copied through byte-for-byte — sharpening is authored state,
   never learned state (§6.2 G3)
7. docs/07 sharpening quality gate: if the affected step's confidence degrades at the next
   Dream run, the run flags `sharpen_regression` and proposes a revert approval
```

`export(out_dir)` is the inverse compile target: for every engram at `min_status` or above,
render `out_dir/<name>/SKILL.md` with `name` + a `description` composed from
`does / use_when / not_when` and a stats-stripped body (docs/04 §SKILL.md as Compile Target).
Round-trip test: `register(skill) → export → register(skill)` is stable at the second import
(AC-018).

---

## 6. Decay, Forgetting, and the P0 Enforcement Point

### 6.1 Decay semantics

- **R (Tier C, fast):** `R(t) = R0 · e^(−λ_R·Δt)`, `λ_R = 0.1/day`. Evaluated **lazily** at read
  time from `r_decayed_at`; materialised in Dream phase 3. R is never written to a file.
- **S (Tier A/B, slow):** `S(t) = S0 · e^(−λ_S·Δt)`, `λ_S = 0.01/day`. Also lazy: `S_effective`
  is computed at read time; the decayed value is **materialised only when** it crosses a
  lifecycle threshold or moves more than `epsilon_write` (0.05), which is what keeps the
  checkpoint write ratio under the docs/03 5% target.
- **Excitability** (docs/03 Class C row 10, explore-vs-exploit): `excitability = e0 · e^(−exposure/κ)`,
  `e0=0.05`, `κ=20` — computed, never stored as a moving value beyond `e0`.
- **Archival:** `S_effective < floor_archived (0.2)` → Dream auto-archives: file moved to
  `.spectra/archive/<YYYY-MM-DD>-<name>.egr.md`, DB row `status='archived'`, journal entry
  appended. **Never deleted** (docs/03 §Forgetting policy). Revival re-registers from the archive.
- **Tier-C retention:** `eph_event` and consumed `eph_tag` rows older than
  `retention_days` (default 30) are deleted in phase 3; candidate edges idle >30 days are dropped.
  This is the only deletion in the system and it touches no learned durable state.

### 6.2 The P0 enforcement point — "never learn from the hot path alone"

Three mechanical guards, in increasing strength:

**G1 — SQLite authorizer (physical).** `storage/authorizer.py` installs
`connection.set_authorizer(cb)` on every hot-path connection. The callback returns
`SQLITE_DENY` for `INSERT/UPDATE/DELETE/DROP/ALTER` on any table whose name does not start with
`eph_`. `route`, `load_skill_body`, `signal_use`, `signal_outcome`, `session_end`, `introspect`
and `flag_dead` receive **only** this connection. A hot-path attempt to write `engram`, `edge`
or any operational table raises `sqlite3.DatabaseError` before touching a page (AC-013).

**G2 — Lease assertion (logical).** `storage/durable.py` is the only module that may open a
`writer_connection()`, and every public function in it starts with `assert_single_writer()`,
which fails unless the calling task holds the `WriterLease`. `engram/writer.py::atomic_write()`
carries the same assertion.

**G3 — Dream-context assertion (semantic).** `writer.write_plasticity()` and
`writer.write_synapses()` additionally call `assert_dream_context()` (a `ContextVar` set by
`core/dream.py::checkpoint_phase()` and by nothing else). The `checkpoint()` tool and
`magicite dream --once` reach learned state **through that same function** — they acquire the
lease and call phase 7, they do not open their own write path. The guard therefore constrains the
*code path*, never the trigger: an agent may ask for a checkpoint, but the bytes are still written
by Dream's checkpoint phase. Consequence, stated as the invariant Kupo checks: *learned* state
(`plasticity.*`, `synapses.*`) has exactly one writer in the codebase — Dream's checkpoint.
`register`/`sharpen`/`promote`/`archive` write **authored/administrative** state (identity,
routing, body, status) through the writer executor, and they may only set plasticity **defaults**
on brand-new engrams.

**Statically forbidden imports** (asserted by an AST test, `tests/unit/test_p0_enforcement.py`):
`magicite.mcp.bind_retrieval`, `magicite.mcp.bind_signals`, `magicite.core.router`,
`magicite.core.signals` MUST NOT import `magicite.storage.durable` or `magicite.engram.writer`.

**Tier gate.** `plasticity.apply(delta)` raises `P0Violation` when `delta.tier == 0` and
`delta.target == 'S'` — Tier-0 inferred signals may move R and bookkeeping only (docs/05 ladder).

---

## 7. Test Strategy

### 7.1 Unit surface per module

| Module | Unit tests (representative, not exhaustive) |
|---|---|
| `engram/parser.py` | frontmatter/body split, exec-block capture, malformed YAML, missing sections |
| `engram/lint.py` | strict vs import profile; ≥3 positive / ≥1 negative triggers; `not_when` present; numbered steps; append-only journal |
| `engram/writer.py` | atomic replace, byte-determinism, comment preservation, journal append, guard assertions |
| `engram/skillmd.py` | import conversion matrix, export rendering, round-trip stability |
| `storage/db.py` | migration idempotency, PRAGMA assertions, fresh-DB creation |
| `storage/authorizer.py` | DENY matrix per table × statement type |
| `storage/lease.py` | acquire/contend/expire/steal, heartbeat renewal |
| `core/activation.py` | PPR convergence, restart mass, disconnected components, inhibition sign |
| `core/composition.py` | Kahn order, cycle break, depth/size caps, dangling exclusion |
| `core/plasticity.py` | saturation ceiling, spacing damping, tier weights, Tier-0 `S` refusal |
| `core/decay.py` | lazy vs materialised equality, floor crossing |
| `core/lifecycle.py` | every transition + every denial reason |
| `core/fitness.py` | each docs/07 gate at boundary values (0.6/0.9/3, 0.85/0.98/10) |
| `core/dream.py` | per-phase stats, watermark advance, failure isolation |
| `core/approvals.py` | state machine legality, autonomous bypass recorded |
| `mcp/registry.py` | metadata completeness for all 16 tools, idempotency replay/conflict |
| `eval/metrics.py` | Hit@k, MRR, Plan F1 against hand-computed fixtures |

Coverage floor: **85%** on `core/` and `engram/`, 70% overall (CI-enforced).

### 7.2 Acceptance checks Kupo runs (the ESL `verify` bar)

```bash
uv sync --all-extras
uv run ruff check . && uv run mypy src               # AC-024
uv run pytest -q --cov=src/magicite --cov-fail-under=70
uv run pytest -q -m acceptance                       # the AC-mapped suite
uv run magicite tools | jq '.tools | length'         # == 16                       (AC-003)
uv run pytest tests/acceptance/test_stdio_handshake.py   # initialize + tools/list  (AC-001/002)
uv run pytest tests/acceptance/test_rebuild_invariant.py # rm db; sync; diff        (AC-009/010)
uv run pytest tests/acceptance/test_p0_hot_path.py       # authorizer + AST guards  (AC-013/014)
uv run pytest tests/acceptance/test_dream_idempotent.py  # second run writes 0      (AC-020/021)
docker build -t magicite:verify . && \
  uv run pytest tests/acceptance/test_docker_smoke.py    # containerised handshake  (AC-026)
```

Every acceptance test carries the `AC-xxx` id it proves in its docstring; the id set is frozen by
`ramza-freeze` and rides the ECL envelope, so Kupo can prove the checks run are the frozen set.

### 7.3 docs/07 fitness functions: v1 vs stub

| docs/07 item | v1 | Notes |
|---|---|---|
| Lifecycle gates (nascent→probation, →consolidated, →promoted) | **ships** | `core/fitness.py`, enforced by the FSM |
| Rubric assessment (≥8/12) | **ships, deterministic** | 12-point structural rubric; LLM judge deferred behind `rubric_provider=host` (CR-3) |
| Reconstruction check | **ships for distilled only** | needs induction traces; imports report `not_applicable` → manual approval |
| Sharpening quality gate | **ships (degraded)** | step-confidence non-degradation check; held-out re-test deferred with reconstruction |
| Silent-engram report (`flag_dead`) | **ships** | standing KPI, target <10% |
| Skill-fitness distribution | **ships** | `introspect(include_health=true)` histogram |
| Black-hole hub detection | **ships** | usage PageRank p95 + traffic-share KPI (<30%) |
| Hit@k / MRR / Plan F1, baselines a–d | **ships as `magicite-bench` (offline)** | `eval/bench.py`, not a server tool |
| Ablations: no-decay, no-tag-capture, no-communities | **ship as config switches** | driven from `magicite.toml` |
| Ablations: no-inhibition, no-behavioural-tagging, no-body | **stubs** | switches exist, no bench fixtures in v1 |
| NDCG | **stub** | metric function only, unwired |
| Per-tier signal yield (ρ, P per tier) | **rows logged; computed offline** | README defers fine-grained telemetry |
| Latency KPI (<100ms) | **ships** | integration test on a 1000-node synthetic registry |

---

## 8. Distribution & Packaging

**Docker (default distribution; sibling-MCP pattern verified against `ghcr.io/rynaro/crystalium`,
`atomos`, `tonberry` in this project's `.mcp.json`):**

- Multi-stage `python:3.12-slim`: `uv build --wheel` + `uv export --frozen --no-dev` in the
  builder; runtime installs `requirements.txt` then the wheel with `--no-deps`.
- Non-root `magicite` user, UID pinned **10001**; `HOME=/tmp`; `PYTHONDONTWRITEBYTECODE=1`;
  `STOPSIGNAL SIGINT` (stdio servers exit cleanly on SIGINT).
- The embedding model is **baked at build time** into `/opt/magicite/models` with
  `FASTEMBED_CACHE_PATH` pinned there and `MAGICITE_EMBEDDING_OFFLINE=1` — the container never
  reaches the network at runtime.
- `ENTRYPOINT ["magicite"]`, `CMD ["serve", "--project-root", "/project"]`, `VOLUME ["/project"]`.
- Published as `ghcr.io/rynaro/magicite`, consumed by digest, with the house hardening flags:

```json
{"command": "docker",
 "args": ["run","--rm","-i","--user","1000:1000","--label","eidolons.project=<project>",
          "-v","<project_root>:<project_root>:z","-w","<project_root>",
          "--cap-drop","ALL","--security-opt","no-new-privileges",
          "ghcr.io/rynaro/magicite@sha256:<digest>","serve","--project-root","<project_root>"]}
```

The registry mount is read-write by necessity (Dream writes `.egr.md`); the compensating control
is `--cap-drop ALL --security-opt no-new-privileges` plus the R5 prohibition — Magicite has no
tool that can execute anything.

**pip (development):** `pip install magicite` → `magicite serve --project-root .`;
`magicite fetch-model` pre-downloads the ONNX weights;
`MAGICITE_EMBEDDING_PROVIDER=hashing` gives a zero-download, deterministic embedder for CI.

**CI:** matrix (3.11, 3.12) × (lint, type, test); image build + Trivy HIGH/CRITICAL gate with the
`[tool.uv] constraint-dependencies` floor list copied from `atlas-aci`; release workflow publishes
the image by digest and the wheel to PyPI.

---

## 9. Corpus Contradictions Resolved (unilateral, per the no-clarification budget)

| ID | Tension (both sides cited) | Resolution shipped | Confidence |
|---|---|---|---|
| **CR-1** | docs/05: `route()` updates `exposure_count`, `last_activated` — but `exposure_count` lives in the Tier-A `plasticity:` block (docs/04) which docs/02–04 say **only Dream** may write. | Bookkeeping counters are **Tier-C accumulators** (`eph_bookkeeping`); the file's value is `last_checkpoint_value + delta`, and Dream folds the delta in at phase 7. Both statements become simultaneously true. | high |
| **CR-2** | docs/06 §Version Control: "`.egr.md` files **and** `skill-graph.db` reside in `.spectra/` and can be committed" vs docs/03/04: the DB is a rebuildable cache and must not be a source of truth. | Path stays where docs/02 puts it (`.spectra/engrams/skill-graph.db`); `register()` writes a `.gitignore` excluding `skill-graph.db*` by default; `MAGICITE_COMMIT_DB=1` restores the docs/06 reading. Committing a cache is opt-in, never default. | high |
| **CR-3** | docs/03 phase 5 ("draft composite engram **via local model**") and docs/07 ("rubric assessment: **LLM** scores") vs docs/02's local-first, no-service-dependency posture and the server-never-executes boundary. | **The v1 server ships no generative model.** `nucleate`/Dream phase 5 emit *proposals* (trace IR + skeleton + support stats); the host agent drafts the `.egr.md` and calls `register()`. The rubric gate is a deterministic 12-point structural rubric hitting docs/07's ≥8/12 bar; `rubric_provider=host` reserves the LLM-judge path. | high |
| **CR-4** | docs/04 `register()`: "any lint violation → **hard error**" vs docs/04 §Migration/GAP-005: "bulk import converts **all** SKILL.md files wholesale" (stock SKILL.md never has `not_when` or negative triggers, so every bulk import would hard-fail). | Two lint profiles. `strict` (native `.egr.md`) keeps the hard error. `import` (SKILL.md conversion) downgrades to warnings and lands the engram in `status='draft', verification_status='pending'` — **not routable**, visible in `warnings[]`, promotable only after a human/agent fills the gaps via `sharpen()`. Nothing is silently accepted into routing. | high |
| **CR-5** | The task brief (and docs/05's "8 tools + 8 additional tools" arithmetic) says **16 tools**; docs/05 actually heads **13**, docs/04 adds `nucleate` (14), and docs/05 names `load_skill_body` only in prose (15). | v1 ships exactly **16** = the 14 unified inventory tools + `load_skill_body` (docs/05 §Progressive Disclosure) + `session_end` (docs/03's "Session ends → episodic trace logged to Dream input", which needs a tool on hookless hosts and is the brief's on-session-end Dream trigger). The 8+8 figure is pre-deduplication and is not a 16th and 17th tool. | high |
| **CR-6** | docs/02 names **Ollama** for embeddings; the user's stack decision says "local embedding model" and the distribution decision requires an offline-capable container. | `Embedder` protocol with three providers: `fastembed` (default, ONNX `BAAI/bge-small-en-v1.5`, baked into the image), `ollama` (opt-in, preserves the docs/02 reading), `hashing` (deterministic, offline, tests only). `embedding.model`/`ref` in the file records whichever produced the vector, so docs/04's `bge-m3` example remains legal (README: examples are illustrative). | high |
| **CR-8** | docs/04 defines `id` as the "content-hash of identity+routing blocks" while `version` is "bumped on every sharpening event" — but sharpening edits triggers/intent, so the id would change on every sharpen and every `edge` referencing it (and every host-held id) would dangle. | The `id` is computed **once**, at first registration, and is thereafter an immutable primary key. The content hash of identity+routing is stored separately as `identity_sha256` and used only for drift detection and duplicate-import detection. The file's `id:` field is never recomputed. | high |
| **CR-7** | docs/06 §Interim Sharing says imports start `status=pending, verification_status=pending` (conflating the two axes) vs docs/04 §register: `status=nascent`, `verification_status=pending`. | The axes stay orthogonal (docs/06's own Trust Model says so): imports are `status ∈ {nascent, draft}` × `verification_status='pending'`. `pending` is never a lifecycle status. | high |

---

## Stories

Eight ordered work packages. Each names its file set and its acceptance-criteria ids; no package
is "done" until its ids pass. Executor hints assume Vivi at frontier tier: goals + contracts,
not keystrokes — except M0, which is deliberately over-specified so the skeleton cannot drift.

### Story M0: Walking skeleton — server boots, register → route → introspect end-to-end

As an agent host, I want a Magicite server that boots over stdio and can register, route and
introspect a toy registry, so that every later milestone extends a running system rather than a design.
Timebox: 3d. Risk tag: P0. Executor hint: frontier tier — **explicit** file list + schemas below.

Action plan: repo scaffold (`pyproject.toml`, `uv.lock`, ruff/mypy/pytest config, CI lint+test
job); `config.py`; `obs/logging.py` (structlog → **stderr only**; a single stdout write corrupts
the stdio channel); `storage/db.py` + `migrations/001_init.sql` (full §2 DDL, all tables);
`engram/{model,parser,ids,lint}.py` (strict profile) + the JSON Schema; `engram/writer.py`
(atomic write, guards stubbed to always-allow-writer); `embeddings/hashing_provider.py`;
`core/router.py` reduced to cosine seed + declared-edge plan expansion; `mcp/app.py` +
`mcp/registry.py` + all six `bind_*.py` registering **all 16 tools with final schemas**, bodies
raising `not_implemented` except `register`, `sync`, `route`, `introspect`, `load_skill_body`;
`tests/fixtures/toy-registry/` (7 engrams, 3 SKILL.md, 40 labelled queries).
Proves: **AC-001, AC-002, AC-003, AC-004, AC-005, AC-006**.

### Story M1: Storage completeness, engram round-trip, and the rebuild invariant

As a maintainer, I want the file store and the graph index to be provably interconvertible, so that
"the DB is a rebuildable index" is a tested property rather than a claim.
Timebox: 4d. Risk tag: P0. Executor hint: frontier — goals + the §2.6 procedure.

Action plan: complete `storage/{durable,ephemeral,queries}.py`; `engram/skillmd.py` (import +
export); import lint profile (CR-4); `sync()` steps 1–10 incl. dangling resolution;
`export()`; `.gitignore` emission (CR-2); byte-determinism of the writer; the
`durable_projection()` helper the invariant test diffs.
Proves: **AC-007, AC-008, AC-009, AC-010, AC-018, AC-021**.

### Story M2: Routing engine — embeddings, activation, communities, composition

As an agent, I want ranked candidates plus an ordered composition plan under 100ms, so that routing
beats description-only matching (H-BODY) and degrades sub-logarithmically with registry size (H-SCALE).
Timebox: 5d. Risk tag: P1. Executor hint: frontier — the §3.3 algorithm is the contract.

Action plan: `embeddings/{base,fastembed_provider,ollama_provider,cache}.py`;
`core/activation.py` (sparse PPR); `core/communities.py` (leiden extra + label-propagation
fallback, detector reported by `introspect`); inhibition pass; hub penalty;
`core/composition.py`; `load_skill_body` levels + truncation; latency test at 1000 nodes.
Proves: **AC-011, AC-012, AC-022, AC-023**.

### Story M3: Signals — the Tier 0/1/2 ladder, tags, and caps

As a host of any capability, I want learning signals to flow with or without hooks, so that plasticity
is never dead (GAP-003) and never poisonable by a caller's claim.
Timebox: 4d. Risk tag: P0. Executor hint: frontier — goals + the caps/tier table.

Action plan: `core/session.py`; `core/signals.py` (tag set/expiry, co-activation candidates,
credit-set selection, recency weighting, per-session caps); server-side tier assignment via
`adapter_token`; `session_end`; `eph_event` ledger + Tier-0 passive-inference capture;
idempotency replay/conflict; the Claude Code hook adapter documented in `docs/adapters/claude-code.md`.
Proves: **AC-013, AC-014, AC-015, AC-019**.

### Story M4: Dream worker — lease, seven phases, `synapses:` checkpoint

As a registry owner, I want durable learning to happen exactly once, offline, and reversibly, so that
Principle 0 holds and the registry stays a clean git snapshot.
Timebox: 5d. Risk tag: P0. Executor hint: frontier — §4 tables are the contract.

Action plan: `storage/lease.py`; `core/dream.py` (phases 1–7, per-phase stats, watermark);
`core/plasticity.py`; `core/decay.py`; `core/audit.py`; `checkpoint()` tool;
`consolidate()` enqueue + dedup; `magicite dream --once`; write-ratio metric; run idempotency.
Proves: **AC-016, AC-017, AC-020, AC-021, AC-025, AC-032, AC-033**.

### Story M5: Lifecycle, trust, and governance

As an operator, I want every registry mutation to be evidence-gated, approval-tracked and reversible,
so that imported and model-generated artifacts cannot enter routing unreviewed.
Timebox: 4d. Risk tag: P0. Executor hint: frontier — §5 tables are the contract.

Action plan: `core/lifecycle.py` + `core/fitness.py`; `core/approvals.py` + the
`.spectra/approvals/` mirror; `promote`/`archive`/`sharpen` as proposals; autonomous mode;
injection scan + quarantine; archival move + revival; `flag_dead`; rollback runbook in
`docs/operations.md`.
Proves: **AC-016, AC-024, AC-027, AC-028**.

### Story M6: Distillation proposals, evaluation harness, standing KPIs

As a researcher, I want the docs/07 baselines and KPIs runnable on demand, so that H-BODY/H-SCALE/
H-COMPOSE/H-LEARN are falsifiable rather than aspirational.
Timebox: 4d. Risk tag: P2. Executor hint: frontier — goals + the §7.3 ship/stub table.

Action plan: `core/distill.py` (frequent-path mining, support/consistency thresholds, proposal
payload); `eval/{bench,metrics,ablations}.py` + `magicite-bench` CLI; baselines a–d;
`introspect(include_health=true)` KPIs; three ablation switches.
Proves: **AC-029, AC-030**.

### Story M7: Packaging, hardening, host adapter docs

As an adopter, I want `docker run` and `pip install` to both work first try, so that Magicite ships
like its sibling MCPs.
Timebox: 2d. Risk tag: P1. Executor hint: mid tier — copy the `atlas-aci` Dockerfile shape.

Action plan: `Dockerfile` (+`.dev`), `.dockerignore`, model bake, non-root UID 10001, STOPSIGNAL,
release workflow + Trivy gate, `README.md` quickstart, `.mcp.json` snippet, `magicite doctor`.
Proves: **AC-026, AC-031**.

---

## Acceptance Criteria

### AC-001 (event-driven)
GIVEN a project root containing `.spectra/engrams/` with the toy registry
WHEN an MCP client sends `initialize` to `magicite serve` over stdio
THEN the server SHALL complete the handshake and report `serverInfo.name == "magicite"`
VERIFY: test: tests/acceptance/test_stdio_handshake.py::test_initialize

### AC-002 (ubiquitous)
THEN the server SHALL write no byte to stdout other than MCP protocol frames
VERIFY: test: tests/acceptance/test_stdio_handshake.py::test_stdout_is_protocol_only

### AC-003 (event-driven)
GIVEN a booted server
WHEN the client calls `tools/list`
THEN the response SHALL contain exactly the 16 tool names listed in spec §3.2
VERIFY: test: tests/acceptance/test_tool_manifest.py::test_sixteen_tools

### AC-004 (ubiquitous)
THEN every registered tool SHALL expose a non-null `risk_class`, `side_effect` and `idempotent` metadata triple
VERIFY: test: tests/unit/mcp/test_registry.py::test_metadata_complete

### AC-005 (unwanted-behavior)
GIVEN any tool input model
WHEN a client sends a payload containing a field not present in the schema
THEN the server SHALL reject the call with `invalid_input`, never ignoring the unknown field
VERIFY: test: tests/unit/mcp/test_schemas.py::test_unknown_field_rejected

### AC-006 (event-driven)
GIVEN the toy registry of 7 engrams
WHEN the client calls `register(path=".spectra/engrams")` then `route(query="rollback proton for a steam game")`
THEN the response SHALL rank `proton-ge-proton-downgrade` first
VERIFY: test: tests/acceptance/test_walking_skeleton.py::test_register_route_introspect

### AC-007 (ubiquitous)
THEN a `.egr.md` file SHALL only ever be replaced atomically via a temp file plus `os.replace`
VERIFY: test: tests/unit/engram/test_writer.py::test_atomic_replace_never_partial

### AC-008 (event-driven)
GIVEN a SKILL.md corpus lacking `not_when` and negative triggers
WHEN the client calls `register(path="skills/", format="skill")`
THEN every converted engram SHALL land with `status="draft"` and appear in `warnings[]`
VERIFY: test: tests/integration/test_register_import.py::test_import_profile_downgrades

### AC-009 (event-driven)
GIVEN a registry that has been consolidated at least once
WHEN `skill-graph.db` is deleted and `sync()` is called
THEN the durable projection of Tier A plus Tier B state SHALL be byte-identical to the pre-deletion projection
VERIFY: test: tests/acceptance/test_rebuild_invariant.py::test_durable_state_survives_rebuild

### AC-010 (state-driven)
GIVEN a freshly rebuilt index
THEN all Tier-C tables (`eph_retrieval`, `eph_tag`, `eph_candidate_edge`, `eph_embedding` excepted for recompute) SHALL be empty
VERIFY: test: tests/acceptance/test_rebuild_invariant.py::test_only_tier_c_is_lost

### AC-011 (event-driven)
GIVEN a registry of 1000 synthetic engrams
WHEN `route(query=..., k=5)` is called 100 times
THEN the p95 end-to-end latency SHALL be below 100ms
VERIFY: test: tests/integration/test_route_latency.py::test_p95_under_100ms

### AC-012 (event-driven)
GIVEN an engram declaring `needs: [steam-prefix-access]`
WHEN that engram wins routing
THEN `composition_plan` SHALL list `steam-prefix-access` before the winner
VERIFY: test: tests/integration/test_route_end_to_end.py::test_topological_plan_order

### AC-013 (unwanted-behavior)
GIVEN a hot-path tool holding an authorizer-restricted connection
WHEN it attempts any INSERT, UPDATE or DELETE on a non-`eph_` table
THEN SQLite SHALL deny the statement and the tool SHALL surface an internal error
VERIFY: test: tests/acceptance/test_p0_hot_path.py::test_authorizer_denies_durable_write

### AC-014 (unwanted-behavior)
GIVEN a Tier-0 inferred signal
WHEN `plasticity.apply()` is asked to move storage strength
THEN it SHALL raise `P0Violation`, leaving S unchanged
VERIFY: test: tests/unit/core/test_plasticity.py::test_tier0_cannot_move_S

### AC-015 (event-driven)
GIVEN a caller that supplies no `adapter_token`
WHEN it calls `signal_outcome(valence=1.0)`
THEN the recorded signal tier SHALL be 1, regardless of any tier the caller claims
VERIFY: test: tests/unit/core/test_signals.py::test_tier_assigned_server_side

### AC-016 (unwanted-behavior)
GIVEN an engram with `S=0.4` and `pass_rate=0.8`
WHEN `promote(name=...)` is called
THEN the call SHALL return `transition_denied` naming the unmet guards, leaving the status unchanged
VERIFY: test: tests/unit/core/test_lifecycle.py::test_promote_denied_below_evidence_bar

### AC-017 (event-driven)
GIVEN captured tags from three sessions with positive outcomes
WHEN a Dream run completes
THEN the affected edge's `storage_strength` SHALL increase by no more than `eta * (1 - w/w_max)` per capture
VERIFY: test: tests/unit/core/test_plasticity.py::test_metaplastic_saturation_bound

### AC-018 (event-driven)
GIVEN a consolidated engram
WHEN `export(out_dir=...)` runs and the result is re-registered
THEN the second import SHALL produce no change to the original engram's durable state
VERIFY: test: tests/integration/test_skillmd_roundtrip.py::test_export_import_stable

### AC-019 (event-driven)
GIVEN a write tool called twice with the same `request_id` and identical arguments
WHEN the second call arrives
THEN the server SHALL return the stored response without repeating the side effect
VERIFY: test: tests/unit/mcp/test_idempotency.py::test_replay_returns_cached_response

### AC-020 (event-driven)
GIVEN a completed Dream run with no new events since its watermark
WHEN `consolidate()` runs again
THEN the second run SHALL write zero `.egr.md` files
VERIFY: test: tests/acceptance/test_dream_idempotent.py::test_second_run_is_a_noop

### AC-021 (event-driven)
GIVEN identical durable state
WHEN the checkpoint procedure renders an engram twice
THEN the two files SHALL be byte-identical
VERIFY: test: tests/unit/engram/test_writer.py::test_render_is_deterministic

### AC-022 (state-driven)
GIVEN `python-igraph` and `leidenalg` are not installed
THEN community detection SHALL fall back to label propagation and report `detector="label_propagation"`
VERIFY: test: tests/unit/core/test_communities.py::test_fallback_detector

### AC-023 (event-driven)
GIVEN an engram whose `inhibits` edge targets a competitor engram
WHEN both are activated by a query
THEN the inhibited engram's score SHALL be strictly lower than without the inhibition edge
VERIFY: test: tests/unit/core/test_router.py::test_inhibition_lowers_score

### AC-024 (ubiquitous)
THEN the modules `magicite.core.router` and `magicite.core.signals` SHALL never import `magicite.storage.durable` or `magicite.engram.writer`
VERIFY: test: tests/unit/test_p0_enforcement.py::test_forbidden_imports

### AC-025 (unwanted-behavior)
GIVEN a Dream run already holding the writer lease
WHEN a second Dream run attempts to start
THEN the second attempt SHALL return `busy` without writing any durable state
VERIFY: test: tests/integration/test_dream_cycle.py::test_single_writer_enforced

### AC-026 (event-driven)
GIVEN the published container image
WHEN it is started with `--cap-drop ALL --security-opt no-new-privileges` and a mounted project
THEN the MCP handshake SHALL succeed with no network access
VERIFY: test: tests/acceptance/test_docker_smoke.py::test_offline_handshake

### AC-027 (unwanted-behavior)
GIVEN review mode (the default)
WHEN `archive(name=...)` is called
THEN the tool SHALL create an approval in state `proposed` without mutating the engram
VERIFY: test: tests/unit/core/test_approvals.py::test_r3_requires_approval_by_default

### AC-028 (event-driven)
GIVEN an imported engram carrying an exec block
WHEN `register()` ingests it
THEN the engram SHALL be recorded with `verification_status="quarantined"` and excluded from routing
VERIFY: test: tests/integration/test_register_import.py::test_exec_block_quarantined

### AC-029 (event-driven)
GIVEN the labelled toy benchmark
WHEN `magicite-bench --baseline b --baseline d` runs
THEN it SHALL emit Hit@1, Hit@3, Hit@5, MRR and Plan F1 for both baselines
VERIFY: test: tests/integration/test_bench.py::test_baseline_metrics_emitted

### AC-030 (event-driven)
GIVEN a registry where one engram absorbs more than 50% of routing traffic
WHEN the audit phase runs
THEN the audit report SHALL flag that engram as a black-hole hub
VERIFY: test: tests/unit/core/test_audit.py::test_hub_detection

### AC-031 (ubiquitous)
THEN the dependency tree SHALL contain no `torch` distribution
VERIFY: command: uv pip list --format json | jq -e 'map(.name) | index("torch") == null'

### AC-032 (event-driven)
GIVEN two `session_end` calls arriving inside the `dream.min_interval_s` window
WHEN the second call is handled
THEN the server SHALL return the already-enqueued `dream_run_id` rather than enqueuing a second run
VERIFY: test: tests/integration/test_dream_cycle.py::test_session_end_debounce

### AC-033 (state-driven)
GIVEN an engram whose effective storage strength has decayed below `floor_archived`
THEN the next Dream run SHALL move its file into `.spectra/archive/` without deleting it
VERIFY: test: tests/integration/test_dream_cycle.py::test_decay_floor_archives_never_deletes

---

## Confidence

Refined once (`ramza-gate refine`, cycle 1/3) after the critic pass: the critique found an unused
`context` argument in `route`, undefined `sharpen` execution semantics, an id-stability hole
(CR-8), an apparent `checkpoint()` vs G3 conflict, and two unlinted behaviours (session-end
debounce, decay-floor archival). All six prescriptions are applied above.

`ramza-score --rubric confidence`: **84.75 / 100 → VALIDATE** (pattern_match 80,
requirement_clarity 85, decomposition_stability 90, constraint_compliance 84).

Read the verdict literally: this spec is decision-ready, and it is **not** an AUTO_PROCEED. Two
things should be validated by a human before Vivi opens an editor, and nothing else:

1. **The eight §9 resolutions.** They were made unilaterally under a zero-clarification budget.
   CR-3 (no generative model in the server) and CR-4 (import lint profile) change what v1 *does*,
   not merely how it is built; if either is wrong, M5/M6 change shape.
2. **Assumption A1** — that "FastMCP" means `mcp.server.fastmcp.FastMCP` from the official SDK
   (which is what the sibling `atlas-aci` already depends on) rather than the standalone
   `fastmcp>=2` distribution. No network was available to verify either package's current
   metadata API, which is also why tool risk-class metadata is kept in our own registry (R6).

Dimension rationale: pattern_match 80 — packaging, CLI shape, Dockerfile, CI and test scaffolding
transfer verbatim from `atlas-aci`, but the engine itself is greenfield with no prior art in this
ecosystem. requirement_clarity 85 — docs/01–07 are unusually complete, discounted for the eight
tensions that had to be resolved here. decomposition_stability 90 — three independent
decompositions (by milestone, by tool group, by state tier) converged on the same M0–M7 ordering
with ~87% package overlap. constraint_compliance 84 — every corpus P0 maps to a named guard
(G1/G2/G3) or an acceptance criterion, discounted for the two environment-dependent assumptions above.

---

## Rejected Alternatives

- **H-A — single-process monolith** (`ramza-score --rubric explore` total **67.5**, weak):
  tools call SQLite directly, Dream is an in-process task, no core/adapter split. Wins on
  simplicity (9) and performance (8); loses on alignment (7) and maintainability (5) because
  docs/02's D2 verdict ("the engine is a library behind a deployment boundary") would exist only
  as a comment. The served profile would then be a rewrite, which is precisely what D2 forbids.
- **H-B — sidecar Dream process** (total **63**, weak): a separate `magicite-dream` daemon with a
  DB queue. Best correctness story for single-writer (8) but simplicity 4: it needs supervisord
  (or two containers) inside a distribution whose selling point is "no operational complexity"
  (docs/02 rationale 2), and it makes on-session-end triggering an IPC problem. H-C keeps the same
  guarantee with a lease and still supports `magicite dream --once` from cron for operators who want it.
- **H-D — event-sourced core** (total **61**, weak): every mutation an event; DB and files both
  projections; rebuild = replay. Highest innovation (9) and correctness (9), but alignment 6 —
  docs/03/04 make `.egr.md` the source of truth for durable state, and full replay is unbounded work
  that buys nothing the git-history rollback path (docs/06) does not already provide.
- **H-E — files-only, no SQLite** (total **42.5**, weak): in-memory index rebuilt at boot. Fails
  the corpus outright: docs/02 requires SQLite WAL for concurrent hot-path writers and Tier C has
  nowhere to live. Recorded because it is the tempting shortcut when someone reads
  "the DB is a rebuildable cache" and concludes the cache is optional.

---

## Risks

| # | Risk | Tag | Mitigation | Owner |
|---|---|---|---|---|
| R1 | **Signal poisoning / adversarial valence.** docs/07 states this is "bounded, not eliminated": a confused agent can call `signal_outcome(+1)` on a failure. | P0 | Two-phase commit (tags ≠ weights), Tier-0 barred from S, Tier-1 capped at 3 Δw/skill/session and weighted 0.6, metaplastic saturation, Dream-only S writes, adversarial-noise robustness test in the ablation suite. | Vivi (M3/M4) |
| R2 | **Tier-2 provenance spoofing.** A caller claiming `hook_verified` would get full Δw weight. | P0 | Tier is assigned server-side; Tier 2 requires `adapter_token == MAGICITE_HOOK_TOKEN` held only by the host adapter config. AC-015. | Vivi (M3) |
| R3 | **Checkpoint file churn** exceeding the docs/03 5% target, making the registry's git history unreviewable. | P1 | ε-hysteresis (0.05), lazy S materialisation, dirty-set computation from state, `checkpoint_write_ratio` metric with a CI assertion on the toy registry. | Vivi (M4) |
| R4 | **Embedding provider weight / offline behaviour** — fastembed downloading at runtime inside a hardened container. | P1 | Model baked at build time, `MAGICITE_EMBEDDING_OFFLINE=1`, `magicite fetch-model` for pip users, `hashing` provider for CI. `torch` banned (AC-031). | Vivi (M2/M7) |
| R5 | **Leiden dependency fragility** (`igraph`/`leidenalg` wheels absent on some platforms) while H-SCALE needs hierarchy. | P1 | `CommunityDetector` protocol; Leiden is an extra installed in the image; pure-Python label propagation is the automatic fallback and is reported by `introspect` (AC-022). | Vivi (M2) |
| R6 | **FastMCP metadata API drift** — risk classes are load-bearing for docs/06 governance but ride a framework field. | P1 | Metadata lives in our own `TOOL_REGISTRY`; MCP `annotations`/`_meta` are projections; `magicite tools` always prints the authoritative manifest (AC-004). | Vivi (M0) |
| R7 | **Lock semantics on non-local filesystems** (NFS/CIFS bind mounts) breaking single-writer. | P2 | flock **plus** a DB `writer_lease` row with TTL/heartbeat; `magicite doctor` warns when the registry is not on a local FS; documented in `docs/operations.md`. | Vivi (M4/M7) |
| R8 | **Distillation quality without an in-server LLM** (CR-3) — proposals may be low value. | P2 | Support/consistency thresholds (≥5 sessions, no failures), proposals are approval-gated and land `nascent`+`pending`, never routable until the rubric gate passes. Revisit only if proposal acceptance is < 30%. | Vivi (M6) |
| R9 | **Cold start / small registries** — docs/07 honest limit: under ~50 skills native SKILL.md matching is fine and Magicite is overhead. | P2 | `route()` returns `registry_size`; `magicite doctor` states the break-even honestly rather than overselling. | Vivi (M2) |
| R10 | **Hypothesis falsification** — H-BODY/H-SCALE/H-COMPOSE/H-LEARN come from UNVERIFIED-2026 sources; the engine's value proposition could fail its own benchmark. | P1 | The bench harness ships in v1 (M6) with baselines a–d so falsification is cheap and early; every claim in the README stays hypothesis-tagged until measured. | Kupo (verify) + Vivi (M6) |
| R11 | **Critic independence** — this spec's critic pass ran in the same session as the author (no second agent was available to RAMZA), recorded as `ramza-maker` vs `ramza-critic` in `plan-state.json`. | P1 | Kupo is the genuinely independent checker at ESL `verify`; the critic record is disclosed, not laundered. Treat the frozen criteria hash, not the critic record, as the tamper-evidence anchor. | Kupo |

---

## Handoff

- **Acceptance-check ids for `change.json`:** AC-001 … AC-033 (33 criteria, frozen via
  `ramza-freeze`; hash recorded in `plan-state.json` and on the ECL envelope as
  `x_ramza_acceptance_criteria`).
- **Declared execution scope** (drift watch): `src/magicite/*`, `tests/*`, `pyproject.toml`,
  `uv.lock`, `Dockerfile*`, `.dockerignore`, `.github/workflows/*`, `README.md`, `docs/adapters/*`,
  `docs/operations.md`. Anything else Vivi touches is drift and needs an amendment.
- **Milestone order is normative.** M0 before everything; M1–M3 may not be reordered (M2 and M3
  both depend on M1's storage completeness); M4 requires M3; M5 requires M4; M6/M7 are the tail.
- **What Vivi must NOT decide alone:** any change to the 16-tool surface, the tier split, the P0
  guard mechanism, or a resolution in §9. Those are spec amendments, not implementation choices.
