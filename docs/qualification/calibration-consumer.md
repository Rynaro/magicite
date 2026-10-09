# Actual-score calibration consumer

This offline consumer closes the engineering score → fit → frozen-final path.
It does not activate a calibration or routing policy. E2/E3 and authentic statistical
readiness remain **UNEVALUATED**. Deterministic test vectors validate mechanics;
they are not production-model measurements or authentic annotation evidence.

## Prepare once

Follow [calibration data preparation](calibration-data.md) to author and freeze a
packet with calibration and final partitions. Preparation authors inspect the
complete labels. A new freeze materializes a digest-bound calibration-only
projection; older freezes without that projection cannot use this consumer.
Fit hashes all frozen inputs but does not semantically decode final labels.

Use an already provisioned project, admitted registry/index, installed local model,
and its observed model manifest (`freeze_model` from `magicite.eval.production`).
The manifest records actual local model bytes and dependency versions; its digest
provides integrity, not publisher or custody authentication. No model is downloaded
by these commands. Fixture cache bytes must never be supplied as real model evidence.

## Fit, then evaluate

```sh
python -m magicite.eval fit-calibration \
  --freeze /absolute/path/freeze.json \
  --project-root /absolute/path/project \
  --model-cache /absolute/path/model-cache \
  --model-manifest /absolute/path/observed-model.json \
  --rank-depth 5 --output /absolute/path/candidate.json

python -m magicite.eval evaluate-frozen-calibration \
  --freeze /absolute/path/freeze.json \
  --project-root /absolute/path/project \
  --model-cache /absolute/path/model-cache \
  --model-manifest /absolute/path/observed-model.json \
  --rank-depth 5 --candidate /absolute/path/candidate.json \
  --output /absolute/path/result.json
```

The adapter invokes the actual production router. Its internal trace preserves
ordered eligible scores before threshold abstention while public MCP selection
stays unchanged. Scores retain their policy-specific units, including sparse
scores above one; they are not probabilities. The existing threshold-margin rule
fits calibration labels only. Empty slates are counted but do not become invented
scalar observations. An entirely empty fit fails explicitly. Operational errors
and nonfinite scores fail calibration rather than silently disappearing.

A candidate binds source bytes, observed model bytes/libraries, protected policy
and trust state, configuration, registry/index/snapshot/schema/tokenizer identity,
rank depth, and all annotation-free query contexts. Drift rejects evaluation.
Candidate identity and fit identity are separate from the base production policy.
The active-loader circular binding is a separate gap; no active store is modified.

Before final labels or actual final queries, the consumer durably publishes a
no-replace run binding and a packet-root owner receipt, then the existing final
access intent. Failure or interruption retains exposure and an incomplete result.
A second fresh run, changed candidate, or refit after exposure is denied. An explicit
`--replay` with the identical candidate and frozen inputs may write a new result
path; it is labeled **exposed replay**, never fresh evidence. Local receipts cannot
prevent out-of-band copying, deletion, rollback, or earlier reads; known exposed
official data cannot be resealed fresh.

Reports retain raw ranking, actual selection, and the offline threshold decision
separately. A threshold allow cannot establish composition readiness for a route
production abstained from, so usable selection is conservatively unconfirmed.
Reports include exact graded ranking, selection and no-match denominators,
empty/error accounting, and partial-run status. Without a frozen statistical
protocol and supported probability fit, confidence and ECE remain null;
no independent-case Wilson interval substitutes for grouped statistical evidence.
Observed calibration maxima do not guarantee abstention on every future query.

Missing projections require preparation of a new freeze before exposure. Changed
inputs require a distinct valid experiment; do not delete receipts or refit an
exposed final partition. Correct prerequisites or inputs before the first final run.


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
