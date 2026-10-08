# Quick Setup: add Magicite to a project

Start here **after [Install](install.md) and administrator [Setup](setup.md)**.
You need a running protected custodian, initialized project journal, and the
client account pinned in the project descriptor. This is the published
1.0.0rc1 developer preview; production macOS custody remains unqualified.

## One-command connection: source / next build only

**The published 1.0.0rc1 package lacks `init`.** Use the manual steps below with
that package. From a source installation containing the command, after completed
administrator Setup, run inside the existing enrolled project:

```sh
magicite init --host claude
# Or select the same canonical project enrolled by the administrator:
magicite init --host claude --project-root /absolute/path/to/project
```

Init refuses missing/unhealthy custody before creating registry state or changing
host files. It requires completed offline FastEmbed inference, then merges
`.mcp.json` and one marked block in `CLAUDE.md`, preserving unrelated content.
Absolute executable/project paths point to this installed environment. A local
initialize/tools-list check verifies connection; reconnect Claude Code afterward.
This checks transport, not a live Claude model request or general routing quality.

When a model is unavailable, an interactive terminal offers separate default-no
network download consent. For intentional noninteractive model fetching:

```sh
magicite init --host claude --fetch-model
```

That flag permits only the existing model fetch, not skill import or approval.
The interactive terminal separately offers import from `skills/`, or an existing
project-contained `--skills my-skills` directory. It preserves partial registration
results and asks again before digest review, requires an operator identity, and
asks default-no approval for each displayed current digest. Imported `SKILL.md`
files stay draft even after approval; preparation still follows the lifecycle
steps below. Noninteractive use never prompts/imports/approves, and reports the
existing MCP register and `magicite trust review/approve` commands.

The result distinguishes **connection verified** from **approved non-draft skills
available**. Even admitted non-drafts must satisfy query policy/context eligibility.
No available skills is a successful connection with more preparation needed.

Rerunning preserves configuration bytes and creates no redundant blocks/backups.
Malformed JSON, duplicate keys, conflicting Magicite entries, malformed markers,
and symlink targets stop with manual remediation. Changed existing targets have
private content-addressed `.magicite-<sha256>.bak` backups beside the file. On a
configuration or connection failure, init restores only files still owned by its
attempt; it preserves concurrent edits and reports recovery paths. Registry,
database and model-cache effects are not rolled back. Import/approval failures
keep the verified connection and earlier committed effects, and exit unsuccessfully.
Never replace a concurrent edit with a backup without reviewing both.

## 1. Prepare the embedding model

As the same client account/environment that will run MCP:

```sh
MAGICITE=/home/alice/.local/share/magicite-preview/venv/bin/magicite
PROJECT=$(realpath /absolute/path/to/your-project)
"$MAGICITE" fetch-model
"$MAGICITE" doctor --project-root "$PROJECT"
```

The explicit fetch acquires the default `BAAI/bge-small-en-v1.5` model over the
network. Fetch before skill import or lookup. Keep the same account/environment
for MCP so it can find the cached model; no custom cache path is assumed here.
`doctor` diagnoses setup and evidence gaps; it is not a v1 qualification pass.

## 2. Connect your MCP client

For Claude Code, merge this entry into the project's `.mcp.json`, preserving
other servers. Replace both paths with permanent absolute paths and use the
**same canonical project root** enrolled during Setup. Restart/reconnect the
client after changing its configuration.

```json
{
  "mcpServers": {
    "magicite": {
      "command": "/home/alice/.local/share/magicite-preview/venv/bin/magicite",
      "args": ["serve", "--project-root", "/absolute/path/to/your-project"],
      "env": {"MAGICITE_EMBEDDING_OFFLINE": "1"}
    }
  }
}
```

Other MCP clients use the same stdio command and arguments. See the
[Claude Code adapter](adapters/claude-code.md) for host details; hooks are optional.

## 3. Import a small skill

