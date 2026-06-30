"""Audit WAVE_COUNTER counts for EXAMPLE grid combos (fresh engine run).

Usage:
  python scripts/audit/audit_example_counters.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.grid.backtest_conf import get_profile, generate_combinations
from backtest.grid.translator import grid_dict_to_bot_config
from backtest.grid.data_cache import load_data
from backtest.engine import BacktestEngine
from backtest.stats import classify_position_kind
from strategy.wave_sequence import is_tp_wave_index


def main() -> None:
    df = load_data("EURUSD", "M30", "2026-03-03", "2026-05-10")
    combos = list(generate_combinations(get_profile("EXAMPLE")))
    print("EXAMPLE counter combos (fresh):")
    for i, c in enumerate(combos):
        cfg = grid_dict_to_bot_config(c)
        if not cfg.counter_position_enabled:
            continue
        eng = BacktestEngine(cfg)
        trades = eng.run(df)
        seq = eng.wave_sequence_info
        wc = [
            t
            for t in trades
            if classify_position_kind(
                is_pp=t.is_pp,
                is_counter=t.is_counter,
                is_bos_reentry=t.is_bos_reentry,
                is_two_sided_mirror=getattr(t, "is_two_sided_mirror", False),
                is_ext=getattr(t, "is_ext", False),
                entry_tag=getattr(t, "entry_tag", "base"),
            )
            == "WAVE_COUNTER"
        ]
        bad = sum(
            1
            for t in wc
            if not is_tp_wave_index(
                (seq.get(t.wave_time).index_in_trend if seq.get(t.wave_time) else 0),
                4,
            )
        )
        print(
            f"  idx={i+1} tp={cfg.tp_mode.value:14s} pcm={cfg.pending_cancel_mode} "
            f"placed={eng.wave_debug.get('counter_positions_placed')} "
            f"closed={len(wc)} bad_idx={bad}"
        )


if __name__ == "__main__":
    main()
