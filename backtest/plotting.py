"""
Vykresleni vysledku backtestu.

Funkce:
  - plot_equity_curve()   : 1 obrazek se 2 panely (+ volitelne Plotly HTML)
                            - top: equity curve (kumulativni equity)
                            - bottom: monthly/weekly PnL bars (zelene/cervene)
  - plot_top_n_grid()     : equity curves TOP N kombinaci (net + volitelne projected z prop firm)
  - plot_waves_structure() : struktura vln + obchody (Plotly HTML; matplotlib PNG volitelne mimo run_backtest)
  - build_waves_structure_plotly_figure() v waves_plotly_figure.py : stejna Plotly figura vln (sdileno s plot_waves_structure)
  - equity_curve_plotly_figure() : Plotly figura kumul. PnL + periodicke PnL (sdileno s plot_equity_curve HTML)
  - plot_price_with_trades() : cena + obchody (Plotly HTML; matplotlib PNG volitelne mimo run_backtest)
  - monthly_kind_html.write_monthly_kind_summary_html() : mesicni PnL + max DD % vs initial
    podle ALL / WAVE / PP / BOS (--plot-monthly-kind-html v run_backtest)
  - combined_scroll_html.write_scroll_combined_plotly_html() : vice figurek pod sebou v jednom HTML (--plot-scroll-combined-html)

Pouziti volane z run_backtest.py kdyz uzivatel da --plot flag.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import List, Optional, Sequence

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd
try:
    import plotly.graph_objects as go
except Exception:
    go = None


def _write_plotly_html_fullsize(fig, path: Path | str) -> None:
    """Uloží Plotly HTML tak, aby graf vyplnil okno (výška ~celá obrazovka, šířka 100 %)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    current_height = getattr(fig.layout, "height", None)
    try:
        resolved_height = max(960, int(current_height)) if current_height is not None else 960
    except (TypeError, ValueError):
        resolved_height = 960
    fig.update_layout(
        autosize=True,
        height=resolved_height,
        margin=dict(l=52, r=40, t=88, b=56),
    )
    fig.write_html(
        str(path),
        include_plotlyjs="cdn",
        config={
            "responsive": True,
            "scrollZoom": True,
            "displaylogo": False,
        },
        default_width="100%",
        default_height="96vh",
    )


def _grid_cfg_scalar(cfg: dict, key: str, *, max_len: int | None = None) -> str:
    """Hodnota z grid combo pro popisky / hover; volitelne zkraceni dlouhych retezcu."""
    v = cfg.get(key)
    if v is None:
        return "—"
    if hasattr(v, "value"):
        v = getattr(v, "value")
    if isinstance(v, bool):
        t = str(v)
    elif isinstance(v, (int, float)):
        t = str(v)
    else:
        t = str(v)
    if max_len is not None and len(t) > max_len:
        return t[: max_len - 1] + "…"
    return t


def _grid_combo_equity_legend_label(combo: dict, *, total_pnl: float | None = None) -> str:
    """
    Kratky popis serie v grid equity grafu — stejna veliciny jako v grid_report.csv:
    test_pozice; timeframe; wave_min_pct; tp_mode; fib_level; rrr
    (+ volitelne celkovy PnL v zavorce pro orientaci v legende).
    """
    tp = combo.get("_grid_test_pozice")
    tp_s = f"{int(tp):05d}" if tp is not None else "—"
    parts = [
        tp_s,
        _grid_cfg_scalar(combo, "timeframe"),
        _grid_cfg_scalar(combo, "wave_min_pct"),
        _grid_cfg_scalar(combo, "tp_mode"),
        _grid_cfg_scalar(combo, "fib_level"),
        _grid_cfg_scalar(combo, "rrr"),
    ]
    core = ";".join(parts)
    if total_pnl is not None:
        return f"{core} ({total_pnl:+,.0f})"
    return core


# ---------------------------------------------------------------------------
# Helper: prevod closed_trades na DataFrame s datem a pnl_usd
# ---------------------------------------------------------------------------

def _trades_to_df(closed_trades: list) -> pd.DataFrame:
    """Vraci DataFrame se sloupci [close_time, pnl_usd]."""
    if not closed_trades:
        return pd.DataFrame(columns=["close_time", "pnl_usd"])
    rows = [
        {"close_time": pd.Timestamp(t.close_time), "pnl_usd": float(t.pnl_usd)}
        for t in closed_trades
    ]
    df = pd.DataFrame(rows).sort_values("close_time").reset_index(drop=True)
    return df


# Ztrátová období na PnL křivce (drawdown od running peak).
EQUITY_LOSS_BG_MIN_USD = 50.0  # pod tímto prahem se období nevykresluje
_EQUITY_LOSS_BG_FILL = "rgba(214, 39, 40, 0.14)"
_EQUITY_LOSS_LABEL_COLOR = "#8b0000"
_GRID_DD_LABEL_COLOR = "#000000"
_GRID_DD_LABEL_ALERT_COLOR = "#d62728"
_GRID_DD_LABEL_ALERT_THRESHOLD_USD = 10_000.0


def _drawdown_usd_from_peak(y_values) -> np.ndarray:
    """Pokles od running peak na zobrazené křivce (USD) — shodné s diagnostika_pa_kumulativni_pnl."""
    y = np.asarray(y_values, dtype=float)
    if y.size == 0:
        return np.array([], dtype=float)
    peak = np.maximum.accumulate(y)
    return np.maximum(0.0, peak - y)


def _usd_loss_from_series(
    y_values,
    *,
    y_mode: str,
    initial_balance: float,
) -> np.ndarray:
    """Ztráta v USD pro pozadí: drawdown od peak na ose equity nebo kumul. PnL."""
    y = np.asarray(y_values, dtype=float)
    if y_mode == "cum_pnl":
        return _drawdown_usd_from_peak(y)
    if y_mode == "equity":
        return _drawdown_usd_from_peak(y)
    raise ValueError(f"unknown y_mode: {y_mode!r}")


def _find_drawdown_episodes(
    times,
    y_values,
    *,
    pnl_values=None,
    min_loss_usd: float = EQUITY_LOSS_BG_MIN_USD,
) -> list[dict]:
    """
    Souvislá období pod running peak na křivce (kumul. PnL / equity).
    loss_usd = hloubka DD v USD (peak − trough), shodné s max_drawdown_usd v reportu.
    """
    t = pd.Series(pd.to_datetime(times)).reset_index(drop=True)
    y = np.asarray(y_values, dtype=float)
    n = len(y)
    if n == 0:
        return []

    episodes: list[dict] = []
    running_peak = -math.inf
    in_episode = False
    start_i = 0
    ep_peak = 0.0
    trough = 0.0

    for i in range(n):
        yi = float(y[i])
        if yi >= running_peak - 1e-9:
            if in_episode:
                loss = float(ep_peak - trough)
                if loss >= min_loss_usd:
                    x0, x1 = _segment_x_bounds(t, start_i, i - 1)
                    episodes.append(
                        {
                            "x0": x0,
                            "x1": x1,
                            "x_mid": x0 + (x1 - x0) / 2,
                            "y_label": float(trough),
                            "loss_usd": loss,
                        }
                    )
                in_episode = False
            running_peak = yi
        else:
            if not in_episode:
                in_episode = True
                start_i = i
                ep_peak = running_peak
                trough = yi
            else:
                trough = min(trough, yi)

    if in_episode:
        loss = float(ep_peak - trough)
        if loss >= min_loss_usd:
            x0, x1 = _segment_x_bounds(t, start_i, n - 1)
            episodes.append(
                {
                    "x0": x0,
                    "x1": x1,
                    "x_mid": x0 + (x1 - x0) / 2,
                    "y_label": float(trough),
                    "loss_usd": loss,
                }
            )
    return episodes


def _segment_x_bounds(
    t: pd.Series, start_i: int, end_i: int
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Časové hranice období (polovina kroku za koncem, jako u _time_range_segments)."""
    x0 = t.iloc[start_i]
    if end_i + 1 < len(t):
        x1 = t.iloc[end_i] + (t.iloc[end_i + 1] - t.iloc[end_i]) / 2
    elif end_i > 0:
        dt = t.iloc[end_i] - t.iloc[end_i - 1]
        x1 = t.iloc[end_i] + dt / 2
    else:
        x1 = t.iloc[end_i] + pd.Timedelta(hours=1)
    return x0, x1


def _grid_drawdown_label_text(loss_usd: float) -> str:
    return f"-{float(loss_usd):,.0f} USD"


def _grid_drawdown_label_color(loss_usd: float) -> str:
    if float(loss_usd) >= _GRID_DD_LABEL_ALERT_THRESHOLD_USD:
        return _GRID_DD_LABEL_ALERT_COLOR
    return _GRID_DD_LABEL_COLOR


def _time_range_segments(times, mask: np.ndarray) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Souvislé časové intervaly, kde mask[i] platí (jako v diagnostika_pa_kumulativni_pnl)."""
    t = pd.Series(pd.to_datetime(times)).reset_index(drop=True)
    m = np.asarray(mask, dtype=bool)
    if len(t) == 0 or not m.any():
        return []
    segments: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    i = 0
    n = len(m)
    while i < n:
        while i < n and not m[i]:
            i += 1
        if i >= n:
            break
        j = i
        while j < n and m[j]:
            j += 1
        x0 = t.iloc[i]
        if j < n:
            x1 = t.iloc[j - 1] + (t.iloc[j] - t.iloc[j - 1]) / 2
        elif j - 1 > 0:
            dt = t.iloc[j - 1] - t.iloc[j - 2]
            x1 = t.iloc[j - 1] + dt / 2
        else:
            x1 = t.iloc[j - 1] + pd.Timedelta(hours=1)
        segments.append((x0, x1))
        i = j
    return segments


def add_equity_loss_background_to_figure(
    fig,
    times,
    y_values,
    *,
    initial_balance: float = 10000.0,
    y_mode: str = "cum_pnl",
    row: int | None = 1,
    col: int | None = 1,
    pnl_values=None,
    min_loss_usd: float = EQUITY_LOSS_BG_MIN_USD,
) -> None:
    """
    Zvýrazní ztrátová období (pod running peak) červeným pásem a popiskem
    hloubky DD v USD (peak − trough).
    """
    if go is None:
        return
    _ = (initial_balance, y_mode)  # zachováno kvůli volajícím API
    episodes = _find_drawdown_episodes(
        times, y_values, pnl_values=pnl_values, min_loss_usd=min_loss_usd
    )
    # row/col jen u figure z make_subplots; go.Figure() jinak padá na _grid_ref.
    use_subplot = row is not None and getattr(fig, "_grid_ref", None) is not None
    for ep in episodes:
        if use_subplot:
            fig.add_vrect(
                x0=ep["x0"],
                x1=ep["x1"],
                fillcolor=_EQUITY_LOSS_BG_FILL,
                layer="below",
                line_width=0,
                row=row,
                col=col if col is not None else 1,
            )
            fig.add_annotation(
                x=ep["x_mid"],
                y=ep["y_label"],
                text=f"−{ep['loss_usd']:,.0f} USD",
                showarrow=False,
                font=dict(size=11, color=_EQUITY_LOSS_LABEL_COLOR),
                bgcolor="rgba(255,255,255,0.85)",
                bordercolor=_EQUITY_LOSS_LABEL_COLOR,
                borderwidth=1,
                row=row,
                col=col if col is not None else 1,
            )
        else:
            fig.add_shape(
                type="rect",
                xref="x",
                yref="paper",
                x0=ep["x0"],
                x1=ep["x1"],
                y0=0,
                y1=1,
                fillcolor=_EQUITY_LOSS_BG_FILL,
                line={"width": 0},
                layer="below",
            )
            fig.add_annotation(
                x=ep["x_mid"],
                y=ep["y_label"],
                text=f"−{ep['loss_usd']:,.0f} USD",
                showarrow=False,
                font=dict(size=11, color=_EQUITY_LOSS_LABEL_COLOR),
                bgcolor="rgba(255,255,255,0.85)",
                bordercolor=_EQUITY_LOSS_LABEL_COLOR,
                borderwidth=1,
            )


def add_equity_loss_zone_background(
    fig,
    times,
    y_values,
    *,
    initial_balance: float = 10000.0,
    y_mode: str = "cum_pnl",
    row: int | None = 1,
    col: int | None = 1,
) -> None:
    """Zpětná kompatibilita — viz add_equity_loss_background_to_figure."""
    add_equity_loss_background_to_figure(
        fig,
        times,
        y_values,
        initial_balance=initial_balance,
        y_mode=y_mode,
        row=row,
        col=col,
    )


def equity_curve_plotly_figure(
    closed_trades: list,
    bot_name: str,
    *,
    initial_balance: float = 10000.0,
    granularity: str = "monthly",
    pa_diagnostic_mode: bool = False,
    ohlc_df=None,
    cfg=None,
    gate_disabled_periods=None,
    gate_threshold: float = 1.3,
    pnl_base_df=None,
    **_,
):
    """
    Plotly figura: kumulativní PnL + měsíční/týdenní sloupce,
    nebo při pa_diagnostic_mode report jako diagnostika_pa_kumulativni_pnl_ALL.html.
    """
    if pa_diagnostic_mode and ohlc_df is not None and cfg is not None:
        from backtest.pa_diagnostic import pa_diagnostic_plotly_figure

        return pa_diagnostic_plotly_figure(
            closed_trades,
            ohlc_df,
            cfg,
            bot_name=bot_name,
            initial_balance=initial_balance,
            gate_disabled_periods=gate_disabled_periods,
            gate_threshold=gate_threshold,
            pnl_base_df=pnl_base_df,
        )

    if go is None:
        return None
    from plotly.subplots import make_subplots

    df = _trades_to_df(closed_trades)
    if df.empty:
        return None

    df["equity"] = initial_balance + df["pnl_usd"].cumsum()
    df_idx = df.set_index("close_time")
    if granularity == "weekly":
        period_pnl = df_idx["pnl_usd"].resample("W-MON").sum()
        period_label = "Weekly PnL"
    else:
        period_pnl = df_idx["pnl_usd"].resample("MS").sum()
        period_label = "Monthly PnL"

    total_pnl = df["pnl_usd"].sum()
    n_trades = len(df)
    n_wins = (df["pnl_usd"] > 0).sum()
    win_rate = (n_wins / n_trades * 100) if n_trades > 0 else 0.0
    final_equity = df["equity"].iloc[-1]
    max_dd_pct = _max_drawdown_pct(df["equity"], initial_balance)

    df["cum_pnl"] = df["pnl_usd"].cumsum()
    fig_h = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.07,
        row_heights=[0.62, 0.38],
        subplot_titles=(
            "Kumulativní PnL (USD) v čase",
            period_label,
        ),
    )
    fig_h.add_trace(
        go.Scatter(
            x=df["close_time"],
            y=df["cum_pnl"],
            mode="lines",
            name="Cumulative PnL",
            line=dict(color="#1f77b4", width=1.8),
            hovertemplate="%{x}<br>kumul. PnL: %{y:,.2f} USD<extra></extra>",
        ),
        row=1,
        col=1,
    )
    fig_h.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.7, row=1, col=1)
    if len(period_pnl) > 0:
        bar_colors = ["#2ca02c" if v >= 0 else "#d62728" for v in period_pnl.values]
        fig_h.add_trace(
            go.Bar(
                x=list(period_pnl.index),
                y=list(period_pnl.values),
                marker_color=bar_colors,
                name=period_label,
                showlegend=False,
                hovertemplate="%{x}<br>PnL: %{y:,.2f} USD<extra></extra>",
            ),
            row=2,
            col=1,
        )
    add_equity_loss_background_to_figure(
        fig_h,
        df["close_time"],
        df["cum_pnl"],
        initial_balance=initial_balance,
        y_mode="cum_pnl",
        row=1,
        col=1,
        pnl_values=df["pnl_usd"],
    )
    fig_h.update_layout(
        title_text=(
            f"{bot_name}<br>"
            f"Trades: {n_trades} | WR: {win_rate:.1f}% | "
            f"PnL: {total_pnl:+,.0f} USD | Final equity: {final_equity:,.0f} | Max DD: {max_dd_pct:.1f}%"
        ),
        template="plotly_white",
        hovermode="x unified",
    )
    fig_h.update_yaxes(title_text="Kumulativní PnL (USD)", row=1, col=1)
    fig_h.update_yaxes(title_text=f"{period_label} (USD)", row=2, col=1)
    fig_h.update_xaxes(title_text="Čas", row=2, col=1)
    return fig_h


