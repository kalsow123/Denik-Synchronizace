"""
Empirický sweep tp_target_wave_index × wave_extension_pct.

Pro každou kombinaci:
  1) TP timing audit (BRZY/VCAS/POZDE, MFE ratio, G extension hit)
  2) Plný backtest (PnL, PF, DD) pro wave_target_n a wave_target_n_g

Usage:
  python scripts/audit/audit_tp_param_sweep.py
  python scripts/audit/audit_tp_param_sweep.py --tp-n 2 4 6 --ext-pct 0.10 0.15 0.20
"""
from __future__ import annotations

import argparse
import sys
from itertools import product
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.data_loader import filter_by_date_range, load_csv
from backtest.engine import BacktestEngine
from backtest.grid.translator import grid_dict_to_bot_config
from backtest.stats import compute_stats, trades_to_df
from scripts.audit.audit_tp_timing_by_trend import analyze_tp_cycles


def _example_grid_dict(
    *,
    tp_target_wave_index: int,
    wave_extension_pct: float,
    tp_mode: str = "wave_target_n",
) -> dict:
    return {
        "timeframe": "M30",
        "wave_min_pct": 0.26,
        "min_opp_bars": 3,
        "rrr": 2.0,
        "fib_level": 0.5,
        "entry_mode": "market_fallback",
        "symbol": "EURUSD.x",
        "sl_fib_level": 0.8,
        "abort_fib_level": "shift_sl",
        "wave_plus": True,
        "order_expiry_days": 3,
        "pending_cancel_mode": "number",
        "wave_max_pct": 1.0,
        "max_wave_age_hours": 12,
        "risk_usd": 500.0,
        "contract_size": 100_000.0,
        "trend_filter_enabled": True,
        "trend_hh_hl_filter_enabled": True,
        "tp_target_wave_index": tp_target_wave_index,
        "wave_extension_pct": wave_extension_pct,
        "wave_size_sl_ladder_base_pct": 0.21,
        "wave_size_sl_ladder_step_pct": 0.16,
        "wave_size_sl_ladder_band_size_pct": 0.50,
        "wave_position_enabled": True,
        "counter_position_enabled": True,
        "ext_enabled": True,
        "wf_enabled": True,
        "wave_2_no_tp_enable": True,
        "wave_2_no_tp_max_index": 2,
        "tp_mode": tp_mode,
        "spread": 0.0001,
        "slippage": 0.0,
        "track_concurrent_positions": True,
    }


def _bucket_pct(cycles: pd.DataFrame, col: str, bucket: str) -> float:
    if cycles.empty:
        return 0.0
    n = int((cycles[col] == bucket).sum())
    return round(n / len(cycles) * 100.0, 1)


def _timing_row(
    df: pd.DataFrame,
    cfg,
    *,
    tp_n: int,
    ext_pct: float,
) -> dict:
    cycles = analyze_tp_cycles(df, cfg)
    n = len(cycles)
    leg = cycles["wave_target_n_bucket"] if n else pd.Series(dtype=str)
    gcol = cycles["wave_target_n_g_bucket"] if n else pd.Series(dtype=str)

    leg_mfe = (
        cycles["wave_target_n_mfe_ratio"].median()
        if n and cycles["wave_target_n_mfe_ratio"].notna().any()
        else None
    )
    g_mfe = (
        cycles["wave_target_n_g_mfe_ratio"].median()
        if n and cycles["wave_target_n_g_mfe_ratio"].notna().any()
        else None
    )
    ext_hit = (
        int((cycles["wave_target_n_g_exit_path"] == "TP_EXTENSION_HIT").sum())
        if n
        else 0
    )
    return {
        "tp_target_wave_index": tp_n,
        "wave_extension_pct": ext_pct,
        "tp_cycles": n,
        "wtn_brzy_pct": _bucket_pct(cycles, "wave_target_n_bucket", "BRZY"),
        "wtn_vcas_pct": _bucket_pct(cycles, "wave_target_n_bucket", "VCAS"),
        "wtn_pozde_pct": _bucket_pct(cycles, "wave_target_n_bucket", "POZDE"),
        "wtn_neutral_pct": _bucket_pct(cycles, "wave_target_n_bucket", "NEUTRAL"),
        "wtn_mfe_ratio_median": round(float(leg_mfe), 4) if leg_mfe is not None else None,
        "wtng_brzy_pct": _bucket_pct(cycles, "wave_target_n_g_bucket", "BRZY"),
        "wtng_vcas_pct": _bucket_pct(cycles, "wave_target_n_g_bucket", "VCAS"),
        "wtng_pozde_pct": _bucket_pct(cycles, "wave_target_n_g_bucket", "POZDE"),
        "wtng_neutral_pct": _bucket_pct(cycles, "wave_target_n_g_bucket", "NEUTRAL"),
        "wtng_mfe_ratio_median": round(float(g_mfe), 4) if g_mfe is not None else None,
        "wtng_extension_hit_pct": round(ext_hit / n * 100.0, 1) if n else 0.0,
        "wtng_extension_hit_n": ext_hit,
    }


