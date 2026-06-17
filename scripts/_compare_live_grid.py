"""Compare LIVE_BOT_CONFIG vs grid EXAMPLE combo 2."""
from __future__ import annotations

from dataclasses import fields

from backtest.data_loader import filter_by_date_range, load_csv
from backtest.engine import BacktestEngine
from backtest.grid.data_cache import csv_path_for
from backtest.grid.study_mode import filter_trades_df_for_grid_stats
from backtest.grid.translator import grid_dict_to_bot_config
from backtest.run_backtest import _prep_grid_combos_for_paths
from backtest.stats import trades_to_df
from config.bot_config import LIVE_BOT_CONFIG
from config.position_modes import apply_wave_positions_only_to_bot_config


def main() -> None:
    combos = _prep_grid_combos_for_paths("EXAMPLE", "2025-11-10", "2026-05-09")
    c2 = combos[1]
    print("combo2:", c2.get("wave_2_no_tp_enable"), c2.get("tp_target_wave_index"), c2.get("pending_cancel_mode"))

    live_cfg = apply_wave_positions_only_to_bot_config(LIVE_BOT_CONFIG)
    grid_cfg = grid_dict_to_bot_config(c2)

    diffs = []
    for f in fields(live_cfg):
        lv = getattr(live_cfg, f.name)
        gv = getattr(grid_cfg, f.name)
        if str(lv) != str(gv):
            diffs.append((f.name, lv, gv))
    print("BotConfig diffs:", len(diffs))
    for name, lv, gv in diffs:
        print(f"  {name}: LIVE={lv!r} GRID={gv!r}")

    df = filter_by_date_range(load_csv(csv_path_for("EURUSD", "M30")), "2025-11-10", "2026-05-09")
    print("bars:", len(df), "last:", df["time"].iloc[-1])

    results = {}
    for label, cfg in [("LIVE", live_cfg), ("GRID", grid_cfg)]:
        eng = BacktestEngine(cfg, backtest_spread=0.0001, backtest_slippage=0.0)
        tr = eng.run(df)
        tdf = trades_to_df(tr)
        w = filter_trades_df_for_grid_stats(tdf, c2)
        results[label] = w
        print(label, "filtered:", len(w), "pnl:", round(w["pnl_usd"].sum(), 2))

    lw = results["LIVE"].copy()
    gw = results["GRID"].copy()
    key = [k for k in ["entry_time", "direction", "entry_price", "sl", "tp"] if k in lw.columns]
    for c in ["entry_time", "wave_time", "exit_time"]:
        if c in lw.columns:
            lw[c] = lw[c].astype(str)
            gw[c] = gw[c].astype(str)

    only_live = lw.merge(gw, on=key, how="left", indicator=True)
    only_live = only_live[only_live["_merge"] == "left_only"]
    only_grid = gw.merge(lw, on=key, how="left", indicator=True)
    only_grid = only_grid[only_grid["_merge"] == "left_only"]
    print("LIVE-only trades:", len(only_live))
    print("GRID-only trades:", len(only_grid))

    cols = [c for c in ["entry_time", "direction", "entry_price", "pnl_usd", "close_reason", "position_kind"] if c in lw.columns]
    if len(only_live):
        print("\n--- LIVE only ---")
        print(only_live[cols].head(15).to_string())
    if len(only_grid):
        print("\n--- GRID only ---")
        print(only_grid[cols].head(15).to_string())

    # PnL diff on matched trades
    merged = lw.merge(gw, on=key, suffixes=("_live", "_grid"))
    if "pnl_usd_live" in merged.columns:
        merged["pnl_diff"] = merged["pnl_usd_live"] - merged["pnl_usd_grid"]
        big = merged[merged["pnl_diff"].abs() > 0.01].sort_values("pnl_diff", key=abs, ascending=False)
        print("\nMatched trades with PnL diff:", len(big))
        if len(big):
            show = [c for c in ["entry_time", "direction", "pnl_usd_live", "pnl_usd_grid", "pnl_diff", "close_reason_live", "close_reason_grid"] if c in big.columns]
            print(big[show].head(20).to_string())


if __name__ == "__main__":
    main()
