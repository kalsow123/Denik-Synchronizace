"""
END-TO-END verifikace: ziva smycka (replay_missed_closed_bar) proti FAKE MT5
brokeru pres historicke CSV. Cil: zmerit, zda LIVE plumbing (placement + BOS /
WAVE_TARGET_N / G-extension exity) da stejne WAVE PnL/DDi jako backtest engine.

Trust model:
  - Fake broker drzi pending/position stav a na KAZDY uzavreny bar aplikuje
    fill model (trigger pending) + broker SL/TP — verbatim podle backtest enginu
    (_trigger_pending L1789-1804, _check_sl_tp L1898-1946), slippage=0.
  - Vsechny exit ROZHODNUTI (BOS close, TP_WAVE_N, G-ext) dela SKUTECNY live kod
    (infra.orders.close_*), ktery cte fake pozice — tj. testujeme realne live
    plumbing, ne nasi simulaci.
  - Poradi na baru = engine: BOS/TP closy (replay) -> entries (replay) ->
    trigger fill -> SL/TP. Pozice fillnuta na baru i se SL/TP kontroluje az od i+1
    (engine: bar_idx <= entry_bar -> skip).

POZN.: realny MT5 broker NEzna EXT-1/E23 protekci (tu ma jen engine _check_sl_tp).
Fake broker proto protekci NEaplikuje (= realita live). Pripadny rozdil vuci
enginu na EXT-1 vlnach je tak SKUTECNE zjisteni, ne chyba simulace.

Spusteni: .venv\\Scripts\\python.exe scripts/e2e_live_broker_sim.py
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATE_FROM = "2025-11-10"
DATE_TO = "2026-05-09"
CSV = ROOT / "data" / "EURUSD_M30.csv"
SPREAD = 0.00002  # DEFAULT_BACKTEST_SPREAD (parita decision-price s enginem)


# ---------------------------------------------------------------------------
# FAKE MetaTrader5
# ---------------------------------------------------------------------------
def _ns(**kw):
    return types.SimpleNamespace(**kw)


def _clean_wave_time(comment: str) -> str:
    """Normalizuj MT5 comment na cisty wave_time (jako backtest trade.wave_time)."""
    from infra.pending_snapshot import wave_time_from_pending_comment

    wt = wave_time_from_pending_comment(comment)
    return wt if wt else str(comment or "")


class FakeMt5:
    # konstanty
    TIMEFRAME_M1 = 1; TIMEFRAME_M3 = 3; TIMEFRAME_M5 = 5; TIMEFRAME_M15 = 15
    TIMEFRAME_M30 = 30; TIMEFRAME_H1 = 16385; TIMEFRAME_H4 = 16388
    TIMEFRAME_D1 = 16408; TIMEFRAME_W1 = 32769
    TRADE_ACTION_DEAL = 1; TRADE_ACTION_PENDING = 5
    TRADE_ACTION_SLTP = 6; TRADE_ACTION_REMOVE = 8
    ORDER_TYPE_BUY = 0; ORDER_TYPE_SELL = 1
    ORDER_TYPE_BUY_LIMIT = 2; ORDER_TYPE_SELL_LIMIT = 3
    ORDER_TYPE_BUY_STOP = 4; ORDER_TYPE_SELL_STOP = 5
    POSITION_TYPE_BUY = 0; POSITION_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_RETURN = 2; ORDER_FILLING_IOC = 1; ORDER_FILLING_FOK = 0
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_REQUOTE = 10004
    TRADE_RETCODE_PRICE_OFF = 10015
    TRADE_RETCODE_INVALID_PRICE = 10015

    _TYPE_NAME = {
        2: "BUY_LIMIT", 3: "SELL_LIMIT", 4: "BUY_STOP", 5: "SELL_STOP",
        0: "BUY", 1: "SELL",
    }

    def __init__(self, symbol: str, contract_size: float):
        self.symbol = symbol
        self.contract_size = float(contract_size)
        self.digits = 5
        self.point = 1e-5
        self._tick_ask = 0.0
        self._tick_bid = 0.0
        self._tick_time = 0
        self._orders: list = []      # pending
        self._positions: list = []   # open
        self._closed: list = []      # E2E vysledky (wave_time, dir, lot, ep, cp, reason, pnl)
        self._deals: list = []
        self._seq = 1000
        self._bar_idx = 0
        self._bar_time = None
        self.promoted_waves: set[str] = set()

    # --- bar context ---
    def set_bar(self, bar_idx: int, bar_time, close: float):
        self._bar_idx = int(bar_idx)
        self._bar_time = bar_time
        half = SPREAD / 2.0
        self._tick_ask = float(close) + half
        self._tick_bid = float(close) - half
        try:
            self._tick_time = int(bar_time.timestamp())
        except Exception:
            self._tick_time = 0

    def _next_ticket(self) -> int:
        self._seq += 1
        return self._seq

    # --- lifecycle / info ---
    def initialize(self, *a, **k):
        return True

    def shutdown(self, *a, **k):
        return True

    def last_error(self):
        return (0, "ok")

    def version(self):
        return (500, 3800, "fake")

    def terminal_info(self):
        return _ns(connected=True, trade_allowed=True, community_account=False)

    def account_info(self):
        return _ns(login=0, balance=100000.0, equity=100000.0, currency="USD",
                   margin_free=100000.0, leverage=100, trade_allowed=True)

    def symbol_select(self, *a, **k):
        return True

    def symbol_info(self, *a, **k):
        return _ns(
            name=self.symbol, digits=self.digits, point=self.point, visible=True,
            trade_contract_size=self.contract_size, trade_stops_level=0,
            trade_freeze_level=0, volume_min=0.01, volume_max=1000.0,
            volume_step=0.01, spread=int(round(SPREAD / self.point)),
            trade_tick_size=self.point, trade_tick_value=self.contract_size * self.point,
            bid=self._tick_bid, ask=self._tick_ask,
            currency_profit="USD", currency_base="EUR",
        )

    def symbol_info_tick(self, *a, **k):
        return _ns(ask=self._tick_ask, bid=self._tick_bid, last=self._tick_bid,
                   time=self._tick_time, volume=0)

    def orders_get(self, *a, **k):
        return tuple(self._orders)

    def positions_get(self, *a, **k):
        return tuple(self._positions)

    def history_deals_get(self, *a, **k):
        return tuple(self._deals)

    def positions_total(self):
        return len(self._positions)

    def orders_total(self):
        return len(self._orders)

    # --- write ---
    def order_send(self, request: dict):
        action = request.get("action")
        if action == self.TRADE_ACTION_PENDING:
            return self._do_pending(request)
        if action == self.TRADE_ACTION_DEAL:
            if "position" in request and request.get("position"):
                return self._do_close(request)
            return self._do_market(request)
        if action == self.TRADE_ACTION_REMOVE:
            return self._do_remove(request)
        if action == self.TRADE_ACTION_SLTP:
            return self._do_sltp(request)
        return _ns(retcode=10013, order=0, comment="unknown action")

    def _do_pending(self, r):
        t = self._next_ticket()
        otype = int(r["type"])
        self._orders.append(_ns(
            ticket=t, symbol=self.symbol, type=otype,
            price_open=float(r["price"]), sl=float(r.get("sl", 0.0) or 0.0),
            tp=float(r.get("tp", 0.0) or 0.0),
            volume_current=float(r["volume"]), volume_initial=float(r["volume"]),
            magic=int(r.get("magic", 0)), comment=str(r.get("comment", "")),
            time_setup=self._tick_time, _type_name=self._TYPE_NAME.get(otype, "?"),
            _created_bar=self._bar_idx,
        ))
        return _ns(retcode=self.TRADE_RETCODE_DONE, order=t, deal=0,
                   price=float(r["price"]), comment="done")

    def _do_market(self, r):
        t = self._next_ticket()
        otype = int(r["type"])
        is_buy = otype == self.ORDER_TYPE_BUY
        price = float(r.get("price") or (self._tick_ask if is_buy else self._tick_bid))
        self._positions.append(_ns(
            ticket=t, symbol=self.symbol,
            type=self.POSITION_TYPE_BUY if is_buy else self.POSITION_TYPE_SELL,
            volume=float(r["volume"]), price_open=price,
            sl=float(r.get("sl", 0.0) or 0.0), tp=float(r.get("tp", 0.0) or 0.0),
            magic=int(r.get("magic", 0)), comment=str(r.get("comment", "")),
            time=self._tick_time, price_current=price, profit=0.0,
            _entry_bar=self._bar_idx,
        ))
        return _ns(retcode=self.TRADE_RETCODE_DONE, order=t, deal=t,
                   price=price, comment="done")

    def _do_close(self, r):
        tk = int(r["position"])
        for p in list(self._positions):
            if p.ticket == tk:
                price = float(r.get("price") or
                              (self._tick_bid if p.type == self.POSITION_TYPE_BUY
                               else self._tick_ask))
                self._record_close(p, price, reason=str(r.get("comment", "close")))
                self._positions.remove(p)
                return _ns(retcode=self.TRADE_RETCODE_DONE, order=tk, deal=tk,
                           price=price, comment="done")
        return _ns(retcode=self.TRADE_RETCODE_DONE, order=0, deal=0, comment="no pos")

    def _do_remove(self, r):
        tk = int(r["order"])
        before = len(self._orders)
        self._orders = [o for o in self._orders if o.ticket != tk]
        rc = self.TRADE_RETCODE_DONE if len(self._orders) < before else 10013
        return _ns(retcode=rc, order=tk, comment="removed")

    def _do_sltp(self, r):
        tk = int(r["position"])
        for p in self._positions:
            if p.ticket == tk:
                if "sl" in r:
                    p.sl = float(r["sl"] or 0.0)
                if "tp" in r:
                    p.tp = float(r["tp"] or 0.0)
                return _ns(retcode=self.TRADE_RETCODE_DONE, order=tk, comment="sltp")
        return _ns(retcode=10013, order=0, comment="no pos")

    def _record_close(self, p, close_price: float, *, reason: str):
        dir_ = 1 if p.type == self.POSITION_TYPE_BUY else -1
        pnl = (close_price - p.price_open) * p.volume * self.contract_size * dir_
        wt = _clean_wave_time(p.comment)
        
        is_two_sided = False
        is_promoted = False
        if p.comment and p.comment.startswith("TS2_"):
            is_two_sided = True
            if wt in self.promoted_waves:
                is_promoted = True

        self._closed.append(_ns(
            wave_time=wt, dir=dir_, lot=p.volume, entry_price=p.price_open,
            close_price=close_price, reason=reason, pnl_usd=pnl,
            entry_bar=getattr(p, "_entry_bar", 0), close_bar=self._bar_idx,
            comment=p.comment,
            is_two_sided_mirror=is_two_sided and not is_promoted,
            is_pp=bool(p.comment and (p.comment.startswith("PP_") or p.comment.startswith("PPM_"))),
            is_counter=bool(p.comment and (p.comment.startswith("CNTR_") or p.comment.startswith("ECT_") or p.comment.startswith("ECB_"))),
            is_bos_reentry=bool(p.comment and (p.comment.startswith("BOS_") or p.comment.startswith("RENT_"))),
            is_ext=bool(p.comment and (p.comment.startswith("E23_") or p.comment.startswith("ECT_") or p.comment.startswith("ECB_") or p.comment.startswith("EWP_"))),
            entry_tag=(
                "wave_counter" if p.comment and p.comment.startswith("CNTR_")
                else "ext_0236" if p.comment and p.comment.startswith("E23_")
                else "ext_counter_time" if p.comment and p.comment.startswith("ECT_")
                else "ext_counter_bos" if p.comment and p.comment.startswith("ECB_")
                else "base"
            )
        ))
        self._deals.append(_ns(ticket=p.ticket, profit=pnl, symbol=self.symbol,
                               time=self._tick_time, comment=reason))

    # --- resting SL/TP (engine _check_sl_tp) — REALITA: resting stop na MT5
    # fillne intrabar na SL/TP cene DRIV, nez bot na close baru spusti BOS/TP
    # logiku. Proto se kontroluje PRED live zpracovanim baru (replay), jen pro
    # pozice otevrene na DRIVEJSICH barech (engine: bar_idx <= entry_bar skip).
    def check_resting_sltp(self, bar_idx: int, high: float, low: float):
        still = []
        for p in self._positions:
            if bar_idx <= getattr(p, "_entry_bar", bar_idx):
                still.append(p)
                continue
            has_tp = p.tp and p.tp > 0.0
            is_buy = p.type == self.POSITION_TYPE_BUY
            tp_ok_side = has_tp and (
                (is_buy and p.tp > p.price_open) or (not is_buy and p.tp < p.price_open)
            )
            if is_buy:
                sl_hit = (p.sl and p.sl > 0.0) and low <= p.sl
                tp_hit = bool(tp_ok_side and high >= p.tp)
            else:
                sl_hit = (p.sl and p.sl > 0.0) and high >= p.sl
                tp_hit = bool(tp_ok_side and low <= p.tp)
            if sl_hit or tp_hit:
                reason = "SL" if sl_hit else "TP"
                price = p.sl if sl_hit else p.tp
                self._record_close(p, float(price), reason=reason)
            else:
                still.append(p)
        self._positions = still

    # --- pending trigger -> fill (engine _trigger_pending). Bezi PO live
    # zpracovani baru (entries placnu pendingy, pak se triggeruji), nove fill
    # se na svem baru SL/TP nekontroluje (engine bar_idx <= entry_bar skip).
    def fill_pendings(self, bar_idx: int, high: float, low: float,
                      open_: float, close: float, *, cfg=None):
        remaining = []
        for o in self._orders:
            triggered = False
            ep = o.price_open
            actual = ep
            if o.type == self.ORDER_TYPE_BUY_STOP:
                if high >= ep:
                    actual = max(ep, open_); triggered = True
            elif o.type == self.ORDER_TYPE_SELL_STOP:
                if low <= ep:
                    actual = min(ep, open_); triggered = True
            elif o.type == self.ORDER_TYPE_BUY_LIMIT:
                if low <= ep:
                    actual = min(ep, open_); triggered = True
            elif o.type == self.ORDER_TYPE_SELL_LIMIT:
                if high >= ep:
                    actual = max(ep, open_); triggered = True
            if not triggered:
                remaining.append(o)
                continue
            is_buy = o.type in (self.ORDER_TYPE_BUY_LIMIT, self.ORDER_TYPE_BUY_STOP)
            volume = o.volume_current
            if cfg is not None:
                c = str(getattr(o, "comment", "") or "")
                if c.startswith("TS2_"):
                    from strategy.two_sided import live_study_ts2_use_wave_primary_sizing
                    if live_study_ts2_use_wave_primary_sizing(cfg):
                        from core.risk import calc_lot
                        volume = calc_lot(actual, float(o.sl), cfg)
            # placement lot/sl/tp (= realita live MT5; E2E prepocet lotu pri fillu = engine)
            self._positions.append(_ns(
                ticket=o.ticket, symbol=self.symbol,
                type=self.POSITION_TYPE_BUY if is_buy else self.POSITION_TYPE_SELL,
                volume=volume, price_open=actual,
                sl=o.sl, tp=o.tp, magic=o.magic, comment=o.comment,
                time=self._tick_time, price_current=actual, profit=0.0,
                _entry_bar=bar_idx,
            ))
        self._orders = remaining


# ---------------------------------------------------------------------------
# install fake PRED importem projektovych modulu
# ---------------------------------------------------------------------------
def install_fake(symbol: str, contract_size: float) -> FakeMt5:
    fake = FakeMt5(symbol, contract_size)
    sys.modules["MetaTrader5"] = fake
    return fake


# ---------------------------------------------------------------------------
# DDi/PnL stejnou cestou jako backtest report
# ---------------------------------------------------------------------------
def pnl_ddi_from_closed(
    closed: list,
    *,
    bot_name: str,
    date_from: str | None = None,
    date_to: str | None = None,
    combo: dict | None = None,
) -> dict:
    import pandas as pd
    from backtest.grid.study_mode import (
        apply_wave_isolation_report_stats,
        filter_trades_df_for_grid_stats,
    )
    from backtest.metrics.robustness import compute_robustness_metrics
    from backtest.stats import compute_stats

    d_from = date_from or DATE_FROM
    d_to = date_to or DATE_TO
    combo_cfg = dict(combo or {})
    combo_cfg.setdefault("wave_isolation_study", True)
    combo_cfg.setdefault("wave_positions_only", True)
    combo_cfg["date_from"] = d_from
    combo_cfg["date_to"] = d_to

    rows = []
    for t in closed:
        rows.append({
            "wave_time": str(t.wave_time), "dir": int(t.dir), "lot": float(t.lot),
            "entry_price": float(t.entry_price), "close_price": float(t.close_price),
            "close_reason": str(t.reason), "pnl_usd": float(t.pnl_usd),
            "close_time": _wt_to_ts(t.wave_time, fallback=d_from), "position_kind": "WAVE",
            "is_ext": bool(getattr(t, "is_ext", False)),
            "is_counter": bool(getattr(t, "is_counter", False)),
            "is_pp": bool(getattr(t, "is_pp", False)),
            "is_bos_reentry": bool(getattr(t, "is_bos_reentry", False)),
            "is_two_sided_mirror": bool(getattr(t, "is_two_sided_mirror", False)),
            "entry_tag": str(getattr(t, "entry_tag", "base")),
        })
    df = pd.DataFrame(rows)
    wdf = filter_trades_df_for_grid_stats(df, combo_cfg) if not df.empty else df
    stats = compute_stats(wdf, date_from=d_from, date_to=d_to)
    stats = apply_wave_isolation_report_stats(stats, combo_cfg)
    stats.update(compute_robustness_metrics(
        wdf, max_dd_pct_vs_peak=stats.get("max_drawdown_pct_vs_peak"),
        max_dd_pct_vs_initial=stats.get("max_drawdown_pct"), bot_name=bot_name,
    ))
    stats["config"] = combo_cfg
    return stats


def _wt_to_ts(wt: str, *, fallback: str = DATE_FROM):
    import pandas as pd
    try:
        return pd.Timestamp(
            f"{wt[0:4]}-{wt[4:6]}-{wt[6:8]} {wt[8:10]}:{wt[10:12]}"
        )
    except Exception:
        return pd.Timestamp(fallback)


def filter_e2e_wave_closed(
    closed: list,
    cfg,
    *,
    promoted_waves: set[str] | None = None,
) -> list:
    """WAVE slice E2E vysledku — parita s live deploy config (guard + promoted TS2)."""
    from backtest.stats import classify_position_kind

    promoted = promoted_waves or set()
    out = []
    for t in closed:
        wt = _clean_wave_time(getattr(t, "comment", getattr(t, "wave_time", "")))
        is_promoted_ts2 = wt in promoted
        kind = classify_position_kind(
            is_pp=bool(getattr(t, "is_pp", 0)),
            is_counter=bool(getattr(t, "is_counter", 0)),
            is_bos_reentry=bool(getattr(t, "is_bos_reentry", 0)),
            is_two_sided_mirror=(
                bool(getattr(t, "is_two_sided_mirror", 0)) and not is_promoted_ts2
            ),
            is_ext=bool(getattr(t, "is_ext", 0)),
            entry_tag=str(getattr(t, "entry_tag", "base")),
        )
        if kind == "WAVE":
            out.append(t)
        elif (
            kind == "WAVE_TWO_SIDED"
            and bool(getattr(cfg, "live_study_two_sided_mirror_orders", False))
        ):
            out.append(t)
    return out


def main() -> None:
    from config.bot_config import LIVE_BOT_CONFIG
    from config.position_modes import resolve_grid_engine_config

    engine_cfg = resolve_grid_engine_config(LIVE_BOT_CONFIG)
    fake = install_fake(engine_cfg.symbol, engine_cfg.contract_size)

    import pandas as pd
    from backtest.data_loader import filter_by_date_range, load_csv
    from backtest.engine import BacktestEngine
    from backtest.stats import classify_position_kind
    from runtime.live_wave_isolation import resolve_live_execution_config

    df = filter_by_date_range(load_csv(str(CSV)), DATE_FROM, DATE_TO).reset_index(drop=True)
    live_cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)

    print("=" * 72)
    print("E2E LIVE (fake broker) vs BACKTEST |", DATE_FROM, "..", DATE_TO)
    print("=" * 72)
    print(f"baru: {len(df)}  symbol={engine_cfg.symbol}  contract={engine_cfg.contract_size}")

    # --- BACKTEST reference (WAVE only — engine promoted mirrors = WAVE) ---
    def bt_wave_pnl(closed):
        return [t for t in closed if classify_position_kind(
            is_pp=bool(getattr(t, "is_pp", 0)), is_counter=bool(getattr(t, "is_counter", 0)),
            is_bos_reentry=bool(getattr(t, "is_bos_reentry", 0)),
            is_two_sided_mirror=bool(getattr(t, "is_two_sided_mirror", 0)),
            is_ext=bool(getattr(t, "is_ext", 0)),
            entry_tag=str(getattr(t, "entry_tag", "base"))) == "WAVE"]

    bt_wave = bt_wave_pnl(BacktestEngine(engine_cfg).run(df, retain_wave_snapshot=False))
    bt = pnl_ddi_from_closed(
        [_ns(wave_time=t.wave_time, dir=t.dir, lot=t.lot, entry_price=t.entry_price,
             close_price=t.close_price, reason=t.close_reason, pnl_usd=t.pnl_usd)
         for t in bt_wave], bot_name=engine_cfg.bot_name)
    print(f"\nBACKTEST WAVE: {len(bt_wave)} obchodu / {round(float(bt.get('net_pnl_usd',0)),2)} USD")

    # --- LIVE E2E pres fake broker ---
    from runtime.live_wave_stats import position_kind_from_mt5_comment
    
    live_closed_all = run_e2e(df, live_cfg, fake)
    live_closed = filter_e2e_wave_closed(
        live_closed_all, live_cfg, promoted_waves=fake.promoted_waves,
    )
    ts2_count = sum(
        1 for t in live_closed_all
        if str(getattr(t, "comment", "")).startswith("TS2_")
    )
    lv = pnl_ddi_from_closed(live_closed, bot_name=engine_cfg.bot_name)
    print(f"LIVE E2E WAVE: {len(live_closed)} obchodu / {round(float(lv.get('net_pnl_usd',0)),2)} USD")
    print(f"  TS2_ mirror count: {ts2_count}  promoted: {len(fake.promoted_waves)}")

    _report(bt, lv, len(bt_wave), len(live_closed))
    _diagnostics(bt_wave, live_closed)
    _dump_trace()


def _dump_trace() -> None:
    import os
    from runtime.missed_bar_replay import _TRACE_LOG, _TRACE_WAVES
    if not _TRACE_WAVES:
        return
    print("\n" + "=" * 72)
    print("TRACE rozhodovaci cesty (E2E_TRACE_WAVES)")
    print("=" * 72)
    by_wt: dict[str, list] = {}
    for bar_idx, wt, branch, kw in _TRACE_LOG:
        by_wt.setdefault(wt, []).append((bar_idx, branch, kw))
    for wt in sorted(_TRACE_WAVES):
        entries = by_wt.get(wt, [])
        if not entries:
            print(f"\n  {wt}: (zadny trace - vlna se v loopu vubec neobjevila)")
            continue
        print(f"\n  {wt}:  ({len(entries)} zaznamu)")
        # kolaps po sobe jdoucich shodnych vetvi: ukaz prvni vyskyt kazde zmeny
        prev_branch = None
        for bar_idx, branch, kw in entries:
            if branch == prev_branch:
                continue
            prev_branch = branch
            kws = " ".join(f"{k}={v}" for k, v in kw.items())
            print(f"    bar {bar_idx:>5}  {branch:<32}{kws}")


def _diagnostics(bt_wave, live_closed) -> None:
    from collections import Counter
    print("\n" + "=" * 72)
    print("DIAGNOSTIKA EXIT CESTY")
    print("=" * 72)
    bt_reasons = Counter(str(getattr(t, "close_reason", "?")) for t in bt_wave)
    lv_reasons = Counter(str(getattr(t, "reason", "?")) for t in live_closed)
    print("  BACKTEST close reasons:", dict(bt_reasons))
    print("  LIVE E2E close reasons:", dict(lv_reasons))

    bt_by_wt: dict[str, list] = {}
    for t in bt_wave:
        bt_by_wt.setdefault(str(t.wave_time), []).append(t)
    lv_by_wt: dict[str, list] = {}
    for t in live_closed:
        lv_by_wt.setdefault(str(t.wave_time), []).append(t)

    common = sorted(set(bt_by_wt) & set(lv_by_wt))
    bt_only = sorted(set(bt_by_wt) - set(lv_by_wt))
    lv_only = sorted(set(lv_by_wt) - set(bt_by_wt))
    common_bt = sum(t.pnl_usd for wt in common for t in bt_by_wt[wt])
    common_lv = sum(t.pnl_usd for wt in common for t in lv_by_wt[wt])
    bt_only_pnl = sum(t.pnl_usd for wt in bt_only for t in bt_by_wt[wt])
    lv_only_pnl = sum(t.pnl_usd for wt in lv_only for t in lv_by_wt[wt])
    print("\n  PnL ROZKLAD:")
    print(f"    common wt ({len(common)}): BT {common_bt:>9.0f}  LV {common_lv:>9.0f}  delta {common_lv-common_bt:>9.0f}")
    print(f"    BT-only wt ({len(bt_only)}): {bt_only_pnl:>9.0f}   {bt_only}")
    print(f"    LV-only wt ({len(lv_only)}): {lv_only_pnl:>9.0f}   {lv_only}")

    def _bt_kind(t):
        if bool(getattr(t, "is_ext", 0)):
            return "EXT" if str(getattr(t, "entry_tag", "")) == "base" else "EXTblk"
        if bool(getattr(t, "is_bos_reentry", 0)):
            return "BOSretro"
        if bool(getattr(t, "is_counter", 0)):
            return "COUNTER"
        return "WAVE"
    from collections import Counter
    bt_only_kinds = Counter(_bt_kind(bt_by_wt[wt][0]) for wt in bt_only)
    lv_only_kinds = Counter(
        ("EXT" if str(getattr(lv_by_wt[wt][0], "comment", "")).startswith("EWP_")
         else "WAVE/other")
        for wt in lv_only
    )
    print(f"    BT-only KIND: {dict(bt_only_kinds)}")
    print(f"    LV-only KIND: {dict(lv_only_kinds)}")
    print(f"\n  spolecne wave_time: {len(common)}")
    # entry parita: kolik ma shodny entry_bar a entry_price (do 1 pip)
    entry_match = 0
    entry_total = 0
    for wt in common:
        br = bt_by_wt[wt][0]
        lr = lv_by_wt[wt][0]
        b_eb = int(getattr(br, "close_bar", 0)) - int(getattr(br, "bars_held", 0))
        l_eb = int(getattr(lr, "entry_bar", 0))
        entry_total += 1
        if b_eb == l_eb and abs(float(br.entry_price) - float(lr.entry_price)) <= 1e-5:
            entry_match += 1
    print(f"  ENTRY parita (stejny entry_bar + EP<=1pip): {entry_match}/{entry_total}")

    diffs = []
    for wt in common:
        bp = sum(t.pnl_usd for t in bt_by_wt[wt])
        lp = sum(t.pnl_usd for t in lv_by_wt[wt])
        if abs(bp - lp) > 1.0:
            br = bt_by_wt[wt][0]
            lr = lv_by_wt[wt][0]
            b_eb = int(getattr(br, "close_bar", 0)) - int(getattr(br, "bars_held", 0))
            diffs.append((abs(bp - lp), wt, bp, lp,
                          str(getattr(br, "close_reason", "?"))[:10],
                          str(getattr(lr, "reason", "?"))[:14],
                          b_eb, int(getattr(br, "close_bar", 0)),
                          int(getattr(lr, "entry_bar", 0)), int(getattr(lr, "close_bar", 0)),
                          float(br.entry_price), float(lr.entry_price)))
    diffs.sort(reverse=True)
    print(f"  z toho s rozdilem PnL > 1 USD: {len(diffs)}")
    print(f"  {'wave_time':<14}{'BTpnl':>8}{'LVpnl':>8} {'BTrsn':<10}{'LVrsn':<14} {'BTeb':>5}{'BTcb':>5}{'LVeb':>5}{'LVcb':>5} {'BTep':>9}{'LVep':>9}")
    for _, wt, bp, lp, brsn, lrsn, beb, bcb, leb, lcb, bep, lep in diffs[:25]:
        print(f"  {wt:<14}{bp:>8.0f}{lp:>8.0f} {brsn:<10}{lrsn:<14} {beb:>5}{bcb:>5}{leb:>5}{lcb:>5} {bep:>9.5f}{lep:>9.5f}")


def run_e2e(df, cfg, fake: FakeMt5) -> list:
    """Prozene replay_missed_closed_bar bar-po-baru proti fake brokeru."""
    from strategy.wave_detection import detect_waves
    from strategy.wave_detection_pine import compute_wave_birth_bars_pine
    from strategy.trend_bos import (
        _detect_close_bos_timeline_flips, compute_bos_wave_flip_map,
        compute_trend_states_per_bar, compute_trend_states_per_wave,
        reconcile_bos_flip_map_with_wave_sequence,
    )
    from strategy.wave_sequence import sync_wave_sequence_state
    from strategy.ext_range import ext_range_enabled, reapply_ext_range_tags
    from strategy.two_sided import two_sided_enabled
    from runtime.ext_live import ExtLiveRuntime
    from runtime.wf_live import WfLiveRuntime
    from runtime.missed_bar_replay import MissedBarReplayState, replay_missed_closed_bar
    from infra.orders import get_active_counter_wave_times
    from config.enums import PendingCancelMode
    import runtime.live_loop as ll
    from core.logging_utils import log_event
    import pandas as pd

    waves = detect_waves(df, cfg)
    if not waves:
        return []
    wave_birth = compute_wave_birth_bars_pine(df, cfg)

    # ENGINE PARITA (per-bar trend): engine pocita trend_states_per_bar JEDNOU nad
    # PRVOTNI detect mnozinou PRED WF merge (engine.py _recompute_bos_state @596) a
    # uz ji po WF NEaktualizuje. Live driv pocital bar trend nad POST-WF waves →
    # 285 baru jiny smer → fill-bar re-check (entry_allowed_at_fill_bar) blokoval
    # wf_continued vlny (napr. 202603051030, engine ji pousti jako first_in_trend).
    # Snapshot PRED WF + ext_range tagy (jako engine @593) = shodny trend zdroj.
    _pre_wf_waves = [dict(w) for w in waves]
    if ext_range_enabled(cfg):
        reapply_ext_range_tags(_pre_wf_waves, cfg, df=df, wave_birth=dict(wave_birth))
    pre_wf_bar_trend_states = compute_trend_states_per_bar(df, _pre_wf_waves, cfg)

    wf_runtime = WfLiveRuntime()
    wf_runtime.process(df, cfg, waves, wave_birth_by_time=wave_birth)
    wf_queue = wf_runtime.pop_activation_results()

    if cfg.trend_filter_enabled or two_sided_enabled(cfg):
        trend_states_per_wave = compute_trend_states_per_wave(df, waves, cfg)
    else:
        trend_states_per_wave = {}
    seq_info, protected_waves = sync_wave_sequence_state(df, waves, cfg)
    if ext_range_enabled(cfg):
        reapply_ext_range_tags(waves, cfg, df=df, wave_birth=wave_birth)
        seq_info, protected_waves = sync_wave_sequence_state(df, waves, cfg)

    bos_flip_map: dict[int, str] = {}
    bos_wave_times: set[str] = set()
    if cfg.trend_filter_enabled:
        flips = _detect_close_bos_timeline_flips(df, waves, cfg, wave_birth_bars=wave_birth)
        bos_flip_map = reconcile_bos_flip_map_with_wave_sequence(
            compute_bos_wave_flip_map(df, waves, cfg, wave_birth_bars=wave_birth),
            flips, waves, seq_info, wave_birth,
        )
        bos_wave_times = set(bos_flip_map.values())

    ext_runtime = ExtLiveRuntime()
    ext_runtime.sync_from_mt5(cfg)
    ext_runtime.refresh_simulation(df, cfg, seq_info=seq_info,
                                   protected_waves=protected_waves, waves=waves)
    ext_runtime.run_ext1_rrr_better_exit(cfg, df)
    ext1_per_bar = ext_runtime._ext1_protection_per_bar

    if two_sided_enabled(cfg):
        ll._live_two_sided_tracker.clear_all()

    # TEST hypotezy: pre-marking TP-vln dle birth misto draw_right preskakuje
    # eventy u vln s birth<draw_right. Pri kontinualnim behu od baru 1 staci
    # pre-marking VYPNOUT (eventy vystreli prirozene na draw_right).
    import os
    if os.environ.get("E2E_NO_PREMARK") == "1":
        import runtime.wave_target_n_bar as _wtb
        _orig_sync = _wtb.sync_wave_target_n_live_state

        def _patched_sync(*a, **k):
            r = _orig_sync(*a, **k)
            r.processed_tp_wave_times = set()
            return r

        _wtb.sync_wave_target_n_live_state = _patched_sync

    bar_trend_states = pre_wf_bar_trend_states
    signal_digits = 5
    sent_signals: set[str] = set()
    failed_signals: dict[str, dict] = {}
    state = MissedBarReplayState(
        last_known_trend_dir=None, prev_cycle_last_bar_time=None,
        processed_tp_wave_times=set(), forming_tp_watch=None,
        ext_sl_anchor=None, retro_bos_attempted=set(),
        promoted_two_sided_wave_times=set(),
    )
    pcm = (PendingCancelMode(cfg.pending_cancel_mode)
           if isinstance(cfg.pending_cancel_mode, str) else cfg.pending_cancel_mode)

    for bar_idx in range(1, len(df)):
        if bar_idx % 1000 == 0:
            print(f"Processing bar {bar_idx} / {len(df)}")
        row = df.iloc[bar_idx]
        bt = pd.Timestamp(row["time"]).to_pydatetime()
        fake.set_bar(bar_idx, bt, float(row["close"]))
        
        from runtime.missed_bar_replay import replay_two_sided_tracker_live_parity
        if not hasattr(ll, "_e2e_waves_by_bar"):
            from strategy.two_sided import build_two_sided_wave_bar_maps
            ll._e2e_waves_by_bar, ll._e2e_waves_by_end_bar = build_two_sided_wave_bar_maps(waves, wave_birth)
        replay_two_sided_tracker_live_parity(
            tracker=ll._live_two_sided_tracker,
            df=df,
            bar_idx=bar_idx,
            waves_by_end_bar=ll._e2e_waves_by_end_bar,
            waves_by_birth_bar=ll._e2e_waves_by_bar,
            cfg=cfg,
            trend_states_per_wave=trend_states_per_wave,
        )

        # 0) resting SL/TP (broker) — intrabar fill DRIV nez bot zpracuje bar
        #    (realita: stop order na MT5 fillne na SL/TP cene, ne na bar_close).
        fake.check_resting_sltp(bar_idx, float(row["high"]), float(row["low"]))
        # 1) live processing baru: BOS/TP closy, cancel, entries (place pending)
        state = replay_missed_closed_bar(
            cfg=cfg, df=df, waves=waves, bar_idx=bar_idx, state=state,
            bar_trend_states=bar_trend_states, seq_info=seq_info,
            protected_waves=protected_waves, bos_flip_map=bos_flip_map,
            bos_wave_times=bos_wave_times, trend_states_per_wave=trend_states_per_wave,
            ext1_per_bar=ext1_per_bar, ext_runtime=ext_runtime,
            wf_activations=wf_queue, sent_signals=sent_signals,
            failed_signals=failed_signals, signal_digits=signal_digits,
            entries_allowed=True, wave_birth_by_time=wave_birth,
            active_counter_wave_times=get_active_counter_wave_times(cfg), pcm=pcm,
            place_live_bos_reentry=ll._place_live_bos_reentry,
            place_live_counter_from_g_extension=ll._place_live_counter_from_g_extension,
            g_extension_hit_closed_positions=ll._g_extension_hit_closed_positions,
            place_live_counter_position=ll._place_live_counter_position,
            log_event_fn=log_event, two_sided_tracker=ll._live_two_sided_tracker,
            get_open_comments=lambda: [p.comment for p in fake._positions],
        )
        if hasattr(state, "promoted_two_sided_wave_times"):
            fake.promoted_waves = state.promoted_two_sided_wave_times
        ll._live_missed_bar_state = state
        fake.promoted_waves = state.promoted_two_sided_wave_times
        # 2) trigger pendingu -> fill (po entries; engine trigger po exitech)
        fake.fill_pendings(
            bar_idx, float(row["high"]), float(row["low"]),
            float(row["open"]), float(row["close"]),
            cfg=cfg,
        )

    return fake._closed


def _report(bt: dict, lv: dict, bt_n: int, lv_n: int) -> None:
    def f(d, k):
        try:
            return float(d.get(k, 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0
    bd = bt.get("ddi_profile", {}) or {}
    ld = lv.get("ddi_profile", {}) or {}
    rows = [
        ("WAVE obchodu", bt_n, lv_n, ""),
        ("net_pnl_usd", round(f(bt, "net_pnl_usd"), 2), round(f(lv, "net_pnl_usd"), 2), "USD"),
        ("win_rate_pct", round(f(bt, "win_rate_pct"), 2), round(f(lv, "win_rate_pct"), 2), "%"),
        ("max_drawdown_pct", round(f(bt, "max_drawdown_pct"), 2), round(f(lv, "max_drawdown_pct"), 2), "%"),
        ("max_ddi_pct", round(f(bd, "max_ddi_pct"), 2), round(f(ld, "max_ddi_pct"), 2), "%"),
        ("p90_ddi_pct", round(f(bd, "p90_ddi_pct"), 2), round(f(ld, "p90_ddi_pct"), 2), "%"),
        ("dnu_poruseni_5pct", int(f(bd, "dnu_poruseni_5pct")), int(f(ld, "dnu_poruseni_5pct")), "dni"),
    ]
    print("\n" + "=" * 72)
    print(f"  {'metrika':<20} {'BACKTEST':>14} {'LIVE E2E':>14}  delta")
    print("  " + "-" * 64)
    for name, a, b, unit in rows:
        delta = ""
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            d = float(b) - float(a)
            delta = f"{d:+.2f}{unit}" if unit in ("%", "USD") else f"{d:+.0f}{unit}"
        print(f"  {name:<20} {str(a):>14} {str(b):>14}  {delta}")


if __name__ == "__main__":
    main()
