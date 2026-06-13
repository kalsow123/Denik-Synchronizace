"""Obchody bez wave boxu — supplement_visual_waves_for_trades doplní jen vizuál."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from backtest.visual_waves import (
    build_wave_visual_bundle,
    supplement_visual_waves_for_trades,
    visual_params_from_combo_and_args,
)
from backtest.waves_plotly_figure import _wave_visible_in_html_plot


def _testing_combo_bos_off():
    for combo in generate_combinations(get_profile("testing")):
        if (
            combo.get("bos_entry_enable") is False
            and combo.get("pp_enabled") is False
            and combo.get("wave_counter_two_sided_enabled") is True
        ):
            return combo
    raise AssertionError("testing combo not found")


def _load_df(combo: dict) -> pd.DataFrame:
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    return df[
        (df["time"] >= combo["date_from"]) & (df["time"] <= combo["date_to"])
    ].reset_index(drop=True)


def test_supplement_covers_all_non_bos_trades_with_visible_wave():
    combo = _testing_combo_bos_off()
    df = _load_df(combo)
    cfg = grid_dict_to_bot_config(combo)
    eng = BacktestEngine(cfg)
    trades = eng.run(df, retain_wave_snapshot=True)

    _, _, _, _, full_span = visual_params_from_combo_and_args(
        combo, cli_visual_waves=True, cli_plotly=True
    )
    wave_seq = eng.wave_sequence_info
    bundle = build_wave_visual_bundle(
        df,
        list(eng.last_waves_for_visual or []),
        eng.wave_birth_by_time,
        trades,
        full_span=full_span,
        pending_vis=eng.pending_vis,
        wave_seq_by_time=wave_seq,
    )
    assert bundle is not None

    before = len(bundle.waves)
    supplement_visual_waves_for_trades(
        bundle,
        last_waves=list(eng.last_waves or []),
        all_waves=list(eng._all_waves or []),
        wave_birth=eng.wave_birth_by_time,
        wave_seq_by_time=wave_seq,
        pending_vis=eng.pending_vis,
        df_full=df,
    )
    assert len(bundle.waves) > before

    bos_times = set(getattr(eng, "_visual_bos_wave_times", None) or set())
    vis_wt = {
        str(w.get("wave_time"))
        for w in bundle.waves
        if _wave_visible_in_html_plot(w, cfg, bos_wave_times=bos_times)
    }
    missing = [
        t
        for t in bundle.trades
        if not str(getattr(t, "wave_time", "")).startswith("BOS_REENTRY_")
        and str(getattr(t, "wave_time", "")) not in vis_wt
    ]
    assert not missing, (
        "po supplement chybí wave box pro: "
        + ", ".join(str(getattr(t, "wave_time", "")) for t in missing[:5])
    )

    anchors = [w for w in bundle.waves if w.get("_visual_trade_anchor")]
    assert 10 <= len(anchors) <= 20
    assert any(w.get("_visual_reconstructed") for w in anchors)
