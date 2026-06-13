"""WF continuation: BOS na WF vubec ne (swing, flip mapa, vizual, freeze okno)."""
from __future__ import annotations

import pandas as pd

from config.bot_config import BotConfig
from strategy.trend_bos import (
    TrendState,
    _update_state_with_wave,
    _wave_is_wf_origin,
    bar_in_wf_bos_freeze,
    build_wf_bos_freeze_ranges,
    collect_bos_flip_events,
    compute_bos_wave_flip_map,
)
from strategy.wick_fakeout import WAVE_ORIGIN_WF


def _cfg() -> BotConfig:
    return BotConfig(
        wave_min_pct=0.26,
        min_opp_bars=3,
        trend_filter_enabled=True,
        trend_hh_hl_filter_enabled=True,
    )


def test_wf_wave_does_not_update_bos_swing_level():
    state = TrendState(direction="bull")
    classic = {
        "wave_time": "up1",
        "dir": 1,
        "box_top": 1.12,
        "box_bottom": 1.08,
    }
    _update_state_with_wave(state, classic)
    wf = {
        "wave_time": "wf1",
        "dir": 1,
        "box_top": 1.15,
        "box_bottom": 1.07,
        "wave_origin": WAVE_ORIGIN_WF,
        "wf_wave_position": True,
    }
    _update_state_with_wave(state, wf)

    assert state.last_up_wave_time == "up1"
    assert state.last_up_box_bottom == 1.08
    assert state.last_up_from_wf is False
    assert _wave_is_wf_origin(wf)


def test_bos_flip_map_never_assigns_wf_wave():
    cfg = _cfg()
    df = pd.DataFrame(
        {
            "time": pd.date_range("2026-03-04 06:00", periods=20, freq="30min"),
            "open": [1.10] * 20,
            "high": [1.11] * 20,
            "low": [1.09] * 20,
            "close": [1.105] * 20,
        }
    )
    birth = {"classic": 5, "wf": 8, "down": 12}
    waves = [
        {
            "wave_time": "classic",
            "dir": 1,
            "draw_left": 2,
            "draw_right": 4,
            "box_top": 1.11,
            "box_bottom": 1.08,
        },
        {
            "wave_time": "wf",
            "dir": 1,
            "draw_left": 6,
            "draw_right": 8,
            "box_top": 1.12,
            "box_bottom": 1.07,
            "wave_origin": WAVE_ORIGIN_WF,
            "wf_wave_position": True,
        },
        {
            "wave_time": "down",
            "dir": -1,
            "draw_left": 10,
            "draw_right": 12,
            "box_top": 1.10,
            "box_bottom": 1.06,
        },
    ]
    flip_map = compute_bos_wave_flip_map(df, waves, cfg, wave_birth_bars=birth)
    assert "wf" not in set(flip_map.values())


def test_collect_bos_events_skip_swing_from_wf_only_reference():
    """Kdyz je posledni swing z WF (from_wf=True), close break neni BOS event."""
    cfg = _cfg()
    closes = [1.10] * 10
    closes[7] = 1.06  # break below fakeout wick low
    df = pd.DataFrame(
        {
            "time": pd.date_range("2026-03-04", periods=10, freq="30min"),
            "open": closes,
            "high": [c + 0.01 for c in closes],
            "low": [c - 0.01 for c in closes],
            "close": closes,
        }
    )
    waves = [
        {
            "wave_time": "wf",
            "dir": 1,
            "draw_left": 4,
            "draw_right": 6,
            "box_top": 1.12,
            "box_bottom": 1.08,
            "wave_origin": WAVE_ORIGIN_WF,
            "wf_wave_position": True,
        },
    ]
    birth = {"wf": 6}
    # WF neposouva swing — bez predchozi classic vlny neni co lamat
    events = collect_bos_flip_events(df, waves, cfg)
    assert events == []


def test_wf_freeze_ranges_helpers():
    """build_wf_bos_freeze_ranges + bar_in_wf_bos_freeze."""
    waves = [
        {
            "wave_time": "wf",
            "draw_left": 6,
            "draw_right": 8,
            "wave_origin": WAVE_ORIGIN_WF,
            "wf_wave_position": True,
        }
    ]
    assert build_wf_bos_freeze_ranges(waves) == [(6, 8)]
    assert bar_in_wf_bos_freeze(7, [(6, 8)])
    assert not bar_in_wf_bos_freeze(5, [(6, 8)])


def test_bos_flip_map_post_filter_drops_flips_inside_wf_box():
    """`compute_bos_wave_flip_map` nesmi vratit flip uvnitr WF okna."""
    cfg = _cfg()
    n = 14
    closes = [1.10] * n
    closes[4] = 1.12  # nastavi bear swing
    closes[7] = 1.13  # break swing uvnitr WF okna → musi se vyfiltrovat
    df = pd.DataFrame(
        {
            "time": pd.date_range("2026-03-04", periods=n, freq="30min"),
            "open": closes,
            "high": [c + 0.01 for c in closes],
            "low": [c - 0.01 for c in closes],
            "close": closes,
        }
    )
    birth = {"down": 3, "wf": 7}
    waves = [
        {
            "wave_time": "down",
            "dir": -1,
            "draw_left": 2,
            "draw_right": 4,
            "box_top": 1.11,
            "box_bottom": 1.08,
        },
        {
            "wave_time": "wf",
            "dir": 1,
            "draw_left": 6,
            "draw_right": 8,
            "box_top": 1.14,
            "box_bottom": 1.09,
            "wave_origin": WAVE_ORIGIN_WF,
            "wf_wave_position": True,
        },
    ]
    flip_map = compute_bos_wave_flip_map(df, waves, cfg, wave_birth_bars=birth)
    for bar_ix in flip_map.keys():
        assert not bar_in_wf_bos_freeze(int(bar_ix), [(6, 8)])


def test_engine_mar4_wf_no_bos_in_freeze_or_attribution():
    """Integrace: WF 202603040830 nema BOS roli ani flip v jeho boxu."""
    from backtest.engine import BacktestEngine
    from config.bot_config import LIVE_BOT_CONFIG

    cfg = LIVE_BOT_CONFIG
    df = pd.read_csv(
        "data/EURUSD.x_M30.csv", parse_dates=["datetime"]
    ).rename(columns={"datetime": "time"})
    df = df[(df["time"] >= "2026-03-03") & (df["time"] <= "2026-03-05")].reset_index(
        drop=True
    )
    eng = BacktestEngine(cfg)
    eng.run(df, retain_wave_snapshot=True)
    wf = next(
        w
        for w in eng.last_waves
        if str(w.get("wave_time", "")) == "202603040830"
    )
    freeze = build_wf_bos_freeze_ranges([wf])
    assert freeze
    wf_times = set()
    for lo, hi in freeze:
        for i in range(lo, hi + 1):
            if i < len(df):
                wf_times.add(pd.Timestamp(df.iloc[i]["time"]))
    for ev in eng.bos_flip_events:
        assert pd.Timestamp(ev[0]) not in wf_times
        if ev[3] is not None:
            assert pd.Timestamp(ev[3]) not in {
                pd.Timestamp(df.iloc[int(wf["draw_left"])]["time"])
            }
    assert "202603040830" not in eng._bos_wave_times
    assert "202603040830" not in eng._visual_bos_wave_times
    for bar_ix, w in eng._bos_flip_wave_by_bar.items():
        assert not _wave_is_wf_origin(w)
        assert not bar_in_wf_bos_freeze(int(bar_ix), freeze)
