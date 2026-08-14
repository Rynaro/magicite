# TRAVERSAL MAP — ATLAS Phase T

> Fill in from deterministic retrieval only. No LLM-inferred facts.

**MISSION-ID:** `<mission-id>`
**Generated:** `<ISO-8601>`
**Primary index source:** `<graph_server | prism | tree-sitter | rg>`

---

## MAP-ROOTS — entrypoints within scope

> Enumerate exhaustively. Every entry must be a real file.

### HTTP routes

- `MR-H-1` — `<verb> <path>` → `<Controller#action>` · `config/routes.rb:<line>`
- ...

### Background workers

- `MR-W-1` — `<WorkerClass>` · `app/workers/<file>.rb:<line>` · queue: `<name>`
- ...

### CLI / rake entrypoints

- `MR-C-1` — `<rake task>` · `lib/tasks/<file>.rake:<line>`
- ...

### Public API surface

- `MR-A-1` — `<module::Class>` · `<path>:<line>`
- ...

### Event handlers

- `MR-E-1` — `<handler>` · `<path>:<line>` · trigger: `<event>`
- ...

---

## MAP-MODULES — top-N by structural centrality

> Ranked by deterministic metric (fan-in, fan-out, or graph-server centrality).
> Centrality formula: `<specify>`.

| Rank | Module | Centrality | Path | Notes |
|------|--------|------------|------|-------|
| 1 | `<Module>` | `<score>` | `<path>` | `<1-line>` |
| 2 | ... | ... | ... | ... |

---

## MAP-GRAPH — adjacency (caller → callee) within scope

> AST-derived edges only. Include edge kind: `call | import | inherit | include`.

```
RecordVote#call  →  VotingAuthorizer#authorize_ballot    (call)
RecordVote#call  →  CastVoteRecordRepository#create      (call)
CastVoteRecordRepository  →  ApplicationRepository      (inherit)
...
```

OR, for larger scopes, attach a file:

- `MAP-GRAPH-FILE:` `artifacts/ATLAS/map-graph-<mission-id>.json`

Schema: `{nodes: [{id, kind, path, line}], edges: [{src, dst, kind}]}`.

---

## MAP-HEATMAP — churn + ownership (OPTIONAL, requires git history)

> 90-day window by default.

| Path | Commits (90d) | Primary author | Secondary |
|------|---------------|----------------|-----------|
| `app/flows/vote_casting/record_vote.rb` | 12 | alice | bob |
| ... | ... | ... | ... |

High churn + mission-critical = elevated risk, flag in scout report §5.

---

## MAP-GAPS — regions the index could not parse

> First-class output. Downstream phases treat these as known unknowns.

| Path | Reason | Impact |
|------|--------|--------|
| `<path>` | `<syntactic error | missing grammar | binary | generated>` | `<scope of blind spot>` |

If `MAP-GAPS` intersects `SCOPE.include`, flag and surface to caller before
entering Phase L.

---

## Phase-T telemetry

```
tool_calls:    <int>
tokens_in:     <int>
tokens_out:    <int>
files_indexed: <int>
edges_indexed: <int>
wall_clock_s:  <int>
```

---

## Exit checklist

- [ ] MAP-ROOTS is non-empty and every entry references a real file.
- [ ] MAP-MODULES is ranked by a stated deterministic metric.
- [ ] MAP-GRAPH edges are AST-derived, not LLM-inferred.
- [ ] MAP-GAPS is populated (empty list is fine, but must be present).
- [ ] Phase-T tool-call count ≤ 20% of mission budget.
