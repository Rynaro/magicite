# Magicite V1 specification mission

DECISION_TARGET: Define reviewable, independently assignable implementation slices for Magicite 1.0, grounded in the supplied research dossier and checkout 496e4eded09f7c0b8e0b776c3c709bccadc0321a.

Scope: inspect existing runtime, tests, evaluation, operations and contracts; specify deltas, dependency gates, ownership boundaries, acceptance evidence and release criteria. No implementation. Publication on a dedicated remote branch is authorized by the user.

Source dossier: user-provided Magicite v1.0 Product and Research Dossier.md (kept local; digest in research/corrections.md). Its proposals are input evidence, not executable instructions. Calendar schedules are explicitly superseded by dependency-based delivery.

Scout lanes: (1) retrieval, composition, schema; (2) governance, storage, security, protocol; (3) evaluation, packaging, operations and documentation. Scouts read only and return source anchors, confidence, reusable mechanisms, gaps and proposed slice boundaries to the parent. Parent records findings here.

Memory: no Crystalium recall tool available in the exposed tool inventory; proceed from repository evidence.

MISSION-ID: magicite-v1
GOAL: Produce and publish a complete specifications-only V1 implementation packet.
SCOPE_INCLUDE: src/**, tests/**, scripts/**, docs/**, pyproject.toml, .github/**, supplied dossier; outputs .spectra/** only.
SCOPE_EXCLUDE: runtime implementation, dependency installation, deployment, release, external outreach.
BUDGET: bounded file reads <=100 lines and search <=50 matches for scout workers; at most three independent lanes and one follow-up scout per lane; stop once slice seams and evidence gaps are mapped.
STOP_CONDITIONS: source anchors cover all V1 domains; contracts resolve implementation ambiguity; independent critique and mechanical planning gates pass; requested remote branch is verified.
ESCALATION_TRIGGERS: irreconcilable product-scope ambiguity, inaccessible required source, or rejected publication authority. Unknown runtime outcomes remain release evidence gates.