def _run_backtest(
    df: pd.DataFrame,
    *,
    tp_n: int,
    ext_pct: float,
    tp_mode: str,
) -> dict:
    combo = _example_grid_dict(
        tp_target_wave_index=tp_n,
        wave_extension_pct=ext_pct,
        tp_mode=tp_mode,
    )
    cfg = grid_dict_to_bot_config(combo)
    engine = BacktestEngine(
        cfg,
        backtest_position_cap_mode="off",
        backtest_max_open_positions=None,
        backtest_spread=float(combo["spread"]),
        backtest_slippage=float(combo["slippage"]),
    )
    trades = engine.run(df)
    stats = compute_stats(
        trades_to_df(trades),
        track_concurrent=bool(combo["track_concurrent_positions"]),
    )
    return {
        "tp_target_wave_index": tp_n,
        "wave_extension_pct": ext_pct,
        "tp_mode": tp_mode,
        "trades": stats.get("trades", 0),
        "net_pnl_usd": stats.get("net_pnl_usd"),
        "profit_factor": stats.get("profit_factor"),
        "win_rate_pct": stats.get("win_rate_pct"),
        "max_drawdown_pct": stats.get("max_drawdown_pct"),
        "max_drawdown_pct_vs_peak": stats.get("max_drawdown_pct_vs_peak"),
        "net_pnl_wave_usd": stats.get("net_pnl_wave_usd"),
        "net_pnl_wave_counter_usd": stats.get("net_pnl_wave_counter_usd"),
        "error": stats.get("error"),
    }


