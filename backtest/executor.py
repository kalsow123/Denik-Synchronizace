"""
Executor protokol (I/O hranice mezi rozhodovanim a provedenim) — VARIANTA A.txt
sekce 1.5 / 3.4 (akce 1D).

CIL:
  `BacktestEngine.process_bar()` rozhoduje (strategy/), ale order lifecycle
  (pending/market placement, SL/TP fill, cancel/expire, prune, partial close,
  modify SL/TP/lot) provadi VYHRADNE pres `Executor` rozhrani. V backtestu
  rozhrani implementuje `BacktestExecutor`, ktery obaluje dnesni in-memory
  simulaci (`engine.pending_orders` / `open_trades` / `closed_trades`,
  `_trigger_pending` fill model, `_check_sl_tp` exit fill, position-cap prune).

  Ve fazi 2 stejne rozhrani implementuje `LiveExecutor` (infra/orders -> MT5),
  takze `process_bar` zustane beze zmeny a rozhodnuti se shoduji Z KONSTRUKCE.

PARITA (tvrda podminka 1D):
  `BacktestExecutor` jen PRESOUVA dnesni simulacni mechaniku za rozhrani —
  vola tytéž metody enginu (`_trigger_pending`, `_check_sl_tp`, `_expire_pending`,
  `apply_pending_prune`, `enforce_market_overflow`, `_make_closed`, ...), takze
  `closed_trades` zustavaji bit-identicke s puvodnim monolitem.

GAP-CHECK (rozhrani nesmi byt moc uzke — viz 3.4):
  - §24 TS2 lot mirror        -> `place_pending(...)` (engine `_add_pending`
                                  predava lot z `strategy/two_sided.py`).
  - EXT-1 broker SL ochrana   -> `modify_sltp` / `close_position` (rozhodnuti
                                  `_ext1_close_blocked` zustava v enginu/strategy).
  - position-cap prune        -> `prune_pendings(...)` (cancel-semantika nad
                                  `apply_pending_prune`).
  - two-sided promote/TP clear -> `modify_sltp(tp=None)` / `close_position`
                                  (trade_tracker promote/TP clear).
"""
from __future__ import annotations

import abc
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

from backtest.position_cap import apply_pending_prune, enforce_market_overflow

if TYPE_CHECKING:  # pragma: no cover - jen typy, zadny runtime import (cykly)
    import pandas as pd

    from backtest.engine import BacktestEngine, ClosedTrade, OpenTrade, PendingOrder
    from backtest.ohlc_arrays import OhlcArrays
    from config.bot_config import BotConfig


class BarContext:
    """
    Explicitni stav baru predavany mezi `prepare()` a `process_bar()`.

    Drzi to, co bylo drive lokalnimi promennymi bar-loopu `engine.run()`:
      - read-only precompute: `df`, `ohlc`, `cfg`, `waves_by_bar`,
        `waves_by_end_bar`, `all_waves`, `wave_birth`,
      - mutable stav prubehu: `waves_up_to_now`, `protected_waves_bar`.

    Engine-uroven stav (pending/open/closed trady, trackery, EXT/PP/WF state)
    zustava na `self` enginu (kontejner backtestu); fazi 2 ho prevezme executor.
    """

    def __init__(
        self,
        *,
        df: "pd.DataFrame",
        ohlc: "OhlcArrays",
        cfg: "BotConfig",
        waves_by_bar: Dict[int, List[dict]],
        waves_by_end_bar: Dict[int, List[dict]],
        all_waves: List[dict],
        wave_birth: Dict[str, int],
    ) -> None:
        self.df = df
        self.ohlc = ohlc
        self.cfg = cfg
        self.waves_by_bar = waves_by_bar
        self.waves_by_end_bar = waves_by_end_bar
        self.all_waves = all_waves
        self.wave_birth = wave_birth
        # Mutable prubehovy stav (akumuluje se pres bary).
        self.waves_up_to_now: List[dict] = []
        self.protected_waves_bar: Set[str] = set()


