import logging

import MetaTrader5 as mt5

from config.bot_config import BotConfig
from core.logging_utils import log_event

log = logging.getLogger(__name__)

MODE_OFF = "off"
MODE_MARKET_CLOSE = "market_close"
MODE_PENDING_PRUNE = "pending_prune"


def _mode(cfg: BotConfig) -> str:
    return str(getattr(cfg, "live_position_cap_mode", MODE_OFF)).lower()


def _limit(cfg: BotConfig):
    value = getattr(cfg, "live_max_open_positions", None)
    if value is None:
        return None
    try:
        lim = int(value)
    except Exception:
        return None
    return lim if lim > 0 else None


def _bot_positions(cfg: BotConfig) -> list:
    positions = mt5.positions_get(symbol=cfg.symbol)
    if not positions:
        return []
    return [p for p in positions if p.magic == cfg.magic]


def _bot_pendings(cfg: BotConfig) -> list:
    orders = mt5.orders_get(symbol=cfg.symbol)
    if not orders:
        return []
    return [o for o in orders if o.magic == cfg.magic]


def _order_distance(order, tick) -> float:
    order_type = int(getattr(order, "type", -1))
    price_open = float(getattr(order, "price_open", 0.0))
    if order_type in (getattr(mt5, "ORDER_TYPE_BUY_STOP", -101), getattr(mt5, "ORDER_TYPE_BUY_LIMIT", -102)):
        return abs(price_open - float(tick.ask))
    if order_type in (getattr(mt5, "ORDER_TYPE_SELL_STOP", -103), getattr(mt5, "ORDER_TYPE_SELL_LIMIT", -104)):
        return abs(price_open - float(tick.bid))
    return abs(price_open - float((tick.ask + tick.bid) / 2.0))


def _close_position_market(cfg: BotConfig, position) -> bool:
    tick = mt5.symbol_info_tick(cfg.symbol)
    if tick is None:
        log.warning(f"POSITION_CAP: nelze zavrit #{position.ticket}, chybi tick")
        return False
    if position.type == mt5.POSITION_TYPE_BUY:
        close_type = mt5.ORDER_TYPE_SELL
        price = tick.bid
    else:
        close_type = mt5.ORDER_TYPE_BUY
        price = tick.ask
    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": cfg.symbol,
        "volume": position.volume,
        "type": close_type,
        "position": position.ticket,
        "price": float(price),
        "deviation": 20,
        "magic": cfg.magic,
        "comment": "POSITION_CAP_CLOSE",
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(req)
    if result is None:
        log.warning(f"POSITION_CAP: close #{position.ticket} bez odpovedi | {mt5.last_error()}")
        return False
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        return True
    log.warning(
        f"POSITION_CAP: close #{position.ticket} selhal retcode={result.retcode} | {result.comment}"
    )
    return False


def _cancel_pending(cfg: BotConfig, order) -> bool:
    req = {
        "action": mt5.TRADE_ACTION_REMOVE,
        "order": int(order.ticket),
    }
    result = mt5.order_send(req)
    if result is None:
        return False
    return result.retcode == mt5.TRADE_RETCODE_DONE


def enforce_live_position_cap(cfg: BotConfig) -> None:
    mode = _mode(cfg)
    limit = _limit(cfg)
    if mode == MODE_OFF or limit is None:
        return

    positions = _bot_positions(cfg)
    open_count = len(positions)

    if mode == MODE_PENDING_PRUNE:
        free_slots = max(0, limit - open_count)
        pendings = _bot_pendings(cfg)
        if pendings and len(pendings) > free_slots:
            tick = mt5.symbol_info_tick(cfg.symbol)
            if tick is not None:
                sorted_pending = sorted(pendings, key=lambda o: _order_distance(o, tick))
                keep_tickets = {o.ticket for o in sorted_pending[:free_slots]}
                cancelled = 0
                for order in pendings:
                    if order.ticket in keep_tickets:
                        continue
                    if _cancel_pending(cfg, order):
                        cancelled += 1
                if cancelled > 0:
                    log_event(
                        cfg,
                        "warning",
                        "POSITION_CAP_PENDING_PRUNE",
                        mode=mode,
                        limit=limit,
                        open_positions=open_count,
                        cancelled_pending=cancelled,
                    )

    positions = _bot_positions(cfg)
    overflow = len(positions) - limit
    if overflow <= 0:
        return

    # Overflow pojistka: v obou modech zavira marketem nejnovejsi pozice.
    sorted_positions = sorted(
        positions,
        key=lambda p: (getattr(p, "time_msc", 0), getattr(p, "time", 0)),
        reverse=True,
    )
    closed = 0
    for pos in sorted_positions[:overflow]:
        if _close_position_market(cfg, pos):
            closed += 1

    log_event(
        cfg,
        "warning",
        "POSITION_CAP_OVERFLOW_ENFORCED",
        mode=mode,
        limit=limit,
        overflow=overflow,
        closed_positions=closed,
    )
