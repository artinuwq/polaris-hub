from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from polaris.shared.exceptions import PolarisError


class InstallError(PolarisError):
    """Ошибка установки зависимостей после обновления."""


class Installer:
    def __init__(self, repo_dir: Path) -> None:
        self.repo_dir = repo_dir

    def install_requirements(self) -> None:
        requirements = self.repo_dir / "requirements.txt"
        if not requirements.exists():
            # deployed layout: polaris/requirements.txt inside monorepo
            nested = self.repo_dir / "polaris" / "requirements.txt"
            requirements = nested if nested.exists() else requirements

        if not requirements.exists():
            return

        completed = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(requirements)],
            cwd=requirements.parent,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise InstallError(detail or "pip install failed")

    def install(self) -> None:
        self.install_requirements()
