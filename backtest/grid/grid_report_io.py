"""Průběžný a finální zápis grid_report.xlsx do výstupní složky běhu."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from backtest.grid.aggregator import build_grid_report, collect_errors, save_report
from backtest.grid.backtest_conf import get_profile, resolve_grid_prop_firms
from backtest.grid.combo_columns import finalize_export_column_order
from backtest.grid.summary_sheet import build_grid_summaries_sheet
from backtest.io.csv_export import export_csv
from backtest.grid.grid_report_progress import GridReportProgress
from backtest.grid.ddi_sheet import build_grid_ddi_sheet
from backtest.io.excel_export import (
    GRID_REPORT_XLSX,
    GRID_SHEET_CHYBY,
    GRID_SHEET_DDI_EPIZODY,
    GRID_SHEET_E2E,
    GRID_SHEET_PROP_FIRM,
    GRID_SHEET_SUMMARIES,
    GRID_SHEET_VYSLEDKY,
    export_grid_workbook,
)

GRID_CHECKPOINT_EVERY = 100
GRID_REPORT_CSV = "grid_report.csv"
GRID_SUMMARIES_CSV = "grid_summaries.csv"
GRID_PROP_FIRM_CSV = "grid_prop_firm_compliance.csv"
GRID_ERRORS_CSV = "grid_errors.csv"
GRID_DDI_EPIZODY_CSV = "grid_ddi_epizody.csv"
GRID_E2E_CSV = "grid_e2e.csv"

# DDi sloupce z ddi_epizody doplněné k summaries formátu (list E2E).
E2E_DDI_EXTRA_COLUMNS: tuple[str, ...] = (
    "max_ddi_pct",
    "p90_ddi_pct",
    "median_ddi_pct",
    "pct_dnu_ge_10",
    "dnu_testu_celkem",
)


def _resolve_prop_firms(profile: dict, args: Any | None) -> dict:
    """Profil prop_firms z backtest_conf; CLI args mají prioritu, když jsou předány."""
    if args is None:
        from types import SimpleNamespace

        args = SimpleNamespace(
            prop_firms=None,
            prop_firm_config=None,
            prop_firm_html=False,
            account_size_override=None,
        )
    return resolve_grid_prop_firms(profile, args)


def build_grid_workbook_sheets(
    results: dict,
    *,
    profile: dict,
    args: Any | None = None,
    progress: GridReportProgress | None = None,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, pd.DataFrame, str]:
    """Sestaví dict listů pro export_grid_workbook + df_report + df_long."""
    from backtest.prop_firm.compliance import (
        apply_prop_firm_compliance,
        build_all_ranking_sheets,
        enrich_prop_firm_long_sheet,
        enrich_report_prop_firm_summary,
    )

    if progress is not None:
        progress.update(
            10,
            f"tabulku vysledku — {len(results)} kombinaci (list '{GRID_SHEET_VYSLEDKY}')",
        )
    df_report = build_grid_report(results)
    pf_opts = _resolve_prop_firms(profile, args)
    preset_names = pf_opts["preset_names"]
    df_long = pd.DataFrame()

    if preset_names:
        if progress is not None:
            presets_label = ", ".join(preset_names)
            progress.update(
                30,
                f"prop-firm sloupce a list '{GRID_SHEET_PROP_FIRM}' ({presets_label})",
            )
        df_report, df_long = apply_prop_firm_compliance(
            df_report,
            results,
            preset_names,
            custom_config_path=pf_opts["config_path"],
            account_size_override=pf_opts["account_size_usd"],
        )
        df_report = enrich_report_prop_firm_summary(df_report, preset_names)
    else:
        if progress is not None:
            progress.update(30, f"razeni vysledku (list '{GRID_SHEET_VYSLEDKY}')")
        df_report = enrich_report_prop_firm_summary(df_report, [])

    primary_prop_preset = preset_names[0] if preset_names else ""
    if primary_prop_preset:
        from backtest.prop_firm.report_keys import sort_report_by_projected_pnl

        df_report = sort_report_by_projected_pnl(df_report, primary_prop_preset)

    if progress is not None:
        progress.update(
            50,
            f"list '{GRID_SHEET_SUMMARIES}' + ranking listy (Ranking_<preset>)",
        )
    ranking_sheets = (
        build_all_ranking_sheets(df_report, df_long, preset_names)
        if preset_names and not df_long.empty
        else {}
    )
    df_errors = collect_errors(results)

    sheets: dict[str, pd.DataFrame] = {
        GRID_SHEET_VYSLEDKY: finalize_export_column_order(df_report),
    }
    df_summaries = build_grid_summaries_sheet(df_report, preset_names=preset_names)
    if not df_summaries.empty:
        sheets[GRID_SHEET_SUMMARIES] = df_summaries
    if not df_long.empty:
        sheets[GRID_SHEET_PROP_FIRM] = finalize_export_column_order(
            enrich_prop_firm_long_sheet(df_long, df_report)
        )
    sheets.update(ranking_sheets)
    if not df_errors.empty:
        sheets[GRID_SHEET_CHYBY] = df_errors

    if progress is not None:
        progress.update(55, f"list '{GRID_SHEET_DDI_EPIZODY}'")
    df_ddi = build_grid_ddi_sheet(results, df_report)
    if not df_ddi.empty:
        sheets[GRID_SHEET_DDI_EPIZODY] = df_ddi

    if progress is not None:
        sheet_names = ", ".join(sheets.keys())
        progress.update(65, f"workbook {GRID_REPORT_XLSX} — {len(sheets)} listu ({sheet_names})")
    return sheets, df_report, df_long, primary_prop_preset


def write_grid_csv_exports(
    output_dir: Path | str,
    sheets: dict[str, pd.DataFrame],
    df_report: pd.DataFrame,
    df_long: pd.DataFrame,
    *,
    progress: GridReportProgress | None = None,
) -> list[Path]:
    """Tier 2: rychlé CSV výstupy před (nebo místo) těžkého xlsx."""
    output_dir = Path(output_dir)
    written: list[Path] = []
    if not df_report.empty:
        if progress is not None:
            progress.update(58, f"soubor {GRID_REPORT_CSV}")
        p = output_dir / GRID_REPORT_CSV
        save_report(df_report, p)
        written.append(p)
    df_summaries = sheets.get(GRID_SHEET_SUMMARIES)
    if df_summaries is not None and not df_summaries.empty:
        if progress is not None:
            progress.update(59, f"soubor {GRID_SUMMARIES_CSV}")
        p = output_dir / GRID_SUMMARIES_CSV
        export_csv(df_summaries, p, index=False)
        written.append(p)
    if not df_long.empty:
        if progress is not None:
            progress.update(60, f"soubor {GRID_PROP_FIRM_CSV}")
        p = output_dir / GRID_PROP_FIRM_CSV
        export_csv(df_long, p, index=False)
        written.append(p)
    df_errors = sheets.get(GRID_SHEET_CHYBY)
    if df_errors is not None and not df_errors.empty:
        if progress is not None:
            progress.update(61, f"soubor {GRID_ERRORS_CSV}")
        p = output_dir / GRID_ERRORS_CSV
        export_csv(df_errors, p, index=False)
        written.append(p)
    df_ddi = sheets.get(GRID_SHEET_DDI_EPIZODY)
    if df_ddi is not None and not df_ddi.empty:
        if progress is not None:
            progress.update(62, f"soubor {GRID_DDI_EPIZODY_CSV}")
        p = output_dir / GRID_DDI_EPIZODY_CSV
        export_csv(df_ddi, p, index=False)
        written.append(p)
    return written


LIVE_MATCH_GRID_PROFILE: dict = {
    "prop_firms": {"enabled": False},
}


def write_live_match_grid_report(
    stats: dict,
    output_dir: Path | str,
    *,
    args: Any | None = None,
) -> Path | None:
    """
    live_match: grid_report.xlsx + CSV (vysledky, summaries, ddi_epizody).
    Prop-firm listy se neexportuji (enabled=False).
    """
    if "error" in stats:
        return None
    cfg = stats.get("config") or {}
    bot_name = str(cfg.get("bot_name") or "live_match")
    output_dir = Path(output_dir)
    results = {bot_name: stats}

    sheets, df_report, df_long, _primary = build_grid_workbook_sheets(
        results,
        profile=LIVE_MATCH_GRID_PROFILE,
        args=args,
    )
    write_grid_csv_exports(output_dir, sheets, df_report, df_long)

    xlsx_path = output_dir / GRID_REPORT_XLSX
    if export_grid_workbook(xlsx_path, sheets):
        parts = ", ".join(sheets.keys())
        print(f"Grid report: {xlsx_path} ({len(df_report)} radku | listy: {parts})")
        return xlsx_path

    print(
        "VAROVANI: grid_report.xlsx — nainstaluj openpyxl. "
        "CSV zalohy jsou v output slozce."
    )
    return None


def build_e2e_summaries_sheet(
    live_e2e_stats: dict,
    combo: dict,
    *,
    parity: dict | None = None,
) -> pd.DataFrame:
    """
    List E2E — stejný formát jako summaries + DDi metriky + parity sloupce.
    """
    if not live_e2e_stats or "error" in live_e2e_stats:
        return pd.DataFrame()
    cfg = dict(combo or {})
    bot_name = str(cfg.get("bot_name") or live_e2e_stats.get("bot_name") or "live_e2e")
    cfg.setdefault("bot_name", bot_name)
    results = {bot_name: {**live_e2e_stats, "config": cfg}}
    df_report = build_grid_report(results)
    df_summaries = build_grid_summaries_sheet(df_report, preset_names=None)
    if df_summaries.empty:
        return pd.DataFrame()

    profile = dict(live_e2e_stats.get("ddi_profile") or {})
    for col in E2E_DDI_EXTRA_COLUMNS:
        if col in profile:
            df_summaries[col] = profile[col]

    wr = live_e2e_stats.get("win_rate_pct")
    if wr is not None:
        df_summaries["win_rate_%"] = round(float(wr), 1)

    if parity:
        df_summaries["e2e_common_wave_times"] = parity.get("common_wave_times")
        df_summaries["e2e_bt_only_wave_times"] = parity.get("bt_only_wave_times")
        df_summaries["e2e_lv_only_wave_times"] = parity.get("lv_only_wave_times")
        df_summaries["backtest_net_pnl_usd"] = parity.get("backtest_net_pnl_usd")
        df_summaries["live_e2e_net_pnl_usd"] = parity.get("live_e2e_net_pnl_usd")
        bt_wr = parity.get("backtest_win_rate_pct")
        if bt_wr is not None:
            df_summaries["backtest_win_rate_%"] = round(float(bt_wr), 1)

    return df_summaries


def _load_existing_workbook_sheets(xlsx_path: Path) -> dict[str, pd.DataFrame]:
    if not xlsx_path.is_file():
        return {}
    with pd.ExcelFile(xlsx_path) as xf:
        return {
            str(name): pd.read_excel(xf, sheet_name=name)
            for name in xf.sheet_names
        }


def append_e2e_sheet_to_grid_report(
    output_dir: Path | str,
    live_e2e_stats: dict,
    combo: dict,
    *,
    parity: dict | None = None,
) -> Path | None:
    """
    Po E2E doplní/ přepíše list E2E v grid_report.xlsx (formát summaries + DDi).
    """
    df_e2e = build_e2e_summaries_sheet(live_e2e_stats, combo, parity=parity)
    if df_e2e.empty:
        return None

    output_dir = Path(output_dir)
    xlsx_path = output_dir / GRID_REPORT_XLSX
    sheets = _load_existing_workbook_sheets(xlsx_path)
    sheets[GRID_SHEET_E2E] = df_e2e

    export_csv(df_e2e, output_dir / GRID_E2E_CSV, index=False)

    if export_grid_workbook(xlsx_path, sheets):
        parts = ", ".join(sheets.keys())
        print(
            f"Grid report (E2E): {xlsx_path} "
            f"(list E2E: {len(df_e2e)} radku | listy: {parts})"
        )
        return xlsx_path

    print(
        "VAROVANI: grid_report.xlsx (E2E) — nainstaluj openpyxl. "
        f"CSV zaloha: {output_dir / GRID_E2E_CSV}"
    )
    return None


def init_grid_report_workbook(output_dir: Path | str, *, quiet: bool = True) -> Path:
    """Prázdný grid_report.xlsx hned na začátku běhu (nebo CSV záloha)."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    xlsx_path = output_dir / GRID_REPORT_XLSX
    sheets = {GRID_SHEET_VYSLEDKY: pd.DataFrame()}
    if export_grid_workbook(xlsx_path, sheets):
        if not quiet:
            print(f"Grid report (inicializace): {xlsx_path}")
        return xlsx_path
    csv_path = output_dir / "grid_report.csv"
    save_report(pd.DataFrame(), csv_path)
    return csv_path


