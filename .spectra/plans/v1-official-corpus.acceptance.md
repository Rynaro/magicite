# Official corpus acceptance

### AC-CQ-01 (event-driven)
GIVEN the prospectively pinned immutable dataset revision and expected artifact identities
WHEN acquisition runs
THEN all seven primary data files and declared metadata SHALL match their expected object identities and recorded raw SHA256 lengths
VERIFY: immutable URLs, LFS SHA256 or Git blob SHA1 checks, complete file coverage and unchanged raw bytes

### AC-CQ-02 (event-driven)
GIVEN the complete acquired raw source
WHEN rows and relations are scanned
THEN every master and split record SHALL be accounted with validated identities, membership and query-qrel references
VERIFY: byte-derived counts, card reconciliation, duplicates/overlaps/conflicts ledger and split-local foreign-key checks

### AC-CQ-03 (event-driven)
GIVEN a source skill record
WHEN it is converted
THEN its supplied full SKILL.md SHALL remain exactly recoverable independently of the native wrapper
VERIFY: raw-row binding, exact decoded UTF8 sidecar/hash, body-alias equality and Unicode/frontmatter fidelity

### AC-CQ-04 (event-driven)
GIVEN the complete official split pools
WHEN native artifacts and their identity map are emitted
THEN each source skill SHALL have a deterministic reversible mapping to a safe parser-valid artifact or an explicit blocking incompatibility
VERIFY: all-artifact parser/inventory checks, collision handling and no reduced-pool readiness claim

### AC-CQ-05 (event-driven)
GIVEN upstream train and test records
WHEN runtime and scoring projections are generated
THEN original split identities and labels SHALL remain separate from exact query-only runtime inputs
VERIFY: original-ID and split mapping, full qrel retention, isolated field sets and no invented groups or negative labels

### AC-CQ-06 (event-driven)
GIVEN source license and attribution declarations
WHEN provenance is recorded
THEN original declarations and missing or unresolved metadata SHALL remain explicit without blanket substitutions
VERIFY: per-row metadata coverage, pinned card and distinction between dataset pin and independent origin/license verification

### AC-CQ-07 (event-driven)
GIVEN a clean frozen converter candidate and frozen raw inputs
WHEN conversion runs twice offline in separate clean directories
THEN canonical bodies, mappings, split inventories and runtime/scoring artifacts SHALL match byte-for-byte
VERIFY: complete output seals and separately labelled operational timestamps and paths

### AC-CQ-08 (unwanted-behavior)
GIVEN tampered bytes, missing records, unsafe paths or contradictory identities and labels
WHEN acquisition or conversion validation runs
THEN it SHALL reject a ready-for-evaluation seal without silently dropping or repairing data
VERIFY: meaningful corruption, omission, duplicate, dangling-qrel, alias and split-leakage negative controls

### AC-CQ-09 (event-driven)
GIVEN the completed acquisition and conversion evidence
WHEN an independent checker reviews the freeze
THEN every readiness claim SHALL bind exact candidate, upstream revision, complete raw and converted inventories and actual replay results
VERIFY: digest and coverage reconciliation, source diff, focused regressions, independent review and current CI

### AC-CQ-10 (ubiquitous)
THEN this slice SHALL preserve the no-embedding, no-ranking, no-publication and no-release-acceptance boundary
VERIFY: execution commands/import paths and explicit remaining E3/E6, original provenance, external/operator and human-approval obligations
