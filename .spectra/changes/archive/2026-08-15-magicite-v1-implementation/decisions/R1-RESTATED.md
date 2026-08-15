---
eidolon: ramza
kind: decision
id: R1-RESTATED
version: 1.0.0
created_at: 2026-08-15
change_id: magicite-v1-implementation
supersedes: "R1's mitigation as emitted (spec.yaml risks[R1].mitigation; spec.md §Risks R1 row)"
status: recorded
disposition: "restatement of a risk-register mitigation so it matches the shipped artifact — no formal spec amendment, no re-plan, no criteria change, no re-scoring"
authorizing_verdict: "FORGE (Reasoner) — 'Magicite Signal Integrity and Identity Model', 2026-08-14, RISK-ASSESSMENT with an embedded CONSTRAINT-SATISFACTION sub-decision on identity, 2 passes, 1 gate, gate PASS, confidence 86%, requires_checker false"
authorizing_verdict_path: "scratchpad/signal-integrity-verdict.md (session-local; ephemeral — the decisive content is reproduced below so this record stands alone)"
evidence_path: "scratchpad/m3-security-review.md (executed adversarial review at M3) and scratchpad/m4-security-retest.md (executed post-fix re-test against pinned commit 3660c5d)"
implementing_commits: "9044d1f (M3 ladder), 3660c5d (M4 signal hygiene: refractory, decay-at-read, spacing gate, retro-credit bound, eph_event test), 15eda70 (M5 data-loss fixes), 9f5deb3 (M6 session_end grace floor); docs corrected separately by IDG at 1ce6f99"
implemented_by: vivi
recorded_by: ramza
appendonly: true
---

# R1-RESTATED — signal poisoning: the bound is temporal, not authenticational

> **Append-only.** This record is written once. If R1 is restated again, write `R1-RESTATED-2.md`
> and mark this record superseded; do not edit it in place.

---

## 1. The governing principle

*(FORGE §4.3, reproduced as written. Everything below is RAMZA's governance record of how it was
landed in the spec.)*

> Under local-first stdio, Magicite's achievable integrity guarantee is **temporal, not
> authenticational**. Any hot-path-writable quantity that influences routing must be (i)
> **rate-limited in wall-clock time** and (ii) **self-reversing without further input**. Both are
> enforceable with zero caller identity. Anything stronger requires Tier 2 (out-of-band
> verification) or the served profile (real principals). This is not a weaker version of the
> intended guarantee — it is the strongest guarantee the posture admits, and it is worth stating as
> such rather than pretending to the other one.

Two consequences follow, and both are now written into R1:

1. **The bound moves from the subject to the object.** Per-subject quotas are structurally
   impossible here (§3). What a caller *cannot* mint is an **engram** and **elapsed wall clock**, so
   every bound R1 now cites is keyed on one of those: a per-engram refractory window on the R bump,
   spacing-gated potentiation at Dream, decay applied at read, a bounded retroactive-credit set.
