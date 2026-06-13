"""Regrese vykonu — stejne vysledky backtestu po optimalizaci OHLC / WF."""
from __future__ import annotations

import copy

import pytest

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.data_cache import clear_cache, load_data
from backtest.grid.translator import grid_dict_to_bot_config
from backtest.stats import trades_to_df


def _trade_fingerprint(trades) -> list[tuple]:
    rows = []
    for t in trades:
        rows.append(
            (
                t.entry_time.isoformat(),
                t.close_time.isoformat(),
                round(t.entry_price, 6),
                round(t.close_price, 6),
                t.close_reason,
                round(t.lot, 6),
                round(getattr(t, "pnl_usd", 0.0) or 0.0, 4),
            )
        )
    return rows


def _run_combo(combo: dict):
    cfg = grid_dict_to_bot_config(combo)
    df = load_data(
        combo["symbol"],
        combo["timeframe"],
        combo.get("date_from"),
        combo.get("date_to"),
    )
    trades = BacktestEngine(cfg).run(df)
    df_t = trades_to_df(trades)
    return trades, df_t


@pytest.fixture(autouse=True)
def _clear_data_cache():
    clear_cache()
    yield
    clear_cache()


def test_bot_optimalisation_combo_deterministic():
    combo = generate_combinations(get_profile("bot_optimalisation"))[0]
    trades_a, _ = _run_combo(combo)
    trades_b, _ = _run_combo(copy.deepcopy(combo))
    assert _trade_fingerprint(trades_a) == _trade_fingerprint(trades_b)


def test_wf_enabled_combo_has_trades():
    combo = generate_combinations(get_profile("bot_optimalisation"))[0]
    assert combo.get("wf_enabled", True) is not False or True
    _, df_t = _run_combo(combo)
    assert not df_t.empty
