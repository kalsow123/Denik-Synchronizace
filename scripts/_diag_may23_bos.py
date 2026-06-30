"""Diagnostika: první UP po bear WAVE 3 kolem 2025-05-23."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from backtest.waves_plotly_figure import _wave_visible_in_html_plot


def _testing_cfg():
    combos = generate_combinations(get_profile("testing"))
    for combo in combos:
        if (
            combo.get("wf_enabled")
            and combo.get("trend_hh_hl_filter_enabled")
            and combo.get("tp_mode") == "wave_target_n"
            and combo.get("wave_counter_two_sided_enabled") is False
            and combo.get("pp_enabled") is False
        ):
            return combo, grid_dict_to_bot_config(combo)
    raise RuntimeError("testing combo not found")


def main() -> None:
    combos = generate_combinations(get_profile("testing"))
    combo = next(
        c
        for c in combos
        if c.get("trend_hh_hl_filter_enabled")
        and c.get("wave_counter_two_sided_enabled")
        and c.get("tp_mode") == "wave_target_n"
    )
    cfg = grid_dict_to_bot_config(combo)
    print("combo two_sided=", combo.get("wave_counter_two_sided_enabled"), flush=True)
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2025-05-10") & (df["time"] <= "2025-05-26")].reset_index(
        drop=True
    )

    eng = BacktestEngine(cfg)
    eng.run(df, retain_wave_snapshot=True)
    bos = set(getattr(eng, "_visual_bos_wave_times", set()) or set())

    print("=== ALL WAVES (draw_right bar time) ===")
    birth = eng.wave_birth_by_time
    for w in eng._all_waves:
        wt = str(w["wave_time"])
        dr = int(w.get("draw_right", -1))
        if dr < 0 or dr >= len(df):
            continue
        bar_t = df.iloc[dr]["time"]
        if bar_t < pd.Timestamp("2025-05-21") or bar_t > pd.Timestamp("2025-05-25 23:59"):
            continue
        info = eng.wave_sequence_info.get(wt)
        idx = info.index_in_trend if info else None
        ibos = info.is_bos_wave if info else False
        vis = _wave_visible_in_html_plot(w, cfg, bos_wave_times=bos)
        hh = w.get("hh_hl_pass", True)
        d = "UP" if w["dir"] == 1 else "DN"
        bi = birth.get(wt)
        prop = w.get("index_in_trend")
        print(
            f"{wt} dr_bar={bar_t} birth={bi} {d} idx={idx} bos={ibos} hh={hh} "
            f"vis={vis} dr={dr} prop={prop}",
            flush=True,
        )

    print("\n=== BOS flip events (May 21+) ===", flush=True)
    for ev in eng.bos_flip_events or []:
        label = str(ev).encode("ascii", "replace").decode("ascii")
        print(label, flush=True)

    bos_bar = None
    for i in range(len(df)):
        if df.iloc[i]["time"] == pd.Timestamp("2025-05-22 15:00:00"):
            bos_bar = i
            break
    print(f"\n=== waves with draw_right >= BOS bar {bos_bar} (May 22 15:00) ===", flush=True)
    if bos_bar is not None:
        for w in eng._all_waves:
            dr = int(w.get("draw_right", -1))
            if dr < bos_bar:
                continue
            wt = str(w["wave_time"])
            info = eng.wave_sequence_info.get(wt)
            idx = info.index_in_trend if info else None
            ibos = info.is_bos_wave if info else False
            d = "UP" if w["dir"] == 1 else "DN"
            bar_t = df.iloc[dr]["time"]
            vis = _wave_visible_in_html_plot(w, cfg, bos_wave_times=bos)
            print(
                f"{wt} dr_bar={bar_t} {d} idx={idx} bos={ibos} hh={w.get('hh_hl_pass')} "
                f"vis={vis} prop={w.get('index_in_trend')}",
                flush=True,
            )

    t0, t1 = pd.Timestamp("2025-05-22"), pd.Timestamp("2025-05-24")
    print("\n=== visual waves spanning May 22-24 ===", flush=True)
    vis_src = eng.last_waves_for_visual or eng.last_waves
    for w in vis_src:
        dl, dr = int(w["draw_left"]), int(w["draw_right"])
        if dl >= len(df) or dr >= len(df):
            continue
        left_t, right_t = df.iloc[dl]["time"], df.iloc[dr]["time"]
        if right_t < t0 or left_t > t1:
            continue
        wt = str(w["wave_time"])
        info = eng.wave_sequence_info.get(wt)
        idx = info.index_in_trend if info else None
        ibos = info.is_bos_wave if info else False
        vis = _wave_visible_in_html_plot(w, cfg, bos_wave_times=bos)
        d = "UP" if w["dir"] == 1 else "DN"
        print(
            f"{wt} {d} idx={idx} prop={w.get('index_in_trend')} bos={ibos} "
            f"hh={w.get('hh_hl_pass')} vis={vis} span={left_t}..{right_t}",
            flush=True,
        )


if __name__ == "__main__":
    main()