Inside the project, create `skills/review-checklist.egr.md`. This small native
example uses the supported legacy `engram/0.2` reader and starts `nascent`,
so explicit trust approval can make it eligible. A `SKILL.md` import starts
`draft`; trust approval alone does not complete its lifecycle.

```markdown
---
spec: engram/0.2
name: review-checklist
id: egr_8d91c2a0
version: 1
provenance: authored
intent:
  does: Review a code change for correctness, error handling, and test coverage.
  use_when: Reviewing a code change.
  not_when: Writing a new feature.
triggers:
  positive: [review a code change, check error handling, assess test coverage]
  negative: [write a new feature]
plasticity:
  status: nascent
trust:
  origin: authored
  verification_status: pending
---

## Procedure
1. Read the changed code and its callers.
2. Check error handling and boundary cases.
3. Identify meaningful missing tests.
4. Report concrete findings with file locations.
```

Ask your assistant to call the Magicite **MCP `register` tool** with:

```json
{"path": "skills", "format": "auto"}
```

This is an MCP tool, not a CLI command. Check the response's `registered` entries
and `validation_errors`; retain the imported entry's `id`. Imported content
starts pending and cannot approve itself. Routing/body disclosure before
approval can exclude it or deny access; this is expected.

## 4. Review and approve the exact imported content

In your client terminal, substitute the returned ID:

```sh
ENGRAM_ID=replace-with-registered-id
"$MAGICITE" trust review --project-root "$PROJECT" --engram-id "$ENGRAM_ID"
```

Read the imported file and the review output. Only after human review, copy the
content digest into this command and supply your operator identity:

```sh
REVIEWED_CONTENT_DIGEST=replace-with-reviewed-content-digest
"$MAGICITE" trust approve --project-root "$PROJECT" --engram-id "$ENGRAM_ID" \
  --expected-digest "$REVIEWED_CONTENT_DIGEST" --actor your-operator-identity
```

Repeat review separately for each real skill. Keep autonomous approval disabled.
A digest mismatch means content changed: inspect and review again.

## 5. Route, then read the selected body

Ask the assistant:

> Use Magicite to route “Review a code change for correctness, error handling,
> and test coverage.” Inspect the returned selection. Load its L2 skill body
> with the current content and policy digests before following the procedure.

The corresponding MCP `route` arguments are:

```json
{"query": "Review a code change for correctness, error handling, and test coverage.", "session_id": "first-project-session"}
```

If `status` is `selected`, choose an ID from `selected_ids`, find its `name`
in `candidates`, and take its digest from `selected_content_digests[ID]`.
Use that name/digest and the top-level `policy_digest` in MCP `load_skill_body`:

```json
{
  "name": "replace-with-selected-name",
  "level": "L2",
  "expected_content_digest": "replace-with-selected-content-digest",
  "expected_policy_digest": "replace-with-route-policy-digest"
}
```

A successful body response contains the procedure. Never invent or reuse stale
digests. If `stale_decision` occurs, route again; if `missing_context` occurs,
supply the required current digests. Abstention is a valid result, not an
instruction to bypass eligibility. This small example checks wiring and review,
not retrieval quality or safe-abstention accuracy.

## If the first run fails

| Symptom | Next action |
|---|---|
| Custody unavailable / UID mismatch | Check the service, pinned client account, descriptor and journal with your administrator using [Setup](setup.md). |
| Offline model unavailable | Run `fetch-model` as the MCP client account with the same environment, then reconnect. |
| Path outside project / wrong registry | Match `--project-root` to the enrolled canonical root; keep imported skills inside it. |
| Pending / quarantined skill | Inspect `trust review` and scanner findings; approve only reviewed eligible content. Do not disable custody or trust checks. |
| No selection | Inspect route exclusions and query relevance; do not force disclosure of an unselected body. |

Magicite stores, routes, and audits skills. Your host owns execution and its
permissions. For the full command/tool contracts, see [Operations](operations.md)
and [Protocol and signals](05-protocol-and-signals.md).
