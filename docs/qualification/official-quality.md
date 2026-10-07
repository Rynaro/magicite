# Official fixed-policy retrieval observation

This slice observes actual `dense-v1` and `experimental/adaptive-blend-v1` routing on the accepted complete SkillRet v1.1 conversion. It adds no policy fit, default activation or calibration. `ActualRouter` retains its default depth5; the official observer explicitly requests10. Adaptive blend is not hybrid-RRF.

Before any embedding/query compute, commit a clean candidate and freeze accepted corpus hashes, expected14-file model inventory, installed library versions, source inputs, exact configurations, metrics and operational bounds. The model pin describes self-observed downloaded bytes, not authenticated publisher provenance. Runtime inputs contain only original query text, namespaced query ID and compatibility context. Labels are used separately for deterministic train-group bookkeeping and post-prediction scoring.

The smoke sample is exactly64 train queries: sort original_id groups by SHA256(`magicite-official-quality-v1` + NUL + upstream original_id), choose first64, then choose each group's smallest hashed converted runtime query_id, including `train:`. Group variants and memberships are retained; original_id does not prove statistical independence. Both arms use all10,123 train candidates. The fixed final observation uses all4,392 test queries in lexical runtime-ID order and exactly6,006 test candidates.

The runner uses one supported bulk register operation and existing digest-bound review API under a batch writer lease. Custody is explicitly simulated for isolated evaluation, not deployment trust or proof that source skills/assets execute. Each worker receives a fresh byte-verified admitted snapshot clone with identical registry, authority, vector/graph and configuration inputs. New-process/cache and ephemeral-state resets are disclosed; outcomes and cumulative budgets remain intact across restart. No source assets are fetched or executed. The accepted corpus's1,978 original strict-import failures and3,642 detected unavailable relative references remain limitations.

Example with supported dependencies, from the candidate checkout:

```sh
python scripts/qualify_official_quality.py --freeze \
  --experiment /tmp/official-quality-run \
  --corpus /path/to/accepted/replay-1 \
  --model-manifest /path/to/expected-model.json --model-cache /path/to/model-cache
python scripts/qualify_official_quality.py --prepare --experiment /tmp/official-quality-run
python scripts/qualify_official_quality.py --phase smoke --experiment /tmp/official-quality-run
```

An independent checker must reconcile the frozen candidate, complete pools, model/configuration and smoke before final test. Its authorization JSON must carry `checker:"vigil"`, `verdict:"ACCEPTED"`, source commit, experiment SHA256, smoke-report SHA256 and test-snapshot SHA256. This is a manually verified engineering handoff, not a Gauge or human release receipt.

```sh
python scripts/qualify_official_quality.py --phase test \
  --experiment /tmp/official-quality-run --authorization /path/to/test-authorization.json
python scripts/qualify_official_quality.py --verify --experiment /tmp/official-quality-run
```

Each query has a120-second hard deadline; each arm has a cumulative two-hour query budget, separate from the two-hour full-pool build budget. Progress is emitted at least every30 seconds while awaiting work. Outcomes are appended, flushed and fsynced; atomic durable checkpoints bind source/input/policy, row hashes and spent budget. A fully durable result preceding checkpoint update is completed and recovered without reranking. A genuine timeout is terminal. Unknown interrupted elapsed is a disclosed allocated upper-bound estimate, not measured latency. A phase stops incomplete when less than a full120-second allocation remains. Changed bindings, duplicate or reordered completed rows and concurrent resumes are rejected. Source or setting changes after test exposure create a separate experiment and retain the exposure history; no retry-until-quality-pass.

Primary decision Hit@1 counts a correct actual first selected ID divided by every requested query; abstention, missing outcome, errors and timeouts are misses. Rank-only Hit@1, macro Recall5/10, truncated MRR10 and linear-gain NDCG10 describe the actually retained candidate slate. No unobserved pre-abstention ranking is inferred. Selection coverage uses the full denominator; selective accuracy is null when nothing is selected. Excluded returned candidates are reported as safety violations even when not selected. Reports are recomputed from raw outcomes, without running final queries again.

Paired differences use10,000 seed0 query-row bootstrap resamples with descriptive95% percentile intervals. These are not independent-group E3 intervals. No-match false-selection rates, calibrated confidence/ECE, valid negative calibration, three-partition/power/critical-group/Holm inference and hybrid baselines remain unavailable. Operational timings do not qualify E6 reference hardware/performance. Complete engineering evidence may coexist with poor observed relevance. No E3/E6, original repository/license authenticity, host usefulness, external/operator, human security or GA promotion is implied. Large datasets, models, snapshots and complete raw observation packets remain task-owned outside Git; prior packets are immutable.

Imported procedure lists can restart visible numbering. Storage uses occurrence
keys only for duplicate labels, preserving all parsed text, counters and faults;
unique labels retain their existing keys. Source labels, parser/lint behavior and
exported bodies remain unchanged. Explicit isolated evaluation review does not
assert original-skill safety or deployment approval.
