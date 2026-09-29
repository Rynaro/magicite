# Independent RAMZA critique and resolution

Checker: `spec-critic`; author: `root-spec-author`. Review was performed in a separate agent context using the persisted plan/criteria/state and referenced spec packet. The checker did not edit the spec. This is independent specification review, not independent product validation.

## First review — changes required

Mechanical checks passed: full-tier plan lint and 68 EARS criteria. Content score through `ramza-score --rubric refine --cycle 1`: total 2.8, clarity 2, completeness 2, actionability 3, efficiency 4, testability 3; verdict **fail**. The tool state preserves the result and distinct maker/checker identities.

Prescriptions and applied changes:

1. **Host prerequisites versus plannable artifacts:** C1/C2/C5 now define explicit host/artifact capability kinds and known-empty/unknown inventory semantics. S06 keeps consumers eligible for planning; S08 adds the A→B producer/consumer integration case. AC-S06-05 and AC-S08-05.
2. **Restore authority:** C8 now defines a separate current RecoveryOverlay and operator-held latest-sequence anchor; missing/stale inputs keep routing and evidence access disabled. S12 recovery is bounded by backup recovery-point sequences. AC-S12-02/03 amended; AC-S12-05 added.
3. **Normative edges:** C1 freezes exact-revision requires/before/supersedes representation and migration semantics; S02/S08 share fixtures. AC-S02-05 and AC-S08-06.
4. **Mandatory stable activation:** S07 now owns the durable policy_store API and reviewed compare-and-swap activation/rollback. Optional S10 consumes it; S07/S11 retain router/config/binding ownership. AC-S07-05 exercises activation with S10 absent.
5. **Operational clarity:** C4/E2 allow a stronger simple policy to replace initial dense before final evaluation; S13 scaffold and published-channel milestones are explicit in plan.json.

Refinement was entered through `ramza-gate refine`, then returned to Test. Revised criteria count: 74. Second independent review is recorded in verification.md and state when completed.

## Second review — passed

The independent checker reran structure/EARS/DAG/criterion-consistency checks: all passed, 74 criteria. Stricter post-refinement rubric (`--cycle 2`): clarity 4, completeness 4, actionability 4, efficiency 4, testability 4; total 4.0, **pass**, no remaining blockers. The single nonblocking wording correction (S14 rollback keeps the frozen strongest simple incumbent rather than necessarily dense) was applied. The state records one actual refinement loop and two independent review scoring passes.
