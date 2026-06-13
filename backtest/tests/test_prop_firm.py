"""Testy prop-firm compliance (scale_factor, binding, challenge)."""
from __future__ import annotations

import pandas as pd
import pytest

from backtest.prop_firm.limits import PropFirmLimits
from backtest.prop_firm.presets import PROP_FIRM_PRESETS
from backtest.prop_firm.html_report import write_prop_firm_html
from backtest.prop_firm.compliance import (
    RANKING_SHEET_EXCLUDE_COLUMNS,
    _max_dd_pct_vs_initial_from_stats,
    build_prop_firm_ranking_sheet,
    ranking_sheet_name,
)
from backtest.grid.ranking import build_grid_ranking
from backtest.prop_firm.scaling import calculate_max_scale_factor


def _limits(**kw) -> PropFirmLimits:
    base = dict(
        name="Test",
        account_size_usd=100_000.0,
        max_risk_per_moment_pct=None,
        max_risk_single_position_pct=None,
        max_daily_dd_pct=5.0,
        max_overall_dd_pct=10.0,
        daily_dd_basis="static_initial",
        profit_target_pct=None,
        min_trading_days=None,
    )
    base.update(kw)
    return PropFirmLimits(**base)


def _single_open_trade(peak_risk_usd: float, contract_size: float = 1.0) -> pd.DataFrame:
    lot = peak_risk_usd / contract_size
    return pd.DataFrame(
        [{
            "entry_time": "2024-01-01 10:00:00",
            "close_time": "2024-01-01 18:00:00",
            "entry_price": 100.0,
            "sl": 99.0,
            "lot": lot,
            "pnl_usd": 100.0,
        }]
    )


def test_ranking_sheet_name_and_hidden_columns():
    assert ranking_sheet_name("FTMO") == "Ranking_FTMO"
    df_rep = pd.DataFrame(
        {
            "combo_no": [1],
            "bot_name": ["a"],
            "net_pnl_usd": [100.0],
            "profit_factor": [1.5],
            "wave_min_pct": [0.28],
            "max_dd_%_vs_initial": [-5.0],
            "max_pos_open": [4],
            "trades": [99],
            "rrr": [2.5],
            "tp_mode": ["wave_target_n"],
            "tp_target_wave_index": [6],
            "prop_firm_pass_count": [1],
            "prop_firm_best_match": ["FTMO"],
        }
    )
    from backtest.prop_firm.compliance import _attach_max_open_positions_to_ranking

    base = _attach_max_open_positions_to_ranking(build_grid_ranking(df_rep), df_rep)
    long = pd.DataFrame(
        {
            "prop_firm_name": ["FTMO"],
            "bot_name": ["a"],
            "backtest_risk_usd": [500.0],
            "headroom_scale": [1.2],
            "max_risk_per_trade_usd": [600.0],
            "projected_net_pnl_at_max_risk_usd": [120.0],
            "original_net_pnl_usd": [100.0],
            "scale_factor": [1.0],
            "scaled_net_pnl_usd": [100.0],
            "scaled_net_pnl_acc_pct": [0.1],
            "scale_for_overall_dd": [2.0],
            "worst_day_loss_pct": [-3.5],
            "risk_change_usd": [100.0],
            "headroom_binding": ["none"],
        }
    )
    out = build_prop_firm_ranking_sheet(base, long, "FTMO")
    assert "max_otevrenych_pozic" in out.columns
    assert out["max_otevrenych_pozic"].iloc[0] == 4
    assert "celkovy_pocet_otevrenych_obchodu" in out.columns
    assert out["celkovy_pocet_otevrenych_obchodu"].iloc[0] == 99
    assert "max_ddd_%" in out.columns
    assert out["max_ddd_%"].iloc[0] == pytest.approx(3.5)
    ix_rrr = list(out.columns).index("RRR_TP")
    ix_max_risk = list(out.columns).index("max_risk_per_trade_usd")
    assert ix_rrr == ix_max_risk - 1
    assert out["RRR_TP"].iloc[0] == "WAVE N=6"
    ix_pf = list(out.columns).index("profit_factor")
    ix_wmp = list(out.columns).index("wave_min_pct")
    assert ix_wmp == ix_pf + 1
    assert out["wave_min_pct"].iloc[0] == pytest.approx(0.28)
    assert "scale_factor" not in out.columns
    assert "net_pnl_usd" not in out.columns
    assert not (RANKING_SHEET_EXCLUDE_COLUMNS & set(out.columns))


