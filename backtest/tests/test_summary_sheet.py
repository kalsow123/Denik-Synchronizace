"""List summaries v grid_report.xlsx."""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.grid.summary_sheet import build_grid_summaries_sheet, rr_tp_summary
from config.enums import TPMode


def test_rr_tp_bos_priority_hides_rrr_numeric():
    s = rr_tp_summary(2.5, TPMode.BOS_EXIT_PRIORITY, None)
    assert "bez klasick" in s
    assert "2.5" not in s


def test_rr_tp_fixed_shows_rrr():
    assert rr_tp_summary(2.5, TPMode.RRR_FIXED, 4) == "2.5"
    assert rr_tp_summary(2.5, "rrr_fixed", 4) == "2.5"


def test_rr_tp_wave_target_n_family_labels():
    assert rr_tp_summary(2.0, TPMode.WAVE_TARGET_N, 4) == "WAVE N=4"
    assert rr_tp_summary(2.0, "wave_target_n", 4) == "WAVE N=4"
    assert rr_tp_summary(2.0, TPMode.WAVE_TARGET_N_G, 4) == "WAVE N=4 G"
    assert rr_tp_summary(2.0, "wave_target_n_g", 4) == "WAVE N=4 G"


def test_wave_counter_two_sided_fallback_and_pd_na():
    df = pd.DataFrame(
        [
            {
                "combo_no": 1,
                "bot_name": "a",
                "timeframe": 30,
                "min_opp_bars": 2,
                "rrr": 2.0,
                "tp_mode": "rrr_fixed",
                "trades": 1,
                "fib_level": 0.5,
                "entry_mode": "market_fallback",
                "pending_cancel_mode": "trend",
                "tp_target_wave_index": None,
                "wave_counter_two_sided_enabled": pd.NA,
                "counter_position_enabled": True,
                "bos_entry_enable": False,
                "wave_position_enabled": True,
                "pp_enabled": False,
                "profit_factor": 1.0,
                "max_dd_%_vs_initial": -1.0,
            }
        ]
    )
    out = build_grid_summaries_sheet(df)
    assert out.loc[0, "wave_counter_two_sided_enabled"] == True


def test_build_summaries_sheet_order_and_ftmo_tail():
    df = pd.DataFrame(
        [
            {
                "combo_no": 1,
                "bot_name": "a",
                "timeframe": 15,
                "min_opp_bars": 2,
                "rrr": 2.0,
                "tp_mode": "bos_exit_priority",
                "trades": 12,
                "trades_wave": 7,
                "trades_wave_counter": 3,
                "fib_level": 0.55,
                "entry_mode": "market_fallback",
                "pending_cancel_mode": "trend",
                "tp_target_wave_index": None,
                "counter_position_enabled": False,
                "bos_entry_enable": True,
                "wave_position_enabled": False,
                "pp_enabled": True,
                "pp_sl_pct": 0.4,
                "profit_factor": 1.25,
                "wave_min_pct": 0.26,
                "max_dd_%_vs_initial": -3.5,
                "net_pnl_wave_usd": 10.0,
                "max_dd_%_vs_initial_wave": -1.2,
                "net_pnl_wave_counter_usd": 4.4,
                "max_dd_%_vs_initial_wave_counter": -0.6,
                "trades_wave_two_sided": 1,
                "net_pnl_wave_two_sided_usd": 3.5,
                "max_dd_%_vs_initial_wave_two_sided": -0.4,
                "trades_pp": 2,
                "net_pnl_pp_usd": 2.2,
                "max_dd_%_vs_initial_pp": -0.3,
                "trades_bos": 2,
                "net_pnl_bos_usd": -0.25,
                "max_dd_%_vs_initial_bos": -0.1,
                "FTMO__headroom_scale": 1.0,
                "FTMO__max_risk_per_trade_usd": 500.0,
                "FTMO__projected_net_pnl_at_max_risk_usd": 777.7,
                "FTMO__original_net_pnl_usd": 700.7,
            }
        ]
    )
    out = build_grid_summaries_sheet(df, preset_names=["FTMO", "FXIFY"])
    ix_entry = list(out.columns).index("entry_mode")
    ix_pcm = list(out.columns).index("pending_cancel_mode")
    ix_tw = list(out.columns).index("tp_target_wave_index")
    assert ix_entry < ix_pcm < ix_tw
    ix_trades = list(out.columns).index("trades")
    ix_pf = list(out.columns).index("profit_factor")
    ix_wmp = list(out.columns).index("wave_min_pct")
    ix_dd = list(out.columns).index("max_dd_%_vs_initial")
    ix_trades_wave = list(out.columns).index("trades_wave")
    ix_wave = list(out.columns).index("net_pnl_wave_usd")
    ix_trades_wave_counter = list(out.columns).index("trades_wave_counter")
    ix_wave_ts = list(out.columns).index("trades_wave_two_sided")
    assert ix_trades < ix_pf < ix_wmp < ix_dd < ix_trades_wave < ix_wave < ix_trades_wave_counter < ix_wave_ts
    assert float(out.iloc[0]["wave_min_pct"]) == pytest.approx(0.26)
    assert list(out.columns)[ix_trades - 1] == "RRR_TP"
    assert out.iloc[0]["pending_cancel_mode"] == "trend"
    assert float(out.iloc[0]["max_dd_%_vs_initial"]) == pytest.approx(-3.5)
    assert list(out.columns[-4:]) == [
        "headroom_scale",
        "max_risk_per_trade_usd",
        "projected_net_pnl_at_max_risk_usd",
        "original_net_pnl_usd",
    ]
    assert out.iloc[0]["RRR_TP"] == rr_tp_summary(2.0, "bos_exit_priority", None)
    ix_fib = list(out.columns).index("Fib_vstup")
    ix_rrr = list(out.columns).index("RRR_TP")
    assert ix_fib < ix_rrr < ix_trades
    assert abs(float(out.iloc[0]["original_net_pnl_usd"]) - 700.7) < 0.001
    assert int(out.iloc[0]["trades_wave"]) == 7
    assert int(out.iloc[0]["trades_wave_counter"]) == 3
    assert int(out.iloc[0]["trades_wave_two_sided"]) == 1
