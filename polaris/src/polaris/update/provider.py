from __future__ import annotations

import subprocess
from pathlib import Path

from polaris.shared.exceptions import PolarisError


class GitError(PolarisError):
    """Ошибка выполнения git-команды."""


class UpdateProvider:
    """Провайдер обновлений из git-ветки."""

    def __init__(self, repo_dir: Path, remote: str = "origin", branch: str = "main") -> None:
        self.repo_dir = repo_dir
        self.remote = remote
        self.branch = branch

    def _run(self, *args: str, check: bool = True) -> str:
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise GitError("git не найден в PATH") from exc

        if check and completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise GitError(detail or f"git {' '.join(args)} failed")
        return (completed.stdout or "").strip()

    def ensure_repo(self) -> None:
        if not (self.repo_dir / ".git").exists():
            # allow repo root one level up when running from polaris/
            parent_git = self.repo_dir.parent / ".git"
            if parent_git.exists():
                self.repo_dir = self.repo_dir.parent
                return
            raise GitError(f"Не найден git-репозиторий в {self.repo_dir}")

    def current_sha(self) -> str:
        return self._run("rev-parse", "HEAD")

    def remote_sha(self) -> str:
        self._run("fetch", self.remote, self.branch)
        return self._run("rev-parse", f"{self.remote}/{self.branch}")

    def is_dirty(self) -> bool:
        return bool(self._run("status", "--porcelain", check=False))

    def get_release(self) -> str:
        return self.remote_sha()

    def checkout_remote(self) -> str:
        """Жёстко выровнять рабочую копию по remote/branch."""
        self._run("fetch", self.remote, self.branch)
        self._run("checkout", self.branch)
        self._run("reset", "--hard", f"{self.remote}/{self.branch}")
        return self.current_sha()
