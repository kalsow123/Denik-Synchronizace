"""Tier 1 (prop-firm ve workeru) + Tier 2 (CSV před xlsx)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from backtest.grid.aggregator import build_grid_report
from backtest.grid.grid_report_io import (
    GRID_PROP_FIRM_CSV,
    GRID_REPORT_CSV,
    build_grid_workbook_sheets,
    write_grid_csv_exports,
    write_grid_progress_workbook,
)
from backtest.grid.grid_runner import run_single
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.io.excel_export import GRID_REPORT_XLSX, GRID_SHEET_VYSLEDKY, load_grid_report_sheet
from backtest.prop_firm.compliance import apply_prop_firm_compliance


def _sample_results_with_trades() -> dict:
    records = [
        {
            "entry_time": "2026-01-02 10:00:00",
            "close_time": "2026-01-02 18:00:00",
            "entry_price": 1.10,
            "sl": 1.09,
            "lot": 0.5,
            "pnl_usd": 120.0,
        }
    ]
    return {
        "bot_a": {
            "net_pnl_usd": 120.0,
            "max_drawdown_pct": -4.5,
            "total_trades": 1,
            "_prop_trades": records,
            "config": {
                "bot_name": "bot_a",
                "_grid_test_pozice": 1,
                "symbol": "EURUSD",
                "timeframe": "M30",
                "date_from": "2026-01-01",
                "date_to": "2026-05-10",
                "contract_size": 100_000.0,
                "risk_usd": 500.0,
            },
        }
    }


def test_precomputed_prop_firm_matches_legacy():
    results = _sample_results_with_trades()
    df_report = build_grid_report(results)
    preset_names = ["FTMO"]

    legacy_report, legacy_long = apply_prop_firm_compliance(
        df_report.copy(),
        results,
        preset_names,
        account_size_override=100_000,
    )

    stats = results["bot_a"]
    from backtest.prop_firm.compliance import attach_prop_firm_to_stats

    attach_prop_firm_to_stats(
        "bot_a",
        stats,
        preset_names,
        account_size_override=100_000,
    )
    assert "_prop_firm_wide" in stats
    assert "_prop_trades" not in stats

    pre_report, pre_long = apply_prop_firm_compliance(
        df_report.copy(),
        results,
        preset_names,
        account_size_override=100_000,
    )

    wide_cols = [c for c in legacy_report.columns if "__" in c]
    pd.testing.assert_frame_equal(
        legacy_report[wide_cols].sort_index(axis=1),
        pre_report[wide_cols].sort_index(axis=1),
    )
    assert len(legacy_long) == len(pre_long)
    for col in ("scale_factor", "projected_net_pnl_at_max_risk_usd", "challenge_passed"):
        assert legacy_long[col].iloc[0] == pre_long[col].iloc[0]


def test_worker_prop_firm_via_run_single(monkeypatch):
    profile = get_profile("EXAMPLE")
    combo = generate_combinations(profile)[0]
    monkeypatch.setattr(
        "backtest.grid.grid_runner._WORKER_PROP_FIRM_OPTS",
        {
            "preset_names": ["FTMO"],
            "config_path": None,
            "account_size_usd": 100_000,
        },
    )
    name, stats = run_single(combo)
    assert "error" not in stats
    assert stats.get("_prop_firm_wide")
    assert stats.get("_prop_firm_long_rows")
    assert "_prop_trades" not in stats


def test_write_grid_csv_exports(tmp_path):
    results = _sample_results_with_trades()
    profile = get_profile("EXAMPLE")
    sheets, df_report, df_long, _ = build_grid_workbook_sheets(
        results, profile=profile, args=None
    )
    paths = write_grid_csv_exports(tmp_path, sheets, df_report, df_long)
    assert (tmp_path / GRID_REPORT_CSV) in paths
    assert (tmp_path / GRID_REPORT_CSV).is_file()
    if not df_long.empty:
        assert (tmp_path / GRID_PROP_FIRM_CSV).is_file()


def test_write_grid_progress_workbook_csv_before_xlsx(tmp_path):
    results = _sample_results_with_trades()
    write_grid_progress_workbook(
        results,
        tmp_path,
        "EXAMPLE",
        args=None,
        done=1,
        total=1,
        final=True,
        quiet=True,
    )
    csv_path = tmp_path / GRID_REPORT_CSV
    xlsx_path = tmp_path / GRID_REPORT_XLSX
    assert csv_path.is_file()
    assert xlsx_path.is_file()
    assert csv_path.stat().st_mtime <= xlsx_path.stat().st_mtime + 1
    df_xlsx = load_grid_report_sheet(xlsx_path, GRID_SHEET_VYSLEDKY)
    df_csv = pd.read_csv(csv_path)
    assert len(df_xlsx) == len(df_csv)
