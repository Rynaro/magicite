# Install Magicite

Magicite **1.0.0** is published on [PyPI](https://pypi.org/project/magicite/1.0.0/),
as wheel/source artifacts on [GitHub Releases](https://github.com/Rynaro/magicite/releases/tag/v1.0.0),
and as a signed OCI image at `ghcr.io/rynaro/magicite:1.0.0`. For a standard
install, use the pinned PyPI package:

```sh
MAGICITE_HOME="$HOME/.local/share/magicite"
mkdir -p "$MAGICITE_HOME"
python3.12 -m venv "$MAGICITE_HOME/venv-1.0.0"
"$MAGICITE_HOME/venv-1.0.0/bin/python" -m pip install 'magicite==1.0.0'
"$MAGICITE_HOME/venv-1.0.0/bin/python" -m pip check
"$MAGICITE_HOME/venv-1.0.0/bin/magicite" --version
```

Expect version `1.0.0`. For GitHub artifact provenance verification, continue
with the wheel and attestation procedure below. The
[PyPI publication receipt](https://github.com/Rynaro/magicite/releases/download/v1.0.0/pypi-publication-receipt.json)
records that channel's release evidence. These distribution channels do not
qualify runtime security, retrieval quality, or deployment profiles.

Use Python 3.11 or 3.12 with `venv` and `pip`, `curl`, and GitHub CLI (`gh`) for
provenance verification. Linux protected custody is the supported runtime
profile. macOS production custody, scale performance, retrieval quality, and
independent security certification are unqualified.

## Download and verify

Run as the account that will run your MCP client. Keep the environment at a
permanent location: MCP configuration points directly to its executable.

```sh
MAGICITE_HOME="$HOME/.local/share/magicite"
mkdir -p "$MAGICITE_HOME/downloads"
curl --fail --location \
  'https://github.com/Rynaro/magicite/releases/download/v1.0.0/magicite-1.0.0-py3-none-any.whl' \
  --output "$MAGICITE_HOME/downloads/magicite-1.0.0-py3-none-any.whl"
curl --fail --location \
  'https://github.com/Rynaro/magicite/releases/download/v1.0.0/release-manifest.json' \
  --output "$MAGICITE_HOME/downloads/release-manifest.json"
gh attestation verify "$MAGICITE_HOME/downloads/magicite-1.0.0-py3-none-any.whl" \
  --repo Rynaro/magicite --signer-workflow Rynaro/magicite/.github/workflows/release.yml
python3.12 - "$MAGICITE_HOME/downloads" <<'VERIFY'
import hashlib
import json
import pathlib
import sys
root = pathlib.Path(sys.argv[1])
manifest = json.loads((root / 'release-manifest.json').read_text())
assert manifest['schema'] == 'magicite/release-manifest/1'
assert manifest['version'] == '1.0.0'
wheel = root / 'magicite-1.0.0-py3-none-any.whl'
entries = [a for a in manifest['artifacts'] if a['name'] == wheel.name]
assert len(entries) == 1, 'Expected exactly one wheel entry'
assert hashlib.sha256(wheel.read_bytes()).hexdigest() == entries[0]['sha256'], 'Wheel checksum mismatch'
assert wheel.stat().st_size == int(entries[0]['bytes']), 'Wheel size mismatch'
print('Wheel manifest verified')
VERIFY
```

Proceed only if **both** attestation and manifest verification succeed. The
manifest alone is a consistency check, not proof of origin. Verification binds
the wheel to the repository's release workflow; it does not certify runtime
security or retrieval quality. Python 3.11 users can substitute `python3.11` in
these commands. No final artifact hash is asserted before publication.

## Install and check

```sh
python3.12 -m venv "$MAGICITE_HOME/venv"
"$MAGICITE_HOME/venv/bin/python" -m pip install \
  "$MAGICITE_HOME/downloads/magicite-1.0.0-py3-none-any.whl"
"$MAGICITE_HOME/venv/bin/magicite" --version
"$MAGICITE_HOME/venv/bin/magicite" --help
```

Expect version `1.0.0`. Dependencies may be acquired over the network. Record the
permanent absolute executable path, for example
`/home/alice/.local/share/magicite/venv/bin/magicite`.

`magicite doctor --project-root /absolute/path/to/project` may exit nonzero for
missing protected custody or an unfetched model. Complete [Setup](setup.md) as
administrator, then use [Quick Setup](quick-setup.md) for project connection.
Keep autonomous approval disabled and use supported runtime/restore procedures
in [Operations](operations.md). The OCI image does not provision independent
protected custody or establish a production deployment by itself; see the
[release notes](releases/1.0.0.md) for its channel and limitations.
