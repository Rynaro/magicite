# Disposable v1 operator tutorial

This path tests local-source installation, a real generic stdio client, external
intake and explicit review, route explanations and digest-bound body disclosure,
backup/restore, and format upgrade/downgrade. It uses hashing fixtures and creates
only temporary projects. It is **not** an independent operator transcript, a real
Claude Code session, a published-channel install, or production model evidence.
Those obligations remain UNEVALUATED.

With the repository's pinned dependencies installed (`uv sync --all-extras`), run:

```sh
uv run python scripts/smoke_operator_tutorial.py --output /tmp/magicite-tutorial.json
```

The script creates a disposable interpreter, installs this source package, and
reuses the already installed dependency distributions. The install may acquire
build dependencies; subsequent hashing runtime operations are offline. Its JSON
captures actual outputs with local workspace paths redacted. Any failed required
step exits nonzero. The steps below describe the same executed path; replace the
fixture project path only when intentionally operating on a real registry.

1. Start `magicite serve --project-root PROJECT`; the generic SDK client negotiates
   its actual protocol, lists exactly 16 tools, and calls `register` on the supplied
   `incoming` fixture directory. This is external intake, so content cannot grant
   itself trusted origin or admission.
2. Run `magicite trust review --project-root PROJECT --engram-id ID`, inspect its
   content digest, then explicitly run `magicite trust approve --project-root PROJECT
   --engram-id ID --expected-digest DIGEST --actor OPERATOR`. Tutorial approval is
   limited to its known fixture and never grants trust to a real downloaded skill.
3. Call `route` with the fixture query. Inspect status, reason codes, exclusions,
   policy identity and selected content digests. Call `load_skill_body` with the
   chosen name, `level: L2`, `expected_content_digest` and `expected_policy_digest`.
   A stale content digest is deliberately submitted and must return `stale_decision`;
   reroute to obtain current identities before retrying. An abstention is a valid
   safety outcome, not permission to bypass body checks.
4. Run `magicite doctor --project-root PROJECT`. It never repairs or writes. A tiny
   fixture may report an unavailable production model or insufficient empirical
   evidence; inspect captured findings rather than treating them as a release pass.
5. Run `magicite backup create --project-root PROJECT --dest SNAPSHOT` and
   `magicite backup status --project-root PROJECT --backup-path SNAPSHOT`.
   Use the existing `backup.build_recovery_overlay` and `issue_sequence_anchor`
   domain APIs to preserve current authenticated control metadata in independent
   custody. The script advances beyond the observed snapshot control sequences; real
   custody must preserve monotonically increasing sequence history. Restore with
   `magicite backup restore --project-root PROJECT --backup-path SNAPSHOT
   --overlay OVERLAY_JSON --anchor ANCHOR_JSON`. Missing/stale custody stays closed.
6. Run `magicite migration preview --project-root PROJECT` before upgrade. Then
   `magicite migration apply --project-root PROJECT --operation-id YOUR_ID`, inspect
   `magicite migration status --project-root PROJECT --operation-id YOUR_ID`, and
   use `magicite migration resume` with that same ID after interruption. A matching
   pre-upgrade backup is required for `magicite migration restore --project-root
   PROJECT --backup-path BACKUP_FROM_APPLY`; tampered/unmatched backups are rejected.

Policy registration/review is separate from promotion: `policy register-evaluated
--manifest FILE --evaluation-status STATUS --evidence REFERENCE`, then `policy approve
--policy-digest DIGEST --actor OPERATOR`, followed only when authorized by CAS activation.
Interrupted policy commits use `policy reconcile`; missing/conflicting mirrors remain
closed. No tutorial step promotes an empirically unvalidated alternative policy.

For fingerprint-key custody, the domain API
`backup.create_snapshot(cfg, conn, dest, include_secrets=True,
encrypted_custody_path=operator_managed_path)` copies the key to that destination.
It does not encrypt; provision actual encrypted custody separately. Ordinary backups
exclude the key. A backup protects its recorded recovery point, not later uncheckpointed
receipts. See [operations](operations.md) and [support/deprecation policy](support-policy.json).

## Explicit evidence checkpoint

Ordinary MCP routing remains read-only. To make a new decision durable, explicitly
run `magicite evidence checkpoint --project-root PROJECT --route-request -
--event-id EVENT_ID` and supply a RouteInput JSON object on stdin. This command
runs the real router in its own process and checkpoints that exact server-built
decision. It does not recover an earlier uncheckpointed MCP receipt. Raw queries
are used only in memory; a local keyed request fingerprint binds event-ID retries.
Identical retries return the original decision; changed input with the same event
ID conflicts. The tutorial executes this mode and verifies replay.

For delayed feedback use `magicite evidence checkpoint --project-root PROJECT
--decision-event-id ORIGINAL_EVENT --event-id OUTCOME_EVENT --outcome success`.
The original committed decision supplies selected content revisions and policy
identity. Missing/deleted original events fail closed. The CLI always records
operator feedback as tier-1 self_reported; it cannot claim a host verifier or
promote this feedback to efficacy evidence.
