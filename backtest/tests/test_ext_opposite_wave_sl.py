"""SL prvni opacne WAVE vlny po EXT — extrém EXT misto sl_fib_level."""
from __future__ import annotations

from config.bot_config import BotConfig
from strategy.ext_logic import (
    apply_first_opposite_wave_sl_after_ext,
    sl_at_ext_extreme_for_opposite_wave,
)


def _cfg(**kw) -> BotConfig:
    base = {
        "ext_enabled": True,
        "ext_wave_min_pct": 0.76,
    }
    base.update(kw)
    return BotConfig(**base)


def test_bear_ext_long_sl_at_ext_low():
    ext = {
        "dir": -1,
        "is_ext": True,
        "ext_high": 1.1700,
        "ext_low": 1.1500,
        "box_top": 1.1700,
        "box_bottom": 1.1500,
        "move_pct": 1.2,
    }
    up = {"dir": 1, "fib50": 1.1600, "sl": 1.1550, "move_pct": 0.3}
    sl = sl_at_ext_extreme_for_opposite_wave(up, ext)
    assert sl == 1.1500


def test_bull_ext_short_sl_at_ext_high():
    ext = {
        "dir": 1,
        "is_ext": True,
        "ext_high": 1.1800,
        "ext_low": 1.1600,
        "box_top": 1.1800,
        "box_bottom": 1.1600,
        "move_pct": 1.2,
    }
    down = {"dir": -1, "fib50": 1.1700, "sl": 1.1750, "move_pct": 0.3}
    sl = sl_at_ext_extreme_for_opposite_wave(down, ext)
    assert sl == 1.1800


def test_only_first_opposite_consumes_anchor():
    cfg = _cfg()
    ext = {
        "dir": -1,
        "is_ext": True,
        "ext_high": 1.17,
        "ext_low": 1.15,
        "box_top": 1.17,
        "box_bottom": 1.15,
        "move_pct": 1.0,
    }
    up1 = {"dir": 1, "fib50": 1.16, "sl": 1.155, "move_pct": 0.3}
    up2 = {"dir": 1, "fib50": 1.165, "sl": 1.158, "move_pct": 0.25}

    anchor = ext
    w1, anchor = apply_first_opposite_wave_sl_after_ext(up1, ext_anchor=anchor, cfg=cfg)
    assert w1["sl"] == 1.15
    assert anchor is None

    w2, anchor = apply_first_opposite_wave_sl_after_ext(up2, ext_anchor=anchor, cfg=cfg)
    assert w2["sl"] == 1.158
    assert anchor is None


def test_same_dir_wave_after_ext_keeps_anchor():
    cfg = _cfg()
    ext = {"dir": -1, "is_ext": True, "ext_high": 1.17, "ext_low": 1.15,
           "box_top": 1.17, "box_bottom": 1.15, "move_pct": 1.0}
    down2 = {"dir": -1, "fib50": 1.14, "sl": 1.16, "move_pct": 0.2}
    up = {"dir": 1, "fib50": 1.16, "sl": 1.155, "move_pct": 0.3}

    anchor = ext
    down2_out, anchor = apply_first_opposite_wave_sl_after_ext(
        down2, ext_anchor=anchor, cfg=cfg,
    )
    assert down2_out["sl"] == 1.16
    assert anchor is ext

    up_out, anchor = apply_first_opposite_wave_sl_after_ext(up, ext_anchor=anchor, cfg=cfg)
    assert up_out["sl"] == 1.15
    assert anchor is None
