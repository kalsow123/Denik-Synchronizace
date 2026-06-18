"""
Live base-PnL tracker.

Tracks a "PnL základní" curve using each trade's configured risk
(cfg.risk_usd or cfg.pp_risk_usd via trade_risk_usd()).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
import csv
import json
from typing import Sequence


@dataclass
class PnLPoint:
    time: str
    pnl_usd: float
    cumulative_pnl_usd: float
    source_risk_usd: Optional[float]
    monitor_risk_usd: float
    note: str = ""


@dataclass
class PnLTrackerState:
    cumulative_pnl_usd: float = 0.0
    equity_high_usd: float = 0.0
    previous_high_usd: float = 0.0
    points: list[PnLPoint] = field(default_factory=list)


def scale_trade_pnl_to_monitor(
    *,
    pnl_usd: float | None = None,
    pnl_r: float | None = None,
    source_risk_usd: float | None = None,
    monitor_risk_usd: float = 500.0,
) -> float:
    """Přepočet uzavřeného obchodu na PnL základní (monitor_risk_usd)."""
    if pnl_r is not None:
        return float(pnl_r) * float(monitor_risk_usd)
    if pnl_usd is not None and source_risk_usd and source_risk_usd > 0:
        return float(pnl_usd) * float(monitor_risk_usd) / float(source_risk_usd)
    if pnl_usd is not None:
        return float(pnl_usd)
    raise ValueError("Provide either pnl_r or pnl_usd.")


class BasePnLTracker:
    def __init__(
        self,
        state_path: str | Path = "runtime/pnl_base_tracker_state.json",
        jsonl_path: str | Path = "runtime/pnl_base_tracker.jsonl",
        csv_path: str | Path = "runtime/pnl_base_curve.csv",
    ):
        self.state_path = Path(state_path)
        self.jsonl_path = Path(jsonl_path)
        self.csv_path = Path(csv_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        if self.state_path.exists():
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            raw.pop("monitor_risk_usd", None)
            raw["points"] = [PnLPoint(**p) for p in raw.get("points", [])]
            self.state = PnLTrackerState(**raw)
        else:
            self.state = PnLTrackerState()
            self._save()

    def record_closed_trade(
        self,
        close_time: str | datetime,
        pnl_usd: Optional[float] = None,
        source_risk_usd: Optional[float] = None,
        pnl_r: Optional[float] = None,
        note: str = "",
    ) -> PnLPoint:
        """Record a closed trade and scale it to monitor_risk_usd.

        Preferred: pass pnl_r if the engine can provide R result.
        Alternative: pass pnl_usd and source_risk_usd; the module scales:
          monitor_pnl = pnl_usd * monitor_risk_usd / source_risk_usd
        Fallback: if source_risk_usd is missing, pnl_usd is used as-is.
        """
        if isinstance(close_time, datetime):
            t = close_time.isoformat(sep=" ")
        else:
            t = str(close_time)

        if not source_risk_usd or float(source_risk_usd) <= 0:
            raise ValueError("source_risk_usd must be a positive trade risk (risk_usd / pp_risk_usd).")

        monitor_risk_usd = float(source_risk_usd)
        monitor_pnl = scale_trade_pnl_to_monitor(
            pnl_usd=pnl_usd,
            pnl_r=pnl_r,
            source_risk_usd=monitor_risk_usd,
            monitor_risk_usd=monitor_risk_usd,
        )

        self.state.cumulative_pnl_usd += monitor_pnl
        if self.state.cumulative_pnl_usd > self.state.equity_high_usd:
            self.state.previous_high_usd = self.state.equity_high_usd
            self.state.equity_high_usd = self.state.cumulative_pnl_usd

        point = PnLPoint(
            time=t,
            pnl_usd=round(monitor_pnl, 6),
            cumulative_pnl_usd=round(self.state.cumulative_pnl_usd, 6),
            source_risk_usd=source_risk_usd,
            monitor_risk_usd=monitor_risk_usd,
            note=note,
        )
        self.state.points.append(point)
        self._append_jsonl("PNL_BASE_UPDATE", asdict(point))
        self._append_csv(point)
        self._save()
        return point

    def current_equity(self) -> float:
        return self.state.cumulative_pnl_usd

    def current_high(self) -> float:
        return self.state.equity_high_usd

    def crossed_previous_high(self, previous_high: Optional[float] = None) -> bool:
        level = self.state.previous_high_usd if previous_high is None else previous_high
        return self.state.cumulative_pnl_usd > level

    def points_dataframe(self):
        """CSV-compatible křivka: time, cumulative_pnl_usd (PnL základní)."""
        import pandas as pd

        if not self.state.points:
            return pd.DataFrame(columns=["time", "cumulative_pnl_usd", "pnl_usd", "note"])
        rows = []
        for p in self.state.points:
            rows.append(
                {
                    "time": pd.to_datetime(p.time),
                    "cumulative_pnl_usd": float(p.cumulative_pnl_usd),
                    "pnl_usd": float(p.pnl_usd),
                    "note": p.note,
                }
            )
        return pd.DataFrame(rows)

    @staticmethod
    def build_curve_from_closed_trades(closed_trades: Sequence, *, cfg) -> "pd.DataFrame":
        """Replay PnL základní z uzavřených obchodů (bez zápisu na disk)."""
        import pandas as pd

        from config.bot_config import trade_risk_usd

        rows = []
        cumulative = 0.0
        high = 0.0
        for t in sorted(closed_trades, key=lambda x: x.close_time):
            is_pp = bool(getattr(t, "is_pp", False))
            risk = float(
                getattr(t, "risk_usd", None) or trade_risk_usd(cfg, is_pp=is_pp)
            )
            monitor_pnl = scale_trade_pnl_to_monitor(
                pnl_usd=float(getattr(t, "pnl_usd", 0.0)),
                source_risk_usd=risk,
                monitor_risk_usd=risk,
            )
            cumulative += monitor_pnl
            if cumulative > high:
                high = cumulative
            rows.append(
                {
                    "time": pd.Timestamp(t.close_time),
                    "cumulative_pnl_usd": round(cumulative, 6),
                    "pnl_usd": round(monitor_pnl, 6),
                    "equity_high_usd": round(high, 6),
                    "note": str(getattr(t, "entry_type", "") or ""),
                }
            )
        if not rows:
            return pd.DataFrame(
                columns=["time", "cumulative_pnl_usd", "pnl_usd", "equity_high_usd", "note"]
            )
        return pd.DataFrame(rows)

    def _append_jsonl(self, event: str, payload: dict) -> None:
        record = {"event": event, "time": datetime.utcnow().isoformat(), **payload}
        with self.jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _append_csv(self, point: PnLPoint) -> None:
        new_file = not self.csv_path.exists()
        with self.csv_path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(asdict(point).keys()))
            if new_file:
                writer.writeheader()
            writer.writerow(asdict(point))

    def _save(self) -> None:
        data = asdict(self.state)
        self.state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Append one closed trade to the base PnL tracker.")
    parser.add_argument("--time", required=True)
    parser.add_argument("--pnl-usd", type=float)
    parser.add_argument("--source-risk-usd", type=float)
    parser.add_argument("--pnl-r", type=float)
    parser.add_argument("--monitor-risk-usd", type=float)
    parser.add_argument("--state", default="runtime/pnl_base_tracker_state.json")
    args = parser.parse_args()

    source_risk = args.source_risk_usd or args.monitor_risk_usd
    if not source_risk:
        parser.error("Provide --source-risk-usd or --monitor-risk-usd.")

    tracker = BasePnLTracker(state_path=args.state)
    point = tracker.record_closed_trade(
        close_time=args.time,
        pnl_usd=args.pnl_usd,
        source_risk_usd=source_risk,
        pnl_r=args.pnl_r,
    )
    print(json.dumps(asdict(point), ensure_ascii=False))
