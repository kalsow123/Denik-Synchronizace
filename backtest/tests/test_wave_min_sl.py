from __future__ import annotations

from backtest.grid.translator import grid_dict_to_bot_config
from config.bot_config import BotConfig
from strategy.wave_detection_pine import _append_wave_sig


def _cfg(**kwargs) -> BotConfig:
    base = dict(
        wave_min_pct=0.26,
        min_opp_bars=3,
        entry_fib_level=0.5,
        sl_fib_level=0.8,
        wave_min_sl=0.12,
        rrr=2.0,
    )
    base.update(kwargs)
    return BotConfig(**base)


def test_wave_buy_sl_respects_min_pct_floor():
    cfg = _cfg()
    sig = _append_wave_sig(
        cfg,
        w_dir=1,
        pivot_level=100.00,
        cand_level=100.05,
        box_top=100.05,
        box_bottom=100.00,
        pivot_bar_idx=0,
        cand_bar_idx=5,
        wave_time_str="BUY_MIN_SL",
    )

    assert sig is not None
    expected_entry = 100.025
    expected_sl = expected_entry * (1 - 0.12 / 100.0)
    assert abs(float(sig["fib50"]) - expected_entry) < 1e-9
    assert abs(float(sig["sl"]) - expected_sl) < 1e-9


def test_wave_sell_sl_respects_min_pct_floor():
    cfg = _cfg()
    sig = _append_wave_sig(
        cfg,
        w_dir=-1,
        pivot_level=100.00,
        cand_level=99.95,
        box_top=100.00,
        box_bottom=99.95,
        pivot_bar_idx=0,
        cand_bar_idx=5,
        wave_time_str="SELL_MIN_SL",
    )

    assert sig is not None
    expected_entry = 99.975
    expected_sl = expected_entry * (1 + 0.12 / 100.0)
    assert abs(float(sig["fib50"]) - expected_entry) < 1e-9
    assert abs(float(sig["sl"]) - expected_sl) < 1e-9


def test_wave_sl_keeps_fib_geometry_when_already_wider_than_min():
    cfg = _cfg()
    sig = _append_wave_sig(
        cfg,
        w_dir=1,
        pivot_level=100.00,
        cand_level=101.00,
        box_top=101.00,
        box_bottom=100.00,
        pivot_bar_idx=0,
        cand_bar_idx=5,
        wave_time_str="BUY_FIB_SL_OK",
    )

    assert sig is not None
    assert abs(float(sig["fib50"]) - 100.5) < 1e-9
    assert abs(float(sig["sl"]) - 100.2) < 1e-9


def test_grid_translator_accepts_wave_min_sl_aliases():
    base = {
        "timeframe": "M30",
        "wave_min_pct": 0.26,
        "min_opp_bars": 3,
        "rrr": 2.0,
        "fib_level": 0.5,
    }

    cfg_direct = grid_dict_to_bot_config({**base, "wave_min_sl": 0.19})
    cfg_pct = grid_dict_to_bot_config({**base, "wave_min_sl_pct": 0.18})
    cfg_symbol = grid_dict_to_bot_config({**base, "wave_min_sl_%": 0.17})

    assert float(cfg_direct.wave_min_sl) == 0.19
    assert float(cfg_pct.wave_min_sl) == 0.18
    assert float(cfg_symbol.wave_min_sl) == 0.17
