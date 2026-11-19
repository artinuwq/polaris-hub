"""Polaris Agent: настройки из Hub, самообновление из репозитория и откат неудачной версии."""
from __future__ import annotations

import importlib.util
import json
import os
import signal
from argparse import Namespace
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1] / "agent" / "polaris-agent.py"


@pytest.fixture()
def agent(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("polaris_agent_under_test", AGENT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setenv("POLARIS_AGENT_STATE_DIR", str(tmp_path / "state"))
    old = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    yield mod
    for sig, handler in old.items():
        signal.signal(sig, handler)


def source(agent, version: str) -> bytes:
    return AGENT.read_text(encoding="utf-8").replace(
        f'__version__ = "{agent.__version__}"', f'__version__ = "{version}"'
    ).encode("utf-8")


# ───────────────────────── версии и проверка файла ─────────────────────────

def test_version_comparison(agent):
    assert agent.is_newer("0.3.0", "0.2.9")
    assert agent.is_newer("0.10.0", "0.9.0")  # числа, а не строки
    assert not agent.is_newer("0.3.0", "0.3.0")
    assert not agent.is_newer("0.2.0", "0.3.0")
    assert not agent.is_newer("junk", "0.3.0") and not agent.is_newer("0.3.0", "")


def test_validate_source_rejects_everything_that_is_not_the_agent(agent):
    assert agent.validate_source(source(agent, "0.9.0")) == "0.9.0"
    for bad in (b"def broken(:\n", b"print('hi')\n", b"\xff\xfe", b"x" * (agent.MAX_SOURCE_BYTES + 1),
                b'__version__ = "9.9.9"\nprint(1)\n'):
        with pytest.raises(agent.UpdateError):
            agent.validate_source(bad)


# ───────────────────────── скачивание ─────────────────────────

def test_stage_update_requires_enabled_https_url(agent, tmp_path):
    state = str(tmp_path / "state")
    assert agent.stage_update({"remote_update": False, "update_url": "https://x/a.py"}, state)[0] == "failed"
    assert agent.stage_update({"update_url": ""}, state)[0] == "failed"
    assert agent.stage_update({"update_url": "http://x/a.py"}, state)[0] == "failed"  # только https


def test_stage_update_download_errors_and_current_version(agent, tmp_path, monkeypatch):
    state = str(tmp_path / "state")
    cfg = {"update_url": "https://example.test/polaris-agent.py"}

    def boom(*_a, **_k):
        raise agent.UpdateError("не удалось скачать: timeout")
    monkeypatch.setattr(agent, "_http_get", boom)
    assert agent.stage_update(cfg, state) == ("failed", "не удалось скачать: timeout")

    monkeypatch.setattr(agent, "_http_get", lambda *_a, **_k: source(agent, agent.__version__))
    result, detail = agent.stage_update(cfg, state)
    assert result == "current" and agent.__version__ in detail
    assert not (tmp_path / "state" / "staged").exists()

    monkeypatch.setattr(agent, "_http_get", lambda *_a, **_k: b"<html>404</html>")
    assert agent.stage_update(cfg, state)[0] == "failed"


def test_stage_update_saves_newer_version(agent, tmp_path, monkeypatch):
    new = source(agent, "0.9.0")
    monkeypatch.setattr(agent, "_http_get", lambda *_a, **_k: new)
    assert agent.stage_update({"update_url": "https://example.test/a.py"}, str(tmp_path / "state")) == ("staged", "0.9.0")
    assert (tmp_path / "state" / "staged" / "polaris-agent.py").read_bytes() == new


# ───────────────────────── root-шаг: установка и откат ─────────────────────────

def _install(agent, tmp_path, version="0.3.0"):
    bin_path = tmp_path / "bin" / "polaris-agent.py"
    bin_path.parent.mkdir()
    bin_path.write_bytes(source(agent, version))
    state = tmp_path / "state"
    (state / "staged").mkdir(parents=True)
    return bin_path, state


def test_apply_update_installs_new_file_and_keeps_previous_copy(agent, tmp_path):
    bin_path, state = _install(agent, tmp_path)
    old = bin_path.read_bytes()
    new = source(agent, "0.4.0")
    (state / "staged" / "polaris-agent.py").write_bytes(new)

    assert agent.apply_update(str(bin_path), str(state), current="0.3.0") == 0
    assert bin_path.read_bytes() == new
    assert (tmp_path / "bin" / "polaris-agent.py.prev").read_bytes() == old
    assert json.loads((state / "applied.json").read_text()) == {"from": "0.3.0", "to": "0.4.0", "starts": 1}
    assert agent.read_result(str(state)) == {"result": "updated", "detail": "0.3.0 → 0.4.0"}
    assert not (state / "staged" / "polaris-agent.py").exists()
    assert oct(bin_path.stat().st_mode & 0o777) == "0o755"


@pytest.mark.parametrize("content,expected", [
    (b"def broken(:\n", "failed"),                 # мусор вместо агента
    (None, "current"),                              # версия не новее установленной
])
def test_apply_update_refuses_bad_or_old_files(agent, tmp_path, content, expected):
    bin_path, state = _install(agent, tmp_path)
    before = bin_path.read_bytes()
    (state / "staged" / "polaris-agent.py").write_bytes(content if content is not None else source(agent, "0.3.0"))
    agent.apply_update(str(bin_path), str(state), current="0.3.0")
    assert bin_path.read_bytes() == before
    assert agent.read_result(str(state))["result"] == expected
    assert not (state / "applied.json").exists()


def test_apply_update_ignores_symlinked_staged_file(agent, tmp_path):
    bin_path, state = _install(agent, tmp_path)
    before = bin_path.read_bytes()
    target = tmp_path / "elsewhere.py"
    target.write_bytes(source(agent, "0.9.0"))
    os.symlink(target, state / "staged" / "polaris-agent.py")  # агент не может подсунуть чужой файл ссылкой
    agent.apply_update(str(bin_path), str(state), current="0.3.0")
    assert bin_path.read_bytes() == before and not (state / "applied.json").exists()


def test_unconfirmed_version_is_rolled_back_after_two_starts(agent, tmp_path):
    bin_path, state = _install(agent, tmp_path)
    old = bin_path.read_bytes()
    (state / "staged" / "polaris-agent.py").write_bytes(source(agent, "0.4.0"))
    agent.apply_update(str(bin_path), str(state), current="0.3.0")      # установили 0.4.0
    agent.apply_update(str(bin_path), str(state), current="0.4.0")      # старт №2: новой версии ещё дают шанс
    assert json.loads((state / "applied.json").read_text())["starts"] == 2
    assert b'__version__ = "0.4.0"' in bin_path.read_bytes()
    agent.apply_update(str(bin_path), str(state), current="0.4.0")      # старт №3 без связи с Hub -> откат
    assert bin_path.read_bytes() == old
    assert not (state / "applied.json").exists()
    result = agent.read_result(str(state))
    assert result["result"] == "rollback" and "0.4.0" in result["detail"]


def test_confirmed_version_is_kept(agent, tmp_path):
    bin_path, state = _install(agent, tmp_path)
    (state / "staged" / "polaris-agent.py").write_bytes(source(agent, "0.4.0"))
    agent.apply_update(str(bin_path), str(state), current="0.3.0")
    (state / "confirmed").write_text("0.4.0")                           # новая версия вышла на связь
    installed = bin_path.read_bytes()
    for _ in range(3):
        agent.apply_update(str(bin_path), str(state), current="0.4.0")
    assert bin_path.read_bytes() == installed
    assert not (state / "applied.json").exists() and not (state / "confirmed").exists()


def test_apply_update_never_fails_the_service_start(agent, tmp_path, capsys):
    assert agent.cmd_apply_update(Namespace(state_dir=str(tmp_path / "nothing-here"))) == 0


# ───────────────────────── настройки из Hub ─────────────────────────

def test_remote_config_is_sanitized(agent):
    clean = agent.sanitize_remote_config({
        "services": ["nginx", "ok.service", "bad name", "-x", "", "a;b", "x" * 101],
        "heartbeat_interval": 5, "metrics_interval": 99999,
    })
    assert clean == {"services": ["nginx", "ok.service"], "heartbeat_interval": 15, "metrics_interval": 3600}
    assert agent.sanitize_remote_config({"heartbeat_interval": "junk", "services": "nginx"}) == {}


# ───────────────────────── цикл run: обмен с Hub ─────────────────────────

class FakeHub:
    """Подменяет _post: отвечает на heartbeat заданной директивой и останавливает агента сигналом."""

    def __init__(self, agent, directive, status=200):
        self.agent, self.directive, self.status, self.heartbeats = agent, directive, status, []

    def __call__(self, url, payload, headers=None, timeout=10):
        if url.endswith("/heartbeat"):
            self.heartbeats.append(payload)
            os.kill(os.getpid(), signal.SIGTERM)  # хватит одного heartbeat
            body = {"success": True, "data": self.directive}
            return self.status, json.dumps(body if self.status == 200 else {"detail": "no"}).encode()
        return 200, b'{"success": true}'


def _run(agent, tmp_path, monkeypatch, directive, status=200, preset=None):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"hub_url": "https://hub.test", "agent_id": "a1", "agent_token": "t1",
                               "update_url": "https://example.test/polaris-agent.py"}))
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    for name, data in (preset or {}).items():
        (state / name).write_text(json.dumps(data))
    hub = FakeHub(agent, directive, status)
    monkeypatch.setattr(agent, "_post", hub)
    code = agent.cmd_run(Namespace(config=str(cfg), verbose=False))
    return code, hub, state


