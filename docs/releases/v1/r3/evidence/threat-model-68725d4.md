# Magicite v1 release-scoped threat model

**Gate obligation:** SECURITY `threat-model` (`.spectra/plans/magicite-v1/release-gates.md:24`).
**Status:** draft for the r3 evidence package. Nothing here is a PASS claim; gate
statuses come only from the recorded evidence packages. The current recorded status is
SECURITY **UNEVALUATED** with `threat-model`, `bounded-fuzz-adversarial` and
`critical-resolved` all UNEVALUATED (`docs/releases/v1/r2/gate-table.md:22`).
**Findings:** see the [findings register](findings-register.md). **Open items:** see [r3-open-items.md](r3-open-items.md), which is grounded in the bodies of merged PRs #34 to #50 (PRs #48 to #50 recorded no remaining items).

## 1. Scope and binding

- **Source:** branch `codex/v1-integration` at `bcdf680` (all r3 slices merged; the
  head of the `codex/v1-r3-b3-threat-model` worktree). Every `path:line` citation
  below refers to that tree. The r3 evidence package **must re-bind** this document to
  its own clean source commit and re-check the citations. This document is not evidence
  for `9bf9ca0` (r2) or `c0782fd` (v1).
- **Product surface in scope:** the stdio MCP server (16 tools, `docs/AUTHORITY.md:10-11`),
  the CLI, the trust custodian and its Unix-socket client, bundle intake and import, the
  writable registry (`.magicite/`, `.egr.md`), backup/restore/migration, the OCI image
  and model acquisition.
- **Inherited model:** the adopted trust-hardening threat model
  (`.spectra/plans/magicite-v1-trust-hardening/threat-model.md:1-39`) and amendment
  TH-A01 are normative for trust history. This document extends that model to the
  rest of the release surface. It does not replace it.
- **Citation shorthand.** Bare `threat-model.md:N`, `implementation-notes.md` and
  `amendments/TH-A01-…` refer to files under `.spectra/plans/magicite-v1-trust-hardening/`.
  `manifest.json:N` and `TAC-nnn` refer to the adversarial corpus at
  `tests/fixtures/adversarial/trust/manifest.json`. TAC entries are executed by
  `tests/integration/test_trust_adversarial_corpus.py`. Fuzz targets are in
  `tests/fuzz/test_bounded_fuzz.py:316-325`. `OI-nn` items are recorded in [r3-open-items.md](r3-open-items.md), each grounded
  in a merged PR body.
- **Evidence classes.** *Tested in-repo* means a collected test, corpus entry or fuzz
  target exercises the control with fixture custody, injected transports or fake peer
  credentials. *Deployment-qualified* means it was run against a real separate-UID
  custodian on a supported OS. **No row in this document is deployment-qualified**
  (`threat-model.md:31-34` in the trust-hardening plan; `tests/fixtures/adversarial/trust/manifest.json:46-49`).

## 2. Assets

| ID | Asset | Where | Security property |
|---|---|---|---|
| A1 | Custodian signing key, MAC keys, enrollment profile, pins, root-owned descriptor | Outside the writable project, custodian UID (`implementation-notes.md` "Functional protected-profile ownership") | Confidentiality, integrity |
| A2 | Authenticated trust history (custodian journal and head; local `journal.jsonl`/`head.json` projection) | Custodian state; `trust/authority/` | Integrity, monotonicity (revocations are never lost) |
| A3 | Policy and governance state (policy store, `policy_activate`/`policy_rollback` approval mirrors, trust roots) | `policy_store/`, `approvals/` | Integrity, fail-closed on loss |
| A4 | Engram content and bodies (`.egr.md`, assets) | Registry | Integrity (digest-bound), disclosure only when admitted |
| A5 | Approvals and approval audit | `approvals/`, DB | Integrity, non-repudiation |
| A6 | Backups, overlays, legacy snapshots | Operator-chosen destination | Integrity on restore; confidentiality is the operator's job (`docs/operations.md:494-499`) |
| A7 | MCP host channel (stdio) | Host process | Integrity of tool calls; no leakage in error envelopes |
| A8 | Embedding model files | Fastembed cache or baked image path (`Dockerfile:114-124`) | Integrity; no network fetch at runtime |
| A9 | Configuration (`MAGICITE_*` env, config file) | Operator | Integrity (for example, `autonomous` defaults to `False`, `src/magicite/config.py:287`) |
| A10 | Rebuildable SQLite projection (`skill-graph.db`) | Registry | Not an authority (`docs/AUTHORITY.md:24-30`) |

