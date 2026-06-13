"""Build grid_optimisation_plan.xlsx from grid_report.xlsx results."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "results/EURUSD/grid_bot_optimalisation_M30_2026-01-01_2026-05-10_003/grid_report.xlsx"
OUT = SRC.parent / "grid_optimisation_plan.xlsx"


def elimination_reason(row: pd.Series) -> str:
    reasons: list[str] = []
    if row["min_opp_bars"] == 2:
        reasons.append("min_opp_bars=2 (průměr +3.5k vs +34.3k u o3)")
    if row["wave_pnl_max"] < 12000:
        reasons.append("wave_pnl_max < 12k")
    if row["net_pnl_max"] < 8000:
        reasons.append("net_pnl_max < 8k (i s PP)")
    if row["tp_mode_label"] == "wave_target_n_g" and row["min_opp_bars"] == 2:
        reasons.append("wave_target_n_g + min_opp=2")
    return "; ".join(reasons)


def main() -> int:
    df = pd.read_excel(SRC, sheet_name="vysledky")
    df = df.copy()
    df["sl_fib_level"] = df["bot_name"].str.extract(r"_sf([\d.]+)_")[0].astype(float)
    df["tp_mode_label"] = df["tp_mode"].replace({"wave_target_n_new": "wave_target_n_g"})

    nw = [
        "net_pnl_pp_usd",
        "net_pnl_wave_counter_usd",
        "net_pnl_bos_usd",
        "net_pnl_ext_bos_usd",
        "net_pnl_wave_two_sided_usd",
    ]
    df["non_wave_pnl"] = df[nw].sum(axis=1)

    core_dims = [
        "rrr",
        "tp_mode_label",
        "tp_target_wave_index",
        "min_opp_bars",
        "fib_level",
        "sl_fib_level",
    ]
    ftmo_pnl = (
        "FTMO__projected_net_pnl_at_max_risk_usd"
        if "FTMO__projected_net_pnl_at_max_risk_usd" in df.columns
        else None
    )

    dim_rows: list[pd.DataFrame] = []
    for dim in [
        "rrr",
        "tp_mode_label",
        "tp_target_wave_index",
        "min_opp_bars",
        "fib_level",
        "sl_fib_level",
        "pp_enabled",
        "counter_position_enabled",
        "bos_entry_enable",
        "two_sided_entry_enabled",
        "ext_enabled",
        "wave_position_enabled",
    ]:
        if dim not in df.columns:
            continue
        g = (
            df.groupby(dim)
            .agg(
                count=("combo_no", "count"),
                net_pnl_mean=("net_pnl_usd", "mean"),
                net_pnl_median=("net_pnl_usd", "median"),
                net_pnl_max=("net_pnl_usd", "max"),
                net_pnl_min=("net_pnl_usd", "min"),
                wave_pnl_mean=("net_pnl_wave_usd", "mean"),
                wave_pnl_max=("net_pnl_wave_usd", "max"),
                pf_mean=("profit_factor", "mean"),
                dd_mean=("max_dd_%_vs_initial", "mean"),
            )
            .reset_index()
        )
        g.insert(0, "dimension", dim)
        g.rename(columns={dim: "value"}, inplace=True)
        dim_rows.append(g)
    df_dims = pd.concat(dim_rows, ignore_index=True).round(1)

    gcore = (
        df.groupby(core_dims)
        .agg(
            combos=("combo_no", "count"),
            net_pnl_mean=("net_pnl_usd", "mean"),
            net_pnl_min=("net_pnl_usd", "min"),
            net_pnl_max=("net_pnl_usd", "max"),
            wave_pnl_mean=("net_pnl_wave_usd", "mean"),
            wave_pnl_min=("net_pnl_wave_usd", "min"),
            wave_pnl_max=("net_pnl_wave_usd", "max"),
            non_wave_mean=("non_wave_pnl", "mean"),
            pf_mean=("profit_factor", "mean"),
            dd_mean=("max_dd_%_vs_initial", "mean"),
        )
        .reset_index()
        .round(1)
    )

    elim_flags: list[str] = []
    reasons: list[str] = []
    priorities: list[str] = []
    for _, row in gcore.iterrows():
        if row["min_opp_bars"] == 2:
            elim_flags.append("ANO")
        elif row["net_pnl_max"] < 8000 and row["wave_pnl_max"] < 12000:
            elim_flags.append("ANO")
        elif (
            row["tp_mode_label"] == "wave_target_n_g"
            and row["fib_level"] == 0.45
            and row["sl_fib_level"] >= 0.9
        ):
            elim_flags.append("ANO")
        else:
            elim_flags.append("NE")
        reasons.append(elimination_reason(row))

        if row["tp_mode_label"] in ("wave_target_n", "wave_target_n_g"):
            if row["wave_pnl_max"] >= 40000:
                priorities.append("VYSOKÁ")
            elif row["wave_pnl_max"] >= 25000 and row["net_pnl_mean"] < row["wave_pnl_mean"] * 0.85:
                priorities.append("STŘEDNÍ (drag non-WAVE)")
            elif row["wave_pnl_max"] >= 20000:
                priorities.append("NÍZKÁ")
            else:
                priorities.append("—")
        else:
            priorities.append("—")

    gcore["eliminovat"] = elim_flags
    gcore["duvod_vyrazeni"] = reasons
    gcore["priorita_wave_only"] = priorities

    df_eliminate = gcore[gcore["eliminovat"] == "ANO"].sort_values(
        ["tp_mode_label", "wave_pnl_max"]
    )
    df_keep = gcore[gcore["eliminovat"] == "NE"].sort_values("wave_pnl_max", ascending=False)

    wtn = df[df["tp_mode_label"].isin(["wave_target_n", "wave_target_n_g"])].copy()
    wtn["wave_only_scenario_pnl"] = wtn["net_pnl_wave_usd"]
    wtn["drag"] = wtn["non_wave_pnl"]

    gem_cols = [
        "combo_no",
        "rrr",
        "tp_mode_label",
        "tp_target_wave_index",
        "min_opp_bars",
        "fib_level",
        "sl_fib_level",
        "pp_enabled",
        "net_pnl_usd",
        "net_pnl_wave_usd",
        "non_wave_pnl",
        "drag",
        "net_pnl_pp_usd",
        "net_pnl_bos_usd",
        "net_pnl_wave_two_sided_usd",
        "net_pnl_wave_counter_usd",
        "profit_factor",
        "max_dd_%_vs_initial",
    ]
    gems = wtn[
        (wtn["wave_only_scenario_pnl"] >= 20000)
        & ((wtn["net_pnl_usd"] < 15000) | (wtn["drag"] < -5000))
    ].sort_values(["wave_only_scenario_pnl", "drag"], ascending=[False, True])
    gems_out = gems[gem_cols].round(1)

    wtn_g_gems = wtn[
        (wtn["tp_mode_label"] == "wave_target_n_g")
        & (wtn["net_pnl_wave_usd"] >= 25000)
        & (wtn["net_pnl_usd"] < wtn["net_pnl_wave_usd"] * 0.75)
    ].sort_values("net_pnl_wave_usd", ascending=False)
    wtn_g_out = wtn_g_gems[gem_cols].round(1)

    labels = {
        "net_pnl_wave_usd": "WAVE",
        "net_pnl_pp_usd": "PP",
        "net_pnl_bos_usd": "WAVE_BOS",
        "net_pnl_wave_counter_usd": "WAVE_COUNTER",
        "net_pnl_wave_two_sided_usd": "WAVE_TWO_SIDED",
        "net_pnl_ext_bos_usd": "EXT_BOS",
        "net_pnl_ext_usd": "EXT",
    }
    df_pos = pd.DataFrame(
        [
            {
                "typ_pozice": label,
                "celkem_pnl": round(df[col].sum(), 0),
                "prumer_pnl": round(df[col].mean(), 0),
                "podil_ziskovych_kombinaci_%": round((df[col] > 0).mean() * 100, 1),
                "max_pnl": round(df[col].max(), 0),
                "min_pnl": round(df[col].min(), 0),
            }
            for col, label in labels.items()
            if col in df.columns
        ]
    )

    df_rec = pd.DataFrame(
        [
            {
                "oblast": "wave_min_pct",
                "aktualni": "0.26",
                "doporuceni": "[0.20, 0.23, 0.26, 0.30, 0.35]",
                "duvod": "První nová osa — ověřit citlivost na velikost vlny",
            },
            {
                "oblast": "min_opp_bars",
                "aktualni": "[2, 3]",
                "doporuceni": "[3]",
                "duvod": "o2 průměr +3.5k vs o3 +34.3k — vyřadit úplně",
            },
            {
                "oblast": "tp_mode",
                "aktualni": "4 režimy",
                "doporuceni": "bos_exit, rrr_fixed, wave_target_n, wave_target_n_g",
                "duvod": "Všechny 4 — wave_target_n_g vyžaduje WAVE-only test",
            },
            {
                "oblast": "tp_target_wave_index",
                "aktualni": "[4, 6, 8]",
                "doporuceni": "[4, 6, 8] pro wave_target_n*; [4, 8] pro bos/rrr",
                "duvod": "Index 6 dominuje u wave_target_n; 8 silný u bos_exit",
            },
            {
                "oblast": "fib_level",
                "aktualni": "[0.45..0.60]",
                "doporuceni": "[0.50, 0.55, 0.60]",
                "duvod": "0.45 slabší průměr; 0.55 nejlepší",
            },
            {
                "oblast": "sl_fib_level",
                "aktualni": "[0.75..0.90]",
                "doporuceni": "[0.75, 0.80, 0.85]",
                "duvod": "0.90 nejslabší SL pásmo",
            },
            {
                "oblast": "pp_enabled",
                "aktualni": "[True, False]",
                "doporuceni": "samostatný grid ON/OFF",
                "duvod": "PP +1268/combo, ale kombinovat zvlášť od BOS/two-sided",
            },
            {
                "oblast": "bos_entry_enable",
                "aktualni": "True",
                "doporuceni": "[False, True]",
                "duvod": "BOS průměr -1278/combo — hlavní drag u wave_target_n",
            },
            {
                "oblast": "two_sided_entry_enabled",
                "aktualni": "True",
                "doporuceni": "[False, True]",
                "duvod": "TWO_SIDED průměr -1990/combo — druhý největší drag",
            },
            {
                "oblast": "ext_enabled",
                "aktualni": "True",
                "doporuceni": "[False, True]",
                "duvod": "EXT PnL=0 v období, ale ovlivňuje logiku WAVE",
            },
        ]
    )

    keep_sigs = len(df_keep)
    df_rec_size = pd.DataFrame(
        [
            {"polozka": "Aktuální grid", "kombinace": len(df)},
            {"polozka": "Core signatury po vyřazení", "kombinace": keep_sigs},
            {"polozka": "wave_min_pct hodnoty", "kombinace": 5},
            {"polozka": "Profily pozic (WAVE-only + 5 jednotlivých + vše)", "kombinace": 7},
            {"polozka": "ODHAD dalšího gridu (konzervativní)", "kombinace": keep_sigs * 5 * 7},
            {"polozka": "ODHAD s plným 2^5 position matrix", "kombinace": keep_sigs * 5 * 32},
        ]
    )

    best_combo = df.loc[df["net_pnl_usd"].idxmax()]
    summary = pd.DataFrame(
        [
            {"sekce": "Zdroj", "hodnota": str(SRC)},
            {"sekce": "Počet kombinací", "hodnota": len(df)},
            {"sekce": "Období", "hodnota": "2026-01-01 až 2026-05-10 M30 EURUSD"},
            {
                "sekce": "Nejlepší combo (celkem)",
                "hodnota": (
                    f"#{int(best_combo['combo_no'])} | {best_combo['net_pnl_usd']:.0f} USD | "
                    f"{best_combo['tp_mode_label']} ti={int(best_combo['tp_target_wave_index'])}"
                ),
            },
            {
                "sekce": "Nejlepší WAVE-only potenciál",
                "hodnota": f"{df['net_pnl_wave_usd'].max():.0f} USD | wave_target_n ti=6",
            },
            {
                "sekce": "Vyřadit core signatur",
                "hodnota": f"{len(df_eliminate)} / {len(gcore)} ({100 * len(df_eliminate) / len(gcore):.0f}%)",
            },
            {"sekce": "Zachovat core signatur", "hodnota": f"{len(df_keep)} / {len(gcore)}"},
            {
                "sekce": "Hidden gems wave_target_n*",
                "hodnota": f"{len(gems_out)} řádků (wave≥20k, drag nebo slabý total)",
            },
            {
                "sekce": "wave_target_n_g speciální",
                "hodnota": f"{len(wtn_g_out)} řádků (wave≥25k, total<<wave)",
            },
            {
                "sekce": "Klíčové zjištění 1",
                "hodnota": "min_opp_bars=2 vyřadit — dramatický rozdíl",
            },
            {
                "sekce": "Klíčové zjištění 2",
                "hodnota": "wave_target_n dominuje; wave_target_n_g slabší v mixu, ale wave PnL až 55k",
            },
            {
                "sekce": "Klíčové zjištění 3",
                "hodnota": "BOS (-1278/combo) a TWO_SIDED (-1990/combo) ničí wave_target_n výsledky",
            },
            {
                "sekce": "Klíčové zjištění 4",
                "hodnota": "PP je ziskové (+1268/combo) — testovat zvlášť od BOS/two-sided",
            },
        ]
    )

    top_cols = [
        "combo_no",
        "rrr",
        "tp_mode_label",
        "tp_target_wave_index",
        "min_opp_bars",
        "fib_level",
        "sl_fib_level",
        "pp_enabled",
        "net_pnl_usd",
        "net_pnl_wave_usd",
        "non_wave_pnl",
        "profit_factor",
        "max_dd_%_vs_initial",
    ]
    if ftmo_pnl:
        top_cols.append(ftmo_pnl)
    top_out = df[df["min_opp_bars"] == 3].nlargest(30, "net_pnl_usd")[top_cols].round(1)

    with pd.ExcelWriter(OUT, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="prehled", index=False)
        df_rec.to_excel(writer, sheet_name="doporuceni_grid", index=False)
        df_rec_size.to_excel(writer, sheet_name="odhad_rozsahu", index=False)
        df_dims.to_excel(writer, sheet_name="analyza_dimenzi", index=False)
        df_pos.to_excel(writer, sheet_name="dopad_typu_pozic", index=False)
        df_eliminate.to_excel(writer, sheet_name="vyřadit", index=False)
        df_keep.to_excel(writer, sheet_name="zachovat", index=False)
        gems_out.to_excel(writer, sheet_name="wave_only_potencial", index=False)
        wtn_g_out.to_excel(writer, sheet_name="wave_target_n_g_gems", index=False)
        top_out.to_excel(writer, sheet_name="top30_o3", index=False)

    print(f"Written: {OUT}")
    print(
        f"Eliminate: {len(df_eliminate)}, Keep: {len(df_keep)}, "
        f"Gems: {len(gems_out)}, wtn_g: {len(wtn_g_out)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
