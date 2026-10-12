---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-10-06
---
# Linux custody boundary and bounded recovery qualification

## Scope
CHANGE: close a bounded, real-environment evidence gap in AC-TH-04/05/09/12 and S04/S12 using the existing production custody implementation. Base e8fa127c9e21e24f3da5124f22d404e83e9e0601, branch codex/v1-custody-qualification, worktree /Users/henrique/.codex/worktrees/v1-r3-qualification/magicite.
In: actual hosted Linux kernel peer credentials with separate numeric UIDs; protected deployment layout; selected real process deaths around prepared/committed/acknowledged revocation; pre-revoke backup recovery with current authenticated suffix or fail-closed absence; candidate-bound report.
Out: macOS separate-UID qualification, exhaustive crash/fault matrix, full OS ACL/xattr branch coverage, performance, full release campaign, production account changes, human risk approval. PR51 remains unmerged/pending. Immutable docs/releases/v1/** evidence and the user's primary checkout remain unchanged.
Mechanical size: lite score4 (6 files, security, high stakes); complexity9/12 extended. This is standard-tier native role orchestration, not live Gauge acceptance.
Environment: local Darwin UID501; Docker daemon inaccessible, no local Linux claims. Run actual Linux witness on ephemeral hosted Ubuntu runner. Root privileges only set up the isolated fixture/drop child privileges; require successful privilege and environment preflight, never green-skip the qualification job. Numeric setgid/setuid needs no persistent accounts.

## Approach
Adapt production CustodyProfile, CustodianClient, CustodianService/serve and existing backup/recovery contracts. Do not inject FixtureCustodian or monkeypatch peer identity/permission checks for the production qualification cases. Tests may use explicit barriers to pause child processes at observed persistence boundaries; death must be a real OS process kill, not an in-process raised exception. Record which process/boundary was killed.
All protected-path ancestors are validated. Create a unique ephemeral root-owned tree under a safe /var/lib or /opt ancestor, not /tmp, RUNNER_TEMP or the runner checkout. Use separate custodian-owned private0700 state/key directories and a correctly protected shared socket parent. Root fixture setup chooses distinct numeric writer/custodian/foreign-peer UIDs, then clears supplementary groups before setgid/setuid and runs relevant clients/service with those real credentials. Install the protected enrollment descriptor under the ephemeral runner’s /etc/magicite/registries/<projecthash>.json using the exact root-installable descriptor produced by the production custody profile CLI for the canonical project hash; never fabricate it through an injected profile or patched resolver. Give the writer exactly the access the production profile permits; test actual denied opens/writes of keys, profile and descriptor. Socket mode may permit connecting peers while real kernel peer enforcement rejects unauthorized requests. Clean only the explicitly owned fixture tree and descriptor after child shutdown. A SIGKILL leaves a stale socket because serve does not unlink preexisting sockets: prove the old PID exited, record explicit operator cleanup of only the known fixture socket, then relaunch. Do not claim automatic stale-socket recovery. Do not change /etc accounts or host-wide permissions.
Small complete scenario: valid enrolled workflow → admit/revoke → kernel peer negatives → three controlled death/restart cases → pre-revoke backup recovery pair. Reuse live service and production socket transport for Linux boundary assertions. Production-path recovery tests must not borrow fixture-only results and relabel them as deployment evidence.
Selected hypothesis88; actual score is recorded in state. Alternatives: extend fixtures only79 (cheap but cannot advance deployment evidence); multi-platform framework75.5 (reusable but expands scope). Use bounded scripts/tests and a dedicated hosted job.

## Stories
1. As an operator, I need proof that the deployed Linux peer/custody boundary works under real process credentials. Timebox1d, riskP0, executor standard Vivi. Implement isolated numeric-UID fixture, valid production service/client exchange, same-UID production-config rejection, unauthorized foreign-peer rejection, and writer-denied access to protected secret/authority/profile bytes. Report actual UIDs/PIDs and Linux/kernel/platform metadata.
2. As an operator, I need acknowledged revocation to survive process death and old-backup restoration. Timebox1d, riskP0, executor standard Vivi. Exercise (a) client death after prepare before durable commit, (b) client death after durable custodian commit before local finalization, (c) service death/restart after revocation acknowledgement. Use bounded deterministic IPC barriers, not sleeps as synchronization. A pause after the real durable commit and before reply is disclosed harness instrumentation; no production peer/path/authority validation may be monkeypatched. Valid unacknowledged preparation must reconcile under the existing active fence or remain explicitly closed; never silently discard it into admission. Exact committed replay has one effect. Restore pre-revoke backup with retained authenticated suffix and current independent anchor; without suffix, assert restricted fail-closed recovery. Existing injected fixtures remain supporting tests only.
3. As a reviewer, I need a reproducible candidate-bound record that distinguishes observed subclauses from remaining gaps. Timebox1d, riskP1, executor Vivi for witness/report, separate checker for claims. Hosted job runs against exact PR/candidate SHA, fails on missing root/Linux/preconditions or failing cases, uploads machine-readable report and test logs even on failure. Report source SHA, source hashes, runner/kernel/Python versions, test IDs, subprocess UID/PID/boundary data, commands, exit results and artifact hashes. No private-key bytes or sensitive configured paths in report. A later evidence-only documentation commit may summarize the tested candidate; source changes require new tests.

## Owned paths
- tests/integration/test_custody_linux_qualification.py (new, real process/socket/recovery witnesses)
- tests/integration/custody_qualification_support.py (new only if subprocess helper needed)
- scripts/qualify_custody_linux.py (new, preflight/report runner if needed)
- .github/workflows/custody-qualification.yml (new, PR-triggered plus manual trigger)
- docs/qualification/linux-custody.md (new concise results/reproduction/limits)
- .spectra/* (plan/results handoff)
These are allowed surfaces, not a requirement to create unnecessary helpers. Do not edit production trust code unless a concrete failure is reproduced and a narrow scope amendment is recorded first. No r3 package rewrites.

## Acceptance Criteria
Authoritative atomic checks are v1-custody-qualification.acceptance.md, frozen at Assemble. Meeting them qualifies only the explicitly exercised Linux setup and selected failure boundaries. Whole AC-TH-04/05/09/12, SECURITY, RELIABILITY and GA statuses do not become PASS by implication.

## Verification and completion
Run native-safe harness/unit checks locally using /private/tmp/magicite-r3-qualification/venv/bin/python, then run the real Linux job on actual candidate. A native Darwin skip is not the required witness. Hosted job must fail rather than skip its core cases when prerequisites are absent. Review code/harness independently; run affected tests, lint/type and full supported suite once after final changes. Preserve practical CI runtime by reusing existing helpers where correct and avoiding redundant independent pipelines.
Complete this bounded slice only with successful actual Linux core cases, source-bound artifacts and independent code/claim review. If runner access/privilege is unavailable, finish harness/preflight/docs and return exact blocker with Linux criteria UNEVALUATED; do not substitute fixture PASS. macOS deployment and unexercised crash/ACL/xattr branches remain named follow-ups.

## Confidence
Mechanical confidence93.25% → AUTO_PROCEED, recorded at Assemble. Confidence concerns the bounded plan, not success of currently unrun kernel/recovery experiments.

## Handoff
Receiver Vivi; separate maker/checker. Root controls commits/PR and actual candidate identity. Return changed files, tested SHA, acceptance-to-test/result mapping, hosted run/artifact identifiers, exact remaining claims and any necessary code repair proposal. Do not revert other work.
