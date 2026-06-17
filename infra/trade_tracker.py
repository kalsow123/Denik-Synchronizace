
# ───── TRADE TRACKER ──────────────────────────
# Detekuje zmeny mezi cykly:
# - novy pending order   -> ORDER_PLACED (uz se loguje primo v orders.py)
# - pending -> position  -> ORDER_FILLED + POSITION_OPENED
# - position zmizla      -> POSITION_CLOSED (s pnl_usd, commission, swap, close_reason)
# - MT5 disconnect/reconnect -> MT5_CONNECTION

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

try:
    import MetaTrader5 as mt5
    _HAS_MT5 = True
except ImportError:
    mt5 = None
    _HAS_MT5 = False

from config.bot_config import BotConfig
from core.logging_utils import log_event

log = logging.getLogger(__name__)


@dataclass
class TradeTrackerState:
    """
    Persistentni stav mezi cykly. Drzi se v live_loop.py jako lokalni promenna,
    predava se do update().
    """
    # ticket -> snapshot dict
    # ticket -> snapshot dict
    known_orders: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    known_positions: Dict[int, Dict[str, Any]] = field(default_factory=dict)

    # MT5 connection state
    mt5_connected:   bool = True
    last_mt5_check:  Optional[datetime] = None


# ─── HELPERS ─────────────────────────────────────────────

def _classify_close_reason(deal, position_snapshot: dict) -> str:
    """
    Urci proc se pozice zavrela:
      - TP    pokud cena ~ tp z position_snapshot
      - SL    pokud cena ~ sl z position_snapshot
      - MANUAL pokud comment obsahuje "manual" nebo nic neodpovida TP/SL
      - EXPIRED pokud comment obsahuje "expir"
    """
    comment = (getattr(deal, "comment", "") or "").lower()
    price   = float(getattr(deal, "price", 0.0))

    if "expir" in comment:
        return "EXPIRED"

    sl = float(position_snapshot.get("sl", 0.0) or 0.0)
    tp = float(position_snapshot.get("tp", 0.0) or 0.0)

    # Tolerance ~ 5 bodu (zalezi na symbolu, ale pro indexy a forex je to bezpecne)
    tol = max(abs(price * 0.0005), 0.5)

    if tp and abs(price - tp) <= tol:
        return "TP"
    if sl and abs(price - sl) <= tol:
        return "SL"
    if "tp" in comment:
        return "TP"
    if "sl" in comment or "stop" in comment:
        return "SL"
    return "MANUAL"


def _fetch_close_data(position_id: int, position_snapshot: dict) -> dict:
    """
    Z mt5.history_deals_get() zjisti pnl, commission, swap, close_reason
    pro uzavrenou pozici.
    """
    out = {
        "pnl_usd":      0.0,
        "commission":   0.0,
        "swap":         0.0,
        "close_reason": "UNKNOWN",
        "exit_price":   0.0,
        "close_time":   None,
    }

    if not _HAS_MT5 or mt5 is None:
        return out

    try:
        # history_deals_get podle position_id - vrati vsechny dealy teto pozice
        # (entry deal + exit deal, pripadne i partial closes)
        deals = mt5.history_deals_get(position=position_id)
        if not deals:
            # Fallback: zkus podle casoveho rozsahu
            time_to = datetime.now(timezone.utc) + timedelta(minutes=5)
            time_from = datetime.now(timezone.utc) - timedelta(days=30)
            all_deals = mt5.history_deals_get(time_from, time_to)
            if all_deals:
                deals = [d for d in all_deals if d.position_id == position_id]

        if not deals:
            return out

        total_pnl = 0.0
        total_commission = 0.0
        total_swap = 0.0
        exit_deal = None

        for d in deals:
            total_pnl        += float(getattr(d, "profit", 0.0))
            total_commission += float(getattr(d, "commission", 0.0))
            total_swap       += float(getattr(d, "swap", 0.0))
            # entry=1 = OUT (zaviraci deal); entry=0 = IN (otviraci)
            if getattr(d, "entry", 0) == 1:
                exit_deal = d

        out["pnl_usd"]    = round(total_pnl, 2)
        out["commission"] = round(total_commission, 2)
        out["swap"]       = round(total_swap, 2)

        if exit_deal:
            out["exit_price"]   = float(exit_deal.price)
            out["close_time"]   = datetime.fromtimestamp(exit_deal.time, timezone.utc).isoformat().replace("+00:00", "Z")
            out["close_reason"] = _classify_close_reason(exit_deal, position_snapshot)
        else:
            out["close_reason"] = "UNKNOWN"

    except Exception as e:
        log.error(f"_fetch_close_data selhal pro position={position_id}: {e}", exc_info=True)

    return out


