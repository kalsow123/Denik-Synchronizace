"""1A — golden regrese legacy backtest (LIVE_BOT_CONFIG, 2025-11-10 .. 2026-05-09).

Zamkne dnešní výsledek BacktestEngine v režimu legacy_precompute (jako dnes,
bez incremental detektoru) jako baseline před akcemi 1B–1D.

Referenční čísla z manuálu (VARIANTA A.txt §3.1 / §6):
  WAVE obchodů: 164
  net PnL:       +39 041 USD (zaokrouhleno)

Naměřeno na repu (resolve_grid_engine_config + data/EURUSD_M30.csv):
  trades_wave = 164 (shoda)
  net_pnl_wave_usd = 39040.88 (−0.12 USD vs zaokrouhlených +39 041)
"""
from __future__ import annotations

import pytest

from backtest.engine import BacktestEngine
from backtest.grid.data_cache import clear_cache, load_data
from backtest.stats import compute_stats, trades_to_df
from backtest.wave_sim_cache import clear_pine_sim_cache
from config.bot_config import LIVE_BOT_CONFIG
from config.position_modes import resolve_grid_engine_config

DATE_FROM = "2025-11-10"
DATE_TO = "2026-05-09"

# Golden baseline — WAVE slice (compute_stats, track_concurrent=True)
EXPECTED_TRADES_WAVE = 164
EXPECTED_NET_PNL_WAVE_USD = 39040.88


@pytest.fixture(autouse=True)
def _reset_caches():
    clear_cache()
    clear_pine_sim_cache()
    yield
    clear_cache()
    clear_pine_sim_cache()


def test_legacy_golden_regression_wave_baseline():
    cfg = resolve_grid_engine_config(
        LIVE_BOT_CONFIG,
        date_from=DATE_FROM,
        date_to=DATE_TO,
    )
    wave_mode = getattr(cfg, "wave_detection_mode", "legacy_precompute")
    assert wave_mode == "legacy_precompute"

    df = load_data(
        cfg.symbol,
        cfg.timeframe_label,
        DATE_FROM,
        DATE_TO,
    )
    assert not df.empty

    trades = BacktestEngine(cfg).run(df)
    stats = compute_stats(trades_to_df(trades), track_concurrent=True)

    assert stats["trades_wave"] == EXPECTED_TRADES_WAVE
    assert stats["net_pnl_wave_usd"] == EXPECTED_NET_PNL_WAVE_USD
