# A01 — AC-S02-01 preserved-bytes scope

Status: applied. Plan: `magicite-v1`. Criterion: `AC-S02-01` (slice S02). Gate: `ramza-freeze --amend`.

## Ruling

The amendment is accepted. It does not weaken C1's lossless imported-payload guarantee. It names the intentional preserved store and excludes dual-writer YAML re-render from the identity set.

C1 already requires lossless imported SKILL.md **payloads**, comments and unknown optional extension data, and forbids body-content normalization that destroys imported bytes. It does not require byte-identity of re-rendered host frontmatter YAML, nor byte-identity of hand-authored non-canonical engram/0.2 YAML through parse→write. Requiring those bytes would fight the dual-writer design (stored `skill_md_source` payload vs ruamel-canonical frontmatter). Independent ATLAS review (maker≠checker) recommended a formal criterion amendment, not a code change.

V1 safety intent that remains mandatory:

- Imported SKILL.md body/payload bytes are not normalized away.
- Unknown optional extension extras carried with that payload remain recoverable.
- Writer-canonical engram/0.2 output is byte-stable under parse→write.
- A pure 0.2→1.0 transform does not mutate on-disk source archives.

Out of the identity set (not a C1 loss):

- Full SKILL.md **file** bytes after export, when host frontmatter YAML is re-rendered.
- Hand-authored non-canonical 0.2 YAML layout (unusual indentation or equivalent) through parse→write.

C1 contract text is unchanged; this amendment operationalizes it in AC-S02-01.

## Previous freeze text

```
### AC-S02-01 (event-driven)
GIVEN archived 0.2 and SKILL.md fixtures with prose, fences and extensions
WHEN parse/write/export roundtrip runs
THEN preserved source bytes SHALL remain identical
VERIFY: extend tests/integration/test_skillmd_roundtrip.py with v1 fixture corpus
```

The previous THEN was over-broad: "preserved source bytes" could be read as the entire SKILL.md file and any 0.2 YAML text, which is not C1's payload contract.

## Amended freeze text

```
### AC-S02-01 (event-driven)
GIVEN archived 0.2 and SKILL.md fixtures with prose, fences and extensions
WHEN persist, export, writer-canonical parse/write, or the pure 0.2-to-1.0 transform runs
THEN preserved source bytes SHALL remain identical on the C1 identity set: imported skill_md_source.body_raw plus extra_frontmatter through persist/export; writer-canonical engram/0.2 parse-write bytes; on-disk source archives after the pure transform
VERIFY: extend tests/integration/test_skillmd_roundtrip.py with v1 fixture corpus covering body_raw/extra_frontmatter persist-export identity, writer-canonical 0.2 parse-write identity, and untouched on-disk archives under the pure transform
```

One criterion ID is retained: the three named surfaces are the definition of "preserved source bytes", not three independent features. The THEN avoids a compound ` AND ` so `ramza-ears-lint` still treats it as one assertion.

## Evidence

Implementation (S02, independent of this amendment):

- Imported host SKILL.md body bytes are stored losslessly in `skill_md_source.body_raw`, with YAML extras in `skill_md_source.extra_frontmatter`, and proved byte-identical through persist/export.
- Writer-canonical engram/0.2 files are byte-stable through parse→write→bytes.
- On-disk archives are untouched by the pure 0.2→1.0 transform.

Known non-identity (expected under dual-writer):

- Full SKILL.md file bytes are not identical after export because host frontmatter YAML is re-rendered (ruamel round-trip normalization).
- Hand-authored non-canonical 0.2 YAML is not byte-stable through parse→write.

Reviewer: ATLAS, maker≠checker versus the S02 implementation. Recommendation: formal amendment of AC-S02-01, not a writer change to preserve original YAML bytes.

## Packet updates

- `.spectra/plans/magicite-v1/acceptance.md` — canonical amended criterion.
- `.spectra/plans/magicite-v1/slices/s02-engram-1.0-schema-and-lossless-artifacts.md` — verbatim duplicate; action plan step 2 aligned.
- `.spectra/plans/magicite-v1/plan.json` — `criteria_amendments` entry A01.
- `.spectra/plans/magicite-v1.state.json` — hash-chained via `ramza-freeze --amend`.
- `.spectra/plans/magicite-v1/verification.md` — amendment recorded with new freeze hash.
- Packet manifest and ECL envelope fingerprints regenerated for changed payloads.

## Freeze chain

- Previous `criteria_sha256`: `77a5af3e5e0420aef2ddff0d7691c44cc21d4b820679fef6be0e4e5de3bdc458`
- Amended `criteria_sha256`: `20a5c8c9a8e0d938074e1ee893ba758eaf31bc919466be62ad41897e5f3f6673`
- Recorded in `.spectra/plans/magicite-v1.state.json` via `ramza-freeze --amend` at `2026-09-29T22:42:58Z`.
