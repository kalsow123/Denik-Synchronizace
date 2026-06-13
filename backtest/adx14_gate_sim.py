"""ADX14 equity gate simulation for backtest (mirrors live Adx14LiveRuntime)."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from config.bot_config import BotConfig
from runtime.adx14_equity_gate import ADX14EquityGate, GateConfig
from runtime.adx14_live import dataframe_to_bars
from runtime.pnl_base_equity_tracker import BasePnLTracker
from strategy.adx14_change_indicator import ADX14Point, compute_adx14_points, load_normalizer


class Adx14BacktestSim:
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.active = bool(
            cfg.adx14_change_enabled or cfg.adx14_equity_gate_enabled
        )
        self.daily_points: list[ADX14Point] = []
        self.bar_times: list[datetime] = []
        self.bar_signals: list[Optional[float]] = []
        self.timeline: list[dict] = []
        self._tmpdir: Optional[str] = None
        self.pnl_tracker: Optional[BasePnLTracker] = None
        self.gate: Optional[ADX14EquityGate] = None
        self._bar_ix = 0

        if not self.active:
            return

        self._tmpdir = tempfile.mkdtemp(prefix="adx14_bt_")
        tmp = self._tmpdir

        need_base_pnl = bool(
            cfg.pnl_base_tracker_enabled or cfg.adx14_equity_gate_enabled
        )
        if need_base_pnl:
            self.pnl_tracker = BasePnLTracker(
                state_path=os.path.join(tmp, "pnl_state.json"),
                jsonl_path=os.path.join(tmp, "pnl.jsonl"),
                csv_path=os.path.join(tmp, "pnl.csv"),
                monitor_risk_usd=cfg.pnl_base_tracker_risk_usd,
            )

        if cfg.adx14_equity_gate_enabled:
            self.gate = ADX14EquityGate(
                GateConfig(
                    adx14_disable_threshold=cfg.adx14_disable_threshold,
                    auto_restart_calendar_months=cfg.adx14_auto_restart_calendar_months,
                    bos_confirm_enabled=cfg.adx14_bos_confirm_enabled,
                    bos_confirm_calendar_weeks=cfg.adx14_bos_confirm_calendar_weeks,
                    state_path=os.path.join(tmp, "gate_state.json"),
                    jsonl_path=os.path.join(tmp, "gate.jsonl"),
                )
            )

    def prepare(self, df: pd.DataFrame) -> None:
        if not self.active:
            return

        norm_path = Path(self.cfg.adx14_change_normalizer_json)
        if not norm_path.exists():
            self.active = False
            return

        normalizer = load_normalizer(norm_path)
        self.daily_points = compute_adx14_points(
            dataframe_to_bars(df), normalizer=normalizer
        )
        signal_by_day: dict[str, float] = {}
        for p in self.daily_points:
            if p.adx14_signal is not None:
                signal_by_day[p.day] = float(p.adx14_signal)

        self.bar_times = []
        self.bar_signals = []
        last_sig: Optional[float] = None
        for _, row in df.iterrows():
            t = pd.Timestamp(row["time"]).to_pydatetime()
            if hasattr(t, "tzinfo") and t.tzinfo is not None:
                t = t.replace(tzinfo=None)
            d = t.date().isoformat()
            if d in signal_by_day:
                last_sig = signal_by_day[d]
            self.bar_times.append(t)
            self.bar_signals.append(last_sig)

        self.timeline = []
        self._bar_ix = 0

    def on_bar(self, bar_ix: int, bar_time: datetime) -> None:
        if not self.active:
            return
        self._bar_ix = bar_ix
        signal = (
            self.bar_signals[bar_ix]
            if bar_ix < len(self.bar_signals)
            else None
        )
        if self.gate:
            self._update_gate(bar_time)
        enabled = self.allow_new_entries()
        self.timeline.append(
            {
                "time": bar_time,
                "adx14_signal": signal,
                "gate_enabled": enabled,
                "base_equity_usd": (
                    self.pnl_tracker.current_equity() if self.pnl_tracker else None
                ),
            }
        )

    def allow_new_entries(self) -> bool:
        if not self.cfg.adx14_equity_gate_enabled:
            return True
        if self.gate is None:
            return True
        return self.gate.allow_new_entries()

    def on_trade_closed(
        self,
        *,
        pnl_usd: float,
        close_time: datetime,
        source_risk_usd: float,
        note: str = "",
    ) -> None:
        if not self.active:
            return
        if self.pnl_tracker:
            self.pnl_tracker.record_closed_trade(
                close_time=close_time,
                pnl_usd=float(pnl_usd),
                source_risk_usd=float(source_risk_usd),
                note=note,
            )
        self._update_gate(close_time)

    def _update_gate(self, now: datetime) -> None:
        if not self.gate:
            return
        signal = (
            self.bar_signals[self._bar_ix]
            if self._bar_ix < len(self.bar_signals)
            else None
        )
        base_eq = self.pnl_tracker.current_equity() if self.pnl_tracker else 0.0
        high = self.pnl_tracker.current_high() if self.pnl_tracker else 0.0
        self.gate.update(
            now=now,
            adx14_signal=signal,
            base_equity_usd=base_eq,
            current_equity_high_usd=high,
        )

    def base_pnl_dataframe(self) -> pd.DataFrame:
        if self.pnl_tracker:
            return self.pnl_tracker.points_dataframe()
        return pd.DataFrame(columns=["time", "cumulative_pnl_usd"])

    def daily_plot_dataframe(self) -> pd.DataFrame:
        rows = []
        for p in self.daily_points:
            if p.adx14_signal is None:
                continue
            rows.append(
                {
                    "time": pd.to_datetime(p.day),
                    "adx14": p.adx14,
                    "adx14_change_pct": p.adx14_change_pct,
                    "adx14_signal": p.adx14_signal,
                }
            )
        return pd.DataFrame(rows) if rows else pd.DataFrame()

    def gate_disabled_periods(self) -> list[tuple[datetime, datetime]]:
        if not self.timeline:
            return []
        out: list[tuple[datetime, datetime]] = []
        start: Optional[datetime] = None
        for row in self.timeline:
            t = row["time"]
            enabled = bool(row.get("gate_enabled", True))
            if not enabled and start is None:
                start = t
            elif enabled and start is not None:
                out.append((start, t))
                start = None
        if start is not None and self.timeline:
            out.append((start, self.timeline[-1]["time"]))
        return out
