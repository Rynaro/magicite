# Linux custody qualification criteria

### AC-CQ-01 (unwanted-behavior)
GIVEN the dedicated hosted qualification job lacks Linux, necessary privilege or a valid protected fixture layout
WHEN its preflight runs
THEN the job SHALL fail instead of reporting a green skipped qualification
VERIFY: explicit preflight negative checks and hosted case-count/result inspection

### AC-CQ-02 (event-driven)
GIVEN a real Linux custodian service and writer client with distinct numeric UIDs
WHEN a valid production custody workflow runs over its Unix socket
THEN authenticated operations SHALL succeed using observed kernel peer credentials
VERIFY: actual CustodianClient/CustodianService positive workflow with UID/PID/kernel report

### AC-CQ-03 (unwanted-behavior)
GIVEN an actual foreign-UID client attempts a protected custody operation
WHEN the production service checks its socket peer
THEN it SHALL reject the operation without advancing authority
VERIFY: foreign process request plus protected-head before/after comparison

### AC-CQ-04 (unwanted-behavior)
GIVEN a production profile configures writer and custodian as the same UID
WHEN it is used
THEN the production custody path SHALL fail closed
VERIFY: real production-profile same-UID rejection case

### AC-CQ-05 (unwanted-behavior)
GIVEN the unprivileged registry-writer process
WHEN it attempts prohibited access to custody secrets or writes to protected authority/profile state
THEN the kernel permissions SHALL deny those accesses
VERIFY: actual writer-UID open/write probes with protected-byte canaries

### AC-CQ-06 (unwanted-behavior)
GIVEN a prepared revocation that has not reached durable commit
WHEN the client process is killed and recovery attempted
THEN the valid prepared revocation SHALL be reconciled under an active fence or leave trust access explicitly closed
VERIFY: real process-death barrier test checking no silent discard into admission

### AC-CQ-07 (unwanted-behavior)
GIVEN a revocation committed at the custodian before local finalization
WHEN the client is killed and the exact operation retried
THEN the committed revocation SHALL retain one authoritative effect
VERIFY: real process-death/retry test inspecting committed sequence/identity and revoked-or-closed access

### AC-CQ-08 (unwanted-behavior)
GIVEN an acknowledged revocation
WHEN the custodian service is killed and restarted from durable state
THEN that revocation SHALL remain enforced
VERIFY: service kill/restart followed by authenticated access denial

### AC-CQ-09 (event-driven)
GIVEN a pre-revoke backup plus retained authenticated suffix through the current independent anchor
WHEN supported restore runs
THEN the later revocation SHALL remain effective
VERIFY: production-transport restore scenario and post-restore access check

### AC-CQ-10 (unwanted-behavior)
GIVEN a pre-revoke backup without the authenticated suffix required by the current anchor
WHEN restore is attempted
THEN trust-dependent access SHALL remain restricted
VERIFY: missing-suffix recovery negative control

### AC-CQ-11 (event-driven)
GIVEN the actual Linux qualification completes
WHEN its report is reviewed
THEN every result SHALL be bound to its candidate source and observed process/environment evidence
VERIFY: independent source-SHA/hash, test-log, UID/PID, command and artifact-digest reconciliation

### AC-CQ-12 (ubiquitous)
THEN the qualification report SHALL preserve unrun macOS and unexercised boundary statuses without altering historical release evidence or claiming whole-gate approval
VERIFY: independent claim review and scoped base-to-head diff
