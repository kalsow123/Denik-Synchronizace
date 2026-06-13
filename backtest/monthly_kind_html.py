"""
Měsíční přehled PnL a max_dd_%_vs_initial podle druhu pozice (WAVE / PP / BOS) + celkem.

Výstup: jeden Plotly HTML (sloupce PnL, sloupce max_dd_%_vs_initial, tabulka čísel).
Používá stejnou klasifikaci a výpočet DD jako `backtest.stats` (`_max_dd_pct_vs_initial` = sloupec
`max_drawdown_pct` / CSV `max_dd_%_vs_initial` v souhrnném reportu).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

from backtest.stats import _max_dd_pct_vs_initial, classify_position_kind
from backtest.plotting import _write_plotly_html_fullsize, BROKER_PROJECTED_LINE_COLORS

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
except Exception:  # pragma: no cover
    go = None
    make_subplots = None


def _ensure_position_kind(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "position_kind" in out.columns:
        return out
    if "is_pp" not in out.columns:
        out["position_kind"] = "WAVE"
        return out
    _pp = out["is_pp"].astype(bool)
    _ctr = out["is_counter"].astype(bool) if "is_counter" in out.columns else pd.Series(
        False, index=out.index
    )
    _bre = (
        out["is_bos_reentry"].astype(bool)
        if "is_bos_reentry" in out.columns
        else pd.Series(False, index=out.index)
    )
    _tsm = (
        out["is_two_sided_mirror"].astype(bool)
        if "is_two_sided_mirror" in out.columns
        else pd.Series(False, index=out.index)
    )
    out["position_kind"] = [
        classify_position_kind(
            is_pp=a, is_counter=b, is_bos_reentry=c, is_two_sided_mirror=f
        )
        for a, b, c, f in zip(_pp, _ctr, _bre, _tsm)
    ]
    return out


def prepare_trades_for_monthly(df: pd.DataFrame) -> pd.DataFrame:
    """Vyhodí END_OF_DATA, doplní position_kind, seřadí podle close_time."""
    if df is None or df.empty:
        return pd.DataFrame()
    out = df[df["close_reason"] != "END_OF_DATA"].copy()
    if out.empty:
        out = df.copy()
    out = _ensure_position_kind(out)
    out["close_time"] = pd.to_datetime(out["close_time"])
    return out.sort_values("close_time", kind="mergesort").reset_index(drop=True)


def build_monthly_kind_metrics(
    trades_df: pd.DataFrame,
    *,
    initial_balance: float = 100_000.0,
) -> pd.DataFrame:
    """
    Pro každý kalendářní měsíc (podle close_time) vrátí řádky:
      month, pnl_ALL, max_dd_pct_ALL, pnl_WAVE, max_dd_pct_WAVE, … PP, BOS.
    """
    df = prepare_trades_for_monthly(trades_df)
    if df.empty:
        return pd.DataFrame()

    df["_ym"] = df["close_time"].dt.to_period("M")
    months = sorted(df["_ym"].unique())
    init = float(initial_balance)
    rows = []
    for per in months:
        mdf = df[df["_ym"] == per]
        month_str = str(per)
        row: dict = {"month": month_str}
        for label, sub in (
            ("ALL", mdf),
            ("WAVE", mdf[mdf["position_kind"] == "WAVE"]),
            ("PP", mdf[mdf["position_kind"] == "PP"]),
            ("BOS", mdf[mdf["position_kind"] == "BOS"]),
        ):
            if sub.empty:
                row[f"pnl_{label}"] = 0.0
                row[f"max_dd_pct_{label}"] = 0.0
            else:
                row[f"pnl_{label}"] = round(float(sub["pnl_usd"].astype(float).sum()), 2)
                row[f"max_dd_pct_{label}"] = _max_dd_pct_vs_initial(sub, init)
        rows.append(row)
    out = pd.DataFrame(rows)
    return out


def _period_kind_subsets(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Celé testovací období (po prepare_trades_for_monthly) — výřezy podle druhu."""
    return {
        "ALL": df,
        "WAVE": df[df["position_kind"] == "WAVE"],
        "PP": df[df["position_kind"] == "PP"],
        "BOS": df[df["position_kind"] == "BOS"],
    }


