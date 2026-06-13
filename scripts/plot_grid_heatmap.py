"""
Heatmapa z grid_report.xlsx (list vysledky): wave_min_pct × rrr, barva = metrika.

Příklad:
  python scripts/plot_grid_heatmap.py results/grid_EXAMPLE_20260101_120000/grid_report.xlsx
  python scripts/plot_grid_heatmap.py results/.../grid_report.xlsx --metric profit_factor --tf M15
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backtest.io.csv_export import read_csv
from backtest.io.excel_export import GRID_SHEET_VYSLEDKY, load_grid_report_sheet


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_report(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f"Soubor neexistuje: {path}")
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        df = load_grid_report_sheet(path, sheet=GRID_SHEET_VYSLEDKY)
    else:
        df = read_csv(path, format="auto")
    need = {"wave_min_pct", "rrr"}
    missing = need - set(df.columns)
    if missing:
        raise ValueError(f"Chybí sloupce: {sorted(missing)}. Dostupné: {list(df.columns)}")
    return df


def build_pivot(
    df: pd.DataFrame,
    metric: str,
    agg: str,
    timeframe: str | None,
    entry_mode: str | None,
) -> pd.DataFrame:
    if metric not in df.columns:
        raise ValueError(f"Sloupec '{metric}' v CSV není. Zkus: net_pnl_usd, profit_factor, sharpe, …")
    sub = df.copy()
    if timeframe:
        if "timeframe" not in sub.columns:
            raise ValueError("CSV nemá sloupec timeframe — nelze filtrovat --tf.")
        sub = sub[sub["timeframe"].astype(str) == timeframe]
    if entry_mode:
        if "entry_mode" not in sub.columns:
            raise ValueError("CSV nemá sloupec entry_mode.")
        sub = sub[sub["entry_mode"].astype(str) == entry_mode]
    if sub.empty:
        raise ValueError("Po filtrech nezbyla žádná řádka.")

    aggfunc = {"mean": "mean", "sum": "sum", "max": "max", "min": "min"}[agg]
    pt = sub.pivot_table(
        index="wave_min_pct",
        columns="rrr",
        values=metric,
        aggfunc=aggfunc,
    )
    pt = pt.sort_index(axis=0).sort_index(axis=1)
    return pt


def plot_heatmap(
    pt: pd.DataFrame,
    *,
    title: str,
    colorbar_label: str,
    out_path: Path | None,
    annotate: bool,
    cmap: str,
) -> Path | None:
    data = pt.to_numpy(dtype=float)
    nrows, ncols = data.shape

    fig, ax = plt.subplots(figsize=(max(8.0, ncols * 0.55), max(6.0, nrows * 0.35)))

    use_div = _metric_is_diverging(pt)
    cmap_use = "RdYlGn" if use_div else cmap
    vmin = vmax2 = None
    if use_div and np.isfinite(data).any():
        m = float(np.nanmax(np.abs(data)))
        if m > 0:
            vmin, vmax2 = -m, m

    im_kw: dict = dict(
        aspect="auto",
        cmap=cmap_use,
        origin="lower",
    )
    if vmin is not None and vmax2 is not None:
        im_kw["vmin"] = vmin
        im_kw["vmax"] = vmax2

    im = ax.imshow(data, **im_kw)
    ax.set_xticks(np.arange(ncols))
    ax.set_xticklabels([_fmt_axis(x) for x in pt.columns], rotation=45, ha="right")
    ax.set_yticks(np.arange(nrows))
    ax.set_yticklabels([_fmt_axis(y) for y in pt.index])
    ax.set_xlabel("rrr")
    ax.set_ylabel("wave_min_pct")
    ax.set_title(title, fontsize=11)
    fig.colorbar(im, ax=ax, shrink=0.82, label=colorbar_label)

    if annotate and nrows * ncols <= 400:
        for i in range(nrows):
            for j in range(ncols):
                v = data[i, j]
                if np.isfinite(v):
                    txt = f"{v:.0f}" if abs(v) >= 10 else f"{v:.1f}"
                    ax.text(
                        j,
                        i,
                        txt,
                        ha="center",
                        va="center",
                        fontsize=6,
                        color="white",
                        path_effects=[pe.withStroke(linewidth=2.0, foreground="black")],
                    )

    plt.tight_layout()
    if out_path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, dpi=140, bbox_inches="tight")
        plt.close(fig)
        return out_path
    plt.show()
    plt.close(fig)
    return None


def _metric_is_diverging(pt: pd.DataFrame) -> bool:
    """Heuristika: pokud má data znaménka + i -, použij diverging škálu."""
    s = pd.Series(pt.to_numpy().ravel()).dropna()
    if s.empty:
        return False
    return (s > 0).any() and (s < 0).any()


def _fmt_axis(x) -> str:
    try:
        xf = float(x)
        if abs(xf - round(xf)) < 1e-9:
            return str(int(round(xf)))
        return f"{xf:g}"
    except (TypeError, ValueError):
        return str(x)


def main() -> None:
    sys.path.insert(0, str(_repo_root()))

    p = argparse.ArgumentParser(description="Heatmap wave_min_pct × rrr z grid_report.xlsx")
    p.add_argument(
        "report",
        type=Path,
        nargs="?",
        default=None,
        help="Cesta k grid_report.xlsx (list vysledky) nebo starší .csv.",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Uložit PNG (např. heatmap_pnl.png). Bez -o se zobrazí okno.",
    )
    p.add_argument(
        "--metric",
        default="net_pnl_usd",
        help="Sloupec z reportu (default: net_pnl_usd).",
    )
    p.add_argument(
        "--agg",
        default="mean",
        choices=["mean", "sum", "max", "min"],
        help="Agregace při více řádcích ve stejné buňce (default: mean).",
    )
    p.add_argument("--tf", default=None, help="Filtrovat jen timeframe (např. M15).")
    p.add_argument("--entry-mode", default=None, dest="entry_mode", help="Filtrovat entry_mode.")
    p.add_argument("--annotate", action="store_true", help="Vypsat čísla v buňkách (do 400 buněk).")
    p.add_argument("--cmap", default="viridis", help="Matplotlib colormap pro ne-diverging metriky.")
    args = p.parse_args()

    report_path = args.report
    if report_path is None:
        results = _repo_root() / "results"
        if not results.is_dir():
            p.error("Není zadán report a složka results/ neexistuje.")
        xlsx = sorted(results.glob("**/grid_report.xlsx"), key=lambda x: x.stat().st_mtime, reverse=True)
        csv_legacy = sorted(results.glob("**/grid_report.csv"), key=lambda x: x.stat().st_mtime, reverse=True)
        candidates = xlsx or csv_legacy
        if not candidates:
            p.error("Není zadán report a v results/ není grid_report.xlsx ani grid_report.csv.")
        report_path = candidates[0]
        print(f"Použit nejnovější report: {report_path}")

    df = load_report(report_path)
    pt = build_pivot(df, args.metric, args.agg, args.tf, args.entry_mode)

    parts = [f"{args.metric} ({args.agg})", report_path.name]
    if args.tf:
        parts.append(f"TF={args.tf}")
    if args.entry_mode:
        parts.append(f"mode={args.entry_mode}")
    title = " — ".join(parts)

    saved = plot_heatmap(
        pt,
        title=title,
        colorbar_label=args.metric,
        out_path=args.output,
        annotate=args.annotate,
        cmap=args.cmap,
    )
    if saved:
        print(f"Uloženo: {saved}")


if __name__ == "__main__":
    main()
