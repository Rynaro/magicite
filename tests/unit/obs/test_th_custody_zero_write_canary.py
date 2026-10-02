"""AC-TH-11 gap closure: zero-write diagnosis over custody states + private-key canary.

Test-only. Fixture custody comes from tests/support/custody_adapter.py (the
in-process simulated custodian; no OS isolation is claimed).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import shutil
import stat
from pathlib import Path

import pytest
from click.testing import CliRunner

from magicite.__main__ import cli
from magicite.core import backup as backup_mod
from magicite.core import registry as registry_mod
from magicite.core import trust, writer_guard
from magicite.core.trust_custodian import CustodianError, CustodianStore
from magicite.embeddings.hashing_provider import get_embedder
from magicite.mcp import app as app_mod
from magicite.storage import db as db_mod

# --------------------------------------------------------------------- helpers


def _fingerprint(*roots: Path) -> dict[str, tuple[int, int, int, str]]:
    """mode, size, mtime_ns, sha256 for every file and dir (dir mtime catches creates/deletes)."""
    out: dict[str, tuple[int, int, int, str]] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in [root, *sorted(root.rglob("*"))]:
            st = path.lstat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "dir"
            out[str(path)] = (stat.S_IMODE(st.st_mode), st.st_size, st.st_mtime_ns, digest)
    return out


def _canaries(custody_dir: Path) -> dict[str, bytes]:
    """Real secret values of the fixture custodian in raw/hex/base64/urlsafe-base64 form."""
    out: dict[str, bytes] = {}
    for name in ("signing.key", "journal.key"):
        raw = (custody_dir / name).read_bytes()
        assert len(raw) == 32
        out[name + ":raw"] = raw
        out[name + ":hex"] = raw.hex().encode()
        out[name + ":b64"] = base64.b64encode(raw)
        out[name + ":b64url"] = base64.urlsafe_b64encode(raw)
        out[name + ":b64-nopad"] = base64.b64encode(raw).rstrip(b"=")
        out[name + ":b64url-nopad"] = base64.urlsafe_b64encode(raw).rstrip(b"=")
    return out


def _leaks(haystack: bytes, canaries: dict[str, bytes]) -> list[str]:
    return sorted(label for label, value in canaries.items() if value in haystack)


def _scan_tree(root: Path, canaries: dict[str, bytes]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            hits = _leaks(path.read_bytes() + str(path.relative_to(root)).encode(), canaries)
            if hits:
                found[path.relative_to(root).as_posix()] = hits
    return found


@pytest.fixture
def custody(cfg):
    """(cfg, provider, custody_dir, canaries). cfg already has fixture custody + genesis."""
    provider = writer_guard.resolve_custody(cfg)[1]
    return cfg, provider, provider.store.directory, _canaries(provider.store.directory)


def _decision(policy: trust.TrustPolicy) -> dict:
    return trust.TrustDecision(
        decision_id="canary-pending",
        engram_id="subject",
        content_digest="a" * 64,
        decision="pending",
        source_channel="local_authored",
        policy_id=policy.policy_id,
        policy_revision=policy.revision,
        policy_digest=policy.digest(),
        actor="operator",
        timestamp="2026-09-30T00:00:00Z",
    ).to_dict()


def _prepare(provider, *, commit: bool) -> None:
    store, rid = provider.store, provider.registry_id
    fence = store.register_fence(
        rid, predecessor=store.read_current(rid), attempt_id="a" * 8, holder="h", local_token=1
    )
    head = store.read_current(rid)
    record = store.prepare_record(
        rid,
        fence=fence,
        expected_head=head,
        record_id="canary-pending",
        kind="trust_decision",
        payload=_decision(trust.default_policy()),
    )
    if commit:
        store.commit_record(rid, fence=fence, expected_head=head, record=record)


class _Unavailable:
    """Real custody-unavailable: the store is re-opened per call with its key file removed."""

    def __init__(self, directory: Path, registry_id: str):
        self.directory, self.registry_id = directory, registry_id

    def call(self, operation, **arguments):
        store = CustodianStore.open(self.directory)  # raises CustodianError (redacted)
        try:
            return getattr(store, operation)(self.registry_id, **arguments)
        finally:
            store.close()


def _state_unavailable(cfg, provider, tmp_path, monkeypatch):
    broken = tmp_path / "broken-custody"
    shutil.copytree(provider.store.directory, broken)
    (broken / "signing.key").unlink()
    adapter = _Unavailable(broken, provider.registry_id)
    monkeypatch.setattr(writer_guard, "resolve_custody", lambda c: (provider.registry_id, adapter))


def _state_corrupt_journal(cfg, provider, tmp_path, monkeypatch):
    journal = cfg.data_dir / "trust/authority/journal.jsonl"
    assert journal.is_file()
    journal.write_bytes(b'{"truncated": \xff\xfe not json\n')


def _state_stale_head(cfg, provider, tmp_path, monkeypatch):
    head = cfg.data_dir / "trust/authority/head.json"
    stale = head.read_bytes()
    _prepare(provider, commit=True)  # custody advances; the local head.json is now stale
    head.write_bytes(stale)
    assert (
        provider.store.read_current(provider.registry_id)["head_sequence"]
        > json.loads(stale)["head_sequence"]
    )


def _state_pending(cfg, provider, tmp_path, monkeypatch):
    _prepare(provider, commit=False)
    assert provider.store.read_current(provider.registry_id)["pending_record_id"] == "canary-pending"


def _state_anchor_ahead(cfg, provider, tmp_path, monkeypatch):
    local = json.loads((cfg.data_dir / "trust/authority/head.json").read_bytes())
    _prepare(provider, commit=True)
    assert provider.store.read_current(provider.registry_id)["head_sequence"] > local["head_sequence"]


STATES = {
    "custody-unavailable": _state_unavailable,
    "corrupt-journal": _state_corrupt_journal,
    "stale-head": _state_stale_head,
    "pending": _state_pending,
    "anchor-ahead": _state_anchor_ahead,
}

# ------------------------------------------------------- 1. zero-write diagnosis


@pytest.mark.parametrize("state", sorted(STATES))
def test_doctor_and_custody_status_write_nothing_under_custody_states(
    state, custody, tmp_path, monkeypatch, caplog
):
    """AC-TH-11 / VERIFY "doctor and custody status diagnose without writing": for
    custody unavailable, corrupt journal, stale head.json, pending and anchor-ahead
    states, neither `magicite doctor` nor `magicite custody status` changes any
    byte, mode, mtime or file set under the project or custody directories, and
    their output is redacted (no key material) and, on failure, actionable."""
    cfg, provider, custody_dir, canaries = custody
    STATES[state](cfg, provider, tmp_path, monkeypatch)
    roots = (cfg.project_root, custody_dir, tmp_path / "broken-custody")
    before = _fingerprint(*roots)
    assert any("trust/authority" in k for k in before)  # non-vacuous: trust state is in scope
    assert any(k.endswith("signing.key") for k in before) or state == "custody-unavailable"
    runner = CliRunner()
    caplog.set_level(logging.DEBUG)

    doctor = runner.invoke(cli, ["doctor", "--project-root", str(cfg.project_root)])
    status = runner.invoke(cli, ["custody", "status", "--project-root", str(cfg.project_root)])

    assert _fingerprint(*roots) == before, f"{state}: diagnosis mutated files"
    for result in (doctor, status):
        blob = result.output.encode() + result.stderr_bytes if result.stderr_bytes else result.output.encode()
        assert _leaks(blob, canaries) == []
    assert _leaks(caplog.text.encode(), canaries) == []
    # doctor ran to a doctor/1 report (not a crash) -- it reports no custody-specific signal
    assert doctor.exception is None or isinstance(doctor.exception, SystemExit)
    report = json.loads(doctor.output[: doctor.output.rindex("}") + 1])
    assert report["kind"] == "doctor/1"
    assert not any("custody" in c["id"] for c in report["checks"])
    if state == "custody-unavailable":
        assert status.exit_code != 0
        assert "reconciliation_required" in status.output
        assert "verify protected custody" in status.output  # actionable
    else:
        assert status.exit_code == 0, status.output
        head = json.loads(status.output)
        assert set(head) >= {"registry_id", "head_sequence", "pending_record_id"}
        if state == "pending":
            assert head["pending_record_id"] == "canary-pending"


def test_zero_write_comparator_detects_a_write(custody):
    """Positive control for the zero-write assertion: the fingerprint sees byte, mtime
    and create changes in both directories."""
    cfg, _, custody_dir, _ = custody
    before = _fingerprint(cfg.project_root, custody_dir)
    (cfg.data_dir / "trust/authority/head.json").write_bytes(b"x")
    assert _fingerprint(cfg.project_root, custody_dir) != before
    mid = _fingerprint(cfg.project_root, custody_dir)
    (custody_dir / "new").write_bytes(b"")
    assert _fingerprint(cfg.project_root, custody_dir) != mid


# ------------------------------------------------------------ 2. key canary


def test_scanner_detects_planted_canary_in_every_encoding(custody, tmp_path):
    """AC-TH-11 / VERIFY "private keys never appear in ...": positive control."""
    _, _, _, canaries = custody
    assert len(canaries) == 12
    for label, value in canaries.items():
        assert label in _leaks(b"prefix " + value + b" suffix", canaries)
        planted = tmp_path / ("p-" + label.replace(":", "_"))
        planted.mkdir()
        (planted / "member.bin").write_bytes(b"junk" + value)
        assert label in _scan_tree(planted, canaries).get("member.bin", [])
    assert _leaks(b"innocuous output", canaries) == []


def test_backup_archive_members_contain_no_custodian_secret(custody, tmp_path):
    """AC-TH-11 / VERIFY "private keys never appear in backup archives": scan every
    member of a complete backup (incl. member names) for signing.key / journal.key."""
    cfg, _, _, canaries = custody
    cfg.ensure_dirs()
    conn = db_mod.connect(cfg.db_path)
    try:
        outcome = registry_mod.register(cfg, conn, get_embedder(dim=256), path=".magicite/engrams")
        assert outcome.ingested >= 1
        cfg.toml_path.write_text("[routing]\n# canary\n", encoding="utf-8")
        backup_dir = tmp_path / "backup"
        snapshot = backup_mod.create_snapshot(cfg, conn, backup_dir)
    finally:
        conn.close()
    members = [p for p in backup_dir.rglob("*") if p.is_file()]
    assert len(members) >= len(snapshot["files"]) >= 3  # non-vacuous: real content scanned
    assert any(
        p.relative_to(backup_dir).parts[:2] == ("files", "trust") or "trust" in p.parts for p in members
    )
    assert _scan_tree(backup_dir, canaries) == {}

    # positive control: a canary in a backed-up domain (config) IS carried and IS found.
    cfg.toml_path.write_text("[routing]\n# " + canaries["signing.key:hex"].decode() + "\n", encoding="utf-8")
    conn = db_mod.connect(cfg.db_path)
    try:
        backup_mod.create_snapshot(cfg, conn, tmp_path / "backup-planted")
    finally:
        conn.close()
    assert _scan_tree(tmp_path / "backup-planted", canaries)


def _secret_laden(canaries: dict[str, bytes]) -> str:
    return "secret=" + canaries["signing.key:hex"].decode() + " " + canaries["journal.key:b64"].decode()


class _Leaky:
    """Custody whose failures embed the real secrets, to prove outer layers redact."""

    def __init__(self, registry_id: str, canaries: dict[str, bytes]):
        self.registry_id, self.canaries = registry_id, canaries
        self.armed = False

    def call(self, operation, **arguments):
        if self.armed:
            raise CustodianError(_secret_laden(self.canaries))
        return {"registry_id": self.registry_id, "legacy_reconciliation": None, "head_sequence": 1}


def test_cli_custody_failure_json_and_logs_carry_no_secret(custody, monkeypatch, caplog):
    """AC-TH-11 / VERIFY "private keys never appear in CLI error JSON or logs": a failing
    custody command whose underlying exception embeds the secrets emits only the fixed
    diagnosis."""
    cfg, provider, _, canaries = custody
    leaky = _Leaky(provider.registry_id, canaries)
    leaky.armed = True
    monkeypatch.setattr(writer_guard, "resolve_custody", lambda c: (provider.registry_id, leaky))
    caplog.set_level(logging.DEBUG)
    result = CliRunner().invoke(cli, ["custody", "status", "--project-root", str(cfg.project_root)])
    assert result.exit_code != 0
    assert "reconciliation_required" in result.output
    assert _leaks(result.output.encode() + (result.stderr_bytes or b""), canaries) == []
    assert _leaks(caplog.text.encode(), canaries) == []
    # control: the exception really carried the canary (so redaction, not absence, is witnessed)
    with pytest.raises(CustodianError) as raised:
        leaky.call("read_current")
    assert _leaks(str(raised.value).encode(), canaries)


def test_cli_magicite_error_json_carries_no_secret(custody, capsys):
    """AC-TH-11 / VERIFY "CLI error JSON": the shared `_die_magicite` envelope redacts
    a MagiciteError whose message/hint embed the secrets."""
    from magicite.__main__ import _die_magicite
    from magicite.errors import MagiciteError

    _, _, _, canaries = custody
    error = MagiciteError(_secret_laden(canaries), hint=_secret_laden(canaries))
    with pytest.raises(SystemExit):
        _die_magicite(error)
    out = capsys.readouterr()
    assert out.out.strip() and json.loads(out.out)["code"]
    assert _leaks((out.out + out.err).encode(), canaries) == []


def test_mcp_error_payload_and_logs_carry_no_secret(custody, monkeypatch, caplog, capfd):
    """AC-TH-11 / VERIFY "MCP error payloads": a custody-touching tool call (`register`,
    writer-connection tool going through the registry writer lease) whose custody
    fails with a secret-bearing exception returns only the redacted internal-error
    envelope, and nothing reaches logs/stderr."""
    cfg, provider, _, canaries = custody
    records: list[str] = []

    class _Recorder:  # structlog's stream is bound at import, so record events directly
        def __getattr__(self, level):
            return lambda event, **kw: records.append(f"{level} {event} {kw!r}")

    monkeypatch.setattr(app_mod, "logger", _Recorder())
    state = app_mod.build_state(cfg)
    try:
        leaky = _Leaky(provider.registry_id, canaries)
        monkeypatch.setattr(writer_guard, "resolve_custody", lambda c: (provider.registry_id, leaky))
        leaky.armed = True
        caplog.set_level(logging.DEBUG)
        result = app_mod.dispatch_call(state, "register", {"path": ".magicite/engrams"})
        assert result.is_error is True
        payload = json.dumps(result.structured_content) + json.dumps([c.model_dump() for c in result.content])
        assert result.structured_content["message"] == "internal error"  # custody failure reached
        assert leaky.armed and "code" in result.structured_content
        assert _leaks(payload.encode(), canaries) == []
        logged = caplog.text + capfd.readouterr().err + "\n".join(records)
        assert "CustodianError" in logged  # the failing custody call was logged, only by type
        assert _leaks(logged.encode(), canaries) == []
    finally:
        state.conn.close()
        state.writer_conn.close()


def test_mcp_error_scan_has_teeth(custody):
    """Control for the MCP/log scans: the redaction layer, not the scanner, is what keeps
    secrets out -- a payload built from the raw exception text is flagged."""
    _, _, _, canaries = custody
    raw = json.dumps({"message": _secret_laden(canaries)})
    assert _leaks(raw.encode(), canaries)
