"""``storage/durable.py``'s declared-edge write path (spec §2.6 step 4,
§3.3.1). AC-009's rebuild-invariant coverage lives in
tests/acceptance/test_rebuild_invariant.py; AC-036 is this module's own
unit coverage of the authored channel's persisted-column contract."""

from __future__ import annotations

import pytest

from magicite.core import registry as registry_mod
from magicite.engram import lint, parser
from magicite.storage import durable, lease, queries


def test_authored_weight_is_never_persisted(cfg, db_conn, embedder) -> None:
    """AC-036: GIVEN a declared edge that no Dream run has ever
    potentiated THEN its persisted edge.storage_strength SHALL still be
    exactly 0.0 -- the guard that keeps §3.3.1's authored channel
    computed-at-read, never stored, so AC-009/AC-010's rebuild invariant
    is unmoved. ``wire_declared_edges`` (this module) always writes the
    literal 0.0; nothing in v1 can raise it except Dream potentiating a
    ``co_activation`` edge, which a declared depends_on/composes/inhibits
    edge is never tagged as (decisions/DECLARED-EDGES-AMENDED.md §4.1)."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")

    rows = db_conn.execute("SELECT storage_strength FROM edge WHERE provenance = 'declared'").fetchall()
    assert rows, "expected at least one declared edge in the toy registry fixture"
    assert all(r["storage_strength"] == 0.0 for r in rows)


@pytest.mark.parametrize("numbers", [[1, 2, 3], [2, 5, 9], [1, 2, 1, 2]])
def test_procedure_storage_keys_preserve_unique_labels(cfg, db_conn, numbers) -> None:
    source = next(cfg.registry_dir.glob("*.egr.md"))
    artifact, _ = parser.parse_artifact_file(source, registry_root=source.parent)
    artifact.body = parser.parse_body(
        "## Procedure\n"
        + "\n".join(
            f"{number}. [1/3] [fault: timeout] occurrence {ordinal}"
            for ordinal, number in enumerate(numbers, 1)
        )
    )
    original = artifact.model_dump()
    with lease.writer_lease():
        durable.upsert_engram(db_conn, artifact, identity_sha256="test-identity")
    rows = db_conn.execute(
        "SELECT step_no,text,ok_count,total_count,fault_class FROM engram_step ORDER BY step_no"
    ).fetchall()
    expected_keys = numbers if len(set(numbers)) == len(numbers) else list(range(1, len(numbers) + 1))
    assert [row["step_no"] for row in rows] == expected_keys
    assert [(row["text"], row["ok_count"], row["total_count"], row["fault_class"]) for row in rows] == [
        (step.text, step.ok_count, step.total_count, step.fault_class) for step in artifact.body.procedure
    ]
    assert artifact.model_dump() == original


def test_restarted_procedure_labels_rebuild_losslessly(cfg, db_conn) -> None:
    # Same 42-occurrence/restarted-list shape as the observed official record,
    # without committing upstream skill text or changing its visible labels.
    numbers = [number for length in (10, 5, 3, 3, 5, 10, 3, 3) for number in range(1, length + 1)]
    source = next(cfg.registry_dir.glob("*.egr.md"))
    yaml_text, _ = parser.split_frontmatter(source.read_text())
    raw = (
        "---\n"
        + yaml_text
        + "\n---\n## Procedure\n"
        + "\n".join(
            f"{number}. [{ordinal % 3}/{ordinal + 3}] [fault: fault-{ordinal}] occurrence {ordinal}"
            for ordinal, number in enumerate(numbers, 1)
        )
        + "\n"
    )
    source.write_text(raw)
    original_bytes = source.read_bytes()
    artifact, _ = parser.parse_artifact_file(source, registry_root=source.parent)
    original = artifact.model_dump()
    original_lint = lint.lint_import(artifact)
    assert len(artifact.body.procedure) == 42
    projections = []
    with lease.writer_lease():
        for attempt in range(3):
            if attempt == 2:
                db_conn.execute("DELETE FROM engram WHERE id=?", (artifact.id,))
            durable.upsert_engram(db_conn, artifact, identity_sha256="test-identity")
            projections.append(queries.durable_projection(db_conn))
    assert projections[0] == projections[1] == projections[2]
    assert len(projections[0]["steps"]) == 42
    rows = projections[0]["steps"]
    assert [row["step_no"] for row in rows] == list(range(1, 43))
    assert [(row["text"], row["ok_count"], row["total_count"], row["fault_class"]) for row in rows] == [
        (step.text, step.ok_count, step.total_count, step.fault_class) for step in artifact.body.procedure
    ]
    assert artifact.model_dump() == original
    assert [step.step_no for step in artifact.body.procedure] == numbers
    assert lint.lint_import(artifact) == original_lint
    assert source.read_bytes() == original_bytes
