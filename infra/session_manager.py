"""
Session manager pro live bota.

Resi off/on cyklus podle session casu:
  - V pre-close case (close - buffer) zrusi pendingy.
  - V close case usne (vlny se nedetekuji, nove ordery se nevystavuji).
  - V open case se probudi a spusti startup recovery.

LIVE ONLY - backtester tento modul ignoruje.
Casy jsou v BROKER TIME (MT5 tick -> datetime.fromtimestamp).

Kdyz je session_weekdays_only=True, mezi tydennim zavrenim a otevrenim bot spi:
  - session_week_close_weekday + session_week_close_time (napr. patek 21:00)
  - session_week_open_weekday + session_week_open_time (napr. nedele 23:00 nebo pondeli 02:00)
  Pouzijte stejny cas jako session_open_time / session_close_time, pokud chcete chovani jako drive.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from typing import Optional

import MetaTrader5 as mt5

from config.bot_config import BotConfig

log = logging.getLogger(__name__)

def _parse_hhmm(s: str) -> time:
    """Parse 'HH:MM' string na datetime.time."""
    return datetime.strptime(s, "%H:%M").time()


def _today_at(now: datetime, hhmm: time) -> datetime:
    """Vrati dnesek s casem hhmm."""
    return now.replace(hour=hhmm.hour, minute=hhmm.minute, second=0, microsecond=0)


def _week_anchor_monday(now: datetime) -> datetime:
    """Pulnoc pondeli tydne, ve kterem lezi `now` (pondeli 0:00)."""
    base = now - timedelta(days=now.weekday())
    return base.replace(hour=0, minute=0, second=0, microsecond=0)


def _weekly_close_open_for_monday_week(monday_midnight: datetime, cfg: BotConfig) -> tuple[datetime, datetime]:
    """
    Pro tyden zacinajici v pondeli `monday_midnight` vrati (week_close_dt, week_open_dt).
    week_open_dt je vzdy az PO week_close_dt (muze byt o 7 dnu pozdeji, napr. patek -> pristi pondeli 02:00).
    """
    d0 = monday_midnight.date()
    close_t = _parse_hhmm(cfg.session_week_close_time)
    open_t = _parse_hhmm(cfg.session_week_open_time)
    c_wd = int(cfg.session_week_close_weekday)
    o_wd = int(cfg.session_week_open_weekday)
    tz = monday_midnight.tzinfo
    close_dt = datetime.combine(d0 + timedelta(days=c_wd), close_t, tzinfo=tz)
    open_dt = datetime.combine(d0 + timedelta(days=o_wd), open_t, tzinfo=tz)
    while open_dt <= close_dt:
        open_dt += timedelta(days=7)
    return close_dt, open_dt


def _in_weekly_break(cfg: BotConfig, now: datetime) -> bool:
    """True pokud `now` lezi v [week_close, week_open) pro nejaky prislusny tyden."""
    if not cfg.session_weekdays_only:
        return False
    anchor = _week_anchor_monday(now)
    for week_shift in (-7, 0, 7):
        mon = anchor + timedelta(days=week_shift)
        close_dt, open_dt = _weekly_close_open_for_monday_week(mon, cfg)
        if close_dt <= now < open_dt:
            return True
    return False


def is_session_enabled(cfg: BotConfig) -> bool:
    """Vrati True pokud je session manager zapnuty v configu."""
    return getattr(cfg, "session_enabled", False)


def get_broker_now(cfg: BotConfig) -> datetime:
    """
    Vrati aktualni broker time podle MT5 ticku.
    Fallback je UTC cas, pokud MT5 zatim nevrati tick.
    """
    try:
        tick = mt5.symbol_info_tick(cfg.symbol)
        if tick is not None and getattr(tick, "time", None):
            return datetime.fromtimestamp(tick.time, tz=timezone.utc)
    except Exception:
        pass
    return datetime.now(timezone.utc)


def _daily_session_active(cfg: BotConfig, now: datetime) -> bool:
    """Vnitrni: denne okno session_open_time .. session_close_time (vcetne pres pulnoc)."""
    open_t = _parse_hhmm(cfg.session_open_time)
    close_t = _parse_hhmm(cfg.session_close_time)
    cur_t = now.time()
    if open_t <= close_t:
        return open_t <= cur_t < close_t
    return cur_t >= open_t or cur_t < close_t


def is_in_session(cfg: BotConfig, now: Optional[datetime] = None) -> bool:
    """
    Vrati True, pokud aktualni cas spada do trading session:
    - denne podle session_open_time / session_close_time
    - pokud session_weekdays_only: navic mimo tydenni pauzu week_close -> week_open
    """
    if not is_session_enabled(cfg):
        return True

    if now is None:
        now = get_broker_now(cfg)

    if cfg.session_weekdays_only and _in_weekly_break(cfg, now):
        return False

    return _daily_session_active(cfg, now)


def is_pre_close_buffer(cfg: BotConfig, now: Optional[datetime] = None) -> bool:
    """
    True v okne [close - buffer_min, close) pro dnesni denni session_close_time
    a stejne pro session_week_close_* v den tydenniho zavreni (pokud weekdays_only).
    Behem tydenni pauzy False.
    """
    if not is_session_enabled(cfg):
        return False

    if now is None:
        now = get_broker_now(cfg)

    if cfg.session_weekdays_only and _in_weekly_break(cfg, now):
        return False

    buf = timedelta(minutes=cfg.session_pre_close_buffer_min)
    close_times: list[time] = [_parse_hhmm(cfg.session_close_time)]
    if cfg.session_weekdays_only and now.weekday() == int(cfg.session_week_close_weekday):
        wct = _parse_hhmm(cfg.session_week_close_time)
        if wct not in close_times:
            close_times.append(wct)

    for ct in close_times:
        close_dt = _today_at(now, ct)
        if close_dt - buf <= now < close_dt:
            return True
    return False


def seconds_until_open(cfg: BotConfig, now: Optional[datetime] = None) -> float:
    """
    Sekundy do nejblizsiho okamziku, kdy is_in_session bude True.
    Pouziva diskretni kandidaty (tydenni open + denni open) a overuje pres is_in_session.
    """
    if now is None:
        now = get_broker_now(cfg)

    if not is_session_enabled(cfg):
        return 0.0
    if is_in_session(cfg, now):
        return 0.0

    open_t = _parse_hhmm(cfg.session_open_time)
    candidates: set[datetime] = set()
    anchor = _week_anchor_monday(now)
    if cfg.session_weekdays_only:
        for ofs in (-7, 0, 7, 14):
            mon = anchor + timedelta(days=ofs)
            _, wopen = _weekly_close_open_for_monday_week(mon, cfg)
            if wopen > now:
                candidates.add(wopen)
    for i in range(16):
        d = now.date() + timedelta(days=i)
        dt = datetime.combine(d, open_t, tzinfo=now.tzinfo)
        if dt > now:
            candidates.add(dt)

    for t in sorted(candidates):
        if is_in_session(cfg, t):
            return max(1.0, (t - now).total_seconds())
    return 3600.0


def is_week_close_pre_buffer(cfg: BotConfig, now: Optional[datetime] = None) -> bool:
    """
    True v den session_week_close_weekday v okne [week_close - buffer, week_close).
    Pro session_close_positions_on_friday / zavreni pozic pred tydennim zavrenim.
    """
    if now is None:
        now = get_broker_now(cfg)
    if now.weekday() != int(cfg.session_week_close_weekday):
        return False
    close_t = _parse_hhmm(cfg.session_week_close_time)
    close_dt = _today_at(now, close_t)
    buffer_start = close_dt - timedelta(minutes=cfg.session_pre_close_buffer_min)
    return buffer_start <= now < close_dt


def is_friday_pre_close(cfg: BotConfig, now: Optional[datetime] = None) -> bool:
    """Zpetna kompatibilita — pouziva session_week_close_* misto pevneho pateku."""
    return is_week_close_pre_buffer(cfg, now)