def _snapshot_order(o) -> Dict[str, Any]:
    return {
        "ticket":   int(o.ticket),
        "symbol":   o.symbol,
        "type":     int(o.type),
        "volume":   float(o.volume_initial),
        "price":    float(o.price_open),
        "sl":       float(o.sl),
        "tp":       float(o.tp),
        "magic":    int(o.magic),
        "comment":  str(o.comment),
        "time_setup": int(o.time_setup),
    }


def _snapshot_position(p) -> Dict[str, Any]:
    return {
        "ticket":     int(p.ticket),
        "symbol":     p.symbol,
        "type":       int(p.type),
        "volume":     float(p.volume),
        "price_open": float(p.price_open),
        "sl":         float(p.sl),
        "tp":         float(p.tp),
        "magic":      int(p.magic),
        "comment":    str(p.comment),
        "time":       int(p.time),
    }


def _order_type_name(t: int) -> str:
    if not _HAS_MT5:
        return str(t)
    mapping = {
        mt5.ORDER_TYPE_BUY:        "BUY_MARKET",
        mt5.ORDER_TYPE_SELL:       "SELL_MARKET",
        mt5.ORDER_TYPE_BUY_LIMIT:  "BUY_LIMIT",
        mt5.ORDER_TYPE_SELL_LIMIT: "SELL_LIMIT",
        mt5.ORDER_TYPE_BUY_STOP:   "BUY_STOP",
        mt5.ORDER_TYPE_SELL_STOP:  "SELL_STOP",
    }
    return mapping.get(t, str(t))


def _position_side(t: int) -> str:
    if not _HAS_MT5:
        return str(t)
    if t == mt5.POSITION_TYPE_BUY:
        return "BUY"
    if t == mt5.POSITION_TYPE_SELL:
        return "SELL"
    return str(t)


def _wave_id_from_comment(comment: str) -> Optional[str]:
    """Vytahne wave_time z comment 'W202604291430'."""
    if comment and comment.startswith("W") and len(comment) == 13:
        return comment[1:]
    return None


# ─── MAIN UPDATE ─────────────────────────────────────────

