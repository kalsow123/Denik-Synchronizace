"""
Ochrana proti duplicitním MT5 orderům/pozicím na live botu (stejná vlna / setup).

Backtester tento modul nepoužívá — tam je deduplikace v engine (`sent_signals`).
"""
from __future__ import annotations

import logging

import MetaTrader5 as mt5

from config.bot_config import BotConfig
from core.logging_utils import log_event
from infra.state_sync import get_active_wave_times, get_position_wave_times

log = logging.getLogger(__name__)


def _log_duplicate_blocked(
    cfg: BotConfig,
    *,
    order_kind: str,
    wave_time: str,
    label: str = "",
) -> None:
    """Strukturovaný zápis do .jsonl (event DUPLICATE_ORDER_BLOCKED)."""
    log_event(
        cfg,
        "warning",
        "DUPLICATE_ORDER_BLOCKED",
        order_kind=order_kind,
        wave_time=wave_time,
        label=label or order_kind,
        message=(
            f"Pokus o duplicitní {label or order_kind} pro vlnu {wave_time} "
            f"— setup už existuje v MT5"
        ),
    )


def _magic_matches(cfg: BotConfig, record) -> bool:
    return int(getattr(record, "magic", -1)) == int(cfg.magic)


def _wave_time_from_comment(comment: str, prefix: str) -> str | None:
    if not comment.startswith(prefix):
        return None
    wt = comment[len(prefix):]
    return wt or None


def _pending_wave_times_with_prefix(cfg: BotConfig, prefix: str) -> set[str]:
    out: set[str] = set()
    orders = mt5.orders_get(symbol=cfg.symbol)
    if not orders:
        return out
    for o in orders:
        if not _magic_matches(cfg, o):
            continue
        wt = _wave_time_from_comment(str(getattr(o, "comment", "") or ""), prefix)
        if wt:
            out.add(wt)
    return out


def _position_wave_times_with_prefix(cfg: BotConfig, prefix: str) -> set[str]:
    out: set[str] = set()
    positions = mt5.positions_get(symbol=cfg.symbol)
    if not positions:
        return out
    for p in positions:
        if not _magic_matches(cfg, p):
            continue
        wt = _wave_time_from_comment(str(getattr(p, "comment", "") or ""), prefix)
        if wt:
            out.add(wt)
    return out


def wave_setup_already_in_mt5(cfg: BotConfig, wave_time: str) -> bool:
    """Klasický WAVE setup (comment W{wave_time}) — pending nebo pozice."""
    active = get_active_wave_times(cfg) | get_position_wave_times(cfg)
    return wave_time in active


def pp_setup_already_in_mt5(cfg: BotConfig, wave_time: str) -> bool:
    """PP pending (PP_) nebo PP market pozice (PPM_)."""
    from infra.orders import (
        PP_PENDING_COMMENT_PREFIX,
        PP_REENTRY_COMMENT_PREFIX,
        get_pp_pending_wave_times,
    )

    if wave_time in get_pp_pending_wave_times(cfg):
        return True
    pp_open = (
        _position_wave_times_with_prefix(cfg, PP_PENDING_COMMENT_PREFIX)
        | _position_wave_times_with_prefix(cfg, PP_REENTRY_COMMENT_PREFIX)
    )
    return wave_time in pp_open


def counter_setup_already_in_mt5(cfg: BotConfig, wave_time: str) -> bool:
    from infra.orders import COUNTER_PENDING_COMMENT_PREFIX, get_counter_pending_wave_times

    if wave_time in get_counter_pending_wave_times(cfg):
        return True
    return wave_time in _position_wave_times_with_prefix(cfg, COUNTER_PENDING_COMMENT_PREFIX)


def bos_reentry_already_in_mt5(cfg: BotConfig, broken_wave_time: str | None) -> bool:
    from infra.orders import BOS_REENTRY_COMMENT_PREFIX

    suffix = broken_wave_time or "bos"
    prefix = BOS_REENTRY_COMMENT_PREFIX
    for wt in _pending_wave_times_with_prefix(cfg, prefix):
        if wt == suffix:
            return True
    for wt in _position_wave_times_with_prefix(cfg, prefix):
        if wt == suffix:
            return True
    return False


