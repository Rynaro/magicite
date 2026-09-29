"""``.egr.md`` reader: split frontmatter/body, parse both (spec §2.5).

Frontmatter is parsed with ``ruamel.yaml`` round-trip (comments and key
order preserved) so ``engram/writer.py`` can re-render byte-deterministic
files later without clobbering author comments. Body sections are matched
by heading; fenced code blocks anywhere in the body are captured as
**inert text** and never evaluated (spec §2.5, docs/04 exec-block rule).

Engram 1.0 dual reader (C8 / S02): :func:`parse_artifact` accepts
``engram/0.2`` and ``engram/1.0``. Legacy :func:`parse_text` /
:func:`parse_file` remain 0.2-oriented for existing call sites and raise
when handed a 1.0 document (use :func:`parse_artifact` instead).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from magicite.engram import ids
from magicite.engram.model import (
    Engram,
    EngramBody,
    EngramFrontmatter,
    ExampleEntry,
    ExecBlock,
    PitfallEntry,
    ProcedureStep,
)
from magicite.engram.model_v1 import EngramFrontmatterV1, EngramV1

_FENCE = "---"
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(?P<yaml>.*?)\r?\n---\r?\n?", re.DOTALL)
_EXEC_BLOCK_RE = re.compile(r"```([A-Za-z0-9_+-]*)\r?\n(.*?)```", re.DOTALL)
_STEP_RE = re.compile(
    r"^(?P<num>\d+)\.\s*(?:\[(?:(?P<label>[a-zA-Z]+):\s*)?(?P<ok>\d+)/(?P<total>\d+)\]\s*)?"
    r"(?:\[fault:\s*(?P<fault>[A-Za-z0-9_.:-]+)\]\s*)?(?P<text>.*)$"
)
_PITFALL_RE = re.compile(r"^-\s*(?:\(×(?P<count>\d+)\)\s*)?(?P<text>.*)$")
_EXAMPLE_RE = re.compile(r"^(?P<sign>[+-])\s*(?P<text>.*)$")
_PROVENANCE_RE = re.compile(r"^-\s*(?P<text>.*)$")

_yaml = YAML(typ="rt")
_yaml.preserve_quotes = True
_yaml.width = 100000  # avoid ruamel's default line-wrapping on long strings


class EngramParseError(ValueError):
    """Malformed ``.egr.md`` (bad frontmatter fence, unparsable YAML, ...)."""


@dataclass
class ParsedFile:
    engram: Engram
    frontmatter_doc: Any  # ruamel CommentedMap, round-trip carrier for the writer


def split_frontmatter(raw_text: str) -> tuple[str, str]:
    """Return ``(yaml_text, body_text)``, split on the first ``---`` fence pair."""
    match = _FRONTMATTER_RE.match(raw_text)
    if not match:
        raise EngramParseError("missing or malformed frontmatter fence (expected '---' pair)")
    yaml_text = match.group("yaml")
    body_text = raw_text[match.end() :]
    return yaml_text, body_text


def load_frontmatter_doc(yaml_text: str) -> Any:
    try:
        doc = _yaml.load(yaml_text)
    except Exception as exc:  # ruamel raises its own YAMLError subclasses
        raise EngramParseError(f"invalid YAML frontmatter: {exc}") from exc
    if doc is None:
        raise EngramParseError("empty frontmatter")
    return doc


def parse_body(body_text: str) -> EngramBody:
    exec_blocks = [
        ExecBlock(language=lang or "text", text=text) for lang, text in _EXEC_BLOCK_RE.findall(body_text)
    ]
    # Strip exec blocks before section splitting so fenced code doesn't get
    # mistaken for section prose; the blocks themselves are preserved above.
    stripped = _EXEC_BLOCK_RE.sub("", body_text)

    sections = _split_sections(stripped)

    procedure_lines = sections.get("procedure", [])
    steps: list[ProcedureStep] = []
    unmatched: list[str] = []
    for line in procedure_lines:
        m = _STEP_RE.match(line.strip())
        if m:
            ok = int(m.group("ok")) if m.group("ok") else 0
            total = int(m.group("total")) if m.group("total") else 0
            steps.append(
                ProcedureStep(
                    step_no=int(m.group("num")),
                    text=m.group("text").strip(),
                    ok_count=ok,
                    total_count=total,
                    fault_class=m.group("fault"),
                )
            )
        elif line.strip():
            unmatched.append(line)

    pitfalls: list[PitfallEntry] = []
    for line in sections.get("pitfalls", []):
        m = _PITFALL_RE.match(line.strip())
        if m and m.group("text"):
            count = int(m.group("count")) if m.group("count") else 1
            pitfalls.append(PitfallEntry(text=m.group("text").strip(), count=count))

    examples: list[ExampleEntry] = []
    for line in sections.get("examples", []):
        m = _EXAMPLE_RE.match(line.strip())
        if m and m.group("text"):
            examples.append(ExampleEntry(positive=m.group("sign") == "+", text=m.group("text").strip()))

    provenance_lines: list[str] = []
    for line in sections.get("provenance", []):
        m = _PROVENANCE_RE.match(line.strip())
        if m and m.group("text"):
            provenance_lines.append(m.group("text").strip())

    return EngramBody(
        procedure=steps,
        procedure_raw="\n".join(unmatched).strip(),
        pitfalls=pitfalls,
        examples=examples,
        provenance_lines=provenance_lines,
        exec_blocks=exec_blocks,
    )


_SECTION_HEADING_RE = re.compile(r"^##\s+(?P<title>\w+)\s*$")
_KNOWN_SECTIONS = {"procedure", "pitfalls", "examples", "provenance"}


def _split_sections(body_text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in body_text.splitlines():
        m = _SECTION_HEADING_RE.match(line.strip())
        if m and m.group("title").lower() in _KNOWN_SECTIONS:
            current = m.group("title").lower()
            sections.setdefault(current, [])
            continue
        if current is not None and (line.strip() or sections[current]):
            sections[current].append(line)
    # drop trailing blank lines per section
    for lines in sections.values():
        while lines and not lines[-1].strip():
            lines.pop()
    return sections


def parse_artifact(raw_text: str, *, relpath: str, file_mtime_ns: int = 0) -> tuple[Engram | EngramV1, Any]:
    """Pure dual reader for ``engram/0.2`` and ``engram/1.0`` (C8).

    Returns ``(artifact, frontmatter_doc)`` where ``frontmatter_doc`` is the
    ruamel round-trip carrier for writers.
    """
    yaml_text, body_text = split_frontmatter(raw_text)
    doc = load_frontmatter_doc(yaml_text)
    as_dict = dict(doc)
    spec = as_dict.get("spec", "engram/0.2")
    body = parse_body(body_text)
    content_sha = ids.content_sha256(raw_text.encode("utf-8"))
    body_sha = ids.body_sha256(body_text)

    if spec == "engram/1.0":
        frontmatter = EngramFrontmatterV1.model_validate(as_dict)
        artifact: Engram | EngramV1 = EngramV1(
            frontmatter=frontmatter,
            body=body,
            path=relpath,
            content_sha256=content_sha,
            body_sha256=body_sha,
            file_mtime_ns=file_mtime_ns,
        )
        return artifact, doc

    if spec != "engram/0.2":
        raise EngramParseError(f"unsupported engram spec version: {spec!r}")

    frontmatter_v02 = EngramFrontmatter.model_validate(as_dict)
    artifact = Engram(
        frontmatter=frontmatter_v02,
        body=body,
        path=relpath,
        content_sha256=content_sha,
        body_sha256=body_sha,
        file_mtime_ns=file_mtime_ns,
    )
    return artifact, doc


def parse_text(raw_text: str, *, relpath: str, file_mtime_ns: int = 0) -> ParsedFile:
    """Parse ``.egr.md`` text as engram/0.2 for existing call sites.

    For dual-format intake use :func:`parse_artifact`.
    """
    artifact, doc = parse_artifact(raw_text, relpath=relpath, file_mtime_ns=file_mtime_ns)
    if not isinstance(artifact, Engram):
        raise EngramParseError("engram/1.0 artifact requires parse_artifact(); parse_text() is 0.2-only")
    return ParsedFile(engram=artifact, frontmatter_doc=doc)


def parse_file(path: Path, *, registry_root: Path) -> ParsedFile:
    raw_text = path.read_text(encoding="utf-8")
    relpath = str(path.resolve().relative_to(registry_root.resolve()))
    mtime_ns = path.stat().st_mtime_ns
    return parse_text(raw_text, relpath=relpath, file_mtime_ns=mtime_ns)


def parse_artifact_file(path: Path, *, registry_root: Path) -> tuple[Engram | EngramV1, Any]:
    """Dual-reader file entrypoint (0.2 or 1.0)."""
    raw_text = path.read_text(encoding="utf-8")
    relpath = str(path.resolve().relative_to(registry_root.resolve()))
    mtime_ns = path.stat().st_mtime_ns
    return parse_artifact(raw_text, relpath=relpath, file_mtime_ns=mtime_ns)
