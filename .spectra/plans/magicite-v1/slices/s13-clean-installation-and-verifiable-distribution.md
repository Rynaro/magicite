# S13 — Clean installation and verifiable distribution

## Assignment

Status: ready to assign subject to dependency gates. Priority: P0 V1 release scope.
Build after: none; start immediately.
Merge after: S01, S02, S03, S04.
Executor: reasoning-capable implementation agent; follow repository Vivi workflow. This packet itself is specs only.
Work bound: one independently reviewed slice; split into smaller PRs at the listed action boundaries if needed. No elapsed-day deadline; completion is evidence-gated, per user direction.

Read [shared contracts](../contracts.md), [evaluation](../evaluation.md), [release gates](../release-gates.md) and [dispatch plan](../plan.json) before coding. Shared-file ownership in C9 overrides broad owned-path patterns below.

## Story and current evidence

As a new operator, I want a published package or signed image that works outside the source checkout.

PyPI publishing is conditional at .github/workflows/release.yml:274-287; wheel validation is dry-run/no-deps at :304-315 (H). Python >=3.11 is declared at pyproject.toml:7 while CI tests 3.11/3.12 at ci.yml:38-46 (H).

## Scope and contract

Release policy in release-gates.md. This slice can build installation/CI scaffolding immediately; final merge/release evidence consumes S01/S02/S03/S04. Domain CLI functionality comes through S11 later. Package account configuration is an external release prerequisite, not a task this planning request performs.

Owned implementation surfaces (future work, not modified by this packet):

- `pyproject.toml`
- `uv.lock`
- `Dockerfile`
- `.github/workflows/ci.yml`
- `.github/workflows/release.yml`
- `tests/acceptance/test_install_channels.py`
- `scripts/check_supply_chain.py`

Out of scope: unrelated refactors, changing another slice's contract or thresholds, claiming unrun verification, host execution inside the runtime, publishing a release without release authority.

## Action plan

1. Test built wheels/sdists outside checkout with resolved dependencies and packaged schema/migration resources.
2. Add pip/pipx/uvx/source and digest-pinned OCI probes with explicit model acquisition then blocked-network fixture routing.
3. Rehearse TestPyPI OIDC trusted publishing and wheel attestations; record project ownership/publisher setup requirement.
4. Generate release manifest, checksums, SBOM/provenance/signature checks and immutable artifact retrieval verification.
5. Wire existing and S01/S15 validation commands into CI only when their producing slices are merged; preserve negative/security finding visibility.

## Acceptance Criteria

### AC-S13-01 (event-driven)
GIVEN a built wheel in a clean temporary environment outside checkout
WHEN installation and fixture probe run
THEN the packaged runtime SHALL route the offline fixture
VERIFY: new tests/acceptance/test_install_channels.py::test_clean_wheel

### AC-S13-02 (event-driven)
GIVEN a fresh install without model cache
WHEN offline routing is attempted
THEN the error SHALL provide explicit model acquisition remediation
VERIFY: new test_install_channels.py::test_missing_model

### AC-S13-03 (event-driven)
GIVEN each advertised distribution channel
WHEN the published artifact is installed
THEN the same conformance fixture SHALL pass
VERIFY: new test_install_channels.py::test_channel_matrix with recorded channel versions/digests

### AC-S13-04 (event-driven)
GIVEN a release manifest and fetched image/wheel
WHEN signature/provenance/resource checks run
THEN every advertised artifact SHALL match its declared digest
VERIFY: extend scripts/check_supply_chain.py and published artifact verification job

## Verification and handoff

VERIFY targets above name expected future tests/gates; they are not claims that these tests exist or passed at spec time. Extend the cited existing tests where appropriate. Run focused tests first, then repository lint/type and affected integration suites; preserve current hot-path, writer lease, rebuild and idempotency invariants. Record actual commands, dependency/model pins and outputs. Empirical/release gates require their own artifacts beyond unit tests.

Deliver: implementation branch/base SHA, dependency SHAs, owned-path diff, contract fixtures, criterion-to-test/result mapping, migration/rollback notes, and any unpassed criterion. Do not mark complete merely because tests for the happy path pass. Integration owner applies shared-file patches in dependency order. A contract change requires an explicit spec/criteria amendment before merging dependent work.

## Failure and rollback

Never replace published bytes. Withdraw/yank a bad package or publish a corrective version with notice; pin prior known-good image/package where data schema supports it.
