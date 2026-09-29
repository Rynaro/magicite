# Reference MCP Server — ATLAS Bounded ACI

> **Reference implementation available.** The canonical Python MCP server
> conforming to this spec ships at
> **[Rynaro/atlas-aci](https://github.com/Rynaro/atlas-aci)**.
> It includes mechanical enforcement, tree-sitter indexing, ripgrep search,
> SQLite code graph, hashed-directory Memex, and host integrations for
> Claude Code, GitHub Copilot, and Cursor. Use atlas-aci for production.
> This document is the **normative spec**; atlas-aci is the conforming
> implementation.

---

> A minimal, language-agnostic skeleton showing how to expose the ATLAS ACI
> primitives over Model Context Protocol. Drop-in compatible with any
> MCP-aware host (Claude Code, Copilot custom agents, Cursor, LangGraph,
> local OpenAI-SDK agents via the litellm MCP bridge).

This is a **reference design**, not a production implementation. Goal: make
the contract implementable in any language within a few hundred lines.

---

## Design

```
┌──────────────────┐       MCP (JSON-RPC 2.0 over stdio or SSE)
│  LLM / Agent     │ ◀─────────────────────────────────────────┐
└──────────────────┘                                            │
         │                                                       │
         │ tool_call(view_file|search_text|search_symbol|...)    │
         ▼                                                       │
┌──────────────────────────────────────────────────────────┐    │
│ mcp-atlas-aci (this server)                              │    │
│   ┌─────────────────────────────────────────────────┐    │    │
│   │ Enforcement layer (bounds, read-only, logging)  │    │    │
│   └──────────────┬──────────────────────────────────┘    │    │
│                  │                                        │    │
│   ┌──────────────▼──────────────┐   ┌────────────────┐   │    │
│   │ Filesystem primitives       │   │ Code-graph adapter  ──┼────┐
│   │ (view_file, list_dir,       │   │ (search_symbol,      │    │  │
│   │  search_text via ripgrep)   │   │  graph_query)        │    │  │
│   └─────────────────────────────┘   └──────────────────┘   │    │  │
│                  │                                        │    │  │
│   ┌──────────────▼─────────────────────────────────┐     │    │  │
│   │ Memex adapter (sqlite-vec | hashed-dir | KV)   │ ────┼────┼──┼──┐
│   └────────────────────────────────────────────────┘     │    │  │  │
└──────────────────────────────────────────────────────────┘    │  │  │
                                                                 │  │  │
┌─────────────────┐       ┌──────────────────┐       ┌──────────┴──┴──┴─┐
│ ripgrep (binary)│       │ prism-codegraph  │       │  sqlite-vec DB    │
└─────────────────┘       │  (separate MCP)  │       └───────────────────┘
                          └──────────────────┘
```

The enforcement layer is the **only** place bounds are enforced. Language
backends beneath it can be swapped without rewriting the contract.

---

## Tool manifest (MCP `tools/list` response)

```jsonc
{
  "tools": [
    {
      "name": "view_file",
      "description": "Read a window of lines from a file. MAX 100 lines per call. Overflow returns next_cursor.",
      "inputSchema": {
        "type": "object",
        "required": ["path", "start_line", "end_line"],
        "properties": {
          "path": {"type": "string"},
          "start_line": {"type": "integer", "minimum": 1},
          "end_line": {"type": "integer", "minimum": 1, "maximum": 100000}
        }
      }
    },
    {
      "name": "list_dir",
      "description": "List a directory. MAX 200 entries per call.",
      "inputSchema": {
        "type": "object",
        "required": ["path"],
        "properties": {
          "path": {"type": "string"},
          "glob": {"type": "string"}
        }
      }
    },
    {
      "name": "search_text",
      "description": "Ripgrep-backed regex search. MAX 50 matches. Overflow → use search_symbol or narrow scope.",
      "inputSchema": {
        "type": "object",
        "required": ["pattern", "scope"],
        "properties": {
          "pattern": {"type": "string"},
          "scope": {"type": "string", "description": "Path glob"},
          "regex": {"type": "boolean", "default": true},
          "limit": {"type": "integer", "maximum": 50, "default": 50}
        }
      }
    },
    {
      "name": "search_symbol",
      "description": "Index-backed symbol lookup. Returns definitions + references.",
      "inputSchema": {
        "type": "object",
        "required": ["name"],
        "properties": {
          "name": {"type": "string"},
          "kind": {
            "type": "string",
            "enum": ["any", "class", "module", "method", "constant"],
            "default": "any"
          }
        }
      }
    },
    {
      "name": "graph_query",
      "description": "Query the code graph. Implementation-specific DSL.",
      "inputSchema": {
        "type": "object",
        "required": ["query"],
        "properties": {
          "query": {"type": "string"}
        }
      }
    },
    {
      "name": "test_dry_run",
      "description": "Run a test without persisting side effects. Sandboxed.",
      "inputSchema": {
        "type": "object",
        "required": ["path"],
        "properties": {
          "path": {"type": "string"},
          "case": {"type": "string"}
        }
      }
    },
    {
      "name": "memex_read",
      "description": "Byte-exact retrieval of a previously captured excerpt.",
      "inputSchema": {
        "type": "object",
        "required": ["ref"],
        "properties": {
          "ref": {"type": "string", "pattern": "^memex://excerpt/[a-f0-9]+$"}
        }
      }
    }
  ]
}
```

**Explicitly absent** (do NOT add): `edit_file`, `write_file`, `shell_exec`,
`git_commit`, `git_push`, `migration_apply`, `deploy_run`. An ATLAS ACI
server that exposes these is non-conformant.

---

## Enforcement reference (pseudocode)

The enforcement layer is what makes the ACI *mechanical* rather than
advisory. Pseudocode in a Python-ish dialect:

```python
MAX_LINES = 100
MAX_ENTRIES = 200
MAX_MATCHES = 50
MAX_BYTES = 8 * 1024

def view_file(path, start_line, end_line):
    assert_path_in_scope(path)
    if end_line - start_line > MAX_LINES:
        end_line = start_line + MAX_LINES
        overflow = True
    else:
        overflow = False

    lines = read_lines(path, start_line, end_line)
    body = "\n".join(lines)
    if len(body.encode()) > MAX_BYTES:
        # Still too big — file has extremely long lines (minified, etc)
        return {
            "error": "BINARY_CONTENT",
            "message": f"Lines exceed {MAX_BYTES}B; treat as opaque.",
            "retry_hint": "different_tool"
        }

    result = {"lines": lines, "start_line": start_line, "end_line": end_line}
    if overflow:
        result["next_cursor"] = end_line + 1
    log_tool_call("view_file", path=path, bytes_out=len(body), lines=len(lines))
    return result


def search_text(pattern, scope, regex=True, limit=50):
    assert_scope_in_project(scope)
    limit = min(limit, MAX_MATCHES)
    matches = ripgrep(pattern, scope, regex=regex, limit=limit + 1)
    overflow = len(matches) > limit
    matches = matches[:limit]
    # Trim preview to 120 chars
    for m in matches:
        m["preview"] = m["preview"][:120]
    log_tool_call("search_text", pattern=pattern, matches=len(matches),
                  overflow=overflow)
    return {"matches": matches, "overflow": overflow}


def read_only_guard(tool_name):
    # Whitelist the read-only tools. Anything else → FORBIDDEN.
    READ_ONLY = {
        "view_file", "list_dir", "search_text", "search_symbol",
        "graph_query", "test_dry_run", "memex_read",
    }
    if tool_name not in READ_ONLY:
        raise ToolError("FORBIDDEN", f"{tool_name} is not in the ATLAS read-only set.")
```

---

## Memex reference implementation (minimal)

Content-addressable store, backed by a hashed-file directory. No DB
required for a first cut.

```python
import hashlib, pathlib

class Memex:
    def __init__(self, root: pathlib.Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, content: bytes) -> str:
        h = hashlib.sha256(content).hexdigest()
        p = self.root / h[:2] / h[2:4] / h
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_bytes(content)
        return f"memex://excerpt/{h}"

    def read(self, ref: str) -> bytes:
        if not ref.startswith("memex://excerpt/"):
            raise ToolError("NOT_FOUND", "Invalid memex ref.")
        h = ref.removeprefix("memex://excerpt/")
        p = self.root / h[:2] / h[2:4] / h
        if not p.exists():
            raise ToolError("NOT_FOUND", f"Ref {ref} not in this Memex.")
        return p.read_bytes()
```

For production + semantic search over the Memex: swap the backend for
sqlite-vec with BM25 + vector hybrid retrieval. Same public interface.

---

## Wiring into hosts

### GitHub Copilot custom agents

Register this server in your agent's `.github/agents/<agent>.agent.md`
frontmatter:

```yaml
---
name: reggie
tools:
  mcp_servers:
    - name: atlas-aci
      transport: stdio
      command: ["atlas-aci", "--repo", "${workspaceFolder}"]
---
```

### Claude Code

Add to `claude_code_config.json`:

```jsonc
{
  "mcpServers": {
    "atlas-aci": {
      "command": "atlas-aci",
      "args": ["--repo", "."],
      "transport": "stdio"
    }
  }
}
```

### Cursor

Configure via Cursor Settings → MCP Servers → add custom server pointing
to the `atlas-aci` binary.

### LangGraph / local

Use the `mcp` Python client or the `@modelcontextprotocol/sdk` Node client
to connect your agent runtime to the server over stdio.

---

## What this skeleton intentionally omits

- **Auth/ACL.** Production deployments need per-repo access control. This
  skeleton assumes the server runs with the caller's filesystem
  permissions.
- **Rate limiting.** Add per-tool rate limits at the enforcement layer if
  exposing the server to multi-tenant agents.
- **Auditing.** The `log_tool_call` hook is where you'd pipe telemetry
  into your CORTEX JSONL or equivalent.
- **Concurrency.** Multi-agent swarms hitting the same Memex concurrently
  are safe (content-addressable writes are idempotent) but graph queries
  may need connection pooling.
- **Secret scanning.** Files containing secrets should be redacted before
  returning content. Wire in `trufflehog` or `gitleaks` as a
  post-processor.

Each of these is independently implementable and does not affect the
contract agents see.
