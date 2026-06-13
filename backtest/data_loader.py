
from __future__ import annotations

from pathlib import Path

import pandas as pd

from backtest.io.csv_export import read_csv as read_csv_formatted

# ───── LOADING DATA CSV ──────────────────────────

# CSV ve /date
# MT5 fetch (live only); fallback (CSV)


TIME_COL_CANDIDATES = ["time", "datetime", "date", "<date>", "timestamp", "Date", "DateTime"]

def _find_time_col(columns: list) -> str:
    cols_lower = {c.lower(): c for c in columns}
    for candidate in TIME_COL_CANDIDATES:
        if candidate.lower() in cols_lower:
            return cols_lower[candidate.lower()]
    raise ValueError(
        f"CSV neobsahuje sloupec s casem. Dostupne sloupce: {columns}"
    )

    # Načtení CSV s OHLC daty.
def load_csv(path, time_col: str | None = None,
             datetime_format: str | None = None) -> pd.DataFrame:
    path = str(path)
    df = read_csv_formatted(path, format="auto")
    df.columns = [c.strip() for c in df.columns]

    if time_col is None:
        time_col = _find_time_col(list(df.columns))

    sample = str(df[time_col].iloc[0])
    try:
        float(sample)
        df["time"] = pd.to_datetime(df[time_col], unit="s")
    except (ValueError, TypeError):
        if datetime_format:
            df["time"] = pd.to_datetime(df[time_col], format=datetime_format)
        else:
            df["time"] = pd.to_datetime(df[time_col])

    df.columns = [c.lower() if c != "time" else "time" for c in df.columns]

    required = ["open", "high", "low", "close"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"CSV chybi sloupce: {missing}. Dostupne: {list(df.columns)}")

    for col in required:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=required + ["time"]).reset_index(drop=True)
    df = df.sort_values("time").reset_index(drop=True)

    vol_col = None
    if "tick_volume" in df.columns:
        vol_col = "tick_volume"
    elif "volume" in df.columns:
        vol_col = "volume"

    base_cols = ["time", "open", "high", "low", "close"]
    return df[base_cols + ([vol_col] if vol_col else [])]

    # Načtení dat z MT5 - když je MT5 dostupná only
def load_from_mt5(symbol: str, timeframe_str: str, n_bars: int = 5000) -> pd.DataFrame:
    try:
        import MetaTrader5 as mt5
    except ImportError:
        raise ImportError("MetaTrader5 neni nainstalovano. Pouzij load_csv().")

    TF_MAP = {
        "M1": mt5.TIMEFRAME_M1,
        "M3": mt5.TIMEFRAME_M3,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
        "W1": mt5.TIMEFRAME_W1,
    }
    tf = TF_MAP.get(timeframe_str.upper())
    if tf is None:
        raise ValueError(f"Neznamy timeframe: {timeframe_str}")

    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize() selhal: {mt5.last_error()}")

    rates = mt5.copy_rates_from_pos(symbol, tf, 0, n_bars)
    mt5.shutdown()

    if rates is None or len(rates) == 0:
        raise RuntimeError(f"MT5 nevratilo data pro {symbol} {timeframe_str}")

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df[["time", "open", "high", "low", "close", "tick_volume"]]

    # Dataframe filtr (date_from / date_to)
def filter_by_date_range(df: pd.DataFrame,
                         date_from: str | None = None,
                         date_to: str | None = None) -> pd.DataFrame:
    if date_from:
        df = df[df["time"] >= pd.Timestamp(date_from)]
    if date_to:
        df = df[df["time"] <= pd.Timestamp(date_to)]
    return df.reset_index(drop=True)
