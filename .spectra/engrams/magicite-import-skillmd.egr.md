---
spec: engram/0.2
name: magicite-import-skillmd
id: egr_f16e1287
version: 1
provenance: authored
intent:
  does: "Import existing SKILL.md files into a Magicite registry and finish the conversion so they become routable"
  use_when: "migrating a directory of host-native SKILL.md skills into Magicite, or an imported engram is stuck as a draft"
  not_when: "writing a brand-new skill from scratch — author a native .egr.md instead, which can reach verified in one pass"
triggers:
  positive:
  - "migrate my SKILL.md files into magicite engrams"
  - "why is my imported magicite engram stuck in draft"
  - "convert claude code skills to the engram format"
  - "magicite register skill import path"
  negative:
  - "hand-write a new magicite engram from nothing"
context_affinity: [magicite, engram-format, registry, migration]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: []
yields: [imported-draft-engram]
composes: []
inhibits: [magicite-author-engram]
provenance_journal:
- version: 1
  timestamp: '2026-08-15T00:00:00Z'
  author: claude-orchestrator
  event: authored
  note: First-party dogfood registry (change magicite-dogfoods-itself, AC-D1)
trust:
  origin: authored
  verification_status: pending
---
## Procedure
1. Call `register()` with the `skill` payload rather than a native `.egr.md` path; both shapes funnel through the same ingestion endpoint, which is what closes the registration-unit contradiction recorded as FINDING-010 in `docs/04`.
2. Expect the conversion mapping in `engram/skillmd.py::to_engram`: `intent.does` comes from the description, `use_when` from a "Use when:" line or the description tail, `not_when` from a "Not when:" line or the literal placeholder, and positive triggers are synthesised from name tokens plus description key phrases.
3. Expect `triggers.negative` to be empty — the importer never invents one. That single gap is normally enough to keep the import lint profile from passing cleanly.
4. Expect the landed state: `origin: imported`, `status: draft`, `verification_status: pending`. An imported engram is deliberately not routable; `initial_verification_status` pins every `imported` origin to `pending` regardless of how clean the lint was.
5. Finish the conversion with `sharpen()` to supply the missing negative triggers and a real `not_when`, which is the intended path out of draft.
6. Understand the remaining gap before planning around it: v1's tool surface exposes no `pending -> verified` review transition, so promotion of an import needs the manual review path described in `docs/operations.md`. Budget for that; do not expect an import to route on its own.
7. Re-run sync and confirm the file still parses under the lenient profile — `core/registry.py` keys the profile off the durable `provenance: imported` field, so an import keeps the lenient profile on every later rescan rather than hard-failing strict lint at the next rebuild.

## Pitfalls
- (x1) Treating draft status as a bug. It is the designed outcome of an import, and the CR-4 rule is that nothing is silently accepted or silently rejected — the draft is the loud middle state.
- (x1) Editing the imported file to claim `verification_status: verified`. The server assigns that field at ingest and never reads the file's value, so the edit is inert at best and is the exact pattern the trust gate treats as adversarial.
- (x1) Round-tripping an import and expecting a new id. Re-importing byte-identical description content re-derives the same id by design, so the duplicate is skipped rather than duplicated.

## Examples
+ "I imported forty SKILL.md files and none of them route" -> steps 4 through 6
+ "what does the importer do about negative triggers" -> step 3
- "author a fresh engram for a new procedure" -> NOT this engram (native authoring reaches verified directly)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the dogfood registry
