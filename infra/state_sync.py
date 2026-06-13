
from typing import Set

import MetaTrader5 as mt5

from config.bot_config import BotConfig

# ───── MT5 READER ──────────────────────────
# Čte co MT5 má momentálně jako pending a otevřené pozice `cfg.magic`; "W{wave_time}"

#     Vraci mnozinu wave_time stringu (YYYYMMDDHHMM) z PENDING orderu,
#     ktere patri tomuto botu (par MAGIC + komentar "W{wave_time}").
def get_active_wave_times(cfg: BotConfig) -> Set[str]:
    orders = mt5.orders_get(symbol=cfg.symbol)
    if not orders:
        return set()

    active: Set[str] = set()
    for o in orders:
        if o.magic == cfg.magic and o.comment.startswith("W") and len(o.comment) == 13:
            active.add(o.comment[1:])

    return active

    # Neotevření setupu, který se již z orderu proměnil na pozici
def get_position_wave_times(cfg: BotConfig) -> Set[str]:
    positions = mt5.positions_get(symbol=cfg.symbol)
    if not positions:
        return set()

    active: Set[str] = set()
    for p in positions:
        if p.magic == cfg.magic and p.comment.startswith("W") and len(p.comment) == 13:
            active.add(p.comment[1:])

    return active
