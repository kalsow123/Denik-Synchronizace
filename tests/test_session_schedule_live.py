"""Kontrola session casu LIVE_BOT_CONFIG (broker time)."""

from datetime import datetime, timezone

from config.bot_config import LIVE_BOT_CONFIG
from infra.session_manager import (
    is_in_session,
    is_pre_close_buffer,
    is_week_close_pre_buffer,
)


def _dt(y, m, d, h, mi, wd=None):
    dt = datetime(y, m, d, h, mi, tzinfo=timezone.utc)
    if wd is not None:
        assert dt.weekday() == wd
    return dt


def test_daily_session_crosses_midnight():
    cfg = LIVE_BOT_CONFIG
    # Pondeli 00:30 — jeste pred open 23:05 predchoziho dne? 
    # open 23:05 -> session bezi do 21:45; v 00:30 Po jsme v session (od Ne 23:05)
    assert is_in_session(cfg, _dt(2026, 6, 15, 0, 30, wd=0))
    assert is_in_session(cfg, _dt(2026, 6, 15, 10, 0, wd=0))
    assert not is_in_session(cfg, _dt(2026, 6, 15, 22, 0, wd=0))


def test_pre_close_buffer_daily():
    cfg = LIVE_BOT_CONFIG
    # 21:42 = 3 min pred close 21:45
    assert is_pre_close_buffer(cfg, _dt(2026, 6, 17, 21, 42, wd=2))
    assert not is_pre_close_buffer(cfg, _dt(2026, 6, 17, 21, 30, wd=2))


def test_weekly_break_friday_to_sunday():
    cfg = LIVE_BOT_CONFIG
    # Patek 22:00 — po week close 21:45
    assert not is_in_session(cfg, _dt(2026, 6, 19, 22, 0, wd=4))
    # Sobota
    assert not is_in_session(cfg, _dt(2026, 6, 20, 12, 0, wd=5))
    # Nedele pred open 23:05
    assert not is_in_session(cfg, _dt(2026, 6, 21, 20, 0, wd=6))
    # Nedele po open
    assert is_in_session(cfg, _dt(2026, 6, 21, 23, 30, wd=6))


def test_friday_pre_close_flag():
    cfg = LIVE_BOT_CONFIG
    assert is_week_close_pre_buffer(cfg, _dt(2026, 6, 19, 21, 42, wd=4))
