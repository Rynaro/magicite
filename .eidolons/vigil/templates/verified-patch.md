# Verified Patch Template

Emitted when:

- Authority ≥ `sandbox`
- Survivor intervention (from `intervention-log.md`) was a `code_change` type
- Patch scope passes minimum-intervention review (≤3 files, no out-of-scope improvements)

Not emitted when:

- Authority = `read-only` — patches are described in the root-cause report textually
- Survivor was oracle injection, state correction, or timing fix — those are not directly applicable diffs
- Root cause is `SPEC_DEFECT` — no patch exists; routes to SPECTRA

---

## Document Skeleton

```markdown
# Verified Patch

**Mission ID**: VIGIL-YYYYMMDD-NNN
**Generated**: [ISO-8601]
**Base commit**: [SHA of reproduction commit]
**Authority**: sandbox | write
**Verified in sandbox**: yes

---

## Provenance

- **Intervention ID**: I-[NNN]
- **Hypothesis**: H-[NNN]
- **Counterfactual result**: FLIPPED
- **Mode**: deterministic | statistical
- **Evidence**:
  - Deterministic: 1 run, failure → success
  - Statistical: [N] of [M] runs pass, previously [N] of [M] failed

---

## Diff

\`\`\`diff
diff --git a/[path] b/[path]
index [hash]..[hash] [mode]
--- a/[path]
+++ b/[path]
@@ -[L],[N] +[L],[N] @@
 [context line]
 [context line]
-[removed line]
+[added line]
 [context line]
 [context line]
\`\`\`

[Repeat per file, up to 3 files. If more, split justification required.]

---

## Scope

**Files affected**: [count]
- [path] — [change type: modified | added | deleted]
- [path] — [change type]

**Lines changed**: [+N / -N]

**Out-of-scope changes**: none

[If any change is arguably out of scope for the hypothesis, explain here. Harness flags for review.]

---

## Verification

Run the following to verify the patch in downstream environment:

\`\`\`
[exact reproduction command from reproduction.md]
\`\`\`

**Expected result**: pass

**Additional regression checks recommended**:
- [command] — [what it checks]
- [command] — [what it checks]

---

## Application

This patch is **not auto-applied**. Downstream recipient (APIVR-Δ or human) applies explicitly.

**Recommended application**:

\`\`\`
cd [repo root]
git apply [path/to/this/patch.diff]
# or
git apply --check [path/to/this/patch.diff]   # dry run first
\`\`\`

**On application failure** (conflicts or rejected hunks):

- Downstream re-resolves on current head
- Re-run verification after re-apply
- If re-application diverges from the verified-sandbox outcome, re-invoke VIGIL on the current head

---

## Risk Assessment

**Risk level**: low | medium | high

**Rationale**: [one paragraph — what could go wrong with this patch, what other behavior might be affected, what the blast radius is]

**Blast radius**: [files / modules / users affected in worst case]

**Rollback**: `git revert` or `git apply -R` — this patch is self-contained

---

## Linked Artifacts

- Root-cause report: [path to root-cause-report.md]
- Intervention log: [path to intervention-log.md]
- Reproduction evidence: [path to reproduction.md]

---

*VIGIL — Verified Patch Template*
```

---

## Guidance

- **No improvements.** If you had an urge to fix surrounding code "while you're at it," resist. The patch must contain only the verified counterfactual. Out-of-scope improvements route to SPECTRA as a Delta suggestion, not to VIGIL's patch.
- **Minimal diff syntax.** Use standard unified diff format. Not a rewrite, not a refactor — just what the counterfactual required.
- **Risk honesty.** If the risk is high, say so. Downstream needs to decide whether to apply directly or go through a plan (SPECTRA).
- **Base commit matters.** Record the SHA at which the sandbox verification happened. If downstream applies to a different SHA, they re-verify.
- **No auto-apply.** VIGIL emits; downstream applies. Even with `write` authority, the patch is a handoff artifact, not an in-place modification.

---

*VIGIL — Verified Patch Template*
