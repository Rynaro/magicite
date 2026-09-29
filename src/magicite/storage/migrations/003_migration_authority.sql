-- S03: migration authority + index-safe generation pointers (C8 / C11).
-- Additive only. Does not rewrite engram rows; artifact format upgrades
-- are journaled file operations owned by core/migration.py.

CREATE TABLE IF NOT EXISTS migration_operation (
  operation_id     TEXT PRIMARY KEY,
  kind             TEXT NOT NULL,
  state            TEXT NOT NULL
                     CHECK (state IN ('pending','running','completed','failed','restored')),
  source_format    TEXT NOT NULL,
  target_format    TEXT NOT NULL,
  backup_relpath   TEXT,
  preview_digest   TEXT,
  manifest_digest  TEXT,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  completed_at     TEXT,
  error_message    TEXT
);

CREATE TABLE IF NOT EXISTS migration_journal_step (
  operation_id  TEXT NOT NULL REFERENCES migration_operation(operation_id) ON DELETE CASCADE,
  step_key      TEXT NOT NULL,
  status        TEXT NOT NULL CHECK (status IN ('done','skipped')),
  committed_at  TEXT NOT NULL,
  detail_json   TEXT,
  PRIMARY KEY (operation_id, step_key)
);

CREATE TABLE IF NOT EXISTS index_generation (
  generation_id       TEXT PRIMARY KEY,
  fingerprint_json    TEXT NOT NULL,
  fingerprint_digest  TEXT NOT NULL,
  state               TEXT NOT NULL
                        CHECK (state IN ('building','complete','published','superseded','failed')),
  schema_version      INTEGER NOT NULL,
  created_at          TEXT NOT NULL,
  completed_at        TEXT,
  published_at        TEXT,
  error_message       TEXT
);

-- Single-row active pointer. Atomic swap updates generation_id and
-- previous_generation_id together under the writer lease (C11).
CREATE TABLE IF NOT EXISTS index_active_pointer (
  id                      INTEGER PRIMARY KEY CHECK (id = 1),
  generation_id           TEXT REFERENCES index_generation(generation_id),
  previous_generation_id  TEXT REFERENCES index_generation(generation_id),
  updated_at              TEXT NOT NULL
);

INSERT OR IGNORE INTO index_active_pointer (id, generation_id, previous_generation_id, updated_at)
VALUES (1, NULL, NULL, '1970-01-01T00:00:00+00:00');
