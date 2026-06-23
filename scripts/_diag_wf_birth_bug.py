"""Kolik z 32 BT-only vln je wf_continued_classic s birth=None (live WF birth bug)."""
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATE_FROM, DATE_TO = "2025-11-10", "2026-05-09"

# 32 BT-only z _diag_btonly_split (PnL ze splitu)
BT_ONLY = {
 "202605072330":-501,"202602040600":-501,"202512051800":-501,"202603170900":-500,
 "202602031300":-500,"202604171600":-500,"202511261830":-500,"202602131530":-500,
 "202512102200":-500,"202601021800":-500,"202602251930":-500,"202601220130":-500,
 "202602230130":-499,"202604082200":-499,"202511261030":-287,"202604092030":-195,
 "202604301800":-162,"202605061930":-155,"202602162330":63,"202601261800":338,
 "202603171200":533,"202602250900":685,"202603242300":907,"202604301400":1014,
 "202601122230":1032,"202603160400":1074,"202602231400":1304,"202602192330":1410,
 "202603181600":1624,"202603051030":3227,"202603310430":3340,"202603250100":6122,
}


def main() -> None:
    from backtest.data_loader import filter_by_date_range, load_csv
    from config.bot_config import LIVE_BOT_CONFIG
    from config.position_modes import resolve_grid_engine_config
    from strategy.wave_detection import detect_waves
    from strategy.wave_detection_pine import compute_wave_birth_bars_pine
    from runtime.wf_live import WfLiveRuntime

    df = filter_by_date_range(load_csv("data/EURUSD_M30.csv"), DATE_FROM, DATE_TO).reset_index(drop=True)
    cfg = resolve_grid_engine_config(LIVE_BOT_CONFIG)

    waves = detect_waves(df, cfg)
    wave_birth = compute_wave_birth_bars_pine(df, cfg)  # monolitic — jak ho ma run_e2e
    wf = WfLiveRuntime()
    wf.process(df, cfg, waves, wave_birth_by_time=wave_birth)
    post = {str(w["wave_time"]): w for w in waves}

    n_bug = n_trend = n_other = 0
    pnl_bug = pnl_trend = pnl_other = 0
    print(f"{'wave_time':<14}{'pnl':>7}  {'post_WF':>7} {'wf_cont':>7} {'birth':>7}  kategorie")
    for wt, pnl in sorted(BT_ONLY.items(), key=lambda x: x[1]):
        w = post.get(wt)
        in_post = w is not None
        wf_cont = bool(w.get("wf_continued_classic")) if w else False
        birth = wave_birth.get(wt)
        if in_post and wf_cont and birth is None:
            cat = "WF_BIRTH_BUG (opravitelne)"
            n_bug += 1; pnl_bug += pnl
        elif in_post and birth is not None:
            cat = "post-WF znama, jiny gate (trend/aged)"
            n_trend += 1; pnl_trend += pnl
        else:
            cat = "jine (mimo post-WF / look-ahead)"
            n_other += 1; pnl_other += pnl
        print(f"{wt:<14}{pnl:>7}  {str(in_post):>7} {str(wf_cont):>7} "
              f"{str(birth):>7}  {cat}")

    print("\nSHRNUTI:")
    print(f"  WF_BIRTH_BUG (birth=None, kauzalne opravitelne): {n_bug} vln  pnl={pnl_bug}")
    print(f"  post-WF znama ale jiny gate:                     {n_trend} vln  pnl={pnl_trend}")
    print(f"  jine:                                            {n_other} vln  pnl={pnl_other}")


if __name__ == "__main__":
    main()
