# Bounded hardening acceptance

### AC-BH-01 (unwanted-behavior)
GIVEN enrollment may have committed before its reply was lost
WHEN the operator repeats enrollment and receives duplicate-enrollment rejection
THEN the error SHALL give truthful actionable read-only verification guidance without asserting that the enrollment never happened
VERIFY: custody_admin repeated/lost-reply UX regression with sanitized error assertions

### AC-BH-02 (ubiquitous)
THEN low-level duplicate enrollment SHALL remain fail-closed with exactly one enrollment effect
VERIFY: existing test_th_retry_matrix.py::test_genesis_lost_enroll_reply_exact_retry_has_one_effect and test_custody_admin.py repeat-enrollment rejection

### AC-BH-03 (unwanted-behavior)
GIVEN custody authority has advanced after genesis
WHEN enrollment ambiguity guidance is produced
THEN matching current policy SHALL not be presented as proof of an exact original genesis enrollment
VERIFY: advanced-authority regression or reviewed error path proving it makes no such success claim

### AC-BH-04 (unwanted-behavior)
GIVEN an MCP export destination escaping project root through an absolute path, parent traversal or symlink
WHEN the actual MCP export tool is invoked
THEN it SHALL return the existing typed containment failure
VERIFY: MCP entrypoint parameterized absolute/parent/symlink regression

### AC-BH-05 (unwanted-behavior)
GIVEN an escaping MCP export destination
WHEN export is rejected
THEN files outside the project SHALL remain unchanged
VERIFY: outside-directory byte/entry canary snapshots across the escape matrix

### AC-BH-06 (event-driven)
GIVEN a valid in-project export destination
WHEN the MCP export tool is invoked
THEN the existing successful export contract SHALL still hold
VERIFY: existing or focused MCP in-project export test

### AC-BH-07 (event-driven)
GIVEN bind_inspect and another binding module are imported in different fresh-process orders
WHEN generated reference checks run
THEN unchanged runtime tool metadata SHALL match the current generated snapshot in both orders
VERIFY: two fresh-process import-order regressions including bind_inspect before check_generated_docs

### AC-BH-08 (unwanted-behavior)
GIVEN a tool metadata or schema field differs from its generated snapshot
WHEN the reference check runs
THEN it SHALL report runtime reference drift
VERIFY: changed-metadata/schema negative control while retaining sixteen unique tool assertions

### AC-BH-09 (event-driven)
GIVEN fetch-model and scanner CI source at the tested candidate
WHEN F16/F17 assessment is written
THEN it SHALL distinguish verified behavior, missing evidence, risk implications and next action without declaring human acceptance or SECURITY PASS
VERIFY: independent source-to-claim review of bounded-hardening-assessment.md

### AC-BH-10 (ubiquitous)
THEN existing release evidence and primary checkout changes SHALL remain unchanged
VERIFY: scoped base-to-head diff plus primary checkout preservation check
