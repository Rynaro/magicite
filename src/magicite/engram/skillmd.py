"""SKILL.md import + export (spec §5.3 step 3b, §5.4; docs/04 §SKILL.md
as Compile Target; CR-4, CR-7, CR-8).

``to_engram()`` is the conversion half of the unified ``register()``
pipeline (docs/04 closes FINDING-010: native ``.egr.md`` and SKILL.md
both end at the same ingestion endpoint). ``render_skillmd()`` is the
inverse: the "compile target" a stock Claude-Code-style harness can read
without knowing anything about ENGRAM.

Conversion rules (spec §5.3, verbatim):

- ``intent.does``      <- description (first sentence(s), <=200 chars)
- ``intent.use_when``  <- "Use when:" line, else the description tail,
  else ``"general purpose"``
- ``intent.not_when``  <- "Not when:" line, else the literal
  ``"unspecified — requires review"``
- ``triggers.positive``<- dedup(name tokens + description key phrases +
  use_when)  (target >=3)
- ``triggers.negative``<- ``[]`` (recorded as a lint warning, not an
  error)
- body sections        <- heading match; unmatched body -> Procedure
  verbatim
- ``origin='imported'``, ``status='nascent'``, ``verification_status='pending'``
- ``provenance_journal += {event:'imported', note:'migrated from
  SKILL.md', author:<actor>}``
- then ``lint(profile='import')``: any remaining violation (typically at
  least ``negative_triggers``, since ``triggers.negative`` is always
  empty for a fresh import) downgrades the landed ``status`` from
  ``nascent`` to ``draft`` (CR-4/CR-7) — the fresh engram never reaches
  ``routable`` without a human/agent filling the gaps via ``sharpen()``.

Idempotency (CR-8): the assigned ``id`` is the content-hash of
identity+routing (name/intent/triggers), computed once. Re-importing
byte-identical *description* content (e.g. the ``register(skill) ->
export -> register(skill)`` round trip, AC-018) always re-derives the
same ``id`` — the caller (``core/registry.py``) uses that to detect a
duplicate import and skip, rather than comparing the whole-file
``content_sha256`` (which would spuriously differ on every re-import's
fresh ``provenance_journal`` timestamp).
"""

from __future__ import annotations

import copy
import io
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarstring import LiteralScalarString

from magicite.engram import ids as ids_mod
from magicite.engram import lint as lint_mod
from magicite.engram import parser as parser_mod
from magicite.engram import writer as writer_mod
from magicite.engram.model import (
    Engram,
    EngramBody,
    EngramFrontmatter,
    Intent,
    Plasticity,
    ProvenanceJournalEntry,
    SkillMdSourceSnapshot,
    Triggers,
    Trust,
)

_NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_KNOWN_HEADING_RE = re.compile(r"^##\s+(Procedure|Pitfalls|Examples)\s*$", re.MULTILINE | re.IGNORECASE)
_USE_WHEN_MARKER_RE = re.compile(r"\buse when(?:\s*:)?\s*", re.IGNORECASE)
_NOT_WHEN_MARKER_RE = re.compile(r"\b(?:not when|not for)(?:\s*:)?\s*", re.IGNORECASE)
_DOES_MAX_LEN = 200
_NOT_WHEN_FALLBACK = "unspecified — requires review"
_USE_WHEN_FALLBACK = "general purpose"
_SKILLMD_RESERVED_KEYS = frozenset({"name", "description"})

_skill_yaml = YAML(typ="rt")
_skill_yaml.preserve_quotes = True
_skill_yaml.default_flow_style = False
_skill_yaml.width = 100000


class SkillMdParseError(parser_mod.EngramParseError):
    """Malformed SKILL.md (bad frontmatter fence, missing/invalid ``name``)."""


class SkillMdLossyExportError(ValueError):
    """Export would discard or overwrite preserved SKILL.md content."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class SkillMdSource:
    name: str
    description: str
    body_text: str
    extra_frontmatter: dict[str, Any]


def _plain_yaml_value(value: Any, *, path: str) -> Any:
    """Normalize host frontmatter into JSON-safe, deterministic values."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SkillMdParseError(f"SKILL.md frontmatter {path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        plain: dict[str, Any] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise SkillMdParseError(f"SKILL.md frontmatter {path} contains a non-string key: {key!r}")
            plain[key] = _plain_yaml_value(child, path=f"{path}.{key}")
        return plain
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain_yaml_value(child, path=f"{path}[{index}]") for index, child in enumerate(value)]
    raise SkillMdParseError(f"SKILL.md frontmatter {path} has unsupported YAML value {type(value).__name__}")


