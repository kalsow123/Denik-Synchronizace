"""Live wiring: ADX14 signal, equity gate, base PnL tracker."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from config.bot_config import BotConfig
from core.logging_utils import log_event
from runtime.adx14_equity_gate import ADX14EquityGate, GateConfig
from runtime.pnl_base_equity_tracker import BasePnLTracker
from strategy.adx14_change_indicator import (
    ADX14Point,
    compute_adx14_points,
    load_normalizer,
    write_adx14_html,
)

if TYPE_CHECKING:
    import pandas as pd

log = logging.getLogger(__name__)


def dataframe_to_bars(df: "pd.DataFrame") -> list[dict]:
    if df is None or df.empty:
        return []
    bars: list[dict] = []
    for _, row in df.iterrows():
        t = row["time"]
        if hasattr(t, "to_pydatetime"):
            t = t.to_pydatetime()
        vol = row.get("tick_volume", row.get("volume", 0.0))
        bars.append(
            {
                "datetime": t,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(vol or 0.0),
            }
        )
    return bars


class Adx14LiveRuntime:
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.pnl_tracker: Optional[BasePnLTracker] = None
        self.gate: Optional[ADX14EquityGate] = None
        self.normalizer = None
        self.latest: Optional[ADX14Point] = None
        self.entries_allowed = True
        self._last_html_day: Optional[str] = None

        if cfg.pnl_base_tracker_enabled or cfg.adx14_equity_gate_enabled:
            self.pnl_tracker = BasePnLTracker(
                state_path=cfg.pnl_base_tracker_state_path,
                jsonl_path=cfg.pnl_base_tracker_jsonl_path,
                csv_path=cfg.pnl_base_tracker_csv_path,
                monitor_risk_usd=cfg.pnl_base_tracker_risk_usd,
            )

        if cfg.adx14_equity_gate_enabled:
            self.gate = ADX14EquityGate(
                GateConfig(
                    adx14_disable_threshold=cfg.adx14_disable_threshold,
                    auto_restart_calendar_months=cfg.adx14_auto_restart_calendar_months,
                    bos_confirm_enabled=cfg.adx14_bos_confirm_enabled,
                    bos_confirm_calendar_weeks=cfg.adx14_bos_confirm_calendar_weeks,
                    state_path=cfg.adx14_gate_state_path,
                    jsonl_path=cfg.adx14_gate_jsonl_path,
                )
            )

        if cfg.adx14_change_enabled or cfg.adx14_equity_gate_enabled:
            norm_path = Path(cfg.adx14_change_normalizer_json)
            if norm_path.exists():
                self.normalizer = load_normalizer(norm_path)
            else:
                log.warning("ADX14 normalizer JSON nenalezen: %s", norm_path)

    @property
    def active(self) -> bool:
        return (
            self.pnl_tracker is not None
            or self.gate is not None
            or self.normalizer is not None
        )

    def needs_history_bars(self) -> bool:
        return bool(self.cfg.adx14_change_enabled or self.cfg.adx14_equity_gate_enabled)

    def update(self, df: "pd.DataFrame", now: datetime) -> None:
        if not self.needs_history_bars():
            return
        if self.normalizer is None:
            self.entries_allowed = not self.cfg.adx14_equity_gate_enabled
            return

        points = compute_adx14_points(dataframe_to_bars(df), normalizer=self.normalizer)
        valid = [p for p in points if p.adx14_signal is not None]
        if not valid:
            return

        self.latest = valid[-1]

        if self.cfg.adx14_change_enabled:
            log_event(
                self.cfg,
                "info",
                "ADX14_CHANGE_UPDATE",
                adx14=self.latest.adx14,
                adx14_change_pct=self.latest.adx14_change_pct,
                adx14_signal=self.latest.adx14_signal,
            )
            if self.latest.day != self._last_html_day:
                self._last_html_day = self.latest.day
                html_path = Path(self.cfg.adx14_change_html_path)
                html_path.parent.mkdir(parents=True, exist_ok=True)
                write_adx14_html(points, html_path)

        self._update_gate(now)
        self._log_gate_status()

    def on_position_closed(
        self,
        *,
        close_time: str,
        pnl_usd: float,
        source_risk_usd: float,
        note: str,
        now: datetime,
    ) -> None:
        if self.pnl_tracker:
            self.pnl_tracker.record_closed_trade(
                close_time=close_time,
                pnl_usd=pnl_usd,
                source_risk_usd=source_risk_usd,
                note=note,
            )
        if self.gate:
            self._update_gate(now)
            self._log_gate_status()

    def _update_gate(self, now: datetime) -> None:
        if not self.gate:
            return
        signal = self.latest.adx14_signal if self.latest else None
        base_eq = self.pnl_tracker.current_equity() if self.pnl_tracker else 0.0
        high = self.pnl_tracker.current_high() if self.pnl_tracker else 0.0
        self.gate.update(
            now=now,
            adx14_signal=signal,
            base_equity_usd=base_eq,
            current_equity_high_usd=high,
        )
        if self.cfg.adx14_equity_gate_enabled:
            self.entries_allowed = self.gate.allow_new_entries()

    def _log_gate_status(self) -> None:
        if not self.gate:
            return
        st = self.gate.state
        log_event(
            self.cfg,
            "info",
            "ADX14_GATE_STATUS",
            enabled=bool(st.enabled),
            adx14_signal=st.last_adx14_signal,
            base_equity_usd=st.last_equity_usd,
            restart_equity_high_usd=st.restart_equity_high_usd,
            disabled_since=st.disabled_since,
        )
