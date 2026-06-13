"""Integrace prop-firm compliance do grid_report a souvisejících výstupů."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import pandas as pd

from backtest.grid.combo_columns import attach_combo_no, bot_name_to_combo_no, finalize_export_column_order
from backtest.prop_firm.limits import PropFirmLimits
from backtest.prop_firm.presets import load_prop_firm_presets
from backtest.prop_firm.scaling import calculate_max_scale_factor, trades_records_to_df

# Sloupce v grid_report (wide): preset__suffix
WIDE_COLUMN_MAP = {
    "final_scale_factor": "scale_factor",
    "binding_constraint": "binding_constraint",
    "original_net_pnl_usd": "original_net_pnl_usd",
    "scaled_net_pnl_usd": "scaled_net_pnl_usd",
    "scaled_net_pnl_acc_pct": "scaled_net_pnl_acc_pct",
    "scaled_max_dd_pct_vs_initial": "scaled_max_dd_pct_vs_initial",
    "scaled_risk_per_trade_usd": "scaled_risk_per_trade_usd",
    "headroom_scale": "headroom_scale",
    "headroom_binding": "headroom_binding",
    "backtest_risk_usd": "backtest_risk_usd",
    "max_risk_per_trade_usd": "max_risk_per_trade_usd",
    "risk_change_usd": "risk_change_usd",
    "projected_net_pnl_at_max_risk_usd": "projected_net_pnl_at_max_risk_usd",
    "scale_for_overall_dd": "scale_for_overall_dd",
    "peak_overall_dd_pct": "peak_overall_dd_pct",
    "prop_firm_survives": "survives",
    "challenge_passed": "challenge_passed",
    "max_all_positions_risk_pct": "max_all_positions_risk",
    "max_risk_per_position_pct": "max_risk_per_position",
    "peak_margin_usd": "peak_margin_usd",
    "min_lot_warning": "min_lot_warning",
}

# Tučně v Excelu (prop_firm list + ranking).
PROP_FIRM_BOLD_COLUMNS = frozenset({
    "scale_factor",
    "headroom_scale",
    "backtest_risk_usd",
    "original_net_pnl_usd",
    "scaled_net_pnl_usd",
    "scaled_net_pnl_acc_pct",
    "scaled_max_dd_pct_vs_initial",
    "peak_overall_dd_pct",
    "max_risk_per_trade_usd",
    "projected_net_pnl_at_max_risk_usd",
    "scale_for_overall_dd",
    "headroom_binding",
    "wave_min_pct",
})

# Sloupce pro merge z prop_firm long (interní; část se v Excel ranking listu skryje).
RANKING_PROP_MERGE_COLUMNS = [
    "backtest_risk_usd",
    "headroom_scale",
    "max_risk_per_trade_usd",
    "risk_change_usd",
    "projected_net_pnl_at_max_risk_usd",
    "original_net_pnl_usd",
    "scale_factor",
    "scaled_net_pnl_usd",
    "scaled_net_pnl_acc_pct",
    "scale_for_overall_dd",
    "headroom_binding",
    "worst_day_loss_pct",
]

# Sloupce viditelné v Excel listu Ranking_<PRESET> (bez scale_factor, scaled_*, net_pnl_usd, …).
# max_pos_open = max současně otevřených pozic; trades = celkový počet obchodů (stats.total_trades).
RANKING_COL_MAX_OPEN_POSITIONS = "max_otevrenych_pozic"
RANKING_COL_TOTAL_TRADES = "celkovy_pocet_otevrenych_obchodu"

RANKING_SHEET_VISIBLE_COLUMNS = [
    "combo_no",
    "bot_name",
    "backtest_risk_usd",
    RANKING_COL_MAX_OPEN_POSITIONS,
    RANKING_COL_TOTAL_TRADES,
    "headroom_scale",
    "RRR_TP",
    "max_risk_per_trade_usd",
    "risk_change_usd",
    "projected_net_pnl_at_max_risk_usd",
    "original_net_pnl_usd",
    "max_ddd_%",
    "max_dd_%_vs_initial",
    "profit_factor",
    "wave_min_pct",
    "robustness_score",
    "calmar",
    "sortino",
    "cagr_pct",
    "headroom_binding",
    "prop_firm_pass_count",
    "prop_firm_best_match",
]

RANKING_SHEET_EXCLUDE_COLUMNS = frozenset({
    "scale_factor",
    "scaled_net_pnl_usd",
    "scaled_net_pnl_acc_pct",
    "scale_for_overall_dd",
    "net_pnl_usd",
    "prop_firm_preset",
})


def ranking_sheet_name(preset: str) -> str:
    """Excel list: Ranking_FTMO (max 31 znaků)."""
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in str(preset).strip())
    name = f"Ranking_{safe or 'prop'}"
    return name[:31]


def _max_dd_pct_vs_initial_from_stats(stats: dict) -> float:
    """
    DD pro prop-firm limity: vždy % vůči počátečnímu kapitálu (stats.max_drawdown_pct).

    Nepoužívat max_drawdown_pct_vs_peak / max_dd_% (DD od běžícího equity peaku).
    """
    if stats.get("max_drawdown_pct") is not None:
        return float(stats["max_drawdown_pct"])
    # Zpětná kompatibilita — stejná metrika pod jiným klíčem v reportu
    if stats.get("max_dd_%_vs_initial") is not None:
        return float(stats["max_dd_%_vs_initial"])
    return 0.0


def _wide_columns(preset_key: str, metrics: dict) -> dict:
    out = {}
    for src, suffix in WIDE_COLUMN_MAP.items():
        out[f"{preset_key}__{suffix}"] = metrics.get(src)
    return out


def _long_row(
    bot_name: str,
    preset_key: str,
    limits: PropFirmLimits,
    metrics: dict,
) -> dict:
    return {
        "prop_firm_name": preset_key,
        "bot_name": bot_name,
        "account_size_usd": limits.account_size_usd,
        "scale_factor": metrics["final_scale_factor"],
        "backtest_risk_usd": metrics["backtest_risk_usd"],
        "headroom_binding": metrics.get("headroom_binding"),
        "original_net_pnl_usd": metrics["original_net_pnl_usd"],
        "scaled_net_pnl_usd": metrics["scaled_net_pnl_usd"],
        "scaled_net_pnl_acc_pct": metrics["scaled_net_pnl_acc_pct"],
        "scaled_max_dd_pct_vs_initial": metrics["scaled_max_dd_pct_vs_initial"],
        "scaled_risk_per_trade_usd": metrics["scaled_risk_per_trade_usd"],
        "max_risk_per_trade_usd": metrics["max_risk_per_trade_usd"],
        "risk_change_usd": metrics["risk_change_usd"],
        "projected_net_pnl_at_max_risk_usd": metrics["projected_net_pnl_at_max_risk_usd"],
        "binding_constraint": metrics["binding_constraint"],
        "max_all_positions_risk": metrics["max_all_positions_risk_pct"],
        "max_risk_per_position": metrics["max_risk_per_position_pct"],
        "peak_overall_dd_pct": metrics["peak_overall_dd_pct"],
        "peak_margin_usd": metrics.get("peak_margin_usd", 0.0),
        "worst_day_loss_pct": metrics["worst_day_loss_pct"],
        "worst_day_loss_usd": metrics.get("worst_day_loss_usd"),
        "headroom_scale": metrics.get("headroom_scale"),
        "scale_for_peak_risk": metrics.get("scale_for_peak_risk"),
        "scale_for_single_position_risk": metrics.get("scale_for_single_position_risk"),
        "scale_for_daily_dd": metrics.get("scale_for_daily_dd"),
        "scale_for_overall_dd": metrics.get("scale_for_overall_dd"),
        "min_lot_warning": metrics.get("min_lot_warning"),
        "survives": metrics["prop_firm_survives"],
        "profit_target_hit": metrics["profit_target_hit"],
        "challenge_passed": metrics["challenge_passed"],
    }


def _results_have_precomputed_prop_firm(results: dict) -> bool:
    for stats in results.values():
        if isinstance(stats, dict) and stats.get("_prop_firm_wide") is not None:
            return True
    return False


def compute_prop_firm_for_stats(
    bot_name: str,
    stats: dict,
    preset_names: List[str],
    *,
    all_presets: Dict[str, PropFirmLimits],
    account_size_override: Optional[float] = None,
) -> Tuple[dict, List[dict]]:
    """Prop-firm metriky pro jednu kombinaci (worker nebo fallback z _prop_trades)."""
    if not preset_names or not stats or "error" in stats:
        return {}, []

    records = stats.get("_prop_trades") or []
    trades_df = trades_records_to_df(records)
    cfg = stats.get("config", {})
    contract_size = float(cfg.get("contract_size", 100_000.0))
    risk_usd = float(cfg.get("risk_usd", 500.0))
    net_pnl = float(stats.get("net_pnl_usd", 0.0))
    max_dd_initial = _max_dd_pct_vs_initial_from_stats(stats)

    bot_wide: dict = {}
    long_rows: List[dict] = []
    for key in preset_names:
        if key not in all_presets:
            continue
        lim = all_presets[key]
        if account_size_override is not None:
            lim = lim.with_account_size(account_size_override)
        metrics = calculate_max_scale_factor(
            trades_df,
            lim,
            contract_size=contract_size,
            original_net_pnl_usd=net_pnl,
            original_max_dd_pct_vs_initial=max_dd_initial,
            original_risk_usd=risk_usd,
            peak_overall_dd_pct=abs(max_dd_initial),
        )
        bot_wide.update(_wide_columns(key, metrics))
        long_rows.append(_long_row(bot_name, key, lim, metrics))
    return bot_wide, long_rows


def attach_prop_firm_to_stats(
    bot_name: str,
    stats: dict,
    preset_names: List[str],
    *,
    custom_config_path: Optional[str] = None,
    account_size_override: Optional[float] = None,
) -> None:
    """
    Spočítá prop-firm ve workeru a uloží do stats; odstraní _prop_trades (menší pickle).
    """
    if not preset_names or not stats or "error" in stats:
        return
    all_presets = load_prop_firm_presets(custom_config_path)
    wide, long_rows = compute_prop_firm_for_stats(
        bot_name,
        stats,
        preset_names,
        all_presets=all_presets,
        account_size_override=account_size_override,
    )
    if wide:
        stats["_prop_firm_wide"] = wide
        stats["_prop_firm_long_rows"] = long_rows
    stats.pop("_prop_trades", None)


def _merge_prop_firm_wide_long(
    df_report: pd.DataFrame,
    wide_by_bot: Dict[str, dict],
    long_rows: List[dict],
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    if not wide_by_bot:
        return df_report, pd.DataFrame()
    extra = pd.DataFrame.from_dict(wide_by_bot, orient="index")
    extra.index.name = "bot_name"
    extra = extra.reset_index()
    merged = df_report.merge(extra, on="bot_name", how="left")
    df_long = pd.DataFrame(long_rows)
    if df_long.empty:
        return merged, df_long
    combo_map = bot_name_to_combo_no(merged)
    df_long = attach_combo_no(df_long, combo_map)
    return merged, df_long


def _merge_precomputed_prop_firm(
    df_report: pd.DataFrame,
    results: dict,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    wide_by_bot: Dict[str, dict] = {}
    long_rows: List[dict] = []
    for bot_name in df_report["bot_name"]:
        stats = results.get(bot_name)
        if not stats or "error" in stats:
            continue
        wide = stats.get("_prop_firm_wide")
        if wide:
            wide_by_bot[bot_name] = wide
        rows = stats.get("_prop_firm_long_rows") or []
        long_rows.extend(rows)
    return _merge_prop_firm_wide_long(df_report, wide_by_bot, long_rows)


def apply_prop_firm_compliance(
    df_report: pd.DataFrame,
    results: dict,
    preset_names: List[str],
    *,
    custom_config_path: Optional[str] = None,
    account_size_override: Optional[float] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Rozšíří grid_report o sloupce preset__* a vrátí long DataFrame pro grid_prop_firm_compliance.csv.
    """
    if not preset_names or df_report.empty:
        return df_report, pd.DataFrame()

    if _results_have_precomputed_prop_firm(results):
        return _merge_precomputed_prop_firm(df_report, results)

    all_presets = load_prop_firm_presets(custom_config_path)
    long_rows: List[dict] = []
    wide_by_bot: Dict[str, dict] = {}

    for bot_name in df_report["bot_name"]:
        stats = results.get(bot_name)
        if not stats or "error" in stats:
            continue
        bot_wide, bot_long = compute_prop_firm_for_stats(
            bot_name,
            stats,
            preset_names,
            all_presets=all_presets,
            account_size_override=account_size_override,
        )
        if bot_wide:
            wide_by_bot[bot_name] = bot_wide
            long_rows.extend(bot_long)

    return _merge_prop_firm_wide_long(df_report, wide_by_bot, long_rows)


