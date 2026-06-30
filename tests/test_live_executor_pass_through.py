"""2E — LiveExecutor pass-through (WAVE place_pending → infra.send_order).

Overuje, ze `LiveExecutor.place_pending` predava parametry orderu na
`infra.orders.send_order` BEZ jakekoli zmeny hodnot (ep/sl/lot/wave_time).
guard + dedup jsou mockovane na "povol"; send_order je mock (zadny realny MT5).
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from backtest.engine import PendingOrder
from config.bot_config import LIVE_BOT_CONFIG
from runtime.live_executor import LiveExecutor


def _wave_signal():
    return {
        "wave_time": "202601020930",
        "fib50": 1.23456,   # ep
        "sl": 1.22000,
        "dir": 1,
        "lot": 0.37,
        "move_pct": 1.5,
    }


def _wave_pending(signal):
    return PendingOrder(
        signal=signal,
        order_type="BUY_LIMIT",
        entry_price=float(signal["fib50"]),
        sl=float(signal["sl"]),
        tp=None,
        lot=float(signal["lot"]),
        created_bar=10,
        created_time=datetime(2026, 1, 2, 9, 30),
    )


def test_place_pending_wave_passes_values_unchanged(monkeypatch):
    sent = MagicMock(return_value=True)
    monkeypatch.setattr("infra.orders.send_order", sent)
    # guard vrati False = NEblokovat; dedup False = neni duplikat.
    monkeypatch.setattr(
        "runtime.live_wave_isolation.guard_live_send_order",
        lambda *a, **k: False,
    )
    monkeypatch.setattr(
        "infra.live_order_guard.block_duplicate_wave_order",
        lambda *a, **k: False,
    )

    signal = _wave_signal()
    order = _wave_pending(signal)
    ex = LiveExecutor(LIVE_BOT_CONFIG)

    ex.place_pending(order, bar_idx=10, bar_time=datetime(2026, 1, 2, 9, 30))

    assert sent.call_count == 1
    args, kwargs = sent.call_args
    passed_signal = args[0]
    # Pass-through: tentyz signal objekt, beze zmeny hodnot.
    assert passed_signal is signal
    assert passed_signal["fib50"] == 1.23456      # ep
    assert passed_signal["sl"] == 1.22000
    assert passed_signal["lot"] == 0.37
    assert passed_signal["wave_time"] == "202601020930"  # → comment "W..."
    assert kwargs.get("is_two_sided_mirror") is False


def test_place_pending_noop_when_apply_orders_false(monkeypatch):
    sent = MagicMock(return_value=True)
    monkeypatch.setattr("infra.orders.send_order", sent)
    monkeypatch.setattr(
        "runtime.live_wave_isolation.guard_live_send_order",
        lambda *a, **k: False,
    )

    signal = _wave_signal()
    order = _wave_pending(signal)
    ex = LiveExecutor(LIVE_BOT_CONFIG, apply_orders=False)

    ex.place_pending(order, bar_idx=10, bar_time=datetime(2026, 1, 2, 9, 30))

    sent.assert_not_called()
