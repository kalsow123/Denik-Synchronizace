import pandas as pd
from typing import List, Dict

from config.bot_config import BotConfig
from strategy.wave_sequence import compute_wave_sequence_info_per_wave, compute_wave_2_no_tp_protected_waves


def _cfg(enabled: bool = True):
    cfg = BotConfig(
        symbol="EURUSD",
        timeframe=30,
        trend_hh_hl_filter_enabled=True,
    )
    cfg.wave_2_no_tp_enable = enabled
    cfg.wave_2_no_tp_max_index = 2
    return cfg


def _w(time, dir_val, is_ext=False, index_in_trend=None, prev_same_dir=None, **kwargs):
    w = {
        "wave_time": time,
        "dir": dir_val,
    }
    if is_ext:
        w["is_ext"] = True
    for k, v in kwargs.items():
        w[k] = v
    return w


def test_wave_2_no_tp_blocks_bos_exit():
    # Simulace 2 vln v trendu
    df = pd.DataFrame()
    waves = [
        _w("W1", 1),
        _w("W2", 1),
    ]
    # Namisto vytvareni manualni mapy nechame wave_sequence to spocitat.
    # Udelame dummy dataframe, aby wave_sequence proslo bez padu.
    df = pd.DataFrame({"time": ["T0", "T1", "T2"], "close": [1.15, 1.20, 1.10]})
    waves = [
        {"wave_time": "W1", "dir": 1, "draw_right": 1, "box_top": 1.15, "box_bottom": 1.10},
        {"wave_time": "W2", "dir": 1, "draw_right": 2, "box_top": 1.20, "box_bottom": 1.15},
    ]
    cfg = _cfg()
    seq_info = compute_wave_sequence_info_per_wave(df, waves, cfg)
    
    protected = compute_wave_2_no_tp_protected_waves(waves, seq_info, cfg)
    assert "W1" in protected
    assert "W2" in protected


def test_wave_2_no_tp_falls_at_idx_3():
    df = pd.DataFrame({"time": ["T0", "T1", "T2", "T3"], "close": [1.15, 1.20, 1.25, 1.10]})
    waves = [
        {"wave_time": "W1", "dir": 1, "draw_right": 1, "box_top": 1.15, "box_bottom": 1.10},
        {"wave_time": "W2", "dir": 1, "draw_right": 2, "box_top": 1.20, "box_bottom": 1.15},
        {"wave_time": "W3", "dir": 1, "draw_right": 3, "box_top": 1.25, "box_bottom": 1.20},
    ]
    cfg = _cfg()
    seq_info = compute_wave_sequence_info_per_wave(df, waves, cfg)
    
    protected = compute_wave_2_no_tp_protected_waves(waves, seq_info, cfg)
    # Trend ma 3 vlny, ochrana pada pro vsechny.
    assert "W1" not in protected
    assert "W2" not in protected
    assert "W3" not in protected


def test_wave_2_no_tp_disabled():
    df = pd.DataFrame({"time": ["T0", "T1", "T2"], "close": [1.15, 1.20, 1.10]})
    waves = [
        {"wave_time": "W1", "dir": 1, "draw_right": 1, "box_top": 1.15, "box_bottom": 1.10},
        {"wave_time": "W2", "dir": 1, "draw_right": 2, "box_top": 1.20, "box_bottom": 1.15},
    ]
    cfg = _cfg(enabled=False)
    seq_info = compute_wave_sequence_info_per_wave(df, waves, cfg)
    
    protected = compute_wave_2_no_tp_protected_waves(waves, seq_info, cfg)
    # Vypnuto -> zadne chranene vlny
    assert "W1" not in protected
    assert "W2" not in protected


def test_wave_2_no_tp_ext_bos_fib_35():
    # Scenar A EXT vlna = idx 1.
    df = pd.DataFrame({"time": ["T0", "T1", "T2"], "close": [1.15, 1.10, 1.05]})
    waves = [
        {"wave_time": "EXT1", "dir": 1, "draw_right": 1, "box_top": 1.20, "box_bottom": 1.10, "is_ext": True},
    ]
    cfg = _cfg()
    seq_info = compute_wave_sequence_info_per_wave(df, waves, cfg)
    
    protected = compute_wave_2_no_tp_protected_waves(waves, seq_info, cfg)
    # Trend ma max 1 vlnu, je chranena
    assert "EXT1" in protected


