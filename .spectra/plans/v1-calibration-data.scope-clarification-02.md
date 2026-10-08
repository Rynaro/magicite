---
eidolon: ramza
kind: spec
version: 1.0.2
created_at: 2026-10-08
---
# Prospective scope clarification 02: documentation-claim test contract

Root authorizes one narrow ancillary test path, tests/unit/test_docs_v1.py, for fixture alignment and regression coverage only. This prospective clarification is emitted before maker tracked edits. Original plan, all ten frozen criteria, earlier clarification, states, envelopes and receipts remain immutable. No product source, schema, policy, model, threshold, guard or empirical acceptance semantics change.

Original spec SHA-256: 0ef93afaacb058c1a48f6861f9bf2987d4c97b783b2896c95d76a7526fcf5d55. Original ten-criteria SHA-256: a5f8cbbd36a7104ba2839e92b889ba1eaf095f1266528a933466213c136a0ff7. Original plan message 809ab2e7-af1c-486b-af20-02db7f4b0d7a and clarification 01 message 8f2e6fd3-60ca-4fec-a451-1f3c7b58eeff belong to actual thread 59f3b808-f1e0-43c3-baed-a40b525be9e8.

## Evidenced reason

VIGIL root-cause message 4ca87e22-6f3f-4b60-b5f1-ea12f39bd251, report root-cause-ci-a2-to-idg.md SHA-256 2d2a20ac6878443146cbce601249f52ce6deaa36e09cc4779dad587bb1338cd2, identifies the actual archive-A2 head 16d66bb906eddb0c4192d0905a02dc572daf99db failure. Both Python jobs in run 37773569708 reported one failed/2017 passed/12 skipped/1 deselected; coverage 87.27%/87.26% exceeds the 70% floor. Ruff/type checks passed. This is an independently reproduced stale positive unit-test expectation, not a coverage deficiency or justification for weakening the guard.

The existing test_readme_claim_uses_full_integrity_chain[None] assumes a supported final claim backed only by a boolean seal will qualify. Frozen AC-CD-08 requires supported empirical final claims to have verified data/access bindings. The new rejection is correct. VIGIL reproduced the exact failure twice locally and exercised a temporary development-only counterfactual that passed all six original positive/mutation cases with production validation unchanged. The counterfactual keeps the fixture's existing development query and original split labels, recomputes the canonical corpus identity, and binds the claim/README to development. This report is diagnostic evidence, not a tracked implementation or a new qualification dataset.

## Narrow permitted repair

Vivi may edit only tests/unit/test_docs_v1.py under this added path. Adapt the positive integrity-chain fixture to select existing development-only query content; recompute and consistently bind canonical corpus, labels, experiment, prediction, result and claim identities according to existing APIs. The claim and README population must both state development. Never rename final/test/holdout content as development to evade the final guard. Preserve the metric semantics and declared value consistency of the tiny diagnostic fixture.

Retain meaningful wrong-value, wrong-metric, wrong-scope, missing-predictions and historical-evidence rejection assertions. The unchanged positive case must succeed for its intended development integrity-chain reason; mutants must fail for their intended corruption, rather than all being masked by an unrelated final-binding rejection. Add a separate explicit regression proving that an otherwise well-bound supported final claim with a bare final seal is rejected for missing qualifying data/access bindings. No fixture exemption may turn synthetic final content into qualifying empirical evidence. Do not modify shared fixture/source bytes outside the previously owned paths or expand the change into unrelated documentation tests.

This is fixture contract alignment and safety regression coverage, not a feature, performance improvement, empirical result or additional closed readiness obligation. Vivi remains sole source writer, is not alone in the codebase, and must preserve others' edits and the primary checkout.

## Effective scope and verification

The additive scope/state copies for clarification 02 include the original scope, clarification 01's conditional canonical generated-reference path, and tests/unit/test_docs_v1.py. Existing restrictions on the conditional generated reference still apply; no generated delta is required by this test repair. Use the additive state for subsequent drift checks while preserving all earlier states.

Run the focused documentation-claim positive/mutation/new-final-rejection cases and directly affected tests; independent checker must inspect why each positive/negative verdict occurs and confirm that no final content was relabeled. After commit, require all six current-head CI checks to succeed; neither previous failed head nor source-C focused passes substitute for those current-head results. Preserve both failed CI histories and the counterfactual as diagnostic records.

Measured source C5167757c5b5e661096cf86bacab670e90f44980e and its offline execution evidence may remain valid if exact runtime, runner, lock and fixture input equality is independently verified. The successor's tests/unit/test_docs_v1.py bytes will differ and must be explicitly recorded as a new test-source change; never claim all source/test bytes are unchanged or relabel old test execution as execution of the new tests. Historical 173-source/20-fixture checks must retain their original measured identities and limitations. Record the new commit and its own CI separately.

Authentic no-match/group/temporal/fresh-final data, calibration, E2/E3/E6 remain UNEVALUATED where unsupported. No model, fitting, ranking campaign, default activation, data authenticity, Gauge or human acceptance is authorized or claimed. RAMZA writes only additive temporary planner artifacts; root independently verifies the new envelope before maker handoff.
