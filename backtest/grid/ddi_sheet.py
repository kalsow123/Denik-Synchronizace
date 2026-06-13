"""Wide list ddi_epizody pro grid_report.xlsx / grid_ddi_epizody.csv."""
from __future__ import annotations

from typing import Any

import pandas as pd

from backtest.grid.combo_columns import COMBO_NO_COL
from backtest.grid.summary_sheet import rr_tp_summary
from backtest.metrics.dd_episodes import parse_dd_episodes_from_report
from backtest.metrics.ddi_profile import DDI_STAT_COLUMNS

_COL_RRR_TP = "RRR_TP"


def _row_val(row: pd.Series, col: str) -> Any:
    if col not in row.index:
        return None
    val = row[col]
    if isinstance(val, pd.Series):
        return val.iloc[0] if len(val) else None
    return val


def _calendar_days_from_range(date_from: Any, date_to: Any) -> int | None:
    if date_from is None or date_to is None:
        return None
    try:
        d0 = pd.Timestamp(date_from).normalize()
        d1 = pd.Timestamp(date_to).normalize()
        return int((d1 - d0).days) + 1
    except (TypeError, ValueError):
        return None


def _resolve_episodes(stats: dict) -> list[dict]:
    eps = stats.get("dd_episodes_ge10pct")
    if isinstance(eps, list) and eps:
        return eps
    legacy = stats.get("dd_ge_10pct_obdobi", "")
    if legacy:
        return parse_dd_episodes_from_report(str(legacy))
    return []


def _resolve_dnu_testu_celkem(
    profile: dict,
    *,
    report_row: pd.Series | None,
    cfg: dict,
) -> int:
    v = profile.get("dnu_testu_celkem")
    if v:
        return int(v)
    if report_row is not None:
        for col in ("date_from", "date_to"):
            if col not in report_row.index and cfg.get(col) is not None:
                pass
        days = _calendar_days_from_range(
            _row_val(report_row, "date_from") or cfg.get("date_from"),
            _row_val(report_row, "date_to") or cfg.get("date_to"),
        )
        if days is not None:
            return days
    days = _calendar_days_from_range(cfg.get("date_from"), cfg.get("date_to"))
    return int(days) if days is not None else 0


def _combo_report_row(df_report: pd.DataFrame, combo_no: int) -> pd.Series | None:
    if df_report is None or df_report.empty or COMBO_NO_COL not in df_report.columns:
        return None
    sub = df_report[df_report[COMBO_NO_COL] == combo_no]
    if sub.empty:
        return None
    return sub.iloc[0]


def build_grid_ddi_sheet(
    results: dict,
    df_report: pd.DataFrame,
) -> pd.DataFrame:
    """Wide tabulka: 1 řádek = 1 kombinace (combo_no)."""
    rows: list[dict[str, Any]] = []
    max_episodes = 0

    entries: list[tuple[int, str, dict]] = []
    for bot_name, stats in results.items():
        if not isinstance(stats, dict) or "error" in stats:
            continue
        cfg = stats.get("config", {}) or {}
        combo_no = cfg.get("_grid_test_pozice")
        if combo_no is None:
            continue
        entries.append((int(combo_no), str(bot_name), stats))

    for combo_no, _bot_name, stats in sorted(entries, key=lambda x: x[0]):
        cfg = stats.get("config", {}) or {}
        report_row = _combo_report_row(df_report, combo_no)
        profile = dict(stats.get("ddi_profile") or {})
        episodes = _resolve_episodes(stats)
        max_episodes = max(max_episodes, len(episodes))

        rrr_tp = rr_tp_summary(
            cfg.get("rrr") if cfg.get("rrr") is not None else (
                _row_val(report_row, "rrr") if report_row is not None else None
            ),
            cfg.get("tp_mode") if cfg.get("tp_mode") is not None else (
                _row_val(report_row, "tp_mode") if report_row is not None else None
            ),
            cfg.get("tp_target_wave_index") if cfg.get("tp_target_wave_index") is not None else (
                _row_val(report_row, "tp_target_wave_index") if report_row is not None else None
            ),
        )

        trades = stats.get("total_trades")
        if trades is None and report_row is not None:
            trades = _row_val(report_row, "trades")
        pf = stats.get("profit_factor")
        if pf is None and report_row is not None:
            pf = _row_val(report_row, "profit_factor")
        max_dd = stats.get("max_drawdown_pct")
        if max_dd is None and report_row is not None:
            max_dd = _row_val(report_row, "max_dd_%_vs_initial")

        row: dict[str, Any] = {
            COMBO_NO_COL: combo_no,
            _COL_RRR_TP: rrr_tp,
            "trades": trades if trades is not None else 0,
            "profit_factor": pf if pf is not None else 0,
            "max_dd_%_vs_initial": max_dd if max_dd is not None else 0,
            "dnu_testu_celkem": _resolve_dnu_testu_celkem(
                profile, report_row=report_row, cfg=cfg
            ),
        }
        for n, ep in enumerate(episodes, start=1):
            row[f"DD{n}_zacatek"] = ep.get("start_date", "")
            row[f"DD{n}_konec"] = ep.get("end_recovery_date", "")
            row[f"DDi{n}"] = ep.get("min_dd_pct", 0.0)
        for col in DDI_STAT_COLUMNS:
            row[col] = profile.get(col, 0)
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    fixed = [
        COMBO_NO_COL,
        _COL_RRR_TP,
        "trades",
        "profit_factor",
        "max_dd_%_vs_initial",
        "dnu_testu_celkem",
    ]
    ep_cols: list[str] = []
    for n in range(1, max_episodes + 1):
        ep_cols.extend([f"DD{n}_zacatek", f"DD{n}_konec", f"DDi{n}"])
    col_order = fixed + ep_cols + list(DDI_STAT_COLUMNS)
    df = pd.DataFrame(rows)
    for col in col_order:
        if col not in df.columns:
            df[col] = "" if col.startswith("DD") and col.endswith(("_zacatek", "_konec")) else 0
    return df[col_order]
