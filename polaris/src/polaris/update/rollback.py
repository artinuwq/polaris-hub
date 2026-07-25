from __future__ import annotations

from pathlib import Path


class RollbackManager:
    """Простой откат к предыдущему SHA через git reset."""

    def __init__(self, state_file: Path) -> None:
        self.state_file = state_file

    def remember(self, sha: str) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(sha.strip() + "\n", encoding="utf-8")

    def previous_sha(self) -> str | None:
        if not self.state_file.exists():
            return None
        value = self.state_file.read_text(encoding="utf-8").strip()
        return value or None

    def rollback(self, provider) -> str:
        sha = self.previous_sha()
        if not sha:
            raise RuntimeError("Нет сохранённого SHA для отката")
        provider._run("reset", "--hard", sha)
        return provider.current_sha()
