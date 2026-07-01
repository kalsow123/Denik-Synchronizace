

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Set

import MetaTrader5 as mt5
import pandas as pd

from config.bot_config import BotConfig
from config.enums import TPMode
from core.logging_utils import (
    LOG_TARGETS_JSONL_ONLY,
    LOG_TARGETS_TEXT_SINKS,
    log_event,
    maybe_prune_jsonl_runtime,
)
from core.signal_keys import get_signal_key
from infra.account import get_account_snapshot
from infra.trade_tracker import TradeTrackerState, update_trade_tracker
from core.market_data import load_bars, strip_forming_bar
from infra.position_cap_live import enforce_live_position_cap
from infra.orders import (
    cancel_expired_pending,
    cancel_all_pendings,
    cancel_pendings_by_direction,
    cancel_flip_follower_pendings_on_bos,
    cancel_counter_trend_wave_pendings,
    cancel_pp_pendings,
    close_all_positions,
    close_flip_follower_positions_on_bos,
    close_positions_by_direction,
    enforce_counter_positions_min_sl,
    get_pp_pending_wave_times,
    get_active_counter_wave_times,
    place_bos_reentry_market,
    place_counter_position_pending,
    place_counter_position_market,
    place_pp_market_fallback,
    place_pp_pending,
    send_order,
)
from core.risk import calc_lot, round_to_step
from infra.session_manager import (
    get_broker_now,
    get_session_now,
    is_session_enabled,
    is_in_session,
    is_pre_close_buffer,
    is_week_close_pre_buffer,
    seconds_until_open,
)
from infra.state_sync import get_active_wave_times, get_position_wave_times
from strategy.filters import is_wave_too_old, is_wave_too_large, is_wave_in_allowed_session
from strategy.trend_bos import (
    bos_flip_time_to_log_str,
    bos_per_bar_close_reason,
    compute_bos_wave_flip_map,
    compute_bos_wave_times,
    compute_trend_states_per_bar,
    compute_trend_states_per_wave,
    find_close_bos_flip_for_target_since,
    pp_trend_confirmed_by_close_bos,
    find_pp_candidate_wave,
    pp_wave_eligible_for_break,
    bos_entry_in_rrr_fixed_enabled,
    bos_entry_should_open_on_flip,
    bos_flip_handler_should_run,
    tp_mode_uses_bos_per_bar_exit,
    wave_allowed_for_entry,
    _wave_is_wf_origin,
)
from strategy.wave_detection import detect_waves
from strategy.wave_sequence import (
    compute_ladder_sl_from_wave_size,
    compute_sl_price_from_pct,
    sync_wave_sequence_state,
    compute_wave_2_no_tp_protected_waves,
    compute_wave_target_tp_price,
    compute_wave_counter_take_profit,
    compute_wave_counter_sl_setup,
    find_wave_by_time,
    is_tp_wave_index,
    wave_counter_min_sl_pct,
)
from strategy.wave_target_n_mode import is_wave_target_n_family, is_wave_target_n_g
from strategy.wave_target_n_early import (
    FormingTpWatch,
    g_counter_wave_time,
    wave_counter_entry_allowed,
)
from strategy.two_sided import (
    TwoSidedTracker,
    find_parent_wave_for_two_sided,
    parent_wave_qualifies,
    prepare_ts2_mirror_entry_signal,
    replay_two_sided_tracker_engine_parity,
    skip_primary_entry_on_parent_wave,
    should_open_two_sided_counter,
    two_sided_enabled,
    wave_counter_two_sided_orders_enabled,
)
from strategy.ext_logic import (
    apply_first_opposite_wave_sl_after_ext,
    is_ext_wave,
)
from strategy.trend_bos import resolve_effective_tp
from runtime.adx14_live import Adx14LiveRuntime
from runtime.ext_live import ExtLiveRuntime
from runtime.wf_live import WfLiveRuntime

# ───── LIVE BOT ──────────────────────────
# Nastavení jak bot funguje. Co dělá během errorů, jak je řeší.

log = logging.getLogger(__name__)

_live_two_sided_tracker = TwoSidedTracker()

"""
    Bezi neustale dokud neprijde KeyboardInterrupt nebo shutdown signal.
    V kazdem cyklu:
      0) SESSION MANAGER (pokud zapnuty): kontrola jestli je trading session.
         - Pre-close buffer: snapshot vsech pendingu -> cancel (volitelne i pozice v patek)
         - Po wake-up: obnovi snapshot (WAVE/CNTR/PP/EXT/...) + pine WAVE doplneni
      1) Zrusi expirovane pendingy.
      2) Nacte poslednich cfg.startup_bars baru z MT5 (ADX/housekeeping kazdych cfg.sleep_sec).
         Strategie (vlny, BOS, TP-WAVE, vstupy, EXT/WF) jen pri novem uzavrenem M30 baru;
         forming bar (-1) se pred detekci odstrani (parita backtest close-only).
      3) Sesynchronizuje signaly z MT5 (pendingy + pozice) do `sent_signals`.
      4) Projde vlny:
           - prilis stare (> max_wave_age_hours od posledniho close baru) -> oznaci jako zpracovane
           - mimo povolenou session -> oznaci jako zpracovane, preskoci
           - birth_bar != posledni close bar -> preskoci (parita backtest; recovery jde jinou cestou)
           - jeste nezpracovane -> posle pres send_order() s `cfg.entry_mode`
      5) Time-based status logy a OLD_WAVES_SUMMARY (text vs jsonl lze ruzne periody).
    """

def _pp_calc_lot_live(cfg: BotConfig, entry_price: float, sl_price: float) -> float:
    """
    Live verze _pp_calc_lot. risk = cfg.pp_risk_usd; contract_size z MT5
    symbol_info pokud je dostupne, jinak cfg.contract_size.
    """
    sl_dist = abs(float(entry_price) - float(sl_price))
    if sl_dist == 0.0:
        return float(getattr(cfg, "min_lot", 0.01))
    contract_size = None
    try:
        info = mt5.symbol_info(cfg.symbol)
        if info is not None:
            contract_size = float(info.trade_contract_size)
    except Exception:
        contract_size = None
    if not contract_size:
        contract_size = float(cfg.contract_size)
    risk_per_lot = sl_dist * contract_size
    if risk_per_lot <= 0:
        return float(getattr(cfg, "min_lot", 0.01))
    risk_usd = float(getattr(cfg, "pp_risk_usd", cfg.risk_usd))
    return round_to_step(risk_usd / risk_per_lot, cfg)


def _log_adx14_entry_blocked(cfg: BotConfig, entry_type: str, **extra) -> None:
    log_event(cfg, "info", "ENTRY_BLOCKED_BY_ADX14_GATE", entry_type=entry_type, **extra)


def _wave_birth_bar_index(wave_time: str, wave_birth_by_time: dict) -> int | None:
    birth = wave_birth_by_time.get(wave_time)
    if birth is None:
        birth = wave_birth_by_time.get(str(wave_time))
    if birth is None:
        return None
    return int(birth)


def _wave_born_on_last_bar(
    wave_time: str,
    *,
    wave_birth_by_time: dict,
    last_bar_idx: int,
) -> bool:
    """Parita s backtest engine: vstup jen pro vlny narozene na poslednim close baru."""
    birth = _wave_birth_bar_index(wave_time, wave_birth_by_time)
    if birth is None:
        return False
    return birth == int(last_bar_idx)


def _bos_flip_bar_for_wave(
    wave_time: str,
    bos_flip_map: dict[int, str],
) -> int | None:
    """Bar index close-based BOS flipu pro danou bos-vlnu (inverze flip mapy)."""
    wt = str(wave_time)
    for bar_ix, mapped_wt in bos_flip_map.items():
        if str(mapped_wt) == wt:
            return int(bar_ix)
    return None


def _apply_birth_bar_gate(
    wave_time: str,
    *,
    wave_birth_by_time: dict,
    last_bar_idx: int,
    sent_signals: Set[str],
    sig_key: str,
    bos_flip_bar: int | None = None,
    is_bos_retro_candidate: bool = False,
) -> bool:
    """
    True = pokracovat na send_order (vlna narozena na poslednim baru).
    False = preskocit; pri minulem birth baru oznacit sig_key jako zpracovany.

    BOS retro vlny (wave_against_trend cekajici na flip) se pred flip barem
    neoznacuji jako permanently missed — parita s engine `_bos_flip_wave_by_bar`.
    Vstup z retro probehne v `_attempt_live_bos_retro_entry` na flip baru.
    Startup recovery / MT5 pending sync birth_bar gate neobchazi — tam se send_order nevolá.
    """
    birth = _wave_birth_bar_index(wave_time, wave_birth_by_time)
    if birth is None:
        return False
    if birth == int(last_bar_idx):
        return True
    if birth < int(last_bar_idx):
        if (
            is_bos_retro_candidate
            and bos_flip_bar is not None
            and int(last_bar_idx) < int(bos_flip_bar)
        ):
            return False
        sent_signals.add(sig_key)
    return False


def _attempt_live_bos_retro_entry(
    *,
    cfg: BotConfig,
    wave: dict,
    last_bar_idx: int,
    last_bar_time: datetime,
    wave_birth_by_time: dict,
    bos_flip_map: dict[int, str],
    sent_signals: Set[str],
    failed_signals: Dict[str, Dict[str, Any]],
    retro_bos_attempted: Set[str],
    signal_digits: int,
    entries_allowed: bool,
    fill_trend_state: Any,
    ext_sl_anchor: Optional[dict],
    seq_info: dict,
    waves: list,
    bar_close: float | None = None,
) -> tuple[bool, Optional[dict]]:
    """
    BOS retro aktivace na close-based flip baru — parita engine
    `_bos_flip_wave_by_bar` + `_process_new_wave(bypass_trend_filter=True)`.

    Same-bar birth+flip resi hlavni wave loop (trend filter retro bypass).
    """
    from runtime.missed_bar_replay import _trace as _retro_trace

    wt = str(wave.get("wave_time", "") or "")
    if not wt or wt in retro_bos_attempted:
        _retro_trace(wt, last_bar_idx, "retro:already_attempted_or_empty")
        return False, ext_sl_anchor

    birth = _wave_birth_bar_index(wt, wave_birth_by_time)
    if birth is None or birth >= int(last_bar_idx):
        _retro_trace(wt, last_bar_idx, "retro:birth_ge_bar", birth=birth)
        return False, ext_sl_anchor

    flip_bar = _bos_flip_bar_for_wave(wt, bos_flip_map)
    if flip_bar is None or int(flip_bar) != int(last_bar_idx):
        _retro_trace(wt, last_bar_idx, "retro:not_flip_bar", flip_bar=flip_bar)
        return False, ext_sl_anchor

    if _wave_is_wf_origin(wave):
        _retro_trace(wt, last_bar_idx, "retro:wf_origin")
        return False, ext_sl_anchor

    sig_key = get_signal_key(wave, digits=signal_digits)
    if sig_key in sent_signals:
        _retro_trace(wt, last_bar_idx, "retro:already_sent")
        retro_bos_attempted.add(wt)
        return False, ext_sl_anchor

    retro_bos_attempted.add(wt)

    if bool(wave.get("post_ext_trend_suppressed", False)):
        _retro_trace(wt, last_bar_idx, "retro:post_ext_suppressed")
        sent_signals.add(sig_key)
        return False, ext_sl_anchor

    if is_wave_too_old(wt, cfg, ref_time=last_bar_time):
        _retro_trace(wt, last_bar_idx, "retro:too_old", flip_bar=flip_bar, birth=birth)
        sent_signals.add(sig_key)
        return False, ext_sl_anchor

    if not is_wave_in_allowed_session(wt, cfg):
        _retro_trace(wt, last_bar_idx, "retro:session")
        sent_signals.add(sig_key)
        return False, ext_sl_anchor

    if is_wave_too_large(wave["move_pct"], cfg, is_ext=is_ext_wave(wave, cfg)):
        _retro_trace(wt, last_bar_idx, "retro:too_large")
        sent_signals.add(sig_key)
        return False, ext_sl_anchor

    if not bool(getattr(cfg, "wave_position_enabled", True)):
        if _try_live_counter_only_on_wave(
            cfg=cfg,
            wave=wave,
            seq_info=seq_info,
            all_waves=waves,
            entries_allowed=entries_allowed,
            sent_signals=sent_signals,
            sig_key=sig_key,
        ):
            retro_bos_attempted.add(wt)
            return False, ext_sl_anchor
        sent_signals.add(sig_key)
        retro_bos_attempted.add(wt)
        return False, ext_sl_anchor

    if not entries_allowed:
        _log_adx14_entry_blocked(cfg, entry_type="WAVE_BOS_RETRO", wave_id=wt)
        sent_signals.add(sig_key)
        return False, ext_sl_anchor

    wave_order = wave
    if is_ext_wave(wave, cfg):
        ext_sl_anchor = wave
    else:
        wave_order, ext_sl_anchor = apply_first_opposite_wave_sl_after_ext(
            wave,
            ext_anchor=ext_sl_anchor,
            cfg=cfg,
        )

    log.info(
        f"BOS RETRO VLNA | {'BUY' if wave_order['dir'] == 1 else 'SELL'} "
        f"EP={wave_order['fib50']:.5f} SL={wave_order['sl']:.5f} "
        f"TP={wave_order['tp']:.5f} | Wave {wave_order['move_pct']:.2f}% | {wt}"
    )
    log_event(
        cfg,
        "info",
        "WAVE_BOS_RETRO_ENTRY",
        wave_id=wt,
        flip_bar=int(flip_bar),
        birth_bar=int(birth),
    )

    placed_meta: Dict[str, Any] = {}
    if send_order(
        wave_order,
        cfg,
        entry_mode=cfg.entry_mode,
        placed_meta=placed_meta,
        trend_state_at_fill=fill_trend_state,
        bypass_trend_filter=True,
        bar_close=bar_close,
    ):
        _maybe_place_live_counter_from_tp(
            cfg=cfg,
            wave=wave_order,
            seq_info=seq_info,
            tp_price=placed_meta.get("tp_price"),
            all_waves=waves,
            entries_allowed=entries_allowed,
        )
        _retro_trace(wt, last_bar_idx, "retro:SENT")
        sent_signals.add(sig_key)
        failed_signals.pop(sig_key, None)
        return True, ext_sl_anchor

    _retro_trace(wt, last_bar_idx, "retro:send_failed")
    prev = failed_signals.get(sig_key, {"wave": wave_order, "attempts": 0})
    prev["wave"] = wave_order
    prev["attempts"] = int(prev.get("attempts", 0)) + 1
    failed_signals[sig_key] = prev
    log_event(
        cfg,
        "warning",
        "WAVE_BOS_RETRO_FAILED",
        wave_id=wt,
        flip_bar=int(flip_bar),
        attempts=int(prev["attempts"]),
    )
    return False, ext_sl_anchor


