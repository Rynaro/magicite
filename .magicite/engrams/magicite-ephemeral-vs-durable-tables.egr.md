---
spec: engram/0.2
name: magicite-ephemeral-vs-durable-tables
id: egr_11ea9756
version: 1
provenance: authored
intent:
  does: "Work with Magicite's two connection factories and the eph_ table boundary the SQLite authorizer enforces at runtime"
  use_when: "a write raises a database error from a tool handler, or you are deciding which table a new piece of state belongs in"
  not_when: "the write was refused by the single-writer lease or the Dream-context assertion rather than denied by the connection"
triggers:
  positive:
  - "magicite sqlite3 DatabaseError on insert from a tool handler"
  - "which magicite tables can the hot path write"
  - "magicite eph_ table naming convention authorizer"
  - "ephemeral_connection versus writer_connection in magicite"
  negative:
  - "magicite raised a lease or dream-context error on write"
context_affinity: [magicite, storage, sqlite, security]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: [magicite-p0-hot-path-boundary]
yields: [correct-table-placement]
composes: []
inhibits: [magicite-writer-lease-and-dream-context]
provenance_journal:
- version: 1
  timestamp: '2026-08-15T00:00:00Z'
  author: claude-orchestrator
  event: authored
  note: Codebase tranche (change magicite-codebase-skill-tranche, AC-T1)
trust:
  origin: authored
  verification_status: pending
---
## Procedure
1. Identify which connection your code is running on. `storage/authorizer.py` provides exactly two factories: `ephemeral_connection`, which installs the authorizer, and `writer_connection`, which installs none. Both open the same WAL-mode file — they are two handles on one database, never two databases.
2. Recall which tools get the guarded connection: the seven hot-path tools are `route`, `load_skill_body`, `signal_use`, `signal_outcome`, `session_end`, `introspect`, and `flag_dead`.
3. Read the rule literally. The authorizer denies `INSERT`, `UPDATE`, `DELETE`, `DROP TABLE`, and `ALTER TABLE` on any table whose name does not start with `eph_`. Those four statement kinds and no more.
4. Note that reads are never touched. `SQLITE_SELECT` is not in the guarded set, so hot-path debounce and summary reads against durable tables pass through untouched — the boundary is about writes only.
5. Note that `SQLITE_CREATE_TABLE` is deliberately excluded. Migrations run once at startup over the writer connection, before any hot-path connection exists, and a table that does not yet exist cannot hold learned state.
6. Place new state accordingly. If the hot path must write it, the table name must begin with `eph_` and the state must genuinely be ephemeral. If it is durable, the write belongs on the writer path, which means it belongs to a different tool.
7. Do not reach for `writer_connection` to make a hot-path write succeed. That is not a workaround, it is a removal of the guarantee: the authorizer exists to constrain the hot path specifically, and the writer path is constrained by the lease instead.

## Pitfalls
- (x1) Naming a durable table with an `eph_` prefix to get past the authorizer. The prefix is the security boundary's only input, so misnaming a table silently grants the hot path write access to durable state.
- (x1) Assuming the authorizer protects the writer path too. It does not, by design — that connection has no authorizer at all and is governed by the lease.
- (x1) Confusing a denial with a refusal. The authorizer raises a `sqlite3.DatabaseError` before touching a page; the lease raises its own error type. They have different causes and different fixes.
- (x1) Expecting the guard to catch a `CREATE TABLE`. It will not, and that is intentional rather than an oversight.

## Examples
+ "my signal handler cannot insert into engram" -> correct, step 3; that state belongs on the writer path
+ "where does new session-scoped state go" -> an eph_ table, steps 5 and 6
- "assert_single_writer raised on my write" -> NOT this engram (that is the lease)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the codebase tranche