def deduplicate_magic_pendings(cfg: BotConfig) -> int:
    """
    Zruší duplicitní pending ordery se stejným commentem (stejná vlna/setup).
    Ponechá nejstarší ticket. Vrací počet zrušených orderů.
    """
    orders = mt5.orders_get(symbol=cfg.symbol) or []
    by_comment: dict[str, list] = {}
    for o in orders:
        if not _magic_matches(cfg, o):
            continue
        comment = str(getattr(o, "comment", "") or "")
        if not comment:
            continue
        by_comment.setdefault(comment, []).append(o)

    cancelled = 0
    for comment, group in by_comment.items():
        if len(group) <= 1:
            continue
        group.sort(key=lambda o: int(getattr(o, "ticket", 0)))
        for dup in group[1:]:
            ticket = int(dup.ticket)
            req = {"action": mt5.TRADE_ACTION_REMOVE, "order": ticket}
            result = mt5.order_send(req)
            if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
                cancelled += 1
                log.info(
                    "DEDUP PENDING: zrusen duplicitni order #%s | comment=%s",
                    ticket,
                    comment,
                )
            else:
                err = getattr(result, "comment", None) if result else mt5.last_error()
                log.warning(
                    "DEDUP PENDING: nelze zrusit #%s comment=%s | %s",
                    ticket,
                    comment,
                    err,
                )
    if cancelled:
        log_event(
            cfg,
            "warning",
            "PENDING_DEDUP",
            cancelled=int(cancelled),
            message="Zruseny duplicitni pending ordery po recovery",
        )
    return cancelled


def block_duplicate_wave_order(cfg: BotConfig, wave_time: str, *, label: str) -> bool:
    """
    Vrátí True, pokud má caller přeskočit odeslání (duplicita v MT5).
    Loguje důvod; u WAVE orderů se má vrátit success=True z send_order.
    """
    if wave_setup_already_in_mt5(cfg, wave_time):
        msg = (
            f"SKIP duplicitní {label}: vlna {wave_time} už má WAVE pending/pozici v MT5"
        )
        log.info(msg)
        _log_duplicate_blocked(cfg, order_kind="WAVE", wave_time=wave_time, label=label)
        return True
    return False


def block_duplicate_pp_order(cfg: BotConfig, wave_time: str) -> bool:
    if pp_setup_already_in_mt5(cfg, wave_time):
        log.info(
            f"SKIP duplicitní PP: vlna {wave_time} už má PP pending/pozici v MT5"
        )
        _log_duplicate_blocked(cfg, order_kind="PP", wave_time=wave_time, label="PP")
        return True
    return False


def block_duplicate_counter_order(cfg: BotConfig, wave_time: str) -> bool:
    if counter_setup_already_in_mt5(cfg, wave_time):
        log.info(
            f"SKIP duplicitní COUNTER: vlna {wave_time} už má counter pending/pozici v MT5"
        )
        _log_duplicate_blocked(cfg, order_kind="COUNTER", wave_time=wave_time, label="COUNTER")
        return True
    return False


def ext_secondary_setup_already_in_mt5(cfg: BotConfig, wave_time: str) -> bool:
    from infra.orders import EXT_SECONDARY_COMMENT_PREFIX, get_ext_secondary_wave_times

    if wave_time in get_ext_secondary_wave_times(cfg):
        return True
    return wave_time in _position_wave_times_with_prefix(cfg, EXT_SECONDARY_COMMENT_PREFIX)


def ext_counter_time_setup_already_in_mt5(cfg: BotConfig, wave_time: str) -> bool:
    from infra.orders import EXT_COUNTER_TIME_COMMENT_PREFIX, get_ext_counter_time_wave_times

    if wave_time in get_ext_counter_time_wave_times(cfg):
        return True
    return wave_time in _position_wave_times_with_prefix(cfg, EXT_COUNTER_TIME_COMMENT_PREFIX)