def _last_closed_bar_time(df: pd.DataFrame) -> pd.Timestamp:
    """Posledni uzavreny bar: MT5 posledni radek je forming bar (-1), close je -2."""
    if len(df) < 2:
        return pd.Timestamp(df["time"].iloc[-1])
    return pd.Timestamp(df["time"].iloc[-2])


def _df_closed_bars_only(df: pd.DataFrame) -> pd.DataFrame:
    """Strategie bezi jen na uzavrenych barech — odstrani forming bar z MT5."""
    return strip_forming_bar(df)


def _maybe_fire_pp_break_event(*, cfg: BotConfig, df, waves,
                                current_trend: str,
                                processed_pp_wave_times: Set[str],
                                wave_birth_by_time: dict[str, int],
                                entries_allowed: bool = True,
                                bar_idx: int | None = None) -> None:
    """
    Detekuje PP break na poslednim close-baru:
      - V UP trendu hleda nejnovejsi UP vlnu, jejiz box_top je prelomeny posledni
        close cenou (close > box_top a vlna nebyla jeste PP-brokana).
      - V DOWN trendu obracene (close < box_bottom).

    Kandidat = nejnovejsi narozena vlna ve smeru trendu; PP az po ukonceni vlny
    (existuje dalsi narozena vlna). 1× break per wave_time.
    """
    if df is None or df.empty:
        return
    if not entries_allowed:
        return
    if current_trend not in ("bull", "bear"):
        return
    if not pp_trend_confirmed_by_close_bos(df, waves, cfg, current_trend):
        log_event(
            cfg,
            "info",
            "PP_SKIPPED_TREND_FROM_SEED_RESET",
            trend=str(current_trend),
        )
        return
    _bar_ix = int(bar_idx) if bar_idx is not None else len(df) - 1
    bar_close = float(df["close"].iloc[_bar_ix])
    trend_dir = 1 if current_trend == "bull" else -1
    last_bar_idx = _bar_ix

    candidate = find_pp_candidate_wave(
        waves,
        wave_birth_by_time,
        last_bar_idx,
        trend_dir,
        broken_wave_times=processed_pp_wave_times,
    )
    if candidate is None:
        return

    pp_ok_wave, pp_skip_reason = pp_wave_eligible_for_break(
        candidate,
        bar_idx=last_bar_idx,
        wave_birth=wave_birth_by_time,
        cfg=cfg,
    )
    if not pp_ok_wave:
        log_event(
            cfg,
            "info",
            "PP_SKIPPED",
            wave_id=str(candidate.get("wave_time", "")),
            reason=pp_skip_reason,
            trend=str(current_trend),
        )
        return

    try:
        box_top = float(candidate["box_top"])
        box_bot = float(candidate["box_bottom"])
    except (KeyError, TypeError, ValueError):
        return

    if trend_dir == 1:
        broken = bar_close > box_top
        trigger_level = box_top
    else:
        broken = bar_close < box_bot
        trigger_level = box_bot
    if not broken:
        return

    # NOVY PP! Zrus stare PP pendingy (max 1 najednou).
    try:
        n_cancelled = cancel_pp_pendings(cfg)
    except Exception as e:
        log.error(f"PP: cancel_pp_pendings selhal: {e}", exc_info=True)
        n_cancelled = 0

    # SL z `pp_sl_pct` procent od trigger_level.
    is_buy = (trend_dir == 1)
    pp_sl_pct = float(getattr(cfg, "pp_sl_pct", 0.21))
    sl_price = compute_sl_price_from_pct(trigger_level, pp_sl_pct, is_buy=is_buy)

    # TP dle aktualniho tp_mode (resolve_effective_tp; pro WAVE_TARGET_N vrati
    # None — TP se nastavi az pri pristim TP-wave eventu v trendu).
    tp = resolve_effective_tp(cfg, candidate, trigger_level, sl_price, is_buy=is_buy)

    lot = _pp_calc_lot_live(cfg, trigger_level, sl_price)
    if lot <= 0.0:
        log.warning(f"PP: lot={lot} <= 0 — preskakuji")
        return

    info_symbol = mt5.symbol_info(cfg.symbol)
    digits = int(getattr(info_symbol, "digits", 5)) if info_symbol else 5
    wave_time_str = str(candidate["wave_time"])

    log_event(
        cfg, "info", "PP_BREAK_TRIGGERED",
        wave_time=wave_time_str,
        trend=current_trend,
        bar_close=float(bar_close),
        trigger_level=float(trigger_level),
        sl_pct=float(pp_sl_pct),
        sl=float(sl_price),
        tp=(None if tp is None else float(tp)),
        lot=float(lot),
        cancelled_old_pp=int(n_cancelled),
    )

    # 1) Pokus o LIMIT
    ok = False
    try:
        ok = place_pp_pending(
            cfg,
            wave_time=wave_time_str,
            trend_dir=int(trend_dir),
            entry_price=float(trigger_level),
            sl_price=float(sl_price),
            tp_price=(None if tp is None else float(tp)),
            lot=float(lot),
            digits=digits,
        )
    except Exception as e:
        log.error(f"PP LIMIT selhal: {e}", exc_info=True)
        ok = False

    # 2) Fallback MARKET pokud LIMIT odmitnut
    if not ok:
        try:
            place_pp_market_fallback(
                cfg,
                wave_time=wave_time_str,
                trend_dir=int(trend_dir),
                entry_price=float(trigger_level),
                sl_price=float(sl_price),
                tp_price=(None if tp is None else float(tp)),
                lot=float(lot),
                digits=digits,
            )
        except Exception as e:
            log.error(f"PP MARKET fallback selhal: {e}", exc_info=True)

    processed_pp_wave_times.add(wave_time_str)


def _place_live_bos_reentry(*, cfg: BotConfig, new_trend_dir: str,
                            broken_trend_dir: str, bar_trend_states,
                            waves, entries_allowed: bool = True) -> None:
    """
    LIVE ekvivalent backtest engine `_place_bos_reentry_market`.

    SL = ladder z velikosti POSLEDNI vlny rozbiteho smeru (bar_trend_states[-2]
    pred BOS flipem). Lot = calc_lot(actual_entry, sl_price, cfg). Entry = aktualni
    ASK (BUY) / BID (SELL) z mt5.symbol_info_tick.
    """
    if not bar_trend_states:
        return
    if not entries_allowed:
        _log_adx14_entry_blocked(cfg, entry_type="BOS_REENTRY")
        return
    new_dir = 1 if new_trend_dir == "bull" else -1

    # Predchozi stav (pred flipem): hledame zpetne, dokud najdeme bar s
    # broken_trend_dir (cca [-2], pripadne [-3] kdyby byl flip vice baru).
    broken_wave_time = None
    for state in reversed(bar_trend_states[:-1]):
        if state.direction == broken_trend_dir:
            if broken_trend_dir == "bull":
                broken_wave_time = getattr(state, "last_up_wave_time", None)
            else:
                broken_wave_time = getattr(state, "last_down_wave_time", None)
            break
    if not broken_wave_time:
        log.warning(
            "BOS re-entry: nepodarilo se zjistit posledni vlnu rozbiteho smeru — preskakuji"
        )
        return

    broken_wave = next((w for w in waves if w["wave_time"] == broken_wave_time), None)
    if broken_wave is None:
        log.warning("BOS re-entry: rozbita vlna nenalezena v `waves` — preskakuji")
        return

    tick = mt5.symbol_info_tick(cfg.symbol)
    if tick is None:
        log.warning("BOS re-entry: nelze ziskat tick — preskakuji")
        return
    is_buy = (new_dir == 1)
    entry_price = float(tick.ask if is_buy else tick.bid)
    wave_size_pct = float(broken_wave.get("move_pct", 0.0))
    sl_pct, sl_price = compute_ladder_sl_from_wave_size(
        entry_price, wave_size_pct, cfg, is_buy=is_buy
    )
    if sl_pct <= 0.0:
        log.warning(f"BOS re-entry: sl_pct={sl_pct} <= 0 — preskakuji")
        return

    lot = calc_lot(entry_price, sl_price, cfg)
    if lot <= 0.0:
        log.warning(f"BOS re-entry: lot={lot} <= 0 — preskakuji")
        return

    synth_signal = dict(broken_wave)
    synth_signal["dir"] = new_dir
    synth_signal.pop("wave_target_tp_price", None)
    tp = resolve_effective_tp(cfg, synth_signal, entry_price, sl_price, is_buy=is_buy)

    info = mt5.symbol_info(cfg.symbol)
    digits = int(getattr(info, "digits", 5)) if info else 5

    log_event(
        cfg, "info", "BOS_REENTRY_TRIGGERED",
        new_trend=new_trend_dir,
        broken_trend=broken_trend_dir,
        broken_wave_time=str(broken_wave_time),
        wave_size_pct=float(wave_size_pct),
        sl_pct=float(sl_pct),
        entry=float(entry_price),
        sl=float(sl_price),
        lot=float(lot),
        tp=(None if tp is None else float(tp)),
    )
    place_bos_reentry_market(
        cfg,
        new_trend_dir=new_dir,
        entry_price=entry_price,
        sl_price=sl_price,
        lot=lot,
        digits=digits,
        broken_wave_time=str(broken_wave_time),
        tp_price=(None if tp is None else float(tp)),
    )