## 3. Actors and capabilities

The first eight rows are the adversarial corpus vocabulary
(`tests/fixtures/adversarial/trust/manifest.json`, key `capabilities`). The last four
rows extend it to the non-trust surface.

| Actor | Capability | In scope? |
|---|---|---|
| `same-uid-local-writer` | Edits, erases, reorders, replays or copies any byte in the writable registry under the client UID. Holds no custodian key. | Yes |
| `wire-relay` | Captures, replays, splices or rewrites custodian wire frames and receipts on the local channel. Cannot sign as the custodian. | Yes |
| `malicious-bundle-author` | Supplies crafted archives, manifests or external engram files to intake and import. | Yes |
| `crash-and-withhold` | Kills the client at any persistence boundary, drops replies or withholds custody. | Yes |
| `concurrent-stale-writer` | Resumes a paused or older lease holder after a newer holder registered. | Yes |
| `backup-restorer` | Restores old backups, overlays or legacy bytes through supported runtime paths (TH-A01 scope). | Yes |
| `misconfigured-deployment` (operator error) | Places the custody profile, keys or service where the registry writer can reach them, or runs on an unsupported OS. | Yes (must fail closed) |
| `foreign-uid-peer` | A process under an unexpected UID connects to or answers on the custody socket. | Yes, fixture-tested only |
| Compromised MCP host / prompt injection | Issues any of the 16 tools with arbitrary validated arguments. | Yes |
| Operator running a pre-hardening binary on hand-rolled bytes | See TH-A01. | Disclosed residual (§6) |
| Remote network attacker | No network listener exists: MCP is stdio and custody is a local Unix socket (`manifest.json:56-59`). The only network step is explicit model acquisition. | Only at model acquisition (§4.7) |
| Root, kernel, custodian account or authorized-client compromise | Excluded by design (`threat-model.md:15-20`). | Out of scope |

## 4. Trust boundaries and threats

**Status vocabulary:**
- **Mitigated:** a control exists and is tested in-repo.
- **Partially mitigated:** a control exists, but a named part of it is untested or open.
- **Accepted residual:** an owner decision or design choice, disclosed.
- **Out of scope:** excluded by the threat model.
- **UNEVALUATED:** no evidence either way.

The STRIDE letters are S (spoofing), T (tampering), R (repudiation), I (information
disclosure), D (denial of service) and E (elevation of privilege).

### 4.1 B1: stdio MCP host channel

