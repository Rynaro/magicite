# Evaluation contract and preregistration defaults

These are proposed V1 decision rules, not observed performance. S01 implements schemas/validators; S07 fits and compares candidate policies; S14 produces release-scale evidence; S16 verifies completeness. Changes after opening final labels invalidate that run for promotion and require a new frozen experiment and holdout.

## E1 — Immutable evidence chain

Each `ExperimentManifest/1` records experiment ID, hypothesis, reversal condition, source commit and dirty-tree digest (release runs require clean tree), runner/dependency lock hashes, corpus/labels/split hashes, label author/reviewer provenance, dataset revision/license, policies and config hashes, embedder/model/tokenizer artifacts, seeds, device/OS/filesystem, warmup/cache conditions, primary metric, secondary metrics, subgroup definitions, sample/power plan, statistical method, thresholds, timestamp and supersedes IDs.

`CorpusManifest/1` lists immutable artifact/query/label files with SHA-256 and counts derived from bytes. Query records include query ID, split, relevance judgments, compatibility context, negative/ambiguous labels and provenance; no runtime-derived “gold.” Where several answers/plans are valid, store accepted sets and partial orders. Deduplicate by normalized content identity as well as ID. Split related queries by source task/repository/session group to prevent near-duplicate leakage; preserve a temporal final holdout for project routing.

`Prediction/1` stores query ID, exact ordered candidate IDs, selection/abstention, policy/calibration IDs, compatibility exclusions, per-stage timing and task-verifier result if applicable. `ResultManifest/1` references all per-query predictions and the experiment hash; aggregates must be recomputable from them. Missing/null measures remain unevaluated. Invalid/skipped cases carry reasons and are not silently removed from denominators.

`Claim/1`: claim ID, text location, exact metric/number/unit, population/split, result digest, confidence interval, evidence class (`historical|synthetic|structural|retrieval|host-task|external`), status (`supported|failed|inconclusive|superseded`), limitations and superseding claim. Validators reject mismatched numbers, missing raw predictions and structural-to-efficacy relabeling. Historical v0.3 artifacts can remain carried-forward with missing reproducibility inputs; they cannot satisfy a new-run gate.

## E2 — Baselines and model selection

Compare dense-only incumbent, sparse-only, trigger-only where applicable, and sparse+dense RRF with identical eligibility filters, candidate budgets, body availability and snapshots. Preserve a legacy graph/plasticity run as a research comparison. Optional rerankers receive the same eligible slate and declared resource budget. Select the strongest simple safe incumbent on development data, then freeze it before final testing. Dense is the starting policy, not a privileged mandatory final default; sparse-only or trigger-only may become the operational incumbent if they pass the same safety/abstention gates. S07 mandatory activation machinery owns this choice independently of S10. This prevents selecting a weak baseline after seeing candidate results.

