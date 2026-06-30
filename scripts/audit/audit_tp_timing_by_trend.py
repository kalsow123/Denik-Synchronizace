"""
TP timing audit — celé trendy pro tp_mode wave_target_n a wave_target_n_g.

Pro každý TP cyklus W(N-1) → forming/birth W(N) v rámci BOS segmentu spočítá
simulované chování OBOU módů (stejné vlny, stejné cfg kromě tp_mode):

  wave_target_n   — exit vždy na birth W(N) @ bar_close (TP_WAVE_N)
  wave_target_n_g — exit na extension hit @ armed_tp, jinak fallback birth W(N)

Buckety: BRZY / VCAS / POZDE / NEUTRAL (+ G_FALLBACK u wave_target_n_g)

Usage:
  python scripts/audit/audit_tp_timing_by_trend.py
  python scripts/audit/audit_tp_timing_by_trend.py --csv data/EURUSD_M30.csv \\
      --date-from 2026-03-03 --date-to 2026-05-10 \\
      --output results/audit_tp_timing
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.data_loader import filter_by_date_range, load_csv
from backtest.grid.translator import grid_dict_to_bot_config
from strategy.trend_bos import compute_trend_states_per_bar, iter_close_based_bos_flips
from strategy.wave_detection import detect_waves
from strategy.wave_detection_pine import compute_wave_birth_bars_pine
from strategy.wave_sequence import (
    compute_wave_sequence_info_per_wave,
    compute_wave_target_tp_price,
    is_tp_wave_index,
    propagate_seq_info_to_waves,
)
from strategy.wave_target_n_early import (
    extension_tp_hit_on_bar,
    start_forming_tp_watch,
)


from strategy.wave_target_n_mode import is_wave_target_n_g


def _example_cfg(*, tp_mode: str = "wave_target_n") -> Any:
    """EXAMPLE grid vetev — wave_target_n nebo wave_target_n_g (G preset z translatoru)."""
    return grid_dict_to_bot_config(
        {
            "timeframe": "M30",
            "wave_min_pct": 0.26,
            "min_opp_bars": 3,
            "rrr": 2.0,
            "fib_level": 0.5,
            "entry_mode": "market_fallback",
            "symbol": "EURUSD",
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
            "tp_target_wave_index": 4,
            "wave_extension_pct": 0.10,
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
        }
    )


def _example_cfg_pair() -> tuple[Any, Any]:
    cfg_n = _example_cfg(tp_mode="wave_target_n")
    cfg_g = _example_cfg(tp_mode="wave_target_n_g")
    assert not is_wave_target_n_g(cfg_n)
    assert is_wave_target_n_g(cfg_g)
    return cfg_n, cfg_g


def _prev_wave_size(prev_wave: dict) -> float:
    try:
        return abs(float(prev_wave["box_top"]) - float(prev_wave["box_bottom"]))
    except (KeyError, TypeError, ValueError):
        return 0.0


def _dir_label(d: int) -> str:
    return "bull" if int(d) == 1 else "bear"


def _trend_dir_int(label: str) -> int:
    return 1 if label == "bull" else -1 if label == "bear" else 0


def _price_touched_level(trend_dir: int, level: float, high: float, low: float) -> bool:
    if trend_dir == 1:
        return float(high) >= float(level)
    return float(low) <= float(level)


def _mfe_mae(
    df: pd.DataFrame,
    exit_bar: int,
    exit_price: float,
    trend_dir: int,
    end_bar: int,
) -> tuple[float, float]:
    if exit_bar >= end_bar or trend_dir not in (1, -1):
        return 0.0, 0.0
    sub = df.iloc[int(exit_bar) + 1 : int(end_bar) + 1]
    if sub.empty:
        return 0.0, 0.0
    if trend_dir == 1:
        mfe = max(0.0, float(sub["high"].max()) - float(exit_price))
        mae = max(0.0, float(exit_price) - float(sub["low"].min()))
    else:
        mfe = max(0.0, float(exit_price) - float(sub["low"].min()))
        mae = max(0.0, float(sub["high"].max()) - float(exit_price))
    return mfe, mae


def _classify_g(
    *,
    g_hit: bool,
    mfe: float,
    mae: float,
    prev_size: float,
    mfe_early_pct: float,
    mae_vcas_pct: float,
    mfe_neutral_pct: float,
) -> str:
    if not g_hit:
        return "NO_G_HIT"
    if prev_size <= 0.0:
        return "NEUTRAL"
    mfe_r = mfe / prev_size
    mae_r = mae / prev_size
    if mfe_r >= mfe_early_pct:
        return "BRZY"
    if mae_r >= mae_vcas_pct and mfe_r < mfe_neutral_pct:
        return "VCAS"
    return "NEUTRAL"


def _classify_legacy(
    *,
    legacy_bar: Optional[int],
    first_touch_bar: Optional[int],
    mfe: float,
    prev_size: float,
    late_min_bars: int,
    mfe_early_pct: float,
    mae: float,
    mae_vcas_pct: float,
) -> str:
    if legacy_bar is None or prev_size <= 0.0:
        return "NEUTRAL"
    mfe_r = mfe / prev_size
    mae_r = mae / prev_size
    if (
        first_touch_bar is not None
        and int(legacy_bar) - int(first_touch_bar) >= late_min_bars
        and mfe_r >= 0.05
    ):
        return "POZDE"
    if mfe_r >= mfe_early_pct:
        return "BRZY"
    if mae_r >= mae_vcas_pct:
        return "VCAS"
    return "NEUTRAL"


def _effective_g_mode(
    *,
    g_hit_bar: Optional[int],
    g_tp: Optional[float],
    g_bucket: str,
    legacy_bucket: str,
    g_mfe: float,
    g_mae: float,
    leg_mfe: float,
    leg_mae: float,
    wn_birth: int,
    legacy_close: float,
) -> dict:
    """Skutecne chovani wave_target_n_g: extension hit nebo fallback birth."""
    if g_hit_bar is not None and g_tp is not None:
        return {
            "wave_target_n_g_exit_path": "TP_EXTENSION_HIT",
            "wave_target_n_g_exit_bar": int(g_hit_bar),
            "wave_target_n_g_exit_price": float(g_tp),
            "wave_target_n_g_bucket": g_bucket,
            "wave_target_n_g_mfe_after_exit": round(g_mfe, 5),
            "wave_target_n_g_mae_after_exit": round(g_mae, 5),
        }
    return {
        "wave_target_n_g_exit_path": "TP_WAVE_N_FALLBACK",
        "wave_target_n_g_exit_bar": int(wn_birth),
        "wave_target_n_g_exit_price": float(legacy_close),
        "wave_target_n_g_bucket": legacy_bucket,
        "wave_target_n_g_mfe_after_exit": round(leg_mfe, 5),
        "wave_target_n_g_mae_after_exit": round(leg_mae, 5),
    }


def build_bos_segments(df: pd.DataFrame, waves: list, cfg) -> list[dict]:
    states = compute_trend_states_per_bar(df, waves, cfg)
    initial_dir = states[0].direction if states else "neutral"
    segments: list[dict] = []
    prev_bar = 0
    current_dir = initial_dir

    flips = list(iter_close_based_bos_flips(df, waves, cfg))
    for flip_bar, flip_time, _target, label, _swing, _seg_start in flips:
        end_bar = max(prev_bar, int(flip_bar) - 1)
        segments.append(
            {
                "start_bar": prev_bar,
                "end_bar": end_bar,
                "direction": current_dir,
                "start_time": df.iloc[prev_bar]["time"],
                "end_time": df.iloc[min(end_bar, len(df) - 1)]["time"],
            }
        )
        prev_bar = int(flip_bar)
        current_dir = "bull" if "bull" in str(label) else "bear"

    segments.append(
        {
            "start_bar": prev_bar,
            "end_bar": len(df) - 1,
            "direction": current_dir,
            "start_time": df.iloc[prev_bar]["time"],
            "end_time": df.iloc[-1]["time"],
        }
    )
    return segments


def segment_for_bar(segments: list[dict], bar: int) -> Optional[dict]:
    for seg in segments:
        if seg["start_bar"] <= bar <= seg["end_bar"]:
            return seg
    return None


def analyze_tp_cycles(
    df: pd.DataFrame,
    cfg,
    *,
    mfe_early_pct: float = 0.15,
    mae_vcas_pct: float = 0.10,
    mfe_neutral_pct: float = 0.08,
    late_min_bars: int = 3,
) -> pd.DataFrame:
    waves = detect_waves(df, cfg)
    seq_info = compute_wave_sequence_info_per_wave(df, waves, cfg)
    propagate_seq_info_to_waves(waves, seq_info)
    birth_bars = compute_wave_birth_bars_pine(df, cfg)
    waves_by_time = {str(w["wave_time"]): w for w in waves}
    segments = build_bos_segments(df, waves, cfg)
    target_n = int(getattr(cfg, "tp_target_wave_index", 0) or 0)

    rows: list[dict] = []
    seg_id = 0
    for seg in segments:
        if seg["direction"] not in ("bull", "bear"):
            continue
        seg_id += 1
        trend_dir = _trend_dir_int(seg["direction"])
        seg_end = int(seg["end_bar"])

        for w in waves:
            wt = str(w["wave_time"])
            info = seq_info.get(wt)
            if info is None or info.index_in_trend is None:
                continue
            if not is_tp_wave_index(int(info.index_in_trend), target_n):
                continue
            if int(w["dir"]) != trend_dir:
                continue

            wn_birth = birth_bars.get(wt)
            if wn_birth is None:
                continue
            if not (seg["start_bar"] <= wn_birth <= seg_end):
                continue

            prev_wt = info.prev_same_dir_in_trend_wave_time
            if not prev_wt or prev_wt not in waves_by_time:
                continue
            prev_wave = waves_by_time[prev_wt]
            prev_birth = birth_bars.get(prev_wt)
            if prev_birth is None or prev_birth >= wn_birth:
                continue

            prev_size = _prev_wave_size(prev_wave)
            legacy_tp = compute_wave_target_tp_price(w, prev_wave, cfg)
            legacy_close = float(df.iloc[int(wn_birth)]["close"])

            watch = start_forming_tp_watch(
                prev_wave=prev_wave,
                index_in_trend=int(info.index_in_trend) - 1,
                target_n=target_n,
                start_bar=int(prev_birth),
            )
            if watch is None:
                continue

            g_hit_bar: Optional[int] = None
            g_tp: Optional[float] = None
            arm_bar: Optional[int] = None
            armed_tp: Optional[float] = None
            first_touch_bar: Optional[int] = None

            scan_end = int(wn_birth)
            for bar_idx in range(int(prev_birth) + 1, scan_end + 1):
                row = df.iloc[bar_idx]
                hi = float(row["high"])
                lo = float(row["low"])
                cl = float(row["close"])
                op = float(row["open"])
                watch.update_extreme(hi, lo)
                if watch.try_arm(cfg):
                    arm_bar = bar_idx
                    armed_tp = watch.armed_tp
                if watch.armed and armed_tp is not None:
                    if _price_touched_level(trend_dir, float(armed_tp), hi, lo):
                        if first_touch_bar is None:
                            first_touch_bar = bar_idx
                    if extension_tp_hit_on_bar(
                        watch, high=hi, low=lo, close=cl, open_=op,
                    ):
                        g_hit_bar = bar_idx
                        g_tp = float(armed_tp)
                        watch.extension_hit_done = True
                        break

            # Trend extreme v segmentu od watch start do konce segmentu
            sub_ext = df.iloc[int(prev_birth) : seg_end + 1]
            if trend_dir == 1:
                trend_extreme = float(sub_ext["high"].max())
            else:
                trend_extreme = float(sub_ext["low"].min())

            g_mfe, g_mae = (0.0, 0.0)
            if g_hit_bar is not None and g_tp is not None:
                g_mfe, g_mae = _mfe_mae(df, g_hit_bar, g_tp, trend_dir, seg_end)

            leg_mfe, leg_mae = _mfe_mae(
                df, int(wn_birth), legacy_close, trend_dir, seg_end,
            )

            g_bucket = _classify_g(
                g_hit=g_hit_bar is not None,
                mfe=g_mfe,
                mae=g_mae,
                prev_size=prev_size,
                mfe_early_pct=mfe_early_pct,
                mae_vcas_pct=mae_vcas_pct,
                mfe_neutral_pct=mfe_neutral_pct,
            )
            legacy_bucket = _classify_legacy(
                legacy_bar=int(wn_birth),
                first_touch_bar=first_touch_bar,
                mfe=leg_mfe,
                prev_size=prev_size,
                late_min_bars=late_min_bars,
                mfe_early_pct=mfe_early_pct,
                mae=leg_mae,
                mae_vcas_pct=mae_vcas_pct,
            )

            bars_g_vs_legacy = (
                int(g_hit_bar) - int(wn_birth) if g_hit_bar is not None else None
            )
            bars_legacy_after_touch = (
                int(wn_birth) - int(first_touch_bar)
                if first_touch_bar is not None
                else None
            )

            g_eff = _effective_g_mode(
                g_hit_bar=g_hit_bar,
                g_tp=g_tp,
                g_bucket=g_bucket,
                legacy_bucket=legacy_bucket,
                g_mfe=g_mfe,
                g_mae=g_mae,
                leg_mfe=leg_mfe,
                leg_mae=leg_mae,
                wn_birth=int(wn_birth),
                legacy_close=legacy_close,
            )

            rows.append(
                {
                    "segment_id": seg_id,
                    "segment_dir": seg["direction"],
                    "segment_start": seg["start_time"],
                    "segment_end": seg["end_time"],
                    "tp_wave_time": wt,
                    "tp_index": int(info.index_in_trend),
                    "prev_wave_time": str(prev_wt),
                    "prev_wave_size": round(prev_size, 5),
                    "arm_bar": arm_bar,
                    "armed_tp": armed_tp,
                    "first_touch_bar": first_touch_bar,
                    "g_hit_bar": g_hit_bar,
                    "g_tp_price": g_tp,
                    "legacy_birth_bar": int(wn_birth),
                    "legacy_tp_price": legacy_tp,
                    "legacy_close_price": round(legacy_close, 5),
                    "bars_g_vs_legacy_birth": bars_g_vs_legacy,
                    "bars_legacy_after_first_touch": bars_legacy_after_touch,
                    "trend_extreme_in_segment": round(trend_extreme, 5),
                    # --- wave_target_n (legacy birth) ---
                    "wave_target_n_exit_bar": int(wn_birth),
                    "wave_target_n_exit_price": round(legacy_close, 5),
                    "wave_target_n_bucket": legacy_bucket,
                    "wave_target_n_mfe_after_exit": round(leg_mfe, 5),
                    "wave_target_n_mae_after_exit": round(leg_mae, 5),
                    "wave_target_n_mfe_ratio": (
                        round(leg_mfe / prev_size, 4) if prev_size else None
                    ),
                    # --- wave_target_n_g (extension nebo fallback) ---
                    **g_eff,
                    "wave_target_n_g_mfe_ratio": (
                        round(g_eff["wave_target_n_g_mfe_after_exit"] / prev_size, 4)
                        if prev_size
                        else None
                    ),
                    "bucket_differs": (
                        g_eff["wave_target_n_g_bucket"] != legacy_bucket
                    ),
                    # raw simulace (debug)
                    "g_mfe_after_exit": round(g_mfe, 5),
                    "g_mae_after_exit": round(g_mae, 5),
                    "g_bucket_raw": g_bucket,
                    "legacy_bucket_raw": legacy_bucket,
                }
            )

    return pd.DataFrame(rows)


def _bucket_summary(df: pd.DataFrame, col: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    counts = df[col].value_counts()
    total = len(df)
    out = counts.rename("count").to_frame()
    out["pct"] = (out["count"] / total * 100.0).round(1)
    return out.reset_index().rename(columns={"index": col})


def _mode_summary(cycles: pd.DataFrame, bucket_col: str) -> pd.DataFrame:
    return _bucket_summary(cycles, bucket_col)


def _mode_compare_table(cycles: pd.DataFrame) -> pd.DataFrame:
    """Side-by-side bucket counts pro wave_target_n vs wave_target_n_g."""
    rows = []
    for tp_mode, col in (
        ("wave_target_n", "wave_target_n_bucket"),
        ("wave_target_n_g", "wave_target_n_g_bucket"),
    ):
        sm = _mode_summary(cycles, col)
        if sm.empty:
            continue
        for _, r in sm.iterrows():
            rows.append(
                {
                    "tp_mode": tp_mode,
                    "bucket": r[col],
                    "count": int(r["count"]),
                    "pct": float(r["pct"]),
                }
            )
    return pd.DataFrame(rows)


def write_html_report(
    out_path: Path,
    cycles: pd.DataFrame,
    mode_compare: pd.DataFrame,
    g_path_summary: pd.DataFrame,
    meta: dict,
) -> None:
    def _table(frame: pd.DataFrame, title: str) -> str:
        if frame.empty:
            return f"<h3>{title}</h3><p>(prázdné)</p>"
        return f"<h3>{title}</h3>{frame.to_html(index=False, border=0)}"

    meta_rows = "".join(
        f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in meta.items()
    )
    sample = cycles.head(40)
    diff_n = int(cycles["bucket_differs"].sum()) if not cycles.empty else 0
    ext_n = (
        int((cycles["wave_target_n_g_exit_path"] == "TP_EXTENSION_HIT").sum())
        if not cycles.empty
        else 0
    )
    html = f"""<!DOCTYPE html>