def enrich_report_prop_firm_summary(df_report: pd.DataFrame, preset_names: List[str]) -> pd.DataFrame:
    """prop_firm_best_match, prop_firm_pass_count z wide sloupců."""
    if not preset_names or df_report.empty:
        df_report = df_report.copy()
        df_report["prop_firm_best_match"] = ""
        df_report["prop_firm_pass_count"] = 0
        return df_report

    out = df_report.copy()
    pass_counts = []
    best_matches = []
    for _, row in out.iterrows():
        best_name = ""
        best_pct = float("-inf")
        passed = 0
        for key in preset_names:
            col_pass = f"{key}__challenge_passed"
            col_pct = f"{key}__scaled_net_pnl_acc_pct"
            if col_pass in row.index and bool(row.get(col_pass)):
                passed += 1
            if col_pct in row.index:
                v = row.get(col_pct)
                if pd.notna(v) and float(v) > best_pct:
                    best_pct = float(v)
                    best_name = key
        pass_counts.append(passed)
        best_matches.append(best_name)
    out["prop_firm_pass_count"] = pass_counts
    out["prop_firm_best_match"] = best_matches
    return out


def build_prop_firm_ranking_sheet(
    df_ranking: pd.DataFrame,
    df_long: pd.DataFrame,
    preset: str,
) -> pd.DataFrame:
    """
    Ranking pro jednu prop-firmu (list Ranking_<PRESET>).
    max_ddd_% = nejhorší denní ztráta % z backtestu (|worst_day_loss_pct|).
    """
    if df_ranking.empty or df_long.empty or not preset:
        return pd.DataFrame()

    pick = ["bot_name"] + [c for c in RANKING_PROP_MERGE_COLUMNS if c in df_long.columns]
    sub = (
        df_long[df_long["prop_firm_name"] == preset][pick]
        .drop_duplicates(subset=["bot_name"])
        .copy()
    )
    if "worst_day_loss_pct" in sub.columns:
        sub["max_ddd_%"] = pd.to_numeric(sub["worst_day_loss_pct"], errors="coerce").abs().round(4)
        sub = sub.drop(columns=["worst_day_loss_pct"])

    merged = df_ranking.merge(sub, on="bot_name", how="left", suffixes=("", "_pf"))
    for col in list(merged.columns):
        if col.endswith("_pf") and col[:-3] in merged.columns:
            merged = merged.drop(columns=[col])
    merged = merged.loc[:, ~merged.columns.duplicated()]

    keep = [c for c in RANKING_SHEET_VISIBLE_COLUMNS if c in merged.columns]
    hide = RANKING_SHEET_EXCLUDE_COLUMNS | {c for c in merged.columns if c.endswith("_pf")}
    extra = [c for c in merged.columns if c not in keep and c not in hide]
    out = merged[[c for c in keep if c in merged.columns] + extra]
    return finalize_export_column_order(out)


