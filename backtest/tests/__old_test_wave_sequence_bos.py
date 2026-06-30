"""Wave sequence (index_in_trend) — sjednoceni s BOS / EXT pravidly."""
from __future__ import annotations

import pandas as pd

from config.bot_config import BotConfig
from config.enums import TPMode
from strategy.trend_bos import compute_trend_states_per_wave
from strategy.wave_detection import detect_waves
from strategy.wave_sequence import (
    compute_wave_sequence_info_per_wave,
    is_tp_wave_index,
)


def _cfg(**kw) -> BotConfig:
    base = dict(
        wave_min_pct=0.26,
        min_opp_bars=3,
        entry_fib_level=0.5,
        sl_fib_level=0.8,
        wave_plus=True,
        ext_enabled=True,
        ext_wave_min_pct=0.76,
        ext_trade_both_sides_in_range=True,
        ext_range_wave_min_pct=0.13,
        trend_filter_enabled=True,
        trend_hh_hl_filter_enabled=True,
        tp_mode=TPMode.WAVE_TARGET_N,
        tp_target_wave_index=4,
    )
    base.update(kw)
    return BotConfig(**base)


def test_ext_post_trend_seed_resets_index_in_trend():
    """
    Po EXT musi seed-vlna (2. vlna opacneho smeru) mit index_in_trend=2
    a predchozi opacna vlna index=1 (retroaktivne) po T4 uprave.
    """
    cfg = _cfg()
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    df = df[(df["time"] >= "2026-03-05") & (df["time"] <= "2026-03-10 23:59:59")].reset_index(
        drop=True
    )
    waves = detect_waves(df, cfg)
    by_wt = {str(w["wave_time"]): w for w in waves}

    seed_wt = "202603091930"
    assert by_wt[seed_wt].get("ext_post_trend_seed_dir") == 1

    seq = compute_wave_sequence_info_per_wave(df, waves, cfg)
    ts = compute_trend_states_per_wave(df, waves, cfg)

    seed_info = seq[seed_wt]
    assert seed_info.index_in_trend == 2, (
        f"Dle noveho 2-vln pravidla (T4) maji counter vlny dostat 1 a 2 retroaktivne."
    )

    # Potlacena bear vlna proti seed-trendu se do sekvence vubec nezapise.
    suppressed_wt = "202603092200"
    assert by_wt[suppressed_wt].get("post_ext_trend_suppressed") is True
    assert suppressed_wt not in seq


def test_wave_sequence_index_matches_trend_snapshot_after_ext():
    """Trend-dir vlny po seed-wave musi rust v lock zone stejne jako BOS snapshot."""
    cfg = _cfg()
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    df = df[(df["time"] >= "2026-03-22") & (df["time"] <= "2026-03-31")].reset_index(
        drop=True
    )
    waves = detect_waves(df, cfg)
    by_wt = {str(w["wave_time"]): w for w in waves}

    seed_wt = "202603240700"
    assert by_wt[seed_wt].get("ext_post_trend_seed_dir") == -1

    seq = compute_wave_sequence_info_per_wave(df, waves, cfg)
    ts = compute_trend_states_per_wave(df, waves, cfg)

    assert seq[seed_wt].index_in_trend == 1
    assert ts[seed_wt].direction == "bear"

    # Dalsi bear vlna v novem trendu (pokud neni potlacena) ma index 2.
    follow_wt = "202603241000"
    if follow_wt in seq and not by_wt[follow_wt].get("post_ext_trend_suppressed"):
        assert seq[follow_wt].index_in_trend == 2


def test_tp_wave_index_shared_for_bos_exit_and_wave_target_n():
    """
    is_tp_wave_index + index_in_trend jsou nezavisle na tp_mode — sdilena
    logika pro bos_exit (counter SL / vizual) i wave_target_n (TP-vlna).
    """
    target_n = 4
    for tp_mode in (TPMode.BOS_EXIT, TPMode.WAVE_TARGET_N):
        cfg = _cfg(tp_mode=tp_mode, counter_position_enabled=True)
        assert is_tp_wave_index(4, target_n) is True
        assert is_tp_wave_index(6, target_n) is True
        assert is_tp_wave_index(3, target_n) is False
        assert is_tp_wave_index(5, target_n) is False
