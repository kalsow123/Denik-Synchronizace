"""Test: po compute_wave_sequence_info_per_wave musí mít waves nastavené
index_in_trend a is_bos_wave."""
import pandas as pd
from strategy.wave_detection import detect_waves
from strategy.wave_sequence import (
    compute_wave_sequence_info_per_wave,
    propagate_seq_info_to_waves,
)
from config.bot_config import LIVE_BOT_CONFIG


def _load_test_data():
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    df = df[(df["time"] >= "2026-03-03") & (df["time"] <= "2026-05-10")].reset_index(drop=True)
    return df


def test_propagate_sets_index_in_trend_and_is_bos_wave():
    df = _load_test_data()
    cfg = LIVE_BOT_CONFIG
    waves = detect_waves(df, cfg)
    seq_info = compute_wave_sequence_info_per_wave(df, waves, cfg)
    propagate_seq_info_to_waves(waves, seq_info)

    for w in waves:
        assert "index_in_trend" in w
        assert "is_bos_wave" in w
        assert "prev_same_dir_in_trend_wave_time" in w

    waves_with_idx = [w for w in waves if w.get("index_in_trend") is not None and w["index_in_trend"] >= 1]
    assert len(waves_with_idx) > 0

    bos_waves = [w for w in waves if w.get("is_bos_wave") is True]
    assert len(bos_waves) > 0


def test_propagate_is_idempotent():
    df = _load_test_data()
    cfg = LIVE_BOT_CONFIG
    waves = detect_waves(df, cfg)
    seq_info = compute_wave_sequence_info_per_wave(df, waves, cfg)

    propagate_seq_info_to_waves(waves, seq_info)
    snap1 = [(str(w.get("wave_time")), w.get("index_in_trend"), w.get("is_bos_wave")) for w in waves]

    propagate_seq_info_to_waves(waves, seq_info)
    snap2 = [(str(w.get("wave_time")), w.get("index_in_trend"), w.get("is_bos_wave")) for w in waves]

    assert snap1 == snap2
