"""Full problem inventory May29-Jun4 for testing combo."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from backtest.visual_wave_filter import wave_passes_visual_filter
from strategy.trend_bos import collect_bos_flip_events, wave_allowed_for_entry


def main() -> None:
    combo = next(
        c
        for c in generate_combinations(get_profile("testing"))
        if c.get("bos_entry_enable")
        and not c.get("pp_enabled")
        and c.get("wave_counter_two_sided_enabled")
    )
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[
        (df["time"] >= combo["date_from"]) & (df["time"] <= combo["date_to"])
    ].reset_index(drop=True)
    cfg = grid_dict_to_bot_config(combo)
    eng = BacktestEngine(cfg)
    trades = eng.run(df.copy(), retain_wave_snapshot=True)

    t0, t1 = pd.Timestamp("2025-05-29 12:00"), pd.Timestamp("2025-06-04 00:00")
    birth = eng.wave_birth_by_time
    bos_times = set(eng._visual_bos_wave_times or eng._bos_wave_times or set())

    rows = []
    for w in eng._all_waves:
        wt = str(w["wave_time"])
        b = birth.get(wt)
        if b is None:
            continue
        bt = df.iloc[b]["time"]
        if not (t0 <= bt <= t1):
            continue
        info = eng.wave_sequence_info.get(wt)
        ts = eng.trend_states_per_wave.get(wt)
        allowed, reason = wave_allowed_for_entry(w, ts, cfg) if ts else (None, "no_ts")
        traded = any(str(t.wave_time) == wt for t in trades)
        vis = wave_passes_visual_filter(w, cfg, bos_wave_times=bos_times)
        rows.append(
            {
                "time": bt,
                "wt": wt,
                "dir": "UP" if int(w.get("dir", 0)) == 1 else "DN",
                "idx": getattr(info, "index_in_trend", None),
                "seq_bos": getattr(info, "is_bos_wave", None),
                "rt_bos": wt in (eng._bos_wave_times or set()),
                "supp": w.get("post_ext_trend_suppressed"),
                "lock": w.get("post_ext_confirmed_trend_lock"),
                "hh": w.get("hh_hl_pass"),
                "ext": w.get("is_ext"),
                "in_ext": w.get("in_ext_range"),
                "allowed": allowed,
                "reason": reason,
                "traded": traded,
                "visible": vis,
            }
        )
    rows.sort(key=lambda r: r["time"])

    print("=== WAVES May29 12:00 - Jun4 ===")
    for r in rows:
        flags = []
        if r["supp"]:
            flags.append("SUPP")
        if r["lock"]:
            flags.append("LOCK")
        if r["seq_bos"] and not r["rt_bos"]:
            flags.append("BOS_DESYNC")
        if r["idx"] is None:
            flags.append("NO_IDX")
        if not r["visible"]:
            flags.append("HIDDEN")
        if r["allowed"] is False:
            flags.append("BLOCK:" + str(r["reason"]))
        if r["traded"]:
            flags.append("TRADED")
        flag_str = " ".join(flags)
        print(f"{r['time']} {r['dir']:2} idx={r['idx']} wt={r['wt']} {flag_str}")

    print("\n=== BOS close-flip events in window ===")
    for ev in collect_bos_flip_events(df, eng._all_waves, cfg):
        t = pd.Timestamp(ev["time"])
        if t0 <= t <= t1:
            print(ev)

    print("\n=== TRADES in window ===")
    for t in trades:
        et = pd.Timestamp(t.entry_time)
        if t0 <= et <= t1:
            print(
                et,
                "wt",
                t.wave_time,
                t.entry_type,
                t.close_reason,
                "bos_reentry",
                t.is_bos_reentry,
            )

    print("\n=== PROBLEM SUMMARY ===")
    print(
        {
            "waves_total": len(rows),
            "no_idx": sum(1 for r in rows if r["idx"] is None),
            "suppressed": sum(1 for r in rows if r["supp"]),
            "locked": sum(1 for r in rows if r["lock"]),
            "bos_desync": sum(1 for r in rows if r["seq_bos"] and not r["rt_bos"]),
            "hidden": sum(1 for r in rows if not r["visible"]),
            "blocked": sum(1 for r in rows if r["allowed"] is False),
            "traded": sum(1 for r in rows if r["traded"]),
            "bos_reentry_trades": sum(
                1
                for t in trades
                if t0 <= pd.Timestamp(t.entry_time) <= t1 and t.is_bos_reentry
            ),
        }
    )

    # seed context
    for wt in ["202505291300", "202505292100", "202505300400"]:
        w = eng.waves_by_wave_time.get(wt)
        if w:
            print(
                wt,
                "seed",
                w.get("ext_post_trend_seed_dir"),
                "is_ext",
                w.get("is_ext"),
                "idx",
                getattr(eng.wave_sequence_info.get(wt), "index_in_trend", None),
            )


if __name__ == "__main__":
    main()