def _timing_score(row: dict, *, mode: str) -> float:
    """Nižší = lepší timing (méně BRZY, nižší MFE po exitu)."""
    prefix = "wtn_" if mode == "wave_target_n" else "wtng_"
    brzy = float(row[f"{prefix}brzy_pct"])
    mfe = row[f"{prefix}mfe_ratio_median"]
    mfe_val = float(mfe) if mfe is not None else 0.0
    return brzy + mfe_val * 100.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Sweep TP parametrů (timing + backtest)")
    parser.add_argument("--csv", type=Path, default=ROOT / "data" / "EURUSD.x_M30.csv")
    parser.add_argument("--date-from", default="2026-03-03")
    parser.add_argument("--date-to", default="2026-05-10")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "results" / "audit_tp_param_sweep",
    )
    parser.add_argument("--tp-n", type=int, nargs="+", default=[2, 4, 6])
    parser.add_argument("--ext-pct", type=float, nargs="+", default=[0.10, 0.15, 0.20])
    parser.add_argument(
        "--skip-backtest",
        action="store_true",
        help="Jen timing audit (rychlejsi)",
    )
    args = parser.parse_args()

    df = load_csv(args.csv)
    df = filter_by_date_range(df, args.date_from, args.date_to)

    out_dir = args.output
    out_dir.mkdir(parents=True, exist_ok=True)

    timing_rows: list[dict] = []
    for tp_n, ext_pct in product(args.tp_n, args.ext_pct):
        cfg = grid_dict_to_bot_config(
            _example_grid_dict(
                tp_target_wave_index=tp_n,
                wave_extension_pct=ext_pct,
            )
        )
        timing_rows.append(_timing_row(df, cfg, tp_n=tp_n, ext_pct=ext_pct))
        print(
            f"timing N={tp_n} ext={ext_pct:.2f}  "
            f"cycles={timing_rows[-1]['tp_cycles']}  "
            f"WTN BRZY={timing_rows[-1]['wtn_brzy_pct']}%  "
            f"WTNg BRZY={timing_rows[-1]['wtng_brzy_pct']}%"
        )

    timing_df = pd.DataFrame(timing_rows)
    timing_df["timing_score_wtn"] = timing_df.apply(
        lambda r: _timing_score(r, mode="wave_target_n"), axis=1
    )
    timing_df["timing_score_wtng"] = timing_df.apply(
        lambda r: _timing_score(r, mode="wave_target_n_g"), axis=1
    )
    timing_df = timing_df.sort_values(["timing_score_wtn", "tp_target_wave_index"])
    timing_df.to_csv(out_dir / "timing_sweep.csv", index=False)

    bt_df = pd.DataFrame()
    if not args.skip_backtest:
        bt_rows: list[dict] = []
        for tp_n, ext_pct in product(args.tp_n, args.ext_pct):
            for tp_mode in ("wave_target_n", "wave_target_n_g"):
                row = _run_backtest(
                    df, tp_n=tp_n, ext_pct=ext_pct, tp_mode=tp_mode
                )
                bt_rows.append(row)
                print(
                    f"backtest {tp_mode} N={tp_n} ext={ext_pct:.2f}  "
                    f"PnL={row.get('net_pnl_usd')}  trades={row.get('trades')}"
                )
        bt_df = pd.DataFrame(bt_rows)
        bt_df.to_csv(out_dir / "backtest_sweep.csv", index=False)

        merged = timing_df.merge(
            bt_df,
            on=["tp_target_wave_index", "wave_extension_pct"],
            how="left",
        )
        merged.to_csv(out_dir / "combined_sweep.csv", index=False)

    print()
    print("=== Timing — nejlepší wave_target_n (nejnižší score = méně BRZY) ===")
    top_wtn = timing_df.nsmallest(3, "timing_score_wtn")
    for _, r in top_wtn.iterrows():
        print(
            f"  N={int(r['tp_target_wave_index'])} ext={r['wave_extension_pct']:.2f}  "
            f"BRZY={r['wtn_brzy_pct']}%  MFE_med={r['wtn_mfe_ratio_median']}  "
            f"cycles={int(r['tp_cycles'])}"
        )

    print()
    print("=== Timing — nejlepší wave_target_n_g ===")
    top_wtng = timing_df.nsmallest(3, "timing_score_wtng")
    for _, r in top_wtng.iterrows():
        print(
            f"  N={int(r['tp_target_wave_index'])} ext={r['wave_extension_pct']:.2f}  "
            f"BRZY={r['wtng_brzy_pct']}%  ext_hit={r['wtng_extension_hit_pct']}%  "
            f"MFE_med={r['wtng_mfe_ratio_median']}"
        )

    if not bt_df.empty:
        print()
        for tp_mode in ("wave_target_n", "wave_target_n_g"):
            sub = bt_df[bt_df["tp_mode"] == tp_mode].sort_values(
                "net_pnl_usd", ascending=False
            )
            print(f"=== Backtest PnL — top 3 {tp_mode} ===")
            for _, r in sub.head(3).iterrows():
                print(
                    f"  N={int(r['tp_target_wave_index'])} ext={r['wave_extension_pct']:.2f}  "
                    f"PnL={r['net_pnl_usd']}  PF={r['profit_factor']}  "
                    f"DD={r['max_drawdown_pct']}%  trades={int(r['trades'])}"
                )

    meta = {
        "csv": str(args.csv),
        "date_from": args.date_from,
        "date_to": args.date_to,
        "tp_n_values": args.tp_n,
        "ext_pct_values": args.ext_pct,
        "best_timing_wtn": top_wtn.iloc[0][
            ["tp_target_wave_index", "wave_extension_pct", "wtn_brzy_pct"]
        ].to_dict(),
        "best_timing_wtng": top_wtng.iloc[0][
            ["tp_target_wave_index", "wave_extension_pct", "wtng_brzy_pct"]
        ].to_dict(),
    }
    if not bt_df.empty:
        for tp_mode in ("wave_target_n", "wave_target_n_g"):
            best = bt_df[bt_df["tp_mode"] == tp_mode].sort_values(
                "net_pnl_usd", ascending=False
            ).iloc[0]
            meta[f"best_pnl_{tp_mode}"] = {
                "tp_target_wave_index": int(best["tp_target_wave_index"]),
                "wave_extension_pct": float(best["wave_extension_pct"]),
                "net_pnl_usd": best["net_pnl_usd"],
            }

    pd.Series(meta).to_json(out_dir / "summary.json", force_ascii=False, indent=2)
    print()
    print(f"Vystup: {out_dir}")


if __name__ == "__main__":
    main()