Use three partitions: development (algorithm choices), calibration (confidence/thresholds), final evaluation (promotion decision). Imported official splits retain their meaning; do not train/tune on SkillRet evaluation skills or queries. Pin the [official SkillRet source linked by v3](https://arxiv.org/abs/2605.05726v3), dataset commit, hashes and license at acquisition. Do not infer its current item counts from the dossier. CI uses a tiny offline adapter fixture, while GA evidence uses the declared complete official evaluation split or clearly reports any unavailable cases.

Primary retrieval metric is Hit@1 across all eligible labeled queries; abstentions count as misses. Report Recall@5/10, MRR, NDCG@10, selective accuracy, coverage, false-selection rate on no-match queries, score-margin distribution and ECE where probabilities are actually calibrated. Safety exclusion violations are counted separately and must be zero; better relevance cannot compensate for a permission violation.

## E3 — Statistical release and promotion gates

[DECISION] Default retrieval noninferiority margin is 0.02 absolute Hit@1. Compute paired query-group bootstrap with 10,000 seeded resamples and percentile 95% CI on candidate-minus-incumbent; resample the original grouping unit (task/repository/session), never correlated individual variants. Pass noninferiority only if lower CI >= -0.02. Improvement claims require lower CI > 0 and point improvement >= 0.01. Report group count and effective independent sample count. These margins are local release choices, not evidence from cited papers.

A priori sample size uses development variance for 80% power at one-sided alpha .025 against the 0.02 margin. Archive calculation, assumptions and fixed sample floor before final evaluation. If available independent samples cannot support the planned test, report inconclusive and keep the frozen incumbent. Do not use optional stopping or repeat final evaluations until a desired CI appears.

Predeclare slices for language/framework, host/platform, rare exact terms, long-body skills, popular versus rare skills, unseen/changed skill versions, required unknown context and no-match queries. Each hard safety slice must have complete fixture coverage. Empirical slices need at least 30 independent groups for a separately reported interval; below that, report insufficient evidence and collect data. For preregistered critical empirical slices, lower paired bound must be >= -0.05; apply Holm correction across their inferiority tests. All final overall/critical-slice gates must pass for hybrid promotion. Other exploratory subgroups remain descriptive, never hidden.

Abstention gate: on separately locked no-match queries, the one-sided 95% upper confidence bound on false selections must be <= 0.05; on answerable queries, the lower 95% coverage bound must be >= 0.80. Select thresholds on calibration only. Use binomial Wilson bounds for independent cases or predeclared grouped bootstrap bounds for grouped cases. No calibration artifact → confidence null; do not report calibration success. The selected simple policy may remain the final default with an independently fitted abstention rule if hybrid does not pass.

Promotion reversal: any trust/compatibility violation reverses candidate eligibility; failed/inconclusive paired quality gate keeps incumbent. No algorithm complexity quota: V1 may ship hybrid as an opt-in candidate if dense remains strongest under the same filters and abstention policy.

## E4 — Composition and useful task outcomes

Keep four distinct measurements: selected skill/set correctness; typed structural/partial-order validity; executable-plan rate in a host; verified end-task pass rate. A plan with prerequisites omitted, cycles, conflicts, or exhausted bounds is invalid regardless of task luck. Structural corpus labels are authored independently of the planner and include multiple accepted topological orders.

Required fixture categories: empty selection, independent steps, shared dependency, deep chain, branching, ambiguity, conflicting producers, versioned capability mismatch, unknown host tools, quarantined/archived/excluded dependency, cycle, dangling reference, node/edge/depth exhaustion, changed content after route, unsafe asset and malicious instruction text. Planner runtime never executes the procedure.

Host evaluation uses isolated, disposable fixtures with fixed repositories/models/toolsets and deterministic verifiers. Compare paired no-skill, selected-skill and composed-plan arms on the same tasks/seeds. Publish costs, timeouts and failures, with intention-to-test denominator. At least one independently authored host-task corpus must yield actual execution evidence for GA. The dossier does not justify claiming universal usefulness: a positive usefulness claim requires a paired 95% lower bound >0; absence of benefit can still support a bounded structural feature if documented and all safe-execution gates pass. A claim that composition improves tasks must never rely on the 24-case structural corpus alone.

## E5 — Learning research, optional for GA

For optional adaptive promotion, require candidate-slate behavior policy probabilities, outcome provenance, temporal holdout, partition compatibility and exposure diagnostics. Deterministic logged actions cannot identify unsupported alternate actions. IPS/SNIPS or doubly robust methods require stated assumptions, overlap, weight clipping bias report and effective sample size; no valid support returns `insufficient_support`.

Preregister minimum effective sample size based on the same power method, plus worst-slice/popularity concentration limits before canary. New candidate versus incumbent must pass E3 primary and critical-slice rules on eligible externally verified outcomes. Self-reported or inferred signals are sensitivity analyses, not substitutes for verifier evidence. Promotion is a reviewed operation, never automatic from a point estimate. Canary policy follows C7. Poisoning, delayed feedback, distribution shift and popular-skill collapse are required rejection/rollback tests.

## E6 — Performance profiles and support envelope

[DECISION] Initial target reference runner: dedicated Linux amd64, 4 CPU cores, 16 GiB RAM, local SSD, no GPU; record exact CPU, OS, dependency versions and model digest. A separate Linux arm64 runner records architecture results. Do not extrapolate synthetic hashing results into production FastEmbed latency. Default production model is the repository's configured FastEmbed model pinned to actual artifact digest during S01; any change creates a new experiment.

| Profile | Corpus | Initial target, to be accepted before run |
|---|---|---|
| Small | 100 / 1,000 artifacts | Warm end-to-end route p95 <=250 ms; process RSS <=1 GiB |
| Supported local scale | 10,000 artifacts | Warm end-to-end route p95 <=1 s; p99 <=2 s; RSS <=2 GiB; index <=2 GiB |
| Exploratory scale | 50,000 artifacts | Measure all envelopes; no GA support claim until an amended frozen budget passes |

These budgets replace any implied universal 100 ms guarantee. End-to-end route includes query embedding, candidate generation, eligibility, configured reranking, composition and serialization. Cold startup/model loading and ingestion/index construction have separate measured envelopes; acquisition/download time is explicit and excluded from offline latency. Cold process ready-to-first-route target <=30 s with model bytes cached; 10k initial index build target <=30 min and peak RSS <=4 GiB on the reference runner. If targets fail, optimize or amend support scope before a fresh preregistered run; never quietly rename a failed gate.

Per profile, run at least 1,000 measured distinct/rotated queries after 50 declared warmups; report repeated-query hot-cache separately. Run three clean-process repetitions, report distributions and between-run variation. Publish p50/p95/p99, peak RSS, artifacts/body bytes, embedding dimensions, index/disk bytes, token payload, build time, cache hit rates and every truncation/fallback. Synthetic 10k plus a real 10k licensed corpus are both required for a 10k support claim. Dedicated runner gates budgets; shared CI gates semantic equality and manifest completeness.
