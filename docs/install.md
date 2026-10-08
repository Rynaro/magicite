# Install Magicite

Install the published **1.0.0rc1 developer preview** with Python 3.12. This
native package is available from [GitHub Releases](https://github.com/Rynaro/magicite/releases/tag/preview/v1.0.0-rc.1).
The preview does not qualify full v1 GA, production macOS custody, or a v1
container channel. This procedure uses the wheel, not a PyPI package.

You need Python 3.12 with `venv` and `pip`, `curl`, and an MCP client. On Linux,
your administrator must also complete [Setup](setup.md) before project use.

## Download and verify

Run as the account that will run your MCP client. Keep the environment at a
permanent location: MCP configuration will point directly to its executable.

```sh
MAGICITE_HOME="$HOME/.local/share/magicite-preview"
mkdir -p "$MAGICITE_HOME/downloads"
curl --fail --location \
  'https://github.com/Rynaro/magicite/releases/download/preview/v1.0.0-rc.1/magicite-1.0.0rc1-py3-none-any.whl' \
  --output "$MAGICITE_HOME/downloads/magicite-1.0.0rc1-py3-none-any.whl"
python3.12 - "$MAGICITE_HOME/downloads/magicite-1.0.0rc1-py3-none-any.whl" <<'VERIFY'
import hashlib
import pathlib
import sys
expected = '3bdeec9ea288c3d317faa9eff7463b9dfac3182f246aa3393e6753c7d03415e9'
actual = hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest()
if actual != expected:
    raise SystemExit('Wheel checksum mismatch: stop before installing')
print('Wheel checksum verified')
VERIFY
```

Proceed only if verification succeeds. The pinned hash identifies this release
asset; it is not a signed provenance attestation.

## Install and check

```sh
python3.12 -m venv "$MAGICITE_HOME/venv"
"$MAGICITE_HOME/venv/bin/python" -m pip install \
  "$MAGICITE_HOME/downloads/magicite-1.0.0rc1-py3-none-any.whl"
"$MAGICITE_HOME/venv/bin/magicite" --version
"$MAGICITE_HOME/venv/bin/magicite" --help
```

Expect version `1.0.0rc1`. Package dependencies may be acquired over the network.
Record the absolute executable path, for example
`/home/alice/.local/share/magicite-preview/venv/bin/magicite`.
Do not point MCP at a temporary environment.

`magicite doctor --project-root /absolute/path/to/project` may exit nonzero on
an unconfigured project: missing protected custody or an unfetched model is a
setup diagnosis, not proof that wheel installation failed.

Next: [Setup](setup.md) for the Linux administrator, or
[Quick Setup](quick-setup.md) if custody has already been provisioned.
