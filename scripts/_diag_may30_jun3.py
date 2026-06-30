"""Diagnostika May 30 - Jun 3: EXT3, WAVE4, BOS, Bear 2."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from backtest.stats import classify_position_kind
from backtest.visual_wave_filter import wave_passes_visual_filter
from strategy.ext_logic import is_ext_wave
from strategy.trend_bos import wave_allowed_for_entry


def pick_combo(bos: bool):
    for c in generate_combinations(get_profile("testing")):
        if (
            c.get("bos_entry_enable") is bos
            and c.get("pp_enabled") is False
            and c.get("wave_counter_two_sided_enabled") is True
        ):
            return c
    raise SystemExit("combo not found")


def wt_time(wt: str):
    return pd.to_datetime(str(wt), format="%Y%m%d%H%M")


def in_range(wt: str, t0: str, t1: str) -> bool:
    t = wt_time(wt)
    return pd.Timestamp(t0) <= t <= pd.Timestamp(t1)


def show_w(w, eng, cfg, tag=""):
    wt = str(w.get("wave_time"))
    info = (eng.wave_sequence_info or {}).get(wt)
    idx = getattr(info, "index_in_trend", None) if info else w.get("index_in_trend")
    is_bos = wt in (eng._visual_bos_wave_times or set()) or wt in (
        eng._bos_wave_times or set()
    )
    vis = wave_passes_visual_filter(
        w, cfg, bos_wave_times=set(eng._visual_bos_wave_times or set())
    )
    ts = eng.trend_states_per_wave.get(wt)
    allowed, reason = (
        wave_allowed_for_entry(w, ts, cfg) if ts is not None else (None, None)
    )
    ext = bool(w.get("is_ext") or is_ext_wave(w, cfg))
    print(
        f"{tag} wt={wt} ~{wt_time(wt)} dir={w.get('dir')} idx={idx} ext={ext} "
        f"in_ext={w.get('in_ext_range')} hh={w.get('hh_hl_pass')} bos={is_bos} "
        f"vis={vis} allowed={allowed}({reason}) dl={w.get('draw_left')} dr={w.get('draw_right')}"
    )


def run(bos: bool):
    combo = pick_combo(bos)
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[
        (df["time"] >= combo["date_from"]) & (df["time"] <= combo["date_to"])
    ].reset_index(drop=True)
    cfg = grid_dict_to_bot_config(combo)
    eng = BacktestEngine(cfg)
    trades = eng.run(df.copy(), retain_wave_snapshot=True)

    print(f"\n{'='*60}\nBOS_ENTRY={bos} tp_mode={cfg.tp_mode}\n{'='*60}")

    print("\n--- last_waves May28-Jun4 ---")
    for w in eng.last_waves:
        wt = str(w.get("wave_time"))
        if in_range(wt, "2025-05-28", "2025-06-04 23:59"):
            show_w(w, eng, cfg)

    print("\n--- in last_waves_for_visual? ---")
    vis_set = {str(w.get("wave_time")) for w in eng.last_waves_for_visual}
    for w in eng.last_waves:
        wt = str(w.get("wave_time"))
        if in_range(wt, "2025-05-28", "2025-06-04 23:59"):
            print(wt, "in_visual_set", wt in vis_set)

    print("\n--- BOS flip events ---")
    for ev in eng.bos_flip_events or []:
        t = pd.Timestamp(ev[0])
        if pd.Timestamp("2025-05-28") <= t <= pd.Timestamp("2025-06-04 23:59"):
            print(t, "swing", round(float(ev[1]), 5), "|", str(ev[2])[:72])

    print("\n--- _bos_flip_wave_by_bar (May28-Jun4 bars) ---")
    t0 = pd.Timestamp("2025-05-28")
    t1 = pd.Timestamp("2025-06-04 23:59")
    for i, row in df.iterrows():
        ts = pd.Timestamp(row["time"])
        if t0 <= ts <= t1 and i in eng._bos_flip_wave_by_bar:
            bw = eng._bos_flip_wave_by_bar[i]
            print(f"bar={i} {ts} bos_wave={bw.get('wave_time')} dir={bw.get('dir')}")

    print("\n--- trades ---")
    for t in trades:
        et = pd.Timestamp(t.entry_time)
        if pd.Timestamp("2025-05-28") <= et <= pd.Timestamp("2025-06-04 23:59"):
            k = classify_position_kind(
                is_pp=t.is_pp,
                is_counter=t.is_counter,
                is_bos_reentry=t.is_bos_reentry,
                is_two_sided_mirror=t.is_two_sided_mirror,
                is_ext=t.is_ext,
                entry_tag=t.entry_tag,
            )
            print(et, k, "wt", t.wave_time, "dir", t.dir)

    # chronology from EXT3 anchor
    print("\n--- chronology from 202505290530 (EXT3 candidate) ---")
    anchor = pd.Timestamp("2025-05-29 05:30")
    seq = []
    for w in eng.last_waves:
        wt = str(w.get("wave_time"))
        try:
            t = wt_time(wt)
        except Exception:
            continue
        if anchor <= t <= pd.Timestamp("2025-06-04"):
            info = (eng.wave_sequence_info or {}).get(wt)
            idx = getattr(info, "index_in_trend", None) if info else None
            vis = wave_passes_visual_filter(
                w, cfg, bos_wave_times=set(eng._visual_bos_wave_times or set())
            )
            seq.append((t, wt, w.get("dir"), idx, vis, w.get("hh_hl_pass")))
    for row in sorted(seq):
        print(row)


if __name__ == "__main__":
    run(bos=True)
    run(bos=False)
