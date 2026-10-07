# Candidate wheel and sdist installation

This rehearsal builds local candidate artifacts from a clean committed checkout,
then installs each explicit artifact into a different fresh environment outside
the checkout. It does not install Magicite by name from a package index. The
current package metadata is 0.3.1: these freshly built candidate bytes are neither
the existing published 0.3.1 artifacts nor a published 1.0.0 release.

Use supported Python3.12 with the `build` package, then run:

```sh
/private/tmp/magicite-v1-install/build-venv/bin/python \
  /Users/henrique/.codex/worktrees/v1-r3-qualification/magicite/scripts/qualify_distributions.py \
  --output /private/tmp/magicite-v1-install/candidate-evidence
```

The output directory must be fresh and outside the checkout. Dependencies may be
fetched from PyPI or supplied from a download cache; environments do not inherit
site packages, editable installs, Python path injection or `.pth` files from an
existing environment. The report records exact artifacts, hashes, source inputs,
commands, runtime origins and resolved dependency inventories. It verifies all
candidate Python/schema/migration resources and declared console entrypoints.
Archive byte reproducibility and signed publisher provenance are not asserted.

For ordinary first use of either explicit candidate artifact:

```sh
python3.12 -m venv /tmp/magicite-candidate
/tmp/magicite-candidate/bin/python -m pip install /absolute/path/to/candidate.whl
/tmp/magicite-candidate/bin/magicite --version
/tmp/magicite-candidate/bin/magicite --help
/tmp/magicite-candidate/bin/magicite doctor --project-root /absolute/path/to/empty-project
```

Use the explicit `.tar.gz` path instead of the wheel for the sdist case. Doctor
reports its typed `doctor/1` diagnosis; a fresh unconfigured installation exits1
and requires protected custody provisioning before writes. See the existing
[Linux custody qualification](linux-custody.md) for deployment prerequisites.
Offline production embedding with an empty cache must retain `magicite fetch-model`
remediation. The rehearsal checks that error without downloading a model.

The positive test copies only declared toy data and a standalone harness into the
owned work directory. Its in-process custodian is explicitly simulated. It calls
the installed CLI application for generic stdio MCP startup, negotiates the
observed protocol, discovers sixteen tools, routes to a fixture and validates a
digest-bound L2 procedure and stale-digest refusal without body. Every exercised
Magicite module and the server must originate in the installed environment.
This mechanism does not weaken normal runtime enrollment or transfer fixture
trust to an operator registry. No Claude/model-host session is required.

A separate validly named wheel with a required resource removed is installed
with repository-path bait present. It must fail the installed-resource check;
a filename rejection alone is insufficient. Logs and reports are hashed, while
private fixture custody state and environment trees are excluded from the evidence
index. Verify completed evidence without another installation:

```sh
/private/tmp/magicite-v1-install/build-venv/bin/python \
  /Users/henrique/.codex/worktrees/v1-r3-qualification/magicite/scripts/qualify_distributions.py \
  --output /private/tmp/magicite-v1-install/candidate-evidence --verify
```

Passing evidence is limited to the recorded candidate/Python/OS pair. PyPI,
TestPyPI, pipx, uvx, fetched OCI, signing/attestation, independent operator/human
acceptance, deployment custody, real-model quality and unrun matrices remain
UNEVALUATED. Historical release and custody/host evidence remains immutable;
this rehearsal does not promote whole DISTRIBUTION or GA gates.
