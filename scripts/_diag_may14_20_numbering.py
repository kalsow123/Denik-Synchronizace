"""Diag: May 14-20 wave numbering vs ghost visibility."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from backtest.visual_wave_filter import wave_passes_visual_filter


def main() -> None:
    cfg = grid_dict_to_bot_config(generate_combinations(get_profile("testing"))[0])
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2025-05-14") & (df["time"] <= "2025-05-22")].reset_index(
        drop=True
    )
    eng = BacktestEngine(cfg)
    eng.run(df.copy(), retain_wave_snapshot=True)

    print("=== WAVES May 14-20 (chronological) ===")
    for w in sorted(
        eng.last_waves,
        key=lambda x: (int(x.get("draw_right", 0)), str(x.get("wave_time", ""))),
    ):
        dr = int(w.get("draw_right", 0))
        if dr < 0 or dr >= len(df):
            continue
        bt = df.iloc[dr]["time"]
        wt = str(w["wave_time"])
        info = eng.wave_sequence_info.get(wt)
        idx = info.index_in_trend if info else None
        d = "UP" if int(w.get("dir", 0)) == 1 else "DN"
        flags: list[str] = []
        if w.get("is_ext"):
            flags.append("EXT")
        hh = w.get("hh_hl_pass")
        if hh is False:
            flags.append("ghost_hh")
        elif hh is True:
            flags.append("hh_ok")
        if w.get("in_ext_range"):
            flags.append("in_ext")
        if w.get("ext_post_range_terminator"):
            flags.append("TERM")
        if info and info.is_bos_wave:
            flags.append("BOS")
        if not wave_passes_visual_filter(w, cfg, check_bos=True):
            flags.append("HIDDEN")
        fl = " ".join(flags)
        print(f"{bt} {d:2} idx={str(idx):>4} {fl:40} wt={wt}")

    for label, direction in (("BEAR DN", -1), ("UP", 1)):
        print()
        print(f"=== {label} numbered + visible ===")
        for w in sorted(eng.last_waves, key=lambda x: int(x.get("draw_right", 0))):
            if int(w.get("dir", 0)) != direction:
                continue
            wt = str(w["wave_time"])
            info = eng.wave_sequence_info.get(wt)
            if not info or info.index_in_trend is None:
                continue
            if not wave_passes_visual_filter(w, cfg, check_bos=True):
                continue
            dr = int(w["draw_right"])
            kind = "EXT" if w.get("is_ext") else "WAVE"
            ghost = " ghost" if w.get("hh_hl_pass") is False else ""
            print(
                f"  {df.iloc[dr]['time']} idx={info.index_in_trend} {kind}{ghost} wt={wt}"
            )


if __name__ == "__main__":
    main()