def test_run_applies_and_remembers_settings_from_hub(agent, tmp_path, monkeypatch):
    new = {"services": ["nginx", "bad name"], "heartbeat_interval": 20, "metrics_interval": 45}
    code, hub, state = _run(agent, tmp_path, monkeypatch, {"config_version": 3, "config": new, "update": False})
    assert code == 0
    assert hub.heartbeats[0]["config_version"] == 0 and hub.heartbeats[0]["agent_version"] == agent.__version__
    saved = json.loads((state / "remote.json").read_text())
    assert saved == {"config_version": 3, "config": {"services": ["nginx"], "heartbeat_interval": 20, "metrics_interval": 45}}
    assert (state / "confirmed").read_text() == agent.__version__  # связь с Hub есть -> версия подтверждена

    # после перезапуска агент сообщает Hub, какую версию настроек уже применил
    code, hub, _ = _run(agent, tmp_path, monkeypatch, {"config_version": 3, "update": False})
    assert hub.heartbeats[0]["config_version"] == 3


def test_run_exits_for_restart_when_hub_asks_to_update(agent, tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "stage_update", lambda cfg, state: ("staged", "0.9.0"))
    code, hub, state = _run(agent, tmp_path, monkeypatch, {"config_version": 0, "update": True})
    assert code == agent.EXIT_UPDATE
    # запасной итог: если root-шаг не подхватит файл, Hub узнает причину, а не зациклится
    result = agent.read_result(str(state))
    assert result["result"] == "failed" and "0.9.0" in result["detail"]


