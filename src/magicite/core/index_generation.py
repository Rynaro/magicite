"""Index generation, full-content projection, and durable catalog (C3 / C11 / S05).

Lifecycle / active-pointer authority lives in S03
(``index_generation`` / ``index_active_pointer`` + ``storage.migration_ops``).
S05 owns generation-scoped ``index_entry`` / ``index_fts`` (migration 004) and
the pure projection / candidate-facing loaders.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np

from magicite.engram.digests import canonical_json_bytes, routing_body_digest, sha256_hex
from magicite.engram.model import Engram
from magicite.engram.model_v1 import EngramV1
from magicite.storage import migration_ops as ops
from magicite.storage.migrations.registry import MAX_KNOWN_SCHEMA_VERSION

PROJECTION_VERSION = "full-content/1"
INDEX_SCHEMA_VERSION = "s05-fulltext/1"
DEFAULT_CHUNK_TOKENS = 256
DEFAULT_CHUNK_OVERLAP = 32
DEFAULT_MAX_CHUNKS = 64

#: Word tokenizer identity bound into IndexFingerprint/1 (C11).
TOKENIZER_ID = "unicode61-word/1"

_WORD_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-/:]*")
_SYMBOL_RE = re.compile(
    r"(?:`([^`]+)`)"
    r"|(?:\begr_[0-9a-f]{8}\b)"
    r"|(?:\b[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+\b)"
    r"|(?:\b[A-Z]{2,}[A-Z0-9_]+\b)"
    r"|(?:\bE_[A-Z0-9_*]+\b)"
)

#: Shipped DDL reference (003 + 004). Kept for docs/adapters — not applied here.
SHIPPED_INDEX_DDL_NOTE = (
    "Lifecycle/pointer: storage/migrations/003_migration_authority.sql (S03). "
    "Entries/FTS: storage/migrations/004_fulltext_index.sql (S05)."
)


class IndexGenerationError(Exception):
    """Base error for index generation / validation failures."""


class FingerprintConflictError(IndexGenerationError):
    """Same model name registered with a different artifact digest (C11)."""


class IncompleteGenerationError(IndexGenerationError):
    """Pinned or published generation is not complete."""


class StaleGenerationError(IndexGenerationError):
    """Stored projection digests do not match the declared snapshot (C11)."""


class FTS5UnavailableError(IndexGenerationError):
    """SQLite build lacks FTS5; callers must choose an explicit fallback."""


@dataclass(frozen=True)
class ChunkerConfig:
    max_tokens: int = DEFAULT_CHUNK_TOKENS
    overlap_tokens: int = DEFAULT_CHUNK_OVERLAP
    max_chunks: int = DEFAULT_MAX_CHUNKS

    def to_dict(self) -> dict[str, int]:
        return {
            "max_tokens": self.max_tokens,
            "overlap_tokens": self.overlap_tokens,
            "max_chunks": self.max_chunks,
        }


@dataclass(frozen=True)
class IndexFingerprint:
    """IndexFingerprint/1 (C11)."""

    provider: str
    model_artifact_digest: str
    dimension: int
    model_revision: str | None = None
    normalization: str = "l2"
    projection_version: str = PROJECTION_VERSION
    tokenizer_id: str = TOKENIZER_ID
    chunker: ChunkerConfig = field(default_factory=ChunkerConfig)
    index_schema_version: str = INDEX_SCHEMA_VERSION
    schema_version: Literal["IndexFingerprint/1"] = "IndexFingerprint/1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "provider": self.provider,
            "model_artifact_digest": self.model_artifact_digest,
            "model_revision": self.model_revision,
            "dimension": self.dimension,
            "normalization": self.normalization,
            "projection_version": self.projection_version,
            "tokenizer_id": self.tokenizer_id,
            "chunker": self.chunker.to_dict(),
            "index_schema_version": self.index_schema_version,
        }

    def digest(self) -> str:
        return sha256_hex(canonical_json_bytes(self.to_dict()))


@dataclass(frozen=True)
class ProjectedChunk:
    chunk_index: int
    text: str
    content_sha256: str
    start_token: int
    end_token: int


@dataclass(frozen=True)
class FullContentProjection:
    """Full-content routing projection including preserved prose and symbols.

    ``body_digest`` is the normative engram/1.0 ``routing.body_digest``
    (:func:`magicite.engram.digests.routing_body_digest`).
    ``projection_sha256`` hashes the full-content retrieval projection and is
    intentionally distinct.
    """

    engram_id: str
    revision: int
    name: str
    title: str
    intent_text: str
    triggers_text: str
    body_text: str
    full_text: str
    projection_sha256: str
    body_digest: str
    chunks: tuple[ProjectedChunk, ...]
    truncated_chunks: int
    symbols: tuple[str, ...]


def fts5_available(conn: sqlite3.Connection | None = None) -> bool:
    """Return True when this Python's SQLite can create an FTS5 virtual table."""
    owns = conn is None
    c = conn or sqlite3.connect(":memory:")
    try:
        c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _magicite_fts5_probe USING fts5(x)")
        c.execute("DROP TABLE IF EXISTS _magicite_fts5_probe")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        if owns:
            c.close()


