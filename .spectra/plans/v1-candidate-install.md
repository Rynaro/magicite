---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-10-07
---
# Candidate distributable installation and first use

## Scope
CHANGE: bounded S13 candidate-distribution rehearsal and S15 reproducible first-use instructions. Base c7b5efff3cad554f30089d4b8dcd75f7e6fae516 after PR56 merge; branch codex/v1-install-qualification in /Users/henrique/.codex/worktrees/v1-r3-qualification/magicite. Primary dirty checkout remains untouched.
In: freshly built wheel and sdist tied to exact candidate; independent clean installs outside checkout; installed CLI and generic MCP first-use probes; package resources/metadata; positive offline fixture route/body and explicit missing-custody/model behavior; artifact/probe provenance and honest report.
Out: publishing/tagging/version bump, PyPI/TestPyPI/pipx/uvx/OCI fetched-channel qualification, signing/attestation certification, independent external operator, real-model quality, deployment custody, host Claude reruns, human security approval, complete DISTRIBUTION/GA claims. The package version remains its current actual metadata (0.3.1 at base); these candidate artifacts are not the existing published0.3.1 and not a published1.0.0.
Mechanical tier lite score3 (8 files, security, medium stakes); complexity7/12 extended. Standard native role chain. No full planning ceremony beyond required gates.

