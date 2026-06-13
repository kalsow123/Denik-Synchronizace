"""Ztrátová období na PnL křivce — drawdown od running peak (USD)."""
import numpy as np
import pandas as pd

from backtest.plotting import (
    EQUITY_LOSS_BG_MIN_USD,
    _add_grid_drawdown_overlay_traces,
    _drawdown_usd_from_peak,
    _find_drawdown_episodes,
    _grid_drawdown_label_color,
    _grid_table_font_color_matrix,
    _usd_loss_from_series,
)


def test_drawdown_from_peak_not_from_initial():
    equity = np.array([10_000.0, 100_000.0, 85_000.0])
    dd = _drawdown_usd_from_peak(equity)
    assert dd.tolist() == [0, 0, 15_000]
    loss_equity_mode = _usd_loss_from_series(equity, y_mode="equity", initial_balance=10_000)
    assert loss_equity_mode.tolist() == [0, 0, 15_000]


def test_cum_pnl_drawdown():
    cum = np.array([0, 50_000, 35_000])
    dd = _usd_loss_from_series(cum, y_mode="cum_pnl", initial_balance=10_000)
    assert dd.tolist() == [0, 0, 15_000]


def test_find_drawdown_episodes_sums_negative_pnl():
    times = pd.date_range("2024-01-01", periods=5, freq="D")
    y = np.array([0, 100, 50, -20, 80])
    pnl = np.array([0, 100, -50, -70, 100])
    eps = _find_drawdown_episodes(times, y, pnl_values=pnl, min_loss_usd=1)
    assert len(eps) == 1
    assert eps[0]["loss_usd"] == 120.0


def test_min_loss_threshold():
    times = pd.date_range("2024-01-01", periods=4, freq="D")
    y = np.array([0, 10, 8, 12])
    pnl = np.array([0, 10, -2, 4])
    eps = _find_drawdown_episodes(times, y, pnl_values=pnl, min_loss_usd=100)
    assert eps == []


def test_threshold_constant():
    assert EQUITY_LOSS_BG_MIN_USD == 50.0


def test_grid_drawdown_overlay_uses_peak_to_trough_depth_only():
    from plotly.subplots import make_subplots

    times = pd.date_range("2024-01-01", periods=6, freq="D")
    equity = np.array([0.0, 100.0, 60.0, 80.0, 30.0, 95.0])
    pnl = np.array([0.0, 100.0, -40.0, 20.0, -50.0, 65.0])
    fig = make_subplots(rows=1, cols=1)

    episodes = _add_grid_drawdown_overlay_traces(
        fig,
        series_name="combo",
        color="#636efa",
        times=times,
        y_values=equity,
        pnl_values=None,
        y_min=float(equity.min()),
        y_max=float(equity.max()),
        row=1,
        col=1,
    )

    assert len(episodes) == 1
    assert episodes[0]["loss_usd"] == 70.0
    assert len(fig.data) == 1

    eps_with_trade_sum = _find_drawdown_episodes(times, equity, pnl_values=pnl, min_loss_usd=1)
    assert eps_with_trade_sum[0]["loss_usd"] == 90.0


def test_grid_drawdown_label_color_threshold():
    assert _grid_drawdown_label_color(9_999.0) == "#000000"
    assert _grid_drawdown_label_color(10_000.0) == "#d62728"


def test_grid_table_font_color_matrix_repeats_row_colors_across_columns():
    tbl = pd.DataFrame({"combo_no": ["1", "2"], "RRR_TP": ["bos", "rrr"]})

    colors = _grid_table_font_color_matrix(
        tbl,
        row_bot_names=["bot_a", "bot_b"],
        series_color_by_name={"bot_a": "#111111", "bot_b": "#222222"},
    )

    assert colors == [["#111111", "#222222"], ["#111111", "#222222"]]
