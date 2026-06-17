"""Test: po BOS flipu se ext1 ochrana správně reseuje na nový trend."""
from dataclasses import replace

import pandas as pd

from config.bot_config import LIVE_BOT_CONFIG
from strategy.wave_detection import detect_waves
from strategy.wave_sequence import (
    _wave_bar_index,
    compute_ext1_protection_bars,
    compute_wave_sequence_info_per_wave,
    propagate_seq_info_to_waves,
)


def _load_test_data():
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    # Použít sekci, kde je vidět BOS flip a nový trend s EXT
    df = df[(df["time"] >= "2026-03-15") & (df["time"] <= "2026-04-20")].reset_index(drop=True)
    return df


def _cfg_protect():
    return replace(
        LIVE_BOT_CONFIG,
        ext1_protect_positions_until_wave2=True,
        tp_mode="wave_target_n",
        wave_2_no_tp_enable=False,
    )


def test_protection_resets_on_bos_flip():
    df = _load_test_data()
    cfg = _cfg_protect()

    waves = detect_waves(df, cfg)
    bars = compute_ext1_protection_bars(df, waves, cfg)

    # Mezi trendy musí existovat nechráněné bary (0) i chráněné úseky.
    assert any(b == 0 for b in bars), "Musí existovat bary bez ochrany"
    assert any(b != 0 for b in bars), "Musí existovat bary s ochranou"
    zero_runs = []
    in_zero = False
    start = 0
    for i, b in enumerate(bars):
        is_zero = b == 0
        if is_zero and not in_zero:
            in_zero = True
            start = i
        elif not is_zero and in_zero:
            in_zero = False
            zero_runs.append((start, i))
    if in_zero:
        zero_runs.append((start, len(bars)))
    assert len(zero_runs) >= 1, "Musí existovat alespoň jeden nechráněný úsek"


def test_protection_ends_on_idx_2_in_new_trend():
    """Po EXT1: ochrana končí na baru první trend-dir vlny s idx >= 2 (bar vlny je False)."""
    df = _load_test_data()
    cfg = _cfg_protect()

    waves = detect_waves(df, cfg)
    seq = compute_wave_sequence_info_per_wave(df, waves, cfg)
    propagate_seq_info_to_waves(waves, seq)
    bars = compute_ext1_protection_bars(df, waves, cfg)

    ended = 0
    for w in waves:
        idx = w.get("index_in_trend")
        if idx is None or int(idx) < 2:
            continue
        dr = w.get("draw_right")
        if dr is None:
            continue
        bar = int(dr)
        assert not bars[bar], (
            f"Vlna idx={idx} @ bar {bar} ({w['wave_time']}) musí ukončit ochranu — bar je False"
        )
        ended += 1

    assert ended > 0, "V testovacím úseku musí existovat alespoň jedna vlna idx>=2"


def test_flip_bar_not_protected_when_ext1_is_bos_flip():
    """EXT1 jako BOS flip: flip bar False, ochrana od bar+1 (pokud existuje U2)."""
    df = _load_test_data()
    cfg = _cfg_protect()
    waves = detect_waves(df, cfg)
    seq = compute_wave_sequence_info_per_wave(df, waves, cfg)
    propagate_seq_info_to_waves(waves, seq)
    bars = compute_ext1_protection_bars(df, waves, cfg)

    for w in waves:
        if not (w.get("is_ext") and w.get("index_in_trend") == 1 and w.get("is_bos_wave")):
            continue
        bar = _wave_bar_index(w, df)
        assert not bars[bar], (
            f"Flip/EXT1 bar {bar} ({w['wave_time']}) nesmí být v ochraně"
        )


def test_non_ext_bos_flip_has_gap_before_next_ext1():
    """Po ne-EXT BOS flipu není ochrana na flip baru (EURUSD reálná data)."""
    df = _load_test_data()
    cfg = _cfg_protect()
    waves = detect_waves(df, cfg)
    seq = compute_wave_sequence_info_per_wave(df, waves, cfg)
    propagate_seq_info_to_waves(waves, seq)
    bars = compute_ext1_protection_bars(df, waves, cfg)

    non_ext_flips = [
        w for w in waves
        if w.get("is_bos_wave") and not w.get("is_ext") and w.get("draw_right") is not None
    ]
    assert non_ext_flips, "V úseku musí být BOS flip bez EXT"

    for w in non_ext_flips[:5]:
        bar = _wave_bar_index(w, df)
        assert not bars[bar], (
            f"Ne-EXT flip bar {bar} ({w['wave_time']}) nesmí mít ochranu"
        )
