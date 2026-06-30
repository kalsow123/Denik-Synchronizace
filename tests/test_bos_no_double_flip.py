"""Regression: BOS vizualizace — zadne dve sousedni cary stejneho smeru."""
from __future__ import annotations

import pandas as pd

from config.bot_config import BotConfig, LIVE_BOT_CONFIG
from strategy import trend_bos
from strategy.trend_bos import collect_bos_flip_events


def _flip_targets(events) -> list[str]:
    out: list[str] = []
    for _t, _lvl, label, _t0 in events:
        lbl = str(label)
        if "bear" in lbl:
            out.append("bear")
        elif "bull" in lbl:
            out.append("bull")
        else:
            out.append("?")
    return out


def _assert_no_adjacent_same_direction(events) -> None:
    dirs = _flip_targets(events)
    for i in range(len(dirs) - 1):
        assert dirs[i] != dirs[i + 1], (
            f"sousedni BOS cary stejneho smeru: {dirs[i]} -> {dirs[i + 1]} "
            f"(index {i}, {i + 1} z {len(dirs)})"
        )


def test_collect_bos_flip_events_no_adjacent_same_direction_synthetic(monkeypatch):
    """
    Po EXT seed-resetu (bez close-BOS) a dalsim close-BOS do stejneho smeru
    nesmi vizual obsahovat dve sousedni cary napr. bull -> bull.
    """
    times = pd.date_range("2026-01-02 00:00", periods=8, freq="30min")
    df = pd.DataFrame(
        {
            "time": times,
            "open": [1.10, 1.11, 1.12, 1.13, 1.14, 1.15, 1.16, 1.17],
            "high": [1.11, 1.12, 1.13, 1.14, 1.15, 1.16, 1.17, 1.18],
            "low": [1.09, 1.10, 1.11, 1.12, 1.13, 1.14, 1.15, 1.16],
            # bar 1: close pod UP low (bear BOS); bar 5: close nad DOWN high (bull BOS);
            # mezi tim seed na baru 3 prepne bull->bear bez close-BOS;
            # bar 7: dalsi close nad DOWN high (bull BOS) — bez dedup by bylo bull-bull.
            "close": [1.10, 1.08, 1.12, 1.13, 1.14, 1.165, 1.16, 1.175],
        }
    )

    waves = [
        {
            "wave_time": "w0",
            "dir": 1,
            "box_top": 1.12,
            "box_bottom": 1.10,
            "draw_left": 0,
            "draw_right": 0,
        },
        {
            "wave_time": "w1",
            "dir": -1,
            "box_top": 1.11,
            "box_bottom": 1.09,
            "draw_left": 1,
            "draw_right": 1,
        },
        {
            "wave_time": "w2",
            "dir": 1,
            "box_top": 1.14,
            "box_bottom": 1.12,
            "draw_left": 2,
            "draw_right": 2,
            "ext_post_trend_seed_dir": -1,
        },
        {
            "wave_time": "w3",
            "dir": -1,
            "box_top": 1.165,
            "box_bottom": 1.14,
            "draw_left": 3,
            "draw_right": 3,
        },
    ]

    monkeypatch.setattr(
        trend_bos,
        "compute_wave_birth_bars_pine",
        lambda _df, _cfg: {"w0": 0, "w1": 1, "w2": 3, "w3": 4},
    )
    cfg = BotConfig(
        wave_min_pct=0.26,
        min_opp_bars=3,
        trend_hh_hl_filter_enabled=False,
    )
    events = collect_bos_flip_events(df, waves, cfg)
    _assert_no_adjacent_same_direction(events)
    assert _flip_targets(events) == ["bear", "bull"]


def test_collect_bos_flip_events_no_adjacent_same_direction_live_data():
    """LIVE_BOT_CONFIG + EURUSD M30 — drive 5 dvojic bull-bull / bear-bear."""
    cfg = LIVE_BOT_CONFIG
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    df = df[(df["time"] >= "2026-03-01") & (df["time"] <= "2026-05-01")].reset_index(
        drop=True
    )
    from strategy.wave_detection import detect_waves

    waves = detect_waves(df, cfg)
    events = collect_bos_flip_events(df, waves, cfg)
    _assert_no_adjacent_same_direction(events)
