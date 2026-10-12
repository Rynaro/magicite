# Frozen additive trust-history acceptance criteria

Version: 1 (Amended by amendments/TH-A01). Adopted scope is additive to the original v1 packet. Changing these
obligations requires a recorded planning review; implementation may not weaken them.
Fixture success is not production custody qualification.

### AC-TH-01 — Authenticated monotonic authority
GIVEN an enrolled registry with a committed revocation or policy/root restriction
WHEN decision mirrors, policy/root projections, the journal, or local head are deleted, edited, truncated, reordered, duplicated, spliced, or rolled back
THEN prior admission SHALL NOT reappear; projections SHALL rebuild only from authenticated committed history, and missing or conflicting authoritative bytes SHALL close trust-dependent access
VERIFY: decision and policy/root mutation matrix; revoke-mirror deletion; missing-policy/default-fallback and root-revocation rollback regressions

### AC-TH-02 — Record identity and idempotency
GIVEN an authenticated record ID for any supported record kind
WHEN an identical operation retries or the same ID is reused with changed payload, kind, or sequence
THEN exact committed retries SHALL have one effect; conflicting payloads and resequencing SHALL be rejected without advancing authority
VERIFY: prepare/commit/lost-reply retry matrix across decision, policy, genesis and epoch records

### AC-TH-03 — Fresh independently held head
GIVEN a protected enrollment and a fresh independent custodian head
WHEN all local history and head are rolled back, or a receipt has stale nonce, wrong signing key, registry, epoch, policy binding, or substituted enrollment identity
THEN the client SHALL reject stale or mismatched authority and remain closed; a cached receipt alone SHALL NOT prove freshness
VERIFY: full-local-state rollback, replayed nonce, key/epoch/registry/policy mismatch and protected-profile substitution cases

### AC-TH-04 — Custody and peer boundary
GIVEN production trust configuration
WHEN the custodian, key, pin, peer credentials, protected enrollment/configuration or protected custody state is missing, writable by the registry writer, same-UID, or unsupported on the current OS
THEN trust-dependent operations SHALL fail closed without an in-process or environment fallback; qualification SHALL NOT be inferred from a test adapter or same-account fixture
VERIFY: Linux SO_PEERCRED/macOS getpeereid adapter fixtures, both-peer UID rejection, protected-parent/path checks, unavailable transport and explicit production qualification status

### AC-TH-05 — Acknowledgement and crash order
GIVEN a prepared authenticated record under the active fence
WHEN a fault occurs at prepare, client append/fsync, local-head handling, custodian durable commit/fsync, projection finalization, response delivery, or acknowledgement
THEN every acknowledged revocation SHALL remain enforced; acknowledgement SHALL occur only after durable custodian CAS and fenced local finalization; valid unacknowledged preparations SHALL remain recoverable and exact extensions SHALL reconcile under an active fence or keep access closed and SHALL NOT silently discard a prepared revoke
VERIFY: process/fault matrix around every persistence boundary and lost replies; acknowledgement trace shows custodian fsync first

### AC-TH-06 — Existing lease and independent fence generation
GIVEN a nonce-authenticated full predecessor (registry, epoch, generation, head sequence and head digest) captured before one existing writer-lease acquisition attempt
WHEN another holder registers a newer custodian fence, including without changing the journal head, and a paused old holder later registers, prepares, commits or acknowledges
THEN the stale predecessor/generation/attempt SHALL be rejected; registration SHALL use durable full-predecessor CAS with a unique attempt ID; an attempt SHALL NOT refresh its predecessor while retaining that acquisition; nested operations SHALL reuse its registered fence without introducing a second application writer lock
VERIFY: pause-old-before-registration/new-registration/resume; pause-old-before-commit/new-fence/resume; head-unchanged races; nested guard; existing lease assertions before and after external commit and before local replace/ACK

