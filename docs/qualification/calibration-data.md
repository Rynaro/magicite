# Calibration-data controls

This workflow validates companion data packets and demonstrates local freeze/access controls. The new packet CLI commands perform no model calls, ranking, calibration fitting, save or activation. Retained unit regression tests exercise existing diagnostic/UnitModel ranking mechanics and are not empirical observations. Structural control success leaves authentic annotation, independent groups, no-match judgments, project-temporal history, fresh final, E2, E3 and E6 readiness **UNEVALUATED**. No qualifying dataset was supplied for this slice.

`CorpusManifest/1` query identity semantics stay intact. The companion `magicite/calibration-data-packet/1` binds the complete candidate pool, exact runtime rows, scoring labels, preserved partition assignments, typed provenance, preregistration and referenced supporting declarations. Paths must remain inside the packet directory, with SHA256 and exact row counts. Hashes verify bytes; they do not authenticate people, source independence, annotations or event truth.

Partition families are development (`train`, `development`), calibration (`calibration`) and final (`test`, `final`, `holdout`). Every pair must be separated by query identity, normalized text and declared source/task/repository/session relationships. Unknown groups or temporal history must have explicit unavailable reasons. No guessed groups or timestamps supply empirical evidence. Referenced event times are distinct from collection and annotation times; an explicit temporal cutoff tests event ordering across partitions.

Positive targets must belong to the complete frozen pool. Ambiguity and no-match require explicit rationale and pool-bound judgment references; an empty relevance map alone is insufficient. Runtime rows contain only `query_id`, `query_text`, `compatibility_context`, and reject gold/group/annotation injection. The small `tests/fixtures/calibration-data` packet invents tasks, people, sources and times for controls only.

Run with the repository's frozen Python environment from a clean committed source candidate:

```sh
python scripts/qualify_calibration_data.py validate tests/fixtures/calibration-data/packet.json
python scripts/qualify_calibration_data.py freeze tests/fixtures/calibration-data/packet.json --source-commit "$(git rev-parse HEAD)" --output /tmp/calibration-freeze.json
python scripts/qualify_calibration_data.py verify /tmp/calibration-freeze.json
python scripts/qualify_calibration_data.py open /tmp/calibration-freeze.json --purpose synthetic-control
python scripts/qualify_calibration_data.py open /tmp/calibration-freeze.json --purpose synthetic-control --replay
```

Preparation authors inspect labels while validating/freezing. Evaluation-consumer `open` verifies all frozen input bytes and source/runner bindings, then durably publishes a no-replace access intent before decoding scoring labels. Failed persistence releases no labels; concurrent fresh opens have one winner. Replay must match the exact freeze/preregistration/experiment identity and purpose, and retains the original exposure receipt. Failure after intent publication conservatively retains exposure. Freeze files cannot be overwritten.

These are local bookkeeping controls. The receipt cannot prevent direct filesystem reads, copying a freeze to another location, or ledger rollback, and cannot prove data was untouched. The recorded first-access intent time describes this workflow's intent publication, not an exact model call.

SkillRet revision `6583d7d2ed07644d0fb8938ed8178f3a7dc42a12` test was previously exposed on source `389834606e6b9d4158f128515517ce5d5ad97126`; the committed `official-quality` archive retains first exposure, raw runtime/scoring identities and compressed scoring projection. Known identities are rejected as fresh final even after experiment renaming. Old archives remain immutable. Clearing a boolean cannot make exposed labels fresh.

Existing hashing retrieval, paired-policy and abstention commands explicitly classify their outputs as synthetic diagnostics, `qualifying=false`, and demote efficacy verdicts. Direct seal and claim-integrity validation reject bare `final_labels_opened=true`; a typed local receipt also cannot qualify synthetic declarations. Missing/unclassified qualifying contexts fail closed. This slice adds no real empirical adapter.

