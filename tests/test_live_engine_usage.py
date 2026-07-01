"""FAZE 3, akce 3C — `cfg.live_engine_usage` dispatch v `runtime.live_loop.run_live_loop()`.

Overuje:
  - `LIVE_BOT_CONFIG.live_engine_usage` je default `LiveEngineUsage.BACKTESTER`.
  - `run_live_loop()` s `LiveEngineUsage.E2E` deleguje na
    `runtime.live_loop_legacy.run_live_loop()`, NE na `_run_live_loop_backtester()`.
  - `run_live_loop()` s `LiveEngineUsage.BACKTESTER` vola `_run_live_loop_backtester()`,
    NE legacy cestu.

Vse mock/patch — bez skutecneho MT5 pripojeni.
"""
from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

from config.bot_config import LIVE_BOT_CONFIG
from config.enums import LiveEngineUsage
import runtime.live_loop as live_loop
import runtime.live_loop_legacy as live_loop_legacy


def test_live_bot_config_default_engine_usage_is_backtester() -> None:
    assert LIVE_BOT_CONFIG.live_engine_usage == LiveEngineUsage.BACKTESTER


def test_run_live_loop_e2e_dispatches_to_legacy() -> None:
    cfg = replace(LIVE_BOT_CONFIG, live_engine_usage=LiveEngineUsage.E2E)
    sent_signals: set[str] = set()

    with patch.object(live_loop_legacy, "run_live_loop") as mock_legacy, \
         patch.object(live_loop, "_run_live_loop_backtester") as mock_backtester:
        live_loop.run_live_loop(cfg, sent_signals, json_log_file="dummy.jsonl")

    mock_legacy.assert_called_once_with(cfg, sent_signals, json_log_file="dummy.jsonl")
    mock_backtester.assert_not_called()


def test_run_live_loop_backtester_dispatches_to_backtester_path() -> None:
    cfg = replace(LIVE_BOT_CONFIG, live_engine_usage=LiveEngineUsage.BACKTESTER)
    sent_signals: set[str] = set()

    with patch.object(live_loop, "_run_live_loop_backtester") as mock_backtester, \
         patch.object(live_loop_legacy, "run_live_loop") as mock_legacy:
        live_loop.run_live_loop(cfg, sent_signals, json_log_file="dummy.jsonl")

    mock_backtester.assert_called_once_with(cfg, sent_signals, json_log_file="dummy.jsonl")
    mock_legacy.assert_not_called()
