# Functional v1 release path — approved

**Status: approved.** The maintainer replied “Approved” on 2026-10-11 to the bounded scope and four-checkpoint roadmap below. It does not publish a release or accept undisclosed security residuals; any material finding not covered by the recorded security disposition needs its own decision.

## Four checkpoints

1. **Approve the v1 promise and channels.** Bound v1 to the current MCP dense knowledge workflow, local lifecycle, Linux protected-custody instructions, Python 3.11/3.12, and `magicite init --host claude` project connection. State plainly that 10,000-item performance, retrieval-quality improvement, production adoption, and macOS production custody are unqualified; do not claim 1,000-item qualification from the diagnostic run. Review the known security residuals and record owner disposition without calling them certified. Recommended first-release channels: GitHub wheel/sdist and signed OCI. Production PyPI may be added only if the trusted publisher is confirmed during release preparation; otherwise defer it and document the available channels accurately.
2. **Prepare one release-only change and freeze the candidate.** Set package version to `1.0.0` in `pyproject.toml` and `uv.lock`; align the current README, generated reference, Install/Setup/Quick Setup/AUTHORITY docs, and release notes with the approved scope and channels. Correct obsolete RC1/package-init statements. Preserve prior RC evidence as historical. Record the owner’s security-residual disposition. Freeze the current integration at `90e97ea366c6f6a921cd508c1f461f34af95b56d` plus this release-only delta.
3. **Run existing required CI once on the exact candidate.** Require the existing checks to pass, including generated-document and installed-package checks. Repair an actual failure and rerun affected required checks. Do not add a new performance, platform, or qualification campaign.
4. **Publish the approved candidate, then verify delivery.** After the scope is approved, candidate checks pass, and the existing security disposition is recorded, tag `v1.0.0` and use the existing release workflow. Verify downloaded wheel/sdist hashes and provenance, OCI digest/signature, and the documented install and project-connection smoke using published artifacts. Record one release receipt and finish.

## Deferred issues

These are tracked after v1; they are not prerequisites in this proposed release path. Issues #77–#81 carry `status:post-v1`, `type:qualification`, and their respective `area:*` labels. Issue #82 carries `status:post-v1` and `area:documentation`, without `type:qualification`. They have no milestone or assignee. The broader assessment in #81 and its nonblocking follow-ups are deferred; checkpoint 1 still requires an explicit disposition of the known critical/high findings for this release.

- [#77 — Quality](https://github.com/Rynaro/magicite/issues/77) — post-v1 quality qualification.
- [#78 — Composition](https://github.com/Rynaro/magicite/issues/78) — post-v1 composition qualification.
- [#79 — Performance](https://github.com/Rynaro/magicite/issues/79) — post-v1 scale/performance qualification.
- [#80 — External validation](https://github.com/Rynaro/magicite/issues/80) — post-v1 independent reproduction or pilot evidence.
- [#81 — Formal security](https://github.com/Rynaro/magicite/issues/81) — post-v1 broader security assessment and nonblocking follow-ups.
- [#82 — RC historical evidence](https://github.com/Rynaro/magicite/issues/82) — document and link existing RC evidence; documentation label, not a new RC execution requirement.

**Stop condition:** once the approved scope is published as v1.0.0, its artifacts and provenance are verified, and the install/connection smoke is recorded, this roadmap is done. Report a real release-blocking defect separately; do not automatically restart the qualification campaign.

**Provenance:** based on the release-path analysis at [`/private/tmp/magicite-v1-release-reconciliation/release-path.md`](/private/tmp/magicite-v1-release-reconciliation/release-path.md) and the bounded status audit at [`/private/tmp/magicite-v1-release-reconciliation/audit.md`](/private/tmp/magicite-v1-release-reconciliation/audit.md). Approval authorizes this bounded execution path. Artifact publication and verification are still pending; approval is not evidence of qualification or acceptance of undisclosed risks.
