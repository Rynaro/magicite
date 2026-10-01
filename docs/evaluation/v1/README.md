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
| `supported-10k` | 10k | dedicated runner | only with production provider **and** real licensed corpus (`corpus.kind=manifest`) |
| `exploratory-50k` | 50k | `--opt-in-exploratory` | no until amended budget |

Synthetic matrix runs always set `corpus.kind=synthetic` and `ga_eligible=false`. Supplying `--corpus-manifest` validates the CorpusManifest, builds/measures that registry, and sets `corpus.kind=manifest`. A manifest run is still `ga_eligible=false` unless it uses the production provider, a profile with `ga_support_claim`, `--envelope-mode budget` with a passing envelope, and a non-fixture licensed corpus at the profile's size; every unmet condition is listed in `corpus.ga_ineligible_reasons`.


The benchmark builds a disposable registry, which cannot hold protected custody enrollment. The default `--custody protected` therefore fails closed. `--custody disposable-simulated` uses an in-process evaluation custodian and explicitly reviews exactly the generated or digest-bound bytes; such runs record `custody.deployment_qualification=UNEVALUATED` and are never GA-eligible. Dedicated-runner GA evidence needs a protected enrolled benchmark registry, which the matrix does not yet support, so those claims remain UNEVALUATED.

```bash
# Shared CI completeness (hashing or production; no budget PASS claim)
python scripts/run_benchmark_matrix.py --profile ci-smoke --provider hashing \
  --envelope-mode completeness --custody disposable-simulated --output /tmp/ci-smoke.json

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

`payload_tokens` uses the repository's pinned lexical word tokenizer, identified
by `fingerprint.payload_tokenizer` (`TOKENIZER_ID`). This is a reproducible
word-token payload measure, not BPE tokens or a host context-capacity claim.
Missing or mixed tokenizer identities cannot qualify a support evidence bundle.
The payload surface is serialized public `RouteOutput/1` JSON; the summary is
the maximum across measured responses. Unit, surface and aggregation are
explicit fingerprint fields and must match across combined evidence.
