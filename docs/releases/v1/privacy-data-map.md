# Evidence privacy data map

This maps the current S09/S12 implementation, not a new storage contract. Sources:
`src/magicite/core/evidence.py`, `fingerprint_key.py`, `backup.py`, and
`docs/operations.md`. Gate witnesses bind these files and the execution report by digest.

| Surface | Contents and lifetime | Erasure/recovery rule |
|---|---|---|
| Route receipts | Bounded ephemeral decisions; no routine checkpoint | Process loss before explicit checkpoint loses unacknowledged receipts |
| Evidence segments | Explicitly checkpointed, minimized event payloads; operational default 30 days, audit metadata 90 days | Retention/deletion physically erases payloads; defaults are operator-configurable |
| Tombstones/segment stubs | Event IDs, deletion time and reason; no query/context text, secrets or paths | Minimal erasure residual prevents restored or rebuilt evidence reappearing |
| SQLite projections | Rebuildable views of authoritative evidence | Rebuild from surviving segments and tombstones; never a backup substitute |
| Local fingerprint key | HMAC authority for sensitive low-entropy correlators and authenticated control records | Never logged/exported as evidence; separately protected, operator-encrypted custody |
| Managed exports | Fresh per-export pseudonyms and authenticated registered destination metadata | Purge managed export directory and registered paths; reject poisoned registrations |
| Operator copies | Copies/moves outside tracked management | Outside deletion scope; every export carries the copy limitation notice (A02) |
| Historical raw-query records | Legacy managed history | Upgrade/default checkpoint cleanup removes managed raw sentinels |
| Backups | Complete authoritative snapshot through its declared recovery point | Separate expiry/custody; current authenticated overlay and independent anchor must replay before access |

Default persistence/export excludes raw prompts, procedure output, secrets and
absolute project paths. Context fingerprints are keyed locally; export correlators
are re-HMACed with a fresh random key that is not persisted. Checkpoint
acknowledgement follows durable commit under the existing writer lease. Backups
cannot recover later unretained receipts. Missing/stale overlay or anchor leaves
restored routing and evidence inaccessible. The known live trust-mirror weakness
is tracked separately in TRUST/SECURITY and is not concealed by privacy tests.
