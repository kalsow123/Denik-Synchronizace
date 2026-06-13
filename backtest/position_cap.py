from datetime import datetime

MODE_OFF = "off"
MODE_MARKET_CLOSE = "market_close"
MODE_PENDING_PRUNE = "pending_prune"


def _mode(cfg) -> str:
    return str(getattr(cfg, "backtest_position_cap_mode", MODE_OFF)).lower()


def _limit(cfg):
    value = getattr(cfg, "backtest_max_open_positions", None)
    if value is None:
        return None
    try:
        lim = int(value)
    except Exception:
        return None
    return lim if lim > 0 else None


def _pending_distance(order, mid_price: float) -> float:
    return abs(float(order.entry_price) - float(mid_price))


def apply_pending_prune(engine, mid_price: float) -> list:
    """
    Vrati seznam pending orderu, ktere byly z fronty odstraneny (position cap prune).
    """
    mode = _mode(engine)
    limit = _limit(engine)
    if mode != MODE_PENDING_PRUNE or limit is None:
        return []

    free_slots = max(0, limit - len(engine.open_trades))
    if len(engine.pending_orders) <= free_slots:
        return []

    # Protected = two-sided mirror + EXT WAVE pendingy (na pozadavek uzivatele
    # EXT WAVE pendingy se nikdy nerusi zadnou jinou funkci).
    def _is_protected(o) -> bool:
        return (
            bool(getattr(o, "is_two_sided_mirror", False))
            or (bool(getattr(o, "is_ext", False))
                and not bool(getattr(o, "is_counter", False)))
        )

    protected = [o for o in engine.pending_orders if _is_protected(o)]
    regular = [o for o in engine.pending_orders if not _is_protected(o)]
    sorted_regular = sorted(regular, key=lambda o: _pending_distance(o, mid_price))
    keep_n = max(0, free_slots - len(protected))
    keep_ids = {id(o) for o in protected}
    keep_ids.update(id(o) for o in sorted_regular[:keep_n])
    before = len(engine.pending_orders)
    removed = [o for o in engine.pending_orders if id(o) not in keep_ids]
    engine.pending_orders = [o for o in engine.pending_orders if id(o) in keep_ids]
    removed_n = before - len(engine.pending_orders)
    if removed_n > 0:
        engine.wave_debug["position_cap_pending_pruned"] = engine.wave_debug.get("position_cap_pending_pruned", 0) + removed_n
    return removed


def enforce_market_overflow(engine, bar_idx: int, bar_time: datetime, market_mid_price: float) -> None:
    mode = _mode(engine)
    limit = _limit(engine)
    if mode not in (MODE_MARKET_CLOSE, MODE_PENDING_PRUNE) or limit is None:
        return

    overflow = len(engine.open_trades) - limit
    if overflow <= 0:
        return

    # Zavira nejnovejsi obchody (LIFO trim).
    sorted_open = sorted(engine.open_trades, key=lambda t: t.entry_bar, reverse=True)
    to_close = sorted_open[:overflow]
    keep_ids = {id(t) for t in sorted_open[overflow:]}

    for trade in to_close:
        if trade.dir == 1:
            close_price = float(market_mid_price) - (engine.backtest_spread / 2.0)
        else:
            close_price = float(market_mid_price) + (engine.backtest_spread / 2.0)
        ct = engine._make_closed(trade, bar_idx, close_price, bar_time, "POSITION_CAP_CLOSE")
        engine._append_closed_trade(ct, bar_time)

    engine.open_trades = [t for t in engine.open_trades if id(t) in keep_ids]
    engine.wave_debug["position_cap_market_closed"] = engine.wave_debug.get("position_cap_market_closed", 0) + len(to_close)
