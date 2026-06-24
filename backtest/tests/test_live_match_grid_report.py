"""live_match — grid_report.xlsx (vysledky, summaries, ddi_epizody) bez prop-firm."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from backtest.grid.aggregator import build_grid_report
from backtest.grid.grid_report_io import (
    GRID_REPORT_XLSX,
    append_e2e_sheet_to_grid_report,
    write_live_match_grid_report,
)
from backtest.grid.study_mode import study_base_key
from backtest.grid.translator import bot_config_to_grid_combo_dict
from backtest.io.excel_export import (
    GRID_SHEET_DDI_EPIZODY,
    GRID_SHEET_E2E,
    GRID_SHEET_PROP_FIRM,
    GRID_SHEET_SUMMARIES,
    GRID_SHEET_VYSLEDKY,
    load_grid_report_sheet,
)
from config.bot_config import LIVE_BOT_CONFIG


def _sample_stats() -> dict:
    return {
        "net_pnl_usd": 1500.0,
        "max_drawdown_pct": -8.5,
        "max_drawdown_pct_vs_peak": -6.0,
        "total_trades": 12,
        "win_rate_pct": 55.0,
        "profit_factor": 1.4,
        "trades_wave": 12,
        "net_pnl_wave_usd": 1500.0,
        "max_drawdown_pct_wave": -8.5,
        "ddi_profile": {
            "dnu_testu_celkem": 90,
            "pocet_epizod_ge10pct": 1,
            "max_ddi_pct": -12.0,
            "median_ddi_pct": -4.0,
            "p90_ddi_pct": -9.0,
            "episodes": [
                {
                    "start": "2025-12-01",
                    "end": "OTEVRENO",
                    "max_ddi_pct": -12.0,
                }
            ],
        },
        "config": {
            "bot_name": "LIVE_EURUSD_M30_v1",
            "_grid_test_pozice": 1,
            "symbol": "EURUSD",
            "timeframe": "M30",
            "date_from": "2025-11-10",
            "date_to": "2026-05-09",
            "wave_min_pct": 0.26,
            "rrr": 2.5,
            "fib_level": 0.55,
            "entry_mode": "market_fallback",
            "tp_mode": "wave_target_n",
            "wave_isolation_study": True,
            "wave_counter_two_sided_enabled": True,
        },
    }


def test_live_match_grid_report_sheets(tmp_path: Path):
    out = write_live_match_grid_report(_sample_stats(), tmp_path)
    assert out is not None
    assert out.name == GRID_REPORT_XLSX
    assert load_grid_report_sheet(out, GRID_SHEET_VYSLEDKY).shape[0] == 1
    assert load_grid_report_sheet(out, GRID_SHEET_SUMMARIES).shape[0] >= 1
    assert load_grid_report_sheet(out, GRID_SHEET_DDI_EPIZODY).shape[0] >= 1
    with pd.ExcelFile(out) as xf:
        assert GRID_SHEET_PROP_FIRM not in xf.sheet_names


def test_append_e2e_sheet_to_grid_report(tmp_path: Path):
    stats = _sample_stats()
    out = write_live_match_grid_report(stats, tmp_path)
    assert out is not None
    combo = stats["config"]
    e2e_stats = {
        **stats,
        "net_pnl_usd": 1200.0,
        "net_pnl_wave_usd": 1200.0,
        "total_trades": 10,
        "trades_wave": 10,
        "max_drawdown_pct": -7.0,
        "max_drawdown_pct_wave": -7.0,
        "win_rate_pct": 52.5,
        "ddi_profile": {
            "dnu_testu_celkem": 90,
            "max_ddi_pct": -9.5,
            "p90_ddi_pct": -3.2,
            "median_ddi_pct": -2.0,
            "pct_dnu_ge_10": 0.0,
        },
    }
    parity = {
        "common_wave_times": 8,
        "bt_only_wave_times": 2,
        "lv_only_wave_times": 1,
        "backtest_net_pnl_usd": 1500.0,
        "live_e2e_net_pnl_usd": 1200.0,
        "backtest_win_rate_pct": 55.0,
        "live_e2e_win_rate_pct": 52.5,
    }
    out2 = append_e2e_sheet_to_grid_report(tmp_path, e2e_stats, combo, parity=parity)
    assert out2 is not None
    df_e2e = load_grid_report_sheet(out2, GRID_SHEET_E2E)
    assert len(df_e2e) == 1
    assert "max_dd_%_vs_initial" in df_e2e.columns
    assert "max_ddi_pct" in df_e2e.columns
    assert "p90_ddi_pct" in df_e2e.columns
    assert "win_rate_%" in df_e2e.columns
    assert float(df_e2e.iloc[0]["win_rate_%"]) == 52.5
    assert float(df_e2e.iloc[0]["backtest_win_rate_%"]) == 55.0
    assert float(df_e2e.iloc[0]["live_e2e_net_pnl_usd"]) == 1200.0
    with pd.ExcelFile(out2) as xf:
        assert GRID_SHEET_E2E in xf.sheet_names
        assert GRID_SHEET_SUMMARIES in xf.sheet_names


def test_study_base_key_hashable_with_list_values():
    cfg = {"wave_allowed_sessions": ["LONDON", "USA"], "rrr": 2.5}
    key = study_base_key(cfg)
    assert isinstance(key, tuple)
    {key: 1}


def test_live_bot_config_combo_builds_grid_report():
    combo = bot_config_to_grid_combo_dict(
        LIVE_BOT_CONFIG,
        date_from="2025-11-10",
        date_to="2026-05-09",
    )
    stats = {
        "net_pnl_usd": 100.0,
        "max_drawdown_pct": -5.0,
        "total_trades": 1,
        "config": combo,
    }
    df = build_grid_report({combo["bot_name"]: stats})
    assert len(df) == 1
