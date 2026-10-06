#!/usr/bin/env python3
"""Build/verify a bounded, candidate-bound r3 NO-GA qualification package.

Adapted from r2's clean-source/raw-check preconditions and digest references.
All release gates remain UNEVALUATED; criterion evidence is narrower than GA.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

PACKAGE = "docs/releases/v1/r3"
MAKER = "Vivi /root/implement_r3"
PRODUCT_REVIEWER = "VIGIL /root/verify_r3"
PR_SOURCE = "68725d4120569b246503d790bfcaaf85b7a11f0f"
CHECKS = ("pytest", "focused", "ruff", "mypy", "generated", "docs", "release-negative-controls")
SOURCES = (
    "uv.lock",
    "src/magicite/core/registry.py",
    "src/magicite/core/approvals.py",
    "src/magicite/obs/doctor.py",
    "src/magicite/core/trust_journal.py",
    "src/magicite/core/trust_custodian.py",
    "src/magicite/core/trust_custodian_transport.py",
    "src/magicite/core/writer_guard.py",
    "src/magicite/core/evidence.py",
    "src/magicite/core/fingerprint_key.py",
    "src/magicite/core/backup.py",
    "src/magicite/config.py",
    "tests/unit/core/test_review_approve_busy_wait.py",
    "tests/unit/obs/test_doctor.py",
    "tests/unit/obs/test_th_custody_zero_write_canary.py",
    "tests/integration/test_trust_recovery.py",
    "tests/unit/core/test_trust_atlas_fixes.py",
    "tests/unit/core/test_th_single_snapshot_sites.py",
    "tests/integration/test_th10_old_reader_downgrade.py",
    "scripts/check_release_manifest.py",
    ".spectra/plans/magicite-v1/release-gates.md",
    ".spectra/plans/v1-r3-qualification.acceptance.md",
)
GATE_REASONS = {
    "POLICY": (
        "Current tests pass; complete obligation-by-obligation policy adjudication "
        "is outside this bounded package."
    ),
    "EVIDENCE": "Independent final label provenance and a complete new empirical run chain are absent.",
    "SCHEMA": (
        "Schema regressions pass in the current suite; the full frozen "
        "roundtrip/migration/downgrade obligation set is not separately adjudicated."
    ),
    "TRUST": (
        "Approval completion and diagnostic gaps are repaired and independently "
        "reviewed; separate-UID deployment and all trust/adversarial release "
        "obligations remain unproven."
    ),
    "ROUTING": (
        "Current functional tests do not establish E3 quality or calibrated abstention on release workloads."
    ),
    "COMPOSITION": "Independent structural corpus and actual-host outcomes remain absent.",
    "PRIVACY": (
        "The successor map covers current trust/custody and evidence surfaces; all "
        "privacy lifecycle obligations have not received a complete fresh gate "
        "adjudication."
    ),
    "LEARNING": "Current regressions do not replace complete containment/gated-availability qualification.",
    "PROTOCOL": (
        "Current protocol tests do not establish the complete advertised host "
        "transcript and retry/cancel matrix."
    ),
    "RELIABILITY": (
        "Read-only doctor and bounded approval replay are observed; all-store "
        "kill/replay/fencing and complete backup/restore/upgrade matrix are "
        "outstanding."
    ),
    "PERFORMANCE": "Dedicated E6, production model, and supported-scale evidence are absent.",
    "DISTRIBUTION": "Published installs, TestPyPI rehearsal and fetched-distribution checks are outstanding.",
    "DOCS": (
        "Generated-reference and documentation contracts pass; the complete release "
        "operator/support/governance obligation set is not freshly adjudicated."
    ),
    "RC-CONTRACT": "Two compatible independently built RCs and a complete supported matrix are absent.",
    "EXTERNAL": "Native agent review is not independent external reproduction or two real pilots.",
    "SECURITY": (
        "PR #51 documents are immutable unmerged provenance; named human "
        "assessment, mitigation/expiry review and High residual sign-off remain "
        "pending."
    ),
    "GA-ALL": "Required release gates, maintainer sign-off and fetched-artifact qualification remain absent.",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ref(root: Path, relative: str) -> dict[str, str]:
    return {"path": relative, "sha256": digest(root / relative)}


def write(root: Path, relative: str, value: Any) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value, indent=2, sort_keys=True) + "\n")


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True)


def validator(root: Path) -> Any:
    spec = importlib.util.spec_from_file_location(
        "release_validator", root / "scripts/check_release_manifest.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_guard(root: Path, candidate: str) -> None:
    result = subprocess.run(
        ["git", "diff", "--quiet", candidate, "--", ".", f":(exclude){PACKAGE}"], cwd=root
    )
    if result.returncode:
        raise ValueError("non-package tracked tree differs from the tested candidate")
    if git(root, "diff", candidate, "--", "docs/releases/v1/r2"):
        raise ValueError("historical r2 changed")


def verify(root: Path, candidate: str, *, artifact_root: Path | None = None) -> dict[str, Any]:
    source_guard(root, candidate)
    root = artifact_root or root
    index = json.loads((root / PACKAGE / "package-index.json").read_text())
    if index["source_commit"] != candidate:
        raise ValueError("package source mismatch")
    expected_paths = {row["path"] for row in index["artifacts"]}
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in (root / PACKAGE).rglob("*")
        if path.is_file() and path.name != "package-index.json" and "__pycache__" not in path.parts
    }
    if expected_paths != actual_paths:
        raise ValueError("package artifact inventory mismatch")
    for row in index["artifacts"]:
        if ref(root, row["path"]) != row:
            raise ValueError("package artifact digest mismatch: " + row["path"])
    source = json.loads((root / PACKAGE / "evidence/source-bindings.json").read_text())
    if source["source_commit"] != candidate:
        raise ValueError("source bindings mismatch")
    for row in source["artifacts"]:
        if ref(root, row["path"]) != row:
            raise ValueError("candidate source artifact mismatch: " + row["path"])
    ledger = json.loads((root / PACKAGE / "criterion-ledger.json").read_text())
    for row in ledger["criteria"]:
        if row["source_commit"] != candidate:
            raise ValueError("criterion source mismatch")
        if ref(root, row["acceptance"]["path"]) != row["acceptance"]:
            raise ValueError("frozen acceptance artifact mismatch")
        for artifact in row.get("artifacts", []):
            if ref(root, artifact["path"]) != artifact:
                raise ValueError("criterion artifact mismatch")
    native = validator(root)
    manifest = json.loads((root / PACKAGE / "release-manifest.json").read_text())
    if manifest["source_commit"] != candidate or manifest["source_dirty"] is not False:
        raise ValueError("manifest source mismatch")
    for row in manifest["gates"]:
        for witness in row["evidence"]:
            native._witness(witness, root, row["id"], candidate)
    decision = native.validate(manifest, root)
    allowed = {f"{gate}: UNEVALUATED does not satisfy a mandatory gate" for gate in native.OBLIGATIONS}
    allowed |= {
        "RC-CONTRACT: two RCs required",
        "EXTERNAL: external reproduction or two-pilot path required",
        "GA-ALL: explicit maintainer sign-off at candidate source required",
    }
    if decision["eligible"] or set(decision["errors"]) - allowed:
        raise ValueError("unexpected release validator diagnostics: " + repr(decision))
    return {
        "integrity": "PASS",
        "source_commit": candidate,
        "artifacts_checked": len(index["artifacts"]),
        "release_decision": decision,
        "native_validator_expected_exit": 1,
    }


def build(root: Path, raw: Path, candidate: str, review: Path | None) -> None:
    source_guard(root, candidate)
    if (raw / "head.txt").read_text().strip() != candidate:
        raise ValueError("raw source differs from candidate")
    if any((raw / name).read_text() for name in ("status-before.txt", "status-after.txt")):
        raise ValueError("raw candidate was dirty")
    checks = json.loads((raw / "checks.json").read_text())
    if {row["id"] for row in checks} != set(CHECKS) or any(row["exit_code"] for row in checks):
        raise ValueError("required current check missing or failed")
    for row in checks:
        if row["source_commit"] != candidate or digest(raw / row["log"]) != row["sha256"]:
            raise ValueError("raw check source/log mismatch")
    destination = root / PACKAGE / "evidence"
    destination.mkdir(parents=True, exist_ok=True)
    for path in sorted(raw.iterdir()):
        if path.is_file():
            if path.resolve() != (destination / path.name).resolve():
                shutil.copy2(path, destination / path.name)
    scratch = raw.parent
    for name in (
        "review-final.md",
        "review-candidate-binding.md",
        "review-c2.md",
        "review-candidate-binding-c2.md",
        "independent-c2-focused.log",
        "independent-c2-adversarial.log",
        "test_independent_c2.py",
        "primary-preservation.json",
        "independent-focused-final.log",
        "independent-adversarial-final.log",
        "independent-adjacent-final.log",
        "test_independent_review_final.py",
    ):
        archived_name = name + ".txt" if name.endswith(".py") else name
        shutil.copy2(scratch / name, destination / archived_name)
    historical_runs = []
    for label, name, source_label in (
        ("base red gate", "red-regressions.log", "9692735 product source plus new acceptance test anchors"),
        (
            "independent audit finding red gate",
            "red-independent-audit.log",
            "pre-C1 uncommitted product repair",
        ),
        (
            "nested ownership red gate",
            "red-nested-owner.log",
            "6543f9d product source plus nested test anchor",
        ),
        (
            "failed C1 qualification",
            "candidate-evidence-6543f9d/pytest.txt",
            "6543f9dfbc7c49c77543462727ede62432895a5d",
        ),
    ):
        original = scratch / name
        text = original.read_text()
        historical_runs.append(
            {
                "label": label,
                "source": source_label,
                "original_log_sha256": digest(original),
                "summary": next(
                    (line for line in reversed(text.splitlines()) if re.search(r"\d+ failed", line)),
                    "failed red reproduction",
                ),
                "failed_nodes": re.findall(r"^FAILED (.+)$", text, re.M),
                "raw_storage": (
                    "immutable external qualification workspace; not copied into versioned package"
                ),
            }
        )
    write(
        root,
        PACKAGE + "/evidence/historical-failure-summary.json",
        {
            "schema": "magicite/red-gate-summary/1",
            "runs": historical_runs,
            "sanitization": (
                "Original logs are unchanged. Full traces omitted because pytest prints "
                "temporary paths and ephemeral fixture private-key canaries in failure argument repr."
            ),
            "current_acceptance_evidence": False,
        },
    )
    text = (raw / "pytest.txt").read_text()
    write(
        root,
        PACKAGE + "/evidence/test-results.json",
        {
            "source_commit": candidate,
            "full_summary": text.splitlines()[-1],
            "coverage": next((line for line in text.splitlines() if "Total coverage:" in line), None),
            "skips": [line for line in text.splitlines() if line.startswith("SKIPPED")],
            "deselection": (
                "One benchmark-marked wall-clock test excluded by the repository blocking CI command; "
                "not counted as performance qualification."
            ),
            "runtime_limit": (
                "Current macOS arm64 Python 3.12 run only; "
                "no fresh Linux/Python 3.11, OCI or published-distribution qualification."
            ),
        },
    )
    for name in ("findings-register", "threat-model", "r3-open-items"):
        shutil.copy2(scratch / "base-evidence" / (name + "-68725d4.md"), destination / (name + "-68725d4.md"))
    for name in ("build_r3_package.py", "privacy-data-map.template.md"):
        origin = Path(__file__).with_name(name)
        if origin.resolve() != (destination / name).resolve():
            shutil.copy2(origin, destination / name)
    write(
        root,
        PACKAGE + "/evidence/source-bindings.json",
        {
            "schema": "magicite/r3-source-bindings/1",
            "source_commit": candidate,
            "artifacts": [ref(root, path) for path in SOURCES],
            "historical_r2": {
                "base_source": "9692735e453c0b2f722ced1c251934839342bcb2",
                "base_tree_sha256": hashlib.sha256(
                    git(root, "ls-tree", "-r", "9692735", "--", "docs/releases/v1/r2").encode()
                ).hexdigest(),
                "candidate_tree_sha256": hashlib.sha256(
                    git(root, "ls-tree", "-r", candidate, "--", "docs/releases/v1/r2").encode()
                ).hexdigest(),
                "changed": bool(git(root, "diff", "9692735", candidate, "--", "docs/releases/v1/r2")),
            },
        },
    )
    write(
        root,
        PACKAGE + "/privacy-data-map.md",
        Path(__file__).with_name("privacy-data-map.template.md").read_text().format(source=candidate),
    )
    write(
        root,
        PACKAGE + "/evidence/inherited-provenance.json",
        {
            "pr51": {
                "source_commit": PR_SOURCE,
                "state": "UNMERGED; human security approval pending",
                "artifacts": [
                    ref(root, PACKAGE + "/evidence/" + name + "-68725d4.md")
                    for name in ("findings-register", "threat-model", "r3-open-items")
                ],
            },
            "linux_base": {
                "source_commit": "9692735e453c0b2f722ced1c251934839342bcb2",
                "run": "https://github.com/Rynaro/magicite/actions/runs/37076396315",
                "result": "1714 passed, 10 skipped; BASE ONLY; not a candidate witness",
            },
            "security_limits": [
                (
                    "AC-TH-10 maps to test_th10_old_reader_downgrade.py and TH-A01/F-10 High "
                    "residual; human review pending"
                ),
                "Projection deletion guards are inert regressions, not active authority defenses",
                (
                    "Unobserved trust-journal retry/reconcile assertions and isolated "
                    "append/replace fences remain open"
                ),
                "Real process death/lost-reply/socket/peer/ACL/OS branches and fuzz limitations remain open",
            ],
        },
    )
    reviewer = None
    approvals: set[str] = set()
    if review:
        decision = json.loads(review.read_text())
        if (
            decision["source_commit"] != candidate
            or decision["status"] != "APPROVED"
            or decision["reviewer"] == MAKER
        ):
            raise ValueError("invalid independent package review")
        if decision["gate_statuses"] != {gate: "UNEVALUATED" for gate in GATE_REASONS}:
            raise ValueError("review does not approve the bounded gate disposition")
        reviewer = decision["reviewer"]
        approvals = set(decision["approved_criteria"])
        shutil.copy2(review, destination / "package-review.json")
        report = review.with_suffix(".md")
        if digest(report) != decision["report_sha256"]:
            raise ValueError("package review report digest mismatch")
        shutil.copy2(report, destination / "package-review.md")
        for row in decision["evidence_logs"]:
            path = review.parent / row["name"]
            if digest(path) != row["sha256"]:
                raise ValueError("independent package review log digest mismatch")
            shutil.copy2(path, destination / row["name"])
        shutil.copy2(
            review.parent / "independent_package_check.py",
            destination / "independent_package_check.py.txt",
        )
    env = json.loads((raw / "environment.json").read_text())
    artifacts = [
        ref(root, PACKAGE + "/evidence/" + name)
        for name in (
            "focused.txt",
            "checks.json",
            "junit.xml",
            "review-final.md",
            "review-c2.md",
            "review-candidate-binding-c2.md",
        )
    ]
    package_reasons = {
        "AC-R3-10": "Independent verification bound current C2 witnesses to source and artifact digests.",
        "AC-R3-11": (
            "Positive composed overlay passed; mismatched source and modified artifact were rejected."
        ),
        "AC-R3-12": (
            "Independent adjudicator reviewed current privacy map and all 17 UNEVALUATED gate dispositions."
        ),
        "AC-R3-13": (
            "Native validator returned exit 1, eligible:false, NO-GA "
            "with exactly 20 unmet release requirements."
        ),
        "AC-R3-14": (
            "Historical v1/r2 source tree and primary tracked-diff/porcelain baseline remained unchanged; "
            "no untracked-content hash baseline was captured or byte-equivalence claimed."
        ),
    }
    package_support = {
        "AC-R3-10": ["source-bindings.json", "independent-package-references.log"],
        "AC-R3-11": ["independent-package-verify.log", "release-negative-controls.txt"],
        "AC-R3-12": ["inherited-provenance.json"],
        "AC-R3-13": ["independent-release-validator.log"],
        "AC-R3-14": ["primary-preservation.json", "source-bindings.json"],
    }
    criteria = []
    for acceptance in (
        ".spectra/plans/magicite-v1/acceptance.md",
        ".spectra/plans/magicite-v1-trust-hardening/acceptance.md",
        ".spectra/plans/v1-r3-qualification.acceptance.md",
    ):
        text = (root / acceptance).read_text()
        for match in re.finditer(r"^### (AC-[A-Z0-9-]+)[^\n]*\n(.*?)(?=^### |\Z)", text, re.M | re.S):
            identity = match[1]
            bounded = identity.startswith("AC-R3-") and int(identity.rsplit("-", 1)[1]) <= 9
            status = "PASS" if bounded or identity in approvals else "UNEVALUATED"
            criteria.append(
                {
                    "id": identity,
                    "frozen_text": match[2].strip(),
                    "source_commit": candidate,
                    "status": status,
                    "producer": MAKER,
                    "reviewer": PRODUCT_REVIEWER if bounded else reviewer,
                    "command": next(row["command"] for row in checks if row["id"] == "focused")
                    if bounded
                    else "Independent commands recorded in evidence/package-review.md"
                    if identity in approvals
                    else "See checks.json and package reproduction commands",
                    "environment": env,
                    "acceptance": ref(root, acceptance),
                    "artifacts": artifacts
                    if bounded
                    else [
                        ref(root, PACKAGE + "/evidence/" + name)
                        for name in ["package-review.json", "package-review.md"] + package_support[identity]
                    ]
                    + ([ref(root, PACKAGE + "/privacy-data-map.md")] if identity == "AC-R3-12" else [])
                    if identity in approvals
                    else [ref(root, PACKAGE + "/evidence/source-bindings.json")],
                    "reason": "Fresh bounded product tests and independent code review."
                    if bounded
                    else package_reasons[identity]
                    if identity in approvals
                    else (
                        "Whole frozen criterion is not re-adjudicated by this bounded milestone; "
                        "historical PASS labels are not imported."
                    ),
                }
            )
    write(
        root,
        PACKAGE + "/criterion-ledger.json",
        {"schema": "magicite/r3-criterion-ledger/1", "source_commit": candidate, "criteria": criteria},
    )
    # This narrow release obligation is observed; the whole RELIABILITY gate remains unproven.
    witness = {
        "schema": "magicite/release-witness/1",
        "gate": "RELIABILITY",
        "obligation": "zero-write-doctor",
        "status": "PASS",
        "source_commit": candidate,
        "producer": MAKER,
        "reviewer": PRODUCT_REVIEWER,
        "command": next(row["command"] for row in checks if row["id"] == "focused"),
        "environment": env,
        "lock": ref(root, "uv.lock"),
        "artifacts": artifacts,
        "scope": (
            "Same-account simulated custody, actual filesystem byte/entry/mode/mtime "
            "canaries; no separate-UID deployment claim"
        ),
    }
    write(root, PACKAGE + "/evidence/zero-write-doctor.json", witness)
    gates = [
        {
            "id": gate,
            "status": "UNEVALUATED",
            "reason": reason,
            "owner": "maintainer / qualification operator",
            "obligations": list(validator(root).OBLIGATIONS[gate]),
            "review_status": "APPROVED" if reviewer else "PENDING",
            "reviewer": reviewer,
            "evidence": [ref(root, PACKAGE + "/evidence/zero-write-doctor.json")]
            if gate == "RELIABILITY"
            else [],
        }
        for gate, reason in GATE_REASONS.items()
    ]
    manifest = {
        "schema": "magicite/release-manifest/1",
        "source_commit": candidate,
        "source_dirty": False,
        "draft_only": True,
        "producer": MAKER,
        "gates": gates,
        "release_candidates": [],
        "external": None,
        "maintainer_signoff": None,
        "criterion_ledger": ref(root, PACKAGE + "/criterion-ledger.json"),
    }
    write(root, PACKAGE + "/release-manifest.json", manifest)
    argv = [
        sys.executable,
        "scripts/check_release_manifest.py",
        PACKAGE + "/release-manifest.json",
        "--root",
        str(root),
    ]
    result = subprocess.run(argv, cwd=root, text=True, capture_output=True)
    write(
        root,
        PACKAGE + "/evidence/release-decision.json",
        {
            "source_commit": candidate,
            "argv": argv,
            "exit_code": result.returncode,
            "manifest": ref(root, PACKAGE + "/release-manifest.json"),
            "result": json.loads(result.stdout),
            "stderr": result.stderr,
        },
    )
    write(
        root,
        PACKAGE + "/gate-table.md",
        (
            "# R3 gate assessment: NO-GA\n\nAll 17 whole gates are UNEVALUATED. This does "
            "not assert 17 new failures; broader\nrelease obligations remain unproven by "
            "this bounded milestone.\n\nIndependent adjudicator: "
        )
        + (reviewer or "PENDING package review")
        + "\n\n| Gate | Status | Missing evidence / owner |\n|---|---|---|\n"
        + "\n".join(
            f"| {row['id']} | UNEVALUATED | {row['reason']} Owner: {row['owner']}. |" for row in gates
        )
        + "\n",
    )
    write(
        root,
        PACKAGE + "/README.md",
        f"""# V1 r3 qualification: NO-GA

