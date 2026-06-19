
import logging
import time
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5

from config.bot_config import BotConfig, abort_fib_shift_sl_mode
from core.trading_days import business_time_delta
from config.enums import EntryMode
from core.logging_utils import log_event
from core.risk import calc_lot
from infra.live_order_guard import (
    block_duplicate_bos_reentry,
    block_duplicate_counter_order,
    block_duplicate_ext_counter_bos,
    block_duplicate_ext_counter_time,
    block_duplicate_ext_secondary,
    block_duplicate_pp_order,
    block_duplicate_wave_order,
)
from strategy.ext_logic import (
    EXT_PRIMARY_WAVE_COMMENT_PREFIX,
    compute_ext_secondary_take_profit,
    ext_block_wave_time_from_comment,
    is_ext_block_comment,
    is_ext_block_trade_on_parent_wave,
    is_ext_wave,
    is_ext_wave_pending_comment,
    is_trade_within_parent_ext_window,
)
from strategy.trend_bos import TrendState, entry_allowed_at_fill_bar, resolve_effective_tp
from strategy.ext_range import pending_protected_from_bos_direction_cancel_by_comment
from strategy.wave_sequence import (
    compute_sl_price_from_pct,
    ext1_close_blocked_on_bar,
    is_bos_flip_follower_trade,
    should_close_trade_on_bos_flip,
    should_close_trade_on_tp_wave_n,
)

# ───── ORDER DEFF. ──────────────────────────
# Nastavení toho jak bot vstuppuje do pozic. Obsahuje funkce:
# - Rušení starých orderů
# - Základní STOP buy / sell po definici vlny
# - Mody pro vstupy je-li po definici vlny pozdě na STOP EP


log = logging.getLogger(__name__)

# MT5 comment prefixy (sdilene napric LIMIT/MARKET/close helpery)
COUNTER_PENDING_COMMENT_PREFIX = "CNTR_"
TWO_SIDED_MIRROR_COMMENT_PREFIX = "TS2_"
BOS_REENTRY_COMMENT_PREFIX = "RENT_"
PP_PENDING_COMMENT_PREFIX = "PP_"
PP_REENTRY_COMMENT_PREFIX = "PPM_"
EXT_SECONDARY_COMMENT_PREFIX = "E23_"
EXT_COUNTER_TIME_COMMENT_PREFIX = "ECT_"
EXT_COUNTER_BOS_COMMENT_PREFIX = "ECB_"


class _DetectedExistingResult:
    """Fallback result pro pripad, kdy broker order pravdepodobne prijal, ale klient nedostal odpoved."""

    def __init__(self, ticket: int | None = None):
        self.retcode = mt5.TRADE_RETCODE_DONE
        self.comment = "EXISTING_ORDER_DETECTED_AFTER_RETRYABLE_FAILURE"
        self.order = int(ticket or 0)
        self.deal = 0


def _price_digits(info) -> int:
    digits = getattr(info, "digits", 5)
    try:
        return max(0, int(digits))
    except Exception:
        return 5


def _round_price(value: float, digits: int) -> float:
    return round(float(value), digits)


def _float_close(a: float, b: float, eps: float) -> bool:
    return abs(float(a) - float(b)) <= float(eps)


def _find_matching_pending_ticket(request: dict) -> int | None:
    symbol = request.get("symbol")
    if not symbol:
        return None
    orders = mt5.orders_get(symbol=symbol)
    if not orders:
        return None

    info = mt5.symbol_info(symbol)
    point = float(getattr(info, "point", 0.0) or 0.0)
    price_eps = point * 2 if point > 0 else 1e-9
    vol_eps = 1e-8

    req_magic = request.get("magic")
    req_comment = request.get("comment")
    req_type = request.get("type")
    req_price = float(request.get("price", 0.0))
    req_vol = float(request.get("volume", 0.0))

    for o in orders:
        if req_magic is not None and int(getattr(o, "magic", -1)) != int(req_magic):
            continue
        if req_comment is not None and str(getattr(o, "comment", "")) != str(req_comment):
            continue
        if req_type is not None and int(getattr(o, "type", -1)) != int(req_type):
            continue
        order_price = float(getattr(o, "price_open", 0.0))
        order_vol = float(getattr(o, "volume_current", getattr(o, "volume_initial", 0.0)))
        if _float_close(order_price, req_price, price_eps) and _float_close(order_vol, req_vol, vol_eps):
            return int(getattr(o, "ticket", 0) or 0)
    return None


def _find_matching_position_ticket(request: dict) -> int | None:
    symbol = request.get("symbol")
    if not symbol:
        return None
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return None

    req_magic = request.get("magic")
    req_comment = request.get("comment")
    req_type = request.get("type")
    req_vol = float(request.get("volume", 0.0))
    vol_eps = 1e-8

    if req_type == getattr(mt5, "ORDER_TYPE_BUY", None):
        expected_pos_type = getattr(mt5, "POSITION_TYPE_BUY", None)
    elif req_type == getattr(mt5, "ORDER_TYPE_SELL", None):
        expected_pos_type = getattr(mt5, "POSITION_TYPE_SELL", None)
    else:
        return None

    for p in positions:
        if expected_pos_type is not None and int(getattr(p, "type", -1)) != int(expected_pos_type):
            continue
        if req_magic is not None and int(getattr(p, "magic", -1)) != int(req_magic):
            continue
        if req_comment is not None and str(getattr(p, "comment", "")) != str(req_comment):
            continue
        if not _float_close(float(getattr(p, "volume", 0.0)), req_vol, vol_eps):
            continue
        return int(getattr(p, "ticket", 0) or 0)
    return None


def _find_existing_request_ticket(request: dict) -> int | None:
    action = request.get("action")
    if action == getattr(mt5, "TRADE_ACTION_PENDING", None):
        return _find_matching_pending_ticket(request)
    if action == getattr(mt5, "TRADE_ACTION_DEAL", None):
        return _find_matching_position_ticket(request)
    return None


def _retryable_retcode_set() -> set:
    codes = set()
    for name in (
        "TRADE_RETCODE_REQUOTE",
        "TRADE_RETCODE_PRICE_CHANGED",
        "TRADE_RETCODE_PRICE_OFF",
        "TRADE_RETCODE_TIMEOUT",
        "TRADE_RETCODE_CONNECTION",
        "TRADE_RETCODE_TOO_MANY_REQUESTS",
        "TRADE_RETCODE_LOCKED",
    ):
        code = getattr(mt5, name, None)
        if code is not None:
            codes.add(code)
    return codes


def _order_send_with_retry(request: dict, label: str, max_attempts: int = 3, backoff_sec: float = 0.35):
    retryable_codes = _retryable_retcode_set()
    last_result = None

    for attempt in range(1, max_attempts + 1):
        result = mt5.order_send(request)
        last_result = result

        if result is None:
            if attempt < max_attempts:
                existing_ticket = _find_existing_request_ticket(request)
                if existing_ticket is not None:
                    log.warning(
                        f"{label}: order_send bez odpovedi, ale nasel se existujici ticket #{existing_ticket}; "
                        "retry se zastavuje kvuli idempotenci."
                    )
                    return _DetectedExistingResult(existing_ticket)
                log.warning(
                    f"{label}: order_send bez odpovedi (attempt {attempt}/{max_attempts}) | "
                    f"last_error={mt5.last_error()}"
                )
                time.sleep(backoff_sec * attempt)
                continue
            return None

        if result.retcode == mt5.TRADE_RETCODE_DONE:
            return result

        if result.retcode in retryable_codes and attempt < max_attempts:
            existing_ticket = _find_existing_request_ticket(request)
            if existing_ticket is not None:
                log.warning(
                    f"{label}: retryable retcode={result.retcode}, ale nasel se existujici ticket #{existing_ticket}; "
                    "retry se zastavuje kvuli idempotenci."
                )
                return _DetectedExistingResult(existing_ticket)
            log.warning(
                f"{label}: retryable retcode={result.retcode} (attempt {attempt}/{max_attempts}) | "
                f"{result.comment}"
            )
            time.sleep(backoff_sec * attempt)
            continue

        return result

    return last_result


def _resolve_retry_policy(cfg: BotConfig, request: dict) -> tuple[int, float]:
    req_type = request.get("type")
    market_types = {
        getattr(mt5, "ORDER_TYPE_BUY", None),
        getattr(mt5, "ORDER_TYPE_SELL", None),
    }
    is_market = req_type in market_types
    if is_market:
        attempts = max(1, int(getattr(cfg, "retry_market_attempts", 2)))
    else:
        attempts = max(1, int(getattr(cfg, "retry_pending_attempts", 3)))
    backoff = float(getattr(cfg, "retry_backoff_sec", 0.35))
    if backoff < 0:
        backoff = 0.0
    return attempts, backoff


# ─── CANCEL EXPIRED PENDING ORDERS ─────────────────────────

# Ruší ordery starší než N dnů. Per-pending limit:
#   - EXT WAVE pending (comment prefix `EWP_` / `E23_`) → cfg.ext_order_expiry_days
#   - Counter pending  (comment prefix `CNTR_`)  → NIKDY (counter se rusi jen na BOS flipu)
#   - PP pending       (comment prefix `PP_`)    → NIKDY (PP se rusi jen na nove PP vlne / BOS)
#   - pending_cancel_mode == "number"            → cfg.pending_cancel_after_days
#   - jinak                                       → cfg.order_expiry_days
def cancel_expired_pending(cfg: BotConfig) -> None:
    from config.enums import PendingCancelMode
    orders = mt5.orders_get(symbol=cfg.symbol)
    if not orders:
        return
    now = datetime.now(timezone.utc)
    default_limit = timedelta(days=int(cfg.order_expiry_days))
    ext_limit = timedelta(days=int(getattr(cfg, "ext_order_expiry_days", 7)))
    number_limit = timedelta(days=int(getattr(cfg, "pending_cancel_after_days", 14)))
    pcm_raw = getattr(cfg, "pending_cancel_mode", PendingCancelMode.NUMBER)
    try:
        pcm = PendingCancelMode(pcm_raw) if isinstance(pcm_raw, str) else pcm_raw
    except ValueError:
        pcm = PendingCancelMode.NUMBER
    use_number = pcm == PendingCancelMode.NUMBER

    for o in orders:
        if o.magic != cfg.magic:
            continue
        comment = (o.comment or "").upper()
        # EXT WAVE pending (EWP_* / E23_*) — vlastni delsi expirace, NIKDY se nezavre
        # zadnou jinou funkci nez timeoutem `ext_order_expiry_days`.
        is_ext = is_ext_wave_pending_comment(comment)
        is_counter = comment.startswith("CNTR_") or comment.startswith("ECT_") or comment.startswith("ECB_")
        is_pp = comment.startswith("PP_")
        if is_counter or is_pp:
            continue  # counter / PP nikdy neexpiruji timeoutem
        if is_ext:
            expiry_limit = ext_limit
        elif use_number:
            expiry_limit = number_limit
        else:
            expiry_limit = default_limit
        setup_time = datetime.fromtimestamp(o.time_setup, timezone.utc)
        order_age = business_time_delta(setup_time, now)
        if order_age > expiry_limit:
            req = {
                "action": mt5.TRADE_ACTION_REMOVE,
                "order":  o.ticket,
            }
            attempts, backoff = _resolve_retry_policy(cfg, req)
            result = _order_send_with_retry(req, "CANCEL_EXPIRED", max_attempts=attempts, backoff_sec=backoff)
            if result is None:
                log.warning(f"Nepodarilo se zrusit order #{o.ticket}: zadna odpoved")
                continue
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                log.info(
                    f"Zrusen expirovany order #{o.ticket} | EP={o.price_open:.5f} "
                    f"| Stari: {order_age.days}d {order_age.seconds//3600}h | "
                    f"tag={'EXT' if is_ext else ('NUMBER' if use_number else 'DEFAULT')}"
                )
            else:
                log.warning(f"Nepodarilo se zrusit order #{o.ticket}: {result.comment}")


# ─── PLACE ORDER (LIVE, TREND-FOLLOW LIMIT) ─────────────────
#
# Strategie:
#   - UP vlna  -> signal['dir'] = +1 -> primarne BUY  LIMIT na entry (= signal['fib50'])
#   - DOWN vlna -> signal['dir'] = -1 -> primarne SELL LIMIT na entry
#   - SL je na sl_fib_level (signal['sl']), tj. hloubeji v retracementu nez entry
#
# Pokud je v okamziku zpracovani vlny cena uz ZA entry (tj. pretekla pres entry
# smerem k SL), nelze poslat klasicky LIMIT. Resi entry_mode:
#   MARKET_FALLBACK - vstup za market (lot/TP prepocitan, SL drzi 0.8 fib)
#   STOP_FALLBACK   - BUY_STOP / SELL_STOP zpet na entry urovni
#   NO_FALLBACK     - vlnu preskoc
#   LIMIT_FALLBACK  - deprecated v live, chovani jako NO_FALLBACK + warning
#
# Pojistka pro vsechny mody: pokud je cena uz za SL urovni, vlna se preskoci
# (strategie selhala drive nez vubec mohla vstoupit).

def _send_request(request: dict, label: str, cfg: BotConfig):
    """Spolecna obalka pro retry policy + order_send."""
    attempts, backoff = _resolve_retry_policy(cfg, request)
    return _order_send_with_retry(request, label, max_attempts=attempts, backoff_sec=backoff)


def _build_pending_request(cfg: BotConfig, order_type: int, ep: float, sl: float,
                           tp: float | None, lot: float, wave_time: str, digits: int,
                           *, comment_override: str | None = None) -> dict:
    """
    MT5 pending request. tp=None / tp=0.0 → broker dostane TP=0.0 (= bez TP),
    coz MT5 interpretuje jako "neaktivni TP" — pozice se nezavre auto-TP.
    `comment_override` umoznuje rozlisit counter / re-entry orders v MT5 logu.
    """
    tp_val = 0.0 if tp is None else float(tp)
    return {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": cfg.symbol,
        "volume": lot,
        "type": order_type,
        "price": _round_price(ep, digits),
        "sl": _round_price(sl, digits),
        "tp": _round_price(tp_val, digits) if tp is not None else 0.0,
        "magic": cfg.magic,
        "comment": comment_override or f"W{wave_time}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_RETURN,
    }