def _attach_max_open_positions_to_ranking(
    df_ranking: pd.DataFrame,
    df_report: pd.DataFrame,
) -> pd.DataFrame:
    """Z listu vysledky: max_otevrenych_pozic, celkovy_pocet_otevrenych_obchodu, RRR_TP."""
    from backtest.grid.summary_sheet import rr_tp_summary

    if df_ranking.empty or "bot_name" not in df_ranking.columns:
        return df_ranking
    cols = ["bot_name"]
    rename: dict[str, str] = {}
    if "max_pos_open" in df_report.columns:
        cols.append("max_pos_open")
        rename["max_pos_open"] = RANKING_COL_MAX_OPEN_POSITIONS
    if "trades" in df_report.columns:
        cols.append("trades")
        rename["trades"] = RANKING_COL_TOTAL_TRADES
    out = df_ranking
    if len(cols) > 1:
        extra = df_report[cols].drop_duplicates(subset=["bot_name"]).rename(columns=rename)
        out = df_ranking.merge(extra, on="bot_name", how="left")
        if RANKING_COL_MAX_OPEN_POSITIONS in out.columns:
            dup = f"{RANKING_COL_MAX_OPEN_POSITIONS}_report"
            if dup in out.columns:
                out[RANKING_COL_MAX_OPEN_POSITIONS] = out[RANKING_COL_MAX_OPEN_POSITIONS].fillna(
                    out[dup]
                )
                out = out.drop(columns=[dup])
        if RANKING_COL_TOTAL_TRADES in out.columns:
            dup_t = f"{RANKING_COL_TOTAL_TRADES}_report"
            if dup_t in out.columns:
                out[RANKING_COL_TOTAL_TRADES] = out[RANKING_COL_TOTAL_TRADES].fillna(out[dup_t])
                out = out.drop(columns=[dup_t])
    if {"bot_name", "rrr", "tp_mode"}.issubset(df_report.columns):
        rrr_cols = ["bot_name", "rrr", "tp_mode"]
        if "tp_target_wave_index" in df_report.columns:
            rrr_cols.append("tp_target_wave_index")
        rrr_extra = df_report[rrr_cols].drop_duplicates(subset=["bot_name"]).copy()
        rrr_extra["RRR_TP"] = rrr_extra.apply(
            lambda r: rr_tp_summary(r["rrr"], r["tp_mode"], r.get("tp_target_wave_index")),
            axis=1,
        )
        out = out.merge(rrr_extra[["bot_name", "RRR_TP"]], on="bot_name", how="left")
    if "wave_min_pct" in df_report.columns:
        wmp = df_report[["bot_name", "wave_min_pct"]].drop_duplicates(subset=["bot_name"])
        out = out.merge(wmp, on="bot_name", how="left")
        if "wave_min_pct_report" in out.columns:
            out["wave_min_pct"] = out["wave_min_pct"].fillna(out["wave_min_pct_report"])
            out = out.drop(columns=["wave_min_pct_report"])
    return out


