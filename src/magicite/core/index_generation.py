"""Index generation, full-content projection, and atomic pointers (C3 / C11 / S05).

Pure generation builder: S03 owns durable migration adoption; S07 wires route
pinning. This module is independently testable against Engram 1.0 fixtures.

Provisional FTS DDL lives in :data:`PROVISIONAL_INDEX_DDL` for S03 to serialize
into the migration authority — do not claim a production migration number here.
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

from magicite.engram.digests import canonical_json_bytes, sha256_hex
from magicite.engram.model import Engram
from magicite.engram.model_v1 import EngramV1

PROJECTION_VERSION = "full-content/1"
INDEX_SCHEMA_VERSION = "s05-provisional/1"
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
    r"|(?:\bE_[A-Z0-9_]+\b)"
)

#: Isolated DDL artifact for S03 migration authority (C8 / C9). Not applied by S05.
PROVISIONAL_INDEX_DDL = """\
-- S05 provisional retrieval-index DDL (C3/C11). S03 assigns the migration
-- sequence number at integration time. Do not invent 003_*.sql in S05.

CREATE TABLE IF NOT EXISTS idx_generation (
  generation_id   TEXT PRIMARY KEY,
  snapshot_id     TEXT NOT NULL,
  fingerprint_json TEXT NOT NULL,
  fingerprint_sha256 TEXT NOT NULL,
  status          TEXT NOT NULL CHECK (status IN ('building','complete','rejected')),
  created_at      TEXT NOT NULL,
  completed_at    TEXT
);

