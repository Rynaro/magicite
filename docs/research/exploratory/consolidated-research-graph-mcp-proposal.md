# The Synaptic Graph Engine: Consolidated Research and MCP Proposal

**Status:** Research consolidation + design proposal
**Context:** Graphed Skills project — augmenting agent skills with a synaptic-plasticity-inspired graph routing layer
**Date:** 2026-08-10

---

# Part I — Graph Theory: Digest, Augmentation, Consolidation

## I.1 Digest of the source dossier

The source text traces graph theory across three eras:

1. **Origins (1735–1936).** Euler's Königsberg proof — abstraction of geography into nodes/edges, the handshaking lemma, and the Eulerian circuit/path degree conditions — simultaneously founded graph theory and topology (*geometria situs*). Formalization took ~150 years (*Graph Theory 1736–1936*).
2. **Classical algorithms (1950s–2000s).** BFS/DFS as \(O(V+E)\) primitives from which connectivity, bipartiteness, topological sort, and cycle detection derive; Dijkstra (1956/1959, three sets + relaxation, non-negative weights, \(O((V+E)\log V)\) with a binary heap); Bellman-Ford for negative weights; MST (Prim/Kruskal); max-flow/min-cut (Ford-Fulkerson, Edmonds-Karp); PageRank as eigenvector of the link matrix via power iteration. Hardness frontier: TSP, coloring, Hamiltonian cycle, max clique are NP-hard.
3. **Learning era (2016–2026).** GCN (Kipf & Welling, 2016/ICLR 2017) reduced spectral convolutions to the first-order propagation rule \(H^{(l+1)} = \sigma(\hat{D}^{-1/2}\hat{A}\hat{D}^{-1/2}H^{(l)}W^{(l)})\), birthing the message-passing family (GraphSAGE, GAT, R-GCN). GraphRAG (Microsoft Research, 2024) solved vector-RAG's "global sensemaking" failure via LLM entity extraction → knowledge graph → Leiden communities → hierarchical summaries (72–83% comprehensiveness win rates, at high indexing cost). The 2025–2026 frontier: Graph2Text serialization is a known bottleneck; GraphLLM, GraCoRe, and the GraphAlgorithm benchmark show LLMs execute memorized algorithms but reason shallowly over topology; GRASP (ICML 2026) and Mario (CVPR 2026) push agentic, code-solver-augmented, multimodal graph reasoning; surveys taxonomize integration as prompting / augmentation / training / agent-based.

## I.2 Augmentation: what the dossier needs for our purposes

The dossier is strong on history and GNNs but omits four graph-theoretic tools that matter specifically for a *skill-routing engine*:

### A. Spreading activation and Personalized PageRank

The missing bridge between graph theory and memory retrieval. Collins & Loftus (1975) modeled semantic memory as activation spreading from a cue node along weighted edges with decay — this is exactly truncated, weighted BFS, and algebraically equivalent to Personalized PageRank (PPR):

\[ \mathbf{a} = \alpha\, W_{\text{norm}}^{\top}\, \mathbf{a} + (1-\alpha)\,\mathbf{s} \]

where \(\mathbf{s}\) is a seed vector (query similarity) and \(\alpha \approx 0.85\) controls hop decay. PPR is computable locally in \(O(\text{edges explored})\) via the push algorithm — no full-graph linear algebra needed. For a skill graph of \(10^2\)–\(10^4\) nodes this is microseconds. **This is the retrieval primitive the engine will use**: seed by embedding similarity, spread by learned edge weights, rank by converged activation.

### B. Temporal weighted multigraphs