class Executor(abc.ABC):
    """
    Abstraktni I/O hranice. `process_bar` vola JEN tyto metody pro provedeni
    rozhodnuti — zadne live-only veci (MT5, session, lock, recovery, telemetry)
    sem nepatri (ty zije v live obalu, ne v rozhrani provedeni).
    """

    # --- order lifecycle ---------------------------------------------------
    @abc.abstractmethod
    def place_pending(
        self, order: "PendingOrder", bar_idx: int, bar_time: datetime
    ) -> None:
        ...

    @abc.abstractmethod
    def place_market(
        self, trade: "OpenTrade", bar_idx: int, bar_time: datetime
    ) -> None:
        ...

    @abc.abstractmethod
    def close_position(
        self,
        trade: "OpenTrade",
        *,
        reason: str,
        price: float,
        bar_idx: int,
        bar_time: datetime,
    ) -> None:
        ...

    @abc.abstractmethod
    def cancel_pending(self, order: "PendingOrder") -> None:
        ...

    @abc.abstractmethod
    def modify_sltp(
        self,
        trade: "OpenTrade",
        *,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
    ) -> None:
        ...

    @abc.abstractmethod
    def close_partial(
        self,
        trade: "OpenTrade",
        lot: float,
        *,
        reason: str,
        price: float,
        bar_idx: int,
        bar_time: datetime,
    ) -> None:
        ...

    @abc.abstractmethod
    def modify_lot(self, trade: "OpenTrade", lot: float) -> None:
        ...

    # --- state readers -----------------------------------------------------
    @abc.abstractmethod
    def get_open_positions(self) -> List["OpenTrade"]:
        ...

    @abc.abstractmethod
    def get_pendings(self) -> List["PendingOrder"]:
        ...

    # --- fill model (per-bar) ---------------------------------------------
    @abc.abstractmethod
    def on_bar_open(
        self, bar_idx: int, bar_time: datetime, high: float, low: float, open_: float
    ) -> None:
        """Entry fill model: trigger pending STOP/LIMIT proti rozsahu baru."""
        ...

    @abc.abstractmethod
    def on_bar_range(
        self, bar_idx: int, bar_time: datetime, high: float, low: float
    ) -> None:
        """Exit fill model: SL/TP kontrola otevrenych pozic pres rozsah baru."""
        ...


class BacktestExecutor(Executor):
    """
    In-memory simulacni executor — obaluje dnesni mechaniku `BacktestEngine`.

    Drzi referenci na engine a deleguje na jeho existujici metody/seznamy, takze
    chovani je bit-identicke s puvodnim monolitem (parita 1D). `process_bar` tim
    ziska stabilni rozhrani, ktere ve fazi 2 nahradi `LiveExecutor`.
    """

    def __init__(self, engine: "BacktestEngine") -> None:
        self.engine = engine

    # --- order lifecycle ---------------------------------------------------
    def place_pending(
        self, order: "PendingOrder", bar_idx: int, bar_time: datetime
    ) -> None:
        eng = self.engine
        eng.pending_orders.append(order)
        eng._append_pending_vis("pending_created", bar_idx, bar_time, order)
        eng.wave_debug["orders_created_pending"] += 1

    def place_market(
        self, trade: "OpenTrade", bar_idx: int, bar_time: datetime
    ) -> None:
        self.engine.open_trades.append(trade)

    def close_position(
        self,
        trade: "OpenTrade",
        *,
        reason: str,
        price: float,
        bar_idx: int,
        bar_time: datetime,
    ) -> None:
        self.engine._close_position_market(
            trade, reason=reason, price=price, bar_idx=bar_idx, bar_time=bar_time
        )

    def cancel_pending(self, order: "PendingOrder") -> None:
        eng = self.engine
        eng.pending_orders = [o for o in eng.pending_orders if o is not order]

    def modify_sltp(
        self,
        trade: "OpenTrade",
        *,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        _tp_set: bool = False,
    ) -> None:
        if sl is not None:
            trade.sl = float(sl)
        if _tp_set or tp is not None:
            trade.tp = None if tp is None else float(tp)

    def close_partial(
        self,
        trade: "OpenTrade",
        lot: float,
        *,
        reason: str,
        price: float,
        bar_idx: int,
        bar_time: datetime,
    ) -> None:
        eng = self.engine
        part = max(0.0, min(float(lot), float(trade.lot)))
        if part <= 0.0:
            return
        remaining = float(trade.lot) - part
        original_lot = trade.lot
        trade.lot = part
        ct = eng._make_closed(trade, bar_idx, price, bar_time, reason)
        eng._append_closed_trade(ct, bar_time)
        if remaining > 0.0:
            trade.lot = remaining
        else:
            trade.lot = original_lot
            eng.open_trades = [t for t in eng.open_trades if t is not trade]

    def modify_lot(self, trade: "OpenTrade", lot: float) -> None:
        trade.lot = float(lot)

    # --- state readers -----------------------------------------------------
    def get_open_positions(self) -> List["OpenTrade"]:
        return self.engine.open_trades

    def get_pendings(self) -> List["PendingOrder"]:
        return self.engine.pending_orders

    # --- fill model (per-bar) ---------------------------------------------
    def on_bar_open(
        self, bar_idx: int, bar_time: datetime, high: float, low: float, open_: float
    ) -> None:
        self.engine._trigger_pending(bar_idx, bar_time, high, low, open_)

    def on_bar_range(
        self, bar_idx: int, bar_time: datetime, high: float, low: float
    ) -> None:
        self.engine._check_sl_tp(bar_idx, bar_time, high, low)

    # --- position-cap / expiry (executor-rizene udrzbove kroky) -----------
    def prune_pendings(self, mid_price: float) -> List["PendingOrder"]:
        return apply_pending_prune(self.engine, mid_price)

    def enforce_overflow(
        self, bar_idx: int, bar_time: datetime, mid_price: float
    ) -> None:
        enforce_market_overflow(self.engine, bar_idx, bar_time, mid_price)

    def expire_pendings(self, bar_idx: int, bar_time: datetime) -> None:
        self.engine._expire_pending(bar_idx, bar_time)
