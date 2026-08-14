# MISSION BRIEF — ATLAS

> Produced in Phase A (Assess). This is the contract that bounds the entire
> ATLAS run. Every field is required unless marked OPTIONAL.

---

**MISSION-ID:** `<YYYYMMDD-NNN>`
**Created:** `<ISO-8601>`
**Parent mission / caller:** `<id or "user">`

---

## GOAL

> One-sentence imperative. No ambiguity verbs.

<!-- Example: "Map all code paths that mutate cast_vote_records and identify
the authorization policies governing each." -->

<goal here>

---

## DECISION_TARGET

> The specific downstream question this mission must answer. If you cannot
> state it as a question with a checkable answer, STOP — the mission is not
> yet ready.

<!-- Example: "Which FlowObjects write to cast_vote_records, and is each
write guarded by an authorization check that refuses anonymous users?" -->

<decision target here>

### Sub-questions

- `DT-1` — `<sub-question>`
- `DT-2` — `<sub-question>`
- `DT-3` — `<sub-question>`

---

## SCOPE

### Include (path globs)

```
app/**
config/**
db/migrate/2024*
```

### Exclude (path globs)

```
tmp/**
vendor/**
node_modules/**
public/assets/**
spec/fixtures/**
```

---

## BUDGET

| Field | Value |
|-------|-------|
| `max_tool_calls` | `<int>` |
| `max_tokens_input` | `<int>` |
| `max_wall_clock_s` | `<int>` |
| `max_subagents` | `<int>` (default 4) |
| `max_recursion_depth` | `1` (ATLAS invariant) |

---

## STOP_CONDITIONS

Enumerate explicitly. ATLAS halts when ANY fires.

- `SC-1` — All `DT-N` sub-questions have `FINDING-XXX` at confidence ≥ M or
  are recorded in `GAPS`.
- `SC-2` — `max_tool_calls` reached.
- `SC-3` — `max_tokens_input` reached.
- `SC-4` — Three-strike halt fired on ≥ 50% of sub-questions.
- `SC-5` — Any `ESCALATION_TRIGGER` fires (see below).

---

## ESCALATION_TRIGGERS

Patterns that halt the mission and surface to human review.

- `ET-1` — `<pattern>`
- `ET-2` — `<pattern>`

<!-- Project-specific triggers go here. Examples from election-critical
domains:
- File paths matching /cast_vote_records|tally|vote_result|voter_pii/
- Symbols matching /Tallier|CastVoteRecord|VoterIdentity/
- Migration files touching vote-related tables
-->

---

## HANDOFF_RECIPIENTS

Expected downstream agents for the scout report:

- Primary: `<SPECTRA | APIVR-Δ | human>`
- Fallback: `<human>`

---

## ACCEPTANCE_NOTES  (OPTIONAL)

Free-form notes from the caller about what "good" looks like. Useful when
the caller is another agent rather than a human.

---

## SIGN-OFF

- [ ] `GOAL` has a single imperative verb.
- [ ] `DECISION_TARGET` is a checkable question.
- [ ] All `DT-N` sub-questions reference `DECISION_TARGET`.
- [ ] Scope globs are non-empty and non-overlapping.
- [ ] Budget fields are populated with integers.
- [ ] At least one `ESCALATION_TRIGGER` if domain is safety-sensitive.

Once all boxes are checked, ATLAS enters Phase T (Traverse).