def _build_market_request(cfg: BotConfig, order_type: int, market_price: float,
                          sl: float, tp: float | None, lot: float, wave_time: str,
                          digits: int,
                          *, comment_override: str | None = None) -> dict:
    """
    MT5 market request. tp=None / tp=0.0 → bez TP (MT5 nepouzije auto-TP).
    """
    tp_val = 0.0 if tp is None else float(tp)
    return {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": cfg.symbol,
        "volume": lot,
        "type": order_type,
        "price": _round_price(market_price, digits),
        "sl": _round_price(sl, digits),
        "tp": _round_price(tp_val, digits) if tp is not None else 0.0,
        "deviation": 20,
        "magic": cfg.magic,
        "comment": comment_override or f"W{wave_time}",
        "type_filling": mt5.ORDER_FILLING_IOC,
    }


def _log_order_placed(cfg: BotConfig, result, *, side: str, type_label: str,
                      ep: float, sl: float, tp: float | None, lot: float, wave_time: str,
                      move_pct: float | None = None, fallback: str | None = None,
                      digits: int = 5) -> None:
    extra: dict = {
        "order_id": int(result.order),
        "side": side,
        "type": type_label,
        "price": _round_price(ep, digits),
        "sl": _round_price(sl, digits),
        "tp": None if tp is None else _round_price(tp, digits),
        "volume": lot,
        "wave_id": str(wave_time),
    }
    if move_pct is not None:
        extra["move_pct"] = round(float(move_pct), 2)
    if fallback is not None:
        extra["fallback"] = fallback
    log_event(cfg, "info", "ORDER_PLACED", **extra)


def _check_min_dist(value_close: float, value_far: float, min_stop_dist: float,
                    label: str, wave_time: str, near: str, far: str) -> bool:
    """Vrati True pokud vzdalenost OK, jinak zaloguje warning a vrati False."""
    if min_stop_dist <= 0:
        return True
    dist = abs(value_close - value_far)
    if dist < min_stop_dist:
        log.warning(
            f"Preskocena vlna {wave_time} - {label} {near} moc blizko {far} | "
            f"{near}={value_close:.5f} {far}={value_far:.5f} MinDist={min_stop_dist:.5f}"
        )
        return False
    return True


def decision_prices_from_bar_close(
    bar_close: float | None,
    tick,
) -> tuple[float, float]:
    """
    Synthetic ask/bid z close posledniho uzavreneho baru (parita backtest engine).

    Rozhodnuti LIMIT vs fallback / abort bezi na techto cenach; exekuce MARKET
    porad pouziva realny tick.
    """
    if bar_close is None:
        return float(tick.ask), float(tick.bid)
    half = max((float(tick.ask) - float(tick.bid)) / 2.0, 0.0)
    bc = float(bar_close)
    return bc + half, bc - half


def send_order(
    signal: dict,
    cfg: BotConfig,
    entry_mode: EntryMode = None,
    placed_meta: dict | None = None,
    *,
    bar_close: float | None = None,
    trend_state_at_fill: TrendState | None = None,
    bypass_trend_filter: bool = False,
    is_two_sided_mirror: bool = False,
) -> bool:
    """
    Posle TREND-FOLLOW order podle signalu (dir/fib50/sl) a entry_mode.

    Primarne BUY/SELL LIMIT na fib50; pokud je cena uz za fib50 smerem k SL,
    pouzije se entry_mode pro fallback (MARKET / STOP / NO).
    """
    if entry_mode is None:
        entry_mode = cfg.entry_mode
    em_value = entry_mode.value if isinstance(entry_mode, EntryMode) else str(entry_mode)

    from runtime.live_wave_isolation import guard_live_send_order

    if guard_live_send_order(
        cfg,
        signal,
        is_two_sided_mirror=is_two_sided_mirror,
        bypass_trend_filter=bypass_trend_filter,
    ):
        return True

    if (
        not is_two_sided_mirror
        and not bool(getattr(cfg, "wave_position_enabled", True))
    ):
        return True

    ep = float(signal["fib50"])
    sl = float(signal["sl"])
    direction = int(signal["dir"])
    wave_time = signal["wave_time"]
    move_pct = signal.get("move_pct")

    is_buy = (direction == 1)

    if signal.get("counted_via_volatility_threshold", False):
        try:
            min_sl_pct = float(getattr(cfg, "ext_post_both_sides_default_sl_pct", 0.10))
            if min_sl_pct > 0:
                current_sl_pct = abs(sl - ep) / abs(ep) * 100.0
                if current_sl_pct < min_sl_pct:
                    from strategy.wave_sequence import compute_sl_price_from_pct
                    sl = compute_sl_price_from_pct(ep, min_sl_pct, is_buy=is_buy)
        except (ValueError, TypeError):
            pass

    if block_duplicate_wave_order(cfg, wave_time, label="WAVE"):
        return True

    tick = mt5.symbol_info_tick(cfg.symbol)
    info = mt5.symbol_info(cfg.symbol)
    if tick is None:
        log.error("Nelze ziskat tick data")
        return False
    if info is None:
        log.error(f"Nelze ziskat symbol_info pro {cfg.symbol}")
        return False

    point = info.point
    stops_level = info.trade_stops_level
    min_stop_dist = stops_level * point if stops_level and point else 0.0
    digits = _price_digits(info)

    is_buy = (direction == 1)
    side = "BUY" if is_buy else "SELL"
    market_price = tick.ask if is_buy else tick.bid
    decision_ask, decision_bid = decision_prices_from_bar_close(bar_close, tick)
    decision_price = decision_ask if is_buy else decision_bid

    # ── Pojistka: cena uz prosla za SL ──
    if is_buy and decision_ask <= sl:
        log.info(
            f"Preskocena vlna {wave_time} - BUY: cena uz za SL | "
            f"Ask={decision_ask:.5f} SL={sl:.5f}"
        )
        return False
    if (not is_buy) and decision_bid >= sl:
        log.info(
            f"Preskocena vlna {wave_time} - SELL: cena uz za SL | "
            f"Bid={decision_bid:.5f} SL={sl:.5f}"
        )
        return False

    # ── Pasionka před SL (abort_fib_level -> signal['fib_abort']);
    #    režim deep_retrace_shift_sl místo skip pokračuje do fallbacku se posunutým SL u MARKET.
    fa_raw = signal.get("fib_abort")
    past_abort = False
    if fa_raw is not None:
        fib_abort = float(fa_raw)
        if is_buy and decision_ask <= fib_abort:
            past_abort = True
        elif (not is_buy) and decision_bid >= fib_abort:
            past_abort = True

    if past_abort:
        if not abort_fib_shift_sl_mode(cfg):
            log.info(
                f"Preskocena vlna {wave_time} - {side}: cena na/za abort Fib | "
                f"{('Ask' if is_buy else 'Bid')}={decision_price:.5f} "
                f"Abort={fib_abort:.5f} SL={sl:.5f}"
            )
            return False

    risk_span = (
        abs(ep - sl)
        if (past_abort and abort_fib_shift_sl_mode(cfg))
        else None
    )

    # ── Rozhodnuti: LIMIT (primary) vs FALLBACK ──
    # BUY:  LIMIT pokud Ask > ep (cena nad entry, ceka na pokles)
    # SELL: LIMIT pokud Bid < ep (cena pod entry, ceka na rust)
    can_limit = (decision_ask > ep) if is_buy else (decision_bid < ep)

    if can_limit:
        return _place_limit_primary(
            cfg, side=side, is_buy=is_buy, ep=ep, sl=sl, lot_calc=True,
            wave_time=wave_time, move_pct=move_pct,
            tick=tick, min_stop_dist=min_stop_dist, digits=digits,
            signal=signal, placed_meta=placed_meta,
            is_two_sided_mirror=is_two_sided_mirror,
        )

    # ── Fallback (cena je uz za entry smerem k SL) ──
    if em_value == "no_fallback":
        log.info(
            f"Preskocena vlna {wave_time} - {side} fallback vypnut | "
            f"EP={ep:.5f} {('Ask' if is_buy else 'Bid')}={decision_price:.5f}"
        )
        return False

    if em_value == "limit_fallback":
        # Stara hodnota pro backtester. V live trend-follow strategii nedava smysl
        # (LIMIT primary je defaultni cesta, fallback by uz fungoval proti smeru).
        log.warning(
            f"Preskocena vlna {wave_time} - {side} entry_mode='limit_fallback' "
            f"je deprecated v live (trend-follow). Pouzij MARKET_FALLBACK / STOP_FALLBACK / NO_FALLBACK."
        )
        return False

    if em_value == "market_fallback":
        return _place_market_fallback(
            cfg, side=side, is_buy=is_buy, sl=sl, wave_time=wave_time,
            tick=tick, min_stop_dist=min_stop_dist, digits=digits,
            signal=signal, risk_span=risk_span, placed_meta=placed_meta,
            trend_state_at_fill=trend_state_at_fill,
            bypass_trend_filter=bypass_trend_filter,
            is_two_sided_mirror=is_two_sided_mirror,
        )

    if em_value == "stop_fallback":
        return _place_stop_fallback(
            cfg, side=side, is_buy=is_buy, ep=ep, sl=sl, wave_time=wave_time,
            move_pct=move_pct, tick=tick, min_stop_dist=min_stop_dist, digits=digits,
            signal=signal, placed_meta=placed_meta,
        )

    log.error(f"Neznamy ENTRY_MODE: {em_value}")
    return False


def _fmt_tp(tp: float | None) -> str:
    """Formatuje TP cenu pro log (None -> "—" = bez TP)."""
    return "—" if tp is None else f"{float(tp):.5f}"


def _store_placed_order_meta(
    placed_meta: dict | None,
    *,
    entry_price: float,
    sl_price: float,
    tp_price: float | None,
    order_type: str,
    wave_time: str,
) -> None:
    if placed_meta is None:
        return
    placed_meta.clear()
    placed_meta.update(
        {
            "entry_price": float(entry_price),
            "sl_price": float(sl_price),
            "tp_price": (None if tp_price is None else float(tp_price)),
            "order_type": str(order_type),
            "wave_time": str(wave_time),
        }
    )


def _place_limit_primary(cfg: BotConfig, *, side: str, is_buy: bool, ep: float,
                         sl: float, lot_calc: bool, wave_time: str,
                         move_pct: float | None, tick, min_stop_dist: float,
                         digits: int, signal: dict | None = None,
                         placed_meta: dict | None = None,
                         is_two_sided_mirror: bool = False) -> bool:
    """Primarni BUY/SELL LIMIT na entry (fib50).

    TP se resi pres `resolve_effective_tp(cfg, signal, ep, sl, is_buy)` —
    pro BOS_EXIT_PRIORITY / WAVE_TARGET_N (K<N nebo non-TP-wave) vraci None
    a broker pak dostane TP=0.0 (= bez TP). Min-stop-dist kontrola pro TP se
    pri tp=None preskoci.
    """
    lot = calc_lot(ep, sl, cfg)
    if signal is None:
        signal = {}
    tp = resolve_effective_tp(cfg, signal, ep, sl, is_buy)

    market_ref = tick.ask if is_buy else tick.bid
    label = f"{side}_LIMIT"
    if not _check_min_dist(market_ref, ep, min_stop_dist, label, wave_time,
                           near="EP", far=("Ask" if is_buy else "Bid")):
        return False
    if not _check_min_dist(ep, sl, min_stop_dist, label, wave_time, near="SL", far="EP"):
        return False
    if tp is not None:
        if not _check_min_dist(tp, ep, min_stop_dist, label, wave_time, near="TP", far="EP"):
            return False

    order_type = mt5.ORDER_TYPE_BUY_LIMIT if is_buy else mt5.ORDER_TYPE_SELL_LIMIT
    if is_two_sided_mirror:
        comment = f"{TWO_SIDED_MIRROR_COMMENT_PREFIX}{wave_time}"[:31]
    elif signal is not None and is_ext_wave(signal, cfg):
        comment = f"{EXT_PRIMARY_WAVE_COMMENT_PREFIX}{wave_time}"[:31]
    else:
        comment = None
    request = _build_pending_request(
        cfg, order_type, ep, sl, tp, lot, wave_time, digits,
        comment_override=comment,
    )
    result = _send_request(request, label, cfg)

    if result is None:
        log.error(f"CHYBA order_send {label}: zadna odpoved | {mt5.last_error()}")
        return False
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        log.info(
            f"OK Prikaz odeslan | {label} "
            f"EP={ep:.5f} SL={sl:.5f} TP={_fmt_tp(tp)} Lot={lot} "
            f"(Wave {move_pct:.2f}% | {wave_time})" if move_pct is not None else
            f"OK Prikaz odeslan | {label} EP={ep:.5f} SL={sl:.5f} TP={_fmt_tp(tp)} Lot={lot} | Wave {wave_time}"
        )
        _store_placed_order_meta(
            placed_meta,
            entry_price=ep,
            sl_price=sl,
            tp_price=tp,
            order_type=label,
            wave_time=wave_time,
        )
        _log_order_placed(cfg, result, side=side, type_label=label,
                          ep=ep, sl=sl, tp=tp, lot=lot, wave_time=wave_time,
                          move_pct=move_pct, digits=digits)
        return True
    log.error(f"CHYBA order_send {label}: retcode={result.retcode} | {result.comment}")
    return False


