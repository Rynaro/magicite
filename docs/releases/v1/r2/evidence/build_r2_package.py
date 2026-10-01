#!/usr/bin/env python3
# ruff: noqa: E501  (long evidence prose strings are data, kept verbatim)
"""Deterministically build the S16 r2 release-evidence package (stdlib only).

Inputs are the immutable raw outputs of the clean-source run at SOURCE (``--raw-dir``)
and the repository tree, whose tracked content must equal SOURCE outside the r2
package. Every digest is computed here; nothing is copied by hand. Re-running with
identical inputs produces byte-identical files.

Review is never fabricated: until an independent checker is recorded by re-running
this builder with ``--reviewer`` and ``--review-status``, every reviewer field is
empty and every review status is PENDING, so the release validator rejects the
witnesses (fail closed). The maker identity is PRODUCER; the reviewer must differ.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

SOURCE = "9bf9ca025fc4513592f72d7f6f1c04563a31bc8c"
HISTORICAL_SOURCE = "c0782fd5ea477857b7fe6e7a967a9bbf85808620"
HISTORICAL_REVIEWED = "52f7bba54eaf9e94acd6408ddaaa93fd144b5183"
HARDENING_BASE = "22ae4e01acde9e99e64c5a6c6fa0bcb6a625684c"
PRODUCER = "Vivi /s16-readjudication r2 maker"
V1 = "docs/releases/v1"
R2 = "docs/releases/v1/r2"
EV = R2 + "/evidence"
ACCEPTANCE = ".spectra/plans/magicite-v1/acceptance.md"
ADDITIVE = ".spectra/plans/magicite-v1-trust-hardening/acceptance.md"
HARDENING = ".spectra/plans/magicite-v1-trust-hardening"
PATH_PREFIX = "/tmp/uvtool/bin"
REVIEW_STATUSES = ("PENDING", "APPROVED", "CHANGES_REQUESTED")
PASS_REASON = (
    "All mapped frozen mechanical obligations have executed witnesses at the r2 source; "
    "this does not qualify empirical or external release gates."
)
SOURCE_SCOPE = (
    "Recorded from a clean tracked and untracked worktree at 9bf9ca0 (git status empty before "
    "and after the full run); r2 files are evidence-only additions. Not a published RC or "
    "external reproduction."
)
ROOT_PROBE_SCOPE = "untracked docs/releases/v1/r2 scripts only; tracked tree identical to source"
STATIC_FILES = {
    "build_r2_package.py",
    "reproduce-trust-mirror-loss-custody.py",
    "probe-checkpoint-process-death-custody.py",
}
WITNESSES = (
    ("POLICY", "nonadaptive-default", "policy-nonadaptive-default.json"),
    ("POLICY", "dream-isolation", "policy-dream-isolation.json"),
    ("POLICY", "explicit-experimental", "policy-explicit-experimental.json"),
    ("POLICY", "pinned-incumbent", "policy-pinned-incumbent.json"),
    ("SCHEMA", "frozen-schema", "schema-frozen-schema.json"),
    ("SCHEMA", "roundtrip-corpus", "schema-roundtrip-corpus.json"),
    ("SCHEMA", "migration-downgrade-replay", "schema-migration-downgrade-replay.json"),
    ("PRIVACY", "data-map", "privacy-data-map.json"),
    ("PRIVACY", "no-default-raw", "privacy-no-default-raw.json"),
    ("PRIVACY", "historical-cleanup", "privacy-historical-cleanup.json"),
    ("PRIVACY", "checkpoint-rpo", "privacy-checkpoint-rpo.json"),
    ("PRIVACY", "lifecycle-tests", "privacy-lifecycle-tests.json"),
    ("LEARNING", "containment", "learning-containment.json"),
    ("LEARNING", "fully-gated-or-unavailable", "learning-fully-gated-or-unavailable.json"),
)
# Disclosed for the checker only; never applied automatically (strict v1 node mapping).
SUCCESSOR_CANDIDATES = {
    "tests/integration/test_backup_restore_v1.py::test_deleted_revoke_mirror_refuses_preserve": [
        "tests/integration/test_backup_restore_v1.py::"
        "test_deleted_revoke_mirror_preserves_authenticated_restriction"
    ],
}
GATE_ORDER = (
    "POLICY",
    "EVIDENCE",
    "SCHEMA",
    "TRUST",
    "ROUTING",
    "COMPOSITION",
    "PRIVACY",
    "LEARNING",
    "PROTOCOL",
    "RELIABILITY",
    "PERFORMANCE",
    "DISTRIBUTION",
    "DOCS",
    "RC-CONTRACT",
    "EXTERNAL",
    "SECURITY",
    "GA-ALL",
)

T = "tests/unit/core/"
JOURNAL, CUSTODIAN = T + "test_trust_journal.py::", T + "test_trust_custodian.py::"
TRANSPORT, GUARD = T + "test_trust_custodian_transport.py::", T + "test_custody_writer_guard.py::"
ADMIN, AUTHORITY = T + "test_custody_admin.py::", T + "test_trust_authority_integration.py::"
ROTATION, ROT_TRANSPORT = T + "test_trust_rotation.py::", T + "test_trust_rotation_transport.py::"
LEGACY, RECON = T + "test_trust_legacy.py::", T + "test_trust_reconciliation.py::"
PAGING = T + "test_trust_history_paging.py::"
BACKUP = "tests/integration/test_backup_restore_v1.py::"
FENCING = "tests/integration/test_policy_fencing.py::"
SERVE = "tests/unit/test_cli_serve_errors.py::"
OPERATOR = "tests/integration/test_operator_cli_completeness.py::"
S = "src/magicite/core/"
DEPLOYMENT_GAP = (
    "Separate-UID Linux/macOS custody deployment qualification: no actual corresponding "
    "distinct-UID deployment run; fixture and same-account custody never qualify."
)
MIRROR_LOSS = "mirror-loss"
FULL_CHECKS = "full-checks"

# AC-TH witness map: (VERIFY sub-clause, [test nodes or evidence keys]); gaps are unwitnessed.
TH: dict[str, dict[str, Any]] = {
    "AC-TH-01": {
        "sources": [S + "trust.py", S + "trust_journal.py", S + "trust_custodian.py", S + "backup.py"],
        "clauses": [
            (
                "Decision history mutation matrix: delete, edit, duplicate, reorder, truncate, whole-local "
                "rollback and head edit/delete close trust-dependent reads.",
                [
                    JOURNAL + "test_local_journal_mutations_never_read_as_current",
                    JOURNAL + "test_whole_local_rollback_against_current_custody_closes",
                    JOURNAL + "test_verified_snapshot_reuse_closes_on_local_change",
                    JOURNAL + "test_same_size_edit_with_restored_mtime_still_closes",
                ],
            ),
            (
                "Policy projection edit/delete and conflicting pending policy mirror never become authority.",
                [
                    AUTHORITY + "test_policy_projection_cannot_reset_authenticated_policy",
                    FENCING + "test_conflicting_pending_mirror_never_repaired_from_untrusted_bytes",
                ],
            ),
            (
                "Revoke-mirror deletion cannot reinstate prior admission.",
                [
                    AUTHORITY + "test_actual_domain_mirror_loss_cannot_resurrect_revoke",
                    BACKUP + "test_deleted_revoke_mirror_preserves_authenticated_restriction",
                    MIRROR_LOSS,
                ],
            ),
            (
                "Missing-policy/default-fallback regressions stay closed.",
                [
                    AUTHORITY + "test_unenrolled_mirror_never_becomes_authority",
                    T
                    + "test_trust_atlas_fixes.py::test_corrupt_authority_fails_closed_while_mirror_is_only_projection",
                ],
            ),
            (
                "Root-revocation rollback regressions.",
                [
                    AUTHORITY + "test_stale_root_pin_cannot_undo_interleaved_acknowledged_revocation",
                    AUTHORITY + "test_actual_route_and_body_deny_after_authenticated_policy_change",
                ],
            ),
        ],
        "gaps": [
            "Spliced local history (a WHEN mutation) has no case in the decision mutation matrix.",
            "Root projection file delete/edit/rollback is not in the policy/root mutation matrix; root "
            "coverage is through authenticated policy state only.",
        ],
    },
    "AC-TH-02": {
        "sources": [S + "trust_custodian.py", S + "trust_journal.py", S + "trust_rotation.py"],
        "clauses": [
            (
                "Exact committed retry has one effect; same ID with changed payload rejected (policy record).",
                [CUSTODIAN + "test_exact_committed_retry_has_one_effect_and_conflict_rejected"],
            ),
            (
                "Resequencing and tampered prepared records cannot advance authority.",
                [
                    CUSTODIAN + "test_prepared_record_tamper_cannot_advance_custodian",
                    CUSTODIAN + "test_old_admission_cannot_be_resequenced_under_new_wrapper_after_revoke",
                ],
            ),
            (
                "Decision lost-reply retry recovers without losing the revoke.",
                [
                    JOURNAL + "test_lost_commit_reply_closes_then_recovers_without_losing_revoke",
                    PAGING + "test_near_limit_preparation_survives_lost_reply",
                ],
            ),
            (
                "Invalid decision payloads cannot be prepared.",
                [CUSTODIAN + "test_invalid_domain_payload_cannot_be_prepared"],
            ),
            (
                "Genesis and epoch kinds cannot be forged through general preparation.",
                [ROTATION + "test_general_preparation_cannot_forge_rotation_or_genesis"],
            ),
            (
                "Epoch record prepare/commit interruption resumes exactly.",
                [
                    ROT_TRANSPORT + "test_client_resumes_fsynced_preparation_after_commit_interruption",
                    ROTATION + "test_rotation_commit_preserves_history_policy_and_requires_exact_finish",
                ],
            ),
        ],
        "gaps": [
            "Same record ID reused with a changed kind is not exercised.",
            "Genesis record prepare/commit/lost-reply exact-retry case is not exercised (genesis is "
            "covered only by non-forgeability and enrollment non-reset), so the retry matrix across "
            "decision, policy, genesis and epoch records is incomplete.",
        ],
    },
    "AC-TH-03": {
        "sources": [
            S + "trust_custodian_transport.py",
            S + "trust_journal.py",
            S + "trust_rotation_client.py",
        ],
        "clauses": [
            (
                "Full-local-state rollback against the current custodian head closes.",
                [JOURNAL + "test_whole_local_rollback_against_current_custody_closes"],
            ),
            (
                "Replayed nonce and registry/epoch/operation/request mismatch receipts are rejected.",
                [TRANSPORT + "test_receipt_requires_fresh_nonce_identity_epoch_and_signature"],
            ),
            (
                "Wrong signing key and non-integer epoch receipts are rejected.",
                [TRANSPORT + "test_receipt_wrong_key_and_boolean_epoch_are_rejected"],
            ),
            (
                "Protected-profile substitution and transition-pin substitution are rejected.",
                [
                    TRANSPORT + "test_descriptor_binds_mutable_profile_identity_and_pending_closes",
                    ROT_TRANSPORT + "test_pending_profile_rejects_substituted_transition_pin",
                ],
            ),
            (
                "Changed authenticated history page binding closes.",
                [PAGING + "test_changed_history_page_binding_closes"],
            ),
        ],
        "gaps": [
            "Policy-binding mismatch between a fresh receipt/head and the enrolled policy is not "
            "exercised (receipt verification cases cover nonce, registry, epoch, operation, request and key).",
        ],
    },
    "AC-TH-04": {
        "sources": [S + "trust_custodian_transport.py", S + "writer_guard.py", S + "custody_admin.py"],
        "clauses": [
            (
                "Both-peer UID rejection: same-UID profile, mismatched peer UID and custodian running as client.",
                [
                    TRANSPORT + "test_same_uid_production_profile_denied",
                    TRANSPORT + "test_actual_peer_credentials_match_current_uid",
                    TRANSPORT + "test_production_service_refuses_running_as_client_identity",
                ],
            ),
            (
                "macOS getpeereid peer-credential branch (this run is macOS arm64).",
                [TRANSPORT + "test_actual_peer_credentials_match_current_uid"],
            ),
            (
                "Protected parent/path, ACL and private-file permission checks.",
                [
                    TRANSPORT + "test_profile_in_writable_project_tree_is_never_trusted",
                    TRANSPORT + "test_acl_inspection_rejects_extended_grant",
                    CUSTODIAN + "test_existing_private_custody_files_cannot_be_world_readable",
                    GUARD + "test_real_factory_requires_protected_enrollment_before_any_acquisition",
                ],
            ),
            (
                "Unavailable transport or missing enrollment fails closed with zero local writes.",
                [
                    ADMIN + "test_valid_profile_unavailable_service_has_no_local_writes",
                    ADMIN + "test_normal_custody_status_is_failclosed_without_enrollment",
                    SERVE + "test_stateful_entrypoint_missing_custody_is_zero_write",
                    SERVE + "test_build_state_unavailable_custody_is_zero_write",
                ],
            ),
        ],
        "gaps": [
            "Linux SO_PEERCRED adapter branch was not executed (macOS-only run).",
            DEPLOYMENT_GAP,
        ],
    },
    "AC-TH-05": {
        "sources": [
            S + "trust_custodian.py",
            S + "trust_journal.py",
            S + "trust_rotation_service.py",
            S + "trust_rotation_client.py",
        ],
        "clauses": [
            (
                "Prepared records are durable, non-advancing and survive store restart.",
                [
                    CUSTODIAN + "test_preparation_does_not_advance_head_or_authorize_reads",
                    CUSTODIAN + "test_prepared_history_survives_store_restart",
                ],
            ),
            (
                "Lost commit replies keep access closed and recover the prepared revoke.",
                [
                    JOURNAL + "test_lost_commit_reply_closes_then_recovers_without_losing_revoke",
                    PAGING + "test_near_limit_preparation_survives_lost_reply",
                ],
            ),
            (
                "Pending preparation closes reads until exact reconciliation under an active fence.",
                [
                    JOURNAL + "test_pending_preparation_closes_read_until_exact_reconciliation",
                    JOURNAL + "test_pending_exact_record_is_finished_by_new_fence",
                ],
            ),
            (
                "Rotation crash points (real child exit, profile advance, commit interruption, write failure).",
                [
                    ROTATION + "test_rotation_preparation_survives_real_child_exit",
                    ROT_TRANSPORT
                    + "test_crash_after_profile_advance_before_finish_remains_closed_and_resumes",
                    ROT_TRANSPORT + "test_client_resumes_fsynced_preparation_after_commit_interruption",
                    ROT_TRANSPORT + "test_profile_write_failure_keeps_authority_closed_and_exact_preparation",
                ],
            ),
        ],
        "gaps": [
            "Process/fault matrix at every persistence boundary (client append/fsync, local-head "
            "handling, projection finalization, response delivery, acknowledgement) is not enumerated.",
            "No acknowledgement trace shows custodian durable commit/fsync precedes acknowledgement.",
        ],
    },
    "AC-TH-06": {
        "sources": [
            S + "writer_guard.py",
            "src/magicite/storage/lease.py",
            S + "trust_custodian.py",
            S + "trust_journal.py",
        ],
        "clauses": [
            (
                "Pause-old-before-registration/new-registration/resume and head-unchanged races.",
                [
                    CUSTODIAN + "test_stale_registration_rejected_even_when_head_unchanged",
                    CUSTODIAN + "test_same_attempt_retry_cannot_reactivate_superseded_generation",
                    CUSTODIAN + "test_full_predecessor_compares_head_even_with_same_generation",
                ],
            ),
            (
                "Pause-old-before-commit/new-fence/resume.",
                [
                    CUSTODIAN + "test_new_fence_rejects_old_commit_and_can_recover_exact_preparation",
                    ROTATION + "test_rotation_resumed_fence_rejects_old_commit_even_before_head_change",
                ],
            ),
            (
                "Predecessor capture order, unique attempt and nested guard reuse.",
                [
                    GUARD + "test_custody_predecessor_capture_precedes_local_lease_and_nested_reuses",
                    GUARD + "test_failed_custody_registration_releases_existing_local_lease",
                    GUARD + "test_nested_custody_cannot_attach_to_already_acquired_bare_lease",
                    GUARD + "test_nested_direct_try_cannot_refresh_outer_custody_attempt",
                    GUARD + "test_queued_contender_captures_predecessor_after_flock_not_before",
                    GUARD + "test_explicit_factory_adapter_binds_real_custodian_generation",
                ],
            ),
            (
                "Existing lease assertions after remote preparation and final remote snapshot.",
                [
                    JOURNAL + "test_exact_retry_checks_lease_after_remote_preparation",
                    JOURNAL + "test_append_checks_lease_after_final_remote_snapshot",
                    AUTHORITY + "test_all_production_outer_leases_use_explicit_registry_factory",
                    AUTHORITY + "test_nested_domain_rejects_unenrolled_or_foreign_outer_lease",
                ],
            ),
        ],
        "gaps": [
            "No node loses the existing lease after the external custodian commit and asserts it is "
            "checked before local replace/ACK.",
        ],
    },
    "AC-TH-07": {
        "sources": [
            S + "trust.py",
            S + "router.py",
            "src/magicite/mcp/bind_retrieval.py",
            S + "backup.py",
            S + "trust_artifacts.py",
        ],
        "clauses": [
            (
                "Backdated revoke against future-dated admit resolves by sequence.",
                [
                    JOURNAL + "test_sequence_wins_over_future_admit_timestamp",
                    JOURNAL + "test_verified_snapshot_reuse_never_hides_a_later_revoke",
                ],
            ),
            (
                "Post-route policy/root/history drift and body revalidation deny disclosure.",
                [
                    AUTHORITY + "test_actual_route_and_body_deny_after_authenticated_policy_change",
                    AUTHORITY + "test_router_never_restores_invalid_authenticated_admission",
                    AUTHORITY + "test_unmarked_source_cannot_route_or_disclose_even_with_old_admission",
                    AUTHORITY + "test_revoked_signer_is_denied_with_matching_current_policy_before_commit",
                ],
            ),
            (
                "Direct restore/import writer regressions.",
                [
                    AUTHORITY + "test_restore_rejects_unbound_trust_head_before_local_file_mutation",
                    T + "test_trust_r2_import_safety.py::test_import_bundle_refuses_overwrite_existing",
                    T + "test_trust_r2_import_safety.py::test_import_bundle_refuses_casefold_collision",
                    T
                    + "test_trust_r4_journal_auth.py::test_planted_unauthenticated_journal_does_not_delete_authored",
                ],
            ),
        ],
        "gaps": [
            "All trust call sites (route, body disclosure, rebuild, restore, import, review, revoke, "
            "policy/root) are not enumerated against one validated snapshot; no node asserts call-site "
            "completeness.",
        ],
    },
    "AC-TH-08": {
        "sources": [
            S + "trust_legacy.py",
            S + "trust_reconciliation.py",
            S + "custody_admin.py",
            S + "migration.py",
        ],
        "clauses": [
            (
                "Zero-write preview.",
                [
                    LEGACY + "test_legacy_preview_is_zero_write_and_discloses_unsigned_history",
                    LEGACY + "test_public_migration_missing_review_is_zero_write",
                ],
            ),
            (
                "Erased enrolled state versus explicit virgin enrollment; no silent key creation.",
                [
                    JOURNAL + "test_missing_local_tree_cannot_be_implicitly_reenrolled",
                    CUSTODIAN + "test_missing_registry_never_auto_enrolls",
                    CUSTODIAN + "test_explicit_enrollment_is_durable_and_cannot_reset",
                    ADMIN + "test_custody_admin_init_enroll_profile_explicit_only",
                    ADMIN + "test_custody_enroll_rejects_unreviewed_bytes",
                ],
            ),
            (
                "Reconciliation and complete-backup guards.",
                [
                    LEGACY + "test_changed_business_row_or_manifest_denies_backup_before_migration",
                    LEGACY + "test_incomplete_backup_never_initializes_history_and_can_resume",
                    LEGACY + "test_reviewed_backup_includes_wal_and_all_managed_domains_before_migration",
                    LEGACY + "test_apply_boundary_resume_never_implicitly_admits",
                    RECON + "test_active_gate_closes_normal_snapshot_despite_fake_local_completion",
                    RECON + "test_exact_resume_and_completion_preserve_restriction_without_admission",
                ],
            ),
            (
                "Unsigned/missing historical provenance disclosure and preserved originals.",
                [
                    LEGACY + "test_legacy_preview_is_zero_write_and_discloses_unsigned_history",
                    LEGACY + "test_unsigned_legacy_signer_claim_only_restricts_and_never_verifies_target",
                    LEGACY
                    + "test_actual_reviewed_apply_preserves_original_revoke_and_resumes_nonempty_targets",
                ],
            ),
            (
                "Conflicting legacy inventory and unlisted admissions rejected.",
                [
                    LEGACY + "test_legacy_preview_rejects_conflicting_decision_identity",
                    RECON + "test_plan_rejects_out_of_order_and_unlisted_admission_and_rotation",
                ],
            ),
        ],
        "gaps": [],
    },
    "AC-TH-09": {
        "sources": [S + "backup.py", S + "trust_journal.py", S + "trust_legacy.py"],
        "clauses": [
            (
                "Pre-revoke backup restores with the current authenticated suffix; revocation stays effective.",
                [
                    AUTHORITY + "test_pre_revoke_backup_restore_uses_current_authenticated_suffix",
                    BACKUP + "test_deleted_revoke_mirror_preserves_authenticated_restriction",
                ],
            ),
            (
                "Missing retained suffix or unbound trust head stops restore before local writes.",
                [
                    AUTHORITY + "test_missing_retained_custody_suffix_stops_restore_before_local_writes",
                    AUTHORITY + "test_restore_rejects_unbound_trust_head_before_local_file_mutation",
                ],
            ),
            (
                "No rekey; absent or stale overlay/anchor stays restricted.",
                [BACKUP + "test_custody_key_install_no_rekey", BACKUP + "test_missing_stale_overlay_closed"],
            ),
            (
                "Public restore stages exact backups without active mutation.",
                [
                    LEGACY + "test_public_restore_stages_exact_backup_without_active_mutation",
                    LEGACY + "test_public_restore_rejects_unrelated_staging",
                ],
            ),
        ],
        "gaps": [
            "No node enumerates all direct mirror-writing restore paths.",
            "No node shows valid RecoveryOverlay revocation records or fingerprint-key MACs cannot be "
            "resequenced or substituted for custodian authority (overlay cases cover absent/stale only).",
        ],
    },
    "AC-TH-10": {
        "sources": [
            S + "trust_rotation.py",
            S + "trust_rotation_client.py",
            S + "trust_rotation_service.py",
            S + "trust_artifacts.py",
        ],
        "clauses": [
            (
                "Dual-bound epoch transition binds old/new keys, epochs and heads.",
                [
                    ROTATION + "test_rotation_dual_signatures_bind_complete_tuple",
                    ROTATION + "test_transition_certificate_binds_exact_prepared_record",
                    ROT_TRANSPORT + "test_real_maintenance_client_verifies_old_then_new_epoch_receipts",
                    ROT_TRANSPORT + "test_socket_rotation_receipts_change_signer_only_after_commit",
                ],
            ),
            (
                "Stale or wrong-key rotation rejected.",
                [
                    ROTATION + "test_rotation_resumed_fence_rejects_old_commit_even_before_head_change",
                    ROTATION + "test_pending_journal_record_blocks_rotation_without_new_keys",
                    ROT_TRANSPORT + "test_pending_profile_rejects_substituted_transition_pin",
                ],
            ),
            (
                "Acknowledged revocation preserved across rotation.",
                [
                    ROTATION + "test_rotation_keeps_acknowledged_revoke_and_supports_next_epoch",
                    ROT_TRANSPORT
                    + "test_rotation_client_keeps_revoke_and_reconciles_epoch_under_existing_lease",
                ],
            ),
        ],
        "gaps": [
            "Old-format rejection: no node runs a pre-hardening reader (for example 22ae4e0) against the "
            "new authority format.",
            "Revocation-preserving downgrade/restricted recovery: no downgrade-attempt node.",
        ],
    },
    "AC-TH-11": {
        "sources": [
            "src/magicite/obs/doctor.py",
            S + "trust_custodian_transport.py",
            S + "trust_legacy.py",
            S + "backup.py",
        ],
        "clauses": [
            (
                "Zero-write diagnosis and read-only diagnostics.",
                [
                    "tests/unit/obs/test_doctor.py::test_zero_write_matrix",
                    SERVE + "test_stateful_entrypoint_missing_custody_is_zero_write",
                    SERVE + "test_build_state_unavailable_custody_is_zero_write",
                    FENCING + "test_public_cli_reconciliation_and_read_only_diagnostics",
                ],
            ),
            (
                "Error sentinels and redaction.",
                [
                    SERVE + "test_serve_startup_error_is_redacted_stderr",
                    OPERATOR + "test_cli_exception_details_never_disclose_canaries",
                    TRANSPORT + "test_missing_operation_is_redacted_protocol_error_not_uncaught_keyerror",
                    TRANSPORT + "test_parser_recursion_is_normalized_to_redacted_protocol_error",
                ],
            ),
            (
                "Archive inventory excludes control keys and restore markers.",
                [
                    LEGACY + "test_existing_control_key_requires_encrypted_custody_and_is_not_archived",
                    BACKUP + "test_backup_excludes_restore_generation_markers",
                ],
            ),
        ],
        "gaps": [
            "Doctor byte/mtime zero-write matrix does not include unavailable, corrupt, stale or "
            "restricted custody/trust-history states.",
            "No custodian private-key (signing.key/journal.key) canary over registry archives and logs.",
        ],
    },
    "AC-TH-12": {
        "sources": [S + "trust.py", S + "registry.py", S + "router.py", S + "backup.py"],
        "clauses": [
            (
                "Original admit -> revoke -> mirror-delete scenario fails closed.",
                [
                    AUTHORITY + "test_actual_domain_mirror_loss_cannot_resurrect_revoke",
                    BACKUP + "test_deleted_revoke_mirror_preserves_authenticated_restriction",
                    MIRROR_LOSS,
                ],
            ),
            (
                "Successful authenticated approval, revocation, routing and recovery workflows.",
                [
                    AUTHORITY + "test_real_register_review_revoke_flow_uses_custody",
                    "tests/integration/test_trust_recovery.py::test_review_replay_rebuild",
                    AUTHORITY + "test_actual_promote_accepts_bound_marked_v1",
                    AUTHORITY + "test_pre_revoke_backup_restore_uses_current_authenticated_suffix",
                ],
            ),
            ("Full regression, type, lint and docs checks at the clean source.", [FULL_CHECKS]),
        ],
        "gaps": [DEPLOYMENT_GAP],
    },
}


class BuildError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dump(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


class Builder:
    def __init__(self, args: argparse.Namespace) -> None:
        self.root: Path = args.root.resolve()
        self.raw: Path = args.raw_dir.resolve()
        self.python: str = args.venv_python
        self.reviewer: str = args.reviewer
        self.status: str = args.review_status
        self.reviewed_package: str = args.reviewed_package
        self.written: set[str] = set()
        if self.status not in REVIEW_STATUSES:
            raise BuildError(f"review status must be one of {REVIEW_STATUSES}")
        if self.status == "PENDING" and (self.reviewer or self.reviewed_package):
            raise BuildError("a PENDING package must not name a reviewer or reviewed package")
        if self.status != "PENDING" and not self.reviewer.strip():
            raise BuildError("a recorded review requires a named reviewer")
        if self.reviewer and self.reviewer == PRODUCER:
            raise BuildError("producer and reviewer must differ")

    # ---- primitives -------------------------------------------------------------------------
    def read(self, rel: str) -> bytes:
        path = self.root / rel
        if not path.is_file():
            raise BuildError(f"missing file: {rel}")
        return path.read_bytes()

    def ref(self, rel: str) -> dict[str, str]:
        return {"path": rel, "sha256": sha(self.read(rel))}

    def write(self, rel: str, data: bytes) -> dict[str, str]:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.written.add(rel)
        return {"path": rel, "sha256": sha(data)}

    def raw_text(self, name: str) -> str:
        path = self.raw / name
        if not path.is_file():
            raise BuildError(f"missing raw input: {name}")
        return path.read_text()

    def raw_exit(self, name: str) -> int:
        return int(self.raw_text(name + ".exit").strip())

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=self.root, text=True, capture_output=True, check=False)

    # ---- preconditions ----------------------------------------------------------------------
    def preconditions(self) -> None:
        if self.raw_text("head.txt").strip() != SOURCE:
            raise BuildError("raw run head is not the declared source")
        if self.raw_text("status-before.txt") or self.raw_text("status-after.txt"):
            raise BuildError("raw run worktree was not clean")
        for name in ("pytest", "mypy", "ruff", "docs", "generated"):
            if self.raw_exit(name) != 0:
                raise BuildError(f"{name} did not exit 0")
        diff = self.git(
            "diff",
            "--quiet",
            SOURCE,
            "--",
            ".",
            f":(exclude){R2}",
            f":(exclude){V1}/README.md",
        )
        if diff.returncode != 0:
            raise BuildError("tracked tree differs from source outside the r2 package")
        for name in (
            "trust-mirror-loss-custody",
            "checkpoint-process-death-custody",
            "v1-reproduce-trust-mirror-loss",
            "v1-probe-checkpoint-process-death",
        ):
            self.probe_record(name)
        for path in sorted((self.root / R2).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                rel = path.relative_to(self.root).as_posix()
                if path.name not in STATIC_FILES and rel not in self.expected_outputs():
                    raise BuildError(f"unexpected file in r2 package: {rel}")

    def expected_outputs(self) -> set[str]:
        names = {
            "junit.xml",
            "pytest.txt",
            "mypy.txt",
            "ruff.txt",
            "docs.txt",
            "generated.txt",
            "environment.json",
            "test-results.json",
            "checks.json",
            "trust-mirror-loss-custody.json",
            "trust-mirror-diagnostic-provenance.json",
            "checkpoint-process-death-custody.json",
            "checkpoint-process-death-provenance.json",
            "historical-diagnostics-at-r2-source.json",
            "review-record.json",
            *(name for _, _, name in WITNESSES),
        }
        top = {"criterion-ledger.json", "release-manifest.json", "gate-table.md", "README.md"}
        return {f"{EV}/{n}" for n in names} | {f"{R2}/{n}" for n in top}

    def probe_record(self, name: str) -> dict[str, Any]:
        if self.raw_text(name + ".head").strip() != SOURCE:
            raise BuildError(f"{name}: probe head is not the declared source")
        before, after = self.raw_text(name + ".status-before"), self.raw_text(name + ".status-after")
        if before != after:
            raise BuildError(f"{name}: probe changed the worktree")
        untracked = [line[3:] for line in before.splitlines()]
        if any(not line.startswith("?? ") for line in before.splitlines()) or any(
            not path.startswith(R2 + "/") for path in untracked
        ):
            raise BuildError(f"{name}: probe ran with changes outside untracked r2 files")
        return {"exit_code": self.raw_exit(name), "untracked_scope": untracked}

    # ---- junit ------------------------------------------------------------------------------
    def load_junit(self) -> None:
        tree = ET.parse(self.raw / "junit.xml")
        self.results: list[dict[str, str]] = []
        self.skip_messages: list[str] = []
        for case in tree.getroot().iter("testcase"):
            node = case.get("classname", "").replace(".", "/") + ".py::" + case.get("name", "")
            status = "PASS"
            for child in case:
                if child.tag == "skipped":
                    status = "SKIP"
                    self.skip_messages.append(child.get("message", ""))
                elif child.tag in ("failure", "error"):
                    status = "FAIL"
            self.results.append({"node": node, "status": status})
        self.status_of = {row["node"]: row["status"] for row in self.results}
        if len(self.status_of) != len(self.results):
            raise BuildError("duplicate junit node ids")
        self.counts = {k: sum(r["status"] == k for r in self.results) for k in ("PASS", "SKIP", "FAIL")}
        summary = self.raw_text("pytest.txt").strip().splitlines()[-1]
        match = re.match(r"(\d+) passed, (\d+) skipped", summary)
        if not match or (int(match[1]), int(match[2])) != (self.counts["PASS"], self.counts["SKIP"]):
            raise BuildError("pytest summary disagrees with junit")
        if self.counts["FAIL"]:
            raise BuildError("junit records failures")
        docker = sum("image not built" in m for m in self.skip_messages)
        channels = re.findall(r"channel (\S+) \(\S+\) reserved/unevaluated", "\n".join(self.skip_messages))
        if docker + len(channels) != self.counts["SKIP"]:
            raise BuildError("unexplained skip")
        self.skip_scope = (
            f"{docker} local Docker-image checks ('magicite:verify' image not built), "
            f"{len(channels)} unpublished channel reservations ({', '.join(channels)})"
        )

    def expand(self, node: str) -> list[str]:
        return [n for n in self.status_of if n == node or n.startswith(node + "[")]

    def node_status(self, executed: list[str]) -> tuple[str, list[str]]:
        bad = [n for n in executed if self.status_of.get(n) != "PASS"]
        if not executed or any(n not in self.status_of for n in executed):
            return "UNEVALUATED", bad or executed
        if any(self.status_of[n] == "FAIL" for n in executed):
            return "FAIL", bad
        return ("UNEVALUATED", bad) if bad else ("PASS", [])

    # ---- evidence files ---------------------------------------------------------------------
    def commands(self) -> dict[str, str]:
        py = self.python
        ruff = str(Path(py).parent / "ruff")
        return {
            "pytest": f"PATH={PATH_PREFIX}:$PATH {py} -m pytest -q -p no:cacheprovider "
            f"--junitxml={self.raw}/junit.xml",
            "mypy": f"{py} -m mypy src",
            "ruff": f"{ruff} check .",
            "docs": f"{py} scripts/check_docs.py",
            "generated": f"{py} scripts/check_generated_docs.py",
        }

    def environment(self) -> None:
        code = (
            "import json,platform,importlib.metadata as m\n"
            "pk={}\n"
            "for d in m.distributions():\n"
            "    n=(d.metadata['Name'] or '').lower().replace('_','-')\n"
            "    if n: pk[n]=d.version\n"
            "print(json.dumps({'python':platform.python_version(),'platform':platform.platform(),"
            "'packages':dict(sorted(pk.items()))}))\n"
        )
        proc = subprocess.run(
            [self.python, "-c", code], cwd=self.root, text=True, capture_output=True, check=False
        )
        if proc.returncode != 0:
            raise BuildError("cannot capture venv environment: " + proc.stderr[-500:])
        info = json.loads(proc.stdout)
        selected = {k: info["packages"][k] for k in ("magicite", "mcp", "mcp-types", "pytest")}
        self.env = {
            "dependency_note": "Isolated venv built by UV_PROJECT_ENVIRONMENT=<venv> uv sync --frozen "
            "--all-extras --python 3.14 from uv.lock (editable magicite bound to the "
            "worktree; leiden/igraph extras included). Not the declared 3.11/3.12 matrix.",
            "interpreter": self.python,
            "packages": selected,
            "installed_distributions": info["packages"],
            "path_prefix": f"{PATH_PREFIX} prepended to PATH for the pytest run only",
            "platform": info["platform"],
            "provider": "hashing fixtures except explicit isolated install/model checks",
            "python": info["python"],
        }
        self.env_ref = self.write(f"{EV}/environment.json", dump(self.env))
        self.lock = self.ref("uv.lock")

    def copy_raw(self) -> None:
        self.raw_refs = {}
        for name in (
            "junit.xml",
            "pytest.txt",
            "mypy.txt",
            "ruff.txt",
            "docs.txt",
            "generated.txt",
            "trust-mirror-loss-custody.json",
            "checkpoint-process-death-custody.json",
        ):
            self.raw_refs[name] = self.write(f"{EV}/{name}", (self.raw / name).read_bytes())

    def test_results(self) -> None:
        review = self.review_fields()
        report = {
            "checker_scope": "Pending independent inspection of immutable r2 local execution evidence; "
            "not external product reproduction.",
            "checker_status": review["review_status"],
            "command": self.commands()["pytest"],
            "environment": self.env_ref,
            "failed": self.counts["FAIL"],
            "independent_checker": review["reviewer"],
            "junit": self.raw_refs["junit.xml"],
            "lock": self.lock,
            "passed": self.counts["PASS"],
            "raw_junit_sha256": self.raw_refs["junit.xml"]["sha256"],
            "results": self.results,
            "schema": "magicite/local-verification/1",
            "skip_scope": self.skip_scope,
            "skipped": self.counts["SKIP"],
            "source_commit": SOURCE,
            "source_dirty": False,
            "source_scope": SOURCE_SCOPE,
            "tracked_code_diff": False,
            "untracked_scope": [],
        }
        self.results_ref = self.write(f"{EV}/test-results.json", dump(report))

    def checks(self) -> None:
        commands = self.commands()
        outputs = {
            "pytest": "pytest.txt",
            "mypy": "mypy.txt",
            "ruff": "ruff.txt",
            "docs": "docs.txt",
            "generated": "generated.txt",
        }
        report = {
            "checks": {
                name: {
                    "command": commands[name],
                    "exit_code": self.raw_exit(name),
                    "output": self.raw_refs[outputs[name]],
                }
                for name in outputs
            },
            "source_commit": SOURCE,
            "source_dirty": False,
            "tracked_code_diff": False,
        }
        self.checks_ref = self.write(f"{EV}/checks.json", dump(report))

    def provenance(self) -> None:
        review = self.review_fields()
        probes = (
            (
                "trust-mirror-loss-custody",
                "reproduce-trust-mirror-loss-custody.py",
                "trust-mirror-diagnostic-provenance.json",
                [
                    "tests/support/custody_adapter.py",
                    "tests/integration/test_trust_recovery.py",
                    S + "trust.py",
                    S + "trust_journal.py",
                    S + "registry.py",
                ],
            ),
            (
                "checkpoint-process-death-custody",
                "probe-checkpoint-process-death-custody.py",
                "checkpoint-process-death-provenance.json",
                [
                    "tests/support/custody_adapter.py",
                    "tests/integration/test_evidence_durability.py",
                    S + "evidence.py",
                ],
            ),
        )
        self.provenance_refs = {}
        for name, script, output, sources in probes:
            record = self.probe_record(name)
            if record["exit_code"] != 0:
                raise BuildError(f"{name} exited {record['exit_code']}")
            body = {
                "artifacts": [
                    self.raw_refs[name + ".json"],
                    self.ref(f"{EV}/{script}"),
                    *(self.ref(path) for path in sources),
                ],
                "command": f"{self.python} {EV}/{script} > {self.raw}/{name}.json",
                "environment": self.env_ref,
                "exit_code": record["exit_code"],
                "interpreter": self.python,
                "lock": self.lock,
                "producer": PRODUCER,
                "script_sha256": sha(self.read(f"{EV}/{script}")),
                "schema": "magicite/diagnostic-provenance/1",
                "source_commit": SOURCE,
                "source_dirty": False,
                "tracked_code_diff": False,
                "untracked_scope": record["untracked_scope"],
                "untracked_scope_note": ROOT_PROBE_SCOPE,
                **review,
            }
            self.provenance_refs[name] = self.write(f"{EV}/{output}", dump(body))
        self.mirror = json.loads(self.read(f"{EV}/trust-mirror-loss-custody.json"))
        self.checkpoint = json.loads(self.read(f"{EV}/checkpoint-process-death-custody.json"))
        if self.mirror.get("schema") != "magicite/trust-mirror-diagnostic/2":
            raise BuildError("unexpected mirror diagnostic schema")
        reruns = []
        for name, script in (
            ("v1-reproduce-trust-mirror-loss", "reproduce-trust-mirror-loss.py"),
            ("v1-probe-checkpoint-process-death", "probe-checkpoint-process-death.py"),
        ):
            record = self.probe_record(name)
            errors = re.findall(r"(\w+Error): ([^\\\n']+)", self.raw_text(name + ".stderr"))
            reruns.append(
                {
                    "command": f"{self.python} {V1}/evidence/{script}",
                    "exit_code": record["exit_code"],
                    "final_error": f"{errors[-1][0]}: {errors[-1][1]}" if errors else None,
                    "script": self.ref(f"{V1}/evidence/{script}"),
                    "stdout_bytes": len(self.raw_text(name + ".stdout").encode()),
                    "untracked_scope": record["untracked_scope"],
                }
            )
        self.historical_ref = self.write(
            f"{EV}/historical-diagnostics-at-r2-source.json",
            dump(
                {
                    "note": "The c0782fd diagnostics are immutable historical observations. On the r2 source they "
                    "fail closed before their scenario because trust/evidence writes require enrolled "
                    "custody; the r2 custody scripts replay the same scenarios.",
                    "reruns": reruns,
                    "schema": "magicite/historical-diagnostic-rerun/1",
                    "source_commit": SOURCE,
                }
            ),
        )

    def review_fields(self) -> dict[str, str]:
        return {
            "review_status": self.status,
            "reviewed_package": self.reviewed_package,
            "reviewer": self.reviewer,
        }

    def review_record(self) -> None:
        record = {
            "evidence": {
                "producer": PRODUCER,
                "reviewed_head": self.reviewed_package,
                "reviewer": self.reviewer,
                "scope": "r2 local mechanical evidence re-adjudication and NO-GA draft: 88 criteria "
                "(76 frozen v1 + 12 additive AC-TH), 17 gates, fresh TRUST/PROTOCOL/RELIABILITY/"
                "SECURITY/GA-ALL proposals. Not GA approval, external reproduction or deployment "
                "qualification.",
                "tested_source": SOURCE,
                "verdict": self.status,
            },
            "historical_package": {
                "manifest": self.ref(f"{V1}/release-manifest.json"),
                "review_record": self.ref(f"{V1}/evidence/review-record.json"),
                "reviewed_package": HISTORICAL_REVIEWED,
                "tested_source": HISTORICAL_SOURCE,
            },
            "schema": "magicite/review-record/2",
        }
        self.review_ref = self.write(f"{EV}/review-record.json", dump(record))

    # ---- witnesses --------------------------------------------------------------------------
    def witnesses(self) -> None:
        self.witness_refs: dict[str, list[dict[str, str]]] = {}
        self.witness_status: dict[tuple[str, str], tuple[str, list[str]]] = {}
        remap = {
            f"{V1}/evidence/test-results.json": lambda: self.results_ref,
            f"{V1}/evidence/pytest.txt": lambda: self.raw_refs["pytest.txt"],
            f"{V1}/evidence/checkpoint-process-death-provenance.json": lambda: self.provenance_refs[
                "checkpoint-process-death-custody"
            ],
        }
        for gate, obligation, name in WITNESSES:
            old_rel = f"{V1}/evidence/{name}"
            old = json.loads(self.read(old_rel))
            if (old["gate"], old["obligation"]) != (gate, obligation):
                raise BuildError(f"historical witness mismatch: {name}")
            nodes = list(old["executed_nodes"])
            status, bad = self.node_status(nodes)
            artifacts = [
                remap[a["path"]]() if a["path"] in remap else self.ref(a["path"]) for a in old["artifacts"]
            ]
            body: dict[str, Any] = {
                "artifacts": artifacts,
                "command": self.commands()["pytest"],
                "derived_from": {"path": old_rel, "sha256": sha(self.read(old_rel))},
                "environment": self.env,
                "executed_nodes": nodes,
                "gate": gate,
                "lock": self.lock,
                "obligation": obligation,
                "producer": PRODUCER,
                "schema": "magicite/release-witness/1",
                "scope": "Draft r2 local mechanical evidence against clean tested source "
                f"{SOURCE}; same node mapping as the c0782fd witness; status derived from the r2 "
                "junit; no production, external or RC qualification.",
                "source_commit": SOURCE,
                "status": status,
                **self.review_fields(),
            }
            if bad:
                body["unpassed_or_missing_nodes"] = bad
                candidates = {n: SUCCESSOR_CANDIDATES[n] for n in bad if n in SUCCESSOR_CANDIDATES}
                if candidates:
                    body["successor_candidates_for_checker"] = candidates
            self.witness_status[(gate, obligation)] = (status, bad)
            self.witness_refs.setdefault(gate, []).append(self.write(f"{EV}/{name}", dump(body)))

    # ---- ledger -----------------------------------------------------------------------------
    def acceptance_blocks(self, rel: str) -> dict[str, list[str]]:
        lines = self.read(rel).decode().splitlines()
        blocks = {}
        for index, line in enumerate(lines):
            match = re.match(r"### (AC-[A-Z0-9]+-\d+)\b", line)
            if match:
                body = lines[index + 1 : index + 5]
                if [b.split(" ", 1)[0] for b in body] != ["GIVEN", "WHEN", "THEN", "VERIFY:"]:
                    raise BuildError(f"malformed acceptance block {match[1]}")
                blocks[match[1]] = body
        return blocks

    def row_common(self) -> dict[str, Any]:
        return {
            "checker_status": self.status,
            "command": self.commands()["pytest"],
            "environment": self.env_ref,
            "execution_report": self.results_ref,
            "independent_checker": self.reviewer,
            "lock": self.lock,
            "review_record": self.review_ref,
            "reviewed_package": self.reviewed_package,
            "source_commit": SOURCE,
        }

    def node_subcheck(self, node: str, obligation: str | None = None) -> dict[str, Any]:
        executed = self.expand(node)
        status, bad = self.node_status(executed or [node])
        entry: dict[str, Any] = {
            "executed_nodes": executed,
            "status": status,
            "test_node": node,
            "test_source": self.ref(node.split("::")[0]),
        }
        if obligation is None:
            entry["name"] = node.split("::")[1]
        else:
            entry["obligation"] = obligation
        if bad:
            entry["unpassed_or_missing_nodes"] = bad
        return entry

    def rebind(self, sub: dict[str, Any]) -> dict[str, Any]:
        if "executed_nodes" in sub:
            status, bad = self.node_status(sub["executed_nodes"])
            out = {k: v for k, v in sub.items() if k != "test_source"}
            out["status"], out["test_source"] = status, self.ref(sub["test_source"]["path"])
            if bad:
                out["unpassed_or_missing_nodes"] = bad
            return out
        evidence = sub["evidence"]
        if evidence == []:
            return dict(sub)
        if evidence["path"] == f"{V1}/evidence/checkpoint-process-death-provenance.json":
            ok = self.checkpoint.get("status") == "PASS"
            return {
                "evidence": self.provenance_refs["checkpoint-process-death-custody"],
                "obligation": sub["obligation"],
                "status": "PASS" if ok else "FAIL",
            }
        return {
            "evidence": [],
            "historical_evidence": evidence,
            "obligation": sub["obligation"] + " [c0782fd observation; not re-executed at the r2 source]",
            "status": "UNEVALUATED",
        }

    @staticmethod
    def derive(subchecks: list[dict[str, Any]]) -> str:
        statuses = {s["status"] for s in subchecks}
        return "FAIL" if "FAIL" in statuses else ("UNEVALUATED" if statuses - {"PASS"} else "PASS")

    def ledger(self) -> None:
        frozen = self.acceptance_blocks(ACCEPTANCE)
        additive = self.acceptance_blocks(ADDITIVE)
        old = json.loads(self.read(f"{V1}/criterion-ledger.json"))
        if old["acceptance"]["sha256"] != sha(self.read(ACCEPTANCE)):
            raise BuildError("frozen acceptance changed since v1")
        rows = []
        for row in old["criteria"]:
            lines = frozen[row["id"]]
            if [row["given"], row["when"], row["then"], row["verify"]] != lines or row[
                "frozen_text"
            ] != "\n".join(lines):
                raise BuildError(f"frozen text mismatch: {row['id']}")
            subchecks = [self.rebind(s) for s in row["subchecks"]]
            status = self.derive(subchecks)
            new = {
                **self.row_common(),
                "frozen_text": row["frozen_text"],
                "given": row["given"],
                "id": row["id"],
                "implementation_sources": [self.ref(s["path"]) for s in row["implementation_sources"]],
                "reason": PASS_REASON if status == "PASS" else row["reason"],
                "status": status,
                "subchecks": subchecks,
                "test_nodes": row["test_nodes"],
                "then": row["then"],
                "verify": row["verify"],
                "when": row["when"],
            }
            if status != row["status"] and status != "PASS":
                new["reason"] = f"r2 re-binding: {status}; v1 reason: {row['reason']}"
            if "limitations" in row:
                new["limitations"] = row["limitations"]
            if row["id"] == "AC-S04-03":
                new["limitations"] = (
                    "Content and explicit policy-revision changes invalidate approval in this test. "
                    "Authenticated anti-shrink trust history is covered by additive AC-TH-01..12, not "
                    "by this row; the S16 mirror-deletion exploit was not reproduced at the r2 source."
                )
            rows.append(new)
        if [r["id"] for r in rows] != list(frozen):
            raise BuildError("frozen criteria order/count mismatch")
        for identity, spec in TH.items():
            lines = additive[identity]
            subchecks = []
            for clause, nodes in spec["clauses"]:
                for node in nodes:
                    if node == MIRROR_LOSS:
                        ok = (
                            self.mirror["status"] == "NOT_REPRODUCED"
                            and self.mirror["precondition_admit_then_revoke_observed"] is True
                        )
                        subchecks.append(
                            {
                                "evidence": self.provenance_refs["trust-mirror-loss-custody"],
                                "obligation": clause + " Original-scenario reproduction with "
                                "simulated custody: " + self.mirror["status"] + ".",
                                "status": "PASS" if ok else "FAIL",
                            }
                        )
                    elif node == FULL_CHECKS:
                        ok = all(
                            self.raw_exit(n) == 0 for n in ("pytest", "mypy", "ruff", "docs", "generated")
                        )
                        subchecks.append(
                            {
                                "evidence": [self.checks_ref, self.results_ref],
                                "obligation": clause,
                                "status": "PASS" if ok else "FAIL",
                            }
                        )
                    else:
                        subchecks.append(self.node_subcheck(node, clause))
            for gap in spec["gaps"]:
                subchecks.append(
                    {"evidence": [], "obligation": "Unwitnessed: " + gap, "status": "UNEVALUATED"}
                )
            status = self.derive(subchecks)
            if status == "PASS":
                reason = PASS_REASON
            else:
                reason = "Mapped nodes pass; not PASS because: " + " ".join(spec["gaps"])
            nodes = sorted({s["test_node"] for s in subchecks if "test_node" in s})
            rows.append(
                {
                    **self.row_common(),
                    "frozen_text": "\n".join(lines),
                    "given": lines[0],
                    "id": identity,
                    "implementation_sources": [self.ref(p) for p in spec["sources"]],
                    "limitations": "Simulated/injected custody adapters exercise mechanisms only; fixture "
                    "success is not production custody qualification.",
                    "reason": reason,
                    "status": status,
                    "subchecks": subchecks,
                    "test_nodes": nodes,
                    "then": lines[2],
                    "verify": lines[3],
                    "when": lines[1],
                }
            )
        self.rows = rows
        ledger = {
            "acceptance": self.ref(ACCEPTANCE),
            "additive_acceptance": self.ref(ADDITIVE),
            "criteria": rows,
            "historical_ledger": {
                **self.ref(f"{V1}/criterion-ledger.json"),
                "source_commit": HISTORICAL_SOURCE,
            },
            "revision": "r2",
            "schema": "magicite/criterion-ledger/1",
            "scope": "Draft r2 integration evidence, not release authorization. PASS means the complete "
            "stated mechanical criterion only. Actual external prerequisites and unwitnessed "
            "VERIFY sub-clauses remain UNEVALUATED.",
            "source_commit": SOURCE,
            "source_dirty": False,
            "source_scope": SOURCE_SCOPE,
            "upstream_heads": {
                "historical_reviewed_package": HISTORICAL_REVIEWED,
                "historical_tested_source": HISTORICAL_SOURCE,
                "trust_hardening_base": HARDENING_BASE,
                "trust_hardening_merge": SOURCE,
            },
        }
        self.ledger_ref = self.write(f"{R2}/criterion-ledger.json", dump(ledger))

    def counts_for(self, prefix: str | None) -> dict[str, int]:
        rows = [
            r
            for r in self.rows
            if (prefix is None and not r["id"].startswith("AC-TH")) or (prefix and r["id"].startswith(prefix))
        ]
        return {k: sum(r["status"] == k for r in rows) for k in ("PASS", "UNEVALUATED", "FAIL")}

    # ---- manifest ---------------------------------------------------------------------------
    def gates(self) -> list[dict[str, Any]]:
        th_open = [r["id"] for r in self.rows if r["id"].startswith("AC-TH") and r["status"] != "PASS"]
        mirror = self.provenance_refs["trust-mirror-loss-custody"]
        ckpt = self.provenance_refs["checkpoint-process-death-custody"]
        old = {g["id"]: g for g in json.loads(self.read(f"{V1}/release-manifest.json"))["gates"]}
        u = "UNEVALUATED"
        fresh: dict[str, tuple[str, dict[str, tuple[str, str]], list[dict[str, str]]]] = {
            "TRUST": (
                "Known v1 mirror-deletion defect is not reproduced at r2 and its regressions pass, but a "
                "named adversarial corpus is absent, additive trust rows remain partly unwitnessed and "
                "separate-UID custody is unqualified. No remaining known defect: proposed UNEVALUATED, "
                "not FAIL (checker decision).",
                {
                    "offline-tamper": (
                        "PASS",
                        "Candidate: AC-S04-02 signed-bundle tamper/escape node and the "
                        "local journal tamper matrix pass at r2; no fresh witness file produced.",
                    ),
                    "local-review-revocation": (
                        u,
                        "Mirror-loss reinstatement not reproduced (custody replay "
                        "NOT_REPRODUCED; regression nodes pass), but AC-TH-01/07 "
                        "sub-clauses (splice, root projection, call-site completeness) "
                        "lack witnesses.",
                    ),
                    "server-origin": (
                        "PASS",
                        "Candidate: AC-S04-01 forged-origin node passes at r2; no fresh "
                        "witness file produced.",
                    ),
                    "adversarial-corpus": (u, "No named adversarial trust corpus exists."),
                },
                [mirror],
            ),
            "SECURITY": (
                "The v1 critical finding is not reproduced at r2, but no release-scoped threat model, "
                "bounded fuzz/adversarial evidence or independent security review of the r2 source is "
                "recorded. Proposed UNEVALUATED, not FAIL (checker decision).",
                {
                    "threat-model": (
                        u,
                        "Only the plan-scoped trust-custody threat model exists "
                        f"({HARDENING}/threat-model.md); no release-scoped model for S04/S11/S12/S13.",
                    ),
                    "bounded-fuzz-adversarial": (
                        u,
                        "No fuzz harness or adversarial corpus; bounded frame/parser "
                        "unit cases are targeted tests, not fuzzing.",
                    ),
                    "critical-resolved": (
                        u,
                        "Original exploit closed in observation; four threat-model critical "
                        "risks have mapped nodes, but additive rows "
                        + ", ".join(th_open)
                        + " are not PASS and no independent security review is recorded.",
                    ),
                },
                [mirror, self.ref(f"{HARDENING}/threat-model.md")],
            ),
            "PROTOCOL": (
                "Generic protocol probes pass at r2 including the custody-revalidated body gate; the "
                "advertised real Claude Code transcript remains absent.",
                {
                    "schemas": ("PASS", "Candidate: AC-S11-02 payload parity passes at r2."),
                    "retry-cancel-errors": (
                        "PASS",
                        "Candidate: AC-S11-03 commit-boundary and AC-S11-01 stale-body nodes pass at r2.",
                    ),
                    "advertised-transcripts": (
                        u,
                        "No archived real Claude Code host transcript (AC-S11-04).",
                    ),
                },
                [],
            ),
            "RELIABILITY": (
                "Local kill/replay/fencing coverage now includes custody fences, real child exits "
                "and an r2 checkpoint process-death probe; the advertised OS/channel/published-"
                "upgrade/store matrix is unrun.",
                {
                    "all-store-kill-replay-fencing": (
                        u,
                        "Local evidence: lease multiprocess, custody writer guard, "
                        "rotation real child exit, checkpoint process death at r2; "
                        "supported OS/store matrix not run.",
                    ),
                    "backup-restore-upgrade-matrix": (
                        u,
                        "Local 0.2/0.3 upgrade and restore nodes pass; published "
                        "upgrade and OS/channel matrix not run.",
                    ),
                    "zero-write-doctor": (
                        u,
                        "AC-S12-01 matrix passes, but it does not cover custody/trust-history "
                        "states (AC-TH-11 gap).",
                    ),
                },
                [ckpt],
            ),
            "GA-ALL": (
                "Required gates do not all pass; maintainer sign-off and fetched published artifact checks "
                "are absent. No gate is a known FAIL at r2: proposed UNEVALUATED (checker decision).",
                {
                    "all-required-gates": (u, "Fourteen gates are not PASS at r2."),
                    "maintainer-signoff": (u, "No maintainer sign-off."),
                    "fetched-artifacts": (u, "No published artifacts fetched or verified."),
                },
                [],
            ),
        }
        out = []
        ledger_support = [self.ledger_ref]
        for gate in GATE_ORDER:
            entry: dict[str, Any] = {
                "id": gate,
                "obligations": old[gate]["obligations"],
                **self.review_fields(),
            }
            if gate in self.witness_refs:
                statuses = [self.witness_status[(g, o)] for g, o, _ in WITNESSES if g == gate]
                ok = all(s == "PASS" for s, _ in statuses)
                entry.update(
                    evidence=self.witness_refs[gate],
                    status="PASS" if ok else "UNEVALUATED",
                    supporting_artifacts=ledger_support,
                )
                if ok:
                    entry["reason"] = old[gate]["reason"] + " Re-derived from the r2 junit."
                else:
                    missing = sorted({n for _, b in statuses for n in b})
                    successor = all(n in SUCCESSOR_CANDIDATES for n in missing)
                    entry["reason"] = (
                        "Strict v1 node mapping is not fully executed at r2: "
                        + ", ".join(missing)
                        + " is not a passed r2 node"
                        + (
                            "; a successor node is disclosed in the witness for the checker"
                            if successor
                            else ""
                        )
                        + ". Other obligations re-derived PASS."
                    )
            elif gate in fresh:
                reason, proposals, support = fresh[gate]
                entry.update(
                    evidence=[],
                    status=u,
                    reason=reason,
                    adjudication="fresh (traceability.md)",
                    obligation_proposals={
                        k: {"proposed_status": s, "reason": r} for k, (s, r) in proposals.items()
                    },
                    supporting_artifacts=ledger_support + support,
                )
            else:
                entry.update(
                    evidence=[],
                    status=old[gate]["status"],
                    reason=old[gate]["reason"],
                    supporting_artifacts=ledger_support,
                )
            out.append(entry)
        return out

    def manifest(self) -> None:
        self.gate_rows = self.gates()
        manifest = {
            "criterion_ledger": self.ledger_ref,
            "draft_only": True,
            "external": None,
            "gates": self.gate_rows,
            "generated_by": self.ref(f"{EV}/build_r2_package.py"),
            "held_decisions": [
                "MCP surface amendment (16 tools remain)",
                "Optional S10",
                "Publishing",
                "Worktree deletion",
                "Shared venv resync (r2 used an isolated scratch venv)",
                "Separate-UID Linux/macOS custodian provisioning and deployment qualification "
                "(account creation/service setup not authorized)",
            ],
            "historical_observation": {
                **self.ref(f"{V1}/release-manifest.json"),
                "reviewed_package": HISTORICAL_REVIEWED,
                "source_commit": HISTORICAL_SOURCE,
            },
            "maintainer_signoff": None,
            "release_candidates": [],
            "resolved_holds": {
                "Trust ledger proposal/custody": "Adopted as the additive trust-hardening plan and implemented "
                f"(merged at {SOURCE}); qualification remains UNEVALUATED."
            },
            "revision": "r2",
            "schema": "magicite/release-manifest/1",
            "source_commit": SOURCE,
            "source_dirty": False,
            "source_scope": SOURCE_SCOPE,
        }
        self.write(f"{R2}/release-manifest.json", dump(manifest))

    # ---- markdown ---------------------------------------------------------------------------
    def markdown(self) -> None:
        def link(ref: dict[str, str]) -> str:
            rel = os.path.relpath(ref["path"], R2)
            return f"[{Path(rel).stem}]({rel})"

        lines = [
            "# V1 gate table r2 — NO-GA draft",
            "",
            f"All 17 gates are explicit. Tested source is `{SOURCE[:7]}` (trust hardening merged). Statuses "
            "are maker proposals pending independent review; no review is recorded and no release is "
            f"authorized. The `{HISTORICAL_SOURCE[:7]}` table remains the historical observation "
            "([v1 gate table](../gate-table.md)).",
            "",
            "| Gate | Proposed status | Evidence and remaining obligation |",
            "|---|---|---|",
        ]
        for gate in self.gate_rows:
            refs = gate["evidence"] or [r for r in gate["supporting_artifacts"]]
            links = ", ".join(link(r) for r in refs)
            extra = ""
            if "obligation_proposals" in gate:
                extra = (
                    " Obligations: "
                    + "; ".join(
                        f"{k} {v['proposed_status']}" for k, v in gate["obligation_proposals"].items()
                    )
                    + "."
                )
            lines.append(f"| {gate['id']} | **{gate['status']}** | {gate['reason']}{extra} {links} |")
        tally = {k: sum(g["status"] == k for g in self.gate_rows) for k in ("PASS", "UNEVALUATED", "FAIL")}
        lines += [
            "",
            f"{tally['PASS']} mechanical PASS gates, {tally['UNEVALUATED']} UNEVALUATED gates and "
            f"{tally['FAIL']} FAIL gates are proposed. Obligation-level PASS candidates are not gate PASS: "
            "no witness file was produced for them and their gates have other unmet obligations.",
            "",
            "Missing external evidence is not waived. Absent mandatory evidence independently blocks GA. "
            "See the [r2 package scope](README.md).",
        ]
        self.write(f"{R2}/gate-table.md", ("\n".join(lines) + "\n").encode())

        frozen, th = self.counts_for(None), self.counts_for("AC-TH")
        th_rows = [r for r in self.rows if r["id"].startswith("AC-TH")]
        th_open = ", ".join(r["id"] for r in th_rows if r["status"] != "PASS")
        th_pass = ", ".join(r["id"] for r in th_rows if r["status"] == "PASS") or "none"
        mirror_variants = "; ".join(
            f"{v['variant']}: reinstated={str(v['admission_reinstated']).lower()}"
            for v in self.mirror["variants"]
        )
        revoke_mirrors = self.mirror["variants"][0]["mutation"].get("revoke_mirror_files_found")
        readme = f"""# V1 release decision draft r2: NO-GA

