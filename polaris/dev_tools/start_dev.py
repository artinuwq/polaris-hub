"""Скрипт запуска проекта в режиме разработки."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
print(f"Polaris dev environment ready at {ROOT}")
