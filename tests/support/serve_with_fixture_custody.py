"""Run the real ``magicite`` CLI in a child process attached to fixture custody.

Usage: ``serve_with_fixture_custody.py <project_root> <custody_dir> <registry_id> <cli args...>``.
The parent test must already have enrolled the simulated authority; this
launcher only attaches it, never enrolls or creates history. It does not
qualify an installed channel or distinct-UID deployment.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main() -> None:
    from magicite.__main__ import cli
    from tests.support.custody_adapter import attach_fixture

    project_root, custody_directory, registry_id, *cli_args = sys.argv[1:]
    with attach_fixture(Path(project_root), Path(custody_directory), registry_id):
        cli.main(args=cli_args, prog_name="magicite")


if __name__ == "__main__":
    main()
