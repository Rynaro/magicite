# Candidate installation acceptance

### AC-IQ-01 (event-driven)
GIVEN a clean frozen candidate and empty build output directory
WHEN wheel and sdist are built
THEN the evidence SHALL identify both produced artifacts by candidate source and SHA256
VERIFY: build logs, source identity/clean-state check and artifact digest manifest

### AC-IQ-02 (event-driven)
GIVEN each explicit wheel or sdist artifact
WHEN it is installed into its own fresh external environment
THEN the exercised Magicite runtime SHALL resolve exclusively from that installation
VERIFY: independent environments, scrubbed ambient path variables, module/server origin assertions and dependency inventory

### AC-IQ-03 (event-driven)
GIVEN either installed candidate distribution
WHEN its package inventory is inspected
THEN all source-declared runtime resources and metadata SHALL be present with expected content
VERIFY: complete schema/migration/resource/entrypoint inventory against candidate hashes

### AC-IQ-04 (event-driven)
GIVEN either fresh installed distribution
WHEN documented CLI and generic stdio MCP startup are exercised
THEN their actual results SHALL satisfy the existing command and sixteen-tool protocol contracts
VERIFY: installed CLI output plus actual MCP protocol-mode/version and tool-list request/result capture

### AC-IQ-05 (event-driven)
GIVEN the explicitly simulated offline first-use fixture in either installed environment
WHEN routing and digest-bound body retrieval run
THEN the returned L2 procedure SHALL match the registered fixture
VERIFY: route/body digest and fixture identity/content checks for wheel and sdist

### AC-IQ-06 (unwanted-behavior)
GIVEN stale expected body digest or absent required protected custody
WHEN the corresponding installed-runtime operation is attempted
THEN it SHALL produce its existing typed fail-closed outcome
VERIFY: stale-body and pre-fixture missing-custody negative probes with no unintended body disclosure

### AC-IQ-07 (unwanted-behavior)
GIVEN offline production embedding with an empty model cache
WHEN embedding is requested from the installed distribution
THEN the error SHALL retain explicit model acquisition remediation
VERIFY: missing-model probe without a model download

### AC-IQ-08 (unwanted-behavior)
GIVEN a validly named artifact with a required resource or runtime module deliberately broken
WHEN the isolated probe runs despite ambient repository-path bait
THEN it SHALL fail for the relevant installed-runtime defect
VERIFY: meaningful corrupt-artifact negative control; wheel-filename parsing failure alone does not count

### AC-IQ-09 (event-driven)
GIVEN completed install probes
WHEN their report and reproduction instructions are reviewed
THEN every passing row SHALL bind actual candidate, artifact, environment, commands and observed results
VERIFY: independent provenance/command reconciliation and report integrity checks

### AC-IQ-10 (ubiquitous)
THEN the report SHALL preserve actual-publication, unrun-matrix and human-approval limitations without rewriting historical evidence
VERIFY: explicit channel/status table, independent claim review and scoped diff