def test_write_prop_firm_html_accepts_current_long_schema(tmp_path):
    df_long = pd.DataFrame(
        {
            "prop_firm_name": ["FTMO"],
            "bot_name": ["bot_a"],
            "scale_factor": [0.8],
            "scaled_net_pnl_acc_pct": [12.34],
            "scaled_max_dd_pct_vs_initial": [-4.56],
            "binding_constraint": ["overall_dd"],
            "challenge_passed": [True],
            "peak_risk_pct": [1.2],
            "original_net_pnl_usd": [1234.0],
        }
    )
    out_path = tmp_path / "prop_firm_compliance.html"

    write_prop_firm_html(df_long, out_path, ["FTMO"])

    html = out_path.read_text(encoding="utf-8")
    assert out_path.is_file()
    assert "bot_a" in html
    assert "12.34" in html
    assert "overall_dd" in html


def test_max_dd_from_stats_uses_vs_initial_not_vs_peak():
    stats = {
        "max_drawdown_pct": -8.0,
        "max_drawdown_pct_vs_peak": -25.0,
    }
    assert _max_dd_pct_vs_initial_from_stats(stats) == pytest.approx(-8.0)


def test_presets_ftmo_fxify_fintokei():
    ftmo = PROP_FIRM_PRESETS["FTMO"]
    assert ftmo.max_risk_per_moment_pct is None
    assert ftmo.max_risk_single_position_pct == 1.0
    assert ftmo.max_daily_dd_pct == 5.0
    assert ftmo.max_overall_dd_pct == 10.0

    fxify = PROP_FIRM_PRESETS["FXIFY"]
    assert fxify.max_risk_per_moment_pct is None
    assert fxify.max_risk_single_position_pct is None
    assert fxify.max_daily_dd_pct == 4.0
    assert fxify.max_overall_dd_pct == 8.0

    fintokei = PROP_FIRM_PRESETS["FINTOKEI"]
    assert fintokei.max_risk_per_moment_pct == 3.0
    assert fintokei.max_risk_single_position_pct is None


def test_ftmo_single_position_1pct_ok():
    lim = _limits(max_risk_single_position_pct=1.0)
    df = _single_open_trade(1000.0)  # 1 % účtu
    r = calculate_max_scale_factor(
        df, lim, contract_size=1.0,
        original_net_pnl_usd=5000.0,
        original_max_dd_pct_vs_initial=-2.0,
        original_risk_usd=500.0,
    )
    assert r["max_risk_per_position_pct"] == pytest.approx(1.0, rel=1e-4)
    assert r["final_scale_factor"] == pytest.approx(1.0, rel=1e-4)
    assert r["binding_constraint"] == "none"


def test_max_risk_and_projected_pnl_headroom():
    """headroom_scale < 1 → max_risk = risk * headroom; PnL škáluje stejně."""
    lim = _limits(max_risk_single_position_pct=1.0, max_overall_dd_pct=10.0)
    df = _single_open_trade(2000.0)  # 2 % na pozici
    r = calculate_max_scale_factor(
        df, lim, contract_size=1.0,
        original_net_pnl_usd=10_000.0,
        original_max_dd_pct_vs_initial=-20.0,
        original_risk_usd=500.0,
        peak_overall_dd_pct=20.0,
    )
    assert r["max_risk_per_trade_usd"] == pytest.approx(500.0 * r["headroom_scale"], rel=1e-3)
    assert r["projected_net_pnl_at_max_risk_usd"] == pytest.approx(
        10_000.0 * r["headroom_scale"], rel=1e-3
    )
    assert r["scaled_risk_per_trade_usd"] == pytest.approx(500.0 * r["final_scale_factor"], rel=1e-3)


def test_ftmo_single_position_1pct_scale_half():
    lim = _limits(max_risk_single_position_pct=1.0)
    df = _single_open_trade(2000.0)  # 2 % → scale 0.5
    r = calculate_max_scale_factor(
        df, lim, contract_size=1.0,
        original_net_pnl_usd=5000.0,
        original_max_dd_pct_vs_initial=-2.0,
        original_risk_usd=500.0,
    )
    assert r["final_scale_factor"] == pytest.approx(0.5, rel=1e-4)
    assert r["binding_constraint"] == "single_position_risk"


