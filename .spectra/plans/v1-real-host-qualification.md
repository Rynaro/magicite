---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-10-06
---
# Actual Claude Code read-workflow qualification

## Scope
CHANGE: bounded S11 host compatibility witness plus reproduction notes, using actual installed Claude Code against the candidate Magicite stdio server. Base46b1c412a0d38968ae6ceb6c1c61aaaadb3e219c; branch codex/v1-host-qualification; worktree /Users/henrique/.codex/worktrees/v1-r3-qualification/magicite.
In: actual supported host protocol establishment, exact16 tool advertisement, deterministic offline-fixture route→L2 body retrieval with digest binding, stale-digest negative, captured host/tool wire evidence and source-bound report.
Out: full cancellation/retry/commit matrix, independent human operator tutorial, external product validation, published installs, production-model quality, separate-UID Darwin custody, Linux custody requalification, human security approval or full PROTOCOL/DOCS/GA approval. Historical docs/releases/v1/** and generic SDK fixture transcripts remain unchanged; PR51 remains unmerged/pending.
Right-size: lite score3 (6 estimated surfaces, security, medium stakes), complexity8/12 extended. Standard native pipeline; no Gauge runtime acceptance claim.
Feasibility: Claude Code2.1.288 exists. Initial sandbox auth:false was incorrect because keychain access was blocked; narrow escalated read-only auth reports loggedIn:true Claude Pro OAuth. Use existing login with required narrow escalation; never ask for or dump credentials, modify login, or use --bare (disables OAuth). Signed-in status does not establish API readiness: subsequent actual sessions returned authentication errors before model tool calls. Keep route/body/stale criteria unmet until genuine calls occur; report the exact actionable failure without exposing secrets.

## Approach
Use a small isolated runner and transparent stdio capture, not a new conformance framework. Adapt deterministic disposable registry/fixture setup from scripts/smoke_operator_tutorial.py and tests/support/custody_adapter.py and tests/support/serve_with_fixture_custody.py; existing tests/acceptance/test_v1_conformance.py defines generic SDK probes but is not actual Claude evidence. FixtureCustody may support these read-only host fixtures only when prominently identified: it does not prove deployment custody and must not become a production runtime fallback.
Create temporary project/cwd and explicit strict MCP config naming only the candidate server. Use supported CLI settings/flags to disable user/project hooks/settings, built-in tools and session persistence while preserving OAuth; verify exact installed --help semantics before execution. Restrict permitted tool calls to magicite introspect/route/load_skill_body; perform fixture registration/setup before launching the host. Do not mutate user's home/project configuration or expose unrelated files/tools. Bound run duration/retries and record a real failure rather than endlessly prompting for a desired result.
Launch the actual Claude binary, record version/binary identity, argv with no secret values, exit result and stream-json tool events. A transparent stdio relay captures supported protocol establishment: either legacy initialize request/clientInfo and negotiated response protocol, or modern version adoption evidenced by actual request _meta protocolVersion/clientInfo and matched successful tools/list response/serverInfo. Record observed mode as adopted or negotiated; absence of initialize is valid only for the witnessed supported modern path, never synthesized. Capture tools/list inventory/schema fingerprints and relevant tools/call request/result pairs. Relay complete messages faithfully; observations never synthesize missing messages or change payloads. Keep diagnostic stderr separate from protocol stdout. Only synthetic fixture content is sent through this session; no environment dumps, OAuth/token/header capture or unrelated user content.
The host must actually call route, use its result to retrieve the matching L2 body with its expected digest, and invoke a stale expected-digest request that returns the typed refusal with no body. Validate actual captured request/results, not the model's final prose. Capture inventory of all16 advertised tools even though only the narrow read allowlist is usable in this session. Match observations to exact host, Magicite source, Python/server MCP SDK and observed adopted or negotiated protocol versions; record server SDK from installed package metadata, and record host SDK as unavailable if the host does not expose it rather than inferring a version; do not hardcode assumed protocol or treat configured version alone as proof or classify a generic SDK client as Claude.
Selected hypothesis90. Rejected generic-only extension80 cannot close this host gap; cross-host framework75.5 exceeds the slice.

## Stories
1. As a host integrator, I need a reproducible actual-host read workflow with bounded access. Timebox1d, riskP1, executor standard Vivi. Build only the necessary capture runner/relay and deterministic fixture preparation; use existing server/fixture APIs, no public behavior changes.
2. As a reviewer, I need trustworthy evidence that the real host completed positive and stale-body paths. Timebox1d, riskP1, executor standard Vivi plus separate checker. Validate wire events and captured host tool events where emitted; host stream layout must be observed rather than assumed. Missing required observations fail qualification. Archive source-bound synthetic transcripts and a concise result/limitations document.
3. As a maintainer, I need the capture/validator to fail when evidence is absent or inconsistent. Timebox1d, riskP1, executor standard Vivi. Add focused tests for transparent relay preservation and validation against omitted/mismatched protocol-establishment evidence, inventory, digest/negative result or candidate bindings. Avoid tests mirroring every private function or creating a framework.

## Prospective write-protection addendum (A02)
Root authorized this bounded execution protection after the disposable canary proved ordinary/atomic-replace/child writes denied while reads and unrelated writes remained allowed. Before any new live host/model run, implement and independently review a process-scoped macOS sandbox-exec guard over only the existing monitored user configuration/settings/MCP paths, including lexical and resolved targets where applicable. Test the guard using disposable canaries first; failure or unavailability aborts the run. Root's explicit review gate applies to the committed runner before another user/model invocation.
Wrap both the actual host and any authentication-readiness comparison launches in the same guard, with inherited child protection. Preserve the exact existing before/after stat guard; denied writes, inability to tolerate runtime write refusal, or any remaining stat drift must not be excused into PASS. Record guard mechanism/policy identity and path roles without exposing private configuration contents or credentials. Do not read configuration contents, copy credentials, globally chmod files, restore snapshots, change account configuration, or impose new network/IPC restrictions. This adds protection to the current process; it is not a change to the user's persistent security/configuration settings.
A02 does not change any frozen acceptance criterion. Attempt3 at f499dd2 retains AC01 UNEVALUATED despite its independently observed workflow successes. The future protected run can establish preservation only from its own evidence; no retroactive PASS or broad approval of host configuration writes. Existing scoped tools, protocol establishment, body/digest causality and evidence/privacy requirements all remain in force. See amendment-02.md for canary provenance and authorization.

## Owned paths
- scripts/qualify_claude_host.py (new, runner/validator)
- scripts/capture_mcp_stdio.py (new only if separation improves small runner)
- tests/unit/test_real_host_qualification.py (new focused regressions)
- docs/qualification/claude-code.md (new current result, reproduction and limits)
- docs/qualification/evidence/claude-code/* (new sanitized synthetic evidence/manifest)
- .spectra/* (handoff/plan record)
Allowed surfaces are not mandatory files. Reuse existing fixture setup without editing it where possible. If an actual runtime defect appears, reproduce it and request a narrow scope amendment before source changes. Do not change the existing broad host-support manifest from experimental/required to fully supported on this subset alone.

## Acceptance Criteria
Authoritative checks: v1-real-host-qualification.acceptance.md. Actual-host criteria start UNEVALUATED. Completion requires a real authenticated host run, not just harness tests. If auth/capability becomes unavailable, finish independently useful harness work and report the exact unrun criteria honestly.

## Verification
Run focused capture/validator regressions, affected conformance/doc checks, lint/type and a full supported suite for final source changes. Perform actual Claude session outside sandbox only as needed for existing keychain auth, with isolated files/config/tool permissions. Record exact tested candidate; any later source change requires affected rerun and source binding. An evidence-only later commit may describe that candidate without implying it was itself the tested runtime.
Independent checker reviews proxy transparency, session isolation, protocol/tool event validation, digest semantics and every result claim. Report native Darwin arm64 fixture-host evidence separately from earlier Linux custody evidence. Actual agent-driven host compatibility is not independent human/operator acceptance.

## Confidence
Mechanical confidence94.25% → AUTO_PROCEED is recorded at Assemble; this does not predict that unrun host calls will pass. Amendment01 adapts AC-HQ02 to primary-source-supported modern version adoption; all actual tool-call and failure requirements remain unchanged.

## Handoff
Receiver Vivi. Root handles candidate/PR lifecycle and canonical envelope verification. Return actual source SHA, exact host/SDK/protocol versions, invocation/results, transcript hashes, criterion mapping, review findings and remaining advertised-matrix gaps. Preserve other agents' changes.
