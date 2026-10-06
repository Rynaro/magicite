# Claude Code read-workflow qualification

The reusable runner and validator are ready for an actual authenticated host
terminal. Live route/body qualification remains **UNEVALUATED** until its report
passes separate review. The user's host session is signed in; agent-exec auth
results describe that execution context and do not establish host logout.
Computer Use refuses control of the Terminal app, so this task does not bypass
that restriction or ask for a new login, token or credential export.

From the actual host terminal, run this isolated candidate command:

```sh
/private/tmp/magicite-r3-qualification/venv/bin/python \
  /Users/henrique/.codex/worktrees/v1-r3-qualification/magicite/scripts/qualify_claude_host.py \
  --output /private/tmp/magicite-v1-host/host-terminal-guarded-4
```

The runner requires a clean reviewed checkout.
It creates a disposable synthetic registry with one reviewed Steam fixture, then
launches the installed Claude binary with restricted mode, explicit strict MCP
configuration/settings, no built-in tools, only three read-tool permissions,
no session persistence and a 120-second timeout. It uses the existing login via
Claude itself; it never opens credential files, reads keychain values or changes
login/settings. Temporary fixture custody is explicit simulation, not production
Darwin custody qualification. Agent-managed command contexts can still lack the
host authentication capability; that failure remains an unqualified observation.

Only allowlisted MCP request/result fields and host tool-use/result IDs are saved.
Opaque string IDs/cursors use stable typed hashes and digests require hexadecimal grammar.
Unknown body values are replaced with markers; raw debug logs, arbitrary model
prose, credentials, environment dumps and session transcripts are not archived.
The relay forwards the original protocol bytes unchanged. The validator checks
exact RPC IDs, schema fingerprints, actual host events and the route's selected
identity/content/policy digests. It requires a matching fixture L2 body and a
second stale-digest request returning `stale_decision` with every body field empty.
A model's final prose cannot produce PASS.

Verification, after the host command finishes:

```sh
/private/tmp/magicite-r3-qualification/venv/bin/python \
  /Users/henrique/.codex/worktrees/v1-r3-qualification/magicite/scripts/qualify_claude_host.py \
  --output /private/tmp/magicite-v1-host/host-terminal-guarded-4 --verify
```

The report binds the Git candidate, source hashes, Claude version/binary digest,
server MCP SDK, invocation, transcript hashes and scoped fixture expectations.
The host SDK is unavailable unless exposed; it is not inferred from server SDK.
Configuration preservation checks compare inode/size/mtime metadata only, without
reading potentially sensitive configuration contents. They do not prove byte
identity or absence of unrelated concurrent changes. Generated config lives only
in the disposable directory and is hashed before launch.

Early context-limited discovery observed Claude Code 2.1.288 advertising modern
`2026-07-28` in request metadata and receiving sixteen tools plus serverInfo.
No initialize request was observed: this is **modern version adoption**, not a
negotiated legacy handshake. Installed MCP SDK 2.0.0 documents pinned-modern
adoption without initialize (`mcp/client/client.py`, `ConnectMode`); the frozen
plan amendment preserves that distinction. Early execution-context authentication
failure produced no agent tool calls and does not qualify route/body behavior.

This bounded agent-driven fixture test is separate from independent human/operator
acceptance, custody/security, published-channel, model-quality and complete
protocol retry/cancellation qualification. Existing host-support classifications,
generic SDK transcripts and archived release evidence remain unchanged. No whole
PROTOCOL, DOCS, SECURITY or GA gate is promoted by this readiness document.


The first user-run host attempt at `694195c` used Claude Code 2.1.292, had no
sandbox network marker, preserved configuration metadata, and discovered all
sixteen tools through modern version adoption. It failed before any host tool
calls. The initial capture retained only a general authentication category, which
cannot distinguish account status from expired request credentials, settings
isolation or a different executable/auth source.

The diagnostic runner compares `claude auth status --json` in the disposable
project with normal versus restricted/explicit settings. It saves only exit code,
logged-in boolean and a bounded method label, never account identities or auth
values. These two read-only checks have ten-second timeouts and send no model
requests. The runner invokes the resolved executable directly; shell aliases or
functions are not applied. Restricted mode ignores user/project/local settings,
so settings-based auth helpers/providers may differ; this is a possibility until
observed. Safe mode documents normal auth but also disables custom MCP servers;
no isolation mode is changed without evidence.

Failed requests now retain fixed error reason signals, recognized API error types
and HTTP status when exposed. Stderr is classified in memory and discarded; raw
messages, credentials and debug output are never saved. Unknown causes remain
unknown, and no diagnostic alone qualifies the actual tool workflow.

A third user-run attempt at `f499dd2` authenticated normally in both preflights
and completed four real host calls: introspection, route, a matching L2 body and
stale-digest refusal without body. It exited successfully, but full qualification
remains UNEVALUATED: the global `~/.claude.json` file's inode, size and modification
time changed. The settings/MCP metadata roles remained unchanged. No private
configuration contents were read, so this evidence cannot distinguish runtime
bookkeeping from changed preferences or configuration.

The validator now accepts the fixture's known business-name alias as well as its
routed ID, matching the production lookup, while requiring the routed digest,
policy, returned identity and exact body. This corrects an independent validator
refusal; it does not waive configuration preservation or retroactively produce a
passing report. The original third-attempt evidence remains source-bound to
`f499dd2` and requires separate scoped review.

[Claude settings documentation](https://code.claude.com/docs/en/settings) describes
`~/.claude.json` as self-written state containing sign-in, MCP/trust information
and global preferences. A fresh `CLAUDE_CONFIG_DIR` also has its own sign-in,
according to the [authentication documentation](https://code.claude.com/docs/en/authentication).
Using that alternative would require operator authentication in the isolated
namespace, without copying credentials. No additional run or broader acceptance
of configuration writes is implied by this bounded workflow observation.


The prospective guarded command first validates a native macOS process-scoped
write-denial mechanism against disposable canaries: reads and unrelated writes
must work; ordinary writes, atomic replacements and inherited child writes to the
canary must fail, and its bytes must remain unchanged. It then wraps every Claude
invocation, including version and auth readiness, with the same guard denying
writes only to the monitored configuration paths and their resolved targets.
It records the guard command, profile/binary hashes and canary proof. The profile
adds no network or Keychain IPC restriction. Actual Claude behavior under this
guard remains untested until the operator executes the reviewed command.

The guard never reads, copies, changes permissions on or restores private
configuration/credentials. It fails closed on unsupported guard execution,
alternate `CLAUDE_CONFIG_DIR`, changed monitored metadata or a host failure;
the strict configuration check remains unchanged. All original evidence remains
immutable. The previous unguarded workflow cannot become a full PASS through
this prospective correction.