def parse_source(raw_text: str) -> SkillMdSource:
    """Split + validate a raw SKILL.md file into its ingestion inputs."""
    yaml_text, body_text = parser_mod.split_frontmatter(raw_text)
    doc = parser_mod.load_frontmatter_doc(yaml_text)
    name = doc.get("name")
    if not name or not _NAME_RE.match(str(name)):
        raise SkillMdParseError(f"SKILL.md frontmatter 'name' must match [a-z0-9-]{{1,64}}, got {name!r}")
    description = str(doc.get("description") or "")
    extra_frontmatter: dict[str, Any] = {}
    for key, value in doc.items():
        if not isinstance(key, str):
            raise SkillMdParseError(f"SKILL.md frontmatter contains a non-string key: {key!r}")
        if key in _SKILLMD_RESERVED_KEYS:
            continue
        extra_frontmatter[key] = _plain_yaml_value(value, path=key)
    return SkillMdSource(
        name=str(name),
        description=description,
        body_text=body_text,
        extra_frontmatter=extra_frontmatter,
    )


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s.strip()]


def _extract_intent(description: str) -> tuple[str, str, str]:
    """Returns ``(does, use_when, not_when)`` per the spec §5.3 fallback chain.

    A YAML ``|`` block scalar preserves the author's line wrapping, but
    that wrapping is arbitrary-column-width prose formatting, not a
    semantic delimiter: "Use when:"/"Not when:" routinely land *mid-line*
    (e.g. "...changing its download region. Use when: Steam downloads
    are far below..."), not just at a line start. Lines are therefore
    collapsed into one continuous text block first, and the markers are
    located as substrings (case-insensitive, word-anchored) within it.
    """
    text = " ".join(ln.strip() for ln in (description or "").strip().splitlines() if ln.strip())

    not_when: str | None = None
    m = _NOT_WHEN_MARKER_RE.search(text)
    if m:
        not_when = text[m.end() :].strip()
        text = text[: m.start()].strip()

    use_when: str | None = None
    m = _USE_WHEN_MARKER_RE.search(text)
    if m:
        use_when = text[m.end() :].strip()
        does = text[: m.start()].strip()
    else:
        does = text

    if use_when is None:
        # spec §5.3: "else the description tail" -- the last sentence of
        # whatever prose remains, when there is more than one sentence.
        sentences = _split_sentences(does)
        if len(sentences) >= 2:
            use_when = sentences[-1]
            does = " ".join(sentences[:-1]).strip()
        else:
            use_when = _USE_WHEN_FALLBACK

    does = does.strip() or "imported skill (no description provided)"
    if len(does) > _DOES_MAX_LEN:
        does = does[:_DOES_MAX_LEN].rstrip()

    if not_when is None:
        not_when = _NOT_WHEN_FALLBACK

    return does, use_when, not_when


def _derive_positive_triggers(name: str, does: str, use_when: str) -> list[str]:
    """dedup(name tokens + description key phrases + use_when), target >=3."""
    candidates: list[str] = [name.replace("-", " ").replace("_", " ")]
    candidates.extend(_split_sentences(does))
    if use_when:
        candidates.append(use_when)

    seen: set[str] = set()
    deduped: list[str] = []
    for c in candidates:
        c = c.strip()
        key = c.lower()
        if c and key not in seen:
            seen.add(key)
            deduped.append(c)
    return deduped


def _parse_body(body_text: str) -> EngramBody:
    if _KNOWN_HEADING_RE.search(body_text):
        return parser_mod.parse_body(body_text)
    # Preserve unmatched bodies as unstructured Procedure content while still
    # parsing fenced blocks as inert text for trust inspection.
    stripped = body_text.strip()
    if not stripped:
        return EngramBody()
    return parser_mod.parse_body(f"## Procedure\n{stripped}\n")


