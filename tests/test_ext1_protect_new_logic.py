"""Test nové logiky ext1_protect_positions_until_wave2."""
from dataclasses import fields, replace
from types import SimpleNamespace

import pandas as pd
from config.bot_config import BotConfig, LIVE_BOT_CONFIG
from strategy.wave_detection import detect_waves
from strategy.wave_sequence import (
    build_ext1_wave_times,
    compute_ext1_protection_bars,
    ext1_close_blocked_on_bar,
    ext1_protection_active_on_bar,
)


def _load_test_data():
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    df = df[(df["time"] >= "2026-03-03") & (df["time"] <= "2026-05-10")].reset_index(drop=True)
    return df


def _synthetic_ext1_trend_waves():
    """Kontrolovaný bull trend po EXT1 s idx až 7 (pro test max_index prahu)."""
    df = pd.DataFrame({
        "time": [f"T{i}" for i in range(15)],
        "close": [
            1.15, 1.25, 1.20, 1.26, 1.28, 1.30, 1.32, 1.34,
            1.36, 1.38, 1.40, 1.42, 1.44, 1.46, 1.48,
        ],
    })
    waves = [
        {
            "wave_time": "EXT_UP",
            "dir": 1,
            "draw_right": 1,
            "is_ext": True,
            "hh_hl_pass": True,
            "box_bottom": 1.10,
            "box_top": 1.30,
        },
        {
            "wave_time": "D1",
            "dir": -1,
            "draw_right": 2,
            "hh_hl_pass": True,
            "box_bottom": 1.15,
            "box_top": 1.24,
        },
        {
            "wave_time": "U2",
            "dir": 1,
            "draw_right": 3,
            "hh_hl_pass": True,
            "box_bottom": 1.21,
            "box_top": 1.28,
        },
        {
            "wave_time": "U3",
            "dir": 1,
            "draw_right": 5,
            "hh_hl_pass": True,
            "box_bottom": 1.27,
            "box_top": 1.32,
        },
        {
            "wave_time": "U4",
            "dir": 1,
            "draw_right": 7,
            "hh_hl_pass": True,
            "box_bottom": 1.31,
            "box_top": 1.36,
        },
        {
            "wave_time": "U5",
            "dir": 1,
            "draw_right": 9,
            "hh_hl_pass": True,
            "box_bottom": 1.35,
            "box_top": 1.40,
        },
        {
            "wave_time": "U6",
            "dir": 1,
            "draw_right": 11,
            "hh_hl_pass": True,
            "box_bottom": 1.39,
            "box_top": 1.44,
        },
        {
            "wave_time": "U7",
            "dir": 1,
            "draw_right": 13,
            "hh_hl_pass": True,
            "box_bottom": 1.43,
            "box_top": 1.48,
        },
    ]
    return df, waves


def _cfg_ext1_protect(**overrides):
    base = {
        "ext1_protect_positions_until_wave2": True,
        "wave_2_no_tp_enable": False,
        "tp_mode": "wave_target_n",
    }
    base.update(overrides)
    return replace(LIVE_BOT_CONFIG, **base)


def test_protection_ends_on_idx_2_when_no_tp_disabled():
    df = _load_test_data()
    cfg = _cfg_ext1_protect(wave_2_no_tp_enable=False, tp_mode="wave_target_n")

    waves = detect_waves(df, cfg)
    bars = compute_ext1_protection_bars(df, waves, cfg)

    assert any(bars), "Žádný bar nemá ochranu"


def test_higher_max_index_extends_protection():
    df, waves = _synthetic_ext1_trend_waves()
    waves_copy = [dict(w) for w in waves]

    cfg2 = _cfg_ext1_protect(wave_2_no_tp_enable=True, wave_2_no_tp_max_index=2)
    cfg5 = _cfg_ext1_protect(wave_2_no_tp_enable=True, wave_2_no_tp_max_index=5)

    bars_max2 = compute_ext1_protection_bars(df, [dict(w) for w in waves], cfg2)
    bars_max5 = compute_ext1_protection_bars(df, waves_copy, cfg5)

    assert sum(bars_max5) >= sum(bars_max2), (
        f"max_index=5 ({sum(bars_max5)}) musí dát >= barů než max_index=2 ({sum(bars_max2)})"
    )


def test_rrr_fixed_ignores_wave_2_no_tp_enable():
    df = _load_test_data()
    waves = detect_waves(df, LIVE_BOT_CONFIG)

    cfg_off = _cfg_ext1_protect(tp_mode="rrr_fixed", wave_2_no_tp_enable=False)
    cfg_on = _cfg_ext1_protect(
        tp_mode="rrr_fixed",
        wave_2_no_tp_enable=True,
        wave_2_no_tp_max_index=5,
    )

    bars_off = compute_ext1_protection_bars(df, waves, cfg_off)
    bars_on = compute_ext1_protection_bars(df, detect_waves(df, cfg_on), cfg_on)

    assert bars_off == bars_on, (
        "Pro rrr_fixed mode se wave_2_no_tp_enable NESMÍ aplikovat"
    )


def test_old_config_key_backward_compat():
    df = _load_test_data()
    legacy = {f.name: getattr(LIVE_BOT_CONFIG, f.name) for f in fields(LIVE_BOT_CONFIG)}
    legacy.pop("ext1_protect_positions_until_wave2", None)
    legacy["ext1_protect_positions_until_ext2"] = True
    cfg = SimpleNamespace(**legacy)

    waves = detect_waves(df, cfg)
    bars = compute_ext1_protection_bars(df, waves, cfg)

    assert any(bars), "Starý config klíč musí stále fungovat"


def test_ext1_close_blocked_helpers():
    from strategy.ext_logic import ENTRY_TAG_EXT_COUNTER_BOS

    cfg = SimpleNamespace(ext1_protect_positions_until_wave2=True)
    per_bar = [0, 1, 1, 0]
    assert ext1_protection_active_on_bar(1, per_bar, cfg)
    assert ext1_close_blocked_on_bar(1, per_bar, cfg, "BOS_EXIT")
    assert not ext1_close_blocked_on_bar(1, per_bar, cfg, "SL")
    ext_cnt = SimpleNamespace(
        dir=-1, entry_tag=ENTRY_TAG_EXT_COUNTER_BOS, is_ext=True, is_counter=False,
    )
    assert not ext1_close_blocked_on_bar(1, per_bar, cfg, "BOS_EXIT", trade=ext_cnt)
    waves = _synthetic_ext1_trend_waves()[1]
    waves[0]["index_in_trend"] = 1
    waves[0]["is_ext"] = True
    assert "EXT_UP" in build_ext1_wave_times(waves)
