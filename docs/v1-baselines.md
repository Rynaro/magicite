# v1 production baseline dispatch

The production router now accepts these explicitly experimental policies:

| Policy | Candidate sources | Selection score |
| --- | --- | --- |
| `experimental/sparse-v1` | FTS5 BM25 | Negated raw SQLite BM25 score |
| `experimental/trigger-v1` | Positive triggers and exact symbols | Raw trigger score |
| `experimental/hybrid-rrf-v1` | Dense cosine and sparse BM25 ranks | Sum of `1 / (60 + rank)` across sources |

`dense-v1` remains the stable default. The existing adaptive policy remains
experimental. No stable activation privileges or frozen incumbent exemptions
are extended to these baselines. Set `routing_policy` explicitly to use one.

These paths use the common live eligibility/trust boundary before candidate
scoring and the common reranker, abstention and composition finalizer. One
published catalog generation supplies both candidate scores and decision
identity. Missing generations, stale projections/body/revision/assets, model
identity or vector mismatches, and unavailable sparse capability fail closed.
There is no implicit dense or live-index fallback. On reranker failure with a
configured fallback, the retained baseline slate is named as the actual
fallback mechanism.

Source limits remain 100 hits and 1000 scanned rows, with bounded route refill.
Policy fingerprints bind source sets, raw-versus-RRF semantics, RRF constant,
fixed budgets and route selection settings. Decision diagnostics preserve raw
component scores and ranks, without rounding away small BM25 distinctions.
Truncations describe unexamined source tails (including denied sparse matches),
source result limits, fused output and final output limits. Sparse scan budgets
include denied hits; dense/trigger budgets count eligible entries. Comparisons
must account for this distinction. Dense incumbent scans its full live eligible
pool, so an identical-budget E2 comparison remains outstanding.

BM25, trigger and reciprocal-rank scores are neither cosine nor probabilities.
Existing configured score/margin thresholds are unchanged; their numeric scale
is policy dependent. Confidence remains null without authenticated calibration
matching the exact policy and configuration. No safe no-match threshold is
claimed.

This closure is **structural production dispatch evidence**, verified with
deterministic fixture embeddings and test custody adapters. It does not qualify
empirical E2 comparisons, E3 calibration/statistics, original v1 GA, or reference
E6 performance. Existing official-test exposure remains historical: there is
no rerun, retuning, new dataset or model acquisition in this change. Published
RC1 remains unchanged and does not contain these additions.
