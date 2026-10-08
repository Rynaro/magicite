# Calibration-data controls

This workflow validates companion data packets and demonstrates local freeze/access controls. It performs no model calls, ranking, calibration fitting, save or activation. Structural control success leaves authentic annotation, independent groups, no-match judgments, project-temporal history, fresh final, E2, E3 and E6 readiness **UNEVALUATED**. No qualifying dataset was supplied for this slice.

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
