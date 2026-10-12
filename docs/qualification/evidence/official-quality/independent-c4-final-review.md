# Independent measured-C4 acceptance

All ten bounded engineering criteria pass for measured commit `389834606e6b9d4158f128515517ce5d5ad97126`. C3 `b752eab99e22950df5c9f6d3c3dc56b3c11c2970` remains the actual pool/embedding/admission producer.

All 8,784 outcomes were checked independently: 4,392 unique queries per policy, complete 6,006-record test pools, exact runtime text, finite ranks/scores, selected-content identities, policy/config/model/registry/snapshot bindings, commands/PIDs and durable budgets. Both arms selected every query, with no operational errors, timeouts, abstentions or observed exclusion violations. All 64,536 copied pool-file hashes, 151 source inputs and 13 model-material hashes were rechecked. Exact-C4 archived verification exited 0; all six exact-head hosted CI checks succeeded.

Both policies returned the same ordered rankings, but their raw scores differ on every query. Each recorded 2,011 correct first selections out of 4,392 (45.7878%). Recall@5 is 48.0343%, Recall@10 53.2559%, MRR@10 0.537718 and linear-gain NDCG@10 0.466259. Independent per-query recomputation matches the report. Identical per-query metric vectors imply zero paired differences for every resample; the reported seed-0, 10,000-resample intervals are descriptive query-row intervals, not independent-group inference.

Actual query-time totals were 1,055.908 seconds dense and 1,099.324 seconds adaptive. These local observations do not qualify E6. Thresholds stayed at 0/0; confidence, ECE and no-match false-selection rates remain unavailable. No quality tuning or release threshold pass is inferred.

The prior failures, incomplete C3 smoke and separate P01/P02 controls remain preserved. Simulated evaluation review is not human security approval or source-skill safety/license authentication. E3, E6, broader release obligations and GA remain unqualified. Any evidence-only successor requires a separate archive/source-equivalence/current-head CI review and must retain the measured-C4 attribution.

Machine acceptance, ten criterion bases and evidence hashes: `independent-c4-final-review.json`. No checker model queries were run.
