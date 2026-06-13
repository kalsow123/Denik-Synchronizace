"""
Numpy reprezentace OHLC pro rychly bar-by-bar backtest (bez opakovaneho df.iloc).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional

import numpy as np
import pandas as pd

_OHLC_DF_ATTR = "_backtest_ohlc_arrays"


@dataclass
class BarView:
    """Lehky nahrad za pd.Series v bar loopu — stejne bar['close'] API."""

    time: Any
    open: float
    high: float
    low: float
    close: float

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


@dataclass
class OhlcArrays:
    n: int
    time: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    after_data_gap: np.ndarray

    @classmethod
    def from_dataframe(cls, df: pd.DataFrame) -> "OhlcArrays":
        n = len(df)
        open_ = np.ascontiguousarray(df["open"].to_numpy(dtype=np.float64, copy=True))
        high = np.ascontiguousarray(df["high"].to_numpy(dtype=np.float64, copy=True))
        low = np.ascontiguousarray(df["low"].to_numpy(dtype=np.float64, copy=True))
        close = np.ascontiguousarray(df["close"].to_numpy(dtype=np.float64, copy=True))
        time = df["time"].to_numpy(copy=True)
        gap = np.asarray(
            compute_after_data_gap_mask(time),
            dtype=np.bool_,
        )
        return cls(
            n=n,
            time=time,
            open=open_,
            high=high,
            low=low,
            close=close,
            after_data_gap=gap,
        )

    def time_at(self, i: int) -> Any:
        return self.time[i]

    def bar_time_at(self, i: int) -> datetime:
        ts = pd.Timestamp(self.time[i])
        return ts.to_pydatetime()

    def bar_view(self, i: int) -> BarView:
        return BarView(
            time=self.time[i],
            open=float(self.open[i]),
            high=float(self.high[i]),
            low=float(self.low[i]),
            close=float(self.close[i]),
        )


def compute_after_data_gap_mask(
    times: np.ndarray | pd.Series,
    *,
    gap_mult: float = 2.5,
) -> List[bool]:
    """mask[i] True = bar i nasleduje po casove mezere bez svice (vikend apod.)."""
    n = len(times)
    mask = [False] * n
    if n < 2:
        return mask
    t = pd.to_datetime(times)
    deltas = t.diff().dropna()
    if deltas.empty:
        threshold = pd.Timedelta(minutes=30) * gap_mult
    else:
        md = deltas.median()
        if pd.isna(md) or md <= pd.Timedelta(0):
            threshold = pd.Timedelta(minutes=30) * gap_mult
        else:
            threshold = md * gap_mult
    for i in range(1, n):
        if t[i] - t[i - 1] > threshold:
            mask[i] = True
    return mask


def ohlc_from_dataframe(df: pd.DataFrame) -> OhlcArrays:
    cached = getattr(df, _OHLC_DF_ATTR, None)
    if isinstance(cached, OhlcArrays):
        return cached
    arr = OhlcArrays.from_dataframe(df)
    try:
        object.__setattr__(df, _OHLC_DF_ATTR, arr)
    except (AttributeError, TypeError):
        pass
    return arr
