#!/usr/bin/env python3
"""Validate S16 release evidence; exit 1 for NO-GA, 2 for malformed input.

This validates evidence integrity and independently reviewed attestations, not
truth by declaration. Reviewers must inspect the underlying immutable artifacts.
Synthetic unit fixtures are never evidence for a product release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

OBLIGATIONS = {
    "POLICY": ("nonadaptive-default", "dream-isolation", "explicit-experimental", "pinned-incumbent"),
    "EVIDENCE": ("manifested-predictions", "independent-labels", "claim-ci", "negative-history"),
    "SCHEMA": ("frozen-schema", "roundtrip-corpus", "migration-downgrade-replay"),
    "TRUST": ("offline-tamper", "local-review-revocation", "server-origin", "adversarial-corpus"),
    "ROUTING": ("full-content-indexes", "eligibility", "explanations", "calibrated-abstention", "e3-quality"),
    "COMPOSITION": ("independent-structural-corpus", "actual-host-outcomes", "safe-invalid-plan"),
    "PRIVACY": ("data-map", "no-default-raw", "historical-cleanup", "checkpoint-rpo", "lifecycle-tests"),
    "LEARNING": ("containment", "fully-gated-or-unavailable"),
    "PROTOCOL": ("schemas", "retry-cancel-errors", "advertised-transcripts"),
    "RELIABILITY": ("all-store-kill-replay-fencing", "backup-restore-upgrade-matrix", "zero-write-doctor"),
    "PERFORMANCE": ("dedicated-e6", "production-model", "supported-scale"),
    "DISTRIBUTION": ("published-installs", "supply-chain", "testpypi-rehearsal"),
    "DOCS": (
        "generated-reference-claims",
        "tested-operator-path",
        "redirects",
        "support-security-governance",
    ),
    "RC-CONTRACT": ("two-independent-rcs", "stable-fingerprints", "passing-matrix"),
    "EXTERNAL": ("independent-reproduction-or-two-pilots",),
    "SECURITY": ("threat-model", "bounded-fuzz-adversarial", "critical-resolved"),
    "GA-ALL": ("all-required-gates", "maintainer-signoff", "fetched-artifacts"),
}
MATRIX = (
    "python-3.11",
    "python-3.12",
    "oci-linux-amd64",
    "oci-linux-arm64",
    "native-linux-amd64",
    "native-macos-arm64",
    "generic-stdio",
    "claude-code",
    "upgrade-0.2",
    "upgrade-0.3",
    "rc-upgrade",
    "complete-backup-downgrade",
    "offline-failure",
    "model-acquisition",
    "local-filesystem",
)
REPRODUCTION_CHECKS = (
    "external-human-or-team",
    "clean-environment",
    "frozen-published-manifest",
    "versions-and-log-hashes",
    "discrepancy-report",
    "preregistered-tolerance-met",
)
PILOT_CHECKS = (
    "external-human-or-team",
    "independent-labels",
    "deterministic-outcomes",
    "registry-host-model-policy-versions",
    "scoped-workload",
    "install-route-explain-recovery",
    "failures-and-guarantees",
    "sample-counts-and-uncertainty",
    "consented-redacted",
)


def _sha(value: Any, length: int = 64) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{" + str(length) + "}", value) is not None


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _artifact(ref: Any, root: Path) -> Path:
    if not isinstance(ref, dict) or not _text(ref.get("path")) or not _sha(ref.get("sha256")):
        raise ValueError("invalid artifact reference")
    relative = Path(ref["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("artifact path escapes evidence root")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("artifact missing or escapes evidence root")
    if hashlib.sha256(path.read_bytes()).hexdigest() != ref["sha256"]:
        raise ValueError("artifact digest mismatch")
    return path


def _unique_json(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path: Path) -> Any:
    return json.loads(
        path.read_text(),
        object_pairs_hook=_unique_json,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")),
    )


def _witness(ref: Any, root: Path, gate: str, source: str) -> str:
    report = read_json(_artifact(ref, root))
    if not isinstance(report, dict) or report.get("schema") != "magicite/release-witness/1":
        raise ValueError("unsupported witness schema")
    obligation = report.get("obligation")
    if report.get("gate") != gate or obligation not in OBLIGATIONS[gate]:
        raise ValueError("witness does not cover a frozen gate obligation")
    if report.get("status") != "PASS" or report.get("source_commit") != source:
        raise ValueError("witness is not PASS at candidate source")
    if (
        not _text(report.get("reviewer"))
        or not _text(report.get("producer"))
        or report["reviewer"] == report["producer"]
    ):
        raise ValueError("independent named review required")
    if not _text(report.get("command")) or not isinstance(report.get("environment"), dict):
        raise ValueError("command and environment required")
    if not report["environment"]:
        raise ValueError("empty environment")
    _artifact(report.get("lock"), root)
    artifacts = report.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("underlying evidence required")
    for artifact in artifacts:
        _artifact(artifact, root)
    return str(obligation)


def _bound_report(row: dict[str, Any], root: Path, schema: str, keys: tuple[str, ...]) -> None:
    """The digest-bound report, rather than an unsigned wrapper assertion, is authoritative."""
    report = read_json(_artifact(row.get("artifact"), root))
    if not isinstance(report, dict) or report.get("schema") != schema:
        raise ValueError("unsupported immutable report schema")
    for key in keys:
        if key not in row or key not in report or report[key] != row[key]:
            raise ValueError(f"immutable report contradicts or omits {key}")


def _rcs(value: Any, root: Path, source: str) -> None:
    if not isinstance(value, list) or len(value) != 2 or not all(isinstance(x, dict) for x in value):
        raise ValueError("two RCs required")
    left, right = value
    if right.get("source_commit") != source:
        raise ValueError("latest RC source must match candidate")
    for row in value:
        for key in ("id", "builder"):
            if not _text(row.get(key)):
                raise ValueError("RC identity and independent builder required")
        if not _sha(row.get("source_commit"), 40):
            raise ValueError("RC source commit missing")
        _bound_report(
            row,
            root,
            "magicite/rc-build-report/1",
            ("id", "builder", "source_commit", "fingerprints", "matrix", "build_artifact"),
        )
        _artifact(row.get("build_artifact"), root)
        fingerprints = row.get("fingerprints")
        if (
            not isinstance(fingerprints, dict)
            or set(fingerprints) != {"api", "schema"}
            or not all(_sha(x) for x in fingerprints.values())
        ):
            raise ValueError("stable API/schema fingerprints required")
        matrix = row.get("matrix")
        if not isinstance(matrix, dict) or not set(MATRIX) <= set(matrix):
            raise ValueError("RC supported matrix incomplete")
        if any(status != "PASS" for status in matrix.values()):
            raise ValueError("RC supported matrix not passing")
    if any(left[key] == right[key] for key in ("id", "builder")):
        raise ValueError("RCs must be distinct independent builds")
    if left["build_artifact"]["sha256"] == right["build_artifact"]["sha256"]:
        raise ValueError("RC artifacts must be distinct")
    if left["fingerprints"] != right["fingerprints"]:
        raise ValueError("stable fingerprints changed: fresh RC pair required")


def _external(value: Any, root: Path, source: str) -> None:
    if not isinstance(value, dict) or value.get("path") not in {"reproduction", "pilots"}:
        raise ValueError("external reproduction or two-pilot path required")
    pilots = value["path"] == "pilots"
    reports = value.get("reports")
    if not isinstance(reports, list) or len(reports) != (2 if pilots else 1):
        raise ValueError("external report count mismatch")
    operators, repositories = set(), set()
    for report in reports:
        if not isinstance(report, dict):
            raise ValueError("invalid external report")
        operator, reviewer, authors = report.get("operator"), report.get("reviewer"), report.get("authors")
        if (
            not _text(operator)
            or not _text(reviewer)
            or not isinstance(authors, list)
            or not authors
            or not all(_text(author) for author in authors)
            or operator in authors
            or reviewer in authors
            or operator == reviewer
        ):
            raise ValueError("external operator/reviewer must be independent of implementation and labels")
        if report.get("source_commit") != source:
            raise ValueError("external report source mismatch")
        if report.get("status") != "PASS" or report.get("independent") is not True:
            raise ValueError("external report must attest independently evaluated PASS")
        keys = ("operator", "reviewer", "authors", "source_commit", "checks", "status", "independent")
        if pilots:
            keys += ("repository",)
        _bound_report(
            report, root, "magicite/external-pilot/1" if pilots else "magicite/external-reproduction/1", keys
        )
        checks = report.get("checks")
        if not isinstance(checks, dict) or any(
            checks.get(key) is not True for key in (PILOT_CHECKS if pilots else REPRODUCTION_CHECKS)
        ):
            raise ValueError("external workflow evidence incomplete")
        operators.add(operator)
        if pilots:
            if not _text(report.get("repository")):
                raise ValueError("pilot repository missing")
            repositories.add(report["repository"])
    if pilots and (len(operators) != 2 or len(repositories) != 2):
        raise ValueError("two distinct external repositories/operators required")


def validate(manifest: Any, root: Path) -> dict[str, Any]:
    """Return explicit rejection diagnostics for malformed and incomplete evidence."""
    errors: list[str] = []
    if not isinstance(manifest, dict) or manifest.get("schema") != "magicite/release-manifest/1":
        return {"eligible": False, "recommendation": "NO-GA", "errors": ["unsupported manifest schema"]}
    source = manifest.get("source_commit")
    if not _sha(source, 40):
        errors.append("candidate source commit missing")
    gates = manifest.get("gates")
    if not isinstance(gates, list):
        gates = []
        errors.append("gates must be an explicit list")
    seen: set[str] = set()
    for gate in gates:
        if not isinstance(gate, dict) or not isinstance(gate.get("id"), str):
            errors.append("malformed gate")
            continue
        name = gate["id"]
        if name not in OBLIGATIONS or name in seen:
            errors.append(f"unknown or duplicate gate: {name}")
            continue
        seen.add(name)
        if gate.get("status") != "PASS":
            errors.append(f"{name}: {gate.get('status')} does not satisfy a mandatory gate")
            continue
        refs = gate.get("evidence")
        if not isinstance(refs, list):
            refs = []
        covered: set[str] = set()
        for ref in refs:
            try:
                obligation = _witness(ref, root, name, source)
                if obligation in covered:
                    raise ValueError("duplicate witness obligation")
                covered.add(obligation)
            except (ValueError, OSError, TypeError, KeyError) as exc:
                errors.append(f"{name}: {exc}")
        missing = set(OBLIGATIONS[name]) - covered
        if missing:
            errors.append(f"{name}: unsupported PASS; missing {', '.join(sorted(missing))}")
    if set(OBLIGATIONS) - seen:
        errors.append("missing gates: " + ", ".join(sorted(set(OBLIGATIONS) - seen)))
    try:
        _rcs(manifest.get("release_candidates"), root, source)
    except (ValueError, OSError, TypeError, KeyError) as exc:
        errors.append(f"RC-CONTRACT: {exc}")
    try:
        _external(manifest.get("external"), root, source)
    except (ValueError, OSError, TypeError, KeyError) as exc:
        errors.append(f"EXTERNAL: {exc}")
    try:
        signoff = manifest.get("maintainer_signoff")
        if (
            not isinstance(signoff, dict)
            or not _text(signoff.get("identity"))
            or signoff.get("source_commit") != source
            or signoff.get("approved") is not True
        ):
            raise ValueError("explicit maintainer sign-off at candidate source required")
        _bound_report(
            signoff, root, "magicite/maintainer-signoff/1", ("identity", "source_commit", "approved")
        )
    except (ValueError, OSError, TypeError, KeyError) as exc:
        errors.append(f"GA-ALL: {exc}")
    return {
        "eligible": not errors,
        "recommendation": "NO-GA" if errors else "GA-RECOMMENDATION",
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        result = validate(read_json(args.manifest), args.root)
    except (ValueError, OSError) as exc:
        print(json.dumps({"eligible": False, "recommendation": "NO-GA", "errors": [str(exc)]}))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result["eligible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
