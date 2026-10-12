# V1 release gates and scope coverage

Release readiness is a conjunction of gates, not a calendar milestone. This planning branch is not a release candidate and contains no implementation. Status for every future runtime/release gate starts **UNEVALUATED**. S16 may change status only by attaching evidence with commit/artifact digests and verifier identity.

## Required evidence ledger

| Gate | Owner | Required evidence | Waivable? |
|---|---|---|---|
| POLICY | S00/S07 | Nonadaptive default, Dream isolation, explicit experimental selection, pinned incumbent | No |
| EVIDENCE | S01 | Manifested predictions, independent labels, claim CI, negative history | No |
| SCHEMA | S02/S03 | Frozen engram/1.0 schema, roundtrip corpus, 0.2 migration/downgrade/replay | No |
| TRUST | S04 | Offline signed bundle tamper tests, local review/revocation, server-owned origin, adversarial corpus | No |
| ROUTING | S05–S07/S14 | Full-content indexes, eligibility, explanations, calibrated abstention, E3 noninferiority | No |
| COMPOSITION | S08/S14 | Independent structural corpus and actual host-side execution outcomes; safe invalid-plan behavior | No |
| PRIVACY | S09 | Data map, no default raw capture, historical cleanup, checkpoint RPO, export/delete/retention/restore tests | No |
| LEARNING | S00/S10 | Containment mandatory; optional learning either fully gated or explicitly unavailable | Efficacy feature optional; containment no |
| PROTOCOL | S11 | Schemas, retry/cancel/error tests, exact advertised host/SDK/protocol transcripts | No |
| RELIABILITY | S03/S12 | Kill/replay/fencing/backup/restore/upgrade matrix across all authoritative stores; zero-write doctor | No |
| PERFORMANCE | S14 | Dedicated-runner E6 envelopes at declared supported scale, production model measurements | No for advertised scale |
| DISTRIBUTION | S13 | Clean installs from actual published artifacts; signatures/checksums/SBOM/provenance; TestPyPI rehearsal | No |
| DOCS | S15 | Generated reference/claims gates, one tested operator path, redirects, support/security/governance policies | No |
| RC-CONTRACT | S16 | Two independently built RCs with unchanged stable API/schema fingerprints and passing matrix | No |
| EXTERNAL | S16 | One independent reproduction OR two external production pilots meeting criteria below | No |
| SECURITY | S04/S11/S12/S13 | Threat model, bounded fuzz/adversarial corpus, critical findings resolved | No |
| GA-ALL | S16 | All required gates passed, explicit maintainer release sign-off, fetched artifact checks | No |

No `waived` status may satisfy a nonwaivable gate. An amendment can narrow advertised scope before a new RC/evaluation, but cannot assert the original scope passed. Future budgets and experimental feature availability must be visible in the release manifest.

## Proposed supported matrix

[DECISION] Initial V1 stable targets: Python 3.11 and 3.12; Linux amd64 and arm64 for OCI; Linux amd64 and macOS arm64 for native package installs; local filesystems only for writable registries. Other Python/OS/filesystem combinations are experimental until evidence is added. Packaging metadata/classifiers and documentation must match the chosen matrix rather than implying all future Python versions are tested. Do not add an arbitrary Python upper bound without checking dependency compatibility; state the tested support window explicitly.

MCP stable target: stdio generic conforming client plus Claude Code adapter already documented by the project. S11 pins exact client and SDK versions and actually negotiated protocol versions in a checked-in support manifest before claiming support. An untested current protocol is not implied by an MCP 2.x dependency. Additional hosts are opt-in experimental until conformance transcripts exist. Every advertised OS/channel must cover handshake, declared tool manifest, packaged resources, offline fixture route and body retrieval.

Upgrade source matrix: last published patch of every minor actually published before V1 (discover from release metadata; at minimum existing 0.2 and 0.3 snapshots) to each RC, plus RC1→RC2→GA and complete-backup downgrade. Do not invent 0.4–0.8 releases just because the dossier used those milestone names. Offline failure and model acquisition tested separately. Unsupported network filesystems must be detected or require documented explicit unsafe opt-in that is excluded from supported guarantees.

Security: no unresolved critical finding. Every high finding has a named reviewer, exploitability assessment, mitigation and expiry; exceptions remain visible. Vulnerability scanner settings must not hide unfixed findings in the evidence report. Signature validity does not establish safety or efficacy. No third-party security certification is claimed.

## External evidence definition

Independent reproduction: a person/team other than the implementation/label authors runs a frozen published evaluation manifest from a clean environment, returns versions/log hashes and a discrepancy report, and reproduces metric/interval conclusions within declared tolerance (deterministic results exact; nondeterministic results within preregistered interval policy). A self-run in a second container is not independent reproduction.

Two-pilot alternative: two distinct external repositories/operators use the declared stable workflow with independently produced routing labels and deterministic outcome checks where task-success is claimed. Each report identifies registry/host/model/policy versions, workload scope, install/route/explain/recovery experience, observed failures and whether advertised guarantees held. No minimum number of elapsed days is required; each pilot must cover all scoped workflow checks and report sample counts/uncertainty. User consent governs exporting any real usage evidence.

Recruitment/contact and signing/publisher account setup are explicit external dependencies for future maintainers. This spec task neither contacts people nor creates production accounts. Independent code/spec review by another agent is valuable but does not satisfy external product validation.

## Coverage of dossier epics

| Dossier epic | Slices | Deliberate interpretation |
|---|---|---|
| E1 Evidence integrity | S01, S14, S15 | Preserve historical negatives and remove circular labels |
| E2 Router v2 | S00, S05, S06, S07 | Hybrid candidate earns default; reranker optional |
| E3 Engram 1.0 | S02, S03 | Inline canonical artifacts retained; explicit assets optional |
| E4 Composition | S06, S08, S14 | Validity, execution and efficacy measured separately |
| E5 Policy learning | S00, S09, S10 | Isolation/evidence mandatory; learning promotion optional |
| E6 Trust bundles | S04 | Offline Ed25519 bundle trust, local policy separate from OCI Cosign |
| E7 Conformance | S11, S15 | Schema/CLI/protocol/host matrix with real artifacts |
| E8 Operations | S03, S12, S13, S14 | Reuse lease/recovery; repair zero-write diagnosis |
| E9 External validation | S16 | Independent reproduction or two pilots blocks GA, not coding parallelism |

Dossier P1 dashboards/leaderboards/visualizations/team policy extensions remain optional follow-up specs. Policy-as-code basics needed for trust/eligibility are included; broad team administration is deferred. P2 federation, service profile, learned query decomposition, marketplace and autonomous synthesis remain outside V1. The dossier's versioned stepping stones are dependency checkpoints, not compulsory releases.
