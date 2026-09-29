"""Atomic ``.egr.md`` writer (spec §2.5, §6.2 G2/G3).

Write path: ``tmp = <path>.tmp`` -> write -> ``fsync(file)`` ->
``os.replace(tmp, path)`` -> ``fsync(dir)``. Never partial, never in
place (AC-007).

Determinism (AC-021): floats render to 4 decimals, the ``synapses:`` list
sorts by ``(type, target)``, LF endings, no trailing whitespace — two
checkpoints of identical state produce byte-identical files.

G2 (lease assertion, spec §6.2) is real from M1 onward -- ``atomic_write()``
asserts ``storage.lease.assert_single_writer()``, so every caller
(``register()``/``sync()``/``export()`` in ``core/registry.py``, and Dream's
checkpoint phase) must hold ``storage.lease.writer_lease()`` around the
call.

**G3 (M4)**, per ``storage/lease.py``'s module docstring, is scoped
narrowly to :func:`write_plasticity`/:func:`write_synapses` -- **not** to
this generic ``atomic_write()`` primitive, because ``register()``/
``sharpen()`` legitimately write *authored* state (identity, routing, body)
through ``atomic_write()`` without ever being inside Dream's checkpoint
phase. Only the two functions that render the ``plasticity:``/``synapses:``
blocks assert :func:`~magicite.storage.lease.assert_dream_context`; Dream's
checkpoint phase (``core/dream.py``) calls them once each, right before the
real ``write_engram()``/``atomic_write()`` call, so the gate is exercised on
every learned-state write without constraining the write primitive itself.
"""

from __future__ import annotations

import copy
import io
import os
from pathlib import Path
from typing import Any, Literal

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import LiteralScalarString

from magicite.engram.model import Engram, EngramBody
from magicite.engram.model_v1 import EngramV1
from magicite.storage.lease import assert_dream_context, assert_single_writer

_yaml = YAML(typ="rt")
_yaml.preserve_quotes = True
_yaml.default_flow_style = False
_yaml.width = 100000

TargetFormat = Literal["engram/0.2", "engram/1.0"]


def write_plasticity(engram: Engram) -> CommentedMap:
    """G3: the **only** function that renders the ``plasticity:`` block for
    a checkpoint write. Raises :class:`~magicite.storage.lease.DreamContextError`
    unless called from inside ``core.dream.checkpoint_phase()`` (spec §6.2 G3).
    """
    assert_dream_context()
    if engram.frontmatter.plasticity is None:
        raise ValueError("engram has no plasticity block to checkpoint")
    return _render_plasticity(engram.frontmatter.plasticity)


def write_synapses(engram: Engram) -> CommentedSeq:
    """G3: the **only** function that renders the ``synapses:`` block for a
    checkpoint write. Same guard as :func:`write_plasticity`."""
    assert_dream_context()
    return _render_synapses(engram.frontmatter.synapses)


def atomic_write(path: str | Path, content: str) -> None:
    """Replace ``path`` with ``content`` atomically. Never leaves a partial file.

    G2 (spec §6.2): raises :class:`magicite.storage.lease.WriterLeaseError`
    unless the caller holds ``storage.lease.writer_lease()`` (AC-007's
    "only ever replaced atomically" is necessary but not sufficient --
    it must also only ever happen under the single-writer lease).
    """
    assert_single_writer()

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
    """DECISION (VIVI, spec-conformance remediation): keep 4-decimal
    rendering for every checkpointed float (S_node, S_edge, excitability,
    peak_storage_strength). This is a considered tradeoff, not an
    accident:

    - AC-021's determinism test only requires "render an engram twice ->
      byte-identical", not any particular precision -- widening would not
      violate the frozen AC, but this module's own docstring already
      names 4dp as *why* AC-021 holds (git-diff reviewability), and that
      reasoning is sound independent of AC-021.
    - The worst-case rounding error (<=5e-5) is 2-3 orders of magnitude
      below every threshold the engine actually compares S against:
      ``epsilon_write`` (0.05), ``theta_prune`` (0.10), ``floor_archived``
      (0.20), ``theta_synapse`` (0.35) (spec §4.3/§6.1, ``config.py``).
      No lifecycle/prune/materialisation decision can flip on this error.
    - It is bounded, not compounding: the DB row (``engram.
      storage_strength``/``edge.storage_strength``) always carries full
      float precision between checkpoints -- only the *file* is rounded,
      and only a full ``skill-graph.db`` rebuild (re-parsing the rounded
      file) ever actually consumes the rounded value as a new baseline.
    """
    return round(float(value), 4)