def _place_live_counter_position(*, cfg: BotConfig, wave: dict, info,
                                  trend_dir: int, tp_price: float,
                                  all_waves, entries_allowed: bool = True) -> None:
    """
    LIVE ekvivalent backtest engine `_place_counter_position_pending`.

    SL = ladder z velikosti PREDCHOZI stejnosmerne vlny v trendu (info.prev_same_dir_in_trend_wave_time).
    Lot = calc_lot(tp_price, counter_sl, cfg). Counter dir = -trend_dir.
    """
    if not entries_allowed:
        _log_adx14_entry_blocked(cfg, entry_type="COUNTER_POSITION")
        return
    prev_wt = info.prev_same_dir_in_trend_wave_time
    if not prev_wt:
        log.info("Counter-position: zadna predchozi stejnosmerna vlna — preskakuji")
        return
    prev_wave = next((w for w in all_waves if w["wave_time"] == prev_wt), None)
    if prev_wave is None:
        log.info("Counter-position: predchozi vlna nenalezena v `waves` — preskakuji")
        return

    counter_dir = -int(trend_dir)
    is_buy_counter = (counter_dir == 1)
    prev_size_pct = float(prev_wave.get("move_pct", 0.0))
    sl_pct, counter_sl = compute_ladder_sl_from_wave_size(
        tp_price,
        prev_size_pct,
        cfg,
        is_buy=is_buy_counter,
        min_sl_pct=wave_counter_min_sl_pct(cfg),
    )
    if sl_pct <= 0.0:
        log.info(f"Counter-position: sl_pct={sl_pct} <= 0 — preskakuji")
        return

    lot = calc_lot(tp_price, counter_sl, cfg)
    if lot <= 0.0:
        log.info(f"Counter-position: lot={lot} <= 0 — preskakuji")
        return

    info_symbol = mt5.symbol_info(cfg.symbol)
    digits = int(getattr(info_symbol, "digits", 5)) if info_symbol else 5

    log_event(
        cfg, "info", "COUNTER_POSITION_TRIGGERED",
        tp_wave_time=str(wave["wave_time"]),
        prev_same_dir_wave_time=str(prev_wt),
        prev_size_pct=float(prev_size_pct),
        sl_pct=float(sl_pct),
        counter_dir=int(counter_dir),
        tp_price=float(tp_price),
        counter_sl=float(counter_sl),
        lot=float(lot),
    )
    counter_tp = compute_wave_counter_take_profit(
        cfg, float(tp_price), float(counter_sl), is_buy=is_buy_counter
    )
    place_counter_position_pending(
        cfg,
        wave_time=str(wave["wave_time"]),
        counter_dir=counter_dir,
        tp_price=float(tp_price),
        counter_sl=float(counter_sl),
        lot=float(lot),
        digits=digits,
        tp=(None if counter_tp is None else float(counter_tp)),
    )


def _g_extension_hit_closed_positions(ext_stats: dict) -> bool:
    """Backtest parita: counter z G jen kdyz extension hit neco zavrel (TP nebo SL)."""
    return (
        int(ext_stats.get("trend_dir_closed", 0)) > 0
        or int(ext_stats.get("wave_counter_closed", 0)) > 0
        or int(ext_stats.get("two_sided_closed", 0)) > 0
        or int(ext_stats.get("sl_protected", 0)) > 0
    )


def _place_live_counter_from_g_extension(
    *,
    cfg: BotConfig,
    watch: FormingTpWatch,
    entries_allowed: bool = True,
) -> None:
    """G varianta: MARKET counter na armed_tp ve stejnem cyklu jako TP_EXTENSION_HIT."""
    if watch.counter_placed:
        return
    if not wave_counter_entry_allowed(cfg):
        return
    if watch.armed_tp is None:
        return
    if not entries_allowed:
        _log_adx14_entry_blocked(cfg, entry_type="COUNTER_POSITION")
        return

    prev_wave = watch.prev_wave
    tp_price = float(watch.armed_tp)
    setup = compute_wave_counter_sl_setup(
        cfg,
        trend_dir=int(watch.trend_dir),
        tp_price=tp_price,
        prev_wave=prev_wave,
    )
    if setup is None:
        return
    counter_dir, sl_pct, counter_sl, counter_tp = setup
    lot = calc_lot(tp_price, counter_sl, cfg)
    if lot <= 0.0:
        return

    wave_time_key = g_counter_wave_time(watch)
    info_symbol = mt5.symbol_info(cfg.symbol)
    digits = int(getattr(info_symbol, "digits", 5)) if info_symbol else 5

    log_event(
        cfg,
        "info",
        "COUNTER_G_EXTENSION_TRIGGERED",
        wave_time_key=str(wave_time_key),
        prev_wave_time=str(prev_wave.get("wave_time", "")),
        target_tp_index=int(watch.target_tp_index),
        sl_pct=float(sl_pct),
        counter_dir=int(counter_dir),
        armed_tp=float(tp_price),
        counter_sl=float(counter_sl),
        lot=float(lot),
    )
    ok = place_counter_position_market(
        cfg,
        wave_time=str(wave_time_key),
        counter_dir=int(counter_dir),
        counter_sl=float(counter_sl),
        lot=float(lot),
        digits=digits,
        tp=(None if counter_tp is None else float(counter_tp)),
        reference_ep=float(tp_price),
    )
    if ok:
        watch.counter_placed = True
        watch.counter_wave_time_key = str(wave_time_key)
        enforce_counter_positions_min_sl(
            cfg, min_sl_pct=wave_counter_min_sl_pct(cfg),
        )


def _try_live_counter_only_on_wave(
    *,
    cfg: BotConfig,
    wave: dict,
    seq_info: Dict[str, Any],
    all_waves,
    entries_allowed: bool,
    sent_signals: Set[str],
    sig_key: str,
) -> bool:
    """
    Parita engine: wave_position_enabled=False — bez primarniho WAVE vstupu,
    counter jen na TP-vlnach (pokud wave_counter_two_sided_orders_enabled).
    """
    if bool(getattr(cfg, "wave_position_enabled", True)):
        return False
    sent_signals.add(sig_key)
    if not wave_counter_two_sided_orders_enabled(cfg):
        return True
    if not entries_allowed:
        _log_adx14_entry_blocked(
            cfg,
            entry_type="COUNTER_ONLY",
            wave_id=str(wave.get("wave_time", "")),
        )
        return True
    ep = float(wave["fib50"])
    sl = float(wave["sl"])
    is_buy = int(wave["dir"]) == 1
    tp = resolve_effective_tp(cfg, wave, ep, sl, is_buy=is_buy)
    _maybe_place_live_counter_from_tp(
        cfg=cfg,
        wave=wave,
        seq_info=seq_info,
        tp_price=tp,
        all_waves=all_waves,
        entries_allowed=entries_allowed,
    )
    log_event(
        cfg,
        "info",
        "WAVE_COUNTER_ONLY_PROCESSED",
        wave_id=str(wave.get("wave_time", "")),
    )
    return True


def _maybe_place_live_counter_from_tp(
    *,
    cfg: BotConfig,
    wave: dict,
    seq_info: Dict[str, Any],
    tp_price: float | None,
    all_waves,
    entries_allowed: bool = True,
) -> None:
    """
    Zaloz counter pending pri WAVE vstupu.

    Counter jen na TP-vlne (N, N+2, ...) — vsechny tp_mode.
    WAVE_TARGET_N: extension TP; ostatni: TP z resolve_effective_tp (RRR).
    wave_target_n_g: skip z WAVE entry (counter na extension hit nebo fallback birth).
    """
    if is_wave_target_n_g(cfg):
        return
    if bool(wave.get("post_ext_trend_suppressed", False)):
        log_event(
            cfg,
            "info",
            "COUNTER_SKIPPED_POST_EXT_SUPPRESSED",
            wave_id=str(wave.get("wave_time", "")),
        )
        return
    if getattr(cfg, "ext_post_confirmed_trend_lock_blocks_both_sides", True) and wave.get("post_ext_confirmed_trend_lock", False):
        log_event(cfg, "info", "POST_EXT_CONFIRMED_LOCK_SKIP",
                  wave_id=str(wave.get("wave_time", "")),
                  confirmed_dir=wave.get("post_ext_confirmed_trend_dir"),
                  reason="lock_blocks_both_sides")
        return
    if not wave_counter_two_sided_orders_enabled(cfg):
        return
    info = seq_info.get(wave["wave_time"])
    if info is None:
        log.info(
            f"Counter-position: chybi sequence info pro wave {wave.get('wave_time')} — preskakuji"
        )
        return

    target_n = int(getattr(cfg, "tp_target_wave_index", 0) or 0)
    idx = info.index_in_trend if info else None
    if idx is None:
        return
    if target_n <= 0 or not is_tp_wave_index(idx, target_n):
        return

    if is_wave_target_n_family(cfg):
        raw_tp = wave.get("wave_target_tp_price")
        if raw_tp is not None:
            tp_price = float(raw_tp)
        else:
            prev_w = find_wave_by_time(
                all_waves, info.prev_same_dir_in_trend_wave_time
            )
            tp_price = compute_wave_target_tp_price(wave, prev_w, cfg)

    if tp_price is None:
        return
    _place_live_counter_position(
        cfg=cfg,
        wave=wave,
        info=info,
        trend_dir=int(wave["dir"]),
        tp_price=float(tp_price),
        all_waves=all_waves,
        entries_allowed=entries_allowed,
    )


