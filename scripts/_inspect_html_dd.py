"""Inspect DD labels in grid HTML vs xlsx."""
import json
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
run_dir = ROOT / "results/EURUSD/grid_EXAMPLE_M30_2024-05-10_2024-11-09_001"
html_path = run_dir / "grid_all_equity_interactive.html"
xlsx_path = run_dir / "grid_report.xlsx"


def main():
    html = html_path.read_text(encoding="utf-8")
    idx = html.find("-10,491 USD")
    print(f"-10,491 USD found at index: {idx}")
    if idx >= 0:
        print("context:", html[idx - 400 : idx + 150].replace("\n", " ")[:600])

    # Extract plotly data JSON
    m = re.search(r'Plotly\.newPlot\(\s*"[^"]+",\s*(\[.*?\])\s*,\s*\{', html, re.DOTALL)
    if not m:
        print("Could not parse Plotly data")
        return
    data = json.loads(m.group(1))

    combo1_groups = []
    for trace in data:
        lg = trace.get("legendgroup", "")
        text = trace.get("text")
        if text and isinstance(text, list):
            for t in text:
                if t and ("USD" in str(t) or "DD" in str(t)):
                    combo1_groups.append((lg, trace.get("name"), t))
        if "combo 1" in lg.lower() or "combo_1" in lg.lower() or lg.endswith("_1"):
            pass

    print("\nAll DD USD annotations in HTML:")
    for lg, name, t in combo1_groups:
        print(f"  group={lg!r} name={name!r} text={t!r}")

    # xlsx combo 1
    df = pd.read_excel(xlsx_path, sheet_name="grid_summary")
    c1 = df[df["combo_no"] == 1].iloc[0]
    print("\nxlsx combo 1:")
    for col in ["max_dd_usd", "max_dd_%_vs_initial", "max_dd_%"]:
        if col in c1:
            print(f"  {col}: {c1[col]}")


if __name__ == "__main__":
    main()
