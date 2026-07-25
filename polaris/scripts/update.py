"""Обновление приложения из git-ветки."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from polaris.update.manager import UpdateManager


def main() -> None:
    parser = argparse.ArgumentParser(description="Polaris update")
    parser.add_argument("--check", action="store_true", help="Только проверить наличие обновлений")
    parser.add_argument("--force", action="store_true", help="Обновить даже если уже актуально")
    args = parser.parse_args()

    manager = UpdateManager()
    if args.check:
        status = manager.check()
        print(status.message)
        sys.exit(0 if status.up_to_date else 2)

    result = manager.apply(force=args.force)
    print(result.message)
    sys.exit(0 if result.success else 1)


if __name__ == "__main__":
    main()
