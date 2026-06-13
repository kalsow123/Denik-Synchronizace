"""
List „summaries“ v grid_report.xlsx — přehled kombinací + PnL/DD podle druhu vstupu.

Snadné rozšíření o další druh P&A: dopište řádek do SUMMARY_PA_KINDS.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from backtest.grid.study_mode import resolve_study_mode
from backtest.grid.combo_columns import BOT_NAME_COL, COMBO_NO_COL, finalize_export_column_order
from config.bot_config import TIMEFRAME_LABEL_MAP
from config.enums import EntryMode, TPMode

SUMMARY_PA_KINDS: tuple[tuple[str, str, str, str], ...] = (
    ("WAVE", "trades_wave", "net_pnl_wave_usd", "max_dd_%_vs_initial_wave"),
    (
        "WAVE_COUNTER",
        "trades_wave_counter",
        "net_pnl_wave_counter_usd",
        "max_dd_%_vs_initial_wave_counter",
    ),
    (
        "WAVE_TWO_SIDED",
        "trades_wave_two_sided",
        "net_pnl_wave_two_sided_usd",
        "max_dd_%_vs_initial_wave_two_sided",
    ),
    ("PP", "trades_pp", "net_pnl_pp_usd", "max_dd_%_vs_initial_pp"),
    ("EXT", "trades_ext", "net_pnl_ext_usd", "max_dd_%_vs_initial_ext"),
    ("BOS", "trades_bos", "net_pnl_bos_usd", "max_dd_%_vs_initial_bos"),
    ("EXT_BOS", "trades_ext_bos", "net_pnl_ext_bos_usd", "max_dd_%_vs_initial_ext_bos"),
)

_COL_RRR_TP = "RRR_TP"
_COL_TIMEFRAME = "Timeframe"
_COL_FIB = "Fib_vstup"


def timeframe_label(tf: Any) -> str:
    """MT5 timeframe int → např. M15."""
    if tf is None or (isinstance(tf, float) and pd.isna(tf)):
        return ""
    try:
        i = int(tf)
    except (TypeError, ValueError):
        return str(tf).strip()
    return str(TIMEFRAME_LABEL_MAP.get(i, i))


def _tp_mode_lower(tp_mode: Any) -> str:
    """TPMode je (str, Enum) — nelze spoléhat na isinstance(..., str)."""
    if tp_mode is None:
        return ""
    if isinstance(tp_mode, TPMode):
        return tp_mode.value.lower().replace("-", "_")
    ev = getattr(tp_mode, "value", None)
    if isinstance(ev, str):
        return ev.strip().lower().replace("-", "_")
    return str(tp_mode or "").strip().lower().replace("-", "_")


def _entry_mode_value(v: Any) -> Any:
    if isinstance(v, EntryMode):
        return v.value
    ev = getattr(v, "value", None)
    if isinstance(ev, str):
        return ev
    return v


def rr_tp_summary(rrr: Any, tp_mode: Any, tp_target_wave_index: Any) -> str:
    """Sloučený údaj RRR + TP režim: číslo, nebo text když režim RRR klasicky nahrazuje."""
    mode_lower = _tp_mode_lower(tp_mode)

    effective = ""
    for m in TPMode:
        if m.value == mode_lower or m.name.lower() == mode_lower:
            effective = m.value
            break
    if not effective:
        effective = mode_lower

    try:
        rf = float(rrr)
        r_plain = str(int(rf)) if rf == int(rf) else str(rf).rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        r_plain = ""

    if effective in ("", TPMode.RRR_FIXED.value):
        return r_plain if r_plain else ("" if rrr is None else str(rrr))

    if effective == TPMode.BOS_EXIT.value:
        base = r_plain if r_plain else "(RRR cfg)"
        return f"{base} (broker TP = R×SL jako poj.; výstup řídí BOS)"

    if effective == TPMode.BOS_EXIT_PRIORITY.value:
        return "bez klasického RRR TP — výstup jen BOS"

    if effective in (TPMode.WAVE_TARGET_N.value, TPMode.WAVE_TARGET_N_G.value):
        tw = tp_target_wave_index if tp_target_wave_index is not None else "?"
        if hasattr(tw, "value"):
            tw = getattr(tw, "value", tw)
        base = f"WAVE N={tw}"
        if effective == TPMode.WAVE_TARGET_N_G.value:
            return f"{base} G"
        return base

    tail = effective if effective else str(tp_mode or "")
    return f"{r_plain + ' | ' if r_plain else ''}{tail}"


def _pick_prop_preset(preset_names: list[str] | None) -> str | None:
    if not preset_names:
        return None
    if "FTMO" in preset_names:
        return "FTMO"
    return preset_names[0]


def _row_val(row: pd.Series, col: str) -> Any:
    """Hodnota bunky; pri duplicitnim sloupci vezme prvni skalar."""
    if col not in row.index:
        return None
    val = row[col]
    if isinstance(val, pd.Series):
        return val.iloc[0] if len(val) else None
    return val


def _is_missing_cell(val: Any) -> bool:
    if val is None:
        return True
    try:
        return bool(pd.isna(val))
    except (TypeError, ValueError):
        return False


def _wave_counter_two_sided_val(row: pd.Series) -> Any:
    v = _row_val(row, "wave_counter_two_sided_enabled")
    if not _is_missing_cell(v):
        return v
    return _row_val(row, "counter_position_enabled")


def build_grid_summaries_sheet(
    df_report: pd.DataFrame,
    *,
    preset_names: list[str] | None = None,
) -> pd.DataFrame:
    """
    List „summaries“. Na konci 4 prop sloupce dle uživatele; preferuje FTMO, jinak první preset.
    """
    if df_report is None or df_report.empty:
        return pd.DataFrame()
    if COMBO_NO_COL not in df_report.columns or BOT_NAME_COL not in df_report.columns:
        return pd.DataFrame()

    prop_preset = _pick_prop_preset(list(preset_names) if preset_names else None)

    cols_front = [
        COMBO_NO_COL,
        "study_mode",
        BOT_NAME_COL,
        _COL_TIMEFRAME,
        "min_opp_bars",
        _COL_FIB,
        "entry_mode",
        "pending_cancel_mode",
        "tp_target_wave_index",
        "wave_counter_two_sided_enabled",
        "bos_entry_enable",
        "bos_entry_in_rrr_fixed",
        "wave_position_enabled",
        "pp_enabled",
        "pp_sl_pct",
    ]
    # RRR_TP hned vlevo od trades; pak profit_factor, wave_min_pct + celkový DD před blokem WAVE/PP/BOS
    cols_before_pa = [_COL_RRR_TP, "trades", "profit_factor", "wave_min_pct", "max_dd_%_vs_initial"]

    cols_pa: list[str] = []
    for _, col_trades, col_pnl, col_dd in SUMMARY_PA_KINDS:
        cols_pa.extend([col_trades, col_pnl, col_dd])

    cols_tail_plain = [
        "headroom_scale",
        "max_risk_per_trade_usd",
        "projected_net_pnl_at_max_risk_usd",
        "original_net_pnl_usd",
    ]
    wide_tail = (
        [
            ("headroom_scale", f"{prop_preset}__headroom_scale"),
            ("max_risk_per_trade_usd", f"{prop_preset}__max_risk_per_trade_usd"),
            (
                "projected_net_pnl_at_max_risk_usd",
                f"{prop_preset}__projected_net_pnl_at_max_risk_usd",
            ),
            ("original_net_pnl_usd", f"{prop_preset}__original_net_pnl_usd"),
        ]
        if prop_preset
        else []
    )

    out_rows: list[dict[str, Any]] = []
    for _, row in df_report.iterrows():
        entry_mode_v = _entry_mode_value(_row_val(row, "entry_mode"))

        d: dict[str, Any] = {
            COMBO_NO_COL: _row_val(row, COMBO_NO_COL),
            BOT_NAME_COL: _row_val(row, BOT_NAME_COL),
            _COL_TIMEFRAME: timeframe_label(_row_val(row, "timeframe")),
            "min_opp_bars": _row_val(row, "min_opp_bars"),
            _COL_FIB: _row_val(row, "fib_level"),
            "entry_mode": entry_mode_v,
            "pending_cancel_mode": _row_val(row, "pending_cancel_mode"),
            "tp_target_wave_index": _row_val(row, "tp_target_wave_index"),
            "wave_counter_two_sided_enabled": _wave_counter_two_sided_val(row),
            "bos_entry_enable": _row_val(row, "bos_entry_enable"),
            "bos_entry_in_rrr_fixed": _row_val(row, "bos_entry_in_rrr_fixed"),
            "wave_position_enabled": _row_val(row, "wave_position_enabled"),
            "pp_enabled": _row_val(row, "pp_enabled"),
            "pp_sl_pct": _row_val(row, "pp_sl_pct"),
            _COL_RRR_TP: rr_tp_summary(
                _row_val(row, "rrr"),
                _row_val(row, "tp_mode"),
                _row_val(row, "tp_target_wave_index"),
            ),
            "trades": _row_val(row, "trades"),
            "profit_factor": _row_val(row, "profit_factor"),
            "wave_min_pct": _row_val(row, "wave_min_pct"),
            "max_dd_%_vs_initial": _row_val(row, "max_dd_%_vs_initial"),
        }

        for _label, col_trades, col_pnl, col_dd in SUMMARY_PA_KINDS:
            if col_trades in df_report.columns:
                vt = _row_val(row, col_trades)
                try:
                    d[col_trades] = int(vt) if vt is not None and str(vt) != "" else None
                except (TypeError, ValueError):
                    d[col_trades] = vt
            if col_pnl in df_report.columns:
                vp = _row_val(row, col_pnl)
                try:
                    d[col_pnl] = float(vp) if vp is not None and str(vp) != "" else None
                except (TypeError, ValueError):
                    d[col_pnl] = vp
            if col_dd in df_report.columns:
                vd = _row_val(row, col_dd)
                try:
                    d[col_dd] = float(vd) if vd is not None and str(vd) != "" else None
                except (TypeError, ValueError):
                    d[col_dd] = vd

        if prop_preset:
            for plain, wide_col in wide_tail:
                if wide_col in df_report.columns:
                    d[plain] = _row_val(row, wide_col)
                else:
                    d[plain] = None

        out_rows.append(d)

    out = pd.DataFrame(out_rows)

    ordered = (
        cols_front
        + cols_before_pa
        + cols_pa
        + (cols_tail_plain if prop_preset else [])
    )
    ordered = [c for c in ordered if c in out.columns]
    rest = [c for c in out.columns if c not in ordered]
    return finalize_export_column_order(out[ordered + rest])
