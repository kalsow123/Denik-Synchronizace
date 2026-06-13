"""combo_no — stejné číslování kombinací ve všech CSV/XLSX a v názvech souborů."""
from __future__ import annotations

from typing import Dict, Optional

import pandas as pd

COMBO_NO_COL = "combo_no"
BOT_NAME_COL = "bot_name"
BOT_NAME_EXCEL_WIDTH_CM = 6.0


def combo_no_from_config(cfg: dict | None) -> Optional[int]:
    if not cfg:
        return None
    v = cfg.get("_grid_test_pozice", cfg.get("test_pozice"))
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def bot_name_to_combo_no(df_report: pd.DataFrame) -> Dict[str, int]:
    if df_report is None or df_report.empty or BOT_NAME_COL not in df_report.columns:
        return {}
    if COMBO_NO_COL in df_report.columns:
        sub = df_report[[BOT_NAME_COL, COMBO_NO_COL]].drop_duplicates(subset=[BOT_NAME_COL])
        return {
            str(r[BOT_NAME_COL]): int(r[COMBO_NO_COL])
            for _, r in sub.iterrows()
            if pd.notna(r[COMBO_NO_COL])
        }
    if "test_pozice" in df_report.columns:
        sub = df_report[[BOT_NAME_COL, "test_pozice"]].drop_duplicates(subset=[BOT_NAME_COL])
        return {
            str(r[BOT_NAME_COL]): int(r["test_pozice"])
            for _, r in sub.iterrows()
            if pd.notna(r["test_pozice"])
        }
    return {}


def finalize_export_column_order(df: pd.DataFrame) -> pd.DataFrame:
    """
    První sloupce: combo_no, bot_name.
    test_pozice se sloučí do combo_no a odstraní (duplicita).
    """
    if df is None or df.empty:
        return df

    out = df.copy()
    if COMBO_NO_COL not in out.columns and "test_pozice" in out.columns:
        out.insert(
            0,
            COMBO_NO_COL,
            pd.to_numeric(out["test_pozice"], errors="coerce").astype("Int64"),
        )

    if COMBO_NO_COL in out.columns and "test_pozice" in out.columns:
        out = out.drop(columns=["test_pozice"])

    # Pro srovnani full vs wave_isolation srovname i podle study_mode
    if "net_pnl_usd" in out.columns and "study_mode" in out.columns:
        if "FTMO__projected_net_pnl_at_max_risk_usd" in out.columns:
            out.sort_values(
                ["FTMO__projected_net_pnl_at_max_risk_usd", "study_mode"], 
                ascending=[False, True], 
                na_position="last",
                inplace=True
            )
        else:
            out.sort_values(
                ["net_pnl_usd", "study_mode"], 
                ascending=[False, True], 
                na_position="last",
                inplace=True
            )
        out.reset_index(drop=True, inplace=True)

    front: list[str] = []
    if COMBO_NO_COL in out.columns:
        front.append(COMBO_NO_COL)
    if BOT_NAME_COL in out.columns:
        front.append(BOT_NAME_COL)
    rest = [c for c in out.columns if c not in front]
    return out[front + rest]


def attach_combo_no(df: pd.DataFrame, combo_map: Dict[str, int]) -> pd.DataFrame:
    if df is None or df.empty or BOT_NAME_COL not in df.columns or not combo_map:
        return finalize_export_column_order(df)
    out = df.copy()
    if COMBO_NO_COL not in out.columns:
        out.insert(
            0,
            COMBO_NO_COL,
            out[BOT_NAME_COL].map(lambda n: combo_map.get(str(n))),
        )
    return finalize_export_column_order(out)
