"""EXT BOS market counter — po EXT aktivni hned, ale zrusi ho nova trendova vlna."""
from __future__ import annotations

from strategy.ext_logic import (
    advance_ext_bos_state,
    build_ext_bos_state_map,
    classify_ext_bos_state,
    ext_bos_market_entry_allowed,
    is_ext_wave,
)


class _Cfg:
    ext_enabled = True
    ext_wave_min_pct = 0.5


def _waves_and_birth():
    ext_time = "202601011200"
    waves = [
        {"wave_time": "202601011000", "dir": -1, "move_pct": 0.3},
        {
            "wave_time": ext_time,
            "dir": 1,
            "move_pct": 1.0,
            "is_ext": True,
            "ext_bos_level": 1.05,
        },
        {"wave_time": "202601011400", "dir": -1, "move_pct": 0.4},
        {"wave_time": "202601011600", "dir": 1, "move_pct": 0.35},
    ]
    birth = {
        "202601011000": 10,
        ext_time: 20,
        "202601011400": 30,
        "202601011600": 40,
    }
    return ext_time, waves, birth


def test_no_subsequent_wave_keeps_bos_market_armed():
    ext_time = "202601011200"
    waves = [
        {
            "wave_time": ext_time,
            "dir": -1,
            "move_pct": 1.0,
            "is_ext": True,
            "ext_bos_level": 0.95,
        },
    ]
    birth = {ext_time: 20}
    assert classify_ext_bos_state(ext_time, -1, waves, birth) == "armed"
    assert ext_bos_market_entry_allowed("armed") is True


def test_opposite_wave_keeps_bos_market_armed():
    ext_time, waves, birth = _waves_and_birth()
    assert classify_ext_bos_state(ext_time, 1, waves[:3], birth) == "armed"
    assert ext_bos_market_entry_allowed("armed") is True


def test_classify_trend_first_cancels_bos_market():
    ext_time = "202601011200"
    waves = [
        {
            "wave_time": ext_time,
            "dir": 1,
            "move_pct": 1.0,
            "is_ext": True,
            "ext_bos_level": 1.05,
        },
        {"wave_time": "202601011400", "dir": 1, "move_pct": 0.4},
    ]
    birth = {ext_time: 20, "202601011400": 30}
    assert classify_ext_bos_state(ext_time, 1, waves, birth) == "cancelled"
    assert ext_bos_market_entry_allowed("cancelled") is False


def test_trend_wave_after_opposite_cancels_bos_market():
    ext_time, waves, birth = _waves_and_birth()
    assert classify_ext_bos_state(ext_time, 1, waves, birth) == "cancelled"
    assert ext_bos_market_entry_allowed("cancelled") is False


def test_advance_state_cancels_after_trend_resumes():
    state = "armed"
    assert state == "armed"
    state = advance_ext_bos_state(state, ext_dir=-1, wave_dir=1)
    assert state == "armed"
    state = advance_ext_bos_state(state, ext_dir=-1, wave_dir=-1)
    assert state == "cancelled"
    state = advance_ext_bos_state(state, ext_dir=-1, wave_dir=1)
    assert state == "cancelled"


def test_build_map_marks_only_ext_waves():
    ext_time, waves, birth = _waves_and_birth()
    cfg = _Cfg()
    m = build_ext_bos_state_map(waves, birth, cfg)
    assert set(m) == {str(w["wave_time"]) for w in waves if is_ext_wave(w, cfg)}
    assert m[ext_time] == "cancelled"
