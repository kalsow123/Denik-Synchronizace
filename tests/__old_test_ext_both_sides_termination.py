"""T4 tests: Mechanizmy ukonceni EXT both-sides a retro-indexovani."""
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
        ext_enabled=True,
        ext_trade_both_sides_in_range=True,
    )


def _df(n: int, overrides: dict[int, float], base_close: float = 1.11) -> pd.DataFrame:
    closes = [base_close] * n
    for i, val in overrides.items():
        closes[i] = val
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
    is_ext: bool = False,
    ext_high: float = 0.0,
    ext_low: float = 0.0,
    ext_bos_level: float = 0.0,
    ext_post_trend_seed_dir: int | None = None,
    move_pct: float = 0.5,
) -> dict:
    w = {
        "wave_time": wt,
        "dir": wdir,
        "draw_right": draw_right,
        "draw_left": max(0, draw_right - 3),
        "box_top": box_top,
        "box_bottom": box_bottom,
        "move_pct": move_pct,
    }
    if is_ext:
        w["is_ext"] = True
        w["ext_high"] = ext_high or box_top
        w["ext_low"] = ext_low or box_bottom
        w["ext_bos_level"] = ext_bos_level
    if ext_post_trend_seed_dir is not None:
        w["ext_post_trend_seed_dir"] = ext_post_trend_seed_dir
    return w


def test_close_above_ext_high_ends_both_sides():
    # EXT UP wave, pak bar prorazi ext_high.
    waves = [
        _wave("ext_up", wdir=1, draw_right=5, box_top=1.20, box_bottom=1.10, is_ext=True, ext_high=1.20, ext_bos_level=1.15),
        _wave("dn1", wdir=-1, draw_right=15, box_top=1.19, box_bottom=1.11),
        _wave("dn2", wdir=-1, draw_right=20, box_top=1.18, box_bottom=1.10, ext_post_trend_seed_dir=-1), # 2. counter, ale po ukonceni
    ]
    # base_close = 1.16 aby se neprorazil ext_bos_level (1.15) dolu.
    # Na baru 10 close > 1.20 -> both sides konci (Mechanismus A)
    df = _df(25, {10: 1.21}, base_close=1.16)

    seq = compute_wave_sequence_info_per_wave(df, waves, _cfg())
    
    assert seq["ext_up"].index_in_trend == 1
    assert seq["dn1"].index_in_trend is None
    # Protoze both-sides skoncil na baru 10, dn2 uz nedostane retro-index, i kdyz ma seed tag.
    # Seed tag ovsem zpusobi flip trendu (standardni chovani), takze dn2 se stane prvni vlnou noveho bear trendu!
    assert seq["dn2"].index_in_trend == 1


def test_two_counter_waves_after_ext_become_idx_1_2():
    # EXT UP, DN1, DN2 -> DN1 a DN2 dostanou 1 a 2.
    waves = [
        _wave("ext_up", wdir=1, draw_right=5, box_top=1.20, box_bottom=1.10, is_ext=True, ext_high=1.20, ext_bos_level=1.15),
        _wave("dn1", wdir=-1, draw_right=10, box_top=1.19, box_bottom=1.16),
        _wave("dn2", wdir=-1, draw_right=15, box_top=1.18, box_bottom=1.14, ext_post_trend_seed_dir=-1),
    ]
    df = _df(20, {}, base_close=1.16)

    seq = compute_wave_sequence_info_per_wave(df, waves, _cfg())

    assert seq["ext_up"].index_in_trend == 1
    assert seq["dn1"].index_in_trend == 1
    assert seq["dn2"].index_in_trend == 2
    assert seq["dn2"].prev_same_dir_in_trend_wave_time == "dn1"


def test_ext_bos_via_fib_35_ends_both_sides():
    # EXT UP, na baru 10 prorazi bos level (1.15)
    waves = [
        _wave("ext_up", wdir=1, draw_right=5, box_top=1.20, box_bottom=1.10, is_ext=True, ext_high=1.20, ext_bos_level=1.15),
        _wave("dn1", wdir=-1, draw_right=15, box_top=1.19, box_bottom=1.11),
    ]
    df = _df(20, {10: 1.14}, base_close=1.16)

    seq = compute_wave_sequence_info_per_wave(df, waves, _cfg())

    assert seq["ext_up"].index_in_trend == 1
    # Na baru 10 flipnul trend pres mechanismus C (bos_triggered_for_ext_close).
    # Na baru 15 se narodi DN1, trend je bear. DN1 je prvni vlna bear trendu.
    assert seq["dn1"].index_in_trend == 1


def test_ext_post_trend_seed_priority_vs_new_rules():
    # EXT UP, DN1, DN2 ktera zaroven prorazi i klasicky BOS.
    # DN2 musi dostat idx=2 a DN1 idx=1.
    waves = [
        _wave("ext_up", wdir=1, draw_right=5, box_top=1.20, box_bottom=1.10, is_ext=True, ext_high=1.20, ext_bos_level=1.15),
        _wave("dn1", wdir=-1, draw_right=10, box_top=1.19, box_bottom=1.16),
        _wave("dn2", wdir=-1, draw_right=15, box_top=1.18, box_bottom=1.09, ext_post_trend_seed_dir=-1), # prorazi 1.10 (klasicky BOS swing)
    ]
    df = _df(20, {15: 1.09}, base_close=1.16)

    seq = compute_wave_sequence_info_per_wave(df, waves, _cfg())

    assert seq["dn1"].index_in_trend == 1
    assert seq["dn2"].index_in_trend == 2


def test_ext_then_two_counter_in_existing_trend():
    # EXT v bezicim trendu, napr. bear trend a DOWN EXT vlna.
    # DOWN1, DOWN2(EXT), UP1, UP2(seed). 
    # Ocekavani: DOWN1(1), DOWN2(2), UP1(1), UP2(2) v novem bull trendu.
    waves = [
        _wave("dn1", wdir=-1, draw_right=5, box_top=1.30, box_bottom=1.25),
        _wave("ext_dn", wdir=-1, draw_right=10, box_top=1.28, box_bottom=1.10, is_ext=True, ext_low=1.10, ext_bos_level=1.15),
        _wave("up1", wdir=1, draw_right=15, box_top=1.14, box_bottom=1.11),
        _wave("up2", wdir=1, draw_right=20, box_top=1.16, box_bottom=1.12, ext_post_trend_seed_dir=1),
    ]
    df = _df(25, {1: 1.30, 10: 1.10}, base_close=1.13) # startovni bar pro trigger "bear" trend state

    seq = compute_wave_sequence_info_per_wave(df, waves, _cfg())

    assert seq["dn1"].index_in_trend == 1
    assert seq["ext_dn"].index_in_trend == 2
    assert seq["up1"].index_in_trend == 1
    assert seq["up2"].index_in_trend == 2
    assert seq["up2"].prev_same_dir_in_trend_wave_time == "up1"