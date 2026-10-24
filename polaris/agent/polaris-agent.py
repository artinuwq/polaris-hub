#!/usr/bin/env python3
"""Polaris Agent — лёгкий read-only монитор сервера.

Один файл, только стандартная библиотека Python 3.7+: без pip, venv и
компиляции. Читает /proc, раз в интервал шлёт heartbeat и метрики в Hub
по HTTPS. Входящих соединений не принимает и команд не выполняет.

  polaris-agent.py register --hub URL --token TOKEN [--services a,b]
  polaris-agent.py run
  polaris-agent.py once        # напечатать метрики и выйти (без сети)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

__version__ = "0.2.0"

DEFAULT_CONFIG = "/etc/polaris-agent/config.json"
MIN_HEARTBEAT = 15  # нижние пороги: Hub не может попросить агента работать чаще
MIN_METRICS = 30
MAX_RETRY_DELAY = 300
MAX_RESPONSE_BYTES = 64 * 1024

log = logging.getLogger("polaris-agent")


# ───────────────────────────── config ─────────────────────────────

def load_config(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        data = {}
    data.setdefault("hub_url", "")
    data.setdefault("agent_id", "")
    data.setdefault("agent_token", "")
    data.setdefault("heartbeat_interval", 30)
    data.setdefault("metrics_interval", 60)
    data.setdefault("services", [])
    return data


def save_config(path, cfg):
    """Атомарная запись с правами 0600 — внутри секрет агента."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, mode=0o750, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# ───────────────────────────── http ─────────────────────────────

class HubUnavailable(Exception):
    pass


class AuthRejected(Exception):
    pass


def _post(url, payload, headers=None, timeout=10):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "polaris-agent/" + __version__)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_RESPONSE_BYTES)
            return resp.status, raw
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(MAX_RESPONSE_BYTES)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise HubUnavailable(str(exc))


def _detail(raw):
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
        return str(data.get("detail") or data.get("message") or data)[:200]
    except Exception:  # noqa: BLE001
        return raw[:200].decode("utf-8", "replace")


class Hub:
    def __init__(self, cfg):
        self.cfg = cfg

    def _authed(self, path, payload):
        headers = {
            "X-Agent-Id": self.cfg["agent_id"],
            "Authorization": "Bearer " + self.cfg["agent_token"],
        }
        status, raw = _post(self.cfg["hub_url"] + path, payload, headers)
        if status == 401:
            raise AuthRejected("Hub не признаёт credentials агента (401) — нужна повторная регистрация")
        if status >= 500:
            raise HubUnavailable("Hub вернул %d" % status)
        if status >= 400:
            log.warning("Hub отклонил %s: %s", path, _detail(raw))

    def heartbeat(self):
        self._authed("/api/v1/agent/heartbeat", {"agent_version": __version__, "status": "online"})

    def metrics(self, payload):
        self._authed("/api/v1/agent/metrics", payload)


# ───────────────────────────── collectors (/proc) ─────────────────────────────

def _read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


_prev_cpu = None


def _cpu_times():
    fields = _read("/proc/stat").split("\n", 1)[0].split()[1:9]
    values = [int(x) for x in fields]
    idle = values[3] + (values[4] if len(values) > 4 else 0)  # idle + iowait
    return sum(values), idle


def collect_cpu():
    global _prev_cpu
    cur = _cpu_times()
    if _prev_cpu is None:
        _prev_cpu = cur
        time.sleep(0.25)
        cur = _cpu_times()
    d_total = cur[0] - _prev_cpu[0]
    d_idle = cur[1] - _prev_cpu[1]
    _prev_cpu = cur
    usage = round(100.0 * (1.0 - d_idle / d_total), 1) if d_total > 0 else 0.0
    load = (None, None, None)
    try:
        load = tuple(round(x, 2) for x in os.getloadavg())
    except (OSError, AttributeError):
        pass
    return {"usage": max(0.0, min(100.0, usage)), "load1": load[0], "load5": load[1], "load15": load[2]}


def collect_memory():
    info = {}
    for line in _read("/proc/meminfo").splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            info[key] = int(parts[0]) * 1024
    total = info.get("MemTotal", 0)
    available = info.get("MemAvailable")
    if available is None:
        available = info.get("MemFree", 0) + info.get("Buffers", 0) + info.get("Cached", 0)
    used = max(0, total - available)
    percent = round(100.0 * used / total, 1) if total else None
    return {"total": total, "used": used, "available": available, "percent": percent}


