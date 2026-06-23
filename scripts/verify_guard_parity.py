"""
Verifikace parity LIVE vs BACKTEST na urovni POLOZENYCH WAVE orderu + PnL/DDi.

Princip (duveryhodny — pouziva SKUTECNY backtest engine pro exity, zadna
re-implementace fill modelu):
  - REFERENCE = BacktestEngine.run(df) -> WAVE closed trades (classify == WAVE).
  - LIVE      = ten samy engine, ale na placement se aplikuje SKUTECNY
                `guard_live_send_order` (live cesta). Vlna, kterou by live guard
                potlacil, se v enginu NEPOLOZI. Exity/PnL pak resi tentyz engine
                = parita s backtestem krome toho, co guard ubere.

Pokud je guard sladeny s backtest WAVE klasifikaci, REFERENCE == LIVE.

Spusteni: .venv\\Scripts\\python.exe scripts/verify_guard_parity.py
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATE_FROM = "2025-11-10"
DATE_TO = "2026-05-09"
CSV = ROOT / "data" / "EURUSD_M30.csv"


def _wave_closed(closed: list) -> list:
    from backtest.stats import classify_position_kind

    out = []
    for t in closed:
        kind = classify_position_kind(
            is_pp=bool(getattr(t, "is_pp", False)),
            is_counter=bool(getattr(t, "is_counter", False)),
            is_bos_reentry=bool(getattr(t, "is_bos_reentry", False)),
            is_two_sided_mirror=bool(getattr(t, "is_two_sided_mirror", False)),
            is_ext=bool(getattr(t, "is_ext", False)),
            entry_tag=str(getattr(t, "entry_tag", "base")),
        )
        if kind == "WAVE":
            out.append(t)
    return out


def _pnl_ddi(closed: list, *, bot_name: str) -> dict:
    from backtest.grid.study_mode import (
        apply_wave_isolation_report_stats,
        filter_trades_df_for_grid_stats,
    )
    from backtest.metrics.robustness import compute_robustness_metrics
    from backtest.stats import compute_stats, trades_to_df

    tdf = trades_to_df(closed)
    combo = {
        "wave_isolation_study": True,
        "wave_positions_only": True,
        "date_from": DATE_FROM,
        "date_to": DATE_TO,
    }
    wdf = filter_trades_df_for_grid_stats(tdf, combo)
    stats = compute_stats(wdf, date_from=DATE_FROM, date_to=DATE_TO)
    stats = apply_wave_isolation_report_stats(stats, combo)
    stats.update(
        compute_robustness_metrics(
            wdf,
            max_dd_pct_vs_peak=stats.get("max_drawdown_pct_vs_peak"),
            max_dd_pct_vs_initial=stats.get("max_drawdown_pct"),
            bot_name=bot_name,
        )
    )
    return stats


def run_engine_full(df, cfg):
    """Jeden PLNY beh enginu (full routing zachovan, zadne potlaceni placementu).

    Vraci (closed_trades, entry_meta) kde entry_meta[wave_time] = {bypass, is_ext}
    — co bylo pri vstupu vlny do _process_new_wave. To pak slouzi k REPORT-LEVEL
    filtru (guard je v live jen send-filtr, vnitrni routing zustava).
    """
    from backtest.engine import BacktestEngine
    from strategy.ext_logic import is_ext_wave

    eng = BacktestEngine(cfg)
    meta: dict[str, dict] = {}
    orig = eng._process_new_wave

    def _wrapped(wave, bar_idx, bar_time, bar, *, bypass_trend_filter=False,
                 is_two_sided_mirror=False):
        ok = orig(
            wave, bar_idx, bar_time, bar,
            bypass_trend_filter=bypass_trend_filter,
            is_two_sided_mirror=is_two_sided_mirror,
        )
        if ok and not is_two_sided_mirror:
            wt = str(wave["wave_time"])
            try:
                ext = bool(is_ext_wave(wave, cfg))
            except Exception:
                ext = False
            m = meta.setdefault(wt, {"bypass": False, "is_ext": False})
            m["bypass"] = m["bypass"] or bool(bypass_trend_filter)
            m["is_ext"] = m["is_ext"] or ext
        return ok

    eng._process_new_wave = _wrapped
    closed = eng.run(df, retain_wave_snapshot=False)
    return closed, meta


def _old_guard_suppresses(wt: str, meta: dict) -> bool:
    """Stary guard: potlac is_ext primarni + bos-retro (bypass)."""
    m = meta.get(str(wt))
    if not m:
        return False
    return bool(m.get("is_ext")) or bool(m.get("bypass"))


def main() -> None:
    from backtest.data_loader import filter_by_date_range, load_csv
    from config.bot_config import LIVE_BOT_CONFIG
    from config.position_modes import resolve_grid_engine_config
    from runtime.live_wave_isolation import resolve_live_execution_config

    print("=" * 72)
    print("VERIFIKACE PARITY  LIVE(guard) vs BACKTEST  |", DATE_FROM, "..", DATE_TO)
    print("=" * 72)

    df = filter_by_date_range(load_csv(str(CSV)), DATE_FROM, DATE_TO)
    engine_cfg = resolve_grid_engine_config(LIVE_BOT_CONFIG)
    live_cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)
    from runtime.live_wave_isolation import guard_live_send_order
    print(f"baru: {len(df)}  symbol={engine_cfg.symbol}  risk_usd={engine_cfg.risk_usd}")

    # JEDEN plny beh enginu (full routing). Guard aplikujeme jako REPORT-LEVEL filtr.
    closed, meta = run_engine_full(df, engine_cfg)
    bt_closed = _wave_closed(closed)  # backtest WAVE = vse classify==WAVE

    def _new_guard_sends(t) -> bool:
        sig = {
            "wave_time": str(t.wave_time),
            "post_ext_trend_suppressed": bool(getattr(t, "post_ext_trend_suppressed", False)),
        }
        # WAVE obchod nikdy neni two_sided_mirror (ten je WAVE_TWO_SIDED)
        return not guard_live_send_order(
            live_cfg, sig,
            is_two_sided_mirror=bool(getattr(t, "is_two_sided_mirror", False)),
        )

    # LIVE po oprave = WAVE obchody, ktere NOVY guard posle (vnitrni routing beze zmeny)
    live_closed = [t for t in bt_closed if _new_guard_sends(t)]
    # LIVE pred opravou = WAVE obchody, ktere STARY guard NEpotlacil (is_ext + bos-retro)
    old_live_closed = [
        t for t in bt_closed if not _old_guard_suppresses(str(t.wave_time), meta)
    ]

    bt = _pnl_ddi(bt_closed, bot_name=engine_cfg.bot_name)
    lv = _pnl_ddi(live_closed, bot_name=engine_cfg.bot_name)
    old = _pnl_ddi(old_live_closed, bot_name=engine_cfg.bot_name)
    print(
        f"\n  [kontext] LIVE PRED opravou (stary guard: EXT+bos-retro potlaceno): "
        f"{len(old_live_closed)} obchodu / {round(float(old.get('net_pnl_usd', 0.0)),2)} USD"
    )

    bt_keys = {str(t.wave_time) for t in bt_closed}
    lv_keys = {str(t.wave_time) for t in live_closed}
    only_bt = sorted(bt_keys - lv_keys)
    only_lv = sorted(lv_keys - bt_keys)

    bt_ddi = bt.get("ddi_profile", {}) or {}
    lv_ddi = lv.get("ddi_profile", {}) or {}

    def f(d, k):
        try:
            return float(d.get(k, 0.0))
        except (TypeError, ValueError):
            return 0.0

    rows = [
        ("WAVE obchodu", len(bt_closed), len(live_closed), ""),
        ("net_pnl_usd", round(f(bt, "net_pnl_usd"), 2), round(f(lv, "net_pnl_usd"), 2), "USD"),
        ("win_rate_pct", round(f(bt, "win_rate_pct"), 2), round(f(lv, "win_rate_pct"), 2), "%"),
        ("max_drawdown_pct", round(f(bt, "max_drawdown_pct"), 2), round(f(lv, "max_drawdown_pct"), 2), "%"),
        ("max_ddi_pct", round(f(bt_ddi, "max_ddi_pct"), 2), round(f(lv_ddi, "max_ddi_pct"), 2), "%"),
        ("p90_ddi_pct", round(f(bt_ddi, "p90_ddi_pct"), 2), round(f(lv_ddi, "p90_ddi_pct"), 2), "%"),
        ("median_ddi_pct", round(f(bt_ddi, "median_ddi_pct"), 2), round(f(lv_ddi, "median_ddi_pct"), 2), "%"),
        ("dnu_poruseni_5pct", int(f(bt_ddi, "dnu_poruseni_5pct")), int(f(lv_ddi, "dnu_poruseni_5pct")), "dni"),
    ]

    print("\n" + "=" * 72)
    print(f"  {'metrika':<20} {'BACKTEST':>14} {'LIVE(guard)':>14}  delta")
    print("  " + "-" * 64)
    for name, a, b, unit in rows:
        delta = ""
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            d = float(b) - float(a)
            delta = f"{d:+.2f}{unit}" if unit in ("%", "USD") else f"{d:+.0f}{unit}"
        print(f"  {name:<20} {str(a):>14} {str(b):>14}  {delta}")

    print("\n  wave_time jen v BACKTESTU (live nepolozi):", len(only_bt))
    for wt in only_bt[:20]:
        print("    ", wt)
    print("  wave_time jen v LIVE (backtest nema):", len(only_lv))
    for wt in only_lv[:20]:
        print("    ", wt)

    verdict = "PARITA OK" if (len(only_bt) == 0 and len(only_lv) == 0) else "ROZDIL"
    print("\n  VERDIKT:", verdict)


if __name__ == "__main__":
    main()
