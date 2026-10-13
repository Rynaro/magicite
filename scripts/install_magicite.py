#!/usr/bin/env python3
"""Install Magicite and finish protected custody, model/index and harness setup.

Runs as the nonroot harness user. The packaged installer requests sudo only for
reviewed protected provisioning. --accept-default-policy approves the displayed
baseline TrustPolicy/1 (roots=[]); existing authority is never reenrolled.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--harness", choices=("all", "claude", "codex", "opencode"), default="all")
    parser.add_argument("--package", default="magicite")
    parser.add_argument("--accept-default-policy", action="store_true")
    args = parser.parse_args()
    root = Path(args.project_root).expanduser().resolve()
    if sys.platform not in ("darwin", "linux"):
        parser.error("automated custody requires macOS launchd or Linux systemd")
    if os.getuid() == 0:
        parser.error("run as your actual nonroot harness user; custody provisioning requests sudo")
    if not root.is_dir():
        parser.error("project root must already exist")
    target = root / ".magicite" / "venv"
    if any(path.is_symlink() for path in (root / ".magicite", target)):
        parser.error("refusing symlinked installation directory")
    venv.create(target, with_pip=True)
    python = target / "bin/python"
    assets = root / ".magicite/install-wheels"
    if assets.is_symlink():
        parser.error("refusing symlinked installation assets")
    assets.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="build-", dir=assets) as temporary:
        subprocess.run(
            [str(python), "-m", "pip", "wheel", "--no-deps", "--wheel-dir", temporary, args.package],
            check=True,
        )
        wheels = list(Path(temporary).glob("magicite-*.whl"))
        if len(wheels) != 1:
            parser.error("package must produce exactly one nominated Magicite wheel")
        wheel = assets / wheels[0].name
        if wheel.is_symlink():
            parser.error("refusing symlinked package wheel")
        shutil.copyfile(wheels[0], wheel)
        wheel = wheel.resolve()
    subprocess.run([str(python), "-m", "pip", "install", str(wheel)], check=True)
    command = [
        str(python),
        "-m",
        "magicite.install",
        "--project-root",
        str(root),
        "--package-wheel",
        str(wheel),
        "--harness",
        args.harness,
    ]
    if args.accept_default_policy:
        command.append("--accept-default-policy")
    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
