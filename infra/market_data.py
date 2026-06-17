
from typing import Optional

import MetaTrader5 as mt5
import pandas as pd

from config.bot_config import BotConfig

# ───── NAČÍTÁNÍ OHLC (candles) DAT Z MT5 ──────────────────────────

# Načítá candles z MT5 (copy_rates_from_pos) — live bot, ne CSV.
def get_bars(cfg: BotConfig, n: int = 300) -> Optional[pd.DataFrame]:
    rates = mt5.copy_rates_from_pos(cfg.symbol, cfg.timeframe, 0, n)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df
