"""LIVE_BOT_CONFIG: PP pending jen ve smeru trendu v okamziku breaku (placement bar)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from config.bot_config import LIVE_BOT_CONFIG
from backtest.engine import BacktestEngine
from strategy.trend_bos import compute_trend_states_per_bar
from strategy.wave_detection import detect_waves


@pytest.mark.skipif(
    not Path("data/EURUSD_M30.csv").exists(),
    reason="EURUSD M30 CSV not present",
)
def test_live_config_zero_pp_counter_trend_at_placement():
    cfg = LIVE_BOT_CONFIG
    if not cfg.pp_enabled:
        pytest.skip("pp_enabled off")
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2026-03-01") & (df["time"] <= "2026-04-30")].reset_index(
        drop=True
    )
    eng = BacktestEngine(cfg)
    trades = eng.run(df)
    pp_trades = [t for t in trades if getattr(t, "is_pp", False)]

    waves = detect_waves(df, cfg)
    bar_states = compute_trend_states_per_bar(df, waves, cfg)

    counter = []
    for t in pp_trades:
        entry_bar = int(t.close_bar) - int(t.bars_held)
        if entry_bar < 0 or entry_bar >= len(bar_states):
            continue
        trend = bar_states[entry_bar].direction
        trade_trend = "bull" if int(t.dir) == 1 else "bear"
        if trend in ("bull", "bear") and trade_trend != trend:
            counter.append(
                (t.entry_time, trade_trend, trend, entry_bar)
            )

    assert counter == [], f"counter-trend PP at placement: {counter}"
    assert eng.wave_debug.get("pp_skipped_trend_from_seed_reset", 0) > 0, (
        "ocekavan alespon jeden skip seed-reset vs close-BOS"
    )
