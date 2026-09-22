"""The version the CLI prints is the version the package is built with."""

from __future__ import annotations

import tomllib
from pathlib import Path

from almanak_keeperhub import __version__


def test_version_matches_pyproject() -> None:
    pyproject = tomllib.loads((Path(__file__).parents[2] / "pyproject.toml").read_text())
    assert __version__ == pyproject["project"]["version"]