def _place_market_fallback(cfg: BotConfig, *, side: str, is_buy: bool, sl: float,
                           wave_time: str, tick, min_stop_dist: float, digits: int,
                           signal: dict | None = None,
                           risk_span: float | None = None,
                           placed_meta: dict | None = None,
                           trend_state_at_fill: TrendState | None = None,
                           bypass_trend_filter: bool = False,
                           is_two_sided_mirror: bool = False) -> bool:
    """MARKET fallback: vstup za aktualni cenu, lot/TP prepocitan, SL dle fib nebo risk_span (abort_shift_sl).

    TP resi `resolve_effective_tp`. None = bez TP (broker dostane TP=0.0).
    """
    if signal is None:
        signal = {}
    allowed, reason = entry_allowed_at_fill_bar(
        signal,
        trend_state_at_fill,
        cfg,
        bypass_trend_filter=bypass_trend_filter,
        is_two_sided_mirror=is_two_sided_mirror,
    )
    if not allowed:
        log.info(
            f"Preskocena vlna {wave_time} - {side} MARKET fallback: "
            f"trend re-check na fill baru ({reason})"
        )
        log_event(
            cfg,
            "info",
            "MARKET_FALLBACK_SKIPPED_TREND_FILTER",
            wave_id=str(wave_time),
            side=side,
            reason=reason,
            trend=getattr(trend_state_at_fill, "direction", "unknown")
            if trend_state_at_fill is not None
            else "unknown",
        )
        return False
    market_price = tick.ask if is_buy else tick.bid
    sl_eff = float(sl)
    if risk_span is not None and risk_span > 0:
        sl_eff = (market_price - risk_span) if is_buy else (market_price + risk_span)
        log.info(
            f"[ABORT_SHIFT_SL] {side} MARKET | Wave {wave_time} | "
            f"cena={market_price:.5f} risk_span={risk_span:.5f} → SL={sl_eff:.5f}"
        )
    lot = calc_lot(market_price, sl_eff, cfg)
    if signal is None:
        signal = {}
    tp = resolve_effective_tp(cfg, signal, market_price, sl_eff, is_buy)
    label = f"{side}_MARKET"

    if not _check_min_dist(market_price, sl_eff, min_stop_dist, label, wave_time,
                           near=("Ask" if is_buy else "Bid"), far="SL"):
        return False
    if tp is not None:
        if not _check_min_dist(tp, market_price, min_stop_dist, label, wave_time,
                               near="TP", far=("Ask" if is_buy else "Bid")):
            return False

    order_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
    if is_two_sided_mirror:
        comment = f"{TWO_SIDED_MIRROR_COMMENT_PREFIX}{wave_time}"[:31]
    elif is_ext_wave(signal, cfg):
        comment = f"{EXT_PRIMARY_WAVE_COMMENT_PREFIX}{wave_time}"[:31]
    else:
        comment = None
    request = _build_market_request(
        cfg, order_type, market_price, sl_eff, tp, lot, wave_time, digits,
        comment_override=comment,
    )
    result = _send_request(request, label, cfg)

    if result is None:
        log.error(f"CHYBA order_send {label}: zadna odpoved | {mt5.last_error()}")
        return False
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        log.info(
            f"[MARKET-FALLBACK] {side} | Wave {wave_time} | "
            f"{('Ask' if is_buy else 'Bid')}={market_price:.5f} SL={sl_eff:.5f} TP={_fmt_tp(tp)} Lot={lot}"
        )
        _store_placed_order_meta(
            placed_meta,
            entry_price=market_price,
            sl_price=sl_eff,
            tp_price=tp,
            order_type=label,
            wave_time=wave_time,
        )
        _log_order_placed(cfg, result, side=side, type_label=label,
                          ep=market_price, sl=sl_eff, tp=tp, lot=lot, wave_time=wave_time,
                          fallback="MARKET", digits=digits)
        return True
    log.error(f"CHYBA order_send {label}: retcode={result.retcode} | {result.comment}")
    return False


def _place_stop_fallback(cfg: BotConfig, *, side: str, is_buy: bool, ep: float,
                         sl: float, wave_time: str, move_pct: float | None,
                         tick, min_stop_dist: float, digits: int,
                         signal: dict | None = None,
                         placed_meta: dict | None = None) -> bool:
    """STOP fallback: BUY_STOP / SELL_STOP zpet na entry urovni (fib50).

    TP resi `resolve_effective_tp` (None = bez TP).
    """
    lot = calc_lot(ep, sl, cfg)
    if signal is None:
        signal = {}
    tp = resolve_effective_tp(cfg, signal, ep, sl, is_buy)
    label = f"{side}_STOP"

    market_ref = tick.ask if is_buy else tick.bid
    # Pro BUY STOP: ep musi byt nad Ask. Pro SELL STOP: ep musi byt pod Bid.
    if is_buy and ep <= market_ref:
        log.info(
            f"Preskocena vlna {wave_time} - {label} fallback nelze: EP <= Ask | "
            f"EP={ep:.5f} Ask={market_ref:.5f}"
        )
        return False
    if (not is_buy) and ep >= market_ref:
        log.info(
            f"Preskocena vlna {wave_time} - {label} fallback nelze: EP >= Bid | "
            f"EP={ep:.5f} Bid={market_ref:.5f}"
        )
        return False

    if not _check_min_dist(ep, market_ref, min_stop_dist, label, wave_time,
                           near="EP", far=("Ask" if is_buy else "Bid")):
        return False
    if not _check_min_dist(ep, sl, min_stop_dist, label, wave_time, near="SL", far="EP"):
        return False
    if tp is not None:
        if not _check_min_dist(tp, ep, min_stop_dist, label, wave_time, near="TP", far="EP"):
            return False

    order_type = mt5.ORDER_TYPE_BUY_STOP if is_buy else mt5.ORDER_TYPE_SELL_STOP
    if signal is not None and is_ext_wave(signal, cfg):
        comment = f"{EXT_PRIMARY_WAVE_COMMENT_PREFIX}{wave_time}"[:31]
    else:
        comment = None
    request = _build_pending_request(
        cfg, order_type, ep, sl, tp, lot, wave_time, digits,
        comment_override=comment,
    )
    result = _send_request(request, label, cfg)

    if result is None:
        log.error(f"CHYBA order_send {label}: zadna odpoved | {mt5.last_error()}")
        return False
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        log.info(
            f"[STOP-FALLBACK] {label} | Wave {wave_time} | "
            f"EP={ep:.5f} SL={sl:.5f} TP={_fmt_tp(tp)} Lot={lot}"
        )
        _store_placed_order_meta(
            placed_meta,
            entry_price=ep,
            sl_price=sl,
            tp_price=tp,
            order_type=label,
            wave_time=wave_time,
        )
        _log_order_placed(cfg, result, side=side, type_label=label,
                          ep=ep, sl=sl, tp=tp, lot=lot, wave_time=wave_time,
                          move_pct=move_pct, fallback="STOP", digits=digits)
        return True
    log.error(f"CHYBA order_send {label}: retcode={result.retcode} | {result.comment}")
    return False


# ─── STARTUP: PENDING ONLY ───────────────────────────────────
def send_startup_pending_only(
    signal: dict,
    cfg: BotConfig,
    *,
    pine_recovery: bool = False,
    bar_close: float | None = None,
) -> bool:
    """
    Posle pouze pending LIMIT order - bez market / stop fallbacku.
    Pouziva se ve startup recovery, abychom nezpoznili obnoveni starych
    setupu otevrenim nahodneho marketu pri restartu bota.

    pine_recovery=True: simulace (pine) potvrdila, ze LIMIT jeste nebyl fillnut
    na uzavrenych barech — LIMIT posleme i kdyz aktualni tick je za entry
    (vikendovy gap / spread). Rozhodnuti SL/abort na bar_close pokud je k dispozici.

    Trend-follow (bez pine_recovery):
      BUY  LIMIT pokud Ask > ep (cena nad entry, ceka se pokles)
      SELL LIMIT pokud Bid < ep (cena pod entry, ceka se rust)
      Jinak setup preskocime - cena uz je za entry, recovery NEDOPLNUJE.
    """
    ep = float(signal["fib50"])
    from runtime.live_wave_isolation import guard_live_send_order

    if guard_live_send_order(cfg, signal):
        return True

    sl = float(signal["sl"])
    direction = int(signal["dir"])
    wave_time = signal["wave_time"]

    if block_duplicate_wave_order(cfg, wave_time, label="WAVE_STARTUP"):
        return True

    tick = mt5.symbol_info_tick(cfg.symbol)
    info = mt5.symbol_info(cfg.symbol)
    if tick is None:
        log.error("STARTUP: Nelze ziskat tick data")
        return False
    if info is None:
        log.error(f"STARTUP: Nelze ziskat symbol_info pro {cfg.symbol}")
        return False

    point = info.point
    stops_level = info.trade_stops_level
    min_stop_dist = stops_level * point if stops_level and point else 0.0
    digits = _price_digits(info)

    is_buy = (direction == 1)
    side = "BUY" if is_buy else "SELL"
    decision_ask, decision_bid = decision_prices_from_bar_close(bar_close, tick)

    # SL pojistka (parita send_order — rozhodnuti na close baru pokud znamy)
    if is_buy and decision_ask <= sl:
        log.info(
            f"STARTUP SKIP {wave_time} | BUY cena uz za SL | "
            f"Ask={decision_ask:.5f} SL={sl:.5f}"
        )
        return False
    if (not is_buy) and decision_bid >= sl:
        log.info(
            f"STARTUP SKIP {wave_time} | SELL cena uz za SL | "
            f"Bid={decision_bid:.5f} SL={sl:.5f}"
        )
        return False

    fa_raw = signal.get("fib_abort")
    past_abort = False
    if fa_raw is not None:
        fib_abort = float(fa_raw)
        if is_buy and decision_ask <= fib_abort:
            past_abort = True
        elif (not is_buy) and decision_bid >= fib_abort:
            past_abort = True

    if past_abort and not abort_fib_shift_sl_mode(cfg):
        log.info(
            f"STARTUP SKIP {wave_time} | {side} na/za abort Fib | "
            f"{('Ask' if is_buy else 'Bid')}={(decision_ask if is_buy else decision_bid):.5f} "
            f"Abort={fib_abort:.5f}"
        )
        return False

    if not pine_recovery:
        # Cena uz prosla entry -> recovery pro bezpecnost preskoci
        can_limit = (tick.ask > ep) if is_buy else (tick.bid < ep)
        if not can_limit:
            log.info(
                f"STARTUP SKIP {wave_time} | {side} cena uz za entry | "
                f"EP={ep:.5f} {('Ask' if is_buy else 'Bid')}={(tick.ask if is_buy else tick.bid):.5f}"
            )
            return False

    lot = calc_lot(ep, sl, cfg)
    # TP resi resolve_effective_tp — None znamena bez TP (broker dostane 0.0).
    tp = resolve_effective_tp(cfg, signal, ep, sl, is_buy)
    label = f"{side}_LIMIT"

    market_ref = tick.ask if is_buy else tick.bid
    if min_stop_dist > 0:
        if abs(market_ref - ep) < min_stop_dist:
            log.info(
                f"STARTUP SKIP {wave_time} | {label} EP moc blizko trhu | "
                f"EP={ep:.5f} {('Ask' if is_buy else 'Bid')}={market_ref:.5f} "
                f"MinDist={min_stop_dist:.5f}"
            )
            return False
        if abs(ep - sl) < min_stop_dist:
            log.info(
                f"STARTUP SKIP {wave_time} | {label} SL moc blizko EP | "
                f"EP={ep:.5f} SL={sl:.5f} MinDist={min_stop_dist:.5f}"
            )
            return False
        if tp is not None and abs(tp - ep) < min_stop_dist:
            log.info(
                f"STARTUP SKIP {wave_time} | {label} TP moc blizko EP | "
                f"EP={ep:.5f} TP={tp:.5f} MinDist={min_stop_dist:.5f}"
            )
            return False

    order_type = mt5.ORDER_TYPE_BUY_LIMIT if is_buy else mt5.ORDER_TYPE_SELL_LIMIT
    if is_ext_wave(signal, cfg):
        comment = f"{EXT_PRIMARY_WAVE_COMMENT_PREFIX}{wave_time}"[:31]
    else:
        comment = None
    request = _build_pending_request(
        cfg, order_type, ep, sl, tp, lot, wave_time, digits,
        comment_override=comment,
    )
    result = _send_request(request, "STARTUP_PENDING", cfg)

    if result is None:
        log.error(f"STARTUP CHYBA order_send: zadna odpoved | {mt5.last_error()}")
        return False
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        log.info(
            f"STARTUP RECOVERY OK | {label} "
            f"EP={ep:.5f} SL={sl:.5f} TP={_fmt_tp(tp)} Lot={lot} | Wave {wave_time}"
        )
        return True
    log.error(f"STARTUP CHYBA order_send: retcode={result.retcode} | {result.comment}")
    return False


# ─── SESSION MANAGER: CANCEL ALL PENDINGS ─────────────────────
def cancel_all_pendings(cfg: BotConfig) -> int:
    """
    Zrusi VSECHNY pending ordery pro tento bot (filtrovano podle magic + symbol).
    Pouziva se pri pre-close v session manageru.
    Vraci pocet uspesne zrusenych orderu.
    """
    orders = mt5.orders_get(symbol=cfg.symbol)
    if not orders:
        log.info("SESSION CANCEL: zadne pending ordery k zruseni")
        return 0

    cancelled = 0
    for o in orders:
        if o.magic != cfg.magic:
            continue
        req = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order":  o.ticket,
        }
        attempts, backoff = _resolve_retry_policy(cfg, req)
        result = _order_send_with_retry(req, "SESSION_CANCEL", max_attempts=attempts, backoff_sec=backoff)
        if result is None:
            log.warning(f"SESSION CANCEL: nepodarilo se zrusit order #{o.ticket}: zadna odpoved")
            continue
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            cancelled += 1
            log.info(
                f"SESSION CANCEL: zrusen order #{o.ticket} | EP={o.price_open:.5f} | "
                f"comment={o.comment}"
            )
        else:
            log.warning(
                f"SESSION CANCEL: nepodarilo se zrusit order #{o.ticket}: {result.comment}"
            )

    log.info(f"SESSION CANCEL: zruseno {cancelled} pending orderu")
    return cancelled