def run_live_loop(cfg: BotConfig, sent_signals: Set[str], *, json_log_file: str | None = None) -> None:
    from runtime.live_wave_isolation import (
        log_live_execution_mode,
        resolve_live_execution_config,
    )

    cfg = resolve_live_execution_config(cfg)
    log_live_execution_mode(cfg)
    # Pri startu bota odpalime STATUS hned (timer nastaven do minulosti
    # tak, aby prvni kontrola v loopu hned vystrelila log).
    def _status_init_last(hours: float) -> datetime:
        if hours <= 0:
            return datetime.now(timezone.utc)
        return datetime.now(timezone.utc) - timedelta(hours=hours)

    last_status_text_time = _status_init_last(cfg.status_log_text_hours)
    last_status_jsonl_time = _status_init_last(cfg.status_log_jsonl_hours)
    # Text: první souhrn až po první periodě (ne hned při startu). JSONL: jako STATUS — první řádek brzy.
    last_old_waves_text_time = datetime.now(timezone.utc)
    last_old_waves_jsonl_time = _status_init_last(cfg.old_waves_log_jsonl_hours)
    old_waves_since_last_text = 0
    skipped_session_since_last_text = 0
    old_waves_since_last_jsonl = 0
    skipped_session_since_last_jsonl = 0
    # Trend filter (BOS) — pocty preskoceni za periodu OLD_WAVES_SUMMARY.
    # Inkrementuje se kdyz `cfg.trend_filter_enabled=True` a vlna neprosla
    # filtrem (smer trendu nebo HH/HL — viz strategy/trend_bos.py).
    skipped_trend_filter_since_last_text = 0
    skipped_trend_filter_since_last_jsonl = 0
    # Trade tracker state - drzi snapshot orderu/pozic mezi cykly
    tracker_state = TradeTrackerState()
    adx14_runtime = Adx14LiveRuntime(cfg)
    # Heartbeat — pravidelny puls pro Adamuv monitoring (NO_HEARTBEAT alert > 30 min)
    bot_start_ts = time.time()
    heartbeat_interval_sec = max(1, int(getattr(cfg, "heartbeat_interval_sec", 180)))
    last_heartbeat_time = datetime.now(timezone.utc) - timedelta(seconds=heartbeat_interval_sec)  # at hned vystreli prvni

    # Session manager state
    pre_close_done_for_today: bool = False
    last_pre_close_date = None
    was_outside_session: bool = False  # detekce wake-up (mimo session -> v session)
    # Signaly, ktere nesly odeslat (typicky transient MT5 chyba), drzi se pro replay.
    failed_signals: Dict[str, Dict[str, Any]] = {}
    retro_bos_attempted: Set[str] = set()
    promoted_two_sided_wave_times: Set[str] = set()
    # Posledni EXT vlna cekajici na prvni opacnou (WAVE SL na ext_low/ext_high).
    ext_sl_anchor: Optional[dict] = None
    was_mt5_connected: bool = True

    # TREND state cross-cycle: pamatujeme si posledni nezavinene "non-neutral"
    # smer trendu, abychom detekovali BOS flip (bull → bear / bear → bull).
    # Pri startu None → prvni cyklus jen inicializuje, neflipuje.
    last_known_trend_dir: str | None = None  # "bull" / "bear" / None
    # Cas posledniho close baru z predchoziho cyklu (close-BOS flip jen na novejsich barech).
    prev_cycle_last_bar_time: Optional[datetime] = None
    # Posledni uzavreny bar, na kterem uz probehla strategie (5s polling preskoci duplicitu).
    last_processed_closed_bar_time: Optional[pd.Timestamp] = None

    # 2B STRANGLER (VARIANTA A.txt §5.2): feature flag live_use_process_bar.
    # Default OFF → tato session se NIKDY nevytvoří a live_loop běží jako dnes.
    # True → rozhodování deleguje na LiveEngineSession.process_closed_bars
    # (jeden rozhodovač = BacktestEngine.process_bar). Vytvoří se líně níže.
    live_engine_session = None  # type: ignore[var-annotated]

    # WAVE_TARGET_N — TP-vlny W(N) uz zpracovane; forming_tp_watch pro G.
    # Po restartu / kazdem cyklu sync z historickych vln + MT5 CNTR_ (parita backtest).
    processed_tp_wave_times: Set[str] = set()
    forming_tp_watch: Optional[FormingTpWatch] = None
    from runtime.live_wave_stats import LiveWaveStatsTracker

    live_wave_stats = LiveWaveStatsTracker()
    last_live_wave_summary_closes = 0

    # PP wave_times, kterym uz PP order byl polozen (max 1x per vlna).
    # Po restartu se inicializuje z MT5 (comment prefix PP_).
    processed_pp_wave_times: Set[str] = set()
    pp_latest_wave_by_trend: Dict[str, str] = {}
    ext_runtime = ExtLiveRuntime()
    ext_runtime.sync_from_mt5(cfg)
    wf_runtime = WfLiveRuntime()

    def _emit_live_periodic_logs(
        *,
        old_waves_this_cycle: int = 0,
        skipped_session_this_cycle: int = 0,
        skipped_trend_filter_this_cycle: int = 0,
    ) -> None:
        nonlocal last_heartbeat_time
        nonlocal last_status_text_time, last_status_jsonl_time
        nonlocal last_old_waves_text_time, last_old_waves_jsonl_time
        nonlocal old_waves_since_last_text, skipped_session_since_last_text
        nonlocal old_waves_since_last_jsonl, skipped_session_since_last_jsonl
        nonlocal skipped_trend_filter_since_last_text, skipped_trend_filter_since_last_jsonl

        old_waves_since_last_text += old_waves_this_cycle
        skipped_session_since_last_text += skipped_session_this_cycle
        old_waves_since_last_jsonl += old_waves_this_cycle
        skipped_session_since_last_jsonl += skipped_session_this_cycle
        skipped_trend_filter_since_last_text += skipped_trend_filter_this_cycle
        skipped_trend_filter_since_last_jsonl += skipped_trend_filter_this_cycle

        now_local = get_broker_now(cfg)

        if now_local - last_heartbeat_time >= timedelta(seconds=heartbeat_interval_sec):
            log_event(
                cfg, "info", "HEARTBEAT",
                uptime_sec=int(time.time() - bot_start_ts),
            )
            last_heartbeat_time = now_local
            if json_log_file:
                maybe_prune_jsonl_runtime(
                    json_log_file,
                    getattr(cfg, "jsonl_retention_days", None),
                )
            try:
                from infra.telemetry_sync import ensure_telemetry_sync_running

                ensure_telemetry_sync_running(log, cfg)
            except Exception:
                pass

        text_status_due = (
            cfg.status_log_text_hours > 0
            and now_local - last_status_text_time >= timedelta(hours=cfg.status_log_text_hours)
        )
        jsonl_status_due = (
            cfg.status_log_jsonl_hours > 0
            and now_local - last_status_jsonl_time >= timedelta(hours=cfg.status_log_jsonl_hours)
        )
        if text_status_due or jsonl_status_due:
            snap = get_account_snapshot(cfg)

            if snap.valid:
                status_kwargs = dict(
                    balance=float(snap.balance),
                    equity=float(snap.equity),
                    profit_total=float(snap.profit_total),
                    profit_bot=float(snap.profit_bot),
                    margin=float(snap.margin),
                    margin_free=float(snap.margin_free),
                    margin_level=float(snap.margin_level) if snap.margin > 0 else None,
                    open_positions=int(snap.open_positions),
                    pending_orders=int(snap.pending_orders),
                    currency=str(snap.currency),
                )
                if text_status_due:
                    log_event(
                        cfg,
                        "info",
                        "STATUS",
                        log_targets=LOG_TARGETS_TEXT_SINKS,
                        **status_kwargs,
                    )
                    last_status_text_time = now_local
                if jsonl_status_due:
                    log_event(
                        cfg,
                        "info",
                        "STATUS",
                        log_targets=LOG_TARGETS_JSONL_ONLY,
                        **status_kwargs,
                    )
                    last_status_jsonl_time = now_local
            else:
                log_event(
                    cfg,
                    "warning",
                    "LOG",
                    message="STATUS preskocen: MT5 account_info() vratilo None",
                    logger="runtime.live_loop",
                )
                if text_status_due:
                    last_status_text_time = now_local
                if jsonl_status_due:
                    last_status_jsonl_time = now_local

        text_old_waves_due = (
            cfg.old_waves_log_text_hours > 0
            and now_local - last_old_waves_text_time >= timedelta(hours=cfg.old_waves_log_text_hours)
        )
        jsonl_old_waves_due = (
            cfg.old_waves_log_jsonl_hours > 0
            and now_local - last_old_waves_jsonl_time >= timedelta(hours=cfg.old_waves_log_jsonl_hours)
        )
        if text_old_waves_due or jsonl_old_waves_due:
            summary_kwargs = dict(
                max_wave_age_hours=cfg.max_wave_age_hours,
                trend_filter_enabled=bool(cfg.trend_filter_enabled),
                trend_hh_hl_filter_enabled=bool(cfg.trend_hh_hl_filter_enabled),
            )
            if text_old_waves_due:
                log_event(
                    cfg,
                    "info",
                    "OLD_WAVES_SUMMARY",
                    log_targets=LOG_TARGETS_TEXT_SINKS,
                    skipped_old_waves=old_waves_since_last_text,
                    skipped_session_waves=skipped_session_since_last_text,
                    skipped_trend_filter_waves=skipped_trend_filter_since_last_text,
                    **summary_kwargs,
                )
                old_waves_since_last_text = 0
                skipped_session_since_last_text = 0
                skipped_trend_filter_since_last_text = 0
                last_old_waves_text_time = now_local
            if jsonl_old_waves_due:
                log_event(
                    cfg,
                    "info",
                    "OLD_WAVES_SUMMARY",
                    log_targets=LOG_TARGETS_JSONL_ONLY,
                    skipped_old_waves=old_waves_since_last_jsonl,
                    skipped_session_waves=skipped_session_since_last_jsonl,
                    skipped_trend_filter_waves=skipped_trend_filter_since_last_jsonl,
                    **summary_kwargs,
                )
                old_waves_since_last_jsonl = 0
                skipped_session_since_last_jsonl = 0
                skipped_trend_filter_since_last_jsonl = 0
                last_old_waves_jsonl_time = now_local

    while True:
        try:
            broker_now = get_broker_now(cfg)

            # ───── SESSION MANAGER ─────────────────────────
            if is_session_enabled(cfg):
                session_now = get_session_now(cfg)
                in_session = is_in_session(cfg, session_now)

                # 1) Pre-close buffer - zrusit pendingy (jen jednou za den)
                if is_pre_close_buffer(cfg, session_now):
                    today_key = session_now.date()
                    if last_pre_close_date != today_key:
                        log_event(
                            cfg,
                            "info",
                            "SESSION_PRE_CLOSE",
                            time=session_now.strftime("%H:%M:%S"),
                            buffer_min=cfg.session_pre_close_buffer_min,
                            session_timezone=getattr(cfg, "session_timezone", "broker"),
                        )
                        from infra.pending_snapshot import (
                            capture_pending_snapshot,
                            save_pending_snapshot,
                        )
                        save_pending_snapshot(cfg, capture_pending_snapshot(cfg))
                        cancel_all_pendings(cfg)

                        # V patek volitelne zavreme i pozice
                        if (cfg.session_close_positions_on_friday
                                and is_week_close_pre_buffer(cfg, session_now)):
                            log_event(cfg, "info", "SESSION_WEEK_CLOSE_POSITIONS")
                            close_all_positions(cfg)

                        last_pre_close_date = today_key

                # 2) Mimo session - usnout do open
                if not in_session:
                    if not was_outside_session:
                        secs = seconds_until_open(cfg, session_now)
                        wake_at = session_now + timedelta(seconds=secs)
                        log_event(
                            cfg,
                            "info",
                            "SESSION_SLEEP",
                            now=session_now.strftime("%Y-%m-%d %H:%M:%S"),
                            wake_at=wake_at.strftime("%Y-%m-%d %H:%M:%S"),
                            seconds_to_wake=int(secs),
                            session_timezone=getattr(cfg, "session_timezone", "broker"),
                        )
                        was_outside_session = True

                    # Chytry sleep: spi az do open (max 60s pro pripad ze user
                    # zmeni system clock nebo neco)
                    secs = seconds_until_open(cfg, session_now)
                    sleep_for = min(60.0, max(1.0, secs))
                    time.sleep(sleep_for)
                    continue

                # 3) V session - pokud jsme byli mimo, probudili jsme se
                if was_outside_session:
                    log_event(
                        cfg,
                        "info",
                        "SESSION_WAKE_UP",
                        time=session_now.strftime("%Y-%m-%d %H:%M:%S"),
                        session_timezone=getattr(cfg, "session_timezone", "broker"),
                    )
                    was_outside_session = False
                    # Po wake-up spustime startup recovery, abychom obnovili
                    # vlny ktere vznikly behem spanku
                    try:
                        from runtime.startup import run_full_startup_recovery

                        sent_signals = run_full_startup_recovery(
                            cfg,
                            sent_signals,
                            failed_signals=failed_signals,
                            recovery_reason="session_wake_up",
                        )
                        from runtime.wave_target_n_live import reset_wave_target_n_runtime_state

                        processed_tp_wave_times, forming_tp_watch = (
                            reset_wave_target_n_runtime_state()
                        )
                        ext_runtime.sync_from_mt5(cfg)
                        wf_runtime.reset()
                        log.info(
                            f"SESSION WAKE-UP RECOVERY: dohromady {len(sent_signals)} signalu"
                        )
                    except Exception as e:
                        log.error(f"SESSION WAKE-UP RECOVERY: chyba {e}", exc_info=True)

            # ───── HLAVNI LOOP ─────────────────────────────
            # Trade tracker - detekce ORDER_FILLED, POSITION_OPENED/CLOSED, MT5 connection
            update_trade_tracker(
                cfg,
                tracker_state,
                adx14_runtime=adx14_runtime,
                live_wave_stats=live_wave_stats,
                promoted_two_sided_wave_times=promoted_two_sided_wave_times,
            )
            from runtime.live_wave_stats import maybe_emit_live_wave_summary

            last_live_wave_summary_closes = maybe_emit_live_wave_summary(
                cfg,
                live_wave_stats,
                last_emit_wave_closes=last_live_wave_summary_closes,
            )
            enforce_counter_positions_min_sl(
                cfg, min_sl_pct=wave_counter_min_sl_pct(cfg)
            )
            enforce_live_position_cap(cfg)

            # EQUITY TARGET STOP (prop-firm challenge mode)
            if cfg.equity_target_usd is not None:
                snap = get_account_snapshot(cfg)
                if snap.valid and snap.equity >= float(cfg.equity_target_usd):
                    log_event(
                        cfg,
                        "warning",
                        "EQUITY_TARGET_REACHED",
                        equity=float(snap.equity),
                        equity_target_usd=float(cfg.equity_target_usd),
                        balance=float(snap.balance),
                        open_positions=int(snap.open_positions),
                        pending_orders=int(snap.pending_orders),
                    )
                    cancelled = cancel_all_pendings(cfg)
                    closed = close_all_positions(cfg)
                    log_event(
                        cfg,
                        "warning",
                        "EQUITY_TARGET_STOP_DONE",
                        equity=float(snap.equity),
                        equity_target_usd=float(cfg.equity_target_usd),
                        cancelled_pending=int(cancelled),
                        closed_positions=int(closed),
                    )
                    log.info(
                        "EQUITY TARGET STOP: target dosažen, bot ukoncuje live loop."
                    )
                    return

            # Cancel expirovanych pendingu
            cancel_expired_pending(cfg)

            df = load_bars(cfg, source="mt5", n=cfg.startup_bars, include_forming=False)
            if df is None or df.empty:
                log.warning("Nepodarilo se nacist data, zkousim znovu...")
                time.sleep(cfg.sleep_sec)
                continue

            if adx14_runtime.active and adx14_runtime.needs_history_bars():
                adx14_df = load_bars(
                    cfg,
                    source="mt5",
                    n=int(getattr(cfg, "adx14_history_bars", 5000)),
                    include_forming=False,
                )
                adx14_runtime.update(adx14_df if adx14_df is not None else df, broker_now)
            elif adx14_runtime.active:
                adx14_runtime.update(df, broker_now)

            entries_allowed = adx14_runtime.entries_allowed

            # load_bars(include_forming=False) = jen uzavrene bary; posledni radek = posledni close.
            closed_bar_ts = pd.Timestamp(df["time"].iloc[-1])
            if (
                last_processed_closed_bar_time is not None
                and closed_bar_ts <= last_processed_closed_bar_time
            ):
                _emit_live_periodic_logs()
                continue

            from runtime.missed_bar_replay import new_closed_bar_indices

            new_bar_indices = new_closed_bar_indices(
                df, last_processed_closed_bar_time,
            )
            if not new_bar_indices:
                _emit_live_periodic_logs()
                continue

            if len(new_bar_indices) > 1:
                log_event(
                    cfg,
                    "info",
                    "MISSED_BARS_CATCH_UP",
                    missed_bars=int(len(new_bar_indices) - 1),
                    first_bar_idx=int(new_bar_indices[0]),
                    last_bar_idx=int(new_bar_indices[-1]),
                )

            last_processed_closed_bar_time = closed_bar_ts

            # ───── 2B STRANGLER: live volá process_bar() (feature flag) ─────
            # Default OFF → tento blok se přeskočí a běží dnešní rozhodování níže
            # (detect_waves + send_order bloky se NEMAŽÍ — to je 2B-cleanup/2G).
            # ON → rozhodování deleguje na engine.process_bar přes LiveEngineSession.
            # Live-only kontrakt zůstává v orchestraci: forming-bar strip
            # (_df_closed_bars_only výše), session pre-close cancel, cancel_expired_pending,
            # guard/dedup (LiveExecutor), recovery (startup.py), TZ align (session_manager).
            if bool(getattr(cfg, "live_use_process_bar", False)):
                from runtime.live_engine_session import LiveEngineSession

                # Cold start / reset každý cyklus nad aktuálním closed df; pak
                # process_bar přes nové closed bary (catch-up = N× process_bar).
                # MISSED_BARS_CATCH_UP už zalogován výše (new_bar_indices).
                # TODO(2F): persistentní ctx + per-bar advance pro growing-df live
                # cestu a reconciliation engine stavu vs MT5 (broker fills).
                live_engine_session = LiveEngineSession(cfg, df)
                live_engine_session.process_closed_bars(df, new_bar_indices)
                _emit_live_periodic_logs()
                continue

            waves = detect_waves(df, cfg)
            if not waves:
                # POZN.: I bez vln muze byt aktivni BOS_EXIT (kdyz mam otevrene pozice
                # a trend se mezi tim flipl). Pri zadne vlne ale nemame swing levels,
                # takze trend stav by byl 'neutral' → nic se nezavre. Bezpecne preskocit.
                _emit_live_periodic_logs()
                continue

            from strategy.wave_detection_pine import compute_wave_birth_bars_pine

            _wave_birth_for_wf = compute_wave_birth_bars_pine(df, cfg)

            # ENGINE PARITA (per-bar trend): engine pocita trend_states_per_bar JEDNOU nad
            # detect mnozinou PRED WF merge (engine.py _recompute_bos_state) a uz ji po WF
            # NEaktualizuje. Snapshot PRED WF + ext_range tagy = shodny trend zdroj jako
            # engine; jinak wf_continued vlny (napr. 202603051030) blokuje fill-bar re-check
            # kvuli post-WF zmene smeru trendu. Snapshot je kauzalni (jen data <= aktualni bar).
            from strategy.ext_range import (
                ext_range_enabled as _ext_enabled_pre,
                reapply_ext_range_tags as _reapply_ext_pre,
            )
            _pre_wf_waves = [dict(w) for w in waves]
            if _ext_enabled_pre(cfg):
                _reapply_ext_pre(
                    _pre_wf_waves, cfg, df=df, wave_birth=dict(_wave_birth_for_wf)
                )

            wf_prep = wf_runtime.process(
                df,
                cfg,
                waves,
                wave_birth_by_time=_wave_birth_for_wf,
            )
            if wf_prep.ext_skipped and wf_prep.eval_result is not None:
                log_event(
                    cfg,
                    "info",
                    "WF_SKIPPED_EXT",
                    wave_id=str(waves[-1].get("wave_time", "?")),
                    reason="ext_active",
                )

            # TREND FILTER (BOS) — pripravime snapshot trendu pro kazdou vlnu.
            # Pocitame ho take pri zapnutem TWO-SIDED (parent A v trend-direction,
            # counter B counter-trend; viz strategy/two_sided.py).
            # `trend_states_per_wave` = dict {wave_time: TrendState v okamziku
            # narozeni vlny}. Filter se aplikuje pozdeji v iteraci vln.
            if cfg.trend_filter_enabled or two_sided_enabled(cfg):
                trend_states_per_wave = compute_trend_states_per_wave(df, waves, cfg)
            else:
                trend_states_per_wave = {}

            # BOS vlna — po finalnich EXT tagach a wave_sequence (viz engine).
            seq_info, protected_waves = sync_wave_sequence_state(df, waves, cfg)

            from strategy.ext_range import ext_range_enabled, reapply_ext_range_tags
            from strategy.trend_bos import (
                _detect_close_bos_timeline_flips,
                reconcile_bos_flip_map_with_wave_sequence,
            )

            _wave_birth_by_time = _wave_birth_for_wf
            if ext_range_enabled(cfg):
                reapply_ext_range_tags(
                    waves, cfg, df=df, wave_birth=_wave_birth_by_time
                )
                seq_info, protected_waves = sync_wave_sequence_state(df, waves, cfg)

            _bos_flip_map_live: dict[int, str] = {}
            if cfg.trend_filter_enabled:
                _flips = _detect_close_bos_timeline_flips(
                    df, waves, cfg, wave_birth_bars=_wave_birth_by_time
                )
                _bos_flip_map_live = reconcile_bos_flip_map_with_wave_sequence(
                    compute_bos_wave_flip_map(
                        df, waves, cfg, wave_birth_bars=_wave_birth_by_time
                    ),
                    _flips,
                    waves,
                    seq_info,
                    _wave_birth_by_time,
                )
                bos_wave_times = set(_bos_flip_map_live.values())
            else:
                bos_wave_times = set()

            ext_runtime.refresh_simulation(
                df, cfg, seq_info=seq_info, protected_waves=protected_waves, waves=waves,
            )
            ext_runtime.run_ext1_rrr_better_exit(cfg, df)
            last_bar_idx = new_bar_indices[-1]
            last_bar_time = pd.Timestamp(df["time"].iloc[last_bar_idx]).to_pydatetime()
            bar_entry_close = float(df.iloc[last_bar_idx]["close"])
            ext1_per_bar = ext_runtime._ext1_protection_per_bar

            if two_sided_enabled(cfg):
                global _live_two_sided_tracker
                replay_two_sided_tracker_engine_parity(
                    _live_two_sided_tracker,
                    df,
                    waves,
                    cfg,
                    wave_birth_by_time=_wave_birth_by_time,
                    trend_states_per_wave=trend_states_per_wave,
                )

            # ───── TP MODE: BOS_EXIT / BOS_EXIT_PRIORITY / WAVE_TARGET_N ─────
            # Pri techto rezimech zavreme vsechny otevrene pozice toho smeru,
            # ktery aktualne neni trendem (po BOS flipu): bull → BUY zustavaji
            # otevrene, SELL by se zavrely; bear obracene.
            # Hodnotime per-bar trend timeline z nactenych dat — stav v `[-1]`
            # je aktualni "do chvile posledniho close baru".
            bar_trend_states = None
            current_trend = "neutral"
            from config.enums import PendingCancelMode as _PCM2
            _pcm_pre = getattr(cfg, "pending_cancel_mode", _PCM2.NUMBER)
            try:
                _pcm_pre = _PCM2(_pcm_pre) if isinstance(_pcm_pre, str) else _pcm_pre
            except ValueError:
                _pcm_pre = _PCM2.NUMBER
            need_bar_trend = (
                tp_mode_uses_bos_per_bar_exit(cfg)
                or _pcm_pre == _PCM2.TREND
                or cfg.trend_filter_enabled
                or bos_entry_in_rrr_fixed_enabled(cfg)
            )
            if need_bar_trend:
                bar_trend_states = compute_trend_states_per_bar(df, _pre_wf_waves, cfg)
                current_trend = bar_trend_states[last_bar_idx].direction if bar_trend_states else "neutral"
            fill_trend_state = (
                bar_trend_states[last_bar_idx]
                if bar_trend_states and last_bar_idx < len(bar_trend_states)
                else None
            )
            if cfg.trend_filter_enabled and fill_trend_state is not None:
                try:
                    cancel_counter_trend_wave_pendings(cfg, fill_trend_state, waves)
                except Exception as e:
                    log.error(f"TREND_FILL_GUARD cancel selhal: {e}", exc_info=True)

            from config.enums import PendingCancelMode as _PCM
            pcm_raw = getattr(cfg, "pending_cancel_mode", _PCM.NUMBER)
            try:
                pcm = _PCM(pcm_raw) if isinstance(pcm_raw, str) else pcm_raw
            except ValueError:
                pcm = _PCM.NUMBER

            wf_activation_queue = wf_runtime.pop_activation_results()
            if wf_prep.wf_wave is not None:
                wf_activation_queue.append(wf_prep)

            symbol_info = mt5.symbol_info(cfg.symbol)
            signal_digits = int(getattr(symbol_info, "digits", 4)) if symbol_info else 4
            new_signal_sent = False

            if len(new_bar_indices) > 1:
                from runtime.missed_bar_replay import (
                    MissedBarReplayState,
                    replay_missed_closed_bar,
                )

                _replay_state = MissedBarReplayState(
                    last_known_trend_dir=last_known_trend_dir,
                    prev_cycle_last_bar_time=prev_cycle_last_bar_time,
                    processed_tp_wave_times=processed_tp_wave_times,
                    forming_tp_watch=forming_tp_watch,
                    ext_sl_anchor=ext_sl_anchor,
                    retro_bos_attempted=retro_bos_attempted,
                    promoted_two_sided_wave_times=promoted_two_sided_wave_times,
                )
                for _missed_idx in new_bar_indices[:-1]:
                    _replay_state = replay_missed_closed_bar(
                        cfg=cfg,
                        df=df,
                        waves=waves,
                        bar_idx=_missed_idx,
                        state=_replay_state,
                        bar_trend_states=bar_trend_states,
                        seq_info=seq_info,
                        protected_waves=protected_waves,
                        bos_flip_map=_bos_flip_map_live,
                        bos_wave_times=bos_wave_times,
                        trend_states_per_wave=trend_states_per_wave,
                        ext1_per_bar=ext1_per_bar,
                        ext_runtime=ext_runtime,
                        wf_activations=wf_activation_queue,
                        sent_signals=sent_signals,
                        failed_signals=failed_signals,
                        signal_digits=signal_digits,
                        entries_allowed=entries_allowed,
                        wave_birth_by_time=_wave_birth_by_time,
                        active_counter_wave_times=get_active_counter_wave_times(cfg),
                        pcm=pcm,
                        place_live_bos_reentry=_place_live_bos_reentry,
                        place_live_counter_from_g_extension=_place_live_counter_from_g_extension,
                        g_extension_hit_closed_positions=_g_extension_hit_closed_positions,
                        place_live_counter_position=_place_live_counter_position,
                        log_event_fn=log_event,
                        two_sided_tracker=_live_two_sided_tracker,
                        get_open_comments=lambda: [snap.get("comment", "") for snap in tracker_state.known_positions.values()],
                    )
                last_known_trend_dir = _replay_state.last_known_trend_dir
                prev_cycle_last_bar_time = _replay_state.prev_cycle_last_bar_time
                processed_tp_wave_times = _replay_state.processed_tp_wave_times
                forming_tp_watch = _replay_state.forming_tp_watch
                ext_sl_anchor = _replay_state.ext_sl_anchor
                promoted_two_sided_wave_times = _replay_state.promoted_two_sided_wave_times

            for _wf_act in wf_activation_queue:
                if _wf_act.wf_wave is None:
                    continue
                if (
                    _wf_act.activation_bar_idx is not None
                    and int(_wf_act.activation_bar_idx) != int(last_bar_idx)
                ):
                    continue
                try:
                    _wf_wave = _wf_act.wf_wave
                    _wf_result = _wf_act.eval_result or {}
                    _wf_wt_str = str(_wf_wave.get("wave_time", ""))
                    _wf_sig_key = get_signal_key(_wf_wave, digits=signal_digits)
                    if _wf_sig_key not in sent_signals:
                        _wf_origin = _wf_result.get("last_wave") or waves[-1]
                        log_event(
                            cfg,
                            "info",
                            "WF_ACTIVATED",
                            wave_id=_wf_wt_str,
                            w_dir=(
                                "down"
                                if int(_wf_origin.get("dir", 0)) == -1
                                else "up"
                            ),
                            last_wave_high=float(_wf_origin.get("box_top", 0.0)),
                            last_wave_low=float(_wf_origin.get("box_bottom", 0.0)),
                            fakeout_pivot=float(_wf_result.get("fakeout_pivot", 0.0)),
                            activation_close=float(bar_entry_close),
                            window_size_bars=int(_wf_result.get("window_size", 0)),
                        )
                        if send_order(
                            _wf_wave,
                            cfg,
                            entry_mode=cfg.entry_mode,
                            trend_state_at_fill=fill_trend_state,
                            bar_close=bar_entry_close,
                        ):
                            sent_signals.add(_wf_sig_key)
                            new_signal_sent = True
                    if _wf_act.resumed_count:
                        log_event(
                            cfg,
                            "info",
                            "WF_CLASSIC_WAVES_RESUMED",
                            wf_wave_id=_wf_wt_str,
                            resumed=_wf_act.resumed_count,
                        )
                except Exception as _wf_exc:
                    log.error("WF live aktivace selhala: %s", _wf_exc, exc_info=True)

            # Rozhodovaci tabulka pro BOS-driven actions:
            #   close_positions: zavre pozice ve smeru staré trendovky (ridi tp_mode)
            #   cancel_pendings: zrusi pendingy ve smeru staré trendovky
            #                    - "trend"  → vzdy
            #                    - "number" → nikdy (jen časová expirace)
            bos_active = tp_mode_uses_bos_per_bar_exit(cfg)
            do_close_pos = bos_active
            do_cancel_pend = pcm == _PCM.TREND

            _trend_dir_changed = (
                last_known_trend_dir in ("bull", "bear")
                and current_trend in ("bull", "bear")
                and current_trend != last_known_trend_dir
            )
            _close_bos_flip = None
            _bos_protect_wave_time: str | None = None
            if _trend_dir_changed:
                _close_bos_flip = find_close_bos_flip_for_target_since(
                    df,
                    waves,
                    cfg,
                    target_direction=current_trend,
                    after_time=prev_cycle_last_bar_time,
                )
            if bos_active and not df.empty:
                _flip_bar_ix = last_bar_idx
                if _close_bos_flip is not None:
                    _flip_bar_ix = int(_close_bos_flip[2])
                _bos_flip_map = (
                    _bos_flip_map_live
                    if _bos_flip_map_live
                    else compute_bos_wave_flip_map(df, waves, cfg)
                )
                _wt_bos = _bos_flip_map.get(int(_flip_bar_ix))
                if _wt_bos:
                    _bos_protect_wave_time = str(_wt_bos)

            _last_bar = df.iloc[last_bar_idx] if not df.empty else None
            _bar_high = float(_last_bar["high"]) if _last_bar is not None else None
            _bar_low = float(_last_bar["low"]) if _last_bar is not None else None
            _promoted_ts2 = (
                promoted_two_sided_wave_times
                if bool(getattr(cfg, "live_study_promoted_two_sided_as_wave", False))
                else None
            )

            if do_close_pos and _last_bar is not None:
                br = bos_per_bar_close_reason(cfg)
                _dir_kw = dict(
                    reason=br,
                    protected_wave_times=protected_waves,
                    promoted_two_sided_wave_times=_promoted_ts2,
                    protect_ext_block_from_wave=_bos_protect_wave_time,
                    ext1_protection_per_bar=ext1_per_bar,
                    current_bar_idx=last_bar_idx,
                    bar_high=_bar_high,
                    bar_low=_bar_low,
                    wave_birth_by_time=ext_runtime._wave_birth_by_time,
                    main_trend_dir=1 if current_trend == "bull" else -1 if current_trend == "bear" else 0,
                )
                if current_trend == "bear":
                    closed_buy = close_positions_by_direction(
                        cfg, direction=+1, **_dir_kw,
                    )
                    if closed_buy:
                        log_event(
                            cfg, "info", "BOS_EXIT_TRIGGERED",
                            trend="bear", closed_direction="BUY", closed_count=int(closed_buy),
                            tp_mode=str(cfg.tp_mode),
                        )
                elif current_trend == "bull":
                    closed_sell = close_positions_by_direction(
                        cfg, direction=-1, **_dir_kw,
                    )
                    if closed_sell:
                        log_event(
                            cfg, "info", "BOS_EXIT_TRIGGERED",
                            trend="bull", closed_direction="SELL", closed_count=int(closed_sell),
                            tp_mode=str(cfg.tp_mode),
                        )

            if (
                do_close_pos
                and _trend_dir_changed
                and _close_bos_flip is not None
                and _last_bar is not None
            ):
                br = bos_per_bar_close_reason(cfg)
                _flip_broken_dir = (
                    +1 if last_known_trend_dir == "bull" else -1
                )
                try:
                    n_flip_follow = close_flip_follower_positions_on_bos(
                        cfg,
                        broken_dir=_flip_broken_dir,
                        bar_high=_bar_high,
                        bar_low=_bar_low,
                        reason=br,
                        protected_wave_times=protected_waves,
                        promoted_two_sided_wave_times=_promoted_ts2,
                        ext1_protection_per_bar=ext1_per_bar,
                        current_bar_idx=last_bar_idx,
                        protect_ext_block_from_wave=_bos_protect_wave_time,
                        wave_birth_by_time=ext_runtime._wave_birth_by_time,
                        main_trend_dir=1 if current_trend == "bull" else -1 if current_trend == "bear" else 0,
                    )
                    if n_flip_follow:
                        log_event(
                            cfg, "info", "BOS_EXIT_FLIP_FOLLOWERS",
                            closed_count=int(n_flip_follow),
                            tp_mode=str(cfg.tp_mode),
                        )

                    # NOVE: promote two-sided mirror, ktery prezil close_flip_follower_positions_on_bos
                    from runtime.two_sided_promote_live import on_bos_flip_promote_two_sided
                    _comments = [snap.get("comment", "") for snap in tracker_state.known_positions.values()]
                    promoted_two_sided_wave_times = on_bos_flip_promote_two_sided(
                        flipped=True,
                        existing_promoted=promoted_two_sided_wave_times,
                        open_comments=_comments,
                        cfg=cfg,
                    )
                except Exception as e:
                    log.error(f"BOS close flip followers selhal: {e}", exc_info=True)

            # PENDING CANCELLATION pri BOS flipu (tp_mode / pending_cancel_mode).
            # Jen pri close-based flipu (ne seed-reset po EXT).
            if do_cancel_pend and _trend_dir_changed:
                if _close_bos_flip is not None:
                    _flip_time, _flip_label, _flip_bar = _close_bos_flip
                    broken_dir = +1 if last_known_trend_dir == "bull" else -1
                    try:
                        n_pend = cancel_pendings_by_direction(
                            cfg,
                            direction=broken_dir,
                            reason="BOS_CANCEL_PENDING",
                            waves=waves,
                        )
                        n_flip_pend = cancel_flip_follower_pendings_on_bos(cfg)
                        if n_pend or n_flip_pend:
                            log_event(
                                cfg, "info", "BOS_PENDING_CANCEL",
                                broken_dir="BUY" if broken_dir == 1 else "SELL",
                                cancelled=int(n_pend),
                                flip_followers_cancelled=int(n_flip_pend),
                                pending_cancel_mode=str(pcm.value),
                                bos_event_time=bos_flip_time_to_log_str(_flip_time),
                                bos_flip_label=str(_flip_label)[:72],
                                bos_flip_bar=int(_flip_bar),
                            )
                    except Exception as e:
                        log.error(f"BOS cancel pendings selhal: {e}", exc_info=True)
                else:
                    log_event(
                        cfg,
                        "info",
                        "BOS_TREND_CHANGE_WITHOUT_CLOSE_FLIP_SKIPPED",
                        action="pending_cancel",
                        from_trend=str(last_known_trend_dir),
                        to_trend=str(current_trend),
                        prev_cycle_last_bar_time=(
                            None
                            if prev_cycle_last_bar_time is None
                            else bos_flip_time_to_log_str(prev_cycle_last_bar_time)
                        ),
                    )

            # ───── BOS ENTRY MARKET (bos_entry_enable / bos_entry_in_rrr_fixed) ─────
            # Jen pri close-based flipu na novem baru (ne seed-reset po EXT).
            if (bos_entry_should_open_on_flip(cfg) and _trend_dir_changed):
                if getattr(cfg, "ext_post_confirmed_trend_lock_blocks_both_sides", True) and waves and waves[0].get("_live_post_ext_lock_active"):
                    log_event(cfg, "info", "POST_EXT_CONFIRMED_LOCK_SKIP",
                              wave_id="BOS_ENTRY",
                              confirmed_dir=waves[0].get("_live_post_ext_lock_dir"),
                              reason="lock_blocks_both_sides")
                elif _close_bos_flip is not None:
                    _flip_time, _flip_label, _flip_bar = _close_bos_flip
                    try:
                        log_event(
                            cfg,
                            "info",
                            "BOS_ENTRY_MARKET",
                            new_trend=str(current_trend),
                            broken_trend=str(last_known_trend_dir),
                            bos_event_time=bos_flip_time_to_log_str(_flip_time),
                            bos_flip_label=str(_flip_label)[:72],
                            bos_flip_bar=int(_flip_bar),
                        )
                        _place_live_bos_reentry(
                            cfg=cfg,
                            new_trend_dir=current_trend,
                            broken_trend_dir=last_known_trend_dir,
                            bar_trend_states=bar_trend_states,
                            waves=waves,
                            entries_allowed=entries_allowed,
                        )
                    except Exception as e:
                        log.error(f"BOS entry market selhal: {e}", exc_info=True)
                else:
                    log_event(
                        cfg,
                        "info",
                        "BOS_TREND_CHANGE_WITHOUT_CLOSE_FLIP_SKIPPED",
                        action="bos_entry_market",
                        from_trend=str(last_known_trend_dir),
                        to_trend=str(current_trend),
                        prev_cycle_last_bar_time=(
                            None
                            if prev_cycle_last_bar_time is None
                            else bos_flip_time_to_log_str(prev_cycle_last_bar_time)
                        ),
                    )

            # Wave sequence — uz spocteno vyse (po detect_waves).
            # WAVE_TARGET_N / G: jednotny bar cyklus (parita backtest + missed-bar replay).
            if is_wave_target_n_family(cfg):
                try:
                    from runtime.wave_target_n_bar import run_wave_target_n_bar_cycle

                    _tp_bar_result = run_wave_target_n_bar_cycle(
                        cfg=cfg,
                        df=df,
                        waves=waves,
                        seq_info=seq_info,
                        bar_idx=last_bar_idx,
                        birth_by_time=ext_runtime._wave_birth_by_time,
                        active_counter_wave_times=get_active_counter_wave_times(cfg),
                        processed_tp_wave_times=processed_tp_wave_times,
                        forming_tp_watch=forming_tp_watch,
                        ext1_per_bar=ext1_per_bar,
                        current_trend=current_trend,
                        entries_allowed=entries_allowed,
                        bar_high=float(_bar_high or 0.0),
                        bar_low=float(_bar_low or 0.0),
                        bar_close=(
                            float(_last_bar["close"])
                            if _last_bar is not None
                            else float(bar_entry_close)
                        ),
                        bar_open=(
                            float(_last_bar["open"])
                            if _last_bar is not None
                            else float(bar_entry_close)
                        ),
                        place_g_extension_counter=_place_live_counter_from_g_extension,
                        g_extension_closed=_g_extension_hit_closed_positions,
                        place_fallback_counter=_place_live_counter_position,
                        log_event_fn=log_event,
                    )
                    forming_tp_watch = _tp_bar_result.forming_tp_watch
                except Exception as e:
                    log.error(
                        "WAVE_TARGET_N bar cycle selhal: %s", e, exc_info=True,
                    )

            # Po vyhodnoceni BOS-exit / TP-wave eventu aktualizuj last_known_trend_dir
            # pro pristi cyklus (slouzi k detekci skutecneho flipu).
            if current_trend in ("bull", "bear"):
                last_known_trend_dir = current_trend
            if not df.empty:
                prev_cycle_last_bar_time = pd.Timestamp(
                    df["time"].iloc[last_bar_idx]
                ).to_pydatetime()

            # ───── PP BREAK DETEKCE (cfg.pp_enabled) ─────
            # Pokud je v aktualnim trendu nejnovejsi vlna ve smeru trendu, ktera
            # jeste nebyla PP-brokana, a aktualni close prekroci box_top/bot, polozi
            # PP LIMIT pending. Pri novem PP se stary PP pending (pokud existuje)
            # zrusi (max 1 PP pending najednou — uzivatelske pravidlo).
            if bool(getattr(cfg, "pp_enabled", False)):
                # PP vyzaduje stejny bar-by-bar snapshot jako BOS exit (vcetne seed
                # v case), ale samotny PP break musi byt potvrzen close-based BOS
                # (viz pp_trend_confirmed_by_close_bos v _maybe_fire_pp_break_event).
                if bar_trend_states is None:
                    bar_trend_states = compute_trend_states_per_bar(df, _pre_wf_waves, cfg)
                current_trend_local = (
                    bar_trend_states[last_bar_idx].direction
                    if bar_trend_states and last_bar_idx < len(bar_trend_states)
                    else "neutral"
                )

                if current_trend_local in ("bull", "bear"):
                    trend_dir_pp = 1 if current_trend_local == "bull" else -1
                    latest_pp_wt: str | None = None
                    for w in reversed(waves):
                        if int(w.get("dir", 0)) == trend_dir_pp:
                            latest_pp_wt = str(w.get("wave_time", ""))
                            break
                    if latest_pp_wt:
                        prev = pp_latest_wave_by_trend.get(current_trend_local)
                        if prev is not None and prev != latest_pp_wt:
                            try:
                                cancel_pp_pendings(cfg)
                            except Exception as e:
                                log.error(f"PP cancel pri nove vlne selhal: {e}", exc_info=True)
                        pp_latest_wave_by_trend[current_trend_local] = latest_pp_wt
                    try:
                        existing_pp = get_pp_pending_wave_times(cfg)
                    except Exception:
                        existing_pp = set()
                    processed_pp_wave_times |= existing_pp

                    if getattr(cfg, "ext_post_confirmed_trend_lock_blocks_both_sides", True) and waves and waves[0].get("_live_post_ext_lock_active"):
                        if latest_pp_wt:
                            processed_pp_wave_times.add(latest_pp_wt)
                        log_event(cfg, "info", "POST_EXT_CONFIRMED_LOCK_SKIP",
                                  wave_id=latest_pp_wt or "PP_UNKNOWN",
                                  confirmed_dir=waves[0].get("_live_post_ext_lock_dir"),
                                  reason="lock_blocks_both_sides")
                    else:
                        try:
                            _maybe_fire_pp_break_event(
                                cfg=cfg,
                                df=df,
                                waves=waves,
                                current_trend=current_trend_local,
                                processed_pp_wave_times=processed_pp_wave_times,
                                wave_birth_by_time=ext_runtime._wave_birth_by_time,
                                entries_allowed=entries_allowed,
                                bar_idx=last_bar_idx,
                            )
                        except Exception as e:
                            log.error(f"PP break detekce selhala: {e}", exc_info=True)

            # Synchronizace aktivnich pendingu i pozic
            active_order_times = get_active_wave_times(cfg)
            active_position_times = get_position_wave_times(cfg)
            active_wave_times = active_order_times | active_position_times
            symbol_info = mt5.symbol_info(cfg.symbol)
            signal_digits = int(getattr(symbol_info, "digits", 4)) if symbol_info else 4

            active_signal_keys = set()
            for wt in active_wave_times:
                for w in waves:
                    if w["wave_time"] == wt:
                        active_signal_keys.add(get_signal_key(w, digits=signal_digits))
                        break

            sent_signals |= active_signal_keys

            # MT5 reconnect detekce + replay fronty nepodarenych signalu.
            mt5_connected_now = bool(mt5.terminal_info()) and bool(mt5.account_info())
            if mt5_connected_now:
                from infra.mt5_client import enforce_mt5_session

                enforce_mt5_session(cfg)
            if mt5_connected_now and not was_mt5_connected:
                log_event(cfg, "info", "MT5_CONNECTION", status="RECONNECTED")
                from runtime.wave_target_n_live import reset_wave_target_n_runtime_state

                processed_tp_wave_times, forming_tp_watch = (
                    reset_wave_target_n_runtime_state()
                )
            was_mt5_connected = mt5_connected_now

            if mt5_connected_now and failed_signals:
                from runtime.failed_signals_replay import (
                    abandon_failed_signal,
                    failed_signal_replay_eligible,
                )

                replay_keys = list(failed_signals.keys())
                for sig_key in replay_keys:
                    record = failed_signals.get(sig_key)
                    if record is None:
                        continue
                    wave = record["wave"]
                    wt = wave["wave_time"]
                    fresh = find_wave_by_time(waves, wt)
                    if fresh is not None:
                        wave = fresh
                    # stale / invalid signaly uz nereplayujeme
                    if (
                        is_wave_too_old(wt, cfg, ref_time=last_bar_time)
                        or not is_wave_in_allowed_session(wt, cfg)
                        or is_wave_too_large(
                            wave["move_pct"], cfg, is_ext=is_ext_wave(wave, cfg),
                        )
                    ):
                        abandon_failed_signal(
                            cfg=cfg,
                            sig_key=sig_key,
                            wave_time=str(wt),
                            sent_signals=sent_signals,
                            failed_signals=failed_signals,
                            reason="stale_or_filter",
                        )
                        continue
                    if bool(wave.get("post_ext_trend_suppressed", False)):
                        abandon_failed_signal(
                            cfg=cfg,
                            sig_key=sig_key,
                            wave_time=str(wt),
                            sent_signals=sent_signals,
                            failed_signals=failed_signals,
                            reason="post_ext_suppressed",
                        )
                        continue
                    if not failed_signal_replay_eligible(
                        str(wt),
                        wave_birth_by_time=_wave_birth_by_time,
                        last_bar_idx=last_bar_idx,
                        new_bar_indices=new_bar_indices,
                    ):
                        abandon_failed_signal(
                            cfg=cfg,
                            sig_key=sig_key,
                            wave_time=str(wt),
                            sent_signals=sent_signals,
                            failed_signals=failed_signals,
                            reason="birth_bar_passed",
                        )
                        continue
                    # TREND FILTER (BOS) — replay nesmi filter obejit.
                    # Trend state hodnotime aktualnim snapshotem k baru narozeni vlny;
                    # pokud uz neplati (mezi tim BOS prevratil smer), signal zahodime.
                    if cfg.trend_filter_enabled:
                        ts = trend_states_per_wave.get(wt)
                        if ts is None:
                            ts = trend_states_per_wave.get(str(wt))
                        if ts is None:
                            abandon_failed_signal(
                                cfg=cfg,
                                sig_key=sig_key,
                                wave_time=str(wt),
                                sent_signals=sent_signals,
                                failed_signals=failed_signals,
                                reason="no_trend_state",
                            )
                            log_event(
                                cfg,
                                "info",
                                "SIGNAL_REPLAY_NO_TREND_STATE",
                                wave_id=str(wt),
                                signal_key=sig_key,
                            )
                            continue
                        allowed, _reason = wave_allowed_for_entry(wave, ts, cfg)
                        if not allowed:
                            abandon_failed_signal(
                                cfg=cfg,
                                sig_key=sig_key,
                                wave_time=str(wt),
                                sent_signals=sent_signals,
                                failed_signals=failed_signals,
                                reason=f"trend_filter:{_reason}",
                            )
                            continue
                    if sig_key in sent_signals or sig_key in active_signal_keys:
                        failed_signals.pop(sig_key, None)
                        sent_signals.add(sig_key)
                        continue

                    if not bool(getattr(cfg, "wave_position_enabled", True)):
                        if _try_live_counter_only_on_wave(
                            cfg=cfg,
                            wave=wave,
                            seq_info=seq_info,
                            all_waves=waves,
                            entries_allowed=entries_allowed,
                            sent_signals=sent_signals,
                            sig_key=sig_key,
                        ):
                            failed_signals.pop(sig_key, None)
                            continue
                        failed_signals.pop(sig_key, None)
                        sent_signals.add(sig_key)
                        continue

                    if not entries_allowed:
                        _log_adx14_entry_blocked(
                            cfg, entry_type="WAVE_REPLAY", wave_id=str(wt),
                        )
                        continue

                    wave_order = wave
                    if is_ext_wave(wave, cfg):
                        ext_sl_anchor = wave
                    else:
                        wave_order, ext_sl_anchor = apply_first_opposite_wave_sl_after_ext(
                            wave,
                            ext_anchor=ext_sl_anchor,
                            cfg=cfg,
                        )

                    placed_meta: Dict[str, Any] = {}
                    if send_order(
                        wave_order,
                        cfg,
                        entry_mode=cfg.entry_mode,
                        placed_meta=placed_meta,
                        trend_state_at_fill=fill_trend_state,
                        bar_close=bar_entry_close,
                    ):
                        _maybe_place_live_counter_from_tp(
                            cfg=cfg,
                            wave=wave_order,
                            seq_info=seq_info,
                            tp_price=placed_meta.get("tp_price"),
                            all_waves=waves,
                            entries_allowed=entries_allowed,
                        )
                        sent_signals.add(sig_key)
                        failed_signals.pop(sig_key, None)
                        log_event(
                            cfg,
                            "info",
                            "SIGNAL_REPLAY_SUCCESS",
                            wave_id=str(wt),
                            signal_key=sig_key,
                            retry_attempts=int(record.get("attempts", 0)) + 1,
                        )
                    else:
                        record["attempts"] = int(record.get("attempts", 0)) + 1
                        failed_signals[sig_key] = record

            # Dedup signalu: stejna zona (dir + fib50 + sl) se neposila znovu
            new_signal_sent = False
            old_waves_this_cycle = 0
            skipped_session_this_cycle = 0
            skipped_trend_filter_this_cycle = 0

            # BOS retro aktivace — parita engine `_bos_flip_wave_by_bar` (vlny
            # narozene drive, cekajici na close-based flip; same-bar resi hlavni loop).
            if cfg.trend_filter_enabled and _bos_flip_map_live:
                _retro_wt = _bos_flip_map_live.get(int(last_bar_idx))
                if _retro_wt:
                    _retro_wave = find_wave_by_time(waves, _retro_wt)
                    if _retro_wave is not None:
                        _retro_sent, ext_sl_anchor = _attempt_live_bos_retro_entry(
                            cfg=cfg,
                            wave=_retro_wave,
                            last_bar_idx=last_bar_idx,
                            last_bar_time=last_bar_time,
                            wave_birth_by_time=_wave_birth_by_time,
                            bos_flip_map=_bos_flip_map_live,
                            sent_signals=sent_signals,
                            failed_signals=failed_signals,
                            retro_bos_attempted=retro_bos_attempted,
                            signal_digits=signal_digits,
                            entries_allowed=entries_allowed,
                            fill_trend_state=fill_trend_state,
                            ext_sl_anchor=ext_sl_anchor,
                            seq_info=seq_info,
                            waves=waves,
                            bar_close=bar_entry_close,
                        )
                        if _retro_sent:
                            new_signal_sent = True

            for wave in waves:
                wt = wave["wave_time"]
                sig_key = get_signal_key(wave, digits=signal_digits)

                # POST-EXT ZAMEK: vlna proti seed-smeru v zamcene zone neexistuje.
                # Skip cele zpracovani (vcetne two-sided mirror) — vlna se nesmi
                # nikdy stat rodicem ani obchodem.
                if bool(wave.get("post_ext_trend_suppressed", False)):
                    sent_signals.add(sig_key)
                    continue

                if bool(wave.get("wf_wave_position", False)):
                    continue

                if getattr(cfg, "ext_post_confirmed_trend_lock_blocks_both_sides", True) and wave.get("post_ext_confirmed_trend_lock", False):
                    sent_signals.add(sig_key)
                    log_event(cfg, "info", "POST_EXT_CONFIRMED_LOCK_SKIP",
                              wave_id=str(wt),
                              confirmed_dir=wave.get("post_ext_confirmed_trend_dir"),
                              reason="lock_blocks_both_sides")
                    continue

                if (
                    cfg.trend_filter_enabled
                    and trend_states_per_wave.get(wt) is None
                    and trend_states_per_wave.get(str(wt)) is None
                    and not bool(wave.get("wf_continued_classic", False))
                ):
                    sent_signals.add(sig_key)
                    log_event(
                        cfg,
                        "info",
                        "WAVE_NO_TREND_STATE_SNAPSHOT",
                        wave_id=str(wt),
                        reason="missing_in_trend_states_per_wave",
                    )
                    continue

                # 1) (volitelne, ale doporucene) nikdy neobchoduj nic starsi nez MAX_WAVE_AGE_HOURS
                if is_wave_too_old(wt, cfg, ref_time=last_bar_time):
                    # oznac jako zpracovane, ale nikdy neposilej do send_order()
                    sent_signals.add(sig_key)
                    old_waves_this_cycle += 1
                    continue

                # 2) WAVE SESSION FILTER - vlna mimo povolene session se preskoci
                if not is_wave_in_allowed_session(wt, cfg):
                    sent_signals.add(sig_key)
                    skipped_session_this_cycle += 1
                    continue

                # 4) vlna je prilis velka -> preskoc (EXT vlny wave_max_pct nefiltruji)
                if is_wave_too_large(
                    wave["move_pct"], cfg, is_ext=is_ext_wave(wave, cfg),
                ):
                    sent_signals.add(sig_key)
                    continue

                # 5) nova vlna (v ramci casoveho okna), kterou jsme jeste nikdy neobchodovali
                if sig_key not in sent_signals:
                    _bos_flip_bar = (
                        _bos_flip_bar_for_wave(wt, _bos_flip_map_live)
                        if cfg.trend_filter_enabled
                        else None
                    )
                    if not _apply_birth_bar_gate(
                        wt,
                        wave_birth_by_time=_wave_birth_by_time,
                        last_bar_idx=last_bar_idx,
                        sent_signals=sent_signals,
                        sig_key=sig_key,
                        bos_flip_bar=_bos_flip_bar,
                        is_bos_retro_candidate=str(wt) in bos_wave_times,
                    ):
                        continue

                    if not bool(getattr(cfg, "wave_position_enabled", True)):
                        if _try_live_counter_only_on_wave(
                            cfg=cfg,
                            wave=wave,
                            seq_info=seq_info,
                            all_waves=waves,
                            entries_allowed=entries_allowed,
                            sent_signals=sent_signals,
                            sig_key=sig_key,
                        ):
                            continue
                        sent_signals.add(sig_key)
                        continue

                    if not entries_allowed:
                        _log_adx14_entry_blocked(
                            cfg, entry_type="WAVE", wave_id=str(wt),
                        )
                        sent_signals.add(sig_key)
                        continue

                    wave_order = wave
                    if is_ext_wave(wave, cfg):
                        ext_sl_anchor = wave
                        if two_sided_enabled(cfg):
                            _live_two_sided_tracker.clear_all()
                    else:
                        wave_order, ext_sl_anchor = apply_first_opposite_wave_sl_after_ext(
                            wave,
                            ext_anchor=ext_sl_anchor,
                            cfg=cfg,
                        )

                    two_sided_only = False
                    ts_current = trend_states_per_wave.get(str(wt))
                    if two_sided_enabled(cfg):
                        _live_two_sided_tracker.link_counter_b_wave_if_matches(
                            wave_order,
                            waves,
                            cfg,
                            trend_states_per_wave=trend_states_per_wave,
                        )
                    waves_for_two_sided = (
                        _live_two_sided_tracker.waves_with_armed_parents(waves)
                        if two_sided_enabled(cfg)
                        else waves
                    )
                    prev_wave = find_parent_wave_for_two_sided(
                        waves_for_two_sided, wave_order, cfg,
                        trend_states_per_wave=trend_states_per_wave,
                    )
                    if (
                        two_sided_enabled(cfg)
                        and prev_wave is not None
                    ):
                        parent_wt = str(prev_wave.get("wave_time", ""))
                        touched = _live_two_sided_tracker.fib_was_touched(parent_wt)
                        ts_parent = trend_states_per_wave.get(parent_wt)
                        if should_open_two_sided_counter(
                            prev_wave,
                            wave_order,
                            cfg,
                            parent_fib_touched=touched,
                            parent_trend_state=ts_parent,
                            counter_trend_state=ts_current,
                        ):
                            two_sided_only = True
                            _live_two_sided_tracker.register_counter_b_wave(str(wt))
                            if wave_counter_two_sided_orders_enabled(cfg):
                                counter = prepare_ts2_mirror_entry_signal(wave_order, cfg)
                                log.info(
                                    f"TWO-SIDED COUNTER | "
                                    f"{'BUY' if counter['dir'] == 1 else 'SELL'} "
                                    f"EP={counter['fib50']:.5f} SL={counter['sl']:.5f} "
                                    f"TP={counter['tp']:.5f} | prev {parent_wt} "
                                    f"{float(prev_wave['move_pct']):.2f}%"
                                )
                                if send_order(
                                    counter,
                                    cfg,
                                    entry_mode=cfg.entry_mode,
                                    trend_state_at_fill=fill_trend_state,
                                    is_two_sided_mirror=True,
                                    bar_close=bar_entry_close,
                                    bar_open=float(_last_bar["open"]) if _last_bar is not None else None,
                                ):
                                    sent_signals.add(sig_key)
                                    failed_signals.pop(sig_key, None)
                                    new_signal_sent = True
                                    _live_two_sided_tracker.discard_parent(parent_wt)
                                    log_event(
                                        cfg,
                                        "info",
                                        "TWO_SIDED_COUNTER_PLACED",
                                        wave_time=str(wt),
                                        parent_wave_time=parent_wt,
                                        counter_dir=int(counter["dir"]),
                                        prev_wave_size_pct=float(prev_wave["move_pct"]),
                                    )
                                else:
                                    prev = failed_signals.get(
                                        sig_key, {"wave": counter, "attempts": 0}
                                    )
                                    prev["wave"] = counter
                                    prev["attempts"] = int(prev.get("attempts", 0)) + 1
                                    failed_signals[sig_key] = prev
                                    log_event(
                                        cfg,
                                        "warning",
                                        "TWO_SIDED_COUNTER_FAILED",
                                        wave_time=str(wt),
                                        parent_wave_time=parent_wt,
                                    )
                            continue

                    if skip_primary_entry_on_parent_wave(
                        wave_order, cfg, trend_state=ts_current,
                    ):
                        sent_signals.add(sig_key)
                        log_event(
                            cfg,
                            "info",
                            "TWO_SIDED_PARENT_SKIP_PRIMARY",
                            wave_time=str(wt),
                            move_pct=float(wave_order.get("move_pct", 0.0)),
                        )
                        continue

                    if (
                        two_sided_enabled(cfg)
                        and _live_two_sided_tracker.is_b_wave_for_any_parent(str(wt))
                    ):
                        sent_signals.add(sig_key)
                        log_event(
                            cfg,
                            "info",
                            "TWO_SIDED_PRIMARY_SKIP_TRACKER_HIT",
                            wave_time=str(wt),
                        )
                        continue

                # POST-EXT ZAMEK: vlna proti seed-smeru v zamcene zone vubec neexistuje.
                # Bezpodminecne preskoc — bypass / retro NEMA vliv.
                if bool(wave.get("post_ext_trend_suppressed", False)):
                    sent_signals.add(sig_key)
                    log_event(
                        cfg, "info", "WAVE_SKIPPED_TREND_FILTER",
                        log_targets=LOG_TARGETS_JSONL_ONLY,
                        wave_id=str(wt),
                        wave_dir=int(wave["dir"]),
                        move_pct=float(wave["move_pct"]),
                        reason="post_ext_trend_suppressed",
                        trend="locked",
                        hh_hl_required=bool(cfg.trend_hh_hl_filter_enabled),
                    )
                    continue

                # 3) TREND FILTER (BOS) — primarni WAVE (two-sided counter uz probehl vyse)
                wave_bypass_trend_fill = False
                if cfg.trend_filter_enabled:
                    ts = trend_states_per_wave.get(wt)
                    allowed, reason = wave_allowed_for_entry(wave, ts, cfg)
                    # BOS vlna ZPUSOBI flip — vstup z teto vlny je vzdy
                    # povolen, i kdyz HH/HL/smer trendu by ji jinak zablokoval.
                    if not allowed and str(wt) in bos_wave_times and not _wave_is_wf_origin(wave):
                        allowed, reason = True, "retro_after_bos_flip"
                        wave_bypass_trend_fill = True
                    if not allowed:
                        if reason != "wave_against_trend":
                            sent_signals.add(sig_key)
                        skipped_trend_filter_this_cycle += 1
                        log_event(
                            cfg, "info", "WAVE_SKIPPED_TREND_FILTER",
                            log_targets=LOG_TARGETS_JSONL_ONLY,
                            wave_id=str(wt),
                            wave_dir=int(wave["dir"]),
                            move_pct=float(wave["move_pct"]),
                            reason=reason,
                            trend=getattr(ts, "direction", "unknown") if ts else "unknown",
                            hh_hl_required=bool(cfg.trend_hh_hl_filter_enabled),
                        )
                        continue

                if sig_key not in sent_signals:
                    if (
                        two_sided_enabled(cfg)
                        and _live_two_sided_tracker.is_b_wave_for_any_parent(str(wt))
                    ):
                        sent_signals.add(sig_key)
                        log_event(
                            cfg,
                            "info",
                            "TWO_SIDED_PRIMARY_SKIP_TRACKER_HIT",
                            wave_time=str(wt),
                        )
                        continue

                    if not bool(getattr(cfg, "wave_position_enabled", True)):
                        if _try_live_counter_only_on_wave(
                            cfg=cfg,
                            wave=wave,
                            seq_info=seq_info,
                            all_waves=waves,
                            entries_allowed=entries_allowed,
                            sent_signals=sent_signals,
                            sig_key=sig_key,
                        ):
                            continue
                        sent_signals.add(sig_key)
                        continue

                    if not entries_allowed:
                        _log_adx14_entry_blocked(
                            cfg, entry_type="WAVE", wave_id=str(wt),
                        )
                        sent_signals.add(sig_key)
                        continue

                    wave_order = wave
                    if is_ext_wave(wave, cfg):
                        ext_sl_anchor = wave
                    else:
                        wave_order, ext_sl_anchor = apply_first_opposite_wave_sl_after_ext(
                            wave,
                            ext_anchor=ext_sl_anchor,
                            cfg=cfg,
                        )

                    log.info(
                        f"NOVA VLNA | {'BUY' if wave_order['dir'] == 1 else 'SELL'} "
                        f"EP={wave_order['fib50']:.5f} SL={wave_order['sl']:.5f} "
                        f"TP={wave_order['tp']:.5f} | Wave {wave_order['move_pct']:.2f}% | {wt}"
                    )
                    log_event(
                        cfg,
                        "info",
                        "WAVE_DEFINED",
                        wave_id=str(wt),
                        side="BUY" if int(wave_order["dir"]) == 1 else "SELL",
                        move_pct=float(wave_order.get("move_pct", 0.0)),
                        entry_price=float(wave_order["fib50"]),
                        sl=float(wave_order["sl"]),
                        tp=float(wave_order["tp"]) if wave_order.get("tp") is not None else None,
                    )

                    placed_meta: Dict[str, Any] = {}
                    if send_order(
                        wave_order,
                        cfg,
                        entry_mode=cfg.entry_mode,
                        placed_meta=placed_meta,
                        trend_state_at_fill=fill_trend_state,
                        bypass_trend_filter=wave_bypass_trend_fill,
                        bar_close=bar_entry_close,
                    ):
                        _maybe_place_live_counter_from_tp(
                            cfg=cfg,
                            wave=wave_order,
                            seq_info=seq_info,
                            tp_price=placed_meta.get("tp_price"),
                            all_waves=waves,
                            entries_allowed=entries_allowed,
                        )
                        sent_signals.add(sig_key)
                        failed_signals.pop(sig_key, None)
                        new_signal_sent = True
                    else:
                        prev = failed_signals.get(sig_key, {"wave": wave_order, "attempts": 0})
                        prev["wave"] = wave_order
                        prev["attempts"] = int(prev.get("attempts", 0)) + 1
                        failed_signals[sig_key] = prev
                        log_event(
                            cfg,
                            "warning",
                            "SIGNAL_QUEUED_FOR_REPLAY",
                            wave_id=str(wt),
                            signal_key=sig_key,
                            attempts=int(prev["attempts"]),
                        )

            ext_runtime.process_cycle(
                cfg,
                df,
                entries_allowed=entries_allowed,
                signal_digits=signal_digits,
                sent_signals=sent_signals,
                on_adx14_blocked=lambda et: _log_adx14_entry_blocked(
                    cfg, entry_type=et,
                ),
            )

            _emit_live_periodic_logs(
                old_waves_this_cycle=old_waves_this_cycle,
                skipped_session_this_cycle=skipped_session_this_cycle,
                skipped_trend_filter_this_cycle=skipped_trend_filter_this_cycle,
            )

        except Exception as e:
            log_event(
                cfg,
                "error",
                "ERROR",
                error_type=type(e).__name__,
                error_message=str(e),
                location="run_live_loop",
            )
            log.error(f"Chyba v hlavni smycce: {e}", exc_info=True)
            # Detekce MT5 disconnect
            try:
                if not mt5.terminal_info() or not mt5.account_info():
                    log_event(
                        cfg,
                        "warning",
                        "MT5_CONNECTION",
                        status="DISCONNECTED",
                    )
            except Exception:
                pass
        time.sleep(cfg.sleep_sec)