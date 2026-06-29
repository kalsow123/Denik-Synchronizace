"""
LiveExecutor — `Executor` implementace pro LIVE (VARIANTA A.txt §1.5 / §5.5, akce 2E).

ROLE:
  `BacktestEngine.process_bar()` ROZHODUJE (strategy/), `LiveExecutor` jen PROVÁDÍ
  rozhodnutí na MT5 přes `infra/orders.py` — pass-through parametrů, BEZ jakékoli
  změny rozhodnutí (ZÁVAZNÉ PRAVIDLO #7). Tím live cesta sdílí jeden rozhodovač
  s backtesterem a výsledky se shodují Z KONSTRUKCE.

STAV (akce 2B):
  SKELETON. Cíl 2B je strangler vrstva (LiveEngineSession) za feature flagem
  `live_use_process_bar` (default OFF). Tento modul musí být IMPORTOVATELNÝ bez
  balíčku MetaTrader5 (proto jsou importy `infra/orders.py` LÍNÉ — uvnitř metod)
  a TESTOVATELNÝ s mockem. Plná MT5 mechanika (retry/filling/expiry, ticket mapy,
  partial close, modify SL/TP) zůstává odkazem na `infra/orders.py`; co chybí, je
  označeno `TODO(2E)`.

LIVE-ONLY KONTRAKT (NESMÍ se ztratit — žije zde / v live obalu, NE v process_bar):
  - guard / dedup: `guard_live_send_order` + `block_duplicate_*` voláme PŘED odesláním.
    (`infra.orders.send_order` už guard volá interně — pass-through ho neobchází.)
  - filling mode IOC/RETURN, retcode retry, backoff: uvnitř `infra/orders.py`.
  - on_bar_open / on_bar_range: v LIVE NE-simulujeme fill (broker plní realtime);
    rozhodnutí už proběhla na close baru → tyto metody jsou NO-OP (volitelně
    reconciliation hook). Viz dokumentace metod.

APPLY_ORDERS:
  `apply_orders=False` = cold-start / startup recovery replay bez reálného MT5 send
  (engine přepočítá stav, ale nic se neodešle). Default True = produkce.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional, Set

from backtest.executor import Executor

if TYPE_CHECKING:  # pragma: no cover - jen typy
    from backtest.engine import OpenTrade, PendingOrder
    from config.bot_config import BotConfig

log = logging.getLogger(__name__)


class LiveExecutor(Executor):
    """
    Live executor: provádí rozhodnutí `process_bar` na MT5 (pass-through).

    NEDRŽÍ žádné rozhodování — jen mapuje order objekty engine na volání
    `infra/orders.py`. Stav engine (pending/open/closed) drží BacktestEngine
    kontejner uvnitř `LiveEngineSession`; live čtecí stav (MT5 pozice/pendingy)
    se synchronizuje mimo `process_bar`.
    """

    def __init__(
        self,
        cfg: "BotConfig",
        sent_signals: Optional[Set[str]] = None,
        *,
        apply_orders: bool = True,
    ) -> None:
        self.cfg = cfg
        self.sent_signals: Set[str] = sent_signals if sent_signals is not None else set()
        self.apply_orders = bool(apply_orders)

    # --- order lifecycle ---------------------------------------------------
    def place_pending(
        self, order: "PendingOrder", bar_idx: int, bar_time: datetime
    ) -> None:
        """
        Pass-through pending → `infra.orders.send_order(order.signal, cfg, ...)`.

        `send_order` interně volá `guard_live_send_order` + `block_duplicate_wave_order`
        (dedup/idempotence vůči brokeru) a řeší filling/retcode/retry. Engine předává
        kompletní `signal` dict (fib50/sl/dir/wave_time/lot) → žádná změna parametrů.

        TODO(2E): rozlišit specializované cesty dle typu orderu (counter / pp /
        bos_reentry / TS2_ mirror / EXT) přes `is_counter`/`is_pp`/`is_two_sided_mirror`
        a příslušné `place_*` funkce v `infra/orders.py` (gap-check 1E). Skeleton 2B
        posílá WAVE pending pass-through.
        """
        if not self.apply_orders:
            return
        signal = getattr(order, "signal", None)
        if signal is None:
            return
        from infra.orders import send_order

        send_order(
            signal,
            self.cfg,
            bypass_trend_filter=False,
            is_two_sided_mirror=bool(getattr(order, "is_two_sided_mirror", False)),
        )

    def place_market(
        self, trade: "OpenTrade", bar_idx: int, bar_time: datetime
    ) -> None:
        """TODO(2E): MARKET vstup (`_place_market_fallback` / `place_*_market`)."""
        if not self.apply_orders:
            return
        # TODO(2E): pass-through na infra.orders market placement.

    def close_position(
        self,
        trade: "OpenTrade",
        *,
        reason: str,
        price: float,
        bar_idx: int,
        bar_time: datetime,
    ) -> None:
        """
        TODO(2E): zavřít MT5 pozici (`_close_mt5_position_market` /
        `close_positions_by_direction`) se STEJNÝM `reason` stringem jako engine
        (BOS_EXIT / TP_WAVE_N / SL). Skeleton 2B: pass-through hook.
        """
        if not self.apply_orders:
            return
        # TODO(2E): mapovat trade → MT5 ticket, zavřít na trhu, reason zachovat.

    def cancel_pending(self, order: "PendingOrder") -> None:
        """TODO(2E): MT5 `order_delete` podle ticketu mapovaného z comment/wave_time."""
        if not self.apply_orders:
            return
        # TODO(2E): cancel MT5 pending dle mapy wave_time → ticket.

    def modify_sltp(
        self,
        trade: "OpenTrade",
        *,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
    ) -> None:
        """TODO(2E): MT5 `TRADE_ACTION_SLTP`; tp=None = clear fixed TP (TS2 promote)."""
        if not self.apply_orders:
            return
        # TODO(2E): pass-through modify SL/TP na MT5.

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
        """TODO(2E): partial close API v infra/orders (případně tenký wrapper)."""
        if not self.apply_orders:
            return
        # TODO(2E): partial close pass-through.

    def modify_lot(self, trade: "OpenTrade", lot: float) -> None:
        """TODO(2E): úprava lotu (pokud broker podporuje; jinak close+open)."""
        if not self.apply_orders:
            return
        # TODO(2E): modify lot pass-through.

    # --- state readers -----------------------------------------------------
    def get_open_positions(self) -> List["OpenTrade"]:
        """
        TODO(2E): read-only sync MT5 pozic / tracker_state (infra/state_sync.py).
        Skeleton 2B vrací prázdný seznam (engine kontejner drží vlastní stav).
        """
        return []

    def get_pendings(self) -> List["PendingOrder"]:
        """TODO(2E): read-only sync MT5 pendingů / tracker_state. Skeleton 2B: []."""
        return []

    # --- fill model (per-bar) ---------------------------------------------
    def on_bar_open(
        self, bar_idx: int, bar_time: datetime, high: float, low: float, open_: float
    ) -> None:
        """
        LIVE = NO-OP. Fill probíhá u brokera v reálném čase; backtest fill model
        (trigger pending proti rozsahu baru) se v live NESIMULUJE. Rozhodnutí už
        proběhla v `process_bar` na close baru. Volitelně sem patří reconciliation
        hook (sync MT5 fills) — mimo scope 2B skeletonu.
        """
        return None

    def on_bar_range(
        self, bar_idx: int, bar_time: datetime, high: float, low: float
    ) -> None:
        """
        LIVE = NO-OP. SL/TP plní broker realtime; backtest exit fill model se
        v live NESIMULUJE (viz `on_bar_open`).
        """
        return None

    # --- position-cap / expiry (executor-řízené údržbové kroky) ------------
    # process_bar volá i prune_pendings / enforce_overflow / expire_pendings.
    # V live je výběr (KTERÉ pendingy zrušit) rozhodnutí engine; PROVEDENÍ (MT5
    # cancel) je TODO(2E). Skeleton: prune vrací [] (engine nic neproreže navíc),
    # ostatní jsou no-op. (Default flag OFF → tato cesta se v produkci nespustí.)
    def prune_pendings(self, mid_price: float) -> List["PendingOrder"]:
        """TODO(2E): cancel vybraných MT5 pendingů (position-cap). Skeleton: []."""
        return []

    def enforce_overflow(
        self, bar_idx: int, bar_time: datetime, mid_price: float
    ) -> None:
        """TODO(2E): enforce market overflow na MT5 (cancel/close). Skeleton no-op."""
        return None

    def expire_pendings(self, bar_idx: int, bar_time: datetime) -> None:
        """
        TODO(2E): expiry pendingů. V live to dělá `infra.orders.cancel_expired_pending(cfg)`
        v orchestraci live_loop (před barem), NE simulace expiry. Skeleton no-op.
        """
        return None