# Barvy projected křivek/sloupů podle brokerů (sekce scroll HTML).
BROKER_PROJECTED_LINE_COLORS = [
    "#1976d2",
    "#7b1fa2",
    "#ef6c00",
    "#00838f",
    "#6d4c41",
    "#c62828",
    "#5e35b1",
    "#558b2f",
]


def equity_scroll_plotly_figure(
    closed_trades: list,
    bot_name: str,
    *,
    headroom_scale: float = 1.0,
    broker_projections: list[tuple[str, float]] | None = None,
    initial_balance: float = 10000.0,
    granularity: str = "monthly",
    projected_pnl_usd: float | None = None,
    max_risk_per_trade_usd: float | None = None,
    title_supplement: str | None = None,
    pa_diagnostic_mode: bool = False,
    ohlc_df=None,
    cfg=None,
    gate_disabled_periods=None,
    gate_threshold: float = 1.3,
    pnl_base_df=None,
    **_,
):
    """
    Scroll sekce (2): kumulativní PnL základní + projected; měsíční sloupce obě verze.
    Při pa_diagnostic_mode: stejný 2-panel P.A. report jako diagnostika_pa_kumulativni_pnl_ALL.html.
    """
    if pa_diagnostic_mode and ohlc_df is not None and cfg is not None:
        from backtest.pa_diagnostic import pa_diagnostic_plotly_figure

        return pa_diagnostic_plotly_figure(
            closed_trades,
            ohlc_df,
            cfg,
            bot_name=bot_name,
            gate_disabled_periods=gate_disabled_periods,
            gate_threshold=gate_threshold,
            pnl_base_df=pnl_base_df,
        )

    if go is None:
        return None
    from plotly.subplots import make_subplots

    df = _trades_to_df(closed_trades)
    if df.empty:
        return None

    df["cum_pnl"] = df["pnl_usd"].astype(float).cumsum()

    df_idx = df.set_index("close_time")
    if granularity == "weekly":
        period_pnl = df_idx["pnl_usd"].resample("W-MON").sum()
        period_label = "Weekly PnL"
    else:
        period_pnl = df_idx["pnl_usd"].resample("MS").sum()
        period_label = "Monthly PnL"

    # (název, headroom) — zvláště línie + měsíční sloupce
    proj_specs: list[tuple[str, float]] = []
    if broker_projections:
        proj_specs = [
            (str(name), float(h) if h is not None else 1.0)
            for name, h in broker_projections
            if str(name).strip()
        ]
    if not proj_specs:
        hh = float(headroom_scale) if headroom_scale is not None else 1.0
        proj_specs = [("projected", hh)]

    cum_by_broker: list[tuple[str, pd.Series, pd.Series]] = []
    for bname, h in proj_specs:
        hh = float(h) if h is not None else 1.0
        df_pi = df.copy()
        df_pi["pnl_usd"] = df_pi["pnl_usd"].astype(float) * hh
        df_pi["cum_pnl"] = df_pi["pnl_usd"].cumsum()
        df_pi_idx = df_pi.set_index("close_time")
        if granularity == "weekly":
            pp = df_pi_idx["pnl_usd"].resample("W-MON").sum()
        else:
            pp = df_pi_idx["pnl_usd"].resample("MS").sum()
        cum_by_broker.append((bname, df_pi["cum_pnl"].copy(), pp))

    meta = []
    if title_supplement:
        meta.append(title_supplement)
    else:
        if headroom_scale is not None and len(proj_specs) == 1:
            meta.append(f"headroom_scale={headroom_scale:.4g}")
        if max_risk_per_trade_usd is not None and len(proj_specs) == 1:
            meta.append(f"max_risk={max_risk_per_trade_usd:,.0f} USD")
        if projected_pnl_usd is not None and len(proj_specs) == 1:
            meta.append(f"projected_net_pnl={projected_pnl_usd:+,.0f} USD")
    meta_s = " | ".join(meta) if meta else ""

    st1 = (
        "Kumulativní PnL — zelená základní; projected @ max_risk — každý broker barvou"
        if len(proj_specs) > 1
        else "Kumulativní PnL — zelená základní, modrá projected @ max_risk_per_trade"
    )
    st2 = (
        f"{period_label} — základní + projected po brokerech (barvy v legendě)"
        if len(proj_specs) > 1
        else f"{period_label} — základní (zelená/červená) + projected (modrá/červená)"
    )

    fig_h = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.12,
        row_heights=[0.62, 0.38],
        subplot_titles=(st1, st2),
    )
    fig_h.add_trace(
        go.Scatter(
            x=df["close_time"],
            y=df["cum_pnl"],
            mode="lines",
            name="PnL základní",
            line=dict(color="#2ca02c", width=2.0),
        ),
        row=1,
        col=1,
    )
    fig_h.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.7, row=1, col=1)

    for idx, (bname, cum_ser, _) in enumerate(cum_by_broker):
        col = BROKER_PROJECTED_LINE_COLORS[idx % len(BROKER_PROJECTED_LINE_COLORS)]
        leg = "PnL projected" if bname == "projected" and len(proj_specs) == 1 else f"{bname} (projected)"
        fig_h.add_trace(
            go.Scatter(
                x=df["close_time"],
                y=cum_ser,
                mode="lines",
                name=leg,
                line=dict(color=col, width=2.0),
            ),
            row=1,
            col=1,
        )

    if len(period_pnl) > 0:
        x_base = list(period_pnl.index)
        fig_h.add_trace(
            go.Bar(
                x=x_base,
                y=list(period_pnl.values),
                marker_color=["#2ca02c" if v >= 0 else "#d62728" for v in period_pnl.values],
                name=f"{period_label} základní",
                offsetgroup="base",
            ),
            row=2,
            col=1,
        )
        for idx, (bname, _, pp_ser) in enumerate(cum_by_broker):
            col = BROKER_PROJECTED_LINE_COLORS[idx % len(BROKER_PROJECTED_LINE_COLORS)]
            neg = "#c62828"
            idx_ts = pd.DatetimeIndex(x_base)
            y_proj = pp_ser.reindex(idx_ts, fill_value=0.0).astype(float).tolist()
            bn = str(bname)
            nm = (
                f"{period_label} projected"
                if bn == "projected" and len(proj_specs) == 1
                else f"{bn} projected"
            )
            fig_h.add_trace(
                go.Bar(
                    x=x_base,
                    y=y_proj,
                    marker_color=[col if v >= 0 else neg for v in y_proj],
                    name=nm,
                    offsetgroup=f"proj_{idx}",
                ),
                row=2,
                col=1,
            )

    add_equity_loss_background_to_figure(
        fig_h,
        df["close_time"],
        df["cum_pnl"],
        initial_balance=initial_balance,
        y_mode="cum_pnl",
        row=1,
        col=1,
        pnl_values=df["pnl_usd"],
    )
    fig_h.update_layout(
        title_text=f"{bot_name}<br><sup>{meta_s}</sup>" if meta_s else bot_name,
        template="plotly_white",
        barmode="group",
        hovermode="x unified",
    )
    fig_h.update_yaxes(title_text="USD", row=1, col=1)
    fig_h.update_yaxes(title_text="USD", row=2, col=1)
    return fig_h


# ---------------------------------------------------------------------------
# Single config: equity curve + monthly bars
# ---------------------------------------------------------------------------