def ext_counter_bos_setup_already_in_mt5(cfg: BotConfig, wave_time: str) -> bool:
    from infra.orders import EXT_COUNTER_BOS_COMMENT_PREFIX, get_ext_counter_bos_wave_times

    if wave_time in get_ext_counter_bos_wave_times(cfg):
        return True
    return wave_time in _position_wave_times_with_prefix(cfg, EXT_COUNTER_BOS_COMMENT_PREFIX)


def block_duplicate_ext_secondary(cfg: BotConfig, wave_time: str) -> bool:
    if ext_secondary_setup_already_in_mt5(cfg, wave_time):
        log.info(
            f"SKIP duplicitní EXT secondary: vlna {wave_time} už v MT5"
        )
        _log_duplicate_blocked(
            cfg, order_kind="EXT_SECONDARY", wave_time=wave_time, label="EXT_0236",
        )
        return True
    return False


def ext_counter_peer_blocks_entry(cfg: BotConfig, source: str) -> bool:
    """
    True pokud se nesmi otevrit EXT counter (TIME/BOS), protoze uz bezi druhy typ.

    Kontroluje vsechny ECT_/ECB_ pozice v MT5 — ne jen stejny wave_time.
    """
    from infra.orders import (
        get_ext_counter_bos_wave_times,
        get_ext_counter_time_wave_times,
    )

    if source == "time":
        peer_times = get_ext_counter_bos_wave_times(cfg)
        peer_label = "EXT_COUNTER_BOS"
        order_kind = "EXT_COUNTER_TIME"
    else:
        peer_times = get_ext_counter_time_wave_times(cfg)
        peer_label = "EXT_COUNTER_TIME"
        order_kind = "EXT_COUNTER_BOS"
    if not peer_times:
        return False
    sample_wt = sorted(peer_times)[0]
    log.info(
        f"SKIP EXT counter {source}: uz bezi {peer_label} "
        f"(vlny {sorted(peer_times)})"
    )
    _log_duplicate_blocked(
        cfg,
        order_kind=order_kind,
        wave_time=sample_wt,
        label=f"{peer_label}_PEER_OPEN",
    )
    return True


def block_duplicate_ext_counter_time(cfg: BotConfig, wave_time: str) -> bool:
    if ext_counter_peer_blocks_entry(cfg, "time"):
        return True
    if ext_counter_time_setup_already_in_mt5(cfg, wave_time):
        log.info(
            f"SKIP duplicitní EXT counter time: vlna {wave_time} už v MT5"
        )
        _log_duplicate_blocked(
            cfg, order_kind="EXT_COUNTER_TIME", wave_time=wave_time, label="EXT_COUNTER_TIME",
        )
        return True
    return False


def block_duplicate_ext_counter_bos(cfg: BotConfig, wave_time: str) -> bool:
    if ext_counter_peer_blocks_entry(cfg, "bos"):
        return True
    if ext_counter_bos_setup_already_in_mt5(cfg, wave_time):
        log.info(
            f"SKIP duplicitní EXT counter BOS: vlna {wave_time} už v MT5"
        )
        _log_duplicate_blocked(
            cfg, order_kind="EXT_COUNTER_BOS", wave_time=wave_time, label="EXT_COUNTER_BOS",
        )
        return True
    return False


def block_duplicate_bos_reentry(cfg: BotConfig, broken_wave_time: str | None) -> bool:
    if bos_reentry_already_in_mt5(cfg, broken_wave_time):
        setup_id = broken_wave_time or "bos"
        log.info(f"SKIP duplicitní BOS re-entry: setup {setup_id} už v MT5")
        _log_duplicate_blocked(
            cfg,
            order_kind="BOS_REENTRY",
            wave_time=setup_id,
            label="BOS_REENTRY",
        )
        return True
    return False
