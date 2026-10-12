# First steps with Magicite in your project

This guide takes you from installing Magicite to asking an assistant to route
and load a reviewed project skill. It uses the published **Magicite 1.0.0**
package and covers both a new project and the current Jin setup.

Magicite is a local MCP server and skill registry. It helps a host find and
load project instructions; the host decides what to do with them. Magicite
does not execute skills, grant the host permissions, or guarantee a better
answer.

## Know which step you have completed

These are separate milestones. Passing one does not imply the next one has
passed.

| Stage | Evidence | What it means |
|---|---|---|
| Installed | `magicite --version` reports `1.0.0` | The client executable is available. No project is enrolled yet. |
| Enrolled | Administrator setup, protected descriptor, authenticated custody service, and initialized journal all pass | The project has protected trust authority. |
| Connected | The host discovers Magicite MCP tools and can call them | The host can reach the project registry. A config file alone does not prove this. |
| Engram admitted | `register` accepts the artifact and reports no validation errors | The artifact is structurally usable; this does not grant trust. |
| Lifecycle eligible | The artifact's lifecycle state permits routing | Native `engram/1.0` authored content starts `nascent`; an imported `SKILL.md` remains `draft` until its separate preparation lifecycle is complete. |
| Skill trusted | A human reviewed and approved the exact current content digest | Trust does not override lifecycle, policy, capability, or query-fit requirements. |
| Route and load verified | A route selects a skill and `load_skill_body` accepts the same content and policy digests | The assistant received the exact skill body selected by that route. |

An engram file on disk, a successful schema check, or an entry in the SQLite
index does not by itself establish enrollment, approval, or route eligibility.

## Before you begin

Magicite 1.0.0 supports Python 3.11 and 3.12. The documented production
operating profile is Linux with protected project custody. An administrator
sets up a dedicated custodian identity and protected authority separate from
the project client. Follow [protected project setup](setup.md) before
connecting a project. Production macOS custody is unqualified; native support
code exists, but a local macOS install should not be described as a supported
production deployment.

Keep the client installation at a stable absolute path. Use the same client
account for model fetching, any interactive review, and the MCP host. Leave
autonomous approval disabled.

## 1. Install the published package

Create a dedicated environment for the pinned release:

```sh
MAGICITE_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/magicite"
mkdir -p "$MAGICITE_HOME"
python3.12 -m venv "$MAGICITE_HOME/venv-1.0.0"
MAGICITE="$MAGICITE_HOME/venv-1.0.0/bin/magicite"
"$MAGICITE_HOME/venv-1.0.0/bin/python" -m pip install 'magicite==1.0.0'
"$MAGICITE_HOME/venv-1.0.0/bin/python" -m pip check
"$MAGICITE" --version
```

