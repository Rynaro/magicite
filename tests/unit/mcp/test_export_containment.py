"""F18: actual MCP export containment with outside entry/byte canaries."""

from __future__ import annotations

import json

import pytest

from magicite.core import registry
from magicite.mcp import app


def _snapshot(root):
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None for path in root.rglob("*")
    }


@pytest.mark.parametrize("escape", ["absolute", "parent", "symlink"])
def test_mcp_export_escape_has_typed_error_and_no_outside_writes(cfg, embedder, escape):
    outside = cfg.project_root.parent / (cfg.project_root.name + "-outside")
    outside.mkdir()
    (outside / "canary.bin").write_bytes(b"outside byte canary\x00\xff")
    if escape == "absolute":
        destination = str(outside)
    elif escape == "parent":
        destination = "../" + outside.name
    else:
        (cfg.project_root / "export-link").symlink_to(outside, target_is_directory=True)
        destination = "export-link/new-child"
    before = _snapshot(outside)
    state = app.build_state(cfg)
    try:
        result = app.dispatch_call(state, "export", {"out_dir": destination})
        assert result.is_error
        envelope = json.loads(result.content[0].text)
        assert envelope["code"] == "path_outside_project"
        assert _snapshot(outside) == before
    finally:
        state.conn.close()
        state.writer_conn.close()


def test_mcp_export_in_project_still_writes_skill(cfg, embedder):
    state = app.build_state(cfg)
    try:
        registry.register(cfg, state.writer_conn, embedder, path=".magicite/engrams")
        # Test fixture eligibility; export still renders the real imported artifact.
        state.writer_conn.execute("UPDATE engram SET status = 'consolidated'")
        state.writer_conn.commit()
        result = app.dispatch_call(state, "export", {"out_dir": "exports"})
        assert not result.is_error
        payload = json.loads(result.content[0].text)
        assert payload["exported"] > 0
        assert len(list((cfg.project_root / "exports").glob("*/SKILL.md"))) == payload["exported"]
    finally:
        state.conn.close()
        state.writer_conn.close()