Candidate `{candidate}` contains the approval replay and read-only custody diagnostic
repairs. The current supported Python 3.12 run (**1741 passed, 9 skipped, 1 deselected;
86.98% coverage**) and separate-agent product review are
in [evidence](evidence/checks.json). Five Docker-image checks skipped because the
image was not built; four unpublished channel reservations skipped. The one
wall-clock benchmark is deliberately excluded by the blocking CI test command.
Those skips/deselection are not PASS evidence. [All 17 whole gates](gate-table.md) remain
UNEVALUATED: this bounded assessment neither imports r2 PASS labels nor declares
17 new failures. [The criterion ledger](criterion-ledger.json) preserves the original
76 + additive 12 frozen texts plus 14 r3 milestone criteria. Unproven whole criteria
remain UNEVALUATED even when their tests pass. Independent package review:
{reviewer or "PENDING"}. Milestone criteria AC-R3-01 through AC-R3-14 pass under
the stated bounded verification; all 88 original release/trust criteria remain
UNEVALUATED. AC-R3-14 verifies tracked diff and porcelain status preservation;
no baseline hashes of untracked contents were captured. Native role review is not
external acceptance or live Gauge acceptance.

The [current privacy map](privacy-data-map.md) includes trust authority, protected
custody, enrollment, transport and approval audit as well as evidence surfaces.
PR #51 sources are pinned to `{PR_SOURCE}` and explicitly unmerged; the High
findings and TH-A01 old-reader residual still require named human decisions.
AC-TH-10 retains `tests/integration/test_th10_old_reader_downgrade.py`, TH-A01/F-10
and its pre-hardening binary residual. Remaining real crash/lost-reply/OS/transport,
fuzz and isolated fence gaps are recorded in [provenance](evidence/inherited-provenance.json).
The Linux 9692735 run is base-only. No publication, tag, RC pair, external validation,
separate-user deployment or maintainer security/GA sign-off is claimed.

