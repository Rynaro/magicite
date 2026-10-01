"""Explicit legacy inventory and reviewed backup; no implicit enrollment.

Unsigned history cannot prove absence of deleted revocations. These operator
inputs describe observed current state and never authorize legacy admissions.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from magicite.config import Config
from magicite.core import migration, trust_artifacts
from magicite.core.trust_custodian import CustodianError, CustodianStore
from magicite.core.trust_journal import _directory_fd, _read_file


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def logical_db_digest(conn: sqlite3.Connection) -> str:
    """Bind schema and all logical rows except the named operational lease row."""
    objects = [
        tuple(row)
        for row in conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")
    ]
    contents = []
    for kind, name, _, _ in objects:
        if kind != "table" or name == "writer_lease":
            continue
        quoted = '"' + name.replace('"', '""') + '"'
        rows = []
        for row in conn.execute("SELECT * FROM " + quoted):
            rows.append(
                [
                    {"blob": base64.b64encode(value).decode()} if isinstance(value, bytes) else value
                    for value in row
                ]
            )
        contents.append([name, sorted(rows, key=canonical)])
    return digest(
        {
            "schema_version": conn.execute("PRAGMA user_version").fetchone()[0],
            "objects": objects,
            "tables": contents,
            "excluded_rows": ["writer_lease"],
        }
    )


def _files(cfg: Config) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    if not cfg.data_dir.is_dir() or cfg.data_dir.is_symlink():
        raise CustodianError("existing legacy data directory required")
    physical_db = {str(cfg.db_path.relative_to(cfg.data_dir)) + suffix for suffix in ("", "-wal", "-shm")}
    operational = {str(cfg.dream_lock_path.relative_to(cfg.data_dir))}
    files, provenance = {}, {}
    for root, directories, names in os.walk(cfg.data_dir, followlinks=False):
        for name in directories:
            if (Path(root) / name).is_symlink():
                raise CustodianError("legacy inventory contains symbolic directory")
        for name in sorted(names):
            path = Path(root) / name
            rel = path.relative_to(cfg.data_dir).as_posix()
            if rel in operational:
                continue
            with _directory_fd(path.parent) as directory:
                raw = _read_file(directory, name)
            sha = hashlib.sha256(raw).hexdigest()
            if rel in physical_db:
                provenance[rel] = sha
            else:
                files[rel] = {"sha256": sha, "size": len(raw)}
    return dict(sorted(files.items())), dict(sorted(provenance.items()))


def _read(cfg: Config, rel: str) -> bytes:
    path = cfg.data_dir / rel
    if ".." in Path(rel).parts or Path(rel).is_absolute():
        raise CustodianError("invalid legacy relative path")
    with _directory_fd(path.parent) as directory:
        return _read_file(directory, path.name)


def _preview(
    cfg: Config, *, registry_id: str, actor: str, conn: sqlite3.Connection, prepared_at: str | None = None
) -> dict[str, Any]:
    from datetime import UTC, datetime

    if prepared_at is None:
        prepared_at = datetime.now(UTC).isoformat()
    if not registry_id or not actor:
        raise CustodianError("explicit protected identity and reviewer required")
    files, physical_db = _files(cfg)
    if any(rel.startswith("trust/authority/") for rel in files):
        raise CustodianError("existing authenticated history requires migration resume")
    try:
        policy = json.loads(_read(cfg, "trust/policy.json"))
        CustodianStore._validate_payload("policy_snapshot", policy)
    except (OSError, ValueError, KeyError) as exc:
        raise CustodianError("explicit legacy policy reconciliation required") from exc
    decisions: dict[str, dict[str, Any]] = {}
    for rel in files:
        if rel.startswith("trust/decisions/") and rel.endswith(".json"):
            payload = json.loads(_read(cfg, rel))
            CustodianStore._validate_payload("trust_decision", payload)
            identity = payload["decision_id"]
            if identity in decisions and canonical(decisions[identity]) != canonical(payload):
                raise CustodianError("conflicting legacy decision identity")
            decisions[identity] = payload
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "writer_lease" not in tables:
        raise CustodianError("legacy writer lease prerequisite unavailable")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(writer_lease)")}
    if not {"id", "holder", "fencing_token", "acquired_at", "heartbeat_at", "expires_at"} <= columns:
        raise CustodianError("unsupported legacy writer lease schema")
    if "trust_decision" in tables:
        for row in conn.execute("SELECT decision_id,engram_id,content_digest,decision FROM trust_decision"):
            mirrored = decisions.get(row[0])
            if mirrored is None or any(
                mirrored[field] != row[index]
                for index, field in enumerate(("decision_id", "engram_id", "content_digest", "decision"))
            ):
                raise CustodianError("legacy projection/mirror conflict requires reconciliation")
    artifacts = []
    parsed = {}
    registry_prefix = cfg.registry_dir.relative_to(cfg.data_dir).as_posix() + "/"
    for rel in files:
        if rel.startswith(registry_prefix) and rel.endswith(".egr.md"):
            artifact, _ = trust_artifacts.load_registry_artifact(cfg, cfg.data_dir / rel)
            if artifact.id in parsed:
                raise CustodianError("duplicate legacy artifact identity")
            parsed[artifact.id] = (rel, artifact)
    revisions = {identity: int(artifact.frontmatter.version) for identity, (_, artifact) in parsed.items()}
    for identity, (rel, _) in sorted(parsed.items()):
        marked = trust_artifacts.mark_artifact(
            _read(cfg, rel),
            registry_id=registry_id,
            relpath=str((cfg.data_dir / rel).relative_to(cfg.project_root)),
            actor=actor,
            revision_map=revisions,
        )
        artifacts.append(
            {
                "path": rel,
                "engram_id": identity,
                "source_digest": marked.lineage["source_digest"],
                "target_digest": marked.lineage["target_digest"],
                "resources": marked.lineage["resources"],
            }
        )
    return {
        "schema": "LegacyTrustMigration/1",
        "prepared_at": prepared_at,
        "registry_id": registry_id,
        "actor": actor,
        "historical_provenance": "unsigned-incomplete-operator-reconciliation-required",
        "legacy_admissions": "archived-only; exact transformed target requires separate review",
        "files": files,
        "physical_db_provenance": physical_db,
        "logical_db_digest": logical_db_digest(conn),
        "excluded_operational_rows": ["writer_lease"],
        "excluded_operational_files": [str(cfg.dream_lock_path.relative_to(cfg.data_dir))],
        "policy": policy,
        "decisions": [decisions[key] for key in sorted(decisions)],
        "artifacts": artifacts,
        "revision_map": revisions,
    }


def preview(cfg: Config, *, registry_id: str, actor: str) -> dict[str, Any]:
    """Zero writes to the legacy tree, including WAL-aware database inspection."""
    conn, _, owns, temporary = migration._connect_preview_readonly(cfg)
    if conn is None:
        raise CustodianError("existing legacy database required")
    try:
        return _preview(cfg, registry_id=registry_id, actor=actor, conn=conn)
    finally:
        if owns:
            conn.close()
        if temporary is not None:
            shutil.rmtree(temporary)


def _same_reviewed_state(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    # Physical DB/WAL bytes change when the existing operational lease is taken.
    return canonical({k: v for k, v in expected.items() if k != "physical_db_provenance"}) == canonical(
        {k: v for k, v in actual.items() if k != "physical_db_provenance"}
    )


def _write_new_or_exact(path: Path, content: bytes, guard: Any) -> None:
    from magicite.core.trust_journal import _replace_file

    guard()
    with _directory_fd(path.parent, create=True) as directory:
        try:
            old = _read_file(directory, path.name)
        except FileNotFoundError:
            _replace_file(directory, path.name, content, guard)
        else:
            if old != content:
                raise CustodianError("existing backup artifact conflicts with reviewed input")
    guard()


def _complete_backup(
    cfg: Config,
    conn: sqlite3.Connection,
    plan: dict[str, Any],
    destination: Path,
    held: Any,
    *,
    encrypted_custody: dict[str, Any] | None = None,
    fault_hook: Any = None,
) -> dict[str, Any]:
    import tempfile

    from magicite.core.backup import _is_secret_rel

    if destination.resolve().is_relative_to(cfg.data_dir.resolve()):
        raise CustodianError("legacy backup must be outside the managed source tree")
    secrets = {rel: meta["sha256"] for rel, meta in plan["files"].items() if _is_secret_rel(rel)}
    custody = None
    if secrets:
        if (
            not isinstance(encrypted_custody, dict)
            or set(encrypted_custody) != {"schema", "path", "sha256", "source_sha256"}
            or encrypted_custody["schema"] != "OperatorEncryptedCustody/1"
            or encrypted_custody["source_sha256"] != secrets
        ):
            raise CustodianError("existing control keys require reviewed encrypted custody reference")
        encrypted = Path(encrypted_custody["path"])
        with _directory_fd(encrypted.parent) as directory:
            encrypted_bytes = _read_file(directory, encrypted.name)
        if hashlib.sha256(encrypted_bytes).hexdigest() != encrypted_custody["sha256"]:
            raise CustodianError("encrypted custody reference changed")
        custody = {key: value for key, value in encrypted_custody.items() if key != "path"}
    if destination.exists() and not (destination / "reviewed-plan.json").is_file():
        raise CustodianError("backup destination already exists without this reviewed plan")
    _write_new_or_exact(destination / "reviewed-plan.json", canonical(plan), held.assert_owned)
    entries = {}
    for rel, meta in plan["files"].items():
        if rel in secrets:
            continue
        raw = _read(cfg, rel)
        if hashlib.sha256(raw).hexdigest() != meta["sha256"] or len(raw) != meta["size"]:
            raise CustodianError("legacy source changed during backup")
        _write_new_or_exact(destination / "files" / rel, raw, held.assert_owned)
        entries[rel] = meta
        if fault_hook:
            fault_hook("backup_file")
    # SQLite's backup API includes committed WAL content without checkpointing
    # or migrating the source DB. Runtime lease rows are not recovery authority.
    held.assert_owned()
    with tempfile.TemporaryDirectory(prefix="magicite-legacy-db-") as temporary:
        db_copy = Path(temporary) / "snapshot.sqlite"
        target = sqlite3.connect(db_copy)
        try:
            conn.backup(target)
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise CustodianError("legacy database backup failed validation")
            if logical_db_digest(target) != plan["logical_db_digest"]:
                raise CustodianError("legacy database changed during backup")
        finally:
            target.close()
        raw = db_copy.read_bytes()
    rel = cfg.db_path.relative_to(cfg.data_dir).as_posix()
    path = destination / "files" / rel
    # Resumed snapshots can differ only in excluded lease row bytes. Reuse an
    # existing validated logical snapshot instead of overwriting its originals.
    if path.exists():
        previous = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
        try:
            if logical_db_digest(previous) != plan["logical_db_digest"]:
                raise CustodianError("conflicting legacy database backup")
        finally:
            previous.close()
        with _directory_fd(path.parent) as directory:
            raw = _read_file(directory, path.name)
    else:
        _write_new_or_exact(path, raw, held.assert_owned)
    entries[rel] = {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
    actual = _preview(
        cfg, registry_id=plan["registry_id"], actor=plan["actor"], conn=conn, prepared_at=plan["prepared_at"]
    )
    if not _same_reviewed_state(plan, actual):
        raise CustodianError("legacy inventory drift during backup")
    manifest = {
        "schema": "LegacyTrustBackup/1",
        "reviewed_plan_digest": digest(plan),
        "files": entries,
        "logical_db_digest": plan["logical_db_digest"],
        "encrypted_custody": custody,
        "excluded_operational_rows": ["writer_lease"],
        "custodian_keys": "separate protected custody; not included",
    }
    _write_new_or_exact(destination / "manifest.json", canonical(manifest), held.assert_owned)
    if fault_hook:
        fault_hook("before_backup_complete")
    _write_new_or_exact(
        destination / "complete.json", canonical({"manifest_digest": digest(manifest)}), held.assert_owned
    )
    return {**manifest, "complete": True, "manifest_digest": digest(manifest)}


def backup_reviewed(
    cfg: Config,
    *,
    plan: dict[str, Any],
    reviewed_sha256: str,
    destination: Path,
    encrypted_custody: dict[str, Any] | None = None,
    fault_hook: Any = None,
) -> dict[str, Any]:
    """Explicit complete backup under the existing lease, before trust mutation."""
    from magicite.core import writer_guard
    from magicite.core.trust_journal import TrustJournal

    if digest(plan) != reviewed_sha256 or plan.get("schema") != "LegacyTrustMigration/1":
        raise CustodianError("reviewed legacy manifest changed")
    registry_id, client = writer_guard.resolve_custody(cfg)
    if registry_id != plan["registry_id"]:
        raise CustodianError("reviewed enrollment mismatch")
    journal = TrustJournal(cfg.data_dir / "trust/authority", registry_id, client)
    head, records = journal._remote()
    if head["head_sequence"] != 1 or canonical(records[0]["payload"]["policy"]) != canonical(plan["policy"]):
        raise CustodianError("reviewed legacy policy requires explicit protected genesis")
    if not cfg.db_path.is_file() or cfg.db_path.is_symlink():
        raise CustodianError("existing legacy database required")
    conn = sqlite3.connect(
        cfg.db_path.resolve().as_uri() + "?mode=rw", uri=True, isolation_level=None, check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    try:
        actual = _preview(
            cfg, registry_id=registry_id, actor=plan["actor"], conn=conn, prepared_at=plan["prepared_at"]
        )
        if not _same_reviewed_state(plan, actual):
            raise CustodianError("reviewed legacy inputs changed")
        held = writer_guard.registry_writer_lease(cfg, conn)
        with held.acquire():
            actual = _preview(
                cfg, registry_id=registry_id, actor=plan["actor"], conn=conn, prepared_at=plan["prepared_at"]
            )
            if not _same_reviewed_state(plan, actual):
                raise CustodianError("reviewed legacy inputs changed under lease")
            return _complete_backup(
                cfg, conn, plan, destination, held, encrypted_custody=encrypted_custody, fault_hook=fault_hook
            )
    finally:
        conn.close()


def read_verified_backup(destination: Path, *, reviewed_sha256: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """An incomplete or altered archive can never become migration input."""

    def read(path: Path) -> bytes:
        with _directory_fd(path.parent) as directory:
            return _read_file(directory, path.name)

    try:
        plan = json.loads(read(destination / "reviewed-plan.json"))
        manifest = json.loads(read(destination / "manifest.json"))
        complete = json.loads(read(destination / "complete.json"))
        if (
            plan["schema"] != "LegacyTrustMigration/1"
            or digest(plan) != reviewed_sha256
            or manifest["schema"] != "LegacyTrustBackup/1"
            or manifest["reviewed_plan_digest"] != reviewed_sha256
            or complete != {"manifest_digest": digest(manifest)}
        ):
            raise CustodianError("legacy backup commitment mismatch")
        from magicite.core.backup import _is_secret_rel

        expected = {rel: value for rel, value in plan["files"].items() if not _is_secret_rel(rel)}
        db_paths = [rel for rel in plan["physical_db_provenance"] if not rel.endswith(("-wal", "-shm"))]
        if len(db_paths) != 1 or set(manifest["files"]) != set(expected) | set(db_paths):
            raise CustodianError("incomplete legacy backup inventory")
        for rel, meta in manifest["files"].items():
            if Path(rel).is_absolute() or ".." in Path(rel).parts:
                raise CustodianError("invalid backup member path")
            if rel in expected and meta != expected[rel]:
                raise CustodianError("legacy source commitment mismatch")
            raw = read(destination / "files" / rel)
            if meta != {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}:
                raise CustodianError("legacy backup member changed")
        for item in plan["artifacts"]:
            rel = item["path"]
            if (
                not isinstance(rel, str)
                or rel not in expected
                or not rel.endswith(".egr.md")
                or item["source_digest"] != expected[rel]["sha256"]
            ):
                raise CustodianError("invalid reviewed artifact source binding")
        db_path = destination / "files" / db_paths[0]
        conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
        try:
            if (
                conn.execute("PRAGMA quick_check").fetchone()[0] != "ok"
                or logical_db_digest(conn) != plan["logical_db_digest"]
            ):
                raise CustodianError("legacy database backup changed")
        finally:
            conn.close()
        return plan, manifest
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        raise CustodianError("complete reviewed legacy backup required") from exc


def _migration_records(
    plan: dict[str, Any], backup_manifest: dict[str, Any], backup_path: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from magicite.core.trust import TrustDecision, TrustPolicy
    from magicite.engram.digests import assets_manifest_digest

    migration_id = digest(plan)[:32]
    policy = TrustPolicy.from_dict(plan["policy"])
    records, marked_by_path, pending = [], {}, []
    for item in plan["artifacts"]:
        path = backup_path / "files" / item["path"]
        with _directory_fd(path.parent) as directory:
            source = _read_file(directory, path.name)
        originals = [row for row in plan["decisions"] if row["engram_id"] == item["engram_id"]]
        signers = sorted({row["signer_fingerprint"] for row in originals if row.get("signer_fingerprint")})
        signature = (
            {
                "signature_valid": False,
                "signer_fingerprint": signers[0],
                "historical_provenance": "unsigned-operator-reviewed",
            }
            if signers
            else None
        )
        marked = trust_artifacts.mark_artifact(
            source,
            registry_id=plan["registry_id"],
            relpath=item["path"],
            actor=plan["actor"],
            revision_map=plan["revision_map"],
            source_signature=signature,
        )
        if any(marked.lineage[key] != item[key] for key in ("source_digest", "target_digest", "resources")):
            raise CustodianError("reviewed transform changed")
        if originals:
            marked.lineage["legacy_provenance"] = {
                "manifest_digest": digest(plan),
                "backup_digest": digest(backup_manifest),
                "original_decisions": originals,
            }
        trust_artifacts.validate_transform_lineage(marked.lineage)
        marked_by_path[item["path"]] = marked
        records.append(
            {
                "record_id": "transform-" + digest(marked.lineage),
                "kind": "artifact_transform",
                "payload": marked.lineage,
            }
        )
        identity = "legacy-pending-" + digest([migration_id, item["engram_id"], item["target_digest"]])
        payload = TrustDecision(
            decision_id=identity,
            engram_id=item["engram_id"],
            content_digest=item["target_digest"],
            resource_digest=assets_manifest_digest(item["resources"]),
            decision="pending",
            source_channel="local_register",
            policy_id=policy.policy_id,
            policy_revision=policy.revision,
            policy_digest=policy.digest(),
            scanner_revision=policy.scanner_revision,
            actor=plan["actor"],
            timestamp=plan["prepared_at"],
            reasons=("reviewed legacy transform; unsigned history; separate exact-target review required",),
        ).to_dict()
        pending.append({"record_id": identity, "kind": "trust_decision", "payload": payload})
    records.extend(pending)
    records.extend(
        {"record_id": row["decision_id"], "kind": "trust_decision", "payload": row}
        for row in plan["decisions"]
        if row["decision"] in {"reject", "revoke", "quarantine"}
    )
    return records, marked_by_path


def _validate_live_migration(cfg: Config, conn: sqlite3.Connection, plan: dict[str, Any]) -> None:
    actual, _ = _files(cfg)
    targets = {item["path"]: item["target_digest"] for item in plan["artifacts"]}
    sources = {"trust/sources/" + item["source_digest"]: item["source_digest"] for item in plan["artifacts"]}
    controls = {"trust/authority/journal.jsonl", "trust/authority/head.json"}
    for rel, expected in plan["files"].items():
        if rel not in actual:
            raise CustodianError("reviewed legacy source is missing")
        if actual[rel] != expected and actual[rel]["sha256"] != targets.get(rel):
            raise CustodianError("unreviewed legacy source change")
    for rel, meta in actual.items():
        if rel in plan["files"] or rel in controls:
            continue
        if meta["sha256"] != sources.get(rel):
            raise CustodianError("unreviewed additional legacy file")
    if logical_db_digest(conn) != plan["logical_db_digest"]:
        raise CustodianError("legacy business state changed; reconciliation required")


def apply_reviewed(
    cfg: Config, *, backup_path: Path, reviewed_sha256: str, fault_hook: Any = None
) -> dict[str, Any]:
    """Apply/resume exactly reviewed transformations; never import admission."""
    from magicite.core import writer_guard
    from magicite.core.trust_journal import TrustJournal, _replace_file

    plan, backup_manifest = read_verified_backup(backup_path, reviewed_sha256=reviewed_sha256)
    records, marked = _migration_records(plan, backup_manifest, backup_path)
    registry_id, client = writer_guard.resolve_custody(cfg)
    if registry_id != plan["registry_id"]:
        raise CustodianError("reviewed enrollment mismatch")
    migration_id = reviewed_sha256[:32]
    journal = TrustJournal(
        cfg.data_dir / "trust/authority", registry_id, client, reconciliation_id=migration_id
    )
    head, history = journal._remote(allow_pending=True)
    if canonical(history[0]["payload"]["policy"]) != canonical(plan["policy"]):
        raise CustodianError("reviewed protected policy mismatch")
    begin = {
        "schema": "LegacyReconciliation/1",
        "phase": "BEGIN",
        "migration_id": migration_id,
        "registry_id": registry_id,
        "epoch": head["epoch"],
        "manifest_digest": reviewed_sha256,
        "backup_digest": digest(backup_manifest),
        "expected_records": [
            {"record_id": row["record_id"], "kind": row["kind"], "payload_digest": digest(row["payload"])}
            for row in records
        ],
        "targets": [
            {
                "engram_id": row["engram_id"],
                "target_digest": row["target_digest"],
                "resources_digest": digest(row["resources"]),
            }
            for row in plan["artifacts"]
        ],
    }
    begin_id = "legacy-" + migration_id + "-begin"
    existing = next((row for row in history if row["record_id"] == begin_id), None)
    if (existing is None and head["head_sequence"] != 1) or (
        existing is not None and canonical(existing["payload"]) != canonical(begin)
    ):
        raise CustodianError("protected history belongs to a different migration")
    if not cfg.db_path.is_file() or cfg.db_path.is_symlink():
        raise CustodianError("existing migration database required")
    conn = sqlite3.connect(
        cfg.db_path.resolve().as_uri() + "?mode=rw", uri=True, isolation_level=None, check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    try:
        _validate_live_migration(cfg, conn, plan)
        held = writer_guard.registry_writer_lease(cfg, conn)
        with held.acquire():
            _validate_live_migration(cfg, conn, plan)
            _, _, fence = writer_guard.bound_journal(cfg)
            if not journal.directory.exists() and head["head_sequence"] == 1:
                journal.initialize_reviewed_genesis(assert_owned=held.assert_owned)
            else:
                journal.reconcile(fence=fence, assert_owned=held.assert_owned)
            journal.append(
                record_id=begin_id,
                kind="legacy_reconciliation",
                payload=begin,
                fence=fence,
                assert_owned=held.assert_owned,
            )
            if fault_hook:
                fault_hook("begin_committed")
            by_digest = {value.lineage["target_digest"]: (rel, value) for rel, value in marked.items()}
            for record in records:
                journal.append(**record, fence=fence, assert_owned=held.assert_owned)
                if fault_hook:
                    fault_hook("record_committed:" + record["kind"])
                if record["kind"] == "artifact_transform":
                    rel, value = by_digest[record["payload"]["target_digest"]]
                    _write_new_or_exact(
                        cfg.data_dir / "trust/sources" / value.lineage["source_digest"],
                        value.source,
                        held.assert_owned,
                    )
                    target = cfg.data_dir / rel
                    with _directory_fd(target.parent) as directory:
                        current = _read_file(directory, target.name)
                        if hashlib.sha256(current).hexdigest() not in {
                            value.lineage["source_digest"],
                            value.lineage["target_digest"],
                        }:
                            raise CustodianError("legacy target changed during publication")
                        if current != value.target:
                            _replace_file(directory, target.name, value.target, held.assert_owned)
                    held.assert_owned()
                    if fault_hook:
                        fault_hook("target_published")
            _validate_live_migration(cfg, conn, plan)
            for item in plan["artifacts"]:
                artifact, _ = trust_artifacts.load_registry_artifact(cfg, cfg.data_dir / item["path"])
                if artifact.id != item["engram_id"] or artifact.content_sha256 != item["target_digest"]:
                    raise CustodianError("published migration target mismatch")
            if fault_hook:
                fault_hook("before_complete")
            complete = {
                **{key: value for key, value in begin.items() if key not in {"expected_records", "targets"}},
                "phase": "COMPLETE",
            }
            journal.append(
                record_id="legacy-" + migration_id + "-complete",
                kind="legacy_reconciliation",
                payload=complete,
                fence=fence,
                assert_owned=held.assert_owned,
            )
            if fault_hook:
                fault_hook("complete_committed")
            final = journal.snapshot()
            held.assert_owned()
            return {
                "status": "complete_requires_target_review",
                "migration_id": migration_id,
                "manifest_digest": reviewed_sha256,
                "backup_digest": digest(backup_manifest),
                "targets": len(marked),
                "head": final.head,
                "historical_provenance": plan["historical_provenance"],
            }
    finally:
        conn.close()
