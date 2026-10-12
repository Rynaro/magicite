"""V1 documentation contracts; reuse evidence integrity rather than trusting prose labels."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from check_evaluation_results import check_v1_claim_bundle

from magicite.eval.unevaluated import UNEVALUATED_CATALOG

# Performance/efficacy quantities, not versions, tool counts or numbered instructions.
QUANTITATIVE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:%|percent\b|ms\b|milliseconds\b|seconds\b|queries/s\b|[x×]\s+faster\b)"
    r"|\b(?:hit@\d|accuracy|recall|precision|mrr|plan.f1|latency|throughput)\b[^\n|]*?\d+(?:\.\d+)?"
    r"|\b\d+(?:[.,]\d+)?k?\s+skills\b[^\n|]*?(?:routing|latency|support|scale)"
    r"|\b(?:supports?|handles?|scales? to)\s+\d[\d,.]*k?\s+(?:skills|artifacts|queries)\b",
    re.I,
)


def check_readme_claims(root: Path, ledger_path: Path | None = None) -> list[str]:
    errors: list[str] = []
    try:
        ledger = json.loads((ledger_path or root / "docs/readme-claims.json").read_text())
        readme = (root / "README.md").read_text()
    except (OSError, ValueError) as exc:
        return [f"claim ledger unavailable: {exc}"]
    if ledger.get("schema") != "magicite/readme-claims/1" or not isinstance(ledger.get("claims"), list):
        return ["invalid README claim ledger"]
    accepted: set[str] = set()
    ids: set[str] = set()
    for entry in ledger["claims"]:
        if not isinstance(entry, dict):
            errors.append("claim ledger entry must be an object")
            continue
        reference = entry.get("bundle", "")
        path = (root / reference).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            errors.append("claim bundle missing or outside repository")
            continue
        try:
            bundle: dict[str, Any] = json.loads(path.read_text())
        except (OSError, ValueError):
            errors.append("claim bundle unreadable")
            continue
        bundle_errors = check_v1_claim_bundle(path, bundle)
        claim, result = bundle.get("claim", {}), bundle.get("result", {})
        metric, value = claim.get("metric"), claim.get("value")
        numeric = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        aggregate = result.get("aggregates", {}).get(metric)
        if not numeric or isinstance(aggregate, bool) or aggregate != value:
            bundle_errors.append("README claim needs a finite value and matching result aggregate")
        claim_id = claim.get("claim_id", "")
        if claim_id in ids:
            bundle_errors.append("duplicate README claim identity")
        ids.add(claim_id)
        rendered = (
            f"<!-- claim:{claim_id} --> {metric}: {value} {claim.get('unit')} "
            f"({claim.get('population_split')})."
        )
        if claim.get("text_location") != f"README.md#claim:{claim_id}" or rendered not in readme.splitlines():
            bundle_errors.append("README claim text/location/value/unit/scope binding mismatch")
        if claim.get("status") != "supported":
            bundle_errors.append("README accepted claim must be supported")
        if bundle_errors:
            errors.extend(f"{claim_id}: {error}" for error in bundle_errors)
        else:
            accepted.add(rendered)
    for line in readme.splitlines():
        if (
            QUANTITATIVE.search(re.sub(r"\]\([^)]*\)", "]()", line)) or "<!-- claim:" in line
        ) and line not in accepted:
            errors.append(f"quantitative README claim lacks accepted bound evidence: {line.strip()}")
    return errors


def check_support(root: Path) -> list[str]:
    try:
        support = json.loads((root / "docs/support-policy.json").read_text())
    except (OSError, ValueError) as exc:
        return [f"support policy unavailable: {exc}"]
    errors = []
    if support.get("schema") != "magicite/support-policy/1":
        errors.append("support policy schema mismatch")
    window = support.get("deprecation_window", {})
    if (
        window.get("minimum_minor_releases", 0) < 2
        or window.get("minimum_days", 0) < 90
        or window.get("removal_rule") != "whichever-is-longer"
        or not window.get("starts_at")
    ):
        errors.append("deprecation requires at least two minor releases and 90 days, whichever is longer")
    for section in ("support", "deprecations"):
        rows = support.get(section)
        if not isinstance(rows, list) or not rows:
            errors.append(f"missing {section} rows")
            continue
        for row in rows:
            if not isinstance(row, dict) or not all(
                isinstance(row.get(k), str) and row[k].strip()
                for k in ("id", "replacement", "support_deadline", "status")
            ):
                errors.append(f"{section} row requires identity, replacement and support deadline")
                continue
            if row["status"] not in {"tested", "proposed", "experimental", "deprecated"}:
                errors.append(f"unknown support status: {row['id']}")
            if row["status"] == "tested" and not row.get("evidence"):
                errors.append(f"tested support needs evidence: {row['id']}")
    return errors


def check_unevaluated(root: Path) -> list[str]:
    text = (root / "docs/evaluation/v1/unevaluated.md").read_text()
    return [
        f"missing UNEVALUATED item {item.item_id}"
        for item in UNEVALUATED_CATALOG
        if item.item_id not in text or item.operator_command not in text
    ]
