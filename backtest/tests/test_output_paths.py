"""output_paths — denní inkrement a sanitizace."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from backtest.output_paths import (
    grid_output_date_range,
    grid_output_symbol,
    grid_output_timeframe,
    grid_run_output_dir,
    live_match_output_dir,
    next_daily_output_dir,
    run_name_grid,
    safe_path_part,
    symbol_folder_name,
)


def test_safe_path_part():
    assert safe_path_part("EURUSD") == "EURUSD"
    assert safe_path_part("a b/c") == "a_b_c"


def test_symbol_folder_name_strips_liquidity_suffix():
    assert symbol_folder_name("USDCAD.x") == "USDCAD"
    assert symbol_folder_name("EURUSD") == "EURUSD"
    assert symbol_folder_name("GER40.cash") == "GER40"
    assert symbol_folder_name("EU50p") == "EU50p"


def test_next_daily_output_dir_uses_pair_without_suffix(tmp_path: Path):
    out = next_daily_output_dir(
        tmp_path / "results",
        "USDCAD.x",
        "grid_test_M15",
        run_date=datetime(2026, 5, 20),
    )
    assert out.parent.name == "USDCAD"
    assert "USDCAD.x" not in str(out)


def test_next_daily_output_dir_increment(tmp_path: Path):
    base = tmp_path / "results"
    d1 = next_daily_output_dir(base, "EURUSD", "grid_best_M15", run_date=datetime(2026, 5, 20))
    assert d1.name == "grid_best_M15_20260520_001"
    d2 = next_daily_output_dir(base, "EURUSD", "grid_best_M15", run_date=datetime(2026, 5, 20))
    assert d2.name == "grid_best_M15_20260520_002"


def test_live_match_output_dir_is_symbol_folder(tmp_path: Path):
    out = live_match_output_dir(
        tmp_path / "results",
        "EURUSD",
        config_name="LIVE_BOT_CONFIG",
        timeframe_label="M30",
        date_from="2025-11-10",
        date_to="2026-05-09",
    )
    assert out.parent.name == "EURUSD"
    assert out.name == "grid_LIVE_BOT_M30_2025-11-10_2026-05-09_001"
    assert out.is_dir()
    out2 = live_match_output_dir(
        tmp_path / "results",
        "EURUSD",
        config_name="LIVE_BOT_CONFIG",
        timeframe_label="M30",
        date_from="2025-11-10",
        date_to="2026-05-09",
    )
    assert out2.name == "grid_LIVE_BOT_M30_2025-11-10_2026-05-09_002"


def test_grid_mixed_labels():
    combos = [
        {"symbol": "EURUSD", "timeframe": "M15"},
        {"symbol": "GBPUSD.x", "timeframe": "H1"},
    ]
    assert grid_output_symbol(combos) == "MIXED"


def test_grid_single_pair_folder():
    combos = [{"symbol": "USDCAD.x", "timeframe": "M15"}]
    assert grid_output_symbol(combos) == "USDCAD"
    assert grid_output_timeframe(combos) == "M15"
    assert run_name_grid("bot_optimalisation", combos).startswith("grid_bot_optimalisation_")


def test_grid_output_date_range_single_period():
    combos = [
        {"date_from": "2025-04-24", "date_to": "2026-04-24"},
        {"date_from": "2025-04-24", "date_to": "2026-04-24"},
    ]
    assert grid_output_date_range(combos) == ("2025-04-24", "2026-04-24")


def test_grid_run_output_dir_uses_period_and_increment(tmp_path: Path):
    combos = [
        {
            "symbol": "USDCAD.x",
            "timeframe": "M30",
            "date_from": "2025-04-24",
            "date_to": "2026-04-24",
        }
    ]
    base = tmp_path / "results"
    d1 = grid_run_output_dir(base, "full_grid", combos)
    assert d1.name == "grid_full_grid_M30_2025-04-24_2026-04-24_001"
    d2 = grid_run_output_dir(base, "full_grid", combos)
    assert d2.name == "grid_full_grid_M30_2025-04-24_2026-04-24_002"
    assert d1.parent == d2.parent == base / "USDCAD"
