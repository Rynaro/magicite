-- v0.3 integrity recovery: monotonic writer fencing and recoverable
-- idempotency reservations. Existing v0.2 rows remain valid.

ALTER TABLE writer_lease
  ADD COLUMN fencing_token INTEGER NOT NULL DEFAULT 0;

ALTER TABLE eph_idempotency
  ADD COLUMN state TEXT NOT NULL DEFAULT 'completed'
  CHECK (state IN ('pending', 'completed'));
