"""Overeni May 21-26: WAVE4 -> WAVE_BOS -> BEAR 1,2,3."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from strategy.trend_bos import iter_close_based_bos_flips


def main() -> None:
    cfg = grid_dict_to_bot_config(generate_combinations(get_profile("testing"))[0])
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2025-05-10") & (df["time"] <= "2025-05-28")].reset_index(
        drop=True
    )
    eng = BacktestEngine(cfg)
    eng.run(df.copy(), retain_wave_snapshot=True)

    t0, t1 = pd.Timestamp("2025-05-21"), pd.Timestamp("2025-05-26 18:00")
    print("=" * 72)
    print("MAY 21-26 WAVES (engine)")
    print("=" * 72)
    checks: list[tuple[str, bool, str]] = []

    ext3, bear1, w4 = "202505210930", "202505211130", "202505211700"
    bos_bear1, bear2, bear3 = "202505220730", "202505221300", "202505222000"
    post_w4 = "202505212300"

    for w in sorted(
        eng.last_waves,
        key=lambda x: (int(x.get("draw_right", 0)), str(x.get("wave_time", ""))),
    ):
        wt = str(w["wave_time"])
        dr = int(w.get("draw_right", 0))
        if dr < 0 or dr >= len(df):
            continue
        bt = df.iloc[dr]["time"]
        if not (t0 <= bt <= t1):
            continue
        info = eng.wave_sequence_info.get(wt)
        idx = info.index_in_trend if info else None
        bos = info.is_bos_wave if info else False
        d = "UP" if int(w.get("dir", 0)) == 1 else "DN"
        flags = []
        if w.get("is_ext"):
            flags.append("EXT")
        if w.get("in_ext_range"):
            flags.append("in_ext")
        if w.get("ext_post_range_terminator"):
            flags.append("TERM")
        if w.get("ext_post_trend_seed_dir"):
            flags.append(f"seed={w.get('ext_post_trend_seed_dir')}")
        if w.get("post_ext_trend_suppressed"):
            flags.append("SUPP")
        in_bos = wt in (eng._bos_wave_times or set())
        in_vis_bos = wt in (eng._visual_bos_wave_times or eng._bos_wave_times or set())
        if in_bos:
            flags.append("bos_wt")
        if in_vis_bos:
            flags.append("vis_bos")
        print(
            f"{bt} {d:2} idx={idx!s:>4} bos={str(bos):5} wt={wt} "
            f"{' '.join(flags)}"
        )

    # Close-BOS flips
    print("\n" + "=" * 72)
    print("CLOSE-BOS FLIPS May 21-26")
    print("=" * 72)
    bear_flip_bar = None
    for item in iter_close_based_bos_flips(df, eng.last_waves, cfg):
        i, t, target, label, lvl, _t0 = item
        if t0 <= pd.Timestamp(t) <= t1:
            print(f"  bar {i} {t} -> {target} level={lvl:.5f}")
            if target == "bear" and bear_flip_bar is None:
                bear_flip_bar = i

    w4_wave = eng.waves_by_wave_time[w4]
    w4_low = float(w4_wave["box_bottom"])
    w4_dr = int(w4_wave["draw_right"])

    print("\n" + "=" * 72)
    print("ASSERTIONS")
    print("=" * 72)

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        mark = "OK" if ok else "FAIL"
        msg = f"  [{mark}] {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)

    check("EXT3 idx=3", eng.wave_sequence_info[ext3].index_in_trend == 3)
    check("BEAR1 idx=1, not BOS", eng.wave_sequence_info[bear1].index_in_trend == 1 and not eng.wave_sequence_info[bear1].is_bos_wave)
    check("WAVE4 idx=4, TERM", eng.wave_sequence_info[w4].index_in_trend == 4 and w4_wave.get("ext_post_range_terminator") is True)
    check("post-WAVE4 bear bez seed", post_w4 in eng.waves_by_wave_time and eng.waves_by_wave_time[post_w4].get("ext_post_trend_seed_dir") is None)
    check("WAVE_BOS bear idx=1", eng.wave_sequence_info[bos_bear1].index_in_trend == 1 and eng.wave_sequence_info[bos_bear1].is_bos_wave)
    check("BOS bear in _bos_wave_times", bos_bear1 in (eng._bos_wave_times or set()))
    check("BOS bear in visual", bos_bear1 in (eng._visual_bos_wave_times or eng._bos_wave_times or set()))
    check("BEAR2 idx=2", eng.wave_sequence_info[bear2].index_in_trend == 2)
    check("BEAR3 idx=3", eng.wave_sequence_info[bear3].index_in_trend == 3)
    check(
        "close flip pod WAVE4 low",
        bear_flip_bar is not None and bear_flip_bar > w4_dr,
        f"flip bar={bear_flip_bar}, WAVE4 dr={w4_dr}, low={w4_low:.5f}",
    )
    if bear_flip_bar is not None:
        flip_close = float(df.iloc[bear_flip_bar]["close"])
        check(
            "flip close < WAVE4 box_bottom",
            flip_close < w4_low,
            f"close={flip_close:.5f} vs low={w4_low:.5f}",
        )

    failed = [c for c in checks if not c[1]]
    print("\n" + "=" * 72)
    if failed:
        print(f"VYSLEDEK: {len(checks) - len(failed)}/{len(checks)} OK — OPRAVA NENI KOMPLETNI")
        for name, _, detail in failed:
            print(f"  - {name}: {detail}")
        raise SystemExit(1)
    print(f"VYSLEDEK: {len(checks)}/{len(checks)} OK — MAY 21 OPRAVA OVERENA")
    print("=" * 72)


if __name__ == "__main__":
    main()
