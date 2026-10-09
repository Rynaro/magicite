"""Bounded data-packet controls; verified bytes do not authenticate declarations.

Preparation authors inspect labels. Evaluation consumers release them only after
an immutable local access intent; this is not rollback/out-of-band protection.
"""

from __future__ import annotations

import json
import math
import os
import re
import unicodedata
import uuid
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from magicite.core.context import RouteContext
from magicite.eval.digests import canonical_json_bytes, sha256_bytes, sha256_json

SCHEMA = "magicite/calibration-data-packet/1"
FREEZE_SCHEMA = "magicite/calibration-data-freeze/1"
FAMILIES = {
    "train": "development",
    "development": "development",
    "calibration": "calibration",
    "test": "final",
    "final": "final",
    "holdout": "final",
}
CLASSES = {"synthetic_fixture", "native_authored_diagnostic", "externally_sourced_observation"}
REQUIRED = {"pool", "runtime", "labels", "partitions", "provenance", "preregistration"}
OFFICIAL_REVISION = "6583d7d2ed07644d0fb8938ed8178f3a7dc42a12"
# Actual previously committed official test identities; not fresh-seal booleans.
EXPOSED_DIGESTS = {
    "463c4e0999f48e9bd19561dc28acaab0b48942b2599160e4922ed42190174ffd",
    "3e2f84de80bee5fa9bb05d1a1c235afb356f376ef6edbc6ef293ceb0995d03a7",
    "b7bbb6ae816eebd9f107b4c26535de57c46aced3d1d551eff82ce1eb44a552cc",
}
HEX = re.compile(r"^[0-9a-f]{64}$")
LIMITS = [
    "Identity/reviewer/group/time declarations are self-attested; hashes verify bytes only",
    "Local receipt cannot prove absence of out-of-band reads or prevent rollback",
    "Fixture/control success does not qualify E2/E3/E6, fitting, activation or release",
]


def normalize_text(text: str) -> str:
    if not isinstance(text, str):
        raise ValueError("query text must be text")
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def partition_errors(rows: list[dict]) -> list[str]:
    """All-pair families; original split names and group identities stay intact."""
    owners: dict[tuple[str, str], str] = {}
    errors = []
    for row in rows:
        split = row.get("split")
        family = FAMILIES.get(split) if isinstance(split, str) else None
        if family is None:
            errors.append("unknown partition split")
            continue
        for kind, value in [
            ("query_id", row.get("query_id")),
            (
                "text",
                normalize_text(row.get("query_text", ""))
                if isinstance(row.get("query_text", ""), str)
                else "",
            ),
        ]:
            if value:
                key = (kind, value)
                if key in owners and owners[key] != family:
                    errors.append(f"{kind} leakage between {owners[key]} and {family}")
                owners[key] = family
        for value in row.get("groups", [row.get("group_id")]):
            if value:
                key = ("group", value)
                if key in owners and owners[key] != family:
                    errors.append(f"group leakage between {owners[key]} and {family}")
                owners[key] = family
    return sorted(set(errors))


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(field + " must be nonempty text")
    return value


def _object(value: Any, field: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(field + " must be an object")
    return value


def _rows(value: Any, field: str, key: str = "query_id") -> dict[str, dict]:
    if not isinstance(value, list) or not value:
        raise ValueError(field + " requires complete nonempty rows")
    result = {}
    for row in value:
        row = _object(row, field)
        identity = _string(row.get(key), field + "." + key)
        if identity in result:
            raise ValueError(field + " duplicate identity")
        result[identity] = row
    return result


def contained(root: Path, relative: Any) -> Path:
    name = _string(relative, "reference path")
    path = Path(name)
    resolved = (root / path).resolve()
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in name
        or not resolved.is_relative_to(root.resolve())
    ):
        raise ValueError("reference escapes packet root")
    return resolved


def reference_bytes(root: Path, ref: Any, inventory: dict[str, str]) -> bytes:
    ref = _object(ref, "reference")
    digest = ref.get("sha256")
    if not isinstance(digest, str) or not HEX.fullmatch(digest):
        raise ValueError("reference SHA256 required")
    path = contained(root, ref.get("path"))
    raw = path.read_bytes()
    if sha256_bytes(raw) != digest:
        raise ValueError("referenced bytes changed")
    inventory[ref["path"]] = digest
    return raw


