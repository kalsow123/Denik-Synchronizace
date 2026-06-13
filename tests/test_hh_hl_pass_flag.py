"""Trvaly flag wave['hh_hl_pass'] po HH/HL strukturalnim filtru."""
from __future__ import annotations

import pandas as pd

from config.bot_config import BotConfig
from strategy.trend_bos import tag_waves_hh_hl_pass


def _cfg(**kw) -> BotConfig:
    base = dict(
        wave_min_pct=0.26,
        min_opp_bars=3,
        trend_filter_enabled=True,
        trend_hh_hl_filter_enabled=True,
    )
    base.update(kw)
    return BotConfig(**base)


def _flat_df(n: int = 40) -> pd.DataFrame:
    times = pd.date_range("2026-03-01 00:00", periods=n, freq="30min")
    return pd.DataFrame(
        {
            "time": times,
            "open": 1.10,
            "high": 1.12,
            "low": 1.08,
            "close": 1.11,
        }
    )


def _wave(
    wt: str,
    *,
    wdir: int,
    draw_right: int,
    box_top: float,
    box_bottom: float,
) -> dict:
    return {
        "wave_time": wt,
        "dir": wdir,
        "draw_right": draw_right,
        "draw_left": draw_right - 3,
        "box_top": box_top,
        "box_bottom": box_bottom,
    }


def test_hh_hl_pass_flag_bull_trend_with_counter_and_sum_waves():
    df = _flat_df()
    waves = [
        _wave("up1", wdir=1, draw_right=5, box_top=1.10, box_bottom=1.05),
        _wave("dn1", wdir=-1, draw_right=10, box_top=1.09, box_bottom=1.04),
        _wave("up2", wdir=1, draw_right=15, box_top=1.15, box_bottom=1.08),
        _wave("dn2", wdir=-1, draw_right=20, box_top=1.08, box_bottom=1.03),
        _wave("up3", wdir=1, draw_right=25, box_top=1.12, box_bottom=1.06),
    ]

    tag_waves_hh_hl_pass(df, waves, _cfg())

    by_time = {str(w["wave_time"]): w for w in waves}
    assert by_time["up1"]["hh_hl_pass"] is True
    assert by_time["dn1"]["hh_hl_pass"] is False
    assert by_time["up2"]["hh_hl_pass"] is True
    assert by_time["dn2"]["hh_hl_pass"] is False
    assert by_time["up3"]["hh_hl_pass"] is False


def test_hh_hl_pass_all_true_when_filter_disabled():
    df = _flat_df()
    waves = [
        _wave("up1", wdir=1, draw_right=5, box_top=1.10, box_bottom=1.05),
        _wave("dn1", wdir=-1, draw_right=10, box_top=1.09, box_bottom=1.04),
    ]

    tag_waves_hh_hl_pass(df, waves, _cfg(trend_hh_hl_filter_enabled=False))

    assert all(w["hh_hl_pass"] is True for w in waves)


def test_wf_reference_relaxes_secondary_hh_hl_check_for_continuation():
    """Po WF continuation vlne smi nasledujici trend-vlna projit uz jen na novem
    extremu (LL u DOWN / HH u UP). WF box edge na opacne strane je fakeout WICK,
    takze sekundarni podminka (LH/HL) vuci nemu by jinak utla pravou trend-vlnu
    o par pipu (regrese: 202603301800 bear pokracovani po WF na low)."""
    from strategy.trend_bos import TrendState, _wave_passes_hh_hl_structure_live

    # DOWN pokracovani: nove LL (1.14426 < 1.14875), ale box_top 1.15208 je
    # MARGINALNE NAD WF pivotem 1.15203 → klasicky LH check by selhal.
    down_cont = {"dir": -1, "box_top": 1.15208, "box_bottom": 1.14426}

    state_wf = TrendState(
        direction="bear",
        last_down_box_top=1.15203,
        last_down_box_bottom=1.14875,
        last_down_from_wf=True,
    )
    assert _wave_passes_hh_hl_structure_live(state_wf, down_cont) is True

    # Stejna konstelace bez WF reference → prisny LH check ji zahodi.
    state_plain = TrendState(
        direction="bear",
        last_down_box_top=1.15203,
        last_down_box_bottom=1.14875,
        last_down_from_wf=False,
    )
    assert _wave_passes_hh_hl_structure_live(state_plain, down_cont) is False

    # I s WF referenci musi vlna udelat novy extrem — vyssi low (higher low) neprojde.
    higher_low = {"dir": -1, "box_top": 1.15000, "box_bottom": 1.14900}
    assert _wave_passes_hh_hl_structure_live(state_wf, higher_low) is False


def test_wave_passes_hh_hl_reads_cached_flag():
    from strategy.trend_bos import TrendState, _wave_passes_hh_hl_structure

    wave = {
        "dir": 1,
        "box_top": 1.05,
        "box_bottom": 1.00,
        "hh_hl_pass": False,
    }
    state = TrendState(
        direction="bull",
        last_up_box_top=1.10,
        last_up_box_bottom=1.02,
    )
    assert _wave_passes_hh_hl_structure(state, wave) is False


def test_sub_min_pct_ripple_does_not_update_bos_swing():
    """Pine ripple pod wave_min_pct nesmi prepisovat BOS swing (Jun 5 2025 regrese)."""
    from strategy.trend_bos import TrendState, should_update_trend_state_for_wave

    cfg = _cfg()
    state = TrendState(
        direction="bull",
        last_up_wave_time="202506041930",
        last_up_box_top=1.14341,
        last_up_box_bottom=1.13745,
    )
    ripple = {
        "dir": 1,
        "wave_time": "202506050400",
        "move_pct": 0.21,
        "box_top": 1.14344,
        "box_bottom": 1.14098,
        "hh_hl_pass": True,
    }
    assert should_update_trend_state_for_wave(state, ripple, cfg) is False

    structural = {
        "dir": 1,
        "wave_time": "202506051600",
        "move_pct": 0.79,
        "box_top": 1.14946,
        "box_bottom": 1.14041,
        "hh_hl_pass": True,
    }
    assert should_update_trend_state_for_wave(state, structural, cfg) is True