def tokenize_words(text: str) -> list[str]:
    """Deterministic word tokenizer bound to :data:`TOKENIZER_ID`."""
    return _WORD_RE.findall(text)


def extract_symbols(text: str) -> tuple[str, ...]:
    """Exact identifiers / backticked tokens preserved for sparse retrieval."""
    found: list[str] = []
    seen: set[str] = set()
    for match in _SYMBOL_RE.finditer(text):
        token = match.group(1) if match.group(1) is not None else match.group(0)
        token = token.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        found.append(token)
    return tuple(found)


def chunk_text(text: str, cfg: ChunkerConfig | None = None) -> tuple[tuple[ProjectedChunk, ...], int]:
    """Deterministic bounded chunking; returns (chunks, truncated_count)."""
    cfg = cfg or ChunkerConfig()
    tokens = tokenize_words(text)
    if not tokens:
        empty = ProjectedChunk(
            chunk_index=0,
            text="",
            content_sha256=sha256_hex(b""),
            start_token=0,
            end_token=0,
        )
        return (empty,), 0

    step = max(1, cfg.max_tokens - cfg.overlap_tokens)
    chunks: list[ProjectedChunk] = []
    start = 0
    index = 0
    truncated = 0
    while start < len(tokens):
        if index >= cfg.max_chunks:
            truncated = len(tokens) - start
            break
        end = min(len(tokens), start + cfg.max_tokens)
        piece_tokens = tokens[start:end]
        piece = " ".join(piece_tokens)
        chunks.append(
            ProjectedChunk(
                chunk_index=index,
                text=piece,
                content_sha256=sha256_hex(piece.encode("utf-8")),
                start_token=start,
                end_token=end,
            )
        )
        index += 1
        if end >= len(tokens):
            break
        start += step
    return tuple(chunks), truncated


def _intent_text_from_mapping(fm: Mapping[str, Any]) -> str:
    intent = fm.get("intent") or {}
    parts = [
        str(intent.get("does") or ""),
        str(intent.get("use_when") or ""),
        str(intent.get("not_when") or ""),
    ]
    return "\n".join(p for p in parts if p)


def _triggers_text_from_mapping(fm: Mapping[str, Any]) -> str:
    routing = fm.get("routing") or {}
    triggers = fm.get("triggers") or {}
    positive = list(routing.get("positive") or triggers.get("positive") or [])
    negative = list(routing.get("negative") or triggers.get("negative") or [])
    return "\n".join([*(f"+ {t}" for t in positive), *(f"- {t}" for t in negative)])


