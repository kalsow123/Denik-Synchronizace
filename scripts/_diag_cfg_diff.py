"""Porovna resolve_grid_engine_config vs resolve_live_execution_config:
vypise vsechna pole, ktera se lisi (mohou zpusobit jine trend stavy / routing)."""
from __future__ import annotations
import sys
from pathlib import Path
import dataclasses

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    from config.bot_config import LIVE_BOT_CONFIG
    from config.position_modes import resolve_grid_engine_config
    from runtime.live_wave_isolation import resolve_live_execution_config

    eng = resolve_grid_engine_config(LIVE_BOT_CONFIG)
    live = resolve_live_execution_config(LIVE_BOT_CONFIG)

    keys = set()
    for c in (eng, live):
        if dataclasses.is_dataclass(c):
            keys |= {f.name for f in dataclasses.fields(c)}
        else:
            keys |= {k for k in vars(c)}

    print(f"{'field':<42}{'ENGINE':>18}{'LIVE':>18}")
    print("-" * 78)
    n = 0
    for k in sorted(keys):
        ev = getattr(eng, k, "<MISSING>")
        lv = getattr(live, k, "<MISSING>")
        if ev != lv:
            n += 1
            print(f"{k:<42}{str(ev):>18}{str(lv):>18}")
    print(f"\n  rozdilnych poli: {n}")


if __name__ == "__main__":
    main()
