# S02 — Engram 1.0 schema and lossless artifacts

## Assignment

Status: ready to assign subject to dependency gates. Priority: P0 V1 release scope.
Build after: none; start immediately.
Merge after: none.
Executor: reasoning-capable implementation agent; follow repository Vivi workflow. This packet itself is specs only.
Work bound: one independently reviewed slice; split into smaller PRs at the listed action boundaries if needed. No elapsed-day deadline; completion is evidence-gated, per user direction.

Read [shared contracts](../contracts.md), [evaluation](../evaluation.md), [release gates](../release-gates.md) and [dispatch plan](../plan.json) before coding. Shared-file ownership in C9 overrides broad owned-path patterns below.

## Story and current evidence

As a skill author, I want typed applicability and risks without losing my procedure or stable identity.

Only engram/0.2 is modeled at engram/model.py:134-175; immutable IDs at engram/ids.py:1-7 and :49-56 (H). Raw prose is retained at parser.py:87-105; lossless SKILL.md source at skillmd.py:270-307 (H).

## Scope and contract

C1, C8. Publish schema, typed models, canonical examples, negative fixtures and pure dual-reader API, including host/artifact capability kinds and relations.requires/before/supersedes from C1. Do not mass-migrate repository artifacts in this slice. The existing ID remains stable; identity drift is diagnostic. Digest collisions reject registration, never overwrite an existing artifact.

Owned implementation surfaces (future work, not modified by this packet):

- `src/magicite/engram/*`
- `tests/unit/engram/*`
- `tests/fixtures/engram-v1/*`
- `tests/integration/test_skillmd_roundtrip.py`

Out of scope: unrelated refactors, changing another slice's contract or thresholds, claiming unrun verification, host execution inside the runtime, publishing a release without release authority.

## Action plan

1. Add engram/1.0 schema and typed compatibility/capability/risk/assets/extension models.
2. Preserve imported skill_md_source.body_raw plus extra_frontmatter and procedure_raw through persist/export; keep writer-canonical engram/0.2 parse-write identity; leave on-disk source archives untouched by the pure transform; retain frontmatter extensions. Re-rendered host frontmatter YAML and non-canonical 0.2 YAML layout are outside the identity set.
3. Define canonical manifest/projection digests and external resource containment validation.
4. Expose explicit target-format rendering and pure 0.2-to-1.0 transform for S03; keep unsupported legacy state in a nonauthoritative namespace.

## Acceptance Criteria

### AC-S02-01 (event-driven)
GIVEN archived 0.2 and SKILL.md fixtures with prose, fences and extensions
WHEN persist, export, writer-canonical parse/write, or the pure 0.2-to-1.0 transform runs
THEN preserved source bytes SHALL remain identical on the C1 identity set: imported skill_md_source.body_raw plus extra_frontmatter through persist/export; writer-canonical engram/0.2 parse-write bytes; on-disk source archives after the pure transform
VERIFY: extend tests/integration/test_skillmd_roundtrip.py with v1 fixture corpus covering body_raw/extra_frontmatter persist-export identity, writer-canonical 0.2 parse-write identity, and untouched on-disk archives under the pure transform

### AC-S02-02 (event-driven)
GIVEN a legacy ID and references
WHEN the pure transform runs twice
THEN the stable ID SHALL remain unchanged
VERIFY: new tests/unit/engram/test_v1_schema.py::test_identity_preserved

### AC-S02-03 (event-driven)
GIVEN an unknown mandatory extension or invalid version range
WHEN the artifact is validated
THEN validation SHALL fail closed
VERIFY: new test_v1_schema.py::test_required_unknown_rejected

### AC-S02-04 (event-driven)
GIVEN an external asset with traversal or changed bytes
WHEN the artifact is loaded
THEN asset validation SHALL reject it
VERIFY: new test_v1_schema.py::test_asset_containment_and_digest

### AC-S02-05 (event-driven)
GIVEN the shared host/artifact and exact-revision relation fixtures
WHEN schema validation and legacy transform run
THEN the resulting typed relations SHALL match C1 without inferred learned-edge semantics
VERIFY: new tests/unit/engram/test_v1_schema.py::test_shared_relation_fixtures

## Verification and handoff

VERIFY targets above name expected future tests/gates; they are not claims that these tests exist or passed at spec time. Extend the cited existing tests where appropriate. Run focused tests first, then repository lint/type and affected integration suites; preserve current hot-path, writer lease, rebuild and idempotency invariants. Record actual commands, dependency/model pins and outputs. Empirical/release gates require their own artifacts beyond unit tests.

Deliver: implementation branch/base SHA, dependency SHAs, owned-path diff, contract fixtures, criterion-to-test/result mapping, migration/rollback notes, and any unpassed criterion. Do not mark complete merely because tests for the happy path pass. Integration owner applies shared-file patches in dependency order. A contract change requires an explicit spec/criteria amendment before merging dependent work.

## Failure and rollback

Keep the 0.2 reader and original bytes. Never emit 1.0 automatically to a 0.2-only consumer; migration and downgrade belong to S03.
