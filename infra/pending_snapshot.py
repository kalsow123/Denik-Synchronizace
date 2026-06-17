"""
Session pending snapshot — zachyti vsechny MT5 pendingy pred cancel_all_pendings
a po wake-up / startu je obnovi se stejnymi cenami, SL/TP, lotem a commentem.

WAVE pine recovery doplňuje jen WAVE LIMIT, ktere ve snapshotu chybi (cold start).
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

from config.bot_config import BotConfig
from core.trading_days import business_time_delta, is_older_than_business_days
from infra.orders import (
    COUNTER_PENDING_COMMENT_PREFIX,
    EXT_COUNTER_BOS_COMMENT_PREFIX,
    EXT_COUNTER_TIME_COMMENT_PREFIX,
    EXT_SECONDARY_COMMENT_PREFIX,
    PP_PENDING_COMMENT_PREFIX,
    TWO_SIDED_MIRROR_COMMENT_PREFIX,
    _order_send_with_retry,
    _resolve_retry_policy,
    _round_price,
)
from strategy.ext_logic import EXT_PRIMARY_WAVE_COMMENT_PREFIX, is_ext_wave_pending_comment

log = logging.getLogger(__name__)

_SNAPSHOT_VERSION = 1
_DEFAULT_PATH = Path("runtime/session_pending_snapshot.json")

_PENDING_PREFIXES = (
    COUNTER_PENDING_COMMENT_PREFIX,
    TWO_SIDED_MIRROR_COMMENT_PREFIX,
    PP_PENDING_COMMENT_PREFIX,
    EXT_PRIMARY_WAVE_COMMENT_PREFIX,
    EXT_SECONDARY_COMMENT_PREFIX,
    EXT_COUNTER_TIME_COMMENT_PREFIX,
    EXT_COUNTER_BOS_COMMENT_PREFIX,
    "RENT_",
    "PPM_",
)


@dataclass
class PendingOrderSnapshot:
    order_type: int
    price: float
    sl: float
    tp: float
    volume: float
    comment: str
    time_setup_iso: str | None = None


def _snapshot_path(cfg: BotConfig) -> Path:
    raw = getattr(cfg, "session_pending_snapshot_path", None)
    return Path(raw) if raw else _DEFAULT_PATH


def wave_time_from_pending_comment(comment: str) -> str | None:
    c = str(comment or "")
    if c.startswith("W") and len(c) == 13 and c[1:].isdigit():
        return c[1:]
    for prefix in _PENDING_PREFIXES:
        if c.startswith(prefix):
            wt = c[len(prefix):]
            if wt.isdigit() and len(wt) == 12:
                return wt
    return None


def _snapshot_expired(cfg: BotConfig, snap: PendingOrderSnapshot, now_utc: datetime) -> bool:
    comment = snap.comment
    if comment.startswith(COUNTER_PENDING_COMMENT_PREFIX) or comment.startswith(
        PP_PENDING_COMMENT_PREFIX
    ):
        return False

    wt = wave_time_from_pending_comment(comment)
    if wt:
        wave_dt = datetime.strptime(wt, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
        if is_ext_wave_pending_comment(comment):
            days = int(getattr(cfg, "ext_order_expiry_days", 7))
        else:
            days = int(getattr(cfg, "order_expiry_days", 14))
        return is_older_than_business_days(wave_dt, now_utc, days)

    if snap.time_setup_iso:
        try:
            setup = datetime.fromisoformat(snap.time_setup_iso)
            if setup.tzinfo is None:
                setup = setup.replace(tzinfo=timezone.utc)
            limit = timedelta(days=int(getattr(cfg, "order_expiry_days", 14)))
            return business_time_delta(setup, now_utc) > limit
        except ValueError:
            pass
    return False


def capture_pending_snapshot(cfg: BotConfig) -> list[PendingOrderSnapshot]:
    orders = mt5.orders_get(symbol=cfg.symbol)
    if not orders:
        return []
    out: list[PendingOrderSnapshot] = []
    for o in orders:
        if o.magic != cfg.magic:
            continue
        setup_iso = None
        if getattr(o, "time_setup", None):
            setup_iso = datetime.fromtimestamp(
                int(o.time_setup), tz=timezone.utc
            ).isoformat()
        out.append(
            PendingOrderSnapshot(
                order_type=int(o.type),
                price=float(o.price_open),
                sl=float(o.sl),
                tp=float(o.tp),
                volume=float(o.volume_current),
                comment=str(o.comment or ""),
                time_setup_iso=setup_iso,
            )
        )
    return out


def save_pending_snapshot(cfg: BotConfig, snapshots: list[PendingOrderSnapshot]) -> Path:
    path = _snapshot_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": _SNAPSHOT_VERSION,
        "bot_name": cfg.bot_name,
        "symbol": cfg.symbol,
        "magic": cfg.magic,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "orders": [asdict(s) for s in snapshots],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log.info("SESSION SNAPSHOT: ulozeno %s pending orderu do %s", len(snapshots), path)
    return path


def load_pending_snapshot(cfg: BotConfig) -> list[PendingOrderSnapshot] | None:
    path = _snapshot_path(cfg)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("SESSION SNAPSHOT: nelze nacist %s: %s", path, exc)
        return None
    if payload.get("bot_name") != cfg.bot_name:
        log.warning("SESSION SNAPSHOT: bot_name nesedi, preskakuji")
        return None
    if payload.get("magic") != cfg.magic:
        log.warning("SESSION SNAPSHOT: magic nesedi, preskakuji")
        return None
    saved_at = payload.get("saved_at")
    if saved_at:
        try:
            ts = datetime.fromisoformat(str(saved_at))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - ts > timedelta(days=8):
                log.warning("SESSION SNAPSHOT: prilis stary soubor, preskakuji")
                return None
        except ValueError:
            pass
    orders_raw = payload.get("orders") or []
    return [PendingOrderSnapshot(**row) for row in orders_raw]


def clear_pending_snapshot(cfg: BotConfig) -> None:
    path = _snapshot_path(cfg)
    try:
        if path.exists():
            path.unlink()
    except OSError as exc:
        log.warning("SESSION SNAPSHOT: nelze smazat %s: %s", path, exc)


def _pending_comment_exists(cfg: BotConfig, comment: str) -> bool:
    for o in mt5.orders_get(symbol=cfg.symbol) or []:
        if o.magic == cfg.magic and str(o.comment or "") == comment:
            return True
    return False


def _sl_breached(snap: PendingOrderSnapshot, tick, *, is_buy: bool) -> bool:
    sl = float(snap.sl)
    if sl <= 0:
        return False
    if is_buy:
        return float(tick.ask) <= sl
    return float(tick.bid) >= sl


def _is_buy_order_type(order_type: int) -> bool:
    buy_types = {
        int(getattr(mt5, "ORDER_TYPE_BUY_LIMIT", 2)),
        int(getattr(mt5, "ORDER_TYPE_BUY_STOP", 4)),
    }
    return int(order_type) in buy_types


def restore_pending_snapshot(
    cfg: BotConfig,
    snapshots: list[PendingOrderSnapshot],
) -> int:
    """Obnovi pending ordery ze snapshotu. Vraci pocet uspesne obnovenych."""
    if not snapshots:
        return 0

    tick = mt5.symbol_info_tick(cfg.symbol)
    info = mt5.symbol_info(cfg.symbol)
    if tick is None or info is None:
        log.error("SESSION SNAPSHOT RESTORE: chybi tick/symbol_info")
        return 0

    digits = int(getattr(info, "digits", 5))
    point = float(getattr(info, "point", 0) or 0)
    stops_level = int(getattr(info, "trade_stops_level", 0) or 0)
    min_stop_dist = stops_level * point if stops_level and point else 0.0
    now_utc = datetime.now(timezone.utc)
    restored = 0

    for snap in snapshots:
        comment = snap.comment
        if not comment:
            continue
        if _pending_comment_exists(cfg, comment):
            log.info("SESSION SNAPSHOT RESTORE: skip (jiz v MT5) %s", comment)
            continue
        if _snapshot_expired(cfg, snap, now_utc):
            log.info("SESSION SNAPSHOT RESTORE: skip (expirovano) %s", comment)
            continue

        is_buy = _is_buy_order_type(snap.order_type)
        if _sl_breached(snap, tick, is_buy=is_buy):
            log.info("SESSION SNAPSHOT RESTORE: skip (SL) %s", comment)
            continue

        ep = float(snap.price)
        sl = float(snap.sl)
        tp_raw = float(snap.tp)
        tp = None if tp_raw <= 0 else tp_raw
        market_ref = float(tick.ask if is_buy else tick.bid)

        if min_stop_dist > 0:
            if abs(market_ref - ep) < min_stop_dist:
                log.info("SESSION SNAPSHOT RESTORE: skip (min dist EP) %s", comment)
                continue
            if sl > 0 and abs(ep - sl) < min_stop_dist:
                log.info("SESSION SNAPSHOT RESTORE: skip (min dist SL) %s", comment)
                continue
            if tp is not None and abs(tp - ep) < min_stop_dist:
                log.info("SESSION SNAPSHOT RESTORE: skip (min dist TP) %s", comment)
                continue

        request: dict[str, Any] = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": cfg.symbol,
            "volume": float(snap.volume),
            "type": int(snap.order_type),
            "price": _round_price(ep, digits),
            "sl": _round_price(sl, digits) if sl > 0 else 0.0,
            "tp": _round_price(tp, digits) if tp is not None else 0.0,
            "magic": cfg.magic,
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_RETURN,
        }
        attempts, backoff = _resolve_retry_policy(cfg, request)
        result = _order_send_with_retry(
            request, "SESSION_SNAPSHOT_RESTORE", max_attempts=attempts, backoff_sec=backoff
        )
        if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
            restored += 1
            log.info(
                "SESSION SNAPSHOT RESTORE: OK %s | EP=%.5f SL=%.5f TP=%s Lot=%s",
                comment,
                ep,
                sl,
                "—" if tp is None else f"{tp:.5f}",
                snap.volume,
            )
        else:
            err = getattr(result, "comment", None) if result else mt5.last_error()
            log.warning("SESSION SNAPSHOT RESTORE: FAIL %s | %s", comment, err)

    log.info("SESSION SNAPSHOT RESTORE: obnoveno %s / %s", restored, len(snapshots))
    return restored


def restore_session_pending_snapshot(cfg: BotConfig) -> int:
    """Nacte snapshot ze souboru (pokud existuje), obnovi a soubor smaze."""
    snapshots = load_pending_snapshot(cfg)
    if not snapshots:
        return 0
    restored = restore_pending_snapshot(cfg, snapshots)
    clear_pending_snapshot(cfg)
    return restored
