"""Kontrola session casu LIVE_BOT_CONFIG (GMT+3 / UTC+3 pro on/off)."""

from datetime import datetime, timezone, timedelta

from config.bot_config import LIVE_BOT_CONFIG
from infra.session_manager import (
    get_session_now,
    is_in_session,
    is_pre_close_buffer,
    is_week_close_pre_buffer,
)

GMT3 = timezone(timedelta(hours=3))


def _dt(y, m, d, h, mi, wd=None):
    dt = datetime(y, m, d, h, mi, tzinfo=GMT3)
    if wd is not None:
        assert dt.weekday() == wd
    return dt


def test_live_config_uses_gmt_plus_3():
    assert LIVE_BOT_CONFIG.session_timezone == "UTC+3"
    assert LIVE_BOT_CONFIG.session_open_time == "01:05"
    assert LIVE_BOT_CONFIG.session_close_time == "23:45"


def test_daily_session_window():
    cfg = LIVE_BOT_CONFIG
    assert not is_in_session(cfg, _dt(2026, 6, 15, 0, 30, wd=0))
    assert is_in_session(cfg, _dt(2026, 6, 15, 1, 30, wd=0))
    assert is_in_session(cfg, _dt(2026, 6, 15, 10, 0, wd=0))
    assert is_in_session(cfg, _dt(2026, 6, 15, 23, 0, wd=0))
    assert not is_in_session(cfg, _dt(2026, 6, 15, 23, 50, wd=0))


def test_pre_close_buffer_daily():
    cfg = LIVE_BOT_CONFIG
    assert is_pre_close_buffer(cfg, _dt(2026, 6, 17, 23, 42, wd=2))
    assert not is_pre_close_buffer(cfg, _dt(2026, 6, 17, 23, 30, wd=2))


def test_weekly_break_friday_to_sunday():
    cfg = LIVE_BOT_CONFIG
    assert is_in_session(cfg, _dt(2026, 6, 19, 23, 0, wd=4))
    assert not is_in_session(cfg, _dt(2026, 6, 20, 0, 30, wd=5))
    assert not is_in_session(cfg, _dt(2026, 6, 20, 12, 0, wd=5))
    assert not is_in_session(cfg, _dt(2026, 6, 21, 1, 0, wd=6))
    assert is_in_session(cfg, _dt(2026, 6, 21, 1, 30, wd=6))


def test_friday_pre_close_flag():
    cfg = LIVE_BOT_CONFIG
    assert is_week_close_pre_buffer(cfg, _dt(2026, 6, 19, 23, 42, wd=4))


def test_get_session_now_converts_from_utc_instant(monkeypatch):
    cfg = LIVE_BOT_CONFIG

    class _Tick:
        time = datetime(2026, 6, 19, 17, 0, tzinfo=timezone.utc).timestamp()

    monkeypatch.setattr(
        "infra.session_manager.mt5.symbol_info_tick",
        lambda _symbol: _Tick(),
    )
    session_now = get_session_now(cfg)
    assert session_now.utcoffset() == timedelta(hours=3)
    assert session_now.hour == 20
    assert session_now.minute == 0
