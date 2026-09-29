#!/usr/bin/env python3
"""Fail when workflow/container executable inputs are mutable.

S13 scaffold also owns the release-manifest write/verify surface used by CI
and the release harness. Published-channel signature/OIDC checks remain
gated on the ``published-channels`` milestone (after S11/S15).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHA = re.compile(r"[0-9a-f]{40}")
MANIFEST_SCHEMA = "magicite/release-manifest/1"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_immutable_inputs() -> list[str]:
    failures: list[str] = []
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        text = workflow.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("uses: "):
                continue
            reference = stripped.split()[1]
            revision = reference.rsplit("@", 1)[-1]
            if SHA.fullmatch(revision) is None:
                failures.append(f"{workflow.relative_to(ROOT)} has non-SHA action input {reference}")

    for dockerfile_name in ("Dockerfile", "Dockerfile.dev"):
        dockerfile_path = ROOT / dockerfile_name
        if not dockerfile_path.is_file():
            failures.append(f"missing {dockerfile_name}")
            continue
        dockerfile = dockerfile_path.read_text(encoding="utf-8")
        for line in dockerfile.splitlines():
            stripped = line.strip()
            if stripped.startswith("FROM ") or stripped.startswith("COPY --from="):
                image = (
                    stripped.split()[1]
                    if stripped.startswith("FROM ")
                    else stripped.split("=", 1)[1].split()[0]
                )
                if image in {"builder", "runtime"}:
                    continue
                if "@sha256:" not in image:
                    failures.append(f"mutable container input ({dockerfile_name}): {stripped}")
    return failures


def build_manifest(*, artifacts_dir: Path, version: str | None = None) -> dict:
    if not artifacts_dir.is_dir():
        raise FileNotFoundError(f"artifacts directory not found: {artifacts_dir}")
    artifacts: list[dict[str, str]] = []
    for path in sorted(artifacts_dir.iterdir()):
        if not path.is_file():
            continue
        if path.name.endswith(".json") and "manifest" in path.name:
            continue
        artifacts.append(
            {
                "name": path.name,
                "path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
                "sha256": _sha256_file(path),
                "bytes": str(path.stat().st_size),
            }
        )
    if not artifacts:
        raise FileNotFoundError(f"no artifacts found under {artifacts_dir}")
    resolved_version = version
    if resolved_version is None:
        # Prefer wheel filename version when present: magicite-0.3.1-*.whl
        for artifact in artifacts:
            name = artifact["name"]
            if name.startswith("magicite-") and name.endswith(".whl"):
                resolved_version = name.removeprefix("magicite-").split("-py")[0]
                break
    return {
        "schema": MANIFEST_SCHEMA,
        "version": resolved_version or "unknown",
        "artifacts": artifacts,
    }


def write_manifest(*, artifacts_dir: Path, output: Path, version: str | None = None) -> dict:
    payload = build_manifest(artifacts_dir=artifacts_dir, version=version)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def verify_manifest(manifest_path: Path, *, artifacts_root: Path | None = None) -> list[str]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    if payload.get("schema") != MANIFEST_SCHEMA:
        failures.append(f"unexpected manifest schema {payload.get('schema')!r}; expected {MANIFEST_SCHEMA!r}")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        failures.append("manifest has no artifacts[] entries")
        return failures

    root = artifacts_root or ROOT
    for entry in artifacts:
        if not isinstance(entry, dict):
            failures.append(f"invalid artifact entry: {entry!r}")
            continue
        name = entry.get("name")
        declared = entry.get("sha256")
        rel = entry.get("path")
        if not name or not declared:
            failures.append(f"artifact missing name/sha256: {entry!r}")
            continue
        candidates = []
        if isinstance(rel, str):
            candidates.append(Path(rel) if Path(rel).is_absolute() else root / rel)
        candidates.append(root / "dist" / name)
        candidates.append(manifest_path.parent / name)
        path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if path is None:
            failures.append(f"missing artifact file for {name}")
            continue
        actual = _sha256_file(path)
        if actual != declared:
            failures.append(f"digest mismatch for {name}: declared={declared} actual={actual}")
        # Scaffold hooks for later published-channels signature/provenance fields.
        # Presence is optional until S13-published-channels; when present they must be non-empty.
        for optional_key in ("signature", "provenance", "sbom"):
            value = entry.get(optional_key)
            if value is not None and not str(value).strip():
                failures.append(f"{name} declares empty {optional_key}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-manifest",
        type=Path,
        help="Write a release-manifest JSON for files under --artifacts",
    )
    parser.add_argument(
        "--verify-manifest",
        type=Path,
        help="Verify artifact digests declared by a release-manifest JSON",
    )
    parser.add_argument(
        "--artifacts",
        type=Path,
        default=ROOT / "dist",
        help="Artifact directory for --write-manifest (default: ./dist)",
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Optional package version to record in the written manifest",
    )
    parser.add_argument(
        "--skip-immutable-inputs",
        action="store_true",
        help="Skip the workflow/Dockerfile pin check (manifest-only mode)",
    )
    args = parser.parse_args(argv)

    failures: list[str] = []
    if not args.skip_immutable_inputs:
        failures.extend(check_immutable_inputs())

    if args.write_manifest is not None:
        payload = write_manifest(
            artifacts_dir=args.artifacts,
            output=args.write_manifest,
            version=args.version,
        )
        print(
            f"wrote {args.write_manifest} with {len(payload['artifacts'])} artifact(s) "
            f"(schema={MANIFEST_SCHEMA})"
        )

    if args.verify_manifest is not None:
        failures.extend(verify_manifest(args.verify_manifest))

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    if args.verify_manifest is None and args.write_manifest is None:
        print("supply-chain inputs are immutable")
    elif args.verify_manifest is not None:
        print("release manifest digests match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
