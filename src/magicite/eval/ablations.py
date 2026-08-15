"""The three M6-shipped ablation switches (spec §7.3: "Ablations: no-decay,
no-tag-capture, no-communities | ship as config switches | driven from
magicite.toml"). docs/07 §Phase 2 (Online Learning): each switch turns off
one design commitment and reports the Hit@k/MRR delta against the real,
unmodified baseline (d) (``core/router.py::route()``).

**P0 is never touched.** ``no_decay`` and ``no_communities`` are genuine
``Config`` overrides consulted by real code (``core/decay_math.py``,
``core/router.py``) -- flipping them for a bench run is a safe,
read-mostly simulation, exactly like any other ``Config`` field. **
``no_tag_capture`` is different on purpose**: it asks "what if learning
happened synchronously on the hot path, bypassing Dream's two-phase
commit / spacing / saturation / per-tier caps entirely" -- precisely what
P0 (spec §6.2, G1-G3) forbids the live server from ever doing. This
ablation is therefore simulated **entirely inside this module**: no
``Config`` flag anywhere is read by ``core/signals.py``/``core/router.py``
to produce it, so P0 stays an absolute, mechanically-enforced guarantee
in the real server regardless of what this bench-only function computes.

Three docs/07-stubbed ablations (no-inhibition, no-behavioural-tagging,
no-body) are deliberately **not** implemented here -- spec §7.3 marks
them "stubs | switches exist, no bench fixtures in v1" and this milestone
does not build fixtures for them (see the M6 report's deferrals).
"""

from __future__ import annotations

import dataclasses
import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Any

from magicite.config import Config
from magicite.embeddings.base import Embedder
from magicite.eval import bench as bench_mod
from magicite.eval import metrics as metrics_mod

ABLATION_NAMES: tuple[str, ...] = ("no_decay", "no_communities", "no_tag_capture")


@dataclass(frozen=True)
class AblationResult:
    name: str
    hypothesis: str
    baseline_d: metrics_mod.RankingReport
    ablated: metrics_mod.RankingReport
    delta_hit_at_3: float
    delta_mrr: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "hypothesis": self.hypothesis,
            "baseline_d": self.baseline_d.to_dict(),
            "ablated": self.ablated.to_dict(),
            "delta_hit_at_3": round(self.delta_hit_at_3, 4),
            "delta_mrr": round(self.delta_mrr, 4),
        }


def run_no_decay(
    cfg: Config,
    conn: sqlite3.Connection,
    embedder: Embedder,
    queries: list[bench_mod.LabelledQuery],
    *,
    k: int = 5,
) -> AblationResult:
    """``lambda_R = lambda_S = 0`` (docs/07 §Phase 2 table row 1).
    Hypothesis: "without forgetting, old signals dominate; accuracy
    plateaus or degrades." On a registry with no accumulated R/S at all
    (a freshly synced toy registry, never Dream'd), this switch has
    nothing yet to *not* decay -- an honest null result, not a bug; the
    switch is real and takes effect the moment R/S are non-zero."""
    baseline = bench_mod.run_baseline(cfg, conn, embedder, "d", queries, k=k).ranking
    ablated_cfg = dataclasses.replace(cfg, lambda_r_per_day=0.0, lambda_s_per_day=0.0)
    ablated = bench_mod.run_baseline(ablated_cfg, conn, embedder, "d", queries, k=k).ranking
    return AblationResult(
        name="no_decay",
        hypothesis="docs/07: without forgetting, old signals dominate; accuracy plateaus or degrades.",
        baseline_d=baseline,
        ablated=ablated,
        delta_hit_at_3=ablated.hit_at_3 - baseline.hit_at_3,
        delta_mrr=ablated.mrr - baseline.mrr,
    )


