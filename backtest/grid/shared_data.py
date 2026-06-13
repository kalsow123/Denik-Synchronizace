"""
Sdilena OHLC data pro grid workery (multiprocessing shared_memory).

Parent nacte CSV jednou, workery sdili stejne numpy pole bez kopie celeho DataFrame.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
import pandas as pd

try:
    from multiprocessing import shared_memory
except ImportError:  # pragma: no cover
    shared_memory = None  # type: ignore

_WORKER_BUNDLE: Optional["SharedOhlcBundle"] = None


@dataclass(frozen=True)
class SharedOhlcKey:
    symbol: str
    timeframe_label: str
    date_from: str | None
    date_to: str | None


@dataclass
class SharedOhlcBundle:
    """OHLC v shared memory + metadata pro rekonstrukci DataFrame ve workeru."""

    key: SharedOhlcKey
    shm_specs: dict[str, tuple[str, tuple[int, ...], str]]
    time_ns: np.ndarray
    _shm_handles: list[Any]
    _df: pd.DataFrame | None = None

    @classmethod
    def create(cls, df: pd.DataFrame, key: SharedOhlcKey) -> "SharedOhlcBundle":
        if shared_memory is None:
            raise RuntimeError("shared_memory neni k dispozici")
        specs: dict[str, tuple[str, tuple[int, ...], str]] = {}
        handles: list[Any] = []
        for col in ("open", "high", "low", "close"):
            arr = np.ascontiguousarray(df[col].to_numpy(dtype=np.float64, copy=True))
            shm = shared_memory.SharedMemory(create=True, size=arr.nbytes)
            buf = np.ndarray(arr.shape, dtype=arr.dtype, buffer=shm.buf)
            buf[:] = arr
            specs[col] = (shm.name, arr.shape, str(arr.dtype))
            handles.append(shm)
        time_ns = pd.to_datetime(df["time"]).astype("int64").to_numpy(copy=True)
        return cls(
            key=key,
            shm_specs=specs,
            time_ns=time_ns,
            _shm_handles=handles,
        )

    @classmethod
    def attach(cls, info: dict) -> "SharedOhlcBundle":
        if shared_memory is None:
            raise RuntimeError("shared_memory neni k dispozici")
        key = SharedOhlcKey(**info["key"])
        specs = info["shm_specs"]
        handles: list[Any] = []
        arrays: dict[str, np.ndarray] = {}
        for col, (name, shape, dtype_str) in specs.items():
            shm = shared_memory.SharedMemory(name=name)
            handles.append(shm)
            dtype = np.dtype(dtype_str)
            arrays[col] = np.ndarray(shape, dtype=dtype, buffer=shm.buf)
        time_ns = np.asarray(info["time_ns"], dtype=np.int64)
        bundle = cls(
            key=key,
            shm_specs=specs,
            time_ns=time_ns,
            _shm_handles=handles,
        )
        bundle._build_dataframe(arrays)
        return bundle

    def export_info(self) -> dict:
        return {
            "key": {
                "symbol": self.key.symbol,
                "timeframe_label": self.key.timeframe_label,
                "date_from": self.key.date_from,
                "date_to": self.key.date_to,
            },
            "shm_specs": self.shm_specs,
            "time_ns": self.time_ns.tolist(),
        }

    def _build_dataframe(self, arrays: dict[str, np.ndarray] | None = None) -> pd.DataFrame:
        if arrays is None:
            arrays = {}
            for col, (name, shape, dtype_str) in self.shm_specs.items():
                shm = shared_memory.SharedMemory(name=name)
                self._shm_handles.append(shm)
                dtype = np.dtype(dtype_str)
                arrays[col] = np.ndarray(shape, dtype=dtype, buffer=shm.buf)
        self._df = pd.DataFrame(
            {
                "time": pd.to_datetime(self.time_ns),
                "open": arrays["open"],
                "high": arrays["high"],
                "low": arrays["low"],
                "close": arrays["close"],
            }
        )
        return self._df

    def get_dataframe(self) -> pd.DataFrame:
        if self._df is None:
            self._build_dataframe()
        assert self._df is not None
        return self._df

    def matches(
        self,
        symbol: str,
        timeframe_label: str,
        date_from: str | None,
        date_to: str | None,
    ) -> bool:
        return (
            self.key.symbol == symbol
            and self.key.timeframe_label == timeframe_label
            and self.key.date_from == date_from
            and self.key.date_to == date_to
        )

    def close(self) -> None:
        for shm in self._shm_handles:
            try:
                shm.close()
            except Exception:
                pass
            try:
                shm.unlink()
            except Exception:
                pass
        self._shm_handles.clear()
        self._df = None


def init_grid_worker(bundle_info: dict | None) -> None:
    """ProcessPoolExecutor initializer — pripoji sdilena data ve workeru."""
    global _WORKER_BUNDLE
    if not bundle_info:
        _WORKER_BUNDLE = None
        return
    _WORKER_BUNDLE = SharedOhlcBundle.attach(bundle_info)


def get_worker_shared_bundle() -> SharedOhlcBundle | None:
    return _WORKER_BUNDLE


def try_create_shared_bundle(
    df: pd.DataFrame,
    *,
    symbol: str,
    timeframe_label: str,
    date_from: str | None,
    date_to: str | None,
) -> SharedOhlcBundle | None:
    try:
        return SharedOhlcBundle.create(
            df,
            SharedOhlcKey(symbol, timeframe_label, date_from, date_to),
        )
    except Exception:
        return None
