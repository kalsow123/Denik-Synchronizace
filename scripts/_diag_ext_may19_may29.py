"""Diagnostika EXT číslování May 19 a May 29."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from backtest.visual_wave_filter import wave_passes_visual_filter
from strategy.ext_range import ext_scenario_classify
from strategy.trend_bos import TrendState


def _cfg():
    combos = generate_combinations(get_profile("testing"))
    for combo in combos:
        if (
            combo.get("wf_enabled")
            and combo.get("trend_hh_hl_filter_enabled")
            and combo.get("tp_mode") == "wave_target_n"
            and combo.get("wave_counter_two_sided_enabled") is False
        ):
            return grid_dict_to_bot_config(combo)
    raise RuntimeError("combo not found")


def dump_range(eng, df, t0, t1, label):
    bos = set(getattr(eng, "_visual_bos_wave_times", set()) or set())
    print(f"\n=== {label} ===")
    for w in sorted(eng._all_waves, key=lambda x: int(x.get("draw_right", 0))):
        dr = int(w.get("draw_right", -1))
        if dr < 0 or dr >= len(df):
            continue
        bar_t = df.iloc[dr]["time"]
        if bar_t < pd.Timestamp(t0) or bar_t > pd.Timestamp(t1):
            continue
        wt = str(w["wave_time"])
        info = eng.wave_sequence_info.get(wt)
        idx = info.index_in_trend if info else None
        ibos = info.is_bos_wave if info else False
        d = "UP" if w["dir"] == 1 else "DN"
        ext = "EXT" if w.get("is_ext") else "WAVE"
        vis = wave_passes_visual_filter(w, eng.cfg, bos_wave_times=bos)
        hh = w.get("hh_hl_pass", True)
        ts = eng.trend_states_per_wave.get(wt)
        td = ts.direction if ts else "?"
        print(
            f"{wt} {bar_t} {d} {ext} idx={idx} bos={ibos} hh={hh} vis={vis} "
            f"trend@{wt}={td}"
        )
    print("BOS events:")
    for ev in eng.bos_flip_events or []:
        t = pd.Timestamp(ev[0])
        if t >= pd.Timestamp(t0) and t <= pd.Timestamp(t1):
            print(f"  {ev}")


def classify_ext_at_confirm(eng, df, wave_time):
    w = next(x for x in eng._all_waves if str(x["wave_time"]) == wave_time)
    dr = int(w["draw_right"])
    bar_close = float(df.iloc[dr]["close"])
    ts = eng.trend_states_per_wave.get(wave_time)
    if ts is None:
        print(f"no trend state for {wave_time}")
        return
    swing = {
        "last_up_box_bottom": ts.last_up_box_bottom,
        "last_down_box_top": ts.last_down_box_top,
    }
    sc = ext_scenario_classify(w, ts, bar_close, swing)
    print(
        f"EXT classify {wave_time}: scenario={sc} dir={ts.direction} "
        f"ldt={ts.last_down_box_top} lub={ts.last_up_box_bottom} close={bar_close}"
    )


def main():
    cfg = _cfg()
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2025-05-10") & (df["time"] <= "2025-06-05")].reset_index(
        drop=True
    )
    eng = BacktestEngine(cfg)
    eng.run(df, retain_wave_snapshot=True)
    dump_range(eng, df, "2025-05-14", "2025-05-20", "May 14-20 (fotka 1)")
    classify_ext_at_confirm(eng, df, "202505190400")
    dump_range(eng, df, "2025-05-27", "2025-06-02", "May 27-Jun 2 (fotka 2)")
    for wt in ("202505290230", "202505290530", "202505291300", "202505292100"):
        classify_ext_at_confirm(eng, df, wt)


if __name__ == "__main__":
    main()
