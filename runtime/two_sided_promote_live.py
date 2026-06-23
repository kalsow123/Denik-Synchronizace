"""
Logika pro promote two_sided_mirror obchodu po BOS flipu.
Parita s backtest engine, r. 2232-2237.
"""
from __future__ import annotations

import logging
from typing import Set

from config.bot_config import BotConfig
from infra.orders import TWO_SIDED_MIRROR_COMMENT_PREFIX
from infra.trade_tracker import _wave_id_from_comment

log = logging.getLogger(__name__)


def on_bos_flip_promote_two_sided(
    *,
    flipped: bool,
    existing_promoted: Set[str],
    open_comments: list[str],
    cfg: BotConfig | None = None,
) -> Set[str]:
    """
    Po BOS flipu ziska seznam aktualne otevrenych pozic v MT5.
    Vsechny TS2_ pozice, ktere prezily BOS flip close (tj. ty s dir == new_trend_dir),
    jsou oznaceny k promote (is_two_sided_mirror = False v engine).
    Vraci novou mnozinu promoted_wave_times.
    """
    if not flipped:
        return existing_promoted

    new_promoted = set(existing_promoted)
    newly_added: list[str] = []
    for comment in open_comments:
        c = str(comment or "")
        if c.startswith(TWO_SIDED_MIRROR_COMMENT_PREFIX):
            wt = _wave_id_from_comment(c)
            if wt and wt not in new_promoted:
                new_promoted.add(wt)
                newly_added.append(wt)
                log.info(f"Promoted TS2_ pozici {wt} do WAVE (BOS flip)")

    if cfg and newly_added:
        from infra.orders import clear_fixed_tp_on_ts2_wave_times

        clear_fixed_tp_on_ts2_wave_times(cfg, wave_times=set(newly_added))

    return new_promoted
