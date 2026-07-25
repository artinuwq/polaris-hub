"""Запуск веб-сервера."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
os.environ["PYTHONPATH"] = os.pathsep.join(
    [str(SRC), *[p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]]
)

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import uvicorn

from polaris.api.app import app
from polaris.infra.settings import Settings


def main() -> None:
    settings = Settings.from_env()
    uvicorn.run(
        app,
        host=settings.web_host,
        port=settings.web_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