def _read(root: Path, ref: Any, inventory: dict[str, str]) -> Any:
    value = json.loads(reference_bytes(root, ref, inventory))
    if "count" in ref and (
        isinstance(ref["count"], bool)
        or not isinstance(ref["count"], int)
        or not isinstance(value, list)
        or len(value) != ref["count"]
    ):
        raise ValueError("reference row count mismatch")
    return value


def _timestamp(value: Any) -> datetime:
    value = _string(value, "timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return result


def _context(value: Any) -> dict:
    value = _object(value, "runtime compatibility context")
    allowed = {f.name for f in fields(RouteContext)}
    if not set(value).issubset(allowed):
        raise ValueError("runtime annotation/group/gold injection")
    forbidden = {
        "gold",
        "gold_ids",
        "relevance",
        "labels",
        "group_id",
        "no_match",
        "annotation",
        "provenance",
        "source",
    }

    def walk(item):
        if isinstance(item, dict):
            if set(item) & forbidden:
                raise ValueError("runtime nested annotation injection")
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return value


@dataclass(frozen=True)
class PreparedPacket:
    packet_path: Path
    packet: dict
    inputs: dict[str, str]
    report: dict
    final_ids: tuple[str, ...]


def prepare_packet(packet_path: Path) -> PreparedPacket:
    """Preparation-only validation, explicitly includes authored label inspection."""
    root = packet_path.parent.resolve()
    raw = packet_path.read_bytes()
    packet = _object(json.loads(raw), "packet")
    if packet.get("schema") != SCHEMA:
        raise ValueError("unsupported data packet schema")
    dataset = _object(packet.get("dataset"), "dataset")
    _string(dataset.get("id"), "dataset id")
    revision = _string(dataset.get("revision"), "dataset revision")
    refs = _object(packet.get("files"), "files")
    if set(refs) != REQUIRED:
        raise ValueError(
            "packet must bind complete pool/runtime/labels/partitions/provenance/preregistration"
        )
    inventory = {packet_path.name: sha256_bytes(raw)}
    if any("count" not in ref for name, ref in refs.items() if name != "preregistration"):
        raise ValueError("derived array counts required")
    values = {name: _read(root, ref, inventory) for name, ref in refs.items()}
    pool = _rows(values["pool"], "pool", "candidate_id")
    artifact_paths = set()
    for candidate in pool.values():
        ref = _object(candidate.get("artifact"), "pool artifact reference")
        reference_bytes(root, ref, inventory)
        resolved = contained(root, ref["path"])
        if resolved in artifact_paths:
            raise ValueError("duplicate pool artifact path")
        artifact_paths.add(resolved)
    if dataset.get("pool_count") != len(pool) or isinstance(dataset.get("pool_count"), bool):
        raise ValueError("declared complete pool count differs")
    runtime = _rows(values["runtime"], "runtime")
    labels = _rows(values["labels"], "labels")
    partitions = _rows(values["partitions"], "partitions")
    provenance = _rows(values["provenance"], "provenance")
    if any(set(rows) != set(runtime) for rows in [labels, partitions, provenance]):
        raise ValueError("query/label/partition/provenance membership differs")
    evidence = _object(packet.get("evidence"), "typed evidence inventory")
    documents = {
        name: _object(_read(root, ref, inventory), "evidence declaration") for name, ref in evidence.items()
    }

    def declaration(name):
        if name not in documents:
            raise ValueError("missing referenced provenance evidence")
        return documents[name]

    prereg = _object(values["preregistration"], "preregistration")
    _string(prereg.get("experiment_id"), "preregistered experiment id")
    cutoff = _timestamp(packet["temporal_cutoff"]) if packet.get("temporal_cutoff") is not None else None
    group_rows = []
    final_ids = []
    limitations = list(LIMITS)
    classes = set()
    source_group_sets: dict[str, set[str]] = {}
    for qid, query in runtime.items():
        if set(query) != {"query_id", "query_text", "compatibility_context"}:
            raise ValueError("runtime rows must contain only query identity/text/context")
        _string(query.get("query_text"), "query text")
        _context(query["compatibility_context"])
        split = partitions[qid].get("split")
        if split not in FAMILIES:
            raise ValueError("unknown preserved split")
        if FAMILIES[split] == "final":
            final_ids.append(qid)
        prov = provenance[qid]
        kind = prov.get("evidence_class")
        if kind not in CLASSES:
            raise ValueError("typed evidence class required")
        classes.add(kind)
        source = _object(prov.get("source"), "source declaration")
        source_id = _string(source.get("source_id"), "source identity")
        source_revision = _string(source.get("revision"), "source revision")
        doc = declaration(source.get("evidence_ref"))
        if doc.get("evidence_class") != kind:
            raise ValueError("fixture/source evidence class relabeling")
        if doc.get("source_id") != source_id or doc.get("revision") != source_revision:
            raise ValueError("source revision/identity declaration contradicts evidence bytes")
        keys = source.get("group_keys")
        groups = ["source:" + source_id, "evidence:" + evidence[source["evidence_ref"]]["sha256"]]
        if keys is None:
            if doc.get("group_keys") is not None:
                raise ValueError("unavailable group declaration contradicts known referenced source groups")
            _string(source.get("unavailable_reason"), "unavailable group reason")
            limitations.append(qid + ": related source grouping unavailable")
        else:
            keys = _object(keys, "source group keys")
            if set(keys) != {"task", "repository", "session"} or doc.get("group_keys") != keys:
                raise ValueError("group declarations contradict referenced source")
            groups += [k + ":" + _string(v, "group key") for k, v in keys.items()]
        related = source.get("related_sources", [])
        if not isinstance(related, list) or any(not isinstance(x, str) or not x for x in related):
            raise ValueError("related source declarations invalid")
        known_related = doc.get("related_sources", [])
        if not isinstance(known_related, list) or any(not isinstance(x, str) or not x for x in known_related):
            raise ValueError("referenced related source declarations invalid")
        if not set(known_related).issubset(related):
            raise ValueError("related group declaration hides known referenced source aliases")
        source_group_sets.setdefault(source_id, set()).update(groups)
        source_group_sets[source_id].update("source:" + x for x in related)
        annotation = _object(prov.get("annotation"), "annotation")
        author = _string(annotation.get("author_id"), "annotation author")
        if annotation.get("independent_review") is True:
            reviewer = _string(annotation.get("reviewer_id"), "annotation reviewer")
            if author == reviewer:
                raise ValueError("author and reviewer must differ")
            reviewed = declaration(annotation.get("evidence_ref"))
            if reviewed.get("author_id") != author or reviewed.get("reviewer_id") != reviewer:
                raise ValueError("review declaration contradicts evidence")
        else:
            _string(annotation.get("unavailable_reason"), "review unavailable reason")
            limitations.append(qid + ": independent annotation review unavailable")
        temporal = _object(prov.get("temporal"), "temporal")
        if temporal.get("unavailable_reason"):
            _string(temporal["unavailable_reason"], "temporal unavailable reason")
            if set(temporal) & {"event_at", "collected_at", "annotated_at", "evidence_ref"}:
                raise ValueError("unavailable temporal declaration contradicts present history fields")
            limitations.append(qid + ": authentic event history unavailable")
        else:
            times = {
                key: _timestamp(temporal.get(key)) for key in ["event_at", "collected_at", "annotated_at"]
            }
            history = declaration(temporal.get("evidence_ref"))
            if any(history.get(k) != temporal[k] for k in times):
                raise ValueError("temporal declaration contradicts evidence")
            if cutoff is not None and (
                (FAMILIES[split] == "final" and times["event_at"] <= cutoff)
                or (FAMILIES[split] != "final" and times["event_at"] > cutoff)
            ):
                raise ValueError("event time violates partition cutoff")
        label = labels[qid]
        relevance = _object(label.get("relevance"), "graded relevance")
        if any(
            cid not in pool
            or isinstance(g, bool)
            or not isinstance(g, (int, float))
            or not math.isfinite(g)
            or g < 0
            for cid, g in relevance.items()
        ):
            raise ValueError("dangling/invalid graded target")
        label_kind = label.get("kind")
        if label_kind not in {"positive", "ambiguous", "no_match"}:
            raise ValueError("explicit label kind required")
        positive = any(g > 0 for g in relevance.values())
        if (label_kind == "no_match" and relevance) or (label_kind != "no_match" and not positive):
            raise ValueError("contradictory positive/no-match annotation")
        if label_kind in {"no_match", "ambiguous"}:
            rationale = _string(label.get("rationale"), "annotation rationale")
            judgment = declaration(label.get("judgment_ref"))
            if (
                judgment.get("query_id") != qid
                or judgment.get("kind") != label_kind
                or judgment.get("pool_sha256") != refs["pool"]["sha256"]
                or judgment.get("rationale") != rationale
                or judgment.get("author_id") != author
            ):
                raise ValueError("pool-bound annotation judgment missing/contradictory")
        group_rows.append({**query, "split": split, "groups": groups, "source_id": source_id})
    # Declared aliases/transitive relationships share the same grouping component.
    changed = True
    while changed:
        changed = False
        for group in source_group_sets.values():
            for other in list(group):
                if other.startswith("source:") and other[7:] in source_group_sets:
                    merged = group | source_group_sets[other[7:]]
                    if merged != group:
                        group.update(merged)
                        changed = True
    for row in group_rows:
        row["groups"] = sorted(source_group_sets[row["source_id"]])
    statistical_projection = None
    if prereg.get("statistical_protocol") is not None:
        from magicite.eval.grouped_evaluation import canonical_projection

        statistical_projection = canonical_projection(group_rows, prereg["statistical_protocol"])
    errors = partition_errors(group_rows)
    if errors:
        raise ValueError("; ".join(errors))
    known_exposed = bool(final_ids) and (
        revision == OFFICIAL_REVISION
        or any(
            source.get("source", {}).get("revision") == OFFICIAL_REVISION for source in provenance.values()
        )
        or any(ref["sha256"] in EXPOSED_DIGESTS for ref in refs.values())
    )
    report = {
        "statistical_projection": statistical_projection,
        "structural_controls": "PASS",
        "empirical_obligations": {
            key: {
                "status": "UNEVALUATED",
                "reason": "Referenced declaration bytes do not independently prove authentic " + key,
            }
            for key in [
                "annotation_authenticity",
                "group_independence",
                "no_match_judgment",
                "project_temporal",
                "fresh_final",
                "E2",
                "E3",
                "E6",
            ]
        },
        "evidence_classes": sorted(classes),
        "qualifying": False,
        "known_exposed_final": known_exposed,
        "counts": {
            "pool": len(pool),
            "queries": len(runtime),
            "final": len(final_ids),
            "no_match": sum(x["kind"] == "no_match" for x in labels.values()),
        },
        "limitations": sorted(set(limitations)),
        "preparation_role": "Authors/reviewers inspected labels; not evaluation-consumer fresh access",
    }
    return PreparedPacket(packet_path.resolve(), packet, inventory, report, tuple(sorted(final_ids)))


def _publish_immutable(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("xb") as stream:
            stream.write(canonical_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp, path)  # Atomic no-replace publication: one concurrent fresh owner.
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temp.unlink(missing_ok=True)


def freeze_packet(
    packet_path: Path,
    output: Path,
    *,
    source_commit: str,
    runner_sha256: str,
    source_inputs: dict[str, str] | None = None,
) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit) or not HEX.fullmatch(runner_sha256):
        raise ValueError("source commit and runner SHA256 required")
    prepared = prepare_packet(packet_path)
    root = packet_path.parent.resolve()
    partitions = _read(root, prepared.packet["files"]["partitions"], {})
    calibration_ids = {r["query_id"] for r in partitions if FAMILIES[r["split"]] == "calibration"}
    labels = _read(root, prepared.packet["files"]["labels"], {})
    projection = [r for r in labels if r["query_id"] in calibration_ids]
    development_ids = {r["query_id"] for r in partitions if FAMILIES[r["split"]] == "development"}
    development_labels = [r for r in labels if r["query_id"] in development_ids]
    preregistration = _read(root, prepared.packet["files"]["preregistration"], {})
    body = {
        "power_assumptions": preregistration.get("power_assumptions"),
        "development_projection": development_labels,
        "development_projection_sha256": sha256_json(development_labels),
        "statistical_projection": prepared.report.get("statistical_projection"),
        "statistical_projection_sha256": sha256_json(prepared.report.get("statistical_projection")),
        "calibration_projection": projection,
        "calibration_projection_sha256": sha256_json(projection),
        "schema": FREEZE_SCHEMA,
        "source_commit": source_commit,
        "source_inputs": source_inputs or {},
        "runner_sha256": runner_sha256,
        "module_sha256": sha256_bytes(Path(__file__).read_bytes()),
        "packet_sha256": prepared.inputs[packet_path.name],
        "inputs": prepared.inputs,
        "preregistration_sha256": prepared.packet["files"]["preregistration"]["sha256"],
        "partitions_sha256": prepared.packet["files"]["partitions"]["sha256"],
        "final_ids": list(prepared.final_ids),
        "report": prepared.report,
    }
    payload = {
        "identity": sha256_json(body),
        "binding": body,
        "input_root": str(packet_path.parent.resolve()),
        "packet_name": packet_path.name,
    }
    _publish_immutable(output, payload)
    return payload


def link_power_freeze(planning: Path, output: Path, power: dict, selection: dict) -> dict:
    """Immutable successor on the SAME packet root, without resealing labels."""
    from copy import deepcopy

    from magicite.eval.grouped_evaluation import validate_incumbent_selection, validate_power_report

    original = verify_freeze(planning)
    if access_path(planning).exists():
        raise ValueError("planning final already exposed")
    validate_power_report(power)
    incumbent = validate_incumbent_selection(selection)
    if power["identities"]["incumbent_policy_id"] != incumbent:
        raise ValueError("power selected incumbent mismatch")
    body = deepcopy(original["binding"])
    projection = body["statistical_projection"]
    if projection is None or projection["protocol"]["power_plan"] is not None:
        raise ValueError("initial planning freeze required")
    if power["identities"]["protocol_frame_digest"] != sha256_json(projection["protocol"]):
        raise ValueError("power protocol frame mismatch")
    projection["protocol"]["power_plan"] = {"report": power, "sha256": sha256_json(power)}
    body["statistical_projection_sha256"] = sha256_json(projection)
    body["planning_successor"] = {
        "path": str(planning.resolve()),
        "identity": original["identity"],
        "selection": selection,
    }
    value = {
        "identity": sha256_json(body),
        "binding": body,
        "input_root": original["input_root"],
        "packet_name": original["packet_name"],
    }
    _publish_immutable(output, value)
    return value


def verify_freeze(path: Path) -> dict:
    value = _object(json.loads(path.read_bytes()), "freeze")
    body = _object(value.get("binding"), "binding")
    if (
        body.get("schema") != FREEZE_SCHEMA
        or sha256_json(body) != value.get("identity")
        or body.get("module_sha256") != sha256_bytes(Path(__file__).read_bytes())
    ):
        raise ValueError("frozen source/input binding changed")
    successor = body.get("planning_successor")
    if successor is not None:
        from copy import deepcopy

        from magicite.eval.grouped_evaluation import validate_incumbent_selection, validate_power_report

        original = verify_freeze(Path(successor["path"]))
        if original["identity"] != successor["identity"] or any(
            value[k] != original[k] for k in ("input_root", "packet_name")
        ):
            raise ValueError("planning root/identity changed")
        restored = deepcopy(body)
        restored.pop("planning_successor")
        power = restored["statistical_projection"]["protocol"]["power_plan"]["report"]
        validate_power_report(power)
        if power["identities"]["incumbent_policy_id"] != validate_incumbent_selection(successor["selection"]):
            raise ValueError("power selection changed")
        restored["statistical_projection"]["protocol"]["power_plan"] = None
        restored["statistical_projection_sha256"] = sha256_json(restored["statistical_projection"])
        if restored != original["binding"]:
            raise ValueError("successor changes original non-power frozen bytes")
    root = Path(value["input_root"])
    packet = _object(json.loads(contained(root, value["packet_name"]).read_bytes()), "packet")
    if sha256_bytes(contained(root, value["packet_name"]).read_bytes()) != body["packet_sha256"]:
        raise ValueError("packet identity drift")
    partitions = _read(root, packet["files"]["partitions"], {})
    if sorted(r["query_id"] for r in partitions if FAMILIES.get(r["split"]) == "final") != body["final_ids"]:
        raise ValueError("frozen final partition changed")
    # Inspect identity metadata, not scoring labels, before any access intent.
    provenance = _read(root, packet["files"]["provenance"], {})
    exposed = (
        packet["dataset"]["revision"] == OFFICIAL_REVISION
        or any(row.get("source", {}).get("revision") == OFFICIAL_REVISION for row in provenance)
        or any(ref.get("sha256") in EXPOSED_DIGESTS for ref in packet["files"].values())
    )
    if exposed and body["final_ids"]:
        raise ValueError("known exposed official final cannot be resealed fresh")
    if not isinstance(body.get("inputs"), dict) or not body["inputs"]:
        raise ValueError("complete frozen input inventory required")
    for name, digest in body["inputs"].items():
        if sha256_bytes(contained(root, name).read_bytes()) != digest:
            raise ValueError("frozen input bytes changed")
    return value


def access_path(freeze_path: Path) -> Path:
    return freeze_path.with_name(freeze_path.name + ".final-access.json")


def receipt_binding(frozen: dict, purpose: str) -> dict:
    body = frozen["binding"]
    return {
        "freeze_identity": frozen["identity"],
        "packet_sha256": body["packet_sha256"],
        "preregistration_sha256": body["preregistration_sha256"],
        "experiment_identity": body["preregistration_sha256"],
        "purpose": _string(purpose, "evaluation-consumer purpose"),
        "classification": "nonqualifying_local_control; authentic readiness UNEVALUATED",
    }


def validate_receipt(receipt: dict, binding: dict) -> None:
    if (
        receipt.get("schema") != "magicite/final-access-intent/1"
        or receipt.get("binding") != binding
        or receipt.get("exposure_retained") is not True
    ):
        raise ValueError("conflicting receipt/replay binding")
    _timestamp(receipt.get("first_access_intent_at"))


def open_final_labels(freeze_path: Path, *, purpose: str, replay: bool = False) -> list[dict]:
    frozen = verify_freeze(freeze_path)
    body = frozen["binding"]
    if body["report"]["known_exposed_final"]:
        raise ValueError("known exposed official final cannot be resealed fresh")
    _string(purpose, "evaluation-consumer purpose")
    receipt = access_path(freeze_path)
    binding = receipt_binding(frozen, purpose)
    if replay:
        prior = _object(json.loads(receipt.read_bytes()), "access receipt")
        validate_receipt(prior, binding)
    else:
        _publish_immutable(
            receipt,
            {
                "schema": "magicite/final-access-intent/1",
                "binding": binding,
                "first_access_intent_at": datetime.now(UTC).isoformat(),
                "exposure_retained": True,
            },
        )
    # No semantic decode of scoring labels until durable intent/replay validation.
    root = Path(frozen["input_root"])
    packet = json.loads(contained(root, frozen["packet_name"]).read_bytes())
    prepared = prepare_packet(contained(root, frozen["packet_name"]))
    if prepared.inputs != body["inputs"] or prepared.report != body["report"]:
        raise ValueError("prepared structural report/input drift")
    labels = _read(root, packet["files"]["labels"], {})
    return [row for row in labels if row["query_id"] in body["final_ids"]]


@dataclass(frozen=True)
class FinalAccessContext:
    freeze_path: Path
    purpose: str

    def qualification_errors(self) -> list[str]:
        frozen = verify_freeze(self.freeze_path)
        receipt = _object(json.loads(access_path(self.freeze_path).read_bytes()), "receipt")
        validate_receipt(receipt, receipt_binding(frozen, self.purpose))
        return ["verified data declarations are nonqualifying; authentic readiness UNEVALUATED"]
