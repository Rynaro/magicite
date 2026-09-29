-- S05: generation-scoped full-text / projection entries (C3 / C11).
-- Lifecycle and active-pointer tables remain in 003_migration_authority.sql
-- (S03 ownership). This migration only adds entry + FTS payloads keyed by
-- generation_id so concurrent builds never share a global FTS table.

CREATE TABLE IF NOT EXISTS index_entry (
  generation_id     TEXT NOT NULL REFERENCES index_generation(generation_id) ON DELETE CASCADE,
  engram_id         TEXT NOT NULL,
  revision          INTEGER NOT NULL,
  projection_sha256 TEXT NOT NULL,
  body_digest       TEXT NOT NULL,
  asset_digest      TEXT,
  name              TEXT NOT NULL,
  title             TEXT NOT NULL,
  intent_text       TEXT NOT NULL,
  triggers_text     TEXT NOT NULL,
  body_text         TEXT NOT NULL,
  full_text         TEXT NOT NULL,
  symbols_json      TEXT NOT NULL,
  chunks_json       TEXT NOT NULL,
  truncated_chunks  INTEGER NOT NULL DEFAULT 0,
  dense_dim         INTEGER,
  dense_vec         BLOB,
  PRIMARY KEY (generation_id, engram_id)
);

CREATE INDEX IF NOT EXISTS index_entry_gen_idx ON index_entry(generation_id);

CREATE VIRTUAL TABLE IF NOT EXISTS index_fts USING fts5(
  generation_id UNINDEXED,
  engram_id UNINDEXED,
  title,
  intent,
  triggers,
  body,
  tokenize = 'unicode61'
);
