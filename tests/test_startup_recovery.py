"""Startup / session recovery — pine simulace vs MT5 pending/pozice."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from config.bot_config import BotConfig
from runtime.startup import _last_closed_bar_close


def test_last_closed_bar_close_uses_penultimate_row():
    df = pd.DataFrame({"close": [1.0, 1.1, 1.2]})
    assert _last_closed_bar_close(df) == 1.1


def test_pine_recovery_marks_simulated_open_without_resending_pending():
    """Otevřené simulované obchody → signal_key v recovered, send_startup se nevolá."""
    cfg = BotConfig()
    pending = [
        {
            "dir": 1,
            "fib50": 1.10,
            "sl": 1.09,
            "tp": 1.12,
            "wave_time": "202601011000",
            "move_pct": 0.3,
        },
    ]
    open_trades = [
        {
            "dir": -1,
            "fib50": 1.11,
            "sl": 1.12,
            "tp": 1.09,
            "wave_time": "202601011030",
            "entry_bar": 5,
        },
    ]
    df = pd.DataFrame(
        {
            "time": pd.date_range("2026-01-01", periods=10, freq="30min"),
            "open": [1.0] * 10,
            "high": [1.0] * 10,
            "low": [1.0] * 10,
            "close": [1.0] * 10,
        },
    )

    with patch("runtime.startup.get_bars", return_value=df), patch(
        "runtime.startup.simulate_pine_pending_state",
        return_value=(pending, open_trades),
    ), patch(
        "runtime.startup.is_older_than_business_days",
        return_value=False,
    ), patch(
        "runtime.startup.is_wave_in_allowed_session",
        return_value=True,
    ), patch(
        "runtime.startup.is_wave_too_large",
        return_value=False,
    ), patch(
        "runtime.startup.get_active_wave_times",
        return_value=set(),
    ), patch(
        "runtime.startup.get_position_wave_times",
        return_value=set(),
    ), patch(
        "runtime.startup.mt5.symbol_info",
        return_value=SimpleNamespace(digits=5),
    ), patch(
        "runtime.startup.send_startup_pending_only",
        return_value=True,
    ) as mock_send, patch(
        "runtime.startup.log_event",
    ), patch(
        "runtime.startup.deduplicate_magic_pendings",
        return_value=0,
    ):
        from runtime.startup import restore_pine_style_pending_orders

        keys = restore_pine_style_pending_orders(cfg)

    assert mock_send.call_count == 1
    call_wt = mock_send.call_args[0][0]["wave_time"]
    assert call_wt == "202601011000"
    assert mock_send.call_args[1]["pine_recovery"] is True
    assert len(keys) == 2


def test_deduplicate_keeps_oldest_ticket():
    cfg = BotConfig(magic=100001)
    o1 = SimpleNamespace(
        magic=100001, comment="W202601011000", ticket=100,
        type=2, price_open=1.1, sl=1.09, tp=1.12, volume_current=0.1,
    )
    o2 = SimpleNamespace(
        magic=100001, comment="W202601011000", ticket=200,
        type=2, price_open=1.1, sl=1.09, tp=1.12, volume_current=0.1,
    )

    class _Res:
        retcode = 0

    with patch("infra.live_order_guard.mt5.orders_get", return_value=[o1, o2]), patch(
        "infra.live_order_guard.mt5.order_send",
        return_value=_Res(),
    ) as mock_cancel, patch(
        "infra.live_order_guard.mt5.TRADE_RETCODE_DONE", 0,
    ), patch(
        "infra.live_order_guard.mt5.TRADE_ACTION_REMOVE", 1,
    ), patch(
        "infra.live_order_guard.log_event",
    ):
        from infra.live_order_guard import deduplicate_magic_pendings

        n = deduplicate_magic_pendings(cfg)

    assert n == 1
    assert mock_cancel.call_args[0][0]["order"] == 200
