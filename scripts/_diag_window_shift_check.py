"""Window-shift diagnostic — kvantifikuje "seed contamination" bug v INCREMENTAL_CAUSAL
detekci vln (`strategy.wave_detection_pine.PineWaveDetector` / `strategy.wave_source.
IncrementalWaveSource`) a ověřuje burn-in fix (`IncrementalWaveSource(burn_in_df=...)`).

ROOT CAUSE (re-verified zde):
  `PineWaveDetector.__init__` (a `run_pine_wave_simulation`, stejny vzorec), kdyz
  `initial_state is None`, seeduje:
      pivot_price = ohlc.high[0]; pivot_dir = 1 (HARDCODED); cand_price = ohlc.low[0]
  z baru 0 sveho VSTUPNIHO `df` — bez ohledu na to, co se delo pred timto barem.
  `strategy.ext_range.ExtRangeTracker`/`ExtRangeMeasureTracker` pak propaguji stav
  bar-po-baru (`_step`), takze jakakoli odchylka v ranem klasifikovani se kaskaduje
  dopredu (jina pivot/cand -> jiny move_pct -> jiny confirm bar -> jina nasledujici
  vlna -> ...).

  Live (`runtime.live_engine_session.LiveEngineSession.refresh_df_if_needed`) dostava
  z MT5 ROLLING okno (`cfg.startup_bars` nejnovejsich baru) — s KAZDYM novym barem se
  okno posune o 1 (nejstarsi bar vypadne, novy pribude), takze bar 0 (=seed bod) je
  PRI KAZDEM REFRESHI JINY. To zpusobuje, ze uz POUZITE vlny (vstup byl uz odeslan)
  se muzou retroaktivne prekvalifikovat jen kvuli posunu okna — NE kvuli genuine nove
  cenove informaci. Tento skript to kvantifikuje (pred fixem) a overuje, ze burn-in
  (`_WAVE_CAUSAL_BURN_IN_BARS` v `runtime/live_loop.py`) rozdil odstrani/redukuje na
  zanedbatelnou uroven.

METODA:
  Pro par oken (A, B) stejne velikosti (`cfg.startup_bars`), kde B = A posunute o
  `shift` baru dopredu (= presne to, co se stane mezi dvema live cykly), porovna
  definice vln NAROZENYCH v prekryvajici se casti (absolutni bar index) obou oken.
  Bez burn-in (`burn_in_bars=0`) = DNESNI (pred-fix) chovani. S burn-in = PO fixu.

SPUSTENI:
  .venv\\Scripts\\python.exe scripts/_diag_window_shift_check.py
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

# Pole vln, ktera rozhoduji o obchodni definici (box/SL/TP) — pouzita pro
# porovnani "je vlna na tomto baru STEJNA v obou oknech?".
_COMPARE_FIELDS = ("dir", "box_top", "box_bottom", "fib50", "sl", "tp", "move_pct")
_TOL = 1e-9


def _wave_key(w: dict) -> Tuple:
    return tuple(round(float(w[f]), 8) if f != "dir" else int(w[f]) for f in _COMPARE_FIELDS)


def _waves_by_abs_bar(df_window: pd.DataFrame, cfg, *, window_start: int,
                       burn_in_bars: int, full_df: pd.DataFrame) -> Dict[int, dict]:
    """Vsechny vlny narozene v `df_window` (IncrementalWaveSource), klicovane
    ABSOLUTNIM bar indexem (vuci `full_df`), volitelne s burn-in prefixem."""
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
    """Porovna vlny narozene v prekryvu oken A=[base,base+window_size) a
    B=[base+shift, base+shift+window_size), s danym `burn_in_bars`."""
    a_start, b_start = base, base + shift
    dfA = full_df.iloc[a_start : a_start + window_size].reset_index(drop=True)
    dfB = full_df.iloc[b_start : b_start + window_size].reset_index(drop=True)

    wavesA = _waves_by_abs_bar(dfA, cfg, window_start=a_start,
                                burn_in_bars=burn_in_bars, full_df=full_df)
    wavesB = _waves_by_abs_bar(dfB, cfg, window_start=b_start,
                                burn_in_bars=burn_in_bars, full_df=full_df)

    overlap_lo, overlap_hi = b_start, a_start + window_size  # [lo, hi)
    abs_bars = sorted(
        {b for b in wavesA if overlap_lo <= b < overlap_hi}
        | {b for b in wavesB if overlap_lo <= b < overlap_hi}
    )

    n_total = len(abs_bars)
    n_match = 0
    n_mismatch = 0
    max_depth_into_b = 0  # kolik baru OD ZACATKU B ma prvni rozdil (hloubka kontaminace)
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

    # Repo-wide POVINNE 2lete okno (AGENTS.md / backtest-2y-window rule).
    base_cfg = resolve_grid_engine_config(LIVE_BOT_CONFIG)
    cfg = replace(base_cfg, wave_detection_mode=WaveDetectionMode.INCREMENTAL_CAUSAL)

    df = load_data(cfg.symbol, cfg.timeframe_label, None, None)  # centralni 2y okno
    print(f"2y okno: {len(df)} baru  ({df['time'].iloc[0]} .. {df['time'].iloc[-1]})")

    window_size = int(cfg.startup_bars)
    shifts = [1, 5, 50]
    burn_in_variants = [0, 500, 1000, 1500, 2000, 3000]
    max_burn = max(burn_in_variants)

    # Vice `base` pozic rozprostrenych po celem 2y datasetu (ne jen jeden bod) —
    # kontaminace zavisi na konkretni cenove strukture kolem hranice okna, takze
    # jeden base muze vyjit skoro cistý, jiny mnohem hur. `base` musi mit dost
    # historie pro nejvetsi burn-in i dost "budoucnosti" pro window_size+max(shift).
    n_bases = 8
    lo_base = max_burn
    hi_base = len(df) - window_size - max(shifts) - 1
    assert hi_base > lo_base, "dataset moc kratky pro zvolene burn_in/window/shift"
    step = max(1, (hi_base - lo_base) // n_bases)
    bases = list(range(lo_base, hi_base, step))[:n_bases]

    print(f"window_size(startup_bars)={window_size}  shifts={shifts}  bases={bases}")
    print(f"burn_in_variants={burn_in_variants}\n")

    print(f"{'burn_in':>8} {'shift':>6} {'n_waves':>8} {'mismatch':>9} {'%mismatch':>10} {'max_depth':>10}")
    print("-" * 60)
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
                f"{pct:>9.2f}% {agg_depth:>10}"
            )

    print("\n--- SHRNUTI (agregovano pres vsechny base pozice) ---")
    baseline = [r for r in results if r["burn_in_bars"] == 0]
    fixed = [r for r in results if r["burn_in_bars"] == max_burn]
    if baseline:
        worst = max(baseline, key=lambda r: r["pct_mismatch"])
        print(
            f"PRED FIXEM (burn_in=0): nejhorsi %mismatch={worst['pct_mismatch']:.2f}% "
            f"(shift={worst['shift']}), max kontaminacni hloubka={max(r['max_depth_into_b'] for r in baseline)} baru"
        )
    if fixed:
        worst_f = max(fixed, key=lambda r: r["pct_mismatch"])
        print(
            f"PO FIXU (burn_in={max_burn}): nejhorsi %mismatch={worst_f['pct_mismatch']:.2f}% "
            f"(shift={worst_f['shift']}), max kontaminacni hloubka={max(r['max_depth_into_b'] for r in fixed)} baru"
        )

    # Ukazkove priklady prvnich par rozdilu (pred fixem), pro trasovani root cause.
    worst_baseline = max(baseline, key=lambda r: r["pct_mismatch"]) if baseline else None
    if worst_baseline:
        for pb in worst_baseline["per_base"]:
            if pb["mismatch_bars"]:
                b_start = pb["base"] + pb["shift"]
                print(f"\nPrvni rozdilne bary (burn_in=0, shift={pb['shift']}, base={pb['base']}):")
                for b in pb["mismatch_bars"][:5]:
                    print(f"  abs_bar={b}  time={df['time'].iloc[b]}  depth_into_b={b - b_start}")
                break


if __name__ == "__main__":
    main()