def plot_equity_curve(
    closed_trades: list,
    bot_name: str,
    initial_balance: float = 10000.0,
    granularity: str = "monthly",
    save_path: Optional[Path] = None,
    interactive_html_path: Optional[Path] = None,
    show: bool = False,
    *,
    pa_diagnostic_mode: bool = False,
    ohlc_df=None,
    cfg=None,
    gate_disabled_periods=None,
    gate_threshold: float = 1.3,
    **_,
) -> Optional[Path]:
    """
    Vykresli equity curve + periodic PnL bars pro jednu konfiguraci.

    Args:
        closed_trades: list of ClosedTrade objektu z BacktestEngine
        bot_name: nazev konfigurace (do titulku)
        initial_balance: pocatecni equity (default 10k USD)
        granularity: "monthly" nebo "weekly"
        save_path: kam ulozit PNG (None = neulozi se)
        interactive_html_path: volitelne Plotly HTML (hora: kumulativni PnL v case, dolu: periodicke PnL)
        show: jestli otevrit interaktivni okno (plt.show())

    Returns:
        cesta k ulozenemu PNG nebo None
    """
    df = _trades_to_df(closed_trades)
    if df.empty:
        print(f"[plot] {bot_name}: zadne trades, nic nevykreslim")
        return None

    # Equity curve
    df["equity"] = initial_balance + df["pnl_usd"].cumsum()

    html_only = (
        save_path is None
        and not show
        and interactive_html_path is not None
    )

    # Periodic agregace
    df_idx = df.set_index("close_time")
    if granularity == "weekly":
        period_pnl = df_idx["pnl_usd"].resample("W-MON").sum()
        period_label = "Weekly PnL"
    else:
        period_pnl = df_idx["pnl_usd"].resample("MS").sum()
        period_label = "Monthly PnL"

    # Statistiky pro suptitle
    total_pnl = df["pnl_usd"].sum()
    n_trades = len(df)
    n_wins = (df["pnl_usd"] > 0).sum()
    win_rate = (n_wins / n_trades * 100) if n_trades > 0 else 0.0
    final_equity = df["equity"].iloc[-1]
    max_dd_pct = _max_drawdown_pct(df["equity"], initial_balance)

    saved = None
    if not html_only:
        # Vykresleni: 2 paneli pod sebou (equity nahore, bars dole)
        fig, (ax1, ax2) = plt.subplots(
            2, 1, figsize=(13, 8), sharex=True,
            gridspec_kw={"height_ratios": [2, 1]}
        )

        # --- Top panel: equity curve ---
        ax1.plot(df["close_time"], df["equity"], linewidth=1.5, color="#1f77b4",
                 label="Equity")
        ax1.axhline(initial_balance, color="gray", linestyle="--", linewidth=0.8,
                    alpha=0.6, label=f"Start ({initial_balance:,.0f})")
        ax1.fill_between(df["close_time"], initial_balance, df["equity"],
                         where=df["equity"] >= initial_balance,
                         color="#2ca02c", alpha=0.15)
        ax1.fill_between(df["close_time"], initial_balance, df["equity"],
                         where=df["equity"] < initial_balance,
                         color="#d62728", alpha=0.15)
        ax1.set_ylabel("Equity (USD)")
        ax1.set_title(f"Equity curve")
        ax1.legend(loc="upper left", fontsize=9)
        ax1.grid(True, alpha=0.3)

        # --- Bottom panel: periodic bars ---
        colors = ["#2ca02c" if v >= 0 else "#d62728" for v in period_pnl.values]
        ax2.bar(period_pnl.index, period_pnl.values, color=colors,
                width=20 if granularity == "monthly" else 5, alpha=0.85)
        ax2.axhline(0, color="black", linewidth=0.8)
        ax2.set_ylabel(f"{period_label} (USD)")
        ax2.set_title(period_label)
        ax2.grid(True, alpha=0.3)
        ax2.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha="right")

        fig.suptitle(
            f"{bot_name}\n"
            f"Trades: {n_trades} | WR: {win_rate:.1f}% | "
            f"PnL: {total_pnl:+,.0f} USD | "
            f"Final equity: {final_equity:,.0f} | "
            f"Max DD: {max_dd_pct:.1f}%",
            fontsize=11, y=0.995,
        )

        plt.tight_layout(rect=[0, 0, 1, 0.96])

        if save_path is not None:
            save_path = Path(save_path)
            save_path.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(save_path, dpi=120, bbox_inches="tight")
            saved = save_path
            print(f"[plot] Equity curve ulozen: {save_path}")

        if show:
            plt.show()
        else:
            plt.close(fig)

    if interactive_html_path is not None:
        fig_h = equity_curve_plotly_figure(
            closed_trades,
            bot_name,
            initial_balance=initial_balance,
            granularity=granularity,
            pa_diagnostic_mode=pa_diagnostic_mode,
            ohlc_df=ohlc_df,
            cfg=cfg,
            gate_disabled_periods=gate_disabled_periods,
            gate_threshold=gate_threshold,
        )
        if fig_h is None:
            if go is None:
                print(f"[plot] {bot_name}: Plotly neni dostupny — HTML equity/PnL se neulozil")
        else:
            interactive_html_path = Path(interactive_html_path)
            interactive_html_path.parent.mkdir(parents=True, exist_ok=True)
            _write_plotly_html_fullsize(fig_h, interactive_html_path)
            print(f"[plot] Equity / PnL HTML: {interactive_html_path}")

    return saved


# ---------------------------------------------------------------------------
# Grid: TOP N equity curves v jednom grafu
# ---------------------------------------------------------------------------

_GRID_PAIR_COLORS = (
    "#636efa", "#ef553b", "#00cc96", "#ab63fa", "#ffa15a",
    "#19d3f3", "#ff6692", "#b6e880", "#ff97ff", "#fecb52",
)


def _headroom_scale_from_prop_long(
    df_long: Optional[pd.DataFrame],
    bot_name: str,
    preset: str,
) -> float:
    if (
        df_long is None
        or df_long.empty
        or not preset
        or "bot_name" not in df_long.columns
        or "prop_firm_name" not in df_long.columns
    ):
        return 1.0
    sub = df_long[
        (df_long["bot_name"] == bot_name) & (df_long["prop_firm_name"] == preset)
    ]
    if sub.empty:
        return 1.0
    h = sub.iloc[0].get("headroom_scale")
    try:
        hf = float(h)
        if pd.isna(hf):
            return 1.0
        return hf
    except (TypeError, ValueError):
        return 1.0


def _headroom_scale_from_df_report(
    df_report: Optional[pd.DataFrame],
    bot_name: str,
    preset: str,
) -> Optional[float]:
    """Záloha: stejné headroom jako v grid_report.xlsx (wide sloupec)."""
    if df_report is None or df_report.empty or not preset:
        return None
    if "bot_name" not in df_report.columns:
        return None
    col = f"{preset}__headroom_scale"
    if col not in df_report.columns:
        return None
    sub = df_report[df_report["bot_name"] == bot_name]
    if sub.empty:
        return None
    v = sub.iloc[0].get(col)
    try:
        hf = float(v)
        if pd.isna(hf):
            return None
        return hf
    except (TypeError, ValueError):
        return None


def _hex_to_rgba(color: str, alpha: float) -> str:
    import matplotlib.colors as mcolors

    r, g, b = mcolors.to_rgb(color)
    return f"rgba({int(round(r * 255))}, {int(round(g * 255))}, {int(round(b * 255))}, {float(alpha):.3f})"


def _grid_equity_info_table_df(
    bot_order: Sequence[str],
    *,
    df_report: Optional[pd.DataFrame],
    df_prop_long: Optional[pd.DataFrame],
    primary_prop_preset: Optional[str],
) -> tuple[pd.DataFrame, list[str]]:
    """Tabulka pro spodní část grid equity HTML + pořadí bot_name pro barvení řádků."""
    if not bot_order or df_report is None or df_report.empty:
        return pd.DataFrame(), []

    from backtest.grid.summary_sheet import build_grid_summaries_sheet
    from backtest.prop_firm.compliance import build_all_ranking_sheets, ranking_sheet_name

    preset_names = [str(primary_prop_preset).strip()] if str(primary_prop_preset or "").strip() else []
    df_summary = build_grid_summaries_sheet(df_report, preset_names=preset_names)
    if df_summary.empty or "bot_name" not in df_summary.columns:
        return pd.DataFrame(), []

    df_rank = pd.DataFrame()
    if preset_names and df_prop_long is not None and not df_prop_long.empty:
        ranking_sheets = build_all_ranking_sheets(df_report, df_prop_long, preset_names)
        df_rank = ranking_sheets.get(ranking_sheet_name(preset_names[0]), pd.DataFrame())

    keep_summary = [
        "combo_no",
        "Timeframe",
        "min_opp_bars",
        "RRR_TP",
        "Fib_vstup",
        "entry_mode",
        "profit_factor",
        "max_risk_per_trade_usd",
        "projected_net_pnl_at_max_risk_usd",
        "original_net_pnl_usd",
    ]
    keep_summary = [c for c in keep_summary if c in df_summary.columns]
    out = df_summary[["bot_name"] + keep_summary].copy()

    keep_rank = ["bot_name", "max_dd_%_vs_initial", "max_ddd_%"]
    if not df_rank.empty:
        keep_rank = [c for c in keep_rank if c in df_rank.columns]
        if "bot_name" in keep_rank:
            out = out.merge(df_rank[keep_rank].drop_duplicates(subset=["bot_name"]), on="bot_name", how="left")

    order_map = {name: idx for idx, name in enumerate(bot_order)}
    out = out[out["bot_name"].isin(order_map)].copy()
    if out.empty:
        return out, []

    out["_order"] = out["bot_name"].map(order_map)
    sort_cols = ["_order"] + (["combo_no"] if "combo_no" in out.columns else [])
    out = out.sort_values(sort_cols, kind="mergesort")
    ordered_bot_names = [str(v) for v in out["bot_name"].tolist()]
    out = out.drop(columns=["_order", "bot_name"])
    int_cols = {"combo_no", "min_opp_bars"}
    money_cols = {
        "max_risk_per_trade_usd",
        "projected_net_pnl_at_max_risk_usd",
        "original_net_pnl_usd",
    }
    pct_cols = {"max_dd_%_vs_initial", "max_ddd_%"}
    float_cols = {"profit_factor", "Fib_vstup"}
    for col in out.columns:
        rendered: list[str] = []
        for val in out[col].tolist():
            if pd.isna(val):
                rendered.append("—")
                continue
            if col in int_cols:
                try:
                    rendered.append(str(int(float(val))))
                    continue
                except (TypeError, ValueError):
                    pass
            if col in money_cols:
                try:
                    rendered.append(f"{float(val):,.2f}")
                    continue
                except (TypeError, ValueError):
                    pass
            if col in pct_cols:
                try:
                    rendered.append(f"{float(val):.2f}")
                    continue
                except (TypeError, ValueError):
                    pass
            if col in float_cols:
                try:
                    rendered.append(f"{float(val):.2f}".rstrip("0").rstrip("."))
                    continue
                except (TypeError, ValueError):
                    pass
            rendered.append(str(val))
        out[col] = rendered
    return out.reset_index(drop=True), ordered_bot_names


def _grid_table_font_color_matrix(
    tbl: pd.DataFrame,
    *,
    row_bot_names: Sequence[str],
    series_color_by_name: dict[str, str],
) -> list[list[str]]:
    if tbl.empty:
        return []
    default_color = "#111111"
    row_colors = [
        str(series_color_by_name.get(str(bot_name), default_color))
        for bot_name in row_bot_names
    ]
    return [row_colors[:] for _ in tbl.columns]


def _add_grid_drawdown_overlay_traces(
    fig,
    *,
    series_name: str,
    color: str,
    times,
    y_values,
    pnl_values=None,
    y_min: float,
    y_max: float,
    row: int,
    col: int,
) -> list[dict]:
    """DD pozadí pro jednu equity sérii; vrací epizody pro pozdější text nad křivkou."""
    if go is None:
        return []
    episodes = _find_drawdown_episodes(
        times,
        y_values,
        pnl_values=pnl_values,
        min_loss_usd=EQUITY_LOSS_BG_MIN_USD,
    )
    if not episodes:
        return []

    fill = _hex_to_rgba(color, 0.10)
    for ep in episodes:
        fig.add_trace(
            go.Scatter(
                x=[ep["x0"], ep["x1"], ep["x1"], ep["x0"], ep["x0"]],
                y=[y_min, y_min, y_max, y_max, y_min],
                mode="lines",
                line=dict(width=0, color=fill),
                fill="toself",
                fillcolor=fill,
                hoverinfo="skip",
                showlegend=False,
                legendgroup=series_name,
                name=f"{series_name} DD",
            ),
            row=row,
            col=col,
        )
    return episodes


def _add_grid_drawdown_label_traces(
    fig,
    *,
    series_name: str,
    episodes: Sequence[dict],
    row: int,
    col: int,
) -> None:
    if go is None or not episodes:
        return
    texts = [_grid_drawdown_label_text(ep["loss_usd"]) for ep in episodes]
    text_colors = [_grid_drawdown_label_color(ep["loss_usd"]) for ep in episodes]
    losses = [float(ep["loss_usd"]) for ep in episodes]
    fig.add_trace(
        go.Scatter(
            x=[ep["x_mid"] for ep in episodes],
            y=[ep["y_label"] for ep in episodes],
            mode="text",
            text=texts,
            textposition="middle center",
            textfont=dict(size=11, color=text_colors),
            cliponaxis=False,
            customdata=losses,
            hovertemplate="DD: -%{customdata:,.0f} USD<extra></extra>",
            showlegend=False,
            legendgroup=series_name,
            name=f"{series_name} DD text",
        ),
        row=row,
        col=col,
    )


