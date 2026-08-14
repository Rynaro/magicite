"""Config dataclass + TOML/env resolution (spec §1 data layout, §4.3 defaults).

Framework-free (INV-1): no MCP import here. Resolution order, lowest to
highest precedence: dataclass defaults -> ``<project_root>/.spectra/magicite.toml``
-> process environment (``MAGICITE_*``).
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

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

    # ── routing tunables (spec §3.3) ────────────────────────────────────
    session_ttl_hours: float = 3.0
    temperature: float = 0.07
    ppr_restart: float = 0.15
    ppr_max_iter: int = 20
    ppr_tol: float = 1e-4
    hub_penalty: float = 0.15
    inhib_gain: float = 0.7
    context_gain: float = 0.20
    pref_gain: float = 0.10
    w_activation: float = 0.45
    w_similarity: float = 0.30
    w_retrieval: float = 0.15
    w_excitability: float = 0.10
    type_gain: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_TYPE_GAIN))
    plan_max_depth: int = 5
    plan_max_size: int = 8

    # ── signals tunables (spec §3.3, §4.1) ──────────────────────────────
    per_skill_session_cap: int = 3
    tau_credit_seconds: float = 1800.0

    # ── dream trigger tunables (spec §4.1) ──────────────────────────────
    dream_on_session_end: bool = True
    dream_min_interval_s: float = 300.0
    dream_idle_poll_s: float = 0.0
    retention_days: int = 30

    # ── embeddings (CR-6) ────────────────────────────────────────────────
    embedding_provider: str = "hashing"
    embedding_offline: bool = False
    embedding_dim: int = 256

    # ── governance / autonomy ────────────────────────────────────────────
    autonomous: bool = False
    hook_token: str | None = None
    commit_db: bool = False

    # ── logging ───────────────────────────────────────────────────────────
    log_level: str = "INFO"

    @property
    def spectra_dir(self) -> Path:
        return self.project_root / ".spectra"

    @property
    def registry_dir(self) -> Path:
        return self.spectra_dir / "engrams"

    @property
    def archive_dir(self) -> Path:
        return self.spectra_dir / "archive"

    @property
    def approvals_dir(self) -> Path:
        return self.spectra_dir / "approvals"

    @property
    def runtime_dir(self) -> Path:
        return self.spectra_dir / "runtime"

    @property
    def toml_path(self) -> Path:
        return self.spectra_dir / "magicite.toml"

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
        """Resolve config: dataclass defaults -> magicite.toml -> env."""
        resolved_env: Mapping[str, str] = os.environ if env is None else env
        root = Path(
            project_root or resolved_env.get("MAGICITE_PROJECT_ROOT") or Path.cwd()
        ).resolve()
        cfg = cls(project_root=root)

        toml_values = _read_toml(cfg.toml_path)
        cfg = _apply_mapping(cfg, toml_values)
        cfg = _apply_env(cfg, resolved_env)
        return cfg


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


_FIELD_NAMES = {f.name for f in fields(Config)}

#: MAGICITE_<ENV> -> Config field name, for the knobs the spec names explicitly.
_ENV_FIELD_MAP: dict[str, str] = {
    "MAGICITE_EMBEDDING_PROVIDER": "embedding_provider",
    "MAGICITE_EMBEDDING_OFFLINE": "embedding_offline",
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
