"""
Bod 1: rozpad 32 BT-only WAVE vln na priciny, proc je live NEOTEVRE.
Kategorie:
  LOOK_AHEAD        — engine entry_bar < birth_bar (vstup drive nez vlna vznikla)
  NOT_IN_DETECT     — vlna neni v live detect_waves (jen pine_sim)
  GATED:<branch>    — vlna v detect_waves, ale live ji zahodil (trend/session/birth_gate/...)
  UNKNOWN           — zadny trace zaznam (nezjisteno)

Spusteni: $env:E2E_FIRE_ON_BIRTH="1"; .venv\\Scripts\\python.exe scripts/_diag_btonly_split.py
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATE_FROM, DATE_TO = "2025-11-10", "2026-05-09"


def main() -> None:
    # POZOR na poradi: install_fake MUSI byt pred prvnim importem zivych modulu
    # (infra.orders importuje MetaTrader5 pri importu). Mirror oficialniho main().
    from config.bot_config import LIVE_BOT_CONFIG
    from config.position_modes import resolve_grid_engine_config
    from scripts.e2e_live_broker_sim import run_e2e, _clean_wave_time, install_fake

    cfg = resolve_grid_engine_config(LIVE_BOT_CONFIG)
    fake = install_fake(cfg.symbol, cfg.contract_size)

    from backtest.data_loader import filter_by_date_range, load_csv
    from backtest.engine import BacktestEngine
    from backtest.stats import classify_position_kind
    from runtime.live_wave_isolation import resolve_live_execution_config
    from strategy.wave_detection import detect_waves
    import runtime.missed_bar_replay as mbr

    df = filter_by_date_range(load_csv("data/EURUSD_M30.csv"), DATE_FROM, DATE_TO).reset_index(drop=True)
    live_cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)
    live_cfg.live_study_two_sided_mirror_orders = True
    live_cfg.live_study_promoted_two_sided_as_wave = True

    eng = BacktestEngine(cfg)
    closed = eng.run(df, retain_wave_snapshot=True)
    birth = eng.wave_birth_by_time  # pine sim births (kdy vlna vznikla)

    def is_wave(t):
        return classify_position_kind(
            is_pp=bool(getattr(t, "is_pp", 0)), is_counter=bool(getattr(t, "is_counter", 0)),
            is_bos_reentry=bool(getattr(t, "is_bos_reentry", 0)),
            is_two_sided_mirror=bool(getattr(t, "is_two_sided_mirror", 0)),
            is_ext=bool(getattr(t, "is_ext", 0)),
            entry_tag=str(getattr(t, "entry_tag", "base"))) == "WAVE"

    bt_by = defaultdict(list)
    for t in closed:
        if is_wave(t):
            bt_by[str(t.wave_time)].append(t)

    # zapni trace pro vsechny BT wave_times PRED E2E behem
    mbr._TRACE_WAVES = set(bt_by.keys())
    mbr._TRACE_LOG.clear()

    live_waves = {str(w["wave_time"]) for w in detect_waves(df, cfg)}

    lv = run_e2e(df, live_cfg, fake)
    lv_wt = {_clean_wave_time(getattr(t, "comment", "")) for t in lv}

    bt_only = sorted(set(bt_by) - lv_wt)

    # trace branch per wave_time (posbiraj vsechny zaznamy)
    trace_by_wt = defaultdict(list)
    for bar_idx, wt, branch, kw in mbr._TRACE_LOG:
        trace_by_wt[str(wt)].append((bar_idx, branch, kw))

    cat_pnl = Counter()
    cat_cnt = Counter()
    rows = []
    for wt in bt_only:
        trades = bt_by[wt]
        pnl = sum(t.pnl_usd for t in trades)
        # earliest engine entry_bar pro tuto vlnu
        eb = min(int(t.close_bar) - int(t.bars_held) for t in trades)
        bbar = birth.get(wt)
        branches = [b for (_, b, _) in trace_by_wt.get(wt, [])]
        sent = any(b == "SENT_PRIMARY" for b in branches)
        skip_branches = [b for b in branches if b.startswith("skip:")]

        if bbar is not None and eb < int(bbar):
            cat = "LOOK_AHEAD"
        elif wt not in live_waves:
            cat = "NOT_IN_DETECT"
        elif skip_branches:
            cat = "GATED:" + skip_branches[0].split(":", 1)[1]
        elif sent:
            cat = "SENT_but_no_fill_or_closed_diff"
        else:
            cat = "UNKNOWN"

        cat_pnl[cat] += pnl
        cat_cnt[cat] += 1
        rows.append((pnl, wt, eb, bbar, cat,
                     ",".join(sorted(set(branches))) or "(zadny trace)"))

    print("\n" + "=" * 72)
    print(f"BT-ONLY ROZPAD — {len(bt_only)} vln (live je NEOTEVRE)")
    print("=" * 72)
    print("  Kategorie (PnL = co backtest na nich vydelal):")
    for cat in sorted(cat_pnl, key=lambda c: -cat_pnl[c]):
        print(f"    {cat:<42} vln={cat_cnt[cat]:>2}  pnl={cat_pnl[cat]:>9.0f}")
    la = sum(v for c, v in cat_pnl.items() if c == "LOOK_AHEAD")
    nid = sum(v for c, v in cat_pnl.items() if c == "NOT_IN_DETECT")
    gated = sum(v for c, v in cat_pnl.items() if c.startswith("GATED"))
    other = sum(cat_pnl.values()) - la - nid - gated
    print(f"\n  SHRNUTI dosazitelnosti na LIVE:")
    print(f"    NEdosazitelne (LOOK_AHEAD):           {la:>9.0f}  ({cat_cnt['LOOK_AHEAD']} vln)")
    print(f"    NEdosazitelne (NOT_IN_DETECT):        {nid:>9.0f}")
    print(f"    Potencialne dosazitelne (GATED):      {gated:>9.0f}")
    print(f"    Ostatni (UNKNOWN/SENT):               {other:>9.0f}")

    rows.sort()
    print("\n  Detaily (PnL vzestupne):")
    print(f"  {'wave_time':<14}{'pnl':>7}{'entry':>6}{'birth':>6}  {'kategorie':<28} branches")
    for pnl, wt, eb, bbar, cat, br in rows:
        print(f"  {wt:<14}{pnl:>7.0f}{eb:>6}{(bbar if bbar is not None else -1):>6}  {cat:<28} {br}")


if __name__ == "__main__":
    main()
