from datetime import datetime, time, timezone

from config.bot_config import BotConfig

# ───── LIVE BOT, FILTRY ──────────────────────────


# Predefinovane sessions v BROKER TIME (HH:MM od, HH:MM do).
# Tyto casy si muzes upravit zde podle sveho brokera (FXIFY/FXPIG = UTC+2/+3).
# ASIA prechazi pres pulnoc - kod to umi.
SESSIONS: dict = {
    "ASIA":            ("23:00", "09:00"),
    "LONDON":          ("07:00", "15:00"),
    "USA":             ("13:00", "23:00"),
    "OVERLAP_LON_USA": ("13:00", "15:00"),
}


    # Vrací True, pokud je vlna starší než cfg.max_wave_age_hours
def is_wave_too_old(wave_time: str, cfg: BotConfig, now: datetime | None = None) -> bool:
    wave_dt = datetime.strptime(wave_time, "%Y%m%d%H%M")
    if now is None:
        now_ref = datetime.utcnow()
    elif now.tzinfo is not None:
        now_ref = now.astimezone(timezone.utc).replace(tzinfo=None)
    else:
        now_ref = now
    age_sec = (now_ref - wave_dt).total_seconds()
    return age_sec > cfg.max_wave_age_hours * 3600


def is_wave_too_large(move_pct: float, cfg: BotConfig, *, is_ext: bool = False) -> bool:
    """
    Vraci True, pokud velikost vlny presahuje cfg.wave_max_pct.
    Pokud wave_max_pct neni nastavene, filtr je vypnuty.

    EXT BLOK: pokud `is_ext=True`, filter se neaplikuje (EXT je mod pro velke vlny).
    """
    if is_ext:
        return False
    wave_max_pct = getattr(cfg, "wave_max_pct", None)
    if wave_max_pct is None:
        return False
    return float(move_pct) > float(wave_max_pct)


    # Pomocna funkce: je `t` v okne (start, end)? Zvlada i okno pres pulnoc.
def _time_in_window(t: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= t < end
    # pres pulnoc: napr. 23:00 -> 09:00
    return t >= start or t < end


    # Vrací True, pokud vlna spada do nejake z povolenych sessions.
    # Pokud je filter vypnuty, vraci VZDY True (zpetne kompatibilni).
def is_wave_in_allowed_session(wave_time: str, cfg: BotConfig) -> bool:
    if not getattr(cfg, "wave_session_filter_enabled", False):
        return True

    wave_dt = datetime.strptime(wave_time, "%Y%m%d%H%M")
    wave_t = wave_dt.time()

    # Custom okno ma prednost pred wave_allowed_sessions
    custom = getattr(cfg, "wave_custom_window", None)
    if custom is not None:
        start = datetime.strptime(custom[0], "%H:%M").time()
        end = datetime.strptime(custom[1], "%H:%M").time()
        return _time_in_window(wave_t, start, end)

    allowed = getattr(cfg, "wave_allowed_sessions", []) or []
    for sess_name in allowed:
        window = SESSIONS.get(sess_name)
        if window is None:
            continue
        start = datetime.strptime(window[0], "%H:%M").time()
        end = datetime.strptime(window[1], "%H:%M").time()
        if _time_in_window(wave_t, start, end):
            return True

    return False