# ─── SESSION MANAGER: CLOSE ALL POSITIONS (volitelne pred tydennim close) ─
def close_all_positions(cfg: BotConfig) -> int:
    """
    Zavre VSECHNY otevrene pozice tohoto bota (filtrovano podle magic + symbol).
    Pouziva se v session manageru kdyz session_close_positions_on_friday=True (den session_week_close_*).
    Vraci pocet uspesne zavrenych pozic.
    """
    positions = mt5.positions_get(symbol=cfg.symbol)
    if not positions:
        log.info("SESSION CLOSE: zadne otevrene pozice k zavreni")
        return 0

    closed = 0
    for p in positions:
        if p.magic != cfg.magic:
            continue
        tick = mt5.symbol_info_tick(cfg.symbol)
        if tick is None:
            log.error(f"SESSION CLOSE: nelze ziskat tick pro {cfg.symbol}")
            continue
        # Zavreni: opacny smer
        if p.type == mt5.POSITION_TYPE_BUY:
            close_type = mt5.ORDER_TYPE_SELL
            price = tick.bid
        else:
            close_type = mt5.ORDER_TYPE_BUY
            price = tick.ask

        req = {
            "action":    mt5.TRADE_ACTION_DEAL,
            "symbol":    cfg.symbol,
            "volume":    p.volume,
            "type":      close_type,
            "position":  p.ticket,
            "price":     _round_price(price, _price_digits(mt5.symbol_info(cfg.symbol))),
            "deviation": 20,
            "magic":     cfg.magic,
            "comment":   f"SESSION_CLOSE_W{p.comment[1:] if p.comment.startswith('W') else ''}",
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        attempts, backoff = _resolve_retry_policy(cfg, req)
        result = _order_send_with_retry(req, "SESSION_CLOSE", max_attempts=attempts, backoff_sec=backoff)
        if result is None:
            log.warning(f"SESSION CLOSE: nepodarilo se zavrit pozici #{p.ticket}: zadna odpoved")
            continue
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            closed += 1
            log.info(
                f"SESSION CLOSE: zavrena pozice #{p.ticket} | "
                f"vol={p.volume} | open={p.price_open:.5f} | close={price:.5f}"
            )
        else:
            log.warning(
                f"SESSION CLOSE: nepodarilo se zavrit pozici #{p.ticket}: {result.comment}"
            )

    log.info(f"SESSION CLOSE: zavreno {closed} pozic")
    return closed


# ─── BOS EXIT: CANCEL PENDINGS BY DIRECTION ───────────────────
def cancel_pendings_by_direction(cfg: BotConfig, direction: int,
                                  *, reason: str = "BOS_CANCEL",
                                  waves: list | None = None) -> int:
    """
    Zrusi VSECHNY pending ordery tohoto bota (filtr magic + symbol) ktere
    odpovidaji zadanemu smeru:
      direction = +1  → BUY_LIMIT / BUY_STOP
      direction = -1  → SELL_LIMIT / SELL_STOP

    OCHRANA — tyto pendingy se NIKDY nerusi touto funkci:
      - EXT WAVE pendingy        (comment prefix EWP_ / E23_) — vlastni `ext_order_expiry_days`
      - Counter pendingy         (CNTR_)               — rusi se jen pri opacnem BOS flipu
      - Counter time/bos (ext)   (ECT_ / ECB_)         — rusi se jen vlastni logikou
      - PP pendingy              (PP_)                 — rusi se jen pri nove PP vlne

    Pouziva se v live BOS exit logice (`runtime.live_loop`) pri prevratu trendu
    pro `tp_mode = BOS_EXIT/BOS_EXIT_PRIORITY/WAVE_TARGET_N` NEBO pri
    `pending_cancel_mode = "trend"`.

    Returns:
        Pocet uspesne zrusenych pendingu.
    """
    if direction not in (1, -1):
        log.error(f"cancel_pendings_by_direction: neplatny direction={direction}")
        return 0

    orders = mt5.orders_get(symbol=cfg.symbol)
    if not orders:
        return 0

    buy_types = {mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_BUY_STOP}
    sell_types = {mt5.ORDER_TYPE_SELL_LIMIT, mt5.ORDER_TYPE_SELL_STOP}
    target_types = buy_types if direction == 1 else sell_types
    cancelled = 0
    waves_by_time = {str(w.get("wave_time", "")): w for w in (waves or [])}

    for o in orders:
        if int(getattr(o, "magic", -1)) != int(cfg.magic):
            continue
        if int(o.type) not in target_types:
            continue
        comment = (o.comment or "").upper()
        if (is_ext_wave_pending_comment(comment)
                or comment.startswith("CNTR_")
                or comment.startswith("ECT_")
                or comment.startswith("ECB_")
                or comment.startswith("PP_")):
            continue
        if pending_protected_from_bos_direction_cancel_by_comment(
            o.comment or "", cfg, waves_by_time
        ):
            continue

        req = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order":  o.ticket,
        }
        attempts, backoff = _resolve_retry_policy(cfg, req)
        result = _order_send_with_retry(req, reason, max_attempts=attempts, backoff_sec=backoff)
        if result is None:
            log.warning(f"{reason}: nepodarilo se zrusit pending #{o.ticket}: zadna odpoved")
            continue
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            cancelled += 1
            log.info(
                f"{reason}: zrusen pending #{o.ticket} | "
                f"EP={o.price_open:.5f} | dir={'BUY' if direction == 1 else 'SELL'}"
            )
        else:
            log.warning(f"{reason}: nepodarilo se zrusit pending #{o.ticket}: {result.comment}")

    if cancelled > 0:
        log.info(f"{reason}: zruseno {cancelled} pendingu smeru {'BUY' if direction == 1 else 'SELL'}")
    return cancelled


