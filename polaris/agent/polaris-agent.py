#!/usr/bin/env python3
"""Polaris Agent — лёгкий read-only монитор сервера.

Один файл, только стандартная библиотека Python 3.7+: без pip, venv и
компиляции. Читает /proc, раз в интервал шлёт heartbeat и метрики в Hub
по HTTPS. Входящих соединений не принимает и команд не выполняет.

  polaris-agent.py register --hub URL --token TOKEN [--services a,b]
  polaris-agent.py run
  polaris-agent.py once        # напечатать метрики и выйти (без сети)
  polaris-agent.py apply-update  # служебная команда: root-шаг перед стартом сервиса (см. unit)

Раз в 10 минут агент присылает в Hub список установленных systemd-сервисов — из него в интерфейсе
выбирают, за какими следить (упал сервис — уведомление в Telegram).

Настройки (сервисы, интервалы) приходят из Hub в ответе на heartbeat — это данные, не команды.
По команде Hub «обновись» агент сам скачивает новую версию из репозитория (update_url в
config.json), кладёт её в каталог состояния и перезапускается; файл подменяет root-шаг unit-а,
сам агент свой код менять не может. Новая версия подтверждает запуск связью с Hub, иначе —
автоматический откат на .prev.
"""
from __future__ import annotations

import argparse
import ast
import json
import logging
import os
import platform
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

__version__ = "0.4.0"

DEFAULT_CONFIG = "/etc/polaris-agent/config.json"
MIN_HEARTBEAT = 15  # нижние пороги: Hub не может попросить агента работать чаще
MIN_METRICS = 30
MAX_RETRY_DELAY = 300
MAX_RESPONSE_BYTES = 64 * 1024
DEFAULT_STATE_DIR = "/var/lib/polaris-agent"  # unit: StateDirectory=polaris-agent
EXIT_UPDATE = 75                # «перезапусти меня»: systemd поднимет сервис, root-шаг подменит файл
MAX_SOURCE_BYTES = 1024 * 1024
MAX_UNCONFIRMED_STARTS = 2      # столько запусков даётся новой версии, чтобы выйти на связь с Hub
MAX_INTERVAL = 3600
RESULTS = ("updated", "current", "failed", "rollback")
CATALOG_EVERY_SECONDS = 600     # как часто пересылать список установленных сервисов
MAX_CATALOG = 500
_CATALOG_STATES = ("enabled", "enabled-runtime", "linked", "linked-runtime", "disabled")
_CATALOG_IF_RUNNING = ("static", "indirect", "generated")  # у самописного юнита без [Install] состояние static

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
    data.setdefault("update_url", "")      # откуда брать новую версию (https://…/polaris-agent.py)
    data.setdefault("remote_update", True)  # False — игнорировать команду Hub «обновись»
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


# ───────────────────────────── state / update ─────────────────────────────

def state_dir_of(cfg):
    return os.environ.get("POLARIS_AGENT_STATE_DIR") or cfg.get("state_dir") or DEFAULT_STATE_DIR


