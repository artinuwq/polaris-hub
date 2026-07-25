from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from polaris.infra.settings import Settings
from polaris.update.installer import Installer
from polaris.update.manifest import UpdateResult, UpdateStatus
from polaris.update.provider import UpdateProvider
from polaris.update.rollback import RollbackManager


def resolve_repo_dir(settings: Settings) -> Path:
    if settings.update_repo_dir:
        return Path(settings.update_repo_dir).expanduser().resolve()

    here = Path(__file__).resolve()
    # .../polaris/src/polaris/update/manager.py → try polaris/ then repo root
    candidates = [
        here.parents[3],  # polaris/
        here.parents[4],  # repo root (polaris-hub/)
        Path.cwd(),
    ]
    for candidate in candidates:
        if (candidate / ".git").exists():
            return candidate
        if (candidate / "polaris" / ".git").exists():
            return candidate / "polaris"
    return Path.cwd()


class UpdateManager:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()
        self.repo_dir = resolve_repo_dir(self.settings)
        self.provider = UpdateProvider(
            repo_dir=self.repo_dir,
            remote=self.settings.update_remote,
            branch=self.settings.update_branch,
        )
        self.installer = Installer(self.repo_dir)
        state_file = self.repo_dir / "database" / "data" / ".update_previous_sha"
        if not state_file.parent.exists():
            nested = self.repo_dir / "polaris" / "database" / "data" / ".update_previous_sha"
            state_file = nested
        self.rollback = RollbackManager(state_file)

    def check(self) -> UpdateStatus:
        self.provider.ensure_repo()
        local = self.provider.current_sha()
        remote = self.provider.remote_sha()
        dirty = self.provider.is_dirty()
        up_to_date = local == remote
        if up_to_date:
            message = f"Уже актуально ({local[:7]}) на {self.settings.update_branch}"
        else:
            message = (
                f"Доступно обновление: {local[:7]} → {remote[:7]} "
                f"({self.settings.update_branch})"
            )
        if dirty:
            message += ". Есть локальные изменения — они будут сброшены при update."
        return UpdateStatus(
            branch=self.settings.update_branch,
            local_sha=local,
            remote_sha=remote,
            up_to_date=up_to_date,
            dirty=dirty,
            message=message,
        )

    def apply(self, force: bool = False) -> UpdateResult:
        status = self.check()
        if status.up_to_date and not force:
            return UpdateResult(
                success=True,
                message=status.message,
                previous_sha=status.local_sha,
                current_sha=status.local_sha,
            )

        previous = status.local_sha
        self.rollback.remember(previous)
        new_sha = self.provider.checkout_remote()
        self.installer.install()
        restarted = self._maybe_restart()
        message = f"Обновлено: {previous[:7]} → {new_sha[:7]}"
        if restarted:
            message += ". Сервис перезапускается."
        else:
            message += ". Перезапустите bot/web вручную, если нужно."
        return UpdateResult(
            success=True,
            message=message,
            previous_sha=previous,
            current_sha=new_sha,
            restarted=restarted,
        )

    def _maybe_restart(self) -> bool:
        service = self.settings.update_service_name
        if not service:
            return False
        systemctl = shutil.which("systemctl")
        if not systemctl:
            return False
        completed = subprocess.run(
            [systemctl, "restart", service],
            capture_output=True,
            text=True,
            check=False,
        )
        return completed.returncode == 0