The last command should report version `1.0.0`; `pip check` should report no
dependency conflicts. Python 3.11 works too: create the environment with
`python3.11` instead. For GitHub artifact attestation and manifest verification,
see [Install](install.md) and the [v1.0.0 PyPI publication receipt](https://github.com/Rynaro/magicite/releases/download/v1.0.0/pypi-publication-receipt.json).

## 2. Enroll the project with an administrator

Ask an administrator to complete [Setup](setup.md) for the canonical project
directory. The setup creates protected custody and the root-installed project
descriptor, then starts the custody service. The project client initializes
its journal only after those steps are complete.

For a new registry, follow the new-registry procedure in Setup. If the project
already contains Magicite data, pause before initializing anything. Use the
recovery and migration process in [Operations](operations.md); do not create a
fresh genesis, overwrite the old registry, or approve legacy data just to make
the setup command pass. A `reconciliation_required` result means preserve the
existing state and have the administrator reconcile it.

## 3. Fetch the embedding model into a durable cache

Magicite uses `BAAI/bge-small-en-v1.5` for local embeddings. Fetch it as the
same client account that will run the MCP server. Set a permanent cache path so
the host can use the model after temporary directories are cleared:

```sh
MAGICITE_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/magicite"
MAGICITE="$MAGICITE_HOME/venv-1.0.0/bin/magicite"
export FASTEMBED_CACHE_PATH="$MAGICITE_HOME/model-cache"
"$MAGICITE" fetch-model
```

After the model is present, pass both the cache path and offline mode to the
MCP process. This makes its model location explicit and prevents an unexpected
network fetch when the server starts:

```text
MAGICITE_EMBEDDING_OFFLINE=1
FASTEMBED_CACHE_PATH=/absolute/path/to/.local/share/magicite/model-cache
```

Use the actual absolute cache path for your account in the host configuration.
The model fetch is a network download; omit it only if this same client
environment already has the model in the cache you will configure.

## 4. Check project health and connect an MCP host

From the enrolled project, resolve its canonical path and run the doctor:

```sh
MAGICITE_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/magicite"
MAGICITE="$MAGICITE_HOME/venv-1.0.0/bin/magicite"
PROJECT="$(pwd -P)"
"$MAGICITE" doctor --project-root "$PROJECT"
```

Resolve custody or model errors at their source before connecting. Doctor is a
setup check, not a security certification or retrieval-quality evaluation.
For a durable custom model cache, run it with the same environment the host will
use:

```sh
FASTEMBED_CACHE_PATH="$MAGICITE_HOME/model-cache" MAGICITE_EMBEDDING_OFFLINE=1 \
  "$MAGICITE" doctor --project-root "$PROJECT"
```

For Claude Code, use the one-command connection after administrator setup:

```sh
FASTEMBED_CACHE_PATH="$MAGICITE_HOME/model-cache" MAGICITE_EMBEDDING_OFFLINE=1 \
  "$MAGICITE" init --host claude --project-root "$PROJECT"
```

After `init`, inspect the generated Magicite MCP entry. Ensure its `env`
contains both `MAGICITE_EMBEDDING_OFFLINE=1` and
`FASTEMBED_CACHE_PATH=<the same absolute model-cache path>`, preserving other
servers. Reconnect the host only after the values and project root are right.

For a stdio MCP host with manual configuration, add the same command, project
root, and environment to its config. For example, Codex TOML looks like this;
replace both example paths with your absolute paths:

```toml
[mcp_servers.magicite]
command = "/home/alice/.local/share/magicite/venv-1.0.0/bin/magicite"
args = ["serve", "--project-root", "/home/alice/src/my-project"]

[mcp_servers.magicite.env]
MAGICITE_EMBEDDING_OFFLINE = "1"
FASTEMBED_CACHE_PATH = "/home/alice/.local/share/magicite/model-cache"
```

Preserve other server entries. Restart or reconnect the host, then confirm it
lists Magicite tools such as `route` and `load_skill_body`. A valid TOML or JSON
file is only configuration evidence; a successful tool call verifies the MCP
connection.

## 5. Add and approve a project engram

Write a small, focused `engram/1.0` artifact in your project. The front matter
describes its intent, triggers, typed capabilities, and risk; the body gives
the procedure. Copy the [beginner example](examples/first-skill.egr.md) into
your project's `.magicite/engrams/` directory, then adapt it using the
[Engram format guide](04-engram-format.md). The example summarizes material
provided in the conversation; the host writes the answer and Magicite only
supplies the procedure. Keep the skill body versioned with the project.

Engram IDs must be unique within a project. The example's `egr_51a7c001` can
stay if it is unused there; give every additional skill a different ID. Its
body digest matches the exact procedure shown. If you edit that procedure,
recompute both `routing.body_digest` and
`origin.content_hashes.body_sha256`, then register and review the updated
artifact. A body edit changes the digest, so a prior review or approval no
longer covers the changed content.

For native engrams saved in `.magicite/engrams/`, call the MCP `register` tool
with a path relative to the project root:

```json
{"path":".magicite/engrams","format":"auto"}
```

Inspect the response for validation errors and retain each returned engram ID.
Native `engram/1.0` authored artifacts start `nascent`, a routable lifecycle
state. They still need a human trust review and must pass policy, capability,
context, and query-fit checks. Imported `SKILL.md` artifacts start `draft`;
approval alone does not complete their separate preparation lifecycle. See
[Trust and governance](06-trust-governance-lifecycle.md) before importing
existing skills.

Review the exact file and digest in a client terminal:

```sh
PROJECT="$(pwd -P)"
MAGICITE_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/magicite"
MAGICITE="$MAGICITE_HOME/venv-1.0.0/bin/magicite"
ENGRAM_ID=replace-with-registered-id
"$MAGICITE" trust review --project-root "$PROJECT" --engram-id "$ENGRAM_ID"
```

Only after a human has read the content, approve the digest shown by that
review. Replace both placeholders with the current values:

```sh
REVIEWED_CONTENT_DIGEST=replace-with-reviewed-content-digest
"$MAGICITE" trust approve --project-root "$PROJECT" --engram-id "$ENGRAM_ID" \
  --expected-digest "$REVIEWED_CONTENT_DIGEST" --actor your-operator-identity
```

If the digest changes, review the new content before approving it. Do not let
the engram's own metadata grant trust to itself. Autonomous approval stays off.

## 6. Route, inspect, then load

Ask your assistant to route a concrete task, inspect the result, and load the
selected body before following it. At the MCP level, call `route` with the
query and a session ID:

```json
{"query":"Review this change for correctness and missing tests.","session_id":"review-2026-01"}
```

If the result is `selected`, find the selected ID in `selected_ids`, its name
in `candidates`, and its digest in `selected_content_digests[ID]`. Call
`load_skill_body` at level `L2` using that name and both current digests:

```json
{
  "name": "review-checklist",
  "level": "L2",
  "expected_content_digest": "digest-from-this-route",
  "expected_policy_digest": "policy-digest-from-this-route"
}
```

This binds the loaded instructions to the decision you inspected. If the route
used typed context, pass that same context to the load call. If the result is
`stale_decision`, route again. If Magicite abstains, treat that as a valid
result; do not force-load an unselected skill.

For the full worked request/response walkthrough, see
[Quick Setup: route, then read the selected body](quick-setup.md#5-route-then-read-the-selected-body).

The host interprets and executes the loaded procedure using its own tools and
permissions. For protocol users, `signal_use`, `signal_outcome`, and
`session_end` record session evidence; see [Protocol and signals](05-protocol-and-signals.md).
Signals and Dream do not silently change stable `dense-v1` routing order.

## Jin: current integration status

As of 2026-10-12, Jin is **installed but not connected**. The pinned PyPI
client passes `pip check`, and real offline embedding works with the persistent
model cache. Three new native engrams passed schema and body-digest admission:

| Engram | ID | Current state |
|---|---|---|
| `jin-google-account-route-isolation` | `egr_0f2a9101` | Pending review; not approved or route-tested |
| `jin-calendar-projection-interaction` | `egr_0f2a9102` | Pending review; not approved or route-tested |
| `jin-first-run-storage-onboarding` | `egr_0f2a9103` | Pending review; not approved or route-tested |

The existing legacy engrams remain untouched. Jin's protected custody is not
configured, so doctor health is false; the host configuration remains
unchanged, and no authenticated MCP route or body load has passed. The new
engrams request `cargo` and `pnpm` capabilities. Protected policy and typed
route context must authorize those capabilities; listing them in a request
alone does not grant them. An administrator must resolve legacy trust
reconciliation and custody before host connection or per-skill trust review.
Production macOS custody is unqualified, so this staged setup is not evidence
of a supported production deployment.

## Troubleshooting

| Symptom | What to do |
|---|---|
| `doctor` reports missing or unhealthy custody | Stop before host connection. Ask the administrator to check the descriptor, service, pinned client identity, and journal using [Setup](setup.md). |
| `reconciliation_required` on an existing project | Preserve the registry and evidence. Follow [Operations](operations.md) with an administrator; do not run new genesis or repeat enrollment blindly. |
| Offline embedding model unavailable | Fetch it as the MCP client with `FASTEMBED_CACHE_PATH` set to the durable cache, then reconnect with the same environment. |
| Engram is pending, quarantined, or absent from candidates | Inspect current content and policy digests and complete the documented review lifecycle. Do not bypass eligibility. |
| Route abstains | Check that the query fits an approved skill and inspect exclusions. Abstention is expected when no eligible skill fits. |
| Body load reports a stale decision | Route again and load with the new route's content and policy digests. |

For the complete reading path, see [Install](install.md), [Setup](setup.md),
[Quick Setup](quick-setup.md), [Trust and governance](06-trust-governance-lifecycle.md),
and [Operations](operations.md).
