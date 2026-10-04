"""Saving login profiles safely and releasing account reservations on teardown."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest

from releasi.linkedin import login_session as login_module
from releasi.linkedin.login_session import LoginSessionManager, _replace_profile
from releasi.linkedin.pool import BrowserPool


@pytest.fixture
def profiles(tmp_path):
    source = tmp_path / "login"
    destination = tmp_path / "saved" / "account"
    source.mkdir()
    destination.mkdir(parents=True)
    (source / "Preferences").write_text("new profile")
    (destination / "Preferences").write_text("original profile")
    return source, destination


def test_failed_staging_preserves_old_profile(profiles, monkeypatch):
    source, destination = profiles

    def fail(*args, **kwargs):
        raise PermissionError("Profile directory denied")

    monkeypatch.setattr(login_module.shutil, "copytree", fail)
    with pytest.raises(PermissionError):
        _replace_profile(source, destination)
    assert (destination / "Preferences").read_text() == "original profile"
    assert list(destination.parent.iterdir()) == [destination]


def test_failed_promotion_restores_old_profile(profiles, monkeypatch):
    source, destination = profiles
    rename = Path.rename

    def fail_promotion(path, target):
        if path.name == "profile":
            raise PermissionError("Cannot promote staged profile")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_promotion)
    with pytest.raises(PermissionError):
        _replace_profile(source, destination)
    assert (destination / "Preferences").read_text() == "original profile"
    assert list(destination.parent.iterdir()) == [destination]


def test_successful_replacement_excludes_vnc_token_and_browser_locks(profiles):
    source, destination = profiles
    (source / "tokens.cfg").write_text("test token")
    (source / "SingletonLock").symlink_to("nonexistent-chromium-process")
    _replace_profile(source, destination)
    assert (destination / "Preferences").read_text() == "new profile"
    assert not (destination / "tokens.cfg").exists()
    assert not (destination / "SingletonLock").is_symlink()
    assert destination.stat().st_mode & 0o777 == 0o700
    assert list(destination.parent.iterdir()) == [destination]


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session = LoginSessionManager()
    session._account_id = "account"
    source = tmp_path / "temporary-login"
    source.mkdir()
    (source / "Preferences").write_text("new profile")
    session._temp_dir = str(source)
    session._context = SimpleNamespace(
        close=AsyncMock(),
        cookies=AsyncMock(return_value=[{"name": "li_at", "value": "fake-cookie", "expires": -1}]),
    )
    session._playwright = SimpleNamespace(stop=AsyncMock())
    return session


async def test_finish_reports_profile_failure_and_keeps_old_profile(manager, monkeypatch):
    destination = Path("data/browser_data/account")
    destination.mkdir(parents=True)
    (destination / "Preferences").write_text("original profile")

    def fail(*args, **kwargs):
        raise PermissionError("Profile directory denied")

    monkeypatch.setattr(login_module.shutil, "copytree", fail)
    result = await manager.finish_session()
    assert result["profile_saved"] is False
    assert result["profile_error"]
    assert result["li_at"] is not None
    assert (destination / "Preferences").read_text() == "original profile"
    assert not manager.is_active
    assert manager._temp_dir is None


async def test_finish_closes_context_before_copying_profile(manager, monkeypatch):
    context = manager._context
    copy = login_module._replace_profile

    def check_closed(*args):
        context.close.assert_awaited_once()
        assert manager._context is None
        copy(*args)

    monkeypatch.setattr(login_module, "_replace_profile", check_closed)
    result = await manager.finish_session()
    assert result["profile_saved"] is True
    assert result["profile_error"] is None
    assert Path("data/browser_data/account/Preferences").read_text() == "new profile"


async def test_failed_browser_flush_does_not_copy_incomplete_profile(manager, monkeypatch):
    manager._context.close.side_effect = RuntimeError("Browser close failed")
    copy = AsyncMock()
    monkeypatch.setattr(login_module, "_replace_profile", copy)
    result = await manager.finish_session()
    assert result["profile_saved"] is False
    assert result["profile_error"]
    copy.assert_not_called()


async def test_finish_can_hold_lease_until_database_persistence(manager):
    pool = BrowserPool()
    manager._lease = await pool.reserve_external("account")
    result = await manager.finish_session(release_lease=False)
    assert result["profile_saved"] is True
    assert pool.is_busy("account")
    assert not manager.is_active
    manager.release_lease()
    manager.release_lease()
    assert not pool.is_busy("account")


async def test_cancelled_finish_closes_browser_and_releases_lease(manager):
    pool = BrowserPool()
    manager._lease = await pool.reserve_external("account")
    extracting = asyncio.Event()
    context = manager._context

    async def block(*args):
        extracting.set()
        await asyncio.Event().wait()

    context.cookies.side_effect = block
    finishing = asyncio.create_task(manager.finish_session(release_lease=False))
    await extracting.wait()
    finishing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await finishing
    context.close.assert_awaited_once()
    assert not pool.is_busy("account")
    assert not manager.is_active


async def test_cancelled_start_releases_external_reservation(monkeypatch):
    manager = LoginSessionManager()
    pool = BrowserPool()
    starting = asyncio.Event()
    context = SimpleNamespace(close=AsyncMock())

    async def block(*args):
        manager._context = context
        starting.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(manager, "_start_session", block)
    task = asyncio.create_task(manager.start_session("account", browser_pool=pool))
    await starting.wait()
    assert pool.is_busy("account")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    context.close.assert_awaited_once()
    assert not pool.is_busy("account")
    assert manager._lease is None


async def test_idle_timeout_does_not_cancel_or_await_itself(manager, monkeypatch):
    pool = BrowserPool()
    manager._lease = await pool.reserve_external("account")
    monkeypatch.setattr(login_module, "IDLE_TIMEOUT_SECONDS", 0)
    task = asyncio.create_task(manager._idle_timeout_task())
    manager._idle_task = task
    await asyncio.wait_for(task, timeout=1)
    assert not manager.is_active
    assert not pool.is_busy("account")


async def test_cancelled_cleanup_waits_for_browser_close_before_unlocking(manager):
    pool = BrowserPool()
    manager._lease = await pool.reserve_external("account")
    closing = asyncio.Event()
    closed = asyncio.Event()

    async def block():
        closing.set()
        await closed.wait()

    manager._context.close.side_effect = block
    task = asyncio.create_task(manager._cleanup())
    await closing.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert pool.is_busy("account")
    closed.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not pool.is_busy("account")
