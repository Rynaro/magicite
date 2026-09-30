"""UNEVALUATED release-evidence catalog (evaluation.md / release-gates.md).

External corpora, production-provider dedicated-runner budgets, and hybrid
paired comparisons that cannot be obtained offline in this sandbox are
recorded here with the exact operator command. Never emit fabricated
PASS/metric numbers for these items.

Commands listed here MUST match ``python -m magicite.eval`` subparser flags
(or ``scripts/run_benchmark_matrix.py`` flags) exactly — see
``tests/unit/eval/test_scale_unevaluated.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class UnevaluatedItem:
    item_id: str
    gate: str
    reason: str
    operator_command: str
    manifest_or_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "gate": self.gate,
            "status": "UNEVALUATED",
            "reason": self.reason,
            "operator_command": self.operator_command,
            "manifest_or_digest": self.manifest_or_digest,
        }


#: Operator commands assume a clean checkout at the experiment's source_commit
#: with production FastEmbed model bytes already cached offline.
UNEVALUATED_CATALOG: tuple[UnevaluatedItem, ...] = (
    UnevaluatedItem(
        item_id="skillret-official-final-split",
        gate="ROUTING/EXTERNAL",
        reason=(
            "Official SkillRet evaluation split (arXiv:2605.05726v3) is not "
            "shipped in-repo; CI uses docs/evaluation/v1/fixtures/skillret-tiny only"
        ),
        operator_command=(
            "python -m magicite.eval acquire-skillret "
            "--archive /path/to/skillret-official.tar.gz "
            "--expected-sha256 <ARCHIVE_SHA256> "
            "--license <LICENSE> --revision <GIT_OR_RELEASE_SHA> "
            "--corpus-json /path/to/corpus_manifest.json "
            "--output artifacts/skillret-acquired-corpus.json "
            "&& python -m magicite.eval run-retrieval "
            "--experiment docs/evaluation/v1/preregistration-template.json "
            "--corpus artifacts/skillret-acquired-corpus.json "
            "--split final --provider production --output artifacts/skillret-final/"
        ),
        manifest_or_digest=(
            "ExperimentManifest/1 with corpus_sha256 of the acquired official "
            "split; pin arXiv:2605.05726v3 + archive SHA-256 via "
            "magicite.eval.external.record_external_download"
        ),
    ),
    UnevaluatedItem(
        item_id="real-licensed-10k-corpus",
        gate="PERFORMANCE",
        reason=(
            "A 10k support claim requires both synthetic 10k and a real licensed "
            "10k corpus (evaluation.md E6); real corpus is not available offline here. "
            "When --corpus-manifest is supplied the matrix builds/measures that corpus "
            "and labels corpus.kind=manifest; synthetic-only runs are corpus.kind=synthetic "
            "and never GA-eligible."
        ),
        operator_command=(
            "python scripts/run_benchmark_matrix.py "
            "--profile supported-10k --provider production "
            "--corpus-manifest /path/to/licensed-10k/corpus_manifest.json "
            "--environment-label dedicated-linux-amd64-4c-16g "
            "--envelope-mode budget --output artifacts/e6-supported-10k-real.json"
        ),
        manifest_or_digest=(
            "CorpusManifest/1 content_identity_sha256 for the licensed 10k corpus "
            "+ magicite-benchmark-profile-result/1 with corpus.kind=manifest "
            "(synthetic runs set corpus.kind=synthetic and ga_eligible=false)"
        ),
    ),
    UnevaluatedItem(
        item_id="production-provider-e6-budgets",
        gate="PERFORMANCE",
        reason=(
            "Dedicated Linux amd64 4c/16GiB reference runner budgets for the "
            "production FastEmbed provider were not executed in this sandbox"
        ),
        operator_command=(
            "python scripts/run_benchmark_matrix.py "
            "--profile supported-10k --provider production "
            "--calls 1000 --warmup 50 --repetitions 3 "
            "--environment-label dedicated-linux-amd64-4c-16g "
            "--envelope-mode budget --output artifacts/e6-supported-10k-production.json"
        ),
        manifest_or_digest=(
            "magicite-benchmark-profile-result/1 with fingerprint.provider=production "
            "and model_digest of the pinned FastEmbed artifact"
        ),
    ),
    UnevaluatedItem(
        item_id="hybrid-rrf-vs-dense-v1-paired",
        gate="ROUTING",
        reason=(
            "Hybrid vs dense-v1 paired Hit@1 comparison on a locked final split "
            "is not run here; promotion stays with the frozen dense-v1 incumbent"
        ),
        operator_command=(
            "python -m magicite.eval run-paired-policies "
            "--incumbent dense-v1 --candidate hybrid-rrf-v1 "
            "--experiment docs/evaluation/v1/preregistration-template.json "
            "--corpus /path/to/locked-final-corpus.json "
            "--n-resamples 10000 --seed 0 "
            "--output artifacts/hybrid-vs-dense-verdict.json"
        ),
        manifest_or_digest=(
            "ResultManifest/1 aggregates + Verdict JSON; on fail/inconclusive call "
            "policy_store.retain_simple_incumbent_evidence(...); never activate()"
        ),
    ),
    UnevaluatedItem(
        item_id="empirical-abstention-bounds",
        gate="ROUTING",
        reason=(
            "Calibrated abstention Wilson/bootstrap bounds on locked no-match and "
            "answerable final queries require a sealed final split not opened here"
        ),
        operator_command=(
            "python -m magicite.eval run-abstention-gate "
            "--calibration-split calibration --final-split final "
            "--experiment docs/evaluation/v1/preregistration-template.json "
            "--corpus /path/to/locked-corpus.json "
            "--output artifacts/abstention-gate.json"
        ),
        manifest_or_digest=(
            "Calibration artifact digest + ResultManifest/1 abstention aggregates"
        ),
    ),
    UnevaluatedItem(
        item_id="host-task-usefulness-corpus",
        gate="COMPOSITION",
        reason=(
            "GA usefulness claims need an independently authored host-task corpus "
            "with paired no-skill/selected-skill/composed-plan arms at ≥30 groups; "
            "in-repo echo fixture only exercises the pipeline"
        ),
        operator_command=(
            "python -m magicite.eval run-host-tasks "
            "--corpus /path/to/host-task-corpus.json "
            "--arms no_skill,selected_skill,composed_plan "
            "--n-resamples 10000 --seed 0 "
            "--output artifacts/host-task-usefulness.json"
        ),
        manifest_or_digest=(
            "Claim/1 evidence_class=host-task bound to ResultManifest/1 with "
            "paired HostTaskArmResult digests"
        ),
    ),
    UnevaluatedItem(
        item_id="exploratory-50k-envelope",
        gate="PERFORMANCE",
        reason="50k exploratory profile is opt-in; no GA support claim without amended budget",
        operator_command=(
            "python scripts/run_benchmark_matrix.py "
            "--profile exploratory-50k --provider production "
            "--opt-in-exploratory --environment-label dedicated-linux-amd64-4c-16g "
            "--output artifacts/e6-exploratory-50k.json"
        ),
        manifest_or_digest="magicite-benchmark-profile-result/1 for exploratory-50k",
    ),
)


def unevaluated_catalog() -> list[dict[str, Any]]:
    return [item.to_dict() for item in UNEVALUATED_CATALOG]


def unevaluated_by_id(item_id: str) -> UnevaluatedItem:
    for item in UNEVALUATED_CATALOG:
        if item.item_id == item_id:
            return item
    raise KeyError(item_id)


__all__ = [
    "UNEVALUATED_CATALOG",
    "UnevaluatedItem",
    "unevaluated_by_id",
    "unevaluated_catalog",
]
