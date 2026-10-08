# Prospective interpretation C01 — sampler identity and operational recovery

This note clarifies the frozen v1-official-quality plan before any live embedding, smoke or test prediction. It does not alter the plan, its ten criteria, thresholds, sample rule or corpus. Frozen criteria SHA256: b32ab8f7897923e65be87c00c31d11691f4ebdcc94602479ec41c0f33acd8d86.

## Query identity
`query_id` in the sampler is the exact query_id field of the accepted converted runtime-query manifest, including its `train:` namespace. It is not the unprefixed raw upstream query.id. This follows the plan's runtime-input interface and frozen source-to-runtime mapping. `original_id` is the preserved upstream grouping value in the separate scoring/provenance mapping; it is not inferred from the runtime prefix or query text.

Select64 original_id groups by SHA256 of UTF8(seed + U+0000 + original_id), where seed is the literal `magicite-official-quality-v1`, with original_id as the lexical tie-break. Within each selected group select the runtime query_id with smallest SHA256 of UTF8(seed + U+0000 + query_id), with that same runtime query_id as the lexical tie-break. Freeze exact chosen runtime IDs, upstream-ID mapping, original_id membership and input hashes before smoke. Query text and empty compatibility context remain unchanged; no grouping or label metadata enters runtime queries. The checker can independently derive the same64 IDs from the accepted corpus without any prediction outcome.

## Timeout, interruption and budget accounting
A genuine120-second query timeout is a completed error outcome and is not retried for a better result. Reaching the cumulative phase budget is a distinct operational exhaustion event; do not mislabel a shorter remaining-budget stop as a120-second timeout. An interrupted attempt without a completed durable outcome remains explicitly unfinished and can resume only under identical frozen bindings.

If the final elapsed time of an interrupted attempt is unknown, a conservative budget charge is allowed: charge its allocated upper time bound (at most120 seconds, or the smaller remaining-phase allocation). Record this as an estimated/conservative budget charge with actual elapsed unknown, never as a measured latency. Retain measured charges for completed attempts and do not reset cumulative budgets on restart. This may stop work earlier but cannot make an over-budget run appear within budget.

A fully fsynced, valid outcome is completed even if its checkpoint update did not finish. On recovery verify its exact run/model/pool/policy/query bindings and unique identity, recover the checkpoint from that durable row and do not rerun it. Conflicting or duplicate outcomes fail validation; no selective deletion or best-result selection. Reuse only complete, verified input-bound snapshots. Interrupted builds remain incomplete and cannot produce reduced-pool readiness.

No new acceptance requirement or waiver is introduced. Immutable plan/envelope and criteria remain unchanged.