def compute_monthly_html_totals(
    trades_df: pd.DataFrame,
    tbl: pd.DataFrame,
    *,
    initial_balance: float,
    kinds: tuple[str, ...] = ("ALL", "WAVE", "PP", "BOS"),
) -> tuple[dict[str, float], dict[str, float]]:
    """
    Řádek „Celkem“ pro HTML: PnL = součet měsíčních hodnot v tabulce;
    max_dd_%_vs_initial = jedna hodnota za celé období (ne součet měsíců).
    """
    pnl_sum = {k: round(float(tbl[f"pnl_{k}"].sum()), 2) for k in kinds}

    df = prepare_trades_for_monthly(trades_df)
    init = float(initial_balance)
    subs = _period_kind_subsets(df)
    dd_period: dict[str, float] = {}
    for k in kinds:
        sub = subs[k]
        if sub.empty:
            dd_period[k] = 0.0
        else:
            dd_period[k] = _max_dd_pct_vs_initial(sub, init)
    return pnl_sum, dd_period


def build_monthly_kind_figure(
    trades_df: pd.DataFrame,
    *,
    symbol: str,
    bot_name: str,
    initial_balance: float = 100_000.0,
    pnl_variant_label: str = "základní PnL",
):
    """
    Plotly figura měsíčního PnL + max_dd podle druhu (stejná jako write_monthly_kind_summary_html).
    Vrací None při prázdných datech nebo bez Plotly.
    """
    if go is None or make_subplots is None:
        return None

    tbl = build_monthly_kind_metrics(trades_df, initial_balance=initial_balance)
    if tbl.empty:
        return None

    months = tbl["month"].tolist()
    kinds = ("ALL", "WAVE", "PP", "BOS")
    total_label = "Celkem"
    pnl_total, dd_total = compute_monthly_html_totals(
        trades_df, tbl, initial_balance=initial_balance, kinds=kinds
    )
    x_labels = months + [total_label]

    colors = {"ALL": "#1976d2", "WAVE": "#9e9e9e", "PP": "#2e7d32", "BOS": "#000000"}

    fig = make_subplots(
        rows=3,
        cols=1,
        row_heights=[0.36, 0.36, 0.28],
        subplot_titles=(
            f"Měsíční PnL (USD) + {total_label} — {symbol} ({pnl_variant_label})",
            f"max_dd_%_vs_initial + {total_label} ({pnl_variant_label})",
            f"Tabulka hodnot — {pnl_variant_label}",
        ),
        specs=[[{"type": "bar"}], [{"type": "bar"}], [{"type": "table"}]],
        vertical_spacing=0.10,
    )

    for k in kinds:
        y_pnl = list(tbl[f"pnl_{k}"]) + [pnl_total[k]]
        fig.add_trace(
            go.Bar(
                name=k,
                x=x_labels,
                y=y_pnl,
                marker_color=colors[k],
                legendgroup=k,
                showlegend=True,
            ),
            row=1,
            col=1,
        )
    for k in kinds:
        y_dd = list(tbl[f"max_dd_pct_{k}"]) + [dd_total[k]]
        fig.add_trace(
            go.Bar(
                name=f"max_dd_%_vs_initial {k}",
                x=x_labels,
                y=y_dd,
                marker_color=colors[k],
                legendgroup=k,
                showlegend=False,
            ),
            row=2,
            col=1,
        )

    header = ["Měsíc"]
    for k in kinds:
        header.append(f"PnL {k} (USD)")
        header.append(f"max_dd_%_vs_initial {k} (%)")
    month_rows = tbl["month"].tolist()
    row_labels = month_rows + [total_label]
    cells = [row_labels]
    n_rows = len(row_labels)
    row_fill = ["#ffffff"] * (n_rows - 1) + ["#e8eaf6"]
    fill_by_col = [row_fill for _ in range(len(header))]
    for k in kinds:
        cells.append([f"{v:.2f}" for v in tbl[f"pnl_{k}"]] + [f"{pnl_total[k]:.2f}"])
        cells.append([f"{v:.2f}" for v in tbl[f"max_dd_pct_{k}"]] + [f"{dd_total[k]:.2f}"])

    fig.add_trace(
        go.Table(
            header=dict(values=header, fill_color="#eceff1", align="left", font=dict(size=12)),
            cells=dict(
                values=cells,
                align="left",
                font=dict(size=11),
                fill=dict(color=fill_by_col),
            ),
        ),
        row=3,
        col=1,
    )

    fig.update_xaxes(type="category", row=1, col=1)
    fig.update_xaxes(type="category", row=2, col=1)
    fig.update_yaxes(title_text="USD", row=1, col=1)
    fig.update_yaxes(title_text="%", row=2, col=1)

    title = (
        f"Měsíční výsledky podle druhu pozice | {symbol} | {bot_name} | {pnl_variant_label}<br>"
        f"<sup>ALL = všechny obchody; WAVE = klasické fib; PP; BOS = counter + BOS re-entry. "
        f"max_dd_%_vs_initial = stejný výpočet jako v grid_report / trades CSV "
        f"(`_max_dd_pct_vs_initial`), báze initial_balance = {initial_balance:g} USD. "
        f"Sloupec / řádek <b>{total_label}</b>: PnL = součet měsíčních hodnot v daném sloupci; "
        f"max_dd_%_vs_initial = <b>za celé testovací období</b> (sjednocená křivka z obchodů daného druhu — "
        f"není aritmetický součet měsíčních max DD).</sup>"
    )
    fig.update_layout(
        title=dict(text=title, x=0.02, xanchor="left"),
        barmode="group",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def _hex_to_rgba(hex_c: str, alpha: float) -> str:
    h = hex_c.strip().lstrip("#")
    if len(h) != 6:
        return hex_c
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


_KIND_ALPHA_RGB = {"ALL": 1.0, "WAVE": 0.9, "PP": 0.78, "BOS": 0.66}


def build_monthly_kind_multi_broker_projected_figure(
    trades_df_base: pd.DataFrame,
    *,
    broker_scaled_trades: dict[str, pd.DataFrame],
    broker_order: Sequence[str],
    symbol: str,
    bot_name: str,
    initial_balance: float = 100_000.0,
    ftmo_caption: str = "",
) -> Optional[object]:
    """
    Sekce 4b — měsíční PnL + max DD podle druhu: základní + projected zvlášť pro každého brokera
    (barvy shodné se scroll sekcí 2). Tabulka dole = pouze základní (stejná logika jako 4a).
    """
    if go is None or make_subplots is None:
        return None
    order = [str(b) for b in broker_order if str(b).strip() and b in broker_scaled_trades]
    if not order:
        return None

    tbl_base = build_monthly_kind_metrics(trades_df_base, initial_balance=initial_balance)
    if tbl_base.empty:
        return None

    tbl_brokers: dict[str, pd.DataFrame] = {}
    for b in order:
        t = build_monthly_kind_metrics(broker_scaled_trades[b], initial_balance=initial_balance)
        tbl_brokers[b] = t if not t.empty else pd.DataFrame()

    months_set: set[str] = set(tbl_base["month"].astype(str))
    for t in tbl_brokers.values():
        if not t.empty and "month" in t.columns:
            months_set |= set(t["month"].astype(str))
    months = sorted(months_set)

    kinds = ("ALL", "WAVE", "PP", "BOS")
    base_kind_colors = {"ALL": "#1976d2", "WAVE": "#9e9e9e", "PP": "#2e7d32", "BOS": "#000000"}

    def _align(tbl: pd.DataFrame) -> pd.DataFrame:
        z = pd.DataFrame({"month": months})
        if tbl.empty:
            for k in kinds:
                z[f"pnl_{k}"] = 0.0
                z[f"max_dd_pct_{k}"] = 0.0
            return z
        keyed = tbl.copy()
        keyed["month"] = keyed["month"].astype(str)
        return keyed.set_index("month").reindex(months).fillna(0.0).reset_index()

    ab = _align(tbl_base)
    ab_brok = {b: _align(tbl_brokers[b]) for b in order}

    pnl_tot_b: dict[str, dict[str, float]] = {}
    dd_tot_b: dict[str, dict[str, float]] = {}
    pnl_tot0, dd_tot0 = compute_monthly_html_totals(
        trades_df_base, tbl_base, initial_balance=initial_balance, kinds=kinds
    )
    for b in order:
        pnl_t, dd_t = compute_monthly_html_totals(
            broker_scaled_trades[b],
            ab_brok[b],
            initial_balance=initial_balance,
            kinds=kinds,
        )
        pnl_tot_b[b] = pnl_t
        dd_tot_b[b] = dd_t

    total_label = "Celkem"
    x_labels = months + [total_label]

    fig = make_subplots(
        rows=3,
        cols=1,
        row_heights=[0.36, 0.36, 0.28],
        subplot_titles=(
            f"Měsíční PnL (USD) — základní + projected po brokerovi | {symbol}",
            f"max_dd_%_vs_initial — základní + projected po brokerovi ({symbol})",
            "Tabulka — pouze základní PnL (projected jen v grafech výše)",
        ),
        specs=[[{"type": "bar"}], [{"type": "bar"}], [{"type": "table"}]],
        vertical_spacing=0.10,
    )

    for k in kinds:
        y_pnl_z = list(ab[f"pnl_{k}"]) + [pnl_tot0[k]]
        fig.add_trace(
            go.Bar(
                name=f"základní {k}",
                x=x_labels,
                y=y_pnl_z,
                marker_color=base_kind_colors[k],
                legendgroup=f"base_{k}",
                showlegend=True,
            ),
            row=1,
            col=1,
        )
        for bi, brok in enumerate(order):
            y_b = list(ab_brok[brok][f"pnl_{k}"]) + [pnl_tot_b[brok][k]]
            c = BROKER_PROJECTED_LINE_COLORS[bi % len(BROKER_PROJECTED_LINE_COLORS)]
            mc = _hex_to_rgba(c, _KIND_ALPHA_RGB[k])
            fig.add_trace(
                go.Bar(
                    name=f"{brok} {k}",
                    x=x_labels,
                    y=y_b,
                    marker_color=mc,
                    legendgroup=f"{brok}_{k}",
                    showlegend=True,
                ),
                row=1,
                col=1,
            )

    for k in kinds:
        y_dd_z = list(ab[f"max_dd_pct_{k}"]) + [dd_tot0[k]]
        fig.add_trace(
            go.Bar(
                name=f"max_dd základní {k}",
                x=x_labels,
                y=y_dd_z,
                marker_color=base_kind_colors[k],
                legendgroup=f"ddb_{k}",
                showlegend=False,
            ),
            row=2,
            col=1,
        )
        for bi, brok in enumerate(order):
            y_dd = list(ab_brok[brok][f"max_dd_pct_{k}"]) + [dd_tot_b[brok][k]]
            c = BROKER_PROJECTED_LINE_COLORS[bi % len(BROKER_PROJECTED_LINE_COLORS)]
            mc = _hex_to_rgba(c, _KIND_ALPHA_RGB[k])
            fig.add_trace(
                go.Bar(
                    name=f"max_dd {brok} {k}",
                    x=x_labels,
                    y=y_dd,
                    marker_color=mc,
                    legendgroup=f"ddbr_{brok}_{k}",
                    showlegend=False,
                ),
                row=2,
                col=1,
            )

    header = ["Měsíc"]
    for k in kinds:
        header.append(f"PnL {k} (USD)")
        header.append(f"max_dd_%_vs_initial {k} (%)")
    row_labels = months + [total_label]
    cells = [row_labels]
    n_rows = len(row_labels)
    row_fill = ["#ffffff"] * (n_rows - 1) + ["#e8eaf6"]
    fill_by_col = [row_fill for _ in range(len(header))]
    for k in kinds:
        cells.append([f"{v:.2f}" for v in ab[f"pnl_{k}"]] + [f"{pnl_tot0[k]:.2f}"])
        cells.append([f"{v:.2f}" for v in ab[f"max_dd_pct_{k}"]] + [f"{dd_tot0[k]:.2f}"])

    fig.add_trace(
        go.Table(
            header=dict(values=header, fill_color="#eceff1", align="left", font=dict(size=12)),
            cells=dict(
                values=cells,
                align="left",
                font=dict(size=11),
                fill=dict(color=fill_by_col),
            ),
        ),
        row=3,
        col=1,
    )

    fig.update_xaxes(type="category", row=1, col=1)
    fig.update_xaxes(type="category", row=2, col=1)
    fig.update_yaxes(title_text="USD", row=1, col=1)
    fig.update_yaxes(title_text="%", row=2, col=1)

    cap_ftmo = ftmo_caption.strip()
    ftmo_note = f" {cap_ftmo}" if cap_ftmo else ""
    title = (
        f"Měsíční výsledky — projected @ max risk pro: {', '.join(order)}{ftmo_note} | {symbol} | {bot_name}<br>"
        f"<sup>Barvy brokerů laděné jako v sekci (2). max_dd jako v grid_report.<br>"
        f"„Další projected“ popisy (bez grafu All-brokerů) dle nastavení <b>FTMO</b> — viz záhlaví souboru.<br>"
        f"ALL = všechny obchody; WAVE / PP / BOS = druhy vstupů. initial_balance = {initial_balance:g} USD.</sup>"
    )
    fig.update_layout(
        title=dict(text=title, x=0.02, xanchor="left"),
        barmode="group",
        legend=dict(orientation="h", yanchor="bottom", y=1.12, xanchor="right", x=1),
        margin=dict(t=112),
    )
    return fig


def write_monthly_kind_summary_html(
    trades_df: pd.DataFrame,
    *,
    symbol: str,
    bot_name: str,
    out_path: Path | str,
    initial_balance: float = 100_000.0,
) -> Optional[Path]:
    """
    Zapíše Plotly HTML s měsíčním PnL a max_dd_%_vs_initial (ALL + WAVE + PP + BOS).

    Vrací Path při úspěchu, None pokud není plotly nebo nejsou data.
    """
    if go is None or make_subplots is None:
        print("[monthly-kind-html] Plotly není k dispozici — přeskočeno.")
        return None

    tbl = build_monthly_kind_metrics(trades_df, initial_balance=initial_balance)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if tbl.empty:
        html = (
            f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{bot_name}</title></head>"
            f"<body><p>Žádné uzavřené obchody pro měsíční přehled ({symbol} / {bot_name}).</p></body></html>"
        )
        out_path.write_text(html, encoding="utf-8")
        print(f"  [monthly-kind-html] Prázdný přehled: {out_path}")
        return out_path

    fig = build_monthly_kind_figure(
        trades_df, symbol=symbol, bot_name=bot_name, initial_balance=initial_balance
    )
    if fig is None:
        print("[monthly-kind-html] Plotly není k dispozici — přeskočeno.")
        return None

    _write_plotly_html_fullsize(fig, out_path)
    print(f"  Měsíční PnL/DD podle druhu: {out_path}")
    return out_path
