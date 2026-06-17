"""max_wave_age_hours — parita live (ref_time = last bar) vs backtest engine."""

from datetime import datetime, timedelta

from config.bot_config import BotConfig
from strategy.filters import is_wave_too_old


def test_not_too_old_within_limit():
    cfg = BotConfig(max_wave_age_hours=20)
    wave = "202601011200"
    ref = datetime(2026, 1, 1, 12, 0) + timedelta(hours=19, minutes=59)
    assert not is_wave_too_old(wave, cfg, ref_time=ref)


def test_too_old_past_limit():
    cfg = BotConfig(max_wave_age_hours=20)
    wave = "202601011200"
    ref = datetime(2026, 1, 1, 12, 0) + timedelta(hours=20, minutes=1)
    assert is_wave_too_old(wave, cfg, ref_time=ref)


def test_wall_clock_differs_from_bar_time():
    """Stejna vlna: podle baru OK, podle pozdejsiho wall clock uz stara."""
    cfg = BotConfig(max_wave_age_hours=20)
    wave = "202601011200"
    bar_ref = datetime(2026, 1, 1, 12, 0) + timedelta(hours=10)
    wall_ref = datetime(2026, 1, 1, 12, 0) + timedelta(hours=25)
    assert not is_wave_too_old(wave, cfg, ref_time=bar_ref)
    assert is_wave_too_old(wave, cfg, ref_time=wall_ref)
