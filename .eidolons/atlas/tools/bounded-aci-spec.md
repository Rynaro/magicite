# Bounded ACI Specification

> The Agent-Computer Interface that ATLAS requires. Implementations are free
> to back these primitives with MCP servers, direct syscalls, language-server
> bridges, or anything else — as long as the **bounds** hold.

---

## Invariants (mechanical)

| # | Rule | Rationale |
|---|------|-----------|
| B-1 | No tool ever returns >`MAX_BYTES_PER_CALL` (default 8 KiB). | Prevents stdout floods that cause context rot. |
| B-2 | Every overflow returns a pagination cursor, never silently truncates. | Silent truncation destroys the audit trail. |
| B-3 | No tool mutates the repository or the host. | ATLAS is read-only. Write tools are a different surface. |
| B-4 | Every tool call is logged with `{tool, args, bytes_out, ms}`. | Harness telemetry and post-hoc η calculation. |
| B-5 | Tool schemas are loaded progressively, not all at once. | Tool-definition bloat is a real token tax. |

---

## Primitives

### `view_file(path, start_line, end_line) → {lines, next_cursor?}`

- **Bounds:** `end_line - start_line ≤ 100`. Requests beyond 100 are split
  into pages with `next_cursor`.
- **Errors:** missing file → `NOT_FOUND`; binary file → `BINARY_CONTENT`
  with size metadata, no bytes returned.
- **Never** returns the whole file in one shot.

### `list_dir(path, glob?) → {entries[], next_cursor?}`

- **Bounds:** ≤200 entries per call.
- Entries include `{name, kind: file|dir|symlink, size, mtime}`.
- Respects a project-level ignore list (`.gitignore` + a hardcoded baseline).

### `search_text(pattern, scope, regex=true, limit=50) → {matches[], overflow?}`

- **Bounds:** `limit` hard-capped at 50. Requesting 100 returns 50 plus
  `overflow: true`.
- Match record: `{path, line, col, preview(<=120 chars)}`.
- Regex is a server-side concern; agent should not build pathological
  patterns. Server MAY reject patterns likely to backtrack badly.

### `search_symbol(name, kind?=any) → {definitions[], references[]}`

- Index-backed. No file reads from the agent's perspective.
- `definitions` and `references` each carry `{path, line, col, scope}`.
- If the index is unavailable, returns `INDEX_UNAVAILABLE`. Do **not**
  silently fall back to `search_text` — the caller should decide.

### `graph_query(query) → {nodes[], edges[]}`

- DSL is implementation-specific. Recommended primitives:
  - `callers_of(symbol)`, `callees_of(symbol)`
  - `implementers_of(interface)`, `subclasses_of(class)`
  - `writers_to(table_or_global)`, `readers_of(table_or_global)`
  - `imports(module)`, `imported_by(module)`
- Returns edges with provenance: `derived_from: ast | index | heuristic`.

### `test_dry_run(path, case?) → {stdout, stderr, exit_code}`

- Runs the named test without persisting side effects. Harness responsibility
  to sandbox.
- **Bounds:** wall-clock ≤30s default, stdout ≤8KiB.
- Refuse tests that require mutating external services.

### `memex.read(ref) → bytes`

- Content-addressable. Returns byte-exact content of a previously captured
  excerpt. No summarization at read time.

### `memex.write(content) → ref` *(harness-internal)*

- Agents don't call this directly. The harness writes excerpts when a
  Locate probe cites them, returning a ref the agent embeds in FINDINGs.

---

## MCP mapping (recommended)

Expose the ACI as an MCP server so swapping LLM providers requires only a
client-side adapter. Suggested server layout:

```
mcp-atlas-aci/
  tools/
    view_file
    list_dir
    search_text
    search_symbol
    graph_query
    test_dry_run
  resources/
    memex/<ref>
```

JSON-RPC 2.0 schemas for each tool should include the bound in the tool
description text itself so the model sees the cap when choosing a tool.

---

## Non-primitives (explicit exclusions)

The following are **not** part of the ATLAS ACI. They belong to other agents
(APIVR-Δ, infrastructure tooling, etc.):

- `edit_file`, `write_file`, `append_file`
- `shell.exec` with arbitrary commands
- `git.commit`, `git.push`, `git.merge`
- `migration.apply`, `deploy.run`
- Any network egress beyond read-only resource fetchers

An ATLAS implementation that exposes any of these to the agent context is
non-conformant.

---

## Failure semantics

All tool errors are structured:

```json
{
  "error": "NOT_FOUND | BINARY_CONTENT | OVERFLOW | TIMEOUT | INDEX_UNAVAILABLE | FORBIDDEN",
  "message": "<short>",
  "retry_hint": "narrower_scope | pagination_cursor | different_tool | none"
}
```

Agents should act on `retry_hint`. `none` means escalate or record a GAP.

---

## Conforming implementation

**[Rynaro/atlas-aci](https://github.com/Rynaro/atlas-aci)** is the reference
Python MCP server that implements all primitives in this spec with mechanical
enforcement. Its `enforcement.py` module is the canonical example of how to
apply the invariants above (bounds, read-only guard, path-traversal guard,
logging, rate limiting).
