
# ───── ACCOUNT INFO ──────────────────────────
# Cte aktualni stav uctu z MT5 + filtruje pozice podle MAGIC.
# Pouziva se v live_loop pro hodinovy STATUS log a v risk.py pro dynamicky lot sizing.

import logging
from dataclasses import dataclass
from typing import Optional

try:
    import MetaTrader5 as mt5
    _HAS_MT5 = True
except ImportError:
    mt5 = None
    _HAS_MT5 = False

from config.bot_config import BotConfig

log = logging.getLogger(__name__)


@dataclass
class AccountSnapshot:
    # Globalni stav uctu (z MT5)
    balance:       float = 0.0
    equity:        float = 0.0
    profit_total:  float = 0.0    # plovouci P/L vsech open pozic na uctu
    margin:        float = 0.0
    margin_free:   float = 0.0
    margin_level:  float = 0.0
    currency:      str   = "USD"

    # Stav pouze tohoto bota (filtr přes MAGIC)
    profit_bot:       float = 0.0
    open_positions:   int   = 0
    pending_orders:   int   = 0

    # Flag jestli je snapshot validni (MT5 dostupne)
    valid:         bool  = False


def get_account_snapshot(cfg: BotConfig) -> AccountSnapshot:
    """
    Nacte aktualni stav uctu z MT5 + spocita pozice/pendingy tohoto bota.
    Pokud MT5 neni dostupne nebo dojde k chybe, vrati AccountSnapshot(valid=False).
    """
    snap = AccountSnapshot()

    if not _HAS_MT5 or mt5 is None:
        return snap

    try:
        # 1) Globalni info o uctu
        acc = mt5.account_info()
        if acc is None:
            log.warning("account_info() vratilo None")
            return snap

        snap.balance      = float(acc.balance)
        snap.equity       = float(acc.equity)
        snap.profit_total = float(acc.profit)
        snap.margin       = float(acc.margin)
        snap.margin_free  = float(acc.margin_free)
        snap.margin_level = float(acc.margin_level) if acc.margin > 0 else 0.0
        snap.currency     = str(acc.currency)

        # 2) Filtr pozic tohoto bota (podle MAGIC)
        positions = mt5.positions_get(symbol=cfg.symbol) or []
        bot_positions = [p for p in positions if p.magic == cfg.magic]
        snap.open_positions = len(bot_positions)
        snap.profit_bot     = float(sum(p.profit for p in bot_positions))

        # 3) Filtr pendingu tohoto bota
        orders = mt5.orders_get(symbol=cfg.symbol) or []
        bot_orders = [o for o in orders if o.magic == cfg.magic]
        snap.pending_orders = len(bot_orders)

        snap.valid = True
        return snap

    except Exception as e:
        log.error(f"get_account_snapshot() selhal: {e}", exc_info=True)
        return snap


def get_equity(cfg: BotConfig) -> Optional[float]:
    """
    Rychle nacteni equity pro dynamicky lot sizing.
    Vrati None pokud MT5 neni dostupne.
    """
    if not _HAS_MT5 or mt5 is None:
        return None

    try:
        acc = mt5.account_info()
        if acc is None:
            return None
        return float(acc.equity)
    except Exception:
        return None