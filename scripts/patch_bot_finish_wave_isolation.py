#!/usr/bin/env python3
"""Doplni wave_isolation_study do legacy bot_finish_combos.json."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config.position_modes import normalize_legacy_wave_study_combo
DEFAULT_JSON = ROOT / "backtest" / "grid" / "bot_finish_combos.json"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--json", type=Path, default=DEFAULT_JSON)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    path = args.json if args.json.is_absolute() else ROOT / args.json
    payload = json.loads(path.read_text(encoding="utf-8"))
    combos = payload.get("combos", [])
    profile = {"wave_study": {"wave_positions_only": True, "wave_isolation_study": True}}

    patched = 0
    for c in combos:
        before = (c.get("wave_isolation_study"), c.get("wave_positions_only"))
        normalize_legacy_wave_study_combo(c, profile)
        after = (c.get("wave_isolation_study"), c.get("wave_positions_only"))
        if before != after:
            patched += 1

    print(f"Patched {patched} / {len(combos)} combos in {path}")
    if args.dry_run:
        return

    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
