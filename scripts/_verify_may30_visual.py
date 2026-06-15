"""Verify visual HTML for May29 12:00 - Jun4 window (testing combo)."""
from __future__ import annotations

import os

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from backtest.visual_waves import (
    build_wave_visual_bundle,
    supplement_visual_waves_for_trades,
)
from backtest.visual_wave_filter import wave_passes_visual_filter
from backtest.waves_plotly_figure import _wave_visible_in_html_plot
from backtest.plotting import plot_waves_structure
from backtest.run_backtest import _bos_flip_events_in_window
from strategy.trend_bos import collect_bos_flip_events


def main() -> None:
    combo = next(
        c
        for c in generate_combinations(get_profile("testing"))
        if c.get("bos_entry_enable")
        and not c.get("pp_enabled")
        and c.get("wave_counter_two_sided_enabled")
    )
    cfg = grid_dict_to_bot_config(combo)
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[
        (df["time"] >= combo["date_from"]) & (df["time"] <= combo["date_to"])
    ].reset_index(drop=True)

    t0, t1 = pd.Timestamp("2025-05-29 12:00"), pd.Timestamp("2025-06-04 00:00")

    eng = BacktestEngine(cfg)
    trades = eng.run(df.copy(), retain_wave_snapshot=True)
    birth = eng.wave_birth_by_time
    seq = eng.wave_sequence_info
    bos_times = set(
        getattr(eng, "_visual_bos_wave_times", None) or eng._bos_wave_times or set()
    )

    waves_src = getattr(eng, "last_waves_for_visual", None) or eng.last_waves
    bundle = build_wave_visual_bundle(
        df,
        list(waves_src or []),
        birth,
        trades,
        last_n=500,
        max_bars=5000,
        full_span=True,
        wave_seq_by_time=seq,
    )
    supplement_visual_waves_for_trades(
        bundle,
        last_waves=list(eng.last_waves or []),
        all_waves=list(eng._all_waves or []),
        wave_birth=birth,
        wave_seq_by_time=seq,
        pending_vis=getattr(eng, "pending_vis", None),
        df_full=df,
    )

    print("=== WAVES in window (HTML plot visibility) ===")
    visible_n = 0
    hidden_key = []
    for w in bundle.waves:
        wt = str(w.get("wave_time", ""))
        b = birth.get(wt)
        if b is None:
            continue
        bt = df.iloc[b]["time"]
        if not (t0 <= bt <= t1):
            continue
        info = seq.get(wt)
        vis_plot = _wave_visible_in_html_plot(w, cfg, bos_wave_times=bos_times)
        vis_filter = wave_passes_visual_filter(w, cfg, bos_wave_times=bos_times)
        idx = getattr(info, "index_in_trend", None) if info else None
        d = "UP" if int(w.get("dir", 0)) == 1 else "DN"
        flags = []
        if vis_plot:
            flags.append("HTML_BOX")
            visible_n += 1
        elif vis_filter:
            flags.append("filter_ok")
        else:
            flags.append("HIDDEN")
            hidden_key.append(wt)
        if wt in bos_times:
            flags.append("BOS")
        if w.get("post_ext_trend_suppressed"):
            flags.append("SUPP")
        print(f"{bt} wt={wt} idx={idx} {d} {' '.join(flags)}")

    print(f"\nvisible boxes in window: {visible_n}")
    print(f"hidden (non-ghost expected): {hidden_key}")

    print("\n=== BOS flip lines in window ===")
    bos_in_window = 0
    for ev in collect_bos_flip_events(df, eng._all_waves, cfg):
        t = pd.Timestamp(ev[0])
        if t0 <= t <= t1:
            bos_in_window += 1
            print(f"  {t} swing={ev[1]}")
    print(f"count: {bos_in_window}")

    print("\n=== TRADES in window ===")
    for tr in trades:
        et = pd.Timestamp(tr.entry_time)
        if t0 <= et <= t1:
            print(
                f"  {et} wt={tr.wave_time} {tr.entry_type} "
                f"close={tr.close_reason} bos_reentry={tr.is_bos_reentry}"
            )

    clip_waves = [
        w
        for w in bundle.waves
        if birth.get(str(w.get("wave_time"))) is not None
        and t0 <= df.iloc[birth[str(w["wave_time"])]]["time"] <= t1
    ]
    clip_trades = [t for t in trades if t0 <= pd.Timestamp(t.entry_time) <= t1]
    seg_df = df[(df["time"] >= t0) & (df["time"] <= t1)].reset_index(drop=True)

    bos_pts = _bos_flip_events_in_window(
        seg_df, getattr(eng, "bos_flip_events", None) or []
    )
    out = os.path.join("backtest", "output", "_verify_may30_jun3_visual.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    plot_waves_structure(
        df_window=seg_df,
        waves=clip_waves,
        closed_trades=clip_trades,
        bot_name="verify_may30_jun3",
        bos_points=bos_pts or None,
        save_path=None,
        interactive_html_path=out,
        show=False,
        pending_events=[],
        cfg=cfg,
        bos_wave_times=bos_times,
    )
    print(f"\nHTML: {os.path.abspath(out)}")


if __name__ == "__main__":
    main()
