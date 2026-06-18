"""
Simulace PnL variant live vs backtest — stejné období jako live_match grid 021/022.
Spuštění: python scripts/_simulate_live_variants_pnl.py
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd

from backtest.data_loader import filter_by_date_range, load_csv
from backtest.engine import BacktestEngine
from backtest.grid.study_mode import (
    apply_wave_isolation_report_stats,
    filter_trades_df_for_grid_stats,
)
from backtest.stats import compute_stats, trades_to_df
from config.bot_config import LIVE_BOT_CONFIG
from config.position_modes import resolve_grid_engine_config

DATE_FROM = "2025-11-10"
DATE_TO = "2026-05-09"
CSV = Path("data/EURUSD_M30.csv")


def load_df() -> pd.DataFrame:
    df = load_csv(str(CSV))
    return filter_by_date_range(df, DATE_FROM, DATE_TO)


def pnl_by_kind(trades_df: pd.DataFrame) -> dict[str, float]:
    if trades_df.empty or "position_kind" not in trades_df.columns:
        return {}
    return {
        str(k): float(g["pnl_usd"].sum())
        for k, g in trades_df.groupby("position_kind")
    }


def run_variant(label: str, cfg_raw, *, combo_study: bool | None) -> dict:
    engine_cfg = resolve_grid_engine_config(cfg_raw)
    engine = BacktestEngine(engine_cfg)
    trades = engine.run(load_df(), retain_wave_snapshot=False)
    trades_df = trades_to_df(trades)

    combo = {
        "wave_isolation_study": bool(combo_study) if combo_study is not None else False,
        "wave_positions_only": bool(cfg_raw.wave_positions_only),
        "date_from": DATE_FROM,
        "date_to": DATE_TO,
    }

    stats_full = compute_stats(trades_df, date_from=DATE_FROM, date_to=DATE_TO)
    kinds_full = pnl_by_kind(trades_df)

    if combo_study:
        df_wave = filter_trades_df_for_grid_stats(trades_df, combo)
        stats_report = compute_stats(df_wave, date_from=DATE_FROM, date_to=DATE_TO)
        stats_report = apply_wave_isolation_report_stats(stats_report, combo)
    else:
        df_wave = trades_df
        stats_report = stats_full

    return {
        "label": label,
        "study": combo_study,
        "engine_counter": engine_cfg.wave_counter_two_sided_enabled,
        "engine_ext": engine_cfg.ext_enabled,
        "pp_enabled": engine_cfg.pp_enabled,
        "trades_full": int(len(trades_df)),
        "net_pnl_full_usd": round(float(stats_full.get("net_pnl_usd", 0) or 0), 2),
        "trades_report": int(stats_report.get("total_trades", 0) or 0),
        "net_pnl_report_usd": round(float(stats_report.get("net_pnl_usd", 0) or 0), 2),
        "trades_wave": int(stats_full.get("trades_wave", 0) or 0),
        "net_pnl_wave_usd": round(float(stats_full.get("net_pnl_wave_usd", 0) or 0), 2),
        "trades_wave_counter": int(stats_full.get("trades_wave_counter", 0) or 0),
        "net_pnl_wave_counter_usd": round(
            float(stats_full.get("net_pnl_wave_counter_usd", 0) or 0), 2
        ),
        "trades_ext_bos": int(stats_full.get("trades_ext_bos", 0) or 0),
        "net_pnl_ext_bos_usd": round(float(stats_full.get("net_pnl_ext_bos_usd", 0) or 0), 2),
        "max_dd_pct": float(stats_report.get("max_drawdown_pct", 0) or 0),
        "kinds_pnl": kinds_full,
    }


def main() -> None:
    base = LIVE_BOT_CONFIG
    combo2 = replace(
        base,
        wave_positions_only=True,
        wave_isolation_study=True,
        pp_enabled=False,
    )
    variants = [
        (
            "D — study=False (live wave_only, jako grid 022)",
            replace(base, wave_isolation_study=False, pp_enabled=False),
            False,
        ),
        (
            "A — study=True, report/xlsx WAVE slice (jako grid 021)",
            combo2,
            True,
        ),
        (
            "A+ — study=True, celý engine PnL na účtu (MT5 wave_slice)",
            combo2,
            None,
        ),
        (
            "B — study=True, jen WAVE PnL z plné simulace (= report 021)",
            combo2,
            True,
        ),
        (
            "D+ — aktuální registry (study=False, pp=True)",
            base,
            False,
        ),
    ]

    rows = []
    for label, cfg, study in variants:
        key = study
        if study is None:
            r = run_variant(label, cfg, combo_study=False)
            r["label"] = label
            r["net_pnl_report_usd"] = r["net_pnl_full_usd"]
            r["trades_report"] = r["trades_full"]
            rows.append(r)
            continue
        rows.append(run_variant(label, cfg, combo_study=study))

    print("=" * 72)
    print(f"Období: {DATE_FROM} .. {DATE_TO}  CSV: {CSV}")
    print("=" * 72)
    for r in rows:
        print(f"\n{r['label']}")
        print(f"  engine: counter={r['engine_counter']} ext={r['engine_ext']} pp={r['pp_enabled']}")
        print(f"  FULL sim (všechny uzavřené obchody v engine):")
        print(f"    trades={r['trades_full']}  net_pnl_usd={r['net_pnl_full_usd']}")
        print(f"    WAVE: {r['trades_wave']} / {r['net_pnl_wave_usd']} USD")
        print(f"    WAVE_COUNTER: {r['trades_wave_counter']} / {r['net_pnl_wave_counter_usd']} USD")
        print(f"    EXT_BOS: {r['trades_ext_bos']} / {r['net_pnl_ext_bos_usd']} USD")
        if r["kinds_pnl"]:
            print(f"    detail: {r['kinds_pnl']}")
        print(f"  REPORT / live wave_pnl view:")
        print(f"    trades={r['trades_report']}  net_pnl_usd={r['net_pnl_report_usd']}")
        print(f"    max_dd_pct={r['max_dd_pct']}")

    ref = pd.read_excel(
        "results/EURUSD/grid_LIVE_BOT_M30_2025-11-10_2026-05-09_021/grid_report.xlsx"
    ).iloc[0]
    ref2 = pd.read_excel(
        "results/EURUSD/grid_LIVE_BOT_M30_2025-11-10_2026-05-09_022/grid_report.xlsx"
    ).iloc[0]
    print("\n" + "=" * 72)
    print("REFERENCE grid_report.xlsx")
    print(f"  021 study=True:  trades={int(ref['trades'])} pnl={float(ref['net_pnl_usd']):.2f}")
    print(f"  022 study=False: trades={int(ref2['trades'])} pnl={float(ref2['net_pnl_usd']):.2f}")


if __name__ == "__main__":
    main()