2. **These bounds survive the deferred served profile.** They are not v1 stopgaps to be ripped out
   when authentication arrives — in the served/multi-tenant profile they **compose with** per-tenant
   authorization rather than being replaced by it (FORGE §1.5: *"defer the authorization half; land
   the bound half now"*).

R1's own risk statement was never wrong. docs/07's *"bounded, not eliminated"* is exactly right, and
this record keeps it. What was wrong was the **bookkeeping**: the mitigation list credited a control
that cannot bind.

---

## 2. What was wrong

R1's mitigation, as emitted:

> two-phase commit, Tier-0 barred from S, **Tier-1 capped 3 dw/skill/session at weight 0.6**,
> metaplastic saturation, Dream-only S writes

The bolded clause describes a control that does not exist as a security bound. Executed, not argued
(M3 review F-1, reproduced to the row by the M4 re-test §2):

```
5 calls in ONE session          -> 3 tags          <- the cap works *within* a session
200 minted session ids          -> 253 total node tags for ONE skill
50 further calls with NO session_id at all -> still 253 (a fresh session is minted per call)
captured (pending-dw) tags:     [{'signal_tier': 1, 'n': 200, 'w': 199.99490973757938}]
```

`core/signals.py` caps on `(session_id, engram_id)`; `core/session.py::resolve_id` upserts any
string it is handed, and omitting `session_id` mints a fresh UUID per call. So the residual bound
against a **conforming** client is zero: this is a correctness defect before it is a security
finding, because the *documented* calling convention defeats it with no malice at all (FORGE §2, H2
rejected *by execution*).

FORGE weighted this finding the most heavily on integrity grounds: *"a security claim a reader
relies on and that does not hold is worse than an absent one."* IDG corrected `docs/05` and
`docs/03` at commit `1ce6f99`; `spec.yaml` and `spec.md` were the last artifacts still asserting it.

---

## 3. The identity crux — why no per-session cap could ever have worked

*(FORGE §4.1–4.2, condensed; this is the part that makes the restatement permanent rather than a
patch waiting for a better cap.)*

**The category error.** `session_id` was never designed to denote a principal. docs/02 discipline 1
makes the intent explicit — *"No correctness dependence on protocol sessions or connection
persistence — session state is keyed by explicit `session_id` parameters"* — i.e. it exists so state
survives disconnects. It is a **correlation key for the co-activation window**, a continuity device.
R1's mitigation borrowed it as a quota key; everything downstream follows from that borrowing.

**Why a bindable quota identity is structurally impossible in v1.** A quota binds only if you can
(a) name principals and (b) prevent free creation of principals. Under stdio local-first:

- there is exactly **one** OS principal — the user — shared by client and server, so a per-caller
  quota is a quota against oneself;
- session creation is **free by design** (each agent turn, each reconnect, each new server process
  is a legitimate new session) — and a server-signed token does not fix this, because the server
  would issue tokens to whoever asks; there is nobody else to ask;
- even process-scoped bounds are evadable without malice: the host spawns a server process per
  client and several processes share one DB file.

This is a **structural result, not an engineering gap**. It is why the restatement does not promise
a better cap: there is no cap of that shape to build. The per-session cap is therefore kept and
**honestly demoted to runaway protection** — it is still the cheapest guard against the single most
likely adversary (the buggy/runaway caller, whose existence FORGE rates *certain*) — but it stops
being counted as a security control.

---

## 4. What actually ships as R1's mitigation, and what was executed against it

All of the following are implemented **and** tested in the tree; each row's measurement was executed
by the post-fix re-test (`m4-security-retest.md`, pinned commit `3660c5d`) unless noted.

| Mechanism | Keyed on | Executed measurement |
|---|---|---|
| Two-phase commit (tags ≠ weights) | — | structural: hot-path writes only `eph_*`; the G1 authorizer DENYs the rest (AC-013) |
| **Tier-0 barred from S** | tier, server-assigned | `plasticity.apply()` raises `P0Violation` (AC-014) |
| **Dream-only S writes** | G3 dream-context guard | mutation spot-check: disabling `assert_dream_context` fails 5 tests |
| **`eph_tag` is the sole plasticity-S input** — `eph_event` never is | the table, not the caller | 100 no-op `signal_outcome` → 200 ledger rows, **0** captured tags; **plus 100 hand-planted Tier-2 `valence=+1.0` rows naming the attacker** → max \|ΔS\| across the registry = **0.0**. Static trace: no path from `eph_event` into `_phase2_potentiate`. Now held by `test_eph_event_flooding_cannot_move_storage_strength`, not by a docstring (FORGE N-6) |
| **Refractory window on the R bump** (`eta_r_refractory_s = 30s`) | **`engram_id`** + wall clock | 30 calls across 30 minted session ids in < 1 s → **exactly one** bump (R = 0.15), rank 7 → 5 (not 7 → 3); 50 further calls **omitting `session_id`** → R unchanged; `session_end` then 20 more calls → anchor unmoved. R now counts *occasions*, not *calls* |
| **Decay applied at read** (λ_R 0.1/day, λ_S 0.01/day) | wall clock | R pinned at 1.0, anchor aged: 7 d → score 0.0761, 30 d → 0.0090 (baseline). Self-reverses with **no** Dream run, **no** `sync()`, **no** human — which also closes FORGE §3.1's recovery hole (revert-then-`sync` used to re-bind a poisoned row) |
| **Spacing-gated potentiation** (`tau_spacing_hours = 6.0`) | **`engram_id`** + wall clock | **200 captured Tier-1 tags (summed capture_weight 200.0) → ΔS = 0.000000000.** Conversion is priced in wall clock: first commit needs ≳ 85 min elapsed (`\|dw\| > theta_dw_commit = 0.01`), then **at most one commit per engram per Dream run**, ΔS ≤ +0.047, ceiling `eta·(1−S/w_max)·TIER_WEIGHT[1] = 0.048` per occasion — ~18 daily occasions to reach the S ≥ 0.6 consolidation bar from zero |
| Same gate on **edges** | edge identity + wall clock | 4 200 captured edge tags from 101 bursts → **0** committed edges, max edge S = 3.6e-07, **0** synapses written. A burst establishes anchors and commits nothing |
| **Bounded retroactive credit** (`retroactive_credit_max = 10`) | count, per call | closes FORGE §3.6's single-call breadth lever (previously *every* live tag in the window) |
| **Metaplastic saturation** | per-event step | unchanged and still credited — but note precisely what it bounds: the **per-event step**, never the **number of events**. It is not a substitute for a count bound |
| **Tier-1 weight 0.6** | tier | `TIER_WEIGHT = {0: 0.0, 1: 0.6, 2: 1.0}`, verified exact |
| ~~per-session cap as anti-poisoning~~ → **runaway protection** (`per_skill_session_cap = 3`) | `(session_id, engram_id)` — mintable | 253 tags / 200 captures reproduce **exactly**; retained, no longer counted as a security control |

**Guard integrity spot-check.** Eight guards were individually broken in a pinned copy of the tree
and the full suite re-run: **8/8 detected, no guard's removal leaves the suite green** (G3 5
failures; refractory 1; spacing gate 2; `session_end` fix 1; decay-at-read 1; retro-credit cap 1;
cross-process lease 2; archival evidence gate 4).