def _render_skill_md_source(source: Any) -> CommentedMap:
    node = CommentedMap()
    node["body_raw"] = LiteralScalarString(source.body_raw)
    node["projection_sha256"] = source.projection_sha256
    node["extra_frontmatter"] = copy.deepcopy(source.extra_frontmatter)
    return node


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

    # Root-level Tier A field, deliberately outside plasticity: (see
    # engram/model.py's EngramFrontmatter.peak_storage_strength docstring).
    doc["peak_storage_strength"] = _round4(fm.peak_storage_strength)

    doc["synapses"] = _render_synapses(fm.synapses)

    doc["needs"] = list(fm.needs)
    doc["yields"] = list(fm.yields)
    doc["composes"] = list(fm.composes)
    doc["inhibits"] = list(fm.inhibits)
    doc["affinity"] = list(fm.affinity)

    if fm.provenance_journal:
        doc["provenance_journal"] = _render_provenance_journal(fm.provenance_journal)

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
    if fm.skill_md_source is not None:
        doc["skill_md_source"] = _render_skill_md_source(fm.skill_md_source)

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


def _render_provenance_journal(journal: list[Any]) -> CommentedSeq:
    seq = CommentedSeq()
    for entry in journal:
        item: CommentedMap = CommentedMap()
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
        seq.append(item)
    return seq


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
        # Root-level Tier A field (see EngramFrontmatter.peak_storage_strength):
        # must be refreshed on the round-trip carrier explicitly, same as
        # version/plasticity/synapses above -- every other key on `doc` is
        # otherwise inherited verbatim from the original parse, so skipping
        # this line would silently drop every Dream-checkpointed peak value
        # (the exact M5-adjacent rebuild-loss this field exists to close).
        doc["peak_storage_strength"] = _round4(engram.frontmatter.peak_storage_strength)
        doc["synapses"] = _render_synapses(engram.frontmatter.synapses)
        # VIVI (v0.1.0-release conformance fix): provenance_journal is the
        # audit trail docs/06 sells as the mechanism for autonomous-mutation
        # governance -- it must be refreshed here for the exact same reason
        # peak_storage_strength/synapses are above. Before this line, every
        # other key on `doc` (including provenance_journal) was inherited
        # verbatim from the original parse, so every Dream-checkpoint- or
        # archive-appended journal entry (`event: consolidated`,
        # `event: archived`, ...) was computed onto
        # `engram.frontmatter.provenance_journal` in memory but silently
        # never reached the file -- a governance feature that no-ops is the
        # same failure class as a phantom config knob.
        if engram.frontmatter.provenance_journal:
            doc["provenance_journal"] = _render_provenance_journal(engram.frontmatter.provenance_journal)
        if engram.frontmatter.skill_md_source is not None:
            doc["skill_md_source"] = _render_skill_md_source(engram.frontmatter.skill_md_source)
        else:
            doc.pop("skill_md_source", None)
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
        fault = f" [fault: {step.fault_class}]" if step.fault_class else ""
        parts.append(f"{step.step_no}.{stat}{fault} {step.text}".rstrip())
    if body.procedure_raw:
        parts.extend(body.procedure_raw.splitlines())

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


def _dump_frontmatter_doc(doc: CommentedMap) -> str:
    buf = io.StringIO()
    _yaml.dump(doc, buf)
    text = buf.getvalue()
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).rstrip("\n")