def _body_prose(body: Any) -> str:
    """Reconstruct preserved procedure/pitfalls/examples prose (not steps-only)."""
    sections: list[str] = []
    procedure = getattr(body, "procedure", []) or []
    if procedure:
        lines = [f"{getattr(step, 'n', i + 1)}. {step.text}" for i, step in enumerate(procedure)]
        sections.append("## Procedure\n" + "\n".join(lines))
    procedure_raw = getattr(body, "procedure_raw", "") or ""
    if procedure_raw.strip():
        sections.append("## Procedure (raw)\n" + procedure_raw.strip())
    pitfalls = getattr(body, "pitfalls", []) or []
    if pitfalls:
        lines = [f"- {p.text}" for p in pitfalls]
        sections.append("## Pitfalls\n" + "\n".join(lines))
    examples = getattr(body, "examples", []) or []
    if examples:
        lines = [("+" if e.positive else "-") + f" {e.text}" for e in examples]
        sections.append("## Examples\n" + "\n".join(lines))
    return "\n\n".join(sections)


def _resolve_body_digest(
    *,
    artifact: EngramV1 | Engram | Mapping[str, Any],
    fm: Mapping[str, Any],
    raw_body_text: str | None,
    projected_body_text: str,
) -> str:
    """Bind ``body_digest`` to normative ``routing.body_digest`` (S02)."""
    if isinstance(artifact, EngramV1):
        return artifact.frontmatter.routing.body_digest
    routing = fm.get("routing") or {}
    declared = routing.get("body_digest")
    if isinstance(declared, str) and len(declared) == 64:
        return declared
    source = raw_body_text if raw_body_text is not None else projected_body_text
    return routing_body_digest(source)


def project_artifact(
    artifact: EngramV1 | Engram | Mapping[str, Any],
    *,
    chunker: ChunkerConfig | None = None,
    raw_body_text: str | None = None,
) -> FullContentProjection:
    """Build the full-content projection for an Engram artifact or dict fixture."""
    cfg = chunker or ChunkerConfig()
    if isinstance(artifact, (EngramV1, Engram)):
        fm = artifact.frontmatter.model_dump(mode="json")
        engram_id = artifact.id
        revision = int(artifact.frontmatter.version)
        name = artifact.name
        body_text = _body_prose(artifact.body)
    else:
        fm = dict(artifact.get("frontmatter") or artifact)
        engram_id = str(fm["id"])
        revision = int(fm.get("version") or 1)
        name = str(fm["name"])
        body_text = str(artifact.get("body_text") or "")
        if raw_body_text is None and "raw_body_text" in artifact:
            raw_body_text = str(artifact.get("raw_body_text") or "")

    body_digest = _resolve_body_digest(
        artifact=artifact,
        fm=fm,
        raw_body_text=raw_body_text,
        projected_body_text=body_text,
    )
    title = name
    intent_text = _intent_text_from_mapping(fm)
    triggers_text = _triggers_text_from_mapping(fm)
    symbols = extract_symbols("\n".join([intent_text, triggers_text, body_text]))
    symbol_block = "\n".join(symbols)
    full_payload = {
        "projection_version": PROJECTION_VERSION,
        "engram_id": engram_id,
        "revision": revision,
        "title": title,
        "intent": intent_text,
        "triggers": triggers_text,
        "body": body_text,
        "symbols": list(symbols),
    }
    full_text = "\n\n".join(p for p in (title, intent_text, triggers_text, body_text, symbol_block) if p)
    projection_sha256 = sha256_hex(canonical_json_bytes(full_payload))
    chunks, truncated = chunk_text(full_text, cfg)
    return FullContentProjection(
        engram_id=engram_id,
        revision=revision,
        name=name,
        title=title,
        intent_text=intent_text,
        triggers_text=triggers_text,
        body_text=body_text,
        full_text=full_text,
        projection_sha256=projection_sha256,
        body_digest=body_digest,
        chunks=chunks,
        truncated_chunks=truncated,
        symbols=symbols,
    )


def model_artifact_digest(*, model_name: str, dim: int, extra: Mapping[str, Any] | None = None) -> str:
    """Stable digest for an embedder identity (tests / hashing provider)."""
    payload: dict[str, Any] = {"model_name": model_name, "dim": dim}
    if extra:
        payload["extra"] = dict(extra)
    return sha256_hex(canonical_json_bytes(payload))


@dataclass
class IndexedEntry:
    projection: FullContentProjection
    dense_vec: np.ndarray | None = None
    body_digest: str = ""
    asset_digest: str | None = None

    def resolved_body_digest(self) -> str:
        return self.body_digest or self.projection.body_digest


