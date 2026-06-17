"""
Replay fronty neúspěšných WAVE orderů (transient MT5 chyby) — parita s birth-bar vstupem.

Pravidla:
  - Replay na baru narození vlny (stejný closed bar).
  - Po restartu / session wake-up fronta se vyprázdní (recovery ji nahradí).
  - Po opuštění birth baru se vlna zahodí (backtest neotevírá později).
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Set

from config.bot_config import BotConfig
from core.logging_utils import log_event
from core.signal_keys import get_signal_key


def wave_birth_bar_index(
    wave_time: str,
    wave_birth_by_time: dict[str, int],
) -> Optional[int]:
    birth = wave_birth_by_time.get(wave_time)
    if birth is None:
        birth = wave_birth_by_time.get(str(wave_time))
    if birth is None:
        return None
    return int(birth)


def failed_signal_replay_eligible(
    wave_time: str,
    *,
    wave_birth_by_time: dict[str, int],
    last_bar_idx: int,
    new_bar_indices: list[int] | None = None,
) -> bool:
    """
    True = smí se znovu zkusit send_order z failed_signals fronty.

    Povoleno jen na birth baru (parita backtest engine — vstup jen při narození).
    new_bar_indices slouží k tomu, aby replay proběhl ve stejném cyklu jako missed-bar
    catch-up pro poslední missed bar v batchi.
    """
    birth = wave_birth_bar_index(wave_time, wave_birth_by_time)
    if birth is None:
        return False
    if int(birth) == int(last_bar_idx):
        return True
    if new_bar_indices and int(birth) in {int(i) for i in new_bar_indices}:
        return True
    return False


def abandon_failed_signal(
    *,
    cfg: BotConfig,
    sig_key: str,
    wave_time: str,
    sent_signals: Set[str],
    failed_signals: Dict[str, Dict[str, Any]],
    reason: str,
) -> None:
    failed_signals.pop(sig_key, None)
    sent_signals.add(sig_key)
    log_event(
        cfg,
        "info",
        "SIGNAL_REPLAY_ABANDONED",
        wave_id=str(wave_time),
        signal_key=sig_key,
        reason=str(reason),
    )


def clear_failed_signals_on_recovery(
    failed_signals: Dict[str, Dict[str, Any]],
    *,
    cfg: BotConfig | None = None,
    reason: str = "startup_recovery",
) -> int:
    """Po restartu / session wake-up — recovery řeší pendingy, fronta se maže."""
    n = len(failed_signals)
    if n and cfg is not None:
        log_event(
            cfg,
            "info",
            "FAILED_SIGNALS_CLEARED",
            count=int(n),
            reason=str(reason),
        )
    failed_signals.clear()
    return n
