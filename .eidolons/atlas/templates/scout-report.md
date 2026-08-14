# SCOUT REPORT — ATLAS Phase S

> The single artifact downstream agents consume. Hard cap: 3000 tokens.

---

## 1. Mission recap

**MISSION-ID:** `<mission-id>`
**GOAL:** <verbatim from mission.md>
**DECISION_TARGET:** <verbatim>
**SCOPE:** <globs>
**STATUS:** `completed | partial | blocked`

---

## 2. Topology summary

> ≤10 bullets. Each carries a `path` reference.

- <structural fact> · `<path>`
- <structural fact> · `<path>`
- ...

---

## 3. Answer to DECISION_TARGET

> Prose. Every factual clause ends with a `[FINDING-XXX]` reference.
> Confidence language flows with tier.

### DT-1 — <sub-question>

<answer prose with inline citations>

### DT-2 — <sub-question>

<answer prose with inline citations>

### DT-3 — <sub-question>

<answer prose with inline citations, or: "Not resolved; see [GAP-003].">

---

## 4. Recommended next actions

> Ranked. Every item has a handoff label and concrete anchors.

### R-1 · priority: `high | medium | low` · `→ <recipient>`

<one-paragraph description>

- **References:** `FINDING-XXX`, `GAP-XXX`
- **Estimated timebox:** `up to N days` (when applicable)

### R-2 · priority: ... · `→ <recipient>`

...

### R-3 · priority: ... · `→ <recipient>`

...

**Handoff label legend:**
- `→ SPECTRA` — needs spec before implementation
- `→ APIVR-Δ` — spec clear; ready for implementation loop
- `→ human` — blocked on a judgment call
- `→ ATLAS` — deserves a follow-up scout mission (max 1 recursion)

---

## 5. Risks & gaps

| ID | Description | Tier | Proposed mitigation |
|----|-------------|------|---------------------|
| GAP-<n> | <short> | H/M/L | <mitigation or "needs decision"> |
| RISK-<n> | <short> | H/M/L | <mitigation> |

---

## 6. Telemetry

```
phase            | tokens_in | tokens_out | tool_calls
A (Assess)       | <n>       | <n>        | <n>
T (Traverse)     | <n>       | <n>        | <n>
L (Locate)       | <n>       | <n>        | <n>
A (Abstract)     | <n>       | <n>        | <n>
S (Synthesize)   | <n>       | <n>        | <n>
TOTAL            | <n>       | <n>        | <n>

fold_ratio:       <float>   # target ≤ 0.1
η (efficiency):   <float>   # target ≥ 0.25
```

---

## 7. Handoff block

```
<handoff>
  <primary_recipient><SPECTRA|APIVR-Δ|human></primary_recipient>
  <fallback_recipient>human</fallback_recipient>
  <report_path>artifacts/ATLAS/scout-report-<mission-id>.md</report_path>
  <memex_root>artifacts/ATLAS/memex/<mission-id>/</memex_root>
  <critical_gaps><GAP-ids></critical_gaps>
  <open_questions>
    - <R-N summary>
  </open_questions>
</handoff>
```

## 7.1 Envelope sidecar (ECL v2.0)

Emit a `scout-report.envelope.json` adjacent to this report. Use
`templates/scout-report.envelope.json` as the skeleton. Fill all
`<placeholder>` values before emitting. The envelope is a terminal
Phase-S artefact (not a tool call — see `SPEC.md §1 I-9` and
`skills/synthesize/SKILL.md §Envelope sidecar`).

The skeleton carries an `ise` block: `assertion_grade: "self-attested"`
(scout findings are evidence-anchored per I-7 but not externally
verified by another Eidolon) and `receiver_authorization: {auto_route:
true, auto_merge: false, auto_deploy: false}`. Fill `ise.provenance.methodology_version`
(`atlas-<version>`) at emit time; `ise.provenance.tool_surface` /
`lateral_consults` are optional.

Validate against `schemas/ecl-envelope.v2.json` before marking Phase S
complete.

<!-- SCOPE FIELD NOTE (for implementors):
  The envelope frontmatter uses scope.{entrypoints, modules, excluded}
  (defined in schemas/scout-report-profile.v1.json) to describe the
  mission scope in ECL terms.

  The scout-report BODY uses mission_recap.scope.{include, exclude}
  (defined in schemas/scout-report.v1.json) to record the SCOPE_INCLUDE /
  SCOPE_EXCLUDE path globs from mission.md.

  These are DIFFERENT fields at DIFFERENT layers and do NOT duplicate each
  other:
    - Envelope frontmatter scope = ECL routing metadata (entrypoints list,
      modules touched, dirs excluded from indexing).
    - Body mission_recap scope = raw glob patterns from the mission brief.

  Do not confuse them when filling the envelope template.
-->
