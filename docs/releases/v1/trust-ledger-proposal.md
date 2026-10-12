# Trust ledger hardening — proposal awaiting a human decision

Status: **PROPOSED, NOT ADOPTED OR IMPLEMENTED**. The existing mirror-deletion
weakness keeps TRUST and SECURITY failed and prevents GA. This proposal changes
neither frozen acceptance criteria nor release thresholds. RAMZA supplied the
proposal; its earlier independent ATLAS review did not authorize implementation.

The current trust decision mirrors are authoritative without an authenticated,
monotonic history. Removing a revoke mirror can make the earlier admission the
latest decision again. Authenticated import journals and backup reconciliation do
not provide anti-shrink protection to every live trust reader. No severity label
or external certification is inferred here.

Use an append-only versioned journal wrapping unchanged `TrustDecision/1` payloads:
registry ID, epoch, monotonic sequence, decision ID, payload digest, previous MAC,
and current HMAC over canonical bytes, with a domain-separated key. Sequence
orders decisions. Mirrors and SQLite become projections. Repeated decision ID
with identical payload is idempotent; changed payload or resequencing is rejected.
Authenticate the high-water registry/epoch/sequence/head tuple.

A MAC-protected head in the same tree cannot detect rollback of both journal and
head. Before implementation the maintainer must choose independently held custody
unreachable to the data-directory attacker. Synchronously persist that anchor
before acknowledgement for zero rollback of acknowledged decisions. A periodic
checkpoint instead has an explicit loss window and cannot claim full anti-shrink
protection. Another directory controlled by the same principal is not independent
custody. Root/key/custodian compromise or rollback of all state and its independent
anchor is outside this proposed guarantee; no remote service is implied.

Under the existing writer lease, verify chain and anchor, append/fsync, publish
local head, persist independent anchor, update projections, then acknowledge.
Readers use an atomic validated snapshot. Crash reconciliation may finish a valid
extension; it may never remove a revoke. Gaps, divergent heads, invalid MACs,
wrong registry/epoch, or an anchor ahead of a missing suffix require
`reconciliation_required`, with route and body access closed. Never synthesize
missing history during rebuild. Distinguish a virgin registry from missing keys
or heads; never fall back to default admission. Rotation binds old and new epochs
and heads while retaining revocation continuity.

Backups bind the journal head. Restoring an older backup needs a current,
independently held authenticated anchor and retained suffix, or restricted
recovery. Keys remain in operator-controlled encrypted custody; no silent rekey.
Migration needs preview, complete backup, and operator reconciliation of legacy
mirrors, revoke inventories, and seals. A reviewed deterministic genesis or
current-state import must disclose missing history, retain originals, and deny
ambiguous conflicts. Wrapping old bytes cannot retroactively authenticate them or
recover deleted history. Old readers reject new authority. Downgrade uses a
reviewed complete backup and preserves current revocations.

Every route/body/rebuild/restore reader and writer must use this authority;
`backup.py` currently writing mirrors directly also needs conversion. Tests must
cover edit/delete/reorder/duplicate/truncate/splice, rollback of journal plus local
head against the independent anchor, wrong key/registry/epoch, every crash boundary,
stale writer token, projection rebuild, pre-revoke backup restore, missing custody
or suffix, corrupt migration, zero-write doctor, and redacted errors.

Scope: new journal and S04 trust domain; S12 backup/doctor; tests and documentation
under C9 ownership. Affects C10/C8, AC-S04-03/04, AC-S11-01, AC-S12-03/05 and TRUST,
PROTOCOL, RELIABILITY, SECURITY, GA-ALL. A human must explicitly adopt the design
and custody contract, revise it, or defer it before implementation. Deferral keeps
the affected release gates failed; it is not a waiver.
