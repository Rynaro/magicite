# V1 release decision draft: NO-GA

> **Current revision:** [r2](r2/README.md) re-adjudicates the gates against source
> `9bf9ca0` after trust hardening (still NO-GA; see its review record). The evidence in
> this directory remains the historical observation of source `c0782fd`.

This integration is **not eligible for GA**. It is a draft evidence package, not a
release, tag, publisher authorization, or maintainer sign-off. The installed
package remains 0.3.1. Frozen acceptance and release thresholds are unchanged.

The [gate table](gate-table.md) covers all 17 mandatory gates. The
[structured manifest](release-manifest.json) is evaluated by the actual fail-closed
validator:

```sh
python scripts/check_release_manifest.py docs/releases/v1/release-manifest.json
```

Exit 1 and `eligible: false` are the expected result for this draft. Exit 0 means
only that the evidence conjunction supports a GA recommendation; release authority
must still follow the repository process. Missing/duplicate/unknown gates,
nonwaivable waivers, missing obligations, corrupt/missing artifacts, self-review,
source mismatch, contradictory typed report contents, inconsistent RC fingerprints,
incomplete independent validation, or missing explicit maintainer approval block
eligibility. The validator verifies report integrity and declared review provenance;
it cannot authenticate human identities or replace a reviewer inspecting evidence.

[criterion-ledger.json](criterion-ledger.json) preserves all 76 exact frozen
criteria, their GIVEN/WHEN/THEN/VERIFY clauses, mapped test nodes and subchecks,
source heads, test-file/lock/environment/report digests, and independent review
status. RAMZA accepted the evidence package at `52f7bba` against clean tested
source `c0782fd`: **1,031 passed, 10 skipped**, plus type/lint/docs checks.
The final review annotation commit changes only evidence metadata and digests.
Criterion PASS is the complete stated mechanical obligation. It does not
substitute for an empirical release gate: statistical boundary tests do not prove
hybrid quality, and validator fixtures do not constitute RCs or external pilots.
Unrun external clauses remain UNEVALUATED even when their supporting tests pass.

Local verification uses Python 3.14.6 on macOS arm64 with MCP/mcp-types 2.0.0.
That is not the declared Python 3.11/3.12 release matrix. Other inherited dependency
versions are recorded; this is not claimed to be a complete lockfile recreation.
The report identifies clean source c0782fd, including the final S15/S13 integration
merge. Subsequent evidence-only commits archive those immutable results. Final PR
CI is recorded separately. Ten local skips are explicit: five
Docker-image checks, four unpublished-channel reservations, one optional igraph.

The [trust diagnostic](evidence/trust-mirror-loss.json) demonstrates the current
live mirror-deletion weakness. TRUST and SECURITY are FAIL; GA-ALL is FAIL by
conjunction. The [hardening proposal](trust-ledger-proposal.md) requires a human
choice about independent anchor custody before implementation. It neither amends
criteria nor waives a gate. The [privacy map](privacy-data-map.md) assembles current
storage/RPO/export/deletion behavior and does not claim to repair trust history.

Still required: official SkillRet final split, licensed real 10k corpus,
dedicated production E6 measurements, paired retrieval and empirical abstention
bounds, independently authored actual host-task outcomes, real Claude Code
transcripts, bounded fuzz/security evidence, full supported recovery/upgrade matrix,
published-channel and fetched supply-chain checks, independent operator tutorial,
two independent compatible RCs, external reproduction or two production pilots,
and explicit maintainer sign-off. No participants were contacted or publishing
accounts created by this task.

Human decisions remain open for the trust proposal/custody contract, the proposed
MCP expansion (the frozen 16-tool surface remains), optional S10, publishing,
worktree deletion, and shared-environment resynchronization. No release authority
is inferred from code review or green CI.

Rollback of this S16 change removes only the validator/evidence adapters and draft
documents; it has no runtime storage migration. Runtime rollback still follows the
complete-backup/current-overlay policy in the operator documentation. Never recover
revocation history from an older unauthenticated mirror set alone.
