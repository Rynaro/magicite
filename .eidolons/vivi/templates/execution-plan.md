# Execution Plan

**Task**: [task title]
**Date**: [date]
**Complexity tier**: Standard | Complex

---

## Test Anchors (generated BEFORE implementation)

| # | Test Case | Input State | Action | Expected Outcome | Existing Pattern |
|---|-----------|-------------|--------|-----------------|-----------------|
| T1 | [description] | [setup] | [action] | [expected] | [test to follow] |
| T2 | [description] | [setup] | [action] | [expected] | [test to follow] |
| T3 | [description] | [setup] | [action] | [expected] | [test to follow] |

---

## Strategies Evaluated

### Strategy A: [Name]
- **Approach**: [2-3 sentences]
- **Files**: [list with modify/create]
- **Assets used**: [which discovered assets]
- **Scores**: Risk(X) + Effort(X) + Alignment(X) + Maintainability(X) = **[total]**

### Strategy B: [Name]
- **Approach**: [2-3 sentences]
- **Files**: [list with modify/create]
- **Assets used**: [which discovered assets]
- **Scores**: Risk(X) + Effort(X) + Alignment(X) + Maintainability(X) = **[total]**

### Strategy C: [Name]
- **Approach**: [2-3 sentences]
- **Files**: [list with modify/create]
- **Assets used**: [which discovered assets]
- **Scores**: Risk(X) + Effort(X) + Alignment(X) + Maintainability(X) = **[total]**

---

## Selected Strategy: [Name] (Score: X/12)

**Justification**: [one paragraph — why this strategy over the runner-up]

**Runner-up**: [Name] (Score: X/12) — Rejected because: [reason]

**Confidence**: HIGH | MED | LOW

---

## Implementation Steps

| # | Step | Files | Type | Depends On | Abort If |
|---|------|-------|------|-----------|----------|
| 1 | [action] | [files] | USE/EXTEND/WRAP/CREATE | — | [condition] |
| 2 | [action] | [files] | USE/EXTEND/WRAP/CREATE | Step 1 | [condition] |
| 3 | [action] | [files] | USE/EXTEND/WRAP/CREATE | Step 2 | [condition] |

---

## Boundaries

**Do NOT modify**:
- [file/system outside scope]

**Ask before touching**:
- [shared infrastructure]
- [cross-domain files]

---

## Abort Conditions

If any of these occur, STOP and re-plan:
- [condition that invalidates the strategy]
- [condition that invalidates the strategy]