def _read_small(path, limit=MAX_SOURCE_BYTES):
    """Читает обычный файл, не идя по symlink. None — файла нет, он не обычный или слишком большой."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
            return None
        with os.fdopen(fd, "rb", closefd=False) as fh:
            return fh.read(limit + 1)[:limit]
    finally:
        os.close(fd)


def _read_json(path):
    raw = _read_small(path, 64 * 1024)
    if raw is None:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_atomic(path, data, mode=0o644):
    """Атомарная запись. Назначение подменяется через os.replace — symlink на его месте не разыменовывается."""
    tmp = "%s.%d.tmp" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_json(path, obj):
    _write_atomic(path, json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def _remove(path):
    try:
        os.unlink(path)
    except OSError:
        pass


_VERSION_RE = re.compile(r'^__version__\s*=\s*["\']([0-9]+(?:\.[0-9]+){1,3})["\']', re.MULTILINE)


def _vtuple(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", value):
        return None
    return tuple(int(p) for p in value.split("."))


def is_newer(new, current):
    a, b = _vtuple(new), _vtuple(current)
    return bool(a and b and a > b)


class UpdateError(Exception):
    pass


def validate_source(raw):
    """Проверяет скачанный файл и возвращает его версию. Выполнять его не нужно — достаточно разобрать."""
    if len(raw) > MAX_SOURCE_BYTES:
        raise UpdateError("файл слишком большой")
    try:
        text = raw.decode("utf-8")
        ast.parse(text)
    except (UnicodeDecodeError, SyntaxError, ValueError):
        raise UpdateError("скачанный файл — не корректный python-скрипт")
    match = _VERSION_RE.search(text)
    if not match or "polaris-agent" not in text or "def main(" not in text:
        raise UpdateError("это не файл Polaris Agent (проверьте update_url)")
    return match.group(1)


def _http_get(url, timeout=20, limit=MAX_SOURCE_BYTES):
    req = urllib.request.Request(url, headers={"User-Agent": "polaris-agent/" + __version__})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(limit + 1)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise UpdateError("не удалось скачать: %s" % exc)
    if len(raw) > limit:
        raise UpdateError("файл слишком большой")
    return raw


def save_result(state_dir, result, detail):
    """Итог попытки обновления: уйдёт в Hub с ближайшим heartbeat."""
    try:
        os.makedirs(state_dir, mode=0o750, exist_ok=True)
        _write_json(os.path.join(state_dir, "result.json"), {"result": result, "detail": str(detail)[:300]})
    except OSError:
        log.warning("не удалось сохранить результат обновления", exc_info=True)


def read_result(state_dir):
    data = _read_json(os.path.join(state_dir, "result.json"))
    if data.get("result") in RESULTS:
        return {"result": data["result"], "detail": str(data.get("detail", ""))[:300]}
    return None


def stage_update(cfg, state_dir):
    """Скачивает новую версию в каталог состояния. Возвращает (staged|current|failed, подробности)."""
    if not cfg.get("remote_update", True):
        return "failed", "удалённое обновление выключено в config.json (remote_update)"
    url = (cfg.get("update_url") or "").strip()
    if not url.startswith("https://"):
        return "failed", "update_url не настроен (нужна https-ссылка на polaris-agent.py)"
    try:
        raw = _http_get(url)
        new = validate_source(raw)
    except UpdateError as exc:
        return "failed", str(exc)
    if not is_newer(new, __version__):
        return "current", "уже последняя версия %s (в репозитории %s)" % (__version__, new)
    try:
        folder = os.path.join(state_dir, "staged")
        os.makedirs(folder, mode=0o750, exist_ok=True)
        _write_atomic(os.path.join(folder, "polaris-agent.py"), raw)
    except OSError as exc:
        return "failed", "каталог состояния недоступен: %s" % exc
    return "staged", new


def _rollback(bin_path, state_dir, applied):
    prev = _read_small(bin_path + ".prev")
    if prev is None:
        save_result(state_dir, "failed", "версия %s не вышла на связь, а .prev для отката нет" % applied.get("to"))
    else:
        _write_atomic(bin_path, prev, 0o755)
        save_result(state_dir, "rollback", "версия %s не вышла на связь с Hub, возврат к %s"
                    % (applied.get("to"), applied.get("from")))
    _remove(os.path.join(state_dir, "applied.json"))
    _remove(os.path.join(state_dir, "confirmed"))
    _remove(os.path.join(state_dir, "staged", "polaris-agent.py"))


def apply_update(bin_path, state_dir, current=None):
    """Root-шаг перед стартом сервиса (ExecStartPre=+): ставит скачанную версию или откатывает неудачную.

    Файл из каталога состояния считается недоверенным: он читается без symlink, разбирается заново и
    сравнивается по версии, а ставится его собственная копия из памяти."""
    current = current or __version__
    staged = os.path.join(state_dir, "staged", "polaris-agent.py")
    applied_p = os.path.join(state_dir, "applied.json")
    confirmed_p = os.path.join(state_dir, "confirmed")

    applied = _read_json(applied_p)
    if applied:
        confirmed = (_read_small(confirmed_p, 64) or b"").decode("utf-8", "replace").strip()
        if confirmed and confirmed == str(applied.get("to")):
            _remove(applied_p)  # новая версия на связи — обновление подтверждено
            _remove(confirmed_p)
        else:
            starts = int(applied.get("starts") or 1) + 1
            if starts > MAX_UNCONFIRMED_STARTS:
                _rollback(bin_path, state_dir, applied)
            else:
                applied["starts"] = starts
                _write_json(applied_p, applied)
                _remove(staged)
            return 0

    raw = _read_small(staged)
    if raw is None:
        _remove(staged)
        return 0
    _remove(staged)
    try:
        new = validate_source(raw)
    except UpdateError as exc:
        save_result(state_dir, "failed", "установка отклонена: %s" % exc)
        return 0
    if not is_newer(new, current):
        save_result(state_dir, "current", "версия %s не новее установленной %s" % (new, current))
        return 0
    own = _read_small(bin_path)
    if own is not None:
        _write_atomic(bin_path + ".prev", own, 0o755)
    _write_atomic(bin_path, raw, 0o755)
    _write_json(applied_p, {"from": current, "to": new, "starts": 1})
    _remove(confirmed_p)
    save_result(state_dir, "updated", "%s → %s" % (current, new))
    return 0


def sanitize_remote_config(raw):
    """Настройки из Hub — недоверенные данные: фильтруем так же строго, как локальный конфиг."""
    out = {}
    if isinstance(raw.get("services"), list):
        names = [str(x).strip() for x in raw["services"]]
        out["services"] = [n for n in names if n and len(n) <= 100 and _UNIT_RE.match(n) and not n.startswith("-")][:32]
    for key, floor in (("heartbeat_interval", MIN_HEARTBEAT), ("metrics_interval", MIN_METRICS)):
        if key in raw:
            try:
                out[key] = min(MAX_INTERVAL, max(floor, int(raw[key])))
            except (TypeError, ValueError):
                pass
    return out


def load_remote(state_dir):
    data = _read_json(os.path.join(state_dir, "remote.json"))
    version = data.get("config_version")
    config = data.get("config")
    return (version if isinstance(version, int) else 0,
            sanitize_remote_config(config) if isinstance(config, dict) else {})


def save_remote(state_dir, version, config):
    try:
        os.makedirs(state_dir, mode=0o750, exist_ok=True)
        _write_json(os.path.join(state_dir, "remote.json"), {"config_version": version, "config": config})
    except OSError:
        log.warning("не удалось сохранить настройки из Hub", exc_info=True)


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
            return None
        return raw

    def heartbeat(self, extra=None):
        """Возвращает директиву Hub (настройки / «обновись») или None, если Hub heartbeat не принял."""
        payload = {"agent_version": __version__, "status": "online"}
        payload.update(extra or {})
        raw = self._authed("/api/v1/agent/heartbeat", payload)
        if raw is None:
            return None
        try:
            data = json.loads(raw.decode("utf-8")).get("data")
        except (ValueError, AttributeError):
            return {}
        return data if isinstance(data, dict) else {}

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


def _systemctl(args):
    """Вывод systemctl или None, если его нет / он не ответил."""
    if not shutil.which("systemctl"):
        return None
    try:
        res = subprocess.run(["systemctl"] + args + ["--no-legend", "--no-pager", "--plain"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
    except (subprocess.SubprocessError, OSError):
        return None
    return res.stdout.decode("utf-8", "replace")


def parse_running_units(text):
    """`systemctl list-units --type=service --state=running` -> множество имён без .service."""
    names = set()
    for line in text.splitlines():
        parts = [p for p in line.split() if p not in ("●", "*")]
        if parts and parts[0].endswith(".service"):
            names.add(parts[0][:-len(".service")])
    return names


def parse_unit_files(text, running=frozenset()):
    """`systemctl list-unit-files --type=service` -> {имя: состояние} для сервисов, которые имеет смысл показывать:
    включённые/выключенные и запущенные самописные. Шаблоны (name@), алиасы и masked отбрасываются."""
    found = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2 or not parts[0].endswith(".service"):
            continue
        name, state = parts[0][:-len(".service")], parts[1]
        if name.endswith("@") or len(name) > 100 or name.startswith("-") or not _UNIT_RE.match(name):
            continue
        if state in _CATALOG_STATES or (state in _CATALOG_IF_RUNNING and name in running):
            found[name] = state
    return found


def discover_services():
    """Список установленных сервисов [{name, state, running}] или None, если systemd недоступен."""
    files = _systemctl(["list-unit-files", "--type=service"])
    if files is None:
        return None
    running = parse_running_units(_systemctl(["list-units", "--type=service", "--state=running"]) or "")
    items = [{"name": n, "state": st, "running": n in running} for n, st in parse_unit_files(files, running).items()]
    # сначала запущенные и включённые — их выбирают чаще всего
    items.sort(key=lambda x: (not x["running"], x["state"] not in ("enabled", "enabled-runtime"), x["name"]))
    return items[:MAX_CATALOG]


def metrics_payload(cfg, ctx, now):
    """Метрики + (раз в CATALOG_EVERY_SECONDS) список установленных сервисов."""
    data = collect_all(cfg["services"])
    if now >= ctx.get("catalog_at", 0):
        catalog = _safe("catalog", discover_services, None)
        if catalog is not None:
            data["available_services"] = catalog
    return data


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
    if args.update_url:
        cfg["update_url"] = args.update_url.strip()
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

    state_dir = state_dir_of(cfg)
    config_version, remote = load_remote(state_dir)  # настройки, которые раньше прислал Hub
    cfg.update(remote)
    ctx = {"config_version": config_version, "result": read_result(state_dir),
           "confirmed": False, "exit_code": 0, "catalog_at": 0}

    hub = Hub(cfg)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    hb_every = max(MIN_HEARTBEAT, int(cfg["heartbeat_interval"]))
    mt_every = max(MIN_METRICS, int(cfg["metrics_interval"]))
    log.info("Polaris Agent %s запущен (hub=%s, heartbeat=%ds, metrics=%ds)",
             __version__, cfg["hub_url"], hb_every, mt_every)

    def handle_directive(directive):
        new_config, new_version = directive.get("config"), directive.get("config_version")
        if isinstance(new_config, dict) and isinstance(new_version, int) and new_version != ctx["config_version"]:
            clean = sanitize_remote_config(new_config)
            cfg.update(clean)
            ctx["config_version"] = new_version
            save_remote(state_dir, new_version, clean)
            jobs["heartbeat"][2] = max(MIN_HEARTBEAT, int(cfg["heartbeat_interval"]))
            jobs["metrics"][2] = max(MIN_METRICS, int(cfg["metrics_interval"]))
            log.info("Применены настройки из Hub (версия %s)", new_version)
        if directive.get("update") is True and not ctx["result"]:
            result, detail = stage_update(cfg, state_dir)
            if result == "staged":
                log.info("Скачана версия %s — перезапускаюсь, чтобы её установить", detail)
                # если root-шаг unit-а не подхватит файл, Hub узнает об этом, а не зациклится на обновлении
                save_result(state_dir, "failed", "версия %s скачана, но не установлена: нет root-шага "
                            "ExecStartPre в unit-е? Переустановите агент через install.sh" % detail)
                ctx["exit_code"] = EXIT_UPDATE
                stop.set()
            else:
                log.log(logging.INFO if result == "current" else logging.WARNING, "обновление: %s", detail)
                ctx["result"] = {"result": result, "detail": detail}
                save_result(state_dir, result, detail)

    def heartbeat_job():
        extra = {"config_version": ctx["config_version"]}
        if ctx["result"]:
            extra["update_result"], extra["update_detail"] = ctx["result"]["result"], ctx["result"]["detail"]
        directive = hub.heartbeat(extra)
        if directive is None:
            return
        if ctx["result"]:
            ctx["result"] = None
            _remove(os.path.join(state_dir, "result.json"))
        if not ctx["confirmed"]:  # связь с Hub есть — root-шаг не откатит эту версию
            ctx["confirmed"] = True
            try:
                os.makedirs(state_dir, mode=0o750, exist_ok=True)
                _write_atomic(os.path.join(state_dir, "confirmed"), __version__.encode("utf-8"))
            except OSError:
                log.debug("не удалось записать confirmed", exc_info=True)
        handle_directive(directive)

    def metrics_job():
        data = metrics_payload(cfg, ctx, time.monotonic())
        hub.metrics(data)
        if "available_services" in data:  # список ушёл — следующий через CATALOG_EVERY_SECONDS
            ctx["catalog_at"] = time.monotonic() + CATALOG_EVERY_SECONDS

    now = time.monotonic()
    jobs = {"heartbeat": [now, 0, hb_every, heartbeat_job],
            "metrics": [now + 2, 0, mt_every, metrics_job]}  # [next, fails, every, fn]
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
            if stop.is_set():
                break
        wait = min(j[0] for j in jobs.values()) - time.monotonic()
        stop.wait(max(1.0, wait))
    log.info("Polaris Agent остановлен")
    return ctx["exit_code"]


def cmd_apply_update(args):
    """Служебная команда для ExecStartPre=+ (root). Никогда не роняет запуск сервиса."""
    try:
        return apply_update(os.path.abspath(__file__), args.state_dir)
    except Exception as exc:  # noqa: BLE001
        print("apply-update: %s" % exc, file=sys.stderr)
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
    r.add_argument("--update-url", default="", help="https-ссылка на polaris-agent.py для обновлений")
    r.add_argument("--config", default=DEFAULT_CONFIG)
    r.set_defaults(func=cmd_register)

    run = sub.add_parser("run", help="основной цикл: heartbeat + metrics")
    run.add_argument("--config", default=DEFAULT_CONFIG)
    run.add_argument("--verbose", action="store_true")
    run.set_defaults(func=cmd_run)

    once = sub.add_parser("once", help="напечатать метрики и выйти (без сети)")
    once.add_argument("--config", default=DEFAULT_CONFIG)
    once.set_defaults(func=cmd_once)

    ap = sub.add_parser("apply-update", help="служебная команда: установить скачанную версию (root-шаг unit-а)")
    ap.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    ap.set_defaults(func=cmd_apply_update)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