_DISK_FS = {"ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "f2fs", "jfs",
            "reiserfs", "vfat", "ntfs", "exfat"}


def collect_disk():
    result, seen = [], set()
    for line in _read("/proc/mounts").splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[2] not in _DISK_FS:
            continue
        device = parts[0]
        mount = parts[1].replace("\\040", " ")
        if device in seen:  # bind-mount / subvolume того же устройства
            continue
        try:
            st = os.statvfs(mount)
        except OSError:
            continue
        total = st.f_blocks * st.f_frsize
        if total <= 0:
            continue
        seen.add(device)
        used = (st.f_blocks - st.f_bfree) * st.f_frsize
        avail = st.f_bavail * st.f_frsize
        denom = used + avail
        percent = round(100.0 * used / denom, 1) if denom else None
        result.append({"mount": mount, "total": total, "used": used,
                       "available": avail, "percent": percent})
        if len(result) >= 16:
            break
    return result


_SKIP_IFACE = ("lo", "docker", "veth", "br-", "virbr", "cni", "flannel", "tun", "tap")


def collect_network():
    rx = tx = 0
    for line in _read("/proc/net/dev").splitlines()[2:]:
        name, _, data = line.partition(":")
        name = name.strip()
        if not data or name == "lo" or name.startswith(_SKIP_IFACE):
            continue
        cols = data.split()
        rx += int(cols[0])
        tx += int(cols[8])
    return {"rx_bytes": rx, "tx_bytes": tx}


def _pretty_os():
    try:
        for line in _read("/etc/os-release").splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.platform()


def collect_system():
    uptime = None
    try:
        uptime = int(float(_read("/proc/uptime").split()[0]))
    except (OSError, ValueError, IndexError):
        pass
    return {"hostname": socket.gethostname(), "os": _pretty_os(), "kernel": platform.release(),
            "architecture": platform.machine(), "uptime_seconds": uptime}


_UNIT_RE = re.compile(r"^[A-Za-z0-9@_.:\-]+$")
_STATE = {"active": "running", "activating": "running", "reloading": "running",
          "inactive": "stopped", "deactivating": "stopped", "failed": "failed"}


def collect_services(names):
    names = [n for n in names if _UNIT_RE.match(n) and not n.startswith("-")]
    if not names:
        return []
    if not shutil.which("systemctl"):
        return [{"name": n, "status": "unknown"} for n in names]
    try:  # один вызов на все юниты — один fork вместо N
        out = subprocess.run(["systemctl", "is-active"] + names, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=5).stdout
        states = out.decode("utf-8", "replace").split()
    except (subprocess.SubprocessError, OSError):
        states = []
    return [{"name": n, "status": _STATE.get(states[i], "unknown") if i < len(states) else "unknown"}
            for i, n in enumerate(names)]


def _safe(name, fn, default):
    try:
        return fn()
    except Exception:  # noqa: BLE001 — один сбойный коллектор не роняет агента
        log.warning("collector %s: ошибка, пропускаю", name, exc_info=True)
        return default


def collect_all(services):
    return {
        "cpu": _safe("cpu", collect_cpu, {}),
        "memory": _safe("memory", collect_memory, {}),
        "disk": _safe("disk", collect_disk, []),
        "network": _safe("network", collect_network, {}),
        "system": _safe("system", collect_system, {}),
        "services": _safe("services", lambda: collect_services(services), []),
    }


# ───────────────────────────── commands ─────────────────────────────