def cancel_counter_trend_wave_pendings(
    cfg: BotConfig,
    trend_state: TrendState | None,
    waves: list,
) -> int:
    """
    Zrusi bezne W-pendingy, ktere by na aktualnim baru neprosly trend filtrem
    (ekvivalent backtest `_trigger_pending` skip fill).
    """
    if not getattr(cfg, "trend_filter_enabled", False) or trend_state is None:
        return 0

    orders = mt5.orders_get(symbol=cfg.symbol)
    if not orders:
        return 0

    waves_by_time = {str(w.get("wave_time", "")): w for w in waves}
    buy_types = {mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_BUY_STOP}
    cancelled = 0

    for o in orders:
        if int(getattr(o, "magic", -1)) != int(cfg.magic):
            continue
        comment = (o.comment or "").strip()
        if not comment.upper().startswith("W"):
            continue
        wave_time = comment[1:]
        wave = waves_by_time.get(wave_time)
        if wave is None:
            odir = 1 if int(o.type) in buy_types else -1
            wave = {
                "dir": odir,
                "wave_time": wave_time,
                "box_top": 0.0,
                "box_bottom": 0.0,
            }

        allowed, reason = entry_allowed_at_fill_bar(wave, trend_state, cfg)
        if allowed:
            continue

        req = {"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket}
        attempts, backoff = _resolve_retry_policy(cfg, req)
        result = _order_send_with_retry(
            req, "TREND_FILL_GUARD", max_attempts=attempts, backoff_sec=backoff
        )
        if result is None:
            log.warning(
                f"TREND_FILL_GUARD: nepodarilo se zrusit pending #{o.ticket}: zadna odpoved"
            )
            continue
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            cancelled += 1
            log.info(
                f"TREND_FILL_GUARD: zrusen pending #{o.ticket} | wave={wave_time} | "
                f"reason={reason}"
            )
            log_event(
                cfg,
                "info",
                "PENDING_CANCELLED_TREND_FILL_GUARD",
                order_id=int(o.ticket),
                wave_id=str(wave_time),
                reason=reason,
                trend=getattr(trend_state, "direction", "unknown"),
            )
        else:
            log.warning(
                f"TREND_FILL_GUARD: nepodarilo se zrusit pending #{o.ticket}: "
                f"{result.comment}"
            )

    if cancelled > 0:
        log.info(f"TREND_FILL_GUARD: zruseno {cancelled} counter-trend pendingu")
    return cancelled


# ─── BOS EXIT: CLOSE POSITIONS BY DIRECTION ───────────────────
def close_positions_by_direction(cfg: BotConfig, direction: int,
                                  *, reason: str = "BOS_EXIT",
                                  protected_wave_times: set[str] = None,
                                  protect_ext_block_from_wave: str | None = None,
                                  ext1_protection_per_bar: list[bool] | None = None,
                                  current_bar_idx: int | None = None,
                                  bar_high: float | None = None,
                                  bar_low: float | None = None,
                                  wave_birth_by_time: dict[str, int] | None = None,
                                  main_trend_dir: int = 0) -> int:
    """
    Zavre VSECHNY otevrene pozice tohoto bota (filtr magic + symbol) ktere
    maji zadany smer:
      direction = +1  → zavre vsechny BUY pozice
      direction = -1  → zavre vsechny SELL pozice

    Pouziva se v live BOS exit logice (`runtime.live_loop`) pri prevratu trendu:
      - bull → bear: zavre vsechny BUY pozice (direction=+1)
      - bear → bull: zavre vsechny SELL pozice (direction=-1)

    Args:
        cfg:        BotConfig (pro magic + symbol)
        direction:  +1 nebo -1
        reason:     textovy duvod v MT5 comment + log (default "BOS_EXIT")
        protected_wave_times: Vlny chranene proti zavreni (napr. wave_2_no_tp).
        protect_ext_block_from_wave: Parent EXT wave_time — pri EXT BOS 0,35 close
            se nezaviraji E23_/ECT_/ECB_ prave z teto EXT (ostatni EXT block ano).
        ext1_protection_per_bar: Per-bar EXT-1 ochrana (parita s backtest engine).
        current_bar_idx: Index aktualniho baru v `ext1_protection_per_bar`.

    Returns:
        Pocet uspesne zavrenych pozic.
    """
    if direction not in (1, -1):
        log.error(f"close_positions_by_direction: neplatny direction={direction}")
        return 0

    positions = mt5.positions_get(symbol=cfg.symbol)
    if not positions:
        return 0

    from infra.trade_tracker import _wave_id_from_comment

    target_type = mt5.POSITION_TYPE_BUY if direction == 1 else mt5.POSITION_TYPE_SELL
    closed = 0

    for p in positions:
        if int(getattr(p, "magic", -1)) != int(cfg.magic):
            continue
        if int(p.type) != int(target_type):
            continue

        orig_comment = str(getattr(p, "comment", "") or "")
        is_buy = int(p.type) == int(getattr(mt5, "POSITION_TYPE_BUY", -1))
        trade_view = _Mt5PositionTradeView(
            pos_dir=1 if is_buy else -1,
            comment=orig_comment,
        )
        
        # 1) Stejna parent EXT — beze zmeny chovani (chranena)
        if protect_ext_block_from_wave and is_ext_block_trade_on_parent_wave(
            trade_view, str(protect_ext_block_from_wave),
        ):
            sl = float(getattr(p, "sl", 0.0) or 0.0)
            if (
                bar_high is not None
                and bar_low is not None
                and _tp_wave_sl_hit_on_bar(
                    is_buy=is_buy,
                    sl=sl,
                    bar_high=float(bar_high),
                    bar_low=float(bar_low),
                )
            ):
                if _close_mt5_position_market(
                    cfg,
                    p,
                    reason="SL",
                    position_kind="EXT_BLOCK",
                    digits=_price_digits(mt5.symbol_info(cfg.symbol)),
                ):
                    closed += 1
            continue
            
        # 2) NOVE: EXT block z jine parent EXT v okne sve parent vlny — chranena
        if wave_birth_by_time is not None and current_bar_idx is not None:
            if is_trade_within_parent_ext_window(
                trade_view,
                wave_birth_by_time=wave_birth_by_time,
                bar_idx=current_bar_idx,
            ):
                sl = float(getattr(p, "sl", 0.0) or 0.0)
                if (
                    bar_high is not None
                    and bar_low is not None
                    and _tp_wave_sl_hit_on_bar(
                        is_buy=is_buy,
                        sl=sl,
                        bar_high=float(bar_high),
                        bar_low=float(bar_low),
                    )
                ):
                    if _close_mt5_position_market(
                        cfg,
                        p,
                        reason="SL",
                        position_kind="EXT_BLOCK",
                        digits=_price_digits(mt5.symbol_info(cfg.symbol)),
                    ):
                        closed += 1
                continue

        # UZIVATELSKY POZADAVEK: flip-follower (WAVE_COUNTER, EXT primary WAVE, …)
        # nesmi per-bar BOS close zavrit — ceka na flip nebo SL.
        # Vyjimka: broken_dir batch (trade.dir == direction) ma follower zavrit.
        if (
            is_bos_flip_follower_trade(trade_view)
            and int(trade_view.dir) != int(direction)
        ):
            sl = float(getattr(p, "sl", 0.0) or 0.0)
            if (
                bar_high is not None
                and bar_low is not None
                and sl > 0.0
                and _tp_wave_sl_hit_on_bar(
                    is_buy=is_buy,
                    sl=sl,
                    bar_high=float(bar_high),
                    bar_low=float(bar_low),
                )
            ):
                if _close_mt5_position_market(
                    cfg,
                    p,
                    reason="SL",
                    position_kind="FLIP_FOLLOWER",
                    digits=_price_digits(mt5.symbol_info(cfg.symbol)),
                ):
                    closed += 1
            continue

        # UZIVATELSKY POZADAVEK: Vsechny counter pozice (WAVE_COUNTER, EXT_COUNTER, TWO_SIDED_MIRROR)
        # musi prezit EXT_BOS_CLOSE a nesmi se zavrit.
        if reason == "EXT_BOS_CLOSE":
            if is_bos_flip_follower_trade(trade_view):
                continue

        wave_id = _wave_id_from_comment(orig_comment)
        if (
            ext1_protection_per_bar is not None
            and current_bar_idx is not None
            and ext1_close_blocked_on_bar(
                int(current_bar_idx),
                ext1_protection_per_bar,
                cfg,
                reason,
                trade=trade_view,
                main_trend_dir=main_trend_dir,
            )
        ):
            log_event(
                cfg, "info", "EXT1_PROTECT_SKIP_CLOSE",
                reason=reason,
                direction="BUY" if direction == 1 else "SELL",
                bar_idx=int(current_bar_idx),
                ticket=int(p.ticket),
            )
            continue

        if protected_wave_times and wave_id in protected_wave_times:
            log_event(
                cfg, "info", "WAVE_2_NO_TP_PROTECTED",
                ticket=int(p.ticket),
                wave_id=wave_id,
                reason="Protected by wave_2_no_tp"
            )
            continue

        tick = mt5.symbol_info_tick(cfg.symbol)
        if tick is None:
            log.error(f"{reason}: nelze ziskat tick pro {cfg.symbol}")
            continue
        # Uzaviraci order opacneho smeru
        if p.type == mt5.POSITION_TYPE_BUY:
            close_type = mt5.ORDER_TYPE_SELL
            price = tick.bid
        else:
            close_type = mt5.ORDER_TYPE_BUY
            price = tick.ask

        # Bezpecny comment (MT5 limit ~30 znaku, ne vsechny brokeri pripustni)
        orig_comment = str(getattr(p, "comment", "") or "")
        wave_suffix = orig_comment[1:] if orig_comment.startswith("W") else ""
        new_comment = f"{reason}_W{wave_suffix}"[:31]

        req = {
            "action":    mt5.TRADE_ACTION_DEAL,
            "symbol":    cfg.symbol,
            "volume":    p.volume,
            "type":      close_type,
            "position":  p.ticket,
            "price":     _round_price(price, _price_digits(mt5.symbol_info(cfg.symbol))),
            "deviation": 20,
            "magic":     cfg.magic,
            "comment":   new_comment,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        attempts, backoff = _resolve_retry_policy(cfg, req)
        result = _order_send_with_retry(req, reason, max_attempts=attempts, backoff_sec=backoff)
        if result is None:
            log.warning(f"{reason}: nepodarilo se zavrit pozici #{p.ticket}: zadna odpoved")
            continue
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            closed += 1
            log.info(
                f"{reason}: zavrena pozice #{p.ticket} | "
                f"vol={p.volume} | open={p.price_open:.5f} | close={price:.5f} | "
                f"dir={'BUY' if direction == 1 else 'SELL'}"
            )
            log_event(
                cfg, "info", reason,
                ticket=int(p.ticket),
                direction="BUY" if direction == 1 else "SELL",
                volume=float(p.volume),
                price_open=float(p.price_open),
                price_close=float(price),
                wave_comment=orig_comment,
            )
        else:
            log.warning(f"{reason}: nepodarilo se zavrit pozici #{p.ticket}: {result.comment}")

    if closed > 0:
        log.info(f"{reason}: zavreno {closed} pozic smeru {'BUY' if direction == 1 else 'SELL'}")
    return closed


# ============================================================================
# WAVE_TARGET_N — TP-wave eventy (live)
# ============================================================================

# Prefix v MT5 comment — viz konstanty na zacatku modulu (CNTR_, TS2_, …).


def _tp_wave_sl_hit_on_bar(
    *, is_buy: bool, sl: float, bar_high: float, bar_low: float,
) -> bool:
    if sl <= 0.0:
        return False
    if is_buy:
        return bar_low <= sl
    return bar_high >= sl


def _close_mt5_position_market(
    cfg: BotConfig,
    p,
    *,
    reason: str,
    position_kind: str = "",
    digits: int | None = None,
) -> bool:
    """Zavre jednu MT5 pozici marketem. Vraci True pri uspechu."""
    if digits is None:
        digits = _price_digits(mt5.symbol_info(cfg.symbol))
    tick = mt5.symbol_info_tick(cfg.symbol)
    if tick is None:
        log.error(f"{reason}: nelze ziskat tick pro {cfg.symbol}")
        return False
    if int(p.type) == int(getattr(mt5, "POSITION_TYPE_BUY", -1)):
        close_type = mt5.ORDER_TYPE_SELL
        price = tick.bid
    else:
        close_type = mt5.ORDER_TYPE_BUY
        price = tick.ask

    orig_comment = str(getattr(p, "comment", "") or "")
    new_comment = f"{reason}_{orig_comment}"[:31]
    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": cfg.symbol,
        "volume": p.volume,
        "type": close_type,
        "position": int(p.ticket),
        "price": _round_price(price, digits),
        "deviation": 20,
        "magic": int(cfg.magic),
        "comment": new_comment,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    attempts, backoff = _resolve_retry_policy(cfg, req)
    result = _order_send_with_retry(req, reason, max_attempts=attempts, backoff_sec=backoff)
    if result is None:
        log.warning(f"{reason}: nepodarilo se zavrit pozici #{p.ticket}: zadna odpoved")
        return False
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        log.info(
            f"{reason}: zavrena pozice #{p.ticket} | "
            f"vol={p.volume} | open={p.price_open:.5f} | close={price:.5f}"
            + (f" | kind={position_kind}" if position_kind else "")
        )
        log_event(
            cfg, "info", reason,
            ticket=int(p.ticket),
            volume=float(p.volume),
            price_open=float(p.price_open),
            price_close=float(price),
            wave_comment=orig_comment,
            position_kind=position_kind or None,
        )
        return True
    log.warning(
        f"{reason}: nepodarilo se zavrit pozici #{p.ticket}: {result.comment}"
    )
    return False


class _Mt5PositionTradeView:
    """Minimalni adapter MT5 pozice → should_close_trade_on_* helpery."""

    __slots__ = (
        "dir",
        "is_ext",
        "entry_tag",
        "is_counter",
        "is_two_sided_mirror",
        "wave_time",
    )

    def __init__(self, *, pos_dir: int, comment: str) -> None:
        from infra.trade_tracker import _wave_id_from_comment

        self.dir = int(pos_dir)
        c = str(comment or "")
        self.is_ext = (
            is_ext_block_comment(c)
            or c.startswith(EXT_PRIMARY_WAVE_COMMENT_PREFIX)
        )
        self.is_counter = c.startswith(COUNTER_PENDING_COMMENT_PREFIX)
        self.is_two_sided_mirror = c.startswith(TWO_SIDED_MIRROR_COMMENT_PREFIX)
        if c.startswith(EXT_PRIMARY_WAVE_COMMENT_PREFIX):
            self.wave_time = c[len(EXT_PRIMARY_WAVE_COMMENT_PREFIX):]
        else:
            self.wave_time = _wave_id_from_comment(c) or ""
        if self.is_ext and is_ext_block_comment(c):
            ext_wt = ext_block_wave_time_from_comment(c)
            if ext_wt:
                self.wave_time = ext_wt
        if c.startswith(EXT_COUNTER_TIME_COMMENT_PREFIX):
            self.entry_tag = "ext_counter_time"
        elif c.startswith(EXT_COUNTER_BOS_COMMENT_PREFIX):
            self.entry_tag = "ext_counter_bos"
        elif c.startswith(EXT_SECONDARY_COMMENT_PREFIX):
            self.entry_tag = "ext_0236"
        else:
            self.entry_tag = "base"


class _TpWaveTradeView(_Mt5PositionTradeView):
    """Alias pro TP-vlna N close (zpetna kompatibilita)."""


def close_positions_on_tp_wave_n(
    cfg: BotConfig,
    *,
    trend_dir: int,
    bar_high: float,
    bar_low: float,
    bar_close: float,
    reason: str = "TP_WAVE_N",
    ext1_protection_per_bar: list[bool] | None = None,
    current_bar_idx: int | None = None,
    current_wave_time: str | None = None,
    wave_birth_by_time: dict[str, int] | None = None,
    main_trend_dir: int = 0,
) -> dict[str, int]:
    """
    LIVE ekvivalent backtest `_maybe_fire_tp_wave_event`:

      - aktivne zavre trend-dir pozice (vcetne EXT block E23_/ECT_/ECB_),
      - zavre wave counter (CNTR_) a two-sided mirror (TS2_),
      - SL safety: pokud bar sahl SL, reason=SL (stejne jako backtest).

    Pendingy se NEMENI (backtest je na TP-vlne N nerusi).
    """
    if trend_dir not in (1, -1):
        log.error(f"close_positions_on_tp_wave_n: neplatny trend_dir={trend_dir}")
        return {
            "trend_dir_closed": 0,
            "wave_counter_closed": 0,
            "two_sided_closed": 0,
            "sl_protected": 0,
        }

    positions = mt5.positions_get(symbol=cfg.symbol) or ()
    if not positions:
        return {
            "trend_dir_closed": 0,
            "wave_counter_closed": 0,
            "two_sided_closed": 0,
            "sl_protected": 0,
        }

    info = mt5.symbol_info(cfg.symbol)
    digits = _price_digits(info)
    trend_dir_closed = 0
    wave_counter_closed = 0
    two_sided_closed = 0
    sl_protected = 0
    ext_parent_protected = 0

    for p in positions:
        if int(getattr(p, "magic", -1)) != int(cfg.magic):
            continue
        comment = str(getattr(p, "comment", "") or "")
        is_buy = int(p.type) == int(getattr(mt5, "POSITION_TYPE_BUY", -1))
        pos_dir = 1 if is_buy else -1
        sl = float(getattr(p, "sl", 0.0) or 0.0)

        is_wave_counter = comment.startswith(COUNTER_PENDING_COMMENT_PREFIX)
        is_two_sided = comment.startswith(TWO_SIDED_MIRROR_COMMENT_PREFIX)
        trade_view = _TpWaveTradeView(pos_dir=pos_dir, comment=comment)
        close_it = should_close_trade_on_tp_wave_n(trade_view, int(trend_dir))
        if not close_it:
            continue

        sl_hit = _tp_wave_sl_hit_on_bar(
            is_buy=is_buy, sl=sl, bar_high=float(bar_high), bar_low=float(bar_low),
        )
        close_reason = "SL" if sl_hit else reason
        
        # 1) Stejna parent EXT — beze zmeny chovani (chranena)
        if (
            current_wave_time
            and is_ext_block_trade_on_parent_wave(trade_view, str(current_wave_time))
        ):
            if sl_hit:
                if _close_mt5_position_market(
                    cfg, p, reason="SL", position_kind="EXT_BLOCK", digits=digits,
                ):
                    sl_protected += 1
            else:
                ext_parent_protected += 1
            continue
            
        # 2) NOVE: EXT block z jine parent EXT v okne sve parent vlny — chranena
        if wave_birth_by_time is not None and current_bar_idx is not None:
            if is_trade_within_parent_ext_window(
                trade_view,
                wave_birth_by_time=wave_birth_by_time,
                bar_idx=current_bar_idx,
            ):
                if sl_hit:
                    if _close_mt5_position_market(
                        cfg, p, reason="SL", position_kind="EXT_BLOCK", digits=digits,
                    ):
                        sl_protected += 1
                else:
                    ext_parent_protected += 1
                continue

        if (
            not sl_hit
            and ext1_protection_per_bar is not None
            and current_bar_idx is not None
            and ext1_close_blocked_on_bar(
                int(current_bar_idx),
                ext1_protection_per_bar,
                cfg,
                close_reason,
                trade=trade_view,
                main_trend_dir=main_trend_dir,
            )
        ):
            continue
        if is_wave_counter:
            kind = "WAVE_COUNTER"
        elif is_two_sided:
            kind = "TWO_SIDED_MIRROR"
        elif is_ext_block_comment(comment):
            kind = "EXT_BLOCK"
        else:
            kind = "TREND_DIR"
        if _close_mt5_position_market(
            cfg, p, reason=close_reason, position_kind=kind, digits=digits,
        ):
            if sl_hit:
                sl_protected += 1
            elif is_wave_counter:
                wave_counter_closed += 1
            elif is_two_sided:
                two_sided_closed += 1
            else:
                trend_dir_closed += 1

    total = trend_dir_closed + wave_counter_closed + two_sided_closed + sl_protected
    if total > 0 or ext_parent_protected > 0:
        log.info(
            f"{reason}: zavreno trend_dir={trend_dir_closed}, "
            f"wave_counter={wave_counter_closed}, two_sided={two_sided_closed}, "
            f"sl_protected={sl_protected}, ext_parent_protected={ext_parent_protected} "
            f"(bar_close={float(bar_close):.5f})"
        )
    return {
        "trend_dir_closed": trend_dir_closed,
        "wave_counter_closed": wave_counter_closed,
        "two_sided_closed": two_sided_closed,
        "sl_protected": sl_protected,
        "ext_parent_protected": ext_parent_protected,
    }


def close_positions_on_extension_tp_hit(
    cfg: BotConfig,
    *,
    trend_dir: int,
    armed_tp: float,
    bar_high: float,
    bar_low: float,
    bar_close: float,
    bar_open: float,
    ext1_protection_per_bar: list[bool] | None = None,
    current_bar_idx: int | None = None,
    wave_birth_by_time: dict[str, int] | None = None,
    main_trend_dir: int = 0,
) -> dict[str, int]:
    """
    LIVE ekvivalent backtest `_maybe_fire_extension_tp_on_bar` (varianta G).

    Zavre trend-dir pozice pri zasahu armed extension TP ceny; SL safety
    a intrabar priorita pres `tp_wave_intrabar_priority`.
    """
    from strategy.wave_target_n_early import (
        extension_tp_hit_on_bar,
        tp_wave_intrabar_tp_before_sl,
        trade_exit_on_extension_bar,
    )
    from strategy.wave_target_n_early import FormingTpWatch

    if trend_dir not in (1, -1):
        log.error(
            f"close_positions_on_extension_tp_hit: neplatny trend_dir={trend_dir}",
        )
        return {
            "trend_dir_closed": 0,
            "wave_counter_closed": 0,
            "two_sided_closed": 0,
            "sl_protected": 0,
            "ext_parent_protected": 0,
        }

    probe = FormingTpWatch(
        trend_dir=int(trend_dir),
        prev_wave={"dir": int(trend_dir), "box_top": 0.0, "box_bottom": 0.0},
        target_tp_index=0,
        start_bar=0,
        pivot=0.0,
        extreme=0.0,
        armed=True,
        armed_tp=float(armed_tp),
    )
    ext_hit = extension_tp_hit_on_bar(
        probe,
        high=float(bar_high),
        low=float(bar_low),
        close=float(bar_close),
        open_=float(bar_open),
    )
    if not ext_hit:
        return {
            "trend_dir_closed": 0,
            "wave_counter_closed": 0,
            "two_sided_closed": 0,
            "sl_protected": 0,
            "ext_parent_protected": 0,
        }

    positions = mt5.positions_get(symbol=cfg.symbol) or ()
    if not positions:
        return {
            "trend_dir_closed": 0,
            "wave_counter_closed": 0,
            "two_sided_closed": 0,
            "sl_protected": 0,
            "ext_parent_protected": 0,
        }

    info = mt5.symbol_info(cfg.symbol)
    digits = _price_digits(info)
    tp_before_sl = tp_wave_intrabar_tp_before_sl(cfg)
    trend_dir_closed = 0
    wave_counter_closed = 0
    two_sided_closed = 0
    sl_protected = 0
    ext_parent_protected = 0

    for p in positions:
        if int(getattr(p, "magic", -1)) != int(cfg.magic):
            continue
        comment = str(getattr(p, "comment", "") or "")
        is_buy = int(p.type) == int(getattr(mt5, "POSITION_TYPE_BUY", -1))
        pos_dir = 1 if is_buy else -1
        trade_view = _TpWaveTradeView(pos_dir=pos_dir, comment=comment)
        if not should_close_trade_on_tp_wave_n(trade_view, int(trend_dir)):
            continue

        if wave_birth_by_time is not None and current_bar_idx is not None:
            if is_trade_within_parent_ext_window(
                trade_view,
                wave_birth_by_time=wave_birth_by_time,
                bar_idx=current_bar_idx,
            ):
                sl_hit = _tp_wave_sl_hit_on_bar(
                    is_buy=is_buy,
                    sl=float(getattr(p, "sl", 0.0) or 0.0),
                    bar_high=float(bar_high),
                    bar_low=float(bar_low),
                )
                if sl_hit:
                    if _close_mt5_position_market(
                        cfg, p, reason="SL", position_kind="EXT_BLOCK", digits=digits,
                    ):
                        sl_protected += 1
                else:
                    ext_parent_protected += 1
                continue

        price, reason = trade_exit_on_extension_bar(
            trade_view,
            high=float(bar_high),
            low=float(bar_low),
            armed_tp=float(armed_tp),
            ext_hit=ext_hit,
            tp_before_sl=tp_before_sl,
        )
        if price is None or reason is None:
            continue

        if (
            reason != "SL"
            and ext1_protection_per_bar is not None
            and current_bar_idx is not None
            and ext1_close_blocked_on_bar(
                int(current_bar_idx),
                ext1_protection_per_bar,
                cfg,
                reason,
                trade=trade_view,
                main_trend_dir=main_trend_dir,
            )
        ):
            continue

        is_wave_counter = comment.startswith(COUNTER_PENDING_COMMENT_PREFIX)
        is_two_sided = comment.startswith(TWO_SIDED_MIRROR_COMMENT_PREFIX)
        if is_wave_counter:
            kind = "WAVE_COUNTER"
        elif is_two_sided:
            kind = "TWO_SIDED_MIRROR"
        elif is_ext_block_comment(comment):
            kind = "EXT_BLOCK"
        else:
            kind = "TREND_DIR"
        if _close_mt5_position_market(
            cfg, p, reason=reason, position_kind=kind, digits=digits,
        ):
            if reason == "SL":
                sl_protected += 1
            elif is_wave_counter:
                wave_counter_closed += 1
            elif is_two_sided:
                two_sided_closed += 1
            else:
                trend_dir_closed += 1

    total = trend_dir_closed + wave_counter_closed + two_sided_closed + sl_protected
    if total > 0 or ext_parent_protected > 0:
        log.info(
            "TP_EXTENSION_HIT: zavreno trend_dir=%s, wave_counter=%s, "
            "two_sided=%s, sl_protected=%s, ext_parent_protected=%s "
            "(armed_tp=%.5f bar_close=%.5f)",
            trend_dir_closed,
            wave_counter_closed,
            two_sided_closed,
            sl_protected,
            ext_parent_protected,
            float(armed_tp),
            float(bar_close),
        )
    return {
        "trend_dir_closed": trend_dir_closed,
        "wave_counter_closed": wave_counter_closed,
        "two_sided_closed": two_sided_closed,
        "sl_protected": sl_protected,
        "ext_parent_protected": ext_parent_protected,
    }




def cancel_flip_follower_pendings_on_bos(
    cfg: BotConfig, *, reason: str = "BOS_CANCEL_PENDING",
) -> int:
    """Zrusi CNTR_ a TS2_ pendingy pri BOS flipu (backtest-aligned)."""
    orders = mt5.orders_get(symbol=cfg.symbol) or ()
    if not orders:
        return 0
    cancelled = 0
    for o in orders:
        if int(getattr(o, "magic", -1)) != int(cfg.magic):
            continue
        comment = str(getattr(o, "comment", "") or "")
        if not (
            comment.startswith(COUNTER_PENDING_COMMENT_PREFIX)
            or comment.startswith(TWO_SIDED_MIRROR_COMMENT_PREFIX)
        ):
            continue
        req = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": int(o.ticket),
            "symbol": cfg.symbol,
        }
        result = _send_request(req, reason, cfg)
        if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
            cancelled += 1
            log.info(f"{reason}: zrusen flip-follower pending #{o.ticket} | {comment}")
    return cancelled


def close_flip_follower_positions_on_bos(
    cfg: BotConfig,
    *,
    broken_dir: int,
    bar_high: float,
    bar_low: float,
    reason: str = "BOS_EXIT",
    protected_wave_times: set[str] | None = None,
    ext1_protection_per_bar: list[bool] | None = None,
    current_bar_idx: int | None = None,
    protect_ext_block_from_wave: str | None = None,
    wave_birth_by_time: dict[str, int] | None = None,
    main_trend_dir: int = 0,
) -> int:
    """
    Zavre flip-follower pozice pri BOS flipu (backtest-aligned).

    Scope = should_close_trade_on_bos_flip(flipped=True) minus broken_dir pozice,
    ktere uz zavrela close_positions_by_direction ve stejnem cyklu.
    Zahrnuje CNTR_, TS2_, ECT_, ECB_.
    """
    if broken_dir not in (1, -1):
        log.error(f"close_flip_follower_positions_on_bos: neplatny broken_dir={broken_dir}")
        return 0

    positions = mt5.positions_get(symbol=cfg.symbol) or ()
    if not positions:
        return 0
    digits = _price_digits(mt5.symbol_info(cfg.symbol))
    protected = protected_wave_times or set()
    closed = 0
    for p in positions:
        if int(getattr(p, "magic", -1)) != int(cfg.magic):
            continue
        comment = str(getattr(p, "comment", "") or "")
        is_buy = int(p.type) == int(getattr(mt5, "POSITION_TYPE_BUY", -1))
        pos_dir = 1 if is_buy else -1
        trade_view = _Mt5PositionTradeView(pos_dir=pos_dir, comment=comment)
        if not should_close_trade_on_bos_flip(
            trade_view,
            broken_dir=int(broken_dir),
            flipped=True,
            protected_wave_times=protected,
        ):
            continue
        if int(pos_dir) == int(broken_dir):
            continue
        sl = float(getattr(p, "sl", 0.0) or 0.0)
        sl_hit = _tp_wave_sl_hit_on_bar(
            is_buy=is_buy, sl=sl, bar_high=float(bar_high), bar_low=float(bar_low),
        )
        close_reason = "SL" if sl_hit else reason
        
        # 1) Stejna parent EXT — beze zmeny chovani (chranena)
        if (
            protect_ext_block_from_wave
            and is_ext_block_trade_on_parent_wave(
                trade_view, str(protect_ext_block_from_wave),
            )
            and not sl_hit
        ):
            continue
            
        # 2) NOVE: EXT block z jine parent EXT v okne sve parent vlny — chranena
        if wave_birth_by_time is not None and current_bar_idx is not None:
            if is_trade_within_parent_ext_window(
                trade_view,
                wave_birth_by_time=wave_birth_by_time,
                bar_idx=current_bar_idx,
            ) and not sl_hit:
                continue

        if (
            not sl_hit
            and ext1_protection_per_bar is not None
            and current_bar_idx is not None
            and ext1_close_blocked_on_bar(
                int(current_bar_idx), ext1_protection_per_bar, cfg, close_reason,
                trade=trade_view,
                main_trend_dir=main_trend_dir,
            )
        ):
            continue
        if comment.startswith(COUNTER_PENDING_COMMENT_PREFIX):
            kind = "WAVE_COUNTER"
        elif comment.startswith(TWO_SIDED_MIRROR_COMMENT_PREFIX):
            kind = "TWO_SIDED_MIRROR"
        elif comment.startswith(EXT_COUNTER_TIME_COMMENT_PREFIX):
            kind = "EXT_COUNTER_TIME"
        elif comment.startswith(EXT_COUNTER_BOS_COMMENT_PREFIX):
            kind = "EXT_COUNTER_BOS"
        else:
            kind = "FLIP_FOLLOWER"
        if _close_mt5_position_market(
            cfg, p, reason=close_reason, position_kind=kind, digits=digits,
        ):
            closed += 1
    return closed


def close_wave_counter_positions(cfg: BotConfig, *, reason: str = "TP_WAVE_N") -> int:
    """
    Zavre jen wave counter pozice (CNTR_). Preferuj `close_positions_on_tp_wave_n`
    pro plny backtest-aligned TP-wave exit.
    """
    positions = mt5.positions_get(symbol=cfg.symbol) or ()
    if not positions:
        return 0
    info = mt5.symbol_info(cfg.symbol)
    digits = _price_digits(info)
    closed = 0
    for p in positions:
        if int(getattr(p, "magic", -1)) != int(cfg.magic):
            continue
        comment = str(getattr(p, "comment", "") or "")
        if not comment.startswith(COUNTER_PENDING_COMMENT_PREFIX):
            continue
        if _close_mt5_position_market(
            cfg, p, reason=reason, position_kind="WAVE_COUNTER", digits=digits,
        ):
            closed += 1
    return closed


def place_counter_position_pending(cfg: BotConfig, *, wave_time: str,
                                    counter_dir: int, tp_price: float,
                                    counter_sl: float, lot: float,
                                    digits: int,
                                    tp: float | None = None) -> bool:
    """
    Polozi counter LIMIT pending v opacnem smeru aktualniho trendu na TP urovni
    TP-vlny. counter_dir = +1 → BUY_LIMIT (counter v bear trendu), -1 → SELL_LIMIT
    (counter v bull trendu). Comment ma prefix COUNTER_PENDING_COMMENT_PREFIX, aby
    se dal pri startup / sync rozpoznat.

    tp: RRR/BOS_EXIT safety TP (cfg.rrr); None = bez broker TP (WAVE_TARGET_N
        exit na TP-vlne N pres close_wave_counter_positions).
    """
    from runtime.live_wave_isolation import skip_live_non_wave_entry

    if skip_live_non_wave_entry(cfg, "COUNTER", wave_time=str(wave_time)):
        return False

    if block_duplicate_counter_order(cfg, wave_time):
        return True

    is_buy = (counter_dir == 1)
    side = "BUY" if is_buy else "SELL"
    label = f"{side}_LIMIT_COUNTER"
    order_type = mt5.ORDER_TYPE_BUY_LIMIT if is_buy else mt5.ORDER_TYPE_SELL_LIMIT

    comment = f"{COUNTER_PENDING_COMMENT_PREFIX}{wave_time}"[:31]
    request = _build_pending_request(
        cfg, order_type, tp_price, counter_sl, tp, lot, wave_time, digits,
        comment_override=comment,
    )
    result = _send_request(request, label, cfg)
    if result is None:
        log.error(f"CHYBA counter pending: zadna odpoved | {mt5.last_error()}")
        return False
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        tp_log = "—" if tp is None else f"{float(tp):.5f}"
        log.info(
            f"[COUNTER] {label} | Wave {wave_time} | "
            f"EP={tp_price:.5f} SL={counter_sl:.5f} TP={tp_log} Lot={lot}"
        )
        _log_order_placed(
            cfg, result, side=side, type_label=label,
            ep=tp_price, sl=counter_sl, tp=tp, lot=lot, wave_time=wave_time,
            fallback="COUNTER", digits=digits,
        )
        return True
    log.error(f"CHYBA counter pending {label}: retcode={result.retcode} | {result.comment}")
    return False


def place_counter_position_market(
    cfg: BotConfig,
    *,
    wave_time: str,
    counter_dir: int,
    counter_sl: float,
    lot: float,
    digits: int,
    tp: float | None = None,
    reference_ep: float | None = None,
) -> bool:
    """
    G varianta wave counter: MARKET vstup ve stejnem momentu jako TP_EXTENSION_HIT.
    Comment CNTR_ + wave_time (synteticky klic pred birth W(N)).
    """
    from runtime.live_wave_isolation import skip_live_non_wave_entry

    if skip_live_non_wave_entry(cfg, "COUNTER", wave_time=str(wave_time)):
        return False

    if block_duplicate_counter_order(cfg, wave_time):
        return True

    is_buy = (counter_dir == 1)
    side = "BUY" if is_buy else "SELL"
    label = f"{side}_MARKET_COUNTER"

    tick = mt5.symbol_info_tick(cfg.symbol)
    info = mt5.symbol_info(cfg.symbol)
    if tick is None or info is None:
        log.error(f"{label}: nelze ziskat tick/symbol_info")
        return False

    market_price = float(tick.ask if is_buy else tick.bid)
    sl = float(counter_sl)
    if is_buy and market_price <= sl:
        log.warning(f"{label}: market {market_price} <= SL {sl} — preskakuji")
        return False
    if (not is_buy) and market_price >= sl:
        log.warning(f"{label}: market {market_price} >= SL {sl} — preskakuji")
        return False

    comment = f"{COUNTER_PENDING_COMMENT_PREFIX}{wave_time}"[:31]
    order_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
    request = _build_market_request(
        cfg, order_type, market_price, sl, tp, lot, wave_time, digits,
        comment_override=comment,
    )
    result = _send_request(request, label, cfg)
    if result is None:
        log.error(f"CHYBA counter market: zadna odpoved | {mt5.last_error()}")
        return False
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        ref = reference_ep if reference_ep is not None else market_price
        log.info(
            f"[COUNTER] {label} | Wave {wave_time} | "
            f"EP={market_price:.5f} ref={float(ref):.5f} "
            f"SL={sl:.5f} TP={_fmt_tp(tp)} Lot={lot}"
        )
        _log_order_placed(
            cfg, result, side=side, type_label=label,
            ep=market_price, sl=sl, tp=tp, lot=lot, wave_time=wave_time,
            fallback="COUNTER_G", digits=digits,
        )
        return True
    log.error(f"CHYBA counter market {label}: retcode={result.retcode} | {result.comment}")
    return False


def enforce_counter_positions_min_sl(cfg: BotConfig, *, min_sl_pct: float) -> int:
    """
    U otevrenych live counter pozic dorovna SL alespon na `min_sl_pct` od skutecne
    fill ceny (`price_open`), pokud broker vyplnil LIMIT vyrazne lepsi cenou.
    """
    if min_sl_pct <= 0.0:
        return 0
    positions = mt5.positions_get(symbol=cfg.symbol) or ()
    if not positions:
        return 0

    info = mt5.symbol_info(cfg.symbol)
    digits = _price_digits(info)
    modified = 0

    for p in positions:
        if int(getattr(p, "magic", -1)) != int(cfg.magic):
            continue
        comment = str(getattr(p, "comment", "") or "")
        if not comment.startswith(COUNTER_PENDING_COMMENT_PREFIX):
            continue

        entry_price = float(getattr(p, "price_open", 0.0) or 0.0)
        current_sl = float(getattr(p, "sl", 0.0) or 0.0)
        if entry_price <= 0.0 or current_sl <= 0.0:
            continue

        pos_type = int(getattr(p, "type", -1))
        if pos_type == int(getattr(mt5, "POSITION_TYPE_BUY", -1)):
            is_buy = True
        elif pos_type == int(getattr(mt5, "POSITION_TYPE_SELL", -1)):
            is_buy = False
        else:
            continue

        current_sl_pct = abs(current_sl - entry_price) / abs(entry_price) * 100.0
        if current_sl_pct + 1e-12 >= float(min_sl_pct):
            continue

        new_sl = _round_price(
            compute_sl_price_from_pct(entry_price, float(min_sl_pct), is_buy=is_buy),
            digits,
        )
        req = {
            "action":   mt5.TRADE_ACTION_SLTP,
            "symbol":   cfg.symbol,
            "position": int(p.ticket),
            "sl":       float(new_sl),
            "tp":       float(getattr(p, "tp", 0.0) or 0.0),
            "magic":    int(cfg.magic),
        }
        result = _send_request(req, "COUNTER_MIN_SL_ENFORCE", cfg)
        if result is None:
            log.warning(f"COUNTER_MIN_SL: modify pozice #{p.ticket} bez odpovedi")
            continue
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            modified += 1
            log_event(
                cfg, "info", "COUNTER_MIN_SL_ENFORCED",
                ticket=int(p.ticket),
                comment=comment,
                entry=float(entry_price),
                old_sl=float(current_sl),
                new_sl=float(new_sl),
                min_sl_pct=float(min_sl_pct),
            )
        else:
            log.warning(
                f"COUNTER_MIN_SL: pozice #{p.ticket} modify retcode={result.retcode} | {result.comment}"
            )
    return modified


def place_bos_reentry_market(cfg: BotConfig, *, new_trend_dir: int,
                              entry_price: float, sl_price: float, lot: float,
                              digits: int, broken_wave_time: str | None = None,
                              tp_price: float | None = None) -> bool:
    """
    Po BOS flipu otevre MARKET pozici v NOVEM smeru trendu. SL z ladderu
    velikosti posledni vlny rozbiteho smeru — propocet probiha v caller (live_loop).
    TP dle `resolve_effective_tp` (None = bez TP, ceka TP-vlna / BOS / SL).

    new_trend_dir = +1 (bull) → BUY MARKET; -1 (bear) → SELL MARKET.
    """
    from runtime.live_wave_isolation import skip_live_non_wave_entry

    if skip_live_non_wave_entry(
        cfg, "BOS", broken_wave_time=str(broken_wave_time or ""),
    ):
        return False

    if block_duplicate_bos_reentry(cfg, broken_wave_time):
        return True

    is_buy = (new_trend_dir == 1)
    side = "BUY" if is_buy else "SELL"
    label = f"{side}_MARKET_REENTRY"
    order_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL

    suffix = broken_wave_time or "bos"
    comment = f"{BOS_REENTRY_COMMENT_PREFIX}{suffix}"[:31]
    request = _build_market_request(
        cfg, order_type, entry_price, sl_price, tp_price, lot, suffix, digits,
        comment_override=comment,
    )
    result = _send_request(request, label, cfg)
    if result is None:
        log.error(f"CHYBA re-entry market: zadna odpoved | {mt5.last_error()}")
        return False
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        log.info(
            f"[BOS-REENTRY] {label} | "
            f"EP={entry_price:.5f} SL={sl_price:.5f} TP={_fmt_tp(tp_price)} Lot={lot}"
        )
        _log_order_placed(
            cfg, result, side=side, type_label=label,
            ep=entry_price, sl=sl_price, tp=tp_price, lot=lot,
            wave_time=str(broken_wave_time or ""),
            fallback="BOS_REENTRY", digits=digits,
        )
        return True
    log.error(f"CHYBA re-entry market {label}: retcode={result.retcode} | {result.comment}")
    return False


def get_counter_pending_wave_times(cfg: BotConfig) -> set[str]:
    """
    Vrati mnozinu wave_time stringu, ke kterym je v MT5 aktualne otevreny
    counter-position pending pro tento bot (filtr magic + symbol + comment prefix).
    Pouziva live_loop pri startu/synchronizaci, aby vedel, ktere TP-vlny uz maji
    counter pending a nepokladal duplikaty.
    """
    out: set[str] = set()
    orders = mt5.orders_get(symbol=cfg.symbol)
    if not orders:
        return out
    for o in orders:
        if int(getattr(o, "magic", -1)) != int(cfg.magic):
            continue
        comment = str(getattr(o, "comment", "") or "")
        if not comment.startswith(COUNTER_PENDING_COMMENT_PREFIX):
            continue
        wt = comment[len(COUNTER_PENDING_COMMENT_PREFIX):]
        if wt:
            out.add(wt)
    return out


def get_active_counter_wave_times(cfg: BotConfig) -> set[str]:
    """
    CNTR_ wave_time z otevřených pendingů i pozic (LIMIT counter + G MARKET counter).
    """
    out = get_counter_pending_wave_times(cfg)
    positions = mt5.positions_get(symbol=cfg.symbol) or ()
    for p in positions:
        if int(getattr(p, "magic", -1)) != int(cfg.magic):
            continue
        comment = str(getattr(p, "comment", "") or "")
        if not comment.startswith(COUNTER_PENDING_COMMENT_PREFIX):
            continue
        wt = comment[len(COUNTER_PENDING_COMMENT_PREFIX):]
        if wt:
            out.add(wt)
    return out


def place_pp_pending(cfg: BotConfig, *, wave_time: str, trend_dir: int,
                     entry_price: float, sl_price: float,
                     tp_price: float | None, lot: float, digits: int) -> bool:
    """
    Polozi PP LIMIT pending na cene `entry_price` (= wave high/low).
    `trend_dir = +1` -> BUY_LIMIT, `-1` -> SELL_LIMIT.

    Comment ma prefix PP_PENDING_COMMENT_PREFIX, aby se dal pri startup / sync
    rozpoznat (PP pendingy maji vlastni lifecycle — rusi se jen pri novem PP
    nebo pri BOS flipu).
    """
    from runtime.live_wave_isolation import skip_live_non_wave_entry

    if skip_live_non_wave_entry(cfg, "PP", wave_time=str(wave_time)):
        return False

    if block_duplicate_pp_order(cfg, wave_time):
        return True

    is_buy = (trend_dir == 1)
    side = "BUY" if is_buy else "SELL"
    label = f"{side}_LIMIT_PP"
    order_type = mt5.ORDER_TYPE_BUY_LIMIT if is_buy else mt5.ORDER_TYPE_SELL_LIMIT

    comment = f"{PP_PENDING_COMMENT_PREFIX}{wave_time}"[:31]
    request = _build_pending_request(
        cfg, order_type, entry_price, sl_price, tp_price, lot, wave_time, digits,
        comment_override=comment,
    )
    result = _send_request(request, label, cfg)
    if result is None:
        log.error(f"CHYBA PP pending: zadna odpoved | {mt5.last_error()}")
        return False
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        log.info(
            f"[PP] {label} | Wave {wave_time} | "
            f"EP={entry_price:.5f} SL={sl_price:.5f} TP={_fmt_tp(tp_price)} Lot={lot}"
        )
        _log_order_placed(
            cfg, result, side=side, type_label=label,
            ep=entry_price, sl=sl_price, tp=tp_price, lot=lot, wave_time=wave_time,
            fallback="PP_LIMIT", digits=digits,
        )
        return True
    log.error(f"CHYBA PP pending {label}: retcode={result.retcode} | {result.comment}")
    return False


def place_pp_market_fallback(cfg: BotConfig, *, wave_time: str, trend_dir: int,
                              entry_price: float, sl_price: float,
                              tp_price: float | None, lot: float, digits: int) -> bool:
    """
    Fallback MARKET pro PP pozici, pokud broker odmitnul LIMIT (treba kvuli
    min_dist nebo specificke pricelist regule). Pouziva se jen kdyz
    `place_pp_pending` selze v predchozim kroku — v takovem pripade chce
    uzivatel pozici stejne otevrit za aktualni trzni cenu (vetsinou velmi
    blizko `entry_price`, ale s urcitym slippage).
    """
    from runtime.live_wave_isolation import skip_live_non_wave_entry

    if skip_live_non_wave_entry(cfg, "PP", wave_time=str(wave_time)):
        return False

    if block_duplicate_pp_order(cfg, wave_time):
        return True

    is_buy = (trend_dir == 1)
    side = "BUY" if is_buy else "SELL"
    label = f"{side}_MARKET_PP"
    order_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL

    comment = f"{PP_REENTRY_COMMENT_PREFIX}{wave_time}"[:31]
    request = _build_market_request(
        cfg, order_type, entry_price, sl_price, tp_price, lot, wave_time, digits,
        comment_override=comment,
    )
    result = _send_request(request, label, cfg)
    if result is None:
        log.error(f"CHYBA PP market fallback: zadna odpoved | {mt5.last_error()}")
        return False
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        log.info(
            f"[PP-MKT] {label} | Wave {wave_time} | "
            f"EP={entry_price:.5f} SL={sl_price:.5f} TP={_fmt_tp(tp_price)} Lot={lot}"
        )
        _log_order_placed(
            cfg, result, side=side, type_label=label,
            ep=entry_price, sl=sl_price, tp=tp_price, lot=lot, wave_time=wave_time,
            fallback="PP_MARKET", digits=digits,
        )
        return True
    log.error(f"CHYBA PP market {label}: retcode={result.retcode} | {result.comment}")
    return False


def get_pp_pending_wave_times(cfg: BotConfig) -> set[str]:
    """
    Vrati mnozinu wave_time stringu, ke kterym je v MT5 aktualne otevreny
    PP pending pro tento bot (filtr magic + symbol + comment prefix).
    Pouziva live_loop pri startu/synchronizaci — abychom vedeli, ze pro vlnu
    uz PP existuje a nepokladali duplikat.
    """
    out: set[str] = set()
    orders = mt5.orders_get(symbol=cfg.symbol)
    if not orders:
        return out
    for o in orders:
        if int(getattr(o, "magic", -1)) != int(cfg.magic):
            continue
        comment = str(getattr(o, "comment", "") or "")
        if not comment.startswith(PP_PENDING_COMMENT_PREFIX):
            continue
        wt = comment[len(PP_PENDING_COMMENT_PREFIX):]
        if wt:
            out.add(wt)
    return out


def cancel_pp_pendings(cfg: BotConfig) -> int:
    """
    Zrusi VSECHNY aktualne otevrene PP pendingy tohoto bota (filtr magic +
    symbol + comment prefix `PP_`). Pouziva se v live_loop pri vytvoreni
    noveho PP z dalsi vlny (uzivatelske pravidlo: max 1 PP pending najednou).

    Returns: pocet zrusenych pendingu.
    """
    orders = mt5.orders_get(symbol=cfg.symbol)
    if not orders:
        return 0
    cancelled = 0
    for o in orders:
        if int(getattr(o, "magic", -1)) != int(cfg.magic):
            continue
        comment = str(getattr(o, "comment", "") or "")
        if not comment.startswith(PP_PENDING_COMMENT_PREFIX):
            continue
        req = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order":  int(o.ticket),
        }
        result = _send_request(req, "PP_CANCEL_OLD", cfg)
        if result is None:
            log.warning(f"PP_CANCEL: zadna odpoved pro ticket #{o.ticket}")
            continue
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            cancelled += 1
            log.info(f"PP_CANCEL: zrusen pending #{o.ticket} (wave={comment})")
        else:
            log.warning(
                f"PP_CANCEL: ticket #{o.ticket} retcode={result.retcode} | {result.comment}"
            )
    return cancelled


