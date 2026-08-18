"""Small validation CLI for versioned evaluation corpora."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _has_cycle(nodes: set[str], edges: list[dict[str, Any]]) -> bool:
    graph: dict[str, list[str]] = {node: [] for node in nodes}
    for edge in edges:
        if edge.get("resolved") is True:
            graph.setdefault(str(edge["src"]), []).append(str(edge["dst"]))

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(neighbour) for neighbour in graph.get(node, [])):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in sorted(nodes))


def _reachable(winner: str, edges: list[dict[str, Any]]) -> set[str]:
    outgoing: dict[str, list[str]] = {}
    for edge in edges:
        if edge.get("resolved") is True:
            outgoing.setdefault(str(edge["src"]), []).append(str(edge["dst"]))
    found = {winner}
    frontier = [winner]
    while frontier:
        node = frontier.pop()
        for target in outgoing.get(node, []):
            if target not in found:
                found.add(target)
                frontier.append(target)
    return found


def validate_corpus_data(data: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict) or data.get("schema") != "magicite-composition-corpus/1":
        return ["schema must be magicite-composition-corpus/1"]
    policy = data.get("label_policy")
    if not isinstance(policy, dict):
        errors.append("label_policy must be an object")
    elif (
        policy.get("method") != "manual_topological_reasoning"
        or policy.get("production_expansion_used") is not False
    ):
        errors.append("root labels must be manual and independent of production expansion")

    cases = data.get("cases")
    if not isinstance(cases, list):
        return [*errors, "cases must be an array"]
    if len(cases) < 20:
        errors.append(f"corpus has {len(cases)} cases; at least 20 are required")

    seen_ids: set[str] = set()
    has_cycle_case = False
    has_dangling_case = False
    for index, case in enumerate(cases):
        locus = f"cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{locus} must be an object")
            continue
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id:
            errors.append(f"{locus}.id must be a non-empty string")
        elif case_id in seen_ids:
            errors.append(f"duplicate case id {case_id!r}")
        else:
            seen_ids.add(case_id)

        winner = case.get("winner")
        expected = case.get("expected_plan")
        edges = case.get("edges")
        features = case.get("features")
        provenance = case.get("label_provenance")
        if not isinstance(winner, str) or not winner:
            errors.append(f"{locus}.winner must be a non-empty string")
            continue
        if (
            not isinstance(expected, list)
            or not expected
            or not all(isinstance(name, str) and name for name in expected)
        ):
            errors.append(f"{locus}.expected_plan must contain names")
            continue
        if len(expected) != len(set(expected)):
            errors.append(f"{locus}.expected_plan contains duplicates")
        if not isinstance(edges, list):
            errors.append(f"{locus}.edges must be an array")
            continue
        if not isinstance(features, list) or not all(isinstance(item, str) for item in features):
            errors.append(f"{locus}.features must be a string array")
            features = []
        if (
            not isinstance(provenance, dict)
            or provenance.get("method") != "manual_topological_reasoning"
            or provenance.get("production_expansion_used") is not False
            or provenance.get("reviewed") is not True
        ):
            errors.append(f"{locus}.label_provenance is not independently reviewed")

        valid_edges = True
        for edge_index, edge in enumerate(edges):
            edge_locus = f"{locus}.edges[{edge_index}]"
            if not isinstance(edge, dict):
                errors.append(f"{edge_locus} must be an object")
                valid_edges = False
                continue
            if edge.get("type") not in {"depends_on", "composes"}:
                errors.append(f"{edge_locus}.type is not a plan edge")
                valid_edges = False
            if not all(isinstance(edge.get(key), str) and edge[key] for key in ("src", "dst")):
                errors.append(f"{edge_locus} needs non-empty src/dst")
                valid_edges = False
            if not isinstance(edge.get("resolved"), bool):
                errors.append(f"{edge_locus}.resolved must be boolean")
                valid_edges = False
        if not valid_edges:
            continue

        reachable = _reachable(winner, edges)
        if set(expected) != reachable:
            errors.append(f"{locus}.expected_plan membership differs from the resolved winner closure")
        cycle = _has_cycle(reachable, edges)
        dangling = any(edge["resolved"] is False for edge in edges)
        has_cycle_case = has_cycle_case or cycle
        has_dangling_case = has_dangling_case or dangling
        if cycle != ("cycle" in features):
            errors.append(f"{locus}.features cycle marker does not match its graph")
        if dangling != ("dangling" in features):
            errors.append(f"{locus}.features dangling marker does not match its graph")

        order = {name: position for position, name in enumerate(expected)}
        satisfied = sum(
            1
            for edge in edges
            if edge["resolved"] is True
            and edge["src"] in order
            and edge["dst"] in order
            and order[edge["dst"]] < order[edge["src"]]
        )
        confidence = round(satisfied / len(edges), 4) if edges else 1.0
        if case.get("expected_confidence") != confidence:
            errors.append(
                f"{locus}.expected_confidence is {case.get('expected_confidence')!r}; "
                f"manual edge accounting gives {confidence}"
            )
        if not cycle:
            for edge in edges:
                if edge["resolved"] and order[edge["dst"]] >= order[edge["src"]]:
                    errors.append(f"{locus}.expected_plan violates {edge['dst']} before {edge['src']}")

    if not has_cycle_case:
        errors.append("corpus must contain at least one real cycle")
    if not has_dangling_case:
        errors.append("corpus must contain at least one dangling reference")
    return errors


def validate_corpus(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot read corpus {path}: {exc}"]
    return validate_corpus_data(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m magicite.eval")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate-corpus")
    validate.add_argument("path", type=Path)
    args = parser.parse_args(argv)

    errors = validate_corpus(args.path)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    data = json.loads(args.path.read_text(encoding="utf-8"))
    print(f"validated {len(data['cases'])} independent composition cases")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
