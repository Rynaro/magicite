#!/usr/bin/env python3
"""Run bounded real-UID Linux custody drills; prerequisite failures never skip."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import platform
import re
import select
import shutil
import signal
import socket
import sqlite3
import stat
import struct
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CASES = (
    "preflight",
    "production-workflow",
    "foreign-peer",
    "same-uid",
    "protected-permissions",
    "prepared-client-death",
    "committed-client-death",
    "acknowledged-service-restart",
    "restore-retained-suffix",
    "restore-missing-suffix",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


KNOWN_CUSTODY_REASONS = {
    "unprotected custody path",
    "writable custody path",
    "extended custody ACL is unsupported",
    "custody directory required",
    "custody path must be absolute",
    "custody state must remain private",
    "custody ACL inspection unavailable",
    "independent custody requires distinct OS identities",
}


def safe_error_chain(error: BaseException) -> list[dict[str, Any]]:
    chain = []
    current: BaseException | None = error
    for _ in range(4):
        if current is None:
            break
        row: dict[str, Any] = {"type": type(current).__name__}
        if isinstance(current, OSError):
            row["errno"] = current.errno
        if (
            isinstance(current, ModuleNotFoundError)
            and current.name
            and re.fullmatch(r"[A-Za-z0-9_.]+", current.name)
        ):
            row["module"] = current.name
        if str(current) in KNOWN_CUSTODY_REASONS:
            row["reason"] = str(current)
        chain.append(row)
        current = current.__cause__
    return chain


def custody_cli(arguments: list[str]) -> int:
    # Same production Click entrypoint/output, with machine-safe failure causes.
    try:
        from magicite.__main__ import cli

        cli.main(args=["custody", *arguments], standalone_mode=False)
    except Exception as exc:
        print("CUSTODY_CLI_FAILURE " + json.dumps(safe_error_chain(exc)), file=sys.stderr)
        return 1
    return 0


def path_metadata(path: Path, role: str) -> dict[str, Any]:
    value: dict[str, Any] = {"role": role}
    try:
        info = path.lstat()
        value.update(
            owner_uid=info.st_uid,
            owner_gid=info.st_gid,
            mode=oct(stat.S_IMODE(info.st_mode)),
            symlink=stat.S_ISLNK(info.st_mode),
        )
        if path.is_symlink():
            resolved = path.resolve().stat()
            value.update(
                resolved_owner_uid=resolved.st_uid, resolved_mode=oct(stat.S_IMODE(resolved.st_mode))
            )
    except OSError as exc:
        value.update(error_type=type(exc).__name__, errno=exc.errno)
    return value


class ChildCommandFailure(RuntimeError):
    def __init__(self, operation: str, result: subprocess.CompletedProcess, layout: list[dict[str, Any]]):
        self.detail: dict[str, Any] = {
            "operation": operation,
            "exit_code": result.returncode,
            "layout": layout,
        }
        for line in reversed(result.stderr.splitlines()):
            if line.startswith("CUSTODY_CLI_FAILURE "):
                self.detail["error_chain"] = json.loads(line.removeprefix("CUSTODY_CLI_FAILURE "))
                break
        if "error_chain" not in self.detail:
            # No arbitrary stderr or exception values enter the public artifact.
            last = result.stderr.splitlines()[-1] if result.stderr.splitlines() else ""
            category = last.split(":", 1)[0]
            self.detail["stderr_category"] = (
                category if re.fullmatch(r"[A-Za-z]+Error", category) else "unclassified"
            )
        super().__init__("credential-dropped production custody command failed")


def preflight() -> None:
    if platform.system() != "Linux" or os.getuid() != 0:
        raise RuntimeError("Linux root fixture setup is required; qualification cannot skip")
    if not hasattr(socket, "SO_PEERCRED"):
        raise RuntimeError("Linux kernel peer credentials are required")


def source_hashes() -> dict[str, str]:
    paths = list((ROOT / "src").rglob("*.py")) + [Path(__file__)]
    paths += [
        ROOT / name
        for name in (
            ".github/workflows/custody-qualification.yml",
            "tests/integration/test_custody_linux_qualification.py",
            ".spectra/plans/v1-custody-qualification.acceptance.md",
            "docs/qualification/linux-custody.md",
        )
        if (ROOT / name).exists()
    ]
    return {str(path.relative_to(ROOT)): digest(path) for path in sorted(paths)}


def source_identity(candidate: str, allow_dirty: bool) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{40}", candidate):
        raise ValueError("full candidate SHA required")
    if allow_dirty:
        return {"mode": "declared-local-rehearsal", "source_dirty": True}
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    status = subprocess.check_output(["git", "status", "--porcelain=v1"], cwd=ROOT, text=True)
    if head != candidate or status:
        raise ValueError("qualification requires exact clean Git candidate")
    return {"mode": "verified-clean-git", "source_dirty": False}


def protected_head(value: dict[str, Any]) -> dict[str, Any]:
    # Fresh recovery/retry leases legitimately advance fencing, not committed history.
    return {key: item for key, item in value.items() if key != "fence_generation"}


def denied_body(value: dict[str, Any]) -> None:
    if value.get("allowed") is not False or value.get("body_present") is not False:
        raise AssertionError("trust-dependent body access was not closed")


def validate_report(report: dict[str, Any], candidate: str, *, allow_dirty: bool = False) -> None:
    if report.get("source_commit") != candidate or report.get("status") != "PASS":
        raise ValueError("candidate or qualification result mismatch")
    source_identity(candidate, allow_dirty)
    if report.get("source_dirty") != allow_dirty or report.get("source_hashes") != source_hashes():
        raise ValueError("dirty source or source artifact digest mismatch")
    results = report.get("cases", [])
    if [row.get("id") for row in results] != list(CASES) or any(
        row.get("status") != "PASS" for row in results
    ):
        raise ValueError("missing, skipped or failing core qualification case")
    uids = report.get("uids", {})
    if set(uids) != {"writer", "custodian", "foreign"} or len(set(uids.values())) != 3:
        raise ValueError("three distinct process identities required")
    if any(type(uid) is not int or uid <= 0 for uid in uids.values()):
        raise ValueError("unprivileged numeric identity metadata required")
    if report.get("platform", {}).get("system") != "Linux" or not report["platform"].get("kernel"):
        raise ValueError("Linux kernel observation missing")
    peers = report.get("kernel_peers", [])
    for row in peers:
        if row.get("server_uid") != uids["custodian"] or row.get("peer_uid") not in {
            uids["writer"],
            uids["foreign"],
        }:
            raise ValueError("unexpected observed peer identity")
        if any(type(row.get(key)) is not int or row[key] <= 0 for key in ("server_pid", "peer_pid")):
            raise ValueError("observed process PID missing")
    if {row["peer_uid"] for row in peers} != {uids["writer"], uids["foreign"]}:
        raise ValueError("both authorized and foreign kernel peers required")
    rows = {row["id"]: row["evidence"] for row in results}
    positive = rows["production-workflow"]
    if not positive["allowed"] or not positive["body_present"] or positive["uid"] != uids["writer"]:
        raise ValueError("nonvacuous authorized body control missing")
    foreign = rows["foreign-peer"]
    if (
        not foreign["rejected"]
        or not foreign["reached_socket"]
        or foreign["head_before"] != foreign["head_after"]
    ):
        raise ValueError("foreign-peer rejection or unchanged authority missing")
    if not any(row["peer_pid"] == foreign["pid"] and row["peer_uid"] == foreign["uid"] for row in peers):
        raise ValueError("foreign worker lacks matching kernel peer observation")
    if not rows["preflight"]["failed_closed"] or not rows["same-uid"]["rejected"]:
        raise ValueError("negative prerequisite/profile control missing")
    if (
        len(rows["protected-permissions"]["denied"]) != 5
        or not rows["protected-permissions"]["bytes_unchanged"]
    ):
        raise ValueError("protected access canary missing")
    for name in (
        "prepared-client-death",
        "committed-client-death",
        "acknowledged-service-restart",
        "restore-retained-suffix",
        "restore-missing-suffix",
    ):
        denied_body(rows[name]["access"])

    def head(value):
        if (
            not isinstance(value, dict)
            or type(value.get("head_sequence")) is not int
            or value["head_sequence"] < 1
        ):
            raise ValueError("committed head sequence missing")
        if not re.fullmatch(r"[0-9a-f]{64}", value.get("head_mac", "")):
            raise ValueError("committed head MAC missing")

    for name, boundary in (
        ("prepared-client-death", "prepare_record-authenticated-return"),
        ("committed-client-death", "commit_record-authenticated-return"),
    ):
        value = rows[name]
        if type(value.get("pid")) is not int or value["pid"] <= 0 or value.get("signal") != "SIGKILL":
            raise ValueError("actual process-death evidence missing")
        if (
            value.get("boundary") != boundary
            or value.get("uid") != uids["writer"]
            or value.get("groups") != []
        ):
            raise ValueError("death boundary or dropped identity mismatch")
        if not any(peer["peer_pid"] == value["pid"] and peer["peer_uid"] == uids["writer"] for peer in peers):
            raise ValueError("killed writer lacks actual kernel peer observation")
        head(value.get("head_before"))
        head(value.get("head_after"))
        if (
            value.get("effect_count") != 1
            or not value.get("record_id")
            or value["head_after"]["head_sequence"] != value.get("barrier_sequence")
            or value["head_before"]["head_sequence"] + 1 != value.get("barrier_sequence")
        ):
            raise ValueError("revocation one-effect sequence evidence missing")
        denied_body(value.get("closed_before_reconciliation", {}))
    restarted = rows["acknowledged-service-restart"]
    if (
        restarted.get("signal") != "SIGKILL"
        or type(restarted.get("pid")) is not int
        or restarted["pid"] <= 0
        or type(restarted.get("restarted_pid")) is not int
        or restarted["restarted_pid"] <= 0
        or restarted["restarted_pid"] == restarted["pid"]
        or restarted.get("stale_socket_operator_cleanup") is not True
    ):
        raise ValueError("service kill/restart observation missing")
    head(restarted.get("head_before"))
    head(restarted.get("head_after"))
    if restarted["head_before"] != restarted["head_after"]:
        raise ValueError("service restart changed acknowledged authority")
    for pid in (restarted["pid"], restarted["restarted_pid"]):
        if not any(peer["server_pid"] == pid for peer in peers):
            raise ValueError("service PID lacks observed peer evidence")
    restored = rows["restore-retained-suffix"]
    if restored.get("status") != "ok":
        raise ValueError("supported restore success missing")
    head(restored.get("head"))
    missing = rows["restore-missing-suffix"]
    head(missing.get("head_before"))
    head(missing.get("head_after"))
    if (
        missing.get("restricted") is not True
        or missing.get("removed_records") != 1
        or missing.get("failure_type") not in {"CustodianError", "TrustLedgerCorruptError"}
        or missing.get("protected_head_preserved") is not True
        or protected_head(missing["head_before"]) != protected_head(missing["head_after"])
    ):
        raise ValueError("missing suffix rejection or preserved anchor evidence missing")


def emit(value: Any) -> None:
    print(json.dumps(value, sort_keys=True), flush=True)


def worker(operation: str, fixture_path: Path, barrier_fd: int | None) -> None:
    from magicite.config import Config
    from magicite.core import backup, fingerprint_key, registry, routing_policy, trust, writer_guard
    from magicite.core.trust_custodian import CustodianError, CustodianStore
    from magicite.core.trust_custodian_transport import (
        CustodianClient,
        CustodianService,
        CustodyProfile,
        receive_message,
        send_message,
    )
    from magicite.embeddings.hashing_provider import get_embedder
    from magicite.mcp import app
    from magicite.storage import db

    fixture = json.loads(fixture_path.read_text())
    project = Path(fixture["project"])
    cfg = Config.load(project, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    identity = {"uid": os.getuid(), "gid": os.getgid(), "groups": os.getgroups(), "pid": os.getpid()}
    if operation == "preflight-negative":
        try:
            preflight()
        except RuntimeError:
            emit({**identity, "failed_closed": True})
            return
        raise AssertionError("unprivileged preflight accepted")
    if operation == "serve":
        profile = CustodyProfile.load(Path(fixture["profile"]), expected_owner_uid=os.getuid())
        store = CustodianStore.open(Path(fixture["private"]))

        class ObservedService(CustodianService):
            def handle(self, connection):
                pid, uid, gid = struct.unpack(
                    "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                )
                # Observation only; the original production method validates every request.
                with Path(fixture["peers"]).open("a") as log:
                    log.write(
                        json.dumps(
                            {
                                "server_pid": os.getpid(),
                                "server_uid": os.getuid(),
                                "peer_pid": pid,
                                "peer_uid": uid,
                                "peer_gid": gid,
                            }
                        )
                        + "\n"
                    )
                return super().handle(connection)

        try:
            ObservedService(store, profile).serve()
        finally:
            store.close()
        return
    if operation == "same-uid":
        try:
            CustodyProfile("same", 1, Path(fixture["socket"]), os.getuid(), os.getuid(), "00" * 32)
        except CustodianError:
            emit({**identity, "rejected": True})
            return
        raise AssertionError("same-UID profile accepted")
    if operation == "permissions":
        denied = []
        for label, path, mode in (
            ("journal-key-read", fixture["private"] + "/journal.key", os.O_RDONLY),
            ("signing-key-read", fixture["private"] + "/signing.key", os.O_RDONLY),
            ("authority-write", fixture["private"] + "/authority.sqlite", os.O_WRONLY),
            ("profile-write", fixture["profile"], os.O_WRONLY),
            ("descriptor-write", fixture["descriptor"], os.O_WRONLY),
        ):
            try:
                fd = os.open(path, mode | os.O_NOFOLLOW)
            except PermissionError:
                denied.append(label)
            else:
                os.close(fd)
                raise AssertionError("protected access unexpectedly permitted")
        emit({**identity, "denied": denied})
        return
    if operation == "foreign":
        profile = json.loads(Path(fixture["profile"]).read_text())
        request = {
            "version": "trust-custodian/1",
            "nonce": "a" * 64,
            "request_id": "b" * 32,
            "registry_id": fixture["registry"],
            "epoch": profile["epoch"],
            "operation": "read_current",
            "arguments": {},
        }
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(3)
            connection.connect(fixture["socket"])
            try:
                send_message(connection, request)
                receive_message(connection)
            except (CustodianError, OSError):
                emit({**identity, "rejected": True, "reached_socket": True})
                return
        raise AssertionError("foreign peer received a successful response")
    if operation == "history":
        snapshot = writer_guard.journal_for(cfg).snapshot()
        emit(
            {**identity, "head": snapshot.head, "record_ids": [row["record_id"] for row in snapshot.records]}
        )
        return
    if operation == "head":
        _, client = writer_guard.resolve_custody(cfg)
        emit({**identity, "head": client.call("read_current")})
        return
    if operation == "initialize":
        cfg.ensure_dirs()
        conn = db.connect(cfg.db_path)
        try:
            with writer_guard.registry_writer_lease(cfg, conn, ttl_s=1).acquire():
                journal, owner, _ = writer_guard.bound_journal(cfg)
                journal.initialize_reviewed_genesis(assert_owned=owner.assert_owned)
            result = registry.register(cfg, conn, get_embedder(256), path=".magicite/engrams")
            entry = result.registered[0]
            row = conn.execute("SELECT content_sha256 FROM engram WHERE id=?", (entry.id,)).fetchone()
            registry.review_approve(
                cfg, conn, engram_id=entry.id, expected_digest=row[0], actor="drill-review"
            )
            fingerprint_key.load_or_create_fingerprint_key(cfg)
            metadata = {
                "engram_id": entry.id,
                "content_digest": row[0],
                "policy_digest": routing_policy.compute_policy_digest(
                    routing_policy.resolve_policy_id(cfg), cfg
                ),
            }
            (project / "subject.json").write_text(json.dumps(metadata))
            cfg.toml_path.write_text("# pre-revoke backup configuration\n")
            backup.create_snapshot(cfg, conn, project / "backup")
            emit({**identity, "subject": metadata})
        finally:
            conn.close()
        return
    subject = json.loads((project / "subject.json").read_text())
    if operation == "access":
        try:
            state = app.build_state(cfg)
        except (CustodianError, trust.TrustLedgerCorruptError) as exc:
            emit({**identity, "allowed": False, "body_present": False, "failure_type": type(exc).__name__})
            return
        try:
            result = app.dispatch_call(
                state,
                "load_skill_body",
                {
                    "name": subject["engram_id"],
                    "expected_content_digest": subject["content_digest"],
                    "expected_policy_digest": subject["policy_digest"],
                },
            )
            value = result.structured_content or {}
            emit(
                {
                    **identity,
                    "allowed": not result.is_error and value.get("status") == "ok",
                    "body_present": any(
                        bool(value.get(key)) for key in ("procedure", "pitfalls", "examples", "provenance")
                    ),
                    "error": result.is_error,
                    "status": value.get("status"),
                    "reason_codes": value.get("reason_codes", []),
                }
            )
        finally:
            state.conn.close()
            state.writer_conn.close()
        return
    if operation in {"prepare-death", "commit-death"}:
        original = CustodianClient.call
        expected = "prepare_record" if operation == "prepare-death" else "commit_record"

        def observed_call(self, call, **arguments):
            result = original(self, call, **arguments)
            record = result if call == "prepare_record" else arguments.get("record", {})
            if call == expected and record.get("payload", {}).get("decision") == "revoke":
                assert barrier_fd is not None
                os.write(
                    barrier_fd,
                    (
                        json.dumps({**identity, "boundary": call + "-authenticated-return", "record": record})
                        + "\n"
                    ).encode(),
                )
                # IPC barrier: parent observes the completed real call then SIGKILLs this process.
                select.select([], [], [], 30)
                raise AssertionError("death barrier timed out without process kill")
            return result

        CustodianClient.call = observed_call
    conn = db.connect(cfg.db_path)
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                held = writer_guard.registry_writer_lease(cfg, conn, ttl_s=1, heartbeat_interval_s=0.1)
                with held.acquire():
                    journal, owner, fence = writer_guard.bound_journal(cfg)
                    if operation == "reconcile":
                        snapshot = journal.reconcile(fence=fence, assert_owned=owner.assert_owned)
                        trust.reload_from_mirror(cfg, conn)
                        emit({**identity, "head": snapshot.head})
                    elif operation == "retry":
                        record = json.loads((project / "retry-record.json").read_text())
                        decision = trust.TrustDecision.from_dict(record["payload"])
                        trust.persist_decision(cfg, conn, decision)
                        emit({**identity, "decision_id": decision.decision_id})
                    elif operation in {"revoke", "prepare-death", "commit-death"}:
                        decision = registry.review_revoke(
                            cfg,
                            conn,
                            engram_id=subject["engram_id"],
                            actor="drill-review",
                            event_id="drill-revoke",
                        )
                        emit({**identity, "decision_id": decision.decision_id})
                    elif operation == "policy-after-revoke":
                        policy = trust.load_policy(cfg).to_dict()
                        policy["revision"] += 1
                        journal.append(
                            record_id="post-revoke-policy",
                            kind="policy_snapshot",
                            payload=policy,
                            fence=fence,
                            assert_owned=owner.assert_owned,
                        )
                        emit(identity)
                    elif operation in {"restore", "restore-negative"}:
                        key = fingerprint_key.load_or_create_fingerprint_key(cfg)
                        if operation == "restore":
                            overlay = backup.build_recovery_overlay(
                                cfg, control_sequence=2, operator_provenance="qualification", key=key
                            )
                            anchor = backup.issue_sequence_anchor(overlay, key=key)
                            (project / "current-overlay.json").write_text(json.dumps(overlay.to_dict()))
                            (project / "current-anchor.json").write_text(json.dumps(anchor.to_dict()))
                        else:
                            overlay = json.loads((project / "current-overlay.json").read_text())
                            anchor = json.loads((project / "current-anchor.json").read_text())
                        cfg.toml_path.write_text("# changed after backup\n")
                        try:
                            outcome = backup.restore_snapshot(
                                cfg,
                                conn,
                                project / "backup",
                                overlay=overlay,
                                sequence_anchor=anchor,
                                custody_key=key,
                                preserve_live_overlay=False,
                            )
                        except (CustodianError, trust.TrustLedgerCorruptError) as exc:
                            if operation != "restore-negative":
                                raise
                            emit({**identity, "restricted": True, "failure_type": type(exc).__name__})
                        else:
                            assert operation == "restore" and outcome["status"] == "ok"
                            assert cfg.toml_path.read_text() == "# pre-revoke backup configuration\n"
                            emit({**identity, "status": outcome["status"]})
                    else:
                        raise ValueError("unknown worker operation")
                break
            except Exception as exc:
                from magicite.errors import BusyError

                if not isinstance(exc, BusyError) or time.monotonic() >= deadline:
                    raise
                select.select(
                    [], [], [], 0.05
                )  # bounded observed-lease-expiry polling, not crash synchronization
    finally:
        conn.close()


class Fixture:
    def __init__(self, parent: Path, name: str, uids: dict[str, int], owned: list):
        self.process = None
        self.descriptor = None
        owned.append(self)
        self.directory = parent / name
        self.directory.mkdir(mode=0o755)
        custody = self.directory / "custody"
        custody.mkdir(mode=0o755)
        os.chown(custody, uids["custodian"], uids["custodian"])
        project = self.directory / "project"
        project.mkdir(mode=0o755)
        os.chown(project, uids["writer"], uids["writer"])
        self.data = {
            "private": str(custody / "private"),
            "profile": str(custody / "profile.json"),
            "socket": str(custody / "socket"),
            "project": str(project),
            "registry": name,
            "peers": str(custody / "peers.jsonl"),
        }
        self.path = self.directory / "fixture.json"
        self.uids = uids
        self.process: subprocess.Popen | None = None
        self.descriptor: Path | None = None
        policy = self.directory / "policy.json"
        from magicite.core.trust import default_policy

        policy.write_text(json.dumps(default_policy().to_dict()))
        self.cli(["init", "--directory", self.data["private"]])
        self.cli(
            [
                "enroll",
                "--directory",
                self.data["private"],
                "--registry-id",
                name,
                "--policy",
                str(policy),
                "--actor",
                "qualification-operator",
                "--reviewed-sha256",
                digest(policy),
            ]
        )
        descriptor = self.cli(
            [
                "profile",
                "--directory",
                self.data["private"],
                "--registry-id",
                name,
                "--project-root",
                str(project),
                "--client-uid",
                str(uids["writer"]),
                "--socket-path",
                self.data["socket"],
                "--profile-path",
                self.data["profile"],
            ]
        )
        descriptor_root = Path("/etc/magicite/registries")
        descriptor_root.mkdir(parents=True, exist_ok=True, mode=0o755)
        descriptor_path = descriptor_root / (
            hashlib.sha256(str(project.resolve()).encode()).hexdigest() + ".json"
        )
        fd = os.open(descriptor_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        self.descriptor = descriptor_path
        with os.fdopen(fd, "w") as stream:
            stream.write(descriptor)  # exact production CLI stdout, never an injected enrollment
        self.data["descriptor"] = str(self.descriptor)
        self.path.write_text(json.dumps(self.data))
        target = project / ".magicite/engrams"
        target.mkdir(parents=True)
        shutil.copyfile(
            ROOT / "tests/fixtures/toy-registry/engrams/steam-runtime-repair.egr.md",
            target / "steam-runtime-repair.egr.md",
        )
        for entry in project.rglob("*"):
            os.chown(entry, uids["writer"], uids["writer"])
        self.start()
        self.run("initialize")

    def environment(self):
        return {**os.environ, "PYTHONPATH": str(ROOT / "src"), "MAGICITE_EMBEDDING_PROVIDER": "hashing"}

    def command(self, operation: str):
        return [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            operation,
            "--fixture",
            str(self.path),
        ]

    def spawn(self, operation, *, role="writer", **kwargs):
        uid = self.uids[role]
        return subprocess.Popen(
            self.command(operation),
            user=uid,
            group=uid,
            extra_groups=[],
            cwd=ROOT,
            env=self.environment(),
            **kwargs,
        )

    def cli(self, arguments):
        uid = self.uids["custodian"]
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--custody-cli", json.dumps(arguments)],
            cwd=ROOT,
            env=self.environment(),
            user=uid,
            group=uid,
            extra_groups=[],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode:
            layout = [
                path_metadata(Path(sys.executable), "runtime-interpreter"),
                path_metadata(ROOT, "runtime-source"),
                path_metadata(ROOT / "src", "runtime-modules"),
                path_metadata(Path("/opt"), "opt-ancestor"),
                path_metadata(Path("/var/lib"), "var-lib-ancestor"),
                path_metadata(Path(self.data["private"]).parent, "custody-parent"),
            ]
            raise ChildCommandFailure(arguments[0], result, layout)
        return result.stdout

    def run(self, operation, *, role="writer"):
        proc = self.spawn(operation, role=role, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            stdout, _stderr = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate(timeout=10)
            raise RuntimeError(f"worker {operation} timed out") from None
        if proc.returncode:
            raise RuntimeError(f"worker {operation} failed (exit {proc.returncode})")
        value = json.loads(stdout)
        assert value["uid"] == self.uids[role] and value["groups"] == []
        return value

    def start(self):
        self.process = self.spawn(
            "serve", role="custodian", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        deadline = time.monotonic() + 10
        while not Path(self.data["socket"]).is_socket():
            if self.process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("production custodian service failed to start")
            select.select([], [], [], 0.02)

    def kill_service(self):
        assert self.process is not None
        pid = self.process.pid
        self.process.kill()
        assert self.process.wait(timeout=10) == -signal.SIGKILL
        socket_path = Path(self.data["socket"])
        info = socket_path.lstat()
        assert stat.S_ISSOCK(info.st_mode) and info.st_uid == self.uids["custodian"]
        socket_path.unlink()  # explicit fixture operator cleanup, only after observed process exit
        return {"pid": pid, "signal": "SIGKILL", "stale_socket_operator_cleanup": True}

    def kill_client(self, operation):
        read_fd, write_fd = os.pipe()
        uid = self.uids["writer"]
        proc = subprocess.Popen(
            self.command(operation) + ["--barrier-fd", str(write_fd)],
            user=uid,
            group=uid,
            extra_groups=[],
            cwd=ROOT,
            env=self.environment(),
            pass_fds=(write_fd,),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        os.close(write_fd)
        try:
            if not select.select([read_fd], [], [], 15)[0]:
                raise RuntimeError("client did not reach observed persistence barrier")
            with os.fdopen(read_fd, "rb") as stream:
                barrier = json.loads(stream.readline())
            assert barrier["pid"] == proc.pid and barrier["uid"] == uid
            proc.kill()
            assert proc.wait(timeout=10) == -signal.SIGKILL
            return {**barrier, "signal": "SIGKILL"}
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)

    def close(self):
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=10)
        if self.descriptor is not None:
            self.descriptor.unlink(missing_ok=True)


def run(output: Path, candidate: str, source_dirty: bool) -> int:
    output.mkdir(parents=True, exist_ok=True)
    try:
        preflight()
        identity = source_identity(candidate, source_dirty)
    except (RuntimeError, ValueError, subprocess.CalledProcessError, OSError) as exc:
        target = output / "report.json"
        target.write_text(
            json.dumps(
                {
                    "status": "FAIL",
                    "source_commit": candidate,
                    "cases": [],
                    "failure_stage": "preflight",
                    "failure_type": type(exc).__name__,
                }
            )
            + "\n"
        )
        (output / "artifacts.json").write_text(json.dumps({"report.json": digest(target)}) + "\n")
        return 1
    begin = time.monotonic()
    base = Path(tempfile.mkdtemp(prefix="magicite-cq-", dir="/opt"))
    os.chmod(base, 0o755)
    uids = {"custodian": 41001, "writer": 41002, "foreign": 41003}
    report: dict[str, Any] = {
        "schema": "magicite/linux-custody-qualification/1",
        "source_commit": candidate,
        "source_dirty": source_dirty,
        "status": "FAIL",
        "command": sys.argv,
        "platform": {
            "system": platform.system(),
            "kernel": platform.release(),
            "machine": platform.machine(),
            "python": sys.version,
        },
        "uids": uids,
        "cases": [],
        "kernel_peers": [],
        "source_hashes": source_hashes(),
        "source_identity": identity,
        "started_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "limits": [
            "Linux selected cases only; macOS separate UID UNEVALUATED",
            "No exhaustive crash or ACL matrix",
            "No whole release gate, human risk acceptance or Gauge acceptance",
            "Operator removes fixture stale socket",
        ],
    }
    report["source_hashes"][str(Path(__file__).resolve().relative_to(ROOT))] = digest(Path(__file__))
    fixtures: list[Fixture] = []

    def case(identity, evidence):
        report["cases"].append({"id": identity, "status": "PASS", "evidence": evidence})

    try:
        main = Fixture(base, "boundary", uids, fixtures)
        case("preflight", main.run("preflight-negative"))
        positive = main.run("access")
        assert positive["allowed"] and positive["body_present"]
        case("production-workflow", positive)
        before = main.run("head")["head"]
        foreign = main.run("foreign", role="foreign")
        assert foreign["rejected"] and main.run("head")["head"] == before
        case("foreign-peer", {**foreign, "head_before": before, "head_after": main.run("head")["head"]})
        case("same-uid", main.run("same-uid"))
        protected = [
            Path(main.data["private"]) / name for name in ("journal.key", "signing.key", "authority.sqlite")
        ]
        protected += [Path(main.data["profile"]), Path(main.data["descriptor"])]
        canaries = [digest(path) for path in protected]
        denied = main.run("permissions")
        assert len(denied["denied"]) == 5 and canaries == [digest(path) for path in protected]
        case("protected-permissions", {**denied, "bytes_unchanged": True})
        for operation, identity in (
            ("prepare-death", "prepared-client-death"),
            ("commit-death", "committed-client-death"),
        ):
            item = Fixture(base, operation, uids, fixtures)
            initial_head = item.run("head")["head"]
            barrier = item.kill_client(operation)
            closed_before = item.run("access")
            denied_body(closed_before)
            reconciliation = item.run("reconcile")
            record_id = barrier["record"]["record_id"]
            if operation == "commit-death":
                retry_path = Path(item.data["project"]) / "retry-record.json"
                retry_path.write_text(json.dumps(barrier["record"]))
                os.chown(retry_path, uids["writer"], uids["writer"])
                before_retry = item.run("head")["head"]
                item.run("retry")
                after_retry = item.run("head")["head"]
                assert after_retry["head_sequence"] == before_retry["head_sequence"]
            history = item.run("history")
            effect_count = history["record_ids"].count(record_id)
            assert effect_count == 1
            access = item.run("access")
            denied_body(access)
            case(
                identity,
                {
                    **{key: value for key, value in barrier.items() if key != "record"},
                    "record_id": record_id,
                    "barrier_sequence": barrier["record"]["sequence"],
                    "head_before": initial_head,
                    "head_after": history["head"],
                    "reconciliation": reconciliation,
                    "effect_count": effect_count,
                    "closed_before_reconciliation": closed_before,
                    "access": access,
                },
            )
        revoke = main.run("revoke")
        before = main.run("head")["head"]
        death = main.kill_service()
        main.start()
        restarted_head = main.run("head")["head"]
        assert restarted_head == before
        access = main.run("access")
        denied_body(access)
        case(
            "acknowledged-service-restart",
            {
                **death,
                "decision_id": revoke["decision_id"],
                "restarted_pid": main.process.pid,
                "head_before": before,
                "head_after": restarted_head,
                "access": access,
            },
        )
        restored = main.run("restore")
        access = main.run("access")
        denied_body(access)
        case("restore-retained-suffix", {**restored, "head": main.run("head")["head"], "access": access})
        main.run("policy-after-revoke")
        anchor = main.run("head")["head"]
        conn = sqlite3.connect(Path(main.data["private"]) / "authority.sqlite")
        try:
            with conn:
                removed = conn.execute(
                    "DELETE FROM record WHERE registry=? AND record_id=?",
                    (main.data["registry"], revoke["decision_id"]),
                ).rowcount
            assert removed == 1
        finally:
            conn.close()
        assert protected_head(main.run("head")["head"]) == protected_head(anchor)
        negative = main.run("restore-negative")
        assert negative["restricted"]
        access = main.run("access")
        denied_body(access)
        assert protected_head(main.run("head")["head"]) == protected_head(anchor)
        case(
            "restore-missing-suffix",
            {
                **negative,
                "protected_head_preserved": True,
                "removed_records": removed,
                "head_before": anchor,
                "head_after": main.run("head")["head"],
                "access": access,
            },
        )
        for fixture in fixtures:
            report["kernel_peers"].extend(
                json.loads(line) for line in Path(fixture.data["peers"]).read_text().splitlines()
            )
        assert any(row["peer_uid"] == uids["foreign"] for row in report["kernel_peers"])
        assert all(row["server_uid"] == uids["custodian"] for row in report["kernel_peers"])
        report["status"] = "PASS"
        validate_report(report, candidate, allow_dirty=source_dirty)
    except Exception as exc:
        report["failure_type"] = type(exc).__name__
        if isinstance(exc, ChildCommandFailure):
            report["failure_context"] = exc.detail
        report["failure_line"] = traceback.extract_tb(exc.__traceback__)[-1].lineno
        report["completed_cases"] = len(report["cases"])
        report["status"] = "FAIL"
    finally:
        failures = []
        for fixture in fixtures:
            try:
                fixture.close()
            except Exception as exc:
                failures.append(type(exc).__name__)
        try:
            shutil.rmtree(base)
        except OSError as exc:
            failures.append(type(exc).__name__)
        if failures:
            report["status"] = "FAIL"
        report["cleanup_failures"] = failures
        report["finished_at"] = datetime.datetime.now(datetime.UTC).isoformat()
        report["seconds"] = round(time.monotonic() - begin, 3)
        target = output / "report.json"
        target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        (output / "artifacts.json").write_text(json.dumps({"report.json": digest(target)}, indent=2) + "\n")
    emit({"status": report["status"], "cases": len(report["cases"]), "source_commit": candidate})
    return int(report["status"] != "PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--candidate")
    parser.add_argument("--source-dirty", action="store_true")
    parser.add_argument("--worker")
    parser.add_argument("--custody-cli")
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--barrier-fd", type=int)
    parser.add_argument("--verify-report", type=Path)
    args = parser.parse_args()
    if args.custody_cli:
        return custody_cli(json.loads(args.custody_cli))
    if args.worker:
        worker(args.worker, args.fixture, args.barrier_fd)
        return 0
    if args.verify_report:
        validate_report(
            json.loads(args.verify_report.read_text()), args.candidate, allow_dirty=args.source_dirty
        )
        return 0
    if not args.output or not args.candidate:
        parser.error("--output and --candidate required")
    return run(args.output, args.candidate, args.source_dirty)


if __name__ == "__main__":
    raise SystemExit(main())
