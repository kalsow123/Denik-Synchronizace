"""
Plotly figura struktury vln + obchodů (extrahováno z plot_waves_structure kvůli opakovanému použití).
Importuje pomocné funkce z backtest.plotting — volat až po plném načtení plotting (lazy import z plot_waves_structure).
"""
from __future__ import annotations

from typing import List, Optional, Sequence

import numpy as np
import pandas as pd

try:
    import plotly.graph_objects as go
except Exception:  # pragma: no cover
    go = None

from strategy.ext_logic import ext_bos_visual_left_bar
from backtest.plotting import (
    BOS_PLOT_COLOR,
    EXT_BOS_LEVEL_PLOT_COLOR,
    EXT_COUNTER_BOS_PLOT_COLOR,
    EXT_COUNTER_TIME_PLOT_COLOR,
    EXT_SECONDARY_ENTRY_PLOT_COLOR,
    WAVE_COUNTER_PLOT_COLOR,
    WAVE_TWO_SIDED_WAVE_BG_COLOR,
    WAVE_WF_CONTINUATION_BG_COLOR,
    _exit_marker_plotly,
    _entry_marker_plotly,
    _entry_type_hover_letter,
    _entry_type_label,
    _ext_bos_fib_ratio_label,
    _ext_bos_level_for_wave,
    _ext_secondary_entry_for_wave,
    _ext_secondary_fib_ratio_label,
    _fib_ratio_display,
    _is_bos_trade,
    _is_ext_counter_bos_trade,
    _is_ext_counter_time_trade,
    _is_pp_trade,
    _is_two_sided_trade,
    _is_wave_counter_trade,
    _hex_to_rgba,
    _mix_hex,
    _nearest_bar_ix,
    _pa_type_hover_label,
    _trade_hover_kind_line,
    _trade_plot_glow,
    _trade_plot_palette,
    WAVE_TREND_INDEX_LABEL_COLOR,
    WAVE_TWO_SIDED_COLOR,
    _wave_color_by_dir,
)
from strategy.wick_fakeout import WAVE_ORIGIN_WF

# Odstíny wave boxů (viz _wave_color_by_dir) — shodné s POPIS_STRATEGIE.txt / audit HTML.
_WAVE_BOX_UP_SHADES = ("#276d2c", "#1b5e20", "#18541c")
_WAVE_BOX_DOWN_SHADES = ("#874d10", "#7b3f00", "#6b3700")
_WAVE_BOX_EXT_SHADES = ("#5692c3", "#4682B4", "#3b6e9c")
_WAVE_BOX_TS_B_SHADES = ("#fff06b", WAVE_TWO_SIDED_WAVE_BG_COLOR, "#fedc36")


