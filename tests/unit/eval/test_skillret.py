"""Lossless corpus codec boundaries, without models or upstream execution."""

import hashlib
import json

import pytest

from magicite.eval import skillret

UUID = "12345678-1234-1234-1234-123456789abc"


def test_normalized_uuid_alias_collision():
    with pytest.raises(ValueError, match="normalized"):
        skillret.identity_map({UUID, UUID.upper()})


def test_exact_body_and_static_wrapper(tmp_path):
    text = "---\nname: Original Name\ndescription: é\n---\n[asset](scripts/helper.py)\n\n"
    skill = {
        "id": UUID,
        "name": "Original Name",
        "description": "description",
        "body": text,
        "skill_md": text,
    }
    mapped = skillret.identity_map({UUID})[UUID]
    assert skillret.source_body(skill) == text.encode()
    assert skillret.native_wrapper(skill, mapped, "source.SKILL.md").endswith(text.encode())
    status = skillret.source_status(skill)
    assert status["strict_import_status"] == "REJECTED"
    assert status["relative_references"] == ["scripts/helper.py"]
    assert status["referenced_assets"] == "UNAVAILABLE_NOT_FETCHED"


@pytest.mark.parametrize(
    "k,targets,names",
    [(True, [UUID], ["name"]), (1, [UUID, UUID], ["name", "name"]), (2, [UUID], ["name"]), (1, [UUID], [])],
)
def test_conflicting_target_declarations(k, targets, names):
    with pytest.raises(ValueError, match="target"):
        skillret.verify_targets(
            {"k": k, "skill_ids": targets, "skill_names": names}, {UUID: 1}, {UUID: {"name": "name"}}
        )


def test_raw_hash_and_containment(tmp_path):
    path = tmp_path / "raw.jsonl"
    path.write_bytes(b"{}\n")
    lock = {
        "artifacts": [
            {
                "path": path.name,
                "size_bytes": 3,
                "expected_identity": {
                    "algorithm": "sha256",
                    "value": hashlib.sha256(path.read_bytes()).hexdigest(),
                },
            }
        ]
    }
    assert skillret.verify_raw(tmp_path, lock)[0]["bytes"] == 3
    path.write_bytes(b"[]\n")
    with pytest.raises(ValueError, match="mismatch"):
        skillret.verify_raw(tmp_path, lock)
    with pytest.raises(ValueError, match="unsafe"):
        skillret.contained(tmp_path, "../outside")


def fixture_raw(root):
    skill = {
        "id": UUID,
        "name": "name",
        "description": "description",
        "skill_md": "---\nname: name\ndescription: description\n---\nbody é\n\n",
        "license": "MIT",
    }
    skill["body"] = skill["skill_md"]
    data = {"data/skills.jsonl": [skill]}
    for split in ("train", "test"):
        data[f"data/skills/{split}.jsonl"] = [skill]
        data[f"data/queries/{split}.jsonl"] = [
            {
                "id": "q",
                "original_id": "shared",
                "query": "query",
                "skill_ids": [UUID],
                "skill_names": ["name"],
                "k": 1,
            }
        ]
        data[f"data/qrels/{split}.jsonl"] = [{"query_id": "q", "skill_id": UUID, "relevance": 1}]
    artifacts = []
    for name, rows in data.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"".join(skillret.canonical(row) for row in rows))
        artifacts.append(
            {
                "path": name,
                "size_bytes": path.stat().st_size,
                "expected_identity": {"algorithm": "sha256", "value": skillret.digest(path)},
            }
        )
    return {"revision": "test-only", "artifacts": artifacts}, skill


def test_complete_deterministic_codec_and_split_isolation(tmp_path):
    lock, skill = fixture_raw(tmp_path / "raw")
    counts = {"master": 1, "train": (1, 1, 1), "test": (1, 1, 1)}
    first = skillret.convert(tmp_path / "raw", tmp_path / "first", lock, expected_counts=counts)
    second = skillret.convert(tmp_path / "raw", tmp_path / "second", lock, expected_counts=counts)
    assert first == second
    accounting = json.loads((tmp_path / "first/accounting.json").read_text())
    assert accounting["skill_split_overlap"] == [UUID]
    for split in ("train", "test"):
        runtime = json.loads((tmp_path / f"first/{split}/runtime-queries.json").read_text())
        assert set(runtime[0]) == {"query_id", "query_text", "compatibility_context"}
        assert runtime[0]["query_id"] == split + ":q"
    inventory = json.loads((tmp_path / "first/master/inventory.json").read_text())
    assert (tmp_path / "first" / inventory[0]["source_body"]).read_bytes() == skill["skill_md"].encode()
    assert first["status"] == "READY_FOR_EVALUATION_INPUT"


@pytest.mark.parametrize("front", ["- scalar", "scalar", "42"])
def test_nonmapping_original_frontmatter_is_preserved(front):
    text = "---\n" + front + "\n---\nbody\n"
    skill = {"skill_md": text}
    assert skillret.source_status(skill)["strict_import_status"] == "REJECTED"
    assert skillret.source_body(skill) == text.encode()


@pytest.mark.parametrize("fault", ["alias", "duplicate", "dangling", "missing", "targets"])
def test_conflicting_source_never_emits_ready_seal(tmp_path, fault):
    lock, _skill = fixture_raw(tmp_path / "raw")
    path = tmp_path / "raw/data/skills.jsonl"
    if fault == "alias":
        row = json.loads(path.read_text())
        row["body"] += "changed"
        path.write_bytes(skillret.canonical(row))
    elif fault == "duplicate":
        path.write_bytes(path.read_bytes() * 2)
    elif fault == "missing":
        path.write_bytes(b"")
    elif fault == "dangling":
        path = tmp_path / "raw/data/qrels/test.jsonl"
        row = json.loads(path.read_text())
        row["skill_id"] = "ffffffff-ffff-ffff-ffff-ffffffffffff"
        path.write_bytes(skillret.canonical(row))
    else:
        path = tmp_path / "raw/data/queries/test.jsonl"
        row = json.loads(path.read_text())
        row["skill_ids"] *= 2
        row["skill_names"] *= 2
        path.write_bytes(skillret.canonical(row))
    for row in lock["artifacts"]:
        if row["path"] == str(path.relative_to(tmp_path / "raw")):
            row["size_bytes"] = path.stat().st_size
            row["expected_identity"]["value"] = skillret.digest(path)
    with pytest.raises(ValueError):
        skillret.convert(
            tmp_path / "raw",
            tmp_path / "output",
            lock,
            expected_counts={"master": 1, "train": (1, 1, 1), "test": (1, 1, 1)},
        )
    assert not (tmp_path / "output/seal.json").exists()


@pytest.mark.parametrize("control", ["\x80", "\x9f", "\x85", "\u2028", "\u2029"])
def test_generated_header_escapes_yaml_forbidden_source_controls(tmp_path, control):
    skill = {
        "id": UUID,
        "name": "source" + control + "😀name",
        "description": "source" + control + "😀description",
        "skill_md": "---\nname: source\ndescription: source\n---\nbody\x80\n",
    }
    mapped = skillret.identity_map({UUID})[UUID]
    path = tmp_path / "artifact.egr.md"
    path.write_bytes(skillret.native_wrapper(skill, mapped, "source.SKILL.md"))
    from magicite.engram import parser

    artifact, _ = parser.load_artifact_file(path, registry_root=tmp_path)
    assert artifact.frontmatter.intent.does == skill["description"]
    assert artifact.frontmatter.triggers.positive == [skill["name"]]
    assert path.read_bytes().endswith(skill["skill_md"].encode())
