"""Two-sided entry — dotek FIB rodice + WAVE na protivlni."""
from __future__ import annotations

from config.bot_config import BotConfig
from strategy.two_sided import (
    TwoSidedTracker,
    bar_touched_price,
    counter_wave_qualifies_for_two_sided,
    find_parent_wave_for_two_sided,
    parent_wave_qualifies,
    prepare_two_sided_counter_signal,
    retracement_fib_price,
    should_open_two_sided_counter,
    skip_primary_entry_on_parent_wave,
    two_sided_parent_max_wave_pct,
    wave_show_in_visual,
    waves_for_visual_display,
)


def _cfg(**kw) -> BotConfig:
    base = dict(
        wave_min_pct=0.26,
        min_opp_bars=3,
        entry_fib_level=0.5,
        sl_fib_level=0.8,
        rrr=2.0,
        ext_enabled=True,
        ext_wave_min_pct=0.76,
        two_sided_entry_enabled=True,
        two_sided_entry_min_wave_pct=0.55,
        two_sided_entry_min_sl_move_pct=0.16,
    )
    base.update(kw)
    return BotConfig(**base)


def _down_wave(move_pct: float = 0.7) -> dict:
    return {
        "dir": -1,
        "box_top": 1.1000,
        "box_bottom": 1.0900,
        "fib50": 1.0950,
        "sl": 1.0980,
        "tp": 1.0890,
        "move_pct": move_pct,
        "wave_time": "202603121800",
        "draw_left": 10,
        "draw_right": 30,
    }


def _up_wave(move_pct: float = 0.35) -> dict:
    return {
        "dir": 1,
        "box_top": 1.0960,
        "box_bottom": 1.0900,
        "fib50": 1.0930,
        "sl": 1.0905,
        "tp": 1.0980,
        "move_pct": move_pct,
        "wave_time": "202603122000",
        "draw_left": 31,
        "draw_right": 45,
    }


def test_should_open_when_parent_large_fib_touched_opposite_dir():
    cfg = _cfg()
    parent = _down_wave(0.7)
    child = _up_wave(0.35)
    assert should_open_two_sided_counter(
        parent, child, cfg, parent_fib_touched=True
    )


def test_should_not_open_without_fib_touch():
    cfg = _cfg()
    assert not should_open_two_sided_counter(
        _down_wave(0.7), _up_wave(), cfg, parent_fib_touched=False
    )


def test_should_not_open_if_parent_too_small():
    cfg = _cfg()
    assert not should_open_two_sided_counter(
        _down_wave(0.4), _up_wave(), cfg, parent_fib_touched=True
    )


def test_parent_interval_uses_ext_wave_min_pct():
    cfg = _cfg(ext_wave_min_pct=0.76, two_sided_entry_min_wave_pct=0.55)
    assert two_sided_parent_max_wave_pct(cfg) == 0.76
    assert parent_wave_qualifies(_down_wave(0.68), cfg)
    assert not parent_wave_qualifies(_down_wave(0.80), cfg)
    assert not parent_wave_qualifies(_down_wave(0.49), cfg)


def test_should_not_open_if_parent_is_ext_size():
    cfg = _cfg()
    parent = _down_wave(0.86)
    assert not parent_wave_qualifies(parent, cfg)
    assert not should_open_two_sided_counter(
        parent, _up_wave(0.35), cfg, parent_fib_touched=True
    )


def test_counter_in_ext_range_is_blocked():
    """Po EXT pokryva ext_trade_both_sides_in_range obousmerne obchodovani; counter
    v EXT range by se zdvojoval s timto rezimem → musi byt zamitnuty."""
    cfg = _cfg()
    child = _up_wave(0.35)
    child["in_ext_range"] = True
    assert not counter_wave_qualifies_for_two_sided(child, cfg)
    assert not should_open_two_sided_counter(
        _down_wave(0.68), child, cfg, parent_fib_touched=True
    )


