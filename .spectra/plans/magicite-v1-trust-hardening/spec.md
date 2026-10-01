# Authenticated trust history and local custody

## Boundary and authority

Replace unauthenticated decision/policy/root mirrors as trust authority. Keep the
public `TrustDecision/1` payload unchanged inside a versioned journal envelope.
Supported record kinds are `trust_decision`, `policy_snapshot`, `genesis`, and
`epoch_transition`. Policy snapshots contain the complete canonical current trust
policy, roots and revocations. Mirrors and SQLite are projections. A custodian head
binds registry, epoch, sequence, MAC and current policy digest. Latest decisions are
selected by committed journal sequence, never timestamp/ID ordering.

The functional production transport is a bounded local Unix-domain socket. A
separate OS UID owns custodian private state and Ed25519 signing/journal-MAC keys.
A protected profile outside the writable project/registry pins registry enrollment,
public identity, socket endpoint, expected custodian UID and allowed client UID.
Root/custodian-owned protected parent directories must prevent replacement by the
registry writer. Neither an erased local trust directory nor registry-controlled
configuration can select a new identity or endpoint. Both peers verify kernel UID
credentials (Linux SO_PEERCRED; macOS getpeereid). Same-UID production custody,
missing pins or unsupported peer verification fail closed. No local-secret fallback.

The operator provisions the OS account/service and protected profile separately;
this work does not create accounts, elevate privileges or configure external
services. Test adapters and same-account test servers exercise logic only and must
never silently satisfy production qualification.

## Wire and record contracts

Wire version: `trust-custodian/1`. Requests contain operation, registry ID, epoch,
request ID and an unpredictable nonce. Signed responses use domain separation and
bind the exact request identity/nonce. Framing, request sizes, timeouts and accepted
operations are bounded; no arbitrary filesystem operation is exposed.

`read_current` returns signed nonce, registry/epoch, head sequence/MAC, policy
digest, fence generation and signing key ID. Fresh reads are required; cached
receipts cannot establish current authority. Clients validate full journal/policy
state against a fresh custodian head before trust-dependent reads and again before
body disclosure. The latest-decision map and policy belong to that same verified
snapshot; a later mutable policy reread cannot replace its policy authority.

`register_fence` takes the full fixed predecessor (registry, epoch, generation,
head sequence and head digest), unique attempt ID, existing lease holder and local
token. Durable CAS advances a monotonic custodian generation even if the
head is unchanged. Exact registration retries are idempotent only while that
attempt remains active; an older attempt cannot reactivate.

`prepare_record` takes expected head, active generation/attempt, record ID, kind and
payload. Custodian validates and constructs a canonical envelope binding registry,
epoch, sequence, record ID/kind, payload digest, previous MAC and record MAC. Its
signed response binds the prepared envelope to the request. Preparation does not
advance committed authority. Custodian secrets never leave custody.

The client appends and fsyncs envelope plus payload under its current lease, then
calls `commit_record` with expected head, generation/attempt and envelope digest.
The custodian validates its preparation and current fence, computes the new head
and policy commitment itself, durably persists CAS, then returns a signed receipt.
The client finalizes local head/projections with fsync and acknowledges only after
lease validation. A caller-supplied head/policy digest is never trusted as authority.
Resolve lost responses by a fresh read and exact record status, not blind retry.
Remote CAS is the authority linearization point; local SQLite and remote custody
are not claimed to form an atomic distributed transaction.
Prepared but uncommitted records are never routable. Pending preparation must be
durably recoverable at the custodian or through equivalent authenticated recovery
material; a new active fence may finish the exact valid extension but may not alter
payload or sequence. An interrupted valid prepared
revoke requires reconciliation or closed access; never silently discard it.

## Fence composition with the existing writer lease

The existing lease row is deleted on release: its numeric token is not a persistent
global epoch. Capture the full nonce-authenticated custodian predecessor BEFORE
one local `CrossProcessLease` acquire attempt. Under that acquired lease, assert
ownership and CAS-register against the complete predecessor with attempt/holder/token; assert ownership again.
Never refresh the predecessor or retry registration while retaining the acquisition.
CAS failure releases/aborts the attempt; retry starts before a fresh acquisition.
Nested domain calls reuse the bound lease and custodian fence. This is one existing
application writer lease plus the external authority CAS, not a second writer lock.

Before and after external commit, and immediately before local durable replacement
and acknowledgement, assert the existing lease. A newer generation fences old
prepared work even if no newer journal record exists. If an older registration
linearizes first but its local lease was already lost, post-assertion/ACK fail;
retain any valid unacknowledged extension for reconciliation. A newer contender
whose predecessor CAS loses restarts its entire local acquisition.

## Migration, recovery and rotation

Custodian enrollment distinguishes explicit virgin initialization from lost local
state. Config loading, ordinary registration and directory creation never enroll
implicitly. Legacy preview is zero-write. Explicit migration requires complete
backup and reviewed reconciliation of unauthenticated mirrors, policy/root state,
revocation inventories and seals. Deterministic genesis/current-state import records
unsigned historical provenance and missing-history limits, preserves originals,
and denies unresolved conflicts/admissions. Wrapping old bytes cannot authenticate
lost history retroactively.

Backups bind the journal head but exclude private custody keys. Restoring an older
backup needs the current independent anchor and retained authenticated suffix.
Missing suffix/anchor/key leaves restricted reconciliation; no fabricated revoke,
silent rekey or resequencing of `RecoveryOverlay` decisions. Every direct backup
mirror writer must convert to validated history replay. A local fingerprint-key MAC
is not equivalent to custodian history.

Rotation binds old/new keys, epochs and heads and preserves revocation continuity
through custodian CAS. Old readers reject the new authority format. Downgrade must
preserve current restrictions or remain closed; a pre-revoke snapshot alone cannot
authorize restoration. Doctor only diagnoses; it does not reconcile or enroll.

## Verification and release claims

Anchor tests derive from AC-TH-01–12 before implementation. Include genuine process
exit/fault boundaries and the two stale-holder interleavings, not only mocked
success paths. Existing tests may explicitly provision test custody, but production
behavior may not acquire an implicit fallback merely to preserve fixtures. Keep the
sixteen MCP tools, evidence/policy domain authority separation and frozen original
acceptance obligations. Run complete regression, mypy, Ruff and docs checks from a
clean candidate, then independent exact-head review and CI before integration merge.
Production OS/UID qualification remains separately UNEVALUATED unless actually run.
