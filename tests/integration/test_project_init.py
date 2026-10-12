"""Local transport and genuine existing admission machinery; simulated test custody only."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest
from tests.support.custody_adapter import fixture_cli_argv

from magicite import project_init as init
from magicite.core import writer_guard
from magicite.mcp import bind_ops


def test_actual_raw_stdio_handshake_under_explicit_fixture_custody(cfg):
    # Real production CLI/server, explicit test adapter; no host/OS qualification claim.
    asyncio.run(
        init._handshake(
            fixture_cli_argv(cfg, "serve", "--project-root", str(cfg.project_root)),
            {**os.environ, "MAGICITE_EMBEDDING_PROVIDER": "hashing", "MAGICITE_EMBEDDING_OFFLINE": "1"},
            timeout=10,
        )
    )
    writer_guard.preflight_custody(cfg)
    assert cfg.db_path.is_file()


@pytest.mark.parametrize("case", ["timeout", "malformed", "exit", "missing-tools", "bad-initialize"])
def test_protocol_failures_are_bounded_and_child_is_reaped(tmp_path, case):
    pid = tmp_path / "pid"
    driver = tmp_path / "stdio.py"
    driver.write_text("""import json, os, signal, sys, time
from pathlib import Path
Path(sys.argv[1]).write_text(str(os.getpid()))
case=sys.argv[2]
if case == 'timeout':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(30)
if case == 'exit': sys.exit(7)
request=json.loads(sys.stdin.readline())
if case == 'malformed':
    print('not json', flush=True)
    time.sleep(30)
else:
    version='bad' if case == 'bad-initialize' else '2025-11-25'
    print(json.dumps({'jsonrpc':'2.0','id':1,'result':{'protocolVersion':version,
          'serverInfo':{'name':'magicite','version':'1'}}}), flush=True)
    sys.stdin.readline()
    sys.stdin.readline()
    names=['route','load_skill_body'] if case == 'missing-tools' else ['route','load_skill_body','register']
    print(json.dumps({'jsonrpc':'2.0','id':2,'result':{'tools':[{'name':n} for n in names]}}),flush=True)
    time.sleep(30)
""")
    with pytest.raises((TimeoutError, ValueError, KeyError)):
        asyncio.run(init._handshake([sys.executable, str(driver), str(pid), case], dict(os.environ), 0.5))
    assert pid.exists()
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid.read_text()), 0)


def skill_source(cfg) -> Path:
    directory = cfg.project_root / "skills"
    source = Path(__file__).resolve().parents[1] / "fixtures/toy-registry/skills/steam-download-region-fix"
    shutil.copytree(source, directory / "example")
    return directory


def consent(monkeypatch, answers):
    prompts = []
    sequence = iter(answers)

    def confirm(message, default=False):
        assert default is False
        prompts.append(message)
        return next(sequence)

    monkeypatch.setattr(init.click, "confirm", confirm)
    monkeypatch.setattr(init.click, "prompt", lambda *args, **kwargs: "test-explicit-operator")
    return prompts


def test_noninteractive_or_declined_import_and_approval(cfg, monkeypatch):
    directory = skill_source(cfg)
    before = list(cfg.registry_dir.glob("*.egr.md"))
    monkeypatch.setattr(init.click, "confirm", lambda *args, **kwargs: pytest.fail("noninteractive prompt"))
    assert init.onboarding(cfg, directory, False)
    assert list(cfg.registry_dir.glob("*.egr.md")) == before
    prompts = consent(monkeypatch, [False])
    assert init.onboarding(cfg, directory, True)
    assert len(prompts) == 1 and list(cfg.registry_dir.glob("*.egr.md")) == before


def test_explicit_per_digest_approval_leaves_skillmd_draft(cfg, monkeypatch, capsys):
    directory = skill_source(cfg)
    prompts = consent(monkeypatch, [True, True, True])
    assert init.onboarding(cfg, directory, True)
    views = init.inventory(cfg)
    assert len(views) == 1 and views[0]["admitted"] is True
    assert views[0]["lifecycle_status"] == "draft"
    assert len(prompts) == 3
    output = capsys.readouterr().out
    assert views[0]["engram_id"] in output and views[0]["content_digest"] in output
    assert '"lifecycle_status": "draft"' in output
    # Actual trust authority records explicit actor and exact digest.
    decisions = bind_ops.trust_list(cfg.project_root)
    assert any(d["actor"] == "test-explicit-operator" for d in decisions)


def test_stale_digest_rejected_by_existing_approval(cfg, monkeypatch, capsys):
    directory = skill_source(cfg)
    consent(monkeypatch, [True, True, True])
    real_review = bind_ops.trust_review

    def change_after_display(root, *, engram_id):
        view = real_review(root, engram_id=engram_id)
        with sqlite3.connect(cfg.db_path) as conn:
            relpath = conn.execute("SELECT path FROM engram WHERE id=?", (engram_id,)).fetchone()[0]
        artifact = cfg.project_root / relpath
        artifact.write_bytes(artifact.read_bytes() + b"\nChanged after review.\n")
        return view

    monkeypatch.setattr(bind_ops, "trust_review", change_after_display)
    assert not init.onboarding(cfg, directory, True)
    assert "Approval failed" in capsys.readouterr().err
    assert init.inventory(cfg)[0]["admitted"] is False


def test_partial_import_preserves_real_registered_draft(cfg, monkeypatch, capsys):
    directory = skill_source(cfg)
    (directory / "invalid.egr.md").write_text("not an engram")
    consent(monkeypatch, [True, False])
    assert not init.onboarding(cfg, directory, True)
    views = init.inventory(cfg)
    assert len(views) == 1 and views[0]["lifecycle_status"] == "draft" and not views[0]["admitted"]
    output = capsys.readouterr().out
    report = json.loads(output.splitlines()[0])
    assert report["ingested"] == 1 and report["validation_errors"] and report["skipped_unchanged"] == 0


def test_readonly_inventory_checks_live_digest_and_does_not_create_db(cfg, monkeypatch):
    assert init.inventory(cfg) == [] and not cfg.db_path.exists()
    directory = skill_source(cfg)
    consent(monkeypatch, [True, True, True])
    assert init.onboarding(cfg, directory, True)
    assert init.inventory(cfg)[0]["admitted"]
    with sqlite3.connect(cfg.db_path) as conn:
        relpath = conn.execute(
            "SELECT path FROM engram WHERE id=?", (init.inventory(cfg)[0]["engram_id"],)
        ).fetchone()[0]
    artifact = cfg.project_root / relpath
    artifact.write_bytes(artifact.read_bytes() + b"\nUnreviewed bytes.\n")
    assert not init.inventory(cfg)[0]["admitted"]
