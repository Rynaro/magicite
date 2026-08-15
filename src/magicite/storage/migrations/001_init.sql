-- Magicite skill-graph.db — initial schema (v1 §2.2-2.4 of
-- .spectra/changes/magicite-v1-implementation/spec.md).
--
-- This is the durable-mirror + Tier-C + operational schema in one
-- migration; the DB is a rebuildable index (INV-3) so there is no
-- meaningful "v0 -> v1" data migration path to preserve, only the DDL.

CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- ── durable mirror (rebuildable from .egr.md) ──────────────────────────

CREATE TABLE IF NOT EXISTS engram (
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
  -- M5 data-integrity fix: the high-water mark storage_strength has ever
  -- reached. archive_below_floor's eligibility gate needs this in
  -- addition to "(success_count + failure_count) >= 3" -- evidence alone
  -- does not prove the engram ever *had* standing to decay away from; a
  -- skill that has only ever grown toward the floor (never crossed it)
  -- is a novice, not a decayed veteran (docs/03 "reaching"/AC-033
  -- "decayed below" both presuppose a prior peak >= floor_archived).
  peak_storage_strength REAL NOT NULL DEFAULT 0.0,
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
CREATE INDEX IF NOT EXISTS engram_status_idx ON engram(status, verification_status);

CREATE TABLE IF NOT EXISTS engram_step (
  engram_id TEXT NOT NULL REFERENCES engram(id) ON DELETE CASCADE,
  step_no INTEGER NOT NULL, text TEXT NOT NULL,
  ok_count INTEGER NOT NULL DEFAULT 0, total_count INTEGER NOT NULL DEFAULT 0,
  fault_class TEXT,                                -- e.g. GLOBAL_PINNING_BREAKS_SIBLINGS
  PRIMARY KEY (engram_id, step_no)
);

CREATE TABLE IF NOT EXISTS engram_trigger (
  engram_id TEXT NOT NULL REFERENCES engram(id) ON DELETE CASCADE,
  polarity TEXT NOT NULL CHECK (polarity IN ('positive','negative')),
  ord INTEGER NOT NULL, text TEXT NOT NULL,
  PRIMARY KEY (engram_id, polarity, ord)
);

CREATE TABLE IF NOT EXISTS edge (                                -- Tier B + declared composition edges
  src_id           TEXT NOT NULL REFERENCES engram(id) ON DELETE CASCADE,
  dst_name         TEXT NOT NULL,                  -- name, not id: dangling targets are legal
  dst_id           TEXT REFERENCES engram(id) ON DELETE SET NULL,
  type             TEXT NOT NULL CHECK (type IN
                     ('co_activation','composes','depends_on','similar_to','inhibits')),
  storage_strength REAL NOT NULL DEFAULT 0.0,      -- S_edge: the LEARNED
                                                    -- channel ONLY. A declared
                                                    -- edge stays at 0.0 here
                                                    -- forever; its routing
                                                    -- weight is computed, never
                                                    -- stored (DECLARED-EDGES-
                                                    -- AMENDED, spec §3.3.1).
  s_decayed_at     TEXT NOT NULL,
  evidence_count   INTEGER NOT NULL DEFAULT 0,
  provenance       TEXT NOT NULL CHECK (provenance IN ('declared','learned','distilled','derived')),
  first_observed   TEXT NOT NULL, last_updated TEXT,
  below_prune_runs INTEGER NOT NULL DEFAULT 0,     -- docs/03: prune after >=3 consecutive runs
  dangling         INTEGER NOT NULL DEFAULT 0,     -- 1 => inert, excluded from routing
  PRIMARY KEY (src_id, dst_name, type)
);
CREATE INDEX IF NOT EXISTS edge_dst_idx ON edge(dst_id, type);

CREATE TABLE IF NOT EXISTS context_node (                        -- docs/03 Class C row 15: renamed astroengrams
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL  -- project|toolchain|error_class
);
CREATE TABLE IF NOT EXISTS engram_context (
  engram_id TEXT NOT NULL REFERENCES engram(id) ON DELETE CASCADE,
  context_id TEXT NOT NULL REFERENCES context_node(id) ON DELETE CASCADE,
  weight REAL NOT NULL DEFAULT 1.0,
  PRIMARY KEY (engram_id, context_id)
);

CREATE TABLE IF NOT EXISTS engram_community (                    -- derived index; rebuilt, never checkpointed
  engram_id TEXT PRIMARY KEY REFERENCES engram(id) ON DELETE CASCADE,
  community_id INTEGER NOT NULL, algo TEXT NOT NULL, computed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS engram_journal (                      -- mirror of the file's provenance journal
  engram_id TEXT NOT NULL REFERENCES engram(id) ON DELETE CASCADE,
  version INTEGER NOT NULL, ts TEXT NOT NULL, author TEXT NOT NULL,
  event TEXT NOT NULL, note TEXT, signal_tier TEXT, base_version INTEGER,
  PRIMARY KEY (engram_id, version, ts)
);

-- ── Tier C (ephemeral; lost on rebuild, by design) ─────────────────────

CREATE TABLE IF NOT EXISTS eph_session (
  session_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
  ended_at TEXT, host TEXT, adapter_verified INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS eph_bookkeeping (                     -- CR-1: hot-path counters, checkpointed to Tier A
  engram_id TEXT PRIMARY KEY, exposure_delta INTEGER NOT NULL DEFAULT 0,
  last_activated TEXT, route_returns INTEGER NOT NULL DEFAULT 0,
  -- M6 defect fix (write_ratio churn, spec R3 <5% target): Dream Phase 2's
  -- spacing-effect input needs an anchor timestamp advanced on *every*
  -- processed node tag (committed or not), or a first, spacing=0
  -- observation would leave the anchor null forever and permanently block
  -- potentiation (see core/dream.py::_phase2_potentiate's node-loop
  -- docstring). That anchor must NOT be the file-mirrored
  -- `engram.last_applied` column -- advancing a Tier-A/file field on a
  -- zero-dw observation makes a checkpoint candidate byte-differ from the
  -- file on `last_applied` alone, forcing a rewrite even though nothing
  -- was actually learned. `last_dw_input_at` is the Tier-C-only home for
  -- that anchor (lost on rebuild, which is safe/conservative -- it just
  -- resets ramp-up to "first observation" again); `engram.last_applied`
  -- is now advanced only on a real commit.
  last_dw_input_at TEXT
);

CREATE TABLE IF NOT EXISTS eph_retrieval (                       -- R: retrieval strength, fast decay
  engram_id TEXT PRIMARY KEY, r REAL NOT NULL DEFAULT 0.0, r_decayed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS eph_tag (                             -- synaptic tags, two-phase commit
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL, subject_kind TEXT NOT NULL CHECK (subject_kind IN ('node','edge')),
  engram_id TEXT, edge_src TEXT, edge_dst TEXT, edge_type TEXT,
  signal_tier INTEGER NOT NULL CHECK (signal_tier IN (0,1,2)),
  set_at TEXT NOT NULL, expires_at TEXT NOT NULL,
  captured_at TEXT, capture_valence REAL, capture_salience REAL, capture_weight REAL,
  capped INTEGER NOT NULL DEFAULT 0, consumed_run_id TEXT
);
CREATE INDEX IF NOT EXISTS eph_tag_live_idx ON eph_tag(session_id, expires_at, captured_at);

CREATE TABLE IF NOT EXISTS eph_candidate_edge (                  -- sub-threshold edges (Tier C)
  src_id TEXT NOT NULL, dst_id TEXT NOT NULL, type TEXT NOT NULL,
  pending_dw REAL NOT NULL DEFAULT 0.0, evidence_count INTEGER NOT NULL DEFAULT 0,
  first_observed TEXT NOT NULL, last_updated TEXT NOT NULL,
  PRIMARY KEY (src_id, dst_id, type)
);

CREATE TABLE IF NOT EXISTS eph_embedding (
  engram_id TEXT NOT NULL, model TEXT NOT NULL, dim INTEGER NOT NULL,
  vec BLOB NOT NULL,                               -- float32 little-endian, L2-normalised
  source_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY (engram_id, model)
);

CREATE TABLE IF NOT EXISTS eph_event (                           -- episodic ledger = Dream input + audit trail
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, session_id TEXT,
  tool TEXT NOT NULL, signal_tier INTEGER, engram_id TEXT,
  valence REAL, salience REAL, payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS eph_event_ts_idx ON eph_event(id, ts);

-- docs/02 discipline 4. M5 security fix #2: keyed on (tool, request_id) --
-- NOT request_id alone -- so two different tools sharing a caller-chosen
-- request_id (e.g. `checkpoint` and `consolidate`, both near-argument-less)
-- never collide: an idempotency lookup that ignored the tool name would
-- either replay a *different* tool's cached response verbatim (breaking
-- AC-005/AC-019's "identical arguments" contract) or let a caller pre-burn
-- a request_id under one tool to silently no-op a later, legitimate call
-- to another. `expires_at` is a real TTL (`mcp/app.py` sets it to
-- `created_at + cfg.idempotency_ttl_s`, not `created_at` itself); rows
-- past it are purged by `core/decay.py::purge_retention` (Dream phase 3).
CREATE TABLE IF NOT EXISTS eph_idempotency (
  tool TEXT NOT NULL, request_id TEXT NOT NULL, args_sha256 TEXT NOT NULL,
  response_json TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
  PRIMARY KEY (tool, request_id)
);
CREATE INDEX IF NOT EXISTS eph_idempotency_expires_idx ON eph_idempotency(expires_at);

-- ── operational (not learned state, not checkpointed) ──────────────────

CREATE TABLE IF NOT EXISTS writer_lease (
  id INTEGER PRIMARY KEY CHECK (id = 1), holder TEXT NOT NULL, pid INTEGER NOT NULL,
  acquired_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL, expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS consolidation_run (
  id TEXT PRIMARY KEY, trigger TEXT NOT NULL,      -- manual|session_end|cli|idle
  state TEXT NOT NULL,                             -- queued|running|succeeded|failed
  phase TEXT, started_at TEXT, finished_at TEXT,
  watermark_event_id INTEGER NOT NULL DEFAULT 0, stats_json TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS approval (
  id TEXT PRIMARY KEY, op TEXT NOT NULL, target_name TEXT NOT NULL, payload_json TEXT NOT NULL,
  state TEXT NOT NULL,                             -- proposed|approved|rejected|executed|failed
  proposed_by TEXT NOT NULL, proposed_at TEXT NOT NULL,
  decided_by TEXT, decided_at TEXT, reason TEXT, executed_run_id TEXT
);
