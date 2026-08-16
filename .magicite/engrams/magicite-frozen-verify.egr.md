---
spec: engram/0.2
name: magicite-frozen-verify
id: egr_30f52c01
version: 1
provenance: authored
intent:
  does: "Run the exact frozen verification command this project gates every change on, and read its four independent gates correctly"
  use_when: "about to declare a Magicite change done, or CI disagrees with a local run about whether the tree is green"
  not_when: "a single named test is failing and you are still isolating the cause — run that test directly instead"
triggers:
  positive:
    - "run the frozen verify command for magicite"
    - "what gates must pass before a magicite change is done"
    - "magicite coverage gate cov-fail-under 70"
    - "confirm magicite still reports sixteen tools"
  negative:
    - "one magicite test fails and i am debugging it"
context_affinity: [magicite, ci, verification, quality-gate]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: []
yields: [green-verification-run]
composes: []
inhibits: []
provenance_journal:
  - version: 1
    timestamp: "2026-08-15T00:00:00Z"
    author: "claude-orchestrator"
    event: authored
    note: "First-party dogfood registry (change magicite-dogfoods-itself, AC-D1)"
trust:
  origin: authored
  verification_status: pending
---
## Procedure
1. Run the four gates in order and stop at the first failure: `uv run ruff check .`, then `uv run mypy src`, then `uv run pytest -q --cov=src/magicite --cov-fail-under=70`, then `uv run pytest -q -m acceptance`.
2. Run the fifth gate separately because it is a surface assertion rather than a test: `uv run magicite tools` must report exactly sixteen tools. The tool count is a frozen contract, not an incidental number.
3. Treat the coverage floor as a floor and not a target. The suite currently sits far above 70%, so a run that lands near the threshold means coverage was lost, even though the gate passes.
4. Run the acceptance marker pass on its own even though those tests also run in the main pass, because it is the gate that maps to spec acceptance criteria and it is the one worth reading test-by-test when it fails.
5. In a fresh worktree, run `uv sync --all-extras` first. A worktree inherits the branch but not the virtualenv, and a missing dev extra shows up as pytest not being found rather than as a test failure.
6. Never weaken an assertion to make this pass. The frozen verify exists so that a change is measured against the same bar every time; adjusting the bar to fit the change destroys the only comparison it offers.

## Pitfalls
- (x1) Running pytest without the coverage flags locally and then being surprised by CI. The coverage gate is part of the command, not an optional extra.
- (x1) Forgetting the tool-count check because it is not a pytest gate. It is the cheapest possible regression detector for the MCP surface and it is easy to skip.
- (x1) Running in a worktree with an unsynced virtualenv and misreading a spawn failure as a broken test suite.
- (x1) Assuming the container is covered by this command. It is not; the container path is exercised by its own docker acceptance tests.

## Examples
+ "am I allowed to call this change done" -> run all five gates, steps 1 and 2
+ "pytest is not found in my new worktree" -> step 5
- "test_checkpoint_persists_provenance_journal is failing and I want to know why" -> NOT this engram (run that test directly)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the dogfood registry
