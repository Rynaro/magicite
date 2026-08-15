"""Atomic ``.egr.md`` writer (spec §2.5, §6.2 G2/G3).

Write path: ``tmp = <path>.tmp`` -> write -> ``fsync(file)`` ->
``os.replace(tmp, path)`` -> ``fsync(dir)``. Never partial, never in
place (AC-007).

Determinism (AC-021): floats render to 4 decimals, the ``synapses:`` list
sorts by ``(type, target)``, LF endings, no trailing whitespace — two
checkpoints of identical state produce byte-identical files.

M1 note: G2 (lease assertion, spec §6.2) is real from M1 onward --
``atomic_write()`` asserts ``storage.lease.assert_single_writer()``, so
every caller (``register()``/``sync()``/``export()`` in ``core/registry.py``)
must hold ``storage.lease.writer_lease()`` around the call. G3 (the
Dream-context assertion) stays a no-op stub until M4 defines
``core.dream.assert_dream_context()`` -- it is scoped to the specific
plasticity/synapses-rendering functions Dream's checkpoint phase calls,
not to this generic atomic-replace primitive (see ``storage/lease.py``'s
module docstring for the full rationale). Call sites do not change shape
when G3 lands for real.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from magicite.engram.model import Engram, EngramBody
from magicite.storage.lease import assert_single_writer

_yaml = YAML(typ="rt")
_yaml.preserve_quotes = True
_yaml.default_flow_style = False
_yaml.width = 100000


def assert_dream_context_stub() -> None:
    """M0/M1 stub for G3. Becomes ``core.dream.assert_dream_context()`` in M4."""
    return None


def atomic_write(path: str | Path, content: str) -> None:
    """Replace ``path`` with ``content`` atomically. Never leaves a partial file.

    G2 (spec §6.2): raises :class:`magicite.storage.lease.WriterLeaseError`
    unless the caller holds ``storage.lease.writer_lease()`` (AC-007's
    "only ever replaced atomically" is necessary but not sufficient --
    it must also only ever happen under the single-writer lease).
    """
    assert_single_writer()
    assert_dream_context_stub()

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")

    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    os.replace(tmp_path, path)

    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _round4(value: float) -> float:
    return round(float(value), 4)


def _fresh_doc(engram: Engram) -> CommentedMap:
    """Build a ruamel document from scratch (no prior round-trip carrier)."""
    doc: CommentedMap = CommentedMap()
    fm = engram.frontmatter
    doc["spec"] = fm.spec
    doc["name"] = fm.name
    doc["id"] = fm.id
    doc["version"] = fm.version
    doc["provenance"] = fm.provenance
    if fm.parents:
        doc["parents"] = list(fm.parents)

    intent: CommentedMap = CommentedMap()
    intent["does"] = fm.intent.does
    intent["use_when"] = fm.intent.use_when
    if fm.intent.not_when is not None:
        intent["not_when"] = fm.intent.not_when
    doc["intent"] = intent

    triggers: CommentedMap = CommentedMap()
    triggers["positive"] = list(fm.triggers.positive)
    triggers["negative"] = list(fm.triggers.negative)
    doc["triggers"] = triggers

    if fm.context_affinity:
        doc["context_affinity"] = list(fm.context_affinity)
    if fm.embedding is not None:
        emb: CommentedMap = CommentedMap()
        emb["model"] = fm.embedding.model
        emb["ref"] = fm.embedding.ref
        emb["last_refreshed"] = fm.embedding.last_refreshed
        doc["embedding"] = emb

    if fm.plasticity is not None:
        doc["plasticity"] = _render_plasticity(fm.plasticity)

    doc["synapses"] = _render_synapses(fm.synapses)

    doc["needs"] = list(fm.needs)
    doc["yields"] = list(fm.yields)
    doc["composes"] = list(fm.composes)
    doc["inhibits"] = list(fm.inhibits)
    doc["affinity"] = list(fm.affinity)

    if fm.provenance_journal:
        journal = CommentedSeq()
        for entry in fm.provenance_journal:
            item = CommentedMap()
            item["version"] = entry.version
            item["timestamp"] = entry.timestamp
            item["author"] = entry.author
            item["event"] = entry.event
            if entry.note is not None:
                item["note"] = entry.note
            if entry.summary_of_change is not None:
                item["summary_of_change"] = entry.summary_of_change
            if entry.signal_tier is not None:
                item["signal_tier"] = entry.signal_tier
            if entry.base_version is not None:
                item["base_version"] = entry.base_version
            journal.append(item)
        doc["provenance_journal"] = journal

    if fm.trust is not None:
        trust: CommentedMap = CommentedMap()
        trust["origin"] = fm.trust.origin
        trust["verification_status"] = fm.trust.verification_status
        if fm.trust.signer is not None:
            trust["signer"] = fm.trust.signer
        if fm.trust.import_source is not None:
            trust["import_source"] = fm.trust.import_source
        doc["trust"] = trust

    if fm.exports is not None:
        doc["exports"] = CommentedMap({"skill_md": fm.exports.skill_md})

    return doc


def _render_plasticity(plasticity: Any) -> CommentedMap:
    node: CommentedMap = CommentedMap()
    node["storage_strength"] = _round4(plasticity.storage_strength)
    node["exposure_count"] = plasticity.exposure_count
    outcome: CommentedMap = CommentedMap()
    outcome["success"] = plasticity.outcome.success
    outcome["failure"] = plasticity.outcome.failure
    node["outcome"] = outcome
    node["last_applied"] = plasticity.last_applied
    node["excitability"] = _round4(plasticity.excitability)
    node["last_checkpoint"] = plasticity.last_checkpoint
    node["status"] = plasticity.status
    return node


def _render_synapses(synapses: list[Any]) -> CommentedSeq:
    ordered = sorted(synapses, key=lambda s: (s.type, s.target))
    seq = CommentedSeq()
    for s in ordered:
        item: CommentedMap = CommentedMap()
        item["target"] = s.target
        item["type"] = s.type
        item["storage_strength"] = _round4(s.storage_strength)
        item["evidence_count"] = s.evidence_count
        item["provenance"] = s.provenance
        item["first_observed"] = s.first_observed
        if s.last_updated is not None:
            item["last_updated"] = s.last_updated
        seq.append(item)
    return seq


def render_frontmatter(engram: Engram, frontmatter_doc: Any | None = None) -> str:
    """Render the YAML frontmatter block (without the ``---`` fences)."""
    if frontmatter_doc is not None:
        doc = frontmatter_doc
        doc["version"] = engram.frontmatter.version
        if engram.frontmatter.plasticity is not None:
            doc["plasticity"] = _render_plasticity(engram.frontmatter.plasticity)
        doc["synapses"] = _render_synapses(engram.frontmatter.synapses)
    else:
        doc = _fresh_doc(engram)

    buf = io.StringIO()
    _yaml.dump(doc, buf)
    text = buf.getvalue()
    # Strip trailing whitespace per line; force LF (StringIO already uses \n).
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).rstrip("\n")


def render_body(body: EngramBody) -> str:
    parts: list[str] = ["## Procedure"]
    for step in sorted(body.procedure, key=lambda s: s.step_no):
        stat = f" [{step.ok_count}/{step.total_count}]" if step.total_count else ""
        parts.append(f"{step.step_no}.{stat} {step.text}".rstrip())

    parts.append("")
    parts.append("## Pitfalls")
    for p in body.pitfalls:
        prefix = f"(×{p.count}) " if p.count > 1 else ""
        parts.append(f"- {prefix}{p.text}")

    parts.append("")
    parts.append("## Examples")
    for ex in body.examples:
        sign = "+" if ex.positive else "-"
        parts.append(f"{sign} {ex.text}")

    parts.append("")
    parts.append("## Provenance")
    for line in body.provenance_lines:
        parts.append(f"- {line}")

    for block in body.exec_blocks:
        parts.append("")
        parts.append(f"```{block.language}")
        parts.append(block.text.rstrip("\n"))
        parts.append("```")

    lines = [line.rstrip() for line in parts]
    return "\n".join(lines).rstrip("\n") + "\n"


def render_document(engram: Engram, frontmatter_doc: Any | None = None) -> str:
    """Assemble the full ``---\\nfrontmatter\\n---\\nbody`` document.

    Exactly **one** newline separates the closing frontmatter fence from
    the body -- matching both docs/04's canonical File Anatomy example and
    ``engram/parser.py::split_frontmatter``'s fence regex (which consumes
    exactly one trailing newline). A second, "for readability" newline
    here would round-trip fine for humans but would make
    ``render_body(engram.body)`` (what gets hashed into ``body_sha256`` at
    write time) byte-*different* from what a later ``parser.parse_file()``
    of that same written file re-extracts as its body text -- silently
    breaking ``body_sha256``-based staleness detection for any file this
    module writes (as opposed to a hand-authored fixture, which already
    has the single-newline separator and never hit this).
    """
    fm_text = render_frontmatter(engram, frontmatter_doc)
    body_text = render_body(engram.body)
    return f"---\n{fm_text}\n---\n{body_text}"


def write_engram(path: str | Path, engram: Engram, frontmatter_doc: Any | None = None) -> None:
    """Render and atomically write a full engram document to ``path``."""
    atomic_write(path, render_document(engram, frontmatter_doc))
