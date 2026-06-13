"""ADX14 subplot helpers for backtest equity HTML figures."""

from __future__ import annotations

from typing import Optional

import pandas as pd

try:
    import plotly.graph_objects as go
except ImportError:
    go = None


def add_adx14_subplot(
    fig,
    adx14_df: pd.DataFrame,
    *,
    threshold: float = 1.3,
    gate_disabled_periods: Optional[list] = None,
    row: int = 3,
    col: int = 1,
) -> None:
    """Add ADX14 signal trace + threshold line + optional gate-off shading."""
    if go is None or adx14_df is None or adx14_df.empty:
        return

    fig.add_trace(
        go.Scatter(
            x=adx14_df["time"],
            y=adx14_df["adx14_signal"],
            mode="lines",
            name="ADX14 změna",
            line=dict(color="#437A22", width=1.8),
            customdata=adx14_df[["adx14", "adx14_change_pct"]].values
            if "adx14" in adx14_df.columns
            else None,
            hovertemplate=(
                "%{x}<br>ADX14 signal: %{y:.2f}"
                "<br>ADX14: %{customdata[0]:.2f}"
                "<br>změna: %{customdata[1]:.1f}%<extra></extra>"
                if "adx14" in adx14_df.columns
                else "%{x}<br>ADX14 signal: %{y:.2f}<extra></extra>"
            ),
        ),
        row=row,
        col=col,
    )
    fig.add_hline(
        y=threshold,
        line_dash="dash",
        line_color="#A13544",
        line_width=2,
        annotation_text=f"gate {threshold}",
        row=row,
        col=col,
    )

    if gate_disabled_periods:
        for x0, x1 in gate_disabled_periods:
            fig.add_vrect(
                x0=x0,
                x1=x1,
                fillcolor="rgba(161,53,68,0.12)",
                line_width=0,
                row=row,
                col=col,
            )

    fig.update_yaxes(title_text="ADX14 signál", row=row, col=col)


def adx14_plot_kwargs_from_df(df, cfg) -> dict:
    """Build ADX14 plot kwargs directly from OHLC dataframe (bez BacktestEngine)."""
    from pathlib import Path

    from runtime.adx14_live import dataframe_to_bars
    from strategy.adx14_change_indicator import compute_adx14_points, load_normalizer

    norm_path = Path(getattr(cfg, "adx14_change_normalizer_json", "runtime/adx14_normalizer.json"))
    if not norm_path.exists():
        return {}
    normalizer = load_normalizer(norm_path)
    points = compute_adx14_points(dataframe_to_bars(df), normalizer=normalizer)
    rows = []
    for p in points:
        if p.adx14_signal is None:
            continue
        rows.append(
            {
                "time": pd.to_datetime(p.day),
                "adx14": p.adx14,
                "adx14_change_pct": p.adx14_change_pct,
                "adx14_signal": p.adx14_signal,
            }
        )
    if not rows:
        return {}
    return {
        "adx14_df": pd.DataFrame(rows),
        "adx14_threshold": float(getattr(cfg, "adx14_disable_threshold", 1.3)),
        "gate_disabled_periods": None,
    }


def adx14_plot_kwargs_from_engine(engine, *, force_plot: bool = False, df=None, cfg=None) -> dict:
    """Build optional kwargs for equity HTML — full P.A. diagnostický report."""
    bot_cfg = cfg if cfg is not None else (engine.cfg if engine is not None else None)
    if bot_cfg is None:
        return {}
    enabled = bool(
        force_plot
        or getattr(bot_cfg, "adx14_change_enabled", False)
        or getattr(bot_cfg, "adx14_equity_gate_enabled", False)
    )
    if not enabled:
        return {}
    if df is None:
        return {}
    periods = None
    pnl_base_df = None
    if engine is not None:
        sim = getattr(engine, "adx14_sim", None)
        if sim is not None and sim.active:
            if getattr(bot_cfg, "adx14_equity_gate_enabled", False):
                periods = sim.gate_disabled_periods()
            if sim.pnl_tracker is not None:
                pnl_base_df = sim.base_pnl_dataframe()
    return {
        "pa_diagnostic_mode": True,
        "ohlc_df": df,
        "cfg": bot_cfg,
        "gate_disabled_periods": periods,
        "gate_threshold": float(getattr(bot_cfg, "adx14_disable_threshold", 1.3)),
        "pnl_base_df": pnl_base_df,
    }
