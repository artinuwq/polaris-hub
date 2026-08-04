from __future__ import annotations

import argparse
import logging
import platform
import socket
import sys
from pathlib import Path

from polaris_agent import __version__
from polaris_agent.client import HubClient, RegistrationFailed
from polaris_agent.collectors.system import pretty_os_name
from polaris_agent.config import DEFAULT_CONFIG_PATH, AgentConfig
from polaris_agent.runner import Runner


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def cmd_register(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    config = AgentConfig.load(config_path)
    config.hub_url = args.hub.rstrip("/")

    client = HubClient(config)
    print(f"→ Регистрируюсь в {config.hub_url} …")

    try:
        data = client.register(
            token=args.token,
            hostname=args.hostname or socket.gethostname(),
            os_name=pretty_os_name(),
            kernel=platform.release(),
            architecture=platform.machine(),
        )
    except RegistrationFailed as exc:
        print(f"✗ Не удалось зарегистрироваться: {exc}", file=sys.stderr)
        return 1

    config.agent_id = data["agent_id"]
    config.agent_token = data["agent_token"]
    config.heartbeat_interval = data.get("heartbeat_interval", config.heartbeat_interval)
    config.metrics_interval = data.get("metrics_interval", config.metrics_interval)

    if args.services:
        config.services = [s.strip() for s in args.services.split(",") if s.strip()]

    config.save()
    print(f"✓ Зарегистрирован. server_id={data['server_id']}")
    print(f"✓ Конфигурация сохранена: {config.config_path}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    _setup_logging(args.verbose)
    config = AgentConfig.load(Path(args.config))
    if not config.is_registered:
        print(
            "Agent не зарегистрирован. Сначала:\n"
            f"  python -m polaris_agent register --hub <URL> --token <TOKEN> --config {args.config}",
            file=sys.stderr,
        )
        return 1

    runner = Runner(config)
    try:
        runner.run_forever()
    except KeyboardInterrupt:
        pass
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="polaris-agent", description="Polaris Agent — read-only server monitoring")
    parser.add_argument("--version", action="version", version=f"polaris-agent {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_register = sub.add_parser("register", help="Зарегистрировать этот сервер в Polaris Hub")
    p_register.add_argument("--hub", required=True, help="URL Polaris Hub, напр. https://polaris.example")
    p_register.add_argument("--token", required=True, help="Одноразовый registration token")
    p_register.add_argument("--hostname", default="", help="Переопределить hostname")
    p_register.add_argument("--services", default="", help="Список systemd-сервисов через запятую, напр. nginx,telemt")
    p_register.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Путь к config.yaml")
    p_register.set_defaults(func=cmd_register)

    p_run = sub.add_parser("run", help="Запустить основной цикл агента (heartbeat + metrics)")
    p_run.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Путь к config.yaml")
    p_run.add_argument("--verbose", action="store_true")
    p_run.set_defaults(func=cmd_run)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
