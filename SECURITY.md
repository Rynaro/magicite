# Security policy

V1 integration has no third-party security certification. Critical findings must
be resolved. Every high exception requires a named reviewer, exploitability
assessment, mitigation and expiry; scanner failures block integration.

Preserve local evidence when reporting a vulnerability, but do not include raw
prompts, credentials or fingerprint keys in public reports. No private reporting
address is asserted here; use the repository's enabled private vulnerability
reporting channel when available, or request a private contact from a maintainer.

Keep fingerprint keys in independently controlled encrypted custody. The backup
API copies bytes and does not encrypt its destination. Current signed trust
bundles establish identity and integrity, not safety or efficacy. Trust-history
hardening remains a proposed human decision recorded in release evidence.

Stable public fields and commands remain for at least two minor releases and
90 days after a published deprecation notice, whichever is longer. Breaking
stable contracts require a major version. Experimental fields are explicitly
namespaced and excluded from stable guarantees. See [support policy](docs/support-policy.json).
