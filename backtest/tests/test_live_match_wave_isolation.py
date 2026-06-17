"""live_match — wave_isolation_study report parita s grid combo 2."""
from __future__ import annotations

from backtest.grid.aggregator import build_grid_report
from backtest.grid.study_mode import apply_wave_isolation_report_stats, resolve_study_mode
from backtest.profile_resolver import resolve_live_match_pair
from config.bot_config import LIVE_BOT_CONFIG


def test_live_match_pair_engine_vs_report_combo():
    engine, combo = resolve_live_match_pair(
        "LIVE_BOT_CONFIG",
        date_from="2025-11-10",
        date_to="2026-05-09",
    )
    assert combo["wave_isolation_study"] is True
    assert resolve_study_mode(combo) == "wave_isolation"
    assert engine.wave_isolation_study is False
    assert engine.wave_counter_two_sided_enabled is True


def test_live_match_report_zeros_non_wave_columns():
    _, combo = resolve_live_match_pair("LIVE_BOT_CONFIG")
    stats = apply_wave_isolation_report_stats(
        {
            "total_trades": 201,
            "net_pnl_usd": 32789.45,
            "trades_wave": 142,
            "net_pnl_wave_usd": 32876.86,
            "trades_wave_counter": 26,
            "trades_ext_bos": 20,
            "net_pnl_wave_counter_usd": 500.0,
            "net_pnl_ext_bos_usd": 100.0,
        },
        combo,
    )
    assert stats["total_trades"] == 142
    assert stats["net_pnl_usd"] == 32876.86
    assert stats["trades_wave_counter"] == 0
    assert stats["trades_ext_bos"] == 0
    assert stats["net_pnl_wave_counter_usd"] == 0.0


def test_live_match_grid_report_study_mode():
    _, combo = resolve_live_match_pair("LIVE_BOT_CONFIG")
    stats = {
        "net_pnl_usd": 32876.86,
        "total_trades": 142,
        "trades_wave": 142,
        "net_pnl_wave_usd": 32876.86,
        "max_drawdown_pct": -6.0,
        "config": combo,
    }
    df = build_grid_report({combo["bot_name"]: stats})
    assert bool(df.iloc[0]["wave_isolation_study"]) is True
    assert df.iloc[0]["study_mode"] == "wave_isolation"
    assert int(df.iloc[0]["trades"]) == 142
    assert int(df.iloc[0]["trades_wave_counter"]) == 0
