"""Composition-v1 fixture helpers + sandboxed host verifier (eval, not runtime).

Host execution belongs here (C5 / AC-S08-04). The planner never invokes
subprocess or network; this harness is test/eval-only and keeps
structural validity distinct from verified task outcome.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from magicite.core.composition import (
    CompositionNode,
    CompositionSnapshot,
    HostVerificationReport,
    Plan,
    host_verification_report,
)
from magicite.core.context import (
    ArtifactInventoryEntry,
    HostFact,
    RouteContext,
    ServerPermissionPolicy,
)
from magicite.core.eligibility import EligibilitySubject, FixtureTrustDecision
from magicite.engram import (
    Capabilities,
    Compatibility,
    EngramRevisionRef,
    ProducedCapability,
    Relations,
    RequiredCapability,
    Risk,
    VersionConstraint,
)
from magicite.engram.model_v1 import SubprocessRisk

FIXTURE_ROOT = Path(__file__).resolve().parent
ENGRAM_V1_RELATIONS = FIXTURE_ROOT.parent / "engram-v1" / "relations" / "shared-relation-fixtures.json"

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64

DEFAULT_POLICY = ServerPermissionPolicy(
    allowed_permissions=frozenset({"perm.read", "perm.write-project"}),
    allowed_tools=frozenset({"protontricks", "shell", "echo"}),
    policy_digest="policy-fixture-v1",
    max_filesystem="write-project",
    max_subprocess="declared-tools",
    max_network="optional",
    max_secrets="redacted",
)


def load_shared_relation_fixtures() -> dict[str, Any]:
    return json.loads(ENGRAM_V1_RELATIONS.read_text(encoding="utf-8"))


def trust(engram_id: str, **overrides: object) -> FixtureTrustDecision:
    base: dict[str, object] = {
        "engram_id": engram_id,
        "content_digest": DIGEST_A,
        "lifecycle_status": "promoted",
        "quarantined": False,
        "origin_trusted": True,
        "admitted": True,
    }
    base.update(overrides)
    return FixtureTrustDecision(**base)  # type: ignore[arg-type]


def node(
    engram_id: str,
    *,
    version: int = 1,
    content_digest: str = DIGEST_A,
    requires: list[RequiredCapability] | None = None,
    produces: list[ProducedCapability] | None = None,
    alternatives: list[str] | None = None,
    conflicts_with: list[str] | None = None,
    relation_requires: list[EngramRevisionRef] | None = None,
    before: list[EngramRevisionRef] | None = None,
    supersedes: list[EngramRevisionRef] | None = None,
    risk: Risk | None = None,
    required_permissions: frozenset[str] = frozenset(),
    compatibility: Compatibility | None = None,
    procedure_ref: str | None = None,
    asset_digests: Mapping[str, str] | None = None,
) -> CompositionNode:
    caps = Capabilities(
        requires=list(requires or []),
        produces=list(produces or []),
        alternatives=list(alternatives or []),
        conflicts_with=list(conflicts_with or []),
    )
    rel = Relations(
        requires=list(relation_requires or []),
        before=list(before or []),
        supersedes=list(supersedes or []),
    )
    subject = EligibilitySubject(
        id=engram_id,
        version=version,
        compatibility=compatibility,
        capabilities=caps,
        risk=risk,
        relations=rel,
        required_permissions=required_permissions,
    )
    return CompositionNode(
        subject=subject,
        content_digest=content_digest,
        asset_digests=dict(asset_digests or {}),
        procedure_ref=procedure_ref,
    )


def snapshot(
    nodes: Sequence[CompositionNode],
    *,
    snapshot_id: str = "snap-fixture-v1",
    policy_id: str = "dense-v1",
    policy_digest: str = "policy-fixture-v1",
    preferred_providers: Mapping[str, str] | None = None,
) -> CompositionSnapshot:
    mapping = {n.id: n for n in nodes}
    return CompositionSnapshot(
        snapshot_id=snapshot_id,
        policy_id=policy_id,
        policy_digest=policy_digest,
        nodes=mapping,
        preferred_providers=dict(preferred_providers or {}),
    )


def base_context(**overrides: object) -> RouteContext:
    data: dict[str, object] = {
        "platform": "linux",
        "host": HostFact(id="cursor", version="0.45.0"),
        "capabilities": {"host.fs.read-project": "1.0.0"},
        "permission_grants": frozenset({"perm.read"}),
        "allowed_tools": frozenset({"protontricks", "shell", "echo"}),
        "artifact_inventory": (),
    }
    data.update(overrides)
    return RouteContext(**data)  # type: ignore[arg-type]


def risk_with_tool(tool: str) -> Risk:
    return Risk(subprocess=SubprocessRisk(mode="declared-tools", tools=[tool]))


@dataclass(frozen=True)
class HostTaskFixture:
    """Independently authored deterministic host task (eval corpus)."""

    task_id: str
    expected_stdout: str
    verifier_id: str = "composition-echo-verifier"
    verifier_version: str = "1"
    seed_command: tuple[str, ...] = ("echo", "ok")

    @property
    def artifact_digest(self) -> str:
        payload = json.dumps(
            {
                "task_id": self.task_id,
                "expected_stdout": self.expected_stdout,
                "seed_command": list(self.seed_command),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def load_echo_task() -> HostTaskFixture:
    path = FIXTURE_ROOT / "tasks" / "echo-ok.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return HostTaskFixture(
        task_id=data["task_id"],
        expected_stdout=data["expected_stdout"],
        verifier_id=data.get("verifier_id", "composition-echo-verifier"),
        verifier_version=str(data.get("verifier_version", "1")),
        seed_command=tuple(data.get("seed_command", ["echo", "ok"])),
    )


def run_deterministic_host_verifier(
    plan: Plan,
    task: HostTaskFixture,
    *,
    execute: Callable[[tuple[str, ...]], str] | None = None,
) -> HostVerificationReport:
    """Sandboxed deterministic verifier.

    Distinguishes structural validity (from Plan/1) from verified task
    outcome. Invalid plans never execute — outcome stays ``unevaluated``.
    Empirical claims that are not actually run remain ``unevaluated``.
    """
    if plan.status != "valid" or not plan.executable:
        return host_verification_report(
            plan,
            verified_task_outcome="unevaluated",
            verifier_type="deterministic_test",
            verifier_id=task.verifier_id,
            verifier_version=task.verifier_version,
            verifier_artifact_digest=task.artifact_digest,
            details="plan not structurally valid; host execution skipped",
        )

    runner = execute or _default_echo_runner
    try:
        stdout = runner(task.seed_command).strip()
    except Exception as exc:  # noqa: BLE001 — verifier failure → fail
        return host_verification_report(
            plan,
            verified_task_outcome="fail",
            verifier_type="deterministic_test",
            verifier_id=task.verifier_id,
            verifier_version=task.verifier_version,
            verifier_artifact_digest=task.artifact_digest,
            details=f"verifier raised: {type(exc).__name__}",
        )

    outcome = "pass" if stdout == task.expected_stdout else "fail"
    return host_verification_report(
        plan,
        verified_task_outcome=outcome,  # type: ignore[arg-type]
        verifier_type="deterministic_test",
        verifier_id=task.verifier_id,
        verifier_version=task.verifier_version,
        verifier_artifact_digest=task.artifact_digest,
        details=f"stdout={stdout!r}",
    )


def _default_echo_runner(command: tuple[str, ...]) -> str:
    """Deterministic in-process stand-in — no real subprocess (sandbox)."""
    if command == ("echo", "ok"):
        return "ok\n"
    if len(command) >= 2 and command[0] == "echo":
        return " ".join(command[1:]) + "\n"
    raise ValueError(f"unsupported sandboxed command: {command!r}")


__all__ = [
    "DEFAULT_POLICY",
    "DIGEST_A",
    "DIGEST_B",
    "DIGEST_C",
    "DIGEST_D",
    "FIXTURE_ROOT",
    "HostTaskFixture",
    "HostVerificationReport",
    "ArtifactInventoryEntry",
    "VersionConstraint",
    "base_context",
    "load_echo_task",
    "load_shared_relation_fixtures",
    "node",
    "risk_with_tool",
    "run_deterministic_host_verifier",
    "snapshot",
    "trust",
]
