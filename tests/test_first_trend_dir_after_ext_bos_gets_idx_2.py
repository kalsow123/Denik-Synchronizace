"""Test: První trend-dir vlna po EXT BOS musí dostat idx 2."""
import pandas as pd
from strategy.wave_sequence import compute_wave_sequence_info_per_wave
from config.bot_config import BotConfig


def _make_df(bars):
    """bars = list of (open, high, low, close) tuples."""
    return pd.DataFrame(bars, columns=["open", "high", "low", "close"])


def _make_wave(wave_time, dir_, draw_left, draw_right, box_top, box_bottom, **kwargs):
    return {
        "wave_time": wave_time,
        "dir": dir_,
        "draw_left": draw_left,
        "draw_right": draw_right,
        "box_top": box_top,
        "box_bottom": box_bottom,
        "is_ext": kwargs.get("is_ext", False),
        "hh_hl_pass": kwargs.get("hh_hl_pass", True),
        "post_ext_trend_suppressed": kwargs.get("post_ext_trend_suppressed", False),
        "is_wf": kwargs.get("is_wf", False),
        "is_two_sided_counter": kwargs.get("is_two_sided_counter", False),
    }


def test_first_up_after_ext_up_bos_gets_idx_2():
    """Bear trend, EXT UP proráží bear swing (Scénář A) → bull flip, idx 1.
    Další UP nepřekoná EXT high → musí dostat idx 2."""

    bars = [
        (1.20, 1.20, 1.10, 1.10),  # 0
        (1.10, 1.12, 1.08, 1.09),  # 1
        (1.09, 1.10, 1.05, 1.06),  # 2
        (1.06, 1.07, 1.03, 1.04),  # 3 (DOWN extrem)
        (1.04, 1.05, 1.03, 1.05),  # 4
        (1.05, 1.06, 1.04, 1.04),  # 5
        (1.04, 1.05, 1.01, 1.02),  # 6 (DOWN extrem 2)
        (1.02, 1.03, 1.02, 1.03),  # 7
        (1.03, 1.05, 1.02, 1.04),  # 8
        (1.04, 1.10, 1.04, 1.09),  # 9
        (1.09, 1.20, 1.08, 1.18),  # 10 EXT UP extrem, close > DOWN1.box_top
        (1.18, 1.19, 1.16, 1.17),  # 11
        (1.17, 1.18, 1.15, 1.16),  # 12 UP další extrem (< EXT high)
    ]
    df = _make_df(bars)

    waves = [
        _make_wave("DOWN1", -1, draw_left=0, draw_right=3, box_top=1.20, box_bottom=1.03),
        _make_wave("DOWN2", -1, draw_left=4, draw_right=6, box_top=1.05, box_bottom=1.01),
        _make_wave("EXT_UP", 1, draw_left=7, draw_right=10, box_top=1.20, box_bottom=1.02, is_ext=True),
        _make_wave("UP2", 1, draw_left=11, draw_right=12, box_top=1.18, box_bottom=1.15),
    ]

    cfg = BotConfig()
    result = compute_wave_sequence_info_per_wave(df, waves, cfg)

    assert result["DOWN1"].index_in_trend == 1, f"DOWN1 expected idx=1, got {result['DOWN1'].index_in_trend}"
    assert result["DOWN2"].index_in_trend == 2, f"DOWN2 expected idx=2, got {result['DOWN2'].index_in_trend}"
    assert result["EXT_UP"].index_in_trend == 1, f"EXT_UP expected idx=1 (Scénář A), got {result['EXT_UP'].index_in_trend}"
    assert result["EXT_UP"].is_bos_wave is True, "EXT_UP must have is_bos_wave=True"
    assert result["UP2"].index_in_trend == 2, f"UP2 expected idx=2 (3.2.a), got {result['UP2'].index_in_trend}"


def test_first_down_after_ext_down_bos_gets_idx_2():
    """Bull trend, EXT DOWN proráží bull swing (Scénář A) → bear flip, idx 1.
    Další DOWN nepřekoná EXT low → musí dostat idx 2."""

    bars = [
        (1.10, 1.20, 1.10, 1.20),  # 0
        (1.20, 1.22, 1.18, 1.21),  # 1
        (1.21, 1.25, 1.20, 1.24),  # 2 (UP extrem)
        (1.24, 1.25, 1.22, 1.23),  # 3
        (1.23, 1.24, 1.22, 1.22),  # 4
        (1.22, 1.25, 1.22, 1.24),  # 5
        (1.24, 1.28, 1.23, 1.27),  # 6 (UP extrem 2)
        (1.27, 1.28, 1.26, 1.26),  # 7
        (1.26, 1.27, 1.24, 1.25),  # 8
        (1.25, 1.26, 1.20, 1.21),  # 9
        (1.21, 1.22, 1.10, 1.12),  # 10 EXT DOWN extrem, close < UP1.box_bottom (1.10)
        (1.12, 1.15, 1.11, 1.13),  # 11
        (1.13, 1.14, 1.11, 1.12),  # 12 DOWN další extrem (> EXT low)
    ]
    df = _make_df(bars)

    waves = [
        _make_wave("UP1", 1, draw_left=0, draw_right=2, box_top=1.25, box_bottom=1.10),
        _make_wave("UP2", 1, draw_left=4, draw_right=6, box_top=1.28, box_bottom=1.22),
        _make_wave("EXT_DOWN", -1, draw_left=7, draw_right=10, box_top=1.28, box_bottom=1.10, is_ext=True),
        _make_wave("DOWN2", -1, draw_left=11, draw_right=12, box_top=1.15, box_bottom=1.11),
    ]

    cfg = BotConfig()
    result = compute_wave_sequence_info_per_wave(df, waves, cfg)

    assert result["UP1"].index_in_trend == 1
    assert result["UP2"].index_in_trend == 2
    assert result["EXT_DOWN"].index_in_trend == 1, f"EXT_DOWN expected idx=1 (Scénář A), got {result['EXT_DOWN'].index_in_trend}"
    assert result["EXT_DOWN"].is_bos_wave is True, "EXT_DOWN must have is_bos_wave=True"
    assert result["DOWN2"].index_in_trend == 2, f"DOWN2 expected idx=2 (3.2.a), got {result['DOWN2'].index_in_trend}"