def plot_top_n_grid(
    grid_results: dict,
    n: Optional[int] = 5,
    initial_balance: float = 10000.0,
    save_path: Optional[Path] = None,
    interactive_html_path: Optional[Path] = None,
    show: bool = False,
    *,
    preferred_bot_order: Optional[Sequence[str]] = None,
    primary_prop_preset: Optional[str] = None,
    df_prop_long: Optional[pd.DataFrame] = None,
    df_report: Optional[pd.DataFrame] = None,
    force_plot_adx14: bool = False,
) -> Optional[Path]:
    """
    Vykresli equity curves TOP N kombinaci (net backtestu + projected @ max risk pro primární prop preset).

    Pozn.: Provede RE-RUN TOP N kombinaci pro closed_trades (grid je po behu bez trades v pameti).
    """
    if not grid_results:
        print("[plot] Zadne grid_results, nic nevykreslim")
        return None

    from backtest.engine import BacktestEngine
    from backtest.grid.data_cache import load_data
    from backtest.grid.translator import grid_dict_to_bot_config, grid_backtest_position_cap_settings
    from backtest.prop_firm.report_keys import scale_trades_df_by_headroom
    from backtest.sim_params import sim_params_from_grid_combo

    valid = [
        (name, stats)
        for name, stats in grid_results.items()
        if "error" not in stats and stats.get("net_pnl_usd") is not None
    ]
    if not valid:
        print("[plot] Zadne validni vysledky v gridu, nic nevykreslim")
        return None

    by_name = {name: (name, stats) for name, stats in valid}
    top: list = []
    if preferred_bot_order:
        for bn in preferred_bot_order:
            if bn in by_name:
                top.append(by_name[bn])
                if n is not None and len(top) >= n:
                    break
        if not top:
            valid.sort(key=lambda r: r[1].get("net_pnl_usd", 0.0), reverse=True)
            top = valid if n is None else valid[: min(n, len(valid))]
            print(
                f"\n[plot] Re-runuji {len(top)} kombinaci (fallback razeni podle net_pnl)..."
            )
        else:
            print(f"\n[plot] Re-runuji {len(top)} kombinaci (poradi dle grid reportu)...")
    elif n is None:
        valid.sort(key=lambda r: r[1].get("net_pnl_usd", 0.0), reverse=True)
        top = valid
        print(f"\n[plot] Re-runuji VSECHNY {len(top)} kombinaci pro equity curves...")
    else:
        valid.sort(key=lambda r: r[1].get("net_pnl_usd", 0.0), reverse=True)
        top = valid[:n]
        print(f"\n[plot] Re-runuji TOP {len(top)} kombinaci pro equity curves...")

    pp = (primary_prop_preset or "").strip()
    has_long = df_prop_long is not None and not df_prop_long.empty
    has_wide = (
        df_report is not None
        and not df_report.empty
        and bool(pp)
        and f"{pp}__headroom_scale" in df_report.columns
    )
    do_projected = bool(pp) and (has_long or has_wide)

    interactive_series: list = []
    pa_plot_kwargs: dict = {}
    plotted = 0
    for name, stats in top:
        combo = stats.get("config")
        if combo is None:
            print(f"[plot]   {name}: chybi 'config' v stats, preskoc")
            continue
        try:
            cfg = grid_dict_to_bot_config(combo)
            cap_mode, cap_limit = grid_backtest_position_cap_settings(combo)
            spr, slip, _ = sim_params_from_grid_combo(combo)
            df = load_data(
                symbol=combo["symbol"],
                timeframe_label=combo["timeframe"],
                date_from=combo.get("date_from"),
                date_to=combo.get("date_to"),
            )
            engine = BacktestEngine(
                cfg,
                backtest_position_cap_mode=cap_mode,
                backtest_max_open_positions=cap_limit,
                backtest_spread=spr,
                backtest_slippage=slip,
            )
            trades = engine.run(df)
            from backtest.plotting_adx14 import adx14_plot_kwargs_from_engine

            sim = getattr(engine, "adx14_sim", None)
            pnl_base_df = (
                sim.base_pnl_dataframe()
                if sim is not None and sim.pnl_tracker is not None
                else None
            )
            gate_periods = (
                sim.gate_disabled_periods()
                if sim is not None
                and getattr(cfg, "adx14_equity_gate_enabled", False)
                else []
            )
            if not pa_plot_kwargs:
                pa_plot_kwargs = adx14_plot_kwargs_from_engine(
                    engine, force_plot=force_plot_adx14, df=df,
                )
            elif gate_periods:
                pa_plot_kwargs["gate_disabled_periods"] = gate_periods
        except Exception as e:
            print(f"[plot]   {name}: re-run selhal: {e}")
            continue

        if not trades:
            print(f"[plot]   {name}: 0 trades, preskoc")
            continue

        from backtest.grid.study_mode import filter_trades_df_for_grid_stats
        from backtest.stats import trades_to_df

        # trades_to_df (ne _trades_to_df) — filter wave_isolation_study potřebuje position_kind.
        df_t = filter_trades_df_for_grid_stats(trades_to_df(trades), combo)
        if df_t.empty:
            print(f"[plot]   {name}: 0 trades po wave_isolation filtru, preskoc")
            continue

        if len(df_t) < len(trades):
            keep_times = set(pd.to_datetime(df_t["close_time"]))
            trades = [
                t for t in trades
                if pd.Timestamp(t.close_time) in keep_times
            ]

        df_t["equity"] = initial_balance + df_t["pnl_usd"].cumsum()
        total_pnl = float(df_t["pnl_usd"].sum())

        h = _headroom_scale_from_prop_long(
            df_prop_long if has_long else None, name, pp
        )
        if do_projected and math.isclose(h, 1.0, rel_tol=0.0, abs_tol=1e-12):
            hr = _headroom_scale_from_df_report(df_report, name, pp)
            if hr is not None:
                h = hr

        proj_entry: dict = {}
        if do_projected:
            df_p = scale_trades_df_by_headroom(
                df_t[["close_time", "pnl_usd"]].copy(), h
            )
            p_total = float(df_p["pnl_usd"].sum())
            df_p["equity"] = initial_balance + df_p["pnl_usd"].cumsum()
            proj_entry = {
                "equity_df_p": df_p[["close_time", "equity"]].copy(),
                "projected_total": p_total,
                "legend_label_p": _grid_combo_equity_legend_label(combo, total_pnl=p_total)
                + f" [{pp} proj]",
            }

        core_leg = _grid_combo_equity_legend_label(combo, total_pnl=total_pnl)
        net_label = core_leg + (" [net]" if do_projected else "")

        interactive_series.append({
            "name": name,
            "equity_df": df_t[["close_time", "equity", "pnl_usd"]].copy(),
            "pnl_base_df": pnl_base_df,
            "gate_disabled_periods": gate_periods,
            "trades": trades,
            "total_pnl": total_pnl,
            "config": combo,
            "legend_label": net_label,
            "headroom_scale": h,
            **proj_entry,
        })
        plotted += 1
        extra = ""
        if proj_entry.get("projected_total") is not None:
            extra = f" | projected ({pp}): {proj_entry['projected_total']:+,.0f}"
        print(
            f"[plot]   OK {name} | trades: {len(trades)} | net PnL: {total_pnl:+,.0f}{extra}"
        )

    if plotted == 0:
        print("[plot] Zadnou TOP kombinaci se nepodarilo vykreslit")
        return None

    series_color_by_name = {
        str(s["name"]): _GRID_PAIR_COLORS[i % len(_GRID_PAIR_COLORS)]
        for i, s in enumerate(interactive_series)
    }

    need_mpl = save_path is not None or show
    saved = None
    if need_mpl:
        fig, ax = plt.subplots(figsize=(13, 7))
        for i, s in enumerate(interactive_series):
            col = _GRID_PAIR_COLORS[i % len(_GRID_PAIR_COLORS)]
            label = s.get("legend_label") or f"{s['name']} ({s['total_pnl']:+,.0f})"
            ax.plot(
                s["equity_df"]["close_time"],
                s["equity_df"]["equity"],
                linewidth=1.3,
                alpha=0.9,
                color=col,
                label=label,
            )
            if "equity_df_p" in s:
                ax.plot(
                    s["equity_df_p"]["close_time"],
                    s["equity_df_p"]["equity"],
                    linewidth=1.3,
                    linestyle="--",
                    alpha=0.85,
                    color=col,
                    label=s["legend_label_p"],
                )
        ax.axhline(initial_balance, color="gray", linestyle="--",
                   linewidth=0.8, alpha=0.6)
        ax.set_xlabel("Date")
        ax.set_ylabel("Equity (USD)")
        ax.set_title(f"TOP {plotted} grid kombinaci - equity curves")
        ax.legend(loc="best", fontsize=7)
        ax.grid(True, alpha=0.3)
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right")

        plt.tight_layout()

        if save_path is not None:
            save_path = Path(save_path)
            save_path.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(save_path, dpi=120, bbox_inches="tight")
            saved = save_path
            print(f"[plot] TOP {plotted} grid graf ulozen: {save_path}")

        if show:
            plt.show()
        else:
            plt.close(fig)

    if interactive_html_path is not None:
        if go is None:
            print("[plot] Plotly neni dostupny - interaktivni HTML graf nevytvoren")
        else:
            from plotly.subplots import make_subplots

            interactive_html_path = Path(interactive_html_path)
            interactive_html_path.parent.mkdir(parents=True, exist_ok=True)
            use_pa = bool(
                pa_plot_kwargs.get("pa_diagnostic_mode")
                and pa_plot_kwargs.get("ohlc_df") is not None
            )

            if use_pa:
                from backtest.pa_diagnostic import (
                    PA_METRIC_SPECS,
                    _compute_daily_metrics,
                    pa_metric_xy,
                    pnl_base_curve_for_trades,
                )

                pa_cfg = pa_plot_kwargs["cfg"]
                pa_df = pa_plot_kwargs["ohlc_df"]
                pa = _compute_daily_metrics(pa_df, pa_cfg)
                gate_thr = float(pa_plot_kwargs.get("gate_threshold", 1.3))

                fig_html = make_subplots(
                    rows=3,
                    cols=1,
                    shared_xaxes=True,
                    vertical_spacing=0.08,
                    row_heights=[0.46, 0.28, 0.26],
                    subplot_titles=(
                        "PnL základní (TOP kombinace)",
                        "ADX14 změna (normalizovaný signál)",
                        "Přehled kombinací",
                    ),
                    specs=[[{"type": "xy"}], [{"type": "xy"}], [{"type": "table"}]],
                )
                for i, s in enumerate(interactive_series):
                    cfg_row = s["config"] or {}
                    col = _GRID_PAIR_COLORS[i % len(_GRID_PAIR_COLORS)]
                    leg = s.get("legend_label") or f"{s['name']} ({s['total_pnl']:+,.0f})"
                    pnl_df = s.get("pnl_base_df")
                    if pnl_df is None or pnl_df.empty:
                        pnl_df = pnl_base_curve_for_trades(
                            s["trades"],
                            grid_dict_to_bot_config(cfg_row) if cfg_row else pa_cfg,
                        )
                    fig_html.add_trace(
                        go.Scatter(
                            x=pnl_df["time"],
                            y=pnl_df["cumulative_pnl_usd"],
                            mode="lines",
                            name=leg,
                            legendgroup=s["name"],
                            line=dict(color=col, width=1.8),
                            hovertemplate="%{x}<br>PnL základní: %{y:,.0f} USD<extra></extra>",
                        ),
                        row=1,
                        col=1,
                    )
                for col_key, label, color in PA_METRIC_SPECS:
                    series = getattr(pa, col_key)
                    x_vals, y_vals = pa_metric_xy(series)
                    if y_vals.empty:
                        continue
                    fig_html.add_trace(
                        go.Scatter(
                            x=x_vals,
                            y=y_vals,
                            mode="lines",
                            name=label,
                            line=dict(color=color, width=1.6),
                        ),
                        row=2,
                        col=1,
                    )
                fig_html.add_hline(
                    y=gate_thr,
                    line_dash="dash",
                    line_color="#A13544",
                    row=2,
                    col=1,
                )
                bot_order = [s["name"] for s in interactive_series]
                tbl, row_bot_names = _grid_equity_info_table_df(
                    bot_order,
                    df_report=df_report,
                    df_prop_long=df_prop_long,
                    primary_prop_preset=primary_prop_preset,
                )
                if not tbl.empty:
                    cell_font_color = _grid_table_font_color_matrix(
                        tbl,
                        row_bot_names=row_bot_names,
                        series_color_by_name=series_color_by_name,
                    )
                    fig_html.add_trace(
                        go.Table(
                            header=dict(
                                values=list(tbl.columns),
                                align="left",
                                fill_color="#e9eef7",
                                font=dict(size=11),
                            ),
                            cells=dict(
                                values=[tbl[c].tolist() for c in tbl.columns],
                                align="left",
                                height=22,
                                font=dict(color=cell_font_color),
                            ),
                        ),
                        row=3,
                        col=1,
                    )
                shapes: list[dict] = []
                gate_periods_plot: list = []
                for s in interactive_series:
                    gate_periods_plot.extend(s.get("gate_disabled_periods") or [])
                if not gate_periods_plot:
                    gate_periods_plot = pa_plot_kwargs.get("gate_disabled_periods") or []
                for x0, x1 in gate_periods_plot:
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
                fig_html.update_layout(
                    title=(
                        f"Diagnostika PnL + ADX14 — TOP {plotted} grid<br>"
                        "<sup>Shora: PnL základní (monitor risk). Dole: ADX14. Růžové pásy = gate OFF (zkrácení při equity BOS).</sup>"
                    ),
                    height=1180,
                    template="plotly_white",
                    hovermode="x unified",
                    legend=dict(orientation="v", x=1.01, y=1, groupclick="togglegroup"),
                    shapes=shapes,
                )
                fig_html.update_yaxes(title_text="Kumulativní PnL (USD)", row=1, col=1)
                fig_html.update_yaxes(
                    title_text="ADX14 změna",
                    row=2,
                    col=1,
                    range=[-4.2, 4.2],
                )
            else:
                fig_html = make_subplots(
                    rows=2,
                    cols=1,
                    shared_xaxes=False,
                    vertical_spacing=0.08,
                    row_heights=[0.74, 0.26],
                    subplot_titles=(
                        f"TOP {plotted} grid kombinaci - equity curves",
                        "Přehled kombinací",
                    ),
                    specs=[[{"type": "xy"}], [{"type": "table"}]],
                )
                y_bounds: list[float] = []
                for s in interactive_series:
                    y_bounds.extend(list(pd.to_numeric(s["equity_df"]["equity"], errors="coerce").dropna()))
                    if "equity_df_p" in s:
                        y_bounds.extend(list(pd.to_numeric(s["equity_df_p"]["equity"], errors="coerce").dropna()))
                y_min = min(y_bounds) if y_bounds else initial_balance
                y_max = max(y_bounds) if y_bounds else initial_balance
                y_pad = max((y_max - y_min) * 0.04, 50.0)
                y_min -= y_pad
                y_max += y_pad

                dd_label_specs: list[tuple[str, list[dict]]] = []
                for i, s in enumerate(interactive_series):
                    cfg_row = s["config"] or {}
                    col = _GRID_PAIR_COLORS[i % len(_GRID_PAIR_COLORS)]
                    leg = s.get("legend_label") or f"{s['name']} ({s['total_pnl']:+,.0f})"
                    dd_episodes = _add_grid_drawdown_overlay_traces(
                        fig_html,
                        series_name=s["name"],
                        color=col,
                        times=s["equity_df"]["close_time"],
                        y_values=s["equity_df"]["equity"],
                        pnl_values=None,
                        y_min=y_min,
                        y_max=y_max,
                        row=1,
                        col=1,
                    )
                    if dd_episodes:
                        dd_label_specs.append((str(s["name"]), dd_episodes))
                    fig_html.add_trace(
                        go.Scatter(
                            x=s["equity_df"]["close_time"],
                            y=s["equity_df"]["equity"],
                            mode="lines",
                            name=leg,
                            legendgroup=s["name"],
                            line=dict(color=col, width=2),
                        ),
                        row=1,
                        col=1,
                    )
                    if "equity_df_p" in s:
                        fig_html.add_trace(
                            go.Scatter(
                                x=s["equity_df_p"]["close_time"],
                                y=s["equity_df_p"]["equity"],
                                mode="lines",
                                name=s["legend_label_p"],
                                legendgroup=s["name"],
                                line=dict(color=col, width=2, dash="dash"),
                            ),
                            row=1,
                            col=1,
                        )
                fig_html.add_hline(
                    y=initial_balance,
                    line_dash="dash",
                    line_color="gray",
                    opacity=0.6,
                    row=1,
                    col=1,
                )
                for series_name, dd_episodes in dd_label_specs:
                    _add_grid_drawdown_label_traces(
                        fig_html,
                        series_name=series_name,
                        episodes=dd_episodes,
                        row=1,
                        col=1,
                    )
                bot_order = [s["name"] for s in interactive_series]
                tbl, row_bot_names = _grid_equity_info_table_df(
                    bot_order,
                    df_report=df_report,
                    df_prop_long=df_prop_long,
                    primary_prop_preset=primary_prop_preset,
                )
                if not tbl.empty:
                    cell_font_color = _grid_table_font_color_matrix(
                        tbl,
                        row_bot_names=row_bot_names,
                        series_color_by_name=series_color_by_name,
                    )
                    fig_html.add_trace(
                        go.Table(
                            header=dict(
                                values=list(tbl.columns),
                                align="left",
                                fill_color="#e9eef7",
                                font=dict(size=11),
                            ),
                            cells=dict(
                                values=[tbl[c].tolist() for c in tbl.columns],
                                align="left",
                                height=22,
                                font=dict(color=cell_font_color),
                            ),
                        ),
                        row=2,
                        col=1,
                    )
                fig_html.update_layout(
                    title=(
                        f"TOP {plotted} grid kombinaci - equity curves"
                        "<br><sup>DD pozadí se počítá zvlášť pro každou kombinaci; klik v legendě přepíná i její DD overlay.</sup>"
                    ),
                    template="plotly_white",
                    hovermode="closest",
                    height=1180,
                    legend=dict(groupclick="togglegroup"),
                )
                fig_html.update_yaxes(title_text="Equity (USD)", row=1, col=1, range=[y_min, y_max])

            _write_plotly_html_fullsize(fig_html, interactive_html_path)
            print(f"[plot] Interaktivni grid graf ulozen: {interactive_html_path}")

    return saved


