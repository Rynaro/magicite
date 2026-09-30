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

## Manifest / digest each operator must archive

- SkillRet: `ExperimentManifest/1` + acquired `CorpusManifest/1` digests + `record_external_download` archive SHA-256 (pin arXiv:2605.05726v3).
- E6 budgets: `magicite-benchmark-profile-result/1` with `fingerprint.provider=production` and FastEmbed `model_digest`.
- Hybrid paired: Verdict JSON; on fail/inconclusive call `policy_store.retain_simple_incumbent_evidence`; never `activate()`.
- Host tasks: `Claim/1` with `evidence_class=host-task` bound to paired arm digests.
- Matrix corpus provenance: synthetic runs set `corpus.kind=synthetic` (`ga_eligible=false`); `--corpus-manifest` runs set `corpus.kind=manifest` and measure that registry.

## Holm critical-slice note

`holm_critical_slice_family` uses family-α percentile CIs with a conservative ordered-reject approximation (does not re-bootstrap at each Holm `alpha_i`). This never turns FAIL into PASS by widening intervals. See `magicite.eval.verdicts.holm_critical_slice_family` docstring.

## Docs forwards for S15

- Surface this table in operator docs with copy-paste commands.
- Link claims gates so docs cannot cite UNEVALUATED items as PASS.
- Document that hashing provider timings are not production FastEmbed evidence.