def write_grid_progress_workbook(
    results: dict,
    output_dir: Path | str,
    profile_name: str,
    args: Any | None = None,
    *,
    done: int,
    total: int,
    final: bool = False,
    quiet: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """
    Kompletní přepis grid_report.xlsx z dosavadních výsledků.
    Volat po každých GRID_CHECKPOINT_EVERY kombinacích a na konci běhu.
    """
    output_dir = Path(output_dir)
    profile = get_profile(profile_name)
    progress: GridReportProgress | None = None
    if final or not quiet:
        header = (
            "Generuji grid_report.xlsx ..."
            if final
            else f"Generuji grid_report.xlsx (prubezny {done}/{total}) ..."
        )
        progress = GridReportProgress(header=header)

    sheets, df_report, df_long, primary_prop_preset = build_grid_workbook_sheets(
        results, profile=profile, args=args, progress=progress
    )

    csv_paths = write_grid_csv_exports(
        output_dir, sheets, df_report, df_long, progress=progress
    )
    if final and csv_paths:
        print(f"Grid report CSV: {csv_paths[0]} ({len(df_report)} radku vysledky)")

    xlsx_path = output_dir / GRID_REPORT_XLSX

    def _on_export_sheet(sheet_idx: int, sheet_total: int, sheet_name: str) -> None:
        if progress is None or sheet_total <= 0:
            return
        pct = 65 + int(34 * sheet_idx / sheet_total)
        df_sheet = sheets.get(sheet_name)
        row_count = len(df_sheet) if isinstance(df_sheet, pd.DataFrame) else 0
        if row_count:
            progress.update(
                pct,
                f"Excel {GRID_REPORT_XLSX} — list '{sheet_name}' ({row_count} radku)",
            )
        else:
            progress.update(pct, f"Excel {GRID_REPORT_XLSX} — list '{sheet_name}'")

    if export_grid_workbook(
        xlsx_path,
        sheets,
        on_sheet_progress=_on_export_sheet if progress is not None else None,
    ):
        if progress is not None:
            progress.finish()
        if final:
            print(f"Grid report: {xlsx_path} ({len(df_report)} radku vysledky)")
        elif not quiet:
            parts = ", ".join(sheets.keys())
            print(
                f"Grid report (prubezny {done}/{total}): {xlsx_path} | "
                f"listy: {parts} | radku: {len(df_report)}"
            )
    else:
        if progress is not None:
            progress.finish()
        label = "finalni" if final else f"prubezny ({done}/{total})"
        if final or not quiet:
            print(
                f"VAROVANI: grid_report.xlsx ({label}) — nainstaluj openpyxl. "
                "Ukladam CSV zalohu."
            )
        save_report(df_report, output_dir / "grid_report.csv")
        df_summaries = sheets.get(GRID_SHEET_SUMMARIES)
        if df_summaries is not None and not df_summaries.empty:
            export_csv(df_summaries, output_dir / "grid_summaries.csv", index=False)
        if not df_long.empty:
            export_csv(
                df_long,
                output_dir / "grid_prop_firm_compliance.csv",
                index=False,
            )
        df_errors = sheets.get(GRID_SHEET_CHYBY)
        if df_errors is not None and not df_errors.empty:
            export_csv(df_errors, output_dir / "grid_errors.csv", index=False)
        df_ddi = sheets.get(GRID_SHEET_DDI_EPIZODY)
        if df_ddi is not None and not df_ddi.empty:
            export_csv(df_ddi, output_dir / GRID_DDI_EPIZODY_CSV, index=False)

    return df_report, df_long, primary_prop_preset