def cmd_register(args):
    cfg = load_config(args.config)
    cfg["hub_url"] = args.hub.rstrip("/")
    payload = {"token": args.token, "hostname": args.hostname or socket.gethostname(),
               "os": _pretty_os(), "kernel": platform.release(),
               "architecture": platform.machine(), "agent_version": __version__, "address": ""}
    print("→ Регистрируюсь в %s …" % cfg["hub_url"])
    try:
        status, raw = _post(cfg["hub_url"] + "/api/v1/agent/register", payload)
    except HubUnavailable as exc:
        print("✗ Не удалось подключиться к Hub: %s" % exc, file=sys.stderr)
        return 1
    if status != 200:
        print("✗ Hub отклонил регистрацию (%d): %s" % (status, _detail(raw)), file=sys.stderr)
        return 1
    try:
        data = json.loads(raw.decode("utf-8"))["data"]
        cfg["agent_id"], cfg["agent_token"] = data["agent_id"], data["agent_token"]
    except (ValueError, KeyError, TypeError):
        print("✗ Неожиданный ответ Hub", file=sys.stderr)
        return 1
    cfg["heartbeat_interval"] = max(MIN_HEARTBEAT, int(data.get("heartbeat_interval", 30)))
    cfg["metrics_interval"] = max(MIN_METRICS, int(data.get("metrics_interval", 60)))
    if args.services:
        cfg["services"] = [s.strip() for s in args.services.split(",") if s.strip()]
    save_config(args.config, cfg)
    print("✓ Зарегистрирован. Конфиг: %s" % args.config)
    return 0


def cmd_once(args):
    cfg = load_config(args.config)
    print(json.dumps(collect_all(cfg["services"]), indent=2, ensure_ascii=False))
    return 0


def cmd_run(args):
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")
    cfg = load_config(args.config)
    if not (cfg["agent_id"] and cfg["agent_token"] and cfg["hub_url"]):
        print("Агент не зарегистрирован: run '%s register --hub URL --token TOKEN'" % sys.argv[0],
              file=sys.stderr)
        return 1

    hub = Hub(cfg)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    hb_every = max(MIN_HEARTBEAT, int(cfg["heartbeat_interval"]))
    mt_every = max(MIN_METRICS, int(cfg["metrics_interval"]))
    log.info("Polaris Agent %s запущен (hub=%s, heartbeat=%ds, metrics=%ds)",
             __version__, cfg["hub_url"], hb_every, mt_every)

    now = time.monotonic()
    jobs = {"heartbeat": [now, 0, hb_every, hub.heartbeat],
            "metrics": [now + 2, 0, mt_every,
                        lambda: hub.metrics(collect_all(cfg["services"]))]}  # [next, fails, every, fn]
    while not stop.is_set():
        now = time.monotonic()
        for name, job in jobs.items():
            if now < job[0]:
                continue
            try:
                job[3]()
                if job[1]:
                    log.info("%s: связь с Hub восстановлена", name)
                job[1], job[0] = 0, time.monotonic() + job[2]
            except AuthRejected as exc:
                job[1] += 1
                job[0] = time.monotonic() + MAX_RETRY_DELAY
                log.error("%s", exc)
            except HubUnavailable as exc:
                job[1] += 1
                delay = min(MAX_RETRY_DELAY, 5 * 2 ** min(job[1], 6))
                job[0] = time.monotonic() + delay
                log.warning("%s: Hub недоступен (%s), повтор через %ds", name, exc, delay)
            except Exception:  # noqa: BLE001
                job[1] += 1
                job[0] = time.monotonic() + job[2]
                log.exception("%s: неожиданная ошибка, продолжаю", name)
        wait = min(j[0] for j in jobs.values()) - time.monotonic()
        stop.wait(max(1.0, wait))
    log.info("Polaris Agent остановлен")
    return 0


def main():
    p = argparse.ArgumentParser(prog="polaris-agent", description="Polaris Agent — read-only мониторинг сервера")
    p.add_argument("--version", action="version", version="polaris-agent " + __version__)
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("register", help="зарегистрировать сервер в Polaris Hub")
    r.add_argument("--hub", required=True)
    r.add_argument("--token", required=True)
    r.add_argument("--hostname", default="")
    r.add_argument("--services", default="", help="systemd-юниты через запятую: nginx,docker")
    r.add_argument("--config", default=DEFAULT_CONFIG)
    r.set_defaults(func=cmd_register)

    run = sub.add_parser("run", help="основной цикл: heartbeat + metrics")
    run.add_argument("--config", default=DEFAULT_CONFIG)
    run.add_argument("--verbose", action="store_true")
    run.set_defaults(func=cmd_run)

    once = sub.add_parser("once", help="напечатать метрики и выйти (без сети)")
    once.add_argument("--config", default=DEFAULT_CONFIG)
    once.set_defaults(func=cmd_once)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
