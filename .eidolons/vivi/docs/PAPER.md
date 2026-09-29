# Vivi: A Flow-Engineered Methodology for LLM-Based Feature Implementation in Brownfield Codebases

**Version 1.0 — June 2026**

---

## Abstract

Large Language Model (LLM) coding agents have demonstrated remarkable capability on isolated programming tasks, yet consistently underperform on brownfield feature implementation — the dominant mode of professional software engineering. We present Vivi v1.0, a flow-engineered methodology that structures LLM agent behavior through five explicit phases: **A**nalyze, **P**lan, **I**mplement, **V**erify, and **Δ** Delta (with a **R**eflect branch for failure recovery). The methodology synthesizes techniques from five distinct schools of thought in agentic coding — flow engineering, minimal scaffolding, tree search, role-separated multi-agent systems, and context engineering — into a unified, implementable framework.

Rather than relying on runtime orchestration libraries or custom agent harnesses, Vivi operates as a declarative specification: a set of markdown instruction files that any LLM-based coding tool can consume. The methodology introduces four key augmentations over prior work: (1) a complexity router that adapts pipeline depth to task difficulty, drawing on adaptive structuring research; (2) test-anchor generation before implementation, informed by AlphaCodium's flow engineering; (3) an evidence-gated failure recovery protocol with structured classification, based on the AgentDebug taxonomy; and (4) a persistent episodic memory system with consolidation rules, extending the Reflexion framework to multi-session workflows.

