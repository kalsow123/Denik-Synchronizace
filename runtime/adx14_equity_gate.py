"""
ADX14 + base-PnL equity gate.

Logic requested:
1. If ADX14 normalized signal reaches 1.3, disable new bot entries.
2. Re-enable if either:
   A) N calendar months pass since disable (weekends included), OR
   B) optional (bos_confirm_enabled): base-PnL exceeds the level at disable and stays
      above it for M calendar weeks (noise filter), then trading resumes.

This module writes JSONL events so the live bot can audit every state change.
No third-party packages are required.
"""

from __future__ import annotations

import calendar
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


def add_calendar_days(dt: datetime, days: int) -> datetime:
    """Přidá kalendářní dny (víkendy se počítají)."""
    return dt + timedelta(days=int(days))


def add_calendar_months(dt: datetime, months: int) -> datetime:
    """Přidá kalendářní měsíce (sobota/neděle se počítají do délky pauzy)."""
    if months == 0:
        return dt
    y = dt.year
    m = dt.month + months
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    last_day = calendar.monthrange(y, m)[1]
    day = min(dt.day, last_day)
    return dt.replace(year=y, month=m, day=day, hour=dt.hour, minute=dt.minute, second=dt.second, microsecond=dt.microsecond)


@dataclass
class GateConfig:
    adx14_disable_threshold: float = 1.3
    auto_restart_calendar_months: int = 2
    bos_confirm_enabled: bool = True
    bos_confirm_calendar_weeks: int = 2
    state_path: str = "runtime/adx14_equity_gate_state.json"
    jsonl_path: str = "runtime/adx14_equity_gate.jsonl"


@dataclass
class GateState:
    enabled: bool = True
    disabled_since: Optional[str] = None
    disabled_reason: Optional[str] = None
    restart_equity_high_usd: Optional[float] = None
    new_high_candidate_since: Optional[str] = None
    last_adx14_signal: Optional[float] = None
    last_equity_usd: Optional[float] = None


@dataclass
class GateDecision:
    enabled: bool
    action: str
    reason: str
    state: GateState


class ADX14EquityGate:
    def __init__(self, config: GateConfig = GateConfig()):
        self.config = config
        self.state_path = Path(config.state_path)
        self.jsonl_path = Path(config.jsonl_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        if self.state_path.exists():
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            raw.setdefault("new_high_candidate_since", None)
            self.state = GateState(**raw)
        else:
            self.state = GateState()
            self._save()

    def update(
        self,
        now: datetime,
        adx14_signal: Optional[float],
        base_equity_usd: float,
        current_equity_high_usd: float,
    ) -> GateDecision:
        self.state.last_adx14_signal = adx14_signal
        self.state.last_equity_usd = base_equity_usd

        if self.state.enabled:
            if adx14_signal is not None and adx14_signal >= self.config.adx14_disable_threshold:
                self.state.enabled = False
                self.state.disabled_since = now.isoformat()
                self.state.disabled_reason = "ADX14_SIGNAL_THRESHOLD"
                # Watermark = PnL základní v okamžiku vypnutí (ne historické maximum).
                self.state.restart_equity_high_usd = base_equity_usd
                self.state.new_high_candidate_since = None
                self._event(
                    "GATE_DISABLED_ADX14",
                    now,
                    {
                        "adx14_signal": adx14_signal,
                        "threshold": self.config.adx14_disable_threshold,
                        "base_equity_usd": base_equity_usd,
                        "restart_equity_high_usd": base_equity_usd,
                        "equity_high_at_disable": current_equity_high_usd,
                    },
                )
                self._save()
                return GateDecision(False, "DISABLE", "ADX14 signal >= threshold", self.state)
            self._save()
            return GateDecision(True, "KEEP_ENABLED", "No disable condition", self.state)

        # Gate is disabled: check restart conditions.
        disabled_since = datetime.fromisoformat(self.state.disabled_since) if self.state.disabled_since else now
        auto_restart_at = add_calendar_months(
            disabled_since, self.config.auto_restart_calendar_months
        )
        if now >= auto_restart_at:
            months = self.config.auto_restart_calendar_months
            self._enable(now, "AUTO_RESTART_AFTER_CALENDAR_MONTHS", base_equity_usd, adx14_signal)
            return GateDecision(
                True,
                "ENABLE",
                f"Auto restart after {months} calendar month(s)",
                self.state,
            )

        restart_high = self.state.restart_equity_high_usd
        if self.config.bos_confirm_enabled and restart_high is not None:
            if base_equity_usd > restart_high:
                if self.state.new_high_candidate_since is None:
                    self.state.new_high_candidate_since = now.isoformat()
                    self._event(
                        "GATE_NEW_HIGH_CANDIDATE",
                        now,
                        {
                            "base_equity_usd": base_equity_usd,
                            "restart_equity_high_usd": restart_high,
                            "confirm_weeks": self.config.bos_confirm_calendar_weeks,
                        },
                    )
                else:
                    since = datetime.fromisoformat(self.state.new_high_candidate_since)
                    confirm_at = add_calendar_days(
                        since, self.config.bos_confirm_calendar_weeks * 7
                    )
                    if now >= confirm_at:
                        weeks = self.config.bos_confirm_calendar_weeks
                        self._enable(
                            now,
                            "EQUITY_NEW_HIGH_CONFIRMED",
                            base_equity_usd,
                            adx14_signal,
                        )
                        return GateDecision(
                            True,
                            "ENABLE",
                            f"Base PnL new high confirmed after {weeks} calendar week(s)",
                            self.state,
                        )
            elif self.state.new_high_candidate_since is not None:
                self.state.new_high_candidate_since = None
                self._event(
                    "GATE_NEW_HIGH_CANDIDATE_RESET",
                    now,
                    {
                        "base_equity_usd": base_equity_usd,
                        "restart_equity_high_usd": restart_high,
                    },
                )

        self._save()
        wait_reason = (
            "Waiting for calendar months or confirmed new high"
            if self.config.bos_confirm_enabled
            else "Waiting for calendar months (BOS confirm disabled)"
        )
        return GateDecision(False, "KEEP_DISABLED", wait_reason, self.state)

    def allow_new_entries(self) -> bool:
        return self.state.enabled

    def _enable(self, now: datetime, reason: str, base_equity_usd: float, adx14_signal: Optional[float]) -> None:
        self.state.enabled = True
        self.state.disabled_since = None
        self.state.disabled_reason = None
        self.state.restart_equity_high_usd = None
        self.state.new_high_candidate_since = None
        self._event(
            "GATE_ENABLED",
            now,
            {"reason": reason, "base_equity_usd": base_equity_usd, "adx14_signal": adx14_signal},
        )
        self._save()

    def _event(self, event: str, now: datetime, payload: dict) -> None:
        record = {"event": event, "time": now.isoformat(), **payload}
        with self.jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _save(self) -> None:
        self.state_path.write_text(json.dumps(asdict(self.state), ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Update ADX14 equity gate.")
    parser.add_argument("--adx14-signal", type=float, required=True)
    parser.add_argument("--base-equity-usd", type=float, required=True)
    parser.add_argument("--equity-high-usd", type=float, required=True)
    parser.add_argument("--state", default="runtime/adx14_equity_gate_state.json")
    args = parser.parse_args()

    gate = ADX14EquityGate(GateConfig(state_path=args.state))
    decision = gate.update(
        now=datetime.utcnow(),
        adx14_signal=args.adx14_signal,
        base_equity_usd=args.base_equity_usd,
        current_equity_high_usd=args.equity_high_usd,
    )
    print(json.dumps(asdict(decision), ensure_ascii=False))
