"""
Audit EXT pozic — po uzavření: pokračoval hlavní BOS trend, nebo se otočil?

Pro každou EXT-related pozici v backtestu (primary EXT vlna, E23_, ECT_, ECB_):
  - trend_at_close = BOS směr na close baru
  - post_close: první BOS flip po exitu do konce segmentu / dat
  - TREND_POKRACOVAL = žádný BOS flip proti trend_at_close
  - TREND_OBRATIL    = BOS flip nastal (trend se otočil)

Usage:
  python scripts/audit/audit_ext_post_exit_trend.py
  python scripts/audit/audit_ext_post_exit_trend.py --date-from 2026-03-03 --date-to 2026-05-10
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.data_loader import filter_by_date_range, load_csv
from backtest.engine import BacktestEngine, ClosedTrade
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from backtest.stats import classify_position_kind, trades_to_df
from strategy.ext_logic import (
    ENTRY_TAG_EXT_COUNTER_BOS,
    ENTRY_TAG_EXT_COUNTER_TIME,
    ENTRY_TAG_EXT_SECONDARY,
    is_ext_block_trade,
    is_ext_counter_trade,
    is_ext_wave,
)
from strategy.trend_bos import compute_trend_states_per_bar, iter_close_based_bos_flips
from strategy.wave_detection import detect_waves


def _load_cfg(*, tp_mode: str = "wave_target_n", profile: str = "EXAMPLE"):
    """První EXAMPLE combo s daným tp_mode (plné grid parametry)."""
    for combo in generate_combinations(get_profile(profile)):
        if str(combo.get("tp_mode", "")) == tp_mode:
            combo = dict(combo)
            combo.setdefault("spread", 0.0001)
            combo.setdefault("slippage", 0.0)
            return grid_dict_to_bot_config(combo)
    raise ValueError(f"Profil {profile} nemá tp_mode={tp_mode}")


def _trend_dir_int(label: str) -> int:
    if label == "bull":
        return 1
    if label == "bear":
        return -1
    return 0


def _ext_trade_subtype(t: ClosedTrade, ext_wave_times: set[str]) -> str:
    tag = str(getattr(t, "entry_tag", "base") or "base")
    if tag == ENTRY_TAG_EXT_SECONDARY:
        return "EXT_SECONDARY"
    if tag == ENTRY_TAG_EXT_COUNTER_TIME:
        return "EXT_COUNTER_TIME"
    if tag == ENTRY_TAG_EXT_COUNTER_BOS:
        return "EXT_COUNTER_BOS"
    if str(t.wave_time) in ext_wave_times and not getattr(t, "is_counter", False):
        return "EXT_PRIMARY"
    if bool(getattr(t, "is_ext", False)):
        return "EXT_OTHER"
    return "NOT_EXT"


def _is_ext_related(t: ClosedTrade, ext_wave_times: set[str]) -> bool:
    if bool(getattr(t, "is_ext", False)):
        return True
    if str(t.wave_time) in ext_wave_times and not getattr(t, "is_pp", False):
        return True
    return False


def _build_bos_segments(df: pd.DataFrame, waves: list, cfg) -> list[dict]:
    states = compute_trend_states_per_bar(df, waves, cfg)
    initial_dir = states[0].direction if states else "neutral"
    segments: list[dict] = []
    prev_bar = 0
    current_dir = initial_dir
    for flip_bar, _flip_time, _target, label, _swing, _seg_start in iter_close_based_bos_flips(
        df, waves, cfg
    ):
        end_bar = max(prev_bar, int(flip_bar) - 1)
        segments.append(
            {
                "start_bar": prev_bar,
                "end_bar": end_bar,
                "direction": current_dir,
            }
        )
        prev_bar = int(flip_bar)
        current_dir = "bull" if "bull" in str(label) else "bear"
    segments.append(
        {
            "start_bar": prev_bar,
            "end_bar": len(df) - 1,
            "direction": current_dir,
        }
    )
    return segments


def _segment_at_bar(segments: list[dict], bar: int) -> Optional[dict]:
    for seg in segments:
        if seg["start_bar"] <= bar <= seg["end_bar"]:
            return seg
    return None


def _first_bos_flip_after(
    df: pd.DataFrame,
    waves: list,
    cfg,
    *,
    after_bar: int,
    until_bar: int,
) -> Optional[int]:
    for flip_bar, *_rest in iter_close_based_bos_flips(df, waves, cfg):
        fb = int(flip_bar)
        if fb > after_bar and fb <= until_bar:
            return fb
    return None


def _price_continuation(
    df: pd.DataFrame,
    *,
    close_bar: int,
    until_bar: int,
    close_price: float,
    trade_dir: int,
) -> tuple[str, float, float]:
    """Po exitu: pokračovala cena ve směru pozice, nebo proti?"""
    if close_bar >= until_bar or trade_dir not in (1, -1):
        return "NEUTRAL", 0.0, 0.0
    sub = df.iloc[int(close_bar) + 1 : int(until_bar) + 1]
    if sub.empty:
        return "NEUTRAL", 0.0, 0.0
    if trade_dir == 1:
        mfe = max(0.0, float(sub["high"].max()) - float(close_price))
        mae = max(0.0, float(close_price) - float(sub["low"].min()))
    else:
        mfe = max(0.0, float(close_price) - float(sub["low"].min()))
        mae = max(0.0, float(sub["high"].max()) - float(close_price))
    if mfe > mae * 1.2 and mfe > 0:
        return "CENA_POKRACOVALA", mfe, mae
    if mae > mfe * 1.2 and mae > 0:
        return "CENA_OBRATILA", mfe, mae
    return "NEUTRAL", mfe, mae


def analyze_ext_trades(
    trades: list[ClosedTrade],
    df: pd.DataFrame,
    cfg,
) -> pd.DataFrame:
    waves = detect_waves(df, cfg)
    bar_states = compute_trend_states_per_bar(df, waves, cfg)
    segments = _build_bos_segments(df, waves, cfg)
    ext_wave_times = {
        str(w["wave_time"]) for w in waves if is_ext_wave(w, cfg)
    }

    rows: list[dict] = []
    for t in trades:
        if not _is_ext_related(t, ext_wave_times):
            continue
        cb = int(t.close_bar)
        if not (0 <= cb < len(df)):
            continue
        seg = _segment_at_bar(segments, cb)
        until_bar = int(seg["end_bar"]) if seg else len(df) - 1
        trend_at_close = bar_states[cb].direction if cb < len(bar_states) else "unknown"
        trend_int = _trend_dir_int(trend_at_close)

        flip_bar = _first_bos_flip_after(
            df, waves, cfg, after_bar=cb, until_bar=until_bar,
        )
        if trend_int in (1, -1):
            if flip_bar is None:
                bos_outcome = "TREND_POKRACOVAL"
            else:
                flip_trend = bar_states[flip_bar].direction if flip_bar < len(bar_states) else "unknown"
                flip_int = _trend_dir_int(flip_trend)
                if flip_int != trend_int and flip_int in (1, -1):
                    bos_outcome = "TREND_OBRATIL"
                else:
                    bos_outcome = "TREND_POKRACOVAL"
            bars_to_flip = (int(flip_bar) - cb) if flip_bar is not None else None
        else:
            bos_outcome = "NEUTRAL_TREND"
            bars_to_flip = None

        price_outcome, mfe, mae = _price_continuation(
            df,
            close_bar=cb,
            until_bar=until_bar,
            close_price=float(t.close_price),
            trade_dir=int(t.dir),
        )
        aligned = (
            int(t.dir) == trend_int if trend_int in (1, -1) else None
        )

        rows.append(
            {
                "entry_time": t.entry_time,
                "close_time": t.close_time,
                "wave_time": t.wave_time,
                "dir": "BUY" if t.dir == 1 else "SELL",
                "ext_subtype": _ext_trade_subtype(t, ext_wave_times),
                "position_kind": classify_position_kind(
                    is_pp=bool(getattr(t, "is_pp", False)),
                    is_counter=bool(getattr(t, "is_counter", False)),
                    is_bos_reentry=bool(getattr(t, "is_bos_reentry", False)),
                    is_two_sided_mirror=bool(getattr(t, "is_two_sided_mirror", False)),
                    is_ext=bool(getattr(t, "is_ext", False)),
                    entry_tag=str(getattr(t, "entry_tag", "base")),
                ),
                "close_reason": t.close_reason,
                "pnl_usd": round(float(t.pnl_usd), 2),
                "trend_at_close": trend_at_close,
                "aligned_with_bos_at_close": aligned,
                "bos_outcome_after_close": bos_outcome,
                "bars_to_bos_flip": bars_to_flip,
                "bars_until_segment_end": until_bar - cb,
                "price_outcome_after_close": price_outcome,
                "mfe_after_close": round(mfe, 5),
                "mae_after_close": round(mae, 5),
            }
        )
    return pd.DataFrame(rows)


def _summary_table(df: pd.DataFrame, group_col: str, outcome_col: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    g = df.groupby([group_col, outcome_col], dropna=False).size().reset_index(name="count")
    totals = df.groupby(group_col).size().rename("total")
    g = g.merge(totals, on=group_col, how="left")
    g["pct"] = (g["count"] / g["total"] * 100.0).round(1)
    return g.sort_values([group_col, outcome_col])


def main() -> None:
    ap = argparse.ArgumentParser(description="EXT pozice — trend po uzavření")
    ap.add_argument("--csv", type=Path, default=ROOT / "data" / "EURUSD.x_M30.csv")
    ap.add_argument("--date-from", default="2026-03-03")
    ap.add_argument("--date-to", default="2026-05-10")
    ap.add_argument("--tp-mode", default="wave_target_n_g")
    ap.add_argument("--profile", default="EXAMPLE")
    ap.add_argument(
        "--output",
        type=Path,
        default=ROOT / "results" / "audit_ext_post_exit_trend",
    )
    args = ap.parse_args()

    cfg = _load_cfg(tp_mode=args.tp_mode, profile=args.profile)
    df = load_csv(args.csv)
    df = filter_by_date_range(df, args.date_from, args.date_to)

    eng = BacktestEngine(cfg)
    trades = eng.run(df)
    audit = analyze_ext_trades(trades, df, cfg)

    out_dir = args.output
    out_dir.mkdir(parents=True, exist_ok=True)
    audit.to_csv(out_dir / "ext_trades_detail.csv", index=False)

    bos_sum = _summary_table(audit, "ext_subtype", "bos_outcome_after_close")
    price_sum = _summary_table(audit, "ext_subtype", "price_outcome_after_close")
    bos_sum.to_csv(out_dir / "summary_bos_after_close.csv", index=False)
    price_sum.to_csv(out_dir / "summary_price_after_close.csv", index=False)

    total = len(audit)
    pok = int((audit["bos_outcome_after_close"] == "TREND_POKRACOVAL").sum())
    obr = int((audit["bos_outcome_after_close"] == "TREND_OBRATIL").sum())
    neu = total - pok - obr

    print(f"Obdobi: {args.date_from} .. {args.date_to}  |  tp_mode={args.tp_mode}")
    print(f"EXT-related pozic celkem: {total}")
    print()
    print("=== BOS trend po uzavreni EXT pozice (do dalsiho flipu / konce segmentu) ===")
    if total:
        print(f"  TREND_POKRACOVAL: {pok}  ({100*pok/total:.1f}%)")
        print(f"  TREND_OBRATIL:    {obr}  ({100*obr/total:.1f}%)")
        if neu:
            print(f"  NEUTRAL/unknown:  {neu}  ({100*neu/total:.1f}%)")
        med = audit.loc[
            audit["bars_to_bos_flip"].notna(), "bars_to_bos_flip"
        ].median()
        if pd.notna(med):
            print(f"  Medián barů do BOS flipu (kde flip nastal): {med:.0f}")
    print()
    print("=== Podle typu EXT pozice (BOS) ===")
    for subtype in sorted(audit["ext_subtype"].unique()):
        sub = audit[audit["ext_subtype"] == subtype]
        n = len(sub)
        p = int((sub["bos_outcome_after_close"] == "TREND_POKRACOVAL").sum())
        o = int((sub["bos_outcome_after_close"] == "TREND_OBRATIL").sum())
        print(
            f"  {subtype:20s}  n={n:3d}  pokracoval={p:3d} ({100*p/n if n else 0:.0f}%)  "
            f"obratil={o:3d} ({100*o/n if n else 0:.0f}%)"
        )
    print()
    print("=== Cena po exitu ve smeru pozice vs proti ===")
    if total:
        for label in ("CENA_POKRACOVALA", "CENA_OBRATILA", "NEUTRAL"):
            c = int((audit["price_outcome_after_close"] == label).sum())
            print(f"  {label:18s}  {c:3d}  ({100*c/total:.1f}%)")
    print()
    print(f"Vystup: {out_dir}")


if __name__ == "__main__":
    main()
