# Evaluation V1 (S01 foundation + S14 scale / release evidence)

Immutable evidence-chain schemas live under `src/magicite/eval/`.

| Schema ID | Module | Purpose |
|---|---|---|
| `magicite-experiment-manifest/1` | `manifests.ExperimentManifest` | Frozen pins: commit, locks, corpus/label digests, seeds, metrics, thresholds |
| `magicite-corpus-manifest/1` | `manifests.CorpusManifest` | Artifact inventory + query/label records; leakage rejected by validators |
| `magicite-prediction/1` | `manifests.Prediction` | Per-query ordered candidates, selection/abstention, policy ids |
| `magicite-result-manifest/1` | `manifests.ResultManifest` | Prediction digests + recomputable aggregates |
| `magicite-claim/1` | `manifests.Claim` | Published metric bound to result digest + evidence class |
| `magicite-benchmark-profile-result/1` | `profiles` + `scripts/run_benchmark_matrix.py` | E6 scale profile result with cache states + hardware/model fingerprint |

Validators: `magicite.eval.validate`. Digests: `magicite.eval.digests`.
Release verdicts: `magicite.eval.verdicts` (paired noninferiority / improvement /
abstention / Holm critical slices / usefulness). Envelopes: `magicite.eval.envelopes`
(shared CI = completeness only; dedicated runner = production budgets).

## Integrity rules (S01, preserved)

Historical v0.3 artifacts under `docs/evaluation/v0.3-*.json` remain addressable and
are labeled `evidence_class=historical`; they cannot satisfy a new-run gate.
Structural evidence cannot support efficacy/retrieval metrics. A `status=supported`
claim requires the full evidence chain (experiment, corpus, result, and predictions).

Diagnostic circular Plan F1 (expand()-as-gold) requires an explicit switch and
cannot satisfy `status=supported`.

## Scale profiles (S14 / evaluation.md E6)

| Profile id | Corpus | CI default | GA claim |
|---|---|---|---|
| `ci-smoke` | 100 synthetic | yes | no |
| `small-100` / `small-1k` | 100 / 1k | opt-in dedicated | no |
| `supported-10k` | 10k | dedicated runner | only with production provider **and** real licensed corpus |
| `exploratory-50k` | 50k | `--opt-in-exploratory` | no until amended budget |

```bash
# Shared CI completeness (hashing or production; no budget PASS claim)
python scripts/run_benchmark_matrix.py --profile ci-smoke --provider hashing \
  --envelope-mode completeness --output /tmp/ci-smoke.json

# Dedicated runner production budgets (reference hardware)
python scripts/run_benchmark_matrix.py --profile supported-10k --provider production \
  --calls 1000 --warmup 50 --environment-label dedicated-linux-amd64-4c-16g \
  --envelope-mode budget --output artifacts/e6-supported-10k-production.json
```

## Offline SkillRet adapter

Fixture: `fixtures/skillret-tiny/`. Official download is explicit via
`magicite.eval.external.record_external_download` (license + digest only; no
auto-fetch). Full official final-split execution is **UNEVALUATED** until an
operator acquires the archive — see `magicite.eval.unevaluated.unevaluated_catalog()`
and `docs/evaluation/v1/unevaluated.md`.

## Host-task usefulness (E4)

Paired no-skill / selected-skill / composed-plan arms: `magicite.eval.host_tasks`.
Structural composition evaluation: `magicite.eval.composition_eval` against
`tests/fixtures/composition-v1/`. Structural validity never substitutes for
paired host-verifier usefulness claims (AC-S14-04).

## Promotion policy

No matrix/verdict result silently changes the default policy. Hybrid promotion
requires S07 reviewed activation after a `pass` overall verdict; failed /
inconclusive / unevaluated retains the frozen `dense-v1` incumbent via
`policy_store.retain_simple_incumbent_evidence`.
