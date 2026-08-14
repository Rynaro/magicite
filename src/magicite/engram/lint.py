"""Strict + import lint profiles (docs/04 register() rules, CR-4).

- ``strict`` (native ``.egr.md``): any violation is a hard error — "format
  discipline is the learning signal" (docs/04).
- ``import`` (SKILL.md conversion, wired in M1): the same rule set, but
  violations downgrade to warnings and the engram lands
  ``status='draft', verification_status='pending'`` — never routable,
  never silently accepted (CR-4).

``injection_scan`` is the docs/06 §Injection Surfaces check referenced by
register() step 5; it is exposed here for M1/M5 to wire in, not yet
called by the M0 register() path (M0 only ingests trusted native
fixtures).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from magicite.engram.model import Engram

LintProfile = Literal["strict", "import"]
Severity = Literal["error", "warning"]

MIN_POSITIVE_TRIGGERS = 3
MIN_NEGATIVE_TRIGGERS = 1


@dataclass(frozen=True)
class LintIssue:
    rule: str
    message: str
    severity: Severity


@dataclass
class LintResult:
    profile: LintProfile
    issues: list[LintIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[LintIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[LintIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        """True iff there is nothing that blocks ingestion under this profile."""
        return not self.errors


def _base_rules(engram: Engram) -> list[LintIssue]:
    issues: list[LintIssue] = []
    fm = engram.frontmatter

    if fm.spec != "engram/0.2":
        issues.append(LintIssue("spec_version", f"unsupported spec version {fm.spec!r}", "error"))

    if len(fm.triggers.positive) < MIN_POSITIVE_TRIGGERS:
        issues.append(
            LintIssue(
                "positive_triggers",
                f"expected >= {MIN_POSITIVE_TRIGGERS} positive triggers, got "
                f"{len(fm.triggers.positive)}",
                "error",
            )
        )
    if len(fm.triggers.negative) < MIN_NEGATIVE_TRIGGERS:
        issues.append(
            LintIssue(
                "negative_triggers",
                f"expected >= {MIN_NEGATIVE_TRIGGERS} negative trigger, got "
                f"{len(fm.triggers.negative)}",
                "error",
            )
        )
    if not fm.intent.not_when or not fm.intent.not_when.strip():
        issues.append(LintIssue("not_when", "intent.not_when is required and must be non-empty", "error"))

    if not engram.body.procedure:
        issues.append(LintIssue("procedure_numbered", "Procedure section has no numbered steps", "error"))
    if engram.body.procedure_raw.strip():
        issues.append(
            LintIssue(
                "procedure_numbered",
                "Procedure section contains text that is not a numbered step",
                "error",
            )
        )

    step_numbers = [s.step_no for s in engram.body.procedure]
    if step_numbers and step_numbers != list(range(1, len(step_numbers) + 1)):
        issues.append(
            LintIssue("procedure_order", "Procedure steps must be numbered 1..N with no gaps", "error")
        )

    versions = [e.version for e in fm.provenance_journal]
    if versions and versions != sorted(versions):
        issues.append(
            LintIssue("provenance_append_only", "provenance_journal versions must be non-decreasing", "error")
        )
    if fm.provenance_journal and fm.provenance_journal[-1].version > fm.version:
        issues.append(
            LintIssue(
                "provenance_append_only",
                "latest provenance_journal entry versions past frontmatter.version",
                "error",
            )
        )

    return issues


def lint(engram: Engram, profile: LintProfile = "strict") -> LintResult:
    issues = _base_rules(engram)
    if profile == "import":
        issues = [
            LintIssue(i.rule, i.message, "warning") if i.severity == "error" else i for i in issues
        ]
    return LintResult(profile=profile, issues=issues)


def lint_strict(engram: Engram) -> LintResult:
    return lint(engram, profile="strict")


def lint_import(engram: Engram) -> LintResult:
    return lint(engram, profile="import")


# ── injection scan (docs/06 §Injection Surfaces; wired by register() in M1/M5) ──

_SUSPICIOUS_PHRASES = (
    "ignore previous instructions",
    "ignore all previous",
    "disregard the above",
    "you must now",
    "system prompt",
    "act as",
    "reveal your instructions",
)


@dataclass
class InjectionScanResult:
    has_exec_blocks: bool
    over_broad_triggers: bool
    suspicious_pitfalls: list[str]

    @property
    def quarantine_recommended(self) -> bool:
        return self.has_exec_blocks or self.over_broad_triggers or bool(self.suspicious_pitfalls)


def injection_scan(engram: Engram, probe_queries: list[str] | None = None) -> InjectionScanResult:
    has_exec_blocks = bool(engram.body.exec_blocks)

    over_broad = False
    if probe_queries:
        triggers = [t.lower() for t in engram.frontmatter.triggers.positive]
        hits = sum(
            1
            for q in probe_queries
            if any(t and t in q.lower() for t in triggers)
        )
        over_broad = (hits / len(probe_queries)) > 0.3

    suspicious = [
        p.text
        for p in engram.body.pitfalls
        if any(phrase in p.text.lower() for phrase in _SUSPICIOUS_PHRASES)
    ]

    return InjectionScanResult(
        has_exec_blocks=has_exec_blocks,
        over_broad_triggers=over_broad,
        suspicious_pitfalls=suspicious,
    )