## Approach
Adapt scripts/verify_wheel_install.py and existing install-channel tests with a small artifact-pair orchestration/report layer. Existing wheel probe installs outside checkout and uses explicitly simulated custody; it checks only an early schema/migration pair. Existing scripts/smoke_operator_tutorial.py uses editable installation plus dependency .pth reuse and therefore is reference behavior, not qualifying distributable-install evidence.
Freeze clean source candidate C containing final runner changes; build wheel and sdist into a fresh empty output directory from C, with build/dependency tool versions recorded. Never pick the latest leftover dist/*.whl or package-index magicite by name. Install explicit hashed artifact paths into distinct fresh environments (one wheel, one sdist) outside checkout, with real dependency installation/resolution and recorded versions. Installing the sdist must build from its archive without falling back to the source checkout. Caches may supply dependency bytes, but no inherited site-packages, system-site-packages, editable installation or .pth reuse of an existing environment.
Scrub Python path/home/venv/user-site leakage as existing probe does. Check every exercised magicite module/server process resolves inside the intended installation, not merely a similarly prefixed path. Copy only declared synthetic fixture data and standalone harness into owned temp locations; do not import magicite from repo/src or borrow test-support modules that add the checkout to runtime sys.path. Fixture custody is allowed only as an explicitly simulated probe mechanism, never a public runtime fallback or deployment claim.
For both installed artifacts, exercise installed CLI version/help and a documented diagnostic/first-use command; launch the installed CLI stdio server for actual generic MCP protocol establishment, exact16-tool discovery, fixture import/review where required, route→matching digest-bound L2 body and stale-body refusal. Protocol may use supported legacy negotiation or modern adoption; capture the actual mode/version. CLI-first trust/review setup stays outside MCP names. Reuse deterministic hashing fixture semantics, not a model download. Verify absent protected custody fails closed before fixture attachment. Existing offline missing-model error must name acquisition remediation without attempting a network fetch.
Verify all package-required schemas (including engram1.0), migration SQL and declared package resources against source-bound expected inventory/hashes; validate metadata/entrypoints/version and that wheel/sdist contain no unintended agent/user/cache trees. Do not require archive byte reproducibility unless it is actually demonstrated. A validly named deliberately broken wheel/resource must fail the relevant installed-runtime check despite ambient repo PYTHONPATH bait; invalid wheel filename alone is not a meaningful negative control.
Selected approach88.5, scored mechanically in state. Wheel-only repetition79 misses sdist/CLI/provenance; full publication framework75.5 needs separate release authority and exceeds this slice.

## Stories
1. As an operator, I need candidate artifacts that install and run without the source checkout. Timebox1d, riskP1, standard Vivi. Build explicit clean artifact pair and isolated environments, resolve dependencies, record installed origin/resource completeness and exercise CLI/MCP first-use.
2. As a reviewer, I need broken packaging and contaminated environments to fail visibly. Timebox1d, riskP1, standard Vivi. Extend the smallest meaningful negative controls for valid artifact corruption/missing resource and source-path contamination; retain existing probe CLI behavior where practical.
3. As an operator, I need accurate reproduction instructions and evidence. Timebox1d, riskP1, Vivi results plus independent checker. Document exact artifact installation/first-use commands and prerequisites; mark simulated custody and generic client explicitly. Archive source/artifact/environment/command/result hashes and map each check. Qualification requires both wheel and sdist on at least the actually exercised supported Python3.12 platform; other Python/OS rows stay UNEVALUATED unless explicitly run. Do not turn fixture success into independent-user acceptance.

## Prospective scope amendment A01 — installed CLI version inspection
The frozen plan explicitly includes installed CLI version/help in first use, and the new reproduction guide invokes magicite --version. Actual candidate C1 fd5ee46 installed successfully but that command exited2 because the existing CLI had no version option; this is a missing capability for the accepted slice flow, not a claim that an older supported option regressed.
Root authorizes src/magicite/__main__.py to add the standard eager Click version_option(package_name="magicite"), using installed distribution metadata without a hardcoded version or package version bump. Add tests/unit/test_cli_version.py with a real CLI invocation comparing output to importlib.metadata.version("magicite"); model the actual executable name in CliRunner so the assertion reflects the console entrypoint. Preserve help/doctor/serve semantics and avoid runtime initialization merely to inspect the version. Existing owned probe code may persist command stdout/stderr/exit before assertions so subsequent failures remain reviewable.
All ten acceptance criteria are unchanged. Keep C1 artifacts/logs and its failed version result immutable. After the narrow fix and required tests/review, freeze fresh C2, rebuild both wheel and sdist and run the full pair against C2. No relabeling C1 as passing and no source-equivalence carryforward for this production change. No publication or approval claim follows from the fix.

## Owned paths
- src/magicite/__main__.py (A01: metadata-backed eager version option only)
- tests/unit/test_cli_version.py (A01: real CLI metadata regression)
- scripts/verify_wheel_install.py (extend/reuse without breaking existing callers)
- scripts/qualify_distributions.py (new thin build/run/report layer only if needed)
- tests/unit/test_verify_wheel_install.py
- tests/acceptance/test_install_channels.py
- tests/unit/test_qualify_distributions.py (new only if orchestration has meaningful validation logic)
- docs/qualification/candidate-install.md (new first-use/results/limits)
- docs/qualification/evidence/candidate-install/* (new source-bound synthetic logs/manifests)
- .spectra/* (plan/results)
Production packaging metadata/resources or CI changes require a narrow recorded scope amendment tied to an observed defect or necessary execution gap, not a broad refactor. Historical docs/releases/v1/**, prior custody/host evidence, public-channel reserved statuses and unmerged PR51 sign-offs remain unchanged.

## Acceptance Criteria
Authoritative criteria: v1-candidate-install.acceptance.md. Initial statuses UNEVALUATED. This is candidate distributable qualification, not actual published-channel qualification. Expected entry points/resources come from runtime/source contracts, never adjusted silently to a broken artifact.

## Verification and completion
Run focused helper/negative tests, actual pair installation/probe, affected conformance/docs checks and appropriate lint/type/full supported suite once for final source. Independent checker validates no source/venv leakage, real CLI/server origins, meaningful negative control failure reason and every provenance/claim. Reuse existing supply-chain manifest hashing helpers where suitable; local hashes are not signed publisher provenance.
Record exact tested candidate C and the resulting artifact SHA256, dependency versions, Python/platform, build/install/probe commands, exit codes and report hashes. An evidence-only successor may summarize C; any runtime/build-input change requires new builds/probes. Preserve original C evidence if a later test-only correction needs independently reviewed source-equivalence mapping; never silently relabel old artifacts as new candidate bytes.
Completion: both independent artifact rows pass with actual commands/results and independent review, docs clearly describe the scope, and CI passes. Missing package-index/network/tool prerequisites remain exact blockers rather than green skips or reused editable environment results. Named PyPI,pipx,uvx,OCI,TestPyPI/signature/attestation/fetched-artifact and full support-matrix obligations remain explicit follow-up requirements.

## Confidence
Mechanical confidence95.25% → AUTO_PROCEED recorded at Assemble; it applies to this bounded plan, not success of currently unrun fresh installations.

## Handoff
Receiver Vivi. Root assigns feature branch/worktree and handles PR lifecycle. Return source/artifact identities, changed files, criterion-to-result mapping, independent review findings, current CI and remaining publication gates. No source changes by RAMZA and no reverting other agents' changes.
