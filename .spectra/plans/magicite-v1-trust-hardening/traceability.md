# Traceability and single-writer ownership

| Additive criteria | Existing obligation / surface | Required change |
|---|---|---|
| TH-01–04 | C10; S04 trust decisions and policy/roots | New journal/custodian/profile modules; trust.py authenticated authority |
| TH-05–06 | C0/C9; S12 fencing | Existing lease composition with monotonic external generation; minimal centrally owned lease helper |
| TH-07 | S04, S07 router, S11 body gate | Validated sequence snapshot for every reader/writer; fresh pre-disclosure check |
| TH-08 | C8; S03 migration | Explicit zero-write preview and reviewed legacy reconciliation; no inferred virgin state |
| TH-09–10 | C8; S12 backup/restore | Authenticated retained suffix, epoch continuity, no direct mirror authority or unsafe downgrade (supported paths, TH-A01) |
| TH-11 | S12 doctor; S15 operations | Zero-write/redacted diagnosis, archive key exclusion, custody runbook |
| TH-12 | S16 TRUST/SECURITY/GA-ALL | Original exploit closure and separate honest deployment qualification |

Affected original criteria include AC-S04-03/04, AC-S11-01 and AC-S12-03/05; all
original criteria remain frozen and applicable. TRUST, PROTOCOL, RELIABILITY,
SECURITY and GA-ALL require fresh adjudication after implementation. Historical
S16 records remain immutable observations of their earlier source.

Sole writer owns the new modules/tests and centrally coordinates minimal changes to
trust.py, registry/router trust readers, backup.py, migration.py, doctor, config,
CLI, and the shared lease helper. Preserve evidence/policy domain authority except
necessary shared-guard compatibility. Disclose each existing C9 shared file and its
reason in the PR. No parallel writer is authorized; independent reviewers are
read-only and maker must differ from checker.

Action sequence: (1) adopted additive packet and independent design closure;
(2) acceptance anchors and explicit test custody; (3) bounded protected transport,
journal and fencing; (4) complete reader/writer, legacy/recovery/rotation integration;
(5) adversarial/crash/regression checks and operator documents; (6) independent
exact-head review, green CI and integration-owner merge. No elapsed-time deadline
or gate waiver is inferred from this sequence.
