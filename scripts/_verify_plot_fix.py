"""Rychlá kontrola po opravě plotu."""
from scripts._verify_all_combos_html_xlsx import (
    RUN_DIR,
    INIT,
    combo_from_report_row,
    peak_trough_usd,
)

import pandas as pd
from backtest.engine import BacktestEngine
from backtest.grid.data_cache import load_data
from backtest.grid.study_mode import filter_trades_df_for_grid_stats
from backtest.grid.translator import grid_backtest_position_cap_settings, grid_dict_to_bot_config
from backtest.plotting import _find_drawdown_episodes
from backtest.sim_params import sim_params_from_grid_combo
from backtest.stats import trades_to_df

df_rep = pd.read_csv(RUN_DIR / "grid_report.csv", sep=";")
for cn in df_rep["combo_no"]:
    row = df_rep[df_rep.combo_no == cn].iloc[0]
    combo = combo_from_report_row(row)
    cfg = grid_dict_to_bot_config(combo)
    cap_mode, cap_limit = grid_backtest_position_cap_settings(combo)
    spr, slip, _ = sim_params_from_grid_combo(combo)
    ohlc = load_data(
        symbol="EURUSD",
        timeframe_label="M30",
        date_from=combo["date_from"],
        date_to=combo["date_to"],
    )
    tdf = trades_to_df(
        BacktestEngine(
            cfg,
            backtest_position_cap_mode=cap_mode,
            backtest_max_open_positions=cap_limit,
            backtest_spread=spr,
            backtest_slippage=slip,
        ).run(ohlc)
    )
    tdf = filter_trades_df_for_grid_stats(tdf, combo)
    _, pct = peak_trough_usd(tdf, INIT)
    eq = INIT + tdf["pnl_usd"].astype(float).cumsum()
    eps = _find_drawdown_episodes(
        tdf["close_time"], eq.values, pnl_values=tdf["pnl_usd"].values
    )
    label = max((e["loss_usd"] for e in eps), default=0.0)
    xlsx = float(str(row["max_dd_%_vs_initial"]).replace(",", "."))
    ok = abs(abs(pct) - abs(xlsx)) < 0.1 and abs(label - abs(float(str(row["max_dd_usd"]).replace(",", ".")))) < 50
    print(
        f"combo {int(cn):2d} iso={bool(combo['wave_isolation_study'])} "
        f"xlsx={xlsx:6.2f}% plot={pct:6.2f}% label={label:8.0f} OK={ok}"
    )
