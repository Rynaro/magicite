# Evaluation V1 schemas (S01)

Immutable evidence-chain schemas implemented under `src/magicite/eval/`.

| Schema ID | Module | Purpose |
|---|---|---|
| `magicite-experiment-manifest/1` | `manifests.ExperimentManifest` | Frozen pins: commit, locks, corpus/label digests, seeds, metrics, thresholds |
| `magicite-corpus-manifest/1` | `manifests.CorpusManifest` | Artifact inventory + query/label records; leakage rejected by validators |
| `magicite-prediction/1` | `manifests.Prediction` | Per-query ordered candidates, selection/abstention, policy ids |
| `magicite-result-manifest/1` | `manifests.ResultManifest` | Prediction digests + recomputable aggregates |
| `magicite-claim/1` | `manifests.Claim` | Published metric bound to result digest + evidence class |

Validators: `magicite.eval.validate`. Digests: `magicite.eval.digests` (SHA-256 over raw bytes or canonical JSON).

Historical v0.3 artifacts under `docs/evaluation/v0.3-*.json` remain addressable and are labeled `evidence_class=historical`; they cannot satisfy a new-run promotion gate.

Offline SkillRet adapter fixture: `fixtures/skillret-tiny/`. Official download is explicit via `magicite.eval.external.record_external_download` (license + digest record only; no auto-fetch).