def test_parent_in_ext_range_is_blocked():
    """Stejne dvody jako u countera: rodic v EXT range nesmi byt rodicem
    two-sided (EXT pokryva oba smery sam)."""
    cfg = _cfg()
    parent = _down_wave(0.68)
    parent["in_ext_range"] = True
    assert not parent_wave_qualifies(parent, cfg)
    assert not should_open_two_sided_counter(
        parent, _up_wave(0.35), cfg, parent_fib_touched=True
    )


def test_tracker_clear_on_ext():
    cfg = _cfg()
    tracker = TwoSidedTracker()
    tracker.register_parent(_down_wave(0.68), 10, cfg)
    assert tracker.watches
    tracker.clear_all()
    assert not tracker.watches
    assert not tracker.armed


def test_should_not_open_same_direction():
    cfg = _cfg()
    parent = _down_wave(0.7)
    child = dict(_down_wave(0.3))
    child["wave_time"] = "202603122100"
    assert not should_open_two_sided_counter(
        parent, child, cfg, parent_fib_touched=True
    )


def test_tracker_marks_fib_touch():
    cfg = _cfg()
    parent = _down_wave(0.7)
    fib = retracement_fib_price(parent, cfg)
    assert bar_touched_price(fib + 0.0001, fib - 0.0001, fib)
    tracker = TwoSidedTracker()
    tracker.register_parent(parent, 30, cfg)
    assert not tracker.fib_was_touched(parent["wave_time"])
    tracker.update_bar(fib + 0.0001, fib - 0.0001, 5)
    assert tracker.fib_was_touched(parent["wave_time"])


def _up_parent(move_pct: float = 0.68) -> dict:
    return {
        "dir": 1,
        "box_top": 1.1100,
        "box_bottom": 1.1000,
        "fib50": 1.1050,
        "sl": 1.1020,
        "tp": 1.1140,
        "move_pct": move_pct,
        "wave_time": "202603201000",
        "draw_left": 10,
        "draw_right": 30,
    }


def _down_counter(move_pct: float = 0.42) -> dict:
    return {
        "dir": -1,
        "box_top": 1.1060,
        "box_bottom": 1.1000,
        "fib50": 1.1030,
        "sl": 1.1055,
        "tp": 1.0970,
        "move_pct": move_pct,
        "wave_time": "202603201200",
        "draw_left": 31,
        "draw_right": 45,
    }


def test_should_open_bear_counter_after_bull_parent():
    """DOWN parent scénář: velká bear → první UP counter."""
    cfg = _cfg()
    parent = _down_wave(0.68)
    child = _up_wave(0.42)
    child["wave_time"] = "202603310430"
    assert should_open_two_sided_counter(
        parent, child, cfg, parent_fib_touched=True
    )
    sig = prepare_two_sided_counter_signal(child, cfg)
    assert int(sig["dir"]) == 1


def test_should_open_bull_counter_after_bear_parent():
    """UP parent scénář: velká bull → první DOWN counter (LONG/SHORT symetrie)."""
    cfg = _cfg()
    parent = _up_parent(0.68)
    child = _down_counter(0.42)
    assert should_open_two_sided_counter(
        parent, child, cfg, parent_fib_touched=True
    )
    sig = prepare_two_sided_counter_signal(child, cfg)
    assert int(sig["dir"]) == -1


def test_fib_touch_symmetric_up_parent():
    cfg = _cfg()
    parent = _up_parent(0.68)
    fib = retracement_fib_price(parent, cfg)
    assert fib == 1.1100 - (1.1100 - 1.1000) * 0.5
    tracker = TwoSidedTracker()
    tracker.register_parent(parent, 10, cfg)
    tracker.update_bar(fib + 0.0001, fib - 0.0001, 5)
    assert tracker.fib_was_touched(parent["wave_time"])


def test_find_parent_first_opposite_after_large_bear():
    cfg = _cfg()
    waves = [_down_wave(0.68), _up_wave(0.42)]
    waves[1]["wave_time"] = "202603310430"
    parent = find_parent_wave_for_two_sided(waves, waves[1], cfg)
    assert parent is not None
    assert parent["wave_time"] == "202603121800"