---

## 5. Residuals — open, verified, and deliberately not papered over

R1 says *"bounded, not eliminated."* These are what "never eliminated" means concretely. Every one
was executed **after** the fixes landed. None of them is closed by this record.

| ID | Residual | Executed evidence | Why it stays open |
|---|---|---|---|
| **RES-1** | **Cap-burning.** A caller that names another agent's session can burn a skill's per-session cap inside it, so the legitimate owner's own honest `signal_use` returns `tagged=[] capped=[…]` | re-test §5 (C) | Closing it requires knowing *who* is calling — caller identity, structurally impossible here (§3) |
| **RES-2** | **Cross-session credit hijack.** A high-salience `valence = −1.0` `signal_outcome` naming another agent's session retroactively credits that agent's live tags negatively (2 skills credited at −1.0, executed) | re-test §5 (D) | Breadth is bounded to 10 by `retroactive_credit_max`, but the hijack itself needs a **participation check** — i.e. identity, or a change to the frozen 16-tool surface |
| **RES-3** | **Pre-capture session suppression.** `session_end(<victim's id>)` before the victim's `signal_outcome` still makes it capture 0 | re-test §5 (A) | Partially bounded at M6 by `session_end_tag_grace_s = 60s` (a tag must be old enough before its expiry can be pulled forward). FORGE's N-7 *as specified* — make `session_end` non-suppressive — did not land; what landed protects the already-captured window plus a grace floor. **Bounded, not eliminated** |
| **RES-4** | **Saturation is reachable by a patient caller.** +0.10 of R in ≈ 3.5 min, 95 % of the +0.15 ceiling in ≈ 9.5 min, the fixture's rank-3 flip in **12 min / 48 calls**; S ≥ 0.6 in ~18 daily occasions | re-test §1–2 | **Deliberate** (FORGE §3.1 part 3): a local-first server cannot distinguish *"used often"* from *"reported as used often"* — that distinction is exactly what Tier 2 buys. The guarantee is that influence accrues no faster than elapsed time allows and self-reverses |
| **RES-5** | **Co-activation row fan-out is uncapped per call** (one 20-id call → 380 candidate edges + 380 edge tags), and an edge at S = 0.06 moves a candidate further than R saturated at 1.0 does (`w_activation` 0.45 × `type_gain` 0.8 vs `w_retrieval` 0.15) | re-test §3 and §"residual steering path" | Fan-out was knowingly deferred by the implementer; **potentiation** is gated, so committed edge weight is noise (3.6e-07) and the temporal bound still governs (≈ 18 h vs 12 min). Recorded because *"R is the routing lever"* is no longer the right mental model |
| **RES-6** | **Row growth.** ≈ 10 KB of DB per 7-id `signal_use` call; `eph_tag` / `eph_event` are not bounded by registry size | re-test §3 | Deferred as an operations problem (FORGE §6.4); `decay.purge_retention` is a real reclaim path once Dream runs |

