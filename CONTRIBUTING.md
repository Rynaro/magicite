# Contributing

V1 changes preserve dense-v1 stability, generated runtime references and frozen
acceptance criteria. Keep maker and checker distinct. Run the relevant tests plus:

```sh
uv run python scripts/check_generated_docs.py
uv run python scripts/check_docs.py
uv run python scripts/smoke_operator_tutorial.py --output /tmp/tutorial.json
```

Regenerate documented inventories with `check_generated_docs.py --write` only
when the runtime change is intentional. Quantitative README claims require a
supported Claim/1 bundle in `docs/readme-claims.json`; historical or structural
evidence cannot substitute for independent efficacy. A fixture pass does not
certify an incomplete release criterion. Preserve superseded evidence and use
explicit amendments for frozen-contract changes. Do not publish artifacts or
activate policies merely because mechanical checks passed.

Maintainers decide whether a reviewed change may merge, alter supported scope,
or proceed toward release. Authors implement and provide reproducible evidence;
an independent reviewer checks the exact commit and records approval or findings.
Authors do not approve their own changes. Neither agent review nor fixture smoke
substitutes for the independent external validation required by release gates.

For a public-contract, governance or support change, open an RFC describing the
problem, proposed replacement, compatibility/deprecation impact and evidence plan.
Link the relevant frozen criterion; record alternatives and the maintainer's
explicit accept/reject decision in a versioned decision record. A changed frozen
contract needs an accepted amendment before implementation. Keep an unaccepted
proposal labeled PROPOSED; do not silently turn it into runtime authority.
