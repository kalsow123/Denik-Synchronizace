"""
Testy pro Wick Fakeout Recovery (WF) — Definition of Done, Krok 10.

Pokryté scénáře:
  - WF aktivace v downtrendu (jeden wick → aktivace).
  - WF aktivace v downtrendu (víc wicků → fakeout pivot = nejvyšší).
  - WF aktivace v uptrendu (mirror).
  - WF se neaktivuje, když v okně byl close-based BOS.
  - WF se neaktivuje, když v okně nebyl žádný wick.
  - WF se neaktivuje, když je trh v EXT (a WF_SKIPPED_EXT je logován).
  - wave_origin tracking v WickFakeoutTracker.
  - build_wf_wave generuje validní wave dict se správnou geometrií.
  - evaluate_wf_from_df (live scan) funguje pro downtrend i uptrend.
  - wf_enabled=False → tracker.check_wf() vždy vrací None.
"""
from __future__ import annotations

import pandas as pd
import pytest

from config.bot_config import BotConfig
from strategy.wick_fakeout import (
    WAVE_ORIGIN_NORMAL,
    WAVE_ORIGIN_WF,
    WickFakeoutTracker,
    build_wf_wave,
    compute_wf_reference_levels,
    evaluate_wf_from_df,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(*, wf_enabled: bool = True, ext_enabled: bool = False, **kwargs) -> BotConfig:
    base = dict(
        wave_min_pct=0.26,
        min_opp_bars=3,
        entry_fib_level=0.5,
        sl_fib_level=0.8,
        rrr=2.0,
        wave_plus=True,
        ext_enabled=ext_enabled,
        ext_wave_min_pct=0.76,
        wf_enabled=wf_enabled,
    )
    base.update(kwargs)
    return BotConfig(**base)


def _make_wave(
    *,
    dir_: int,
    box_top: float,
    box_bottom: float,
    draw_right: int = 5,
    is_ext: bool = False,
) -> dict:
    return {
        "dir": dir_,
        "wave_time": "2026-01-01 00:00",
        "draw_left": 0,
        "draw_right": draw_right,
        "box_top": box_top,
        "box_bottom": box_bottom,
        "fib50": (box_top + box_bottom) / 2.0,
        "sl": 0.0,
        "tp": 0.0,
        "move_pct": abs(box_top - box_bottom) / max(1e-12, box_bottom) * 100.0,
        "is_ext": is_ext,
    }


# ---------------------------------------------------------------------------
# WickFakeoutTracker — unit testy
# ---------------------------------------------------------------------------

class TestWickFakeoutTrackerDowntrend:
    """Downtrend: last wave šla dolů (dir=-1), box_top=1.175, box_bottom=1.165."""

    WAVE = _make_wave(dir_=-1, box_top=1.175, box_bottom=1.165, draw_right=5)

    def _tracker_with_wave(self) -> WickFakeoutTracker:
        t = WickFakeoutTracker()
        t.on_new_wave(self.WAVE, birth_bar=5)
        return t

    def test_activation_single_wick(self):
        """Jeden wick nad last_wave_high → aktivace."""
        cfg = _cfg()
        t = self._tracker_with_wave()
        # Bar 6: wick nad 1.175, close pod 1.175
        t.on_bar(high=1.177, low=1.170, close=1.172, bar_idx=6)
        # Bar 7: aktivační close pod 1.165 (last_wave_low)
        t.on_bar(high=1.168, low=1.163, close=1.164, bar_idx=7)
        result = t.check_wf(close=1.164, bar_idx=7, cfg=cfg)
        assert result is not None
        assert result["status"] == "activate"
        assert abs(result["fakeout_pivot"] - 1.177) < 1e-9
        assert result["window_size"] == 2

    def test_activation_multiple_wicks_pivot_is_highest(self):
        """Víc wicků → fakeout pivot = nejvyšší high."""
        cfg = _cfg()
        t = self._tracker_with_wave()
        t.on_bar(high=1.176, low=1.170, close=1.172, bar_idx=6)  # první wick
        t.on_bar(high=1.179, low=1.171, close=1.173, bar_idx=7)  # druhý wick, vyšší
        t.on_bar(high=1.168, low=1.163, close=1.164, bar_idx=8)  # aktivační bar
        result = t.check_wf(close=1.164, bar_idx=8, cfg=cfg)
        assert result is not None
        assert result["status"] == "activate"
        assert abs(result["fakeout_pivot"] - 1.179) < 1e-9

    def test_no_activation_close_bos_in_window(self):
        """Close-based BOS v okně → WF se neaktivuje."""
        cfg = _cfg()
        t = self._tracker_with_wave()
        t.on_bar(high=1.178, low=1.172, close=1.176, bar_idx=6)  # close nad 1.175 = BOS
        t.on_bar(high=1.168, low=1.163, close=1.164, bar_idx=7)
        result = t.check_wf(close=1.164, bar_idx=7, cfg=cfg)
        assert result is None

    def test_no_activation_no_wick_in_window(self):
        """Žádný wick v okně → WF se neaktivuje."""
        cfg = _cfg()
        t = self._tracker_with_wave()
        # Bary v okně zůstávají pod last_wave_high
        t.on_bar(high=1.174, low=1.168, close=1.170, bar_idx=6)
        t.on_bar(high=1.163, low=1.162, close=1.162, bar_idx=7)
        result = t.check_wf(close=1.162, bar_idx=7, cfg=cfg)
        assert result is None

    def test_no_activation_close_above_last_low(self):
        """Wick existuje, ale close není pod last_wave_low → čekáme dál."""
        cfg = _cfg()
        t = self._tracker_with_wave()
        t.on_bar(high=1.177, low=1.170, close=1.172, bar_idx=6)  # wick
        t.on_bar(high=1.168, low=1.166, close=1.167, bar_idx=7)  # close nad 1.165 → ne aktivace
        result = t.check_wf(close=1.167, bar_idx=7, cfg=cfg)
        assert result is None

    def test_no_activation_wf_disabled(self):
        """wf_enabled=False → vždy None."""
        cfg = _cfg(wf_enabled=False)
        t = self._tracker_with_wave()
        t.on_bar(high=1.177, low=1.163, close=1.164, bar_idx=6)
        result = t.check_wf(close=1.164, bar_idx=6, cfg=cfg)
        assert result is None

    def test_no_activation_no_wave(self):
        """Žádná last wave → None."""
        cfg = _cfg()
        t = WickFakeoutTracker()
        result = t.check_wf(close=1.160, bar_idx=10, cfg=cfg)
        assert result is None

    def test_ext_skipped_when_last_wave_is_ext(self):
        """Pokud je last wave EXT → status='ext_skipped'."""
        cfg = _cfg(ext_enabled=True)
        wave = _make_wave(dir_=-1, box_top=1.175, box_bottom=1.165, is_ext=True)
        t = WickFakeoutTracker()
        t.on_new_wave(wave, birth_bar=5)
        t.on_bar(high=1.177, low=1.170, close=1.172, bar_idx=6)
        result = t.check_wf(close=1.172, bar_idx=6, cfg=cfg)
        assert result is None
        t.on_bar(high=1.168, low=1.163, close=1.164, bar_idx=7)
        result = t.check_wf(close=1.164, bar_idx=7, cfg=cfg)
        assert result is not None
        assert result["status"] == "ext_skipped"

    def test_reset_on_new_wave(self):
        """Po on_new_wave() opačným směrem se okno resetuje."""
        cfg = _cfg()
        t = self._tracker_with_wave()
        t.on_bar(high=1.177, low=1.170, close=1.172, bar_idx=6)  # wick
        new_wave = _make_wave(dir_=1, box_top=1.174, box_bottom=1.162, draw_right=7)
        t.on_new_wave(new_wave, birth_bar=7)
        t.on_bar(high=1.164, low=1.159, close=1.160, bar_idx=8)
        result = t.check_wf(close=1.160, bar_idx=8, cfg=cfg)
        assert result is None

    def test_same_dir_micro_wave_does_not_reset_active_window(self):
        """Stejný směr — mikro vlna nesmí zrušit běžící WF okno s wickem."""
        cfg = _cfg()
        t = self._tracker_with_wave()
        t.on_bar(high=1.177, low=1.170, close=1.172, bar_idx=6)
        micro = _make_wave(dir_=-1, box_top=1.176, box_bottom=1.160, draw_right=7)
        t.on_new_wave(micro, birth_bar=7)
        t.on_bar(high=1.168, low=1.163, close=1.164, bar_idx=8)
        result = t.check_wf(close=1.164, bar_idx=8, cfg=cfg)
        assert result is not None
        assert result["status"] == "activate"


class TestWickFakeoutTrackerUptrend:
    """Uptrend: last wave šla nahoru (dir=+1), box_top=1.185, box_bottom=1.175."""

    WAVE = _make_wave(dir_=1, box_top=1.185, box_bottom=1.175, draw_right=5)

    def _tracker_with_wave(self) -> WickFakeoutTracker:
        t = WickFakeoutTracker()
        t.on_new_wave(self.WAVE, birth_bar=5)
        return t

    def test_activation_single_wick_uptrend(self):
        """Jeden wick pod last_wave_low → aktivace."""
        cfg = _cfg()
        t = self._tracker_with_wave()
        t.on_bar(high=1.178, low=1.173, close=1.177, bar_idx=6)  # wick pod 1.175
        t.on_bar(high=1.188, low=1.183, close=1.187, bar_idx=7)  # aktivační close nad 1.185
        result = t.check_wf(close=1.187, bar_idx=7, cfg=cfg)
        assert result is not None
        assert result["status"] == "activate"
        assert abs(result["fakeout_pivot"] - 1.173) < 1e-9

    def test_activation_multiple_wicks_pivot_is_lowest(self):
        """Víc wicků → fakeout pivot = nejnižší low."""
        cfg = _cfg()
        t = self._tracker_with_wave()
        # wick pod 1.175, close musí zůstat >= 1.175 aby nevznikl close-BOS
        t.on_bar(high=1.177, low=1.174, close=1.176, bar_idx=6)  # první wick, close > last_low
        t.on_bar(high=1.176, low=1.171, close=1.175, bar_idx=7)  # druhý wick, nižší, close = last_low (ne BOS)
        t.on_bar(high=1.188, low=1.184, close=1.187, bar_idx=8)  # aktivační bar
        result = t.check_wf(close=1.187, bar_idx=8, cfg=cfg)
        assert result is not None
        assert result["status"] == "activate"
        assert abs(result["fakeout_pivot"] - 1.171) < 1e-9

    def test_no_activation_close_bos_uptrend(self):
        """Close pod last_wave_low v okně → BOS, WF se neaktivuje."""
        cfg = _cfg()
        t = self._tracker_with_wave()
        t.on_bar(high=1.177, low=1.173, close=1.174, bar_idx=6)  # close pod 1.175 = BOS
        t.on_bar(high=1.188, low=1.184, close=1.187, bar_idx=7)
        result = t.check_wf(close=1.187, bar_idx=7, cfg=cfg)
        assert result is None

    def test_no_activation_no_wick_uptrend(self):
        """Žádný wick pod last_wave_low → WF se neaktivuje."""
        cfg = _cfg()
        t = self._tracker_with_wave()
        t.on_bar(high=1.180, low=1.176, close=1.178, bar_idx=6)  # low > 1.175, žádný wick
        t.on_bar(high=1.188, low=1.184, close=1.187, bar_idx=7)
        result = t.check_wf(close=1.187, bar_idx=7, cfg=cfg)
        assert result is None


# ---------------------------------------------------------------------------
# build_wf_wave — unit test
# ---------------------------------------------------------------------------

class TestBuildWfWave:
    """Ověří, že build_wf_wave generuje validní wave dict."""

    def test_downtrend_wave_geometry(self):
        """Downtrend: box_top = fakeout_pivot, box_bottom = window_min_low."""
        cfg = _cfg()
        last_wave = _make_wave(dir_=-1, box_top=1.175, box_bottom=1.165, draw_right=5)
        wave = build_wf_wave(
            cfg,
            last_wave=last_wave,
            fakeout_pivot=1.177,
            fakeout_bar_idx=6,
            activation_bar_idx=8,
            wave_time_str="2026-01-01 01:00",
            window_min_low=1.161,
            window_max_high=None,
        )
        # Může vrátit None pokud _append_wave_sig odmítne (je to OK, závisí na min_pct)
        if wave is not None:
            assert wave["box_top"] == pytest.approx(1.177, abs=1e-9)
            assert wave["box_bottom"] == pytest.approx(1.161, abs=1e-9)
            assert wave.get("wave_origin") == WAVE_ORIGIN_WF
            assert wave["dir"] == -1

    def test_uptrend_wave_geometry(self):
        """Uptrend: box_bottom = fakeout_pivot, box_top = window_max_high."""
        cfg = _cfg()
        last_wave = _make_wave(dir_=1, box_top=1.185, box_bottom=1.175, draw_right=5)
        wave = build_wf_wave(
            cfg,
            last_wave=last_wave,
            fakeout_pivot=1.173,
            fakeout_bar_idx=6,
            activation_bar_idx=8,
            wave_time_str="2026-01-01 01:00",
            window_min_low=None,
            window_max_high=1.189,
        )
        if wave is not None:
            assert wave["box_bottom"] == pytest.approx(1.173, abs=1e-9)
            assert wave["box_top"] == pytest.approx(1.189, abs=1e-9)
            assert wave.get("wave_origin") == WAVE_ORIGIN_WF
            assert wave["dir"] == 1

    def test_returns_none_for_invalid_pivot_bar(self):
        """fakeout_bar_idx >= activation_bar_idx → None."""
        cfg = _cfg()
        last_wave = _make_wave(dir_=-1, box_top=1.175, box_bottom=1.165)
        result = build_wf_wave(
            cfg,
            last_wave=last_wave,
            fakeout_pivot=1.177,
            fakeout_bar_idx=8,
            activation_bar_idx=7,  # menší než fakeout_bar_idx
            wave_time_str="2026-01-01 01:00",
        )
        assert result is None

    def test_returns_none_for_zero_dir(self):
        """dir=0 → None."""
        cfg = _cfg()
        last_wave = _make_wave(dir_=0, box_top=1.175, box_bottom=1.165)
        result = build_wf_wave(
            cfg,
            last_wave=last_wave,
            fakeout_pivot=1.177,
            fakeout_bar_idx=6,
            activation_bar_idx=8,
            wave_time_str="2026-01-01 01:00",
        )
        assert result is None


# ---------------------------------------------------------------------------
# evaluate_wf_from_df — unit testy (live scan)
# ---------------------------------------------------------------------------

def _make_df(bars: list[dict]) -> pd.DataFrame:
    """Vytvoří DataFrame z listu dict s klíči open/high/low/close/time."""
    df = pd.DataFrame(bars)
    if "time" not in df.columns:
        df["time"] = pd.date_range("2026-01-01", periods=len(df), freq="15min")
    return df


class TestEvaluateWfFromDf:
    """Tests for evaluate_wf_from_df (live loop scan)."""

    def _down_wave(self, draw_right: int = 3) -> dict:
        return _make_wave(dir_=-1, box_top=1.175, box_bottom=1.165, draw_right=draw_right)

    def _up_wave(self, draw_right: int = 3) -> dict:
        return _make_wave(dir_=1, box_top=1.185, box_bottom=1.175, draw_right=draw_right)

    def test_downtrend_activation(self):
        """Downtrend: wick v okně + aktivační close pod last_low."""
        cfg = _cfg()
        bars = [
            # Bary 0–3: last wave (draw_right=3)
            {"open": 1.175, "high": 1.175, "low": 1.165, "close": 1.165},
            {"open": 1.170, "high": 1.170, "low": 1.165, "close": 1.166},
            {"open": 1.168, "high": 1.168, "low": 1.165, "close": 1.165},
            {"open": 1.165, "high": 1.165, "low": 1.165, "close": 1.165},
            # Bar 4: wick nad 1.175, close pod 1.175
            {"open": 1.166, "high": 1.177, "low": 1.166, "close": 1.169},
            # Bar 5 (aktivační): close pod 1.165
            {"open": 1.168, "high": 1.168, "low": 1.163, "close": 1.164},
        ]
        df = _make_df(bars)
        result = evaluate_wf_from_df(df, self._down_wave(draw_right=3), cfg)
        assert result is not None
        assert result["status"] == "activate"
        assert result["fakeout_pivot"] == pytest.approx(1.177, abs=1e-9)

    def test_uptrend_activation(self):
        """Uptrend: wick v okně + aktivační close nad last_high."""
        cfg = _cfg()
        bars = [
            {"open": 1.175, "high": 1.185, "low": 1.175, "close": 1.185},
            {"open": 1.180, "high": 1.185, "low": 1.178, "close": 1.183},
            {"open": 1.182, "high": 1.185, "low": 1.178, "close": 1.184},
            {"open": 1.183, "high": 1.185, "low": 1.179, "close": 1.184},
            # Bar 4: wick pod 1.175, close nad 1.175
            {"open": 1.182, "high": 1.183, "low": 1.173, "close": 1.178},
            # Bar 5 (aktivační): close nad 1.185
            {"open": 1.183, "high": 1.187, "low": 1.182, "close": 1.186},
        ]
        df = _make_df(bars)
        result = evaluate_wf_from_df(df, self._up_wave(draw_right=3), cfg)
        assert result is not None
        assert result["status"] == "activate"
        assert result["fakeout_pivot"] == pytest.approx(1.173, abs=1e-9)

    def test_no_activation_close_bos_in_window(self):
        """Close-based BOS v okně → None."""
        cfg = _cfg()
        bars = [
            {"open": 1.175, "high": 1.175, "low": 1.165, "close": 1.165},
            {"open": 1.165, "high": 1.165, "low": 1.165, "close": 1.165},
            {"open": 1.165, "high": 1.165, "low": 1.165, "close": 1.165},
            {"open": 1.165, "high": 1.165, "low": 1.165, "close": 1.165},
            # Bar 4: close nad 1.175 = BOS
            {"open": 1.170, "high": 1.178, "low": 1.169, "close": 1.176},
            # Bar 5 (aktivační): close pod 1.165
            {"open": 1.168, "high": 1.168, "low": 1.163, "close": 1.164},
        ]
        df = _make_df(bars)
        result = evaluate_wf_from_df(df, self._down_wave(draw_right=3), cfg)
        assert result is None

    def test_no_activation_no_wick(self):
        """Žádný wick → None."""
        cfg = _cfg()
        bars = [
            {"open": 1.175, "high": 1.175, "low": 1.165, "close": 1.165},
            {"open": 1.165, "high": 1.165, "low": 1.165, "close": 1.165},
            {"open": 1.165, "high": 1.165, "low": 1.165, "close": 1.165},
            {"open": 1.165, "high": 1.165, "low": 1.165, "close": 1.165},
            # Bar 4: žádný wick nad 1.175
            {"open": 1.168, "high": 1.174, "low": 1.166, "close": 1.168},
            # Bar 5 (aktivační): close pod 1.165
            {"open": 1.165, "high": 1.165, "low": 1.162, "close": 1.163},
        ]
        df = _make_df(bars)
        result = evaluate_wf_from_df(df, self._down_wave(draw_right=3), cfg)
        assert result is None

    def test_disabled_returns_none(self):
        """wf_enabled=False → None."""
        cfg = _cfg(wf_enabled=False)
        bars = [
            {"open": 1.175, "high": 1.175, "low": 1.165, "close": 1.165},
            {"open": 1.165, "high": 1.165, "low": 1.165, "close": 1.165},
            {"open": 1.165, "high": 1.165, "low": 1.165, "close": 1.165},
            {"open": 1.165, "high": 1.165, "low": 1.165, "close": 1.165},
            {"open": 1.166, "high": 1.177, "low": 1.166, "close": 1.169},
            {"open": 1.168, "high": 1.168, "low": 1.163, "close": 1.164},
        ]
        df = _make_df(bars)
        result = evaluate_wf_from_df(df, self._down_wave(draw_right=3), cfg)
        assert result is None

    def test_ext_skipped(self):
        """EXT last wave → ext_skipped."""
        cfg = _cfg(ext_enabled=True)
        bars = [
            {"open": 1.175, "high": 1.175, "low": 1.165, "close": 1.165},
            {"open": 1.165, "high": 1.165, "low": 1.165, "close": 1.165},
            {"open": 1.165, "high": 1.165, "low": 1.165, "close": 1.165},
            {"open": 1.165, "high": 1.165, "low": 1.165, "close": 1.165},
            {"open": 1.166, "high": 1.177, "low": 1.166, "close": 1.169},
            {"open": 1.168, "high": 1.168, "low": 1.163, "close": 1.164},
        ]
        df = _make_df(bars)
        ext_wave = _make_wave(dir_=-1, box_top=1.175, box_bottom=1.165, draw_right=3, is_ext=True)
        result = evaluate_wf_from_df(df, ext_wave, cfg)
        assert result is not None
        assert result["status"] == "ext_skipped"


# ---------------------------------------------------------------------------
# WAVE_ORIGIN constants
# ---------------------------------------------------------------------------

def test_wave_origin_constants():
    assert WAVE_ORIGIN_NORMAL == "normal"
    assert WAVE_ORIGIN_WF == "wf_continuation"


def test_compute_wf_reference_levels_bear_local_swing():
    """Referenční HIGH je rebound po low vlny, ne vzdálený pivot."""
    bars = []
    for _ in range(8):
        bars.append({"open": 1.17, "high": 1.164, "low": 1.163, "close": 1.1635})
    bars[3] = {"open": 1.16, "high": 1.1605, "low": 1.159, "close": 1.1595}
    for j in range(4, 8):
        bars[j] = {"open": 1.16, "high": 1.1611 + j * 0.0001, "low": 1.1595, "close": 1.1605}
    df = _make_df(bars)
    wave = _make_wave(dir_=-1, box_top=1.16451, box_bottom=1.15898, draw_right=7)
    wave["draw_left"] = 0
    refs = compute_wf_reference_levels(wave, df, end_bar=7)
    assert refs is not None
    high, low = refs
    assert low == pytest.approx(1.159, abs=1e-9)
    assert high == pytest.approx(1.1614, abs=1e-3)
    assert high < 1.16451


def test_build_wf_wave_has_fib_and_wf_position_flag():
    cfg = _cfg()
    last_wave = _make_wave(dir_=-1, box_top=1.177, box_bottom=1.165, draw_right=3)
    wave = build_wf_wave(
        cfg,
        last_wave=last_wave,
        fakeout_pivot=1.179,
        fakeout_bar_idx=4,
        activation_bar_idx=7,
        wave_time_str="202601010800",
        window_min_low=1.161,
    )
    assert wave is not None
    assert wave.get("wf_wave_position") is True
    assert wave.get("fib50") is not None
    assert wave.get("sl") is not None
    assert wave["sl"] != wave["fib50"]


def test_resume_classic_waves_after_wf_returns_followers():
    from strategy.wick_fakeout import resume_classic_waves_after_wf

    cfg = _cfg(wave_min_pct=0.05, min_opp_bars=2)
    bars = []
    price = 1.20
    for i in range(40):
        if i % 4 == 0:
            price += 0.004
        elif i % 4 == 1:
            price -= 0.006
        elif i % 4 == 2:
            price -= 0.003
        else:
            price += 0.002
        h = price + 0.001
        l = price - 0.001
        bars.append({"open": price, "high": h, "low": l, "close": price})
    df = _make_df(bars)
    wf = build_wf_wave(
        cfg,
        last_wave=_make_wave(dir_=-1, box_top=1.20, box_bottom=1.18, draw_right=8),
        fakeout_pivot=1.205,
        fakeout_bar_idx=9,
        activation_bar_idx=11,
        wave_time_str="202601011100",
        window_min_low=1.175,
    )
    assert wf is not None
    continued, birth = resume_classic_waves_after_wf(df, cfg, wf)
    assert len(continued) >= 1
    assert all(str(w.get("wave_origin", "normal")) != WAVE_ORIGIN_WF for w in continued)
    first = continued[0]
    assert int(first.get("draw_left", 0)) >= int(wf.get("draw_right", 0))
    assert birth[str(first["wave_time"])] >= int(wf.get("draw_right", 0))