def set_tp_for_direction(cfg: BotConfig, *, direction: int, tp_price: float) -> tuple[int, int]:
    """
    Nastavi TP na otevrenych pozicich a pending orderech daneho smeru bez TP.

    POZN.: Pro WAVE_TARGET_N TP-wave event pouzivej `close_positions_on_tp_wave_n`
    (backtest-aligned aktivni uzavreni), ne tento helper.
    """
    if direction not in (1, -1):
        log.error(f"set_tp_for_direction: neplatny direction={direction}")
        return 0, 0

    info = mt5.symbol_info(cfg.symbol)
    digits = _price_digits(info)
    tp_round = _round_price(tp_price, digits)
    eps = float(getattr(info, "point", 0.0) or 0.0)

    # 1) POZICE
    positions_modified = 0
    positions = mt5.positions_get(symbol=cfg.symbol) or ()
    target_pos_type = mt5.POSITION_TYPE_BUY if direction == 1 else mt5.POSITION_TYPE_SELL
    for p in positions:
        if int(getattr(p, "magic", -1)) != int(cfg.magic):
            continue
        if int(p.type) != int(target_pos_type):
            continue
        cur_tp = float(getattr(p, "tp", 0.0) or 0.0)
        if cur_tp > 0.0 and not _float_close(cur_tp, 0.0, eps):
            # uz ma TP — nikdy ho neprepisujeme (jednou nastaveno, drzi se)
            continue
        req = {
            "action":   mt5.TRADE_ACTION_SLTP,
            "symbol":   cfg.symbol,
            "position": int(p.ticket),
            "sl":       float(getattr(p, "sl", 0.0) or 0.0),
            "tp":       float(tp_round),
            "magic":    int(cfg.magic),
        }
        result = _send_request(req, "TP_WAVE_SET_POSITION", cfg)
        if result is None:
            log.warning(f"TP_WAVE: nepodarilo se modify pozici #{p.ticket}: zadna odpoved")
            continue
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            positions_modified += 1
            log.info(
                f"TP_WAVE: pozice #{p.ticket} dir={'BUY' if direction==1 else 'SELL'} "
                f"TP={tp_round:.5f}"
            )
            log_event(
                cfg, "info", "TP_WAVE_SET_POSITION",
                ticket=int(p.ticket),
                direction="BUY" if direction == 1 else "SELL",
                tp=float(tp_round),
                sl=float(getattr(p, "sl", 0.0) or 0.0),
            )
        else:
            log.warning(
                f"TP_WAVE: pozice #{p.ticket} modify retcode={result.retcode} | {result.comment}"
            )

    # 2) PENDING ORDERY (mimo counter — ty maji svuj vlastni cyklus)
    pendings_modified = 0
    orders = mt5.orders_get(symbol=cfg.symbol) or ()
    if direction == 1:
        target_order_types = (mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_BUY_STOP)
    else:
        target_order_types = (mt5.ORDER_TYPE_SELL_LIMIT, mt5.ORDER_TYPE_SELL_STOP)
    for o in orders:
        if int(getattr(o, "magic", -1)) != int(cfg.magic):
            continue
        if int(o.type) not in target_order_types:
            continue
        comment = str(getattr(o, "comment", "") or "")
        if comment.startswith(COUNTER_PENDING_COMMENT_PREFIX):
            # counter pendingy nikdy nedostavaji TP timto eventem
            continue
        cur_tp = float(getattr(o, "tp", 0.0) or 0.0)
        if cur_tp > 0.0 and not _float_close(cur_tp, 0.0, eps):
            continue
        req = {
            "action":      mt5.TRADE_ACTION_MODIFY,
            "order":       int(o.ticket),
            "symbol":      cfg.symbol,
            "price":       float(getattr(o, "price_open", 0.0) or 0.0),
            "sl":          float(getattr(o, "sl", 0.0) or 0.0),
            "tp":          float(tp_round),
            "type_time":   int(getattr(o, "type_time", mt5.ORDER_TIME_GTC)),
            "expiration":  int(getattr(o, "time_expiration", 0) or 0),
            "magic":       int(cfg.magic),
        }
        result = _send_request(req, "TP_WAVE_SET_PENDING", cfg)
        if result is None:
            log.warning(f"TP_WAVE: nepodarilo se modify pending #{o.ticket}: zadna odpoved")
            continue
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            pendings_modified += 1
            log.info(
                f"TP_WAVE: pending #{o.ticket} dir={'BUY' if direction==1 else 'SELL'} "
                f"TP={tp_round:.5f}"
            )
            log_event(
                cfg, "info", "TP_WAVE_SET_PENDING",
                ticket=int(o.ticket),
                direction="BUY" if direction == 1 else "SELL",
                tp=float(tp_round),
                sl=float(getattr(o, "sl", 0.0) or 0.0),
            )
        else:
            log.warning(
                f"TP_WAVE: pending #{o.ticket} modify retcode={result.retcode} | {result.comment}"
            )

    return positions_modified, pendings_modified