### AC-TH-07 — Complete reader and writer integration
GIVEN authenticated committed history whose timestamps disagree with sequence order
WHEN route, body disclosure, rebuild, restore, import, review, revoke, or policy/root operations resolve trust
THEN every path SHALL use one validated snapshot whose latest-by-engram journal sequence and authenticated current policy are bound to the same verified head, without a subsequent mutable-policy reread; no legacy mirror/default-policy fallback SHALL authorize access; body disclosure SHALL revalidate freshness
VERIFY: backdated revoke against future-dated admit, all trust call sites, post-route policy/root/history drift and direct restore/import writer regressions

### AC-TH-08 — Explicit legacy reconciliation and enrollment
GIVEN a legacy registry or missing local trust state
WHEN preview, migration, or initialization is requested
THEN preview SHALL write nothing; enrollment SHALL come only from protected custodian state and explicit operator action; reviewed legacy import SHALL require a complete backup, disclose unsigned/missing historical provenance, preserve originals and reject unresolved conflicts/admissions; an empty local directory SHALL NOT imply a virgin identity or authorize silent key creation
VERIFY: zero-write preview, erased-enrolled-state versus explicit virgin enrollment, reconciliation/backup guards, unsigned-history disclosure and conflicting legacy inventory rejection

### AC-TH-09 — Recovery with retained authenticated suffix
GIVEN a backup preceding a committed revocation and the current independent anchor
WHEN restore or rebuild runs
THEN current revocations SHALL remain effective; restoration SHALL require retained authenticated history through the current anchor and SHALL remain restricted when that suffix is absent; RecoveryOverlay mirrors or local fingerprint-key MACs SHALL NOT be resequenced or substituted for custodian authority
VERIFY: pre-revoke backup/current-suffix restoration, anchor-ahead missing suffix, no synthetic history/rekey, and all direct mirror-writing restore paths

### AC-TH-10 — Rotation and downgrade continuity
GIVEN an initialized authenticated epoch and current revocation state
WHEN keys/epochs rotate, an old reader opens the new authority format, a supported downgrade path runs (documented `migration restore` into inactive staging, complete-backup restore through the supported runtime, or the frozen 22ae4e0 reader on migrated bytes including after derived database deletion), or the supported runtime opens rolled-back local bytes
THEN rotation SHALL bind old/new keys, epochs and heads through custodian CAS; unsupported readers SHALL reject the new authority; supported downgrade paths SHALL NOT reactivate revoked content or silently reset enrollment; the supported runtime SHALL keep the latest authenticated revocation authoritative over any rolled-back local bytes or remain closed
VERIFY: dual-bound epoch transition, stale/wrong-key rotation (tests/unit/core/test_trust_rotation.py, tests/unit/core/test_trust_rotation_transport.py), old-format rejection, and tests/integration/test_th10_old_reader_downgrade.py (new-format revoke vs 22ae4e0 reader; old admit → reviewed migration → revoke; migration restore staging; trust/authority deletion fails closed; hand-rollback residual: supported runtime still enforces revoke)

### AC-TH-11 — Diagnosis and confidentiality
GIVEN unavailable, corrupt, stale or restricted custody/history
WHEN doctor, errors, logs or backup archives are produced
THEN doctor SHALL remain zero-write; diagnostics SHALL be actionable without disclosing private keys, sensitive paths or raw exception content; custodian secrets SHALL remain outside registry archives and logs
VERIFY: byte/mtime zero-write diagnosis matrix, error sentinels, archive inventory and private-key canaries

### AC-TH-12 — Original exploit closure and honest qualification
GIVEN the original S16 mirror-loss diagnostic and clean valid trust workflows
WHEN hardened approval, revocation, routing and recovery are exercised
THEN the original deletion exploit SHALL fail closed and valid positive workflows SHALL pass; separate-UID Linux/macOS deployment qualification SHALL be reported PASS only for an actual corresponding run and otherwise UNEVALUATED
VERIFY: original admit→revoke→mirror-delete scenario, successful authenticated workflows, full regression/type/lint/docs checks and explicit deployment qualification ledger
