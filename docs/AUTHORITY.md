# Magicite v1 integration authority

The installed package identity is 1.0.0rc1. A limited native preview is published;
new integration source changes are not included in that published artifact.
Neither the preview nor integration source declares that original v1 GA gates passed.
The [draft preview contract](releases/1.0.0-rc.1.md) defines its limited advertised
scope; it does not amend or pass the original GA acceptance criteria.

## Authority order

1. Frozen [v1 acceptance criteria](../.spectra/plans/magicite-v1/acceptance.md),
   contracts, evaluation and release gates, plus explicitly accepted amendments.
2. Installed runtime schemas and tests, with the [generated reference](generated/runtime-reference.json)
   checked against actual version, engram schemas, 16 MCP tools, CLI and config.
3. Current documents `02` through `07`, operations and adapter guides. The
   current v1 contract at each document's start supersedes its historical context.
4. Historical v0.3 results and append-only archives, retained as evidence rather
   than proof of v1 performance or efficacy.
5. Exploratory research and unaccepted proposals, which cannot change behavior.

The stable default is `dense-v1`; see [ADR 001](decisions/001-stable-dense-v1-incumbent.md).
Adaptive selection is experimental. S10 efficacy is held; its containment is mandatory.
A passing fixture establishes only that fixture's obligation. Official external data,
production envelopes, real-host transcripts, fuzz evidence and independent operator
validation remain separately [UNEVALUATED](evaluation/v1/unevaluated.md).

`skill-graph.db` is local, rebuildable state and is ignored by default. Complete
recovery also requires canonical bodies/assets, approvals, policy/trust state,
evidence checkpoints and separately protected fingerprint-key custody; a copied DB
is not a complete backup. Authenticated trust history is held by a separately
provisioned custodian under the adopted trust-hardening amendment; the project-local
journal is a verified projection, not an authority. A historical [separate-UID Linux custody witness](qualification/linux-custody.md)
exists at source c7b3bfbeae89031bb33658a4bc041aea4a2091ec. It does not qualify
macOS custody or the combination of native installation and real-host deployment.
Those combined deployment obligations remain UNEVALUATED.

## Protected calibration admission (GA-R1)

Runtime thresholds come exclusively from the immutable calibration bundle in
MAC-protected policy state under existing authenticated custody. Local
`calibration/active.json` and named evaluation artifacts never authorize runtime
calibration. The artifact binds the uncalibrated base policy; the admitted
candidate has a separate effective identity containing the artifact digest.
The server permission/tool ceiling is bound once. Current configuration, model
bytes, registry/index pins, installed source mechanism and authenticated custody
history are checked before each calibrated route and cached body disclosure.
Changed history, retired records, missing control corroboration or stale bindings
fail closed. Writer fence advancement alone does not change history identity.

`magicite policy admit-calibration --artifact artifact.json --evidence evidence.json
--actor OPERATOR` gives a read-only digest summary. After reviewing the entire
bundle, the operator supplies `--reviewed-sha256 EXACT_BUNDLE_DIGEST` to admit the
candidate. Existing `policy approve` and CAS `policy activate` remain separate
explicit operations. No calibration admission MCP tool or automatic activation
is added. Generic `register-evaluated` cannot authorize a calibrated policy,
including a policy named `dense-v1`; its legacy exemption applies only without
calibration.

The future `magicite/calibration-qualification/1` attestation requires complete
qualifying E2/E3 PASS reports with immutable fit/final input and candidate anchors,
exact subject/source bindings and digest-linked underlying reports. Current
frozen-calibration consumer reports remain nonqualifying and are rejected even
inside an outer PASS wrapper. Validation checks structure and consistency;
it does not prove authentic data or statistical truth. The exact digest review
is an operator attestation protected by existing custody. Its frozen source
commit is attested provenance; the observed installed Python-source commitment
is the live mechanism identity. The current model adapter must expose immutable
model artifact bytes for non-hashing calibrated admission; an unobservable remote
or default-cache model fails closed. No model is fetched by admission.

Threshold margins do not establish probability calibration. Calibrated decisions
retain null probability confidence. Positive tests use explicitly synthetic
authenticated test custody and prove engineering mechanics only. GA-R1 closure
leaves the five fixed items unchanged: grouped statistical/confidence evaluation,
protected benchmark integration, authentic fresh final quality qualification,
platform/install/recovery/distribution qualification, and independent release
candidates/external reproduction with an explicit GA decision. No authentic
E2/E3 result or release qualification follows from these fixtures.

## Historical 0.3 semantic decisions (superseded where v1 differs)

- FastEmbed is the default production provider; hashing is the deterministic
  CI provider and Ollama is optional.
- Source use is offline by default. `magicite fetch-model` is the explicit
  network-bearing acquisition step; the container bakes the model at build
  time and remains offline at runtime.
- Engram IDs are immutable identity/routing hashes. Whole-file drift is tracked
  by a separate content digest.
- The canonical routing view is versioned and consists of intent, positive
  triggers, and procedure text. Contraindications (`not_when` and negative
  triggers) use a separate representation and score contribution. Pitfalls and
  examples are not silently claimed as routing inputs.
- `yields` is portable composition metadata in 0.3; it is not a graph edge
  until a future governed semantics defines its producer/consumer behavior.
- `skill-graph.db` is local, rebuildable state and is ignored by default. The
  `.egr.md` registry and durable approval mirrors are the portable authority for skills
  and proposals; trust history is not (see the custodian-held journal above).
- Lifecycle status, verification status, and operation execution status are
  independent dimensions. The word `pending` must always name its dimension.
- Register, sync, sharpen, lifecycle operations, and Dream may write durable
  state under the single-writer protocol. Dream alone performs consolidation
  and learned-state checkpointing; it is not the only legitimate file writer.
- Baseline-(c) shares production seed selection and declared-inhibition
  semantics. Results derived from the 0.2 divergent evaluator are superseded,
  not erased.
- Version 0.3 has no native C routing implementation. Index reuse and batched
  native-library operations must be exhausted before reconsideration.

