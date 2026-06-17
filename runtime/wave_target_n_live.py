"""
Live sync WAVE_TARGET_N / G stavu z historických vln + barů (parita s backtestem).

Po restartu / wake-up obnoví:
  - processed_tp_wave_times (TP-vlny W(N) už narozené před aktuálním barem)
  - forming_tp_watch (replay W(N-1) → forming, včetně ARM / extension hit)
  - catch-up extension close, pokud hit proběhl na minulém baru a bot nebyl online
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Set

import pandas as pd

from config.bot_config import BotConfig
from strategy.wave_sequence import is_tp_wave_index
from strategy.wave_target_n_early import (
    FormingTpWatch,
    extension_tp_hit_on_bar,
    g_counter_wave_time,
    start_forming_tp_watch,
    wave_target_n_early_g_enabled,
    wave_target_n_extension_exit_enabled,
)
from strategy.wave_target_n_mode import is_wave_target_n_family


@dataclass
class WaveTargetNLiveSync:
    processed_tp_wave_times: Set[str]
    forming_tp_watch: Optional[FormingTpWatch]
    catch_up_extension: bool = False
    catch_up_bar: Optional[int] = None
    catch_up_high: Optional[float] = None
    catch_up_low: Optional[float] = None
    catch_up_close: Optional[float] = None
    catch_up_open: Optional[float] = None
    catch_up_armed_tp: Optional[float] = None
    catch_up_trend_dir: int = 0


def _wave_birth_bar(
    wave: dict,
    birth_by_time: Dict[str, int],
) -> Optional[int]:
    wt = str(wave.get("wave_time", ""))
    if wt in birth_by_time:
        return int(birth_by_time[wt])
    dr = wave.get("draw_right")
    if dr is None:
        return None
    try:
        bi = int(dr)
    except (TypeError, ValueError):
        return None
    return bi if bi >= 0 else None


def _replay_forming_watch(
    *,
    cfg: BotConfig,
    df: pd.DataFrame,
    watch: FormingTpWatch,
    last_bar_idx: int,
) -> WaveTargetNLiveSync:
    """Replay barů (start_bar+1 .. last_bar_idx-1) — stav před zpracováním aktuálního baru."""
    extra = WaveTargetNLiveSync(
        processed_tp_wave_times=set(),
        forming_tp_watch=watch,
    )
    if not wave_target_n_extension_exit_enabled(cfg):
        return extra

    end_replay = int(last_bar_idx) - 1
    if end_replay < int(watch.start_bar) + 1:
        return extra

    for bar_idx in range(int(watch.start_bar) + 1, end_replay + 1):
        if bar_idx < 0 or bar_idx >= len(df):
            continue
        row = df.iloc[bar_idx]
        hi = float(row["high"])
        lo = float(row["low"])
        cl = float(row["close"])
        op = float(row["open"])
        watch.update_extreme(hi, lo)
        watch.try_arm(cfg)
        if not watch.armed:
            continue
        if extension_tp_hit_on_bar(
            watch, high=hi, low=lo, close=cl, open_=op,
        ):
            watch.extension_hit_done = True
            extra.catch_up_extension = True
            extra.catch_up_bar = int(bar_idx)
            extra.catch_up_high = hi
            extra.catch_up_low = lo
            extra.catch_up_close = cl
            extra.catch_up_open = op
            extra.catch_up_armed_tp = float(watch.armed_tp or 0.0)
            extra.catch_up_trend_dir = int(watch.trend_dir)
            break

    return extra


def sync_wave_target_n_live_state(
    cfg: BotConfig,
    df: pd.DataFrame,
    waves: list,
    seq_info: dict,
    *,
    birth_by_time: Dict[str, int],
    last_bar_idx: int,
    active_counter_wave_times: Set[str],
) -> WaveTargetNLiveSync:
    """
    Obnoví TP/G live stav z detekovaných vln a OHLC (jako backtest do last_bar-1).

    TP-vlna W(N) s birth < last_bar_idx → processed (TP event už proběhl).
    TP-vlna narozená na last_bar_idx → live loop ji zpracuje v aktuálním cyklu.
    """
    processed: Set[str] = set()
    watch: Optional[FormingTpWatch] = None
    target_n = int(getattr(cfg, "tp_target_wave_index", 0) or 0)

    if not is_wave_target_n_family(cfg) or target_n <= 0:
        return WaveTargetNLiveSync(processed_tp_wave_times=processed, forming_tp_watch=None)

    items: list[tuple[int, dict, int]] = []
    for w in waves:
        wt = str(w.get("wave_time", ""))
        info = seq_info.get(wt)
        if info is None or info.index_in_trend is None:
            continue
        birth = _wave_birth_bar(w, birth_by_time)
        if birth is None:
            continue
        items.append((birth, w, int(info.index_in_trend)))
    items.sort(key=lambda x: (x[0], str(x[1].get("wave_time", ""))))

    for birth, w, idx in items:
        wt = str(w.get("wave_time", ""))
        if is_tp_wave_index(idx, target_n):
            if int(birth) < int(last_bar_idx):
                processed.add(wt)
            watch = None
            continue
        nw = start_forming_tp_watch(
            prev_wave=w,
            index_in_trend=idx,
            target_n=target_n,
            start_bar=int(birth),
        )
        if nw is not None:
            watch = nw

    result = WaveTargetNLiveSync(
        processed_tp_wave_times=processed,
        forming_tp_watch=watch,
    )

    if watch is not None and wave_target_n_early_g_enabled(cfg):
        replay = _replay_forming_watch(
            cfg=cfg, df=df, watch=watch, last_bar_idx=last_bar_idx,
        )
        result.forming_tp_watch = replay.forming_tp_watch
        result.catch_up_extension = replay.catch_up_extension
        result.catch_up_bar = replay.catch_up_bar
        result.catch_up_high = replay.catch_up_high
        result.catch_up_low = replay.catch_up_low
        result.catch_up_close = replay.catch_up_close
        result.catch_up_open = replay.catch_up_open
        result.catch_up_armed_tp = replay.catch_up_armed_tp
        result.catch_up_trend_dir = replay.catch_up_trend_dir

        w = result.forming_tp_watch
        if w is not None:
            key = g_counter_wave_time(w)
            if key in active_counter_wave_times:
                w.counter_placed = True
                w.counter_wave_time_key = key
            else:
                prev_wt = str(w.prev_wave.get("wave_time", ""))
                for cwt in active_counter_wave_times:
                    if cwt.endswith(f"@G{int(w.target_tp_index)}"):
                        if prev_wt and cwt.startswith(prev_wt):
                            w.counter_placed = True
                            w.counter_wave_time_key = cwt
                            break

    return result


def reset_wave_target_n_runtime_state() -> tuple[Set[str], None]:
    """Vyprázdní in-memory TP/G stav — další sync z MT5 OHLC ho obnoví (restart/outage)."""
    return set(), None
