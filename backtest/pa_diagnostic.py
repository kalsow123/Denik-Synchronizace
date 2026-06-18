"""
P.A. diagnostický report — zjednodušený: PnL základní + ADX14 změna (adx14_change_indicator).

Horní panel: kumulativní PnL základní.
Dolní panel: pouze normalizovaný ADX14 signál (−4 … +4) z strategy/adx14_change_indicator.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from config.bot_config import BotConfig
from runtime.adx14_live import dataframe_to_bars
from runtime.pnl_base_equity_tracker import BasePnLTracker
from strategy.adx14_change_indicator import compute_adx14_points, load_normalizer

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
except ImportError:
    go = None
    make_subplots = None

PA_METRIC_SPECS = (("adx14_signal", "ADX14 změna", "#437A22"),)

PA_SUMMARY_ROWS = (("ADX14 změna", "adx14_signal"),)


@dataclass
class PADailyFrame:
    days: pd.Series
    adx14_signal: pd.Series


def pnl_base_curve_for_trades(closed_trades: list, cfg: BotConfig) -> pd.DataFrame:
    """PnL základní — risk podle cfg.risk_usd / cfg.pp_risk_usd dle typu pozice."""
    if not closed_trades:
        return pd.DataFrame(columns=["time", "cumulative_pnl_usd"])
    return BasePnLTracker.build_curve_from_closed_trades(closed_trades, cfg=cfg)


def pa_metric_xy(series: pd.Series) -> tuple[pd.Index, pd.Series]:
    """(x, y) pro Plotly — maska vždy ze stejné řady (index = datum)."""
    mask = series.notna()
    if not mask.any():
        return series.index[:0], series.iloc[:0]
    return series.index[mask], series.loc[mask]


def _compute_daily_metrics(df: pd.DataFrame, cfg: BotConfig) -> PADailyFrame:
    bars = dataframe_to_bars(df)
    norm_path = Path(cfg.adx14_change_normalizer_json)
    adx_norm = load_normalizer(norm_path) if norm_path.exists() else None
    adx_points = compute_adx14_points(bars, normalizer=adx_norm)

    days_list = []
    signals = []
    for p in adx_points:
        days_list.append(pd.to_datetime(p.day))
        signals.append(p.adx14_signal)

    if not days_list:
        empty = pd.Series(dtype=float)
        return PADailyFrame(days=pd.Series(dtype="datetime64[ns]"), adx14_signal=empty)

    idx = pd.DatetimeIndex(pd.to_datetime(days_list))
    return PADailyFrame(
        days=pd.Series(idx, index=idx),
        adx14_signal=pd.Series(signals, index=idx),
    )


def build_pa_summary_table_html(pa: PADailyFrame, dd_mask: np.ndarray) -> str:
    """Jednoduchá tabulka pokrytí DD pro ADX14."""
    rows_html = []
    for label, col in PA_SUMMARY_ROWS:
        series = getattr(pa, col)
        valid = series.notna()
        if not valid.any():
            rows_html.append(f"<tr><td>{label}</td><td>—</td><td>—</td><td>—</td><td>—</td></tr>")
            continue
        vals = series[valid].astype(float)
        thr = vals.quantile(0.75)
        ext = vals >= thr
        dd_days = pa.days[dd_mask.reindex(pa.days, fill_value=False).values]
        if len(dd_days) == 0:
            cov = 0.0
        else:
            on_dd = ext.reindex(pa.days, fill_value=False)
            cov = 100.0 * on_dd.loc[pd.to_datetime(dd_days)].sum() / max(1, len(dd_days))
        noise = 100.0 * ext.mean()
        prec = 100.0 * ext[ext].mean() if ext.any() else 0.0
        rows_html.append(
            f"<tr><td>{label}</td><td>{cov:.1f}%</td><td>{noise:.1f}%</td>"
            f"<td>{prec:.1f}%</td><td>diagnostika</td></tr>"
        )
    body = "\n".join(rows_html)
    return (
        "<table><thead><tr><th>Metrika / kombinace</th><th>Pokrytí DD dnů</th>"
        "<th>Šum mimo DD</th><th>Přesnost signálu</th><th>Verdikt</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def pa_diagnostic_plotly_figure(
    closed_trades: list,
    df: pd.DataFrame,
    cfg: BotConfig,
    *,
    bot_name: str = "",
    initial_balance: float = 10000.0,
    gate_disabled_periods: Optional[list] = None,
    gate_threshold: float = 1.3,
    pnl_base_df: Optional[pd.DataFrame] = None,
) -> Optional[object]:
    """2-panel report: PnL základní + ADX14 změna."""
    if go is None or make_subplots is None:
        return None

    from backtest.plotting import _trades_to_df

    trades_df = _trades_to_df(closed_trades)
    if trades_df.empty or df is None or df.empty:
        return None

    pa = _compute_daily_metrics(df, cfg)
    if pa.days.empty:
        return None

    trades_df = trades_df.sort_values("close_time")
    if pnl_base_df is None or pnl_base_df.empty:
        pnl_base_df = pnl_base_curve_for_trades(closed_trades, cfg)

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.52, 0.38],
        subplot_titles=(
            "Kumulativní PnL základní",
            "ADX14 změna (normalizovaný signál)",
        ),
    )

    if not pnl_base_df.empty:
        fig.add_trace(
            go.Scatter(
                x=pnl_base_df["time"],
                y=pnl_base_df["cumulative_pnl_usd"],
                mode="lines",
                name="PnL základní",
                line=dict(color="#2ca02c", width=2.0),
                hovertemplate="%{x}<br>PnL základní: %{y:,.0f} USD<extra></extra>",
            ),
            row=1,
            col=1,
        )
        from backtest.plotting import add_equity_loss_background_to_figure

        _pnl_steps = pnl_base_df["cumulative_pnl_usd"].diff().fillna(
            pnl_base_df["cumulative_pnl_usd"].iloc[0]
        )
        add_equity_loss_background_to_figure(
            fig,
            pnl_base_df["time"],
            pnl_base_df["cumulative_pnl_usd"],
            y_mode="cum_pnl",
            row=1,
            col=1,
            pnl_values=_pnl_steps,
        )
    fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.7, row=1, col=1)

    col_key, label, color = PA_METRIC_SPECS[0]
    series = getattr(pa, col_key)
    x_vals, y_vals = pa_metric_xy(series)
    if len(y_vals):
        fig.add_trace(
            go.Scatter(
                x=x_vals,
                y=y_vals,
                mode="lines",
                name=label,
                line=dict(color=color, width=1.8),
                hovertemplate="%{x}<br>" + label + ": %{y:.2f}<extra></extra>",
            ),
            row=2,
            col=1,
        )

    fig.add_hline(
        y=gate_threshold,
        line_dash="dash",
        line_color="#A13544",
        line_width=1.5,
        annotation_text=f"ADX14 gate {gate_threshold}",
        row=2,
        col=1,
    )

    shapes: list[dict] = []
    if gate_disabled_periods:
        for x0, x1 in gate_disabled_periods:
            shapes.append(
                {
                    "type": "rect",
                    "xref": "x",
                    "yref": "paper",
                    "x0": x0,
                    "x1": x1,
                    "y0": 0,
                    "y1": 1,
                    "fillcolor": "rgba(161, 53, 68, 0.08)",
                    "line": {"width": 0},
                    "layer": "below",
                }
            )

    title = bot_name or getattr(cfg, "bot_name", "Backtest")
    fig.update_layout(
        title={
            "text": (
                f"Diagnostika PnL + ADX14 — {title}<br>"
                "<sup>Nahoře: PnL základní (monitor risk). Dole: ADX14 změna (−4…+4). Růžové pásy = gate OFF.</sup>"
            ),
            "x": 0.02,
            "xanchor": "left",
        },
        height=980,
        template="plotly_white",
        hovermode="x unified",
        legend=dict(orientation="v", x=1.01, y=1),
        shapes=shapes,
    )
    fig.update_yaxes(title_text="Kumulativní PnL (USD)", row=1, col=1, zeroline=True)
    fig.update_yaxes(
        title_text="ADX14 změna",
        row=2,
        col=1,
        zeroline=True,
        range=[-4.2, 4.2],
    )
    fig.update_xaxes(title_text="Čas", row=2, col=1)
    return fig


def pa_diagnostic_html_footer(pa: PADailyFrame, trades_df: pd.DataFrame) -> str:
    if trades_df.empty:
        return ""
    cum = (
        trades_df["cumulative_pnl_usd"]
        if "cumulative_pnl_usd" in trades_df.columns
        else trades_df["pnl_usd"].cumsum()
    )
    loss = np.maximum(0.0, np.maximum.accumulate(cum.values) - cum.values)
    dd_mask = loss > 8000.0
    return build_pa_summary_table_html(pa, dd_mask)
