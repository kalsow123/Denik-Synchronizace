"""Pytest testy pro backtest.metrics.robustness."""
from __future__ import annotations

import math
from datetime import datetime, timedelta

import pandas as pd
import pytest

from backtest.metrics.robustness import (
    MIN_TRADES_FOR_METRICS,
    calculate_cagr,
    calculate_calmar,
    calculate_longest_loss_streak,
    calculate_profitable_months_pct,
    calculate_sortino,
    compute_robustness_metrics,
)


def _make_trades(
    pnls: list[float],
    start: datetime | None = None,
    step_days: int = 3,
) -> pd.DataFrame:
    start = start or datetime(2024, 1, 5)
    rows = []
    for i, pnl in enumerate(pnls):
        t = start + timedelta(days=i * step_days)
        rows.append(
            {
                "close_time": t,
                "pnl_usd": pnl,
                "close_reason": "TP",
            }
        )
    df = pd.DataFrame(rows)
    df["cumulative_pnl"] = df["pnl_usd"].cumsum()
    return df


@pytest.fixture
def profitable_trades() -> pd.DataFrame:
    """Ziskové obchody — jeden obchod v každém kalendářním měsíci."""
    rows = []
    for m in range(1, 13):
        rows.append(
            {
                "close_time": datetime(2024, m, 15),
                "pnl_usd": 500.0,
                "close_reason": "TP",
            }
        )
    df = pd.DataFrame(rows)
    df["cumulative_pnl"] = df["pnl_usd"].cumsum()
    return df


@pytest.fixture
def losing_trades() -> pd.DataFrame:
    """Samé ztráty."""
    return _make_trades([-100.0] * 15, start=datetime(2024, 1, 1), step_days=20)


@pytest.fixture
def mixed_trades() -> pd.DataFrame:
    """Střídání zisku a ztráty."""
    pnls = [200.0, -150.0] * 8
    return _make_trades(pnls, start=datetime(2024, 2, 1), step_days=15)


class TestCalculateCagr:
    def test_profitable_positive_cagr(self, profitable_trades):
        eq = profitable_trades[["close_time", "cumulative_pnl"]]
        cagr = calculate_cagr(eq, 100_000.0)
        assert not math.isnan(cagr)
        assert cagr > 0

    def test_very_short_period_returns_nan(self):
        eq = pd.DataFrame(
            {
                "close_time": [datetime(2024, 1, 1), datetime(2024, 1, 2)],
                "cumulative_pnl": [100.0, 200.0],
            }
        )
        assert math.isnan(calculate_cagr(eq, 10_000.0))

    def test_empty_returns_nan(self):
        assert math.isnan(calculate_cagr(pd.DataFrame(), 10_000.0))


class TestCalculateCalmar:
    def test_basic_ratio(self):
        assert calculate_calmar(20.0, -10.0) == pytest.approx(2.0)

    def test_zero_dd_positive_cagr_returns_cap(self):
        assert calculate_calmar(10.0, 0.0) == pytest.approx(999.0)

    def test_zero_dd_non_positive_cagr_is_nan(self):
        assert math.isnan(calculate_calmar(-5.0, 0.0))


class TestCalculateSortino:
    def test_mixed_has_finite_sortino(self, mixed_trades):
        s = calculate_sortino(mixed_trades)
        assert not math.isnan(s)

    def test_all_wins_downside_zero_returns_cap(self, profitable_trades):
        """Čistě kladné měsíce → cap (dříve NaN)."""
        s = calculate_sortino(profitable_trades)
        assert s == pytest.approx(99.0)

    def test_zero_dd_calmar_cap(self):
        assert calculate_calmar(15.0, 0.0) == pytest.approx(999.0)


class TestCalculateProfitableMonthsPct:
    def test_all_winning_months(self, profitable_trades):
        pct = calculate_profitable_months_pct(profitable_trades)
        assert pct == pytest.approx(100.0)

    def test_losing_mostly_negative(self, losing_trades):
        pct = calculate_profitable_months_pct(losing_trades)
        assert pct < 50.0

    def test_mixed_between_bounds(self, mixed_trades):
        pct = calculate_profitable_months_pct(mixed_trades)
        assert 0.0 <= pct <= 100.0


class TestCalculateLongestLossStreak:
    def test_losing_streak_count(self, losing_trades):
        out = calculate_longest_loss_streak(losing_trades)
        assert out["trades"] == 15.0
        assert out["days"] >= 0

    def test_mixed_has_some_streak(self, mixed_trades):
        out = calculate_longest_loss_streak(mixed_trades)
        assert out["trades"] >= 1.0


class TestComputeRobustnessMetrics:
    def test_too_few_trades_all_nan(self):
        df = _make_trades([10.0] * (MIN_TRADES_FOR_METRICS - 1))
        row = compute_robustness_metrics(df, bot_name="tiny")
        assert all(math.isnan(row[k]) for k in row)

    def test_profitable_returns_rounded_keys(self, profitable_trades):
        row = compute_robustness_metrics(
            profitable_trades,
            max_dd_pct_vs_peak=-5.0,
            bot_name="win",
        )
        assert set(row.keys()) == {
            "cagr_pct",
            "calmar",
            "sortino",
            "profitable_months_pct",
            "longest_loss_streak_trades",
            "longest_loss_streak_days",
        }
        for k, v in row.items():
            if not math.isnan(v):
                assert abs(v * 1000 - round(v * 1000)) < 1e-6

    def test_losing_negative_cagr(self, losing_trades):
        row = compute_robustness_metrics(
            losing_trades,
            max_dd_pct_vs_peak=-15.0,
            bot_name="lose",
        )
        assert row["cagr_pct"] < 0 or math.isnan(row["cagr_pct"])
