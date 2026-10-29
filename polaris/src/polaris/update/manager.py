from __future__ import annotations

import json
import shutil
import subprocess
import time
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
        self.notice_file = state_file.parent / ".update_notice.json"

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

    def apply(self, force: bool = False, restart: bool = True) -> UpdateResult:
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
        if not restart:
            # вызывающий сам сообщит пользователю и перезапустит сервис отдельным шагом
            return UpdateResult(
                success=True,
                message=f"Обновлено: {previous[:7]} → {new_sha[:7]}",
                previous_sha=previous,
                current_sha=new_sha,
            )
        restarted, _ = self.restart_service()
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

    # ── сообщение «обновлено и запущено» после перезапуска сервиса ──
    def save_notice(self, chat_id: int, message_id: int, sha: str) -> None:
        self.notice_file.parent.mkdir(parents=True, exist_ok=True)
        self.notice_file.write_text(
            json.dumps({"chat_id": chat_id, "message_id": message_id, "sha": sha, "at": time.time()}),
            encoding="utf-8",
        )

    def clear_notice(self) -> None:
        try:
            self.notice_file.unlink()
        except FileNotFoundError:
            pass

    def pop_notice(self, max_age: int = 900) -> dict | None:
        """Забирает сохранённое уведомление (и удаляет файл). Старше max_age секунд — игнорируем."""
        try:
            data = json.loads(self.notice_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return None
        self.clear_notice()
        if time.time() - float(data.get("at", 0)) > max_age:
            return None
        return data

    def restart_service(self) -> tuple[bool, str]:
        service = self.settings.update_service_name
        if not service:
            return False, "UPDATE_SERVICE_NAME не задан"
        systemctl = shutil.which("systemctl")
        if not systemctl:
            return False, "systemctl не найден"
        completed = subprocess.run(
            [systemctl, "restart", service],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or f"exit code {completed.returncode}").strip()
            return False, f"Не удалось перезапустить {service}: {detail}"
        # ждём чуть-чуть и проверяем, что сервис реально активен
        time.sleep(2)
        check = subprocess.run(
            [systemctl, "is-active", service],
            capture_output=True,
            text=True,
            check=False,
        )
        if check.returncode == 0 and check.stdout.strip() == "active":
            return True, f"Сервис {service} перезапущен и активен"
        # если не активен — пробуем посмотреть статус для диагностики
        status = subprocess.run(
            [systemctl, "status", service, "--no-pager", "-n", "20"],
            capture_output=True,
            text=True,
            check=False,
        )
        detail = (status.stdout or status.stderr or f"exit code {check.returncode}").strip()
        return False, f"Сервис {service} не активен после рестарта: {detail}"
