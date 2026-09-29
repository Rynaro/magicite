# Anchor plan: CSV player-import via existing ingestion pipeline (score me)

> Calibration anchor. Score this hypothesis on the 7-dimension explore rubric
> (docs/methodology/scoring.md), then compare with `ramza-calibrate`.
> Context: a Rails SaaS with an existing `Ingestion::Pipeline` (used by 3 importers),
> `SidekiqBatch` jobs, and a `Player` model with uniqueness on `(team_id, external_ref)`.

**Hypothesis:** Extend `Ingestion::Pipeline` with a `PlayerCsvSource` adapter. Parse
with the already-vendored `csv` stdlib in streaming mode; validate rows against the
existing `PlayerRow` contract object; batch-upsert 500 rows per Sidekiq job using
`Player.upsert_all` keyed on the existing unique index; report per-row failures to the
importer's standard `ingestion_errors` table surfaced in the existing admin UI.

**Sketch:** new adapter (~120 lines) + a 15-line pipeline registration; no schema
changes; no new dependencies; reuses retry/dead-letter semantics from the pipeline.

**Known trade-offs:** upsert_all bypasses model callbacks (acceptable — importers
already do this; the after-import recompute job covers derived fields). Streaming
parse keeps memory flat but makes total-row-count progress approximate. Duplicate
rows inside a single file resolve last-write-wins within a batch, which matches the
other importers' documented behavior but is not announced in the UI.

**Why this anchors 'solid':** strong pattern reuse and alignment, real but bounded
risks, nothing novel — a competent, unexciting plan that should score in the 70s.
