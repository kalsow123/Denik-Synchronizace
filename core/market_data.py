"""
Unified OHLC data layer — backtest (CSV) and live (MT5).

VARIANTA A.txt akce 2D: jedno API ``load_bars`` se sdíleným forming-bar pravidlem.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

import pandas as pd

from backtest.data_loader import load_csv
from backtest.grid.data_cache import csv_path_for
from config.bot_config import BotConfig

OHLC_COLUMNS = ("time", "open", "high", "low", "close")


def strip_forming_bar(df: pd.DataFrame) -> pd.DataFrame:
    """
    Odstraní MT5 forming bar (poslední řádek).

    Stejná logika jako ``runtime.live_loop._df_closed_bars_only`` /
    ``runtime.live_engine_session.closed_bars_only``.
    """
    if len(df) < 2:
        return df
    return df.iloc[:-1].reset_index(drop=True)


def _normalize_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Kanonické schema §4.2: time, open, high, low, close."""
    missing = [c for c in OHLC_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"OHLC chybi sloupce: {missing}. Dostupne: {list(df.columns)}")
    out = df.loc[:, list(OHLC_COLUMNS)].copy()
    out["time"] = pd.to_datetime(out["time"])
    return out.reset_index(drop=True)


def _default_csv_path(cfg: BotConfig) -> Path:
    return csv_path_for(cfg.symbol, cfg.timeframe_label)


def load_bars(
    cfg: BotConfig,
    *,
    source: Literal["csv", "mt5"],
    n: int = 300,
    path: Path | None = None,
    include_forming: bool = False,
) -> Optional[pd.DataFrame]:
    """
    Načte OHLC data z CSV (backtest) nebo MT5 (live).

    Vrací DataFrame se sloupci: time, open, high, low, close (§4.2).

    Parametr ``n`` platí jen pro ``source="mt5"`` (počet barů z
    ``copy_rates_from_pos``). CSV načte celý soubor (volitelně přes ``path``).

    Forming bar (MT5): poslední řádek je nedokončený bar. Při
    ``include_forming=False`` (default, live chování) se ořízne přes
    ``strip_forming_bar`` — stejně jako dříve ``_df_closed_bars_only`` v
    ``live_loop``.

    TZ: sloupec ``time`` je timezone-naive — shodně s ``load_csv`` a
    ``infra.market_data.get_bars`` (MT5 Unix epoch → UTC wall-clock bez tz
    info; CSV timestampy parsed as-is). ``cfg.session_timezone`` se aplikuje
    až v runtime (session_manager), ne zde.
    """
    if source == "csv":
        csv_path = Path(path) if path is not None else _default_csv_path(cfg)
        df = load_csv(csv_path)
        return _normalize_ohlc(df)

    if source == "mt5":
        from infra.market_data import get_bars

        df = get_bars(cfg, n)
        if df is None or len(df) == 0:
            return None
        df = _normalize_ohlc(df)
        if not include_forming:
            df = strip_forming_bar(df)
        return df

    raise ValueError(f"Neznamy source: {source!r} (ocekavano 'csv' nebo 'mt5')")
