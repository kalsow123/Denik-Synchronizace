from __future__ import annotations

from backtest.grid.translator import grid_dict_to_bot_config
from strategy.ext_logic import compute_counter_signal, compute_ext_counter_sl_price
from strategy.wave_sequence import (
    compute_ladder_sl_from_wave_size,
    compute_sl_pct_from_entry_and_sl,
    compute_sl_pct_from_wave_size_ladder,
    compute_sl_price_from_pct,
    wave_counter_min_sl_pct,
)


def _cfg():
    return grid_dict_to_bot_config(
        {
            "timeframe": "M30",
            "wave_min_pct": 0.26,
            "min_opp_bars": 3,
            "rrr": 2.0,
            "fib_level": 0.5,
            "entry_mode": "market_fallback",
            "symbol": "EURUSD",
            "sl_fib_level": 0.8,
            "wave_plus": True,
            "wave_size_sl_ladder_base_pct": 0.21,
            "wave_size_sl_ladder_step_pct": 0.16,
            "wave_size_sl_ladder_band_size_pct": 0.50,
            "ext_counter_sl_pct": 0.21,
            "ext_counter_min_sl_enabled": True,
            "ext_counter_min_sl_pct": 0.16,
        }
    )


def test_ext_counter_time_and_bos_share_same_sl_model():
    cfg = _cfg()
    wave = {
        "is_ext": True,
        "dir": 1,
        "box_top": 1.1100,
        "box_bottom": 1.1000,
        "wave_time": "202603201000",
    }
    mp = 1.1050
    sig_time = compute_counter_signal(wave, cfg, source="time", market_price=mp)
    sig_bos = compute_counter_signal(wave, cfg, source="bos", market_price=mp)
    expected = compute_ext_counter_sl_price(
        wave, market_price=mp, counter_dir=-1, cfg=cfg
    )

    assert sig_time is not None
    assert sig_bos is not None
    assert expected is not None
    assert float(sig_time["sl"]) == float(sig_bos["sl"]) == float(expected)


def test_ext_counter_min_sl_floor_016_pct():
    cfg = _cfg()
    wave = {
        "is_ext": True,
        "dir": 1,
        "ext_high": 1.10505,
        "ext_low": 1.1000,
        "box_top": 1.1100,
        "box_bottom": 1.1000,
        "wave_time": "202603201000",
    }
    mp = 1.1050  # SHORT counter — ext_high prilis blizko entry
    sl = compute_ext_counter_sl_price(
        wave, market_price=mp, counter_dir=-1, cfg=cfg
    )
    assert sl is not None
    expected = compute_sl_price_from_pct(mp, 0.16, is_buy=False)
    assert round(float(sl), 5) == round(float(expected), 5)


def test_ext_counter_min_sl_disabled_uses_extreme_only():
    cfg = _cfg()
    cfg.ext_counter_min_sl_enabled = False
    wave = {
        "is_ext": True,
        "dir": 1,
        "ext_high": 1.10505,
        "ext_low": 1.1000,
        "box_top": 1.1100,
        "box_bottom": 1.1000,
        "wave_time": "202603201000",
    }
    mp = 1.1050
    sl = compute_ext_counter_sl_price(
        wave, market_price=mp, counter_dir=-1, cfg=cfg
    )
    assert sl is not None
    assert round(float(sl), 5) == round(1.10505, 5)


def test_wave_counter_and_bos_reentry_share_same_ladder_sl_model():
    cfg = _cfg()
    entry = 1.1050
    wave_size_pct = 0.68

    sl_pct, sl_price = compute_ladder_sl_from_wave_size(
        entry, wave_size_pct, cfg, is_buy=True
    )

    assert sl_pct == compute_sl_pct_from_wave_size_ladder(wave_size_pct, cfg)
    assert sl_price == compute_sl_price_from_pct(entry, sl_pct, is_buy=True)


def test_wave_counter_sl_respects_min_016_pct_floor():
    cfg = _cfg()
    cfg.wave_size_sl_ladder_base_pct = 0.10
    cfg.wave_size_sl_ladder_step_pct = 0.00
    entry = 1.1050

    sl_pct, sl_price = compute_ladder_sl_from_wave_size(
        entry,
        0.20,
        cfg,
        is_buy=True,
        min_sl_pct=wave_counter_min_sl_pct(cfg),
    )

    assert sl_pct == 0.16
    assert sl_price == compute_sl_price_from_pct(entry, 0.16, is_buy=True)


def test_wave_counter_min_sl_is_paired_with_ext_secondary_setting():
    cfg = _cfg()
    cfg.ext_min_sl_move_pct = 0.19

    assert wave_counter_min_sl_pct(cfg) == 0.19


def test_counter_gap_fill_keeps_effective_sl_pct_from_planned_limit():
    planned_entry = 1.15990
    planned_sl = 1.16233579
    actual_entry = 1.16186

    sl_pct = compute_sl_pct_from_entry_and_sl(planned_entry, planned_sl)
    adjusted_sl = compute_sl_price_from_pct(actual_entry, sl_pct, is_buy=False)

    assert round(sl_pct, 2) == 0.21
    assert round(adjusted_sl, 5) == 1.16430
