# Trust hardening implementation refinements — revision 1

These additive refinements implement the already adopted AC-TH-02/04/08/10 contract. RAMZA selected them during implementation review; they do not replace the original v1 packet or the twelve frozen acceptance obligations. Independent implementation review remains required.

## Non-authorizing artifact lineage

The journal accepts a fifth typed record kind, `artifact_transform`, in addition to the four kinds in spec.md. This avoids changing TrustDecision/1 or overloading genesis. Its immutable payload binds engram identity, source digest, exact transform ID/version/configuration digest, target digest, unchanged resource/asset map, source decision IDs, explicitly source-only signature provenance, and operator review provenance. Genesis may reference these record IDs or their commitment but cannot introduce an alternative unverified mapping.

A transform record never grants admission, clears a rejection or revocation, changes an original decision, or treats a source signature as a signature over transformed bytes. The exact target requires a separate authenticated TrustDecision/1 under the current policy. Local authorship likewise requires this explicit reviewed decision; mutable origin metadata and the previous read-time local-authorship fallback grant nothing.

Every new, imported, migrated, or mutated active artifact retains the required `magicite.trust_journal` extension bound to protected enrollment and journal version. Legacy active 0.2 artifacts receive the existing reviewed 1.0 transformation or remain inactive. Original source bytes, including SKILL payloads, remain preserved. The supported runtime checks the marker and authenticated content digest. The actual old-reader qualification uses the frozen 22ae4e0 code on migrated bytes, including after derived database deletion. This does not retrofit arbitrary old binaries against a deliberate malicious downgrade under the authorized client account.

## Functional protected-profile ownership

An immutable root-owned descriptor at `/etc/magicite/registries/<canonical-project-hash>.json` binds canonical project identity, registry ID, custodian UID, allowed client UID, and absolute custody-profile path. Descriptor and ancestors are root-owned and not client-writable.

The profile and its protected parent directory are custodian-owned outside the writable project/registry. They hold the active or pending epoch, public pins, transition commitment, and socket endpoint. All ancestry is custodian/root-owned and non-client-writable, including ACL checks. Profile values must match the immutable descriptor. Rotation can update pins and epochs, not registry or peer identities.

The custodian atomically writes and fsyncs pending/active profile transitions and the parent directory. A pending transition closes ordinary route/body/writes until the exact durable transition and a fresh proof under the new pin are reconciled. Descriptor administration is a separate explicit operator action. No account creation, automatic `/etc` writes, provisioning, privilege escalation, or production same-UID fallback is authorized or implemented by these refinements.

## Rotation execution and non-circular certificate — revision 2

Explicit `custody rotate` operator control uses the enrolled client UID and the existing registry lease/fence; it adds no MCP tool or credentials. The custodian service alone generates and retains per-registry epoch keys and replaces its own protected profile. Dedicated rotation control operations supplement the general application allowlist. They cannot change descriptor identities, accept caller private keys, or provision accounts.

The epoch-transition journal record contains an immutable intent. Its new-epoch MAC binds the prior committed head and contiguous sequence. A separate domain-separated, dual-signed Transition/1 certificate binds that intent digest, both public-key identities and epochs, the old head, exact resulting new head, and current policy digest. Keeping the certificate outside the record avoids a self-referential digest. Both are durably retained before exposing pending profile state. Exact retries preserve the same record, keys, and certificate.

Preparation/status exposes the exact record with the certificate. The client verifies their linkage, appends and fsyncs the exact prepared suffix under its existing lease before custodian commit, and never treats preparation as routable authority. Resume accepts that same suffix or restores the retained exact record; conflicting extra bytes close recovery. A maintenance-only coordinator captures a fresh signed predecessor before local acquisition and registers a new fence by the same full-predecessor CAS. Normal clients remain closed during maintenance. After commit, fresh proof under the new pin precedes profile completion, local journal/projection reconciliation, and a final lease assertion before ACK. The active attempt/generation is retained across epoch change; there is no hidden re-registration under an already-held lease.

## Typed mutation lineage — revision 3

The non-authorizing transform validator also recognizes versioned authored-edit, Dream-checkpoint, and archive transforms. Each binds the authenticated source digest and exact rendered target; authored mutations intentionally change content and require separate review. They preserve typed v1 fields and never inherit publisher validity. Dream and automatic archival defer changes to currently admitted stable artifact bytes, preserving the stable routing boundary. These refinements implement the existing acceptance clauses without changing their text.

## Authenticated legacy reconciliation gate — revision 4

RAMZA and the orchestrator approved a sixth typed journal kind, `legacy_reconciliation`, to implement the existing TH08 crash invariant. BEGIN binds a unique migration ID, protected registry/epoch, reviewed inventory and complete-backup digests, exact ordered immutable record commitments, and transformed target/resource commitments. It grants nothing. While BEGIN is active, normal snapshots, review/admission, policy changes, and rotation remain closed. The custodian accepts only the exact next listed non-admitting record or a matching COMPLETE, under its current fence. A mutable local completion file has no effect.

COMPLETE requires every expected committed record, target lineage and pending record, and latest applicable restriction. The client separately verifies actual published target/resource bytes before requesting completion; custody does not claim visibility into the client's filesystem. Completion only removes the maintenance gate and never admits a target. Explicit migration recovery can inspect the gated snapshot under the existing bound lease; it cannot change the accepted record plan. Interrupted operations resume the same immutable IDs and preserve all original restriction payloads. The original acceptance packets remain unchanged.