def _plot_x_segments(
    ts: pd.Series,
    dl: int,
    dr: int,
    bar_td,
    *,
    pad: float = 0.45,
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Souvislé úseky [dl, dr] bez kalendářních mezer (např. víkend)."""
    if dl > dr:
        dl, dr = dr, dl
    gap_limit = bar_td * 1.5
    out: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    seg_start = dl
    for i in range(dl + 1, dr + 1):
        if ts.iloc[i] - ts.iloc[i - 1] > gap_limit:
            out.append(
                (ts.iloc[seg_start] - bar_td * pad, ts.iloc[i - 1] + bar_td * pad)
            )
            seg_start = i
    out.append((ts.iloc[seg_start] - bar_td * pad, ts.iloc[dr] + bar_td * pad))
    return out


def _compute_data_gap_rangebreaks(ts: pd.Series, bar_td) -> list[dict]:
    """Rangebreaks z REALNYCH mezer v datech (vikend/svatek), ne z pevneho
    predpokladu obchodnich hodin.

    Pevny ``bounds=["fri 21:45","sun 23:05"]`` byl chybny: ruzni brokeri maji
    ruzny vikendovy rozvrh. Pro data, ktera obchoduji v patek az do 23:30 a
    znovu otevrou az v pondeli 00:00, predchozi rangebreak SCHOVAL realne
    patecni bary (21:45–23:30, vc. patecniho minima) a naopak nechal viditelny
    prazdny nedelni usek. Vysledek: "cena se v patek nevykreslila".

    Tato funkce skryje POUZE prazdne useky mezi dvema sousednimi realnymi bary
    (delta > 1.5 baru). Zadny realny bar se neskryje — sousedni bary po obou
    stranach mezery zustanou viditelne a graf na sebe plynule navazuje pres
    libovolny vikend/svatek nezavisle na rozvrhu brokera.
    """
    out: list[dict] = []
    if ts is None or len(ts) < 2:
        return out
    t = pd.to_datetime(pd.Series(ts)).reset_index(drop=True)
    try:
        thr = bar_td * 1.5
    except Exception:
        return out
    for i in range(1, len(t)):
        delta = t.iloc[i] - t.iloc[i - 1]
        if delta > thr:
            # Skryj [posledni_bar + 1 interval, dalsi_bar) — oba realne bary
            # zustanou viditelne, prazdny stred mezery zmizi.
            out.append(dict(bounds=[t.iloc[i - 1] + bar_td, t.iloc[i]]))
    return out


def _add_filled_rect_trace(
    fig_html: "go.Figure",
    x0: pd.Timestamp,
    x1: pd.Timestamp,
    y0: float,
    y1: float,
    *,
    color: str,
    opacity: float,
    line_width: float,
    line_dash: str = "solid",
) -> None:
    """Wave box jako Scatter fill — respektuje rangebreaks (add_shape rect ne)."""
    fig_html.add_trace(
        go.Scatter(
            x=[x0, x1, x1, x0, x0],
            y=[y0, y0, y1, y1, y0],
            mode="lines",
            fill="toself",
            fillcolor=_hex_to_rgba(color, opacity),
            line=dict(color=color, width=line_width, dash=line_dash),
            showlegend=False,
            hoverinfo="skip",
        )
    )


def _add_segmented_hline(
    fig_html: "go.Figure",
    ts: pd.Series,
    dl: int,
    dr: int,
    bar_td,
    y: float,
    *,
    trace_kw: dict,
) -> None:
    for x0, x1 in _plot_x_segments(ts, dl, dr, bar_td):
        fig_html.add_trace(
            go.Scatter(x=[x0, x1], y=[y, y], mode="lines", **trace_kw)
        )


def _wave_visible_in_html_plot(
    wave: dict,
    cfg: object | None,
    *,
    bos_wave_times: Optional[set[str]] = None,
) -> bool:
    """
    True pokud se ma vlna vykreslit v Plotly HTML (box + fib/SL/TP + label).
    Engine logiku nemeni — jen vizualni filtr podle flagu z detekce.
    """
    from backtest.visual_wave_filter import wave_passes_visual_filter

    return wave_passes_visual_filter(
        wave,
        cfg,
        bos_wave_times=bos_wave_times or set(),
        include_lock_trend_waves=True,
    )


def _waves_legend_marker(
    name: str,
    *,
    fill: str,
    border: str | None = None,
    symbol: str = "diamond",
    size: int = 11,
) -> "go.Scatter":
    """Dummy trace pro legendu barev (bez dat na ose)."""
    b = border if border is not None else fill
    return go.Scatter(
        x=[None],
        y=[None],
        mode="markers",
        name=name,
        showlegend=True,
        marker=dict(symbol=symbol, size=size, color=fill, line=dict(width=2.2, color=b)),
        hoverinfo="skip",
    )


def _append_waves_color_legend(fig_html: "go.Figure") -> None:
    """Dummy legend traces — pokrytí fill/border barev z audit HTML (viz POPIS_STRATEGIE.txt)."""
    # Wave box fill (square)
    for i, col in enumerate(_WAVE_BOX_UP_SHADES, start=1):
        fig_html.add_trace(
            _waves_legend_marker(
                f"Box UP wave odstín {i}",
                fill=col,
                border=col,
                symbol="square",
            )
        )
    for i, col in enumerate(_WAVE_BOX_DOWN_SHADES, start=1):
        fig_html.add_trace(
            _waves_legend_marker(
                f"Box DOWN wave odstín {i}",
                fill=col,
                border=col,
                symbol="square",
            )
        )
    fig_html.add_trace(
        _waves_legend_marker(
            "Box WF wave",
            fill=WAVE_WF_CONTINUATION_BG_COLOR,
            border=WAVE_WF_CONTINUATION_BG_COLOR,
            symbol="square",
        )
    )
    for i, col in enumerate(_WAVE_BOX_TS_B_SHADES, start=1):
        fig_html.add_trace(
            _waves_legend_marker(
                f"Box Two-sided B wave odstín {i}",
                fill=col,
                border=col,
                symbol="square",
            )
        )
    for i, col in enumerate(_WAVE_BOX_EXT_SHADES, start=1):
        fig_html.add_trace(
            _waves_legend_marker(
                f"Box EXT wave odstín {i}",
                fill=col,
                border=col,
                symbol="square",
            )
        )

    # Entry border (diamond fill = směr / typ obchodu)
    fig_html.add_trace(
        _waves_legend_marker(
            "PP entry (border)",
            fill="#1e88e5",
            border="#1976d2",
        )
    )
    fig_html.add_trace(
        _waves_legend_marker(
            "PP entry SELL (border)",
            fill="#1565c0",
            border="#1976d2",
        )
    )
    fig_html.add_trace(
        _waves_legend_marker(
            "EXT secondary entry (border #3b6e9c)",
            fill="#21a656",
            border="#3b6e9c",
        )
    )
    fig_html.add_trace(
        _waves_legend_marker(
            "EXT secondary entry (border #5692c3)",
            fill="#21a656",
            border="#5692c3",
        )
    )
    fig_html.add_trace(
        _waves_legend_marker(
            "Two-sided counter entry (border)",
            fill=WAVE_TWO_SIDED_COLOR,
            border=WAVE_TWO_SIDED_COLOR,
        )
    )
    fig_html.add_trace(
        _waves_legend_marker(
            "Two-sided B primary attempt (border)",
            fill="#d0453b",
            border="#fff06b",
        )
    )
    fig_html.add_trace(
        _waves_legend_marker(
            "BOS_ENTRY market (border)",
            fill=BOS_PLOT_COLOR,
            border="#ffffff",
        )
    )
    fig_html.add_trace(
        _waves_legend_marker(
            "WAVE_COUNTER entry (border)",
            fill=WAVE_COUNTER_PLOT_COLOR,
            border=WAVE_COUNTER_PLOT_COLOR,
        )
    )
    fig_html.add_trace(
        _waves_legend_marker(
            "WAVE entry ring (border = barva boxu vlny)",
            fill="#21a656",
            border="#1b5e20",
        )
    )

    # Diamond fill (MARKET / obchod)
    fig_html.add_trace(
        _waves_legend_marker("Fill BUY market", fill="#21a656", border="#1b5e20")
    )
    fig_html.add_trace(
        _waves_legend_marker("Fill SELL market (bear)", fill="#7b3f00", border="#6b3700")
    )
    fig_html.add_trace(
        _waves_legend_marker("Fill SELL two-sided counter", fill="#d0453b", border="#00332a")
    )
    fig_html.add_trace(
        _waves_legend_marker("Fill WF MARKET", fill="#000000", border="#000000")
    )
    fig_html.add_trace(
        _waves_legend_marker(
            "Fill EXT_COUNTER_TIME MARKET",
            fill=EXT_COUNTER_TIME_PLOT_COLOR,
            border=EXT_COUNTER_TIME_PLOT_COLOR,
        )
    )
    fig_html.add_trace(
        go.Scatter(
            x=[None],
            y=[None],
            mode="markers",
            name="Sumové (HH/HL side) a post EXT",
            marker=dict(size=0.1, color="rgba(0,0,0,0)"),
            showlegend=True,
            hoverinfo="skip",
        )
    )


def build_waves_structure_plotly_figure(
    *,
    df: pd.DataFrame,
    n: int,
    bar_td,
    waves: List[dict],
    closed_trades: list,
    bot_name: str,
    wave_color_by_time: dict[str, str],
    pending_list: list,
    bos_points: Optional[Sequence[tuple]],
    fib_levels_caption: Optional[str],
    entry_fib_ratio: Optional[float],
    sl_fib_ratio: Optional[float],
    cfg: object | None = None,
    bos_wave_times: Optional[set[str]] = None,
):
    """Stejná Plotly figura jako dříve uvnitř plot_waves_structure (HTML větev)."""
    if go is None:
        return None

    fig_html = go.Figure()
    bos_anns: list = []
    ts_plotly = pd.to_datetime(df["time"])
    fig_html.add_trace(
        go.Scatter(
            x=ts_plotly,
            y=df["close"].astype(float),
            mode="lines",
            name="Close",
            line=dict(color="#333333", width=1.2),
            # Pres vikendovy gap si Plotly pri hovermode="closest" nekdy vybira
            # cenovou krivku misto markeru obchodu, coz pusobi jako falesny cas entry.
            hoverinfo="skip",
        )
    )
    td_plotly = bar_td
    for iw, w in enumerate(waves):
        if not _wave_visible_in_html_plot(w, cfg, bos_wave_times=bos_wave_times):
            continue
        bt = float(w.get("box_top", 0))
        bb = float(w.get("box_bottom", 0))
        if bt < bb:
            bt, bb = bb, bt
        dl = int(np.clip(int(w["draw_left_win"]), 0, n - 1))
        dr = int(np.clip(int(w["draw_right_win"]), 0, n - 1))
        wt_key = w.get("wave_time")
        wcol = (
            wave_color_by_time.get(str(wt_key))
            if wt_key is not None
            else None
        )
        if wcol is None:
            wcol = _wave_color_by_dir(w, iw, cfg)
        is_wf = str(w.get("wave_origin", "")) == WAVE_ORIGIN_WF
        is_recon = bool(w.get("_visual_reconstructed"))
        if is_wf:
            box_opacity = 0.58
            box_line_width = 2.2
            box_line_dash = "solid"
        elif is_recon:
            box_opacity = 0.28
            box_line_width = 1.2
            box_line_dash = "dot"
        else:
            box_opacity = 0.34
            box_line_width = 1.6
            box_line_dash = "solid"
        for x0_p, x1_p in _plot_x_segments(ts_plotly, dl, dr, td_plotly):
            _add_filled_rect_trace(
                fig_html,
                x0_p,
                x1_p,
                bb,
                bt,
                color=wcol,
                opacity=box_opacity,
                line_width=box_line_width,
                line_dash=box_line_dash,
            )
        bi = w.get("birth_win")
        if bi is not None:
            bi = int(bi)
            if 0 <= bi < n:
                tx = ts_plotly.iloc[bi]
                fig_html.add_trace(
                    go.Scatter(
                        x=[tx, tx],
                        y=[bb, bt],
                        mode="lines",
                        line=dict(color=wcol, dash="dot", width=1.8),
                        showlegend=False,
                        hoverinfo="skip",
                    )
                )
        x_mid_idx = (dl + dr) // 2
        x_mid = ts_plotly.iloc[x_mid_idx]

        idx_label = w.get("index_in_trend")
        if is_wf:
            wdir = int(w.get("dir", 0) or 0)
            if wdir == 1:
                y_lbl = float(bb)
                txt_pos = "bottom center"
            elif wdir == -1:
                y_lbl = float(bt)
                txt_pos = "top center"
            else:
                y_lbl = (float(bb) + float(bt)) / 2.0
                txt_pos = "middle center"
            fig_html.add_trace(
                go.Scatter(
                    x=[x_mid],
                    y=[y_lbl],
                    mode="text",
                    text=["WF"],
                    textposition=txt_pos,
                    textfont=dict(
                        size=14,
                        color="#ffffff",
                        family="Arial Black",
                    ),
                    showlegend=False,
                    hovertemplate=(
                        "WF continuation (Wick Fakeout Recovery)"
                        "<extra></extra>"
                    ),
                )
            )
        elif idx_label is not None and int(idx_label) > 0:
            wdir = int(w.get("dir", 0) or 0)
            if wdir == 1:
                y_lbl = float(bb)
                txt_pos = "bottom center"
            elif wdir == -1:
                y_lbl = float(bt)
                txt_pos = "top center"
            else:
                y_lbl = (float(bb) + float(bt)) / 2.0
                txt_pos = "middle center"
            fig_html.add_trace(
                go.Scatter(
                    x=[x_mid],
                    y=[y_lbl],
                    mode="text",
                    text=[str(int(idx_label))],
                    textposition=txt_pos,
                    textfont=dict(
                        size=15,
                        color=WAVE_TREND_INDEX_LABEL_COLOR,
                        family="Arial Black",
                    ),
                    showlegend=False,
                    hovertemplate=(
                        f"vlna v trendu: {int(idx_label)}"
                        "<extra></extra>"
                    ),
                )
            )

        y_fib = w.get("fib50")
        if y_fib is not None:
            yv = float(y_fib)
            _add_segmented_hline(
                fig_html,
                ts_plotly,
                dl,
                dr,
                td_plotly,
                yv,
                trace_kw=dict(
                    line=dict(color="#928659", width=2.0),
                    opacity=0.95,
                    name="wave_entry_fib",
                    showlegend=False,
                    hovertemplate="entry fib %{y:.5f}<extra></extra>",
                ),
            )
            if entry_fib_ratio is not None:
                fig_html.add_trace(
                    go.Scatter(
                        x=[x_mid],
                        y=[yv],
                        mode="text",
                        text=[_fib_ratio_display(entry_fib_ratio)],
                        textposition="middle center",
                        textfont=dict(size=16, color="#928659", family="Arial Black"),
                        showlegend=False,
                        hoverinfo="skip",
                    )
                )

        y_ext_entry = _ext_secondary_entry_for_wave(w, cfg)
        if y_ext_entry is not None:
            yv = float(y_ext_entry)
            _add_segmented_hline(
                fig_html,
                ts_plotly,
                dl,
                dr,
                td_plotly,
                yv,
                trace_kw=dict(
                    line=dict(color=EXT_SECONDARY_ENTRY_PLOT_COLOR, width=2.0),
                    opacity=0.98,
                    name="ext_secondary_entry",
                    showlegend=False,
                    hovertemplate=(
                        f"EXT entry ({_ext_secondary_fib_ratio_label(cfg)}) %{{y:.5f}}<extra></extra>"
                    ),
                ),
            )
            fig_html.add_trace(
                go.Scatter(
                    x=[x_mid],
                    y=[yv],
                    mode="text",
                    text=[_ext_secondary_fib_ratio_label(cfg)],
                    textposition="middle center",
                    textfont=dict(
                        size=16,
                        color=EXT_SECONDARY_ENTRY_PLOT_COLOR,
                        family="Arial Black",
                    ),
                    showlegend=False,
                    hoverinfo="skip",
                )
            )

        y_slw = w.get("sl")
        if y_slw is not None:
            yv = float(y_slw)
            _add_segmented_hline(
                fig_html,
                ts_plotly,
                dl,
                dr,
                td_plotly,
                yv,
                trace_kw=dict(
                    line=dict(color="#808080", width=1.8),
                    opacity=0.95,
                    name="wave_sl_fib",
                    showlegend=False,
                    hovertemplate="SL fib %{y:.5f}<extra></extra>",
                ),
            )
            if sl_fib_ratio is not None:
                fig_html.add_trace(
                    go.Scatter(
                        x=[x_mid],
                        y=[yv],
                        mode="text",
                        text=[_fib_ratio_display(sl_fib_ratio)],
                        textposition="middle center",
                        textfont=dict(size=16, color="#808080", family="Arial Black"),
                        showlegend=False,
                        hoverinfo="skip",
                    )
                )

        y_ext_bos = _ext_bos_level_for_wave(w, cfg)
        if y_ext_bos is not None:
            yv = float(y_ext_bos)
            ext_bos_dl = ext_bos_visual_left_bar(
                w,
                birth_bar=bi if bi is not None else w.get("birth_win"),
                draw_left=dl,
                draw_right=dr,
            )
            if ext_bos_dl <= dr:
                ext_bos_mid = ts_plotly.iloc[(ext_bos_dl + dr) // 2]
                _add_segmented_hline(
                    fig_html,
                    ts_plotly,
                    ext_bos_dl,
                    dr,
                    td_plotly,
                    yv,
                    trace_kw=dict(
                        line=dict(color=EXT_BOS_LEVEL_PLOT_COLOR, width=2.0),
                        opacity=0.95,
                        name="ext_bos_level",
                        showlegend=False,
                        hovertemplate=(
                            f"EXT BOS ({_ext_bos_fib_ratio_label(cfg)}) %{{y:.5f}}<extra></extra>"
                        ),
                    ),
                )
                fig_html.add_trace(
                    go.Scatter(
                        x=[ext_bos_mid],
                        y=[yv],
                        mode="text",
                        text=[_ext_bos_fib_ratio_label(cfg)],
                        textposition="middle center",
                        textfont=dict(
                            size=16,
                            color=EXT_BOS_LEVEL_PLOT_COLOR,
                            family="Arial Black",
                        ),
                        showlegend=False,
                        hoverinfo="skip",
                    )
                )

        y_tpw = w.get("wave_target_tp_price")
        if y_tpw is None:
            y_tpw = w.get("tp")
        if y_tpw is not None:
            yv = float(y_tpw)
            _add_segmented_hline(
                fig_html,
                ts_plotly,
                dl,
                dr,
                td_plotly,
                yv,
                trace_kw=dict(
                    line=dict(color="#5c6d7a", width=1.2),
                    opacity=0.75,
                    name="wave_tp",
                    showlegend=False,
                    hovertemplate="TP %{y:.5f}<extra></extra>",
                ),
            )
        fab = w.get("fib_abort")
        if fab is not None:
            yf = float(fab)
            lvl_m = _mix_hex(wcol, "#000000", 0.35)
            _add_segmented_hline(
                fig_html,
                ts_plotly,
                dl,
                dr,
                td_plotly,
                yf,
                trace_kw=dict(
                    line=dict(color=lvl_m, width=1.2, dash="dot"),
                    opacity=0.72,
                    name="fib_abort",
                    showlegend=False,
                    hovertemplate="fib_abort %{y:.5f}<extra></extra>",
                ),
            )
    for ev in pending_list:
        try:
            biw = int(ev["bar_win"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= biw < n):
            continue
        txp = ts_plotly.iloc[biw]
        epv = float(ev.get("ep", 0.0))
        kind = str(ev.get("kind", ""))
        if kind == "pending_created":
            fig_html.add_trace(
                go.Scatter(
                    x=[txp],
                    y=[epv],
                    mode="markers",
                    marker=dict(
                        symbol="circle-open",
                        size=11,
                        color="#1565c0",
                        line=dict(width=2, color="#1565c0"),
                    ),
                    name="pend_new",
                    showlegend=False,
                    hovertemplate="pending vytvořen<br>%{y:.5f}<extra></extra>",
                )
            )
        elif kind == "pending_expired":
            fig_html.add_trace(
                go.Scatter(
                    x=[txp],
                    y=[epv],
                    mode="markers",
                    marker=dict(symbol="x", size=13, color="#6d4c41", line=dict(width=2)),
                    name="pend_exp",
                    showlegend=False,
                    hovertemplate="pending expirován<br>%{y:.5f}<extra></extra>",
                )
            )
        elif kind == "pending_pruned":
            fig_html.add_trace(
                go.Scatter(
                    x=[txp],
                    y=[epv],
                    mode="markers",
                    marker=dict(symbol="square", size=10, color="#ef6c00", line=dict(width=1)),
                    name="pend_prune",
                    showlegend=False,
                    hovertemplate="pending prune (cap)<br>%{y:.5f}<extra></extra>",
                )
            )
    for t in closed_trades:
        ie = _nearest_bar_ix(df["time"], t.entry_time)
        ic = _nearest_bar_ix(df["time"], t.close_time)
        is_buy = int(t.dir) == 1
        is_pp = _is_pp_trade(t)
        is_ts = _is_two_sided_trade(t)
        is_wc = _is_wave_counter_trade(t)
        is_tp = str(getattr(t, "close_reason", "")).upper() == "TP"
        _pal = _trade_plot_palette(t, is_buy=is_buy, is_tp=is_tp)
        col = _pal["col"]
        sl_col = _pal["sl_col"]
        tp_col = _pal["tp_col"]
        pos_col = _pal["pos_col"]
        exit_col = _pal["exit_col"]
        if is_pp:
            pp_line = "<b style='color:#0d47a1'>PP</b><br>"
        else:
            pp_line = _trade_hover_kind_line(t)
        pa_line = f"P.A. type: {_pa_type_hover_label(t)}<br>"
        fig_html.add_trace(
            go.Scatter(
                x=[ts_plotly.iloc[ie], ts_plotly.iloc[ic]],
                y=[float(t.entry_price), float(t.close_price)],
                mode="lines",
                line=dict(color=pos_col, width=2.8),
                opacity=0.9,
                name="position",
                showlegend=False,
                hovertemplate=(
                    f"{pp_line}"
                    f"position {'BUY' if is_buy else 'SELL'}<br>"
                    f"entry: {float(t.entry_price):.5f}<br>"
                    f"exit: {float(t.close_price):.5f}<br>"
                    f"{pa_line}"
                    f"PnL: {float(getattr(t, 'pnl_usd', 0.0)):+.2f} USD<extra></extra>"
                ),
            )
        )
        fig_html.add_trace(
            go.Scatter(
                x=[ts_plotly.iloc[ie], ts_plotly.iloc[ic]],
                y=[float(t.sl), float(t.sl)],
                mode="lines",
                line=dict(color=sl_col, width=2.3),
                opacity=1.0,
                name="SL",
                showlegend=False,
                hovertemplate=f"SL: {float(t.sl):.5f}<extra></extra>",
            )
        )
        if t.tp is not None:
            fig_html.add_trace(
                go.Scatter(
                    x=[ts_plotly.iloc[ie], ts_plotly.iloc[ic]],
                    y=[float(t.tp), float(t.tp)],
                    mode="lines",
                    line=dict(color=tp_col, width=2.3),
                    opacity=1.0,
                    name="TP",
                    showlegend=False,
                    hovertemplate=f"TP: {float(t.tp):.5f}<extra></extra>",
                )
            )
        ring_h = wave_color_by_time.get(str(getattr(t, "wave_time", "")), None)
        if is_pp:
            entry_line = dict(width=2.6, color="#1976d2")
        elif is_wc:
            entry_line = dict(width=2.6, color=WAVE_COUNTER_PLOT_COLOR)
        elif (
            _is_ext_counter_time_trade(t)
            or _is_ext_counter_bos_trade(t)
            or _is_bos_trade(t)
        ):
            entry_line = dict(width=2.6, color="#ffffff")
        elif is_ts:
            entry_line = dict(width=2.6, color=WAVE_TWO_SIDED_COLOR)
        else:
            entry_line = dict(width=2.6, color=ring_h) if ring_h else dict(width=1.0, color="white")
        glow_h = _trade_plot_glow(t)
        if glow_h is not None:
            fig_html.add_trace(
                go.Scatter(
                    x=[ts_plotly.iloc[ie]],
                    y=[float(t.entry_price)],
                    mode="markers",
                    opacity=0.48,
                    marker=dict(symbol="circle", size=24, color=glow_h, line=dict(width=0)),
                    name="entry_pnl",
                    showlegend=False,
                    hovertemplate=(
                        (f"{pp_line}" if is_pp else "")
                        + (
                            f"[{_entry_type_hover_letter(getattr(t, 'entry_type', None))}] "
                            f"{_entry_type_label(getattr(t, 'entry_type', None))}<br>"
                        )
                        + f"{pa_line}"
                        + f"PnL: {float(getattr(t, 'pnl_usd', 0.0)):+.2f} USD<extra></extra>"
                    ),
                )
            )
        sym_e = _entry_marker_plotly(getattr(t, "entry_type", None), is_buy)
        sz_e = 14 if sym_e in ("square", "diamond") else 13
        fig_html.add_trace(
            go.Scatter(
                x=[ts_plotly.iloc[ie]],
                y=[float(t.entry_price)],
                mode="markers",
                marker=dict(
                    symbol=sym_e,
                    size=sz_e,
                    color=col,
                    line=entry_line,
                ),
                name="entry",
                showlegend=False,
                hovertemplate=(
                    f"{pp_line}"
                    f"entry {'BUY' if is_buy else 'SELL'} [{_entry_type_hover_letter(getattr(t, 'entry_type', None))}] "
                    f"{_entry_type_label(getattr(t, 'entry_type', None))}<br>"
                    f"@{float(t.entry_price):.5f}<br>"
                    f"wave_time: {getattr(t, 'wave_time', '')}<br>"
                    f"{pa_line}"
                    f"PnL: {float(getattr(t, 'pnl_usd', 0.0)):+.2f} USD<extra></extra>"
                ),
            )
        )
        if is_pp:
            fig_html.add_trace(
                go.Scatter(
                    x=[ts_plotly.iloc[ie]],
                    y=[float(t.entry_price)],
                    mode="text",
                    text=["PP"],
                    textposition="top center",
                    textfont=dict(color="#0d47a1", size=12, family="Arial Black"),
                    showlegend=False,
                    hoverinfo="skip",
                )
            )
        elif is_ts:
            fig_html.add_trace(
                go.Scatter(
                    x=[ts_plotly.iloc[ie]],
                    y=[float(t.entry_price)],
                    mode="text",
                    text=["WAVE_TWO_SIDED"],
                    textposition="top center",
                    textfont=dict(color="#e8f5e9", size=10, family="Arial Black"),
                    showlegend=False,
                    hoverinfo="skip",
                )
            )
        elif is_wc:
            fig_html.add_trace(
                go.Scatter(
                    x=[ts_plotly.iloc[ie]],
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
        elif _is_bos_trade(t):
            fig_html.add_trace(
                go.Scatter(
                    x=[ts_plotly.iloc[ie]],
                    y=[float(t.entry_price)],
                    mode="text",
                    text=["BOS"],
                    textposition="top center",
                    textfont=dict(color=BOS_PLOT_COLOR, size=10, family="Arial Black"),
                    showlegend=False,
                    hoverinfo="skip",
                )
            )
        elif _is_ext_counter_bos_trade(t):
            fig_html.add_trace(
                go.Scatter(
                    x=[ts_plotly.iloc[ie]],
                    y=[float(t.entry_price)],
                    mode="text",
                    text=["EXT_BOS"],
                    textposition="top center",
                    textfont=dict(
                        color=EXT_COUNTER_BOS_PLOT_COLOR, size=9, family="Arial Black"
                    ),
                    showlegend=False,
                    hoverinfo="skip",
                )
            )
        elif _is_ext_counter_time_trade(t):
            fig_html.add_trace(
                go.Scatter(
                    x=[ts_plotly.iloc[ie]],
                    y=[float(t.entry_price)],
                    mode="text",
                    text=["EXT_TIME"],
                    textposition="top center",
                    textfont=dict(
                        color=EXT_COUNTER_TIME_PLOT_COLOR, size=9, family="Arial Black"
                    ),
                    showlegend=False,
                    hoverinfo="skip",
                )
            )
        fig_html.add_trace(
            go.Scatter(
                x=[ts_plotly.iloc[ic]],
                y=[float(t.close_price)],
                mode="markers",
                marker=dict(
                    symbol=_exit_marker_plotly(getattr(t, "close_reason", "")),
                    size=12,
                    color=exit_col,
                    line=dict(width=1.2, color=exit_col),
                ),
                name="exit",
                showlegend=False,
                hovertemplate=(
                    f"{pp_line}"
                    f"exit {t.close_reason} @{float(t.close_price):.5f}<br>"
                    f"{pa_line}"
                    f"PnL: {float(getattr(t, 'pnl_usd', 0.0)):+.2f} USD<extra></extra>"
                ),
            )
        )
    if bos_points:
        bos_leg_plotly = False
        for bp in bos_points:
            if bp is None or len(bp) < 2:
                continue
            t_flip = bp[0]
            lvl = float(bp[1])
            lbl = (bp[2] if len(bp) > 2 else "BOS swing")[:72]
            use_seg = len(bp) > 3 and bp[3] is not None
            bi1 = _nearest_bar_ix(df["time"], pd.Timestamp(t_flip))
            if use_seg:
                bi0 = _nearest_bar_ix(df["time"], pd.Timestamp(bp[3]))
                if bi0 > bi1:
                    bi0, bi1 = bi1, bi0
                if bi0 == bi1:
                    continue
            else:
                bi0 = bi1
                bi1 = n - 1
            t_b = ts_plotly.iloc[bi1]
            _add_segmented_hline(
                fig_html,
                ts_plotly,
                bi0,
                bi1,
                td_plotly,
                lvl,
                trace_kw=dict(
                    line=dict(color="black", width=2),
                    name="BOS swing (close flip)",
                    legendgroup="bos_swing",
                    showlegend=not bos_leg_plotly,
                    hovertemplate=(f"{lbl}<br>%{{x}}<br>%{{y:.5f}}<extra></extra>"),
                ),
            )
            bos_leg_plotly = True
            bos_anns.append(
                dict(
                    x=t_b,
                    y=lvl,
                    text=lbl,
                    showarrow=False,
                    xref="x",
                    yref="y",
                    xanchor="left",
                    yanchor="bottom",
                    font=dict(size=9, color="black"),
                )
            )

    _append_waves_color_legend(fig_html)

    html_title = f"{bot_name} — struktura vln (interaktivně)"
    if fib_levels_caption:
        html_title = f"{html_title}<br>{fib_levels_caption}"
    _layout_kw = dict(
        title=html_title,
        xaxis_title="Čas",
        yaxis_title="Price",
        template="plotly_white",
        hovermode="closest",
        legend=dict(
            orientation="v",
            x=1.02,
            y=1,
            xanchor="left",
            yanchor="top",
            font=dict(size=8),
            tracegroupgap=1,
        ),
    )
    if bos_anns:
        _layout_kw["annotations"] = bos_anns
    fig_html.update_layout(**_layout_kw)
    # Rangebreaks z REALNYCH datovych mezer (vikend/svatek), ne z pevneho
    # predpokladu obchodnich hodin. Skryje jen prazdne useky tak, aby na sebe
    # sousedni realne bary navazovaly — patecni bary (vc. patecniho vecera/minima)
    # zustanou viditelne nezavisle na rozvrhu brokera (viz _compute_data_gap_rangebreaks).
    gap_breaks = _compute_data_gap_rangebreaks(ts_plotly, bar_td)
    if gap_breaks:
        fig_html.update_xaxes(rangebreaks=gap_breaks)
    return fig_html
