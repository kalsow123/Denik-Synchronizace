"""PP: break az po ukonceni vlny (dalsi vlna v sekvenci musi existovat)."""
from __future__ import annotations

from config.bot_config import BotConfig
from strategy.trend_bos import (
    find_pp_candidate_wave,
    pp_wave_eligible_for_break,
    pp_wave_finished_for_break,
)


def _w(wt: str, d: int) -> dict:
    return {"wave_time": wt, "dir": d, "box_top": 1.1, "box_bottom": 1.0}


def test_pp_not_finished_without_later_wave():
    birth = {"W1": 10}
    assert pp_wave_finished_for_break(_w("W1", 1), bar_idx=11, wave_birth=birth) is False


def test_pp_finished_after_next_wave_born():
    birth = {"W1": 10, "W2": 12}
    assert pp_wave_finished_for_break(_w("W1", 1), bar_idx=13, wave_birth=birth) is True


def test_pp_not_on_birth_bar_even_with_later_wave():
    birth = {"W1": 10, "W2": 12}
    assert pp_wave_finished_for_break(_w("W1", 1), bar_idx=10, wave_birth=birth) is False


def test_find_pp_candidate_respects_broken_set():
    waves = [_w("W1", -1), _w("W2", -1)]
    birth = {"W1": 5, "W2": 10}
    c = find_pp_candidate_wave(
        waves, birth, bar_idx=15, trend_dir=-1, broken_wave_times={"W2"},
    )
    assert c is not None
    assert c["wave_time"] == "W1"


def test_pp_eligible_requires_finished():
    cfg = BotConfig(trend_hh_hl_filter_enabled=True)
    w = _w("W1", -1)
    w["hh_hl_pass"] = True
    birth = {"W1": 10}
    ok, reason = pp_wave_eligible_for_break(
        w, bar_idx=11, wave_birth=birth, cfg=cfg,
    )
    assert ok is False
    assert reason == "wave_not_finished"

    birth["W2"] = 12
    ok, reason = pp_wave_eligible_for_break(
        w, bar_idx=13, wave_birth=birth, cfg=cfg,
    )
    assert ok is True
    assert reason == "ok"


def test_pp_blocked_on_ext_wave_when_disabled():
    cfg = BotConfig(
        ext_enabled=True,
        ext_wave_min_pct=0.76,
        pp_disabled_in_ext_context=True,
    )
    w = _w("EXT1", 1)
    w["move_pct"] = 1.0
    birth = {"EXT1": 10, "W2": 12}
    ok, reason = pp_wave_eligible_for_break(
        w, bar_idx=13, wave_birth=birth, cfg=cfg,
    )
    assert ok is False
    assert reason == "ext_wave"


def test_pp_blocked_in_ext_range_when_disabled():
    cfg = BotConfig(
        ext_enabled=True,
        ext_trade_both_sides_in_range=True,
        pp_disabled_in_ext_context=True,
    )
    w = _w("W1", 1)
    w["in_ext_range"] = True
    birth = {"W1": 10, "W2": 12}
    ok, reason = pp_wave_eligible_for_break(
        w, bar_idx=13, wave_birth=birth, cfg=cfg,
    )
    assert ok is False
    assert reason == "in_ext_range"


def test_pp_allowed_on_ext_wave_when_flag_off():
    cfg = BotConfig(
        ext_enabled=True,
        ext_wave_min_pct=0.76,
        pp_disabled_in_ext_context=False,
    )
    w = _w("EXT1", 1)
    w["move_pct"] = 1.0
    w["hh_hl_pass"] = True
    birth = {"EXT1": 10, "W2": 12}
    ok, reason = pp_wave_eligible_for_break(
        w, bar_idx=13, wave_birth=birth, cfg=cfg,
    )
    assert ok is True
    assert reason == "ok"
