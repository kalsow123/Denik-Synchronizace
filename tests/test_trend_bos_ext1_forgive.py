"""Trade-core: prvni klasicky BOS po EXT vlne idx 1 se odpusti (trend bezi dal,
nezalozi/neuzavre BOS pozice). Pokud ma EXT idx 2+, ochrana neplati."""
from config.bot_config import BotConfig
from strategy.trend_bos import (
    TrendState,
    _bos_close_flip_with_forgive,
    _update_state_with_wave,
    maybe_update_trend_state_with_wave,
)


def _cfg():
    return BotConfig(symbol="EURUSD", timeframe=30, trend_hh_hl_filter_enabled=True)


def _w(dir_val, box_top, box_bottom, is_ext=False, wave_time="W"):
    w = {
        "wave_time": wave_time,
        "dir": dir_val,
        "box_top": box_top,
        "box_bottom": box_bottom,
    }
    if is_ext:
        w["is_ext"] = True
    return w


def test_first_wave_ext_marks_trend_established_by_ext():
    state = TrendState(direction="bull")  # cerstvy trend po flipu (counts 0)
    _update_state_with_wave(state, _w(1, 1.30, 1.10, is_ext=True))
    assert state.trend_established_by_ext is True


def test_first_wave_non_ext_does_not_mark():
    state = TrendState(direction="bull")
    _update_state_with_wave(state, _w(1, 1.30, 1.10, is_ext=False))
    assert state.trend_established_by_ext is False


def test_second_wave_ext_does_not_mark():
    # EXT az jako 2. vlna trendu -> ochrana neplati.
    state = TrendState(direction="bull")
    _update_state_with_wave(state, _w(1, 1.20, 1.05, is_ext=False, wave_time="W1"))
    _update_state_with_wave(state, _w(1, 1.30, 1.10, is_ext=True, wave_time="EXT"))
    assert state.trend_established_by_ext is False


def test_forgive_first_then_flip_second():
    # Bull trend zalozeny EXT vlnou idx 1, swing low = 1.10.
    state = TrendState(direction="bull")
    _update_state_with_wave(state, _w(1, 1.30, 1.10, is_ext=True))
    assert state.trend_established_by_ext is True
    assert state.last_up_box_bottom == 1.10

    # 1. BOS (close 1.05 < 1.10) -> ODPUSTI: zadny flip, swing vynulovan, flag pryc.
    flipped, state = _bos_close_flip_with_forgive(state, 1.05)
    assert flipped == 0
    assert state.direction == "bull"
    assert state.trend_established_by_ext is False
    assert state.last_up_box_bottom is None

    # Nova trend-dir UP vlna obnovi swing (1.12).
    maybe_update_trend_state_with_wave(state, _w(1, 1.35, 1.12), _cfg())
    assert state.last_up_box_bottom == 1.12

    # 2. BOS (close 1.08 < 1.12) uz FLIPNE na bear.
    flipped, state = _bos_close_flip_with_forgive(state, 1.08)
    assert flipped == -1
    assert state.direction == "bear"


def test_no_forgive_without_ext_establishment():
    # Trend NEzalozeny EXT vlnou -> prvni BOS rovnou flipne.
    state = TrendState(direction="bull")
    _update_state_with_wave(state, _w(1, 1.30, 1.10, is_ext=False))
    assert state.trend_established_by_ext is False
    flipped, state = _bos_close_flip_with_forgive(state, 1.05)
    assert flipped == -1
    assert state.direction == "bear"
