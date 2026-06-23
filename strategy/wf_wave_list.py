"""WF merge vlnového seznamu — sdílené backtest engine + live loop."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from config.bot_config import BotConfig
from strategy.wick_fakeout import (
    WAVE_ORIGIN_WF,
    build_wf_wave,
    evaluate_wf_from_df,
    resume_classic_waves_after_wf,
)


def _birth_map_from_waves(
    waves: list[dict],
    extra: dict[str, int] | None = None,
) -> dict[str, int]:
    birth: dict[str, int] = dict(extra or {})
    for w in waves:
        wt = str(w.get("wave_time", "") or "")
        if not wt or wt in birth:
            continue
        dr = w.get("draw_right")
        if dr is not None:
            birth[wt] = int(dr)
    return birth


def merge_wf_continued_classic_waves(
    df: pd.DataFrame,
    cfg: BotConfig,
    waves: list[dict],
    wf_wave: dict,
    continued: list[dict],
    continued_birth: dict[str, int],
    *,
    wave_birth_by_time: dict[str, int] | None = None,
    ohlc=None,
) -> set[str]:
    """
    Nahradí upfront vlny od draw_right+1 resumed klasickými vlnami (shodně s engine).
    Mutuje ``waves`` in-place. Vrací odstraněné wave_time.
    """
    from strategy.trend_bos import apply_tp_mode_to_waves
    from strategy.wave_detection_pine import _apply_wave_plus_extend

    birth = _birth_map_from_waves(waves, wave_birth_by_time)
    from_bar = int(wf_wave.get("draw_right", 0)) + 1

    remove_times: set[str] = set()
    for w in waves:
        wwt = str(w.get("wave_time", "") or "")
        if str(w.get("wave_origin", "")) == WAVE_ORIGIN_WF:
            continue
        b = birth.get(wwt)
        if b is not None and int(b) >= from_bar:
            remove_times.add(wwt)

    wf_wt = str(wf_wave.get("wave_time", "") or "")
    existing = {str(w.get("wave_time", "") or "") for w in waves}
    if wf_wt and wf_wt not in existing:
        wf_wave.setdefault("wave_origin", WAVE_ORIGIN_WF)
        wf_wave["wf_wave_position"] = True
        waves.append(wf_wave)
        existing.add(wf_wt)
        if wf_wt not in birth and wf_wave.get("draw_right") is not None:
            birth[wf_wt] = int(wf_wave["draw_right"])

    if remove_times:
        waves[:] = [
            w for w in waves
            if str(w.get("wave_time", "") or "") not in remove_times
        ]
        existing -= remove_times

    apply_tp_mode_to_waves(continued, cfg)
    for w in continued:
        w["wf_continued_classic"] = True
        wwt = str(w["wave_time"])
        if wwt in existing:
            continue
        if "wave_time_dt" not in w:
            w["wave_time_dt"] = pd.to_datetime(wwt, format="%Y%m%d%H%M")
        waves.append(w)
        existing.add(wwt)
        birth[wwt] = int(continued_birth[wwt])

    if getattr(cfg, "wave_plus", False) and waves:
        waves.sort(key=lambda w: int(w.get("draw_left", 0)))
        start_idx = 0
        for j, w in enumerate(waves):
            if int(w.get("draw_left", 0)) >= from_bar:
                start_idx = max(0, j - 1)
                break
        _apply_wave_plus_extend(df, cfg, waves, start_idx=start_idx, ohlc=ohlc)

    # Propaguj births WF vlny + resumed klasickych vln zpet do volajiciho
    # wave_birth_by_time (jinak zustane None → birth_bar_gate blokuje vlnu
    # napořád). Engine to dela analogicky (self.wave_birth_by_time[wwt] = b).
    if wave_birth_by_time is not None:
        wf_wt2 = str(wf_wave.get("wave_time", "") or "")
        if wf_wt2 and wf_wt2 not in remove_times and wf_wave.get("draw_right") is not None:
            wave_birth_by_time.setdefault(wf_wt2, int(wf_wave["draw_right"]))
        for w in continued:
            wwt = str(w.get("wave_time", "") or "")
            if wwt and wwt in continued_birth:
                wave_birth_by_time.setdefault(wwt, int(continued_birth[wwt]))

    return remove_times


@dataclass
class WfWavePrepResult:
    wf_wave: dict | None = None
    eval_result: dict | None = None
    ext_skipped: bool = False
    resumed_count: int = 0
    activation_bar_idx: int | None = None


def prepare_waves_after_wf_eval(
    df: pd.DataFrame,
    cfg: BotConfig,
    waves: list[dict],
) -> WfWavePrepResult:
    """
    Vyhodnotí WF na poslední vlně, případně merge resumed vln (před seq_info).
    Vstupní order se neposílá — caller řeší send_order až po trend/seq sync.
    """
    if not bool(getattr(cfg, "wf_enabled", False)) or not waves:
        return WfWavePrepResult()

    last_wave = waves[-1]
    wf_result = evaluate_wf_from_df(df, last_wave, cfg)
    if wf_result is None:
        return WfWavePrepResult()

    if wf_result.get("status") == "ext_skipped":
        return WfWavePrepResult(ext_skipped=True, eval_result=wf_result)

    if wf_result.get("status") != "activate":
        return WfWavePrepResult()

    bar = df.iloc[-1]
    wt_raw = bar["time"]
    wt_str = (
        wt_raw.strftime("%Y%m%d%H%M")
        if hasattr(wt_raw, "strftime")
        else str(wt_raw)
    )
    wf_wave = build_wf_wave(
        cfg,
        last_wave=wf_result["last_wave"],
        fakeout_pivot=float(wf_result["fakeout_pivot"]),
        fakeout_bar_idx=int(wf_result["fakeout_bar_idx"]),
        activation_bar_idx=int(wf_result.get("activation_bar_idx", len(df) - 1)),
        wave_time_str=wt_str,
        window_min_low=wf_result.get("window_min_low"),
        window_max_high=wf_result.get("window_max_high"),
    )
    if wf_wave is None:
        return WfWavePrepResult(eval_result=wf_result)

    continued, continued_birth = resume_classic_waves_after_wf(df, cfg, wf_wave)
    merge_wf_continued_classic_waves(
        df,
        cfg,
        waves,
        wf_wave,
        continued,
        continued_birth,
    )
    return WfWavePrepResult(
        wf_wave=wf_wave,
        eval_result=wf_result,
        resumed_count=len(continued),
    )