def _render_version_constraint(constraint: Any) -> CommentedMap:
    node = CommentedMap()
    node["scheme"] = constraint.scheme
    node["range"] = constraint.range
    return node


def _fresh_doc_v1(engram: EngramV1) -> CommentedMap:
    """Build a ruamel document for an engram/1.0 frontmatter."""
    doc: CommentedMap = CommentedMap()
    fm = engram.frontmatter
    doc["spec"] = fm.spec
    doc["name"] = fm.name
    doc["id"] = fm.id
    doc["version"] = fm.version
    if fm.parents:
        doc["parents"] = list(fm.parents)

    intent = CommentedMap()
    intent["does"] = fm.intent.does
    intent["use_when"] = fm.intent.use_when
    if fm.intent.not_when is not None:
        intent["not_when"] = fm.intent.not_when
    doc["intent"] = intent

    if fm.compatibility is not None:
        compat = CommentedMap()
        if fm.compatibility.os:
            compat["os"] = list(fm.compatibility.os)
        for dim in ("languages", "frameworks", "package_managers"):
            mapping = getattr(fm.compatibility, dim)
            if mapping:
                block = CommentedMap()
                for key, constraint in sorted(mapping.items()):
                    block[key] = _render_version_constraint(constraint)
                compat[dim] = block
        if fm.compatibility.hosts:
            hosts = CommentedSeq()
            for host in fm.compatibility.hosts:
                item = CommentedMap()
                item["id"] = host.id
                if host.version is not None:
                    item["version"] = _render_version_constraint(host.version)
                hosts.append(item)
            compat["hosts"] = hosts
        doc["compatibility"] = compat

    if fm.capabilities is not None:
        caps = CommentedMap()
        reqs = CommentedSeq()
        for req in fm.capabilities.requires:
            item = CommentedMap()
            item["kind"] = req.kind
            item["id"] = req.id
            if req.version is not None:
                item["version"] = _render_version_constraint(req.version)
            reqs.append(item)
        caps["requires"] = reqs
        prods = CommentedSeq()
        for prod in fm.capabilities.produces:
            item = CommentedMap()
            item["kind"] = prod.kind
            item["id"] = prod.id
            if prod.version is not None:
                item["version"] = prod.version
            prods.append(item)
        caps["produces"] = prods
        caps["alternatives"] = list(fm.capabilities.alternatives)
        caps["conflicts_with"] = list(fm.capabilities.conflicts_with)
        doc["capabilities"] = caps

    if fm.relations is not None:
        rel = CommentedMap()
        for field_name in ("requires", "before", "supersedes"):
            seq = CommentedSeq()
            for ref in getattr(fm.relations, field_name):
                item = CommentedMap()
                item["id"] = ref.id
                item["version"] = ref.version
                seq.append(item)
            rel[field_name] = seq
        doc["relations"] = rel

    if fm.risk is not None:
        risk = CommentedMap()
        risk["filesystem"] = fm.risk.filesystem
        sub = CommentedMap()
        sub["mode"] = fm.risk.subprocess.mode
        sub["tools"] = list(fm.risk.subprocess.tools)
        risk["subprocess"] = sub
        net = CommentedMap()
        net["mode"] = fm.risk.network.mode
        net["destinations"] = list(fm.risk.network.destinations)
        risk["network"] = net
        risk["secrets"] = fm.risk.secrets
        doc["risk"] = risk

    routing = CommentedMap()
    routing["positive"] = list(fm.routing.positive)
    routing["negative"] = list(fm.routing.negative)
    routing["body_digest"] = fm.routing.body_digest
    doc["routing"] = routing

    origin = CommentedMap()
    origin["channel"] = fm.origin.channel
    origin["verification_status"] = fm.origin.verification_status
    if fm.origin.signer is not None:
        origin["signer"] = fm.origin.signer
    if fm.origin.import_source is not None:
        origin["import_source"] = fm.origin.import_source
    if fm.origin.content_hashes:
        origin["content_hashes"] = CommentedMap(dict(sorted(fm.origin.content_hashes.items())))
    if fm.origin.journal:
        origin["journal"] = _render_provenance_journal(fm.origin.journal)
    doc["origin"] = origin

    if fm.assets:
        assets = CommentedMap()
        for path_key in sorted(fm.assets):
            desc = fm.assets[path_key]
            item = CommentedMap()
            item["sha256"] = desc.sha256
            item["size"] = desc.size
            if desc.media_type is not None:
                item["media_type"] = desc.media_type
            assets[path_key] = item
        doc["assets"] = assets

    if fm.extensions:
        extensions = CommentedMap()
        for key in sorted(fm.extensions):
            ext = fm.extensions[key]
            item = CommentedMap(ext.model_dump(mode="json"))
            extensions[key] = item
        doc["extensions"] = extensions

    if fm.skill_md_source is not None:
        doc["skill_md_source"] = _render_skill_md_source(fm.skill_md_source)

    if fm.legacy:
        doc["legacy"] = CommentedMap(copy.deepcopy(fm.legacy))

    return doc