@dataclass
class GenerationMeta:
    generation_id: str
    snapshot_id: str
    fingerprint: IndexFingerprint
    status: Literal["building", "complete", "published", "superseded", "failed", "rejected"]
    entries: dict[str, IndexedEntry] = field(default_factory=dict)


def _fsync_dir(path: Path) -> None:
    dir_fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def write_bytes_atomic(path: Path, data: bytes) -> None:
    """Atomic replace with fsync of file contents and parent directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def write_text_atomic(path: Path, text: str) -> None:
    write_bytes_atomic(path, text.encode("utf-8"))


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    write_text_atomic(path, json.dumps(payload, sort_keys=True, indent=2) + "\n")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fingerprint_from_dict(fp_raw: Mapping[str, Any]) -> IndexFingerprint:
    chunker_raw = fp_raw.get("chunker") or {}
    return IndexFingerprint(
        provider=str(fp_raw["provider"]),
        model_artifact_digest=str(fp_raw["model_artifact_digest"]),
        dimension=int(fp_raw["dimension"]),
        model_revision=fp_raw.get("model_revision"),
        normalization=str(fp_raw.get("normalization") or "l2"),
        projection_version=str(fp_raw.get("projection_version") or PROJECTION_VERSION),
        tokenizer_id=str(fp_raw.get("tokenizer_id") or TOKENIZER_ID),
        chunker=ChunkerConfig(
            max_tokens=int(chunker_raw.get("max_tokens") or DEFAULT_CHUNK_TOKENS),
            overlap_tokens=int(chunker_raw.get("overlap_tokens") or DEFAULT_CHUNK_OVERLAP),
            max_chunks=int(chunker_raw.get("max_chunks") or DEFAULT_MAX_CHUNKS),
        ),
        index_schema_version=str(fp_raw.get("index_schema_version") or INDEX_SCHEMA_VERSION),
    )


def _entry_from_projection(
    projection: FullContentProjection,
    *,
    dense_vec: np.ndarray | None = None,
    body_digest: str = "",
    asset_digest: str | None = None,
) -> IndexedEntry:
    return IndexedEntry(
        projection=projection,
        dense_vec=dense_vec,
        body_digest=body_digest or projection.body_digest,
        asset_digest=asset_digest,
    )


class IndexCatalog:
    """Durable generation catalog: S03 pointer authority + S05 entry/FTS tables."""

    def __init__(self, conn: sqlite3.Connection, *, model_registry_path: Path | None = None) -> None:
        self.conn = conn
        self.model_registry_path = Path(model_registry_path) if model_registry_path else None
        if self.model_registry_path is not None:
            self.model_registry_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.model_registry_path.exists():
                _write_json_atomic(self.model_registry_path, {})

    def register_model_fingerprint(self, model_name: str, fingerprint: IndexFingerprint) -> None:
        if self.model_registry_path is None:
            return
        models = _read_json(self.model_registry_path)
        digest = fingerprint.model_artifact_digest
        existing = models.get(model_name)
        if existing is not None and existing.get("model_artifact_digest") != digest:
            raise FingerprintConflictError(
                f"model name {model_name!r} already registered with a different artifact digest",
            )
        models[model_name] = {
            "model_artifact_digest": digest,
            "fingerprint_sha256": fingerprint.digest(),
        }
        _write_json_atomic(self.model_registry_path, models)

    def begin(
        self,
        *,
        snapshot_id: str,
        fingerprint: IndexFingerprint,
        generation_id: str | None = None,
        model_name: str | None = None,
    ) -> str:
        if model_name is not None:
            self.register_model_fingerprint(model_name, fingerprint)
        gid = generation_id or f"gen_{uuid.uuid4().hex[:12]}"
        fp_payload = fingerprint.to_dict()
        fp_payload["snapshot_id"] = snapshot_id
        ops.begin_index_generation(
            self.conn,
            generation_id=gid,
            fingerprint=fp_payload,
            fingerprint_digest=fingerprint.digest(),
            schema_version=MAX_KNOWN_SCHEMA_VERSION,
        )
        self.conn.commit()
        return gid

    def add_entry(
        self,
        generation_id: str,
        projection: FullContentProjection,
        *,
        dense_vec: np.ndarray | None = None,
        body_digest: str = "",
        asset_digest: str | None = None,
    ) -> None:
        entry = _entry_from_projection(
            projection,
            dense_vec=dense_vec,
            body_digest=body_digest,
            asset_digest=asset_digest,
        )
        if dense_vec is not None:
            if dense_vec.dtype != np.float32:
                dense_vec = np.asarray(dense_vec, dtype=np.float32)
            if int(dense_vec.shape[0]) != self._fingerprint_dim(generation_id):
                raise IndexGenerationError(
                    f"dense vector dim {dense_vec.shape} != fingerprint dim",
                )
        p = entry.projection
        self.conn.execute(
            """
            INSERT INTO index_entry (
              generation_id, engram_id, revision, projection_sha256, body_digest,
              asset_digest, name, title, intent_text, triggers_text, body_text,
              full_text, symbols_json, chunks_json, truncated_chunks, dense_dim, dense_vec
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(generation_id, engram_id) DO UPDATE SET
              revision=excluded.revision,
              projection_sha256=excluded.projection_sha256,
              body_digest=excluded.body_digest,
              asset_digest=excluded.asset_digest,
              name=excluded.name,
              title=excluded.title,
              intent_text=excluded.intent_text,
              triggers_text=excluded.triggers_text,
              body_text=excluded.body_text,
              full_text=excluded.full_text,
              symbols_json=excluded.symbols_json,
              chunks_json=excluded.chunks_json,
              truncated_chunks=excluded.truncated_chunks,
              dense_dim=excluded.dense_dim,
              dense_vec=excluded.dense_vec
            """,
            (
                generation_id,
                p.engram_id,
                p.revision,
                p.projection_sha256,
                entry.resolved_body_digest(),
                entry.asset_digest,
                p.name,
                p.title,
                p.intent_text,
                p.triggers_text,
                p.body_text,
                p.full_text,
                json.dumps(list(p.symbols), separators=(",", ":")),
                json.dumps([asdict(c) for c in p.chunks], separators=(",", ":")),
                p.truncated_chunks,
                None if dense_vec is None else int(dense_vec.shape[0]),
                None if dense_vec is None else dense_vec.tobytes(),
            ),
        )
        self.conn.execute(
            "DELETE FROM index_fts WHERE generation_id = ? AND engram_id = ?",
            (generation_id, p.engram_id),
        )
        self.conn.execute(
            "INSERT INTO index_fts(generation_id, engram_id, title, intent, triggers, body) "
            "VALUES (?,?,?,?,?,?)",
            (generation_id, p.engram_id, p.title, p.intent_text, p.triggers_text, p.body_text),
        )
        self.conn.commit()

    def _fingerprint_dim(self, generation_id: str) -> int:
        row = self.conn.execute(
            "SELECT fingerprint_json FROM index_generation WHERE generation_id = ?",
            (generation_id,),
        ).fetchone()
        if row is None:
            raise IndexGenerationError(f"unknown generation {generation_id!r}")
        return int(json.loads(row["fingerprint_json"])["dimension"])

    def _load_entries(self, generation_id: str) -> dict[str, IndexedEntry]:
        rows = self.conn.execute(
            "SELECT * FROM index_entry WHERE generation_id = ?",
            (generation_id,),
        ).fetchall()
        entries: dict[str, IndexedEntry] = {}
        for row in rows:
            chunks_raw = json.loads(row["chunks_json"] or "[]")
            proj = FullContentProjection(
                engram_id=str(row["engram_id"]),
                revision=int(row["revision"]),
                name=str(row["name"]),
                title=str(row["title"]),
                intent_text=str(row["intent_text"]),
                triggers_text=str(row["triggers_text"]),
                body_text=str(row["body_text"]),
                full_text=str(row["full_text"]),
                projection_sha256=str(row["projection_sha256"]),
                body_digest=str(row["body_digest"]),
                chunks=tuple(ProjectedChunk(**chunk) for chunk in chunks_raw),
                truncated_chunks=int(row["truncated_chunks"] or 0),
                symbols=tuple(json.loads(row["symbols_json"] or "[]")),
            )
            dense = None
            if row["dense_vec"] is not None and row["dense_dim"] is not None:
                dense = np.frombuffer(row["dense_vec"], dtype=np.float32).copy()
            entries[proj.engram_id] = IndexedEntry(
                projection=proj,
                dense_vec=dense,
                body_digest=str(row["body_digest"]),
                asset_digest=row["asset_digest"],
            )
        return entries

    def validate_against_snapshot(
        self,
        generation_id: str,
        expected_projections: Sequence[FullContentProjection],
        *,
        expected_fingerprint: IndexFingerprint | None = None,
    ) -> None:
        row = self.conn.execute(
            "SELECT fingerprint_json, fingerprint_digest, state FROM index_generation "
            "WHERE generation_id = ?",
            (generation_id,),
        ).fetchone()
        if row is None:
            raise IndexGenerationError(f"unknown generation {generation_id!r}")
        if expected_fingerprint is not None:
            if row["fingerprint_digest"] != expected_fingerprint.digest():
                raise StaleGenerationError(
                    "fingerprint digest mismatch: model/projection config changed",
                )
            stored = json.loads(row["fingerprint_json"])
            if stored.get("model_artifact_digest") != expected_fingerprint.model_artifact_digest:
                raise StaleGenerationError(
                    "model artifact digest mismatch for generation fingerprint",
                )
            if self.model_registry_path is not None and self.model_registry_path.exists():
                models = _read_json(self.model_registry_path)
                for _name, meta in models.items():
                    if meta.get("fingerprint_sha256") == row["fingerprint_digest"]:
                        if meta.get("model_artifact_digest") != expected_fingerprint.model_artifact_digest:
                            raise StaleGenerationError(
                                "model artifact digest changed under registered model name",
                            )
                        break

        entries = self._load_entries(generation_id)
        expected = {p.engram_id: p for p in expected_projections}
        if set(entries) != set(expected):
            raise StaleGenerationError(
                f"generation entry set mismatch: stored={sorted(entries)} expected={sorted(expected)}",
            )
        for engram_id, proj in expected.items():
            stored = entries[engram_id].projection
            if stored.projection_sha256 != proj.projection_sha256:
                raise StaleGenerationError(
                    f"projection digest mismatch for {engram_id}: "
                    f"frontmatter/body projection changed since generation",
                )
            if entries[engram_id].resolved_body_digest() != proj.body_digest:
                raise StaleGenerationError(
                    f"body_digest mismatch for {engram_id}: routing.body_digest drifted",
                )

    def complete(
        self,
        generation_id: str,
        expected_projections: Sequence[FullContentProjection] | None = None,
        *,
        expected_fingerprint: IndexFingerprint | None = None,
    ) -> None:
        if expected_projections is not None:
            self.validate_against_snapshot(
                generation_id,
                expected_projections,
                expected_fingerprint=expected_fingerprint,
            )
        else:
            entries = self._load_entries(generation_id)
            for engram_id, entry in entries.items():
                if not entry.projection.projection_sha256:
                    raise StaleGenerationError(f"missing projection digest for {engram_id}")
                if not entry.resolved_body_digest():
                    raise StaleGenerationError(f"missing body_digest for {engram_id}")
        ops.complete_index_generation(self.conn, generation_id)
        self.conn.commit()

    def publish(self, generation_id: str) -> dict[str, Any]:
        result = ops.publish_index_generation(self.conn, generation_id)
        self.conn.commit()
        return result

    def rollback(self) -> dict[str, Any]:
        result = ops.rollback_index_generation(self.conn)
        self.conn.commit()
        return result

    def active_generation_id(self) -> str | None:
        active = ops.active_index_generation(self.conn)
        if active is None:
            return None
        return str(active["generation_id"])

    def previous_generation_id(self) -> str | None:
        active = ops.active_index_generation(self.conn)
        if active is None:
            return None
        prev = active.get("previous_generation_id")
        return str(prev) if prev else None

    def generation_state(self, generation_id: str) -> str:
        row = self.conn.execute(
            "SELECT state FROM index_generation WHERE generation_id = ?",
            (generation_id,),
        ).fetchone()
        if row is None:
            raise IndexGenerationError(f"unknown generation {generation_id!r}")
        return str(row["state"])

    def pin(self, generation_id: str | None = None) -> GenerationMeta:
        gid = generation_id if generation_id is not None else self.active_generation_id()
        if gid is None:
            raise IncompleteGenerationError("no active index generation")
        row = self.conn.execute(
            "SELECT fingerprint_json, state FROM index_generation WHERE generation_id = ?",
            (gid,),
        ).fetchone()
        if row is None:
            raise IndexGenerationError(f"unknown generation {gid!r}")
        state = str(row["state"])
        if state not in {"complete", "published"}:
            raise IncompleteGenerationError(
                f"generation {gid!r} is not complete; partial builds are not routable",
            )
        if generation_id is None and state != "published":
            raise IncompleteGenerationError(
                f"generation {gid!r} is not the published active pointer",
            )
        fp_raw = json.loads(row["fingerprint_json"])
        snapshot_id = str(fp_raw.get("snapshot_id") or "")
        return GenerationMeta(
            generation_id=gid,
            snapshot_id=snapshot_id,
            fingerprint=_fingerprint_from_dict(fp_raw),
            status=state,  # type: ignore[arg-type]
            entries=self._load_entries(gid),
        )


# ── filesystem sidecar (optional dense blob cache; not pointer authority) ──


class GenerationStore:
    """Optional filesystem sidecar for generation artifacts.

    Pointer / publish / rollback authority is :class:`IndexCatalog` (S03 tables).
    This store only persists an audit copy of entries + COMPLETE marker with
    fsync, keyed by generation id already recorded in SQLite.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "generations").mkdir(exist_ok=True)

    def _gen_dir(self, generation_id: str) -> Path:
        return self.root / "generations" / generation_id

    def write_sidecar(
        self,
        generation_id: str,
        *,
        snapshot_id: str,
        fingerprint: IndexFingerprint,
        entries: Mapping[str, IndexedEntry],
    ) -> None:
        gdir = self._gen_dir(generation_id)
        gdir.mkdir(parents=True, exist_ok=True)
        meta = {
            "generation_id": generation_id,
            "snapshot_id": snapshot_id,
            "fingerprint": fingerprint.to_dict(),
            "fingerprint_sha256": fingerprint.digest(),
            "status": "building",
        }
        _write_json_atomic(gdir / "meta.json", meta)
        serializable: dict[str, Any] = {}
        dense: dict[str, np.ndarray] = {}
        for engram_id, entry in entries.items():
            p = entry.projection
            serializable[engram_id] = {
                "revision": p.revision,
                "name": p.name,
                "title": p.title,
                "intent_text": p.intent_text,
                "triggers_text": p.triggers_text,
                "body_text": p.body_text,
                "full_text": p.full_text,
                "projection_sha256": p.projection_sha256,
                "body_digest": entry.resolved_body_digest(),
                "chunks": [asdict(c) for c in p.chunks],
                "truncated_chunks": p.truncated_chunks,
                "symbols": list(p.symbols),
                "asset_digest": entry.asset_digest,
            }
            if entry.dense_vec is not None:
                dense[engram_id] = entry.dense_vec
        _write_json_atomic(gdir / "entries.json", serializable)
        if dense:
            np.savez(gdir / "dense.npz", **dense)  # type: ignore[arg-type]

    def mark_complete(self, generation_id: str) -> None:
        gdir = self._gen_dir(generation_id)
        meta_path = gdir / "meta.json"
        if meta_path.is_file():
            meta = _read_json(meta_path)
            meta["status"] = "complete"
            _write_json_atomic(meta_path, meta)
        write_text_atomic(gdir / "COMPLETE", "ok\n")

    def is_complete(self, generation_id: str) -> bool:
        return (self._gen_dir(generation_id) / "COMPLETE").is_file()


def open_sparse_connection(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Return the durable DB connection used for generation-scoped FTS queries."""
    return conn
