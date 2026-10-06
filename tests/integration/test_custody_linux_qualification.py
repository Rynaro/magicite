"""Native-safe runner guard tests; these are not Linux deployment witnesses."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("custody_probe", ROOT / "scripts/qualify_custody_linux.py")
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
SHA = "a" * 40


def test_wrong_platform_fails_preflight(monkeypatch):
    monkeypatch.setattr(probe.platform, "system", lambda: "Darwin")
    with pytest.raises(RuntimeError, match="cannot skip"):
        probe.preflight()


def test_unprivileged_linux_fails_preflight(monkeypatch):
    monkeypatch.setattr(probe.platform, "system", lambda: "Linux")
    monkeypatch.setattr(probe.os, "getuid", lambda: 41002)
    with pytest.raises(RuntimeError, match="cannot skip"):
        probe.preflight()


def test_preflight_failure_persists_failed_report(tmp_path, monkeypatch):
    monkeypatch.setattr(probe.platform, "system", lambda: "Darwin")
    assert probe.run(tmp_path, SHA, False) == 1
    import json

    assert json.loads((tmp_path / "report.json").read_text())["status"] == "FAIL"
    assert (tmp_path / "artifacts.json").exists()


@pytest.mark.parametrize(
    "value",
    [
        {"allowed": True, "body_present": False},
        {"allowed": False, "body_present": True},
        {"allowed": False},
        {},
    ],
)
def test_denial_oracle_rejects_allowed_or_leaking_or_missing_evidence(value):
    with pytest.raises(AssertionError):
        probe.denied_body(value)


def test_denial_oracle_accepts_explicit_closed_no_body():
    probe.denied_body({"allowed": False, "body_present": False})


def valid_unit_report():
    denied = {"allowed": False, "body_present": False}
    before = {"head_sequence": 3, "head_mac": "b" * 64}
    after = {"head_sequence": 4, "head_mac": "c" * 64}
    deaths = {
        "uid": 1,
        "groups": [],
        "signal": "SIGKILL",
        "head_before": before,
        "head_after": after,
        "effect_count": 1,
        "record_id": "revoke",
        "barrier_sequence": 4,
        "closed_before_reconciliation": denied,
        "access": denied,
    }
    evidence = {
        "preflight": {"failed_closed": True},
        "production-workflow": {"allowed": True, "body_present": True, "uid": 1},
        "foreign-peer": {
            "rejected": True,
            "reached_socket": True,
            "head_before": before,
            "head_after": before,
            "pid": 20,
            "uid": 3,
        },
        "same-uid": {"rejected": True},
        "protected-permissions": {"denied": [1, 2, 3, 4, 5], "bytes_unchanged": True},
        "prepared-client-death": {**deaths, "pid": 40, "boundary": "prepare_record-authenticated-return"},
        "committed-client-death": {**deaths, "pid": 50, "boundary": "commit_record-authenticated-return"},
        "acknowledged-service-restart": {
            "access": denied,
            "pid": 30,
            "restarted_pid": 60,
            "signal": "SIGKILL",
            "head_before": after,
            "head_after": after,
            "stale_socket_operator_cleanup": True,
        },
        "restore-retained-suffix": {"access": denied, "status": "ok", "head": after},
        "restore-missing-suffix": {
            "access": denied,
            "protected_head_preserved": True,
            "restricted": True,
            "removed_records": 1,
            "failure_type": "CustodianError",
            "head_before": after,
            "head_after": after,
        },
    }
    return copy.deepcopy(
        {
            "source_commit": SHA,
            "status": "PASS",
            "source_dirty": False,
            "source_hashes": probe.source_hashes(),
            "platform": {"system": "Linux", "kernel": "fixture"},
            "uids": {"writer": 1, "custodian": 2, "foreign": 3},
            "kernel_peers": [
                {"server_uid": 2, "peer_uid": 1, "peer_pid": pid, "server_pid": server}
                for pid, server in [(10, 30), (40, 30), (50, 30), (70, 60)]
            ]
            + [{"server_uid": 2, "peer_uid": 3, "peer_pid": 20, "server_pid": 30}],
            "cases": [{"id": name, "status": "PASS", "evidence": evidence[name]} for name in probe.CASES],
        }
    )


@pytest.mark.parametrize(
    "variant", ["missing-case", "skip", "duplicate", "dirty", "digest", "platform", "uids"]
)
def test_report_rejects_incomplete_or_unbound_observation(monkeypatch, variant):
    # Synthetic report negative controls never claim actual process execution.
    report = valid_unit_report()
    monkeypatch.setattr(probe, "source_identity", lambda *args: {"source_dirty": False})
    probe.validate_report(report, SHA)  # positive baseline; every mutation below is meaningful
    if variant == "missing-case":
        report["cases"].pop()
    elif variant == "skip":
        report["cases"][0]["status"] = "SKIP"
    elif variant == "duplicate":
        report["cases"][1]["id"] = report["cases"][0]["id"]
    elif variant == "dirty":
        report["source_dirty"] = True
    elif variant == "digest":
        report["source_hashes"][next(iter(report["source_hashes"]))] = "0" * 64
    elif variant == "platform":
        report["platform"]["system"] = "Darwin"
    elif variant == "uids":
        report["uids"]["writer"] = report["uids"]["custodian"]
    with pytest.raises(ValueError):
        probe.validate_report(report, SHA)


def test_clean_binding_checks_actual_git_candidate():
    with pytest.raises(ValueError, match="exact clean Git candidate"):
        probe.source_identity(SHA, False)


@pytest.mark.parametrize(
    "case,field",
    [
        ("prepared-client-death", "pid"),
        ("prepared-client-death", "signal"),
        ("prepared-client-death", "boundary"),
        ("prepared-client-death", "head_after"),
        ("committed-client-death", "effect_count"),
        ("committed-client-death", "closed_before_reconciliation"),
        ("acknowledged-service-restart", "pid"),
        ("acknowledged-service-restart", "restarted_pid"),
        ("acknowledged-service-restart", "head_before"),
        ("restore-retained-suffix", "status"),
        ("restore-missing-suffix", "removed_records"),
        ("restore-missing-suffix", "restricted"),
    ],
)
def test_report_rejects_missing_actual_death_or_recovery_evidence(monkeypatch, case, field):
    monkeypatch.setattr(probe, "source_identity", lambda *args: {"source_dirty": False})
    report = valid_unit_report()
    probe.validate_report(report, SHA)
    row = next(row for row in report["cases"] if row["id"] == case)
    row["evidence"].pop(field)
    with pytest.raises((ValueError, AssertionError)):
        probe.validate_report(report, SHA)
