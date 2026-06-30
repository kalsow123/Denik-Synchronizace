"""Regrese cache vln + trend_bos — profil EXAMPLE, vsechny kombinace."""
from __future__ import annotations

import hashlib
import json

import pytest

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.data_cache import clear_cache, load_data
from backtest.grid.translator import grid_dict_to_bot_config
from backtest.stats import compute_stats, trades_to_df
from backtest.wave_sim_cache import clear_pine_sim_cache, set_pine_sim_cache_enabled


def _combo_fingerprint(combo: dict) -> str:
    cfg = grid_dict_to_bot_config(combo)
    df = load_data(
        combo["symbol"],
        combo["timeframe"],
        combo.get("date_from"),
        combo.get("date_to"),
    )
    trades = BacktestEngine(cfg).run(df)
    df_t = trades_to_df(trades)
    stats = compute_stats(df_t, track_concurrent=True)
    trade_rows = []
    if not df_t.empty:
        for _, r in df_t.sort_values(["entry_time", "close_time"]).iterrows():
            trade_rows.append(
                {
                    "entry_time": str(r["entry_time"]),
                    "close_time": str(r["close_time"]),
                    "entry_price": round(float(r["entry_price"]), 8),
                    "close_price": round(float(r["close_price"]), 8),
                    "sl": round(float(r["sl"]), 8),
                    "lot": round(float(r["lot"]), 8),
                    "pnl_usd": round(float(r["pnl_usd"]), 4),
                    "close_reason": str(r["close_reason"]),
                }
            )
    payload = {
        "trades": trade_rows,
        "total_trades": stats.get("total_trades"),
        "net_pnl_usd": round(float(stats.get("net_pnl_usd", 0)), 4),
        "win_rate_pct": round(float(stats.get("win_rate_pct", 0)), 6),
        "max_drawdown_pct": round(float(stats.get("max_drawdown_pct", 0)), 6),
        "waves_accepted": stats.get("waves_accepted"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@pytest.fixture(autouse=True)
def _reset_caches():
    clear_cache()
    clear_pine_sim_cache()
    yield
    clear_cache()
    clear_pine_sim_cache()


def test_example_all_combos_match_with_and_without_pine_cache():
    combos = generate_combinations(get_profile("EXAMPLE"))
    # EXAMPLE byl zamerne prepsan (commit f56800d) z 24-kombinacniho gridu na
    # 4 explicitni VARIAC10 kombinace (combo_no 50, 53, 280, 207). Cache parita
    # se overuje na techto 4 realnych deployment kombinacich.
    assert len(combos) == 4

    set_pine_sim_cache_enabled(False)
    baseline = {i: _combo_fingerprint(c) for i, c in enumerate(combos)}

    clear_pine_sim_cache()
    set_pine_sim_cache_enabled(True)
    cached = {i: _combo_fingerprint(c) for i, c in enumerate(combos)}

    assert baseline == cached
