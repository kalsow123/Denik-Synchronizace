"""
Session manager pro live bota.

Resi off/on cyklus podle session casu:
  - V pre-close case (close - buffer) zrusi pendingy.
  - V close case usne (vlny se nedetekuji, nove ordery se nevystavuji).
  - V open case se probudi a spusti startup recovery.

LIVE ONLY - backtester tento modul ignoruje.

Cas pro session okna (`session_open_time`, `session_close_time`, …):
  - `session_timezone="broker"` — stejny okamzik jako MT5 tick (UTC instant, legacy)
  - `UTC+3` / `GMT+3` — fixni offset bez letniho casu
  - jinak IANA zona, napr. `Europe/Prague`

Strategie, bary a zbytek live logiky dale pouzivaji `get_broker_now()` (MT5).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

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
    Vrati aktualni cas podle MT5 ticku (UTC instant).
    Pouziva strategie, heartbeat intervaly a dalsi live logiku mimo session manager.
    """
    try:
        tick = mt5.symbol_info_tick(cfg.symbol)
        if tick is not None and getattr(tick, "time", None):
            return datetime.fromtimestamp(tick.time, tz=timezone.utc)
    except Exception:
        pass
    return datetime.now(timezone.utc)


def _session_timezone_name(cfg: BotConfig) -> str | None:
    tz = str(getattr(cfg, "session_timezone", "broker") or "broker").strip()
    if not tz or tz.lower() == "broker":
        return None
    return tz


def _session_tzinfo(cfg: BotConfig):
    tz_name = _session_timezone_name(cfg)
    if tz_name is None:
        return timezone.utc
    fixed = re.fullmatch(r"(?:UTC|GMT)\s*([+-])\s*(\d{1,2})", tz_name, re.IGNORECASE)
    if fixed:
        sign = 1 if fixed.group(1) == "+" else -1
        hours = int(fixed.group(2))
        return timezone(timedelta(hours=sign * hours))
    return ZoneInfo(tz_name)


def get_session_now(cfg: BotConfig) -> datetime:
    """
    Cas pro session on/off okna.
    Broker mode = UTC instant z MT5; jinak prevod do `session_timezone`.
    """
    broker = get_broker_now(cfg)
    if _session_timezone_name(cfg) is None:
        return broker
    return broker.astimezone(_session_tzinfo(cfg))


def _resolve_session_now(cfg: BotConfig, now: Optional[datetime]) -> datetime:
    if now is None:
        return get_session_now(cfg)
    if _session_timezone_name(cfg) is None:
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc)
    session_tz = _session_tzinfo(cfg)
    if now.tzinfo is None:
        return now.replace(tzinfo=session_tz)
    return now.astimezone(session_tz)


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
        now = _resolve_session_now(cfg, None)
    else:
        now = _resolve_session_now(cfg, now)

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
        now = _resolve_session_now(cfg, None)
    else:
        now = _resolve_session_now(cfg, now)

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
        now = _resolve_session_now(cfg, None)
    else:
        now = _resolve_session_now(cfg, now)

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
        now = _resolve_session_now(cfg, None)
    else:
        now = _resolve_session_now(cfg, now)
    if now.weekday() != int(cfg.session_week_close_weekday):
        return False
    close_t = _parse_hhmm(cfg.session_week_close_time)
    close_dt = _today_at(now, close_t)
    buffer_start = close_dt - timedelta(minutes=cfg.session_pre_close_buffer_min)
    return buffer_start <= now < close_dt


def is_friday_pre_close(cfg: BotConfig, now: Optional[datetime] = None) -> bool:
    """Zpetna kompatibilita — pouziva session_week_close_* misto pevneho pateku."""
    return is_week_close_pre_buffer(cfg, now)