CREATE TABLE IF NOT EXISTS idx_active_pointer (
  singleton       INTEGER PRIMARY KEY CHECK (singleton = 1),
  generation_id   TEXT NOT NULL REFERENCES idx_generation(generation_id),
  previous_generation_id TEXT,
  updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS idx_model_registry (
  model_name              TEXT PRIMARY KEY,
  model_artifact_digest   TEXT NOT NULL,
  fingerprint_sha256      TEXT NOT NULL,
  registered_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS idx_entry (
  generation_id       TEXT NOT NULL REFERENCES idx_generation(generation_id) ON DELETE CASCADE,
  engram_id           TEXT NOT NULL,
  revision            INTEGER NOT NULL,
  projection_sha256   TEXT NOT NULL,
  body_digest         TEXT NOT NULL,
  asset_digest        TEXT,
  dense_dim           INTEGER,
  dense_vec           BLOB,
  PRIMARY KEY (generation_id, engram_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS idx_fts USING fts5(
  engram_id UNINDEXED,
  title,
  intent,
  triggers,
  body,
  tokenize = 'unicode61'
);
"""


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
    """Full-content routing projection including preserved prose and symbols."""

    engram_id: str
    revision: int
    name: str
    title: str
    intent_text: str
    triggers_text: str
    body_text: str
    full_text: str
    projection_sha256: str
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


def project_artifact(
    artifact: EngramV1 | Engram | Mapping[str, Any],
    *,
    chunker: ChunkerConfig | None = None,
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

    title = name
    intent_text = _intent_text_from_mapping(fm)
    triggers_text = _triggers_text_from_mapping(fm)
    symbols = extract_symbols("\n".join([intent_text, triggers_text, body_text]))
    # Exact symbols appended so sparse retrieval can match identifiers that
    # tokenization might otherwise split awkwardly.
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
    full_text = "\n\n".join(
        p for p in (title, intent_text, triggers_text, body_text, symbol_block) if p
    )
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


@dataclass
class GenerationMeta:
    generation_id: str
    snapshot_id: str
    fingerprint: IndexFingerprint
    status: Literal["building", "complete", "rejected"]
    entries: dict[str, IndexedEntry] = field(default_factory=dict)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


class GenerationStore:
    """Filesystem-backed generation store with atomic active pointer (C11).

    Layout::

        {root}/
          pointer.json
          models.json
          generations/{generation_id}/
            meta.json
            entries.json
            dense.npz          (optional)
            sparse.sqlite      (FTS5)
            COMPLETE
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "generations").mkdir(exist_ok=True)
        if not (self.root / "models.json").exists():
            _write_json_atomic(self.root / "models.json", {})
        if not (self.root / "pointer.json").exists():
            _write_json_atomic(
                self.root / "pointer.json",
                {"active": None, "previous": None},
            )

    def _gen_dir(self, generation_id: str) -> Path:
        return self.root / "generations" / generation_id

    def register_model_fingerprint(self, model_name: str, fingerprint: IndexFingerprint) -> None:
        """Reject model-name reuse when the artifact digest changed (C11 / AC-S05-03)."""
        models = _read_json(self.root / "models.json")
        digest = fingerprint.model_artifact_digest
        fp_sha = fingerprint.digest()
        existing = models.get(model_name)
        if existing is not None and existing.get("model_artifact_digest") != digest:
            raise FingerprintConflictError(
                f"model name {model_name!r} already registered with a different artifact digest",
            )
        models[model_name] = {
            "model_artifact_digest": digest,
            "fingerprint_sha256": fp_sha,
        }
        _write_json_atomic(self.root / "models.json", models)

    def begin(
        self,
        *,
        snapshot_id: str,
        fingerprint: IndexFingerprint,
        generation_id: str | None = None,
        model_name: str | None = None,
    ) -> GenerationBuilder:
        if model_name is not None:
            self.register_model_fingerprint(model_name, fingerprint)
        gid = generation_id or f"gen_{uuid.uuid4().hex[:12]}"
        gdir = self._gen_dir(gid)
        if gdir.exists():
            raise IndexGenerationError(f"generation {gid!r} already exists")
        gdir.mkdir(parents=True)
        meta = {
            "generation_id": gid,
            "snapshot_id": snapshot_id,
            "fingerprint": fingerprint.to_dict(),
            "fingerprint_sha256": fingerprint.digest(),
            "status": "building",
        }
        _write_json_atomic(gdir / "meta.json", meta)
        _write_json_atomic(gdir / "entries.json", {})
        return GenerationBuilder(self, gid, snapshot_id, fingerprint)

    def active_generation_id(self) -> str | None:
        ptr = _read_json(self.root / "pointer.json")
        active = ptr.get("active")
        return str(active) if active else None

    def previous_generation_id(self) -> str | None:
        ptr = _read_json(self.root / "pointer.json")
        prev = ptr.get("previous")
        return str(prev) if prev else None

    def is_complete(self, generation_id: str) -> bool:
        return (self._gen_dir(generation_id) / "COMPLETE").is_file()

    def load_meta(self, generation_id: str) -> dict[str, Any]:
        path = self._gen_dir(generation_id) / "meta.json"
        if not path.is_file():
            raise IndexGenerationError(f"unknown generation {generation_id!r}")
        return _read_json(path)

    def pin(self, generation_id: str | None = None) -> GenerationMeta:
        """Pin a complete generation for routing reads. Incomplete builds fail closed."""
        gid = generation_id if generation_id is not None else self.active_generation_id()
        if gid is None:
            raise IncompleteGenerationError("no active index generation")
        if not self.is_complete(gid):
            raise IncompleteGenerationError(
                f"generation {gid!r} is not complete; partial builds are not routable",
            )
        return self._load_generation(gid)

    def publish(self, generation_id: str) -> None:
        """Atomically swap the active pointer to a complete generation."""
        if not self.is_complete(generation_id):
            raise IncompleteGenerationError(
                f"cannot publish incomplete generation {generation_id!r}",
            )
        ptr = _read_json(self.root / "pointer.json")
        previous = ptr.get("active")
        _write_json_atomic(
            self.root / "pointer.json",
            {"active": generation_id, "previous": previous},
        )

    def rollback(self, *, expected_current: str, prior_generation_id: str) -> None:
        """Atomically select the previous complete generation (C11)."""
        ptr = _read_json(self.root / "pointer.json")
        if ptr.get("active") != expected_current:
            raise IndexGenerationError(
                f"active pointer is {ptr.get('active')!r}, expected {expected_current!r}",
            )
        if not self.is_complete(prior_generation_id):
            raise IncompleteGenerationError(
                f"rollback target {prior_generation_id!r} is not a complete generation",
            )
        _write_json_atomic(
            self.root / "pointer.json",
            {"active": prior_generation_id, "previous": expected_current},
        )

    def validate_against_snapshot(
        self,
        generation_id: str,
        expected_projections: Sequence[FullContentProjection],
        *,
        expected_fingerprint: IndexFingerprint | None = None,
    ) -> None:
        """Reject stale generations when projection or fingerprint digests drift."""
        meta = self.load_meta(generation_id)
        if expected_fingerprint is not None:
            stored_fp = meta.get("fingerprint_sha256")
            if stored_fp != expected_fingerprint.digest():
                raise StaleGenerationError(
                    "fingerprint digest mismatch: model/projection config changed",
                )
            # Same-name model artifact change is also enforced via models.json.
            models = _read_json(self.root / "models.json")
            provider = expected_fingerprint.provider
            # Look up by common model_name keys registered at begin().
            for _name, row in models.items():
                if row.get("fingerprint_sha256") == stored_fp:
                    if row.get("model_artifact_digest") != expected_fingerprint.model_artifact_digest:
                        raise StaleGenerationError(
                            "model artifact digest changed under registered model name",
                        )
                    break
            else:
                # Fingerprint object itself carries the digest; compare fields.
                stored = meta.get("fingerprint") or {}
                if stored.get("model_artifact_digest") != expected_fingerprint.model_artifact_digest:
                    raise StaleGenerationError(
                        "model artifact digest mismatch for generation fingerprint",
                    )
                if stored.get("provider") == provider and stored.get("model_artifact_digest") != (
                    expected_fingerprint.model_artifact_digest
                ):
                    raise StaleGenerationError("provider fingerprint conflict")

        entries = _read_json(self._gen_dir(generation_id) / "entries.json")
        expected = {p.engram_id: p for p in expected_projections}
        if set(entries) != set(expected):
            raise StaleGenerationError(
                f"generation entry set mismatch: stored={sorted(entries)} expected={sorted(expected)}",
            )
        for engram_id, proj in expected.items():
            stored_sha = entries[engram_id].get("projection_sha256")
            if stored_sha != proj.projection_sha256:
                raise StaleGenerationError(
                    f"projection digest mismatch for {engram_id}: "
                    f"frontmatter/body projection changed since generation",
                )

    def _load_generation(self, generation_id: str) -> GenerationMeta:
        meta_raw = self.load_meta(generation_id)
        fp_raw = meta_raw["fingerprint"]
        chunker_raw = fp_raw.get("chunker") or {}
        fingerprint = IndexFingerprint(
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
        entries_raw = _read_json(self._gen_dir(generation_id) / "entries.json")
        dense_path = self._gen_dir(generation_id) / "dense.npz"
        dense_map: dict[str, np.ndarray] = {}
        if dense_path.is_file():
            loaded = np.load(dense_path)
            dense_map = {k: loaded[k] for k in loaded.files}

        entries: dict[str, IndexedEntry] = {}
        for engram_id, row in entries_raw.items():
            proj = FullContentProjection(
                engram_id=engram_id,
                revision=int(row["revision"]),
                name=str(row["name"]),
                title=str(row["title"]),
                intent_text=str(row["intent_text"]),
                triggers_text=str(row["triggers_text"]),
                body_text=str(row["body_text"]),
                full_text=str(row["full_text"]),
                projection_sha256=str(row["projection_sha256"]),
                chunks=tuple(
                    ProjectedChunk(**chunk) for chunk in row.get("chunks") or ()
                ),
                truncated_chunks=int(row.get("truncated_chunks") or 0),
                symbols=tuple(row.get("symbols") or ()),
            )
            entries[engram_id] = IndexedEntry(
                projection=proj,
                dense_vec=dense_map.get(engram_id),
                body_digest=str(row.get("body_digest") or ""),
                asset_digest=row.get("asset_digest"),
            )
        status = meta_raw.get("status") or ("complete" if self.is_complete(generation_id) else "building")
        return GenerationMeta(
            generation_id=generation_id,
            snapshot_id=str(meta_raw["snapshot_id"]),
            fingerprint=fingerprint,
            status=status,  # type: ignore[arg-type]
            entries=entries,
        )


class GenerationBuilder:
    """Staged builder: entries accumulate under ``building`` until finalize()."""

    def __init__(
        self,
        store: GenerationStore,
        generation_id: str,
        snapshot_id: str,
        fingerprint: IndexFingerprint,
    ) -> None:
        self.store = store
        self.generation_id = generation_id
        self.snapshot_id = snapshot_id
        self.fingerprint = fingerprint
        self._entries: dict[str, IndexedEntry] = {}
        self._finalized = False

    def add_entry(
        self,
        projection: FullContentProjection,
        *,
        dense_vec: np.ndarray | None = None,
        body_digest: str = "",
        asset_digest: str | None = None,
    ) -> None:
        if self._finalized:
            raise IndexGenerationError("cannot add entries after finalize()")
        if dense_vec is not None:
            if dense_vec.dtype != np.float32:
                dense_vec = np.asarray(dense_vec, dtype=np.float32)
            if dense_vec.shape != (self.fingerprint.dimension,):
                raise IndexGenerationError(
                    f"dense vector dim {dense_vec.shape} != fingerprint dim "
                    f"{self.fingerprint.dimension}",
                )
        self._entries[projection.engram_id] = IndexedEntry(
            projection=projection,
            dense_vec=dense_vec,
            body_digest=body_digest or projection.projection_sha256,
            asset_digest=asset_digest,
        )
        self._flush_entries()

    def _flush_entries(self) -> None:
        gdir = self.store._gen_dir(self.generation_id)
        serializable: dict[str, Any] = {}
        dense: dict[str, np.ndarray] = {}
        for engram_id, entry in self._entries.items():
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
                "chunks": [asdict(c) for c in p.chunks],
                "truncated_chunks": p.truncated_chunks,
                "symbols": list(p.symbols),
                "body_digest": entry.body_digest,
                "asset_digest": entry.asset_digest,
            }
            if entry.dense_vec is not None:
                dense[engram_id] = entry.dense_vec
        _write_json_atomic(gdir / "entries.json", serializable)
        if dense:
            # np.savez kwarg typing rejects Mapping[str, ndarray] under numpy stubs.
            np.savez(gdir / "dense.npz", **dense)  # type: ignore[arg-type]

    def _build_sparse_fts(self) -> None:
        if not fts5_available():
            raise FTS5UnavailableError(
                "SQLite FTS5 is unavailable; configure an explicit dense-only fallback",
            )
        gdir = self.store._gen_dir(self.generation_id)
        db_path = gdir / "sparse.sqlite"
        if db_path.exists():
            db_path.unlink()
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE idx_fts USING fts5("
                "engram_id UNINDEXED, title, intent, triggers, body, "
                "tokenize='unicode61')"
            )
            for engram_id, entry in self._entries.items():
                p = entry.projection
                conn.execute(
                    "INSERT INTO idx_fts(engram_id, title, intent, triggers, body) "
                    "VALUES (?,?,?,?,?)",
                    (engram_id, p.title, p.intent_text, p.triggers_text, p.body_text),
                )
            conn.commit()
        finally:
            conn.close()

    def validate(self, expected_projections: Sequence[FullContentProjection] | None = None) -> None:
        if expected_projections is not None:
            expected = {p.engram_id: p for p in expected_projections}
            if set(self._entries) != set(expected):
                raise StaleGenerationError("builder entry set does not match snapshot")
            for engram_id, proj in expected.items():
                if self._entries[engram_id].projection.projection_sha256 != proj.projection_sha256:
                    raise StaleGenerationError(
                        f"projection digest mismatch for {engram_id} (frontmatter/body changed)",
                    )
        for engram_id, entry in self._entries.items():
            if not entry.projection.projection_sha256:
                raise StaleGenerationError(f"missing projection digest for {engram_id}")

    def finalize(self) -> str:
        """Mark generation complete (visible only after :meth:`GenerationStore.publish`)."""
        self.validate()
        self._build_sparse_fts()
        gdir = self.store._gen_dir(self.generation_id)
        meta = _read_json(gdir / "meta.json")
        meta["status"] = "complete"
        _write_json_atomic(gdir / "meta.json", meta)
        (gdir / "COMPLETE").write_text("ok\n", encoding="utf-8")
        self._finalized = True
        return self.generation_id


def open_sparse_connection(store: GenerationStore, generation_id: str) -> sqlite3.Connection:
    """Open a read-only-ish connection to a generation's FTS database."""
    path = store._gen_dir(generation_id) / "sparse.sqlite"
    if not path.is_file():
        raise IncompleteGenerationError(f"sparse index missing for {generation_id!r}")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
