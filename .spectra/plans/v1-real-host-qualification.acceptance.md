# Actual host qualification acceptance

### AC-HQ-01 (event-driven)
GIVEN an isolated synthetic project and explicit strict MCP config
WHEN the actual Claude Code CLI session runs
THEN it SHALL use only the scoped server/read-tool configuration without persistent user configuration changes
VERIFY: inspected CLI argv/settings, tool allowlist and configuration preservation check

### AC-HQ-02 (event-driven)
GIVEN actual Claude-to-Magicite stdio traffic
WHEN supported protocol establishment and tool discovery complete
THEN the transcript SHALL contain the observed protocol mode/version and exact sixteen-tool inventory
VERIFY: legacy initialize request/response negotiation OR modern request protocolVersion/clientInfo metadata plus matched successful tools/list response/serverInfo; verify actual schema fingerprints and label adopted versus negotiated

### AC-HQ-03 (event-driven)
GIVEN a deterministic registered fixture
WHEN the actual host routes a request and loads the selected L2 body with its expected digest
THEN captured tool results SHALL contain the matching fixture body
VERIFY: host-origin tools/call events, route digest binding and returned body content comparison

### AC-HQ-04 (unwanted-behavior)
GIVEN an intentionally stale expected digest
WHEN the actual host invokes load_skill_body
THEN the captured result SHALL be the typed stale-decision refusal without the body
VERIFY: actual tools/call request/result pair and absent-body assertion

### AC-HQ-05 (ubiquitous)
THEN the stdio observer SHALL forward protocol messages without changing their contents
VERIFY: transparent relay regression with separate stderr diagnostics

### AC-HQ-06 (unwanted-behavior)
GIVEN required host/handshake/tool evidence is absent or contradicts its claimed candidate/digest result
WHEN the qualification validator runs
THEN it SHALL refuse a passing result
VERIFY: focused missing/mismatched evidence negative controls

### AC-HQ-07 (event-driven)
GIVEN a completed actual host run
WHEN its evidence manifest is reviewed
THEN each compatibility claim SHALL bind the actual host version, observed protocol, installed server SDK, source candidate and hashed observed transcript
VERIFY: independent launch/source/transcript/manifest reconciliation

### AC-HQ-08 (ubiquitous)
THEN published session evidence SHALL contain only scoped synthetic fixture data and necessary non-secret provenance
VERIFY: fixture/credential sentinel and capture-field review, no environment or credential dump

### AC-HQ-09 (ubiquitous)
THEN the report SHALL distinguish this fixture-based agent-driven host run from independent operator, custody, distribution and full protocol qualification
VERIFY: independent claim/scope review plus immutable historical-evidence diff