**Not R1's subject, recorded so this record is not read as claiming a clean re-test.** The same
post-fix run surfaced two defects outside signal poisoning — a lease-blind `sync()` that could
silently destroy a consolidation cycle, and `archive_below_floor` archiving a healthy skill from
*positive* signals in 8 calls / 6 h. Both were addressed at M5 (`15eda70`, whose subject names "two
data-loss fixes": the registry write path takes the cross-process lease, and archival now requires a
genuine prior peak ≥ `floor_archived`, held by
`test_engram_that_never_crossed_the_floor_is_never_archived`). They belong to R7/R3-adjacent
durability, not to R1, and are **not** claimed as R1 mitigations here.

---

## 6. Why this is a restatement, not a re-plan

FORGE routed it explicitly (§5.2, §6.3): *"This is a spec edit and is not mine to make … There is
precedent for the vehicle: the recorded A1-REVISED entry that touched `risks[R6]` under declared
variance. → SPECTRA/RAMZA."* Four confirming checks, all mechanical:

1. **No frozen criterion asserts the cap.** `acceptance-criteria.md` was searched for
   *cap* / *session* / *Tier-1* / *poison* / *noise* / *ablation*: the only hits are AC-017's
   three-session premise for the metaplastic-saturation bound, AC-026's `--cap-drop ALL`, and AC-032's
   `session_end` debounce — none
   references the per-session Δw cap, the mitigation wording, or R1. The false claim lived in the
   **risk register and narrative prose only**, never in a measured criterion. Byte-identical, sha256
   `7bd3d1842de8d0d55c00fc4803e52e012885a26094cc928b7801af556eada448`; `plan-state.json
   amendments[]` remains empty.
2. **Nothing normative moved.** AC-001…AC-033 text, all nine VG commands, INV-1…INV-4, the P0 guard
   mechanism, the Tier A/B/C split, the M0–M7 decomposition and all eight §9 CR resolutions are
   untouched. The **16-tool surface is untouched**: `signal_use`'s signature, defaults and return
   shape in §3.3 are byte-identical — only a `#` comment was added beneath step 6.
3. **Direction of travel.** Measured against the artifact, the register was *false in one direction
   and understated in another*: the cap binds nothing, but a 200-tag Tier-1 burst now moves S by
   **exactly 0.0**, which the emitted mitigation never claimed. A restatement that makes a P0 risk
   entry match its artifact — weaker where the claim was false, stronger where the artifact
   outgrew it — does not warrant re-planning.
4. **Same vehicle, same tier of change as A1-REVISED**, which touched `risks[R6]` under declared
   variance. R1 is not on `handoff.not_vivi_s_call` (the 16-tool surface, the Tier A/B/C split, the
   P0 guard mechanism, any §9 resolution), and nothing here reopens any of those.

`[CONSTRAINT]` `spec.yaml`'s header states that it *"MUST NOT diverge"* from `spec.md`. Both were
therefore patched in the same change-set; patching one alone would have created exactly the
divergence that rule exists to prevent.

---

## 7. What changed in the plan artifacts

| Artifact | Change |
|---|---|
| `decisions/R1-RESTATED.md` | **new** — this record |
| `spec.md` | four located patches: the errata banner on the reading contract, the §3.3 `signal_use` step-6 note (comment only), the Story M3 user-story overclaim (*"never poisonable by a caller's claim"*), and the §Risks R1 row. Superseded clauses are **struck through, not deleted** |
| `spec.yaml` | `risks[R1].risk` (errata pointer appended), `risks[R1].mitigation` (restated), a new `risks[R1].revised_by`, a second `errata:` entry, and refreshed `artifacts[]` hashes |
| `spec.envelope.json` | ECL integrity tag re-stamped to the new `spec.md` hash; second `x_ramza_amendment` entry appended (§8) |
| `plan-state.json` | one appended `errata[]` entry; `amendments[]` and `criteria_sha256` untouched |
| `acceptance-criteria.md` | **untouched, byte-identical** — the tamper anchor |

