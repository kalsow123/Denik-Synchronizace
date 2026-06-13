"""
EXT-1 ochrana a RRR better exit pro live bota (parita s `backtest.engine`).
"""
from __future__ import annotations

import logging
from typing import List, Optional, Set

import MetaTrader5 as mt5

from config.bot_config import BotConfig
from config.enums import TPMode
from core.logging_utils import log_event
from strategy.ext_logic import ext_block_wave_time_from_comment, is_ext_block_comment
from strategy.wave_sequence import (
    _get_ext1_protect_flag,
    ext1_protection_active_on_bar,
)

log = logging.getLogger(__name__)

_REASON_RRR_BETTER = "rrr_fixed_better_after_ext1_protect"


def _tp_mode_is_rrr_fixed(cfg: BotConfig) -> bool:
    tpm = getattr(cfg, "tp_mode", TPMode.RRR_FIXED)
    if isinstance(tpm, TPMode):
        return tpm == TPMode.RRR_FIXED
    return str(tpm).lower() == TPMode.RRR_FIXED.value


def _rrr_target_from_mt5_position(cfg: BotConfig, p) -> float:
    """RRR TP uroven z limit TP nebo z entry/SL/rrr (stejne jako engine._rrr_target_price)."""
    tp = float(getattr(p, "tp", 0.0) or 0.0)
    if tp > 0.0:
        return tp
    entry = float(p.price_open)
    sl = float(getattr(p, "sl", 0.0) or 0.0)
    dist = abs(entry - sl)
    rrr = float(getattr(cfg, "rrr", 0.0) or 0.0)
    is_buy = int(p.type) == int(getattr(mt5, "POSITION_TYPE_BUY", 0))
    if is_buy:
        return entry + rrr * dist
    return entry - rrr * dist


def _mt5_position_is_from_ext1(
    wave_id: Optional[str],
    comment: str,
    ext1_wave_times: Set[str],
) -> bool:
    if not ext1_wave_times:
        return False
    if wave_id and str(wave_id) in ext1_wave_times:
        return True
    if is_ext_block_comment(comment):
        parent = ext_block_wave_time_from_comment(comment)
        if parent is not None and str(parent) in ext1_wave_times:
            return True
    return False


def maybe_rrr_fixed_better_exit_after_ext1_protect_end(
    cfg: BotConfig,
    df,
    *,
    ext1_protection_per_bar: List[bool],
    ext1_wave_times: Set[str],
    rrr_edge_done_bar_time: Optional[str] = None,
) -> Optional[str]:
    """
    Pri prechodu EXT-1 ochrany (predchozi bar True, aktualni False) zavre EXT1
    pozice na trhu, pokud close je za RRR targetem (jen tp_mode=RRR_FIXED).

    Returns:
        bar_time posledniho baru (pro detekci opakovaneho volani na stejnem baru).
    """
    from infra.orders import _close_mt5_position_market, _price_digits

    n = 0 if df is None else len(df)
    if n < 1:
        return rrr_edge_done_bar_time

    bar_idx = n - 1
    bar_time_key = str(df.iloc[bar_idx]["time"])

    if not _tp_mode_is_rrr_fixed(cfg) or not _get_ext1_protect_flag(cfg):
        return bar_time_key

    bars = ext1_protection_per_bar
    if bar_idx <= 0 or not (bars[bar_idx - 1] and not bars[bar_idx]):
        return rrr_edge_done_bar_time

    if rrr_edge_done_bar_time == bar_time_key:
        return bar_time_key

    bar = df.iloc[bar_idx]
    current_close = float(bar["close"])

    positions = mt5.positions_get(symbol=cfg.symbol) or ()
    if not positions:
        return bar_time_key

    from infra.trade_tracker import _wave_id_from_comment

    info = mt5.symbol_info(cfg.symbol)
    digits = _price_digits(info)
    closed = 0

    for p in positions:
        if int(getattr(p, "magic", -1)) != int(cfg.magic):
            continue
        comment = str(getattr(p, "comment", "") or "")
        wave_id = _wave_id_from_comment(comment)
        if not _mt5_position_is_from_ext1(wave_id, comment, ext1_wave_times):
            continue

        rrr_target = _rrr_target_from_mt5_position(cfg, p)
        is_buy = int(p.type) == int(getattr(mt5, "POSITION_TYPE_BUY", 0))
        if (is_buy and current_close > rrr_target) or (
            not is_buy and current_close < rrr_target
        ):
            if _close_mt5_position_market(
                cfg, p, reason=_REASON_RRR_BETTER, position_kind="EXT1_RRR_BETTER", digits=digits,
            ):
                closed += 1
                log_event(
                    cfg, "info", "EXT1_PROTECT_END_BETTER_RRR_TP",
                    ticket=int(p.ticket),
                    wave_id=wave_id,
                    original_rrr_target=rrr_target,
                    market_exit_price=current_close,
                )

    if closed:
        log.info(
            "EXT1 RRR better exit: zavreno %d pozic po skonceni ochrany (close=%.5f)",
            closed, current_close,
        )

    return bar_time_key