# ============================================================================
# EXT — sekundarni (ext_0236), counter cas / BOS (live)
# ============================================================================
# EXT prefixy jsou definovany vyse u WAVE_TARGET_N (sdilene s TP-wave close).

def _ext_wave_times_from_mt5(cfg: BotConfig, prefix: str) -> set[str]:
    out: set[str] = set()
    for coll in (mt5.orders_get(symbol=cfg.symbol) or (), mt5.positions_get(symbol=cfg.symbol) or ()):
        for rec in coll:
            if int(getattr(rec, "magic", -1)) != int(cfg.magic):
                continue
            c = str(getattr(rec, "comment", "") or "")
            if c.startswith(prefix):
                wt = c[len(prefix):]
                if wt:
                    out.add(wt)
    return out


def get_ext_secondary_wave_times(cfg: BotConfig) -> set[str]:
    return _ext_wave_times_from_mt5(cfg, EXT_SECONDARY_COMMENT_PREFIX)


def get_ext_counter_time_wave_times(cfg: BotConfig) -> set[str]:
    return _ext_wave_times_from_mt5(cfg, EXT_COUNTER_TIME_COMMENT_PREFIX)


def get_ext_counter_bos_wave_times(cfg: BotConfig) -> set[str]:
    return _ext_wave_times_from_mt5(cfg, EXT_COUNTER_BOS_COMMENT_PREFIX)


