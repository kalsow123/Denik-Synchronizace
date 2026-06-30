"""Ověření: visual merge nemění runtime vlny, obchody, wave_sequence."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config


def _combo():
    for c in generate_combinations(get_profile("testing")):
        if c.get("bos_entry_enable"):
            return grid_dict_to_bot_config(c)
    raise SystemExit("combo not found")


def _trade_sig(trades) -> list[tuple]:
    return sorted(
        (
            str(t.entry_time)[:19],
            int(getattr(t, "dir", 0) or 0),
            str(getattr(t, "wave_time", "") or ""),
            round(float(getattr(t, "entry_price", 0) or 0), 5),
            round(float(getattr(t, "exit_price", 0) or 0), 5),
            str(getattr(t, "exit_reason", "") or ""),
            bool(getattr(t, "is_bos_reentry", False)),
            bool(getattr(t, "is_ext", False)),
        )
        for t in trades
    )


def _wave_runtime_sig(w: dict) -> tuple:
    return (
        str(w.get("wave_time")),
        int(w.get("dir", 0) or 0),
        int(w.get("draw_left", 0)),
        int(w.get("draw_right", 0)),
        round(float(w.get("box_top", 0)), 5),
        round(float(w.get("box_bottom", 0)), 5),
        w.get("index_in_trend"),
        bool(w.get("post_ext_confirmed_trend_lock")),
        bool(w.get("post_ext_trend_suppressed")),
        bool(w.get("_visual_lock_merged")),
    )


def main() -> None:
    cfg = _combo()
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[
        (df["time"] >= "2025-05-10") & (df["time"] <= "2025-07-25")
    ].reset_index(drop=True)

    eng_no_vis = BacktestEngine(cfg)
    trades_no = eng_no_vis.run(df.copy(), retain_wave_snapshot=False)

    eng_vis = BacktestEngine(cfg)
    trades_vis = eng_vis.run(df.copy(), retain_wave_snapshot=True)

    print("=== 1) OBCHODY: retain_wave_snapshot=False vs True ===")
    sig_no = _trade_sig(trades_no)
    sig_vis = _trade_sig(trades_vis)
    print(f"  pocet obchodu: {len(sig_no)} vs {len(sig_vis)}")
    print(f"  shoda: {sig_no == sig_vis}")
    if sig_no != sig_vis:
        for i, (a, b) in enumerate(zip(sig_no, sig_vis)):
            if a != b:
                print(f"  prvni rozdil idx={i}: {a} != {b}")
                break

    print("\n=== 2) RUNTIME last_waves vs visual (Jul 17 bear chain) ===")
    focus = [
        "202507170430",
        "202507170930",
        "202507171130",
        "202507171530",
        "202507171830",
    ]
    rt = {str(w["wave_time"]): w for w in eng_vis.last_waves}
    vis = {str(w["wave_time"]): w for w in eng_vis.last_waves_for_visual}
    for wt in focus:
        rw = rt[wt]
        print(f"  runtime {wt}: bot={float(rw['box_bottom']):.5f} dr={rw['draw_right']} merged={rw.get('_visual_lock_merged')}")
    print(f"  visual keys Jul17 focus: {[k for k in focus if k in vis]}")
    merged = vis.get("202507170430")
    if merged:
        print(
            f"  visual merged 170430: bot={float(merged['box_bottom']):.5f} "
            f"dr={merged['draw_right']} _visual_lock_merged={merged.get('_visual_lock_merged')}"
        )

    print("\n=== 3) RUNTIME vlny NEMUTOVANY merge (id + box) ===")
    # Snapshot pred visual build neni k dispozici — overime ze runtime nema visual flagy
    bad = [
        w
        for w in eng_vis.last_waves
        if w.get("_visual_lock_merged") or w.get("_visual_merged_from")
    ]
    print(f"  runtime vln s visual flagy: {len(bad)} (ocekavano 0)")

    print("\n=== 4) wave_sequence_info Jul17 ===")
    for wt in focus:
        info = eng_vis.wave_sequence_info.get(wt)
        idx = getattr(info, "index_in_trend", None) if info else None
        bos = getattr(info, "is_bos_wave", False) if info else False
        print(f"  {wt}: idx={idx} is_bos={bos}")

    print("\n=== 5) BOS mapa / bos_wave_times ===")
    print(f"  _bos_wave_times obsahuje 171830: {'202507171830' in (eng_vis._bos_wave_times or set())}")
    flip_wts = {
        str(w.get("wave_time"))
        for w in eng_vis._bos_flip_wave_by_bar.values()
        if w.get("wave_time")
    }
    print(f"  _bos_flip_wave_by_bar wave_times: {sorted(flip_wts)}")

    print("\n=== 6) Porovnani eng bez/s viz — runtime vlny (vsechny) ===")
    rt_no = {_wave_runtime_sig(w) for w in eng_no_vis.last_waves if eng_no_vis.last_waves}
    rt_yes = {_wave_runtime_sig(w) for w in eng_vis.last_waves}
    # eng_no_vis may not have last_waves if retain false
    if not eng_no_vis.last_waves:
        eng_no_vis.last_waves = list(eng_no_vis._all_waves)
    rt_no = {_wave_runtime_sig(w) for w in eng_no_vis.last_waves}
    only_no = rt_no - rt_yes
    only_yes = rt_yes - rt_no
    print(f"  shoda runtime sig: {rt_no == rt_yes} (only_no={len(only_no)} only_yes={len(only_yes)})")

    print("\n=== 7) PnL souhrn ===")
    pnl_no = sum(float(getattr(t, "net_pnl_usd", 0) or 0) for t in trades_no)
    pnl_vis = sum(float(getattr(t, "net_pnl_usd", 0) or 0) for t in trades_vis)
    print(f"  net_pnl_usd: {pnl_no:.2f} vs {pnl_vis:.2f} diff={pnl_vis-pnl_no:.6f}")

    ok = (
        sig_no == sig_vis
        and len(bad) == 0
        and "202507170930" not in vis
        and "202507171530" not in vis
        and merged is not None
        and float(merged["box_bottom"]) < float(rt["202507170430"]["box_bottom"])
    )
    print(f"\n=== VYSLEDEK: {'OK — jen visual' if ok else 'FAIL'} ===")


if __name__ == "__main__":
    main()
