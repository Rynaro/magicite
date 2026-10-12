# Bounded hardening assessment

Assessed integration base `c7e0789c07705f48131b0a0e0d0c19f2f68d28fe` on 2026-10-06.
This additive slice record covers F-14, F-18 and OI-12 fixes and source-grounded
F-16/F-17 assessment. Archived v1/r3 evidence describes its original candidate;
it is unchanged and is not evidence for this slice. PR #51 findings remain
unmerged provenance. Named human High-risk decisions and release approval remain
pending; this document grants no risk acceptance or SECURITY PASS.

## F-14: enrollment reply ambiguity

`src/magicite/core/custody_admin.py::enroll` still checks reviewed policy bytes
before calling the existing store. Errors during enrollment, authenticated current
head reading or output now explain that enrollment may already have committed,
advise read-only `custody status` with the installed protected profile and inspection
of protected authority history against reviewed inputs, and warn against reset or
re-enrollment as reply recovery. Status requires an explicitly installed enrollment
profile; it does not automatically provision one. Current policy cannot prove an
exact original genesis because authority or the requesting actor may differ.
No enrollment recovery or success inference is added. Low-level duplicate rejection
and genesis semantics in `trust_custodian.py` are unchanged. CLI fault injections
are same-account mechanics, not separate-UID deployment qualification.

## F-18: exposed export boundary

Actual MCP `export` dispatch tests exercise absolute, parent traversal and symlink
escape destinations and the existing `path_outside_project` error envelope. A
populated outside-directory byte/entry canary verifies no outside change. A valid
in-project control renders real imported skills. `_resolve_scan_root` remains
unchanged; `ensure_dirs` precedes this guard, so this slice makes no global
zero-write claim. Tests cover a static resolved-root boundary, not concurrent
symlink replacement or general filesystem race resistance.

## OI-12: generated-reference import ordering

The checker previously compared decorator registration arrival order. Fresh-process
binding permutations reproduced false `mcp_tools` drift. Checker-local canonical
comparison and deterministic `--write` ordering now compare complete tool rows
without modifying the public runtime manifest or tool registration. Name, metadata
and input/output-schema hash mutations still fail; exact sixteen-unique-tool and
documented inventory checks remain. The archived generated snapshot is unchanged.

## F-16: model acquisition remains UNEVALUATED and unrated

Verified source: `src/magicite/embeddings/fastembed_provider.py` defines
`BAAI/bge-small-en-v1.5`; `_ensure_model` passes model name, cache directory and
`local_files_only`; `fetch_model` passes name and cache to FastEmbed. Neither call
supplies an immutable revision or expected model-byte digest. `uv.lock` pins
FastEmbed package archives, which does not pin separately downloaded model bytes.
`Dockerfile` bakes the fetched cache: an image digest binds its resulting bytes,
without establishing an expected acquisition artifact in advance.

Missing evidence: no candidate-bound verified model revision/digest manifest or
independent downloaded-artifact qualification was established here. Mutable remote
model selection may impair reproducibility or supply-chain assurance. This is a
source observation, not a demonstrated exploit, severity rating or acceptance.
Next bounded action: evaluate explicit immutable revision and artifact digests, or
an offline verified model manifest, before changing acquisition behavior. No model
or dependency changes occur in this slice.

## F-17: scanner blocking policy remains UNEVALUATED and unrated

Verified source: `.github/workflows/ci.yml` generates all-severity Trivy SARIF with
exit code zero and uploads it; its separate HIGH/CRITICAL blocking step sets
`ignore-unfixed: true`. `.github/workflows/release.yml` uses the same split, with
the blocking step additionally excluded for `-rc` and `-beta` references. Thus
unfixed findings are excluded from blocking decisions; this does not establish
that they are hidden from the all-severity report.

Missing evidence: this assessment did not inspect actual candidate SARIF contents,
scanner database identity or accepted exception records. A green gate cannot prove
zero vulnerabilities or human acceptance of remaining risk. Next bounded action:
attach the full candidate SARIF and scanner/database/configuration provenance,
then decide an explicit unfixed-findings policy or individually reviewed exceptions.
No scanner policy or findings classification changes occur in this slice.
