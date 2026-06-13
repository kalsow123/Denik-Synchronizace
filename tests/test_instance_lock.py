"""Testy zámku live instance (bez MT5)."""
from __future__ import annotations

import os
from dataclasses import replace

import pytest

from config.bot_config import LIVE_BOT_CONFIG
from runtime.instance_lock import LiveInstanceAlreadyRunning, LiveInstanceLock


@pytest.fixture
def lock_cfg():
    return replace(
        LIVE_BOT_CONFIG,
        bot_name="TEST_LOCK_BOT",
        symbol="EURUSD.x",
        magic=999_999,
    )


def test_second_acquire_raises(lock_cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    first = LiveInstanceLock(lock_cfg)
    first.acquire()
    try:
        second = LiveInstanceLock(lock_cfg)
        with pytest.raises(LiveInstanceAlreadyRunning):
            second.acquire()
    finally:
        first.release()


def test_release_allows_reacquire(lock_cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    lock = LiveInstanceLock(lock_cfg)
    lock.acquire()
    lock.release()
    lock2 = LiveInstanceLock(lock_cfg)
    lock2.acquire()
    lock2.release()
