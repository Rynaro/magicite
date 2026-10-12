-- S09: evidence ledger projection metadata (C6 / C8 slot 6).
-- Authoritative evidence lives under <data_dir>/evidence/ (file segments).
-- These tables are rebuildable projections only; rebuild MUST NOT erase
-- the file ledger. Never store raw query / prompt / secret text here.

CREATE TABLE IF NOT EXISTS evidence_meta (
  id                INTEGER PRIMARY KEY CHECK (id = 1),
  ledger_version    TEXT NOT NULL,
  last_sequence     INTEGER NOT NULL DEFAULT 0,
  last_segment_id   TEXT,
  redaction_version INTEGER NOT NULL DEFAULT 1,
  updated_at        TEXT NOT NULL
);

INSERT OR IGNORE INTO evidence_meta
  (id, ledger_version, last_sequence, last_segment_id, redaction_version, updated_at)
VALUES (1, 'EvidenceLedger/1', 0, NULL, 1, '1970-01-01T00:00:00+00:00');

CREATE TABLE IF NOT EXISTS evidence_event_projection (
  event_id          TEXT PRIMARY KEY,
  sequence          INTEGER NOT NULL UNIQUE,
  decision_id       TEXT NOT NULL,
  event_type        TEXT NOT NULL
                      CHECK (event_type IN ('decision','outcome','policy_transition','deletion')),
  payload_digest    TEXT NOT NULL,
  recorded_at       TEXT NOT NULL,
  retention_class   TEXT NOT NULL
                      CHECK (retention_class IN ('operational','audit')),
  source_tier       INTEGER,
  outcome           TEXT CHECK (outcome IS NULL OR outcome IN ('success','failure','unknown')),
  segment_id        TEXT NOT NULL,
  tombstoned        INTEGER NOT NULL DEFAULT 0 CHECK (tombstoned IN (0, 1))
);

CREATE INDEX IF NOT EXISTS evidence_event_decision_idx
  ON evidence_event_projection(decision_id);

CREATE INDEX IF NOT EXISTS evidence_event_recorded_idx
  ON evidence_event_projection(recorded_at);

CREATE TABLE IF NOT EXISTS evidence_tombstone_projection (
  tombstone_id      TEXT PRIMARY KEY,
  target_event_id   TEXT NOT NULL,
  sequence          INTEGER NOT NULL,
  deleted_at        TEXT NOT NULL,
  reason            TEXT,
  actor             TEXT
);