def test_find_parent_first_opposite_after_large_bull():
    cfg = _cfg()
    waves = [_up_parent(0.68), _down_counter(0.42)]
    parent = find_parent_wave_for_two_sided(waves, waves[1], cfg)
    assert parent is not None
    assert parent["wave_time"] == "202603201000"
    assert int(parent["dir"]) == 1
    assert int(waves[1]["dir"]) == -1


def test_find_parent_skips_same_dir_between():
    cfg = _cfg()
    waves = [_down_wave(0.68), _down_wave(0.3), _up_wave(0.42)]
    waves[1]["wave_time"] = "202603122000"
    waves[2]["wave_time"] = "202603310430"
    parent = find_parent_wave_for_two_sided(waves, waves[2], cfg)
    assert parent is not None
    assert parent["wave_time"] == "202603121800"


def test_find_parent_none_if_second_opposite_before_counter():
    cfg = _cfg()
    waves = [_down_wave(0.68), _up_wave(0.35), _up_wave(0.42)]
    waves[1]["wave_time"] = "202603122100"
    waves[2]["wave_time"] = "202603310430"
    assert find_parent_wave_for_two_sided(waves, waves[2], cfg) is None


def test_find_parent_none_if_counter_same_dir_as_prev():
    cfg = _cfg()
    waves = [_down_wave(0.68), _up_wave(0.42), _up_wave(0.89)]
    waves[1]["wave_time"] = "202603310430"
    waves[2]["wave_time"] = "202603311930"
    assert find_parent_wave_for_two_sided(waves, waves[2], cfg) is None


def test_find_parent_none_if_counter_below_wave_min():
    cfg = _cfg()
    waves = [_down_wave(0.68), _up_wave(0.20)]
    waves[1]["wave_time"] = "202603310430"
    assert find_parent_wave_for_two_sided(waves, waves[1], cfg) is None


def test_find_parent_uses_draw_timeline_not_list_order():
    """Parent v seznamu za counterem, ale box parenta konci pred startem counteru."""
    cfg = _cfg()
    parent = _down_wave(0.69)
    parent["wave_time"] = "202603132330"
    parent["draw_left"] = 50
    parent["draw_right"] = 80
    child = _up_wave(0.40)
    child["wave_time"] = "202603160400"
    child["draw_left"] = 81
    child["draw_right"] = 100
    waves = [child, parent]
    found = find_parent_wave_for_two_sided(waves, child, cfg)
    assert found is not None
    assert found["wave_time"] == "202603132330"


def test_register_parent_syncs_fib_from_draw_left():
    import pandas as pd

    cfg = _cfg()
    parent = _down_wave(0.69)
    parent["draw_left"] = 0
    parent["draw_right"] = 4
    fib = retracement_fib_price(parent, cfg)
    rows = []
    for i in range(6):
        rows.append(
            {
                "time": pd.Timestamp("2026-03-13") + pd.Timedelta(minutes=30 * i),
                "open": 1.15,
                "high": fib + 0.0002,
                "low": fib - 0.0002,
                "close": 1.15,
            }
        )
    df = pd.DataFrame(rows)
    tracker = TwoSidedTracker()
    tracker.register_parent(parent, 4, cfg, df=df, sync_from_bar=0)
    assert tracker.fib_was_touched(parent["wave_time"])


def test_skip_primary_on_qualifying_parent():
    cfg_false = _cfg(skip_primary_entry_on_parent_wave_enable=False)
    assert not skip_primary_entry_on_parent_wave(_down_wave(0.7), cfg_false)
    cfg_true = _cfg(skip_primary_entry_on_parent_wave_enable=True)
    assert skip_primary_entry_on_parent_wave(_down_wave(0.7), cfg_true)
    assert not skip_primary_entry_on_parent_wave(_down_wave(0.4), cfg_true)
    assert not skip_primary_entry_on_parent_wave(
        _down_wave(0.7), _cfg(two_sided_entry_enabled=False)
    )