def _nearest_bar_ix(time_series: pd.Series, t) -> int:
    """Index radku v df s nejblizsim casem k t."""
    ts = pd.to_datetime(time_series)
    p = pd.Timestamp(t)
    return int((ts - p).abs().argmin())


def _median_bar_timedelta(times: pd.Series) -> pd.Timedelta:
    """Median krok casu mezi sousednimi svickami v okne."""
    t = pd.to_datetime(times)
    if len(t) < 2:
        return pd.Timedelta(minutes=15)
    deltas = t.diff().dropna()
    if deltas.empty:
        return pd.Timedelta(minutes=15)
    md = deltas.median()
    if pd.isna(md) or md <= pd.Timedelta(0):
        return pd.Timedelta(minutes=15)
    return md


def _wave_distinct_hex(idx: int) -> str:
    """
    Barva vlny podle poradi v grafu — kazdy index jina odstin (HTTPS + PNG/matplotlib).
    Golden ratio na hue rozlozi sousedy vizualne dale od sebe.
    """
    import matplotlib.colors as mcolors
    import matplotlib as mpl

    h = float(((idx + 1) * 0.3819660112501051) % 1.0)
    s = 0.52 + float((idx % 5)) * 0.06
    v = float(0.72 - ((idx >> 3) % 3) * 0.065)
    s = float(np.clip(s, 0.35, 0.85))
    v = float(np.clip(v, 0.55, 0.92))
    rgb = mpl.colors.hsv_to_rgb((h, s, v))
    return mcolors.to_hex(tuple(np.asarray(rgb).clip(0.0, 1.0)))


def _mix_hex(c1: str, c2: str, t: float) -> str:
    """Linearni mix dvou HEX barev; t=0 -> c1, t=1 -> c2."""
    import matplotlib.colors as mcolors

    a = np.asarray(mcolors.to_rgb(c1), dtype=float)
    b = np.asarray(mcolors.to_rgb(c2), dtype=float)
    t = float(np.clip(t, 0.0, 1.0))
    rgb = a * (1.0 - t) + b * t
    return mcolors.to_hex(tuple(np.asarray(rgb).clip(0.0, 1.0)))


def _fib_ratio_display(ratio: float) -> str:
    """Zobrazení Fib poměru s desetinnou čárkou (např. 0,5)."""
    s = f"{float(ratio):g}"
    return s.replace(".", ",")


def _entry_type_label(entry_type: object) -> str:
    """Krátký popis typu vstupu pro hover (LIMIT / STOP / MARKET)."""
    et = str(entry_type or "").upper()
    if "MARKET" in et:
        return "MARKET"
    if "STOP" in et:
        return "STOP"
    if "LIMIT" in et:
        return "LIMIT"
    return str(entry_type or "—")


def _entry_type_hover_letter(entry_type: object) -> str:
    """Jednopísmenková značka: L / S / M."""
    et = str(entry_type or "").upper()
    if "MARKET" in et:
        return "M"
    if "STOP" in et:
        return "S"
    if "LIMIT" in et:
        return "L"
    return "?"


def _entry_marker_mpl(entry_type: object, is_buy: bool) -> str:
    """Matplotlib marker: LIMIT trojúhelník, STOP čtverec, MARKET kosočtverec."""
    et = str(entry_type or "").upper()
    if "MARKET" in et:
        return "D"
    if "STOP" in et:
        return "s"
    return "^" if is_buy else "v"


def _entry_marker_plotly(entry_type: object, is_buy: bool) -> str:
    et = str(entry_type or "").upper()
    if "MARKET" in et:
        return "diamond"
    if "STOP" in et:
        return "square"
    return "triangle-up" if is_buy else "triangle-down"


def _exit_marker_plotly(close_reason: object) -> str:
    """Plotly marker pro exit: TP = hvězda, ostatní = křížek."""
    return "star" if str(close_reason or "").upper() == "TP" else "x"


def _pnl_outcome_color(trade) -> str | None:
    """Barva haló podle uzavřeného PnL (None = bez haló)."""
    if not hasattr(trade, "pnl_usd"):
        return None
    try:
        pnl = float(trade.pnl_usd)
    except (TypeError, ValueError):
        return None
    if pnl > 0:
        return "#2e7d32"
    if pnl < 0:
        return "#c62828"
    return "#757575"


def _is_pp_trade(t) -> bool:
    return bool(getattr(t, "is_pp", False))


def _is_two_sided_trade(t) -> bool:
    return bool(getattr(t, "is_two_sided_mirror", False))


# BOS / EXT_BOS / WAVE_COUNTER — barvy ve visual / Plotly HTML (viz _trade_plot_palette).
BOS_PLOT_COLOR = "#7b3f00"
WAVE_COUNTER_PLOT_COLOR = "#1b5e20"
EXT_COUNTER_BOS_PLOT_COLOR = "#000000"
EXT_COUNTER_TIME_PLOT_COLOR = "#808080"


def _is_ext_counter_time_trade(t) -> bool:
    from strategy.ext_logic import ENTRY_TAG_EXT_COUNTER_TIME

    return str(getattr(t, "entry_tag", "base")) == ENTRY_TAG_EXT_COUNTER_TIME


def _is_ext_counter_bos_trade(t) -> bool:
    from strategy.ext_logic import ENTRY_TAG_EXT_COUNTER_BOS

    return str(getattr(t, "entry_tag", "base")) == ENTRY_TAG_EXT_COUNTER_BOS


def _is_wave_counter_trade(t) -> bool:
    from strategy.wave_sequence import is_wave_counter_trade

    return is_wave_counter_trade(t)


def _is_bos_trade(t) -> bool:
    if _is_pp_trade(t) or _is_two_sided_trade(t) or _is_wave_counter_trade(t):
        return False
    if bool(getattr(t, "is_ext", False)):
        return False
    return bool(getattr(t, "is_bos_reentry", False))


def _mono_trade_palette(color: str) -> dict[str, str]:
    return {
        "col": color,
        "sl_col": color,
        "tp_col": color,
        "pos_col": color,
        "exit_col": color,
    }


def _ext_counter_time_trade_colors() -> dict[str, str]:
    return _mono_trade_palette(EXT_COUNTER_TIME_PLOT_COLOR)


def _ext_counter_bos_trade_colors() -> dict[str, str]:
    return _mono_trade_palette(EXT_COUNTER_BOS_PLOT_COLOR)


def _bos_trade_colors() -> dict[str, str]:
    return _mono_trade_palette(BOS_PLOT_COLOR)


def _wave_counter_trade_colors() -> dict[str, str]:
    return _mono_trade_palette(WAVE_COUNTER_PLOT_COLOR)


def _trade_plot_palette(t, *, is_buy: bool, is_tp: bool) -> dict[str, str]:
    """Barvy entry / SL / TP / spojnice / exit pro PNG a Plotly HTML."""
    if _is_pp_trade(t):
        return {
            "col": "#1e88e5" if is_buy else "#1565c0",
            "sl_col": "#3949ab",
            "tp_col": "#0d47a1",
            "pos_col": "#42a5f5" if is_tp else "#90a4ae",
            "exit_col": "#1565c0" if is_tp else "#4527a0",
        }
    if _is_ext_counter_time_trade(t):
        return _ext_counter_time_trade_colors()
    if _is_ext_counter_bos_trade(t):
        return _ext_counter_bos_trade_colors()
    if _is_wave_counter_trade(t):
        return _wave_counter_trade_colors()
    if _is_bos_trade(t):
        return _bos_trade_colors()
    if _is_two_sided_trade(t):
        return {
            "col": WAVE_TWO_SIDED_COLOR,
            "sl_col": _mix_hex(WAVE_TWO_SIDED_COLOR, "#c62828", 0.35),
            "tp_col": _mix_hex(WAVE_TWO_SIDED_COLOR, "#66bb6a", 0.4),
            "pos_col": (
                _mix_hex(WAVE_TWO_SIDED_COLOR, "#81c784", 0.25)
                if is_tp
                else WAVE_TWO_SIDED_COLOR
            ),
            "exit_col": (
                _mix_hex(WAVE_TWO_SIDED_COLOR, "#66bb6a", 0.4)
                if is_tp
                else _mix_hex(WAVE_TWO_SIDED_COLOR, "#c62828", 0.35)
            ),
        }
    return {
        "col": "#21a656" if is_buy else "#d0453b",
        "sl_col": "#ff0000" if is_buy else "#801922",
        "tp_col": "#39159c",
        "pos_col": "#4682b4" if is_tp else "#928659",
        "exit_col": "#39159c" if is_tp else ("#ff0000" if is_buy else "#801922"),
    }


def _trade_plot_glow(t) -> str | None:
    if _is_pp_trade(t):
        return _pp_glow_color(t)
    if _is_ext_counter_time_trade(t):
        return EXT_COUNTER_TIME_PLOT_COLOR
    if _is_ext_counter_bos_trade(t):
        return EXT_COUNTER_BOS_PLOT_COLOR
    if _is_wave_counter_trade(t):
        return WAVE_COUNTER_PLOT_COLOR
    if _is_bos_trade(t):
        return BOS_PLOT_COLOR
    if _is_two_sided_trade(t):
        return _mix_hex(WAVE_TWO_SIDED_COLOR, "#a5d6a7", 0.35)
    return _pnl_outcome_color(t)