def place_ext_secondary_order(
    signal: dict,
    cfg: BotConfig,
    *,
    entry_mode: EntryMode | None = None,
    ext_wave_time: str,
    bar_close: float | None = None,
) -> bool:
    """
    Sekundarni EXT LIMIT / fallback (parita s backtest `_process_ext_secondary_for_wave`).
    Comment: E23_{ext_wave_time}.
    """
    from runtime.live_wave_isolation import skip_live_non_wave_entry

    if skip_live_non_wave_entry(cfg, "EXT_SECONDARY", wave_time=str(ext_wave_time)):
        return False

    if block_duplicate_ext_secondary(cfg, ext_wave_time):
        return True

    if entry_mode is None:
        entry_mode = cfg.entry_mode
    em_value = entry_mode.value if isinstance(entry_mode, EntryMode) else str(entry_mode)

    ep = float(signal["fib50"])
    sl = float(signal["sl"])
    direction = int(signal["dir"])
    wave_time = str(ext_wave_time)

    tick = mt5.symbol_info_tick(cfg.symbol)
    info = mt5.symbol_info(cfg.symbol)
    if tick is None or info is None:
        log.error("EXT secondary: nelze ziskat tick/symbol_info")
        return False

    digits = _price_digits(info)
    min_stop_dist = (info.trade_stops_level or 0) * (info.point or 0)
    is_buy = direction == 1
    side = "BUY" if is_buy else "SELL"
    decision_ask, decision_bid = decision_prices_from_bar_close(bar_close, tick)

    if is_buy and decision_ask <= sl:
        return False
    if (not is_buy) and decision_bid >= sl:
        return False

    comment = f"{EXT_SECONDARY_COMMENT_PREFIX}{wave_time}"[:31]
    can_limit = (decision_ask > ep) if is_buy else (decision_bid < ep)

    if can_limit:
        lot = calc_lot(ep, sl, cfg)
        if lot <= 0.0:
            return False
        tp = compute_ext_secondary_take_profit(cfg, ep, sl, is_buy=is_buy)
        label = f"{side}_LIMIT_EXT0236"
        market_ref = tick.ask if is_buy else tick.bid
        if not _check_min_dist(
            market_ref, ep, min_stop_dist, label, wave_time,
            near="EP", far=("Ask" if is_buy else "Bid"),
        ):
            return False
        if not _check_min_dist(ep, sl, min_stop_dist, label, wave_time, near="SL", far="EP"):
            return False
        order_type = mt5.ORDER_TYPE_BUY_LIMIT if is_buy else mt5.ORDER_TYPE_SELL_LIMIT
        request = _build_pending_request(
            cfg, order_type, ep, sl, tp, lot, wave_time, digits,
            comment_override=comment,
        )
        result = _send_request(request, label, cfg)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            log.info(f"[EXT-0236] {label} | Wave {wave_time} EP={ep:.5f} SL={sl:.5f}")
            return True
        return False

    if em_value in ("no_fallback", "limit_fallback"):
        return False

    lot = calc_lot(ep, sl, cfg)
    if lot <= 0.0:
        return False
    tp = compute_ext_secondary_take_profit(cfg, ep, sl, is_buy=is_buy)

    if em_value == "market_fallback":
        market_price = float(tick.ask if is_buy else tick.bid)
        label = f"{side}_MARKET_EXT0236"
        order_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
        request = _build_market_request(
            cfg, order_type, market_price, sl, tp, lot, wave_time, digits,
            comment_override=comment,
        )
        result = _send_request(request, label, cfg)
        return bool(result and result.retcode == mt5.TRADE_RETCODE_DONE)

    if em_value == "stop_fallback":
        label = f"{side}_STOP_EXT0236"
        market_ref = decision_ask if is_buy else decision_bid
        if is_buy and ep <= market_ref:
            return False
        if (not is_buy) and ep >= market_ref:
            return False
        order_type = mt5.ORDER_TYPE_BUY_STOP if is_buy else mt5.ORDER_TYPE_SELL_STOP
        request = _build_pending_request(
            cfg, order_type, ep, sl, tp, lot, wave_time, digits,
            comment_override=comment,
        )
        result = _send_request(request, label, cfg)
        return bool(result and result.retcode == mt5.TRADE_RETCODE_DONE)

    return False


def place_ext_counter_market(
    cfg: BotConfig,
    *,
    counter_sig: dict,
    ext_wave_time: str,
    source: str,
) -> bool:
    """
    EXT counter MARKET (cas nebo BOS). Comment ECT_ / ECB_ + ext_wave_time.
    """
    from runtime.live_wave_isolation import skip_live_non_wave_entry

    if skip_live_non_wave_entry(
        cfg, "EXT_COUNTER", wave_time=str(ext_wave_time), source=str(source),
    ):
        return False

    if source == "time":
        if block_duplicate_ext_counter_time(cfg, ext_wave_time):
            return True
        prefix = EXT_COUNTER_TIME_COMMENT_PREFIX
        label_tag = "EXT_COUNTER_TIME"
    else:
        if block_duplicate_ext_counter_bos(cfg, ext_wave_time):
            return True
        prefix = EXT_COUNTER_BOS_COMMENT_PREFIX
        label_tag = "EXT_COUNTER_BOS"

    direction = int(counter_sig["dir"])
    is_buy = direction == 1
    side = "BUY" if is_buy else "SELL"
    sl = float(counter_sig["sl"])

    tick = mt5.symbol_info_tick(cfg.symbol)
    info = mt5.symbol_info(cfg.symbol)
    if tick is None or info is None:
        log.error(f"{label_tag}: nelze ziskat tick/symbol_info")
        return False

    digits = _price_digits(info)
    min_stop_dist = (info.trade_stops_level or 0) * (info.point or 0)
    market_price = float(tick.ask if is_buy else tick.bid)

    if is_buy and market_price <= sl:
        return False
    if (not is_buy) and market_price >= sl:
        return False

    lot = calc_lot(market_price, sl, cfg)
    if lot <= 0.0:
        return False

    synth = dict(counter_sig)
    synth["wave_time"] = ext_wave_time
    tp = resolve_effective_tp(cfg, synth, market_price, sl, is_buy=is_buy)

    comment = f"{prefix}{ext_wave_time}"[:31]
    order_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
    request = _build_market_request(
        cfg, order_type, market_price, sl, tp, lot, ext_wave_time, digits,
        comment_override=comment,
    )
    result = _send_request(request, f"{side}_MARKET_{label_tag}", cfg)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        log.info(
            f"[{label_tag}] {side} | EXT wave {ext_wave_time} | "
            f"EP={market_price:.5f} SL={sl:.5f} TP={_fmt_tp(tp)} Lot={lot}"
        )
        return True
    return False