def test_waves_for_visual_includes_tagged_counter():
    import pandas as pd
    from strategy.trend_bos import filter_waves_for_structure_display

    cfg = _cfg(trend_hh_hl_filter_enabled=True)
    parent = _down_wave(0.7)
    child = _up_wave(0.35)
    child["_two_sided_counter"] = True
    waves = [parent, child]
    df = pd.DataFrame(
        {
            "time": pd.date_range("2026-03-30", periods=50, freq="30min"),
            "open": [1.1] * 50,
            "high": [1.11] * 50,
            "low": [1.09] * 50,
            "close": [1.1] * 50,
        }
    )
    vis = waves_for_visual_display(df, waves, cfg, extra_wave_times={child["wave_time"]})
    assert child["wave_time"] in {w["wave_time"] for w in vis}
    assert wave_show_in_visual(child)


def test_prepare_counter_enforces_min_sl():
    cfg = _cfg()
    w = _up_wave()
    w["sl"] = 1.0929
    sig = prepare_two_sided_counter_signal(w, cfg)
    entry = float(sig["fib50"])
    sl = float(sig["sl"])
    min_dist = entry * 0.16 / 100.0
    assert abs(entry - sl) >= min_dist - 1e-9
    assert sig.get("_two_sided_counter") is True


def test_prepare_counter_sl_at_low_for_buy():
    """BUY counter (UP wave) → SL na LOW (box_bottom), ne na sl_fib_level."""
    cfg = _cfg()
    w = _up_wave(0.42)
    sig = prepare_two_sided_counter_signal(w, cfg)
    assert int(sig["dir"]) == 1
    # box_bottom je dostatecne daleko → SL = box_bottom (min SL check projde).
    assert float(sig["sl"]) == float(w["box_bottom"])


def test_prepare_counter_sl_at_high_for_sell():
    """SELL counter (DOWN wave) → SL na HIGH (box_top), ne na sl_fib_level."""
    cfg = _cfg()
    w = _down_counter(0.42)
    sig = prepare_two_sided_counter_signal(w, cfg)
    assert int(sig["dir"]) == -1
    assert float(sig["sl"]) == float(w["box_top"])


class _FakeTrend:
    """Minimalni stub TrendState — staci nam atribut `direction`."""

    def __init__(self, direction: str):
        self.direction = direction


def test_parent_in_trend_direction_qualifies_with_trend_state():
    """Bull trend → UP parent OK. Bear trend → DOWN parent OK."""
    cfg = _cfg()
    bull = _FakeTrend("bull")
    bear = _FakeTrend("bear")
    assert parent_wave_qualifies(_up_parent(0.68), cfg, trend_state=bull)
    assert parent_wave_qualifies(_down_wave(0.68), cfg, trend_state=bear)


def test_parent_counter_to_trend_is_blocked_with_trend_state():
    """V bear trendu nesmi byt UP parent (counter-trend) a naopak."""
    cfg = _cfg()
    bull = _FakeTrend("bull")
    bear = _FakeTrend("bear")
    assert not parent_wave_qualifies(_down_wave(0.68), cfg, trend_state=bull)
    assert not parent_wave_qualifies(_up_parent(0.68), cfg, trend_state=bear)


def test_parent_in_neutral_trend_is_blocked():
    """Neutral trend → rodic se nikdy neaktivuje (nemame proti cemu kontrovat)."""
    cfg = _cfg()
    neutral = _FakeTrend("neutral")
    assert not parent_wave_qualifies(_up_parent(0.68), cfg, trend_state=neutral)
    assert not parent_wave_qualifies(_down_wave(0.68), cfg, trend_state=neutral)


def test_counter_in_trend_direction_is_blocked_with_trend_state():
    """B counter musi byt counter-trend. V bear trendu DOWN B (= trend-dir) zakaz."""
    cfg = _cfg()
    bull = _FakeTrend("bull")
    bear = _FakeTrend("bear")
    # B UP v bull trendu = trend-dir → zakaz
    assert not counter_wave_qualifies_for_two_sided(
        _up_wave(0.35), cfg, trend_state=bull
    )
    # B DOWN v bear trendu = trend-dir → zakaz
    assert not counter_wave_qualifies_for_two_sided(
        _down_counter(0.35), cfg, trend_state=bear
    )
    # B DOWN v bull trendu = counter-trend → OK
    assert counter_wave_qualifies_for_two_sided(
        _down_counter(0.35), cfg, trend_state=bull
    )
    # B UP v bear trendu = counter-trend → OK
    assert counter_wave_qualifies_for_two_sided(
        _up_wave(0.35), cfg, trend_state=bear
    )


