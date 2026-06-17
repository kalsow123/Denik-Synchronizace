"""
WF (Wick Fakeout) — live bar-by-bar runtime (parita s backtest/engine.py).

Engine na kazdem baru:
  1) on_bar
  2) check_wf
  3) on_new_wave pro vlny narozene na tomto baru

Live drive volal prepare_waves_after_wf_eval() — jednorazovy replay posledni vlny.
Tento modul udrzuje WickFakeoutTracker mezi cykly a zpracovava jen nove uzavrene bary.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

import pandas as pd

from config.bot_config import BotConfig
from strategy.wave_detection_pine import compute_wave_birth_bars_pine
from strategy.wf_wave_list import WfWavePrepResult, merge_wf_continued_classic_waves
from strategy.wick_fakeout import (
    WickFakeoutTracker,
    build_wf_wave,
    resume_classic_waves_after_wf,
)


def _bar_ts(df: pd.DataFrame, idx: int) -> pd.Timestamp:
    return pd.Timestamp(df["time"].iloc[idx])


def _waves_by_birth_bar(
    waves: list[dict],
    wave_birth_by_time: dict[str, int],
) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = defaultdict(list)
    for w in waves:
        wt = str(w.get("wave_time", "") or "")
        if not wt:
            continue
        b = wave_birth_by_time.get(wt)
        if b is None and w.get("draw_right") is not None:
            b = int(w["draw_right"])
        if b is not None:
            out[int(b)].append(w)
    return out


class WfLiveRuntime:
    """Inkrementalni WF tracker pro live loop — stejne poradi jako BacktestEngine."""

    def __init__(self) -> None:
        self._tracker = WickFakeoutTracker()
        self._last_processed_bar_time: Optional[pd.Timestamp] = None
        self._df_anchor_time: Optional[pd.Timestamp] = None
        self._activation_results: list[WfWavePrepResult] = []

    def reset(self) -> None:
        self._tracker = WickFakeoutTracker()
        self._last_processed_bar_time = None
        self._df_anchor_time = None
        self._activation_results = []

    def pop_activation_results(self) -> list[WfWavePrepResult]:
        out = self._activation_results[:]
        self._activation_results = []
        return out

    def _needs_resync(self, df: pd.DataFrame) -> bool:
        if df is None or df.empty:
            return False
        anchor = _bar_ts(df, 0)
        if self._df_anchor_time is None:
            return self._last_processed_bar_time is not None
        return anchor != self._df_anchor_time

    def _replay_state_only(
        self,
        df: pd.DataFrame,
        cfg: BotConfig,
        waves: list[dict],
        wave_birth_by_time: dict[str, int],
    ) -> None:
        """Obnovi tracker stav pres cele df bez WF aktivace (startup / rolled window)."""
        self._tracker = WickFakeoutTracker()
        if df is None or len(df) < 2:
            return
        waves_by_bar = _waves_by_birth_bar(waves, wave_birth_by_time)
        for i in range(1, len(df)):
            row = df.iloc[i]
            self._tracker.on_bar(
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
                i,
            )
            for w in waves_by_bar.get(i, []):
                if not bool(w.get("post_ext_trend_suppressed", False)):
                    self._tracker.on_new_wave(w, birth_bar=i, df=df)

    def _build_activation(
        self,
        df: pd.DataFrame,
        cfg: BotConfig,
        waves: list[dict],
        wf_result: dict,
        bar_idx: int,
    ) -> WfWavePrepResult:
        row = df.iloc[bar_idx]
        wt_raw = row["time"]
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
            activation_bar_idx=bar_idx,
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
            wave_birth_by_time=compute_wave_birth_bars_pine(df, cfg),
        )
        self._tracker.on_new_wave(
            wf_wave,
            birth_bar=int(wf_wave.get("draw_right", bar_idx)),
            df=df,
            force_reset=True,
        )
        return WfWavePrepResult(
            wf_wave=wf_wave,
            eval_result=wf_result,
            resumed_count=len(continued),
            activation_bar_idx=int(bar_idx),
        )

    def process(
        self,
        df: pd.DataFrame,
        cfg: BotConfig,
        waves: list[dict],
        *,
        wave_birth_by_time: dict[str, int] | None = None,
    ) -> WfWavePrepResult:
        """
        Zpracuje nove bary od posledniho cyklu. Pri prvnim behu nebo posunu okna
        nejdriv synchronizuje tracker bez aktivace (historie), pak zpracuje jen
        bary novejsi nez last_processed_bar_time vcetne WF aktivace.
        """
        if not bool(getattr(cfg, "wf_enabled", False)):
            return WfWavePrepResult()
        if df is None or len(df) < 2 or not waves:
            return WfWavePrepResult()

        birth = wave_birth_by_time or compute_wave_birth_bars_pine(df, cfg)
        waves_by_bar = _waves_by_birth_bar(waves, birth)

        if self._needs_resync(df):
            self._replay_state_only(df, cfg, waves, birth)
            self._last_processed_bar_time = _bar_ts(df, len(df) - 1)
            self._df_anchor_time = _bar_ts(df, 0)
            return WfWavePrepResult()

        self._activation_results = []
        activation: Optional[WfWavePrepResult] = None
        ext_skipped: Optional[WfWavePrepResult] = None

        for i in range(1, len(df)):
            bar_time = _bar_ts(df, i)
            if (
                self._last_processed_bar_time is not None
                and bar_time <= self._last_processed_bar_time
            ):
                continue

            row = df.iloc[i]
            close_ = float(row["close"])
            self._tracker.on_bar(
                float(row["high"]),
                float(row["low"]),
                close_,
                i,
            )

            wf_result = self._tracker.check_wf(close_, i, cfg=cfg)
            if wf_result is not None:
                if wf_result.get("status") == "ext_skipped":
                    ext_skipped = WfWavePrepResult(
                        ext_skipped=True,
                        eval_result=wf_result,
                    )
                elif wf_result.get("status") == "activate":
                    act = self._build_activation(
                        df, cfg, waves, wf_result, i,
                    )
                    self._activation_results.append(act)
                    if activation is None:
                        activation = act

            for w in waves_by_bar.get(i, []):
                if not bool(w.get("post_ext_trend_suppressed", False)):
                    self._tracker.on_new_wave(w, birth_bar=i, df=df)

            self._last_processed_bar_time = bar_time

        self._df_anchor_time = _bar_ts(df, 0)
        if activation is not None:
            return activation
        if ext_skipped is not None:
            return ext_skipped
        return WfWavePrepResult()