### 7.1 `spec.md` hash transition

```
old sha256  9fca1c088916cda23de4a815ce335b497c25d58cb7d1f0cc2d7a9350aac7baac   (90934 bytes)
new sha256  57148a2de70fa723b65cddbce2b57de98327a7ba44b272415101a9263aa79c17   (94679 bytes)
```

`spec.yaml artifacts[0].sha256` was updated to the new value. `artifacts[1]`
(`acceptance-criteria.md`, `7bd3d184…`) was left exactly as emitted and re-verified against the file
on disk. `artifacts[]` entries for `plan-state.json` and `spec.envelope.json` were refreshed because
this errata moves them; `decisions/A1-REVISED.md` (append-only, untouched) and
`ramza-calibration.jsonl` keep their recorded hashes, re-verified on disk. A new `artifacts[]` entry
was added for this record.

---

## 8. The ECL envelope re-stamp

Identical mechanism and identical reasoning to A1-REVISED §5, applied a second time.

`spec.envelope.json` carries an ECL v2.0 integrity tag computed over `spec.md`'s bytes; patching
`spec.md` necessarily invalidates it. Leaving the tag stale would make every future
`ramza-verify-emit` run report an integrity mismatch on a change that was authorized, adjudicated
and recorded — a **false** tamper alarm, and tamper signals that are known-broken stop being read.
Tamper-evidence exists to detect **unrecorded** change; a recorded, authorized amendment with a
preserved hash chain extends the audit chain rather than breaking it.

**Mechanism used.** There is no canonical `ramza-*` amendment command for the ECL envelope.
`ramza-freeze --amend` amends only `plan-state.json criteria_sha256` — i.e. the
`acceptance-criteria.md` anchor, which must **not** move — so it was deliberately not used again.
The re-stamp was a targeted `jq`/Python rewrite of `artifact.sha256`, `artifact.size_bytes` and
`integrity.value`, plus a **second** appended `x_ramza_amendment` entry (the A1-REVISED entry is
left intact, so the envelope now carries the full `92372ad6… → 9fca1c08… → 57148a2d…` chain),
verified afterwards by the canonical emission gate
`ramza-verify-emit --spec spec.md --envelope spec.envelope.json`, which recomputes the digest over
the payload bytes and fails on any mismatch.

**Left as sent, on purpose.** `objective`, `context_delta.summary` and `trace.ts` are the message as
it was actually sent to Vivi; retroactively editing prose in a delivered ECL message would be
rewriting history rather than amending it. `x_ramza_acceptance_criteria` is byte-unchanged.

---

## 9. Carry-forwards — open items this record creates or inherits

| ID | Item | Owner |
|---|---|---|
| **CF-1** | The *"adversarial-noise robustness test in the ablation suite"* named in R1's **original** mitigation **never shipped**: `src/magicite/eval/ablations.py` ships three switches, none adversarial, and no AC covers it. The standing evidence in its place is the two executed adversarial reviews plus the 8-guard mutation spot-check. R1's restated mitigation says so rather than continuing to cite it | Vivi (post-M6) |
| **CF-2** | **Guard coverage is thin.** The refractory, decay-at-read, `session_end` and retro-credit guards are each held by **exactly one** test; two of those assert the no-op rather than the behaviour (the refractory test does not assert the `1−0.85ⁿ` progression across windows; the `session_end` test asserts the captured-tag ordering that was already safe). Every one is one careless deletion from becoming decorative — and they are load-bearing for R1 | Vivi / Kupo |
| **CF-3** | **`docs/05` names two knobs that do not exist in the tree.** Its corrected Tier-1 text promises *"at most `per_skill_window_cap` per skill per rolling `signal_window` across all sessions"*; there is no such `Config` field, and the post-fix re-test reproduced **253 tags exactly**, i.e. no window cap binds. FORGE's N-3 (a window-keyed cap) was **not** implemented — what closed the conversion hole instead was the spacing gate at Dream. This spec deliberately does **not** copy that phrasing: doing so would repeat the exact defect this errata exists to fix. `docs/` is outside RAMZA's write boundary and outside this errata's remit | IDG (docs), Vivi (if the knob is to exist) |
| **CF-4** | **FORGE's reversal conditions are live** (§7): the verdict flips to the malicious-caller model if the server is ever deployed behind a privilege boundary the client does not share, if the served/multi-tenant profile is un-deferred, if `commit_db` becomes default-true, if Magicite is offered as a shared registry, or if Dream's S-input is widened beyond `eph_tag`. FORGE's one human ask was to confirm that **M7's packaging does not** create that boundary; **M7 has since shipped (`01c20fc`)**, so that check is now **due and is not adjudicated here** | human / FORGE |
| **CF-5** | **Fix-B (server-only session minting) is still an open decision routed to RAMZA/SPECTRA.** It would make `session_id` an unforgeable capability and close RES-1/RES-2/RES-3 as a class, but it contradicts §3.3's session-resolution rule (an explicit `session_id` is used verbatim and upserted) and breaks the deterministic-host-session adapter pattern. That is a **spec behaviour change**, i.e. a re-plan touching the tool surface — explicitly out of this errata's remit and deliberately not made | RAMZA (next planning cycle) |