def test_should_open_full_trend_filter_bull():
    """Bull trend: parent UP, counter DOWN → OK. Pokud parent DOWN (counter-trend)
    nebo counter UP (trend-dir), blokuj."""
    cfg = _cfg()
    bull = _FakeTrend("bull")
    parent = _up_parent(0.68)
    counter = _down_counter(0.42)
    # spravny scenar
    assert should_open_two_sided_counter(
        parent, counter, cfg,
        parent_fib_touched=True,
        parent_trend_state=bull, counter_trend_state=bull,
    )
    # parent counter-trend → blokuj
    bad_parent = _down_wave(0.68)
    good_counter_for_bad_parent = _up_wave(0.42)
    assert not should_open_two_sided_counter(
        bad_parent, good_counter_for_bad_parent, cfg,
        parent_fib_touched=True,
        parent_trend_state=bull, counter_trend_state=bull,
    )


def test_should_open_blocks_when_bos_flipped_between_a_and_b():
    """Mezi A (UP v bullu) a B (DOWN) doslo k BOS → na B trend = bear, B je v
    trend-direction. counter musi byt odmitnuty."""
    cfg = _cfg()
    bull_at_parent_birth = _FakeTrend("bull")
    bear_at_counter_birth = _FakeTrend("bear")
    parent = _up_parent(0.68)
    counter = _down_counter(0.42)
    assert not should_open_two_sided_counter(
        parent, counter, cfg,
        parent_fib_touched=True,
        parent_trend_state=bull_at_parent_birth,
        counter_trend_state=bear_at_counter_birth,
    )


def test_find_parent_skips_counter_trend_candidates():
    """Bear trend: UP vlna v size [0.5, 0.76) nesmi byt vybrana jako rodic
    (counter-trend). DOWN parent ve smeru trendu je validni."""
    cfg = _cfg()
    bear = _FakeTrend("bear")
    counter_trend_parent = _up_parent(0.68)
    counter_trend_parent["wave_time"] = "202603121800"
    counter_trend_parent["draw_left"] = 10
    counter_trend_parent["draw_right"] = 30
    child = _down_counter(0.42)
    child["wave_time"] = "202603122000"
    child["draw_left"] = 31
    child["draw_right"] = 45
    waves = [counter_trend_parent, child]
    trend_states = {
        counter_trend_parent["wave_time"]: bear,
        child["wave_time"]: bear,
    }
    # Lax mode → najde rodice
    assert find_parent_wave_for_two_sided(waves, child, cfg) is not None
    # Plny mode → rodic je counter-trend, odmitneme
    assert find_parent_wave_for_two_sided(
        waves, child, cfg, trend_states_per_wave=trend_states
    ) is None


def test_skip_primary_respects_trend_state():
    """Skip primary parent jen pokud je parent ve smeru trendu.

    Testuje skip logiku pod flagem skip_primary_entry_on_parent_wave_enable=True;
    novy default (False) primarni vstup na rodici nepreskakuje vubec.
    """
    cfg = _cfg(skip_primary_entry_on_parent_wave_enable=True)
    bull = _FakeTrend("bull")
    bear = _FakeTrend("bear")
    parent_up = _up_parent(0.68)
    parent_down = _down_wave(0.68)
    # UP parent v bull trendu → skip
    assert skip_primary_entry_on_parent_wave(parent_up, cfg, trend_state=bull)
    # UP parent v bear trendu (counter-trend) → primary se nepreskakuje
    assert not skip_primary_entry_on_parent_wave(parent_up, cfg, trend_state=bear)
    # DOWN parent v bear trendu → skip
    assert skip_primary_entry_on_parent_wave(parent_down, cfg, trend_state=bear)
    # DOWN parent v bull trendu (counter-trend) → primary se nepreskakuje
    assert not skip_primary_entry_on_parent_wave(parent_down, cfg, trend_state=bull)
