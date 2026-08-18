"""Config dataclass + TOML/env resolution (spec §1 data layout, §4.3 defaults).

Framework-free (INV-1): no MCP import here. Resolution order, lowest to
highest precedence: dataclass defaults -> ``<data_dir>/magicite.toml``
-> process environment (``MAGICITE_*``).

[DATA-DIR-AMENDED 2026-08-15] spec §1 places every Magicite-owned directory
under ``<project_root>/.spectra/``. That directory is **not Magicite's**:
ESL/tonberry owns ``.spectra/changes/``, and the two tenants collide
confusingly (Magicite's old ``.spectra/archive/`` -- decayed engrams -- sat one
level from ESL's ``.spectra/changes/archive/`` -- archived change records).
The surrounding ecosystem already establishes one-tool-one-dot-directory
(``.atlas/`` for atlas-aci, ``.eidolons/`` for Eidolons), so Magicite now owns
``<project_root>/.magicite/`` and leaves ``.spectra/`` to ESL entirely.

This is a path-resolution change only: nothing about routing, plasticity, the
trust gate, or the 16-tool surface moves with it.

Legacy projects are not broken. :func:`resolve_data_dir_name` falls back to
``.spectra`` when -- and only when -- ``.magicite/`` is absent *and* a legacy
``.spectra/engrams/`` actually exists. A fresh project that merely happens to
carry an ESL ``.spectra/changes/`` tree is never dragged onto the old layout.
The fallback is announced by ``obs/doctor.py`` rather than happening quietly:
silently routing against an empty registry is the worst failure mode a router
has, and it is exactly what a clean break would have produced.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

#: [DATA-DIR-AMENDED 2026-08-15] The directory Magicite owns inside a project.
#: Namespaced to the tool, per the ecosystem convention (``.atlas/``,
#: ``.eidolons/``) -- a generic name like ``.skills/`` would recreate exactly
#: the collision this amendment removes.
DEFAULT_DATA_DIR_NAME = ".magicite"

#: The pre-amendment location. Read-only compatibility: resolution falls back
#: here only when the new directory is absent AND a registry actually exists
#: here (see :func:`resolve_data_dir_name`). Droppable at the next minor
#: version, once v0.1.0's installed base has migrated.
LEGACY_DATA_DIR_NAME = ".spectra"

#: The subdirectory whose presence proves a legacy layout is real rather than
#: incidental. ``.spectra/changes/`` is ESL's and says nothing about Magicite,
#: so it deliberately does NOT count as evidence.
_LEGACY_MARKER = "engrams"

#: type_gain weights used by core/activation.py's edge weighting (spec §3.3 step 4).
DEFAULT_TYPE_GAIN: dict[str, float] = {
    "co_activation": 0.8,
    "composes": 1.0,
    "depends_on": 1.0,
    "similar_to": 0.6,
    "inhibits": 0.0,  # inhibition is applied separately (step 5), never as positive weight
}


@dataclass(slots=True)
class Config:
    """Resolved runtime configuration for one Magicite server instance."""

    project_root: Path = field(default_factory=Path.cwd)
    #: [DATA-DIR-AMENDED 2026-08-15] Name of the project-local directory holding
    #: every Magicite-owned path. Set by :func:`resolve_data_dir_name` during
    #: ``load()``; deliberately NOT settable from ``magicite.toml``, which lives
    #: *inside* this directory and therefore cannot decide where it is.
    data_dir_name: str = DEFAULT_DATA_DIR_NAME

    # ── plasticity / decay tunables (spec §4.3) ────────────────────────
    eta: float = 0.08
    w_max: float = 1.0
    tau_spacing_hours: float = 6.0
    lambda_r_per_day: float = 0.1
    lambda_s_per_day: float = 0.01
    theta_salience: float = 0.7
    theta_synapse: float = 0.35
    theta_consolidate_status: float = 0.6
    floor_archived: float = 0.2
    epsilon_write: float = 0.05
    #: spec §4.3 Phase 2 pseudocode: "commit if |dw| > theta_consolidate
    #: (0.01)" -- named distinctly from ``theta_consolidate_status`` (the
    #: *lifecycle* S>=0.6 bar) since spec's own prose overloads
    #: "theta_consolidate" for two different constants; this is the dw
    #: commit-noise floor, not a status threshold.
    theta_dw_commit: float = 0.01
    #: spec §4.3 Phase 2: "prune: S_edge < theta_prune (0.10) for >=3
    #: consecutive runs -> archive row, drop from synapses". Named inline
    #: in the pseudocode but omitted from the "Defaults" bullet list --
    #: the inline value (0.10) is the only one given, so it is authoritative.
    theta_prune: float = 0.10

    # ── routing tunables (spec §3.3) ────────────────────────────────────
    session_ttl_hours: float = 3.0
    temperature: float = 0.07
    #: [DECLARED-EDGES-AMENDED 2026-08-15] was 0.15. MEASURED (70 engrams
    #: / 210 pre-registered queries, one-Config-field-at-a-time sweep):
    #: Hit@1 0.4619 -> 0.5476 (== embedding baseline (b)), Hit@3 0.7000 ->
    #: 0.7476 (> (b)'s 0.7429), MRR 0.5913 -> 0.6398. At 0.15, 85% of
    #: activation mass diffused along the derived similar_to kNN edges --
    #: spread reflecting neighbourhood mass, not query match. Caveat
    #: (spec R12): measured on the graph *before* declared edges carried
    #: mass; re-measure after §3.3.1 lands (MO-1).
    ppr_restart: float = 0.85
    ppr_max_iter: int = 20
    ppr_tol: float = 1e-4
    hub_penalty: float = 0.15
    #: [INHIB-GAIN-RECALIBRATED 2026-08-15] was 0.7. DERIVED, not selected:
    #: theta_synapse (0.35) x the old gain (0.7). Until §3.3.1, apply_inhibition
    #: was a numeric no-op, so 0.7 had never been measured in any regime; a
    #: learned inhibits edge's intended effect ranged over [0.245, 0.7], and
    #: pinning S_eff at 1.0 handed a day-zero assertion the magnitude reserved
    #: for a maximally potentiated edge. Unrounded on purpose -- re-derive if
    #: theta_synapse moves, do not re-tune. 0.0 is forbidden: it makes AC-023
    #: and AC-034 arithmetically unprovable.
    inhib_gain: float = 0.245
    #: Independent contraindication similarity contribution. It is applied
    #: as ``-weight * max(0, cosine(query, contraindication_view))`` after
    #: positive scoring. Zero is an exact rollback switch.
    negative_cue_weight: float = 0.05
    context_gain: float = 0.20
    pref_gain: float = 0.10
    #: [DECLARED-EDGES-AMENDED 2026-08-15] was 0.30/0.15. PRECAUTIONARY
    #: PENDING FURTHER EXPERIMENT, not a measured optimum (spec §3.3.1
    #: "Routing defaults changed on evidence"): w_retrieval=0.15 has one
    #: strong measurement against it (held-out Hit@1 0.4697 -> 0.1061
    #: under an oracle teacher, a 3.6x collapse) and zero measurements
    #: ever for it, on a uniform-demand workload that makes a popularity
    #: prior maximally uninformative. w_activation is unchanged; the four
    #: still sum to 1.00.
    w_activation: float = 0.45
    w_similarity: float = 0.40
    w_retrieval: float = 0.05
    w_excitability: float = 0.10
    type_gain: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_TYPE_GAIN))
    plan_max_depth: int = 5
    plan_max_size: int = 8
    #: spec §3.3.1 (DECLARED-EDGES-AMENDED, 2026-08-15): an edge's routing
    #: weight has two channels -- S_eff(edge) = max(edge.storage_strength,
    #: w_authored(edge)) -- and this is the authored channel's magnitude
    #: for a `provenance='declared'` edge (needs/composes/inhibits).
    #: Computed at read by core/edge_weight.py::effective_strength, never
    #: stored. 1.0 is not a new magic number: at S_eff=1.0,
    #: S_eff*type_gain[type] IS type_gain[type], the knob that already
    #: expresses relative weighting among declared edge types. 0.0 is an
    #: EXACT, bit-for-bit revert to pre-amendment behaviour (AC-039) --
    #: one config line, ablation-switchable, [routing] in magicite.toml.
    declared_edge_strength: float = 1.0

    # ── graph index build (spec §2.6 steps 8-9) ─────────────────────────
    similar_to_top_m: int = 5
    hub_penalty_percentile: float = 95.0

    # ── signals tunables (spec §3.3, §4.1) ──────────────────────────────
    per_skill_session_cap: int = 3
    tau_credit_seconds: float = 1800.0
    #: spec §3.3 tool 5 step 4: "R nudge (Tier C): R <- min(1, R + eta_R*(1-R))".
    eta_r: float = 0.15
    #: M4 hardening (input-hygiene, not spec-named): the minimum wall-clock
    #: gap between two R "occasions" for the *same* engram, keyed on
    #: engram_id (something a caller cannot forge or rotate away from,
    #: unlike session_id) -- see ``storage.ephemeral.bump_retrieval``. A
    #: buggy/runaway or adversarial caller cannot inflate R faster than
    #: this regardless of how many signal_use calls or session ids it uses.
    #: Not a spec constant; a conservative default (0 = off, matching M3's
    #: prior behaviour) is deliberately *not* the default here.
    eta_r_refractory_s: float = 30.0
    #: M4 hardening: caps how many live tags one high-salience
    #: ``signal_outcome()`` call can retroactively credit (spec §3.3 tool 6
    #: rule 2's "all skills in last T minutes" is otherwise unbounded).
    #: Recency weighting (``capture_weight``) already makes tags older than
    #: this near-zero-weight, so capping loses little signal.
    retroactive_credit_max: int = 10
    #: M6 hardening (carried-forward defect #1, "session-suppression
    #: hijack"): ``session_id`` carries no capability/identity (spec §3.3
    #: forbids server-side session minting/auth), so *any* caller that
    #: names a session_id can call ``session_end`` on it -- including a
    #: caller that is not the session's own owner. Before this fix,
    #: ``session_end`` could pull a not-yet-captured tag's ``expires_at``
    #: forward to "now" unconditionally, so a same-instant
    #: ``session_end(<id>)`` call raced ahead of (or substituted for) the
    #: legitimate owner's own ``signal_outcome()`` call and silently made
    #: it capture 0 -- a stranger (or a confused/injected tool call)
    #: naming a guessed or leaked session_id could suppress a signal that
    #: was never actually reported as failed. Per the mission brief, the
    #: fix bounds the *effect*, not the principal (identity is explicitly
    #: out of scope): ``session_end`` may only pull a tag's expiry forward
    #: once it is already at least this many seconds old (measured from
    #: its immutable ``set_at``, which ``session_end`` never touches), so
    #: a freshly-set tag survives an out-of-band ``session_end`` call long
    #: enough for the realistic same-turn
    #: ``signal_use() -> signal_outcome()`` pattern to complete. This
    #: bounds the blast radius; it does not eliminate it -- the same
    #: "bounded, not eliminated" posture R1 already takes elsewhere in
    #: this codebase (a sufficiently patient, repeated attacker can still
    #: eventually suppress an old-enough, still-uncaptured tag). 0
    #: disables the floor (M4's prior, fully exploitable-by-timing
    #: behaviour).
    session_end_tag_grace_s: float = 60.0

    # ── dream trigger tunables (spec §4.1) ──────────────────────────────
    dream_on_session_end: bool = True
    dream_min_interval_s: float = 300.0
    dream_idle_poll_s: float = 0.0
    retention_days: int = 30
    #: docs/02 discipline 4 / M5 security fix #2: how long a
    #: ``request_id -> response`` idempotency-replay row stays valid.
    #: Previously rows were written already-expired (``expires_at`` equal
    #: to ``created_at``) and nothing ever purged them; both halves are
    #: fixed at M5 -- a real TTL here, and ``core/decay.py::purge_retention``
    #: (Dream phase 3) actually deletes rows past it.
    idempotency_ttl_s: float = 86400.0

    # ── embeddings (CR-6) ────────────────────────────────────────────────
    #: spec §1: fastembed (ONNX BAAI/bge-small-en-v1.5) is the v1 default;
    #: every test in this repo overrides this to "hashing" explicitly
    #: (tests/conftest.py), so flipping the *default* here does not change
    #: what CI exercises -- it changes what an unconfigured `magicite serve`
    #: actually does, which is the point (R4).
    embedding_provider: str = "fastembed"
    embedding_offline: bool = True
    embedding_dim: int = 256
    embedding_cache_size: int = 256
    ollama_host: str = "http://localhost:11434"
    #: docs/04's routing-block example model (CR-6: "bge-m3 example remains legal").
    ollama_model: str = "bge-m3"

    # ── governance / autonomy ────────────────────────────────────────────
    autonomous: bool = False
    hook_token: str | None = None
    commit_db: bool = False

    # ── M6 ablation switches (spec §7.3 ship list: "no-decay,
    # no-tag-capture, no-communities | ship as config switches | driven
    # from magicite.toml") ────────────────────────────────────────────
    # no-decay: set lambda_r_per_day = lambda_s_per_day = 0 directly
    # (the two fields above already ARE the switch; no separate flag
    # needed). no-tag-capture is deliberately NOT a Config field at all
    # -- see eval/ablations.py's module docstring for why (P0).
    #: spec §3.3 step 8's community rerank, disabled when set. Read only
    #: by ``core/router.py::route()``; never touches G1/G2/G3.
    ablation_no_communities: bool = False

    # ── logging ───────────────────────────────────────────────────────────
    log_level: str = "INFO"

    @property
    def data_dir(self) -> Path:
        """[DATA-DIR-AMENDED 2026-08-15] The one directory Magicite owns.

        Every other Magicite path derives from this, so moving the tool's
        footprint is a one-line change here rather than a sweep.
        """
        return self.project_root / self.data_dir_name

    @property
    def uses_legacy_layout(self) -> bool:
        """True when resolution fell back to the pre-amendment ``.spectra/``.

        Surfaced by ``obs/doctor.py`` so the fallback is announced rather than
        silent.
        """
        return self.data_dir_name == LEGACY_DATA_DIR_NAME

    @property
    def registry_dir(self) -> Path:
        return self.data_dir / "engrams"

    @property
    def archive_dir(self) -> Path:
        return self.data_dir / "archive"

    @property
    def approvals_dir(self) -> Path:
        return self.data_dir / "approvals"

    @property
    def runtime_dir(self) -> Path:
        return self.data_dir / "runtime"

    @property
    def bench_queries_path(self) -> Path:
        return self.data_dir / "bench" / "queries.jsonl"

    @property
    def toml_path(self) -> Path:
        return self.data_dir / "magicite.toml"

    @property
    def db_path(self) -> Path:
        return self.registry_dir / "skill-graph.db"

    @property
    def dream_lock_path(self) -> Path:
        return self.runtime_dir / "dream.lock"

    def ensure_dirs(self) -> None:
        for d in (self.registry_dir, self.archive_dir, self.approvals_dir, self.runtime_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ── resolution ────────────────────────────────────────────────────────

    @classmethod
    def load(
        cls,
        project_root: str | Path | None = None,
        *,
        env: Mapping[str, str] | None = None,
    ) -> Config:
        """Resolve config: dataclass defaults -> magicite.toml -> env.

        The data directory is resolved *first*, before the TOML is read, because
        ``magicite.toml`` lives inside it — a file cannot say where it is.
        """
        resolved_env: Mapping[str, str] = os.environ if env is None else env
        root = Path(
            project_root or resolved_env.get("MAGICITE_PROJECT_ROOT") or Path.cwd()
        ).resolve()
        cfg = cls(project_root=root, data_dir_name=resolve_data_dir_name(root, resolved_env))

        toml_values = _read_toml(cfg.toml_path)
        cfg = _apply_mapping(cfg, toml_values)
        cfg = _apply_env(cfg, resolved_env)
        return cfg


def resolve_data_dir_name(project_root: Path, env: Mapping[str, str]) -> str:
    """[DATA-DIR-AMENDED 2026-08-15] Pick the project's Magicite directory.

    Precedence, highest first:

    1. ``MAGICITE_DATA_DIR`` -- an explicit host decision always wins, so a
       deployment with an unusual layout never has to fork.
    2. ``.magicite/`` when it already exists -- the current layout.
    3. ``.spectra/`` **only** when ``.magicite/`` is absent *and*
       ``.magicite/engrams/`` exists. The marker matters: ``.spectra/changes/``
       is ESL's and proves nothing about Magicite, so its presence alone must
       never pull a fresh project onto the legacy layout.
    4. ``.magicite/`` otherwise -- the default for anything new.
    """
    override = env.get("MAGICITE_DATA_DIR", "").strip()
    if override:
        return override
    if (project_root / DEFAULT_DATA_DIR_NAME).is_dir():
        return DEFAULT_DATA_DIR_NAME
    if (project_root / LEGACY_DATA_DIR_NAME / _LEGACY_MARKER).is_dir():
        return LEGACY_DATA_DIR_NAME
    return DEFAULT_DATA_DIR_NAME


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    # magicite.toml is organised in sections (plasticity, routing, signals,
    # dream, embeddings, governance); flatten one level for field lookup.
    flat: dict[str, Any] = {}
    for value in data.values():
        if isinstance(value, dict):
            flat.update(value)
        else:
            flat.update(data)
            break
    return flat


#: Fields the TOML may not set. ``data_dir_name`` is excluded because
#: ``magicite.toml`` lives inside the directory it names -- honouring it would
#: mean a file relocating the directory it was just read from, which is either
#: circular or a silent no-op depending on read order. Set ``MAGICITE_DATA_DIR``
#: instead (resolved before the TOML is opened).
_TOML_EXCLUDED_FIELDS = frozenset({"project_root", "data_dir_name"})

_FIELD_NAMES = {f.name for f in fields(Config)} - _TOML_EXCLUDED_FIELDS

#: MAGICITE_<ENV> -> Config field name, for the knobs the spec names explicitly.
_ENV_FIELD_MAP: dict[str, str] = {
    "MAGICITE_EMBEDDING_PROVIDER": "embedding_provider",
    "MAGICITE_EMBEDDING_OFFLINE": "embedding_offline",
    "MAGICITE_OLLAMA_HOST": "ollama_host",
    "MAGICITE_OLLAMA_MODEL": "ollama_model",
    "MAGICITE_HOOK_TOKEN": "hook_token",
    "MAGICITE_AUTONOMOUS": "autonomous",
    "MAGICITE_COMMIT_DB": "commit_db",
    "MAGICITE_LOG_LEVEL": "log_level",
}

_BOOL_FIELDS = {"embedding_offline", "autonomous", "commit_db", "dream_on_session_end"}


def _coerce(field_name: str, raw: Any) -> Any:
    if not isinstance(raw, str):
        return raw
    if field_name in _BOOL_FIELDS:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    current_type = type(getattr(Config, field_name, raw))
    try:
        if current_type is float:
            return float(raw)
        if current_type is int:
            return int(raw)
    except ValueError:
        pass
    return raw


def _apply_mapping(cfg: Config, values: dict[str, Any]) -> Config:
    for key, raw in values.items():
        name = key.replace("-", "_")
        if name in _FIELD_NAMES:
            setattr(cfg, name, _coerce(name, raw))
    return cfg


def _apply_env(cfg: Config, env: Mapping[str, str]) -> Config:
    for env_key, field_name in _ENV_FIELD_MAP.items():
        if env_key in env:
            setattr(cfg, field_name, _coerce(field_name, env[env_key]))
    return cfg