def run_no_communities(
    cfg: Config,
    conn: sqlite3.Connection,
    embedder: Embedder,
    queries: list[bench_mod.LabelledQuery],
    *,
    k: int = 5,
) -> AblationResult:
    """spec §3.3 step 8's community rerank, disabled (docs/07 §Phase 2
    table row 3, H-SCALE). ``cfg.ablation_no_communities`` is read by
    ``core/router.py::route()`` directly -- a real, additive Config
    switch, not a bench-only reimplementation."""
    baseline = bench_mod.run_baseline(cfg, conn, embedder, "d", queries, k=k).ranking
    ablated_cfg = dataclasses.replace(cfg, ablation_no_communities=True)
    ablated = bench_mod.run_baseline(ablated_cfg, conn, embedder, "d", queries, k=k).ranking
    return AblationResult(
        name="no_communities",
        hypothesis=(
            "docs/07 H-SCALE: without hierarchy, Hit@k vs registry size degrades "
            "logarithmically instead of sub-logarithmically/flat."
        ),
        baseline_d=baseline,
        ablated=ablated,
        delta_hit_at_3=ablated.hit_at_3 - baseline.hit_at_3,
        delta_mrr=ablated.mrr - baseline.mrr,
    )


def _train_test_split(
    queries: list[bench_mod.LabelledQuery], *, test_fraction: float = 0.4
) -> tuple[list[bench_mod.LabelledQuery], list[bench_mod.LabelledQuery]]:
    """A deterministic (no RNG/seed state, reproducible across runs and
    processes) 60/40 train/test split (docs/07 §Experimental Protocol),
    keyed on a stable hash of the query text rather than list position --
    so it is not an artifact of ``queries.jsonl``'s own grouping order."""
    train: list[bench_mod.LabelledQuery] = []
    test: list[bench_mod.LabelledQuery] = []
    cutoff = int(test_fraction * 100)
    for q in queries:
        digest = hashlib.sha256(q.query.encode("utf-8")).hexdigest()
        bucket = int(digest, 16) % 100
        (test if bucket < cutoff else train).append(q)
    return train, test


def run_no_tag_capture(
    cfg: Config,
    conn: sqlite3.Connection,
    embedder: Embedder,
    queries: list[bench_mod.LabelledQuery],
    *,
    k: int = 5,
) -> AblationResult:
    """docs/07 §Phase 2 table row 2: "No tag-capture: Learn on hot path
    (every signal)... Noisy single-session signals -> instability,
    overfitting." Simulated entirely offline (see module docstring): a
    plain, deterministic 60/40 split; every TRAIN query's expected engram
    receives a full, **uncapped** ``eta`` score bump per occurrence --
    modelling direct, synchronous hot-path weight movement with none of
    Dream's defenses (no metaplastic saturation ceiling, no spacing
    damping, no per-tier weight cap, no two-phase commit). Frequently
    -labelled skills accumulate an unbounded bump and can come to
    dominate TEST queries they have no genuine relevance to -- the
    concrete, measurable form of "instability/overfitting" this ablation
    is meant to surface. Compared against the real, gated baseline (d) on
    the *same* test split.
    """
    train, test = _train_test_split(queries)
    baseline = bench_mod.run_baseline(cfg, conn, embedder, "d", test, k=k).ranking

    bumps: dict[str, float] = {}
    for q in train:
        bumps[q.expected_top1] = bumps.get(q.expected_top1, 0.0) + cfg.eta  # no w_max ceiling, ever

    ranking_pairs: list[tuple[list[str], str]] = []
    for q in test:
        ranked = bench_mod._baseline_b_rank(conn, embedder, q.query)  # cosine-only starting order
        boosted = sorted(
            range(len(ranked)), key=lambda i: (-bumps.get(ranked[i], 0.0), i)
        )
        ranking_pairs.append(([ranked[i] for i in boosted], q.expected_top1))
    ablated = metrics_mod.aggregate_ranking(ranking_pairs)

    return AblationResult(
        name="no_tag_capture",
        hypothesis=(
            "docs/07: learning on the hot path (no two-phase commit, no saturation, "
            "no spacing) -> noisy single-session signals cause instability/overfitting "
            "(frequently-labelled skills come to dominate unrelated queries)."
        ),
        baseline_d=baseline,
        ablated=ablated,
        delta_hit_at_3=ablated.hit_at_3 - baseline.hit_at_3,
        delta_mrr=ablated.mrr - baseline.mrr,
    )


def run_all(
    cfg: Config,
    conn: sqlite3.Connection,
    embedder: Embedder,
    queries: list[bench_mod.LabelledQuery],
    *,
    k: int = 5,
) -> dict[str, AblationResult]:
    return {
        "no_decay": run_no_decay(cfg, conn, embedder, queries, k=k),
        "no_communities": run_no_communities(cfg, conn, embedder, queries, k=k),
        "no_tag_capture": run_no_tag_capture(cfg, conn, embedder, queries, k=k),
    }
