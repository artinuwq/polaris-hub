import os
import stat
import subprocess
import time

import pytest

from polaris.infra.settings import Settings
from polaris.update.manager import UpdateManager
from polaris.update.provider import GitError, UpdateProvider


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture()
def repos(tmp_path):
    remote, server, dev = tmp_path / "remote.git", tmp_path / "server", tmp_path / "dev"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(remote), str(dev)], check=True, capture_output=True)
    (dev / "a.txt").write_text("v1")
    git(dev, "checkout", "-b", "main"); git(dev, "add", "-A"); git(dev, "commit", "-m", "v1"); git(dev, "push", "origin", "main")
    subprocess.run(["git", "clone", str(remote), str(server)], check=True, capture_output=True)
    settings = Settings(update_repo_dir=str(server), update_branch="main", update_remote="origin", update_service_name="")
    return dev, server, settings


def test_check_and_apply_without_restart(repos):
    dev, server, settings = repos
    manager = UpdateManager(settings)
    status = manager.check()
    assert status.up_to_date and "Уже актуально" in status.message

    (dev / "a.txt").write_text("v2"); git(dev, "commit", "-am", "v2"); git(dev, "push", "origin", "main")
    status = manager.check()
    assert not status.up_to_date and status.short_local != status.short_remote

    result = manager.apply(force=False, restart=False)       # так бот вызывает apply: перезапуск — отдельным шагом
    assert result.success and not result.restarted
    assert result.previous_sha == status.local_sha and result.current_sha == status.remote_sha
    assert (server / "a.txt").read_text() == "v2"
    assert manager.check().up_to_date


def test_apply_overwrites_local_changes_like_before(repos):
    dev, server, settings = repos
    (server / "a.txt").write_text("local edit")
    (dev / "a.txt").write_text("v2"); git(dev, "commit", "-am", "v2"); git(dev, "push", "origin", "main")
    assert UpdateManager(settings).check().dirty
    UpdateManager(settings).apply(restart=False)
    assert (server / "a.txt").read_text() == "v2"


def test_update_notice_roundtrip_and_expiry(repos):
    _, _, settings = repos
    manager = UpdateManager(settings)
    assert manager.pop_notice() is None
    manager.save_notice(42, 7, "abcdef1234")
    notice = manager.pop_notice()
    assert notice["chat_id"] == 42 and notice["message_id"] == 7 and notice["sha"] == "abcdef1234"
    assert manager.pop_notice() is None                       # одноразовое
    manager.save_notice(42, 7, "abc")
    import json
    data = json.loads(manager.notice_file.read_text())
    data["at"] = time.time() - 3600                           # час назад — слишком старое
    manager.notice_file.write_text(json.dumps(data))
    assert manager.pop_notice() is None                       # устаревшее игнорируется
    assert not manager.notice_file.exists()                   # и файл всё равно удалён


def test_hung_git_gives_error_instead_of_hanging(tmp_path, monkeypatch):
    fake = tmp_path / "bin"; fake.mkdir()
    script = fake / "git"
    script.write_text("#!/bin/sh\nsleep 10\n"); script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{fake}:{os.environ['PATH']}")
    repo = tmp_path / "r"; (repo / ".git").mkdir(parents=True)
    provider = UpdateProvider(repo)
    started = time.time()
    with pytest.raises(GitError) as exc:
        provider._run("fetch", timeout=1)
    assert time.time() - started < 5 and "не ответил" in str(exc.value)


def test_git_never_prompts_for_credentials(tmp_path, monkeypatch):
    fake = tmp_path / "bin"; fake.mkdir()
    script = fake / "git"
    script.write_text('#!/bin/sh\necho "prompt=$GIT_TERMINAL_PROMPT"\n'); script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{fake}:{os.environ['PATH']}")
    repo = tmp_path / "r"; (repo / ".git").mkdir(parents=True)
    assert UpdateProvider(repo)._run("status") == "prompt=0"
