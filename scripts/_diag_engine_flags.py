"""Pro 32 BT-only vln vypise, JAK je engine otevrel (flagy obchodu):
is_bos_reentry / is_ext / is_counter / entry_tag / dir / entry_bar / close_reason.
To rekne, co ma live delat (primarni vs bos-retro vs counter)."""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATE_FROM = "2025-11-10"
DATE_TO = "2026-05-09"
CSV = ROOT / "data" / "EURUSD_M30.csv"

BT_ONLY = {
    '202511261030', '202511261830', '202512051800', '202512102200', '202601021800',
    '202601122230', '202601220130', '202601261800', '202602031300', '202602040600',
    '202602131530', '202602162330', '202602192330', '202602230130', '202602231400',
    '202602250900', '202602251930', '202603051030', '202603160400', '202603170900',
    '202603171200', '202603181600', '202603242300', '202603250100', '202603310430',
    '202604082200', '202604092030', '202604171600', '202604301400', '202604301800',
    '202605061930', '202605072330',
}


def main() -> None:
    from backtest.data_loader import filter_by_date_range, load_csv
    from backtest.engine import BacktestEngine
    from config.bot_config import LIVE_BOT_CONFIG
    from config.position_modes import resolve_grid_engine_config

    cfg = resolve_grid_engine_config(LIVE_BOT_CONFIG)
    df = filter_by_date_range(load_csv(str(CSV)), DATE_FROM, DATE_TO).reset_index(drop=True)
    closed = BacktestEngine(cfg).run(df, retain_wave_snapshot=False)

    def g(t, k, d=0):
        return getattr(t, k, d)

    print(f"{'wave_time':<14}{'dir':>4}{'bosRE':>6}{'ext':>4}{'cnt':>4}{'tag':>8}{'eb':>6}{'cb':>6}  {'reason':<22}{'pnl':>8}")
    rows = []
    for t in closed:
        wt = str(g(t, "wave_time", ""))
        if wt in BT_ONLY:
            rows.append(t)
    rows.sort(key=lambda t: str(g(t, "wave_time", "")))
    seen = set()
    for t in rows:
        wt = str(g(t, "wave_time", ""))
        seen.add(wt)
        eb = int(g(t, "close_bar", 0)) - int(g(t, "bars_held", 0))
        print(f"{wt:<14}{int(g(t,'dir',0)):>4}{int(bool(g(t,'is_bos_reentry',0))):>6}"
              f"{int(bool(g(t,'is_ext',0))):>4}{int(bool(g(t,'is_counter',0))):>4}"
              f"{str(g(t,'entry_tag','')):>8}{eb:>6}{int(g(t,'close_bar',0)):>6}  "
              f"{str(g(t,'close_reason','')):<22}{float(g(t,'pnl_usd',0)):>8.0f}")
    missing = BT_ONLY - seen
    if missing:
        print(f"\n  NENI v engine closed (mozna jine wave_time / counter): {sorted(missing)}")


if __name__ == "__main__":
    main()
