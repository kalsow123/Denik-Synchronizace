"""
Cache OHLC dat pro grid backtest.

Velky grid muze obsahovat tisice kombinaci pro stejny (symbol, timeframe).
Bez cache by se kazda kombinace musela znovu nacist z disku.

V multiprocessingu nelze sdilet python objekty mezi procesy primo - cache
funguje per-worker (kazdy worker si nacte vlastni kopii a drzi ji v pameti
po dobu sve existence).

Konvence pojmenovani CSV souboru:
  data/{SYMBOL}_{TIMEFRAME}.csv
  napr.: data/EURUSD_M15.csv, data/AUS200.cash_M5.csv
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from backtest.data_loader import load_csv, filter_by_date_range


# Per-process cache (kazdy worker ma vlastni)
_CACHE: dict = {}
# Filtrovany slice — stejne (symbol, tf, date_from, date_to) v gridu
_FILTERED_CACHE: dict = {}


def get_data_dir() -> Path:
    """
    Vraci adresar s historickymi CSV soubory.
    Lze prepsat env promennou BACKTEST_DATA_DIR.
    Default: <root>/data/
    """
    env = os.environ.get("BACKTEST_DATA_DIR")
    if env:
        return Path(env).resolve()
    # data/ na urovni trading_bot rootu (sourozenec backtest/)
    return Path(__file__).resolve().parents[2] / "data"


def csv_path_for(symbol: str, timeframe_label: str) -> Path:
    """Sestavi cestu k CSV podle konvence {DATA_DIR}/{symbol}_{tf}.csv."""
    return get_data_dir() / f"{symbol}_{timeframe_label}.csv"


def load_data(symbol: str, timeframe_label: str,
              date_from: str | None = None,
              date_to: str | None = None) -> pd.DataFrame:
    """
    Nacte (a zacachuje) historicka data pro dany symbol+timeframe.
    Datum filtrace probiha PO nacteni z cache.
    """
    from backtest.grid.shared_data import get_worker_shared_bundle

    bundle = get_worker_shared_bundle()
    if bundle is not None and bundle.matches(symbol, timeframe_label, date_from, date_to):
        return bundle.get_dataframe()

    filtered_key = (symbol, timeframe_label, date_from, date_to)
    if filtered_key in _FILTERED_CACHE:
        return _FILTERED_CACHE[filtered_key]

    key = (symbol, timeframe_label)
    if key not in _CACHE:
        path = csv_path_for(symbol, timeframe_label)
        if not path.exists():
            raise FileNotFoundError(
                f"CSV nenalezeno: {path}\n"
                f"Ocekavana cesta: {get_data_dir()}/{symbol}_{timeframe_label}.csv\n"
                f"Lze prepsat env promennou BACKTEST_DATA_DIR."
            )
        df = load_csv(path)
        _CACHE[key] = df

    df = _CACHE[key]
    if date_from or date_to:
        df = filter_by_date_range(df.copy(), date_from, date_to)
    else:
        df = df.copy()
    _FILTERED_CACHE[filtered_key] = df
    return df


def clear_cache() -> None:
    """Vymaze cache (uzitecne pro testy)."""
    _CACHE.clear()
    _FILTERED_CACHE.clear()
    from backtest.wave_sim_cache import clear_pine_sim_cache

    clear_pine_sim_cache()
