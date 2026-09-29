# Anchor plan: greenfield event-sourced rewrite of the import subsystem (score me)

> Calibration anchor. Score this hypothesis on the 7-dimension explore rubric
> (docs/methodology/scoring.md), then compare with `ramza-calibrate`.
> Same context as `solid-import.md` — the ask was "let users import players via CSV."

**Hypothesis:** Replace the import subsystem with an event-sourced core: introduce a
`DomainEvent` store (new Postgres schema + an `EventStore` gem), model imports as
`PlayerImportRequested/Validated/Applied` event streams, project into the existing
`players` table via a new projector process, and expose import progress over a new
websocket channel. CSV parsing moves to a new Rust sidecar for speed.

**Sketch:** ~6 new services, 2 new dependencies (EventStore gem, Rust toolchain in CI),
a second deployable process, schema additions, and a migration path for the 3 existing
importers "in a later phase."

**Known trade-offs:** none stated by the author. (Unstated by the plan, visible to a
reviewer: massive blast radius for a feature that needs none of it; the existing
pipeline's retry semantics are silently lost; the "later phase" migration is load-bearing
and unplanned; the Rust sidecar adds an operational surface no one asked for.)

**Why this anchors 'weak':** high innovation, catastrophic simplicity/risk scores,
poor alignment with the actual ask. A calibrated scorer must land this in the weak
band (<70). Grade inflation — scoring novelty as quality — shows up here first.
