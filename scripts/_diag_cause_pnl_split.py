"""
Rozdeli PnL gap na 132 spolecnych vlnach na:
  - Pricina 1 (trend-source): engine trade zivot protina divergencni trend bar
  - Pricina 2 (fill/identita/exit): trend identicky cely zivot

Spusti engine + E2E (fake broker) jednou, normalizuje wave_time klice.
Spusteni: $env:E2E_FIRE_ON_BIRTH="1"; .venv\\Scripts\\python.exe scripts/_diag_cause_pnl_split.py
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATE_FROM, DATE_TO = "2025-11-10", "2026-05-09"


def main() -> None:
    from backtest.data_loader import filter_by_date_range, load_csv
    from backtest.engine import BacktestEngine
    from backtest.stats import classify_position_kind
    from config.bot_config import LIVE_BOT_CONFIG
    from config.position_modes import resolve_grid_engine_config
    from strategy.wave_detection import detect_waves
    from strategy.trend_bos import compute_trend_states_per_bar
    from scripts.e2e_live_broker_sim import FakeMt5, run_e2e, _clean_wave_time, install_fake

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

    bt_by = {}
    bt_exposed = {}
    for t in closed:
        if not is_wave(t):
            continue
        wt = str(t.wave_time)
        bt_by.setdefault(wt, []).append(t)
        eb = int(t.close_bar) - int(t.bars_held)
        cb = int(t.close_bar)
        if any(b in diff_set for b in range(eb, cb + 1)):
            bt_exposed[wt] = True

    fake = install_fake(live_cfg.symbol, live_cfg.contract_size)
    lv = run_e2e(df, live_cfg, fake)
    lv_by = {}
    for t in lv:
        wt = _clean_wave_time(getattr(t, "comment", ""))
        lv_by.setdefault(wt, []).append(t)

    common = sorted(set(bt_by) & set(lv_by))
    c1_bt = c1_lv = c2_bt = c2_lv = 0.0
    c1_n = c2_n = 0
    for wt in common:
        bp = sum(t.pnl_usd for t in bt_by[wt])
        lp = sum(t.pnl_usd for t in lv_by[wt])
        if bt_exposed.get(wt):
            c1_bt += bp; c1_lv += lp; c1_n += 1
        else:
            c2_bt += bp; c2_lv += lp; c2_n += 1

    print("\n" + "=" * 64)
    print("PnL ROZPAD 132 SPOLECNYCH VLN dle priciny")
    print("=" * 64)
    print(f"  PRICINA 1 (trend-source, {c1_n} vln): BT {c1_bt:.0f} / LV {c1_lv:.0f} "
          f"-> delta {c1_lv-c1_bt:.0f}")
    print(f"  PRICINA 2 (fill/exit,    {c2_n} vln): BT {c2_bt:.0f} / LV {c2_lv:.0f} "
          f"-> delta {c2_lv-c2_bt:.0f}")
    print(f"  CELKEM common: delta {(c1_lv+c2_lv)-(c1_bt+c2_bt):.0f}")


if __name__ == "__main__":
    main()