Reproduction from a clean candidate checkout with Python 3.12 and all locked extras:

```sh
uv sync --frozen --all-extras --python 3.12
python -m pytest -q --cov=src/magicite --cov-fail-under=70 -m 'not benchmark'
python docs/releases/v1/r3/evidence/build_r3_package.py --verify --root . --candidate {candidate}
python scripts/check_release_manifest.py docs/releases/v1/r3/release-manifest.json --root .
```

The integrity check must succeed. The native release validator must exit **1**,
with `eligible: false` and only enumerated unmet whole-gate, RC, external and
maintainer requirements. It does not verify Git HEAD; the successor generator
checks exact non-package source-tree equality with C. A later evidence-only E
commit may be used when its non-package tracked tree is proven identical to C.
Run `--negative-controls` to witness source-mismatch and bound-artifact rejection.
Independent test sources are archived byte-for-byte as `.py.txt` evidence; copy
them to temporary `.py` files when rerunning the recorded pytest commands.
Raw collection commands, actual runtime, start/end times and log hashes are in
`evidence/checks.json`; rebuild with `--raw <collected-dir>`, `--candidate C`, and
optional `--review <independent-package-review.json>`. Private temporary paths in
recorded commands describe the actual run; use your equivalent isolated environment.

Primary preservation evidence covers unchanged tracked diff bytes and porcelain
status; no stronger untracked-content byte baseline was captured. Historical r2
is unchanged. This release remains blocked on human security review, deployment,
empirical performance/quality, the full supported RC/distribution matrix, external
evidence and explicit maintainer sign-off.
""",
    )
    index_rows = [
        ref(root, path.relative_to(root).as_posix())
        for path in sorted((root / PACKAGE).rglob("*"))
        if path.is_file() and path.name != "package-index.json" and "__pycache__" not in path.parts
    ]
    write(
        root,
        PACKAGE + "/package-index.json",
        {"schema": "magicite/r3-package-index/1", "source_commit": candidate, "artifacts": index_rows},
    )


def negative_controls(root: Path, candidate: str) -> dict[str, str]:
    with tempfile.TemporaryDirectory(prefix="magicite-r3-negative-") as directory:
        composed = Path(directory)
        for name in ("src", "tests", "scripts", ".spectra"):
            shutil.copytree(
                root / name, composed / name, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache")
            )
        for name in ("uv.lock", "pyproject.toml"):
            shutil.copy2(root / name, composed / name)
        shutil.copytree(root / PACKAGE, composed / PACKAGE)
        # A valid overlay must pass first: failures below must be caused by
        # the deliberate mutation, not the temporary layout or Git context.
        verify(root, candidate, artifact_root=composed)
        results = {"positive-overlay": "PASS"}
        index = composed / PACKAGE / "package-index.json"
        original = index.read_bytes()
        value = json.loads(original)
        value["source_commit"] = "0" * 40
        index.write_text(json.dumps(value))
        for label in ("mismatched-source", "modified-bound-artifact"):
            if label == "modified-bound-artifact":
                index.write_bytes(original)
                (composed / PACKAGE / "evidence/focused.txt").write_text("tampered\n")
            try:
                verify(root, candidate, artifact_root=composed)
            except ValueError as exc:
                results[label] = "REJECTED: " + str(exc)
            else:
                raise ValueError("negative control unexpectedly accepted: " + label)
        return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--raw", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--negative-controls", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if not re.fullmatch("[0-9a-f]{40}", args.candidate):
        raise ValueError("candidate SHA must be explicit")
    if not args.verify and not args.negative_controls:
        if args.raw is None:
            raise ValueError("raw current evidence directory required")
        build(root, args.raw, args.candidate, args.review)
    result = (
        negative_controls(root, args.candidate) if args.negative_controls else verify(root, args.candidate)
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
