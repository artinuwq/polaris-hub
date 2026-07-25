#!/usr/bin/env python3
"""Проверка состояния установки Polaris."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


def check(name: str, ok: bool, detail: str = "") -> bool:
    mark = "✓" if ok else "✗"
    suffix = f" — {detail}" if detail else ""
    print(f"{mark} {name}{suffix}")
    return ok


def main() -> int:
    print("Polaris doctor")
    print("==============")
    failed = 0

    if not check("Python >= 3.10", sys.version_info >= (3, 10), sys.version.split()[0]):
        failed += 1

    env_path = ROOT / ".env"
    if not check(".env существует", env_path.exists(), str(env_path)):
        failed += 1
        print("Запустите: python scripts/install.py")
        return 1

    from polaris.infra.settings import Settings

    settings = Settings.from_env()
    if not check("TELEGRAM_BOT_TOKEN", bool(settings.telegram_bot_token)):
        failed += 1
    if not check("TELEGRAM_ADMIN_IDS", bool(settings.telegram_admin_ids), str(settings.telegram_admin_ids)):
        failed += 1
    if not check("UPDATE_API_TOKEN", bool(settings.update_api_token)):
        failed += 1

    data_dir = ROOT / "database" / "data"
    if not check("database/data", data_dir.exists(), str(data_dir)):
        failed += 1

    if not check("git в PATH", shutil.which("git") is not None):
        failed += 1

    repo = Path(settings.update_repo_dir) if settings.update_repo_dir else ROOT
    git_ok = (repo / ".git").exists() or (ROOT / ".git").exists() or (ROOT.parent / ".git").exists()
    if not check("git-репозиторий", git_ok, str(repo)):
        failed += 1

    if os.name == "posix" and shutil.which("systemctl"):
        import subprocess

        result = subprocess.run(
            ["systemctl", "is-active", "polaris"],
            capture_output=True,
            text=True,
            check=False,
        )
        active = result.stdout.strip() == "active"
        check("systemd polaris", active, result.stdout.strip() or result.stderr.strip())

    print()
    if failed:
        print(f"Проблем: {failed}")
        return 1
    print("Всё выглядит хорошо.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
