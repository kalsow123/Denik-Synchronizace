"""BOS entry / pending cancel jen pri close-based flipu, ne pri seed-resetu."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from config.bot_config import LIVE_BOT_CONFIG
from strategy import trend_bos
from strategy.trend_bos import (
    collect_bos_flip_events,
    compute_close_based_bos_flip_bar_indices,
    find_close_bos_flip_for_target_since,
)
from strategy.wave_detection import detect_waves


def test_find_close_bos_flip_skips_seed_only_trend_change(monkeypatch):
    times = pd.date_range("2026-01-02 00:00", periods=8, freq="30min")
    df = pd.DataFrame(
        {
            "time": times,
            "open": [1.10, 1.11, 1.12, 1.13, 1.14, 1.15, 1.16, 1.17],
            "high": [1.11, 1.12, 1.13, 1.14, 1.15, 1.16, 1.17, 1.18],
            "low": [1.09, 1.10, 1.11, 1.12, 1.13, 1.14, 1.15, 1.16],
            "close": [1.10, 1.08, 1.12, 1.13, 1.14, 1.165, 1.16, 1.175],
        }
    )
    waves = [
        {
            "wave_time": "w0",
            "dir": 1,
            "box_top": 1.12,
            "box_bottom": 1.10,
            "draw_left": 0,
            "draw_right": 0,
        },
        {
            "wave_time": "w1",
            "dir": -1,
            "box_top": 1.11,
            "box_bottom": 1.09,
            "draw_left": 1,
            "draw_right": 1,
        },
        {
            "wave_time": "w2",
            "dir": 1,
            "box_top": 1.14,
            "box_bottom": 1.12,
            "draw_left": 2,
            "draw_right": 2,
            "ext_post_trend_seed_dir": -1,
        },
    ]
    monkeypatch.setattr(
        trend_bos,
        "compute_wave_birth_bars_pine",
        lambda _df, _cfg: {"w0": 0, "w1": 1, "w2": 3},
    )
    cfg = LIVE_BOT_CONFIG

    # Po seed baru (3) je trend bear, ale bez noveho close-BOS flipu do bear.
    hit = find_close_bos_flip_for_target_since(
        df,
        waves,
        cfg,
        target_direction="bear",
        after_time=times[2],
    )
    assert hit is None


def test_backtest_bos_reentry_count_near_close_bos_flip_bars():
    """Po oprave: pocet BOS re-entry ~= pocet close-BOS baru (viz HTML cary ±dedup)."""
    cfg = LIVE_BOT_CONFIG
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    df = df[(df["time"] >= "2026-03-01") & (df["time"] <= "2026-05-01")].reset_index(
        drop=True
    )
    waves = detect_waves(df, cfg)
    close_bars = compute_close_based_bos_flip_bar_indices(df, waves, cfg)
    bos_lines = len(collect_bos_flip_events(df, waves, cfg))

    eng = BacktestEngine(cfg)
    eng.run(df, retain_wave_snapshot=False)
    reentry = int(eng.wave_debug.get("bos_reentry_positions_opened", 0))

    # Drive 39 re-entry; po oprave jen close-BOS flip bary (34). Rozdil muze byt
    # ADX14 gate nebo chybejici broken_wave — ne seed-only flipy.
    assert reentry <= len(close_bars)
    assert reentry >= bos_lines - 5
    assert reentry < 39
