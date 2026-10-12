# R3 milestone frozen acceptance criteria

### AC-R3-01 (unwanted-behavior)
GIVEN review_approve has committed the trust decision
WHEN BusyError occurs before local admission or approval audit completes
THEN the operation SHALL not report successful approval while required effects are incomplete
VERIFY: focused fault-injection regression for post-decision BusyError

### AC-R3-02 (event-driven)
GIVEN an event_id whose prior approval stopped after decision commit
WHEN the same approval is retried
THEN successful return SHALL require completed local admission and its approval audit
VERIFY: partial-completion replay regression inspecting decision, admission and audit state

### AC-R3-03 (event-driven)
GIVEN a completed approval with event_id
WHEN it is replayed repeatedly or concurrently
THEN the approval SHALL retain exactly one logical decision and approval audit effect
VERIFY: repeated and concurrent replay regression counting logical effects

### AC-R3-04 (unwanted-behavior)
GIVEN writer ownership has been lost
WHEN review_approve processes a post-acquisition BusyError
THEN the implementation SHALL perform no unowned local or audit writes
VERIFY: injected ownership-loss boundary regression

### AC-R3-05 (event-driven)
GIVEN another valid writer owns the acquisition lease
WHEN an event_id approval waits for the lease
THEN acquisition contention SHALL retain its bounded retry behavior
VERIFY: existing concurrency/timeout tests plus affected focused regressions

### AC-R3-06 (event-driven)
GIVEN configured custody and a healthy local journal
WHEN doctor runs
THEN doctor SHALL include a stable custody check grounded in both protected-current and local-journal observations
VERIFY: doctor custody healthy fixture assertion

### AC-R3-07 (unwanted-behavior)
GIVEN missing, unavailable, restricted, corrupt or stale custody state
WHEN doctor runs
THEN the custody check SHALL report the observed unhealthy or unknown state with actionable remediation
VERIFY: parameterized doctor custody status matrix

### AC-R3-08 (ubiquitous)
THEN doctor custody inspection SHALL leave authoritative and diagnostic filesystem bytes and entries unchanged
VERIFY: expanded zero-write snapshot/canary tests across healthy and failure states

### AC-R3-09 (ubiquitous)
THEN the new custody diagnostic SHALL omit planted key material and sensitive configured custody paths
VERIFY: secret-planted successful and exceptional doctor JSON assertions

### AC-R3-10 (event-driven)
GIVEN a clean candidate C containing the completed fixes
WHEN r3 witnesses are packaged
THEN every PASS witness SHALL carry reproducible evidence valid for C with verifiable source and artifact digests
VERIFY: release-manifest validator at composed clean-C-plus-evidence root; no source/digest/witness errors; witness provenance review

### AC-R3-11 (unwanted-behavior)
GIVEN evidence from another source or a modified bound artifact
WHEN r3 qualification validates it
THEN the validator SHALL reject its use as current candidate PASS evidence
VERIFY: existing validator negative controls or targeted regression when generator behavior changes

### AC-R3-12 (event-driven)
GIVEN r3 packaging is complete
WHEN its files are reviewed
THEN the package SHALL contain a current privacy map and explicit per-gate status with independent adjudicator identity
VERIFY: package structure and evidence-to-status review by non-maker

### AC-R3-13 (unwanted-behavior)
GIVEN required security sign-off, empirical or external release evidence remains absent
WHEN the release decision is produced
THEN GA eligibility SHALL remain false with each missing requirement identified
VERIFY: release decision validator exit 1 with eligible:false and only enumerated unmet-release-requirement errors; independent gate-table review

### AC-R3-14 (ubiquitous)
THEN historical v1/r2 evidence and the user's primary harness changes SHALL remain unchanged
VERIFY: base-to-head scoped diff and primary checkout preservation check