This paper documents the design rationale for each component, maps every design decision to its supporting evidence in the research literature, and provides a critical assessment of the methodology's limitations and the open questions it does not resolve.

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [Problem Statement](#2-problem-statement)
3. [Background and Related Work](#3-background-and-related-work)
   - 3.1 [Flow Engineering](#31-flow-engineering)
   - 3.2 [Agent-Computer Interface Design](#32-agent-computer-interface-design)
   - 3.3 [Tree Search and Multi-Path Reasoning](#33-tree-search-and-multi-path-reasoning)
   - 3.4 [Role-Separated Agent Architectures](#34-role-separated-agent-architectures)
   - 3.5 [Context Engineering](#35-context-engineering)
   - 3.6 [Failure Recovery and Self-Debugging](#36-failure-recovery-and-self-debugging)
   - 3.7 [Agent Memory Systems](#37-agent-memory-systems)
4. [Methodology Design](#4-methodology-design)
   - 4.1 [System Architecture](#41-system-architecture)
   - 4.2 [The Complexity Router](#42-the-complexity-router)
   - 4.3 [Phase A — Analyze](#43-phase-a--analyze)
   - 4.4 [Phase P — Plan](#44-phase-p--plan)
   - 4.5 [Phase I — Implement](#45-phase-i--implement)
   - 4.6 [Phase V — Verify](#46-phase-v--verify)
   - 4.7 [Phase R — Reflect](#47-phase-r--reflect)
   - 4.8 [Phase Δ — Delta](#48-phase-δ--delta)
   - 4.9 [Memory Subsystem](#49-memory-subsystem)
5. [Design Rationale: Mapping Decisions to Evidence](#5-design-rationale-mapping-decisions-to-evidence)
6. [Comparison with Existing Systems](#6-comparison-with-existing-systems)
7. [Limitations and Threats to Validity](#7-limitations-and-threats-to-validity)
8. [Future Work](#8-future-work)
9. [Conclusion](#9-conclusion)
10. [References](#10-references)

---

## 1. Introduction

The landscape of LLM-based software engineering has produced an unusual divergence. On standardized benchmarks, autonomous coding agents have achieved striking results: 92.7% pass@1 on HumanEval via Language Agent Tree Search [1], 44% accuracy on CodeContests through flow engineering [2], and above 50% on SWE-bench Verified through various architectures [3, 4]. Yet in professional practice, developers consistently report that AI coding agents struggle with the most common task in software engineering: implementing a new feature in an existing, complex codebase [5, 6].

This divergence has a structural explanation. Benchmarks like HumanEval evaluate isolated function generation with clear specifications and immediate test feedback. Real-world feature implementation requires understanding existing architectural patterns, respecting established conventions, navigating dependency chains, avoiding regressions in untested areas, and making judgment calls about when to reuse versus create. These are fundamentally different cognitive demands.

Vivi addresses this gap by providing a structured methodology specifically designed for brownfield feature implementation — work that takes place in existing, evolving codebases with legacy constraints, partial test coverage, and accumulated architectural decisions. The methodology is not a library, framework, or agent runtime. It is a **declarative specification** expressed as markdown files that any LLM-based coding tool can interpret.

The core insight driving this work is that **the structure of the reasoning pipeline — not the capability of the underlying model — is the primary determinant of quality for complex coding tasks**. This claim is supported by AlphaCodium's finding that pipeline design accounted for approximately 95% of their optimization effort [2], by SWE-Agent's demonstration that interface design matches reasoning algorithms in importance [3], and by the Agentless framework's achievement of competitive SWE-bench scores with a deliberately simple three-phase pipeline [4].

Vivi v1.0 represents the third major iteration of this methodology, informed by a systematic review of the academic literature on LLM-based code generation and agent architectures, analysis of the commercial agent landscape (including leaked and published system prompts from major tools), and community-reported best practices from over 2,500 repositories analyzed by GitHub [7].

---

## 2. Problem Statement

We identify four specific challenges that existing approaches handle poorly in the brownfield context:

**Challenge 1: Context Quality.** LLM agents operating on large codebases must identify relevant existing code from repositories containing thousands of files. Naive approaches (reading entire files, relying on vector similarity search) either exhaust the context window or retrieve semantically similar but functionally irrelevant code. Augment Code's internal analysis has reported that adding high-quality codebase context to generation produces 30–80% quality improvements across tasks [8], making context engineering the highest-leverage intervention available.

**Challenge 2: Asset Ignorance.** Agents default to creating new code rather than discovering and reusing existing patterns. OX Security's experiments demonstrated that AI agents implement features without considering refactoring or reuse opportunities 80–90% of the time [9], leading to progressive integration slowdown. This is the brownfield-specific manifestation of a broader problem: LLMs optimize for the current task, not for long-term codebase health.

**Challenge 3: Unstructured Failure Recovery.** When implementation fails verification, agents typically enter unbounded retry loops — applying the same class of fix repeatedly, or overcorrecting by rewriting large code sections in response to small errors. Stanford's controlled debugging research confirms that LLMs exhibit a strong tendency toward overcorrection, proposing multiple changes for single-line issues [10]. The MAST failure taxonomy identifies step repetition as a dominant failure mode across agent systems [11].

**Challenge 4: Session Amnesia.** Individual coding sessions lose all accumulated knowledge when the context window resets. Patterns discovered, failures debugged, and architectural decisions made in one session are invisible to the next. This forces agents to re-discover the same information repeatedly, wasting computational resources and human attention.

---

## 3. Background and Related Work

### 3.1 Flow Engineering

The term "flow engineering" was introduced by Ridnik et al. (2024) in the AlphaCodium paper [2] to describe the practice of designing multi-stage reasoning pipelines for code generation, where the structure of the pipeline — rather than the wording of individual prompts — is the primary optimization target. Their key result: applying a structured flow of problem reflection, test generation, iterative code refinement, and test-based validation to GPT-4 increased accuracy on CodeContests from 19% to 44%. The authors noted that roughly 95% of their optimization effort was flow engineering, not prompt tuning.

This finding has been substantiated by subsequent work. The Agentless framework (Xia et al., 2024) achieved 32–50% on SWE-bench with a simple three-phase pipeline — localize, repair, validate — at a cost of approximately $0.70 per issue [4], outperforming many more complex autonomous agent systems that used sophisticated tool-use and multi-step reasoning. GitHub's Spec Kit methodology [12] codifies a four-phase gated workflow (Specify → Plan → Tasks → Implement) for their Copilot agent. Thoughtworks' Technology Radar for 2025 elevated "spec-driven development" as a key emerging practice, defining it as a workflow where detailed specifications are created before code generation begins [13].

Vivi is a flow engineering system. Each phase has explicit inputs, outputs, and transition conditions. The methodology's contribution is not the general concept of phased pipelines — which is now well-established — but the specific phase definitions, their internal structure, and the inter-phase protocols designed for brownfield work.

### 3.2 Agent-Computer Interface Design

Yang et al. (2024) introduced the concept of the Agent-Computer Interface (ACI) in the SWE-Agent paper [3], arguing that the design of tools available to an LLM agent matters as much as the agent's reasoning architecture. SWE-Agent achieved strong SWE-bench performance through purpose-built commands for file navigation, viewing, and editing that were optimized for LLM comprehension rather than human usability.

The ACI concept has been validated by two subsequent developments. Claude Code's architecture [14] embodies a deliberately minimal approach: a single-threaded master loop (`while(tool_call) → execute → feed results → repeat`) with regex-based code search (GrepTool) rather than vector databases. Anthropic's engineering team chose regex over embeddings because they found that "Claude understands code structure well enough to craft sophisticated regex" [14]. Perhaps most strikingly, Mini-SWE-Agent — just 100 lines of Python using only bash commands as tools — achieves above 74% on SWE-bench Verified [15], demonstrating that as models improve, simpler scaffolding suffices.

Vivi takes a position between the minimal scaffolding school and the flow engineering school. The methodology itself provides the structured reasoning pipeline, while leaving the tool interface to whatever coding agent executes it. This separation of concerns — methodology as specification, tools as implementation — allows Vivi to be consumed by Copilot, Claude Code, Cursor, Windsurf, or any future tool without modification.

### 3.3 Tree Search and Multi-Path Reasoning

Yao et al. (2023) demonstrated with Tree of Thoughts (ToT) [16] that allowing LLMs to explore multiple reasoning paths and evaluate intermediate states dramatically improves performance on planning tasks. On the Game of 24 puzzle, GPT-4 with standard chain-of-thought prompting solved only 4% of cases, while ToT achieved 74%.

Zhou et al. (2024) extended this approach to code generation with Language Agent Tree Search (LATS) [1], combining Monte Carlo Tree Search with LLM-generated value estimates and environmental feedback (test execution results). LATS achieved 92.7% pass@1 on HumanEval — the highest score reported at the time of publication — while requiring 3.55 fewer search nodes than RAP and 12.12 fewer than ToT for comparable tasks. The critical insight: incorporating external feedback (test results) into the search process provides objective ground truth that dramatically improves state evaluation compared to pure self-evaluation.

Islam et al. (2024) applied multi-agent decomposition to the problem with MapCoder [17], using four specialized agents (Retrieval, Planning, Coding, Debugging) to achieve 93.9% on HumanEval.

However, there are important caveats about applying tree search to brownfield engineering. HumanEval and CodeContests consist of self-contained functions with clear specifications — ideal conditions for search. Real brownfield tasks involve ambiguous requirements, complex dependency chains, and integration concerns that make the search space far less tractable. Furthermore, tree search requires multiple LLM calls per node. AlphaCodium's iterative-but-linear flow achieves comparable gains at dramatically lower computational cost [2].

Vivi incorporates the key insight from tree search (generate and evaluate multiple solution strategies) in the Plan phase without the full computational overhead. The methodology requires 3–5 genuinely different strategies, scored across four dimensions, with the top 2 expanded before selection. This captures the exploration benefit while remaining feasible within a single LLM context window.

### 3.4 Role-Separated Agent Architectures

Gauthier (2024) demonstrated with Aider's architect/editor pattern [18] that separating code reasoning from code editing produces better results than asking a single model to do both. A reasoning model (such as o1-preview or Claude) describes the solution in natural language, then an editor model (such as DeepSeek) translates the description into properly formatted code edits. This approach achieved 85% on Aider's code editing benchmark. The practical motivation: frontier reasoning models like o1 are strong at architectural thinking but often produce malformed diffs, while smaller models reliably format edits but lack strategic reasoning.

Amazon Q Developer implements a similar separation with five specialized agents for different SDLC phases (`/dev`, `/transform`, `/doc`, `/review`, `/test`), each mastering a specific task type [19]. Devin uses a reinforcement-learning-trained planning subagent that operates in read-only mode during the planning phase, achieving approximately 2× speedup in planning while improving file-selection accuracy [20]. Windsurf Cascade runs a dedicated planning agent continuously in the background while a separate action model handles execution [21].

Vivi adopts the architect/editor separation for complex-tier tasks (4+ files, cross-domain changes). The methodology specifies an explicit "architect pass" (describe what changes and why) followed by an "editor pass" (translate to minimal, targeted diffs), though both passes may be executed by the same model instance. This separation functions as a cognitive scaffold even within a single model context.

### 3.5 Context Engineering

The emerging field of context engineering — the systematic design of what information reaches an LLM and when — has been identified as a primary lever for coding agent performance.

Aider's repository map [22] uses tree-sitter to parse Abstract Syntax Trees across all files in a project, then applies a PageRank-like algorithm to rank symbols by how often they are referenced from other files. This structural summary fits an entire repository's architecture into approximately 1,000 tokens, providing agents with a navigation map before they read any file in detail. RepoUnderstander (Deng et al., 2024) extends this concept using Monte Carlo Tree Search to systematically explore repository structures [23].

Packer et al. (2023) introduced MemGPT [24], which applies operating system memory management concepts to LLM context: a "core memory" (analogous to RAM) holds the most relevant information, while "archival memory" (analogous to disk) stores long-term knowledge that can be paged in on demand.

Research from JetBrains (2025) on context management for software engineering agents [25] found a counterintuitive result: simple observation masking — keeping only the most recent 10 turns of agent interaction — often outperforms more sophisticated LLM-based summarization approaches, which cause agents to run approximately 15% more steps. This suggests that aggressive context pruning may be preferable to lossy compression.

Spotify's engineering team published their findings on context engineering for background coding agents [26], identifying context window amnesia — agents forgetting earlier decisions after filling context with implementation details — as a primary failure mode.

Anthropic's published guide on context engineering for AI agents [27] recommended structured "progress documents" at session boundaries, enabling new sessions to resume work without replaying the full conversation history.

Vivi incorporates these findings through three mechanisms: (1) a repo map generation step in the Analyze phase, inspired by Aider's tree-sitter approach; (2) a progressive disclosure protocol that reads directory structure before file interfaces before full implementations; and (3) a context budget management system with explicit token allocation targets per phase.

### 3.6 Failure Recovery and Self-Debugging

The research on LLM self-debugging reveals both promise and significant hazards.

Gulati (2025) at Stanford [10] found that presenting one failing test at a time and requesting minimal fixes outperforms presenting multiple failures simultaneously. This mirrors how human teaching assistants guide students through debugging: targeted, incremental, one issue at a time. The study confirmed a fundamental LLM tendency toward overcorrection — proposing sweeping changes for localized errors.

Zhong et al. (2024) developed LDB (Large Language Model Debugger) [28], which segments programs into basic blocks, traces intermediate variable values during execution, and has the LLM verify correctness block by block. LDB achieved 98.2% accuracy on HumanEval with GPT-4o, demonstrating that fine-grained execution tracing dramatically improves debugging accuracy.

Su et al. (2025) proposed learn-by-interact [29], where agents collect successful interaction trajectories from past resolutions and integrate them as in-context examples for future tasks. This approach systematically improves bug resolution rates as experiential knowledge accumulates.

The most dangerous failure pattern in practice is the infinite retry loop. The MAST taxonomy [11] identifies step repetition as a dominant failure mode across agent systems. Community practitioners report this consistently — agents attempting the same failed approach repeatedly, or alternating between two states where each fix introduces the error the previous fix resolved. Anthropic's engineering guidance on long-running agents [30] highlights premature completion (declaring success without end-to-end verification) as an equally critical failure mode.

Vivi's Reflect phase synthesizes these findings into a three-component protocol: (1) an evidence gate that prevents fix attempts without concrete error artifacts, (2) a failure classification taxonomy adapted from AgentDebug that categorizes errors into actionable types, and (3) a hard retry cap (three attempts maximum for the same failure category) with structured escalation.

### 3.7 Agent Memory Systems

Shinn et al. (2023) introduced Reflexion [31], a framework where LLM agents store verbal summaries of past failures in a persistent buffer accessible during future attempts. This episodic self-reflection achieved 91% on HumanEval versus GPT-4's 80% baseline. The key mechanism is the separation of error identification from solution generation: one LLM call analyzes what went wrong and why; a separate call generates the fix informed by that analysis.

However, subsequent research has revealed limits. Xiong et al. (2025) demonstrated that unbounded memory retention causes error proliferation — indiscriminate storage degrades agent robustness under distribution shifts [32]. Effective memory systems require consolidation: prioritizing recent information, merging related entries, and pruning stale knowledge.

Windsurf Cascade's persistent memory system [21] — which autonomously stores user preferences, code patterns, and architectural decisions across sessions — represents the most ambitious commercial implementation. Community reports indicate that it sometimes retains outdated patterns after major refactors, validating the concern about stale memory.

Vivi implements structured episodic memory with explicit consolidation rules: size caps per memory file, deduplication by root cause for failures, recency-weighted retention, and automatic archival of entries referencing deleted code. The memory architecture separates concerns across five files (task log, pattern registry, failure catalog, delta history, session handoff), each with its own schema and lifecycle.

---

## 4. Methodology Design

### 4.1 System Architecture

Vivi is structured as a modular document system designed for minimal context window consumption:

```
agents/
├── AGENTS.md              ← Entry point (~965 tokens, always loaded)
├── skills/                ← On-demand per phase (~1,900–2,850 tokens each)
│   ├── vivi-methodology.md
│   ├── context-engineering.md
│   ├── failure-recovery.md
│   └── memory-management.md
├── templates/             ← Structured output formats (~300–530 tokens each)
│   ├── discovery-report.md
│   ├── execution-plan.md
│   └── reflect-entry.md
└── memories/              ← Persistent cross-session state
    ├── task-log.md
    ├── pattern-registry.md
    ├── failure-catalog.md
    ├── delta-history.md
    └── session-handoff.md
```

The architecture reflects a deliberate design trade-off informed by JetBrains' finding [25] that context quality outweighs context quantity. The entry point (AGENTS.md) loads in under 1,000 tokens. Skills are loaded on-demand when the agent transitions to the relevant phase. A typical working set — the entry point, one skill, one template, and memory recall — consumes approximately 4,350 tokens, leaving the vast majority of a 128K context window available for actual code, test output, and conversation.

This is a deliberate departure from the "single large system prompt" pattern common in commercial agents. Analysis of leaked and published system prompts from Cursor [33], Devin [20], and Claude Code [14] reveals system prompts ranging from 5,000 to 15,000+ tokens. Vivi's on-demand loading ensures that only phase-relevant instructions occupy the context window at any given time.

### 4.2 The Complexity Router

Vivi introduces an explicit complexity classification step before the pipeline begins, inspired by the Intention Chain-of-Thought (ICoT) research [34] demonstrating that adaptive structuring — using simple prompts for easy tasks and structured chain-of-thought for hard ones — outperforms uniform application of either approach.

The router classifies incoming tasks into four tiers:

**Trivial** (single file, under 20 lines, no cross-module dependencies): Skip directly to Implement → Verify. The full planning pipeline would be pure overhead.

**Standard** (1–3 files, known patterns, clear scope): Execute the full Vivi cycle with a minimum of 3 strategies in the Plan phase.

**Complex** (4+ files, cross-domain changes, architectural decisions): Full Vivi plus test anchoring, architect/editor separation, and enhanced context engineering.

**Uncertain** (ambiguous requirements, unknown codebase areas): Escalate for human clarification before entering the Analyze phase. This tier exists because the methodology cannot produce reliable plans from ambiguous inputs — a lesson from the finding that agent failure rates increase sharply when specifications are underspecified [12].

The router prevents the "over-engineering simple tasks" anti-pattern identified in community discourse. When a developer asks an agent to rename a variable, running a full discovery report and five-strategy evaluation is counterproductive. Conversely, when a developer asks for a cross-cutting feature touching the authentication, billing, and notification systems, skipping analysis is reckless.

### 4.3 Phase A — Analyze

The Analyze phase produces a Discovery Report: a structured assessment of the existing codebase, requirements, and risk landscape before any solution design begins.

**Memory Recall.** The agent queries its persistent memory files for past work in the same module, known reusable assets, and previous failure patterns. This step ensures that knowledge accumulated in prior sessions informs current decisions. Matches are scored by path proximity (same directory > same domain > different domain) and recency (recent entries weighted higher).

**Repo Map Generation.** Adapted from Aider's tree-sitter approach [22], the agent constructs a compressed structural overview of the target domain. The procedure: (1) list the directory tree 2–3 levels deep; (2) extract public interfaces (method signatures, exported functions) from key files; (3) identify dependency relationships; (4) rank files by reference frequency. The output fits in approximately 500–1,000 tokens and serves as the agent's navigation map for the remainder of the task.

**Hierarchical Localization.** Inspired by the Agentless framework [4], the agent narrows from domain to file to symbol through progressive refinement rather than flat search. This four-level protocol (domain identification → file identification → symbol identification → context gathering) ensures that expensive operations (reading full file contents) occur only for confirmed targets.

**Asset Discovery.** The agent searches for existing code that could serve the new feature. For each discovered asset, the agent records location, purpose, relevance, quality (test coverage, recency), and a verdict: USE (as-is), EXTEND (add capability), WRAP (adapter for legacy interface), or AVOID (deprecated, untested, incompatible). This vocabulary is drawn from the Internal First principle — the methodology's strongest directive — which mandates that the agent prove no suitable internal asset exists before creating new code.

**Collision Mapping.** The agent identifies files that will be modified (risk of regression), files that will be created (risk of collision with in-flight work), and high-risk zones (low test coverage, heavily imported, recently changed by other contributors).

### 4.4 Phase P — Plan

The Plan phase produces an Execution Plan: a scored, justified strategy selection with explicit implementation steps and abort conditions.

**Test Anchor Generation.** Before designing any solution, the agent generates expected test cases for each acceptance criterion. This technique — writing test expectations before implementation — is the most impactful single practice from the AlphaCodium research [2]. It transforms the agent's objective from "write code that looks correct" to "write code that passes these specific tests," providing concrete, verifiable targets that resist hallucination.

**Strategy Generation.** The agent generates 3–5 genuinely different approaches, varying in architecture, coupling, scope, or risk profile. Requirements enforce diversity: at least one strategy must maximize use of discovered internal assets; at least one must be the conservative minimal-change approach; none may be strawmen. This is a bounded application of the tree search principle — exploring the solution space at the strategy level rather than the implementation level, which avoids the computational cost of full MCTS while capturing the exploration benefit demonstrated by LATS [1].

**Strategy Scoring.** Each strategy is scored on four dimensions (1–3 scale): Risk (blast radius and existing test coverage), Effort (implementation time and coordination), Alignment (compliance with the Internal First principle), and Maintainability (impact on long-term codebase health). Total scores range from 4 to 12.

**Deep Evaluation.** The top 2 strategies are expanded with detailed step-by-step implementation plans, specific file changes, dependency chains, and abort conditions. This expansion often reveals hidden issues that change the ranking — a common outcome documented by Addy Osmani in his work on specification-driven development [12].

**Selection with Justification.** The agent documents the selected strategy with its score, the runner-up with the reason for rejection, an explicit confidence level (HIGH/MED/LOW), and abort conditions that would trigger re-planning. This documentation serves dual purposes: it enables human review of the decision, and it provides context for the Reflect phase if the strategy fails.

### 4.5 Phase I — Implement

The Implement phase executes the selected plan, following the priority order USE → EXTEND → WRAP → CREATE.

**Architect/Editor Separation.** For Complex-tier tasks, the methodology specifies an explicit two-pass approach inspired by Aider's dual-model pattern [18]. In the architect pass, the agent reasons about what needs to change and why, specifying interface contracts and data flow. In the editor pass, the agent translates this reasoning into minimal, targeted code edits that follow existing code style and conventions. This separation addresses the failure mode where agents reason correctly but produce malformed or overly broad edits.

**Targeted Test Execution.** Rather than running the entire test suite after each change, the agent runs the single most relevant test, fixes that failure, then proceeds. This incremental approach is informed by Gulati's Stanford research [10] demonstrating that presenting one failure at a time produces better fixes than presenting multiple failures simultaneously. It directly counteracts the overcorrection cascade, where fixing one test introduces failures in others.

**Progress Tracking.** The agent maintains a structured task list (inspired by Claude Code's TodoWrite pattern [14]) with task IDs, status, and dependencies. This checklist is re-injected into context after each tool use cycle to prevent the context amnesia identified by Spotify's engineering team [26].

### 4.6 Phase V — Verify

The Verify phase runs all quality checks and captures their output as structured evidence: linter (zero new violations), new tests (all pass), regression suite (no new failures), coverage check (no decrease in affected files), and build (clean). The phase produces a binary decision: all pass → proceed to Delta; any failure → proceed to Reflect.

### 4.7 Phase R — Reflect

The Reflect phase is Vivi's primary failure recovery mechanism. It is entered only when the Verify phase produces failures.

**Evidence Gate.** The first and most important step is a mandatory check for concrete artifacts: test failure output with assertion details, lint errors with file:line, build errors with stack traces, or runtime errors with tracebacks. If no concrete evidence exists, the agent escalates immediately. This gate prevents the speculative fix attempts that are the primary source of agent-induced regressions.

**Failure Classification.** Each failure is classified into exactly one category from a nine-type taxonomy (TEST_ASSERTION, REGRESSION, BUILD_ERROR, TYPE_ERROR, LINT_VIOLATION, RUNTIME_ERROR, LOGIC_ERROR, INTEGRATION_ERROR, ENVIRONMENT_ERROR). The taxonomy is adapted from the AgentDebug research, which demonstrated that fine-grained error typing and identification of the earliest critical error produces 24% higher fix accuracy than surface-level error matching [29].

**Targeted Fix Protocol.** The agent generates exactly one hypothesis per failure, specifying the exact change, location, rationale, confidence level, and risk assessment. The fix must be minimal — addressing only the identified root cause, not "improving" surrounding code. This directly addresses the overcorrection tendency documented in [10].

**Retry Decision Matrix.** The agent follows a strict escalation ladder: first failure with HIGH or MED confidence → apply targeted fix; second failure of the same category → must use a fundamentally different approach; third failure of the same category → escalate. Any failure with LOW confidence → escalate immediately. This hard cap prevents the infinite retry loops identified by the MAST taxonomy [11] as the dominant failure mode in agent systems.

**Loop Detection.** The agent monitors for repetition signals: making the same type of change three times, encountering identical error messages, undoing previous changes, or alternating between two states. Upon detection, the agent stops, summarizes the loop pattern, and either returns to the Plan phase or escalates.

### 4.8 Phase Δ — Delta

The Delta phase is entered only after successful verification. The agent evaluates the touched code and its surroundings for normalization opportunities — patterns that could be improved but are not part of the current task's scope.

Candidates are scored as `Priority = (Severity + Frequency + Velocity) - Cost`, each factor on a 1–3 scale. Only candidates scoring 3 or above are reported. Anti-criteria reject premature abstractions (first occurrence of a pattern), changes to dormant code (unchanged for 6+ months), high-cost changes affecting few files, and speculative improvements.

The Delta phase's output is strictly advisory. The methodology explicitly prohibits the agent from implementing Delta suggestions. They are logged to memory for potential inclusion in future tasks. This restriction exists because unsolicited refactoring by agents is among the most common sources of unintended regressions reported in community discourse [5, 6].

### 4.9 Memory Subsystem

The memory subsystem consists of five files, each with a defined schema, size cap, and consolidation strategy:

**Task Log** (cap: 30 entries) records completed tasks with outcomes, key decisions, discovered assets, and lessons learned. Old entries are consolidated by domain into summary entries that preserve patterns while freeing space.

**Pattern Registry** (no hard cap; pruned for staleness) records discovered reusable assets and architectural patterns with locations, types, and quality assessments. Entries referencing deleted or deprecated code are archived during consolidation.

**Failure Catalog** (cap: 30 entries) records root causes and prevention strategies from past failures. Entries are deduplicated by root cause pattern — multiple failures with the same underlying cause are merged into a single entry with a frequency count.

**Delta History** (cap: 20 entries) records normalization suggestions from the Delta phase with scores and status (OPEN, IMPLEMENTED, REJECTED, SUPERSEDED).

**Session Handoff** (1 entry, overwritten) captures the state of incomplete work at session boundaries, enabling new sessions to resume without full context replay. This is directly informed by Anthropic's recommendation for long-running agents [27, 30].

The consolidation protocol is designed to prevent the error proliferation that Xiong et al. (2025) [32] demonstrated occurs with unbounded memory retention. Recency is explicitly weighted: recent entries are always retained over older ones with equivalent information content.

---

## 5. Design Rationale: Mapping Decisions to Evidence

Each significant design decision in Vivi v1.0 can be traced to specific evidence in the literature. The following table provides this mapping.

| Design Decision | Supporting Evidence | Alternative Considered | Reason for Rejection |
|---|---|---|---|
| Multi-phase pipeline structure | AlphaCodium: 19% → 44% accuracy via flow engineering [2]; Agentless: competitive SWE-bench at $0.70/issue with 3-phase pipeline [4] | Single-shot generation; unconstrained agent loop | Single-shot fails on complex tasks; unconstrained loops produce infinite retries [11] |
| On-demand skill loading | JetBrains: simple observation masking outperforms summarization [25]; Spotify: context amnesia as primary failure mode [26] | Single monolithic system prompt; all instructions always loaded | Monolithic prompts waste context budget on phase-irrelevant instructions |
| Complexity router | ICoT: adaptive structuring outperforms uniform approaches [34] | Uniform pipeline for all tasks | Over-engineers trivial tasks, wasting tokens and developer time |
| Repo map before file reading | Aider repo map: ~1K tokens for full project structure [22]; RepoUnderstander: MCTS for repo exploration [23] | Read all files; vector similarity search | Reading all files exhausts context; vector search flattens code structure |
| Test anchor generation before implementation | AlphaCodium: test-first flow as core of their pipeline [2] | Tests written after implementation | Post-hoc tests validate what was built rather than what was specified |
| 3–5 strategy generation with scoring | LATS: multi-path exploration with evaluation [1]; ToT: 4% → 74% via tree search [16] | Single strategy; unconstrained branching | Single strategy misses better alternatives; unbounded search is too expensive |
| Internal First principle (USE → EXTEND → WRAP → CREATE) | OX Security: 80–90% of agent implementations ignore reuse [9] | Allow free creation; suggest reuse as optional | Without a mandatory check, agents default to creation |
| Architect/editor separation | Aider: 85% on code editing benchmark with dual-model pattern [18] | Single-pass reasoning and editing | Reasoning models often produce malformed edits; separation improves both |
| Evidence gate before fix attempts | Stanford: LLMs overcorrect without targeted evidence [10] | Allow speculative fixes | Speculative fixes introduce more bugs than they resolve |
| Failure classification taxonomy | AgentDebug: 24% higher accuracy with fine-grained error typing [29] | Unstructured "try to fix it" | Untyped fixes lead to wrong fix category (e.g., treating a type error as a logic error) |
| Hard retry cap (3 attempts) | MAST taxonomy: step repetition as dominant failure [11]; Gulati: diminishing returns after 2 retries [10] | Unlimited retries; fixed iteration count | Unlimited retries waste tokens; the 3-attempt threshold captures the vast majority of recoverable failures while limiting damage from non-recoverable ones |
| Structured escalation format | Anthropic: long-running agent failure modes [30] | Unstructured "I'm stuck" | Structured escalation enables efficient human intervention |
| Episodic memory with consolidation | Reflexion: 91% vs 80% baseline with verbal self-reflection [31]; Xiong et al.: unbounded memory degrades performance [32] | No persistent memory; unbounded memory | No memory forces re-discovery; unbounded memory causes error proliferation |
| Session handoff protocol | Anthropic: artifact-based session handoffs for long-running agents [27, 30] | Rely on conversation history; full context replay | Conversation history may be truncated; full replay is token-expensive |
| Delta phase as output-only | Community discourse: unsolicited refactoring causes regressions [5, 6] | Allow agent to implement improvements | Mixing improvement with feature work introduces unscoped risk |
| Progressive disclosure for context | Agentless: hierarchical localization [4]; Augment: 30–80% quality gain from context quality [8] | Front-load all relevant context | Front-loading exhausts context budget; progressive narrowing preserves it |

---

## 6. Comparison with Existing Systems

The following comparison positions Vivi against the most relevant existing methodologies and tools. We compare structural features only; we do not claim benchmark superiority, as Vivi has not been evaluated on standardized benchmarks.

| Feature | Vivi v1.0 | AlphaCodium [2] | Agentless [4] | SWE-Agent [3] | Claude Code [14] | GitHub Spec Kit [12] | MapCoder [17] |
|---|---|---|---|---|---|---|---|
| **Designed for** | Brownfield features | Contest problems | Bug fixing | Bug fixing | General coding | Feature implementation | Contest problems |
| **Phase structure** | 5 phases + reflect | 2 phases (pre-process + iterate) | 3 phases (localize + repair + validate) | Agent loop | Agent loop | 4 phases (spec + plan + tasks + implement) | 4 agents (retrieve + plan + code + debug) |
| **Complexity routing** | Yes (4 tiers) | No | No | No | No | No | No |
| **Asset discovery** | Mandatory phase | N/A (isolated) | File localization only | Tool-based search | Grep/glob search | Not specified | Retrieval agent |
| **Multi-strategy planning** | 3–5 scored strategies | Iterative refinement | Single repair strategy | N/A (reactive) | N/A (reactive) | Task decomposition | Single plan |
| **Test anchoring** | Before implementation | Core to flow | After repair | N/A | N/A | Not specified | After coding |
| **Failure classification** | 9-type taxonomy | N/A | N/A | N/A | N/A | N/A | Debug agent (unstructured) |
| **Retry cap** | Hard cap (3 attempts) | Iterative (no explicit cap) | Not specified | Step limit | Step limit | Not specified | Debug loop |
| **Persistent memory** | 5 structured files | None | None | None | TodoWrite (in-session) | None | None |
| **Escalation protocol** | Structured format | N/A | N/A | N/A | No formal protocol | Not specified | N/A |
| **Implementation format** | Declarative markdown | Python library | Python framework | Python framework | Built-in tool | GitHub Actions + prompts | Python library |
| **Tool dependency** | None (tool-agnostic) | Requires AlphaCodium runtime | Requires Agentless framework | Requires SWE-Agent harness | Claude Code CLI | GitHub Copilot | Requires MapCoder framework |

The primary differentiator of Vivi is its combination of brownfield-specific design (asset discovery, internal-first principle, collision mapping) with tool-agnostic implementation (declarative markdown, no runtime dependency). Most comparable systems are either tool-specific (Claude Code, Spec Kit) or domain-specific (AlphaCodium for contests, Agentless for bugs).

The absence of persistent memory in all compared systems except Vivi is notable. While Claude Code maintains in-session task tracking, no widely-used system provides the cross-session episodic memory that Vivi implements. This may represent either a genuine advantage or an unnecessary complexity — the question is addressed in Section 7.

---

## 7. Limitations and Threats to Validity

We identify the following limitations and unresolved questions.

### 7.1 No Benchmark Evaluation

Vivi has not been evaluated on SWE-bench, HumanEval, or any other standardized benchmark. The design decisions are grounded in published research, but the specific combination of techniques into a unified methodology has not been independently validated. This is the most significant limitation of this work.

The absence of benchmark evaluation is partly by design: Vivi targets brownfield feature implementation, which no existing benchmark adequately represents. SWE-bench evaluates bug fixes, not feature additions. HumanEval evaluates isolated function generation, not multi-file changes in existing codebases. A benchmark specifically designed for brownfield feature implementation would be required for rigorous evaluation, and no such benchmark currently exists.

### 7.2 Methodology as Prompt vs. Methodology as Code

Vivi relies on the LLM interpreting markdown instructions and following multi-step protocols without programmatic enforcement. There is no guarantee that an agent will execute the phases in order, respect the evidence gate, or adhere to the retry cap. The methodology is a specification that depends on the model's instruction-following capability for enforcement.

This is a fundamental trade-off: declarative markdown is universally consumable across tools, but lacks the runtime enforcement that a programmatic harness (like Agentless or SWE-Agent) provides. As model instruction-following improves, this trade-off becomes more favorable. As of early 2026, frontier models (Claude, GPT-4, Gemini) follow multi-step instructions with sufficient reliability for professional use, though not with perfect compliance.

### 7.3 Memory System Scalability and Staleness

The persistent memory system introduces risks not present in stateless agents. Memory entries may become stale after major refactors, leading the agent to recommend or rely on assets that no longer exist. The consolidation protocol mitigates this (entries referencing deleted files are archived), but cannot detect semantic staleness — an asset that still exists but has changed its behavior.

Furthermore, the memory system's effectiveness is proportional to usage duration. A team that deploys Vivi today will see no memory benefit for weeks or months. This creates a cold-start problem that may discourage adoption before the benefits materialize.

### 7.4 Overhead for Small Tasks

Despite the complexity router, the methodology imposes cognitive overhead on agents even for standard tasks. Generating 3+ strategies, scoring them, expanding the top 2, and documenting the selection consumes tokens and time that may not be justified for routine feature work. The router's classification boundaries (what constitutes "trivial" vs. "standard") are heuristic and may need tuning per team and codebase.

### 7.5 Ruby/Rails Bias in Examples

The asset discovery checklist references Ruby on Rails-style directory structures (`app/models/DOMAIN/`, `app/components/`, etc.). While the methodology is framework-agnostic in principle, the default examples and search patterns reflect a specific technology stack. Teams using other frameworks will need to customize these paths, which may reduce the "drop-in" benefit of the system.

### 7.6 Single-Agent Limitation

Vivi is designed for a single agent context (one LLM instance processing one task). It does not address multi-agent coordination, parallel task execution, or the division of work across multiple agent instances. This is an intentional scope limitation — multi-agent coordination introduces failure modes (handoff errors, conflicting edits, divergent strategies) that are outside the brownfield feature implementation focus.

### 7.7 The Spec Drift Problem

Thoughtworks' analysis of spec-driven development [13] raises an unresolved question: are specifications "disposable process intermediates" or "the ultimate truth about software behavior"? Vivi generates specifications (Discovery Reports, Execution Plans) during the process but does not mandate their maintenance after the task completes. If these artifacts are not kept in sync with code, they become misleading. The methodology currently treats them as ephemeral, which avoids the maintenance burden but sacrifices their long-term documentary value.

---

## 8. Future Work

Several directions are apparent.

**Benchmark development.** Creating a benchmark specifically for brownfield feature implementation would enable rigorous comparison of methodologies. Such a benchmark would need to include realistic codebases with established patterns, varying test coverage, and feature requests that require discovering and reusing existing code.

**Empirical evaluation.** Controlled studies comparing agent performance with and without Vivi instructions on real feature implementation tasks would provide the evidence currently missing from this work. Measuring metrics such as: number of iterations to completion, regression rate, percentage of existing assets reused, and human intervention frequency.

**Adaptive pipeline depth.** The current complexity router uses static tier boundaries. A more sophisticated approach would dynamically adjust pipeline depth based on runtime signals: if the Analyze phase reveals a well-covered, well-documented codebase area, the Plan phase could be simplified even for multi-file tasks. Conversely, if Analyze reveals low coverage and complex dependencies, even a single-file task might warrant full planning.

**Multi-agent extension.** Adapting Vivi for parallel agent execution — where one agent analyzes while another begins planning for a related task — could address the throughput limitation. This would require formal handoff protocols between agent instances and conflict resolution mechanisms.

**Automated memory validation.** Integrating the memory system with CI/CD to automatically detect stale entries (references to files that have been deleted or significantly refactored since the entry was created) would address the staleness risk identified in Section 7.3.

---

## 9. Conclusion

Vivi v1.0 represents an attempt to codify the best available evidence on how LLM coding agents should reason about and implement features in existing codebases. The methodology synthesizes flow engineering from AlphaCodium, interface design principles from SWE-Agent, bounded exploration from tree search research, role separation from Aider, context management from Augment Code and Aider, failure recovery from AgentDebug and LDB, and episodic memory from Reflexion into a unified, tool-agnostic framework.

The key contributions are: a complexity router that prevents over-engineering simple tasks, mandatory asset discovery that enforces reuse over creation, test-anchor generation that shifts verification left, evidence-gated failure recovery that prevents speculative fixes, and persistent episodic memory that enables cross-session learning.

The key limitation is the absence of controlled empirical evaluation. The design decisions are individually supported by published research, but the specific combination has not been independently validated. The methodology should be treated as a well-motivated engineering artifact — informed by the best available evidence — rather than a proven system with quantified performance claims.

The broader observation motivating this work is that **the gap between AI coding benchmarks and professional software engineering practice is primarily a gap in methodology, not model capability**. The same models that achieve 90%+ on HumanEval struggle with brownfield features not because they lack coding ability, but because they lack the structured reasoning protocols that human engineers use instinctively: understanding before acting, reusing before creating, testing before declaring success, and learning from failures rather than repeating them. Vivi is an attempt to provide those protocols in a form that any LLM can follow.

---

## 10. References

[1] Zhou, A., Yan, K., Shlapentokh-Rothman, M., Wang, H., & Wang, Y.-X. (2024). Language Agent Tree Search Unifies Reasoning, Acting, and Planning in Language Models. *Proceedings of the 41st International Conference on Machine Learning (ICML 2024)*. arXiv:2310.04406.

[2] Ridnik, T., Kredo, D., & Friedman, I. (2024). Code Generation with AlphaCodium: From Prompt Engineering to Flow Engineering. *CodiumAI Research*. arXiv:2401.08500.

[3] Yang, J., Jimenez, C. E., Wettig, A., Liber, K., Narasimhan, K., & Press, O. (2024). SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering. *Advances in Neural Information Processing Systems (NeurIPS 2024)*. Princeton University.

[4] Xia, C. S., Deng, Y., Dunn, S., & Zhang, L. (2024). Agentless: Demystifying LLM-based Software Engineering Agents. *OpenAutoCoder Project*. GitHub: OpenAutoCoder/Agentless.

[5] Ronacher, A. (2025). Agentic Coding Recommendations. *Armin Ronacher's Thoughts and Writings*. https://lucumr.pocoo.org/2025/6/12/agentic-coding/

[6] Sylvester, T. (2025). What's Wrong with Agentic Coding? *Medium*. https://medium.com/@TimSylvester/whats-wrong-with-agentic-coding-c17f7c1e607b

[7] GitHub Blog. (2025). How to Write a Great agents.md: Lessons from over 2,500 Repositories. *The GitHub Blog, AI and ML*. https://github.blog/ai-and-ml/github-copilot/how-to-write-a-great-agents-md-lessons-from-over-2500-repositories/

[8] Augment Code. (2025). Context Engine: Full Codebase Awareness. *Augment Code Documentation*. https://www.augmentcode.com/

[9] OX Security / SoftwareSeni. (2025). Understanding Anti-Patterns and Quality Degradation in AI-Generated Code. *SoftwareSeni Blog*. https://www.softwareseni.com/understanding-anti-patterns-and-quality-degradation-in-ai-generated-code/

[10] Gulati, A. (2025). Controllable LLM Debugging: Knowing When to Stop Matters. *Stanford University, CS 191W*. https://cs191.stanford.edu/projects/Gulati,%20Aryan_NLP%20191W.pdf

[11] Cemri, M., Pan, M. Z., & Yang, S. (2025). Why Do Multi-Agent LLM Systems Fail? *arXiv preprint*. arXiv:2503.13657.

[12] Osmani, A. (2025). How to Write a Good Spec for AI Agents. *AddyOsmani.com*. https://addyosmani.com/blog/good-spec/

[13] Thoughtworks. (2025). Spec-Driven Development: Unpacking One of 2025's Key New AI-Assisted Engineering Practices. *Thoughtworks Insights*. https://www.thoughtworks.com/en-us/insights/blog/agile-engineering-practices/spec-driven-development-unpacking-2025-new-engineering-practices

[14] Anthropic. (2025). How Claude Code Works. *Claude Code Documentation*. https://code.claude.com/docs/en/how-claude-code-works. Architecture analysis: PromptLayer. (2025). Claude Code: Behind-the-Scenes of the Master Agent Loop. https://blog.promptlayer.com/claude-code-behind-the-scenes-of-the-master-agent-loop/. System prompts archive: Piebald-AI. https://github.com/Piebald-AI/claude-code-system-prompts.

[15] SWE-Agent Team. (2025). Mini-SWE-Agent: The 100-Line AI Agent. *GitHub*. https://github.com/SWE-agent/mini-swe-agent

[16] Yao, S., Yu, D., Zhao, J., Shafran, I., Griffiths, T. L., Cao, Y., & Narasimhan, K. (2023). Tree of Thoughts: Deliberate Problem Solving with Large Language Models. *Advances in Neural Information Processing Systems (NeurIPS 2023)*. arXiv:2305.10601.

[17] Islam, M. A., Pramanik, M. A., & others. (2024). MapCoder: Multi-Agent Code Generation for Competitive Problem Solving. *Proceedings of the 62nd Annual Meeting of the Association for Computational Linguistics (ACL 2024)*. GitHub: Md-Ashraful-Pramanik/MapCoder.

[18] Gauthier, P. (2024). Separating Code Reasoning and Editing. *Aider Blog*. https://aider.chat/2024/09/26/architect.html

[19] Amazon Web Services. (2025). Reinventing the Amazon Q Developer Agent for Software Development. *AWS DevOps Blog*. https://aws.amazon.com/blogs/devops/reinventing-the-amazon-q-developer-agent-for-software-development/

[20] Hu, C. (2025). Cognition / Devin "Planning-Mode" Subagent. *AgenticAIs, Medium*. https://medium.com/agenticais/cognition-devin-planning-mode-subagent-a84ed1c4727a. System prompts archive: Elifuzz. https://elifuzz.github.io/awesome-system-prompts/devin

[21] Windsurf Documentation. (2025). Cascade. https://docs.windsurf.com/windsurf/cascade/cascade

[22] Gauthier, P. (2024). Repository Map. *Aider Documentation*. https://aider.chat/docs/repomap.html

[23] Deng, Y. et al. (2024). How to Understand Whole Software Repository? *arXiv preprint*. arXiv:2406.01422.

[24] Packer, C., Wooders, S., Lin, K., Fang, V., Patil, S. G., Stoica, I., & Gonzalez, J. E. (2023). MemGPT: Towards LLMs as Operating Systems. *MemGPT Research*. https://research.memgpt.ai/

[25] JetBrains Research. (2025). Cutting Through the Noise: Smarter Context Management for LLM-Powered Agents. *The JetBrains Research Blog*. https://blog.jetbrains.com/research/2025/12/efficient-context-management/

[26] Spotify Engineering. (2025). Background Coding Agents: Context Engineering (Part 2). *Spotify Engineering Blog*. https://engineering.atspotify.com/2025/11/context-engineering-background-coding-agents-part-2

[27] Anthropic. (2025). Effective Context Engineering for AI Agents. *Anthropic Engineering Blog*. https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents

[28] Zhong, L. et al. (2024). LDB: A Large Language Model Debugger via Verifying Runtime Execution Step by Step. *Proceedings of the 62nd Annual Meeting of the Association for Computational Linguistics (ACL 2024)*. GitHub: FloridSleeves/LLMDebugger.

[29] Su, H. et al. (2025). Where LLM Agents Fail and How They Can Learn from Failures. *arXiv preprint*. arXiv:2509.25370.

[30] Anthropic. (2025). Effective Harnesses for Long-Running Agents. *Anthropic Engineering Blog*. https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents

[31] Shinn, N., Cassano, F., Gopinath, A., Narasimhan, K., & Yao, S. (2023). Reflexion: Language Agents with Verbal Reinforcement Learning. *Advances in Neural Information Processing Systems (NeurIPS 2023)*. arXiv:2303.11366.

[32] Xiong, R. et al. (2025). Memory in LLM Agents: Retention, Consolidation, and Degradation. Referenced in: Emergent Mind. Persistent Memory in LLM Agents. https://www.emergentmind.com/topics/persistent-memory-for-llm-agents

[33] Leaked system prompts: jujumilk3. (2024). Cursor IDE Sonnet System Prompt. *GitHub*. https://github.com/jujumilk3/leaked-system-prompts/blob/main/cursor-ide-sonnet_20241224.md

[34] Li, H. et al. (2025). Intention Chain-of-Thought Prompting with Dynamic Routing for Code Generation. *arXiv preprint*. arXiv:2512.14048.

[35] Holterhoff, K. (2025). 10 Things Developers Want from Their Agentic IDEs in 2025. *RedMonk*. https://redmonk.com/kholterhoff/2025/12/22/10-things-developers-want-from-their-agentic-ides-in-2025/

[36] Overman, E. et al. (2025). The Oversight Game: Learning to Cooperatively Balance an AI Agent's Safety and Autonomy. *arXiv preprint*. arXiv:2510.26752.

[37] Factory.ai. (2025). The Context Window Problem: Scaling Agents Beyond Token Limits. https://factory.ai/news/context-window-problem

[38] Osmani, A. (2025). My LLM Coding Workflow Going Into 2026. *AddyOsmani.com*. https://addyosmani.com/blog/ai-coding-workflow/

[39] OpenReview. (2025). A Survey of Frontiers in LLM Reasoning: Inference Scaling. https://openreview.net/pdf?id=SlsZZ25InC

[40] Gantz AI. (2025). Why Agents Get Stuck in Loops (And How to Prevent It). https://gantz.ai/blog/post/agent-loops/

---

*Vivi v1.0 — June 2026*
*This paper documents the design rationale and evidence base for the Vivi methodology. It is not a peer-reviewed publication. All cited benchmark results are attributed to their original authors and have not been independently reproduced by the authors of this methodology.*
