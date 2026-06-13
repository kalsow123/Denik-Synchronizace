"""Verifikační testy architektonického refaktoru ARCH-FIX."""
from strategy.wave_sequence import compute_wave_sequence_info_per_wave, compute_wave_2_no_tp_protected_waves
from config.bot_config import BotConfig
import pandas as pd


def _make_test_data():
    """Mini scénář: bull trend s 3 UP vlnami + EXT DOWN BOS + DOWN trend."""
    bars = [
        (1.10, 1.20, 1.10, 1.20),  # 0
        (1.20, 1.22, 1.18, 1.21),  # 1
        (1.21, 1.25, 1.20, 1.24),  # 2 (UP1 extrem)
        (1.24, 1.25, 1.22, 1.23),  # 3
        (1.23, 1.24, 1.22, 1.22),  # 4
        (1.22, 1.25, 1.22, 1.24),  # 5
        (1.24, 1.28, 1.23, 1.27),  # 6 (UP2 extrem)
        (1.27, 1.28, 1.26, 1.26),  # 7
        (1.26, 1.27, 1.24, 1.25),  # 8
        (1.25, 1.26, 1.20, 1.21),  # 9
        (1.21, 1.22, 1.10, 1.12),  # 10 EXT DOWN extrem, close < UP1.box_bottom (1.10)
        (1.12, 1.15, 1.11, 1.13),  # 11
        (1.13, 1.14, 1.11, 1.12),  # 12 DOWN2 extrem (> EXT low)
    ]
    df = pd.DataFrame(bars, columns=["open", "high", "low", "close"])
    df["time"] = pd.date_range("2026-03-01 00:00", periods=len(df), freq="30min")
    
    waves = [
        {
            "wave_time": "UP1",
            "dir": 1,
            "draw_left": 0,
            "draw_right": 2,
            "box_top": 1.25,
            "box_bottom": 1.10,
        },
        {
            "wave_time": "UP2",
            "dir": 1,
            "draw_left": 4,
            "draw_right": 6,
            "box_top": 1.28,
            "box_bottom": 1.22,
        },
        {
            "wave_time": "EXT_DOWN",
            "dir": -1,
            "draw_left": 7,
            "draw_right": 10,
            "box_top": 1.28,
            "box_bottom": 1.10,
            "is_ext": True,
        },
        {
            "wave_time": "DOWN2",
            "dir": -1,
            "draw_left": 11,
            "draw_right": 12,
            "box_top": 1.15,
            "box_bottom": 1.11,
        },
    ]
    
    cfg = BotConfig(
        trend_filter_enabled=True,
        trend_hh_hl_filter_enabled=True,
        ext_enabled=True,
        ext_post_confirmed_trend_lock_enabled=True,
        ext_post_confirmed_trend_count=2,
    )
    
    return df, waves, cfg


def test_seq_info_is_idempotent():
    """compute_wave_sequence_info_per_wave musí být idempotentní."""
    df, waves, cfg = _make_test_data()
    
    result1 = compute_wave_sequence_info_per_wave(df, waves, cfg)
    waves_snapshot = [dict(w) for w in waves]  # deep copy klíčových polí
    
    result2 = compute_wave_sequence_info_per_wave(df, waves, cfg)
    
    # Výsledky musí být identické
    assert set(result1.keys()) == set(result2.keys())
    for wt in result1:
        assert result1[wt].index_in_trend == result2[wt].index_in_trend, f"idx diff for {wt}"
        assert result1[wt].prev_same_dir_in_trend_wave_time == result2[wt].prev_same_dir_in_trend_wave_time


def test_protected_waves_with_same_inputs_is_deterministic():
    """compute_wave_2_no_tp_protected_waves musí dát stejný výsledek pro stejné vstupy."""
    df, waves, cfg = _make_test_data()
    cfg.wave_2_no_tp_enable = True
    
    seq = compute_wave_sequence_info_per_wave(df, waves, cfg)
    
    set1 = compute_wave_2_no_tp_protected_waves(waves, seq, cfg)
    set2 = compute_wave_2_no_tp_protected_waves(waves, seq, cfg)
    
    assert set1 == set2


def test_reapply_ext_range_tags_idempotent():
    """reapply_ext_range_tags musí být idempotentní (volat 2× nesmí změnit stav)."""
    from strategy.ext_range import reapply_ext_range_tags
    df, waves, cfg = _make_test_data()
    wave_birth = {str(w["wave_time"]): w.get("draw_right", 0) for w in waves}
    
    reapply_ext_range_tags(waves, cfg, df=df, wave_birth=wave_birth)
    flags_after_1st = {(str(w["wave_time"])): {
        "hh_hl_pass": w.get("hh_hl_pass"),
        "post_ext_trend_suppressed": w.get("post_ext_trend_suppressed"),
        "post_ext_confirmed_trend_lock": w.get("post_ext_confirmed_trend_lock"),
    } for w in waves}
    
    reapply_ext_range_tags(waves, cfg, df=df, wave_birth=wave_birth)
    flags_after_2nd = {(str(w["wave_time"])): {
        "hh_hl_pass": w.get("hh_hl_pass"),
        "post_ext_trend_suppressed": w.get("post_ext_trend_suppressed"),
        "post_ext_confirmed_trend_lock": w.get("post_ext_confirmed_trend_lock"),
    } for w in waves}
    
    assert flags_after_1st == flags_after_2nd, "reapply_ext_range_tags is not idempotent"
