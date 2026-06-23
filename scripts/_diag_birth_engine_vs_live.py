"""Porovnani birth map: engine (run_pine_wave_simulation) vs live
(compute_wave_birth_bars_pine) pro 32 BT-only vln. Pokud se lisi, je to
primarni pricina rozkolu vstupu (engine vstupuje na jinem baru nez live)."""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATE_FROM = "2025-11-10"
DATE_TO = "2026-05-09"
CSV = ROOT / "data" / "EURUSD_M30.csv"

BT_ONLY = [
    '202511261030', '202511261830', '202512051800', '202512102200', '202601021800',
    '202601122230', '202601220130', '202601261800', '202602031300', '202602040600',
    '202602131530', '202602162330', '202602192330', '202602230130', '202602231400',
    '202602250900', '202602251930', '202603051030', '202603160400', '202603170900',
    '202603171200', '202603181600', '202603242300', '202603250100', '202603310430',
    '202604082200', '202604092030', '202604171600', '202604301400', '202604301800',
    '202605061930', '202605072330',
]


def main() -> None:
    from backtest.data_loader import filter_by_date_range, load_csv
    from config.bot_config import LIVE_BOT_CONFIG
    from config.position_modes import resolve_grid_engine_config
    from runtime.live_wave_isolation import resolve_live_execution_config
    from strategy.wave_detection_pine import (
        compute_wave_birth_bars_pine, run_pine_wave_simulation,
    )

    eng_cfg = resolve_grid_engine_config(LIVE_BOT_CONFIG)
    live_cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)
    df = filter_by_date_range(load_csv(str(CSV)), DATE_FROM, DATE_TO).reset_index(drop=True)

    _, eng_birth, _, _ = run_pine_wave_simulation(df, eng_cfg)
    live_birth = compute_wave_birth_bars_pine(df, live_cfg)

    print(f"{'wave_time':<14}{'eng_birth':>10}{'live_birth':>11}{'delta':>7}")
    n_diff = 0
    for wt in BT_ONLY:
        eb = eng_birth.get(wt)
        lb = live_birth.get(wt)
        d = "" if (eb is None or lb is None) else str(int(lb) - int(eb))
        flag = "  <-- LISI SE" if (eb is not None and lb is not None and eb != lb) else (
            "  <-- chybi" if (eb is None or lb is None) else "")
        if flag:
            n_diff += 1
        print(f"{wt:<14}{str(eb):>10}{str(lb):>11}{d:>7}{flag}")
    print(f"\n  vln s rozdilnym/chybejicim birth: {n_diff}/{len(BT_ONLY)}")

    # globalni porovnani
    all_keys = set(eng_birth) | set(live_birth)
    glob_diff = sum(1 for k in all_keys if eng_birth.get(k) != live_birth.get(k))
    print(f"  GLOBALNE rozdilnych birth (vsechny vlny): {glob_diff}/{len(all_keys)}")


if __name__ == "__main__":
    main()