The dossier treats graphs as static. A learning skill index is a *temporal directed weighted multigraph*: multiple typed edges between the same node pair (co-activation, composition, similarity, inhibition), each carrying time-decaying weights. Storage stays adjacency-list (sparse regime, \(E \ll V^2\) — exactly the dossier's table), implemented as three SQLite tables rather than a graph database at our scale.

### C. Community detection as hierarchical routing

Leiden (mentioned only via GraphRAG) deserves first-class status: it partitions the skill graph into well-connected communities in near-linear time. This converts flat routing into **two-level routing** (query → community → skill), which directly counters the empirically observed logarithmic decay of routing accuracy with registry size (Part III, §III.3). It also mirrors GraphRAG's local/global split: community summaries answer "what kind of task is this?", within-community activation answers "which exact skill?".

### D. Centrality as a routing health metric

PageRank/degree centrality over the *usage* graph detects pathological hubs. The 2026 "Scaling Laws of Skills" work documents "black-hole skills" — overly general skills that capture routing. A centrality monitor turns this from a silent failure into an observable, penalizable quantity.

### E. Local-first graph storage landscape

| Option | Model | Fit for our scale |
|---|---|---|
| SQLite adjacency tables | Relational, hand-rolled traversal | **Best fit**: zero deps, single file, transactional, matches crystalium's self-hosted ethos |
| Kuzu | Embedded property graph (Cypher) | Strong alternative if Cypher queries are wanted; single-file, columnar |
| NetworkX + leidenalg | In-memory prototyping | Right tool for the consolidation worker's batch jobs |
| FalkorDB / Neo4j | Server graph DBs | Overkill; violates local-first minimalism at \(10^3\) nodes |

**Key realization:** at skill-registry scale the graph is tiny. The entire value of the engine is in *edge dynamics* (how weights change), not in graph size. This inverts the usual graph-engineering priorities.

## I.3 Consolidation: the three threads as engine layers

The dossier's closing synthesis maps cleanly onto a three-layer engine architecture:

1. **Classical algorithms → deterministic substrate.** BFS-variant spreading activation, PPR, Leiden, topological sort over the composition DAG, centrality monitoring. Cheap, auditable, local — no LLM calls in the hot path.
2. **GNNs → optional representation layer.** Unnecessary at \(10^3\) nodes; revisit only if node features (skill bodies, code) demand learned embeddings beyond what a local embedding model provides. Explicitly deferred.
3. **LLM–graph integration → agentic traversal.** The 2025–2026 finding that text-serialized graphs bottleneck LLM reasoning argues for keeping the graph *outside* the context window: the MCP returns only ranked identifiers and metadata (Graph2Text avoided by construction), and the agent traverses on demand via tool calls (GRASP-style).

---

# Part II — Neuroscience of Knowledge Consolidation: Digest, Augmentation, Consolidation

## II.1 Digest of the source dossier

The attached dossier traces 75 years of memory science as a citation genealogy:

- **1949, Hebb.** Correlated firing strengthens synapses; cell assemblies as the substrate of thought. Not testable until LTP.
- **1966–1973, Lømo & Bliss.** Long-term potentiation: tetanic stimulation produces hours-to-months synaptic strengthening in the hippocampus — the first physiological Hebbian mechanism.
- **1990s, Kandel/Aplysia.** CREB1 (activator) / CREB2 (repressor) gate the short-term → long-term switch; long-term facilitation is synapse-specific and requires *local* protein synthesis.
- **1997, Frey & Morris.** Synaptic tagging and capture (STC): a stimulated synapse sets a transient tag (~1–3 h); plasticity-related proteins (PRPs), supplied by a stronger co-occurring event, are captured by the tag to convert early-LTP into durable late-LTP. Behavioral tagging (Moncada & Viola, 2007/2009) extended this to whole-animal learning: weak experiences paired with salient ones become persistent.
- **2000, Nader et al.** Reconsolidation: retrieval returns a memory to a labile, protein-synthesis-dependent state — memory is rebuilt on every recall, not read out.
- **2009, Yang/Xu/Gan.** Skill learning induces new dendritic spines within an hour; a sparse stable fraction persists for life. Later work (Peters 2017, Albarran 2021) showed spine stabilization is a correlate and a *causal lever* (PirB blockade enhances learning) but not strictly necessary.
- **2012–2015, Tonegawa lab.** Optogenetic engram identification: sparse labeled populations are necessary/sufficient for recall; "silent engrams" prove amnesia is often a *retrieval-access* failure, not trace destruction.
- **2019–2023, Born/Rasch.** Sleep replay drives systems consolidation: hippocampal episodic traces → neocortical gist schemas; synaptic homeostasis (downscaling) balances local strengthening.
- **2020s, testing effect.** Retrieval practice beats restudy; mechanistically resembles a second tagging event; spaced > massed retrieval for late-phase LTP. Bjork's distinction: *storage strength* vs *retrieval strength*.
- **2024–2026 frontier.** Engrams are dynamic (representational drift, engram overlap links temporally close memories), compositionally heterogeneous (sub-ensembles per learning phase), glial-inclusive (astroengrams), architecturally richer than Hebb assumed (multi-synaptic boutons, Uytiepo 2025), and reversible (OSK partial reprogramming restores aged engrams; AMPAR trafficking silences/un-silences them).

## II.2 Augmentation: the computational translation table

Each biological mechanism has a precise, implementable analog in a skill-routing engine. This is the core synthesis of the document:

| Neuroscience mechanism | Engine analog | Concrete rule |
|---|---|---|
| Hebbian co-activation | Edge potentiation between co-used skills | \(\Delta w_{ij} = \eta\, a_i a_j \cdot \text{outcome}\) |
| LTP / LTD | Bidirectional edge weight dynamics | Success potentiates, failure/misfire depotentiates |
| Synaptic tagging & capture | **Two-phase commit for learning** | Use sets a cheap decaying tag (\(\tau \sim\) session-scale); only a verified outcome signal "captures" the tag into durable weight change |
| Behavioral tagging | Retroactive credit assignment | A salient success strengthens *all* skills tagged within the recent window, not just the final one |
| Reconsolidation | Update-on-read | Every retrieval re-opens the edge record: refresh `last_activated`, re-weight by outcome context |
| Testing effect | Retrieval ≠ exposure | Weight updates only fire on actual *application* of a skill, never on listing/scanning it |
| Bjork: storage vs retrieval strength | **Two variables per edge** | Storage strength \(S\): slow-decay, cumulative. Retrieval strength \(R\): fast-decay, context-sensitive. Routing uses \(R\); consolidation uses \(S\) |
| Spacing effect | Diminishing returns on massed updates | Increments within a short window scaled by \(1 - e^{-\Delta t / \tau_s}\) |
| Metaplasticity / LTP saturation | Learning-rate adaptation | \(\eta_{\text{eff}} = \eta (1 - w/w_{\max})\); frequently-updated edges resist further change — prevents runaway feedback loops |
| Engram allocation / excitability | Exploration prior | New/under-used skills get an excitability bonus \(\epsilon\) in routing, decaying with exposure count |
| Engram overlap / memory linking | Session co-occurrence edges | Skills used adjacently in a session get a temporal-link edge (the graph learns workflows) |
| Sleep replay / systems consolidation | **Offline consolidation worker** (crystalium's Dream cycle) | Replay session traces → strengthen observed paths, prune weak edges, re-derive drifted embeddings, distill recurrent activation sequences into candidate composite skills |
| Synaptic homeostasis | Global weight renormalization | Nightly downscaling keeps total weight mass bounded — no skill monopolizes routing |
| Silent engrams (access ≠ storage) | Diagnostic distinction | A skill that never fires is a *retrieval* problem (bad cues/description) not a *storage* problem — the engine flags it for re-description instead of letting it rot |
| Astroengrams | Non-skill context nodes | Projects, tools, error classes, and environment facts are nodes too; they participate in spreading activation and give skills context-conditional retrieval strength |
| Multi-synaptic boutons (Uytiepo 2025) | Typed multi-edges | Node pairs connect via multiple typed channels (co-activation, composition, similarity, inhibition) rather than one fat edge — richer than pure Hebbian single-synapse wiring |
| Representational drift | Embedding refresh | Skill embeddings/summaries are re-derived periodically; the graph topology is the stable substrate, node features drift |
| Engram reprogramming (reversibility) | Weight reset/repair ops | `forget`/`promote`-style admin ops can silence or restore subgraphs without deleting history |

## II.3 Consolidation: the mechanistic chain as a dataflow

The dossier's closing chain — co-activation → LTP → tag-and-capture → CREB-gated consolidation → structural persistence, with retrieval as re-encoding — becomes the engine's event pipeline:

```
skill used            → tag set (cheap, decaying)              [STC]
skills co-used        → candidate edge / edge tag              [Hebb]
task outcome verified → tag captured → durable Δw              [LTP/LTP→late-LTP]
session ends          → episodic trace logged                  [hippocampal trace]
dream cycle (offline) → replay, prune, renormalize, distill    [systems consolidation + homeostasis]
next query            → spreading activation over (S, R)       [retrieval = reconsolidation event]
```

Two design principles fall out:

1. **Never learn from the hot path alone.** Biology separates fast tags from slow capture; the engine separates in-session signals (cheap, reversible) from outcome-gated consolidation (durable). This makes the system robust to noisy single sessions.
2. **Retrieval is a write.** Every route call updates the graph it reads from. A skill index that doesn't change on use is, neuroscientifically, a dead structure.

---

# Part III — LLM Skills: Internals and Harness Mechanics (August 2026)

## III.1 What a skill is, formally

A skill is a directory with a required `SKILL.md` (YAML frontmatter + Markdown body) and optional `scripts/`, `references/`, `assets/`. The agentskills.io open spec — now running Claude, Cursor, Microsoft Agent Framework, and 40+ tools — mandates:

- `name`: 1–64 chars, lowercase-hyphen, must match the directory name.
- `description`: 1–1024 chars, must state what it does *and when to use it* ("Use when…") — this string is the **sole routing signal** at discovery time.
- Optional: `license`, `compatibility` (≤500 chars), arbitrary `metadata`, experimental `allowed-tools`.
- Body conventions: <500 lines / <5k tokens recommended; file references one level deep.

## III.2 Progressive disclosure: the three-level loading contract

| Level | When loaded | Token cost | Content |
|---|---|---|---|
| L1 Metadata | Always (startup, injected into system prompt) | ~30–100 tokens/skill | `name` + `description` |
| L2 Instructions | On trigger | <5k tokens | Full SKILL.md body |
| L3+ Resources | On demand | 0 until accessed | references/, scripts/ (scripts execute via bash; only *output* enters context) |

## III.3 How the harness actually uses skills

Mechanics vary by harness but the contract is identical:

- **Claude (Code/Desktop/API):** L1 metadata is injected into the system prompt at startup. When a request matches a description, Claude reads `SKILL.md` from the filesystem *via bash*, then reads referenced files or runs scripts, with only outputs consuming context.
- **Microsoft Agent Framework:** same two-step — advertise in system prompt, then the agent calls a `load_skill` tool on match.
- **Consequence:** the entire routing decision rests on a ~100-token name+description string, matched by the model's in-context judgment. There is no retrieval index, no usage history, no relational structure in the native mechanism.

## III.4 The 2026 empirical verdict on this design

Three findings define the gap our engine targets:

1. **SkillRouter (arXiv 2603.22455, Mar 2026).** On an ~80K-skill SkillsBench-derived benchmark with 75 expert-verified queries: hiding the skill *body* and routing on metadata alone costs **31–44 percentage points** of routing accuracy across sparse, dense, and reranking baselines; cross-encoder attention concentrates 91.7% on the body field. Their 0.6B retrieve-and-rerank router hits 74.0% Hit@1 and runs on consumer hardware. **Lesson: body content is the decisive routing signal, and a small dedicated router beats in-context LLM judgment.**
2. **Scaling Laws of Skills (May 2026).** Single-step routing accuracy decays **logarithmically with library size** (\(R^2 > 0.97\) across models). Error taxonomy progresses: local skill competition → cross-family drift → capture by overly general "black-hole skills". **Lesson: flat L1 lists degrade predictably; hierarchy and hub-suppression are structural fixes.**
3. **CompSkillBench (Jun 2026).** Real tasks are compositional: 300 queries over 2,209 real MCP skills require decompose → retrieve-per-step → compose-plan. **Lesson: routing must return *plans over a composition DAG*, not single skills.**

## III.5 Adjacent infrastructure: memory MCPs

- **Official Memory MCP:** entities / relations / observations in a JSONL knowledge graph; tools `create_entities`, `create_relations`, `add_observations`, `read_graph`, `search_nodes`, `open_nodes`. Passive storage — no retrieval dynamics.
- **Memento MCP:** adds edge strength (0–1), confidence, temporal decay, version history. Closest existing analog to synaptic weights, but weights are *set*, not *learned from usage*; not skill-aware; no spreading activation.
- **Your ecosystem:** `crystalium` (four-layer lattice + Aetheryte hybrid retrieval + Dream consolidation worker, with lifecycle skills `recall`/`commit`/`forget`/`promote`/`dream-cycle`) already implements the *memory* half of the brain. What it does not route is *skills*. `atomos` (5th sibling MCP, context-lifecycle) and `ariramba` (local-first agentic appliance for GPT-OSS) define the harness side.

## III.6 Gap analysis

| # | Gap in "common" skills | Evidence |
|---|---|---|
| G1 | Routing rests on a 100-token description; body ignored at selection time | SkillRouter: −31–44pp |
| G2 | Flat registry; accuracy decays logarithmically with size; black-hole capture | Scaling Laws of Skills |
| G3 | No compositionality — skills are atomic, tasks aren't | CompSkillBench |
| G4 | No learning — routing is identical on day 1 and day 1000 | Absent from spec |
| G5 | Memory MCPs store facts but don't route capabilities; skill systems route but don't remember | Memory/Memento MCP review |

---

# Part IV — Proposal: **SYNAPSE** — a Synaptic Graph Engine MCP for Skill Routing

*(Working title; rename per Eidolons conventions. Positioned as a sibling MCP alongside crystalium — crystalium remembers what happened; SYNAPSE learns what works.)*

## IV.1 Thesis

Replace the harness's flat, static, description-only skill matching with a **local, learning, graph-structured routing index** that (a) routes on full skill content via embeddings + spreading activation, (b) strengthens and weakens skill associations through Hebbian-style, outcome-gated plasticity, and (c) consolidates offline in a Dream-cycle worker. Progressive disclosure is preserved: the MCP returns only L1 metadata for top-k candidates; the harness still loads L2/L3 on demand.

## IV.2 Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Harness (Claude Code / ariramba / any MCP host)             │
│   hooks: SessionStart · PreToolUse · PostToolUse · Stop     │
└──────────────┬──────────────────────────────────────────────┘
               │ MCP (stdio)
┌──────────────▼──────────────────────────────────────────────┐
│ SYNAPSE MCP server (Python, FastMCP — matches crystalium)   │
│                                                             │
│  Hot path (per query):                                      │
│    embed(query) → seed vector s                             │
│    spreading activation / PPR over typed weighted edges     │
│    → top-k skills (L1 metadata only) + plan expansion       │
│                                                             │
│  Write path (per event):                                    │
│    signal_use → set/decay tags                              │
│    signal_outcome → tag capture → Δw (gated, saturated)     │
│                                                             │
│  Cold path (Dream cycle, offline worker):                   │
│    replay traces → prune (LTD) → renormalize (homeostasis)  │
│    → Leiden communities → distill composite skill drafts    │
│    → refresh drifted embeddings → black-hole audit          │
│                                                             │
│  Storage: SQLite (nodes, edges, events, tags tables)        │
│  Embeddings: Ollama (bge-m3 / nomic-embed-text), local      │
└─────────────────────────────────────────────────────────────┘
```

## IV.3 Data model

**Nodes.** Two classes:
- *Skill nodes*: pointer to skill dir, content hash, embedding of name+description+**body digest** (SkillRouter finding: the body is the signal), community id, exposure count, excitability bonus \(\epsilon\).
- *Context nodes* (astroengram layer): projects, repos, toolchains, error classes, file-type clusters. Cheap to add; they make retrieval context-conditional.

**Edges.** Typed, directed, weighted multi-edges:
- `co_activation` — learned from sessions (the Hebbian backbone)
- `composes` / `depends_on` — declared or distilled composition DAG
- `similar_to` — embedding kNN, refreshed in Dream cycle
- `inhibits` — conflicts/mutual exclusion (lateral inhibition)
- `affinity` — skill ↔ context node links

**Per-edge state:** storage strength \(S\) (slow), retrieval strength \(R\) (fast), `last_activated`, `tag_expires_at`, `update_count` (metaplasticity), success/fail counts, provenance (declared | learned | distilled).

## IV.4 Update rules (the plasticity engine)

1. **Tag on use (cheap):** skill activation sets \(\text{tag}_i = 1\) with expiry \(\tau \approx\) one session. Co-active pairs get tagged candidate edges.
2. **Capture on outcome (durable):** on verified outcome \(o \in [-1, 1]\) (hook signal: tests pass, task completes, user accepts):
   \[ \Delta w_{ij} = \eta\,(1 - w_{ij}/w_{\max})\, a_i a_j\, o \]
   The \((1 - w/w_{\max})\) term is metaplastic saturation — heavily-used edges resist further change, preventing rich-get-richer blowup.
3. **Behavioral tagging:** a high-salience outcome (\(|o| > \theta\)) captures *all* tags still alive in the window — retroactive credit to early-step skills.
4. **Spacing:** repeated activations within \(\Delta t < \tau_s\) get increment scaled by \(1 - e^{-\Delta t/\tau_s}\) — massed use doesn't farm weight.
5. **Dual-timescale decay:** \(R(t) = R_0 e^{-\lambda_R \Delta t}\) (fast, session-days), \(S(t) = S_0 e^{-\lambda_S \Delta t}\) (slow, weeks-months), applied lazily on read + globally in Dream cycle.
6. **Update-on-read (reconsolidation):** every `route` call refreshes `last_activated` and nudges \(R\) for returned nodes — retrieval is a write.
7. **Application-gated learning (testing effect):** `signal_use` fires only when a skill's instructions are actually loaded/applied, never on L1 scanning.

## IV.5 Retrieval (the hot path)

```
route(query, context, k):
  s      = embed(query)                                  # Ollama, local
  seeds  = top-m nodes by cosine(s, node_embedding)      # body-aware seeding
  A      = personalized_pagerank(seeds, W, alpha=0.85,   # spreading activation
                                   max_hops=2)
  score  = β·A + (1-β)·R + ε·excitability                # exploration bonus
  comms  = aggregate score by Leiden community           # hierarchical rerank
  plan   = topo_expand(winner, composes-DAG)             # compositional output
  return [L1 metadata for top-k] + plan + scores
```

- **Hierarchical rerank** counters the log-decay law: route to community first, then within it (GraphRAG local/global split).
- **Black-hole suppression:** nodes with usage-graph PageRank above threshold get a routing penalty and a Dream-cycle audit flag.
- **Plan expansion** answers CompSkillBench: returns the composition subgraph, topologically ordered, not just a single skill.
- **Progressive disclosure preserved:** response carries only L1 metadata (~100 tokens × k). The harness loads L2 via its native bash read for the chosen skill — SYNAPSE never stuffs bodies into context (Graph2Text avoided by construction).

## IV.6 MCP tool surface

| Tool | Class | Purpose |
|---|---|---|
| `route(query, k, context?)` | read+write | Ranked L1 candidates + composition plan; updates R on return |
| `signal_use(skill_ids)` | write | Set tags / candidate edges (PreToolUse hook) |
| `signal_outcome(valence, salience?, skill_ids?)` | write | Tag capture → Δw (PostToolUse/Stop hook) |
| `link(a, b, type, weight)` | write | Declarative edge authoring (composes, inhibits) |
| `introspect(skill)` | read | Neighborhood, weights, history — full auditability |
| `register(path)` / `sync()` | write | Ingest SKILL.md corpus; embed; wire similarity edges |
| `consolidate()` | write | Trigger Dream cycle manually (else cron/systemd) |
| `flag_dead()` | read | Silent-engram report: stored but never retrieved → re-description candidates |

## IV.7 Harness integration

- **SessionStart hook:** inject top-m "hot skills" (highest \(R\)) as a compact priming block — the retrieval-strength analog of what's currently salient, replacing the ever-growing flat list.
- **PreToolUse/PostToolUse hooks:** fire `signal_use` / `signal_outcome`; outcome valence inferred from test results, command exit codes, or explicit user confirmation.
- **Meta-skill fallback:** for harnesses without hooks, ship a `synaptic-routing` skill whose SKILL.md instructs the agent to call `route` before native matching.
- **crystalium interop:** SYNAPSE writes distilled composite-skill drafts and dead-skill reports into crystalium as memories; crystalium's Dream worker can trigger `consolidate()`. Clear division of labor: crystalium = episodic/semantic memory of *facts*; SYNAPSE = procedural memory of *capabilities*.

## IV.8 Evaluation plan

1. **Benchmark:** assemble a routing benchmark from your own registry (queries → ground-truth skills), plus a SkillsBench-derived public slice for comparability; compositional queries in CompSkillBench style.
2. **Baselines:** (a) native description-only matching (status quo), (b) dense embedding retrieval alone, (c) dense + graph without plasticity, (d) full SYNAPSE.
3. **Metrics:** Hit@1/3, MRR, plan F1 for compositional queries; routing accuracy vs registry size (does the log-decay curve flatten?); token cost per routed task.
4. **Ablations:** no-decay, no-tag-capture (learn on hot path), no-communities, no-inhibition. Each maps to a biological mechanism, so ablations double as a validation of the neuroscience analogies.
5. **Hardware:** entirely local — SQLite + Ollama embeddings; CPU-sufficient, RTX 5070 Ti idle. No API calls, no data residency concerns.

## IV.9 Why this clears the "common skills" bar

| Common skills | SYNAPSE-routed skills |
|---|---|
| Static 100-token description routing | Body-aware embedding seeds + learned graph propagation (targets the 31–44pp gap) |
| Flat list, logarithmic decay with scale | Leiden hierarchy + black-hole suppression (structural counter to the scaling law) |
| Atomic skills | Composition DAG with topological plan expansion |
| No memory of what works | Hebbian edges, outcome-gated capture, dual-timescale strength |
| Retrieval is read-only | Retrieval is a write (reconsolidation); index improves with every task |
| Silent skill rot | Silent-engram diagnostics; re-description instead of duplication |
| Opaque selection | `introspect` gives full weight/provenance audit — deterministic, local, explainable |

## IV.10 Risks and honest limits

- **Feedback loops.** Mitigated by metaplastic saturation, homeostatic renormalization, and the excitability exploration bonus — but needs the ablation suite to prove it.
- **Outcome signal quality.** Hooks approximate "success"; noisy valence = noisy plasticity. Start conservative (capture only on explicit/high-confidence signals).
- **Cold start.** New skills route via similarity edges + excitability bonus until usage data accrues; expect ~sessions, not zero-shot parity.
- **Scale ceiling of the analogy.** At \(10^3\) nodes all computation is trivial; the neuroscience buys *dynamics*, not capacity. If the registry stays under ~50 skills, native matching is fine and SYNAPSE is overhead — the engine pays off exactly where the scaling law says native routing breaks.

---

# Sources

- Graph theory dossier: user-provided text (this conversation).
- Neuroscience dossier: attached file `pasted_text_1786415399.txt` (75-year knowledge-consolidation genealogy, Hebb 1949 → astroengrams 2026).
- Skills internals: Claude Platform Docs (Agent Skills overview); claude.com/blog/skills-explained; Microsoft Learn Agent Framework; agentskills.io specification; anthropics/skills DeepWiki.
- Routing evidence: SkillRouter (arXiv 2603.22455); "The Scaling Laws of Skills in LLM Agent Systems" (DeepSignal, May 2026); CompSkillBench (Jun 2026).
- Memory MCP landscape: modelcontextprotocol/memory; Memento MCP; mcp-knowledge-graph.
- Ecosystem fit: github.com/Rynaro/crystalium, /atomos, /tonberry, /ariramba, /SPECTRA, /ATLAS.

