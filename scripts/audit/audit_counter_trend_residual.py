"""
Counter-trend residual audit for LIVE_BOT_CONFIG backtest.

Usage:
  python scripts/audit/audit_counter_trend_residual.py
  python scripts/audit/audit_counter_trend_residual.py --baseline-html PATH --new-html PATH
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.bot_config import LIVE_BOT_CONFIG
from backtest.engine import BacktestEngine, ClosedTrade
from backtest.stats import classify_position_kind, trades_to_df
from strategy.ext_logic import (
    ENTRY_TAG_EXT_COUNTER_BOS,
    ENTRY_TAG_EXT_COUNTER_TIME,
    ENTRY_TAG_EXT_SECONDARY,
    is_ext_wave,
)
from strategy.trend_bos import compute_trend_states_per_bar
from strategy.wave_detection import detect_waves
from strategy.wick_fakeout import WAVE_ORIGIN_WF


def _load_df(csv_path: Path, date_from: str, date_to: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path, parse_dates=["datetime"]).rename(columns={"datetime": "time"})
    df = df[(df["time"] >= date_from) & (df["time"] <= date_to)].reset_index(drop=True)
    return df


def _trade_dir_label(d: int) -> str:
    return "bull" if int(d) == 1 else "bear"


def _is_counter_trend(trade_dir: int, trend: str) -> bool:
    td = _trade_dir_label(trade_dir)
    if trend not in ("bull", "bear"):
        return False
    return td != trend


def _ext_wave_times(waves: list, cfg) -> list[tuple[str, pd.Timestamp]]:
    out: list[tuple[str, pd.Timestamp]] = []
    for w in waves:
        if is_ext_wave(w, cfg):
            wt = str(w.get("wave_time", ""))
            try:
                ts = pd.Timestamp(wt)
            except Exception:
                continue
            out.append((wt, ts))
    return out


def _in_ext_24h_window(entry_time: pd.Timestamp, ext_times: list[tuple[str, pd.Timestamp]]) -> bool:
    for _, ext_ts in ext_times:
        if ext_ts <= entry_time <= ext_ts + timedelta(hours=24):
            return True
    return False


def _documented_exception(t: ClosedTrade, *, in_ext_24h: bool, cfg) -> tuple[bool, str]:
    kind = classify_position_kind(
        is_pp=bool(getattr(t, "is_pp", False)),
        is_counter=bool(getattr(t, "is_counter", False)),
        is_bos_reentry=bool(getattr(t, "is_bos_reentry", False)),
        is_two_sided_mirror=bool(getattr(t, "is_two_sided_mirror", False)),
        is_ext=bool(getattr(t, "is_ext", False)),
        entry_tag=str(getattr(t, "entry_tag", "base")),
    )
    tag = str(getattr(t, "entry_tag", "base"))
    origin = str(getattr(t, "wave_origin", "normal"))

    if kind == "WAVE_TWO_SIDED":
        return True, "WAVE_TWO_SIDED mirror (záměrně counter-trend)"
    if origin == WAVE_ORIGIN_WF:
        return True, "WF continuation MARKET"
    if tag in (ENTRY_TAG_EXT_COUNTER_TIME, ENTRY_TAG_EXT_COUNTER_BOS):
        return True, f"EXT counter ({tag})"
    if kind == "EXT_BOS":
        return True, "EXT_BOS counter"
    if kind == "EXT" or tag == ENTRY_TAG_EXT_SECONDARY:
        return True, "EXT secondary / primary"
    if kind == "WAVE_COUNTER":
        return True, "WAVE_COUNTER @ TP (wave_target_n)"
    if kind == "BOS":
        return True, "BOS re-entry po close flip"
    if in_ext_24h and bool(getattr(cfg, "ext_trade_both_sides_in_range", False)):
        return True, "EXT 24h okno + ext_trade_both_sides_in_range"
    if in_ext_24h and (bool(getattr(t, "is_ext", False)) or kind in ("EXT", "EXT_BOS", "WAVE")):
        return True, "EXT 24h okno (legitimní EXT režim)"
    if kind == "PP":
        return False, "PP — měl být blokován trend filtrem (Prompt 6)"
    return False, ""


def _code_path_hint(t: ClosedTrade, documented: bool, reason: str) -> str:
    if documented:
        return reason
    kind = classify_position_kind(
        is_pp=bool(getattr(t, "is_pp", False)),
        is_counter=bool(getattr(t, "is_counter", False)),
        is_bos_reentry=bool(getattr(t, "is_bos_reentry", False)),
        is_two_sided_mirror=bool(getattr(t, "is_two_sided_mirror", False)),
        is_ext=bool(getattr(t, "is_ext", False)),
        entry_tag=str(getattr(t, "entry_tag", "base")),
    )
    tag = str(getattr(t, "entry_tag", "base"))
    if kind == "WAVE" and tag == "base":
        return "engine._process_new_wave / replay — trend_filter bypass?"
    if kind == "PP":
        return "engine PP per-bar — pp_trend_allowed_at_bar (seed-reset?)"
    if kind == "BOS":
        return "engine BOS re-entry — close flip guard?"
    if kind == "WAVE_COUNTER":
        return "engine counter @ TP — wave_target_n event"
    return f"neidentifikováno ({kind}, tag={tag})"


def _entry_bar(t: ClosedTrade) -> int:
    return int(t.close_bar) - int(t.bars_held)


def analyze_trades(
    trades: list[ClosedTrade],
    df: pd.DataFrame,
    cfg,
) -> pd.DataFrame:
    waves = detect_waves(df, cfg)
    bar_states = compute_trend_states_per_bar(df, waves, cfg)
    ext_times = _ext_wave_times(waves, cfg)

    rows = []
    for t in trades:
        eb = _entry_bar(t)
        trend = bar_states[eb].direction if 0 <= eb < len(bar_states) else "unknown"
        ct = _is_counter_trend(t.dir, trend)
        et = pd.Timestamp(t.entry_time)
        in_ext = _in_ext_24h_window(et, ext_times)
        documented, doc_reason = _documented_exception(t, in_ext_24h=in_ext, cfg=cfg)
        kind = classify_position_kind(
            is_pp=bool(getattr(t, "is_pp", False)),
            is_counter=bool(getattr(t, "is_counter", False)),
            is_bos_reentry=bool(getattr(t, "is_bos_reentry", False)),
            is_two_sided_mirror=bool(getattr(t, "is_two_sided_mirror", False)),
            is_ext=bool(getattr(t, "is_ext", False)),
            entry_tag=str(getattr(t, "entry_tag", "base")),
        )
        rows.append(
            {
                "entry_time": et,
                "wave_time": t.wave_time,
                "dir": "BUY" if t.dir == 1 else "SELL",
                "entry_type": t.entry_type,
                "entry_tag": getattr(t, "entry_tag", "base"),
                "position_kind": kind,
                "wave_origin": getattr(t, "wave_origin", "normal"),
                "trend_at_entry": trend,
                "counter_trend": ct,
                "in_ext_24h": in_ext,
                "documented_exception": documented if ct else None,
                "exception_reason": doc_reason if (ct and documented) else "",
                "residual": ct and not documented,
                "residual_outside_ext": ct and not documented and not in_ext,
                "code_path_hint": _code_path_hint(t, documented if ct else True, doc_reason),
            }
        )
    return pd.DataFrame(rows)


def _extract_plotly_entry_markers(html_path: Path) -> list[dict]:
    text = html_path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"Plotly\.newPlot\(\s*[^,]+,\s*(\[.*?\])\s*,", text, re.DOTALL)
    if not m:
        return []
    try:
        traces = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    out = []
    for tr in traces:
        if tr.get("mode") != "markers":
            continue
        if tr.get("showlegend") is True and tr.get("x") in ([None], None):
            continue
        xs = tr.get("x") or []
        ys = tr.get("y") or []
        if not xs or xs == [None]:
            continue
        mk = tr.get("marker") or {}
        for i, x in enumerate(xs):
            if x is None:
                continue
            out.append(
                {
                    "x": str(x),
                    "y": float(ys[i]) if i < len(ys) else None,
                    "symbol": mk.get("symbol"),
                    "fill": mk.get("color"),
                    "border": (mk.get("line") or {}).get("color"),
                    "name": tr.get("name"),
                }
            )
    return out


def _marker_key(m: dict) -> tuple:
    return (m["x"], round(m["y"] or 0, 5), m.get("symbol"), m.get("fill"), m.get("border"))


def compare_html_markers(baseline: Path, new: Path) -> dict:
    b = _extract_plotly_entry_markers(baseline)
    n = _extract_plotly_entry_markers(new)
    bset = {_marker_key(x) for x in b}
    nset = {_marker_key(x) for x in n}
    return {
        "baseline_count": len(b),
        "new_count": len(n),
        "only_baseline": len(bset - nset),
        "only_new": len(nset - bset),
        "common": len(bset & nset),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/EURUSD.x_M30.csv")
    ap.add_argument("--date-from", default="2026-03-03")
    ap.add_argument("--date-to", default="2026-05-08")
    ap.add_argument("--baseline-html", default="")
    ap.add_argument("--new-html", default="")
    ap.add_argument("--out-csv", default="results/counter_trend_audit/residual_analysis.csv")
    args = ap.parse_args()

    cfg = LIVE_BOT_CONFIG
    df = _load_df(Path(args.csv), args.date_from, args.date_to)
    eng = BacktestEngine(cfg)
    trades = eng.run(df)
    audit = analyze_trades(trades, df, cfg)

    out_csv = Path(args.out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    audit.to_csv(out_csv, index=False)

    total = len(audit)
    ct = int(audit["counter_trend"].sum())
    ct_ext = int((audit["counter_trend"] & audit["in_ext_24h"]).sum())
    documented = int((audit["counter_trend"] & audit["documented_exception"].fillna(False)).sum())
    residual = int(audit["residual"].sum())
    residual_out = int(audit["residual_outside_ext"].sum())

    print(f"Config: {cfg.bot_name}")
    print(f"Period: {args.date_from} .. {args.date_to}")
    print(f"Total entries: {total}")
    print(f"Counter-trend: {ct} ({100*ct/max(total,1):.1f}%)")
    print(f"  in EXT 24h: {ct_ext}")
    print(f"  documented exceptions: {documented}")
    print(f"  residual (all): {residual}")
    print(f"  residual OUTSIDE EXT 24h: {residual_out}")
    print(f"CSV: {out_csv}")

    if args.baseline_html and args.new_html:
        diff = compare_html_markers(Path(args.baseline_html), Path(args.new_html))
        print("HTML marker diff:", diff)

    residuals = audit[audit["residual_outside_ext"]].copy()
    if not residuals.empty:
        print("\nResidual outside EXT 24h:")
        for _, r in residuals.iterrows():
            print(
                f"  {r['entry_time']} {r['dir']} kind={r['position_kind']} "
                f"tag={r['entry_tag']} trend={r['trend_at_entry']} -> {r['code_path_hint']}"
            )


if __name__ == "__main__":
    main()
