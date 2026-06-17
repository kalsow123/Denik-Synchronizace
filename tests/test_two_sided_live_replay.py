"""Two-sided live replay — parita s backtest engine (birth_bar + draw_right)."""

import pandas as pd

from config.bot_config import BotConfig
from strategy.trend_bos import TrendState
from strategy.two_sided import (
    TwoSidedTracker,
    build_two_sided_wave_bar_maps,
    parent_wave_qualifies,
    replay_two_sided_tracker_engine_parity,
    retracement_fib_price,
    two_sided_enabled,
)


def _cfg(**kw) -> BotConfig:
    base = dict(
        two_sided_entry_enabled=True,
        wave_counter_two_sided_enabled=True,
        two_sided_entry_min_wave_pct=0.55,
        ext_wave_min_pct=3.0,
        wave_min_pct=0.35,
        entry_fib_level=0.5,
        wave_position_enabled=True,
    )
    base.update(kw)
    return BotConfig(**base)


def _down_wave(pct: float) -> dict:
    return {
        "dir": -1,
        "box_top": 1.1000,
        "box_bottom": 1.0900,
        "move_pct": pct,
        "fib50": 1.0950,
        "sl": 1.0980,
        "tp": 1.0890,
        "draw_left": 0,
        "draw_right": 5,
        "wave_time": "202603132330",
    }


def _make_df(n: int, *, fib_touch_bar: int | None, fib: float) -> pd.DataFrame:
    rows = []
    for i in range(n):
        if fib_touch_bar is not None and i == fib_touch_bar:
            rows.append(
                {
                    "time": pd.Timestamp("2026-03-13") + pd.Timedelta(minutes=30 * i),
                    "open": 1.15,
                    "high": fib + 0.0002,
                    "low": fib - 0.0002,
                    "close": 1.15,
                }
            )
        else:
            rows.append(
                {
                    "time": pd.Timestamp("2026-03-13") + pd.Timedelta(minutes=30 * i),
                    "open": 1.15,
                    "high": 1.1510,
                    "low": 1.1490,
                    "close": 1.15,
                }
            )
    return pd.DataFrame(rows)


def _manual_engine_tracker(
    df: pd.DataFrame,
    waves: list[dict],
    cfg: BotConfig,
    birth: dict[str, int],
    trend_states: dict[str, TrendState],
) -> TwoSidedTracker:
    """Minimalni kopie engine smycky pro two-sided registraci."""
    tracker = TwoSidedTracker()
    waves_by_bar, waves_by_end_bar = build_two_sided_wave_bar_maps(waves, birth)
    for i in range(1, len(df)):
        row = df.iloc[i]
        high, low = float(row["high"]), float(row["low"])
        for w in waves_by_end_bar.get(i, []):
            wt = str(w["wave_time"])
            tracker.register_parent(
                w,
                i,
                cfg,
                df=df,
                sync_from_bar=int(w.get("draw_left", 0)),
                trend_state=trend_states.get(wt),
            )
        tracker.update_bar(high, low, i)
        for w in waves_by_bar.get(i, []):
            wt = str(w["wave_time"])
            if parent_wave_qualifies(w, cfg, trend_state=trend_states.get(wt)):
                tracker.register_parent(
                    w,
                    i,
                    cfg,
                    df=df,
                    sync_from_bar=int(w.get("draw_left", 0)),
                    trend_state=trend_states.get(wt),
                )
    return tracker


def test_replay_matches_manual_engine_loop():
    cfg = _cfg()
    parent = _down_wave(0.68)
    parent["draw_left"] = 0
    parent["draw_right"] = 6
    parent["wave_time"] = "202603132330"
    fib = retracement_fib_price(parent, cfg)
    df = _make_df(10, fib_touch_bar=3, fib=fib)
    birth = {"202603132330": 2}
    ts = {"202603132330": TrendState(direction="bear")}

    manual = _manual_engine_tracker(df, [parent], cfg, birth, ts)
    replayed = TwoSidedTracker()
    replay_two_sided_tracker_engine_parity(
        replayed,
        df,
        [parent],
        cfg,
        wave_birth_by_time=birth,
        trend_states_per_wave=ts,
        preserve_counter_b_wave_times=False,
    )

    assert manual.fib_was_touched(parent["wave_time"])
    assert replayed.fib_was_touched(parent["wave_time"])
    assert manual.watches[parent["wave_time"]].birth_bar == replayed.watches[
        parent["wave_time"]
    ].birth_bar


def test_birth_bar_registration_catches_fib_before_draw_right():
    """Stary live (draw_right=6) minul FIB na baru 3; replay s birth_bar=2 ano."""
    cfg = _cfg()
    parent = _down_wave(0.68)
    parent["draw_left"] = 0
    parent["draw_right"] = 6
    parent["wave_time"] = "202603132330"
    fib = retracement_fib_price(parent, cfg)
    df = _make_df(10, fib_touch_bar=3, fib=fib)
    birth = {"202603132330": 2}
    ts = {"202603132330": TrendState(direction="bear")}

    replayed = TwoSidedTracker()
    replay_two_sided_tracker_engine_parity(
        replayed,
        df,
        [parent],
        cfg,
        wave_birth_by_time=birth,
        trend_states_per_wave=ts,
        preserve_counter_b_wave_times=False,
    )
    assert replayed.watches[parent["wave_time"]].birth_bar == 2
    assert replayed.fib_was_touched(parent["wave_time"])

    draw_right_only = TwoSidedTracker()
    draw_right_only.register_parent(
        parent,
        6,
        cfg,
        df=df,
        sync_from_bar=0,
        trend_state=ts[parent["wave_time"]],
    )
    assert not draw_right_only.fib_was_touched(parent["wave_time"])


def test_replay_preserves_counter_b_wave_times():
    cfg = _cfg()
    tracker = TwoSidedTracker()
    tracker.counter_b_wave_times.add("202603160400")
    df = _make_df(5, fib_touch_bar=None, fib=1.1)
    replay_two_sided_tracker_engine_parity(
        tracker,
        df,
        [],
        cfg,
        wave_birth_by_time={},
        preserve_counter_b_wave_times=True,
    )
    assert "202603160400" in tracker.counter_b_wave_times