def _trade_hover_kind_line(t) -> str:
    """HTML prefix pro Plotly hover (visual waves / price+trades)."""
    if _is_ext_counter_time_trade(t):
        return (
            f"<b style='color:{EXT_COUNTER_TIME_PLOT_COLOR}'>EXT_COUNTER_TIME</b><br>"
        )
    if _is_ext_counter_bos_trade(t):
        return (
            f"<b style='color:{EXT_COUNTER_BOS_PLOT_COLOR}'>EXT_COUNTER_BOS</b><br>"
        )
    if _is_wave_counter_trade(t):
        return f"<b style='color:{WAVE_COUNTER_PLOT_COLOR}'>W_C</b><br>"
    if _is_bos_trade(t):
        return f"<b style='color:{BOS_PLOT_COLOR}'>BOS</b><br>"
    if _is_two_sided_trade(t):
        return f"<b style='color:{WAVE_TWO_SIDED_COLOR}'>WAVE_TWO_SIDED</b><br>"
    return ""


# Barva pozic WAVE_TWO_SIDED ve visual HTML (entry, spojnice, stitek).
WAVE_TWO_SIDED_COLOR = "#00332a"
# Poradi vlny v trendu (index_in_trend) na visual_waves.html
WAVE_TREND_INDEX_LABEL_COLOR = "#39159c"
# Pozadi (box) two-sided counter vlny — zluta, odlisena od beznych bull vln.
WAVE_TWO_SIDED_WAVE_BG_COLOR = "#ffeb3b"
# WF continuation vlny (Wick Fakeout Recovery) — cerny box ve visual HTML.
WAVE_WF_CONTINUATION_BG_COLOR = "#000000"


def _pa_type_hover_label(trade) -> str:
    """P.A. typ pro tooltip ve visual waves (BOS → WAVE_BOS)."""
    from backtest.stats import classify_position_kind

    kind = classify_position_kind(
        is_pp=bool(getattr(trade, "is_pp", False)),
        is_counter=bool(getattr(trade, "is_counter", False)),
        is_bos_reentry=bool(getattr(trade, "is_bos_reentry", False)),
        is_two_sided_mirror=bool(getattr(trade, "is_two_sided_mirror", False)),
        is_ext=bool(getattr(trade, "is_ext", False)),
        entry_tag=str(getattr(trade, "entry_tag", "base")),
    )
    return "WAVE_BOS" if kind == "BOS" else kind


def _pp_glow_color(trade) -> str | None:
    """Haló u PP obchodu — modré odstíny podle výsledku (stále odlišné od běžné zelené/červené)."""
    if not hasattr(trade, "pnl_usd"):
        return "#90caf9"
    try:
        pnl = float(trade.pnl_usd)
    except (TypeError, ValueError):
        return "#90caf9"
    if pnl > 0:
        return "#90caf9"
    if pnl < 0:
        return "#5c6bc0"
    return "#b3e5fc"


def _wave_is_ext(wave: dict, cfg: object | None = None) -> bool:
    """True pokud vlna nese EXT metadata (is_ext nebo move_pct >= ext_wave_min_pct)."""
    if bool(wave.get("is_ext", False)):
        return True
    if cfg is not None:
        try:
            from strategy.ext_logic import is_ext_wave
            return is_ext_wave(wave, cfg)
        except Exception:
            pass
    return False


# EXT secondary entry (ext_secondary_fib_level) — modra cara ve visual HTML u EXT vln.
EXT_SECONDARY_ENTRY_PLOT_COLOR = "#1848cc"


def _ext_secondary_entry_for_wave(wave: dict, cfg: object | None = None) -> float | None:
    """Cenova uroven sekundarniho EXT vstupu (typicky fib 0.236); jen pro EXT vlny."""
    if not _wave_is_ext(wave, cfg):
        return None
    if cfg is not None and not bool(getattr(cfg, "ext_secondary_enabled", False)):
        return None
    raw = wave.get("ext_secondary_entry")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    if cfg is None:
        return None
    try:
        box_top = float(wave["box_top"])
        box_bot = float(wave["box_bottom"])
        direction = int(wave.get("dir", 0))
        sec_lvl = float(getattr(cfg, "ext_secondary_fib_level", 0.236))
    except (KeyError, TypeError, ValueError):
        return None
    if box_top <= box_bot or direction not in (1, -1):
        return None
    rng = box_top - box_bot
    if direction == 1:
        return float(box_top - rng * sec_lvl)
    return float(box_bot + rng * sec_lvl)


def _ext_secondary_fib_ratio_label(cfg: object | None) -> str:
    try:
        ratio = float(
            getattr(cfg, "ext_secondary_fib_level", 0.236) if cfg is not None else 0.236
        )
    except (TypeError, ValueError):
        ratio = 0.236
    return _fib_ratio_display(ratio)


# EXT BOS trigger level (ext_bos_fib_level) — cerna cara ve visual HTML u EXT vln.
EXT_BOS_LEVEL_PLOT_COLOR = "#000000"


def _ext_bos_level_for_wave(wave: dict, cfg: object | None = None) -> float | None:
    """Cenova uroven EXT BOS (fib ext_bos_fib_level v boxu); jen pro EXT vlny."""
    if not _wave_is_ext(wave, cfg):
        return None
    raw = wave.get("ext_bos_level")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    if cfg is None:
        return None
    try:
        box_top = float(wave["box_top"])
        box_bot = float(wave["box_bottom"])
        direction = int(wave.get("dir", 0))
        bos_lvl = float(getattr(cfg, "ext_bos_fib_level", 0.35))
    except (KeyError, TypeError, ValueError):
        return None
    if box_top <= box_bot or direction not in (1, -1):
        return None
    rng = box_top - box_bot
    if direction == 1:
        return float(box_top - rng * bos_lvl)
    return float(box_bot + rng * bos_lvl)


def _ext_bos_fib_ratio_label(cfg: object | None) -> str:
    try:
        ratio = float(getattr(cfg, "ext_bos_fib_level", 0.35) if cfg is not None else 0.35)
    except (TypeError, ValueError):
        ratio = 0.35
    return _fib_ratio_display(ratio)


def _wave_color_by_dir(wave: dict, idx: int, cfg: object | None = None) -> str:
    """
    Barva pozadi vlny (PNG + Plotly HTML):
      - WF continuation (wave_origin=wf_continuation): cerna
      - EXT: modra (#4682B4, shodne se summary EXT)
      - WAVE_TWO_SIDED counter: zluta (WAVE_TWO_SIDED_WAVE_BG_COLOR)
      - Bull: zelena #1b5e20
      - Bear: hneda #7b3f00
    S jemnym stinovanim (3 odstiny) podle poradi v seznamu waves.
    """
    from strategy.wick_fakeout import WAVE_ORIGIN_WF

    shade = idx % 3
    if str(wave.get("wave_origin", "")) == WAVE_ORIGIN_WF:
        return WAVE_WF_CONTINUATION_BG_COLOR

    if _wave_is_ext(wave, cfg):
        base = "#4682B4"
        variants = [
            _mix_hex(base, "#90caf9", 0.22),
            base,
            _mix_hex(base, "#1e3a5f", 0.28),
        ]
        return variants[shade]

    if bool(
        wave.get("is_two_sided_counter")
        or wave.get("_two_sided_counter")
        or wave.get("two_sided_show")
    ):
        base = WAVE_TWO_SIDED_WAVE_BG_COLOR
        variants = [
            _mix_hex(base, "#fff9c4", 0.35),
            base,
            _mix_hex(base, "#f9a825", 0.22),
        ]
        return variants[shade]

    dir_raw = str(wave.get("dir", "")).lower()
    is_bull = dir_raw in ("up", "bull", "buy", "1", "long")
    if is_bull:
        base = "#1b5e20"
        # Lepsi rozliseni sousednich bull vln.
        variants = [
            _mix_hex(base, "#66bb6a", 0.16),
            base,
            _mix_hex(base, "#0f2f10", 0.22),
        ]
    else:
        base = "#7b3f00"
        # Lepsi rozliseni sousednich bear vln.
        variants = [
            _mix_hex(base, "#bc8f5a", 0.18),
            base,
            _mix_hex(base, "#3a1d00", 0.25),
        ]
    return variants[shade]


