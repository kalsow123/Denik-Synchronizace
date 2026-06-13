from __future__ import annotations

import pandas as pd

from backtest.stats import classify_position_kind, compute_stats


def test_compute_stats_breaks_out_trade_counts_by_pa():
    df = pd.DataFrame(
        [
            {
                "close_reason": "TP",
                "close_time": "2026-03-01 10:00:00",
                "pnl_usd": 10.0,
                "position_kind": "WAVE",
            },
            {
                "close_reason": "SL",
                "close_time": "2026-03-01 11:00:00",
                "pnl_usd": -4.0,
                "position_kind": "WAVE_COUNTER",
            },
            {
                "close_reason": "TP",
                "close_time": "2026-03-01 12:00:00",
                "pnl_usd": 3.0,
                "position_kind": "WAVE_TWO_SIDED",
            },
            {
                "close_reason": "TP",
                "close_time": "2026-03-01 12:30:00",
                "pnl_usd": 2.5,
                "position_kind": "PP",
            },
            {
                "close_reason": "SL",
                "close_time": "2026-03-01 13:00:00",
                "pnl_usd": -2.0,
                "position_kind": "EXT",
            },
            {
                "close_reason": "TP",
                "close_time": "2026-03-01 14:00:00",
                "pnl_usd": 5.0,
                "position_kind": "BOS",
            },
            {
                "close_reason": "SL",
                "close_time": "2026-03-01 15:00:00",
                "pnl_usd": -1.0,
                "position_kind": "EXT_BOS",
            },
        ]
    )
    df["close_time"] = pd.to_datetime(df["close_time"])

    stats = compute_stats(df)

    assert stats["trades_wave"] == 1
    assert stats["trades_wave_counter"] == 1
    assert stats["trades_wave_two_sided"] == 1
    assert stats["trades_pp"] == 1
    assert stats["trades_ext"] == 1
    assert stats["trades_bos"] == 1
    assert stats["trades_ext_bos"] == 1
    assert stats["net_pnl_wave_usd"] == 10.0
    assert stats["net_pnl_wave_counter_usd"] == -4.0
    assert stats["net_pnl_wave_two_sided_usd"] == 3.0
    assert stats["net_pnl_non_pp_usd"] == 11.0


def test_classify_position_kind_wave_counter_by_entry_tag():
    assert classify_position_kind(
        is_pp=False,
        is_counter=False,
        is_bos_reentry=False,
        is_ext=False,
        entry_tag="wave_counter",
    ) == "WAVE_COUNTER"
