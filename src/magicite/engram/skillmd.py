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

import re
from dataclasses import dataclass
from datetime import UTC, datetime

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
    Triggers,
    Trust,
)

_NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_KNOWN_HEADING_RE = re.compile(r"^##\s+(Procedure|Pitfalls|Examples)\s*$", re.MULTILINE | re.IGNORECASE)
_USE_WHEN_MARKER_RE = re.compile(r"\buse when:\s*", re.IGNORECASE)
_NOT_WHEN_MARKER_RE = re.compile(r"\bnot when:\s*", re.IGNORECASE)
_DOES_MAX_LEN = 200
_NOT_WHEN_FALLBACK = "unspecified — requires review"
_USE_WHEN_FALLBACK = "general purpose"


class SkillMdParseError(parser_mod.EngramParseError):
    """Malformed SKILL.md (bad frontmatter fence, missing/invalid ``name``)."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class SkillMdSource:
    name: str
    description: str
    body_text: str


def parse_source(raw_text: str) -> SkillMdSource:
    """Split + validate a raw SKILL.md file into its two ingestion inputs."""
    yaml_text, body_text = parser_mod.split_frontmatter(raw_text)
    doc = parser_mod.load_frontmatter_doc(yaml_text)
    name = doc.get("name")
    if not name or not _NAME_RE.match(str(name)):
        raise SkillMdParseError(
            f"SKILL.md frontmatter 'name' must match [a-z0-9-]{{1,64}}, got {name!r}"
        )
    description = str(doc.get("description") or "")
    return SkillMdSource(name=str(name), description=description, body_text=body_text)


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
    # spec §5.3: "unmatched body -> Procedure verbatim".
    stripped = body_text.strip()
    if not stripped:
        return EngramBody()
    return EngramBody(procedure_raw=stripped)


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


def render_skillmd(engram: Engram) -> str:
    """The inverse compile target (spec §5.4, docs/04 §SKILL.md as Compile
    Target): ``name`` + a ``description`` composed from
    ``does``/``use_when``/``not_when``, plus a stats-stripped body."""
    fm = engram.frontmatter
    desc_lines = [fm.intent.does, f"Use when: {fm.intent.use_when}"]
    if fm.intent.not_when:
        desc_lines.append(f"Not when: {fm.intent.not_when}")

    header_lines = ["---", f"name: {fm.name}", "description: |"]
    header_lines.extend(f"  {line}".rstrip() for line in desc_lines)
    header_lines.append("---")

    body_text = _render_body_stats_stripped(engram.body)
    return "\n".join(header_lines) + "\n\n" + body_text