def update_trade_tracker(
    cfg: BotConfig,
    state: TradeTrackerState,
    *,
    adx14_runtime=None,
    live_wave_stats=None,
) -> None:
    """
    Vola se kazdy cyklus live_loopu. Detekuje zmeny a loguje strukturovane eventy.
    """
    if not _HAS_MT5 or mt5 is None:
        return

    # ── 1) MT5 CONNECTION CHECK ──
    try:
        terminal = mt5.terminal_info()
        account  = mt5.account_info()
        currently_connected = (terminal is not None and account is not None)
    except Exception:
        currently_connected = False

    if currently_connected != state.mt5_connected:
        if currently_connected:
            log_event(cfg, "info", "MT5_RECONNECTED")
        else:
            log_event(cfg, "warning", "MT5_DISCONNECTED", reason="TERMINAL_INFO_NONE")
        state.mt5_connected = currently_connected

    # Pokud je odpojeno, nemuzeme nic dal delat
    if not currently_connected:
        return

    # ── 2) ORDERS SNAPSHOT ──
    try:
        current_orders_raw = mt5.orders_get(symbol=cfg.symbol) or []
    except Exception:
        current_orders_raw = []

    current_orders: Dict[int, Dict[str, Any]] = {}
    for o in current_orders_raw:
        if o.magic == cfg.magic:
            current_orders[int(o.ticket)] = _snapshot_order(o)

    # ── 3) POSITIONS SNAPSHOT ──
    try:
        current_positions_raw = mt5.positions_get(symbol=cfg.symbol) or []
    except Exception:
        current_positions_raw = []

    current_positions: Dict[int, Dict[str, Any]] = {}
    for p in current_positions_raw:
        if p.magic == cfg.magic:
            current_positions[int(p.ticket)] = _snapshot_position(p)

    # ── 4) DETEKCE: ORDER ZMIZEL (filled or cancelled) ──
    for ticket, snap in list(state.known_orders.items()):
        if ticket not in current_orders:
            wave_id = _wave_id_from_comment(snap.get("comment", ""))
            # Nasel se ten ticket jako pozice? -> FILLED (a soucasne POSITION_OPENED)
            # MT5 ale nezaruci stejny ticket id mezi orderem a pozici!
            # Spolahneme se proto na "novou pozici v tomto cyklu" v sekci 5.

            # Tady alespon zalogujeme ze order zmizel:
            log_event(
                cfg,
                "info",
                "ORDER_GONE",
                order_id=ticket,
                wave_id=wave_id,
                last_known_price=snap.get("price"),
                volume=snap.get("volume"),
                msg="Order zmizel (filled / cancelled / expired)",
            )
            state.known_orders.pop(ticket, None)

    # ── 5) DETEKCE: NOVA POZICE (= ORDER_FILLED + POSITION_OPENED) ──
    for ticket, snap in current_positions.items():
        if ticket not in state.known_positions:
            wave_id = _wave_id_from_comment(snap.get("comment", ""))
            side = _position_side(snap["type"])

            log_event(
                cfg,
                "info",
                "ORDER_FILLED",
                position_id=ticket,
                side=side,
                fill_price=snap["price_open"],
                filled_volume=snap["volume"],
                wave_id=wave_id,
            )
            log_event(
                cfg,
                "info",
                "POSITION_OPENED",
                position_id=ticket,
                side=side,
                entry_price=snap["price_open"],
                sl=snap["sl"],
                tp=snap["tp"],
                volume=snap["volume"],
                wave_id=wave_id,
            )

    # ── 6) DETEKCE: POZICE ZMIZELA (= POSITION_CLOSED) ──
    for ticket, snap in list(state.known_positions.items()):
        if ticket not in current_positions:
            close_data = _fetch_close_data(ticket, snap)
            comment = str(snap.get("comment", "") or "")
            wave_id = _wave_id_from_comment(comment)
            side = _position_side(snap["type"])

            from runtime.live_wave_stats import position_kind_from_mt5_comment

            position_kind = position_kind_from_mt5_comment(comment)

            # duration_sec: rozdil mezi otevrenim a zavrenim pozice
            duration_sec: Optional[int] = None
            try:
                open_dt = datetime.fromtimestamp(int(snap["time"]), tz=timezone.utc)
                close_iso = close_data.get("close_time")
                if close_iso:
                    close_dt = datetime.fromisoformat(close_iso.replace("Z", "+00:00"))
                    duration_sec = int((close_dt - open_dt).total_seconds())
            except Exception as e:
                log.warning(f"[TRADE_TRACKER] duration_sec calc failed: {e}")

            log_event(
                cfg,
                "info",
                "POSITION_CLOSED",
                position_id=ticket,
                side=side,
                entry_price=float(snap["price_open"]),
                close_price=float(close_data["exit_price"]),
                volume=float(snap["volume"]),
                pnl_usd=float(close_data["pnl_usd"]),
                commission=float(close_data["commission"]),
                swap=float(close_data["swap"]),
                close_reason=close_data["close_reason"],
                duration_sec=duration_sec,
                wave_id=wave_id,
                position_kind=position_kind,
                comment=comment,
            )

            if live_wave_stats is not None:
                live_wave_stats.on_position_closed(
                    comment=comment,
                    pnl_usd=float(close_data["pnl_usd"]),
                )

            if adx14_runtime is not None and getattr(adx14_runtime, "pnl_tracker", None):
                close_iso = close_data.get("close_time") or datetime.now(timezone.utc).isoformat()
                adx14_runtime.on_position_closed(
                    close_time=close_iso,
                    pnl_usd=float(close_data["pnl_usd"]),
                    source_risk_usd=float(cfg.risk_usd),
                    note=str(wave_id or ""),
                    now=datetime.now(timezone.utc),
                )

    # ── 7) UPDATE STATE ──
    state.known_orders    = current_orders
    state.known_positions = current_positions
    state.last_mt5_check  = datetime.now(timezone.utc)