| ID | STRIDE | Threat | Control | Evidence | Status |
|---|---|---|---|---|---|
| M1 | E | Compromised host approves trust, activates policy or restores a backup | Trust, policy, evidence, backup and doctor operations are CLI-only. No MCP tool is advertised for them (`docs/operations.md:470-473`; 16-tool manifest checked against the generated reference, `docs/AUTHORITY.md:10-11`) | `scripts/check_generated_docs.py:24, 63, 90` (runtime-reference drift and the exactly-16-tools check); `tests/unit/test_docs_v1.py::test_current_generated_snapshot_matches` | Mitigated |
| M2 | T | Host-initiated lifecycle mutation (sharpen, promote, archive) applied without review | The tool creates a proposal and returns `requires_approval` unless `cfg.autonomous` is set (`src/magicite/mcp/bind_lifecycle.py:146-152`). The default is `False` (`config.py:287`; env `MAGICITE_AUTONOMOUS`, `config.py:440`). Revival never auto-executes. Verification status is server-assigned and an injection scan runs on every register or sync (`src/magicite/core/registry.py:570-582`) | `tests/unit/mcp/test_bind_lifecycle.py::test_sharpen_review_mode_creates_a_proposal_and_does_not_touch_the_file`, `::test_promote_revival_never_auto_executes_even_under_autonomous_mode`, `::test_promote_quarantines_on_injection_scan_hit` | Mitigated by default. **Accepted residual** in autonomous mode (owner decision 2026-10-02): opt-in via `MAGICITE_AUTONOMOUS`, off by default, assumes a trusted MCP host. Expiry: at the v1 GA decision or 2027-01-31, whichever comes first (F-19) |
| M3 | I | Body read of revoked, unadmitted, stale or policy-drifted content | `load_skill_body` requires the content and policy digests. It re-reads one authenticated snapshot and refuses on drift, `not_admitted`, `trust_unavailable` or a snapshot mismatch (`src/magicite/mcp/bind_retrieval.py:465-522`) | `tests/unit/mcp/test_v1_retrieval.py::test_stale_body_denied`, `::test_durable_trust_revocation_blocks_previously_routed_body`, `::test_cas_policy_activation_blocks_previously_routed_body`; TAC-038 | Mitigated |
| M4 | I | Error envelope leaks paths, keys or raw query text | `_error_result` redacts and replaces messages and hints (`src/magicite/mcp/app.py:179-186`). Path and secret redaction is in `src/magicite/mcp/redact.py:15-60` | `tests/unit/mcp/test_redact.py`; `tests/unit/obs/test_th_custody_zero_write_canary.py::test_mcp_error_payload_and_logs_carry_no_secret`, `::test_mcp_error_scan_has_teeth` | Mitigated |
| M5 | T | Path traversal through `register.path` or `export.out_dir` | `_resolve_scan_root` raises `PathOutsideProjectError` (`src/magicite/core/registry.py:305-313`). Export uses the same function (`registry.py:1074-1075`) | `tests/unit/core/test_registry_core.py::test_register_rejects_path_outside_project`. No MCP `export` escape test identified (F-18) | Partially mitigated |
| M6 | T/D | Malformed or extra tool arguments | Pydantic `model_validate` with `extra=forbid` (`app.py:357`; `src/magicite/mcp/schemas.py:23-24`). Unexpected exceptions map to `internal error` (`app.py:489`) | `tests/unit/mcp/test_schemas.py`, `test_dispatch_call.py`. MCP dispatch is **not** a fuzz target (`tests/fuzz/test_bounded_fuzz.py:316-325`) | Partially mitigated |
| M7 | T | Outcome-signal poisoning of learned routing | Adaptive selection is experimental and the stable default is `dense-v1` (`docs/AUTHORITY.md:18-19`). Containment is the LEARNING gate's obligation | Not assessed here | UNEVALUATED (LEARNING gate scope) |

### 4.2 B2: custodian Unix socket and custody profile

