"""Regrese EXT range pending ochrany + unit helper."""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from backtest.engine import BacktestEngine, PendingOrder
from config.bot_config import LIVE_BOT_CONFIG
from strategy.ext_range import pending_protected_from_bos_direction_cancel


def _make_pending(wave_time: str, dir_: int, *, ep: float = 1.1, sl: float = 1.099) -> PendingOrder:
    sig = {"wave_time": wave_time, "dir": dir_, "fib50": ep, "sl": sl}
    return PendingOrder(
        signal=sig,
        order_type=("BUY_LIMIT" if dir_ == 1 else "SELL_LIMIT"),
        entry_price=ep,
        sl=sl,
        tp=None,
        lot=0.01,
        created_bar=0,
        created_time=datetime(2026, 5, 1, 10, 0),
    )


def test_pending_protected_helper_in_ext_range():
    cfg = LIVE_BOT_CONFIG
    order = _make_pending("wt1", dir_=1)
    waves = {"wt1": {"wave_time": "wt1", "dir": 1, "in_ext_range": True}}
    assert pending_protected_from_bos_direction_cancel(order, cfg, waves_by_time=waves)


def test_pending_protected_helper_outside_ext_range():
    cfg = LIVE_BOT_CONFIG
    order = _make_pending("wt2", dir_=1)
    waves = {"wt2": {"wave_time": "wt2", "dir": 1, "in_ext_range": False}}
    assert not pending_protected_from_bos_direction_cancel(order, cfg, waves_by_time=waves)


def test_may29_ext_range_pending_fills_at_0830():
    """Vlna 202505290530: pending prezije 8:00 BOS cancel a fillne v 8:30."""
    cfg = LIVE_BOT_CONFIG
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    mask = (df["time"] >= "2025-05-28") & (df["time"] <= "2025-05-29 12:00:00")
    df = df[mask].reset_index(drop=True)

    eng = BacktestEngine(cfg)
    trades = eng.run(df, retain_wave_snapshot=True)

    filled = [
        t for t in trades
        if getattr(t, "wave_time", "") == "202505290530"
    ]
    assert filled, (
        "Ocekavan fill long z vlny 202505290530 po EXT range pending ochrane; "
        f"debug={eng.wave_debug.get('ext_range_pending_bos_cancel_skipped')}"
    )
    assert eng.wave_debug.get("ext_range_pending_bos_cancel_skipped", 0) >= 1