This revision is **not eligible for GA**. It is a draft evidence package, not a
release, tag, publisher authorization, review or maintainer sign-off. Frozen
acceptance and release thresholds are unchanged.

The `{HISTORICAL_SOURCE[:7]}` package in [`docs/releases/v1/`](../README.md) remains the
immutable historical observation of that source (reviewed at `{HISTORICAL_REVIEWED[:7]}`).
This r2 package re-adjudicates the same 17 gates against clean source
`{SOURCE[:7]}`, which merges the adopted trust-hardening plan
(`{HARDENING}/`). It does not edit or reinterpret the earlier records.

The [gate table](gate-table.md) covers all 17 mandatory gates. The
[structured manifest](release-manifest.json) is evaluated by the fail-closed validator:

```sh
python scripts/check_release_manifest.py docs/releases/v1/r2/release-manifest.json
```

Exit 1 and `eligible: false` are the expected result. **Review is pending:** every
reviewer and independent-checker field is empty and every review status is
`PENDING`, so the validator also rejects the regenerated witnesses for lacking an
independent named review. That rejection is intended. A review is recorded only by
re-running the [builder](evidence/build_r2_package.py) with `--reviewer`,
`--review-status` and `--reviewed-package`; the maker (`{PRODUCER}`) cannot be the
reviewer. Hand edits are not a review.