def _body_projection_sha256(body: EngramBody) -> str:
    """Hash semantic body content while deliberately excluding learning stats."""
    payload = {
        "procedure": [
            {"step_no": step.step_no, "text": step.text, "fault_class": step.fault_class}
            for step in sorted(body.procedure, key=lambda step: step.step_no)
        ],
        "procedure_raw": body.procedure_raw,
        "pitfalls": [pitfall.text for pitfall in body.pitfalls],
        "examples": [
            {"positive": example.positive, "text": example.text, "note": example.note}
            for example in body.examples
        ],
        "provenance_lines": list(body.provenance_lines),
        "exec_blocks": [{"language": block.language, "text": block.text} for block in body.exec_blocks],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return ids_mod.content_sha256(canonical.encode("utf-8"))


def to_engram(source: SkillMdSource, *, target_relpath: str, actor: str = "register") -> Engram:
    """Convert a parsed SKILL.md source into a fully-formed, ready-to-write
    :class:`Engram` (rendered content hashes included)."""
    does, use_when, not_when = _extract_intent(source.description)
    positive = _derive_positive_triggers(source.name, does, use_when)
    body = _parse_body(source.body_text)
    now = _now()

    engram_id = ids_mod.new_engram_id(
        ids_mod.identity_routing_payload(source.name, does, use_when, not_when, positive, [])
    )

    frontmatter = EngramFrontmatter(
        spec="engram/0.2",
        name=source.name,
        id=engram_id,
        version=1,
        provenance="imported",
        intent=Intent(does=does, use_when=use_when, not_when=not_when),
        triggers=Triggers(positive=positive, negative=[]),
        plasticity=Plasticity(status="nascent", last_checkpoint=now),
        trust=Trust(origin="imported", verification_status="pending"),
        skill_md_source=SkillMdSourceSnapshot(
            body_raw=source.body_text,
            projection_sha256=_body_projection_sha256(body),
            extra_frontmatter=source.extra_frontmatter,
        ),
        provenance_journal=[
            ProvenanceJournalEntry(
                version=1,
                timestamp=now,
                author=actor,
                event="imported",
                note="migrated from SKILL.md",
            )
        ],
    )

    engram = Engram(
        frontmatter=frontmatter,
        body=body,
        path=target_relpath,
        content_sha256="",
        body_sha256="",
        file_mtime_ns=0,
    )

    # CR-4/CR-7: import-profile lint downgrades hard errors to warnings, but
    # any remaining warning (triggers.negative is always [] for a fresh
    # import, so `negative_triggers` fires every time) demotes the landed
    # status from 'nascent' to 'draft' -- never silently routable.
    lint_result = lint_mod.lint(engram, profile="import")
    if lint_result.warnings:
        assert engram.frontmatter.plasticity is not None
        engram.frontmatter.plasticity.status = "draft"

    rendered_body = writer_mod.render_body(engram.body)
    engram.body_sha256 = ids_mod.body_sha256(rendered_body)
    engram.content_sha256 = ids_mod.content_sha256(writer_mod.render_document(engram).encode("utf-8"))
    return engram


def _render_body_stats_stripped(body: EngramBody) -> str:
    """docs/04: "Body: Procedure/Pitfalls/Examples with stats stripped;
    human-readable." -- no ``[ok/total]``, no ``(×N)`` counts, no
    Provenance journal, no exec blocks (a stock SKILL.md consumer does not
    understand any of those ENGRAM-specific concepts)."""
    parts: list[str] = ["## Procedure"]
    for step in sorted(body.procedure, key=lambda s: s.step_no):
        parts.append(f"{step.step_no}. {step.text}".rstrip())
    if body.procedure_raw:
        parts.extend(body.procedure_raw.splitlines())

    parts.append("")
    parts.append("## Pitfalls")
    for p in body.pitfalls:
        parts.append(f"- {p.text}")

    parts.append("")
    parts.append("## Examples")
    for ex in body.examples:
        sign = "+" if ex.positive else "-"
        parts.append(f"{sign} {ex.text}")

    lines = [line.rstrip() for line in parts]
    return "\n".join(lines).rstrip("\n") + "\n"


def _render_skillmd_frontmatter(engram: Engram) -> str:
    fm = engram.frontmatter
    desc_lines = [fm.intent.does, f"Use when: {fm.intent.use_when}"]
    if fm.intent.not_when:
        desc_lines.append(f"Not when: {fm.intent.not_when}")

    doc = CommentedMap()
    doc["name"] = fm.name
    doc["description"] = LiteralScalarString("\n".join(desc_lines))

    preserved = fm.skill_md_source
    extras = preserved.extra_frontmatter if preserved is not None else {}
    collisions = sorted(_SKILLMD_RESERVED_KEYS.intersection(extras))
    if collisions:
        joined = ", ".join(collisions)
        raise SkillMdLossyExportError(
            f"{fm.name!r} preserved SKILL.md frontmatter collides with reserved key(s): {joined}"
        )
    for key, value in extras.items():
        doc[key] = copy.deepcopy(value)

    buf = io.StringIO()
    _skill_yaml.dump(doc, buf)
    yaml_text = "\n".join(line.rstrip() for line in buf.getvalue().splitlines())
    return f"---\n{yaml_text}\n---\n"


def render_skillmd(engram: Engram) -> str:
    """Render a host SKILL.md without silently discarding preserved source."""
    header = _render_skillmd_frontmatter(engram)
    preserved = engram.frontmatter.skill_md_source
    if preserved is not None:
        current = _body_projection_sha256(engram.body)
        if current != preserved.projection_sha256:
            raise SkillMdLossyExportError(
                f"{engram.name!r} has a preserved SKILL.md body, but its "
                "structured body changed; refusing a lossy export. Review a "
                "canonical export and remove 'skill_md_source', or re-import "
                "the source SKILL.md."
            )
        return header + preserved.body_raw

    return header + "\n" + _render_body_stats_stripped(engram.body)
