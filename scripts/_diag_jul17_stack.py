"""Skladba vln Jul 15-22: runtime vs HTML (jen diagnostika)."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from backtest.visual_wave_filter import wave_passes_visual_filter
from strategy.ext_logic import is_ext_wave


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
    # Warmup od 14.7., aby EXT/lock kontext sedel
    df = df[
        (df["time"] >= "2025-05-10") & (df["time"] <= "2025-07-25")
    ].reset_index(drop=True)
    cfg = grid_dict_to_bot_config(combo)
    eng = BacktestEngine(cfg)
    eng.run(df.copy(), retain_wave_snapshot=True)
    bos = set(eng._visual_bos_wave_times or eng._bos_wave_times or set())

    def wt_ts(wt: str) -> pd.Timestamp:
        return pd.to_datetime(str(wt), format="%Y%m%d%H%M")

    t0, t1 = pd.Timestamp("2025-07-15"), pd.Timestamp("2025-07-22 23:59")
    rows = []
    for w in eng.last_waves:
        wt = str(w["wave_time"])
        ts = wt_ts(wt)
        if not (t0 <= ts <= t1):
            continue
        info = (eng.wave_sequence_info or {}).get(wt)
        idx = getattr(info, "index_in_trend", None) if info else None
        is_bos = wt in bos
        vis = wave_passes_visual_filter(
            w, cfg, bos_wave_times=bos, include_lock_trend_waves=True
        )
        merged_from = w.get("_visual_merged_from") or []
        merge_tag = f" MERGED<-{merged_from}" if merged_from else ""
        ext = bool(w.get("is_ext") or is_ext_wave(w, cfg))
        dl, dr = int(w["draw_left"]), int(w["draw_right"])
        hide = []
        if w.get("post_ext_confirmed_trend_lock"):
            hide.append("lock")
        if w.get("post_ext_trend_suppressed"):
            hide.append("supp")
        if not w.get("hh_hl_pass", True) and cfg.trend_hh_hl_filter_enabled:
            hide.append("hh_hl")
        rows.append((ts, wt, w, dl, dr, idx, is_bos, vis, ext, hide))

    rows.sort(key=lambda x: x[0])
    hdr = (
        "time       dir bars    bar_from    bar_to      box_bot  box_top  ext   idx  bos  HTML hide"
    )
    print("CHRONOLOGIE Jul15-22 (last_waves)")
    print(hdr)
    print("-" * len(hdr))
    for ts, wt, w, dl, dr, idx, is_bos, vis, ext, hide in rows:
        d = "UP" if int(w["dir"]) == 1 else "DN"
        print(
            f"{ts.strftime('%m-%d %H:%M')} {d:2} {dl:4}-{dr:<4} "
            f"{str(df.iloc[dl]['time'])[5:16]} {str(df.iloc[dr]['time'])[5:16]} "
            f"{float(w['box_bottom']):.5f} {float(w['box_top']):.5f} "
            f"{str(ext):5} {str(idx):4} {str(is_bos):4} {str(vis):5} {'+'.join(hide) or '-'}"
        )

    print("\nVIDITELNE BOXU (HTML):")
    for ts, wt, w, dl, dr, idx, is_bos, vis, ext, hide in rows:
        if not vis:
            continue
        if is_bos:
            lbl = "BOS"
        elif idx is not None:
            lbl = str(idx)
        else:
            lbl = "(bez cisla)"
        d = "UP" if int(w["dir"]) == 1 else "DN"
        print(
            f"  {ts.strftime('%m-%d %H:%M')} {d} "
            f"[{str(df.iloc[dl]['time'])[5:16]} .. {str(df.iloc[dr]['time'])[5:16]}] "
            f"low={float(w['box_bottom']):.5f} label={lbl}"
        )

    print("\nSKRYTE (runtime existuje, HTML ne):")
    for ts, wt, w, dl, dr, idx, is_bos, vis, ext, hide in rows:
        if vis:
            continue
        d = "UP" if int(w["dir"]) == 1 else "DN"
        print(
            f"  {ts.strftime('%m-%d %H:%M')} {d} low={float(w['box_bottom']):.5f} "
            f"hide={'+' .join(hide) or '?'}"
        )

    print("\nEXT + LOCK kontext:")
    for ts, wt, w, dl, dr, idx, is_bos, vis, ext, hide in rows:
        if ext or w.get("ext_post_trend_seed_dir") is not None:
            print(
                f"  {wt} ext={ext} seed={w.get('ext_post_trend_seed_dir')} "
                f"in_ext={w.get('in_ext_range')}"
            )


if __name__ == "__main__":
    main()
