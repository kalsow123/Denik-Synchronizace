"""
WAVE_TARGET_N — varianta G (forming_qualified + extension price hit).

Sleduje forming TP-vlnu po potvrzeni W(N-1), ARM extension TP po wave_min_pct,
exit na zasah armed_tp ceny (bez min_opp_bars). Oficialni detekce vlny beze zmeny.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from config.bot_config import BotConfig
from config.enums import TPMode, TpWaveEarlyMode, TpWaveExitOn, TpWaveIntrabarPriority
from strategy.wave_sequence import compute_wave_target_tp_price, is_tp_wave_index

# MT5 comment: CNTR_ (5 znaku) + wave_time_key, max 31 znaku celkem
_G_COUNTER_WAVE_TIME_MAX_LEN = 26


def _cfg_tp_wave_early_mode(cfg: BotConfig) -> TpWaveEarlyMode:
    raw = getattr(cfg, "tp_wave_early_mode", TpWaveEarlyMode.OFF)
    if isinstance(raw, TpWaveEarlyMode):
        return raw
    try:
        return TpWaveEarlyMode(str(raw).lower())
    except ValueError:
        return TpWaveEarlyMode.OFF


def wave_target_n_early_g_enabled(cfg: BotConfig) -> bool:
    from strategy.wave_target_n_mode import is_wave_target_n_g
    return is_wave_target_n_g(cfg)


def wave_target_n_extension_exit_enabled(cfg: BotConfig) -> bool:
    return wave_target_n_early_g_enabled(cfg)


def tp_wave_early_fallback_birth(cfg: BotConfig) -> bool:
    return bool(getattr(cfg, "tp_wave_early_fallback_birth", True))


def tp_wave_intrabar_tp_before_sl(cfg: BotConfig) -> bool:
    raw = getattr(cfg, "tp_wave_intrabar_priority", TpWaveIntrabarPriority.TP_BEFORE_SL)
    if isinstance(raw, TpWaveIntrabarPriority):
        return raw == TpWaveIntrabarPriority.TP_BEFORE_SL
    return str(raw).lower() == TpWaveIntrabarPriority.TP_BEFORE_SL.value


def should_start_forming_tp_watch(index_in_trend: Optional[int], target_n: int) -> bool:
    """True po birth vlny idx, kdyz dalsi same-dir vlna ma byt TP-vlna (N, N+2, ...)."""
    if index_in_trend is None or index_in_trend <= 0:
        return False
    return is_tp_wave_index(int(index_in_trend) + 1, target_n)


def _forming_pivot_from_prev_wave(prev_wave: dict, trend_dir: int) -> Optional[float]:
    try:
        if int(trend_dir) == 1:
            return float(prev_wave["box_top"])
        return float(prev_wave["box_bottom"])
    except (KeyError, TypeError, ValueError):
        return None


def _forming_move_pct(pivot: float, extreme: float) -> float:
    if abs(pivot) <= 0.0:
        return 0.0
    return abs(extreme - pivot) / abs(pivot) * 100.0


@dataclass
class FormingTpWatch:
    """Interni stav forming TP-vlny mezi birth W(N-1) a birth W(N)."""

    trend_dir: int
    prev_wave: dict
    target_tp_index: int
    start_bar: int
    pivot: float
    extreme: float
    armed: bool = False
    armed_tp: Optional[float] = None
    extension_hit_done: bool = False
    counter_placed: bool = False
    counter_wave_time_key: Optional[str] = None
    min_low_since_arm: Optional[float] = None
    max_high_since_arm: Optional[float] = None

    def forming_wave_dict(self) -> dict:
        if self.trend_dir == 1:
            box_top = float(self.extreme)
            box_bot = float(self.pivot)
        else:
            box_top = float(self.pivot)
            box_bot = float(self.extreme)
        return {
            "dir": int(self.trend_dir),
            "box_top": box_top,
            "box_bottom": box_bot,
        }

    def update_extreme(self, high: float, low: float) -> None:
        if self.trend_dir == 1:
            self.extreme = max(float(self.extreme), float(high))
        else:
            self.extreme = min(float(self.extreme), float(low))

    def move_pct(self) -> float:
        return _forming_move_pct(self.pivot, self.extreme)

    def try_arm(self, cfg: BotConfig) -> bool:
        if self.armed:
            return False
        thr = float(getattr(cfg, "wave_min_pct", 0.0) or 0.0)
        if self.move_pct() < thr:
            return False
        tp = compute_wave_target_tp_price(
            self.forming_wave_dict(), self.prev_wave, cfg,
        )
        if tp is None:
            return False
        self.armed = True
        self.armed_tp = float(tp)
        return True


def g_counter_wave_time(watch: FormingTpWatch) -> str:
    """
    Stabilni wave_time klic pro G counter pred existenci W(N).
    Vejde se do MT5 comment CNTR_ (max 31 znaku celkem).
    """
    prev_wt = str(watch.prev_wave.get("wave_time", "") or "")
    suffix = f"@G{int(watch.target_tp_index)}"
    key = f"{prev_wt}{suffix}"
    if len(key) <= _G_COUNTER_WAVE_TIME_MAX_LEN:
        return key
    keep = max(1, _G_COUNTER_WAVE_TIME_MAX_LEN - len(suffix))
    return f"{prev_wt[:keep]}{suffix}"


def wave_counter_entry_allowed(
    cfg: BotConfig,
    *,
    post_ext_suppressed: bool = False,
) -> bool:
    if not bool(getattr(cfg, "counter_position_enabled", False)):
        return False
    if post_ext_suppressed:
        return False
    return True


def start_forming_tp_watch(
    *,
    prev_wave: dict,
    index_in_trend: int,
    target_n: int,
    start_bar: int,
) -> Optional[FormingTpWatch]:
    if not should_start_forming_tp_watch(index_in_trend, target_n):
        return None
    trend_dir = int(prev_wave.get("dir", 0) or 0)
    if trend_dir not in (1, -1):
        return None
    pivot = _forming_pivot_from_prev_wave(prev_wave, trend_dir)
    if pivot is None:
        return None
    return FormingTpWatch(
        trend_dir=trend_dir,
        prev_wave=dict(prev_wave),
        target_tp_index=int(index_in_trend) + 1,
        start_bar=int(start_bar),
        pivot=float(pivot),
        extreme=float(pivot),
    )


def extension_tp_hit_on_bar(
    watch: FormingTpWatch,
    *,
    high: float,
    low: float,
    close: float,
    open_: float,
) -> bool:
    """
    True pokud bar zasahl armed_tp ve smeru forming vlny (ne reverz po extrému).
    """
    if not watch.armed or watch.armed_tp is None or watch.extension_hit_done:
        return False
    armed_tp = float(watch.armed_tp)
    td = int(watch.trend_dir)

    if td == 1:
        if float(high) < armed_tp:
            return False
        if float(close) < float(open_) and float(close) < armed_tp:
            return False
        return True

    if float(low) > armed_tp:
        return False
    if float(close) > float(open_) and float(close) > armed_tp:
        return False
    return True


def sl_hit_for_trade(trade: Any, *, high: float, low: float) -> bool:
    d = int(getattr(trade, "dir", 0))
    sl = float(getattr(trade, "sl", 0.0) or 0.0)
    if d == 1:
        return float(low) <= sl
    return float(high) >= sl


def trade_exit_on_extension_bar(
    trade: Any,
    *,
    high: float,
    low: float,
    armed_tp: Optional[float],
    ext_hit: bool,
    tp_before_sl: bool,
) -> tuple[Optional[float], Optional[str]]:
    """
    Rozhodni close pro jeden trade na baru s moznym extension hit.
    Vraci (price, reason) nebo (None, None) pokud se nezavira.
    """
    sl_hit = sl_hit_for_trade(trade, high=high, low=low)

    if tp_before_sl:
        if ext_hit and armed_tp is not None:
            return float(armed_tp), "TP_EXTENSION_HIT"
        if sl_hit:
            return float(trade.sl), "SL"
    else:
        if sl_hit:
            return float(trade.sl), "SL"
        if ext_hit and armed_tp is not None:
            return float(armed_tp), "TP_EXTENSION_HIT"
    return None, None
