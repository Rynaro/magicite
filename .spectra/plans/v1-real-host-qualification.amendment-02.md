# A02 — prospective process-scoped configuration write protection

Authorization: /root explicitly approved the narrow reversible runner protection and unchanged AC01. No new model/user run until a separate checker reviews the committed implementation.

Evidence: disposable-only canary /private/tmp/magicite-v1-host/guard-canary-report.json, SHA256 6bb83ac6f05b4e0ad958f9d5a2acb7833616df4c36229defe7f03754cb5bc9fc. Reports read_allowed, ordinary_write_denied, atomic_replace_denied, child_write_denied, unrelated_write_allowed and canary_bytes_unchanged true; exit0. This is mechanism feasibility, not evidence of a protected authenticated host session. It did not access real configuration contents, credentials, keychain or models.

Execution addition: guard only existing monitored configuration/settings/MCP targets (lexical and resolved where applicable), including auth-readiness comparison children; first require disposable canary success; preserve stat verification; fail closed if guard unavailable, host cannot tolerate denial, or preservation cannot be shown. Independent implementation review precedes live use. No file chmod/restoration, credential copying, persistent configuration changes or broad network/IPC sandbox changes.

Criteria unchanged: 5d6894e8c254e85be0ae36de6409b32d2c9f585b94599b0fe8a69b8a95cb46c4. Attempt3 at f499dd2 remains AC01 UNEVALUATED. No retrospective waiver. All remaining host-proof, tool causality, protocol-mode, privacy and candidate-binding requirements remain exact. Source scope unchanged (runner/tests already declared).
