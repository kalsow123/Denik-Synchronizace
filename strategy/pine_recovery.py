
from typing import Dict, List, Tuple

import pandas as pd

from config.bot_config import BotConfig
from strategy.wave_detection_pine import run_pine_wave_simulation

# ───── START EP PREVIOUS POSITIONS ──────────────────────────
# Filtruje při zapnutí bota historii. Rozpoznává z jakých vln už byly otevřeny
# pozice a z jakých ještě ne. Nerealizvoané ordery zadává do MT5

"""Vraci tuple (pending, open_trades), kde:
      - pending     = setupy, ktere k poslednimu baru jeste cekaji na entry
      - open_trades = setupy, ktere zaznamenaly entry a jeste neumrely na SL/TP  """


def simulate_pine_pending_state(df, cfg: BotConfig) -> Tuple[List[dict], List[dict]]:
    if df is None or len(df) < 2:
        return [], []

    waves_all, birth, _, _ = run_pine_wave_simulation(df, cfg)
    waves_by_bar: Dict[int, List[dict]] = {}
    for w in waves_all:
        bi = birth.get(w["wave_time"])
        if bi is not None:
            waves_by_bar.setdefault(bi, []).append(w)

    pending: List[dict] = []
    open_trades: List[dict] = []

    for i in range(1, len(df)):
        row = df.iloc[i]
        high = float(row["high"])
        low = float(row["low"])

        # 1) cekajici pendingy (LIMIT) -> vstup
        # BUY  LIMIT (dir=1): cena musi KLESNOUT na entry  -> low  <= fib50
        # SELL LIMIT (dir=-1): cena musi VYSTOUPAT na entry -> high >= fib50
        new_pending = []
        for p in pending:
            hit = low <= p["fib50"] if p["dir"] == 1 else high >= p["fib50"]
            if hit:
                open_trades.append({
                    "dir": p["dir"],
                    "fib50": p["fib50"],
                    "sl": p["sl"],
                    "tp": p["tp"],
                    "wave_time": p["wave_time"],
                    "entry_bar": i,
                })
            else:
                new_pending.append(p)
        pending = new_pending

        # 2) SL/TP otevrenych pozic az od dalsiho baru
        still_open = []
        for t in open_trades:
            if i > t["entry_bar"]:
                sl_hit = low <= t["sl"] if t["dir"] == 1 else high >= t["sl"]
                tp_hit = high >= t["tp"] if t["dir"] == 1 else low <= t["tp"]
                if sl_hit or tp_hit:
                    continue
            still_open.append(t)
        open_trades = still_open

        # 3) nove setupy z vln potvrzenych na tomto baru (stejna detekce jako detect_waves)
        for w in waves_by_bar.get(i, []):
            if not bool(getattr(cfg, "wave_position_enabled", True)):
                continue
            row_out = {
                "dir": w["dir"],
                "fib50": w["fib50"],
                "sl": w["sl"],
                "tp": w["tp"],
                "move_pct": w["move_pct"],
                "wave_time": w["wave_time"],
            }
            if "fib_abort" in w:
                row_out["fib_abort"] = w["fib_abort"]
            pending.append(row_out)

    return pending, open_trades