def _insert_columns_after(df: pd.DataFrame, after: str, cols: list[str]) -> pd.DataFrame:
    if df.empty or after not in df.columns:
        return df
    order = [c for c in df.columns if c not in cols]
    ix = order.index(after) + 1
    for offset, name in enumerate(cols):
        if name in df.columns:
            order.insert(ix + offset, name)
    return df[order]


def enrich_prop_firm_long_sheet(
    df_long: pd.DataFrame,
    df_report: pd.DataFrame,
) -> pd.DataFrame:
    """Doplní profit_factor a wave_min_pct z vysledky (wave_min_pct hned za profit_factor)."""
    if df_long.empty or df_report.empty or "bot_name" not in df_long.columns:
        return df_long
    metrics = ["profit_factor", "wave_min_pct"]
    pick = ["bot_name"] + [c for c in metrics if c in df_report.columns]
    if len(pick) == 1:
        return df_long
    sub = df_report[pick].drop_duplicates(subset=["bot_name"])
    out = df_long.drop(columns=[c for c in metrics if c in df_long.columns], errors="ignore")
    out = out.merge(sub, on="bot_name", how="left")
    anchor = "original_net_pnl_usd" if "original_net_pnl_usd" in out.columns else "bot_name"
    insert = [c for c in metrics if c in out.columns]
    if insert:
        out = _insert_columns_after(out, anchor, insert)
    return out


