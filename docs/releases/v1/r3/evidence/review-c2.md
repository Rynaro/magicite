# Independent C2 incremental product review

Reviewer /root/verify_r3, 2026-10-05; read-only source authority, distinct from maker. Supersedes C1 product acceptance only after clean candidate binding; earlier reports preserved as history. No external/Gauge acceptance claimed.

Base C1: 6543f9dfbc7c49c77543462727ede62432895a5d. Reviewed stable incremental diff SHA256: 9cf1332876a3331aa3d17c0f76412f4f66b117d8c6605d3d36a4b1c2c776b285, checked before and after runs. Five files: registry.py, existing approval busy/ATLAS fixes/snapshot inventory tests, declared plan amendment.

Read C1 full-run failure evidence: candidate-evidence-6543f9d/pytest.txt (4 failed, 1736 passed, 9 skipped). The two benchmark failures expose nested ownership regression; CrossProcessLease.acquire's reentrant branch validates and yields the outer lease without setting nested wrapper ownership. Registry now asserts current_cross_process_lease (fallback wrapper for existing test adapter), retaining all three fences. No lease code changed.

Existing test adjustments are justified, not weakened expectations: (1) direct approval followed by rejection now explicitly requires old-event replay to fail stale, and then tests a genuinely new approval inside an outer writer lease; (2) AST authority inventory explicitly enumerates doctor custody snapshot with read-only reason, preserving the inventory gate. Atomic acceptance criteria unchanged.

Independent commands, CPython3.12.14 /private/tmp/magicite-r3-qualification/venv/bin/python, worktree cwd, `-m pytest -q -p no:cacheprovider`:

- Original focused4files plus tests/unit/core/test_trust_atlas_fixes.py, tests/unit/core/test_th_single_snapshot_sites.py, tests/unit/eval/test_s14_audit.py::test_authentic_manifest_ingests_full_bodies_and_rejects_tampering, tests/unit/eval/test_scale_unevaluated.py::test_matrix_synthetic_vs_manifest_corpus: exit0; independent-c2-focused.log records75passing checks, including original four full-suite failure sites.
- `-p tests.conftest /private/tmp/magicite-r3-qualification/test_independent_c2.py /private/tmp/magicite-r3-qualification/test_independent_review_final.py`: exit0; independent-c2-adversarial.log records7passing checks. Independently authored nested-owner-loss test injects loss after local admission and verifies BusyError without mirror/DB audit writes. Prior six corrupt-audit scenarios remain fail-closed.

Disposition: ACCEPT incremental repair for C2 freeze and fresh full supported-runtime qualification. No remaining blocker identified in bounded independent review. C1 full-run failure must remain historical, not usable as passing evidence. All package/source-dependent release claims await clean C2 plus fresh full-suite/package evidence and separate adjudication.
