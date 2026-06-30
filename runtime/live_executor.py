"""
LiveExecutor — `Executor` implementace pro LIVE (VARIANTA A.txt §1.5 / §5.5, akce 2E).

ROLE:
  `BacktestEngine.process_bar()` ROZHODUJE (strategy/), `LiveExecutor` jen PROVÁDÍ
  rozhodnutí na MT5 přes `infra/orders.py` — pass-through parametrů, BEZ jakékoli
  změny rozhodnutí (ZÁVAZNÉ PRAVIDLO #7). Tím live cesta sdílí jeden rozhodovač
  s backtesterem a výsledky se shodují Z KONSTRUKCE.

STAV (akce 2E):
  Plná pass-through implementace. Modul musí být IMPORTOVATELNÝ bez balíčku
  MetaTrader5 — proto jsou VŠECHNY importy `infra/*` LÍNÉ (uvnitř metod) a modul
  je testovatelný s mockem. Rozhodovací mechanika (retry/filling/expiry, ticket
  mapy, dedup) zůstává v `infra/orders.py` / `infra/live_order_guard.py`.

LIVE-ONLY KONTRAKT (NESMÍ se ztratit — žije zde / v live obalu, NE v process_bar):
  - guard / dedup: `guard_live_send_order` + `block_duplicate_*` voláme PŘED odesláním
    (`guard_live_send_order` vrací True = BLOKOVAT — viz docstring v
    `runtime/live_wave_isolation.py`; executor se při True hned vrací, nic neodešle).
    `infra.orders.send_order` guard/dedup volá i interně — pass-through ho neobchází.
  - filling mode IOC/RETURN, retcode retry, backoff: uvnitř `infra/orders.py`.
  - on_bar_open / on_bar_range: v LIVE NE-simulujeme fill (broker plní realtime);
    rozhodnutí už proběhla na close baru → tyto metody jsou NO-OP.

NEBLOKUJÍCÍ KONTRAKT:
  Žádná metoda NESMÍ čekat na MT5. Když MT5 chybí / nic nevrátí (positions_get →
  None, ticket nenalezen), metoda se VRACÍ RYCHLE (no-op / None) — nikdy nezacyklí
  ani neblokuje live loop.

APPLY_ORDERS:
  `apply_orders=False` = cold-start / startup recovery replay BEZ reálného MT5 send
  (engine přepočítá stav, ale na MT5 se NIC neodešle/nemodifikuje/neruší). Read-only
  čtení (`get_open_positions`/`get_pendings`) běží i tak. Default True = produkce.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, List, Optional, Set

from backtest.executor import Executor

if TYPE_CHECKING:  # pragma: no cover - jen typy
    from backtest.engine import OpenTrade, PendingOrder
    from config.bot_config import BotConfig

log = logging.getLogger(__name__)

# MT5 comment prefixy — parita s `infra/orders.py` / `infra/state_sync.py`.
# (Drzime lokalni kopie konstant, aby modul sel importovat bez MetaTrader5.)
_WAVE_COMMENT_PREFIX = "W"          # WAVE base / EXT-1 base: "W{wave_time}"
_EXT_PRIMARY_PREFIX = "EWP_"        # EXT primarni WAVE
_TWO_SIDED_MIRROR_PREFIX = "TS2_"   # two-sided mirror
_COUNTER_PREFIX = "CNTR_"           # wave counter (LIMIT i G MARKET)
_PP_PREFIX = "PP_"                  # push-through pending
_BOS_REENTRY_PREFIX = "RENT_"       # BOS re-entry market


class LiveExecutor(Executor):
    """
    Live executor: provádí rozhodnutí `process_bar` na MT5 (pass-through).

    NEDRŽÍ žádné rozhodování — jen mapuje order/trade objekty engine na volání
    `infra/orders.py`. Stav engine (pending/open/closed) drží BacktestEngine
    kontejner uvnitř `LiveEngineSession`; live čtecí stav (MT5 pozice/pendingy)
    se synchronizuje read-only (`get_open_positions`/`get_pendings`).
    """

    def __init__(
        self,
        cfg: "BotConfig",
        sent_signals: Optional[Set[str]] = None,
        tracker_state: Any = None,
        *,
        apply_orders: bool = True,
    ) -> None:
        self.cfg = cfg
        self.sent_signals: Set[str] = sent_signals if sent_signals is not None else set()
        # tracker_state: drzi mj. mapu wave_time → mt5_ticket (sdileno s live_loop);
        # ticket lookup ale primarne resime pres comment (robustni vuci restartu).
        self.tracker_state = tracker_state
        self.apply_orders = bool(apply_orders)

    # --- pomocne mapovani engine objekt → MT5 comment / ticket ------------
    def _mt5_comment(self, obj: Any) -> str:
        """Sestavi MT5 comment (prefix + wave_time) pro engine order/trade.

        Parita s `infra/orders.py` (`_build_pending_request` default `W{wave_time}`
        a specializovane prefixy). Slouzi k dohledani ticketu pro cancel/modify/close.
        """
        wt = str(getattr(obj, "wave_time", "") or "")
        if bool(getattr(obj, "is_two_sided_mirror", False)):
            return f"{_TWO_SIDED_MIRROR_PREFIX}{wt}"[:31]
        if bool(getattr(obj, "is_counter", False)):
            return f"{_COUNTER_PREFIX}{wt}"[:31]
        if bool(getattr(obj, "is_pp", False)):
            return f"{_PP_PREFIX}{wt}"[:31]
        if bool(getattr(obj, "is_bos_reentry", False)):
            return f"{_BOS_REENTRY_PREFIX}{wt}"[:31]
        if bool(getattr(obj, "is_ext", False)):
            return f"{_EXT_PRIMARY_PREFIX}{wt}"[:31]
        return f"{_WAVE_COMMENT_PREFIX}{wt}"[:31]

    def _digits(self) -> int:
        """Cenove digits z MT5 (rychly fallback 5 kdyz MT5 nic nevrati)."""
        try:
            import MetaTrader5 as mt5  # type: ignore

            info = mt5.symbol_info(self.cfg.symbol)
            from infra.orders import _price_digits

            return _price_digits(info)
        except Exception:
            return 5

    def _find_position(self, trade: Any):
        """MT5 pozice odpovidajici engine trade (comment match). None = nenalezeno."""
        from infra.orders import find_bot_position_by_comment

        return find_bot_position_by_comment(self.cfg, self._mt5_comment(trade))

    def _find_pending(self, order: Any):
        """MT5 pending odpovidajici engine order (comment match). None = nenalezeno."""
        from infra.orders import find_bot_pending_by_comment

        return find_bot_pending_by_comment(self.cfg, self._mt5_comment(order))

    # --- order lifecycle ---------------------------------------------------
    def place_pending(
        self, order: "PendingOrder", bar_idx: int, bar_time: datetime
    ) -> None:
        """
        Pass-through pending → `infra/orders.py` podle typu orderu (1E gap-check):
          - WAVE base / EXT primary / TS2 mirror → `send_order(order.signal, cfg, ...)`
          - counter (`is_counter`)               → `place_counter_position_pending(...)`
          - PP (`is_pp`)                          → `place_pp_pending(...)`
          - BOS re-entry (`is_bos_reentry`)       → `place_bos_reentry_market(...)`

        PŘED send: `guard_live_send_order` (True = blokovat → return) a pro WAVE cestu
        i `block_duplicate_wave_order`. Specializovane `place_*` si guard/dedup volaji
        samy uvnitr infra. ZADNA hodnota z `order` se nemeni (pass-through).
        """
        if not self.apply_orders:
            return
        signal = getattr(order, "signal", None)
        if signal is None:
            return

        is_mirror = bool(getattr(order, "is_two_sided_mirror", False))

        # LIVE-ONLY guard PRED odeslanim (True = blokovat).
        from runtime.live_wave_isolation import guard_live_send_order

        if guard_live_send_order(self.cfg, signal, is_two_sided_mirror=is_mirror):
            return

        digits = self._digits()

        # Counter pending (LIMIT v opacnem smeru na TP urovni).
        if bool(getattr(order, "is_counter", False)):
            from infra.orders import place_counter_position_pending

            place_counter_position_pending(
                self.cfg,
                wave_time=str(order.wave_time),
                counter_dir=int(order.dir),
                tp_price=float(order.entry_price),
                counter_sl=float(order.sl),
                lot=float(order.lot),
                digits=digits,
                tp=(None if order.tp is None else float(order.tp)),
            )
            return

        # PP pending (push-through LIMIT).
        if bool(getattr(order, "is_pp", False)):
            from infra.orders import place_pp_pending

            place_pp_pending(
                self.cfg,
                wave_time=str(order.wave_time),
                trend_dir=int(order.dir),
                entry_price=float(order.entry_price),
                sl_price=float(order.sl),
                tp_price=(None if order.tp is None else float(order.tp)),
                lot=float(order.lot),
                digits=digits,
            )
            return

        # BOS re-entry (MARKET v novem smeru po flipu).
        if bool(getattr(order, "is_bos_reentry", False)):
            from infra.orders import place_bos_reentry_market

            place_bos_reentry_market(
                self.cfg,
                new_trend_dir=int(order.dir),
                entry_price=float(order.entry_price),
                sl_price=float(order.sl),
                lot=float(order.lot),
                digits=digits,
                broken_wave_time=str(order.wave_time),
                tp_price=(None if order.tp is None else float(order.tp)),
            )
            return

        # WAVE base / EXT primary / TS2 mirror → send_order (LIMIT primary + fallback).
        from infra.live_order_guard import block_duplicate_wave_order

        if not is_mirror and block_duplicate_wave_order(
            self.cfg, str(order.wave_time), label="WAVE"
        ):
            return

        from infra.orders import send_order

        send_order(
            signal,
            self.cfg,
            bypass_trend_filter=False,
            is_two_sided_mirror=is_mirror,
        )

    def place_market(
        self, trade: "OpenTrade", bar_idx: int, bar_time: datetime
    ) -> None:
        """
        MARKET vstup pass-through. Specializovane MARKET cesty dle typu, jinak
        `send_order` (ten dle ceny zvoli LIMIT primary / market_fallback per cfg).
        """
        if not self.apply_orders:
            return
        digits = self._digits()

        if bool(getattr(trade, "is_bos_reentry", False)):
            from infra.orders import place_bos_reentry_market

            place_bos_reentry_market(
                self.cfg,
                new_trend_dir=int(trade.dir),
                entry_price=float(trade.actual_entry),
                sl_price=float(trade.sl),
                lot=float(trade.lot),
                digits=digits,
                broken_wave_time=str(trade.wave_time),
                tp_price=(None if trade.tp is None else float(trade.tp)),
            )
            return

        if bool(getattr(trade, "is_counter", False)):
            from infra.orders import place_counter_position_market

            place_counter_position_market(
                self.cfg,
                wave_time=str(trade.wave_time),
                counter_dir=int(trade.dir),
                counter_sl=float(trade.sl),
                lot=float(trade.lot),
                digits=digits,
                tp=(None if trade.tp is None else float(trade.tp)),
                reference_ep=float(trade.actual_entry),
            )
            return

        if bool(getattr(trade, "is_pp", False)):
            from infra.orders import place_pp_market_fallback

            place_pp_market_fallback(
                self.cfg,
                wave_time=str(trade.wave_time),
                trend_dir=int(trade.dir),
                entry_price=float(trade.actual_entry),
                sl_price=float(trade.sl),
                tp_price=(None if trade.tp is None else float(trade.tp)),
                lot=float(trade.lot),
                digits=digits,
            )
            return

        # Base WAVE market → send_order (guard/dedup uvnitr).
        signal = getattr(getattr(trade, "pending", None), "signal", None)
        if signal is None:
            return
        from infra.orders import send_order

        send_order(
            signal,
            self.cfg,
            bypass_trend_filter=False,
            is_two_sided_mirror=bool(getattr(trade, "is_two_sided_mirror", False)),
        )

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
        Zavre MT5 pozici marketem se STEJNYM `reason` stringem jako engine
        (BOS_EXIT / TP_WAVE_N / SL …) — `_close_mt5_position_market`. Pozice se
        dohleda podle comment (`_mt5_comment`). Nenalezeno / chybi MT5 → no-op.
        """
        if not self.apply_orders:
            return
        p = self._find_position(trade)
        if p is None:
            return
        from infra.orders import _close_mt5_position_market

        _close_mt5_position_market(self.cfg, p, reason=reason, digits=self._digits())

    def cancel_pending(self, order: "PendingOrder") -> None:
        """MT5 `order_delete` podle ticketu dohledaneho z comment. Nenalezeno → no-op."""
        if not self.apply_orders:
            return
        o = self._find_pending(order)
        if o is None:
            return
        from infra.orders import cancel_pending_order

        cancel_pending_order(self.cfg, int(o.ticket))

    def modify_sltp(
        self,
        trade: "OpenTrade",
        *,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
    ) -> None:
        """
        MT5 `TRADE_ACTION_SLTP`. sl=None → drzi stavajici SL; tp=None → clear fixed TP
        (TS2 promote / two-sided TP clear). Pozice dohledana z comment.
        """
        if not self.apply_orders:
            return
        p = self._find_position(trade)
        if p is None:
            return
        from infra.orders import modify_position_sltp

        modify_position_sltp(self.cfg, p, sl=sl, tp=tp, digits=self._digits())

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
        """Partial close marketem (`close_position_partial_market`). reason zachovan."""
        if not self.apply_orders:
            return
        p = self._find_position(trade)
        if p is None:
            return
        from infra.orders import close_position_partial_market

        close_position_partial_market(
            self.cfg, p, float(lot), reason=reason, digits=self._digits()
        )

    def modify_lot(self, trade: "OpenTrade", lot: float) -> None:
        """
        Uprava lotu. MT5 neumi primy resize pozice: SNIZENI = partial close,
        ZVYSENI nelze atomicky (nechavame na engine routing / dalsi entry) → no-op
        s warningem. NIKDY neblokuje.
        """
        if not self.apply_orders:
            return
        p = self._find_position(trade)
        if p is None:
            return
        cur = float(getattr(p, "volume", 0.0) or 0.0)
        target = float(lot)
        if target >= cur:
            log.warning(
                "modify_lot: zvyseni lotu (%.2f→%.2f) MT5 neumi atomicky — no-op",
                cur, target,
            )
            return
        from infra.orders import close_position_partial_market

        close_position_partial_market(
            self.cfg, p, cur - target, reason="MODIFY_LOT", digits=self._digits()
        )

    # --- state readers -----------------------------------------------------
    def get_open_positions(self) -> List["OpenTrade"]:
        """
        Read-only snapshot otevrenych MT5 pozic tohoto bota (magic filter).
        Pro engine je read-only. Nenalezeno / chybi MT5 → []. NEBLOKUJE.

        Pozn.: vraci raw MT5 position objekty (engine je v live cteni nemutuje);
        bohatsi rekonstrukce na OpenTrade probiha v `LiveEngineSession` recovery.
        """
        try:
            import MetaTrader5 as mt5  # type: ignore

            positions = mt5.positions_get(symbol=self.cfg.symbol)
        except Exception:
            return []
        if not positions:
            return []
        return [p for p in positions if int(getattr(p, "magic", -1)) == int(self.cfg.magic)]

    def get_pendings(self) -> List["PendingOrder"]:
        """Read-only snapshot MT5 pendingu tohoto bota (magic filter). NEBLOKUJE."""
        try:
            import MetaTrader5 as mt5  # type: ignore

            orders = mt5.orders_get(symbol=self.cfg.symbol)
        except Exception:
            return []
        if not orders:
            return []
        return [o for o in orders if int(getattr(o, "magic", -1)) == int(self.cfg.magic)]

    # --- fill model (per-bar) ---------------------------------------------
    def on_bar_open(
        self, bar_idx: int, bar_time: datetime, high: float, low: float, open_: float
    ) -> None:
        """
        LIVE = NO-OP. Fill probiha u brokera v realnem case; backtest fill model
        (trigger pending proti rozsahu baru) se v live NESIMULUJE. Rozhodnuti uz
        probehla v `process_bar` na close baru.
        """
        return None

    def on_bar_range(
        self, bar_idx: int, bar_time: datetime, high: float, low: float
    ) -> None:
        """LIVE = NO-OP. SL/TP plni broker realtime (viz `on_bar_open`)."""
        return None

    # --- position-cap / expiry (executor-řízené údržbové kroky) ------------
    # process_bar volá i prune_pendings / enforce_overflow / expire_pendings.
    # V live je VYBER (které zrušit) rozhodnutí engine; PROVEDENÍ (MT5 cancel) dělá
    # infra. Pod apply_orders guardem; vše NEBLOKUJE.
    def prune_pendings(self, mid_price: float) -> List["PendingOrder"]:
        """Position-cap prune na MT5 (`enforce_live_position_cap`). Vraci [] (cancel
        provedl infra; engine nic dalsiho neproreze)."""
        if not self.apply_orders:
            return []
        try:
            from infra.position_cap_live import enforce_live_position_cap

            enforce_live_position_cap(self.cfg)
        except Exception as exc:  # pragma: no cover - obrana proti chybe MT5 readu
            log.warning("prune_pendings: enforce_live_position_cap selhalo: %s", exc)
        return []

    def enforce_overflow(
        self, bar_idx: int, bar_time: datetime, mid_price: float
    ) -> None:
        """Market-overflow pojistka na MT5 (`enforce_live_position_cap`)."""
        if not self.apply_orders:
            return None
        try:
            from infra.position_cap_live import enforce_live_position_cap

            enforce_live_position_cap(self.cfg)
        except Exception as exc:  # pragma: no cover
            log.warning("enforce_overflow: enforce_live_position_cap selhalo: %s", exc)
        return None

    def expire_pendings(self, bar_idx: int, bar_time: datetime) -> None:
        """Expiry pendingu na MT5 (`cancel_expired_pending`)."""
        if not self.apply_orders:
            return None
        try:
            from infra.orders import cancel_expired_pending

            cancel_expired_pending(self.cfg)
        except Exception as exc:  # pragma: no cover
            log.warning("expire_pendings: cancel_expired_pending selhalo: %s", exc)
        return None
