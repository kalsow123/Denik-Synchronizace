"""Trades vs visual May30-Jun3."""
from __future__ import annotations

from collections import Counter

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from backtest.stats import classify_position_kind
from strategy.trend_bos import wave_allowed_for_entry


def run(bos: bool) -> None:
    combo = next(
        c
        for c in generate_combinations(get_profile("testing"))
        if c.get("bos_entry_enable") is bos
        and not c.get("pp_enabled")
        and c.get("wave_counter_two_sided_enabled")
    )
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[
        (df["time"] >= combo["date_from"]) & (df["time"] <= combo["date_to"])
    ].reset_index(drop=True)
    cfg = grid_dict_to_bot_config(combo)
    eng = BacktestEngine(cfg)
    trades = eng.run(df.copy(), retain_wave_snapshot=True)
    t0, t1 = pd.Timestamp("2025-05-29 20:00"), pd.Timestamp("2025-06-04 00:00")
    seg = [t for t in trades if t0 <= pd.Timestamp(t.entry_time) <= t1]
    print("=== BOS_ENTRY", bos, "trades May29 20:00 - Jun4:", len(seg), "===")
    for t in seg:
        k = classify_position_kind(
            is_pp=t.is_pp,
            is_counter=t.is_counter,
            is_bos_reentry=t.is_bos_reentry,
            is_two_sided_mirror=t.is_two_sided_mirror,
            is_ext=t.is_ext,
            entry_tag=t.entry_tag,
        )
        print(" ", pd.Timestamp(t.entry_time), k, "wt", t.wave_time)

    pend = [e for e in eng.pending_vis if t0 <= pd.Timestamp(e.get("time")) <= t1]
    print(" pending kinds:", dict(Counter(e.get("kind") for e in pend)), "n=", len(pend))

    print(" --- waves May30-Jun3 ---")
    for w in eng.last_waves:
        wt = str(w.get("wave_time"))
        try:
            t = pd.to_datetime(wt, format="%Y%m%d%H%M")
        except Exception:
            continue
        if not (pd.Timestamp("2025-05-30") <= t <= pd.Timestamp("2025-06-03 23:59")):
            continue
        ts = eng.trend_states_per_wave.get(wt)
        allowed, reason = (
            wave_allowed_for_entry(w, ts, cfg) if ts else (None, "no_ts")
        )
        info = eng.wave_sequence_info.get(wt)
        idx = getattr(info, "index_in_trend", None) if info else None
        is_bos = getattr(info, "is_bos_wave", False) if info else False
        traded = any(str(x.wave_time) == wt for x in seg)
        in_bos = wt in (eng._bos_wave_times or set())
        print(
            wt,
            "dir",
            w.get("dir"),
            "allowed",
            allowed,
            reason,
            "supp",
            w.get("post_ext_trend_suppressed"),
            "lock",
            w.get("post_ext_confirmed_trend_lock"),
            "idx",
            idx,
            "seq_bos",
            is_bos,
            "runtime_bos",
            in_bos,
            "traded",
            traded,
        )

    wd = eng.wave_debug
    for k in sorted(wd):
        if any(x in k for x in ("skip", "deferred", "suppressed", "bos", "trend")):
            if wd[k]:
                print(" debug", k, wd[k])


if __name__ == "__main__":
    run(True)
    run(False)