def test_fintokei_moment_risk_3pct_scale_half():
    lim = _limits(max_risk_per_moment_pct=3.0)
    df = _single_open_trade(6000.0)  # 6 % součet → scale 0.5
    r = calculate_max_scale_factor(
        df, lim, contract_size=1.0,
        original_net_pnl_usd=5000.0,
        original_max_dd_pct_vs_initial=-2.0,
        original_risk_usd=600.0,
    )
    assert r["final_scale_factor"] == pytest.approx(0.5, rel=1e-4)
    assert r["binding_constraint"] == "peak_risk"


def test_overall_dd_scale():
    lim = _limits()
    r = calculate_max_scale_factor(
        pd.DataFrame(),
        lim,
        contract_size=1.0,
        original_net_pnl_usd=8000.0,
        original_max_dd_pct_vs_initial=-12.0,
        original_risk_usd=100.0,
        peak_overall_dd_pct=12.0,
    )
    assert r["final_scale_factor"] == pytest.approx(10.0 / 12.0, rel=1e-4)
    assert r["binding_constraint"] == "overall_dd"


def test_daily_dd_scale():
    lim = _limits(max_daily_dd_pct=5.0)
    df = pd.DataFrame([
        {"entry_time": "2024-01-01", "close_time": "2024-01-01 12:00", "entry_price": 1.0, "sl": 0.9, "lot": 0.01, "pnl_usd": -7000.0},
        {"entry_time": "2024-01-02", "close_time": "2024-01-02 12:00", "entry_price": 1.0, "sl": 0.9, "lot": 0.01, "pnl_usd": 100.0},
    ])
    r = calculate_max_scale_factor(
        df, lim, contract_size=1.0,
        original_net_pnl_usd=-6900.0,
        original_max_dd_pct_vs_initial=-5.0,
        original_risk_usd=100.0,
        peak_overall_dd_pct=5.0,
    )
    assert r["worst_day_loss_pct"] == pytest.approx(-7.0, rel=1e-3)
    assert r["final_scale_factor"] == pytest.approx(5.0 / 7.0, rel=1e-3)


def test_fxify_only_dd_limits():
    lim = PROP_FIRM_PRESETS["FXIFY"]
    df = _single_open_trade(50_000.0)  # vysoký risk — FXIFY nemá risk limit
    r = calculate_max_scale_factor(
        df, lim, contract_size=1.0,
        original_net_pnl_usd=5000.0,
        original_max_dd_pct_vs_initial=-4.0,
        original_risk_usd=500.0,
    )
    assert r["scale_for_peak_risk"] == 1.0
    assert r["scale_for_single_position_risk"] == 1.0


def test_negative_pnl_challenge_not_passed():
    lim = _limits(profit_target_pct=5.0)
    df = _single_open_trade(500.0)
    r = calculate_max_scale_factor(
        df, lim, contract_size=1.0,
        original_net_pnl_usd=-1000.0,
        original_max_dd_pct_vs_initial=-3.0,
        original_risk_usd=300.0,
    )
    assert r["challenge_passed"] is False


def test_headroom_scale_can_exceed_one():
    """Nízké DD v backtestu → scale_for_overall_dd a headroom > 1, max_risk > backtest risk."""
    lim = _limits(max_risk_single_position_pct=None, max_overall_dd_pct=10.0)
    df = _single_open_trade(500.0)  # 0.5 % účtu
    r = calculate_max_scale_factor(
        df, lim, contract_size=1.0,
        original_net_pnl_usd=20_000.0,
        original_max_dd_pct_vs_initial=-2.0,
        original_risk_usd=500.0,
        peak_overall_dd_pct=2.0,
    )
    assert r["scale_for_overall_dd"] == pytest.approx(5.0, rel=1e-3)
    assert r["headroom_scale"] == pytest.approx(5.0, rel=1e-3)
    assert r["max_risk_per_trade_usd"] == pytest.approx(2500.0, rel=1e-3)
    assert r["projected_net_pnl_at_max_risk_usd"] == pytest.approx(100_000.0, rel=1e-3)
    assert r["final_scale_factor"] == pytest.approx(1.0, rel=1e-4)


def test_deterministic():
    lim = _limits(max_risk_per_moment_pct=3.0)
    df = _single_open_trade(4500.0)
    kw = dict(
        contract_size=1.0,
        original_net_pnl_usd=2000.0,
        original_max_dd_pct_vs_initial=-8.0,
        original_risk_usd=500.0,
    )
    a = calculate_max_scale_factor(df, lim, **kw)
    b = calculate_max_scale_factor(df, lim, **kw)
    assert a == b