<html lang="cs">
<head>
<meta charset="utf-8"/>
<title>TP timing — wave_target_n vs wave_target_n_g</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 24px; }}
table {{ border-collapse: collapse; margin-bottom: 24px; font-size: 13px; }}
th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: left; }}
th {{ background: #f0f0f0; }}
</style>
</head>
<body>
<h1>TP timing audit — wave_target_n vs wave_target_n_g</h1>
<p><strong>wave_target_n</strong>: exit vždy na birth W(N) @ close.<br/>
<strong>wave_target_n_g</strong>: extension hit @ armed_tp, jinak fallback birth W(N).</p>
<h2>Parametry</h2>
<table><tbody>{meta_rows}</tbody></table>
<h2>Shrnutí bucketů podle tp_mode</h2>
{_table(mode_compare, "")}
<h2>wave_target_n_g — cesta exitu</h2>
{_table(g_path_summary, "")}
<p>Cyklů s rozdílným bucketem mezi módy: <strong>{diff_n}</strong> / {meta.get("tp_cycles", 0)}</p>
<p>wave_target_n_g extension hit (bez fallbacku): <strong>{ext_n}</strong> / {meta.get("tp_cycles", 0)}</p>
<h2>Detail cyklů (max 40)</h2>
{sample.to_html(index=False, border=0) if not sample.empty else "<p>Žádné TP cykly</p>"}
<h2>Bucket interpretace</h2>
<ul>
<li><strong>BRZY</strong> — po TP trend ještě výrazně pokračoval (MFE ≥ {meta.get("mfe_early_pct")}× prev wave).</li>
<li><strong>VCAS</strong> — po TP brzy proti-trend pohyb, malé MFE.</li>
<li><strong>POZDE</strong> — cena zasáhla TP úroveň ≥ {meta.get("late_min_bars")} barů před birth W(N).</li>
<li><strong>NEUTRAL</strong> — ani výrazné pokračování, ani rychlý reversal.</li>
</ul>
</body>
</html>"""
    out_path.write_text(html, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="TP timing audit po BOS trendech")
    parser.add_argument("--csv", type=Path, default=ROOT / "data" / "EURUSD_M30.csv")
    parser.add_argument("--date-from", default="2026-03-03")
    parser.add_argument("--date-to", default="2026-05-10")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "results" / "audit_tp_timing",
    )
    parser.add_argument("--mfe-early-pct", type=float, default=0.15)
    parser.add_argument("--mae-vcas-pct", type=float, default=0.10)
    parser.add_argument("--mfe-neutral-pct", type=float, default=0.08)
    parser.add_argument("--late-min-bars", type=int, default=3)
    args = parser.parse_args()

    cfg_n, cfg_g = _example_cfg_pair()
    df = load_csv(args.csv)
    df = filter_by_date_range(df, args.date_from, args.date_to)

    # Vlny / TP cykly nezavisi na tp_mode (rodina sdili detekci); cfg_n = EXAMPLE parametry
    cycles = analyze_tp_cycles(
        df,
        cfg_n,
        mfe_early_pct=args.mfe_early_pct,
        mae_vcas_pct=args.mae_vcas_pct,
        mfe_neutral_pct=args.mfe_neutral_pct,
        late_min_bars=args.late_min_bars,
    )

    out_dir = args.output
    out_dir.mkdir(parents=True, exist_ok=True)

    cycles.to_csv(out_dir / "tp_timing_cycles.csv", index=False)

    mode_compare = _mode_compare_table(cycles)
    mode_compare.to_csv(out_dir / "summary_by_tp_mode.csv", index=False)

    wtn_summary = _mode_summary(cycles, "wave_target_n_bucket")
    wtng_summary = _mode_summary(cycles, "wave_target_n_g_bucket")
    wtn_summary.to_csv(out_dir / "summary_wave_target_n.csv", index=False)
    wtng_summary.to_csv(out_dir / "summary_wave_target_n_g.csv", index=False)

    if not cycles.empty:
        g_path_summary = (
            cycles["wave_target_n_g_exit_path"]
            .value_counts()
            .rename("count")
            .reset_index()
            .rename(columns={"index": "exit_path"})
        )
        g_path_summary["pct"] = (
            g_path_summary["count"] / len(cycles) * 100.0
        ).round(1)
    else:
        g_path_summary = pd.DataFrame()
    g_path_summary.to_csv(out_dir / "summary_wave_target_n_g_exit_path.csv", index=False)

    meta = {
        "csv": str(args.csv),
        "date_from": args.date_from,
        "date_to": args.date_to,
        "tp_modes": "wave_target_n, wave_target_n_g",
        "tp_target_wave_index": cfg_n.tp_target_wave_index,
        "wave_extension_pct": cfg_n.wave_extension_pct,
        "wave_min_pct": cfg_n.wave_min_pct,
        "tp_cycles": len(cycles),
        "trend_segments_with_tp": (
            cycles["segment_id"].nunique() if not cycles.empty else 0
        ),
        "bucket_differs_count": (
            int(cycles["bucket_differs"].sum()) if not cycles.empty else 0
        ),
        "g_extension_hit_count": (
            int((cycles["wave_target_n_g_exit_path"] == "TP_EXTENSION_HIT").sum())
            if not cycles.empty
            else 0
        ),
        "mfe_early_pct": args.mfe_early_pct,
        "mae_vcas_pct": args.mae_vcas_pct,
        "late_min_bars": args.late_min_bars,
    }
    write_html_report(
        out_dir / "tp_timing_report.html",
        cycles,
        mode_compare,
        g_path_summary,
        meta,
    )

    print(f"TP cyklu: {len(cycles)}  |  trend segmentu: {meta['trend_segments_with_tp']}")
    print(f"tp_mode: wave_target_n + wave_target_n_g (EXAMPLE grid parametry)")
    print()
    for tp_mode, col in (
        ("wave_target_n", "wave_target_n_bucket"),
        ("wave_target_n_g", "wave_target_n_g_bucket"),
    ):
        print(f"=== {tp_mode} ===")
        sm = _mode_summary(cycles, col)
        if sm.empty:
            print("  (zadna data)")
        else:
            for _, r in sm.iterrows():
                print(f"  {r[col]:10s}  {int(r['count']):3d}  ({r['pct']:5.1f}%)")
        print()

    if not cycles.empty:
        ext = int((cycles["wave_target_n_g_exit_path"] == "TP_EXTENSION_HIT").sum())
        fb = int((cycles["wave_target_n_g_exit_path"] == "TP_WAVE_N_FALLBACK").sum())
        diff = int(cycles["bucket_differs"].sum())
        print(f"wave_target_n_g: extension hit {ext}/{len(cycles)}, fallback {fb}/{len(cycles)}")
        print(f"Cyklu s jinym bucketem nez wave_target_n: {diff}/{len(cycles)}")
        sub = cycles[cycles["wave_target_n_g_exit_path"] == "TP_EXTENSION_HIT"]
        if not sub.empty and sub["bars_g_vs_legacy_birth"].notna().any():
            med = sub["bars_g_vs_legacy_birth"].median()
            print(f"Medián extension hit vs birth W(N): {med:.0f} baru (zaporne = G drive)")
    print()
    print(f"Vystup: {out_dir}")


if __name__ == "__main__":
    main()
