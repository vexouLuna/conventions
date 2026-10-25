from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from conventions import __version__, config, synced

PIN = f"v{__version__}"

CI_YML = f"""\
name: ci
on:
  push:
    branches: [main]
  pull_request:
jobs:
  conventions:
    uses: vexouLuna/conventions/.github/workflows/conventions-check.yml@{PIN}
    secrets:
      CONVENTIONS_PRIVATE_PATTERNS: ${{{{ secrets.CONVENTIONS_PRIVATE_PATTERNS }}}}
  ci:
    uses: vexouLuna/conventions/.github/workflows/python-ci.yml@{PIN}
"""

PYPROJECT = """\
[project]
name = "sample"
version = "0.1.0"
requires-python = ">=3.13"

[tool.ruff]
extend = ".conventions/ruff.toml"

[tool.mypy]
strict = true
"""


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A python-lib repo that has adopted the conventions and should pass the check."""
    root = tmp_path / "sample"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "test")
    (root / config.CONFIG_NAME).write_text(config.render(PIN, "python-lib", "trunk"), encoding="utf-8")
    (root / ".python-version").write_text("3.13\n", encoding="utf-8")
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (root / ".github/workflows").mkdir(parents=True)
    (root / ".github/workflows/ci.yml").write_text(CI_YML, encoding="utf-8")
    synced.sync(root, config.load(root))
    git(root, "add", "-A")
    return root
