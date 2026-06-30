"""2E — LiveExecutor respektuje live guard PRED odeslanim.

`runtime.live_wave_isolation.guard_live_send_order` ma kontrakt True = BLOKOVAT
(viz jeho docstring). `LiveExecutor.place_pending` se pri True hned vrati a
`infra.orders.send_order` se NEVOLA. Pri False (povoleno) + zadny duplikat se
send_order zavola. Vse mockovane — zadny realny MT5.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from backtest.engine import PendingOrder
from config.bot_config import LIVE_BOT_CONFIG
from runtime.live_executor import LiveExecutor


def _wave_order():
    signal = {
        "wave_time": "202601020930",
        "fib50": 1.23456,
        "sl": 1.22000,
        "dir": 1,
        "lot": 0.37,
    }
    order = PendingOrder(
        signal=signal,
        order_type="BUY_LIMIT",
        entry_price=1.23456,
        sl=1.22000,
        tp=None,
        lot=0.37,
        created_bar=10,
        created_time=datetime(2026, 1, 2, 9, 30),
    )
    return order


def test_send_order_not_called_when_guard_blocks(monkeypatch):
    sent = MagicMock(return_value=True)
    monkeypatch.setattr("infra.orders.send_order", sent)
    # Guard BLOKUJE (True) → executor se vrati pred odeslanim.
    monkeypatch.setattr(
        "runtime.live_wave_isolation.guard_live_send_order",
        lambda *a, **k: True,
    )

    ex = LiveExecutor(LIVE_BOT_CONFIG)
    ex.place_pending(_wave_order(), bar_idx=10, bar_time=datetime(2026, 1, 2, 9, 30))

    sent.assert_not_called()


def test_send_order_called_when_guard_allows(monkeypatch):
    sent = MagicMock(return_value=True)
    monkeypatch.setattr("infra.orders.send_order", sent)
    # Guard POVOLI (False) a neni duplikat → send_order se zavola.
    monkeypatch.setattr(
        "runtime.live_wave_isolation.guard_live_send_order",
        lambda *a, **k: False,
    )
    monkeypatch.setattr(
        "infra.live_order_guard.block_duplicate_wave_order",
        lambda *a, **k: False,
    )

    ex = LiveExecutor(LIVE_BOT_CONFIG)
    ex.place_pending(_wave_order(), bar_idx=10, bar_time=datetime(2026, 1, 2, 9, 30))

    sent.assert_called_once()
