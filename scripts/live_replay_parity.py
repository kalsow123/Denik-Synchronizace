"""
Live-replay parity harness — porovna WAVE rozhodnuti LIVE cesty vs BacktestEngine.

Cil: overit, ze kdyby live bot bezel pres dane obdobi, polozi STEJNE WAVE ordery
(wave_time, dir, EP, SL, TP) jako backtest. Live cesta dela jen ROZHODNUTI
(send_order = pending), fill/SL/TP resi broker — proto porovnavame mnozinu
WAVE pending orderu vs WAVE obchody z backtestu.

BEZPECNOST: order vrstva je tvrde zamockovana — mt5.order_send je nahrazen
recorderem, takze NA UCET (FTMO real) NEJDE ZADNY order. MT5 se pouziva jen
read-only pro symbol_info (point/digits).

Spusteni: .venv\\Scripts\\python.exe scripts/live_replay_parity.py
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import MetaTrader5 as mt5

DATE_FROM = "2025-11-10"
DATE_TO = "2026-05-09"
CSV = ROOT / "data" / "EURUSD_M30.csv"

# ── stav pro mock tick (aktualni bar close) + recorder orderu ──
_CURRENT_CLOSE = {"v": 1.10}
_CURRENT_BAR_IDX = {"v": 0}
_CURRENT_BAR_TIME: dict[str, Any] = {"v": None}
_SENT_ORDERS: list[dict] = []
_RECORDED_ORDERS: list["RecordedOrder"] = []
_TICKET = {"n": 1}
# wave_time -> list of (send_order_returned: bool, order_skutecne_polozen: bool)
_SEND_ATTEMPTS: dict[str, list] = {}


def _fake_result(request: dict):
    _TICKET["n"] += 1
    return SimpleNamespace(
        retcode=mt5.TRADE_RETCODE_DONE,
        order=_TICKET["n"],
        deal=_TICKET["n"],
        price=request.get("price", 0.0),
        volume=request.get("volume", 0.0),
        comment="mock_done",
        request=SimpleNamespace(**request) if isinstance(request, dict) else request,
    )


def _mock_order_send(request):
    """Zaznamena request misto realneho odeslani na MT5."""
    if isinstance(request, dict):
        _SENT_ORDERS.append(dict(request))
        action = request.get("action")
        if action != getattr(mt5, "TRADE_ACTION_REMOVE", None):
            ro = _recorded_order_from_request(request)
            if ro is not None:
                _RECORDED_ORDERS.append(ro)
        return _fake_result(request)
    _SENT_ORDERS.append({"raw": str(request)})
    return _fake_result({})


def _mock_tick(symbol):
    c = float(_CURRENT_CLOSE["v"])
    return SimpleNamespace(ask=c, bid=c, last=c, time=0, volume=0)


def _empty(*a, **k):
    return []


def install_mocks() -> None:
    """Tvrde zamockuje order vrstvu — NIC nejde na realny ucet."""
    mt5.order_send = _mock_order_send
    mt5.symbol_info_tick = _mock_tick
    mt5.orders_get = _empty
    mt5.positions_get = _empty
    if hasattr(mt5, "history_deals_get"):
        mt5.history_deals_get = _empty
    if hasattr(mt5, "history_orders_get"):
        mt5.history_orders_get = _empty


@dataclass
class WaveOrder:
    wave_time: str
    dir: str
    ep: float
    sl: float
    tp: float | None
    kind: str  # PENDING / MARKET


@dataclass
class RecordedOrder:
    """Jeden WAVE order zaznamenany behem live replay (s barem vzniku)."""
    bar_idx: int
    bar_time: Any
    wave_time: str
    dir: int  # +1 / -1
    ep: float
    sl: float
    tp: float | None
    lot: float
    kind: str  # PENDING / MARKET
    order_type: str  # BUY_LIMIT, BUY_MARKET, ...


def _mt5_order_type_name(otype: int, *, is_buy: bool) -> tuple[str, str]:
    """Vrati (engine order_type, kind PENDING/MARKET)."""
    buy_pending = getattr(mt5, "ORDER_TYPE_BUY_LIMIT", 2)
    sell_pending = getattr(mt5, "ORDER_TYPE_SELL_LIMIT", 3)
    buy_mkt = getattr(mt5, "ORDER_TYPE_BUY", 0)
    sell_mkt = getattr(mt5, "ORDER_TYPE_SELL", 1)
    buy_stop = getattr(mt5, "ORDER_TYPE_BUY_STOP", 4)
    sell_stop = getattr(mt5, "ORDER_TYPE_SELL_STOP", 5)
    if otype == buy_pending:
        return "BUY_LIMIT", "PENDING"
    if otype == sell_pending:
        return "SELL_LIMIT", "PENDING"
    if otype == buy_stop:
        return "BUY_STOP", "PENDING"
    if otype == sell_stop:
        return "SELL_STOP", "PENDING"
    if otype == buy_mkt:
        return "BUY_MARKET", "MARKET"
    if otype == sell_mkt:
        return "SELL_MARKET", "MARKET"
    side = "BUY" if is_buy else "SELL"
    return f"{side}_LIMIT", "PENDING"


def _recorded_order_from_request(request: dict) -> RecordedOrder | None:
    c = str(request.get("comment", "") or "")
    if not _is_wave_comment(c):
        return None
    wt = c[1:]
    otype = int(request.get("type", -1))
    buy_pending = getattr(mt5, "ORDER_TYPE_BUY_LIMIT", 2)
    buy_mkt = getattr(mt5, "ORDER_TYPE_BUY", 0)
    buy_stop = getattr(mt5, "ORDER_TYPE_BUY_STOP", 4)
    is_buy = otype in (buy_pending, buy_mkt, buy_stop)
    order_type, kind = _mt5_order_type_name(otype, is_buy=is_buy)
    tp_raw = request.get("tp")
    tp = None if not tp_raw or float(tp_raw) == 0.0 else float(tp_raw)
    return RecordedOrder(
        bar_idx=int(_CURRENT_BAR_IDX["v"]),
        bar_time=_CURRENT_BAR_TIME["v"],
        wave_time=wt,
        dir=1 if is_buy else -1,
        ep=float(request.get("price", 0.0)),
        sl=float(request.get("sl", 0.0)),
        tp=tp,
        lot=float(request.get("volume", 0.0)),
        kind=kind,
        order_type=order_type,
    )


def _is_wave_comment(c: str) -> bool:
    c = str(c or "")
    return c.startswith("W") and len(c) == 13 and c[1:].isdigit()


def collect_wave_orders() -> dict[str, WaveOrder]:
    """Z recorderu vytahne jen klasicke WAVE pendingy (comment W{wave_time})."""
    out: dict[str, WaveOrder] = {}
    buy_pending = getattr(mt5, "ORDER_TYPE_BUY_LIMIT", 2)
    sell_pending = getattr(mt5, "ORDER_TYPE_SELL_LIMIT", 3)
    buy_mkt = getattr(mt5, "ORDER_TYPE_BUY", 0)
    sell_mkt = getattr(mt5, "ORDER_TYPE_SELL", 1)
    buy_stop = getattr(mt5, "ORDER_TYPE_BUY_STOP", 4)
    sell_stop = getattr(mt5, "ORDER_TYPE_SELL_STOP", 5)
    for r in _SENT_ORDERS:
        c = str(r.get("comment", ""))
        if not _is_wave_comment(c):
            continue
        wt = c[1:]
        otype = r.get("type")
        is_buy = otype in (buy_pending, buy_mkt, buy_stop)
        kind = "PENDING" if otype in (buy_pending, sell_pending, buy_stop, sell_stop) else "MARKET"
        out[wt] = WaveOrder(
            wave_time=wt,
            dir="BUY" if is_buy else "SELL",
            ep=float(r.get("price", 0.0)),
            sl=float(r.get("sl", 0.0)),
            tp=(None if not r.get("tp") else float(r.get("tp"))),
            kind=kind,
        )
    return out


def run_live_replay(df, cfg) -> tuple[list[RecordedOrder], dict]:
    """Prejede CSV bar po baru pres realnou replay_missed_closed_bar (live cesta).

    Vraci (recorded_orders, ctx) — ctx drzi presny kontext (waves, wave_birth,
    trend_states_per_wave, bos_flip_map, bos_wave_times), aby slo klasifikovat,
    PROC live nekterou vlnu (ne)polozil — stejnymi funkcemi jako live loop.
    """
    import pandas as pd

    from strategy.wave_detection import detect_waves
    from strategy.wave_detection_pine import compute_wave_birth_bars_pine
    from strategy.trend_bos import (
        _detect_close_bos_timeline_flips,
        compute_bos_wave_flip_map,
        compute_trend_states_per_bar,
        compute_trend_states_per_wave,
        reconcile_bos_flip_map_with_wave_sequence,
    )
    from strategy.wave_sequence import sync_wave_sequence_state
    from strategy.ext_range import ext_range_enabled, reapply_ext_range_tags
    from strategy.two_sided import two_sided_enabled
    from runtime.ext_live import ExtLiveRuntime
    from runtime.wf_live import WfLiveRuntime
    from runtime.missed_bar_replay import MissedBarReplayState, replay_missed_closed_bar
    from infra.orders import get_active_counter_wave_times
    import runtime.live_loop as ll
    from core.logging_utils import log_event

    _SENT_ORDERS.clear()
    _RECORDED_ORDERS.clear()
    _SEND_ATTEMPTS.clear()
    _TICKET["n"] = 1

    # Instrumentace: zachyt KAZDE volani send_order ve wave-loopu replay cesty,
    # at vime, zda se vlna k send_order vubec dostala a co vratil (guard / cena
    # za SL / abort / fallback off). Patchujeme referenci v namespace replay modulu.
    import runtime.missed_bar_replay as _mbr
    _real_send = _mbr.send_order

    def _wrap_send(signal, cfg_, *a, **kw):
        wt = str(signal.get("wave_time", ""))
        n0 = len(_SENT_ORDERS)
        r = _real_send(signal, cfg_, *a, **kw)
        _SEND_ATTEMPTS.setdefault(wt, []).append((bool(r), len(_SENT_ORDERS) > n0))
        return r

    _mbr.send_order = _wrap_send

    waves = detect_waves(df, cfg)
    if not waves:
        _mbr.send_order = _real_send
        return [], {}

    wave_birth = compute_wave_birth_bars_pine(df, cfg)

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
    if cfg.trend_filter_enabled:
        flips = _detect_close_bos_timeline_flips(df, waves, cfg, wave_birth_bars=wave_birth)
        bos_flip_map = reconcile_bos_flip_map_with_wave_sequence(
            compute_bos_wave_flip_map(df, waves, cfg, wave_birth_bars=wave_birth),
            flips,
            waves,
            seq_info,
            wave_birth,
        )
        bos_wave_times = set(bos_flip_map.values())
    else:
        bos_wave_times = set()

    ext_runtime = ExtLiveRuntime()
    ext_runtime.sync_from_mt5(cfg)
    ext_runtime.refresh_simulation(
        df, cfg, seq_info=seq_info, protected_waves=protected_waves, waves=waves,
    )
    ext_runtime.run_ext1_rrr_better_exit(cfg, df)
    ext1_per_bar = ext_runtime._ext1_protection_per_bar

    # Tracker se buduje bar-po-baru uvnitr replay_missed_closed_bar (parita s
    # kontinualni live smyckou) — proto ho jen vycistime, NEpredplnujeme.
    if two_sided_enabled(cfg):
        ll._live_two_sided_tracker.clear_all()

    bar_trend_states = compute_trend_states_per_bar(df, waves, cfg)

    si = mt5.symbol_info(cfg.symbol)
    signal_digits = int(getattr(si, "digits", 5)) if si else 5

    sent_signals: set[str] = set()
    failed_signals: dict[str, dict] = {}
    state = MissedBarReplayState(
        last_known_trend_dir=None,
        prev_cycle_last_bar_time=None,
        processed_tp_wave_times=set(),
        forming_tp_watch=None,
        ext_sl_anchor=None,
        retro_bos_attempted=set(),
    )

    errors = 0
    for bar_idx in range(1, len(df)):
        _CURRENT_BAR_IDX["v"] = bar_idx
        _CURRENT_BAR_TIME["v"] = pd.Timestamp(df["time"].iloc[bar_idx]).to_pydatetime()
        _CURRENT_CLOSE["v"] = float(df.iloc[bar_idx]["close"])
        try:
            state = replay_missed_closed_bar(
                cfg=cfg,
                df=df,
                waves=waves,
                bar_idx=bar_idx,
                state=state,
                bar_trend_states=bar_trend_states,
                seq_info=seq_info,
                protected_waves=protected_waves,
                bos_flip_map=bos_flip_map,
                bos_wave_times=bos_wave_times,
                trend_states_per_wave=trend_states_per_wave,
                ext1_per_bar=ext1_per_bar,
                ext_runtime=ext_runtime,
                wf_activations=wf_queue,
                sent_signals=sent_signals,
                failed_signals=failed_signals,
                signal_digits=signal_digits,
                entries_allowed=True,
                wave_birth_by_time=wave_birth,
                active_counter_wave_times=get_active_counter_wave_times(cfg),
                pcm=__import__("config.enums", fromlist=["PendingCancelMode"]).PendingCancelMode(
                    cfg.pending_cancel_mode
                ) if isinstance(cfg.pending_cancel_mode, str) else cfg.pending_cancel_mode,
                place_live_bos_reentry=ll._place_live_bos_reentry,
                place_live_counter_from_g_extension=ll._place_live_counter_from_g_extension,
                g_extension_hit_closed_positions=ll._g_extension_hit_closed_positions,
                place_live_counter_position=ll._place_live_counter_position,
                log_event_fn=log_event,
                two_sided_tracker=ll._live_two_sided_tracker,
            )
        except Exception as e:  # noqa: BLE001
            errors += 1
            if errors <= 5:
                print(f"  [replay bar {bar_idx}] chyba: {type(e).__name__}: {e}")

    if errors:
        print(f"  POZOR: {errors} chyb pri replay (z {len(df)-1} baru)")

    _mbr.send_order = _real_send  # restore

    ctx = {
        "cfg": cfg,
        "df": df,
        "waves": waves,
        "wave_birth": wave_birth,
        "trend_states_per_wave": trend_states_per_wave,
        "bos_flip_map": bos_flip_map,
        "bos_wave_times": bos_wave_times,
        "send_attempts": dict(_SEND_ATTEMPTS),
    }
    return list(_RECORDED_ORDERS), ctx


def run_backtest_capture(df, cfg) -> tuple[dict[str, dict], list]:
    """Spusti backtest a zachyti VSECHNY backtestem POLOZENE primarni WAVE ordery.

    Hook na `_process_new_wave`: kdykoli vrati True (= order polozen) a nejde o
    two-sided mirror, zaznamename wave_time/dir/EP(=fib50)/SL. To je presne
    rozhodovaci mnozina, kterou ma live polozit taky (W{wave_time} comment).
    Vraci (placed_dict, closed_trades) — placed je nezavisle na tom, zda fillnul.
    """
    import types

    from backtest.engine import BacktestEngine

    eng = BacktestEngine(cfg)
    placed: dict[str, dict] = {}
    orig = eng._process_new_wave

    def _wrapped(wave, bar_idx, bar_time, bar, *, bypass_trend_filter=False,
                 is_two_sided_mirror=False):
        ok = orig(
            wave, bar_idx, bar_time, bar,
            bypass_trend_filter=bypass_trend_filter,
            is_two_sided_mirror=is_two_sided_mirror,
        )
        if ok and not is_two_sided_mirror:
            wt = str(wave["wave_time"])
            placed.setdefault(wt, {
                "dir": "BUY" if int(wave["dir"]) == 1 else "SELL",
                "entry_price": float(wave["fib50"]),
                "sl": float(wave["sl"]),
                "bar_idx": int(bar_idx),
            })
        return ok

    # instance-level override (plain fn, self uz neni potreba)
    eng._process_new_wave = _wrapped
    closed = eng.run(df, retain_wave_snapshot=False)
    return placed, closed


def run_backtest_wave_trades(df, cfg):
    from backtest.engine import BacktestEngine

    return BacktestEngine(cfg).run(df, retain_wave_snapshot=False)


def _find_wave(waves: list, wt: str):
    for w in waves:
        if str(w["wave_time"]) == str(wt):
            return w
    return None


def classify_live_skip(wt: str, ctx: dict) -> str:
    """PROC live cesta tuto vlnu NEPOLOZILA? (backtest ji polozil)

    Replikuje presne poradi filtru z live smycky / replay_missed_closed_bar,
    pomoci STEJNYCH funkci. Vraci nazev prvniho filtru, ktery vlnu zablokoval.
    """
    import pandas as pd

    import runtime.live_loop as ll
    from strategy.ext_logic import is_ext_wave
    from strategy.filters import (
        is_wave_in_allowed_session,
        is_wave_too_large,
        is_wave_too_old,
    )
    from strategy.trend_bos import wave_allowed_for_entry

    cfg = ctx["cfg"]
    df = ctx["df"]
    waves = ctx["waves"]
    tspw = ctx["trend_states_per_wave"]
    wave_birth = ctx["wave_birth"]
    bos_flip_map = ctx["bos_flip_map"]
    bos_wave_times = ctx["bos_wave_times"]

    # Dostala se vlna vubec do send_order? (instrumentace z replay)
    attempts = ctx.get("send_attempts", {})
    if str(wt) in attempts:
        outcomes = attempts[str(wt)]
        if any(placed for _ret, placed in outcomes):
            return "send_order_POLOZIL(neshoda_v_comment?)"
        if any(ret for ret, _placed in outcomes):
            # send_order vratil True ale order nevznikl = guard / duplicate / wave_off
            return "send_order:guard_true_bez_orderu"
        # send_order volan, ale vratil False = cena za SL / abort / fallback_off
        return "send_order:odmitl(cena_za_SL/abort/fallback_off)"

    wave = _find_wave(waves, wt)
    if wave is None:
        return "wave_not_detected_live"
    if bool(wave.get("post_ext_trend_suppressed", False)):
        return "post_ext_trend_suppressed"
    if bool(wave.get("wf_wave_position", False)):
        return "wf_wave_position"
    if (
        cfg.trend_filter_enabled
        and tspw.get(wt) is None
        and tspw.get(str(wt)) is None
        and not bool(wave.get("wf_continued_classic", False))
    ):
        return "no_trend_state_snapshot"

    birth = wave_birth.get(wt, wave_birth.get(str(wt)))
    flip_bar = (
        ll._bos_flip_bar_for_wave(wt, bos_flip_map)
        if cfg.trend_filter_enabled
        else None
    )
    ref_bar = birth
    if (
        flip_bar is not None
        and birth is not None
        and flip_bar > birth
        and str(wt) in bos_wave_times
    ):
        ref_bar = flip_bar
    if ref_bar is not None and 0 <= ref_bar < len(df):
        ref_time = pd.Timestamp(df["time"].iloc[ref_bar]).to_pydatetime()
        if is_wave_too_old(wt, cfg, ref_time=ref_time):
            return "too_old"
    if not is_wave_in_allowed_session(wt, cfg):
        return "session"
    if is_wave_too_large(wave["move_pct"], cfg, is_ext=is_ext_wave(wave, cfg)):
        return "too_large"
    if cfg.trend_filter_enabled:
        ts = tspw.get(wt) or tspw.get(str(wt))
        allowed, reason = wave_allowed_for_entry(wave, ts, cfg)
        if (
            not allowed
            and str(wt) in bos_wave_times
            and not ll._wave_is_wf_origin(wave)
        ):
            allowed = True
        if not allowed:
            return f"trend_filter:{reason}"
    return "ZADNY_FILTR_NESEDI(mela_by_se_polozit)"


def classify_bt_skip(wt: str, ctx: dict) -> str:
    """PROC backtest tuto vlnu NEPOLOZIL? (live ji polozil)

    Replikuje poradi filtru z BacktestEngine._process_new_wave (vstup na baru
    narozeni vlny). Pro deferred (proti trendu) zkusi i retro pres BOS flip bar.
    """
    import pandas as pd

    import runtime.live_loop as ll
    from strategy.ext_logic import is_ext_wave
    from strategy.filters import (
        is_wave_in_allowed_session,
        is_wave_too_large,
        is_wave_too_old,
    )
    from strategy.trend_bos import wave_allowed_for_entry

    cfg = ctx["cfg"]
    df = ctx["df"]
    waves = ctx["waves"]
    tspw = ctx["trend_states_per_wave"]
    wave_birth = ctx["wave_birth"]
    bos_flip_map = ctx["bos_flip_map"]
    bos_wave_times = ctx["bos_wave_times"]

    wave = _find_wave(waves, wt)
    if wave is None:
        return "wave_not_detected_bt"
    if bool(wave.get("post_ext_trend_suppressed", False)):
        return "post_ext_trend_suppressed"

    birth = wave_birth.get(wt, wave_birth.get(str(wt)))
    if birth is None or not (0 <= birth < len(df)):
        return "no_birth_bar"
    birth_time = pd.Timestamp(df["time"].iloc[birth]).to_pydatetime()

    if is_wave_too_old(wt, cfg, ref_time=birth_time):
        return "too_old@birth"
    if not is_wave_in_allowed_session(wt, cfg):
        return "session"

    if cfg.trend_filter_enabled:
        ts = tspw.get(wt) or tspw.get(str(wt))
        allowed, reason = wave_allowed_for_entry(wave, ts, cfg)
        if (
            not allowed
            and str(wt) in bos_wave_times
            and not ll._wave_is_wf_origin(wave)
        ):
            allowed = True
        if not allowed:
            if reason == "wave_against_trend":
                flip_bar = ll._bos_flip_bar_for_wave(wt, bos_flip_map)
                if flip_bar is not None and flip_bar >= birth and 0 <= flip_bar < len(df):
                    flip_time = pd.Timestamp(df["time"].iloc[flip_bar]).to_pydatetime()
                    if not is_wave_too_old(wt, cfg, ref_time=flip_time):
                        return "deferred_retro_should_place(?)"
                    return "deferred_then_too_old@flip"
                return "deferred_trend_flip_no_bos"
            return f"trend_filter:{reason}"

    if is_wave_too_large(wave["move_pct"], cfg, is_ext=is_ext_wave(wave, cfg)):
        return "too_large"
    return "ZADNY_FILTR_NESEDI(mel_by_polozit)"


def run_backtest_waves(df, cfg) -> dict[str, dict]:
    from backtest.stats import trades_to_df

    tdf = trades_to_df(run_backtest_wave_trades(df, cfg))
    if tdf.empty or "position_kind" not in tdf.columns:
        return {}
    wdf = tdf[tdf["position_kind"] == "WAVE"]
    out: dict[str, dict] = {}
    for _, r in wdf.iterrows():
        out[str(r["wave_time"])] = {
            "dir": str(r["dir"]),
            "entry_price": float(r["entry_price"]),
            "sl": float(r["sl"]),
            "tp": (None if r["tp"] is None else float(r["tp"])),
            "pnl_usd": float(r["pnl_usd"]),
        }
    return out


def _wave_closed_trades(all_closed) -> list:
    from backtest.stats import classify_position_kind

    out = []
    for ct in all_closed:
        kind = classify_position_kind(
            is_pp=bool(getattr(ct, "is_pp", False)),
            is_counter=bool(getattr(ct, "is_counter", False)),
            is_bos_reentry=bool(getattr(ct, "is_bos_reentry", False)),
            is_two_sided_mirror=bool(getattr(ct, "is_two_sided_mirror", False)),
            is_ext=bool(getattr(ct, "is_ext", False)),
            entry_tag=str(getattr(ct, "entry_tag", "base")),
        )
        if kind == "WAVE":
            out.append(ct)
    return out


def simulate_live_wave_fills(df, cfg, recorded: list[RecordedOrder]) -> list:
    """
    Dosimuluje fill/SL/TP/BOS/TP_WAVE_N uzavreni pro zaznamenane live ordery
    stejnym modelem jako BacktestEngine (bar-by-bar, bez engine entry pipeline).
    """
    import types
    from collections import defaultdict

    from backtest.engine import BacktestEngine, ClosedTrade, PendingOrder, OpenTrade
    from core.risk import calc_lot_backtest
    from strategy.trend_bos import resolve_effective_tp

    eng = BacktestEngine(cfg)
    by_bar: dict[int, list[RecordedOrder]] = defaultdict(list)
    for ro in recorded:
        by_bar[int(ro.bar_idx)].append(ro)

    placed_wave_times: set[str] = set()

    def _inject(bar_idx: int, bar_time) -> None:
        for ro in by_bar.get(bar_idx, []):
            if ro.wave_time in placed_wave_times:
                continue
            if any(t.wave_time == ro.wave_time for t in eng.open_trades):
                continue
            if any(p.wave_time == ro.wave_time for p in eng.pending_orders):
                continue

            signal = {
                "wave_time": ro.wave_time,
                "dir": int(ro.dir),
                "fib50": float(ro.ep),
                "sl": float(ro.sl),
                "move_pct": 0.3,
            }
            sl = float(ro.sl)
            lot = float(ro.lot) if ro.lot > 0 else calc_lot_backtest(ro.ep, sl, cfg)

            if ro.kind == "MARKET":
                slipped = ro.ep + eng.backtest_slippage * (1 if ro.dir == 1 else -1)
                tp = (
                    float(ro.tp)
                    if ro.tp is not None and ro.tp > 0
                    else resolve_effective_tp(
                        cfg, signal, slipped, sl, is_buy=(ro.dir == 1),
                    )
                )
                dummy = PendingOrder(
                    signal, ro.order_type, ro.ep, sl, tp, lot, bar_idx, bar_time,
                )
                trade = OpenTrade(
                    dummy, bar_idx, slipped, bar_time, "MARKET", sl, tp,
                )
                trade.lot = lot
                eng.open_trades.append(trade)
            else:
                tp = (
                    float(ro.tp)
                    if ro.tp is not None and ro.tp > 0
                    else resolve_effective_tp(
                        cfg, signal, ro.ep, sl, is_buy=(ro.dir == 1),
                    )
                )
                po = PendingOrder(
                    signal, ro.order_type, ro.ep, sl, tp, lot, bar_idx, bar_time,
                )
                eng.pending_orders.append(po)
            placed_wave_times.add(ro.wave_time)

    orig_trigger = eng._trigger_pending

    def _trigger_with_inject(self, bar_idx, bar_time, high, low, open_):
        _inject(bar_idx, bar_time)
        return orig_trigger(bar_idx, bar_time, high, low, open_)

    eng._trigger_pending = types.MethodType(_trigger_with_inject, eng)

    _entry_noops = (
        "_process_new_wave",
        "_maybe_fire_two_sided_counter",
        "_process_ext_secondary_for_wave",
        "_process_pp_break_on_bar",
        "_process_ext_counter_time",
        "_process_ext_bos_on_bar",
    )
    for name in _entry_noops:
        setattr(eng, name, types.MethodType(lambda self, *a, **k: None, eng))

    eng.run(df)
    return _wave_closed_trades(eng.closed_trades)


def compute_wave_pnl_ddi(
    closed_trades: list,
    *,
    date_from: str,
    date_to: str,
    bot_name: str,
) -> dict:
    from backtest.grid.study_mode import apply_wave_isolation_report_stats, filter_trades_df_for_grid_stats
    from backtest.metrics.robustness import compute_robustness_metrics
    from backtest.stats import compute_stats, trades_to_df

    tdf = trades_to_df(closed_trades)
    combo = {
        "wave_isolation_study": True,
        "wave_positions_only": True,
        "date_from": date_from,
        "date_to": date_to,
    }
    wdf = filter_trades_df_for_grid_stats(tdf, combo)
    stats = compute_stats(wdf, date_from=date_from, date_to=date_to)
    stats = apply_wave_isolation_report_stats(stats, combo)
    stats.update(
        compute_robustness_metrics(
            wdf,
            max_dd_pct_vs_peak=stats.get("max_drawdown_pct_vs_peak"),
            max_dd_pct_vs_initial=stats.get("max_drawdown_pct"),
            bot_name=bot_name,
        )
    )
    return stats


def _is_no_tp(x) -> bool:
    if x is None:
        return True
    try:
        return float(x) != float(x) or float(x) == 0.0  # NaN nebo 0.0 = bez TP
    except (TypeError, ValueError):
        return False


def _tp_equal(a, b) -> bool:
    if _is_no_tp(a) and _is_no_tp(b):
        return True
    if _is_no_tp(a) or _is_no_tp(b):
        return False
    return abs(float(a) - float(b)) <= 1e-4


def _pips(a: float, b: float) -> float:
    """Rozdil v pipech (5-digit EURUSD: 1 pip = 0.0001)."""
    return abs(float(a) - float(b)) / 0.0001


def main() -> None:
    from backtest.data_loader import filter_by_date_range, load_csv
    from config.bot_config import LIVE_BOT_CONFIG
    from config.position_modes import resolve_grid_engine_config
    from runtime.live_wave_isolation import resolve_live_execution_config

    print("=" * 72)
    print("LIVE-REPLAY PARITY  |  obdobi", DATE_FROM, "..", DATE_TO)
    print("=" * 72)

    if not mt5.initialize(
        path=str(__import__("mt5_credentials").MT5_PATH),
        login=__import__("mt5_credentials").MT5_LOGIN,
        password=__import__("mt5_credentials").MT5_PASSWORD,
        server=__import__("mt5_credentials").MT5_SERVER,
    ):
        print("MT5 initialize selhal:", mt5.last_error())
        sys.exit(1)
    install_mocks()  # az PO initialize — order vrstva je ted bezpecna

    df = filter_by_date_range(load_csv(str(CSV)), DATE_FROM, DATE_TO)
    print(f"baru: {len(df)}  ({df['time'].iloc[0]} .. {df['time'].iloc[-1]})")

    from dataclasses import replace

    engine_cfg = resolve_grid_engine_config(LIVE_BOT_CONFIG)
    live_cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)
    # Bod B: ".x"/".r" jsou jen jine nazvy stejneho symbolu — vzdy pouzij
    # zakladni symbol z bot_config (FTMO = "EURUSD"), aby symbol_info sedel.
    _base_sym = str(engine_cfg.symbol).split(".")[0]
    if _base_sym != engine_cfg.symbol:
        print(f"  [symbol] '{engine_cfg.symbol}' -> '{_base_sym}' (suffix je jen jiny nazev)")
        engine_cfg = replace(engine_cfg, symbol=_base_sym)
        live_cfg = replace(live_cfg, symbol=_base_sym)

    print(f"  risk_usd={engine_cfg.risk_usd}  symbol={engine_cfg.symbol}")

    print("\n[1/4] BACKTEST engine — POLOZENE WAVE ordery + PnL/DDi ...")
    bt_placed, bt_closed_all = run_backtest_capture(df, engine_cfg)
    bt_closed = _wave_closed_trades(bt_closed_all)
    bt_filled_keys = {str(t.wave_time) for t in bt_closed}
    bt_stats = compute_wave_pnl_ddi(
        bt_closed, date_from=DATE_FROM, date_to=DATE_TO, bot_name=engine_cfg.bot_name,
    )
    print(f"  backtest POLOZENO primarnich WAVE orderu: {len(bt_placed)}")
    print(f"  backtest z toho FILLED+CLOSED WAVE:       {len(bt_filled_keys)}")

    print("\n[2/4] LIVE replay (mocked orders) ...")
    recorded, live_ctx = run_live_replay(df, live_cfg)
    live = collect_wave_orders()
    print(f"  live WAVE orderu zaznamenano:    {len(recorded)}")
    print(f"  live POLOZENO unikatnich WAVE:   {len(live)}")
    print(f"  (na ucet odeslano realnych orderu: 0 — mt5.order_send zamockovan)")

    print("\n[3/4] LIVE fill simulace (engine model) + PnL/DDi ...")
    live_closed = simulate_live_wave_fills(df, engine_cfg, recorded)
    live_stats = compute_wave_pnl_ddi(
        live_closed, date_from=DATE_FROM, date_to=DATE_TO, bot_name=engine_cfg.bot_name,
    )
    print(f"  live sim WAVE obchodu (filled):  {len(live_closed)}")

    # ── ROZHODOVACI PARITA: placed (BT) vs placed (LIVE) — mimo spread/fill ──
    bt_keys = set(bt_placed)
    live_keys = set(live)
    matched = sorted(bt_keys & live_keys)
    bt_only = sorted(bt_keys - live_keys)    # BT polozil, LIVE ne -> REALNY rozdil
    live_only = sorted(live_keys - bt_keys)  # LIVE polozil, BT ne -> REALNY rozdil

    dir_mismatch = []
    ep_diff_real = []     # > 1 pip, jen LIMIT (kde EP=fib50 v obou) = REALNY
    ep_diff_subpip = 0    # <= 1 pip (spread/rounding — zanedbatelne)
    sl_diff_real = []
    market_fill_cnt = 0   # MARKET fallback: EP=fill cena vs fib50 — fallback, ignore
    decision_exact = 0
    for wt in matched:
        b = bt_placed[wt]
        l = live[wt]
        if b["dir"] != l.dir:
            dir_mismatch.append((wt, b, l))
            continue
        # MARKET fallback: backtest EP capturuju jako fib50, live jako market-fill
        # cenu -> rozdil je fallback fill (spread/posun ceny), NE rozhodovaci rozdil.
        if l.kind == "MARKET":
            market_fill_cnt += 1
            decision_exact += 1  # rozhodnuti (placed stejnou vlnu) je shodne
            continue
        ep_p = _pips(b["entry_price"], l.ep)
        sl_p = _pips(b["sl"], l.sl)
        if ep_p > 1.0:
            ep_diff_real.append((wt, b, l, ep_p))
        elif ep_p > 1e-6:
            ep_diff_subpip += 1
        if sl_p > 1.0:
            sl_diff_real.append((wt, b, l, sl_p))
        if ep_p <= 1.0 and sl_p <= 1.0:
            decision_exact += 1

    print("\n" + "=" * 72)
    print("[4/4] ROZHODOVACI PARITA — POLOZENE ordery BT vs LIVE (mimo spread/fill)")
    print("=" * 72)
    print(f"  backtest POLOZENO WAVE: {len(bt_placed)}")
    print(f"  live     POLOZENO WAVE: {len(live)}")
    print(f"  shoda wave_time:        {len(matched)}")
    print(f"    z toho rozhodnuti shoda (EP+SL+dir <=1pip / MARKET): {decision_exact}")
    print(f"    z toho MARKET fallback (EP=fill, ignore):  {market_fill_cnt}")
    print(f"    EP rozdil <=1 pip LIMIT (spread/round):    {ep_diff_subpip}")
    print(f"    EP rozdil >1 pip LIMIT (REALNY):           {len(ep_diff_real)}")
    print(f"    SL rozdil >1 pip LIMIT (REALNY):           {len(sl_diff_real)}")
    print(f"    dir mismatch:                              {len(dir_mismatch)}")
    print(f"  >>> REALNY rozdil A: BT polozil, LIVE NE: {len(bt_only)}")
    print(f"  >>> REALNY rozdil B: LIVE polozil, BT NE: {len(live_only)}")

    # ── klasifikace REALNYCH divergenci podle filtru ──
    from collections import Counter

    if bt_only:
        reasons = Counter(classify_live_skip(wt, live_ctx) for wt in bt_only)
        print(f"\n  --- PROC LIVE NEPOLOZIL ({len(bt_only)} vln, BT je polozil) ---")
        for reason, n in reasons.most_common():
            print(f"    {n:>4}x  {reason}")
        print("    priklady:")
        for wt in bt_only[:15]:
            b = bt_placed[wt]
            print(f"      {wt} {b['dir']} EP={b['entry_price']:.5f} -> {classify_live_skip(wt, live_ctx)}")

    if live_only:
        reasons = Counter(classify_bt_skip(wt, live_ctx) for wt in live_only)
        print(f"\n  --- PROC BACKTEST NEPOLOZIL ({len(live_only)} vln, LIVE je polozil) ---")
        for reason, n in reasons.most_common():
            print(f"    {n:>4}x  {reason}")
        print("    priklady:")
        for wt in live_only[:15]:
            l = live[wt]
            print(f"      {wt} {l.dir} EP={l.ep:.5f} -> {classify_bt_skip(wt, live_ctx)}")

    if dir_mismatch:
        print("\n  --- dir mismatch (smer obchodu se lisi!) ---")
        for wt, b, l in dir_mismatch[:20]:
            print(f"    {wt}  BT {b['dir']}  LIVE {l.dir}")
    if ep_diff_real:
        print(f"\n  --- EP rozdil > 1 pip (prvnich 20 z {len(ep_diff_real)}) ---")
        for wt, b, l, p in sorted(ep_diff_real, key=lambda x: -x[3])[:20]:
            print(f"    {wt}  BT EP={b['entry_price']:.5f}  LIVE EP={l.ep:.5f} ({l.kind})  diff={p:.1f} pip")
    if sl_diff_real:
        print(f"\n  --- SL rozdil > 1 pip (prvnich 10 z {len(sl_diff_real)}) ---")
        for wt, b, l, p in sorted(sl_diff_real, key=lambda x: -x[3])[:10]:
            print(f"    {wt}  BT SL={b['sl']:.5f}  LIVE SL={l.sl:.5f}  diff={p:.1f} pip")

    _print_pnl_ddi_table(bt_stats, live_stats)

    mt5.shutdown()


def _print_pnl_ddi_table(bt_stats: dict, live_stats: dict) -> None:
    bt_ddi = bt_stats.get("ddi_profile", {}) or {}
    live_ddi = live_stats.get("ddi_profile", {}) or {}

    def _f(d: dict, k: str, default=0.0):
        v = d.get(k, default)
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    rows = [
        ("trades_wave", int(bt_stats.get("trades_wave", 0)), int(live_stats.get("trades_wave", 0)), ""),
        ("wins / losses", f"{bt_stats.get('wins', 0)}/{bt_stats.get('losses', 0)}", f"{live_stats.get('wins', 0)}/{live_stats.get('losses', 0)}", ""),
        ("win_rate_pct", _f(bt_stats, "win_rate_pct"), _f(live_stats, "win_rate_pct"), "%"),
        ("net_pnl_usd", round(_f(bt_stats, "net_pnl_usd"), 2), round(_f(live_stats, "net_pnl_usd"), 2), "USD"),
        ("max_drawdown_pct", round(_f(bt_stats, "max_drawdown_pct"), 2), round(_f(live_stats, "max_drawdown_pct"), 2), "%"),
        ("max_ddi_pct", round(_f(bt_ddi, "max_ddi_pct"), 2), round(_f(live_ddi, "max_ddi_pct"), 2), "%"),
        ("p90_ddi_pct", round(_f(bt_ddi, "p90_ddi_pct"), 2), round(_f(live_ddi, "p90_ddi_pct"), 2), "%"),
        ("median_ddi_pct", round(_f(bt_ddi, "median_ddi_pct"), 2), round(_f(live_ddi, "median_ddi_pct"), 2), "%"),
        ("dnu_poruseni_5pct", int(_f(bt_ddi, "dnu_poruseni_5pct")), int(_f(live_ddi, "dnu_poruseni_5pct")), "dni"),
        ("dnu_poruseni_10pct", int(_f(bt_ddi, "dnu_poruseni_10pct")), int(_f(live_ddi, "dnu_poruseni_10pct")), "dni"),
    ]

    print("\n" + "=" * 72)
    print("PnL / DDi — BACKTEST vs LIVE (fill sim, stejny engine model)")
    print("=" * 72)
    print(f"  {'metrika':<22} {'backtest':>14} {'live sim':>14}  delta")
    print("  " + "-" * 66)
    for name, bt_v, live_v, unit in rows:
        delta = ""
        if isinstance(bt_v, (int, float)) and isinstance(live_v, (int, float)):
            d = float(live_v) - float(bt_v)
            suffix = unit if unit else ""
            delta = f"{d:+.2f}{suffix}" if suffix in ("%", "USD") else f"{d:+.0f}{suffix}"
        print(f"  {name:<22} {str(bt_v):>14} {str(live_v):>14}  {delta}")


if __name__ == "__main__":
    main()
