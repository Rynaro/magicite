# Discovery Report

**Task**: [task title / ticket reference]
**Date**: [date]
**Domain**: [primary module/area]
**Complexity**: Trivial | Standard | Complex | Uncertain

---

## Requirements

**Goal**: [one sentence — what problem does this solve?]

**Scope IN**:
- [file/module being changed]
- [file/module being changed]

**Scope OUT** (boundaries — do NOT touch):
- [file/module explicitly excluded]
- [file/module explicitly excluded]

**Acceptance Criteria**:
1. [Observable, testable condition]
2. [Observable, testable condition]
3. [Observable, testable condition]

---

## Repo Map (target domain)

```
DOMAIN/
  ClassName (inherits Parent) — #method1, #method2 [tested/untested]
  RepositoryClass — #find, #create, #search [tested/untested]
  ServiceClass — #execute, #validate [tested/untested]
  ComponentClass — renders [description] [tested/untested]
```

---

## Discovered Assets

| Asset | Location | Purpose | Relevance | Quality | Verdict |
|-------|----------|---------|-----------|---------|---------|
| [name] | [file:line] | [purpose] | HIGH/MED/LOW | [tested? recent?] | USE/EXTEND/WRAP/AVOID |
| [name] | [file:line] | [purpose] | HIGH/MED/LOW | [tested? recent?] | USE/EXTEND/WRAP/AVOID |

---

## Collision Map

**Files to modify**:
| File | Risk Level | Reason |
|------|-----------|--------|
| [path] | HIGH/MED/LOW | [why this is risky] |

**Files to create**:
| File | Collisions? | Notes |
|------|------------|-------|
| [path] | [any in-flight work?] | [notes] |

**High-risk zones**:
- [file/area]: [why it's high risk — low coverage, heavily imported, etc.]

---

## Memory Recall

**Past work in this area**: [summary from task-log or "none found"]
**Known failure patterns**: [summary from failure-catalog or "none found"]
**Relevant Delta suggestions**: [from delta-history or "none"]

---

## Open Questions

- [Any ambiguities that should be resolved before planning]
