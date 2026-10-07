from __future__ import annotations

import hashlib

import pytest

from magicite.engram import parser


def test_round_trip_sample_engram(toy_registry_dir) -> None:
    path = toy_registry_dir / "engrams" / "proton-ge-proton-downgrade.egr.md"
    parsed = parser.parse_file(path, registry_root=toy_registry_dir)
    engram = parsed.engram

    assert engram.name == "proton-ge-proton-downgrade"
    assert engram.id == "egr_b5320dfd"
    assert len(engram.frontmatter.triggers.positive) >= 3
    assert len(engram.frontmatter.triggers.negative) >= 1
    assert engram.frontmatter.intent.not_when
    assert [s.step_no for s in engram.body.procedure] == [1, 2, 3, 4]
    assert engram.body.procedure_raw == ""
    assert engram.routable is True


def test_missing_frontmatter_fence_raises() -> None:
    with pytest.raises(parser.EngramParseError):
        parser.parse_text("no frontmatter here", relpath="x.egr.md")


def test_malformed_yaml_raises() -> None:
    text = "---\nname: [unterminated\n---\nbody\n"
    with pytest.raises(parser.EngramParseError):
        parser.parse_text(text, relpath="x.egr.md")


def test_exec_blocks_captured_as_inert_text() -> None:
    text = (
        "---\n"
        "spec: engram/0.2\n"
        "name: sample-exec\n"
        "id: egr_00000001\n"
        "version: 1\n"
        "provenance: authored\n"
        "intent:\n"
        "  does: does something\n"
        "  use_when: when needed\n"
        "  not_when: never\n"
        "triggers:\n"
        "  positive: [a, b, c]\n"
        "  negative: [d]\n"
        "---\n"
        "## Procedure\n"
        "1. Do the thing.\n"
        "\n"
        "```bash\n"
        "rm -rf /\n"
        "```\n"
    )
    parsed = parser.parse_text(text, relpath="x.egr.md")
    assert len(parsed.engram.body.exec_blocks) == 1
    assert parsed.engram.body.exec_blocks[0].language == "bash"
    assert "rm -rf /" in parsed.engram.body.exec_blocks[0].text


def test_procedure_fault_class_is_parsed_from_canonical_marker() -> None:
    body = parser.parse_body("## Procedure\n1. [fault: OOMKilled] Reduce the batch.\n")
    assert body.procedure[0].fault_class == "OOMKilled"
    assert body.procedure[0].text == "Reduce the batch."


@pytest.mark.parametrize("ending", ["lf", "crlf", "mixed"])
@pytest.mark.parametrize("api", ["parse_file", "parse_artifact_file", "load_artifact_file"])
def test_file_readers_hash_exact_utf8_bytes(toy_registry_dir, tmp_path, ending, api):
    raw = (toy_registry_dir / "engrams" / "proton-ge-proton-downgrade.egr.md").read_bytes()
    if ending == "crlf":
        raw = raw.replace(b"\n", b"\r\n")
    elif ending == "mixed":
        yaml, body = parser.split_frontmatter(raw.decode())
        lines = body.split("\n")
        body = lines[0] + "\r\n" + lines[1] + "\r" + "\n".join(lines[2:])
        raw = ("---\n" + yaml + "\n---\n" + body).encode()
    path = tmp_path / "exact.egr.md"
    path.write_bytes(raw)
    result = getattr(parser, api)(path, registry_root=tmp_path)
    artifact = result.engram if api == "parse_file" else result[0]
    body = parser.split_frontmatter(raw.decode())[1]
    assert artifact.content_sha256 == hashlib.sha256(raw).hexdigest()
    assert artifact.body_sha256 == hashlib.sha256(body.encode()).hexdigest()
    assert path.read_bytes() == raw


@pytest.mark.parametrize("api", ["parse_file", "parse_artifact_file", "load_artifact_file"])
def test_file_readers_reject_invalid_utf8_without_rewrite(tmp_path, api):
    path = tmp_path / "invalid.egr.md"
    raw = b"---\ninvalid: \xff\n---\nbody\n"
    path.write_bytes(raw)
    with pytest.raises(UnicodeDecodeError):
        getattr(parser, api)(path, registry_root=tmp_path)
    assert path.read_bytes() == raw


@pytest.mark.parametrize("api", ["parse_file", "parse_artifact_file", "load_artifact_file"])
def test_file_readers_do_not_expand_lone_cr_frontmatter(toy_registry_dir, tmp_path, api):
    raw = (toy_registry_dir / "engrams" / "proton-ge-proton-downgrade.egr.md").read_bytes()
    raw = raw.replace(b"\n", b"\r")
    path = tmp_path / "lone-cr.egr.md"
    path.write_bytes(raw)
    with pytest.raises(parser.EngramParseError, match="frontmatter fence"):
        getattr(parser, api)(path, registry_root=tmp_path)
    assert path.read_bytes() == raw
