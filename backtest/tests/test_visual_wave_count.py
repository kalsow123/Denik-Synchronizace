"""Acceptance: last_waves_for_visual nesmi obsahovat sumove/counter-trend vlny."""
from __future__ import annotations

import pandas as pd

from config.bot_config import LIVE_BOT_CONFIG
from backtest.engine import BacktestEngine
from strategy.ext_logic import is_ext_wave
from strategy.wick_fakeout import WAVE_ORIGIN_WF


def _load_segment() -> pd.DataFrame:
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    return df[
        (df["time"] >= "2026-03-03") & (df["time"] <= "2026-05-08")
    ].reset_index(drop=True)


def _visual_exception(w: dict, eng: BacktestEngine, cfg) -> bool:
    wt = str(w.get("wave_time", "") or "")
    if w.get("is_ext") or is_ext_wave(w, cfg):
        return True
    if str(w.get("wave_origin", "")) == WAVE_ORIGIN_WF or w.get("is_wf"):
        return True
    if wt in set(getattr(eng, "_visual_bos_wave_times", set()) or ()) or wt in set(
        getattr(eng, "_bos_wave_times", set()) or ()
    ):
        return True
    if (
        w.get("_two_sided_counter")
        or w.get("two_sided_show")
        or w.get("is_two_sided_counter")
        or wt in set(getattr(eng, "_two_sided_fired_wave_times", set()) or ())
    ):
        return True
    if w.get("in_ext_range") and getattr(cfg, "ext_trade_both_sides_in_range", False):
        return True
    return False


def test_live_config_visual_wave_count_and_no_counter_trend_leaks():
    cfg = LIVE_BOT_CONFIG
    eng = BacktestEngine(cfg)
    eng.run(_load_segment(), retain_wave_snapshot=True)

    all_w = eng.last_waves
    vis = eng.last_waves_for_visual
    assert len(all_w) > len(vis), "visual set must be smaller than full wave set"

    # LIVE segment: po filtru zustava ~100-150 strukturalnich + BOS/EXT/WF/two-sided vln.
    assert 80 <= len(vis) <= 150, f"unexpected visual wave count: {len(vis)}"

    leaks = [
        w
        for w in vis
        if w.get("hh_hl_pass") is False and not _visual_exception(w, eng, cfg)
    ]
    assert not leaks, (
        "counter-trend vlny ve visual setu: "
        + ", ".join(str(w.get("wave_time")) for w in leaks[:10])
    )

    # post_ext_trend_suppressed vlna smi byt ve visual setu jen pokud je to
    # BOS vlna NEBO in_ext_range vlna (EXT obousmerny rezim ma prednost — bod 1).
    bos_vis = set(getattr(eng, "_visual_bos_wave_times", set()) or ())
    both_sides = getattr(cfg, "ext_trade_both_sides_in_range", False)
    assert not any(
        w.get("post_ext_trend_suppressed")
        and str(w.get("wave_time", "")) not in bos_vis
        and not (w.get("in_ext_range") and both_sides)
        for w in vis
    )
    assert not any(w.get("post_ext_confirmed_trend_lock") for w in vis)
