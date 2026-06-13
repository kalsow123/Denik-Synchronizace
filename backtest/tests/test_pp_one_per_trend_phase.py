"""PP: jen nejnovejsi vlna v trendu; regrese Mar 6 (2× PP ze starych vln)."""
from __future__ import annotations

import pandas as pd

from config.bot_config import BotConfig
from backtest.engine import BacktestEngine


def _cfg() -> BotConfig:
    return BotConfig(
        symbol="EURUSD.x",
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


def test_mar6_at_most_one_pp_while_bear_trend_continues():
    """6. 3. 2026: drive 2× PP ve stejne bear fazi (15:00 + 15:30); ma byt max 1."""
    cfg = _cfg()
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2026-03-04") & (df["time"] <= "2026-03-07")].reset_index(
        drop=True
    )
    eng = BacktestEngine(cfg)
    trades = eng.run(df)
    mar6 = pd.Timestamp("2026-03-06").date()
    pp_mar6 = [
        t
        for t in trades
        if getattr(t, "is_pp", False) and pd.Timestamp(t.entry_time).date() == mar6
    ]
    assert len(pp_mar6) <= 1, (
        f"ocekavan max 1 PP na 6.3. v jedne bear fazi, dost {len(pp_mar6)}: "
        f"{[(t.entry_time, t.wave_time) for t in pp_mar6]}"
    )