def test_wave_2_no_tp_keeps_protection_when_only_wave_2_is_ext():
    # W1 klasicka + EXT2: nejde o cisty EXT prefix 1..n → ochrana plati
    df = pd.DataFrame({"time": ["T0", "T1", "T2"], "close": [1.15, 1.20, 1.10]})
    waves = [
        {"wave_time": "W1", "dir": 1, "draw_right": 1, "box_top": 1.15, "box_bottom": 1.10},
        {"wave_time": "EXT2", "dir": 1, "draw_right": 2, "box_top": 1.20, "box_bottom": 1.15, "is_ext": True},
    ]
    cfg = _cfg()
    seq_info = compute_wave_sequence_info_per_wave(df, waves, cfg)

    protected = compute_wave_2_no_tp_protected_waves(waves, seq_info, cfg)
    assert "W1" in protected
    assert "EXT2" in protected


def test_wave_2_no_tp_drops_when_ext_prefix_1_to_n():
    # EXT1 + EXT2 pri n=2 → cely EXT prefix → ochrana neplati
    df = pd.DataFrame({"time": ["T0", "T1", "T2"], "close": [1.15, 1.20, 1.10]})
    waves = [
        {"wave_time": "EXT1", "dir": 1, "draw_right": 1, "box_top": 1.15, "box_bottom": 1.10, "is_ext": True},
        {"wave_time": "EXT2", "dir": 1, "draw_right": 2, "box_top": 1.20, "box_bottom": 1.15, "is_ext": True},
    ]
    cfg = _cfg()
    seq_info = compute_wave_sequence_info_per_wave(df, waves, cfg)

    protected = compute_wave_2_no_tp_protected_waves(waves, seq_info, cfg)
    assert "EXT1" not in protected
    assert "EXT2" not in protected


def test_wave_2_no_tp_drops_when_ext_prefix_1_to_3():
    df = pd.DataFrame({"time": ["T0", "T1", "T2", "T3"], "close": [1.15, 1.20, 1.25, 1.10]})
    waves = [
        {"wave_time": "EXT1", "dir": 1, "draw_right": 1, "box_top": 1.15, "box_bottom": 1.10, "is_ext": True},
        {"wave_time": "EXT2", "dir": 1, "draw_right": 2, "box_top": 1.20, "box_bottom": 1.15, "is_ext": True},
        {"wave_time": "EXT3", "dir": 1, "draw_right": 3, "box_top": 1.25, "box_bottom": 1.20, "is_ext": True},
    ]
    cfg = _cfg()
    cfg.wave_2_no_tp_max_index = 3
    seq_info = compute_wave_sequence_info_per_wave(df, waves, cfg)

    protected = compute_wave_2_no_tp_protected_waves(waves, seq_info, cfg)
    assert protected == set()


def test_wave_2_no_tp_keeps_protection_when_ext_prefix_incomplete():
    # n=3, ale jen EXT1+EXT2 → prefix 1..3 neni kompletni → ochrana plati
    df = pd.DataFrame({"time": ["T0", "T1", "T2"], "close": [1.15, 1.20, 1.10]})
    waves = [
        {"wave_time": "EXT1", "dir": 1, "draw_right": 1, "box_top": 1.15, "box_bottom": 1.10, "is_ext": True},
        {"wave_time": "EXT2", "dir": 1, "draw_right": 2, "box_top": 1.20, "box_bottom": 1.15, "is_ext": True},
    ]
    cfg = _cfg()
    cfg.wave_2_no_tp_max_index = 3
    seq_info = compute_wave_sequence_info_per_wave(df, waves, cfg)

    protected = compute_wave_2_no_tp_protected_waves(waves, seq_info, cfg)
    assert "EXT1" in protected
    assert "EXT2" in protected


def test_wave_2_no_tp_bos_entry_still_opens():
    # Test spise logicky ukazuje, ze bos entry neni soucasti protected (protected vraci jen wave_time existujicich vln).
    # Funkce vraci mnozinu a BOS_ENTRY neobsahuje zadnou vlnu v chranenem listu, protoze to je vstup.
    df = pd.DataFrame({"time": ["T0", "T1"], "close": [1.15, 1.10]})
    waves = [
        {"wave_time": "W1", "dir": 1, "draw_right": 1, "box_top": 1.15, "box_bottom": 1.10},
    ]
    cfg = _cfg()
    seq_info = compute_wave_sequence_info_per_wave(df, waves, cfg)
    protected = compute_wave_2_no_tp_protected_waves(waves, seq_info, cfg)
    assert "W1" in protected
    assert "BOS_ENTRY" not in protected
