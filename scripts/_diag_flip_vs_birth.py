"""Pro 32 BT-only vln: flip_bar (z bos_flip_map) vs birth + engine entry_bar.
Cil: zjistit zda engine otevira vlny PRED jejich potvrzenim (birth) = look-ahead,
ktery live kauzalne nemuze replikovat, nebo zda jde o opravitelny routing bug."""
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
    from backtest.engine import BacktestEngine
    from config.bot_config import LIVE_BOT_CONFIG
    from config.position_modes import resolve_grid_engine_config
    from strategy.wave_detection_pine import (
        compute_wave_birth_bars_pine, run_pine_wave_simulation,
    )
    from strategy.trend_bos import (
        compute_bos_wave_flip_map, reconcile_bos_flip_map_with_wave_sequence,
        _detect_close_bos_timeline_flips,
    )
    from strategy.wave_sequence import sync_wave_sequence_state

    cfg = resolve_grid_engine_config(LIVE_BOT_CONFIG)
    df = filter_by_date_range(load_csv(str(CSV)), DATE_FROM, DATE_TO).reset_index(drop=True)
    waves, birth, _, _ = run_pine_wave_simulation(df, cfg)
    seq_info, _ = sync_wave_sequence_state(df, waves, cfg)

    flips = _detect_close_bos_timeline_flips(df, waves, cfg, wave_birth_bars=birth)
    flip_map = reconcile_bos_flip_map_with_wave_sequence(
        compute_bos_wave_flip_map(df, waves, cfg, wave_birth_bars=birth),
        flips, waves, seq_info, birth,
    )
    # flip_bar per wave_time
    flip_bar_by_wt: dict[str, int] = {}
    for bar_ix, wt in flip_map.items():
        flip_bar_by_wt[str(wt)] = int(bar_ix)

    # engine entry bars
    closed = BacktestEngine(cfg).run(df, retain_wave_snapshot=False)
    eb_by_wt: dict[str, int] = {}
    for t in closed:
        wt = str(getattr(t, "wave_time", ""))
        eb = int(getattr(t, "close_bar", 0)) - int(getattr(t, "bars_held", 0))
        eb_by_wt.setdefault(wt, eb)  # prvni (nejdrivejsi) entry

    print(f"{'wave_time':<14}{'birth':>7}{'flip_bar':>9}{'eng_entry':>10}{'entry<birth?':>14}")
    look_ahead = 0
    for wt in BT_ONLY:
        b = birth.get(wt)
        fb = flip_bar_by_wt.get(wt)
        eb = eb_by_wt.get(wt)
        la = (eb is not None and b is not None and int(eb) < int(b))
        if la:
            look_ahead += 1
        print(f"{wt:<14}{str(b):>7}{str(fb):>9}{str(eb):>10}"
              f"{('  YES LOOK-AHEAD' if la else ''):>14}")
    print(f"\n  vln s engine entry PRED birth (look-ahead): {look_ahead}/{len(BT_ONLY)}")


if __name__ == "__main__":
    main()
