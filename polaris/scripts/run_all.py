"""Запуск всех компонентов Polaris (web + bot)."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
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


def main() -> None:
    python = sys.executable
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)

    processes = [
        subprocess.Popen([python, str(ROOT / "scripts" / "run_web.py")], cwd=ROOT, env=env),
        subprocess.Popen([python, str(ROOT / "scripts" / "run_bot.py")], cwd=ROOT, env=env),
    ]

    def _shutdown(signum, frame):  # noqa: ANN001, ARG001
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()
        for proc in processes:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    print("Polaris started: web + bot")
    while True:
        for proc in processes:
            code = proc.poll()
            if code is not None:
                print(f"Process exited with code {code}, shutting down…")
                _shutdown(None, None)
        time.sleep(1)


if __name__ == "__main__":
    main()
