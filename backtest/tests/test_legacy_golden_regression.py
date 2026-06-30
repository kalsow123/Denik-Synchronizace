"""1A — golden regrese legacy backtest (LIVE_BOT_CONFIG, posledni 2 roky dat).

Zamkne dnešní výsledek BacktestEngine v režimu legacy_precompute (jako dnes,
bez incremental detektoru) jako baseline.

Re-baseline na 2leté okno (BACKTEST_WINDOW_YEARS=2); dříve 6měsíční
2025-11-10..2026-05-09 = 164/+39040.88 resp. 147/+38100.69.

Okno = posledni 2 roky (BACKTEST_WINDOW_YEARS) odvozene z posledniho baru
datasetu (centralni zdroj: backtest.data_loader.default_backtest_date_range),
tj. EURUSD M30 2024-05-20 .. 2026-05-18 (~24 814 baru).

Naměřeno na repu (resolve_grid_engine_config + data/EURUSD_M30.csv, 2leté okno):
  trades_wave = 751
  net_pnl_wave_usd = 279156.28
"""
from __future__ import annotations

import functools

import pytest

from backtest.data_loader import default_backtest_date_range, load_csv
from backtest.engine import BacktestEngine
from backtest.grid.data_cache import clear_cache, csv_path_for, load_data
from backtest.stats import compute_stats, trades_to_df
from backtest.wave_sim_cache import clear_pine_sim_cache
from config.bot_config import LIVE_BOT_CONFIG
from config.position_modes import resolve_grid_engine_config

# Celý modul je pomalý (2letý backtest ~4–5 min) — gate ho pouští jen bez -m "not slow".
pytestmark = pytest.mark.slow


@functools.lru_cache(maxsize=1)
def _window() -> tuple[str | None, str | None]:
    """Centralni 2-lete okno odvozene z datasetu (BACKTEST_WINDOW_YEARS)."""
    df_full = load_csv(csv_path_for(LIVE_BOT_CONFIG.symbol, LIVE_BOT_CONFIG.timeframe_label))
    return default_backtest_date_range(df_full)


# Golden baseline — WAVE slice (compute_stats, track_concurrent=True)
EXPECTED_TRADES_WAVE = 751
EXPECTED_NET_PNL_WAVE_USD = 279156.28


@pytest.fixture(autouse=True)
def _reset_caches():
    clear_cache()
    clear_pine_sim_cache()
    yield
    clear_cache()
    clear_pine_sim_cache()


def test_legacy_golden_regression_wave_baseline():
    date_from, date_to = _window()
    cfg = resolve_grid_engine_config(
        LIVE_BOT_CONFIG,
        date_from=date_from,
        date_to=date_to,
    )
    wave_mode = getattr(cfg, "wave_detection_mode", "legacy_precompute")
    assert wave_mode == "legacy_precompute"

    df = load_data(
        cfg.symbol,
        cfg.timeframe_label,
        date_from,
        date_to,
    )
    assert not df.empty

    trades = BacktestEngine(cfg).run(df)
    stats = compute_stats(trades_to_df(trades), track_concurrent=True)

    assert stats["trades_wave"] == EXPECTED_TRADES_WAVE
    assert stats["net_pnl_wave_usd"] == EXPECTED_NET_PNL_WAVE_USD
