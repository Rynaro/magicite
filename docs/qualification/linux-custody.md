# Linux custody and bounded recovery qualification

This slice adds a production-transport witness runner and a dedicated ephemeral
Ubuntu job. It preserves archived v1/r3 evidence and the pending PR #51 human
risk decisions. This document describes the procedure; actual results belong to
candidate-bound CI artifacts. It grants no whole TRUST, SECURITY, RELIABILITY or
GA approval. macOS separate-UID deployment remains UNEVALUATED.

The dedicated job checks out the exact PR head, copies source/Git metadata into
an isolated root-owned `/opt` runtime and installs the frozen lock with Python
3.12. Missing Linux, privilege, protected setup, source identity or core cases
fails the job; it cannot qualify through skipped tests. Its artifact contains
`report.json`, a report digest index, the command log and `SHA256SUMS`.

Run on an ephemeral Linux machine with an accessible locked runtime:

```sh
sudo /path/to/locked/python scripts/qualify_custody_linux.py \
  --candidate "$(git rev-parse HEAD)" --output /path/to/evidence
sudo /path/to/locked/python scripts/qualify_custody_linux.py \
  --candidate "$(git rev-parse HEAD)" --verify-report /path/to/evidence/report.json
```

The source checkout must be clean and executable/readable after child privilege
drop. Root creates only a unique fixture tree under `/opt` and its exact
production-CLI-generated enrollment descriptor under `/etc/magicite/registries`.
It does not create persistent accounts or change host-wide permissions. Run this
procedure only in the isolated runner/container, not on an operator workstation.

| Case | Observable assertion |
|---|---|
| preflight | Linux/root/protected setup succeeds; a real dropped-UID preflight fails |
| production-workflow | Distinct custodian/writer UIDs use the real service/client and disclose a reviewed body |
| foreign-peer | Foreign process connects directly; actual kernel credentials are observed; authority does not advance |
| same-uid | Production profile refuses a shared writer/custodian UID |
| protected-permissions | Writer cannot read private keys or open authority/profile/descriptor for writes; bytes remain unchanged |
| prepared-client-death | Real SIGKILL after authenticated preparation; body access stays closed, then fresh-fence reconciliation retains revocation |
| committed-client-death | Real SIGKILL after authenticated commit before local finalization; immutable exact retry retains one effect and no body |
| acknowledged-service-restart | SIGKILL/restart preserves acknowledged revocation and committed head |
| restore-retained-suffix | Supported pre-revoke backup restore recovers a changed config and retains later revocation/no body |
| restore-missing-suffix | Privileged fixture corruption removes an intermediate record while retaining current protected head; restore/access fail closed |

The service observer records `SO_PEERCRED` before calling the unchanged production
handler. Peer/path/receipt verification and the custody resolver remain active.
Children clear supplementary groups and use real numeric setgid/setuid. Their
UID/PID, head identity, effect count, denied-body status, source hashes and kernel
version are reconciled in the report and separate review.

Death barriers wrap the real `CustodianClient.call` only after its authenticated
prepare/commit return. They pause via IPC until the parent sends SIGKILL, before
local finalization. This exercises process death after receipt; it does not claim
a pre-receipt lost-wire fault. A short real writer lease and bounded expiry polling
permit recovery without editing the lease table. Fresh fences may advance generation
while committed sequence/MAC remain unchanged.

The service intentionally refuses preexisting sockets. After observed old-process
exit, the fixture operator removes only its known stale socket before restart.
This is explicit operator cleanup, not automatic production socket recovery.
The negative history drill damages only the isolated custodian fixture; real
production corruption/migration procedures are not changed.

Local Docker Linux arm64 rehearsal, when run with `--source-dirty`, records its
declared source state and hashes and is kept separate from clean hosted Ubuntu
qualification. Native preflight/report unit tests are guard checks, not deployment
proof. Required hosted results remain pending until the actual job/report succeeds.

Remaining gaps include macOS deployment, exhaustive crashes and power loss, actual
lost-wire boundaries, complete ACL/xattr branches and broad backup/fault matrices.
Named human residual-risk acceptance, distribution/RC evidence and external release
validation remain separate obligations. No existing release gate is promoted here.
