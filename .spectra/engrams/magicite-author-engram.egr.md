---
spec: engram/0.2
name: magicite-author-engram
id: egr_f640cc8b
version: 1
provenance: authored
intent:
  does: "Author a new .egr.md engram that survives Magicite's strict lint profile and lands routable"
  use_when: "adding a new skill to a Magicite registry by hand, or a hand-written engram is rejected at register/sync time"
  not_when: "converting an existing SKILL.md — that is register(skill) import, which lands as a draft under the lenient import profile"
triggers:
  positive:
  - "write a new magicite engram file by hand"
  - "my .egr.md was rejected by strict lint"
  - "what fields does an engram need to be routable"
  - "add a skill to the magicite registry"
  negative:
  - "import an existing SKILL.md into magicite"
context_affinity: [magicite, engram-format, registry, authoring]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: []
yields: [lint-clean-engram-file]
composes: []
inhibits: [magicite-import-skillmd]
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
1. Create `.spectra/engrams/<name>.egr.md`, where `<name>` matches `^[a-z0-9-]{1,64}$` and is byte-identical to the `name:` field — the filename is not decoration, `core/registry.py` resolves declared edges by name.
2. Set `spec: engram/0.2` and `provenance: authored`. Only `authored` and `sharpened` origins can reach `verification_status: verified`; `imported` and `distilled` are pinned to `pending` by `core/lifecycle.py::initial_verification_status`.
3. Write all three `intent` fields. `not_when` is a hard strict-lint error when missing or empty. Write it for the reader and for the fitness score, and do not expect it to steer retrieval — see the Pitfalls below for what it does and does not reach.
4. Supply at least three `triggers.positive` and at least one `triggers.negative` (`MIN_POSITIVE_TRIGGERS`/`MIN_NEGATIVE_TRIGGERS` in `engram/lint.py`). Keep each trigger specific to this project; a short generic phrase risks the over-broad-trigger quarantine described in the Pitfalls below.
5. Set `id: egr_00000000` as a placeholder and let tooling compute the real value — the id is the first eight hex digits of a canonical-JSON SHA-256 over identity+routing (`engram/ids.py::new_engram_id`), so hand-guessing it is pointless and hand-editing it after registration breaks the CR-8 immutable primary key.
6. Leave `trust.verification_status` at `pending`. The server assigns the real value at ingest and never reads yours; declaring `verified` in your own file is exactly the planted-import attack `initial_verification_status` exists to defeat.
7. Write the body with the four recognised headings only — Procedure, Pitfalls, Examples, Provenance — and number Procedure steps 1..N with no gaps, which strict lint checks directly.
8. Register or sync, then confirm the engram reached `verification_status: verified` before expecting it in any `route()` result; routable means status in nascent/probation/consolidated/promoted AND verified.

## Pitfalls
- (x1) A fenced code block anywhere in the body is parsed as an exec block by `engram/parser.py`, and `has_exec_blocks` alone sets `quarantine_recommended`, so the engram lands quarantined and never routes. Use inline backticks for commands instead — this file does.
- (x1) Over-broad triggers are quarantined too. `injection_scan` matches every positive trigger as a substring against twenty stock developer phrasings in `DEFAULT_PROBE_QUERIES`; more than 30% hits quarantines the engram. A trigger like "run the test suite" is a literal substring of one of those probes.
- (x1) Provenance journal versions must be non-decreasing, and the first entry is version 1. An out-of-order journal is a strict-lint error, not a warning.
- (x1) Declared edge targets that name an unregistered engram become dangling and are dropped from routing rather than erroring, so a typo in `needs:` fails silently at route time instead of loudly at register time.
- (x1) Expecting a negative trigger or `not_when` to steer retrieval away from a near-miss. As of v0.1.0 they do not: `embeddable_text` composes the embedded text from `intent.does`, `intent.use_when`, the positive triggers, and the Procedure steps only, and the router never reads the trigger table. They remain required, fitness-scored, and valuable as documentation and as the id preimage — but they do not move a ranking.
- (x1) Reaching for a declared `inhibits:` edge to fix a near-miss instead. Routing does apply it, but inhibition scales the *target's* activation by the source's, so a symmetric pair suppresses the weaker node harder when one already leads — measured on this registry, adding a mutual pair pushed the intended engram out of the top 3 rather than into first place. Author `inhibits:` for genuine mutual exclusion, not as a ranking lever.

## Examples
+ "I hand-wrote an engram and sync says not_when is required" -> full procedure, step 3
+ "what makes an engram routable versus merely registered" -> steps 6 and 8
- "convert my SKILL.md files into engrams" -> NOT this engram (that is the import path, which lands draft/pending by design)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the dogfood registry
