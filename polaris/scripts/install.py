#!/usr/bin/env python3
"""Интерактивная установка Polaris одной командой.

Пример:
  python scripts/install.py
  python scripts/install.py --repo https://github.com/you/polaris-hub.git
"""

from __future__ import annotations

import argparse
import getpass
import os
import platform
import secrets
import shutil
import subprocess
import sys
import venv
from pathlib import Path

DEFAULT_INSTALL_DIR_LINUX = Path("/opt/polaris")
DEFAULT_BRANCH = "main"
DEFAULT_WEB_HOST = "0.0.0.0"
DEFAULT_WEB_PORT = "8000"
SERVICE_NAME = "polaris"


class InstallError(RuntimeError):
    pass


def info(msg: str) -> None:
    print(f"→ {msg}")


def ok(msg: str) -> None:
    print(f"✓ {msg}")


def warn(msg: str) -> None:
    print(f"! {msg}")


def ask(prompt: str, default: str | None = None, secret: bool = False) -> str:
    suffix = f" [{default}]" if default not in (None, "") else ""
    full = f"{prompt}{suffix}: "
    while True:
        if secret:
            value = getpass.getpass(full)
        else:
            value = input(full).strip()
        if not value and default is not None:
            return default
        if value:
            return value
        print("  Нужно указать значение.")


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    value = input(f"{prompt} [{hint}]: ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes", "д", "да"}


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    info("$ " + " ".join(cmd))
    completed = subprocess.run(cmd, cwd=cwd, check=False, text=True)
    if check and completed.returncode != 0:
        raise InstallError(f"Команда завершилась с кодом {completed.returncode}: {' '.join(cmd)}")
    return completed


def ensure_python() -> None:
    if sys.version_info < (3, 10):
        raise InstallError(f"Нужен Python 3.10+, сейчас {sys.version.split()[0]}")
    ok(f"Python {sys.version.split()[0]}")


def find_app_root(start: Path) -> Path | None:
    """Найти корень приложения (где scripts/install.py и src/polaris)."""
    candidates = [start, start / "polaris"]
    for path in candidates:
        if (path / "scripts" / "install.py").exists() and (path / "src" / "polaris").exists():
            return path.resolve()
    # walk up
    for parent in [start, *start.parents]:
        if (parent / "scripts" / "install.py").exists() and (parent / "src" / "polaris").exists():
            return parent.resolve()
        nested = parent / "polaris"
        if (nested / "scripts" / "install.py").exists() and (nested / "src" / "polaris").exists():
            return nested.resolve()
    return None


def git_available() -> bool:
    return shutil.which("git") is not None


def clone_repo(repo_url: str, target: Path, branch: str) -> Path:
    if target.exists() and any(target.iterdir()):
        found = find_app_root(target)
        if found:
            ok(f"Репозиторий уже есть: {found}")
            return found
        raise InstallError(f"Каталог {target} не пуст и не похож на Polaris")

    target.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", "--branch", branch, "--single-branch", repo_url, str(target)])
    found = find_app_root(target)
    if not found:
        raise InstallError("После clone не найден каталог приложения Polaris")
    return found


def create_venv(app_root: Path) -> Path:
    venv_dir = app_root / ".venv"
    if not venv_dir.exists():
        info(f"Создаю venv: {venv_dir}")
        venv.EnvBuilder(with_pip=True).create(venv_dir)
    else:
        ok(f"venv уже есть: {venv_dir}")

    if platform.system() == "Windows":
        python = venv_dir / "Scripts" / "python.exe"
        pip = venv_dir / "Scripts" / "pip.exe"
    else:
        python = venv_dir / "bin" / "python"
        pip = venv_dir / "bin" / "pip"

    run([str(python), "-m", "pip", "install", "--upgrade", "pip"])
    run([str(pip), "install", "-r", str(app_root / "requirements.txt")], cwd=app_root)
    ok("Зависимости установлены")
    return python


def write_env(app_root: Path, values: dict[str, str]) -> Path:
    env_path = app_root / ".env"
    lines = [
        f"APP_NAME={values['APP_NAME']}",
        f"DEBUG={values['DEBUG']}",
        f"DATABASE_URL={values['DATABASE_URL']}",
        "",
        f"TELEGRAM_BOT_TOKEN={values['TELEGRAM_BOT_TOKEN']}",
        f"TELEGRAM_ADMIN_IDS={values['TELEGRAM_ADMIN_IDS']}",
        "TELEGRAM_WEBHOOK_URL=",
        "",
        f"UPDATE_BRANCH={values['UPDATE_BRANCH']}",
        f"UPDATE_REMOTE={values['UPDATE_REMOTE']}",
        f"UPDATE_REPO_DIR={values['UPDATE_REPO_DIR']}",
        f"UPDATE_SERVICE_NAME={values['UPDATE_SERVICE_NAME']}",
        f"UPDATE_API_TOKEN={values['UPDATE_API_TOKEN']}",
        "",
        f"WEB_HOST={values['WEB_HOST']}",
        f"WEB_PORT={values['WEB_PORT']}",
        "",
    ]
    env_path.write_text("\n".join(lines), encoding="utf-8")
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass
    ok(f"Конфиг записан: {env_path}")
    return env_path


