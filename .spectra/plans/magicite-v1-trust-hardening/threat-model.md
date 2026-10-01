# Trust-history custody threat model

The attacker may edit, erase, reorder or replay every byte in the writable registry,
including decisions, policy/roots, journal, SQLite projections and local head. The
attacker may cause crashes, withhold custody access, replay captured wire receipts,
or restore an old local backup. Failure may deny service; it may not resurrect a
revocation or silently change policy authority.

The trusted boundary contains a separately provisioned OS UID, protected custodian
keys/state and protected enrollment/profile/pin/socket configuration outside the
attacked tree. Kernel peer credentials and fresh signed request-bound receipts
establish the intended local service. Merely putting a key or MAC head in another
directory under the same principal is not independent custody.

Compromise of root, kernel, signing/MAC keys, custodian account, or the authorized
client process is outside this guarantee. The authorized client may request reviewed
trust changes; this mechanism does not determine human intent or establish efficacy
or content safety. Rollback of both custodian authority and every independently held
copy is outside the promised anti-shrink boundary. The service must durably persist
before acknowledging; weaker periodic anchoring is not a conforming implementation.

Pre-hardening binaries are outside the downgrade guarantee (TH-A01). After a
post-migration revocation, hand-copying pre-migration bytes over the registry and
running the frozen 22ae4e0 or earlier binary can route and disclose the revoked
subject, because that binary never reads authenticated custody. The supported
runtime on the same bytes keeps the revocation authoritative. Mitigation is operator
procedure: never run pre-hardening binaries against a hardened registry, never
hand-copy backups or `trust/sources/` over active files, and use
`magicite migration restore`.

No production qualification follows from unit tests with injected transports, fake
peer credentials or same-account fixtures. Linux and macOS distinct-UID deployment
rows start UNEVALUATED. The task authorizes functional code, tests and operator
instructions, not account creation, privilege escalation or service provisioning.

Critical reviewed risks: decision-only protection omits root/policy rollback;
timestamp ordering can hide revocation; writable identity/pins enable re-enrollment;
head-only fencing cannot distinguish a newer lease with no append. All four are
normative requirements in the specification and additive acceptance matrix.
