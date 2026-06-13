"""T3: BOS vlna ma index_in_trend=1 v novem trendu a flag is_bos_wave=True."""
from __future__ import annotations

import pandas as pd

from config.bot_config import BotConfig
from strategy.wave_sequence import compute_wave_sequence_info_per_wave


def _cfg() -> BotConfig:
    return BotConfig(
        wave_min_pct=0.26,
        min_opp_bars=3,
        trend_filter_enabled=True,
        trend_hh_hl_filter_enabled=True,
    )


def _df(n: int, *, bos_bar: int, bos_close: float, default_close: float = 1.11) -> pd.DataFrame:
    closes = [default_close] * n
    closes[bos_bar] = bos_close
    return pd.DataFrame(
        {
            "time": pd.date_range("2026-03-01 00:00", periods=n, freq="30min"),
            "open": closes,
            "high": [c + 0.01 for c in closes],
            "low": [c - 0.01 for c in closes],
            "close": closes,
        }
    )


def _wave(
    wt: str,
    *,
    wdir: int,
    draw_right: int,
    box_top: float,
    box_bottom: float,
) -> dict:
    return {
        "wave_time": wt,
        "dir": wdir,
        "draw_right": draw_right,
        "draw_left": draw_right - 3,
        "box_top": box_top,
        "box_bottom": box_bottom,
    }


def _bull_setup_waves(*, bos_down_bar: int) -> list[dict]:
    return [
        _wave("up1", wdir=1, draw_right=5, box_top=1.10, box_bottom=1.05),
        _wave("up2", wdir=1, draw_right=10, box_top=1.15, box_bottom=1.08),
        _wave("up3", wdir=1, draw_right=15, box_top=1.20, box_bottom=1.10),
        _wave(
            "down_bos",
            wdir=-1,
            draw_right=bos_down_bar,
            box_top=1.12,
            box_bottom=1.07,
        ),
    ]


def test_bos_wave_bull_to_bear_gets_index_1_and_flag():
    """UP1, UP2, UP3 v bull → DOWN BOS na baru s close < UP3.box_bottom."""
    bos_bar = 20
    waves = _bull_setup_waves(bos_down_bar=bos_bar)
    df = _df(30, bos_bar=bos_bar, bos_close=1.06)

    seq = compute_wave_sequence_info_per_wave(df, waves, _cfg())
    by_wt = {str(w["wave_time"]): w for w in waves}

    assert seq["up1"].index_in_trend == 1
    assert seq["up2"].index_in_trend == 2
    assert seq["up3"].index_in_trend == 3
    assert seq["down_bos"].index_in_trend == 1
    assert seq["down_bos"].is_bos_wave is True


def test_bos_wave_bear_to_bull_gets_index_1_and_flag():
    """DOWN1, DOWN2, DOWN3 v bear → UP BOS na baru s close > DOWN3.box_top."""
    bos_bar = 20
    waves = [
        _wave("dn1", wdir=-1, draw_right=5, box_top=1.10, box_bottom=1.05),
        _wave("dn2", wdir=-1, draw_right=10, box_top=1.08, box_bottom=1.03),
        _wave("dn3", wdir=-1, draw_right=15, box_top=1.06, box_bottom=1.00),
        _wave("up_bos", wdir=1, draw_right=bos_bar, box_top=1.12, box_bottom=1.07),
    ]
    df = _df(30, bos_bar=bos_bar, bos_close=1.07, default_close=1.05)

    seq = compute_wave_sequence_info_per_wave(df, waves, _cfg())
    by_wt = {str(w["wave_time"]): w for w in waves}

    assert seq["dn1"].index_in_trend == 1
    assert seq["dn2"].index_in_trend == 2
    assert seq["dn3"].index_in_trend == 3
    assert seq["up_bos"].index_in_trend == 1
    assert seq["up_bos"].is_bos_wave is True

def test_gap_bos_without_wave_next_trend_dir_wave_is_index_1_without_flag():
    """BOS flip na baru bez vlny → dalsi trend-dir vlna je idx=1, ne BOS flag."""
    gap_bar = 20
    follow_bar = 25
    waves = [
        _wave("up1", wdir=1, draw_right=5, box_top=1.10, box_bottom=1.05),
        _wave("up2", wdir=1, draw_right=10, box_top=1.15, box_bottom=1.08),
        _wave("up3", wdir=1, draw_right=15, box_top=1.20, box_bottom=1.10),
        _wave(
            "down_follow",
            wdir=-1,
            draw_right=follow_bar,
            box_top=1.12,
            box_bottom=1.07,
        ),
    ]
    df = _df(30, bos_bar=gap_bar, bos_close=1.06)

    seq = compute_wave_sequence_info_per_wave(df, waves, _cfg())
    by_wt = {str(w["wave_time"]): w for w in waves}

    assert seq["up3"].index_in_trend == 3
    assert seq["down_follow"].index_in_trend == 1
    assert seq["down_follow"].is_bos_wave is True
