"""1F — incremental reference PnL + determinismus (snapshot).

Zamkne referencni vysledek BacktestEngine v rezimu incremental_causal
(LIVE_BOT_CONFIG pres resolve_grid_engine_config, 2025-11-10 .. 2026-05-09).

Naměřeno na varianta-a-faze-e (akce 1F):
  trades_wave = 147
  net_pnl_wave_usd = 38100.69
  max_drawdown_pct_wave = -8.61
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from backtest.engine import BacktestEngine
from backtest.grid.data_cache import clear_cache, load_data
from backtest.stats import compute_stats, trades_to_df
from backtest.wave_sim_cache import clear_pine_sim_cache
from config.bot_config import LIVE_BOT_CONFIG
from config.enums import WaveDetectionMode
from config.position_modes import resolve_grid_engine_config

DATE_FROM = "2025-11-10"
DATE_TO = "2026-05-09"

EXPECTED_TRADES_WAVE = 147
EXPECTED_NET_PNL_WAVE_USD = 38100.69
EXPECTED_MAX_DD_PCT_WAVE = -8.61


def _incremental_cfg():
    cfg = resolve_grid_engine_config(
        LIVE_BOT_CONFIG,
        date_from=DATE_FROM,
        date_to=DATE_TO,
    )
    cfg = replace(cfg, wave_detection_mode=WaveDetectionMode.INCREMENTAL_CAUSAL)
    assert cfg.causal_mode is True
    assert cfg.wave_detection_mode == WaveDetectionMode.INCREMENTAL_CAUSAL
    return cfg


def _run_incremental_backtest():
    cfg = _incremental_cfg()
    df = load_data(cfg.symbol, cfg.timeframe_label, DATE_FROM, DATE_TO)
    assert not df.empty
    trades = BacktestEngine(cfg).run(df)
    stats = compute_stats(trades_to_df(trades), track_concurrent=True)
    return stats


@pytest.fixture(autouse=True)
def _reset_caches():
    clear_cache()
    clear_pine_sim_cache()
    yield
    clear_cache()
    clear_pine_sim_cache()


def test_incremental_reference_snapshot_wave_baseline():
    stats = _run_incremental_backtest()
    assert stats["trades_wave"] == EXPECTED_TRADES_WAVE
    assert stats["net_pnl_wave_usd"] == EXPECTED_NET_PNL_WAVE_USD
    assert stats["max_drawdown_pct_wave"] == EXPECTED_MAX_DD_PCT_WAVE


def test_incremental_reference_deterministic_on_repeat():
    stats_a = _run_incremental_backtest()
    stats_b = _run_incremental_backtest()
    assert stats_a["trades_wave"] == stats_b["trades_wave"]
    assert stats_a["net_pnl_wave_usd"] == stats_b["net_pnl_wave_usd"]
    assert stats_a["max_drawdown_pct_wave"] == stats_b["max_drawdown_pct_wave"]