def build_all_ranking_sheets(
    df_report: pd.DataFrame,
    df_long: pd.DataFrame,
    preset_names: List[str],
) -> Dict[str, pd.DataFrame]:
    """Jeden DataFrame na preset → klíč = Ranking_FTMO, …"""
    from backtest.grid.ranking import build_grid_ranking

    if df_report.empty or not preset_names:
        return {}
    base = _attach_max_open_positions_to_ranking(build_grid_ranking(df_report), df_report)
    sheets: Dict[str, pd.DataFrame] = {}
    for preset in preset_names:
        if df_long.empty:
            continue
        df_sheet = build_prop_firm_ranking_sheet(base, df_long, preset)
        if not df_sheet.empty:
            sheets[ranking_sheet_name(preset)] = df_sheet
    return sheets


def merge_prop_firm_into_ranking(
    df_ranking: pd.DataFrame,
    df_long: pd.DataFrame,
    primary_preset: str,
) -> pd.DataFrame:
    """Zpětná kompatibilita — první preset."""
    return build_prop_firm_ranking_sheet(df_ranking, df_long, primary_preset)


def print_prop_firm_summary(df_long: pd.DataFrame, top_n: int = 5) -> None:
    if df_long.empty:
        return
    print(f"\n{'='*110}")
    print(f"  Prop Firm Compliance Summary (TOP {top_n} by scaled PnL % per preset):")
    print(f"{'='*110}")
    for preset in sorted(df_long["prop_firm_name"].unique()):
        sub = df_long[df_long["prop_firm_name"] == preset].sort_values(
            "scaled_net_pnl_acc_pct", ascending=False
        ).head(top_n)
        print(f"\n  --- {preset} ---")
        hdr = (
            f"{'combo':>5} {'scale':>6} {'max_risk':>9} {'orig_pnl':>10} "
            f"{'proj_pnl':>10} {'binding':<12} {'passed':>6}"
        )
        print(hdr)
        print("-" * len(hdr))
        for _, r in sub.iterrows():
            combo = r.get("combo_no", "")
            passed = "YES" if r.get("challenge_passed") else "no"
            print(
                f"{str(combo):>5} {r['scale_factor']:>6.3f} {r['max_risk_per_trade_usd']:>9.1f} "
                f"{r['original_net_pnl_usd']:>10.0f} {r['projected_net_pnl_at_max_risk_usd']:>10.0f} "
                f"{str(r['binding_constraint']):<12} {passed:>6}"
            )
    print(f"{'='*110}\n")