def plot_waves_structure(
    df_window: pd.DataFrame,
    waves: List[dict],
    closed_trades: list,
    bot_name: str,
    *,
    bos_points: Optional[Sequence[tuple]] = None,
    save_path: Optional[Path] = None,
    interactive_html_path: Optional[Path] = None,
    show: bool = False,
    fib_levels_caption: Optional[str] = None,
    pending_events: Optional[List[dict]] = None,
    entry_fib_ratio: Optional[float] = None,
    sl_fib_ratio: Optional[float] = None,
    cfg: object | None = None,
    bos_wave_times: Optional[set[str]] = None,
) -> Optional[Path]:
    """
    Vizualizace orezaneho useku: close, obdelniky vlny (box), vertikala birth,
    entry/exit markery pro obchody v okne. Volitelne BOS flipy: ``bos_points`` =
    seznam ``(time_flip, swing_price [, popisek [, time_segment_start]])`` —
    cerna vodorovna **usecka** od leveho okraje boxu vlny (``draw_left``, prip.
    bar narozeni) po bar BOS prurazu (close flip / zavreni pozic).
    Bez ``time_segment_start`` (zastarale trojice) se usecka taha od ``time_flip``
    doprava jako drive.

    Kazda vlna ma vlastni barvu (PNG i Plotly HTML): EXT modra, UP zelena, DOWN hneda.
    BUY/SELL vstupy zustavaji zelene/cervene.
    Osa X je cas ze sloupce time (datum/cas CSV), ne index baru.

    Ve vlnach se vykresli signalove urovne fib50 (vstup), sl, tp (z detekce vlny).
    entry_fib_ratio / sl_fib_ratio: popisky u vodorovnych car (napr. 0,5 a 0,8) z BotConfig.
    Obchody se vizualne svazuji s vlnou pres shodu `wave_time` (obrys vstupniho markeru).
    `pending_events`: volitelne udalosti z backtestu (vznik / expirace / prune pendingu).

    df_window: OHLC + time; waves: draw_left_win, draw_right_win, box_top/bottom,
               birth_win (index v okne nebo None).
    """
    if df_window is None or df_window.empty:
        print(f"[plot-waves] {bot_name}: df_window prazdny")
        return None

    df = df_window.reset_index(drop=True)
    if "time" not in df.columns or "close" not in df.columns:
        print(f"[plot-waves] {bot_name}: chybi sloupce time/close")
        return None

    n = len(df)
    ts = pd.to_datetime(df["time"])
    ts_np = ts.to_numpy()
    tnum = mdates.date2num(ts_np)

    bar_td = _median_bar_timedelta(df["time"])
    bar_width_days = max(bar_td.total_seconds() / 86400.0, 1e-9)
    omit_mpl = save_path is None and not show and interactive_html_path is not None
    if omit_mpl and go is None:
        print(f"[plot-waves] {bot_name}: PNG vypnuty a Plotly chybi — nic neexportuji")
        return None

    pending_list = pending_events if pending_events is not None else []

    from backtest.waves_plotly_figure import _wave_visible_in_html_plot

    wave_color_by_time: dict[str, str] = {}
    for iw, w in enumerate(waves):
        wt_key = w.get("wave_time")
        if wt_key is not None:
            wave_color_by_time[str(wt_key)] = _wave_color_by_dir(w, iw, cfg)

    saved = None
    if not omit_mpl:
        fig, ax = plt.subplots(figsize=(15, 7))
        ax.plot(ts, df["close"], color="#333333", linewidth=1.1, label="Close", zorder=1)

        for iw, w in enumerate(waves):
            if not _wave_visible_in_html_plot(w, cfg, bos_wave_times=bos_wave_times):
                continue
            bt = float(w.get("box_top", 0))
            bb = float(w.get("box_bottom", 0))
            if bt < bb:
                bt, bb = bb, bt
            dl = int(np.clip(int(w["draw_left_win"]), 0, n - 1))
            dr = int(np.clip(int(w["draw_right_win"]), 0, n - 1))
            fill_col = _wave_color_by_dir(w, iw, cfg)
            width = float(max(dr - dl + 1, 1))
            rect_x0 = tnum[dl] - 0.45 * bar_width_days
            rect_w = width * bar_width_days * 0.9
            ax.add_patch(
                Rectangle(
                    (rect_x0, bb),
                    rect_w,
                    bt - bb,
                    facecolor=fill_col,
                    edgecolor=fill_col,
                    linewidth=1.45,
                    alpha=0.36,
                    zorder=1,
                )
            )
            bi = w.get("birth_win")
            if bi is not None:
                bi = int(bi)
                if 0 <= bi < n:
                    ax.axvline(
                        ts_np[bi],
                        color=fill_col,
                        linestyle=":",
                        linewidth=1.6,
                        alpha=0.95,
                        zorder=2,
                    )

            # Signalove urovne z vlny — vstup/SL Fib barvy fixni (#928659 / #808080), TP neutralni.
            xw0, xw1 = ts_np[dl], ts_np[dr]
            x_mid_num = (mdates.date2num(xw0) + mdates.date2num(xw1)) / 2.0
            x_mid = mdates.num2date(x_mid_num)

            idx_label = w.get("index_in_trend")
            if idx_label is not None and int(idx_label) > 0:
                wdir = int(w.get("dir", 0) or 0)
                if wdir == 1:
                    y_lbl = float(bb)
                    va = "top"
                elif wdir == -1:
                    y_lbl = float(bt)
                    va = "bottom"
                else:
                    y_lbl = (float(bb) + float(bt)) / 2.0
                    va = "center"
                ax.text(
                    x_mid,
                    y_lbl,
                    str(int(idx_label)),
                    fontsize=13,
                    fontweight="bold",
                    ha="center",
                    va=va,
                    color=WAVE_TREND_INDEX_LABEL_COLOR,
                    zorder=10,
                )

            def _mid_label(yv: float, txt: str, col: str) -> None:
                # Jen text — bez bbox/pozadí; ~2× předchozí „poloviční“ velikost, tučné kvůli čitelnosti.
                ax.text(
                    x_mid,
                    float(yv),
                    txt,
                    fontsize=12,
                    fontweight="bold",
                    ha="center",
                    va="center",
                    color=col,
                    zorder=9,
                )

            y_fib = w.get("fib50")
            if y_fib is not None:
                yv = float(y_fib)
                ax.plot(
                    [xw0, xw1],
                    [yv, yv],
                    color="#928659",
                    linestyle="-",
                    linewidth=1.45,
                    alpha=0.95,
                    zorder=2,
                    solid_capstyle="round",
                )
                if entry_fib_ratio is not None:
                    _mid_label(yv, _fib_ratio_display(entry_fib_ratio), "#928659")

            y_sl = w.get("sl")
            if y_sl is not None:
                yv = float(y_sl)
                ax.plot(
                    [xw0, xw1],
                    [yv, yv],
                    color="#808080",
                    linestyle="-",
                    linewidth=1.35,
                    alpha=0.95,
                    zorder=2,
                    solid_capstyle="round",
                )
                if sl_fib_ratio is not None:
                    _mid_label(yv, _fib_ratio_display(sl_fib_ratio), "#808080")

            y_tp = w.get("wave_target_tp_price")
            if y_tp is None:
                y_tp = w.get("tp")
            if y_tp is not None:
                ax.plot(
                    [xw0, xw1],
                    [float(y_tp), float(y_tp)],
                    color="#5c6d7a",
                    linestyle="-",
                    linewidth=1.05,
                    alpha=0.75,
                    zorder=2,
                )
            fab = w.get("fib_abort")
            if fab is not None:
                lvl_m = _mix_hex(fill_col, "#000000", 0.35)
                ax.plot(
                    [xw0, xw1],
                    [float(fab), float(fab)],
                    color=lvl_m,
                    linestyle=":",
                    linewidth=1.05,
                    alpha=0.72,
                    zorder=2,
                )

        for t in closed_trades:
            entry_time = pd.Timestamp(t.entry_time)
            close_time = pd.Timestamp(t.close_time)
            is_buy = int(t.dir) == 1
            ie = _nearest_bar_ix(ts, entry_time)
            ic = _nearest_bar_ix(ts, close_time)
            is_pp = _is_pp_trade(t)
            is_ts = _is_two_sided_trade(t)
            is_wc = _is_wave_counter_trade(t)
            is_tp = str(getattr(t, "close_reason", "")).upper() == "TP"
            _pal = _trade_plot_palette(t, is_buy=is_buy, is_tp=is_tp)
            col_trade = _pal["col"]
            sl_col = _pal["sl_col"]
            tp_col = _pal["tp_col"]
            pos_col = _pal["pos_col"]
            ec = _pal["exit_col"]
            # Zvysrazneni samotne pozice: silnejsi spojnice entry->exit.
            ax.plot(
                [ts_np[ie], ts_np[ic]],
                [float(t.entry_price), float(t.close_price)],
                color=pos_col,
                linewidth=2.6,
                alpha=0.9,
                zorder=4,
            )
            # Vykresli pasmo rizika/targetu v case obchodu pro lepsi citelnost EP/SL/TP.
            # POZN.: t.tp muze byt None pri tp_mode = WAVE_TARGET_N / BOS_EXIT_PRIORITY.
            _ep = float(t.entry_price)
            _sl_v = float(t.sl)
            _vals = [_ep, _sl_v]
            if t.tp is not None:
                _vals.append(float(t.tp))
            y_lo = min(_vals)
            y_hi = max(_vals)
            ax.add_patch(
                Rectangle(
                    (mdates.date2num(ts_np[ie]), y_lo),
                    max(mdates.date2num(ts_np[ic]) - mdates.date2num(ts_np[ie]), 1e-9),
                    max(y_hi - y_lo, 1e-9),
                    facecolor=pos_col,
                    edgecolor="none",
                    alpha=0.07,
                    zorder=3,
                )
            )
            # SL/TP cary pres dobu obchodu.
            ax.hlines(
                y=float(t.sl),
                xmin=ts_np[ie],
                xmax=ts_np[ic],
                colors=sl_col,
                linestyles="-",
                linewidth=2.2,
                alpha=1.0,
                zorder=4,
            )
            if t.tp is not None:
                ax.hlines(
                    y=float(t.tp),
                    xmin=ts_np[ie],
                    xmax=ts_np[ic],
                    colors=tp_col,
                    linestyles="-",
                    linewidth=2.2,
                    alpha=1.0,
                    zorder=4,
                )
            ring = wave_color_by_time.get(str(getattr(t, "wave_time", "")), None)
            glow = _trade_plot_glow(t)
            if glow is not None:
                ax.scatter(
                    [ts_np[ie]],
                    [float(t.entry_price)],
                    marker="o",
                    color=glow,
                    s=210,
                    zorder=5,
                    alpha=0.42,
                    edgecolors="none",
                )
            m_entry = _entry_marker_mpl(getattr(t, "entry_type", None), is_buy)
            edge_ring = (
                "#1976d2"
                if is_pp
                else (
                    WAVE_COUNTER_PLOT_COLOR
                    if is_wc
                    else (
                        "#ffffff"
                        if (
                            _is_ext_counter_time_trade(t)
                            or _is_ext_counter_bos_trade(t)
                            or _is_bos_trade(t)
                        )
                        else (WAVE_TWO_SIDED_COLOR if is_ts else ring)
                    )
                )
            )
            ax.scatter(
                [ts_np[ie]],
                [float(t.entry_price)],
                marker=m_entry,
                color=col_trade,
                s=84 if m_entry in ("s", "D") else 78,
                zorder=6,
                edgecolors=(edge_ring if edge_ring is not None else "white"),
                linewidths=2.4 if (is_pp or ring is not None) else 1.0,
            )
            if is_pp:
                ax.annotate(
                    "PP",
                    xy=(ts_np[ie], float(t.entry_price)),
                    xytext=(5, 5),
                    textcoords="offset points",
                    fontsize=8,
                    fontweight="bold",
                    color="#0d47a1",
                    bbox=dict(
                        boxstyle="round,pad=0.22",
                        facecolor="#e3f2fd",
                        edgecolor="#1565c0",
                        linewidth=1.1,
                        alpha=0.96,
                    ),
                    zorder=7,
                )
            elif is_ts:
                ax.annotate(
                    "WAVE_TWO_SIDED",
                    xy=(ts_np[ie], float(t.entry_price)),
                    xytext=(5, 5),
                    textcoords="offset points",
                    fontsize=7,
                    fontweight="bold",
                    color="#e8f5e9",
                    bbox=dict(
                        boxstyle="round,pad=0.22",
                        facecolor=WAVE_TWO_SIDED_COLOR,
                        edgecolor="#1b5e20",
                        linewidth=1.1,
                        alpha=0.96,
                    ),
                    zorder=7,
                )
            elif is_wc:
                ax.annotate(
                    "W_C",
                    xy=(ts_np[ie], float(t.entry_price)),
                    xytext=(5, 5),
                    textcoords="offset points",
                    fontsize=8,
                    fontweight="bold",
                    color="#ffffff",
                    bbox=dict(
                        boxstyle="round,pad=0.22",
                        facecolor=WAVE_COUNTER_PLOT_COLOR,
                        edgecolor="#0f2f10",
                        linewidth=1.1,
                        alpha=0.96,
                    ),
                    zorder=7,
                )
            else:
                ax.annotate(
                    _entry_type_hover_letter(getattr(t, "entry_type", None)),
                    xy=(ts_np[ie], float(t.entry_price)),
                    xytext=(5, 5),
                    textcoords="offset points",
                    fontsize=7,
                    fontweight="bold",
                    color="white",
                    bbox=dict(boxstyle="circle,pad=0.12", facecolor="#212121", edgecolor="none", alpha=0.88),
                    zorder=7,
                )
            ax.scatter(
                [ts_np[ic]],
                [float(t.close_price)],
                marker="x",
                color=ec,
                s=74,
                linewidths=2.0,
                zorder=6,
            )

        for ev in pending_list:
            try:
                biw = int(ev["bar_win"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (0 <= biw < n):
                continue
            tx = ts_np[biw]
            epv = float(ev.get("ep", 0.0))
            kind = str(ev.get("kind", ""))
            if kind == "pending_created":
                ax.scatter(
                    [tx],
                    [epv],
                    marker="o",
                    s=36,
                    facecolors="none",
                    edgecolors="#1565c0",
                    linewidths=1.35,
                    zorder=5,
                    alpha=0.95,
                )
            elif kind == "pending_expired":
                ax.scatter(
                    [tx],
                    [epv],
                    marker="x",
                    s=52,
                    color="#6d4c41",
                    linewidths=1.8,
                    zorder=5,
                    alpha=0.9,
                )
            elif kind == "pending_pruned":
                ax.scatter(
                    [tx],
                    [epv],
                    marker="s",
                    s=34,
                    facecolors="#ef6c00",
                    edgecolors="#e65100",
                    linewidths=0.9,
                    zorder=5,
                    alpha=0.95,
                )

        if bos_points:
            ts_end = ts_np[-1]
            bos_legend_done = False
            for bp in bos_points:
                if bp is None or len(bp) < 2:
                    continue
                t_flip = bp[0]
                lvl = float(bp[1])
                lbl = (bp[2] if len(bp) > 2 else "BOS swing")[:72]
                use_seg = len(bp) > 3 and bp[3] is not None
                i1 = _nearest_bar_ix(ts, pd.Timestamp(t_flip))
                if use_seg:
                    i0 = _nearest_bar_ix(ts, pd.Timestamp(bp[3]))
                    if i0 > i1:
                        i0, i1 = i1, i0
                    if i0 == i1:
                        continue
                    t_a, t_b = ts_np[i0], ts_np[i1]
                else:
                    t_a, t_b = ts_np[i1], ts_end
                leg = "BOS swing (close flip)" if not bos_legend_done else "_nolegend_"
                bos_legend_done = True
                ax.plot(
                    [t_a, t_b],
                    [lvl, lvl],
                    color="black",
                    linewidth=1.85,
                    linestyle="-",
                    zorder=6,
                    label=leg,
                )
                ax.annotate(
                    lbl,
                    xy=(t_b, lvl),
                    xytext=(5, 4),
                    textcoords="offset points",
                    fontsize=7,
                    color="black",
                    clip_on=True,
                    zorder=7,
                )

        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(ax.xaxis.get_major_locator()))
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=35, ha="right", fontsize=8)
        fig.autofmt_xdate(bottom=0.22)
        ax.set_xlabel("Čas (ze sloupce time v CSV)")
        ax.set_ylabel("Price")
        title = f"{bot_name} — struktura vln + obchody ({n} baru)"
        if fib_levels_caption:
            title = f"{title}\n{fib_levels_caption}"
        ax.set_title(title, fontsize=10)
        ax.grid(True, alpha=0.25)
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            nleg = min(len(handles), 14)
            ax.legend(handles[:nleg], labels[:nleg], fontsize=8, loc="upper left")

        plt.tight_layout()

        saved = None
        if save_path is not None:
            save_path = Path(save_path)
            save_path.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(save_path, dpi=130, bbox_inches="tight")
            saved = save_path
            print(f"[plot-waves] PNG ulozen: {save_path}")

    if interactive_html_path is not None and go is not None:
        interactive_html_path = Path(interactive_html_path)
        interactive_html_path.parent.mkdir(parents=True, exist_ok=True)
        from backtest.waves_plotly_figure import build_waves_structure_plotly_figure

        fig_html = build_waves_structure_plotly_figure(
            df=df,
            n=n,
            bar_td=bar_td,
            waves=waves,
            closed_trades=closed_trades,
            bot_name=bot_name,
            wave_color_by_time=wave_color_by_time,
            pending_list=pending_list,
            bos_points=bos_points,
            fib_levels_caption=fib_levels_caption,
            entry_fib_ratio=entry_fib_ratio,
            sl_fib_ratio=sl_fib_ratio,
            cfg=cfg,
            bos_wave_times=bos_wave_times,
        )
        if fig_html is not None:
            _write_plotly_html_fullsize(fig_html, interactive_html_path)
            print(f"[plot-waves] HTML ulozen: {interactive_html_path}")
    elif interactive_html_path is not None:
        print("[plot-waves] Plotly neni dostupny — HTML se neulozil")

    if not omit_mpl:
        if show:
            plt.show()
        else:
            plt.close(fig)

    return saved