## What changed since the c0782fd package

- Source: `{HISTORICAL_SOURCE[:7]}` → `{SOURCE[:7]}` (trust hardening, PR #32). Full
  suite: **{self.counts["PASS"]} passed, {self.counts["SKIP"]} skipped**
  ({self.skip_scope}); mypy, ruff, docs and generated-reference checks exit 0
  ([checks](evidence/checks.json), [results](evidence/test-results.json)).
- [criterion-ledger.json](criterion-ledger.json) now has 88 rows: the 76 frozen
  v1 criteria (text unchanged, re-bound to the r2 run) plus the 12 additive
  AC-TH criteria. Frozen rows: {frozen["PASS"]} PASS, {frozen["UNEVALUATED"]} UNEVALUATED,
  {frozen["FAIL"]} FAIL. Additive rows: {th["PASS"]} PASS ({th_pass}), {th["UNEVALUATED"]}
  UNEVALUATED, {th["FAIL"]} FAIL. Each non-PASS AC-TH row names its unwitnessed
  VERIFY sub-clause; {th_open} are therefore not PASS even though every mapped
  node passed.
- The historical diagnostics no longer run: on r2 they fail closed with
  `CustodianError` before their scenario because writes require enrolled custody
  ([rerun record](evidence/historical-diagnostics-at-r2-source.json)). The
  [custody replay](evidence/reproduce-trust-mirror-loss-custody.py) of admit →
  revoke → delete revoke mirror → re-check, using the simulated fixture custodian,
  reports **{self.mirror["status"]}** ({mirror_variants}). Revoke decision mirrors
  found to delete: {revoke_mirrors} (the r2 revoke wrote no decision mirror file);
  a replayed admit mirror did not restore admission. See its
  [output](evidence/trust-mirror-loss-custody.json) and
  [provenance](evidence/trust-mirror-diagnostic-provenance.json).
- AC-S09-02's process-death subcheck is re-executed by a
  [custody-attached probe](evidence/probe-checkpoint-process-death-custody.py)
  ({self.checkpoint["status"]}). The AC-S14-01 hashing-smoke subcheck was not
  re-executed and is marked UNEVALUATED (that row was already UNEVALUATED).
- TRUST and SECURITY move from FAIL to a proposed UNEVALUATED, and GA-ALL from FAIL
  to a proposed UNEVALUATED: no known defect is reproduced at r2, but obligations
  are unrun or unwitnessed.
  PRIVACY moves from PASS to a proposed UNEVALUATED only because the strict v1
  lifecycle-tests mapping names a node absent from the r2 run; the successor node
  is disclosed in the witness for the checker. These are proposals, not findings.

Criterion PASS is the complete stated mechanical obligation only. It does not
substitute for an empirical release gate, and fixture or same-account custody is
never deployment qualification. Unrun external clauses remain UNEVALUATED even
when their supporting tests pass.

Local verification used Python {self.env["python"]} on {self.env["platform"]} in an isolated venv
synced from `uv.lock`. That is not the declared Python 3.11/3.12 release matrix.

## Open notes for the independent checker

- `{HARDENING}/README.md:3` still reads "IMPLEMENTATION AND QUALIFICATION PENDING"
  although the implementation is merged at `{SOURCE[:7]}`. It is not edited here.
- The PRIVACY data-map witness still binds `docs/releases/v1/privacy-data-map.md`,
  whose last sentence describes the historical live trust-mirror weakness and which
  does not list the trust authority journal or custodian surfaces.
- Whether gates with no remaining known defect but unrun obligations (TRUST,
  SECURITY, GA-ALL) are FAIL or UNEVALUATED; whether to adopt the PRIVACY successor
  node; whether obligation-level PASS candidates deserve fresh witness files.

## Still required

Official SkillRet final split, licensed real 10k corpus, dedicated production E6
measurements, paired retrieval and empirical abstention bounds, independently
authored actual host-task outcomes, real Claude Code transcripts, a release-scoped
threat model, bounded fuzz/adversarial evidence and a named adversarial trust
corpus, separate-UID Linux/macOS custodian deployment qualification, the unwitnessed
AC-TH sub-clauses, full supported recovery/upgrade matrix, published-channel and
fetched supply-chain checks, independent operator tutorial, two independent
compatible RCs, external reproduction or two production pilots, an independent
review of this package, and explicit maintainer sign-off. No participants were
contacted, accounts provisioned or publishing accounts created by this task.

Human decisions remain open for the proposed MCP expansion (the frozen 16-tool
surface remains), optional S10, publishing, worktree deletion, shared-environment
resynchronization, and custodian provisioning. The trust ledger proposal itself was
adopted and implemented through the trust-hardening plan. No release authority is
inferred from code review or green CI.

Rollback of this r2 package removes only evidence files and draft documents; it has
no runtime storage migration. Runtime rollback follows the operator documentation.
"""
        self.write(f"{R2}/README.md", readme.encode())

    def build(self) -> None:
        self.preconditions()
        self.load_junit()
        self.copy_raw()
        self.environment()
        self.test_results()
        self.checks()
        self.provenance()
        self.review_record()
        self.witnesses()
        self.ledger()
        self.manifest()
        self.markdown()
        cache = self.root / EV / "__pycache__"
        if cache.is_dir():
            shutil.rmtree(cache)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--reviewer", default="")
    parser.add_argument("--review-status", default="PENDING")
    parser.add_argument("--reviewed-package", default="", help="commit of the reviewed package (review only)")
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--venv-python", required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[5])
    args = parser.parse_args()
    try:
        builder = Builder(args)
        builder.build()
    except BuildError as exc:
        print(f"build_r2_package: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "written": len(builder.written),
                "frozen": builder.counts_for(None),
                "additive": builder.counts_for("AC-TH"),
                "gates": {g["id"]: g["status"] for g in builder.gate_rows},
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
