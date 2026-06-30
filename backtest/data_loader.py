
from __future__ import annotations

import os
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

    from mt5_credentials import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH

    if not mt5.initialize(
        path=str(MT5_PATH),
        login=MT5_LOGIN,
        password=MT5_PASSWORD,
        server=MT5_SERVER,
    ):
        raise RuntimeError(f"MT5 initialize() selhal: {mt5.last_error()}")

    ai = mt5.account_info()
    if ai is None or ai.login != MT5_LOGIN or ai.server != MT5_SERVER:
        mt5.shutdown()
        raise RuntimeError(
            f"MT5 pripojen na jiny ucet nez credentials "
            f"(ocekavano {MT5_LOGIN}/{MT5_SERVER}, "
            f"je {getattr(ai, 'login', None)}/{getattr(ai, 'server', None)})"
        )

    rates = mt5.copy_rates_from_pos(symbol, tf, 0, n_bars)
    mt5.shutdown()

    if rates is None or len(rates) == 0:
        raise RuntimeError(f"MT5 nevratilo data pro {symbol} {timeframe_str}")

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df[["time", "open", "high", "low", "close", "tick_volume"]]

    # ───── CENTRALNI 2-LETE OKNO (bot-wide) ─────────────────────────
    # Jeden zdroj pravdy pro VSECHNY backtesty + regresni testy: posledni
    # BACKTEST_WINDOW_YEARS let dostupnych dat, odvozene z posledniho timestampu
    # datasetu (tj. "vzdy posledni 2 roky dostupnych dat", ne z dnesniho data).
def _effective_backtest_window_years() -> float | None:
    """Pocet let backtest okna: env BACKTEST_WINDOW_YEARS > config.BACKTEST_WINDOW_YEARS."""
    raw = os.environ.get("BACKTEST_WINDOW_YEARS")
    if raw is not None and str(raw).strip():
        try:
            return float(raw)
        except ValueError:
            pass
    from config.bot_config import BACKTEST_WINDOW_YEARS

    try:
        return float(BACKTEST_WINDOW_YEARS)
    except (TypeError, ValueError):
        return None


def _window_offset(years: float) -> pd.DateOffset | pd.Timedelta:
    """DateOffset pro celociselne roky; necelou cast dopocita pres dny (365.25/rok)."""
    whole = int(years)
    frac = years - whole
    off: pd.DateOffset | pd.Timedelta = pd.DateOffset(years=whole)
    if frac:
        off = off + pd.Timedelta(days=round(frac * 365.25))
    return off


def default_backtest_date_range(df: pd.DataFrame) -> tuple[str | None, str | None]:
    """
    Defaultni backtest okno = posledni BACKTEST_WINDOW_YEARS let DOSTUPNYCH dat.

    Start se odvozuje z posledniho timestampu datasetu (df['time'].iloc[-1]),
    konec = None (az do posledniho baru datasetu, inkluzivne). Vraci (date_from, date_to)
    jako YYYY-MM-DD retezce (resp. None), kompatibilni s filter_by_date_range.
    """
    if df is None or len(df) == 0 or "time" not in getattr(df, "columns", []):
        return None, None
    years = _effective_backtest_window_years()
    if years is None or years <= 0:
        return None, None
    last_ts = pd.Timestamp(df["time"].iloc[-1])
    start_ts = last_ts - _window_offset(years)
    return start_ts.strftime("%Y-%m-%d"), None


def resolve_backtest_date_range(
    date_from: str | None,
    date_to: str | None,
    df: pd.DataFrame,
) -> tuple[str | None, str | None]:
    """
    Vrati efektivni (date_from, date_to) pro backtest.

    Pokud uzivatel NEzadal ani date_from ani date_to, pouzije se centralni
    default = posledni 2 roky dostupnych dat (default_backtest_date_range).
    Explicitni datumy (CLI --date-from/--date-to, grid combo) maji prednost.
    """
    if date_from is None and date_to is None:
        return default_backtest_date_range(df)
    return date_from, date_to


    # Dataframe filtr (date_from / date_to)
def filter_by_date_range(df: pd.DataFrame,
                         date_from: str | None = None,
                         date_to: str | None = None) -> pd.DataFrame:
    if date_from:
        df = df[df["time"] >= pd.Timestamp(date_from)]
    if date_to:
        df = df[df["time"] <= pd.Timestamp(date_to)]
    return df.reset_index(drop=True)
