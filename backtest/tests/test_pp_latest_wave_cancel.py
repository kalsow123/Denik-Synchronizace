"""PP: nova vlna ve smeru trendu rusi predchozi PP pending."""
from __future__ import annotations

import pandas as pd

from config.bot_config import BotConfig
from backtest.engine import BacktestEngine


def test_new_trend_wave_cancels_pp_replaced_vis():
    cfg = BotConfig(
        symbol="EURUSD",
        timeframe=30,
        wave_min_pct=0.26,
        min_opp_bars=3,
        entry_fib_level=0.5,
        sl_fib_level=0.8,
        rrr=2.0,
        wave_plus=True,
        pp_enabled=True,
        trend_filter_enabled=True,
        trend_hh_hl_filter_enabled=True,
    )
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2026-03-04") & (df["time"] <= "2026-03-07")].reset_index(
        drop=True
    )
    eng = BacktestEngine(cfg)
    eng.run(df)
    replaced = [e for e in eng.pending_vis if e.get("kind") == "pp_replaced"]
    assert replaced, "ocekavan pp_replaced pri nove vlne ve smeru trendu"
