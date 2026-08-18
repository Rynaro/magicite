# Required repository protection for 0.3

`main` and `v*` release tags are governed surfaces. Before merging the 0.3
candidate, configure the GitHub ruleset to require:

- pull requests for all changes, including administrators;
- at least one approving review from someone other than the change author;
- current `lint-type-test` and `docker-smoke` checks;
- dismissal of stale approvals after new commits;
- resolved review conversations;
- linear history and signed release tags;
- blocked force-pushes and deletions.

Repository settings are external state and must be verified through GitHub;
this file is the reviewable contract, not proof that the ruleset is active.

