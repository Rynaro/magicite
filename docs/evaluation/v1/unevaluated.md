# UNEVALUATED release evidence (S14)

These items cannot be obtained and run offline in the default sandbox. Status
is **UNEVALUATED** — never PASS, never fabricated metrics. Machine-readable
catalog: `magicite.eval.unevaluated.unevaluated_catalog()`.

Commands below match `python -m magicite.eval --help` / `scripts/run_benchmark_matrix.py --help` exactly.

| Item id | Gate | Operator command |
|---|---|---|
| `skillret-official-final-split` | ROUTING/EXTERNAL | `python -m magicite.eval acquire-skillret --archive /path/to/skillret-official.tar.gz --expected-sha256 <ARCHIVE_SHA256> --license <LICENSE> --revision <GIT_OR_RELEASE_SHA> --corpus-json /path/to/corpus_manifest.json --output artifacts/skillret-acquired-corpus.json && python -m magicite.eval run-retrieval --experiment docs/evaluation/v1/preregistration-template.json --corpus artifacts/skillret-acquired-corpus.json --split final --provider production --output artifacts/skillret-final/` |
| `real-licensed-10k-corpus` | PERFORMANCE | `python scripts/run_benchmark_matrix.py --profile supported-10k --provider production --corpus-manifest /path/to/licensed-10k/corpus_manifest.json --environment-label dedicated-linux-amd64-4c-16g --envelope-mode budget --output artifacts/e6-supported-10k-real.json` |
| `production-provider-e6-budgets` | PERFORMANCE | `python scripts/run_benchmark_matrix.py --profile supported-10k --provider production --calls 1000 --warmup 50 --repetitions 3 --environment-label dedicated-linux-amd64-4c-16g --envelope-mode budget --output artifacts/e6-supported-10k-production.json` |
| `hybrid-rrf-vs-dense-v1-paired` | ROUTING | `python -m magicite.eval run-paired-policies --incumbent dense-v1 --candidate hybrid-rrf-v1 --experiment docs/evaluation/v1/preregistration-template.json --corpus /path/to/locked-final-corpus.json --n-resamples 10000 --seed 0 --output artifacts/hybrid-vs-dense-verdict.json` |
| `empirical-abstention-bounds` | ROUTING | `python -m magicite.eval run-abstention-gate --calibration-split calibration --final-split final --experiment docs/evaluation/v1/preregistration-template.json --corpus /path/to/locked-corpus.json --output artifacts/abstention-gate.json` |
| `host-task-usefulness-corpus` | COMPOSITION | `python -m magicite.eval run-host-tasks --corpus /path/to/host-task-corpus.json --arms no_skill,selected_skill,composed_plan --n-resamples 10000 --seed 0 --output artifacts/host-task-usefulness.json` |
| `exploratory-50k-envelope` | PERFORMANCE | `python scripts/run_benchmark_matrix.py --profile exploratory-50k --provider production --opt-in-exploratory --environment-label dedicated-linux-amd64-4c-16g --output artifacts/e6-exploratory-50k.json` |

Custody note: the benchmark commands above omit `--custody`, so they use the default
`--custody protected`, which requires an enrolled protected benchmark registry (not yet
supported by the matrix) and otherwise exits 2. `--custody disposable-simulated` runs are
never GA-eligible and record `custody.deployment_qualification=UNEVALUATED`; they cannot
satisfy these items.

## Manifest / digest each operator must archive

- SkillRet: `ExperimentManifest/1` + acquired `CorpusManifest/1` digests + `record_external_download` archive SHA-256 (pin arXiv:2605.05726v3).
- E6 budgets: `magicite-benchmark-profile-result/1` with `fingerprint.provider=production` and FastEmbed `model_digest`.
- Hybrid paired: Verdict JSON; on fail/inconclusive call `policy_store.retain_simple_incumbent_evidence`; never `activate()`.
- Host tasks: `Claim/1` with `evidence_class=host-task` bound to paired arm digests.
- Matrix corpus provenance: synthetic runs set `corpus.kind=synthetic` (`ga_eligible=false`); `--corpus-manifest` runs set `corpus.kind=manifest` and measure that registry, but stay `ga_eligible=false` (with `corpus.ga_ineligible_reasons`) unless provider, profile, budget envelope and a non-fixture corpus all qualify.
- Operator commands never emit a release `pass`: any gate that would pass is rewritten to `unevaluated`, with the computed value kept under `harness_computed_status` / `harness_computed_usefulness_status`.

## Holm critical-slice note


## Docs forwards for S15

- Surface this table in operator docs with copy-paste commands.
- Link claims gates so docs cannot cite UNEVALUATED items as PASS.
- Document that hashing provider timings are not production FastEmbed evidence.

S14 audited execution contract: every non-smoke profile defaults to 1,000
measured queries after 50 warmups in three distinct child processes. Result
records contain actual process IDs, counts, per-run latency observations and
between-run extrema. Cold-ready timing includes a fresh serving subprocess
through first serialized route, with an already acquired offline model.
Synthetic skills are full admissible bodies indexed through the registry path.

Real corpora use `CorpusManifest.artifacts` entries with `role: skill` pointing
to `.egr.md` files relative to the manifest. The runner verifies every declared
artifact's SHA-256/length and contained path, admits it with the existing parser,
and imports the complete inventory (including unlabeled distractors). Labels
must resolve to artifact IDs or names. Admission is an explicit, isolated
benchmark review; it does not transfer trust to a user's registry. Query-only
manifests are insufficient and return unavailable, never invented skill bodies.

A single run cannot establish the 10k support envelope. Supply a second
production result with `--support-run artifacts/other-stratum.json` to validate
both real licensed and synthetic strata. Both require complete actual E6
measurements, genuine model artifact hashes, and every budget. Missing data,
nonfinite numbers, placeholders, relocated fixtures and failed repetitions
remain ineligible. Hashing runs establish functional completeness only.

Holm correction requires preregistered inferiority p-values; the API orders
those p-values and applies exact step-down thresholds and adjusted p-values.
CI endpoints alone return inconclusive. Every slice must independently clear
its frozen lower-bound and group-count gate. Host usefulness requires all three
arms paired by task and seed; executed timeouts count zero and missing/unrun
arms block evaluation. False-selection uses the one-sided 95% Wilson upper
bound. Operator harness outputs remain UNEVALUATED, including nested verdicts.
