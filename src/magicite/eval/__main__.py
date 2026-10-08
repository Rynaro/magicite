"""Validation CLI for versioned evaluation corpora and V1 manifests."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from magicite.eval.validate import (
    claim_eligible_for_new_run_gate,
    validate_claim_data,
    validate_corpus_manifest_data,
    validate_experiment_data,
    validate_prediction_data,
    validate_result_data,
)


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


def _load_json(path: Path) -> tuple[Any | None, list[str]]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), []
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"cannot read {path}: {exc}"]


def _print_errors(errors: list[str]) -> int:
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m magicite.eval")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate-corpus")
    validate.add_argument("path", type=Path)

    for name in (
        "validate-experiment",
        "validate-corpus-manifest",
        "validate-prediction",
        "validate-result",
        "validate-claim",
    ):
        sp = subparsers.add_parser(name)
        sp.add_argument("path", type=Path)

    acquire = subparsers.add_parser(
        "acquire-skillret",
        help="Record local SkillRet archive digest and write a verified CorpusManifest (offline).",
    )
    acquire.add_argument("--archive", type=Path, required=True)
    acquire.add_argument("--expected-sha256", required=True)
    acquire.add_argument("--license", dest="license_name", required=True)
    acquire.add_argument("--revision", required=True)
    acquire.add_argument(
        "--corpus-json",
        type=Path,
        required=True,
        help="CorpusManifest/1 JSON path (on disk, or basename found inside the archive)",
    )
    acquire.add_argument("--output", type=Path, required=True)
    acquire.add_argument("--acquired-at", default=None)

    retrieval = subparsers.add_parser(
        "run-retrieval",
        help="Run nonqualifying synthetic hashing retrieval diagnostics.",
    )
    retrieval.add_argument("--experiment", type=Path, required=True)
    retrieval.add_argument("--corpus", type=Path, required=True)
    retrieval.add_argument("--split", required=True)
    retrieval.add_argument("--provider", required=True, choices=("hashing", "production"))
    retrieval.add_argument("--output", type=Path, required=True)
    retrieval.add_argument("--policy-id", default="dense-v1")

    paired = subparsers.add_parser(
        "run-paired-policies",
        help=(
            "Nonqualifying synthetic hashing paired diagnostics (never activates policies). "
            "The candidate arm is a seed-perturbed harness arm, not a real policy rank."
        ),
    )
    paired.add_argument("--incumbent", required=True)
    paired.add_argument("--candidate", required=True)
    paired.add_argument("--experiment", type=Path, required=True)
    paired.add_argument("--corpus", type=Path, required=True)
    paired.add_argument("--n-resamples", type=int, default=10_000)
    paired.add_argument("--seed", type=int, default=0)
    paired.add_argument("--output", type=Path, required=True)
    paired.add_argument(
        "--provider",
        choices=("hashing", "production"),
        default="hashing",
        help="Legacy fixture only; production must use the actual empirical adapter.",
    )

    abstain = subparsers.add_parser(
        "run-abstention-gate",
        help="Run nonqualifying synthetic hashing abstention diagnostics (no refit).",
    )
    abstain.add_argument("--calibration-split", required=True)
    abstain.add_argument("--final-split", required=True)
    abstain.add_argument("--experiment", type=Path, required=True)
    abstain.add_argument("--corpus", type=Path, required=True)
    abstain.add_argument("--output", type=Path, required=True)

    host = subparsers.add_parser(
        "run-host-tasks",
        help="Ingest precomputed host-verifier arm JSON; Magicite does not execute host tasks.",
    )
    host.add_argument("--corpus", type=Path, required=True)
    host.add_argument(
        "--arms",
        required=True,
        help="Comma-separated arms, e.g. no_skill,selected_skill,composed_plan",
    )
    host.add_argument("--n-resamples", type=int, default=10_000)
    host.add_argument("--seed", type=int, default=0)
    host.add_argument("--output", type=Path, required=True)

    for name in ("fit-calibration", "evaluate-frozen-calibration"):
        sp = subparsers.add_parser(name, help="Actual-router offline calibration; nonqualifying.")
        sp.add_argument("--freeze", type=Path, required=True)
        sp.add_argument("--project-root", type=Path, required=True)
        sp.add_argument("--model-cache", type=Path, required=True)
        sp.add_argument("--model-manifest", type=Path, required=True)
        sp.add_argument("--rank-depth", type=int, default=5)
        sp.add_argument("--output", type=Path, required=True)
        if name == "evaluate-frozen-calibration":
            sp.add_argument("--candidate", type=Path, required=True)
            sp.add_argument("--replay", action="store_true")
    args = parser.parse_args(argv)
    if args.command in {"fit-calibration", "evaluate-frozen-calibration"}:
        from magicite.eval import calibration_consumer as consumer
        from magicite.eval.production import verify_model

        try:
            model = json.loads(args.model_manifest.read_bytes())
            verify_model(args.model_cache, model)
            actual = consumer.cli_actual(args.project_root, args.model_cache, args.rank_depth)
            try:
                if args.command == "fit-calibration":
                    result = consumer.fit_calibration(
                        args.freeze, args.output, actual, model_cache=args.model_cache, model_manifest=model
                    )
                else:
                    result = consumer.evaluate_frozen_calibration(
                        args.freeze,
                        args.candidate,
                        args.output,
                        actual,
                        model_cache=args.model_cache,
                        replay=args.replay,
                    )
            finally:
                actual.conn.close()
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return _print_errors([str(exc)])

    if args.command == "validate-corpus":
        errors = validate_corpus(args.path)
        if errors:
            return _print_errors(errors)
        data = json.loads(args.path.read_text(encoding="utf-8"))
        print(f"validated {len(data['cases'])} independent composition cases")
        return 0

    if args.command.startswith("validate-"):
        data, load_errors = _load_json(args.path)
        if load_errors:
            return _print_errors(load_errors)
        assert data is not None

        validators = {
            "validate-experiment": validate_experiment_data,
            "validate-corpus-manifest": validate_corpus_manifest_data,
            "validate-prediction": validate_prediction_data,
            "validate-result": validate_result_data,
            "validate-claim": validate_claim_data,
        }
        errors = validators[args.command](data)
        if args.command == "validate-claim":
            for error in claim_eligible_for_new_run_gate(data):
                if error not in errors:
                    errors.append(error)
        if errors:
            return _print_errors(errors)
        print(f"{args.command} ok")
        return 0

    from magicite.eval import operator_cli as ops

    try:
        if args.command == "acquire-skillret":
            result = ops.cmd_acquire_skillret(
                archive=args.archive,
                expected_sha256=args.expected_sha256,
                license_name=args.license_name,
                revision=args.revision,
                corpus_json=args.corpus_json,
                output=args.output,
                acquired_at=args.acquired_at,
            )
        elif args.command == "run-retrieval":
            result = ops.cmd_run_retrieval(
                experiment_path=args.experiment,
                corpus_path=args.corpus,
                split=args.split,
                provider=args.provider,
                output=args.output,
                policy_id=args.policy_id,
            )
        elif args.command == "run-paired-policies":
            result = ops.cmd_run_paired_policies(
                incumbent=args.incumbent,
                candidate=args.candidate,
                experiment_path=args.experiment,
                corpus_path=args.corpus,
                n_resamples=args.n_resamples,
                seed=args.seed,
                output=args.output,
                provider=args.provider,
            )
        elif args.command == "run-abstention-gate":
            result = ops.cmd_run_abstention_gate(
                calibration_split=args.calibration_split,
                final_split=args.final_split,
                experiment_path=args.experiment,
                corpus_path=args.corpus,
                output=args.output,
            )
        elif args.command == "run-host-tasks":
            result = ops.cmd_run_host_tasks(
                corpus_path=args.corpus,
                arms=[a.strip() for a in args.arms.split(",") if a.strip()],
                n_resamples=args.n_resamples,
                seed=args.seed,
                output=args.output,
            )
        else:
            return _print_errors([f"unknown command {args.command!r}"])
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        return _print_errors([str(exc)])

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
