"""Wave study report — maskovani metrik a parovani full twin."""
from __future__ import annotations

from backtest.grid.aggregator import build_grid_report
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.study_mode import apply_wave_isolation_report_stats, study_base_key


def test_wave_isolation_report_masks_counter_and_two_sided():
    stats = {
        "trades_wave": 10,
        "trades_wave_counter": 3,
        "trades_wave_two_sided": 12,
        "net_pnl_wave_usd": 1000.0,
        "net_pnl_wave_counter_usd": 200.0,
        "net_pnl_wave_two_sided_usd": -50.0,
        "net_pnl_usd": 1150.0,
        "total_trades": 25,
    }
    cfg = {"wave_isolation_study": True, "wave_positions_only": True}
    out = apply_wave_isolation_report_stats(stats, cfg)
    assert out["trades_wave_counter"] == 0
    assert out["trades_wave_two_sided"] == 0
    assert out["net_pnl_wave_counter_usd"] == 0.0
    assert out["net_pnl_usd"] == 1000.0
    assert out["total_trades"] == 10


def test_bot_finish_includes_full_twins_for_wave_target_n_study():
    combos = generate_combinations(get_profile("bot_finish"))
    assert len(combos) == 828
    twins = [c for c in combos if c.get("__wave_study_full_twin")]
    assert len(twins) == 128


def test_build_grid_report_pairs_isolation_with_full_twin():
    combos = generate_combinations(get_profile("bot_finish"))
    iso = next(c for c in combos if c.get("wave_isolation_study"))
    iso_key = study_base_key(iso)
    twin = next(
        c
        for c in combos
        if c.get("wave_counter_two_sided_enabled")
        and not c.get("wave_isolation_study")
        and study_base_key(c) == iso_key
    )
    wave_pnl = 12345.67
    results = {
        "full": {
            "net_pnl_usd": 20000.0,
            "net_pnl_wave_usd": wave_pnl,
            "trades_wave": 5,
            "trades_wave_counter": 1,
            "trades_wave_two_sided": 2,
            "total_trades": 8,
            "win_rate_pct": 50.0,
            "config": {**twin, "_grid_test_pozice": 1, "bot_name": "full"},
        },
        "iso": {
            "net_pnl_usd": 20000.0,
            "net_pnl_wave_usd": wave_pnl,
            "trades_wave": 5,
            "trades_wave_counter": 1,
            "trades_wave_two_sided": 2,
            "total_trades": 8,
            "win_rate_pct": 50.0,
            "config": {**iso, "_grid_test_pozice": 2, "bot_name": "iso"},
        },
    }
    df = build_grid_report(results)
    iso_row = df[df["combo_no"] == 2].iloc[0]
    assert iso_row["trades_wave_two_sided"] == 0
    assert iso_row["trades_wave_counter"] == 0
    assert iso_row["net_pnl_usd"] == wave_pnl
    assert int(iso_row["paired_full_combo_no"]) == 1
