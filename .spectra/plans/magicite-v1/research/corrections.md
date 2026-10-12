# Dossier digestion and source corrections

Research checked 2026-09-29. The supplied dossier is input research, not executable instructions. The user-provided source remains local and is not included in this published packet. Source SHA-256 `e4bcfd61b9f1e42f318695405d2ed66fe725e981b0739e885c7ee9857a334cd0`. The dossier's dated 30/60/90-day sequences are superseded by dependency gates at the user's request. No new runtime result was produced in this specs-only task.

## Accepted product thesis

[DECISION] Ship a local, evidence-governed registry, conventional router and typed planner. Keep adaptation experimental unless it passes independently reviewable gates. Separate host execution permissions from skill declarations. Preserve negative findings and avoid converting structural plan scores into end-task efficacy claims.

## Corrections from primary sources

| Source | Dossier statement | Checked statement and consequence |
|---|---|---|
| [SkillRet v3](https://arxiv.org/abs/2605.05726v3) | 17,810 skills; 4,997 evaluation queries; 13.1-point improvement | Abstract reports 16,129 skills, 4,392 evaluation queries, 63,259 training samples, 12.9-point NDCG@10 gain over strongest prior retriever. Pin dataset revision and hashes; never hard-code counts from prose. |
| [SkillRouter v5](https://arxiv.org/abs/2603.22455v5) | Hiding bodies costs 31–44 points | Abstract reports 37–44 points in its benchmark. This motivates testing body-aware retrieval; it does not forecast Magicite's gain. |
| [SkillsBench v4](https://arxiv.org/abs/2602.12670v4) | 87 tasks, 18 configurations, 33.9%→50.5% | Abstract corroborates these figures. Use paired task verification, not these numbers as a Magicite release target. |
| [SWE-Skills-Bench v1](https://arxiv.org/abs/2603.15401v1) | 49 skills, about 565 tasks, 39 no-improvement skills | Abstract corroborates these figures and version-mismatch risk. Compatibility filtering receives its own slice. |
| [SkillLearnBench](https://arxiv.org/abs/2604.20087), [memory study](https://arxiv.org/abs/2604.27003) | Evidence motivates caution about adaptation | Primary landing pages resolve. Their detailed experiments were not re-audited here; no new numerical threshold is derived from them. |
| [MCP tools, 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) | Treat annotations as hints | Current page explicitly describes annotations as untrusted absent trusted-server provenance. Test a pinned protocol/SDK matrix; do not assume SDK support from a version range. |
| [SLSA 1.2](https://slsa.dev/spec/v1.2/) | Provenance and supply-chain controls | Versioned specification resolves. Artifact verification is a release evidence gate; no SLSA level certification is claimed by this plan. |

The other security links in the dossier remain context, not independently re-audited findings. Threat tests in S04/S11/S12 derive from concrete product boundaries and inspected code. Proposed numeric budgets, retention and statistical margins are local design choices in contracts.md/evaluation.md, not research facts.

## Gaps resolved as specification decisions

- Inline `.egr.md` stays the default; external resources are optional and content-addressed.
- Compatibility has explicit version schemes and fail-closed unknown required context.
- Stable routing is isolated before optional learning infrastructure; learning efficacy cannot hold GA hostage.
- Durable evidence uses a separate authority and explicit checkpoint, preserving hot-path write restrictions.
- Signature integrity, source authenticity, content review, compatibility, efficacy and local routing permission remain distinct.
- Delivery uses build/merge dependency gates and explicit shared-file owners; no elapsed-day commitments.

## Evidence still required from execution

[GAP] Current test/benchmark results were inspected, not rerun. Published package availability, image signatures, host support, runtime crash safety and external adoption are unverified here. S01/S11–S16 produce those artifacts. Their absence blocks release claims, not delivery of this specification packet.