---

## 10. Deliberately not changed

- **`acceptance-criteria.md`** — byte-identical, sha256
  `7bd3d1842de8d0d55c00fc4803e52e012885a26094cc928b7801af556eada448`, verified mechanically before
  and after. Not re-frozen, not amended; `amendments[]` stays empty and `criteria_sha256` is
  untouched.
- **The 16-tool surface**, `signal_use`'s §3.3 signature/defaults/returns, §3.3's session-resolution
  rule, INV-1…INV-4, the P0 guard mechanism (G1/G2/G3 + the tier gate), all nine VG commands, the
  M0–M7 decomposition, and all eight §9 CR resolutions.
- **R1's tag (P0), owner, milestone (M3,M4), and the substance of its risk statement** — *"bounded,
  not eliminated"* was correct and is retained verbatim.
- **The confidence score (84.75 → VALIDATE), the complexity score (10/12), the explore scores, the
  refine-cycle count, and the phase walk.** Not re-run and not re-scored: this is a risk-register
  restatement, not a re-plan.
- **`docs/`** — outside RAMZA's write boundary, and already corrected by IDG at `1ce6f99`. CF-3 is
  routed there rather than patched here.
- **`src/` and `tests/`** — not touched by this record. No source file changed, so the 457-test /
  94.04 %-coverage verification stands untouched.
- **`change.json status: verified`** — see §11.

---

## 11. Effect on the `verified` status: none

Stated explicitly because the change was signed off by Kupo (33/33 ACs, 9/9 validation gates, 457
tests, 94.04 % coverage) **before** this errata:

1. **The measurement anchor did not move.** Kupo verified against `acceptance-criteria.md` at
   `7bd3d184…`; that file is byte-identical, and **no AC references the per-session cap, R1, or the
   mitigation wording** (§6 check 1). A verification measured against those criteria is unaffected
   by any wording in the risk register.
2. **Nothing executable changed.** No file under `src/`, `tests/`, or `docs/` was touched by this
   errata; all nine VG commands are unchanged, so every gate re-runs identically.
3. **The correction runs toward honesty, not toward relaxation.** It removes a claim the artifact
   does not support and adds executed evidence for the claims it does. A risk register that
   overstates its mitigations is the condition that *threatens* a verification's meaning; correcting
   it strengthens the record.
4. **Precedent.** A1-REVISED touched `risks[R6]` (retitle + P1→P2 + mitigation extension) with no
   effect on verification status, under the same vehicle and the same disposition.

The one thing a re-verifier should re-check is the **hash chain**, which moved by design: `spec.md`
`9fca1c08… → 57148a2d…`, mirrored in `spec.yaml artifacts[]`, `spec.envelope.json`
(`artifact.sha256` / `integrity.value` / `x_ramza_amendment[1]`), and `plan-state.json errata[1]`.

**If the reader disagrees** — i.e. if the `verified` sign-off is held to cover the spec's prose and
not only its criteria — the remedy is a Kupo re-attest of the two amended documents, not a
re-plan. RAMZA's judgment, recorded here for challenge: it does not, and this errata does not
invalidate the verification.
