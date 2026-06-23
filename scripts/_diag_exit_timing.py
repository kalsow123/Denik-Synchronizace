"""Bod 3: rozpad common-wave PnL delty podle typu exit-rozdilu."""
from __future__ import annotations
import sys
from collections import Counter
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DATE_FROM, DATE_TO = "2025-11-10", "2026-05-09"


def main() -> None:
    from config.bot_config import LIVE_BOT_CONFIG
    from config.position_modes import resolve_grid_engine_config
    from scripts.e2e_live_broker_sim import install_fake, run_e2e, _clean_wave_time

    cfg = resolve_grid_engine_config(LIVE_BOT_CONFIG)
    fake = install_fake(cfg.symbol, cfg.contract_size)

    from backtest.data_loader import filter_by_date_range, load_csv
    from backtest.engine import BacktestEngine
    from backtest.stats import classify_position_kind
    from runtime.live_wave_isolation import resolve_live_execution_config

    df = filter_by_date_range(load_csv("data/EURUSD_M30.csv"), DATE_FROM, DATE_TO).reset_index(drop=True)
    live_cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)

    def is_wave(t):
        return classify_position_kind(
            is_pp=bool(getattr(t, "is_pp", 0)), is_counter=bool(getattr(t, "is_counter", 0)),
            is_bos_reentry=bool(getattr(t, "is_bos_reentry", 0)),
            is_two_sided_mirror=bool(getattr(t, "is_two_sided_mirror", 0)),
            is_ext=bool(getattr(t, "is_ext", 0)),
            entry_tag=str(getattr(t, "entry_tag", "base"))) == "WAVE"

    eng = BacktestEngine(cfg)
    closed = eng.run(df, retain_wave_snapshot=False)
    bt = {str(t.wave_time): t for t in closed if is_wave(t)}

    lv_list = run_e2e(df, live_cfg, fake)
    lv = {}
    for t in lv_list:
        lv[_clean_wave_time(getattr(t, "comment", ""))] = t

    common = set(bt) & set(lv)

    def reason_base(r):
        r = str(r)
        for p in ("BOS_EXIT_WAVE_TARGET", "TP_WAVE_N", "EXT_BOS_CLOSE", "SL", "TP"):
            if r.startswith(p):
                return p
        return r

    cat_pnl = Counter(); cat_cnt = Counter()
    rows = []
    for wt in common:
        b, l = bt[wt], lv[wt]
        d = float(l.pnl_usd) - float(b.pnl_usd)
        br = reason_base(b.close_reason)
        lr = reason_base(getattr(l, "reason", getattr(l, "close_reason", "")))
        b_eb = int(b.close_bar) - int(b.bars_held)
        l_eb = int(getattr(l, "entry_bar", b_eb)) if hasattr(l, "entry_bar") else b_eb
        # kategorie
        if br == "EXT_BOS_CLOSE" or lr == "EXT_BOS_CLOSE":
            cat = "EXT_BOS_CLOSE (engine-only protekce)"
        elif abs(d) < 5:
            cat = "shoda (<5 USD)"
        elif br != lr:
            cat = f"reason mismatch {br}->{lr}"
        elif int(b.close_bar) != int(getattr(l, "close_bar", b.close_bar)):
            cat = "stejny reason, jiny close_bar"
        else:
            cat = "jine"
        cat_pnl[cat] += d; cat_cnt[cat] += 1
        rows.append((d, wt, br, lr, b.close_bar, getattr(l, "close_bar", "?"), b.pnl_usd, l.pnl_usd))

    print(f"COMMON: {len(common)} vln,  sum delta = {sum(r[0] for r in rows):.0f} USD\n")
    # zhrub kategorie (slouc reason mismatch)
    grp = Counter(); grpc = Counter()
    for c, v in cat_pnl.items():
        key = "reason mismatch (ruzne)" if c.startswith("reason mismatch") else c
        grp[key] += v; grpc[key] += cat_cnt[c]
    for c in sorted(grp, key=lambda x: grp[x]):
        print(f"  {c:<40} vln={grpc[c]:>3}  delta={grp[c]:>8.0f}")

    print("\n  TOP 15 nejvetsich rozdilu (|delta|):")
    rows.sort(key=lambda r: abs(r[0]), reverse=True)
    for d, wt, br, lr, bcb, lcb, bp, lp in rows[:15]:
        print(f"    {wt}  d={d:>7.0f}  {br}->{lr}  BTcb={bcb} LVcb={lcb}  "
              f"BT={bp:.0f} LV={lp:.0f}")


if __name__ == "__main__":
    main()
