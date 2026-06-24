"""
Jednotný WAVE_TARGET_N / G cyklus na jeden closed bar — parita live ↔ backtest.

Používá live_loop i missed_bar_replay (catch-up po výpadku).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Optional, Set

import pandas as pd

from config.bot_config import BotConfig
from infra.orders import (
    close_positions_on_extension_tp_hit,
    close_positions_on_tp_wave_n,
)
from runtime.wave_target_n_live import sync_wave_target_n_live_state
from strategy.wave_sequence import (
    compute_wave_target_tp_price,
    find_wave_by_time,
    is_tp_wave_index,
)
from strategy.wave_target_n_early import (
    FormingTpWatch,
    extension_tp_hit_on_bar,
    start_forming_tp_watch,
    tp_wave_early_fallback_birth,
    wave_target_n_early_g_enabled,
    wave_target_n_extension_exit_enabled,
)
from strategy.wave_target_n_mode import is_wave_target_n_family


@dataclass
class WaveTargetNBarResult:
    forming_tp_watch: Optional[FormingTpWatch]
    g_extension_done: bool = False
    g_fallback_birth: bool = False
    g_counter_placed: bool = False


def run_wave_target_n_bar_cycle(
    *,
    cfg: BotConfig,
    df: pd.DataFrame,
    waves: list,
    seq_info: dict,
    bar_idx: int,
    birth_by_time: Dict[str, int],
    active_counter_wave_times: Set[str],
    processed_tp_wave_times: Set[str],
    forming_tp_watch: Optional[FormingTpWatch],
    ext1_per_bar: list[bool] | None,
    current_trend: str,
    entries_allowed: bool,
    bar_high: float,
    bar_low: float,
    bar_close: float,
    bar_open: float,
    place_g_extension_counter: Callable[..., None],
    g_extension_closed: Callable[[dict], bool],
    place_fallback_counter: Callable[..., None],
    log_event_fn: Callable[..., None],
    apply_mt5_effects: bool = True,
) -> WaveTargetNBarResult:
    """
    Sync + G catch-up + forming watch + extension hit + TP_WAVE_N event.
    Mutuje processed_tp_wave_times.
    """
    result = WaveTargetNBarResult(forming_tp_watch=forming_tp_watch)
    if not is_wave_target_n_family(cfg):
        return result

    import os as _os
    _fire_on_birth = _os.environ.get("E2E_FIRE_ON_BIRTH") == "1"

    def _event_bar(w: dict) -> int:
        if _fire_on_birth:
            b = birth_by_time.get(str(w.get("wave_time", "")))
            if b is not None:
                return int(b)
        return int(w.get("draw_right", -1))

    target_n = int(getattr(cfg, "tp_target_wave_index", 0) or 0)
    main_trend_dir = (
        1 if current_trend == "bull" else -1 if current_trend == "bear" else 0
    )

    tp_sync = sync_wave_target_n_live_state(
        cfg,
        df,
        waves,
        seq_info,
        birth_by_time=birth_by_time,
        last_bar_idx=int(bar_idx),
        active_counter_wave_times=active_counter_wave_times,
    )
    processed_tp_wave_times |= tp_sync.processed_tp_wave_times
    result.forming_tp_watch = tp_sync.forming_tp_watch

    if wave_target_n_early_g_enabled(cfg):
        watch = result.forming_tp_watch
        if tp_sync.catch_up_extension and watch is not None and apply_mt5_effects:
            ext_stats = close_positions_on_extension_tp_hit(
                cfg,
                trend_dir=int(tp_sync.catch_up_trend_dir),
                armed_tp=float(tp_sync.catch_up_armed_tp or 0.0),
                bar_high=float(tp_sync.catch_up_high or bar_high),
                bar_low=float(tp_sync.catch_up_low or bar_low),
                bar_close=float(tp_sync.catch_up_close or bar_close),
                bar_open=float(tp_sync.catch_up_open or bar_open),
                ext1_protection_per_bar=ext1_per_bar,
                current_bar_idx=int(tp_sync.catch_up_bar or bar_idx),
                wave_birth_by_time=birth_by_time,
                main_trend_dir=main_trend_dir,
            )
            watch.extension_hit_done = True
            result.g_extension_done = True
            log_event_fn(
                cfg,
                "info",
                "TP_EXTENSION_CATCH_UP",
                catch_up_bar=int(tp_sync.catch_up_bar or -1),
                armed_tp=float(tp_sync.catch_up_armed_tp or 0.0),
                trend_dir_closed=int(ext_stats["trend_dir_closed"]),
            )
            if g_extension_closed(ext_stats):
                place_g_extension_counter(
                    cfg=cfg,
                    watch=watch,
                    entries_allowed=entries_allowed,
                )
            result.g_counter_placed = bool(watch.counter_placed)

        for w in waves:
            if _event_bar(w) != int(bar_idx):
                continue
            wt = str(w["wave_time"])
            info = seq_info.get(wt)
            if info is None or info.index_in_trend is None:
                continue
            idx = int(info.index_in_trend)
            if is_tp_wave_index(idx, target_n):
                if result.forming_tp_watch is not None:
                    result.g_counter_placed = bool(
                        result.forming_tp_watch.counter_placed
                    )
                    if (
                        not result.forming_tp_watch.extension_hit_done
                        and tp_wave_early_fallback_birth(cfg)
                    ):
                        result.g_fallback_birth = True
                result.forming_tp_watch = None
                continue
            new_watch = start_forming_tp_watch(
                prev_wave=w,
                index_in_trend=idx,
                target_n=target_n,
                start_bar=int(bar_idx),
            )
            if new_watch is not None:
                result.forming_tp_watch = new_watch

        watch = result.forming_tp_watch
        if (
            wave_target_n_extension_exit_enabled(cfg)
            and watch is not None
            and not watch.extension_hit_done
        ):
            watch.update_extreme(bar_high, bar_low)
            watch.try_arm(cfg)
            if watch.armed and extension_tp_hit_on_bar(
                watch,
                high=bar_high,
                low=bar_low,
                close=bar_close,
                open_=bar_open,
            ) and apply_mt5_effects:
                ext_stats = close_positions_on_extension_tp_hit(
                    cfg,
                    trend_dir=int(watch.trend_dir),
                    armed_tp=float(watch.armed_tp or 0.0),
                    bar_high=bar_high,
                    bar_low=bar_low,
                    bar_close=bar_close,
                    bar_open=bar_open,
                    ext1_protection_per_bar=ext1_per_bar,
                    current_bar_idx=int(bar_idx),
                    wave_birth_by_time=birth_by_time,
                    main_trend_dir=main_trend_dir,
                )
                watch.extension_hit_done = True
                result.g_extension_done = True
                log_event_fn(
                    cfg,
                    "info",
                    "TP_EXTENSION_HIT",
                    armed_tp=float(watch.armed_tp or 0.0),
                    trend_dir=int(watch.trend_dir),
                    trend_dir_closed=int(ext_stats["trend_dir_closed"]),
                    wave_counters_closed=int(ext_stats["wave_counter_closed"]),
                    sl_protected=int(ext_stats["sl_protected"]),
                    bar_close=float(bar_close),
                )
                if g_extension_closed(ext_stats):
                    place_g_extension_counter(
                        cfg=cfg,
                        watch=watch,
                        entries_allowed=entries_allowed,
                    )
                result.g_counter_placed = bool(watch.counter_placed)

    for w in waves:
        w.pop("wave_target_tp_price", None)
        info = seq_info.get(w["wave_time"])
        if info is None:
            continue
        idx = info.index_in_trend if info else None
        if idx is None:
            continue
        if not is_tp_wave_index(idx, target_n):
            continue
        prev_w = find_wave_by_time(waves, info.prev_same_dir_in_trend_wave_time)
        tp_price = compute_wave_target_tp_price(w, prev_w, cfg)
        if tp_price is not None:
            w["wave_target_tp_price"] = float(tp_price)

    for w in waves:
        wt = str(w["wave_time"])
        if wt in processed_tp_wave_times:
            continue
        if _event_bar(w) != int(bar_idx):
            continue
        info = seq_info.get(wt)
        if info is None or info.index_in_trend is None:
            continue
        idx = int(info.index_in_trend)
        if not is_tp_wave_index(idx, target_n):
            continue

        if wave_target_n_early_g_enabled(cfg) and result.g_extension_done:
            processed_tp_wave_times.add(wt)
            continue

        if wave_target_n_early_g_enabled(cfg) and result.forming_tp_watch is not None:
            watch = result.forming_tp_watch
            if watch.extension_hit_done:
                result.forming_tp_watch = None
                processed_tp_wave_times.add(wt)
                continue
            if not tp_wave_early_fallback_birth(cfg):
                result.forming_tp_watch = None
                processed_tp_wave_times.add(wt)
                continue
        result.forming_tp_watch = None

        tp_raw = w.get("wave_target_tp_price", 0.0)
        trend_dir = int(w["dir"])
        tp_price = float(tp_raw) if tp_raw is not None else 0.0

        close_stats = (
            close_positions_on_tp_wave_n(
                cfg,
                trend_dir=trend_dir,
                bar_high=bar_high,
                bar_low=bar_low,
                bar_close=bar_close,
                reason="TP_WAVE_N",
                ext1_protection_per_bar=ext1_per_bar,
                current_bar_idx=int(bar_idx),
                current_wave_time=wt,
                wave_birth_by_time=birth_by_time,
                main_trend_dir=main_trend_dir,
            )
            if apply_mt5_effects
            else {
                "trend_dir_closed": 0,
                "wave_counter_closed": 0,
                "two_sided_closed": 0,
                "sl_protected": 0,
            }
        )
        processed_tp_wave_times.add(wt)
        if apply_mt5_effects:
            log_event_fn(
                cfg,
                "info",
                "TP_WAVE_EVENT",
                wave_time=wt,
                wave_dir=int(trend_dir),
                tp_price=float(tp_price),
                trend_dir_closed=int(close_stats["trend_dir_closed"]),
                wave_counters_closed=int(close_stats["wave_counter_closed"]),
                two_sided_closed=int(close_stats.get("two_sided_closed", 0)),
                sl_protected=int(close_stats["sl_protected"]),
                bar_close=float(bar_close),
            )
        if (
            apply_mt5_effects
            and wave_target_n_early_g_enabled(cfg)
            and result.g_fallback_birth
            and not result.g_counter_placed
        ):
            place_fallback_counter(
                cfg=cfg,
                wave=w,
                info=info,
                trend_dir=trend_dir,
                tp_price=float(tp_price) if tp_price else float(tp_raw or 0.0),
                all_waves=waves,
                entries_allowed=entries_allowed,
            )

    return result
