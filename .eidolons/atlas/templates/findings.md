# FINDINGS — ATLAS Phase L

> One record per FINDING-XXX. Append-only during Locate.

**MISSION-ID:** `<mission-id>`

---

## FINDING-<NNN>

```yaml
id: FINDING-001
claim: |
  <One-sentence factual claim. Declarative, no hedging unless tier < H.>
evidence:
  - path: <path>
    lines: <start>-<end>
    excerpt_ref: memex://excerpt/<hash>
  - path: <path>
    lines: <start>-<end>
    excerpt_ref: memex://excerpt/<hash>
ruled_out:            # OPTIONAL — negative results are first-class
  - <what was searched and not found>
confidence: H | M | L
supports_decision: DT-<n>
notes: |              # OPTIONAL — short, no speculation
  <contextual note>
```

Repeat for every finding.

---

## GAP-<NNN>

> Record sub-questions that fired the three-strike halt.

```yaml
id: GAP-001
sub_question: DT-<n> — <question>
strikes:
  - probe: <probe signature>
    result: <empty | conflicting | below threshold>
  - probe: <probe signature>
    result: ...
  - probe: <probe signature>
    result: ...
working_hypothesis: |
  <Best-guess answer with L confidence, for downstream judgment.>
escalate_to: human | follow-up-ATLAS | SPECTRA
```

---

## Escalation log

> Every ESCALATION_TRIGGER that fired during Locate.

```yaml
- trigger: ET-<n>
  fired_at: <ISO-8601>
  on_probe: <probe signature>
  on_path: <path:line>
  action_taken: halt | annotate | continue-with-flag
```

---

## Phase-L telemetry

```
probes_total:         <int>
tier1_symbol:         <int>
tier2_graph:          <int>
tier3_lexical:        <int>
tier4_windowed_read:  <int>
tier5_test_dryrun:    <int>
subagents_spawned:    <int>
three_strike_halts:   <int>
tool_calls:           <int>
tokens_in:            <int>
tokens_out:           <int>
wall_clock_s:         <int>
```

---

## Exit checklist

- [ ] Every `DT-n` sub-question has ≥1 finding at confidence ≥ M, OR a `GAP-nnn`.
- [ ] Every finding has at least one `evidence` entry with `path:lines`.
- [ ] Every `excerpt_ref` resolves via `memex.read`.
- [ ] `ruled_out` recorded for every finding derived from a search whose
      negative space matters (most of them).
- [ ] Escalation log captures every `ESCALATION_TRIGGER` firing.
- [ ] Phase-L tool-call count ≤ 60% of mission budget.
