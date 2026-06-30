"""2E — LiveExecutor protokolova parita.

Overuje, ze `LiveExecutor` implementuje VSECHNY abstraktni metody `Executor`
(stejny seznam jako `backtest.tests.test_executor_protocol.REQUIRED_EXECUTOR_METHODS`)
a ze jde instanciovat (zadna abstraktni metoda nezbyla). Vse bez realneho MT5.
"""
from __future__ import annotations

import inspect

from backtest.executor import Executor
from backtest.tests.test_executor_protocol import REQUIRED_EXECUTOR_METHODS
from config.bot_config import LIVE_BOT_CONFIG
from runtime.live_executor import LiveExecutor


def test_live_executor_implements_all_abstract_methods():
    # Seznam abstraktnich metod Executor se musi shodovat s 1E REQUIRED seznamem.
    abstract_names = {
        name
        for name, value in inspect.getmembers(Executor)
        if getattr(value, "__isabstractmethod__", False)
    }
    assert abstract_names == set(REQUIRED_EXECUTOR_METHODS)

    # LiveExecutor nesmi mit zadne nedoplnene abstraktni metody.
    assert getattr(LiveExecutor, "__abstractmethods__", frozenset()) == frozenset()

    ex = LiveExecutor(LIVE_BOT_CONFIG)
    assert isinstance(ex, Executor)
    for name in REQUIRED_EXECUTOR_METHODS:
        assert callable(getattr(ex, name)), f"LiveExecutor chybi metoda {name}"


def test_live_executor_has_process_bar_maintenance_methods():
    ex = LiveExecutor(LIVE_BOT_CONFIG)
    for name in ("prune_pendings", "enforce_overflow", "expire_pendings"):
        assert callable(getattr(ex, name)), f"LiveExecutor chybi {name}"
