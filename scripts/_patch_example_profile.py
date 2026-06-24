"""Patch PROFILES['EXAMPLE'] in backtest_conf.py from generated blocks."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
conf = ROOT / "backtest" / "grid" / "backtest_conf.py"
blocks = (ROOT / "scripts" / "_generated_example_variation.txt").read_text(encoding="utf-8").rstrip()
text = conf.read_text(encoding="utf-8")

new_example = (
    'PROFILES["EXAMPLE"] = {\n'
    "    # VARIAC10 — combo_no 50, 53, 280, 207 (2025-06-10 .. 2026-06-10)\n"
    "    # zdroj: results/EURUSD/grid_report 2025-06-10 2026-06-10.xlsx\n"
    '    "grid": [\n'
    f"{blocks}\n"
    "    ],\n"
    "}"
)

pat = r'PROFILES\["EXAMPLE"\] = \{.*?\n\}\n\nPROFILES\["testing"\]'
m = re.search(pat, text, flags=re.DOTALL)
if not m:
    raise SystemExit("EXAMPLE block not found")

text = text[: m.start()] + new_example + '\n\nPROFILES["testing"]' + text[m.end() - len('PROFILES["testing"]') :]
conf.write_text(text, encoding="utf-8")
print("patched", conf)
