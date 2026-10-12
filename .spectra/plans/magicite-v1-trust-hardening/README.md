# Adopted additive trust-history hardening plan

Status: **ADOPTED REQUIREMENTS; IMPLEMENTATION MERGED (PR #32); QUALIFICATION INCOMPLETE**. Separate-UID
deployment qualification is UNEVALUATED. Per-criterion status is tracked only by the
r2/r3 evidence packages under `docs/releases/v1/`, not here; scope amended by TH-A01.

Authority: the user approved the recommended trust-hardening amendment, acceptance
tests, implementation and independent review. The integration owner authorized the
functional local Unix-socket custodian direction and sole-writer implementation.
RAMZA supplied the specification and twelve additive criteria; VIGIL independently
identified the policy/root, protected-identity, sequence-order and fencing gaps
incorporated here before dependent implementation. This records adoption of the
requirements, not a claim that their implementation or real deployment passed.

Base: `22ae4e01acde9e99e64c5a6c6fa0bcb6a625684c` (v1 integration after PR31).
Branch: `codex/v1-trust-hardening`. Sole writer: Vivi. Independent design/review:
RAMZA and VIGIL. The root integration owner handles remote PR lifecycle.

The original `.spectra/plans/magicite-v1/` packet remains unchanged. Its frozen
`acceptance.md` SHA-256 is
`f10330326a78d50c9d657866dda8367849f79aca36c2740ac516a4db99011a2c`.
This adopted additive plan supersedes the *unadopted proposal status* for this work;
it does not retroactively change the historical S16 evidence or turn failed gates
into PASS. Original criteria remain required alongside [AC-TH-01–12](acceptance.md).
Amendments: [TH-A01](amendments/TH-A01-ac-th-10-supported-downgrade-scope.md) scopes AC-TH-10 downgrade to supported paths.

Read [specification](spec.md), [threat model](threat-model.md), and
[traceability and ownership](traceability.md). No main merge, release tags,
publishing, force-push, worktree/branch deletion, system account provisioning,
credential creation or external-service setup is authorized. The stable MCP surface
remains exactly sixteen tools; S10 remains held. Operator configuration necessary
to implement this approved design needs no further general approval. Real separate
UID Linux/macOS deployment qualification remains UNEVALUATED until actually run.
