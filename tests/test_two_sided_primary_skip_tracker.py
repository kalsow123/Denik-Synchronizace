"""Two-sided B vlna blokuje primarni WAVE i kdyz parent A neni v `waves`."""
from __future__ import annotations

from config.bot_config import BotConfig
from strategy.two_sided import (
    TwoSidedTracker,
    find_parent_wave_for_two_sided,
    skip_primary_entry_on_parent_wave,
)


class _FakeTrend:
    def __init__(self, direction: str) -> None:
        self.direction = direction


def _cfg() -> BotConfig:
    return BotConfig(
        wave_min_pct=0.26,
        min_opp_bars=3,
        ext_wave_min_pct=0.76,
        two_sided_entry_enabled=True,
        two_sided_entry_min_wave_pct=0.55,
        two_sided_entry_min_sl_move_pct=0.16,
        trend_filter_enabled=True,
    )


def _parent_down() -> dict:
    return {
        "dir": -1,
        "box_top": 1.1000,
        "box_bottom": 1.0900,
        "fib50": 1.0950,
        "sl": 1.0980,
        "tp": 1.0890,
        "move_pct": 0.68,
        "wave_time": "202603121800",
        "draw_left": 10,
        "draw_right": 30,
    }


def _counter_up() -> dict:
    return {
        "dir": 1,
        "box_top": 1.0960,
        "box_bottom": 1.0900,
        "fib50": 1.0930,
        "sl": 1.0905,
        "tp": 1.0980,
        "move_pct": 0.35,
        "wave_time": "202603122000",
        "draw_left": 31,
        "draw_right": 45,
    }


def test_link_counter_b_when_parent_only_in_tracker():
    """Parent A chybi v visible `waves`, ale je v tracker.armed — B se zaregistruje."""
    cfg = _cfg()
    parent = _parent_down()
    child = _counter_up()
    bear = _FakeTrend("bear")
    trend_states = {
        parent["wave_time"]: bear,
        child["wave_time"]: bear,
    }
    tracker = TwoSidedTracker()
    tracker.register_parent(parent, 30, cfg, trend_state=bear)
    tracker._mark_fib_touch(str(parent["wave_time"]), 25)

    visible_waves = [child]
    assert find_parent_wave_for_two_sided(visible_waves, child, cfg) is None

    assert tracker.link_counter_b_wave_if_matches(
        child, visible_waves, cfg, trend_states_per_wave=trend_states
    )
    assert tracker.is_b_wave_for_any_parent(child["wave_time"])
    assert not skip_primary_entry_on_parent_wave(child, cfg, trend_state=bear)


def test_is_b_wave_false_for_unrelated_wave():
    cfg = _cfg()
    tracker = TwoSidedTracker()
    assert not tracker.is_b_wave_for_any_parent("202603999999")