def ensure_data_dirs(app_root: Path) -> None:
    for relative in ("database/data", "database/migrations"):
        path = app_root / relative
        path.mkdir(parents=True, exist_ok=True)
    ok("Каталоги данных готовы")


def render_unit(template: Path, replacements: dict[str, str]) -> str:
    text = template.read_text(encoding="utf-8")
    for key, value in replacements.items():
        text = text.replace(key, value)
    return text


def install_systemd(app_root: Path, venv_python: Path, user: str) -> None:
    if platform.system() != "Linux":
        warn("systemd доступен только на Linux — пропускаю")
        return
    if os.geteuid() != 0:
        warn("Нет root — systemd unit не установлен. Запустите install с sudo или поставьте сервис вручную.")
        return
    if not shutil.which("systemctl"):
        warn("systemctl не найден — пропускаю")
        return

    group = user
    try:
        import grp

        group = grp.getgrgid(os.getpwnam(user).pw_gid).gr_name  # type: ignore[name-defined]
    except Exception:
        pass

    # ownership
    run(["chown", "-R", f"{user}:{group}", str(app_root)], check=False)

    replacements = {
        "ROOT_USER": user,
        "ROOT_GROUP": group,
        "INSTALL_DIR": str(app_root),
        "VENV_PYTHON": str(venv_python),
    }
    unit_src = app_root / "services" / "polaris.service"
    unit_text = render_unit(unit_src, replacements)
    unit_dst = Path("/etc/systemd/system/polaris.service")
    unit_dst.write_text(unit_text, encoding="utf-8")
    ok(f"Unit записан: {unit_dst}")

    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", SERVICE_NAME])
    ok("Сервис polaris включён и запущен")


def write_windows_starter(app_root: Path, venv_python: Path) -> None:
    bat = app_root / "start_polaris.bat"
    bat.write_text(
        "\r\n".join(
            [
                "@echo off",
                f'cd /d "{app_root}"',
                f'"{venv_python}" scripts\\run_all.py',
                "pause",
                "",
            ]
        ),
        encoding="utf-8",
    )
    ok(f"Стартер для Windows: {bat}")


def run_doctor(app_root: Path, venv_python: Path) -> None:
    doctor = app_root / "scripts" / "doctor.py"
    if doctor.exists():
        run([str(venv_python), str(doctor)], cwd=app_root, check=False)


def collect_config(app_root: Path, branch: str) -> dict[str, str]:
    print()
    print("=== Основные настройки ===")
    print("Остальное будет заполнено значениями по умолчанию.")
    print()

    token = ask("Токен Telegram-бота (от @BotFather)", secret=True)
    admin_ids = ask("Ваш Telegram user id (узнать: @userinfobot)")

    # optional overrides with defaults
    print()
    if ask_yes_no("Оставить остальные настройки по умолчанию?", default=True):
        web_host = DEFAULT_WEB_HOST
        web_port = DEFAULT_WEB_PORT
        update_branch = branch
    else:
        web_host = ask("WEB_HOST", DEFAULT_WEB_HOST)
        web_port = ask("WEB_PORT", DEFAULT_WEB_PORT)
        update_branch = ask("UPDATE_BRANCH", branch)

    api_token = secrets.token_urlsafe(24)
    repo_dir = str(app_root)
    # if .git is parent (monorepo), point UPDATE_REPO_DIR there
    if not (app_root / ".git").exists() and (app_root.parent / ".git").exists():
        repo_dir = str(app_root.parent.resolve())

    return {
        "APP_NAME": "polaris",
        "DEBUG": "false",
        "DATABASE_URL": "sqlite:///database/data/polaris.db",
        "TELEGRAM_BOT_TOKEN": token,
        "TELEGRAM_ADMIN_IDS": admin_ids,
        "UPDATE_BRANCH": update_branch,
        "UPDATE_REMOTE": "origin",
        "UPDATE_REPO_DIR": repo_dir,
        "UPDATE_SERVICE_NAME": SERVICE_NAME if platform.system() == "Linux" else "",
        "UPDATE_API_TOKEN": api_token,
        "WEB_HOST": web_host,
        "WEB_PORT": web_port,
    }


