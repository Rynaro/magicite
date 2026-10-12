"""Authenticated gate for exact, non-admitting reviewed legacy reconciliation."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from magicite.core.trust_custodian import CustodianError, _bytes


def active_plan(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    active = None
    for record in records:
        if record["kind"] == "legacy_reconciliation":
            active = record["payload"] if record["payload"]["phase"] == "BEGIN" else None
    return active


def validate(payload: dict[str, Any]) -> None:
    base = {"schema", "phase", "migration_id", "registry_id", "epoch", "manifest_digest", "backup_digest"}
    phase = payload.get("phase")
    if (
        phase not in {"BEGIN", "COMPLETE"}
        or payload.get("schema") != "LegacyReconciliation/1"
        or set(payload) != base | ({"expected_records", "targets"} if phase == "BEGIN" else set())
        or type(payload.get("epoch")) is not int
        or payload["epoch"] < 1
    ):
        raise CustodianError("invalid legacy reconciliation gate")
    for key in ("migration_id", "registry_id"):
        if not isinstance(payload[key], str) or not payload[key] or len(payload[key]) > 128:
            raise CustodianError("invalid reconciliation identity")
    for key in ("manifest_digest", "backup_digest"):
        if not isinstance(payload[key], str) or re.fullmatch("[0-9a-f]{64}", payload[key]) is None:
            raise CustodianError("invalid reconciliation commitment")
    if phase == "BEGIN":
        if not isinstance(payload["expected_records"], list) or not isinstance(payload["targets"], list):
            raise CustodianError("invalid reconciliation plan")
        identities = set()
        for item in payload["expected_records"]:
            if (
                not isinstance(item, dict)
                or set(item) != {"record_id", "kind", "payload_digest"}
                or item["kind"] not in {"artifact_transform", "trust_decision"}
                or not isinstance(item["record_id"], str)
                or not item["record_id"]
                or item["record_id"] in identities
                or not isinstance(item["payload_digest"], str)
                or re.fullmatch("[0-9a-f]{64}", item["payload_digest"]) is None
            ):
                raise CustodianError("invalid planned immutable record")
            identities.add(item["record_id"])
        targets = set()
        for target in payload["targets"]:
            if (
                not isinstance(target, dict)
                or set(target) != {"engram_id", "target_digest", "resources_digest"}
                or not isinstance(target["engram_id"], str)
                or not target["engram_id"]
                or target["engram_id"] in targets
                or any(
                    not isinstance(target[key], str) or re.fullmatch("[0-9a-f]{64}", target[key]) is None
                    for key in ("target_digest", "resources_digest")
                )
            ):
                raise CustodianError("invalid planned target commitment")
            targets.add(target["engram_id"])


def check_next(
    records: list[dict[str, Any]],
    *,
    registry: str,
    epoch: int,
    record_id: str,
    kind: str,
    payload: dict[str, Any],
) -> None:
    active = active_plan(records)
    if kind == "artifact_transform" and payload.get("legacy_provenance") is not None:
        legacy = payload["legacy_provenance"]
        if active is None or any(legacy[key] != active[key] for key in ("manifest_digest", "backup_digest")):
            raise CustodianError("legacy provenance requires its authenticated reviewed plan")
    if kind == "legacy_reconciliation":
        validate(payload)
        if (
            payload["registry_id"] != registry
            or payload["epoch"] != epoch
            or record_id != "legacy-" + payload["migration_id"] + "-" + payload["phase"].lower()
        ):
            raise CustodianError("reconciliation enrollment/identity mismatch")
        if payload["phase"] == "BEGIN":
            if active is not None:
                raise CustodianError("another legacy reconciliation is active")
            if any(
                item["record_id"] in {row["record_id"] for row in records}
                for item in payload["expected_records"]
            ):
                raise CustodianError("planned identity already committed")
            return
    if active is None:
        if kind == "legacy_reconciliation":
            raise CustodianError("reconciliation begin is missing")
        return
    begin_index = next(
        i
        for i, row in enumerate(records)
        if row["kind"] == "legacy_reconciliation" and row["payload"] == active
    )
    applied = records[begin_index + 1 :]
    expected = active["expected_records"]
    for index, row in enumerate(applied):
        actual = {key: row[key] for key in ("record_id", "kind", "payload_digest")}
        if index >= len(expected) or _bytes(actual) != _bytes(expected[index]):
            raise CustodianError("reconciliation committed history diverged")
    if kind != "legacy_reconciliation":
        actual = {
            "record_id": record_id,
            "kind": kind,
            "payload_digest": hashlib.sha256(_bytes(payload)).hexdigest(),
        }
        if (
            len(applied) >= len(expected)
            or _bytes(actual) != _bytes(expected[len(applied)])
            or (kind == "trust_decision" and payload["decision"] == "admit")
        ):
            raise CustodianError("only exact non-admitting migration records are permitted")
        return
    if (
        payload["phase"] != "COMPLETE"
        or len(applied) != len(expected)
        or any(payload[key] != active[key] for key in payload if key != "phase")
    ):
        raise CustodianError("legacy reconciliation is incomplete")
    for target in active["targets"]:
        lineages = [
            row["payload"]
            for row in applied
            if row["kind"] == "artifact_transform"
            and row["payload"]["engram_id"] == target["engram_id"]
            and row["payload"]["target_digest"] == target["target_digest"]
        ]
        decisions = [
            row["payload"]
            for row in applied
            if row["kind"] == "trust_decision" and row["payload"]["engram_id"] == target["engram_id"]
        ]
        if (
            not lineages
            or not any(
                hashlib.sha256(_bytes(row["resources"])).hexdigest() == target["resources_digest"]
                for row in lineages
            )
            or not decisions
            or decisions[-1]["decision"] == "admit"
            or not any(
                row["decision"] == "pending" and row["content_digest"] == target["target_digest"]
                for row in decisions
            )
            or (
                any(row["decision"] in {"reject", "revoke", "quarantine"} for row in decisions)
                and decisions[-1]["decision"] == "pending"
            )
        ):
            raise CustodianError("migration targets/restrictions are incomplete")
