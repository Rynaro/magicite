"""The installed CLI exposes its distribution metadata version eagerly."""

from importlib.metadata import version

from click.testing import CliRunner

from magicite.__main__ import cli


def test_cli_version_identifies_installed_distribution():
    result = CliRunner().invoke(cli, ["--version"], prog_name="magicite")
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == f"magicite, version {version('magicite')}"
