"""Harness activation acceptance anchors derived from user requirements."""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest
from click.testing import CliRunner

from magicite.__main__ import cli
from magicite.harnesses import BLOCK, INSTRUCTIONS, manage

pytestmark = pytest.mark.acceptance


def invoke(root, *args):
    return CliRunner().invoke(cli, [*args, "--project-root", str(root)])


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_fresh_all_and_idempotent_with_spaces(tmp_path):
    root = tmp_path / "project with spaces"
    root.mkdir()
    result = invoke(root, "setup")
    assert result.exit_code == 0, result.output
    claude = json.loads((root / ".mcp.json").read_text())["mcpServers"]["magicite"]
    codex = tomllib.loads((root / ".codex/config.toml").read_text())["mcp_servers"]["magicite"]
    opencode = json.loads((root / "opencode.json").read_text())
    assert claude == codex
    assert claude["command"] == str(Path(sys.executable).absolute())
    assert claude["args"] == ["-m", "magicite", "serve", "--project-root", str(root)]
    assert opencode["mcp"]["magicite"]["command"] == [claude["command"], *claude["args"]]
    assert opencode["instructions"] == [".magicite/instructions.md"]
    assert (root / "AGENTS.md").read_text() == BLOCK
    assert (root / ".claude/rules/magicite.md").read_text() == INSTRUCTIONS
    before = snapshot(root)
    assert invoke(root, "setup").exit_code == 0
    assert snapshot(root) == before
    status = manage(str(root), "all", "status")
    assert all(s["state"] == "enabled" and s["ready"] for s in status["harnesses"].values())
    assert status["live_verified"] is False


def test_durable_disable_and_selective_enable(tmp_path):
    assert invoke(tmp_path, "setup").exit_code == 0
    assert invoke(tmp_path, "harness", "disable", "--harness", "codex").exit_code == 0
    before = snapshot(tmp_path)
    assert invoke(tmp_path, "setup").exit_code == 0
    assert snapshot(tmp_path) == before
    statuses = manage(str(tmp_path), "all", "status")["harnesses"]
    assert statuses["codex"]["state"] == "disabled"
    assert statuses["claude"]["state"] == "enabled"
    assert invoke(tmp_path, "harness", "enable", "--harness", "codex").exit_code == 0
    assert manage(str(tmp_path), "codex", "status")["harnesses"]["codex"]["state"] == "enabled"
    assert invoke(tmp_path, "harness", "disable").exit_code == 0
    assert "magicite" not in json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]
    assert json.loads((tmp_path / "opencode.json").read_text())["instructions"] == []
    assert not (tmp_path / ".magicite/instructions.md").exists()


def test_preserve_content_and_override(tmp_path):
    (tmp_path / ".codex").mkdir()
    original = '# user config\nmodel = "mine"\n[mcp_servers.other]\ncommand = "other"\n'
    (tmp_path / ".codex/config.toml").write_text(original)
    (tmp_path / "AGENTS.md").write_text("Base instructions\n")
    (tmp_path / "AGENTS.override.md").write_text("Override instructions\n")
    (tmp_path / "opencode.json").write_text(json.dumps({"instructions": ["custom.md"], "theme": "mine"}))
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "other"}}}))
    assert invoke(tmp_path, "setup").exit_code == 0
    assert (tmp_path / "AGENTS.md").read_text() == "Base instructions\n"
    assert BLOCK in (tmp_path / "AGENTS.override.md").read_text()
    assert invoke(tmp_path, "harness", "disable").exit_code == 0
    assert (tmp_path / ".codex/config.toml").read_text() == original
    assert (tmp_path / "AGENTS.override.md").read_text() == "Override instructions\n"
    assert json.loads((tmp_path / "opencode.json").read_text()) == {
        "instructions": ["custom.md"],
        "theme": "mine",
        "mcp": {},
    }
    assert json.loads((tmp_path / ".mcp.json").read_text()) == {"mcpServers": {"other": {"command": "other"}}}


@pytest.mark.parametrize(
    "path,content",
    [
        ("opencode.jsonc", "{// comment\n}"),
        ("opencode.json", "{// comment\n}"),
        ("opencode.json", '{"mcp":{"magicite":{"type":"local","command":["foreign"]}}}'),
        (".codex/config.toml", "bad = ["),
        ("AGENTS.md", "<!-- magicite:managed:start -->\n"),
        (".mcp.json", '{"mcpServers":{"magicite":null}}'),
        (".mcp.json", '{"mcpServers":{},"mcpServers":{}}'),
    ],
)
def test_preflight_no_partial_writes(tmp_path, path, content):
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    before = snapshot(tmp_path)
    result = invoke(tmp_path, "setup")
    assert result.exit_code != 0
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("path", [".claude/rules/magicite.md", "AGENTS.md", ".magicite/instructions.md"])
def test_edited_owned_content_is_preserved(tmp_path, path):
    assert invoke(tmp_path, "setup").exit_code == 0
    target = tmp_path / path
    target.write_text(target.read_text().replace("Magicite routing", "My custom routing"))
    before = snapshot(tmp_path)
    assert invoke(tmp_path, "harness", "disable").exit_code != 0
    assert invoke(tmp_path, "setup").exit_code != 0
    assert snapshot(tmp_path) == before
    status = manage(str(tmp_path), "all", "status")["harnesses"]
    assert any(s["state"] == "drifted" for s in status.values())


def test_missing_component_repair_and_symlink(tmp_path):
    assert invoke(tmp_path, "setup").exit_code == 0
    (tmp_path / ".claude/rules/magicite.md").unlink()
    assert manage(str(tmp_path), "claude", "status")["harnesses"]["claude"]["state"] == "missing"
    assert invoke(tmp_path, "setup").exit_code == 0
    (tmp_path / ".claude/rules/magicite.md").unlink()
    external = tmp_path / "outside.md"
    external.write_text("User file")
    (tmp_path / ".claude/rules/magicite.md").symlink_to(external)
    before = snapshot(tmp_path)
    assert invoke(tmp_path, "setup").exit_code != 0
    assert snapshot(tmp_path) == before


def test_inline_toml_parent_refused_without_partial_writes(tmp_path):
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex/config.toml").write_text('mcp_servers = { other = { command = "other" } }\n')
    before = snapshot(tmp_path)
    assert invoke(tmp_path, "setup").exit_code != 0
    assert snapshot(tmp_path) == before


def test_opencode_both_components_removed_together(tmp_path):
    assert invoke(tmp_path, "setup", "--harness", "opencode").exit_code == 0
    assert manage(str(tmp_path), "opencode", "status")["harnesses"]["opencode"]["ready"]
    assert invoke(tmp_path, "harness", "disable", "--harness", "opencode").exit_code == 0
    data = json.loads((tmp_path / "opencode.json").read_text())
    assert "magicite" not in data["mcp"]
    assert ".magicite/instructions.md" not in data["instructions"]
