"""Temporary variant of `scripts/_diag_window_shift_check.py` — re-verifies the
window-shift / seed-contamination fix specifically at burn_in=200 (candidate for
reducing `_WAVE_CAUSAL_BURN_IN_BARS` in `runtime/live_loop.py` from 2000 to 200),
plus intermediate sanity points (100, 500) and the previously-validated 2000 and
the pre-fix 0 baseline, for comparison — SAME window positions/shifts as the
original validation (8 bases across the 2y dataset, shifts of 1/5/50 bars).

This is a diagnostic-only, disposable script (not wired into CI/tests). See
`scripts/_diag_window_shift_check.py` for the full original methodology/root-cause
writeup — this file only trims `burn_in_variants` for a faster targeted re-check.

SPUSTENI:
  .venv\\Scripts\\python.exe scripts/_diag_burn_in_200_check.py
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

_COMPARE_FIELDS = ("dir", "box_top", "box_bottom", "fib50", "sl", "tp", "move_pct")


def _wave_key(w: dict) -> Tuple:
    return tuple(round(float(w[f]), 8) if f != "dir" else int(w[f]) for f in _COMPARE_FIELDS)


def _waves_by_abs_bar(df_window: pd.DataFrame, cfg, *, window_start: int,
                       burn_in_bars: int, full_df: pd.DataFrame) -> Dict[int, dict]:
    from strategy.wave_source import IncrementalWaveSource

    burn_in_df = None
    if burn_in_bars > 0:
        lo = max(0, window_start - burn_in_bars)
        if lo < window_start:
            burn_in_df = full_df.iloc[lo:window_start].reset_index(drop=True)

    src = IncrementalWaveSource(df_window, cfg, burn_in_df=burn_in_df)
    for i in range(1, len(df_window)):
        src.waves_at(i)

    birth = src.birth_map()
    by_wt = {str(w["wave_time"]): w for w in src.all_waves()}
    out: Dict[int, dict] = {}
    for wt, local_bar in birth.items():
        w = by_wt.get(wt)
        if w is None:
            continue
        out[window_start + int(local_bar)] = w
    return out


def compare_windows(
    full_df: pd.DataFrame,
    cfg,
    *,
    window_size: int,
    base: int,
    shift: int,
    burn_in_bars: int,
) -> dict:
    a_start, b_start = base, base + shift
    dfA = full_df.iloc[a_start : a_start + window_size].reset_index(drop=True)
    dfB = full_df.iloc[b_start : b_start + window_size].reset_index(drop=True)

    wavesA = _waves_by_abs_bar(dfA, cfg, window_start=a_start,
                                burn_in_bars=burn_in_bars, full_df=full_df)
    wavesB = _waves_by_abs_bar(dfB, cfg, window_start=b_start,
                                burn_in_bars=burn_in_bars, full_df=full_df)

    overlap_lo, overlap_hi = b_start, a_start + window_size
    abs_bars = sorted(
        {b for b in wavesA if overlap_lo <= b < overlap_hi}
        | {b for b in wavesB if overlap_lo <= b < overlap_hi}
    )

    n_total = len(abs_bars)
    n_match = 0
    n_mismatch = 0
    max_depth_into_b = 0
    mismatches: List[int] = []
    for b in abs_bars:
        wa = wavesA.get(b)
        wb = wavesB.get(b)
        if wa is None or wb is None:
            n_mismatch += 1
            mismatches.append(b)
            continue
        if _wave_key(wa) == _wave_key(wb):
            n_match += 1
        else:
            n_mismatch += 1
            mismatches.append(b)

    if mismatches:
        max_depth_into_b = max(m - b_start for m in mismatches)

    pct = (100.0 * n_mismatch / n_total) if n_total else 0.0
    return {
        "base": base,
        "shift": shift,
        "burn_in_bars": burn_in_bars,
        "n_total": n_total,
        "n_match": n_match,
        "n_mismatch": n_mismatch,
        "pct_mismatch": pct,
        "max_depth_into_b": max_depth_into_b,
        "mismatch_bars": mismatches,
    }


def main() -> None:
    from config.bot_config import LIVE_BOT_CONFIG
    from config.enums import WaveDetectionMode
    from config.position_modes import resolve_grid_engine_config
    from backtest.grid.data_cache import load_data

    base_cfg = resolve_grid_engine_config(LIVE_BOT_CONFIG)
    cfg = replace(base_cfg, wave_detection_mode=WaveDetectionMode.INCREMENTAL_CAUSAL)

    df = load_data(cfg.symbol, cfg.timeframe_label, None, None)  # centralni 2y okno
    print(f"2y okno: {len(df)} baru  ({df['time'].iloc[0]} .. {df['time'].iloc[-1]})", flush=True)

    window_size = int(cfg.startup_bars)
    shifts = [1, 5, 50]
    # 0 = pre-fix baseline (kontrola), 100/200/500 = kandidati na redukci,
    # 2000 = soucasna (predchozi) empiricky overena hodnota (referencni kontrola konzistence).
    burn_in_variants = [0, 100, 200, 500, 2000]
    max_burn = max(burn_in_variants)

    n_bases = 8
    lo_base = max_burn
    hi_base = len(df) - window_size - max(shifts) - 1
    assert hi_base > lo_base, "dataset moc kratky pro zvolene burn_in/window/shift"
    step = max(1, (hi_base - lo_base) // n_bases)
    bases = list(range(lo_base, hi_base, step))[:n_bases]

    print(f"window_size(startup_bars)={window_size}  shifts={shifts}  bases={bases}", flush=True)
    print(f"burn_in_variants={burn_in_variants}\n", flush=True)

    print(f"{'burn_in':>8} {'shift':>6} {'n_waves':>8} {'mismatch':>9} {'%mismatch':>10} {'max_depth':>10}", flush=True)
    print("-" * 60, flush=True)
    results = []
    for burn_in in burn_in_variants:
        for shift in shifts:
            agg_total = agg_mismatch = 0
            agg_depth = 0
            per_base = []
            for base in bases:
                r = compare_windows(
                    df, cfg, window_size=window_size, base=base, shift=shift,
                    burn_in_bars=burn_in,
                )
                per_base.append(r)
                agg_total += r["n_total"]
                agg_mismatch += r["n_mismatch"]
                agg_depth = max(agg_depth, r["max_depth_into_b"])
            pct = (100.0 * agg_mismatch / agg_total) if agg_total else 0.0
            results.append({
                "burn_in_bars": burn_in, "shift": shift, "n_total": agg_total,
                "n_mismatch": agg_mismatch, "pct_mismatch": pct,
                "max_depth_into_b": agg_depth, "per_base": per_base,
            })
            print(
                f"{burn_in:>8} {shift:>6} {agg_total:>8} {agg_mismatch:>9} "
                f"{pct:>9.2f}% {agg_depth:>10}",
                flush=True,
            )

    print("\n--- SHRNUTI (agregovano pres vsechny base pozice, PER burn_in) ---", flush=True)
    for burn_in in burn_in_variants:
        rows = [r for r in results if r["burn_in_bars"] == burn_in]
        worst = max(rows, key=lambda r: r["pct_mismatch"])
        max_depth = max(r["max_depth_into_b"] for r in rows)
        print(
            f"burn_in={burn_in}: nejhorsi %mismatch={worst['pct_mismatch']:.4f}% "
            f"(shift={worst['shift']}), max kontaminacni hloubka={max_depth} baru",
            flush=True,
        )

    # Ukazkove priklady prvnich par rozdilu pro nejhorsi non-zero-mismatch burn_in
    # (mimo cistou 0 baseline), pro trasovani, pokud neco selze.
    for burn_in in burn_in_variants:
        if burn_in == 0:
            continue
        rows = [r for r in results if r["burn_in_bars"] == burn_in]
        worst = max(rows, key=lambda r: r["pct_mismatch"])
        if worst["pct_mismatch"] > 0:
            for pb in worst["per_base"]:
                if pb["mismatch_bars"]:
                    b_start = pb["base"] + pb["shift"]
                    print(f"\nPrvni rozdilne bary (burn_in={burn_in}, shift={pb['shift']}, base={pb['base']}):", flush=True)
                    for b in pb["mismatch_bars"][:5]:
                        print(f"  abs_bar={b}  time={df['time'].iloc[b]}  depth_into_b={b - b_start}", flush=True)
                    break


if __name__ == "__main__":
    main()
