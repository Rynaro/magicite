-- S04: bundle trust / digest-bound local admission (C10).
-- Provisional allocation #4 (stem=bundle_trust) promoted for this lane so
-- tests can apply the schema; S03 finalizes numbering at integration.
-- Authoritative trust ledger remains JSON under .magicite/trust/; these
-- tables are a rebuildable cache reloaded via trust.reload_from_mirror.

CREATE TABLE IF NOT EXISTS trust_decision (
  decision_id         TEXT PRIMARY KEY,
  engram_id           TEXT NOT NULL,
  content_digest      TEXT NOT NULL,
  resource_digest     TEXT,
  decision            TEXT NOT NULL
                        CHECK (decision IN ('admit','reject','revoke','quarantine','pending')),
  source_channel      TEXT NOT NULL,
  signer_fingerprint  TEXT,
  signature_valid     INTEGER,              -- 1/0/NULL
  policy_id           TEXT NOT NULL,
  policy_revision     INTEGER NOT NULL,
  policy_digest       TEXT NOT NULL,
  scanner_revision    TEXT NOT NULL,
  actor               TEXT NOT NULL,
  timestamp           TEXT NOT NULL,
  reasons_json        TEXT NOT NULL DEFAULT '[]',
  event_id            TEXT
);
CREATE INDEX IF NOT EXISTS trust_decision_engram_idx
  ON trust_decision(engram_id, timestamp);
CREATE UNIQUE INDEX IF NOT EXISTS trust_decision_event_id_uidx
  ON trust_decision(event_id) WHERE event_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS trust_policy_meta (
  policy_id        TEXT PRIMARY KEY,
  revision         INTEGER NOT NULL,
  policy_digest    TEXT NOT NULL,
  scanner_revision TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);