| ID | STRIDE | Threat | Control | Evidence | Status |
|---|---|---|---|---|---|
| C1 | S | Foreign-UID peer impersonates the custodian or the client | Kernel peer credentials (`SO_PEERCRED` or `getpeereid`) in `check_peer` (`src/magicite/core/trust_custodian_transport.py:36-53`). client UID check (`:397-398`) | TAC-141, TAC-142 (fixture peer credentials); `test_trust_custodian_transport.py::test_actual_peer_credentials_match_current_uid` (same UID only) | Partially mitigated: separate-UID deployment UNEVALUATED |
| C2 | T | Receipt or message rewritten in flight | Ed25519 over the domain-tagged canonical payload, bound to nonce, registry, epoch, operation and request (`trust_custodian_transport.py:29, 358-386`) | TAC-001, TAC-002, TAC-016 to TAC-020, TAC-022, TAC-023; fuzz `custodian.verify_receipt` | Mitigated |
| C3 | T/S | Replay, nonce or receipt substitution, frame splice | Fresh random nonce and request ID per call (`:399`); binding check (`:373-381`) | TAC-009 to TAC-015, TAC-049, TAC-054, TAC-110 | Mitigated |
| C4 | D | Oversized, fragmented, slow-trickle or deeply nested frames | `MAX_FRAME` and `MAX_LOGICAL` (`:30-31`), a per-frame and per-message deadline (`:236-246, 313-333`), and `RecursionError` normalisation (`:138, 264, 280`) | TAC-089 to TAC-101; fuzz `custodian.receive_frame` and `receive_message` | Mitigated in-repo. Real-socket transport not fuzzed (`manifest.json:56-59`) |
| C5 | E | Profile or keys reachable by the registry writer; same-UID production profile; service run as the client | `protected_path` rejects symlinks, foreign owners, group- or world-writable ancestors and extended ACLs (`:56-111`). Key file mode check (`src/magicite/core/trust_custodian.py:160`) | TAC-026 to TAC-030, TAC-139, TAC-140, TAC-143, TAC-152 (key world-readable, `trust_custodian.py:160`); `test_trust_custodian_transport.py::test_same_uid_production_profile_denied`, `::test_production_service_refuses_running_as_client_identity` | Partially mitigated: the Darwin `acl_get_fd_np` branch and Linux `listxattr` error paths are untested (OI-06, PR #38) |
| C6 | T | Type-confused custodian record payloads | `_validate_payload` structure checks (`trust_custodian.py:388-402`). Fixed in F-04 | TAC-107, TAC-108; fuzz `custodian._validate_payload:*` | Mitigated |
| C7 | I | Custody secrets or internals exposed through errors | Redacted protocol errors | TAC-154; `test_trust_custodian_transport.py::test_missing_operation_is_redacted_protocol_error_not_uncaught_keyerror` | Mitigated |
| C8 | T | Duplicate JSON keys on the custody wire (accepted last-wins) | Receipts are verified over the canonical re-encoding of the parsed payload | `manifest.json:71-75` (observation) | Accepted residual (F-15) |

### 4.3 B3: bundle intake and import

| ID | STRIDE | Threat | Control | Evidence | Status |
|---|---|---|---|---|---|
| BI1 | T/E | Zip-slip, absolute, backslash, drive or UNC paths; symlinks; case-fold collisions; duplicate members | `_assert_safe_member_path` (`src/magicite/core/bundles.py:204-230`). Symlink, case-fold and duplicate checks (`:286-308`) | TAC-069 to TAC-074, TAC-082, TAC-084, TAC-087 | Mitigated |
| BI2 | D | Decompression bomb, aggregate size or entry-count flood | Limits (`bundles.py:28-30`) and a streamed remaining budget (`:329-345`) | TAC-075 to TAC-077 | Mitigated |
| BI3 | T/S | Smuggled, unsigned, unpinned or revoked-signer bundles; resource swap under a valid manifest | `verify_manifest_signature` refuses without local roots (`:174-198`). Staged files must match manifest digests exactly (`:348-386`) | TAC-003, TAC-025, TAC-078 to TAC-081 | Mitigated |
| BI4 | T/D | Corrupt archive leaks untyped errors and parser text, or leaves staging behind | Archive corruption maps to a fixed `InvalidInputError` (`bundles.py:233-260`). Staging and the outer temporary directory are removed on failure (`:437-441`). Fixed in F-05 | TAC-083; fuzz `bundles.verify_bundle`; `tests/unit/core/test_fuzz_regressions.py` | Mitigated. Structured member-table mutations are not fuzzed (OI-19, PR #47) |
| BI5 | T | Non-canonical or duplicate-key manifest | `_reject_duplicate_keys` (`bundles.py:137-141`) | TAC-102, TAC-103, TAC-109 | Mitigated |
| BI6 | S | External file claims authored or verified origin | Server-owned origin; intake is pending or quarantined | TAC-131 (`tests/unit/core/test_trust_intake.py::test_forged_origin`) | Mitigated |

Signature validity is not evidence of safety or efficacy (`release-gates.md:37`). That
risk is out of scope (§6).

### 4.4 B4: writable registry and filesystem (same-UID local writer)

| ID | STRIDE | Threat | Control | Evidence | Status |
|---|---|---|---|---|---|
| R1 | T/I | Erase or forge decision mirrors or projections to resurrect a revoked engram (original v1 critical) | Authority comes only from authenticated custody history. Mirror and projection inertness is witnessed by TAC-035 to TAC-040 | TAC-035 to TAC-040; r2 `trust-mirror-loss-custody.json` `NOT_REPRODUCED` (fixture custody); F-01 | Mitigated in-repo |
| R2 | T | Tamper, truncate, roll back, splice or resequence the local journal or head | Custody-verified replay; append only to the exact verified inode and size (`src/magicite/core/trust_journal.py:241-268`) | TAC-004 to TAC-010, TAC-041 to TAC-044, TAC-050 to TAC-060; `test_trust_journal.py::test_concurrent_local_write_during_append_window_closes_and_drops_cache` | Partially mitigated: a same-size in-place overwrite *inside* the append window is caught only when the cache drops and a cold re-verify fails closed (`trust_journal.py:259-262`). This residual is **untested in-repo**: TAC-053 splices outside the window, and the append-window test uses a size-changing write. Accepted with F-09's expiry |
| R3 | T | Mutate policy or root projections to pin an attacker root or un-revoke | Policy is read from the authenticated snapshot | TAC-122 to TAC-127 | Mitigated |
| R4 | T | Truncate or corrupt approval mirrors to reopen fresh-install policy routing | `prior_policy_governance_evidence` raises on unreadable or malformed mirrors (`src/magicite/core/policy_store.py:131-180`). Route maps the error to `policy_store_corrupt` (`src/magicite/core/router.py:1279-1290`). Fixed in F-02 and F-03 | `test_policy_governance_evidence.py::test_truncated_json_mirror_fails_closed_with_typed_error`, `::test_unreadable_mirror_fails_closed_with_typed_error`; `test_router_policy.py::test_malformed_mirror_without_store_is_policy_store_corrupt` | Mitigated |
| R5 | T | Pending, prepared or intake state treated as committed authority | Pending closes reads until exact reconciliation | TAC-128 to TAC-135 | Mitigated |
| R6 | T/E | Re-enrollment, implicit enrollment or re-genesis resets history | Explicit reviewed genesis. Resume only over a byte-identical journal (`trust_journal.py:374-400`) | TAC-146 to TAC-151; `test_th_retry_matrix.py::test_genesis_init_refuses_nonidentical_partial_state` | Mitigated |
| R7 | T | A stale or paused lease holder commits after a newer fence | Custody predecessor captured under the flock (`src/magicite/storage/lease.py:353-371`); custodian compare-and-swap; writer guard (`src/magicite/core/writer_guard.py:71-127`) | TAC-021, TAC-111 to TAC-118, TAC-126; `test_custody_writer_guard.py` | Mitigated in-repo |
| R8 | T/D | Crash or lost reply at a persistence boundary breaks acknowledgement order | Journal before head; exact-retry semantics (F-06) | TAC-011, TAC-119 to TAC-121; `test_th_fault_ack_matrix.py`, `test_th_retry_matrix.py` | Partially mitigated: real process-kill, wire-level lost reply and a literal fsync trace are deferred (OI-10, OI-15, PR #43) |
| R9 | T | Two trust snapshots in one operation observe different heads | One validated snapshot per operation (`registry.py:1948-1975`; `src/magicite/core/trust.py:577-600`). Fixed in F-07 | `test_th_single_snapshot_sites.py` (AST enumeration and drift tests) | Partially mitigated: enumerator gaps (OI-18, PR #46) |
| R10 | R/T | Approval replay skips local admission or the approval audit | `review_approve` replays by `event_id` (`registry.py:1808-1874`) | None for the post-commit path | **Open** (F-11) |
| R11 | R | Operator cannot diagnose custody state; diagnosis writes state | Doctor is zero-write | `test_th_custody_zero_write_canary.py::test_doctor_and_custody_status_write_nothing_under_custody_states` | Partially mitigated: no custody probe in doctor (F-12; OI-09, OI-14, PR #40) |

### 4.5 B5: backups, restore and migration

| ID | STRIDE | Threat | Control | Evidence | Status |
|---|---|---|---|---|---|
| BK1 | T | Restored overlay or MAC record not in custody history; planted mirror in a snapshot | Overlay authority checked against custody | TAC-031 to TAC-034; `tests/integration/test_th_restore_overlay_authority.py` | Mitigated |
| BK2 | T | Restore drops a post-backup revocation | Recovery gate requires the retained authenticated suffix | TAC-045 to TAC-048, TAC-064; `test_th10_old_reader_downgrade.py::test_supported_backup_restore_keeps_post_backup_revoke` | Mitigated |
| BK3 | T | Supported downgrade or migration paths reactivate revoked content | Legacy restore only into explicit inactive staging (`src/magicite/core/migration.py:1211-1228`) | TAC-061 to TAC-068; `test_th10_old_reader_downgrade.py` | Mitigated (supported paths, TH-A01) |
| BK4 | I | Backup exposes the fingerprint or control key | Secrets are excluded unless an encrypted-custody path is supplied (`src/magicite/core/backup.py:1063-1064` skips secret paths unless `include_secrets`; `:1079-1083` refuses `include_secrets` without `encrypted_custody_path`). Magicite does not encrypt destinations (`docs/operations.md:494-499`; `SECURITY.md:12-13`) | TAC-153; `test_th_custody_zero_write_canary.py::test_backup_archive_members_contain_no_custodian_secret` | Mitigated for archive contents. **Accepted residual**: destination confidentiality is the operator's responsibility |
| BK5 | T/I | A pre-hardening binary run on hand-rolled-back bytes re-discloses revoked content | Operator procedure (`docs/operations.md:486-492`). The supported runtime still enforces the revocation | TAC-042 (supported half only); TH-A01 evidence (c2) | **Accepted residual** (TH-A01, F-10) |

### 4.6 B6: OCI image and supply chain

| ID | STRIDE | Threat | Control | Evidence | Status |
|---|---|---|---|---|---|
| D1 | E | Container runs privileged against the host project | Non-root UID 10001 (`Dockerfile:105, 128`). `--cap-drop ALL` and `no-new-privileges` are documented for `docker run` and are not enforced by the image (`Dockerfile:56-63`). With `--user` the container is the same OS principal and no boundary is introduced (`Dockerfile:47-53`) | `tests/acceptance/test_docker_smoke.py` (CI, `.github/workflows/ci.yml:201-202`) | Partially mitigated (by design, no isolation boundary) |
| D2 | T | Vulnerable or substituted base image or dependencies | Base images pinned by digest (`Dockerfile:67, 87`); lockfile export (`Dockerfile:82`); security upgrades (`Dockerfile:98-100`). Trivy HIGH/CRITICAL gate with `ignore-unfixed: true` (`ci.yml:251-258`, `ignore-unfixed: true` at `:258`; compare `SECURITY.md:5`, "scanner failures block integration") plus a full-severity SARIF upload (`ci.yml:236-249`) | No scanner output is recorded in the v1 or r2 release evidence | UNEVALUATED (F-17, scribe observation) |
| D3 | T/S | Published-artifact provenance (signatures, SBOM) | DISTRIBUTION gate obligation (`release-gates.md:20`) | Out of this document | UNEVALUATED (DISTRIBUTION gate scope) |

### 4.7 B7: model acquisition

| ID | STRIDE | Threat | Control | Evidence | Status |
|---|---|---|---|---|---|
| MA1 | I/T | Runtime network fetch of the model | Offline by default (`embedding_offline` is True, threaded into `local_files_only`, `src/magicite/embeddings/fastembed_provider.py:134`); image sets `MAGICITE_EMBEDDING_OFFLINE=1` (`Dockerfile:136`) | `tests/unit/embeddings/test_fastembed_provider.py::test_default_is_offline`, `::test_first_embed_passes_offline_flag_as_local_files_only` | Mitigated |
| MA2 | T | Tampered model downloaded by `magicite fetch-model` | No in-repo digest or revision pin was found in `fetch_model` or `fastembed_provider.py`. Integrity relies on the upstream library and host | None | UNEVALUATED (F-16, scribe observation) |

## 5. Status summary

| Boundary | Threats | Mitigated | Partially mitigated | Accepted residual | Open | UNEVALUATED |
|---|---|---|---|---|---|---|
| B1 MCP | 7 | 3 (M1, M3, M4) | 2 (M5, M6) | 1 (M2, autonomous mode) | 0 | 1 (M7) |
| B2 Custody socket | 8 | 5 (C2, C3, C4, C6, C7) | 2 (C1, C5) | 1 (C8) | 0 | 0 |
| B3 Bundles | 6 | 6 | 0 | 0 | 0 | 0 |
| B4 Registry | 11 | 6 (R1, R3 to R7) | 4 (R2, R8, R9, R11) | 0 | 1 (R10) | 0 |
| B5 Backups/migration | 5 | 3 (BK1 to BK3) | 0 | 2 (BK4, BK5) | 0 | 0 |
| B6 OCI | 3 | 0 | 1 (D1) | 0 | 0 | 2 (D2, D3) |
| B7 Model | 2 | 1 (MA1) | 0 | 0 | 0 | 1 (MA2) |
| **Total** | **42** | **24** | **9** | **4** | **1** | **4** |

M2 counts as an accepted residual for autonomous mode only (owner decision 2026-10-02,
F-19). Its default is mitigated. C4 and BI4 are mitigated in-repo, with fuzz-coverage limits stated in
their rows. "Mitigated" always means *tested in-repo*. It never means deployment-qualified.

## 6. Out of scope and disclosed residuals

- **Separate-UID custodian deployment (Linux and macOS):** UNEVALUATED. Peer and
  protected-path logic is exercised only with injected fixtures. The task authorizes no
  account creation or privilege escalation (`manifest.json:46-49`; AC-TH-04 and
  AC-TH-12 rows).
- **TH-A01 hand rollback with a pre-hardening binary:** a frozen `22ae4e0`-or-earlier
  binary on hand-copied pre-migration bytes routes and discloses the revoked subject.
  The supported runtime keeps the revocation authoritative. Mitigation is operator
  procedure (`amendments/TH-A01-ac-th-10-supported-downgrade-scope.md:18, 50`;
  `threat-model.md:22-29`).
- **Compromise of the custodian, root, kernel, keys or the authorized client:** out of
  guarantee. So is rollback of the custodian and every independent copy
  (`threat-model.md:15-20`).
- **Real network transport:** none exists. Wire attacks use in-memory fakes
  (`manifest.json:56-59`).
- **Content safety and human intent:** an authorized reviewer can admit malicious
  content. Signatures establish identity and integrity, not safety or efficacy
  (`manifest.json:66-69`; `SECURITY.md:13-14`).
- **Third-party certification:** none is claimed (`release-gates.md:37`).

## 7. Gaps

- [GAP] SECURITY, including `critical-resolved`, stays UNEVALUATED until two things
  happen: the maintainer's sign-off (Henrique Aparecido Lavezzo, recorded by approving
  the B3 PR; **pending**) is recorded, **and** the r3 evidence package adjudicates the
  gate. No third-party certification is claimed. See the findings register.
- High findings carry a named reviewer, an exploitability assessment and a mitigation
  (`release-gates.md:37`). The accepted residuals F-10, F-19 and the F-09 residual expire at the v1 GA
  decision or 2027-01-31, whichever comes first, and must be re-reviewed before any GA
  sign-off.
- [GAP] No release evidence records vulnerability scanner output (F-17).
- [GAP] The bounded fuzz does not cover real-socket transport, end-to-end
  `CustodianService.handle`, the `artifact_transform` kind, structured zip member-table
  mutations or `parse_file` (OI-19, PR #47).

<!-- provenance: author=IDG scribe (r3 slice B3); sources=release-gates.md, trust-hardening plan (threat-model, TH-A01, implementation-notes), docs/AUTHORITY.md, docs/operations.md, SECURITY.md, r2 gate-table and evidence, tests/fixtures/adversarial/trust/manifest.json, tests/fuzz, code at bcdf680, r3-open-items.md (PR bodies #34-#50); date=2026-10-02; review=PENDING independent security review -->