def plot_price_with_trades(
    price_df: pd.DataFrame,
    closed_trades: list,
    bot_name: str,
    save_path: Optional[Path] = None,
    interactive_html_path: Optional[Path] = None,
    show: bool = False,
) -> Optional[Path]:
    """
    Vykresli cenovy graf + overlay obchodu:
      - close cena
      - entry/exit markery
      - SL/TP usecky od entry do close
      - volitelne Plotly HTML (interaktivni zoom)
    """
    if price_df is None or price_df.empty:
        print(f"[plot] {bot_name}: price_df je prazdny, nic nevykreslim")
        return None
    if not closed_trades:
        print(f"[plot] {bot_name}: zadne trades, nic nevykreslim")
        return None

    df = price_df.copy().reset_index(drop=True)
    if "time" not in df.columns:
        print(f"[plot] {bot_name}: chybi sloupec 'time'")
        return None

    df["time"] = pd.to_datetime(df["time"])

    html_only = (
        save_path is None
        and not show
        and interactive_html_path is not None
    )

    if not html_only:
        fig, ax = plt.subplots(figsize=(14, 7))
        ax.plot(df["time"], df["close"], color="#444444", linewidth=1.0, label="Close")

        plotted = 0
        for t in closed_trades:
            entry_time = pd.Timestamp(t.entry_time)
            close_time = pd.Timestamp(t.close_time)
            is_buy = int(t.dir) == 1
            is_pp = _is_pp_trade(t)
            is_tp = str(getattr(t, "close_reason", "")).upper() == "TP"
            _pal = _trade_plot_palette(t, is_buy=is_buy, is_tp=is_tp)
            if is_pp:
                entry_color = _pal["col"]
                exit_color = _pal["exit_col"]
                sl_line = _pal["sl_col"]
                tp_line = _pal["tp_col"]
            else:
                entry_color = _pal["col"]
                exit_color = _pal["exit_col"]
                sl_line = _pal["sl_col"]
                tp_line = _pal["tp_col"]

            m_entry = _entry_marker_mpl(getattr(t, "entry_type", None), is_buy)
            sz = 58 if m_entry in ("s", "D") else 55
            kw = dict(
                marker=m_entry,
                color=entry_color,
                s=sz,
                alpha=0.9,
                label="Entry BUY" if (plotted == 0 and is_buy) else ("Entry SELL" if plotted == 0 else None),
            )
            if is_pp:
                kw["edgecolors"] = "#0d47a1"
                kw["linewidths"] = 1.6
            ax.scatter([entry_time], [float(t.entry_price)], **kw)
            if is_pp:
                ax.annotate(
                    "PP",
                    xy=(entry_time, float(t.entry_price)),
                    xytext=(4, 6),
                    textcoords="offset points",
                    fontsize=8,
                    fontweight="bold",
                    color="#0d47a1",
                    bbox=dict(
                        boxstyle="round,pad=0.2",
                        facecolor="#e3f2fd",
                        edgecolor="#1565c0",
                        linewidth=1.0,
                        alpha=0.95,
                    ),
                )
            ax.scatter(
                [close_time], [float(t.close_price)],
                marker="x", color=exit_color, s=50, alpha=0.9,
                label="Exit",
            )

            ax.plot(
                [entry_time, close_time],
                [float(t.entry_price), float(t.close_price)],
                color=entry_color, linewidth=1.0, alpha=0.45,
            )

            ax.hlines(
                y=float(t.sl), xmin=entry_time, xmax=close_time,
                colors=sl_line, linestyles="--", linewidth=0.9, alpha=0.7,
            )
            if t.tp is not None:
                ax.hlines(
                    y=float(t.tp), xmin=entry_time, xmax=close_time,
                    colors=tp_line, linestyles="--", linewidth=0.9, alpha=0.7,
                )

            plotted += 1

        ax.set_title(f"{bot_name} - cena + obchody (entries/exits + SL/TP)")
        ax.set_xlabel("Time")
        ax.set_ylabel("Price")
        ax.grid(True, alpha=0.25)
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha="right")

        handles, labels = ax.get_legend_handles_labels()
        uniq = {}
        for h, l in zip(handles, labels):
            if l and l not in uniq:
                uniq[l] = h
        if uniq:
            ax.legend(uniq.values(), uniq.keys(), fontsize=8, loc="best")

        plt.tight_layout()

        saved = None
        if save_path is not None:
            save_path = Path(save_path)
            save_path.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(save_path, dpi=130, bbox_inches="tight")
            saved = save_path
            print(f"[plot] Cena + obchody ulozeno: {save_path}")

        if show:
            plt.show()
        else:
            plt.close(fig)
    else:
        saved = None

    if interactive_html_path is not None:
        if go is None:
            print(f"[plot] {bot_name}: Plotly neni dostupny — HTML cena+obchody se neulozil")
        else:
            interactive_html_path = Path(interactive_html_path)
            interactive_html_path.parent.mkdir(parents=True, exist_ok=True)
            ts = pd.to_datetime(df["time"])
            fig_h = go.Figure()
            fig_h.add_trace(
                go.Scatter(
                    x=ts,
                    y=df["close"],
                    mode="lines",
                    name="Close",
                    line=dict(color="#444444", width=1),
                    hovertemplate="%{x}<br>close: %{y:.5f}<extra></extra>",
                )
            )
            leg_first = {"eb": True, "es": True, "epp": True, "ex": True, "sl": True, "tp": True}
            for t in closed_trades:
                entry_time = pd.Timestamp(t.entry_time)
                close_time = pd.Timestamp(t.close_time)
                is_buy = int(t.dir) == 1
                is_pp = _is_pp_trade(t)
                is_ts = _is_two_sided_trade(t)
                is_wc = _is_wave_counter_trade(t)
                is_tp = str(getattr(t, "close_reason", "")).upper() == "TP"
                _pal = _trade_plot_palette(t, is_buy=is_buy, is_tp=is_tp)
                entry_color = _pal["col"]
                exit_color = _pal["exit_col"]
                sl_c = _pal["sl_col"]
                tp_c = _pal["tp_col"]
                fig_h.add_trace(
                    go.Scatter(
                        x=[entry_time, close_time],
                        y=[float(t.entry_price), float(t.close_price)],
                        mode="lines",
                        line=dict(color=entry_color, width=1),
                        opacity=0.45,
                        showlegend=False,
                        hoverinfo="skip",
                    )
                )
                sym_e = _entry_marker_plotly(getattr(t, "entry_type", None), is_buy)
                sz_e = 11
                if is_pp:
                    show_e = leg_first["epp"]
                    leg_first["epp"] = False
                    leg_name = "Entry PP"
                    hov_pp = "<b style='color:#0d47a1'>PP</b><br>"
                elif is_ts:
                    show_e = leg_first["eb"] if is_buy else leg_first["es"]
                    if is_buy:
                        leg_first["eb"] = False
                    else:
                        leg_first["es"] = False
                    leg_name = "Entry WAVE_TWO_SIDED"
                    hov_pp = _trade_hover_kind_line(t)
                elif is_wc:
                    show_e = leg_first["eb"] if is_buy else leg_first["es"]
                    if is_buy:
                        leg_first["eb"] = False
                    else:
                        leg_first["es"] = False
                    leg_name = "Entry W_C"
                    hov_pp = _trade_hover_kind_line(t)
                elif _is_bos_trade(t) or _is_ext_counter_bos_trade(t) or _is_ext_counter_time_trade(t):
                    show_e = leg_first["eb"] if is_buy else leg_first["es"]
                    if is_buy:
                        leg_first["eb"] = False
                    else:
                        leg_first["es"] = False
                    leg_name = "Entry BOS/EXT_BOS"
                    hov_pp = _trade_hover_kind_line(t)
                else:
                    show_e = leg_first["eb"] if is_buy else leg_first["es"]
                    if is_buy:
                        leg_first["eb"] = False
                    else:
                        leg_first["es"] = False
                    leg_name = "Entry BUY" if is_buy else "Entry SELL"
                    hov_pp = ""
                m_entry = dict(symbol=sym_e, size=sz_e, color=entry_color)
                if is_pp:
                    m_entry["line"] = dict(width=1.8, color="#0d47a1")
                elif is_ts:
                    m_entry["line"] = dict(width=1.8, color=WAVE_TWO_SIDED_COLOR)
                elif is_wc:
                    m_entry["line"] = dict(width=1.8, color=WAVE_COUNTER_PLOT_COLOR)
                fig_h.add_trace(
                    go.Scatter(
                        x=[entry_time],
                        y=[float(t.entry_price)],
                        mode="markers",
                        marker=m_entry,
                        name=leg_name,
                        showlegend=show_e,
                        hovertemplate=(
                            f"{hov_pp}entry {'BUY' if is_buy else 'SELL'} @ %{{y:.5f}}<extra></extra>"
                        ),
                    )
                )
                if is_pp:
                    fig_h.add_trace(
                        go.Scatter(
                            x=[entry_time],
                            y=[float(t.entry_price)],
                            mode="text",
                            text=["PP"],
                            textposition="top center",
                            textfont=dict(color="#0d47a1", size=11, family="Arial Black"),
                            showlegend=False,
                            hoverinfo="skip",
                        )
                    )
                elif is_ts:
                    fig_h.add_trace(
                        go.Scatter(
                            x=[entry_time],
                            y=[float(t.entry_price)],
                            mode="text",
                            text=["WAVE_TWO_SIDED"],
                            textposition="top center",
                            textfont=dict(color="#e8f5e9", size=9, family="Arial Black"),
                            showlegend=False,
                            hoverinfo="skip",
                        )
                    )
                elif is_wc:
                    fig_h.add_trace(
                        go.Scatter(
                            x=[entry_time],
                            y=[float(t.entry_price)],
                            mode="text",
                            text=["W_C"],
                            textposition="top center",
                            textfont=dict(
                                color=WAVE_COUNTER_PLOT_COLOR,
                                size=10,
                                family="Arial Black",
                            ),
                            showlegend=False,
                            hoverinfo="skip",
                        )
                    )
                fig_h.add_trace(
                    go.Scatter(
                        x=[close_time],
                        y=[float(t.close_price)],
                        mode="markers",
                        marker=dict(
                            symbol=_exit_marker_plotly(getattr(t, "close_reason", "")),
                            size=9,
                            color=exit_color,
                        ),
                        name="Exit",
                        showlegend=leg_first["ex"],
                        hovertemplate=(
                            f"{('<b>PP</b><br>' if is_pp else '')}"
                            f"{('<b>WAVE_TWO_SIDED</b><br>' if is_ts else '')}"
                            f"exit {t.close_reason} @ %{{y:.5f}}<extra></extra>"
                        ),
                    )
                )
                leg_first["ex"] = False
                fig_h.add_trace(
                    go.Scatter(
                        x=[entry_time, close_time],
                        y=[float(t.sl), float(t.sl)],
                        mode="lines",
                        line=dict(color=sl_c, width=1, dash="dash"),
                        name="SL",
                        showlegend=leg_first["sl"],
                        hovertemplate="SL %{y:.5f}<extra></extra>",
                    )
                )
                leg_first["sl"] = False
                if t.tp is not None:
                    fig_h.add_trace(
                        go.Scatter(
                            x=[entry_time, close_time],
                            y=[float(t.tp), float(t.tp)],
                            mode="lines",
                            line=dict(color=tp_c, width=1, dash="dash"),
                            name="TP",
                            showlegend=leg_first["tp"],
                            hovertemplate="TP %{y:.5f}<extra></extra>",
                        )
                    )
                    leg_first["tp"] = False

            fig_h.update_layout(
                title=f"{bot_name} — cena + obchody (interaktivně)",
                xaxis_title="Time",
                yaxis_title="Price",
                template="plotly_white",
                hovermode="closest",
            )
            _write_plotly_html_fullsize(fig_h, interactive_html_path)
            print(f"[plot] Cena + obchody HTML: {interactive_html_path}")

    return saved

# ---------------------------------------------------------------------------
# Helper: max drawdown v procentech
# ---------------------------------------------------------------------------

def _max_drawdown_pct(equity: pd.Series, initial: float) -> float:
    """Vrati max drawdown v procentech (zaporna hodnota)."""
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = (equity - peak) / peak * 100.0
    return float(dd.min())