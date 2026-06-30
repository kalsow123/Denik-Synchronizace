"""1F — incremental reference PnL + determinismus (snapshot).

Zamkne referencni vysledek BacktestEngine v rezimu incremental_causal
(LIVE_BOT_CONFIG pres resolve_grid_engine_config, posledni 2 roky dat).

Re-baseline na 2leté okno (BACKTEST_WINDOW_YEARS=2); dříve 6měsíční
2025-11-10..2026-05-09 = 164/+39040.88 resp. 147/+38100.69.

Okno = posledni 2 roky (BACKTEST_WINDOW_YEARS) odvozene z posledniho baru
datasetu (EURUSD M30 2024-05-20 .. 2026-05-18, ~24 814 baru).

Naměřeno na 2letém okně (akce re-baseline):
  trades_wave = 640
  net_pnl_wave_usd = 124461.68
  max_drawdown_pct_wave = -10.34
"""
from __future__ import annotations

import functools
from dataclasses import replace

import pytest

from backtest.data_loader import default_backtest_date_range, load_csv
from backtest.engine import BacktestEngine
from backtest.grid.data_cache import clear_cache, csv_path_for, load_data
from backtest.stats import compute_stats, trades_to_df
from backtest.wave_sim_cache import clear_pine_sim_cache
from config.bot_config import LIVE_BOT_CONFIG
from config.enums import WaveDetectionMode
from config.position_modes import resolve_grid_engine_config

# Celý modul je pomalý (2letý backtest ~4–5 min) — gate ho pouští jen bez -m "not slow".
pytestmark = pytest.mark.slow


@functools.lru_cache(maxsize=1)
def _window() -> tuple[str | None, str | None]:
    """Centralni 2-lete okno odvozene z datasetu (BACKTEST_WINDOW_YEARS)."""
    df_full = load_csv(csv_path_for(LIVE_BOT_CONFIG.symbol, LIVE_BOT_CONFIG.timeframe_label))
    return default_backtest_date_range(df_full)


EXPECTED_TRADES_WAVE = 640
EXPECTED_NET_PNL_WAVE_USD = 124461.68
EXPECTED_MAX_DD_PCT_WAVE = -10.34


def _incremental_cfg():
    date_from, date_to = _window()
    cfg = resolve_grid_engine_config(
        LIVE_BOT_CONFIG,
        date_from=date_from,
        date_to=date_to,
    )
    cfg = replace(cfg, wave_detection_mode=WaveDetectionMode.INCREMENTAL_CAUSAL)
    assert cfg.causal_mode is True
    assert cfg.wave_detection_mode == WaveDetectionMode.INCREMENTAL_CAUSAL
    return cfg


def _run_incremental_backtest():
    cfg = _incremental_cfg()
    date_from, date_to = _window()
    df = load_data(cfg.symbol, cfg.timeframe_label, date_from, date_to)
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
