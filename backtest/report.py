"""
Reporty backtestu - pretty print do konzole + ulozeni do CSV.
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from backtest.file_stems import export_path_stem, prefixed_export_stem
from backtest.io.csv_export import append_csv_row, export_csv

    # Tisk výsledků do konzole
def print_summary(bot_name: str, stats: dict, trades_df: pd.DataFrame) -> None:
    print(f"\n{'='*60}")
    print(f"  BACKTEST VYSLEDKY: {bot_name}")
    print(f"{'='*60}")
    if "error" in stats:
        print(f"  {stats['error']}")
        return
    print(f"  Celkem obchodu  : {stats['total_trades']}")
    print(f"  Vyhry / Prohry  : {stats['wins']} / {stats['losses']}")
    print(f"  Win rate        : {stats['win_rate_pct']} %")
    print(f"  Net PnL (WAVE)  : {stats.get('net_pnl_wave_usd', 0.0):+.2f} USD")
    print(f"  Net PnL (PP)    : {stats.get('net_pnl_pp_usd', 0.0):+.2f} USD")
    print(f"  Net PnL (BOS)   : {stats.get('net_pnl_bos_usd', 0.0):+.2f} USD")
    print(f"  Net PnL (WAVE+BOS, dříve ostatní): {stats.get('net_pnl_non_pp_usd', stats['net_pnl_usd']):+.2f} USD")
    print(f"  Net PnL (celkem): {stats['net_pnl_usd']:+.2f} USD")
    print(f"  Gross profit    : {stats['gross_profit_usd']:.2f} USD")
    print(f"  Gross loss      : {stats['gross_loss_usd']:.2f} USD")
    print(f"  Profit factor   : {stats['profit_factor']}")
    print(f"  Avg win         : {stats['avg_win_usd']:.2f} USD")
    print(f"  Avg loss        : {stats['avg_loss_usd']:.2f} USD")
    ddp = stats.get("max_drawdown_pct_vs_peak", stats["max_drawdown_pct"])
    print(
        "  Max DD % vs init: celkem "
        f"{stats['max_drawdown_pct']:.1f}% | WAVE {stats.get('max_drawdown_pct_wave', 0):.1f}% | "
        f"PP {stats.get('max_drawdown_pct_pp', 0):.1f}% | BOS {stats.get('max_drawdown_pct_bos', 0):.1f}%"
    )
    print(
        "  Max DD          : "
        f"{stats['max_drawdown_usd']:.2f} USD "
        f"({ddp:.1f}% vs peak, "
        f"{stats['max_drawdown_pct']:.1f}% vs initial celkem)"
    )
    print(f"  Max Daily DD    : {stats.get('max_daily_dd_pct', 0.0):.2f}% ({stats.get('max_daily_dd_date', 'N/A')})")
    print("  DD % (vs init) : stejna baze jako max_dd_%_vs_initial v CSV (prop-style).")
    print(f"  Sharpe ratio    : {stats['sharpe_ratio']:.2f}")
    print(f"  Uzavreni duvod  : {stats['close_by_reason']}")
    if "waves_detected_total" in stats:
        print(
            "  Vlny (det/birth/ok): "
            f"{stats.get('waves_detected_total', 0)} / "
            f"{stats.get('waves_birth_total', 0)} / "
            f"{stats.get('waves_accepted', 0)}"
        )
        print(
            "  Vlny odfiltrovane : "
            f"old={stats.get('waves_skipped_too_old', 0)}, "
            f"session={stats.get('waves_skipped_session', 0)}, "
            f"wave_max_pct={stats.get('waves_skipped_wave_max_pct', 0)}, "
            f"dup={stats.get('waves_skipped_duplicate', 0)}"
        )
        print(
            "  Vytvorene ordery  : "
            f"pending={stats.get('orders_created_pending', 0)}, "
            f"market={stats.get('orders_created_market', 0)}"
        )

    if "max_concurrent" in stats:
        mc = stats["max_concurrent"]
        mcc = stats["max_concurrent_count"]
        smc = stats["second_max_concurrent"]
        smcc = stats["second_max_concurrent_count"]
        print(f"  Max poz. zaroven: {mc} ({mcc}x) / 2nd: {smc} ({smcc}x)")

    print(f"{'=' * 60}\n")

    # Výtisk posledních N uzavřenych obchodu
def print_last_trades(trades_df: pd.DataFrame, n: int = 10) -> None:
    if trades_df.empty:
        print("Zadne uzavrene obchody k zobrazeni.")
        return
    last = trades_df.tail(n).copy()
    print(f"\n{'='*100}")
    print(f"  POSLEDNICH {n} UZAVRENYCH POZIC")
    print(f"{'='*100}")
    cols = ["wave_time", "dir", "entry_type", "position_kind", "entry_time", "close_time",
            "entry_price", "sl", "tp", "close_price", "lot", "close_reason", "pnl_usd"]
    cols = [c for c in cols if c in last.columns]
    print(last[cols].to_string(index=False))
    print(f"{'='*100}\n")

    # Ulozeni trades DataFrame — XLSX (CSV jen kdyz openpyxl neni k dispozici)
def save_trades_csv(
    trades_df: pd.DataFrame,
    output_dir: str,
    bot_name: str,
    *,
    test_pozice: int | None = None,
) -> str:
    os.makedirs(output_dir, exist_ok=True)
    stem = prefixed_export_stem(export_path_stem(bot_name), test_pozice)
    base_path = os.path.join(output_dir, f"{stem}_trades")
    csv_path = base_path + ".csv"
    xlsx_path = base_path + ".xlsx"
    out_df = trades_df.copy()
    if test_pozice is not None:
        out_df.insert(0, "combo_no", int(test_pozice))
        out_df.insert(1, "bot_name", bot_name)
    try:
        from backtest.io.csv_export import _prepare_df_for_export, round_pnl_columns

        _prepare_df_for_export(round_pnl_columns(out_df)).to_excel(
            xlsx_path, index=False, engine="openpyxl"
        )
        print(f"  Obchody ulozeny (XLSX): {xlsx_path}")
        return xlsx_path
    except ImportError:
        print("  XLSX preskocen — nainstaluj openpyxl (pip install openpyxl), ukladam CSV")
    except Exception as exc:
        print(f"  XLSX export selhal: {exc} — ukladam CSV")
    export_csv(out_df, csv_path, index=False)
    print(f"  Obchody ulozeny (CSV): {csv_path}")
    return csv_path


def append_grid_plot_trades_index(
    grid_output_dir: Path | str,
    bot_name: str,
    trades_csv_basename: str,
    price_png_basename: str,
    *,
    test_pozice: int | None = None,
    price_html_basename: str = "",
) -> None:
    """Mapuje plny bot_name na kratke nazvy souboru (grid --plot-trades na Windows)."""
    root = Path(grid_output_dir)
    idx = root / "grid_plot_trades_index.csv"
    append_csv_row(
        idx,
        [
            int(test_pozice) if test_pozice is not None else "",
            bot_name,
            trades_csv_basename,
            price_png_basename,
            price_html_basename or "",
        ],
        header=["combo_no", "bot_name", "trades_csv", "price_trades_png", "price_trades_html"],
    )

def wf_origin_breakdown_html(trades_df: pd.DataFrame) -> str:
    """
    Vrátí HTML tabulku s breakdownem metrik podle wave_origin
    (normal / wf_continuation). Přidává se do HTML backtest reportu
    pokud jsou v datech WF obchody.

    Sloupce: wave_origin, trades, win_rate_%, net_pnl_usd,
             profit_factor, avg_win_usd, avg_loss_usd, max_dd_pct_vs_init
    """
    if trades_df is None or trades_df.empty:
        return ""
    if "wave_origin" not in trades_df.columns:
        return ""
    groups = trades_df.groupby("wave_origin")
    if len(groups) < 2 and "wf_continuation" not in trades_df["wave_origin"].values:
        return ""

    rows_html = []
    for origin, grp in groups:
        wins = grp[grp["pnl_usd"] > 0]
        losses = grp[grp["pnl_usd"] <= 0]
        n = len(grp)
        win_rate = round(len(wins) / n * 100, 1) if n > 0 else 0.0
        net_pnl = round(grp["pnl_usd"].sum(), 2)
        gross_profit = wins["pnl_usd"].sum()
        gross_loss = abs(losses["pnl_usd"].sum())
        pf = (
            round(gross_profit / gross_loss, 2) if gross_loss > 0 else ("∞" if gross_profit > 0 else "N/A")
        )
        avg_win = round(wins["pnl_usd"].mean(), 2) if not wins.empty else 0.0
        avg_loss = round(losses["pnl_usd"].mean(), 2) if not losses.empty else 0.0
        rows_html.append(
            f"<tr>"
            f"<td><b>{origin}</b></td>"
            f"<td>{n}</td>"
            f"<td>{win_rate}%</td>"
            f"<td>{net_pnl:+.2f}</td>"
            f"<td>{pf}</td>"
            f"<td>{avg_win:.2f}</td>"
            f"<td>{avg_loss:.2f}</td>"
            f"</tr>"
        )

    if not rows_html:
        return ""

    html = (
        "<h3>Breakdown podle wave_origin (Wick Fakeout Recovery)</h3>"
        "<table border='1' cellpadding='4' cellspacing='0' style='border-collapse:collapse'>"
        "<tr style='background:#dde'>"
        "<th>wave_origin</th><th>Obchody</th><th>Win rate</th>"
        "<th>Net PnL (USD)</th><th>Profit Factor</th>"
        "<th>Avg Win</th><th>Avg Loss</th>"
        "</tr>"
        + "".join(rows_html)
        + "</table>"
    )
    return html


    # Porovnání výsledků konfigurací
def compare_configs(results: dict) -> pd.DataFrame:
    rows = []
    for name, s in results.items():
        if "error" in s:
            continue
        rows.append({
            "bot_name":      name,
            "trades":        s["total_trades"],
            "win_rate_%":    s["win_rate_pct"],
            "net_pnl_wave_usd": s.get("net_pnl_wave_usd", 0.0),
            "net_pnl_pp_usd": s.get("net_pnl_pp_usd", 0.0),
            "net_pnl_bos_usd": s.get("net_pnl_bos_usd", 0.0),
            "net_pnl_non_pp_usd": s.get("net_pnl_non_pp_usd", s["net_pnl_usd"]),
            "net_pnl_usd":   s["net_pnl_usd"],
            "profit_factor": s["profit_factor"],
            "max_dd_usd":    s["max_drawdown_usd"],
            "max_dd_%":      s.get("max_drawdown_pct_vs_peak", s["max_drawdown_pct"]),
            "max_dd_%_vs_initial": s["max_drawdown_pct"],
            "max_dd_%_vs_initial_wave": s.get("max_drawdown_pct_wave", 0.0),
            "max_dd_%_vs_initial_pp": s.get("max_drawdown_pct_pp", 0.0),
            "max_dd_%_vs_initial_bos": s.get("max_drawdown_pct_bos", 0.0),
            "max_daily_dd_%": s.get("max_daily_dd_pct", 0.0),
            "max_pos_open":  s.get("max_concurrent", 0),
            "max_pos_open_count": s.get("max_concurrent_count", 0),
            "second_max_pos_open": s.get("second_max_concurrent", 0),
            "second_max_pos_open_count": s.get("second_max_concurrent_count", 0),
            "sharpe":        s["sharpe_ratio"],
        })
    return pd.DataFrame(rows).set_index("bot_name") if rows else pd.DataFrame()
