---
spec: engram/0.2
name: magicite-release-container
id: egr_577e0d1e
version: 1
provenance: authored
intent:
  does: "Cut a Magicite release on the container channel and understand why the PyPI wheel channel is gated off"
  use_when: "publishing a new Magicite version, pinning a digest for consumers, or deciding whether to enable wheel publishing"
  not_when: "diagnosing a container that fails to boot on a user's machine, which is a runtime privilege problem rather than a release one"
triggers:
  positive:
  - "cut a magicite release and pin the image digest"
  - "why is magicite not on pypi yet"
  - "enable the gated magicite wheel publishing job"
  - "magicite release workflow container job review"
  negative:
  - "magicite container exits before the mcp handshake completes"
context_affinity: [magicite, release, ci, container, supply-chain]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: [magicite-frozen-verify, magicite-container-privilege-boundary]
yields: [published-pinned-release]
composes: []
inhibits: []
provenance_journal:
- version: 1
  timestamp: '2026-08-15T00:00:00Z'
  author: claude-orchestrator
  event: authored
  note: First-party dogfood registry (change magicite-dogfoods-itself, AC-D1)
trust:
  origin: authored
  verification_status: pending
---
## Procedure
1. Treat the container as the primary artifact. As of the first release the wheel channel is deliberately not published, so a release that produces no image has produced nothing users can install.
2. Run the frozen verify command before anything else, and separately confirm the container acceptance tests pass, including the offline handshake with network access denied.
3. Publish the image and record its digest, then pin that digest — not a moving tag — everywhere the project tells consumers how to run it, including the README quickstart and the adapter documentation.
4. Keep the hardened invocation intact in every published snippet: the explicit user mapping, all capabilities dropped, no new privileges, and the bind mount with the relabel flag. Users copy these snippets verbatim, so a weakened example becomes a weakened deployment.
5. Update the changelog with the honest claim scope rather than a feature list, and do not move the version further than the evidence supports.
6. Leave the wheel job gated until its prerequisite exists. It publishes via OIDC trusted publishing and is guarded by a repository variable, so enabling it before registering the publisher on the index produces a failing job rather than a wheel.
7. Follow the documented enabling order when the time comes: register the pending trusted publisher for this project, workflow, and environment on the index first, and only then flip the repository variable. The order matters and the runbook records it precisely.
8. Re-review the release workflow's container job whenever it changes, since a never-executed CI path is where missing secrets, missing permissions, and broken multi-architecture, signing, SBOM, or scanning steps hide.

## Pitfalls
- (x1) Publishing a moving tag and letting consumers pin to it, which silently changes what every downstream project runs.
- (x1) Flipping the publish variable before the trusted publisher is registered, producing a red release rather than a wheel.
- (x1) Trimming the hardening flags out of a quickstart snippet for brevity, which propagates directly into user deployments.
- (x1) Writing a changelog that lists capabilities while omitting the falsification results, which is the exact overclaim this project's own records exist to prevent.

## Examples
+ "we are ready to tag the next version" -> steps 2 through 5
+ "can we turn on pypi publishing today" -> only after step 7's registration
- "a user reports the container dies on startup" -> NOT this engram (privilege boundary at runtime)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the dogfood registry
