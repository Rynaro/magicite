# R3 current privacy and custody data map

Bound to candidate `{source}`; this is a source inventory, with limits stated below.
It supersedes the evidence-only map for this r3 assessment. Historical maps and r2
packages remain immutable. Sources and hashes are in `evidence/source-bindings.json`.

| Surface | Contents / lifetime | Erasure, recovery and boundary |
|---|---|---|
| Engram sources, assets and archive | Procedure content, metadata, resources and provenance; source files are authoritative | Operator-managed retention/archive; evidence erasure is not universal source deletion |
| Route receipts | Ephemeral minimized decisions before explicit checkpoint | Process loss before acknowledged checkpoint loses unretained receipts |
| Evidence segments | Minimized checkpointed payloads; operational default 30 days, audit default 90 days | Configurable retention; deletion erases payload and retains minimal event-ID/reason/time tombstones |
| Segment stubs and tombstones | Minimal deletion/audit residual | Exclude raw query/context, secrets and absolute project paths; prevent erased managed evidence reappearing |
| SQLite projections | Rebuildable evidence, engram, trust and approval caches; operational lease/control rows | Rebuild does not recover uncheckpointed receipts; an untrusted projection is not authority |
| Local fingerprint key | HMAC authority for evidence correlators and control records | Never log/export key bytes; separate operator custody/expiry; losing the key closes authenticated control access |
| Managed evidence exports | Fresh export pseudonyms and authenticated destination metadata | Purge registered destinations; unregistered operator copies are outside deletion guarantees |
| Historical raw evidence | Legacy managed records | Upgrade/default checkpoint cleanup removes managed raw sentinels; no claim about arbitrary operator copies |
| Local trust authority journal/head | Authenticated decisions, policy, source signers/artifact transforms, IDs, actors, timestamps and digests | Compare to fresh protected custody; missing/corrupt/stale state fails closed until explicit fenced reconciliation |
| Protected custodian store | Signing/journal private keys, enrolled policy, immutable history, monotonic head/fences and maintenance state | Independent OS-owned storage is provisioned explicitly; neither doctor nor ordinary startup initializes it; no generic evidence-retention/erasure promise applies |
| Protected enrollment/profile | Registry/project binding, public pin, OS identities and socket configuration | Fixed protected configuration; operator maintenance; sensitive paths are omitted from the new custody check |
| Custody socket transport | Bounded authenticated requests/replies, history and status | Read diagnosis does not register fences, mutate heads, enroll or reconcile; peer/ACL/OS branches still need real deployment evidence |
| Approval mirrors and DB audit | Decision-bound payload, actor, timestamps and proposal/state transitions | One logical audit reused across replay; malformed/incomplete audit requires reconciliation; these operational records are outside evidence-retention promises |
| Backups and restore overlays | Snapshot content, acknowledged evidence and trust/privacy recovery state | Separate expiry; retain authenticated live restriction/erasure overlay; no claim that unretained receipts can be restored |
| Diagnostics and logs | Existing doctor reports project/data/DB locations; new custody check uses fixed states/remediation | New check omits key material and configured custody/socket paths, including exception text; fixture canaries prove this bounded behavior |

Default evidence persistence/export excludes raw prompts, procedure output, secrets
and absolute project paths. That guarantee does not erase operator-authored engram
content, configured filesystem paths, arbitrary copies, or protected custody history.
Trust decision projections/mirrors are inert as authority; deleting one is not an
active defense. Fresh independently held head/journal validation enforces current
trust restrictions. This source inventory does not certify separate-user custody,
production deployment, complete privacy lifecycle qualification, or security sign-off.
