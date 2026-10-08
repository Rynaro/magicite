# Setup: protected project custody

This is the **Linux administrator** prerequisite for [Quick Setup](quick-setup.md).
Magicite requires a separate custodian OS identity and a root-installed project
descriptor before serving a project. It does not create accounts or write `/etc`.
The recipe below is source-reviewed; it is not a newly executed deployment
qualification. macOS production custody remains unqualified. The existing
[Linux custody qualification](qualification/linux-custody.md) records its own
bounded evidence; its runner is not a workstation installer.

After this prerequisite is complete, source installations containing the new
`magicite init --host claude` command can perform [one-command project connection](quick-setup.md#one-command-connection-source--next-build-only).
The published **1.0.0rc1 wheel does not contain `init`** and uses the manual Quick
Setup steps. Init checks existing custody; it never provisions privileged accounts,
enrollment, protected directories or genesis, and never promotes imported drafts.

Use this recipe for a **new registry**. Existing registries need the migration
and recovery procedures in [Operations](operations.md), not a fresh genesis.

## 1. Choose identities and permanent paths

Have your administrator provision distinct accounts: a project client (here
`alice`) and a dedicated custodian (here `magicite-custodian`). The client owns
and can write the project; the custodian must not be the client. Use the same
client account for CLI approval, model fetching, and MCP.

The client follows [Install](install.md). The custodian also needs the same
verified wheel in an independently managed Python 3.12 environment. As root,
create `/opt/magicite-preview/venv` and install the wheel already verified by
Install; keep that environment and its ancestors root-owned, without group or
world writes. Do not run the custodian using a client-writable interpreter or
package directory.

```bash
set -euo pipefail
# Administrator: replace this with the wheel verified in Install.
VERIFIED_WHEEL=/absolute/path/to/magicite-1.0.0rc1-py3-none-any.whl
sudo mkdir -p /opt/magicite-preview
sudo python3.12 -m venv /opt/magicite-preview/venv
sudo /opt/magicite-preview/venv/bin/python -m pip install "$VERIFIED_WHEEL"
sudo /opt/magicite-preview/venv/bin/magicite --version
```

Verify the custodian can execute this environment. Protected custody paths and
**every ancestor** must be absolute, nonsymlink, owned by root or the custodian,
with no group/world writes or extended ACLs. Do not place them under `/tmp` or
inside the client-owned project. Existing `/var`, `/var/lib`, `/etc`, and `/opt`
ancestry must satisfy these rules; stop and investigate a mismatch rather than
relaxing permissions indiscriminately.

The following examples use one project and one service. Replace `alice`, the
project path, registry ID, and operator identity consistently. Canonicalize the
project path once, after creating the actual project directory.

```bash
# Administrator shell: retain these variables for later administrator steps.
PROJECT=$(realpath /absolute/path/to/your-project)
CLIENT_ACCOUNT=alice
CUSTODIAN_ACCOUNT=magicite-custodian
CLIENT_UID=$(id -u "$CLIENT_ACCOUNT")
CUSTODY_BIN=/opt/magicite-preview/venv/bin/magicite
CUSTODY_BASE=/var/lib/magicite-custody/my-project
REGISTRY_ID=my-project
OPERATOR=your-operator-identity
sudo install -d -o root -g root -m 0755 /var/lib/magicite-custody
sudo install -d -o "$CUSTODIAN_ACCOUNT" -m 0755 "$CUSTODY_BASE"
sudo install -d -o "$CUSTODIAN_ACCOUNT" -m 0755 "$CUSTODY_BASE/run"
```

`custody init` creates the nonexistent `private` store directory itself.
Do not create it in advance.

The client needs read access to the profile and traversal through the socket
parent; keys and authority state remain private. The service creates a writable
socket and authenticates requests by kernel peer UID; directory protection and
the pinned client UID enforce the boundary.

## 2. Export, review, and enroll the starting policy

As administrator, export the installed default policy as the custodian. Then
inspect the **exact bytes**, verify the default empty trust roots and revision
1, and record their hash. This is policy enrollment, not skill approval.
Keep autonomous approval disabled: do not set `MAGICITE_AUTONOMOUS=1` or enable
it in project configuration.

```bash
sudo -u "$CUSTODIAN_ACCOUNT" /opt/magicite-preview/venv/bin/python -c \
  'import json; from magicite.core.trust import default_policy; print(json.dumps(default_policy().to_dict()))' \
  | sudo -u "$CUSTODIAN_ACCOUNT" tee "$CUSTODY_BASE/policy.json" >/dev/null
cat "$CUSTODY_BASE/policy.json"
sha256sum "$CUSTODY_BASE/policy.json"
```

After human review, paste the reviewed digest below. Do not automatically
approve whatever hash happens to be present.

```bash
REVIEWED_POLICY_SHA256=replace-with-reviewed-sha256
sudo -u "$CUSTODIAN_ACCOUNT" "$CUSTODY_BIN" custody init \
  --directory "$CUSTODY_BASE/private"
sudo -u "$CUSTODIAN_ACCOUNT" "$CUSTODY_BIN" custody enroll \
  --directory "$CUSTODY_BASE/private" --registry-id "$REGISTRY_ID" \
  --policy "$CUSTODY_BASE/policy.json" --actor "$OPERATOR" \
  --reviewed-sha256 "$REVIEWED_POLICY_SHA256"
```

Run init/enrollment once. If enrollment reports `reconciliation_required`,
**a commit may already have happened**. Preserve the store and reviewed inputs;
do not reset or blindly re-enroll. Once the protected profile/service is
available, use read-only `magicite custody status --project-root PROJECT` and
inspect protected authority history. Current policy alone does not prove the
original enrollment; seek operator reconciliation if the state is uncertain.

## 3. Create the profile and install its exact descriptor

`custody profile` creates the protected profile but only **prints** the root
descriptor. Capture stdout unchanged, review it, and have root install it at the
hash of the canonical project root. Stop if a descriptor already exists.

```bash
sudo -u "$CUSTODIAN_ACCOUNT" "$CUSTODY_BIN" custody profile \
  --directory "$CUSTODY_BASE/private" --registry-id "$REGISTRY_ID" \
  --project-root "$PROJECT" --client-uid "$CLIENT_UID" \
  --socket-path "$CUSTODY_BASE/run/custody.sock" \
  --profile-path "$CUSTODY_BASE/profile.json" \
  | sudo -u "$CUSTODIAN_ACCOUNT" tee "$CUSTODY_BASE/enrollment.json" >/dev/null
cat "$CUSTODY_BASE/enrollment.json"
PROJECT_HASH=$(printf '%s' "$PROJECT" | sha256sum | cut -d ' ' -f 1)
sudo install -d -o root -g root -m 0755 /etc/magicite/registries
sudo test ! -e "/etc/magicite/registries/$PROJECT_HASH.json"
```

Proceed only if the profile command succeeded, the descriptor contains the
expected project/UIDs/profile path, and that last check succeeded. Then install
those exact bytes, without overwriting an existing descriptor:

```bash
sudo sh -c 'set -C; cat "$1" > "$2"' sh \
  "$CUSTODY_BASE/enrollment.json" "/etc/magicite/registries/$PROJECT_HASH.json"
sudo chmod 0644 "/etc/magicite/registries/$PROJECT_HASH.json"
```

## 4. Start custody, then initialize the client journal

Administrator: run the service under the custodian account in the same shell
where you defined the variables above. Keep it running in the foreground; use
another terminal for the client. Arrange your own service manager for persistence.

```bash
sudo -u "$CUSTODIAN_ACCOUNT" "$CUSTODY_BIN" custody serve \
  --directory "$CUSTODY_BASE/private" --profile-path "$CUSTODY_BASE/profile.json"
```

Project client, in another terminal (replace both absolute paths):

```bash
MAGICITE=/home/alice/.local/share/magicite-preview/venv/bin/magicite
PROJECT=$(realpath /absolute/path/to/your-project)
"$MAGICITE" custody initialize-journal --project-root "$PROJECT"
"$MAGICITE" custody status --project-root "$PROJECT"
```

Expect `INITIALIZED` and a fresh authenticated status. A missing descriptor,
UID mismatch, protected-path violation, unavailable service, or missing journal
must be corrected at its source; do not substitute simulated custody.

Continue with [Quick Setup](quick-setup.md) to fetch the model, connect MCP,
and approve a first skill. For detailed trust and recovery semantics, see
[Trust and governance](06-trust-governance-lifecycle.md) and [Operations](operations.md).