def render_frontmatter_v1(engram: EngramV1, frontmatter_doc: Any | None = None) -> str:
    """Render engram/1.0 YAML frontmatter (without ``---`` fences)."""
    doc = frontmatter_doc if frontmatter_doc is not None else _fresh_doc_v1(engram)
    if frontmatter_doc is not None:
        # Refresh identity/routing digests that must track in-memory state.
        doc["version"] = engram.frontmatter.version
        if "routing" in doc:
            doc["routing"]["body_digest"] = engram.frontmatter.routing.body_digest
        if engram.frontmatter.skill_md_source is not None:
            doc["skill_md_source"] = _render_skill_md_source(engram.frontmatter.skill_md_source)
        else:
            doc.pop("skill_md_source", None)
    return _dump_frontmatter_doc(doc)


def render_document_v1(engram: EngramV1, frontmatter_doc: Any | None = None) -> str:
    """Assemble a full engram/1.0 ``.egr.md`` document."""
    fm_text = render_frontmatter_v1(engram, frontmatter_doc)
    body_text = render_body(engram.body)
    return f"---\n{fm_text}\n---\n{body_text}"


def render_as(
    artifact: Engram | EngramV1,
    *,
    target_format: TargetFormat,
    frontmatter_doc: Any | None = None,
    revision_map: dict[str, int] | None = None,
) -> str:
    """Explicit target-format rendering (C8). Never auto-emits 1.0 to a 0.2 consumer.

    - ``engram/0.2`` artifact → ``engram/0.2``: native render
    - ``engram/0.2`` artifact → ``engram/1.0``: pure transform then render
    - ``engram/1.0`` artifact → ``engram/1.0``: native render
    - ``engram/1.0`` artifact → ``engram/0.2``: refused (downgrade is S03)
    """
    if target_format == "engram/0.2":
        if isinstance(artifact, EngramV1):
            raise ValueError(
                "refusing to emit engram/0.2 from an engram/1.0 artifact; "
                "downgrade/migration authority belongs to S03"
            )
        return render_document(artifact, frontmatter_doc)

    if target_format == "engram/1.0":
        if isinstance(artifact, EngramV1):
            return render_document_v1(artifact, frontmatter_doc)
        from magicite.engram.transform import transform_0_2_to_1_0

        transformed = transform_0_2_to_1_0(artifact, revision_map=revision_map).engram
        return render_document_v1(transformed)

    raise ValueError(f"unsupported target_format: {target_format!r}")


def write_engram_as(
    path: str | Path,
    artifact: Engram | EngramV1,
    *,
    target_format: TargetFormat,
    frontmatter_doc: Any | None = None,
    revision_map: dict[str, int] | None = None,
) -> None:
    """Atomically write ``artifact`` rendered at ``target_format``."""
    atomic_write(
        path,
        render_as(
            artifact,
            target_format=target_format,
            frontmatter_doc=frontmatter_doc,
            revision_map=revision_map,
        ),
    )