def test_run_reports_update_failure_without_restarting(agent, tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "stage_update", lambda cfg, state: ("current", "уже последняя версия"))
    code, hub, state = _run(agent, tmp_path, monkeypatch, {"config_version": 0, "update": True})
    assert code == 0
    assert agent.read_result(str(state)) == {"result": "current", "detail": "уже последняя версия"}


def test_run_sends_pending_result_and_clears_it_only_after_hub_accepts(agent, tmp_path, monkeypatch):
    preset = {"result.json": {"result": "updated", "detail": "0.2.0 → 0.3.0"}}
    code, hub, state = _run(agent, tmp_path, monkeypatch, {"config_version": 0, "update": False}, preset=preset)
    assert hub.heartbeats[0]["update_result"] == "updated" and hub.heartbeats[0]["update_detail"] == "0.2.0 → 0.3.0"
    assert not (state / "result.json").exists()

    code, hub, state = _run(agent, tmp_path, monkeypatch, {}, status=422, preset=preset)  # Hub отклонил heartbeat
    assert (state / "result.json").exists()
    assert not (state / "confirmed").exists() or (state / "confirmed").read_text() == agent.__version__


def test_update_is_not_attempted_while_the_previous_result_is_undelivered(agent, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "stage_update", lambda cfg, state: calls.append(1) or ("staged", "0.9.0"))
    preset = {"result.json": {"result": "failed", "detail": "x"}}
    # Hub не принял heartbeat -> итог ещё не доставлен: новую попытку не начинаем, итог не теряем
    code, _hub, state = _run(agent, tmp_path, monkeypatch, {"config_version": 0, "update": True}, status=422, preset=preset)
    assert code == 0 and calls == [] and (state / "result.json").exists()
