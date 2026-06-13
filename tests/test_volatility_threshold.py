import pytest
import pandas as pd
from typing import List, Dict

from config.bot_config import BotConfig
from strategy.ext_range import reapply_ext_range_tags
from strategy.wave_sequence import compute_wave_sequence_info_per_wave

def _make_wave(wt: str, move_pct: float, d: int) -> dict:
    # Pomocná funkce pro vytvoření vlny
    return {
        "wave_time": wt,
        "dir": d,
        "move_pct": move_pct,
        "fib50": 1.1000,
        "sl": 1.0950 if d == 1 else 1.1050,
        "tp": 1.1100 if d == 1 else 1.0900,
        "box_top": 1.1000 if d == -1 else 1.1100,
        "box_bottom": 1.0900 if d == 1 else 1.1000,
        "draw_left": 0,
        "draw_right": 1,
    }

def _make_cfg(**kwargs) -> BotConfig:
    cfg = BotConfig(
        symbol="EURUSD.x",
        ext_enabled=True,
        ext_trade_both_sides_in_range=True,
        wave_min_pct=0.26,
        ext_wave_min_pct=0.76,
    )
    for k, v in kwargs.items():
        setattr(cfg, k, v)
    return cfg

def test_volatility_threshold_disabled():
    cfg = _make_cfg(wave_min_pct_enable=False)
    
    # 1. EXT vlna
    w_ext = _make_wave("ext", 0.80, 1)
    w_ext["is_ext"] = True
    
    # 2. Mini vlna v EXT okne
    w_mini = _make_wave("mini", 0.15, -1)
    
    waves = [w_ext, w_mini]
    birth = {w["wave_time"]: 1 for w in waves}
    
    df = pd.DataFrame({
        "time": pd.date_range("2026-05-30", periods=5, freq="30min"),
        "open": [1.0, 1.0, 1.0, 1.0, 1.0],
        "high": [1.1, 1.1, 1.1, 1.1, 1.1],
        "low": [0.9, 0.9, 0.9, 0.9, 0.9],
        "close": [1.0, 1.0, 1.0, 1.0, 1.0],
    })
    
    reapply_ext_range_tags(waves, cfg, df, birth)
    
    assert w_mini.get("counted_via_volatility_threshold", False) is False

def test_volatility_threshold_enabled():
    cfg = _make_cfg(
        wave_min_pct_enable=True,
        ext_post_both_sides_wave_min_pct=0.13,
    )
    
    w_ext = _make_wave("ext", 0.80, 1)
    w_ext["is_ext"] = True
    
    w_mini = _make_wave("mini", 0.15, -1)
    
    waves = [w_ext, w_mini]
    birth = {w["wave_time"]: 1 for w in waves}
    
    df = pd.DataFrame({"time": pd.date_range("2026-05-30", periods=5, freq="30min"), "close": [1.0]*5})
    
    reapply_ext_range_tags(waves, cfg, df, birth)
    
    assert w_mini.get("counted_via_volatility_threshold", False) is True

def test_volatility_threshold_outside_ext_window():
    cfg = _make_cfg(
        wave_min_pct_enable=True,
        ext_post_both_sides_wave_min_pct=0.13,
    )
    
    # Neni EXT okno, jen normalni vlna (nebo po skonceni)
    w_normal = _make_wave("norm", 0.30, 1)
    
    w_mini = _make_wave("mini", 0.15, -1)
    
    waves = [w_normal, w_mini]
    birth = {w["wave_time"]: 1 for w in waves}
    df = pd.DataFrame({"time": pd.date_range("2026-05-30", periods=5, freq="30min"), "close": [1.0]*5})
    
    reapply_ext_range_tags(waves, cfg, df, birth)
    
    assert w_mini.get("counted_via_volatility_threshold", False) is False

def test_volatility_threshold_default_sl():
    cfg = _make_cfg(
        wave_min_pct_enable=True,
        ext_post_both_sides_wave_min_pct=0.13,
        ext_post_both_sides_default_sl_pct=0.20,
    )
    
    from infra.orders import send_order
    
    # Simulace signalu, ktery byl counted via volatility threshold
    signal = _make_wave("mini", 0.15, 1)
    signal["counted_via_volatility_threshold"] = True
    # Původní SL dist (1.1000 - 1.0989 = 0.0011 => 0.1%)
    signal["fib50"] = 1.1000
    signal["sl"] = 1.0989
    
    # Simulujeme tick
    import unittest.mock as mock
    tick_mock = mock.Mock()
    tick_mock.ask = 1.1010  # cena je nad fib50, takze se zkusit LIMIT_PRIMARY
    tick_mock.bid = 1.1000
    
    info_mock = mock.Mock()
    info_mock.point = 0.00001
    info_mock.trade_stops_level = 0
    
    with mock.patch("infra.orders.mt5.symbol_info_tick", return_value=tick_mock), \
         mock.patch("infra.orders.mt5.symbol_info", return_value=info_mock), \
         mock.patch("infra.orders._place_limit_primary") as mock_place_limit:
         
        send_order(signal, cfg, entry_mode="market_fallback")
        
        # Volal se _place_limit_primary
        assert mock_place_limit.called
        
        args, kwargs = mock_place_limit.call_args
        # kwargs["sl"] musel byt prepocitan na min 0.20% z 1.1000, coz je 1.1000 - 0.0022 = 1.0978
        assert abs(kwargs["sl"] - 1.0978) < 1e-5
