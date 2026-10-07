"""Lossless static SkillRet conversion; no skill execution or empirical inference."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from magicite.engram import parser, skillmd
from magicite.eval.digests import sha256_json
from magicite.eval.manifests import ArtifactRef, CorpusManifest, QueryRecord


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("utf-8")


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def contained(root: Path, relative: str) -> Path:
    path = Path(relative)
    target = (root / path).resolve()
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in relative
        or not target.is_relative_to(root.resolve())
    ):
        raise ValueError("unsafe corpus path")
    return target


def verify_raw(root: Path, lock: dict[str, Any]) -> list[dict[str, Any]]:
    paths = [row["path"] for row in lock["artifacts"]]
    if len(paths) != len(set(paths)):
        raise ValueError("duplicate source-lock path")
    records = []
    for row in lock["artifacts"]:
        path = contained(root, row["path"])
        if not path.is_file() or path.stat().st_size != row["size_bytes"]:
            raise ValueError("missing or length-mismatched raw source: " + row["path"])
        expected = row["expected_identity"]
        actual = digest(path)
        if expected["algorithm"] == "sha256":
            identity = actual
        elif expected["algorithm"] == "git-blob-sha1":
            value = hashlib.sha1(b"blob " + str(path.stat().st_size).encode() + b"\0")
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    value.update(block)
            identity = value.hexdigest()
        else:
            raise ValueError("unsupported raw object identity")
        if identity != expected["value"]:
            raise ValueError("raw source object mismatch: " + row["path"])
        records.append(
            {
                "path": row["path"],
                "bytes": path.stat().st_size,
                "sha256": actual,
                "expected_identity": expected,
            }
        )
    return records


def rows(path: Path) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    with path.open("rb") as stream:
        for number, raw in enumerate(stream, 1):
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("JSONL row must be an object")
            yield value, {"line": number, "raw_row_sha256": hashlib.sha256(raw).hexdigest()}


def identity_map(ids: set[str]) -> dict[str, dict[str, str]]:
    mapped = {}
    used = set()
    normalized = set()
    for source in sorted(ids):
        parsed = uuid.UUID(source)
        if parsed.hex in normalized:
            raise ValueError("normalized UUID/name/path collision")
        normalized.add(parsed.hex)
        identity = "egr_" + hashlib.sha256(source.encode()).hexdigest()[:8]
        if identity in used:
            raise ValueError("shortened runtime ID collision; explicit resolution required")
        used.add(identity)
        mapped[source] = {"id": identity, "name": "skillret-" + parsed.hex}
    return mapped


def source_body(skill: dict[str, Any]) -> bytes:
    if not isinstance(skill.get("skill_md"), str) or not skill["skill_md"]:
        raise ValueError("missing full supplied skill_md")
    if "body" in skill and skill["body"] != skill["skill_md"]:
        raise ValueError("source body/skill_md aliases disagree")
    return skill["skill_md"].encode("utf-8")


def source_status(skill: dict[str, Any]) -> dict[str, Any]:
    """Inspect inert source text without fetching dependencies or executing instructions."""
    text = source_body(skill).decode("utf-8")
    try:
        skillmd.parse_source(text)
        status, reason = "ACCEPTED", None
    except (ValueError, parser.EngramParseError, AttributeError, TypeError) as exc:
        status, reason = "REJECTED", type(exc).__name__
    references = sorted(
        {
            value
            for value in re.findall(r"\]\(<?([^\s)>]+)", text)
            if not value.startswith(("#", "http:", "https:", "mailto:", "data:"))
        }
    )
    return {
        "strict_import_status": status,
        "strict_import_error_class": reason,
        "relative_references": references,
        "referenced_assets": "UNAVAILABLE_NOT_FETCHED" if references else "NONE_DETECTED",
        "reference_detection_scope": "Markdown inline links only; not exhaustive dependency discovery",
        "runtime_admission": "UNEVALUATED",
    }


def verify_targets(query: dict[str, Any], related: dict[str, Any], skills: dict[str, Any]) -> None:
    targets, names, count = query.get("skill_ids"), query.get("skill_names"), query.get("k")
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or count <= 0
        or not isinstance(targets, list)
        or not all(isinstance(value, str) for value in targets)
        or not isinstance(names, list)
        or len(targets) != count
        or len(names) != count
        or len(set(targets)) != count
        or not related
        or set(targets) != set(related)
        or count != len(related)
    ):
        raise ValueError("query target/k and positive qrels disagree")
    if [skills[sid]["name"] for sid in targets] != names:
        raise ValueError("query target skill names disagree")


def native_wrapper(skill: dict[str, Any], mapped: dict[str, str], sidecar: str) -> bytes:
    body = source_body(skill)
    description = skill.get("description")
    name = skill.get("name")
    if (
        not isinstance(description, str)
        or not description.strip()
        or not isinstance(name, str)
        or not name.strip()
    ):
        raise ValueError("source name/description unavailable for generated metadata")
    # Generated metadata comes only from the skill, never queries or qrels.
    front = {
        "spec": "engram/0.2",
        "name": mapped["name"],
        "id": mapped["id"],
        "version": 1,
        "provenance": "imported",
        "intent": {"does": description, "use_when": description},
        "triggers": {"positive": [name], "negative": []},
        "trust": {"origin": "imported", "verification_status": "pending"},
        "skillret_conversion": {
            "source_uuid": skill["id"],
            "source_body_path": sidecar,
            "source_body_sha256": hashlib.sha256(body).hexdigest(),
            "actor": "skillret-lossless-wrapper-v1",
            "runtime_admission": "UNEVALUATED",
        },
    }
    return b"---\n" + canonical(front) + b"---\n## Procedure\n" + body


def _write(root: Path, relative: str, value: Any) -> dict[str, Any]:
    path = contained(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = value if isinstance(value, bytes) else canonical(value)
    path.write_bytes(content)
    return {"path": relative, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}


def _skills(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    data, references = {}, {}
    for row, reference in rows(path):
        identity = row.get("id")
        if not isinstance(identity, str) or identity in data:
            raise ValueError("missing or duplicate skill identity")
        source_body(row)
        data[identity] = row
        references[identity] = reference
    return data, references


def convert(
    raw: Path,
    output: Path,
    lock: dict[str, Any],
    *,
    expected_counts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    verify_raw(raw, lock)
    if output.exists():
        raise ValueError("fresh conversion output required")
    master, master_refs = _skills(raw / "data/skills.jsonl")
    split_skills, split_refs = {}, {}
    for split in ("train", "test"):
        data, references = _skills(raw / f"data/skills/{split}.jsonl")
        for identity, row in data.items():
            if identity not in master or row != master[identity]:
                raise ValueError("split skill absent or conflicts with master")
        split_skills[split], split_refs[split] = data, references
    mapping = identity_map(set(master))
    output.mkdir(parents=True)
    files = [_write(output, "identity-map.json", mapping)]
    inventories: dict[str, list[dict[str, Any]]] = {split: [] for split in ("master", "train", "test")}
    licenses: Counter[str] = Counter()
    gaps: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    body_owners: dict[str, str] = {}
    normalized_owners: dict[str, str] = {}
    for identity, skill in sorted(master.items()):
        mapped = mapping[identity]
        stem = f"bodies/{mapped['id'][4:6]}/{mapped['name']}"
        sidecar = stem + ".SKILL.md"
        body = source_body(skill)
        files.append(_write(output, sidecar, body))
        wrapper = stem + ".egr.md"
        files.append(_write(output, wrapper, native_wrapper(skill, mapped, sidecar)))
        artifact, _ = parser.load_artifact_file(output / wrapper, registry_root=output)
        if artifact.id != mapped["id"] or artifact.name != mapped["name"]:
            raise ValueError("native codec identity mismatch")
        item = {
            "id": artifact.id,
            "name": artifact.name,
            "source_uuid": identity,
            "path": wrapper,
            "sha256": files[-1]["sha256"],
            "bytes": files[-1]["bytes"],
            "source_body": sidecar,
            "source_body_sha256": files[-2]["sha256"],
        }
        inventories["master"].append(item)
        for split, data in split_skills.items():
            if identity in data:
                inventories[split].append(item)
        metadata = {key: value for key, value in skill.items() if key not in {"body", "skill_md"}}
        files.append(
            _write(
                output,
                stem + ".source.json",
                {
                    "metadata": metadata,
                    "original_source_status": source_status(skill),
                    "raw_source": {"path": "data/skills.jsonl", **master_refs[identity]},
                    "split_rows": {
                        split: split_refs[split][identity]
                        for split in split_refs
                        if identity in split_refs[split]
                    },
                    "body_aliases_verified": "body" in skill,
                    "sidecar": files[-2],
                },
            )
        )
        licenses[str(skill.get("license"))] += 1
        missing = [
            key for key in ("license", "author", "source_url", "raw_url", "repo") if not skill.get(key)
        ]
        if missing:
            gaps.append({"source_uuid": identity, "missing_declarations": missing})
        for mode, content, owners in (
            ("exact", body, body_owners),
            ("whitespace_normalized", b" ".join(body.split()), normalized_owners),
        ):
            key = hashlib.sha256(content).hexdigest()
            if key in owners:
                duplicates.append({"kind": mode, "source_uuid": identity, "other_uuid": owners[key]})
            else:
                owners[key] = identity
    split_reports = {}
    original_ids: dict[str, list[str]] = {}
    for split in ("train", "test"):
        query_data, query_refs = {}, {}
        for query, reference in rows(raw / f"data/queries/{split}.jsonl"):
            qid = query.get("id")
            if (
                not isinstance(qid, str)
                or qid in query_data
                or not isinstance(query.get("query"), str)
                or not query["query"].strip()
            ):
                raise ValueError("missing/duplicate/invalid query")
            query_data[qid], query_refs[qid] = query, reference
        qrels: dict[str, dict[str, int | float]] = {}
        pairs: set[tuple[str, str]] = set()
        for relation, _reference in rows(raw / f"data/qrels/{split}.jsonl"):
            qid, sid, relevance = relation["query_id"], relation["skill_id"], relation["relevance"]
            if qid not in query_data or sid not in split_skills[split] or (qid, sid) in pairs:
                raise ValueError("dangling, cross-split or duplicate qrel")
            if isinstance(relevance, bool) or not isinstance(relevance, (int, float)) or relevance <= 0:
                raise ValueError("unsupported qrel relevance")
            pairs.add((qid, sid))
            qrels.setdefault(qid, {})[sid] = relevance
        runtime, scoring, records = [], [], []
        for qid, query in query_data.items():
            related = qrels.get(qid, {})
            verify_targets(query, related, split_skills[split])
            runtime_id = split + ":" + qid
            runtime.append(
                {"query_id": runtime_id, "query_text": query["query"], "compatibility_context": {}}
            )
            scores = {mapping[sid]["id"]: value for sid, value in related.items()}
            records.append(
                QueryRecord(
                    query_id=runtime_id,
                    query_text=query["query"],
                    split="development" if split == "train" else "final",
                    relevance=scores,
                    group_id="bookkeeping:" + runtime_id,
                    provenance={
                        "origin": "imported_official",
                        "upstream_split": split,
                        "group_independence": "UNRESOLVED",
                    },
                )
            )
            scoring.append(
                {
                    "query_id": runtime_id,
                    "upstream_query": query,
                    "qrels": related,
                    "raw_query": query_refs[qid],
                    "runtime_relevance": scores,
                    "bookkeeping_group_not_independent": True,
                }
            )
            original_ids.setdefault(str(query.get("original_id")), []).append(runtime_id)
        runtime_file = _write(output, f"{split}/runtime-queries.json", runtime)
        files.append(runtime_file)
        files.append(_write(output, f"{split}/scoring.json", scoring))
        inventory_file = _write(output, f"{split}/inventory.json", inventories[split])
        files.append(inventory_file)
        query_payload = [record.to_dict() for record in records]
        corpus = CorpusManifest(
            corpus_id="skillret-v1.1/" + split,
            artifacts=tuple(
                ArtifactRef(path=row["path"], sha256=row["sha256"], role="skill", byte_length=row["bytes"])
                for row in inventories[split]
            ),
            queries=tuple(records),
            label_origin="imported_official",
            dataset_revision=lock["revision"],
            license="source-body-declarations-unverified; metadata Apache-2.0",
            content_identity_sha256=sha256_json({"queries": query_payload}),
        )
        files.append(_write(output, f"{split}/corpus-manifest.json", corpus.to_dict()))
        split_reports[split] = {
            "skills": len(split_skills[split]),
            "queries": len(query_data),
            "qrels": len(pairs),
            "candidate_pool": "official split only",
            "runtime_queries_sha256": runtime_file["sha256"],
            "inventory_sha256": inventory_file["sha256"],
        }
    files.append(_write(output, "master/inventory.json", inventories["master"]))
    overlap = sorted(set(split_skills["train"]) & set(split_skills["test"]))
    name_counts = Counter(row["name"] for row in master.values())
    accounting = {
        "master_skills": len(master),
        "splits": split_reports,
        "skill_split_overlap": overlap,
        "master_extras": sorted(set(master) - set(split_skills["train"]) - set(split_skills["test"])),
        "display_name_collisions": {key: value for key, value in name_counts.items() if value > 1},
        "duplicate_bodies": duplicates,
        "original_id_relationships": original_ids,
        "license_declarations": dict(licenses),
        "provenance_gaps": gaps,
        "original_repository_immutability": "UNRESOLVED: URLs are declarations, often moving main",
        "license_audit": "UNEVALUATED; no blanket per-skill license substitution",
        "runtime_admission": "UNEVALUATED",
        "evaluation": "NOT_RUN",
    }
    files.append(_write(output, "accounting.json", accounting))
    expected_counts = expected_counts or {
        "master": 17810,
        "train": (10123, 63259, 127190),
        "test": (6006, 4392, 7187),
    }
    actual_counts = {
        "master": len(master),
        **{split: (row["skills"], row["queries"], row["qrels"]) for split, row in split_reports.items()},
    }
    if actual_counts != expected_counts:
        raise ValueError("actual source counts disagree with pinned card declarations")
    index = {row["path"]: {"sha256": row["sha256"], "bytes": row["bytes"]} for row in files}
    seal = {
        "schema": "magicite/skillret-converted-seal/1",
        "revision": lock["revision"],
        "files": index,
        "aggregate_sha256": sha256_json(index),
        "status": "READY_FOR_EVALUATION_INPUT",
        "scope": "Codec/pool freeze only; no runtime admission, embedding, E3 or E6 acceptance",
    }
    _write(output, "seal.json", seal)
    return seal
