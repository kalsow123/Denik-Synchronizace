"""Test post-EXT confirmed trend lock."""
from __future__ import annotations

import pandas as pd

from config.bot_config import BotConfig
from config.enums import TPMode
from backtest.engine import BacktestEngine


def _cfg(**kw) -> BotConfig:
    base = dict(
        wave_min_pct=0.26,
        min_opp_bars=3,
        trend_filter_enabled=True,
        trend_hh_hl_filter_enabled=True,
        tp_mode=TPMode.WAVE_TARGET_N,
        ext_enabled=True,
        ext_wave_min_pct=0.76,
        ext_post_confirmed_trend_count=2,
        ext_post_confirmed_trend_lock_enabled=True,
        ext_post_confirmed_trend_lock_blocks_both_sides=True,
        ext_trade_both_sides_in_range=True,
        max_wave_age_hours=24,
    )
    base.update(kw)
    return BotConfig(**base)


def test_post_ext_confirmed_lock_blocks_subsequent_waves():
    """
    Sekvence: EXT-UP -> UP (1) -> DOWN (1) -> UP (2) -> DOWN -> UP.
    Po UP (2) se trend potvrdi a vsechny dalsi vlny maji lock=True.
    """
    cfg = _cfg()
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    # Vybereme obdobi, kde vime, ze mame EXT UP a nasledne vlny
    # (pouzito obdobi z test_ext_range.py)
    df = df[(df["time"] >= "2026-03-22") & (df["time"] <= "2026-03-26 23:59:59")].reset_index(
        drop=True
    )
    eng = BacktestEngine(cfg)
    eng.run(df, retain_wave_snapshot=True)

    by_wt = {str(w["wave_time"]): w for w in eng.last_waves}
    print([w["wave_time"] for w in eng.last_waves])
    
    wt_ext = "202603231730"
    wt_dn1 = "202603231900"
    wt_up1 = "202603232130"
    wt_dn2 = "202603240700"
    wt_up_locked = "202603241000"
    wt_dn_locked = "202603241200"

    assert by_wt[wt_ext].get("is_ext") is True
    assert by_wt[wt_dn1].get("post_ext_confirmed_trend_lock") is False
    assert by_wt[wt_up1].get("post_ext_confirmed_trend_lock") is False
    assert by_wt[wt_dn2].get("post_ext_confirmed_trend_lock") is False
    
    # Vlny po DN (2) musi byt zamcene
    assert by_wt[wt_up_locked].get("post_ext_confirmed_trend_lock") is True
    assert by_wt[wt_dn_locked].get("post_ext_confirmed_trend_lock") is True

    # Overime, ze html vykreslovac by je skryl
    from backtest.waves_plotly_figure import _wave_visible_in_html_plot
    assert _wave_visible_in_html_plot(by_wt[wt_up_locked], cfg) is False
    assert _wave_visible_in_html_plot(by_wt[wt_dn_locked], cfg) is False

    # Overime, ze wave_allowed_for_entry by pro ne v live_loopu vratilo false
    from strategy.trend_bos import wave_allowed_for_entry
    allowed, reason = wave_allowed_for_entry(by_wt[wt_up_locked], None, cfg)
    assert allowed is False
    assert reason == "post_ext_confirmed_lock"