Verification covers tamper/path/count/membership errors, all-pair leakage, label/provenance/time contradictions, runtime injection, deterministic freezes, byte drift, persistence/race/replay, actual legacy consumer classifications, supported-claim rejection and the actual archived exposure identities. Source-bound fixture execution evidence is archived separately after a clean candidate is committed; later evidence documentation does not become the measured source. Current-head CI remains separate from fixture execution and from previous measured official-quality outcomes.

## Actual-score consumer

New preparations include a digest-bound calibration-only label projection. The
[actual-score consumer](calibration-consumer.md) fits the existing threshold rule
from actual production raw rankings and evaluates a frozen final partition only
after durable candidate/run and final-access intents. This additive engineering
path does not authenticate data declarations or qualify E2/E3. Existing freezes
without a projection remain valid for diagnostic controls but cannot be fitted by
the new consumer. Preparation still inspects complete authored labels.


### Frozen grouped protocol and confidence

Preregistration may include `statistical_protocol` with schema
`magicite/grouped-evaluation-protocol/1`. Preparation freezes connected source
groups and slice membership separately from final labels. The consumer fits
weighted isotonic PAV to calibration raw top1 correctness and top1-minus-top2
margin (singleton: top1 score); this is separate from answerability thresholds.
Version 1 artifacts retain null probability. Version 2 binds the complete
probability map and request depth. Errors, abstentions, composition failures and
mismatched depth never publish usable-result confidence.

`fit-calibration` and `evaluate-frozen-calibration` accept optional
`--statistical-protocol` and `--comparison-budget` files only when they exactly
match frozen preregistration. Budgeted artifacts are evaluation-only and
protected admission rejects them. Dense, sparse, trigger and hybrid comparison
routes share declared pre-scoring source ceilings; measured visited/scored,
fetched, truncation and unused allowance remain distinct. SQLite count queries
and engine operations are not bounded by the source row allowance.

Grouped paired inference uses query-weighted complete-group bootstrap with
10,000 seeded resamples, descriptive weight concentration, conservative bounded
group inferiority p-values and the existing Holm family. Target-specific grouped
abstention adds Hoeffding guards; all-zero errors do not imply certainty.
Independent Wilson requires one justified independent case per group. Missing
independence evidence, fewer than 30 applicable groups, or absent frozen power
support leaves inference inconclusive.

`python -m magicite.eval plan-grouped-power --input DEVELOPMENT.json
--freeze PRE_POWER_FREEZE.json --project-root PROJECT --model-cache CACHE
--model-manifest MODEL.json --group-floors 30,60,120 --repetitions 200
--seed 7 --output POWER.json`
runs the actual 10,000-resample primary test on empirical development-group
simulation draws. Input is a `magicite/development-power-input/1` envelope produced by
`calibration_consumer.prepare_power_input` from saved actual candidate/incumbent
raw traces and preparation-materialized DEVELOPMENT labels/groups. The CLI
revalidates live source, model bytes, custody, snapshot, per-arm policy/config,
query context and common budget before and after simulations.
Preregistration separately freezes `power_assumptions` (typed
`development_representative` and referenced `evidence_digests`). A one-group
fixture cannot manufacture the 30-original-group floor.
The planning protocol frame has `power_plan=null`; the generated report is then
archived in preregistration for a new freeze before fitting. Its current-source
model/config/budget/policy pair, DEVELOPMENT group projection and protocol frame
must match later evaluation. This avoids a circular report/projection digest.
Unbound pure mathematical API calls are explicitly diagnostic only. The entire grid is declared before execution; simultaneous
Monte Carlo lower bounds use Bonferroni across it. This can be computationally
expensive. Archive the generated report and its SHA256 in preregistration before
calibration/final. Simulated groups and resamples are never extra authentic data.
Diagnostic reduced runs cannot support a floor.

Final ECE reports ten frozen equal-width bins for all error-free nonempty raw
top1 proposals and separately for usable selections; null/error exclusions and
empty bins remain visible. ECE has no release PASS threshold. All outputs remain
`qualifying=false`, with E2/E3 `UNEVALUATED`: declarations, synthetic custody
fixtures, self-hashes and local numeric results never authorize release or
production calibration. Fresh authentic data and protected benchmark orchestration
remain later named milestones.
