"""Baselines a-d runner (spec §1 "eval/bench.py -- baselines a-d runner";
docs/07 §Four Baselines (A-D), §Experimental Protocol; AC-029).

This is the harness that makes Magicite falsifiable (R10, mission
directive): four cumulative baselines over the *same* labelled query set,
so the value each design commitment (body content, graph structure,
learned weights) actually buys can be measured, including a **negative**
result if a later baseline is not actually better. Every baseline reuses
the real production code paths where one exists (baseline (d) *is*
``core/router.py::route()``, unmodified) rather than a bench-only
reimplementation -- so a change to routing behaviour cannot silently
drift out of sync with what the harness measures.

**CR-3 compliance.** Baseline (a) ("native SKILL.md matching (status
quo)") is docs/07's own harness description ("LLM matches query against
all skill descriptions") -- but the v1 server ships no generative model
(CR-3). This module's baseline (a) is a deterministic, offline proxy:
token-overlap over *description-level* fields only (``intent.does``,
``intent.use_when``, positive triggers -- never the body, never an
embedding), which is the honest, LLM-free operationalisation of "native
description-only matching" the corpus's own baseline (b)/(a) contrast
implies (b adds body *content*; a never sees it). No baseline in this
module calls an LLM, an HTTP endpoint, or any generative provider.

Baselines b/c are ablated versions of the real activation/scoring
primitives (``core/activation.py``), not a parallel reimplementation:

- (a) native, description-only lexical overlap (no embeddings, no graph)
- (b) dense embedding cosine only (no graph, no plasticity)
- (c) embedding + **structural** (fixed ``type_gain``, S_edge ignored)
  spreading activation -- "static-weighted graph edges" per docs/07
- (d) the real ``route()``: embedding + activation weighted by *learned*
  ``S_edge`` + hub penalty + context + community rerank

Composition ground truth (Plan F1) is derived once per distinct
``expected_top1`` name from that engram's own declared
``needs``/``composes`` closure (``core/composition.py::expand``, the same
mechanism ``route()`` itself uses) -- an independent, label-driven truth,
not something any baseline gets to define for itself.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import click
import numpy as np

from magicite.config import Config
from magicite.core import activation as activation_mod
from magicite.core import composition as composition_mod
from magicite.core import router as router_mod
from magicite.embeddings.base import Embedder
from magicite.engram.model import ROUTABLE_STATUSES
from magicite.eval import metrics as metrics_mod

BASELINE_NAMES: tuple[str, ...] = ("a", "b", "c", "d")

BASELINE_LABELS: dict[str, str] = {
    "a": "native_skillmd_matching",
    "b": "dense_embedding",
    "c": "embedding_plus_graph",
    "d": "full_magicite",
}

#: docs/07 baseline (c): "static-weighted graph edges (declared only:
#: composes, inhibits, similarity from embeddings)" propagated with
#: **fixed** weights (``type_gain`` only) -- never ``S_edge``, which is a
#: *learned* weight (baseline (d)'s own differentiator). Matches
#: ``core/router.py``'s own structural hub-penalty graph exactly (same
#: reasoning: S_edge starts at 0.0 for every declared edge until Dream
#: potentiates it, spec §2.6 step 4).
_GRAPH_EDGE_TYPES: tuple[str, ...] = ("co_activation", "composes", "depends_on", "similar_to")

#: Baseline (c)'s own fixed activation/similarity split (distinct from
#: ``Config.w_activation``/``w_similarity`` -- those also blend retrieval
#: and excitability, both outcome-driven signals baseline (c) explicitly
#: excludes: "no learned weights, no outcome signals").
_BASELINE_C_ACTIVATION_WEIGHT = 0.6
_BASELINE_C_SIMILARITY_WEIGHT = 0.4

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


@dataclass(frozen=True)
class LabelledQuery:
    query: str
    expected_top1: str


def load_queries(path: Path) -> list[LabelledQuery]:
    """The toy benchmark's ``queries.jsonl`` (spec §1
    ``tests/fixtures/toy-registry/``): one ``{"query": ..., "expected_top1":
    ...}`` object per line."""
    out: list[LabelledQuery] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        out.append(LabelledQuery(query=str(row["query"]), expected_top1=str(row["expected_top1"])))
    return out


def _routable_rows(conn: sqlite3.Connection, model_name: str) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in ROUTABLE_STATUSES)
    return conn.execute(
        f"""
        SELECT e.id, e.name, e.intent_does, e.intent_use_when, x.vec
        FROM engram e JOIN eph_embedding x ON x.engram_id = e.id AND x.model = ?
        WHERE e.status IN ({placeholders}) AND e.verification_status = 'verified'
        """,
        (model_name, *ROUTABLE_STATUSES),
    ).fetchall()


def _positive_triggers(conn: sqlite3.Connection, engram_id: str) -> str:
    rows = conn.execute(
        "SELECT text FROM engram_trigger WHERE engram_id = ? AND polarity = 'positive'", (engram_id,)
    ).fetchall()
    return " ".join(str(r["text"]) for r in rows)


def _baseline_a_rank(conn: sqlite3.Connection, query: str) -> list[str]:
    """Native, description-only lexical overlap -- no embeddings, no
    graph, no LLM (CR-3). See module docstring."""
    q_tokens = _tokenize(query)
    placeholders = ",".join("?" for _ in ROUTABLE_STATUSES)
    rows = conn.execute(
        f"SELECT id, name, intent_does, intent_use_when FROM engram "
        f"WHERE status IN ({placeholders}) AND verification_status = 'verified'",
        tuple(ROUTABLE_STATUSES),
    ).fetchall()
    scored: list[tuple[int, str]] = []
    for r in rows:
        text = " ".join(
            filter(None, [r["intent_does"], r["intent_use_when"], _positive_triggers(conn, r["id"])])
        )
        overlap = len(q_tokens & _tokenize(text))
        scored.append((overlap, str(r["name"])))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [name for _, name in scored]


def _baseline_b_rank(conn: sqlite3.Connection, embedder: Embedder, query: str) -> list[str]:
    """Dense embedding cosine only -- no graph, no plasticity."""
    qvec = embedder.embed(query)
    rows = _routable_rows(conn, embedder.model_name)
    scored: list[tuple[float, str]] = []
    for r in rows:
        vec = np.frombuffer(r["vec"], dtype=np.float32)
        scored.append((float(np.dot(qvec, vec)), str(r["name"])))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [name for _, name in scored]


def _baseline_c_rank(cfg: Config, conn: sqlite3.Connection, embedder: Embedder, query: str) -> list[str]:
    """Embedding + structural (fixed-weight) spreading activation --
    "static-weighted graph edges", S_edge ignored. See module docstring."""
    qvec = embedder.embed(query)
    rows = _routable_rows(conn, embedder.model_name)
    if not rows:
        return []
    node_ids = [str(r["id"]) for r in rows]
    name_by_id = {str(r["id"]): str(r["name"]) for r in rows}
    cosine = {}
    for r in rows:
        vec = np.frombuffer(r["vec"], dtype=np.float32)
        cosine[str(r["id"])] = float(np.dot(qvec, vec))

    m = min(max(5 * len(node_ids), 25), 200)
    seed_order = sorted(node_ids, key=lambda nid: cosine[nid], reverse=True)[:m]
    seed_cos = {nid: cosine[nid] for nid in seed_order}
    p = activation_mod.softmax_personalization(node_ids, seed_cos, temperature=cfg.temperature)

    placeholders = ",".join("?" for _ in _GRAPH_EDGE_TYPES)
    edge_rows = conn.execute(
        f"SELECT src_id, dst_id, type FROM edge WHERE type IN ({placeholders}) "
        "AND dangling = 0 AND dst_id IS NOT NULL",
        _GRAPH_EDGE_TYPES,
    ).fetchall()
    edges = [
        (str(r["src_id"]), str(r["dst_id"]), cfg.type_gain.get(r["type"], 0.0)) for r in edge_rows
    ]
    graph = activation_mod.build_graph(node_ids, edges)
    a = activation_mod.personalized_pagerank(
        graph, p, restart=cfg.ppr_restart, max_iter=cfg.ppr_max_iter, tol=cfg.ppr_tol
    )
    index = {nid: i for i, nid in enumerate(node_ids)}
    scored = [
        (
            _BASELINE_C_ACTIVATION_WEIGHT * float(a[index[nid]])
            + _BASELINE_C_SIMILARITY_WEIGHT * cosine[nid],
            name_by_id[nid],
        )
        for nid in node_ids
    ]
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [name for _, name in scored]


def _id_for_name(conn: sqlite3.Connection, name: str) -> str | None:
    row = conn.execute("SELECT id FROM engram WHERE name = ?", (name,)).fetchone()
    return str(row["id"]) if row is not None else None


def _expand_plan(cfg: Config, conn: sqlite3.Connection, name: str) -> list[str]:
    winner_id = _id_for_name(conn, name)
    if winner_id is None:
        return []
    plan = composition_mod.expand(
        conn, winner_id, name, max_depth=cfg.plan_max_depth, max_size=cfg.plan_max_size
    )
    return plan.order


@dataclass(frozen=True)
class BaselineReport:
    name: str
    label: str
    ranking: metrics_mod.RankingReport
    plan_f1: metrics_mod.PlanF1Result

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            **self.ranking.to_dict(),
            "plan_f1": self.plan_f1.to_dict(),
            # M7 close-out item #3 (vacuous-truth fix): how many of the
            # n_queries pairs actually fed plan_f1's average, vs. how many
            # were vacuous (nothing to plan against -- see
            # eval/metrics.py::aggregate_plan_f1's docstring). Kept as
            # siblings of "plan_f1", not nested inside it, so the frozen
            # AC-029 anchor's `set(d["plan_f1"]) == {...}` key-set check
            # is unaffected by this addition.
            "plan_f1_n_evaluated": self.plan_f1.n_evaluated,
            "plan_f1_n_total": self.plan_f1.n_total,
        }


def run_baseline(
    cfg: Config,
    conn: sqlite3.Connection,
    embedder: Embedder,
    baseline: str,
    queries: list[LabelledQuery],
    *,
    k: int = 5,
) -> BaselineReport:
    if baseline not in BASELINE_NAMES:
        raise ValueError(f"unknown baseline {baseline!r}, expected one of {BASELINE_NAMES}")

    ranking_pairs: list[tuple[list[str], str]] = []
    plan_pairs: list[tuple[list[str], list[str]]] = []
    expected_plan_cache: dict[str, list[str]] = {}

    for q in queries:
        route_outcome = None
        if baseline == "a":
            ranked = _baseline_a_rank(conn, q.query)
        elif baseline == "b":
            ranked = _baseline_b_rank(conn, embedder, q.query)
        elif baseline == "c":
            ranked = _baseline_c_rank(cfg, conn, embedder, q.query)
        else:  # "d": the real, unmodified route()
            route_outcome = router_mod.route(cfg, conn, embedder, query=q.query, k=k)
            ranked = [c.name for c in route_outcome.candidates]

        ranking_pairs.append((ranked, q.expected_top1))

        if q.expected_top1 not in expected_plan_cache:
            expected_plan_cache[q.expected_top1] = _expand_plan(cfg, conn, q.expected_top1)
        expected_plan = expected_plan_cache[q.expected_top1]

        if not ranked:
            predicted_plan: list[str] = []
        elif route_outcome is not None:
            predicted_plan = route_outcome.composition_plan
        else:
            predicted_plan = _expand_plan(cfg, conn, ranked[0])
        plan_pairs.append((predicted_plan, expected_plan))

    ranking_report = metrics_mod.aggregate_ranking(ranking_pairs)
    plan_report = metrics_mod.aggregate_plan_f1(plan_pairs)
    return BaselineReport(
        name=baseline, label=BASELINE_LABELS[baseline], ranking=ranking_report, plan_f1=plan_report
    )


@dataclass
class BenchReport:
    registry_size: int
    n_queries: int
    baselines: dict[str, BaselineReport] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "registry_size": self.registry_size,
            "n_queries": self.n_queries,
            "baselines": {name: report.to_dict() for name, report in self.baselines.items()},
        }


def _default_queries_path(project_root: Path) -> Path:
    return project_root / ".spectra" / "bench" / "queries.jsonl"


def run_bench(
    cfg: Config,
    conn: sqlite3.Connection,
    embedder: Embedder,
    *,
    queries: list[LabelledQuery],
    baselines: list[str] | None = None,
    k: int = 5,
) -> BenchReport:
    """AC-029: emits Hit@1/3/5, MRR and Plan F1 for every requested
    baseline (default: all four, a-d). Read-only over the DB except for
    baseline (d), which runs the real ``route()`` and therefore performs
    its normal Tier-C bookkeeping (spec §3.3 step 11) -- exactly what a
    genuine benchmark run against a real server would do; nothing here
    mutates durable (Tier A/B) state.
    """
    selected = baselines if baselines is not None else list(BASELINE_NAMES)
    registry_size = int(conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()["n"])
    reports = {
        name: run_baseline(cfg, conn, embedder, name, queries, k=k) for name in selected
    }
    return BenchReport(registry_size=registry_size, n_queries=len(queries), baselines=reports)


# ── ``magicite-bench`` CLI (spec §1: "eval/{bench,metrics,ablations}.py +
#    magicite-bench CLI"; AC-029: "magicite-bench --baseline b --baseline d") ──


@click.command()
@click.option("--project-root", default=".", show_default=True, help="Registry project root.")
@click.option(
    "--queries", "queries_path", default=None,
    help="Labelled query JSONL path (default: <project-root>/.spectra/bench/queries.jsonl).",
)
@click.option(
    "--baseline", "selected_baselines", multiple=True,
    type=click.Choice(BASELINE_NAMES), help="Repeatable; default: all four (a-d).",
)
@click.option("-k", default=5, show_default=True, help="Top-k candidates per query.")
@click.option(
    "--sync/--no-sync", default=True, show_default=True,
    help="Run sync() first so embeddings/communities are current.",
)
def cli(
    project_root: str, queries_path: str | None, selected_baselines: tuple[str, ...], k: int, sync: bool
) -> None:
    """Run the docs/07 baseline benchmark (a-d) and print Hit@k/MRR/Plan F1 as JSON."""
    from magicite.core import registry as registry_mod
    from magicite.embeddings import get_embedder
    from magicite.storage import db as db_mod

    cfg = Config.load(project_root)
    cfg.ensure_dirs()
    conn = db_mod.connect(cfg.db_path)
    embedder = get_embedder(cfg)
    if sync:
        registry_mod.sync(cfg, conn, embedder)

    qpath = Path(queries_path) if queries_path else _default_queries_path(cfg.project_root)
    if not qpath.is_file():
        raise click.ClickException(f"no labelled query file at {qpath} (pass --queries)")
    queries = load_queries(qpath)

    report = run_bench(
        cfg, conn, embedder, queries=queries, baselines=list(selected_baselines) or None, k=k
    )
    click.echo(json.dumps(report.to_dict(), indent=2, default=str))


def main() -> None:  # pragma: no cover - thin entry-point wiring
    cli()
