"""EXT BOS — blokace do draw_right (vikendovy merge: birth < draw_right)."""
from __future__ import annotations

import pandas as pd
import pytest

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from strategy.ext_logic import (
    bos_triggered_for_ext_close,
    ext_bos_allowed_at_bar,
    ext_bos_visual_left_bar,
)


def test_ext_bos_allowed_at_bar_normal_ext():
    wave = {"draw_right": 46, "draw_left": 40, "ext_bos_level": 1.12, "dir": 1}
    assert not ext_bos_allowed_at_bar(wave, 45)
    assert ext_bos_allowed_at_bar(wave, 46)
    assert ext_bos_allowed_at_bar(wave, 50)


def test_ext_bos_allowed_at_bar_weekend_merge():
    """Birth pred draw_right — EXT BOS az po extrému."""
    wave = {"draw_right": 171, "draw_left": 134, "ext_bos_level": 1.123, "dir": 1}
    assert not ext_bos_allowed_at_bar(wave, 155)
    assert ext_bos_allowed_at_bar(wave, 171)


def test_bos_triggered_respects_gate_in_handler_pattern():
    wave = {
        "draw_right": 171,
        "ext_bos_level": 1.1232815,
        "dir": 1,
    }
    close_early = 1.11818
    assert bos_triggered_for_ext_close(wave, close_early)
    assert not ext_bos_allowed_at_bar(wave, 155)


def test_ext_bos_visual_left_bar_weekend():
    wave = {"draw_left": 134, "draw_right": 171}
    assert ext_bos_visual_left_bar(wave, birth_bar=20, draw_left=10, draw_right=37) == 37
    assert ext_bos_visual_left_bar(wave, birth_bar=40, draw_left=10, draw_right=37) == 10


def _may19_cfg():
    for combo in generate_combinations(get_profile("testing")):
        if (
            combo.get("wf_enabled")
            and combo.get("trend_hh_hl_filter_enabled")
            and combo.get("tp_mode") == "wave_target_n"
            and combo.get("wave_counter_two_sided_enabled") is False
        ):
            return grid_dict_to_bot_config(combo)
    raise RuntimeError("combo not found")


def test_may19_no_ext_bos_close_before_weekend_ext_draw_right():
    """May 19 05:30: EXT merge vlna 202505190400 — zadny EXT_BOS_CLOSE pred draw_right."""
    cfg = _may19_cfg()
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2025-05-14") & (df["time"] <= "2025-05-20")].reset_index(
        drop=True
    )
    eng = BacktestEngine(cfg)
    closed = eng.run(df, retain_wave_snapshot=True)

    wt = "202505190400"
    w = eng.waves_by_wave_time[wt]
    birth_bi = eng.wave_birth_by_time[wt]
    dr_bi = int(w["draw_right"])
    assert birth_bi < dr_bi, "precondition: weekend merge birth before draw_right"

    dr_time = df.iloc[dr_bi]["time"]
    early_closes = [
        t
        for t in closed
        if getattr(t, "close_reason", "") == "EXT_BOS_CLOSE"
        and str(getattr(t, "wave_time", "")) == wt
        and pd.Timestamp(t.close_time) < pd.Timestamp(dr_time)
    ]
    assert early_closes == [], (
        f"EXT_BOS_CLOSE pred draw_right {dr_time}: {early_closes}"
    )

    assert not ext_bos_allowed_at_bar(w, birth_bi)
    assert ext_bos_allowed_at_bar(w, dr_bi)
