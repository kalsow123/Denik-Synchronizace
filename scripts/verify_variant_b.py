"""
Ověření varianty B: backtest WAVE slice ~36k + live MT5 jen WAVE.
Spuštění: python scripts/verify_variant_b.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.data_loader import filter_by_date_range, load_csv
from backtest.engine import BacktestEngine
from backtest.grid.study_mode import (
    apply_wave_isolation_report_stats,
    filter_trades_df_for_grid_stats,
)
from backtest.metrics.robustness import compute_robustness_metrics
from backtest.stats import compute_stats, trades_to_df
from config.bot_config import LIVE_BOT_CONFIG
from config.position_modes import resolve_grid_engine_config
from runtime.live_wave_isolation import (
    classify_live_execution_mode,
    guard_live_send_order,
    resolve_live_execution_config,
    skip_live_non_wave_entry,
)

DATE_FROM = "2025-11-10"
DATE_TO = "2026-05-09"
CSV = ROOT / "data" / "EURUSD_M30.csv"

# Očekávané hodnoty z předchozího backtestu (LIVE_BOT_CONFIG, study=True).
EXPECTED = {
    "net_pnl_usd": 36023.37,
    "trades_wave": 141,
    "max_dd_pct": -6.25,
    "max_ddi_pct": -5.67,
    "median_ddi_pct": -1.00,
    "p90_ddi_pct": -4.17,
}
TOL_PNL = 1.0
TOL_DD = 0.15


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def verify_live_mt5_guards() -> None:
    print("--- LIVE MT5 guards (varianta B) ---")
    live = resolve_live_execution_config(LIVE_BOT_CONFIG)
    engine = resolve_grid_engine_config(LIVE_BOT_CONFIG)

    if classify_live_execution_mode(live) != "wave_study_wave_only":
        _fail(f"mode={classify_live_execution_mode(live)}")

    if not (
        live.counter_position_enabled
        and live.wave_counter_two_sided_enabled
        and live.ext_enabled
    ):
        _fail("engine routing vypnuty — counter/EXT musi byt ON")

    for kind in ("COUNTER", "EXT_COUNTER", "TWO_SIDED", "PP", "BOS"):
        if not skip_live_non_wave_entry(live, kind):
            _fail(f"{kind} should be blocked on MT5")

    if skip_live_non_wave_entry(live, "WAVE"):
        _fail("WAVE should pass MT5 filter")

    plain = {
        "wave_time": "202601011030",
        "dir": 1,
        "fib50": 1.1,
        "sl": 1.09,
        "move_pct": 0.35,
    }
    if guard_live_send_order(live, plain, is_two_sided_mirror=True) is not True:
        _fail("two-sided mirror must be blocked")

    if guard_live_send_order(live, plain) is not False:
        _fail("plain WAVE must pass guard")

    print("  mode: wave_study_wave_only")
    print("  engine counter/ext: ON (routing)")
    print("  MT5: jen WAVE — OK")
    print("  engine == backtest engine flags:", engine.wave_counter_two_sided_enabled == live.wave_counter_two_sided_enabled)


def verify_backtest_wave_slice() -> dict:
    print("\n--- BACKTEST WAVE slice (2025-11-10 .. 2026-05-09) ---")
    df = filter_by_date_range(load_csv(str(CSV)), DATE_FROM, DATE_TO)
    cfg = resolve_grid_engine_config(LIVE_BOT_CONFIG)
    trades = BacktestEngine(cfg).run(df, retain_wave_snapshot=False)
    tdf = trades_to_df(trades)
    combo = {
        "wave_isolation_study": True,
        "wave_positions_only": True,
        "date_from": DATE_FROM,
        "date_to": DATE_TO,
    }
    wave_df = filter_trades_df_for_grid_stats(tdf, combo)
    stats = compute_stats(wave_df, date_from=DATE_FROM, date_to=DATE_TO)
    stats = apply_wave_isolation_report_stats(stats, combo)
    stats.update(
        compute_robustness_metrics(
            wave_df,
            max_dd_pct_vs_peak=stats.get("max_drawdown_pct_vs_peak"),
            max_dd_pct_vs_initial=stats.get("max_drawdown_pct"),
            bot_name=cfg.bot_name,
        )
    )
    ddi = stats.get("ddi_profile", {})
    result = {
        "net_pnl_usd": round(float(stats["net_pnl_usd"]), 2),
        "trades_wave": int(stats.get("trades_wave", 0)),
        "max_dd_pct": round(float(stats["max_drawdown_pct"]), 2),
        "max_ddi_pct": round(float(ddi.get("max_ddi_pct", 0)), 2),
        "median_ddi_pct": round(float(ddi.get("median_ddi_pct", 0)), 2),
        "p90_ddi_pct": round(float(ddi.get("p90_ddi_pct", 0)), 2),
    }
    for key, exp in EXPECTED.items():
        got = result[key]
        tol = TOL_PNL if "pnl" in key or "trades" in key else TOL_DD
        if abs(got - exp) > tol:
            _fail(f"{key}: expected {exp}, got {got} (tol {tol})")
        print(f"  {key}: {got} (expected {exp}) OK")

    print("\n  => Backtest WAVE slice odpovida ~36k a DDi.")
    return result


def main() -> None:
    print("=" * 60)
    print("VERIFY VARIANT B")
    print("=" * 60)
    verify_live_mt5_guards()
    verify_backtest_wave_slice()
    print("\n" + "=" * 60)
    print("PASS — varianta B: backtest WAVE ~36k; live MT5 jen WAVE.")
    print("Po nasazeni na MT5: equity ~= wave_pnl (WAVE slice).")
    print("=" * 60)


if __name__ == "__main__":
    main()
