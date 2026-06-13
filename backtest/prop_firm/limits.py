"""Datový model limitů prop-firm účtu."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class PropFirmLimits:
    name: str = "Custom"
    account_size_usd: float = 100_000.0
    # Součet SL rizik všech otevřených pozic v jeden okamžik (None = limit se nevyhodnocuje).
    max_risk_per_moment_pct: Optional[float] = None
    # Max SL riziko jedné otevřené pozice (None = limit se nevyhodnocuje).
    max_risk_single_position_pct: Optional[float] = None
    max_daily_dd_pct: float = 5.0
    max_overall_dd_pct: float = 10.0
    daily_dd_basis: str = "static_initial"  # static_initial | eod_balance
    profit_target_pct: Optional[float] = None
    min_trading_days: Optional[int] = None

    def __post_init__(self) -> None:
        if self.daily_dd_basis not in ("static_initial", "eod_balance"):
            raise ValueError(
                f"daily_dd_basis musí být 'static_initial' nebo 'eod_balance', máte {self.daily_dd_basis!r}"
            )

    def with_account_size(self, account_size_usd: float) -> "PropFirmLimits":
        from dataclasses import replace

        return replace(self, account_size_usd=float(account_size_usd))
