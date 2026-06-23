"""
PRICINA 2 detail: 125 spolecnych vln se SHODNYM trendem, kde live PnL != backtest.
Kategorizuje rozdil dle: entry_bar, close_reason, close_bar, pocet trades, EP.

Spusteni: $env:E2E_FIRE_ON_BIRTH="1"; .venv\\Scripts\\python.exe scripts/_diag_cause2_detail.py
"""
from __future__ import annotations

import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATE_FROM, DATE_TO = "2025-11-10", "2026-05-09"


def _norm_reason(r: str) -> str:
    r = str(r or "")
    for pref in ("BOS_EXIT_WAVE_TARGET", "TP_WAVE_N", "EXT_BOS_CLOSE", "SL", "TP"):
        if r.startswith(pref):
            return pref
    return r


def main() -> None:
    from backtest.data_loader import filter_by_date_range, load_csv
    from backtest.engine import BacktestEngine
    from backtest.stats import classify_position_kind
    from config.bot_config import LIVE_BOT_CONFIG
    from config.position_modes import resolve_grid_engine_config
    from strategy.wave_detection import detect_waves
    from strategy.trend_bos import compute_trend_states_per_bar
    from scripts.e2e_live_broker_sim import run_e2e, _clean_wave_time, install_fake

    df = filter_by_date_range(load_csv("data/EURUSD_M30.csv"), DATE_FROM, DATE_TO).reset_index(drop=True)
    cfg = resolve_grid_engine_config(LIVE_BOT_CONFIG)
    live_cfg = replace(cfg, symbol="EURUSD")

    eng = BacktestEngine(cfg)
    closed = eng.run(df, retain_wave_snapshot=True)
    eng_ts = eng.trend_states_per_bar
    live_ts = compute_trend_states_per_bar(df, detect_waves(df, cfg), cfg)
    n = min(len(eng_ts), len(live_ts))
    diff_set = {i for i in range(n) if eng_ts[i].direction != live_ts[i].direction}

    def is_wave(t):
        return classify_position_kind(
            is_pp=bool(getattr(t, "is_pp", 0)), is_counter=bool(getattr(t, "is_counter", 0)),
            is_bos_reentry=bool(getattr(t, "is_bos_reentry", 0)),
            is_two_sided_mirror=bool(getattr(t, "is_two_sided_mirror", 0)),
            is_ext=bool(getattr(t, "is_ext", 0)),
            entry_tag=str(getattr(t, "entry_tag", "base"))) == "WAVE"

    bt_by, bt_exposed = {}, {}
    for t in closed:
        if not is_wave(t):
            continue
        wt = str(t.wave_time)
        bt_by.setdefault(wt, []).append(t)
        eb = int(t.close_bar) - int(t.bars_held)
        if any(b in diff_set for b in range(eb, int(t.close_bar) + 1)):
            bt_exposed[wt] = True

    fake = install_fake(live_cfg.symbol, live_cfg.contract_size)
    lv = run_e2e(df, live_cfg, fake)
    lv_by = {}
    for t in lv:
        wt = _clean_wave_time(getattr(t, "comment", ""))
        lv_by.setdefault(wt, []).append(t)

    common = sorted(set(bt_by) & set(lv_by))
    cause2 = [wt for wt in common if not bt_exposed.get(wt)]

    cat_pnl = Counter()
    cat_cnt = Counter()
    rows = []
    for wt in cause2:
        bts, lvs = bt_by[wt], lv_by[wt]
        bp = sum(t.pnl_usd for t in bts)
        lp = sum(t.pnl_usd for t in lvs)
        d = lp - bp
        if abs(d) <= 1.0:
            cat_pnl["OK_shoda"] += d; cat_cnt["OK_shoda"] += 1
            continue
        br, lr = bts[0], lvs[0]
        b_eb = int(br.close_bar) - int(br.bars_held)
        l_eb = int(getattr(lr, "entry_bar", 0))
        b_rsn = _norm_reason(getattr(br, "close_reason", ""))
        l_rsn = _norm_reason(getattr(lr, "reason", ""))
        if len(bts) != len(lvs):
            cat = "POCET_TRADES"
        elif b_eb != l_eb:
            cat = "ENTRY_BAR"
        elif b_rsn != l_rsn:
            cat = f"EXIT_REASON({b_rsn}->{l_rsn})"
        elif int(br.close_bar) != int(getattr(lr, "close_bar", 0)):
            cat = "EXIT_BAR(stejny duvod)"
        else:
            cat = "JINE"
        cat_pnl[cat] += d
        cat_cnt[cat] += 1
        rows.append((abs(d), wt, bp, lp, d, b_eb, l_eb, b_rsn, l_rsn,
                     int(br.close_bar), int(getattr(lr, "close_bar", 0)),
                     len(bts), len(lvs)))

    print("\n" + "=" * 72)
    print(f"PRICINA 2 detail — {len(cause2)} vln (shodny trend)")
    print("=" * 72)
    print("  Kategorie rozdilu (delta = LV - BT):")
    for cat, _ in sorted(cat_pnl.items(), key=lambda kv: kv[1]):
        print(f"    {cat:<28} pocet={cat_cnt[cat]:>3}  delta_pnl={cat_pnl[cat]:>9.0f}")

    rows.sort(reverse=True)
    print("\n  TOP 25 dle |delta|:")
    print(f"  {'wave_time':<14}{'BT':>7}{'LV':>7}{'d':>7}  {'BTeb':>5}{'LVeb':>5} "
          f"{'BTrsn':<22}{'LVrsn':<22}{'BTcb':>6}{'LVcb':>6} {'nBT':>3}{'nLV':>3}")
    for r in rows[:25]:
        _, wt, bp, lp, d, beb, leb, brsn, lrsn, bcb, lcb, nbt, nlv = r
        print(f"  {wt:<14}{bp:>7.0f}{lp:>7.0f}{d:>7.0f}  {beb:>5}{leb:>5} "
              f"{brsn:<22}{lrsn:<22}{bcb:>6}{lcb:>6} {nbt:>3}{nlv:>3}")


if __name__ == "__main__":
    main()
