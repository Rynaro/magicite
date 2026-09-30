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