def print_summary(app_root: Path, values: dict[str, str]) -> None:
    print()
    print("=== Готово ===")
    print(f"Каталог:     {app_root}")
    print(f"Админ ID:    {values['TELEGRAM_ADMIN_IDS']}")
    print(f"Ветка:       {values['UPDATE_BRANCH']}")
    print(f"API token:   {values['UPDATE_API_TOKEN']}")
    print(f"Web:         http://{values['WEB_HOST']}:{values['WEB_PORT']}/")
    print()
    if platform.system() == "Linux":
        print("Команды:")
        print("  sudo systemctl status polaris")
        print("  sudo systemctl restart polaris")
        print("  journalctl -u polaris -f")
    else:
        print("Запуск:")
        print(f"  {app_root / 'start_polaris.bat'}")
        print("  или: .venv\\Scripts\\python scripts\\run_all.py")
    print()
    print("В боте: /start, /update, /status")
    print("В Mini App кнопка «Обновить» (токен уже в .env как UPDATE_API_TOKEN)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Установка Polaris")
    parser.add_argument("--repo", help="URL git-репозитория для clone")
    parser.add_argument("--dir", dest="install_dir", help="Каталог установки")
    parser.add_argument("--branch", default=DEFAULT_BRANCH, help="Ветка (по умолчанию main)")
    parser.add_argument("--user", help="Системный пользователь для systemd (Linux)")
    parser.add_argument("--non-interactive", action="store_true", help="Без вопросов (нужны --token и --admin-id)")
    parser.add_argument("--token", help="TELEGRAM_BOT_TOKEN")
    parser.add_argument("--admin-id", help="TELEGRAM_ADMIN_IDS")
    parser.add_argument("--no-service", action="store_true", help="Не ставить systemd unit")
    parser.add_argument("--skip-deps", action="store_true", help="Не ставить pip-зависимости")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print("Polaris installer")
    print("=================")
    ensure_python()

    if not git_available() and args.repo:
        raise InstallError("git не найден, а указан --repo")

    # resolve app root
    existing = find_app_root(Path.cwd())
    if args.install_dir:
        from_dir = find_app_root(Path(args.install_dir).expanduser().resolve())
        if from_dir:
            existing = from_dir

    if args.repo:
        if platform.system() == "Windows":
            default_dir = Path.cwd() / "polaris-install"
        else:
            default_dir = DEFAULT_INSTALL_DIR_LINUX
        target = Path(args.install_dir).expanduser() if args.install_dir else default_dir
        if not args.non_interactive and not args.install_dir:
            target = Path(ask("Каталог установки", str(target)))
        app_root = clone_repo(args.repo, target, args.branch)
    elif existing:
        app_root = existing
        ok(f"Найден проект: {app_root}")
    else:
        raise InstallError(
            "Не найден проект Polaris. Запустите из каталога репозитория "
            "или укажите --repo https://github.com/you/polaris-hub.git"
        )

    if args.skip_deps:
        venv_python = Path(sys.executable)
    else:
        venv_python = create_venv(app_root)

    ensure_data_dirs(app_root)

    if args.non_interactive:
        if not args.token or not args.admin_id:
            raise InstallError("Для --non-interactive нужны --token и --admin-id")
        repo_dir = str(app_root)
        if not (app_root / ".git").exists() and (app_root.parent / ".git").exists():
            repo_dir = str(app_root.parent.resolve())
        values = {
            "APP_NAME": "polaris",
            "DEBUG": "false",
            "DATABASE_URL": "sqlite:///database/data/polaris.db",
            "TELEGRAM_BOT_TOKEN": args.token,
            "TELEGRAM_ADMIN_IDS": args.admin_id,
            "UPDATE_BRANCH": args.branch,
            "UPDATE_REMOTE": "origin",
            "UPDATE_REPO_DIR": repo_dir,
            "UPDATE_SERVICE_NAME": SERVICE_NAME if platform.system() == "Linux" else "",
            "UPDATE_API_TOKEN": secrets.token_urlsafe(24),
            "WEB_HOST": DEFAULT_WEB_HOST,
            "WEB_PORT": DEFAULT_WEB_PORT,
        }
    else:
        values = collect_config(app_root, args.branch)

    write_env(app_root, values)

    service_user = args.user or (os.environ.get("SUDO_USER") or getpass.getuser())
    if platform.system() == "Linux" and not args.no_service:
        if args.non_interactive or ask_yes_no("Создать и запустить systemd-сервис polaris?", default=True):
            install_systemd(app_root, venv_python, service_user)
    elif platform.system() == "Windows":
        write_windows_starter(app_root, venv_python)

    run_doctor(app_root, venv_python)
    print_summary(app_root, values)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nОтменено.")
        raise SystemExit(1)
    except InstallError as exc:
        print(f"\nОшибка установки: {exc}", file=sys.stderr)
        raise SystemExit(1)
