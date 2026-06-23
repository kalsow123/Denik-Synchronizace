"""
Live WAVE statistiky — stejný slice jako backtest wave_isolation_study report.

Sleduje pouze obchody s position_kind=WAVE (comment W{wave_time}).
EXT/CNTR/PP se logují zvlášť s position_kind, ale nepočítají do WAVE souhrnu.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from config.bot_config import BotConfig
from backtest.stats import classify_position_kind
from core.logging_utils import log_event
from infra.trade_tracker import _wave_id_from_comment
from strategy.ext_logic import (
    EXT_COUNTER_BOS_COMMENT_PREFIX,
    EXT_COUNTER_TIME_COMMENT_PREFIX,
    EXT_PRIMARY_WAVE_COMMENT_PREFIX,
    EXT_SECONDARY_COMMENT_PREFIX,
)

def position_kind_from_mt5_comment(
    comment: str,
    cfg: BotConfig | None = None,
    promoted_two_sided_wave_times: set[str] | None = None,
) -> str:
    """Klasifikace pozice dle MT5 comment — stejná logika jako backtest stats."""
    c = str(comment or "")
    if c.startswith("PP_") or c.startswith("PPM_"):
        return classify_position_kind(is_pp=True, is_counter=False, is_bos_reentry=False)
    if c.startswith("CNTR_"):
        return classify_position_kind(
            is_pp=False, is_counter=True, is_bos_reentry=False, entry_tag="wave_counter",
        )
    if c.startswith("TS2_"):
        is_promoted = False
        if cfg and getattr(cfg, "live_study_promoted_two_sided_as_wave", False):
            wt = _wave_id_from_comment(c)
            if promoted_two_sided_wave_times and wt in promoted_two_sided_wave_times:
                is_promoted = True
        
        return classify_position_kind(
            is_pp=False, is_counter=False, is_bos_reentry=False,
            is_two_sided_mirror=not is_promoted,
        )
    if c.startswith(EXT_SECONDARY_COMMENT_PREFIX):
        return classify_position_kind(
            is_pp=False, is_counter=False, is_bos_reentry=False,
            is_ext=True, entry_tag="ext_0236",
        )
    if c.startswith(EXT_COUNTER_TIME_COMMENT_PREFIX):
        return classify_position_kind(
            is_pp=False, is_counter=True, is_bos_reentry=False,
            is_ext=True, entry_tag="ext_counter_time",
        )
    if c.startswith(EXT_COUNTER_BOS_COMMENT_PREFIX):
        return classify_position_kind(
            is_pp=False, is_counter=True, is_bos_reentry=False,
            is_ext=True, entry_tag="ext_counter_bos",
        )
    if c.startswith(EXT_PRIMARY_WAVE_COMMENT_PREFIX):
        return classify_position_kind(
            is_pp=False, is_counter=False, is_bos_reentry=False, is_ext=True,
        )
    if c.startswith("RENT_") or c.startswith("BOS_"):
        return classify_position_kind(
            is_pp=False, is_counter=False, is_bos_reentry=True,
        )
    if c.startswith("W") and len(c) == 13 and c[1:].isdigit():
        return "WAVE"
    return "WAVE"


@dataclass
class LiveWaveStatsTracker:
    """Kumulativní WAVE slice (parita grid wave_isolation_study reportu)."""

    wave_closes: int = 0
    wave_pnl_usd: float = 0.0
    wave_wins: int = 0
    other_closes: int = 0

    def on_position_closed(
        self,
        *,
        comment: str,
        pnl_usd: float,
        cfg: BotConfig | None = None,
        promoted_two_sided_wave_times: set[str] | None = None,
    ) -> Optional[str]:
        kind = position_kind_from_mt5_comment(
            comment,
            cfg=cfg,
            promoted_two_sided_wave_times=promoted_two_sided_wave_times,
        )
        if kind == "WAVE":
            self.wave_closes += 1
            self.wave_pnl_usd += float(pnl_usd)
            if float(pnl_usd) > 0:
                self.wave_wins += 1
        else:
            self.other_closes += 1
        return kind


def maybe_emit_live_wave_summary(
    cfg: BotConfig,
    tracker: LiveWaveStatsTracker,
    *,
    force: bool = False,
    last_emit_wave_closes: int = 0,
) -> int:
    """Emit LIVE_WAVE_SUMMARY do jsonl po každém WAVE close (nebo force)."""
    if not force and tracker.wave_closes == last_emit_wave_closes:
        return last_emit_wave_closes
    if tracker.wave_closes <= 0 and not force:
        return last_emit_wave_closes
    win_rate = (
        round(100.0 * tracker.wave_wins / max(tracker.wave_closes, 1), 1)
        if tracker.wave_closes
        else 0.0
    )
    log_event(
        cfg,
        "info",
        "LIVE_WAVE_SUMMARY",
        trades_wave=int(tracker.wave_closes),
        net_pnl_wave_usd=round(float(tracker.wave_pnl_usd), 2),
        win_rate_wave_pct=float(win_rate),
        other_closes=int(tracker.other_closes),
        message="WAVE slice — parita s backtest wave_isolation_study reportem",
    )
    return tracker.wave_closes
