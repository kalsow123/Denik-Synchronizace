"""Filtr viditelnosti vln v Plotly HTML (hh_hl_pass, post-EXT lock)."""
from __future__ import annotations

from config.bot_config import BotConfig
from config.enums import TPMode
from backtest.waves_plotly_figure import _wave_visible_in_html_plot
from strategy.wick_fakeout import WAVE_ORIGIN_WF


def _cfg(**kw) -> BotConfig:
    base = dict(
        wave_min_pct=0.26,
        min_opp_bars=3,
        trend_filter_enabled=True,
        trend_hh_hl_filter_enabled=True,
        tp_mode=TPMode.WAVE_TARGET_N,
        ext_enabled=True,
        ext_wave_min_pct=0.76,
    )
    base.update(kw)
    return BotConfig(**base)


def test_hides_sum_wave_when_hh_hl_fail():
    w = {
        "wave_time": "202603010000",
        "dir": 1,
        "hh_hl_pass": False,
    }
    assert _wave_visible_in_html_plot(w, _cfg()) is False


def test_shows_bos_wave_even_when_hh_hl_fail():
    w = {
        "wave_time": "202603010000",
        "dir": -1,
        "hh_hl_pass": False,
    }
    assert _wave_visible_in_html_plot(
        w, _cfg(), bos_wave_times={"202603010000"}
    ) is True


def test_hides_post_ext_suppressed():
    w = {
        "wave_time": "202603010000",
        "dir": 1,
        "hh_hl_pass": True,
        "post_ext_trend_suppressed": True,
    }
    assert _wave_visible_in_html_plot(w, _cfg()) is False


def test_shows_wf_and_two_sided_exceptions():
    wf = {"wave_time": "wf1", "dir": 1, "hh_hl_pass": False, "wave_origin": WAVE_ORIGIN_WF}
    ts = {
        "wave_time": "ts1",
        "dir": -1,
        "hh_hl_pass": False,
        "two_sided_show": True,
    }
    assert _wave_visible_in_html_plot(wf, _cfg()) is True
    assert _wave_visible_in_html_plot(ts, _cfg()) is True


def test_bos_wave_visible_even_when_post_ext_suppressed():
    w = {
        "wave_time": "202603050800",
        "dir": -1,
        "hh_hl_pass": False,
        "post_ext_trend_suppressed": True,
    }
    assert _wave_visible_in_html_plot(
        w, _cfg(), bos_wave_times={"202603050800"}
    ) is True


def test_hides_wf_continued_classic_when_hh_hl_fail():
    w = {
        "wave_time": "202603010000",
        "dir": 1,
        "hh_hl_pass": False,
        "wf_continued_classic": True,
    }
    assert _wave_visible_in_html_plot(w, _cfg()) is False


def test_filter_off_shows_sum_wave():
    w = {"wave_time": "x", "dir": 1, "hh_hl_pass": False}
    assert _wave_visible_in_html_plot(w, _cfg(trend_hh_hl_filter_enabled=False)) is True
