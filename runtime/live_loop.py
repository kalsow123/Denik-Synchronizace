

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
)
from core.signal_keys import get_signal_key
from infra.account import get_account_snapshot
from infra.trade_tracker import TradeTrackerState, update_trade_tracker
from infra.market_data import get_bars
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
    close_positions_on_extension_tp_hit,
    close_positions_on_tp_wave_n,
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
    extension_tp_hit_on_bar,
    g_counter_wave_time,
    start_forming_tp_watch,
    tp_wave_early_fallback_birth,
    wave_counter_entry_allowed,
    wave_target_n_early_g_enabled,
    wave_target_n_extension_exit_enabled,
)
from strategy.two_sided import (
    TwoSidedTracker,
    find_parent_wave_for_two_sided,
    parent_monitor_start_bar,
    parent_wave_qualifies,
    prepare_two_sided_counter_signal,
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
from runtime.wave_target_n_live import sync_wave_target_n_live_state

# ───── LIVE BOT ──────────────────────────
# Nastavení jak bot funguje. Co dělá během errorů, jak je řeší.

log = logging.getLogger(__name__)

_live_two_sided_tracker = TwoSidedTracker()

"""
    Bezi neustale dokud neprijde KeyboardInterrupt nebo shutdown signal.
    V kazdem cyklu:
      0) SESSION MANAGER (pokud zapnuty): kontrola jestli je trading session.
         - Pre-close buffer: zrusi pendingy (volitelne i pozice v patek)
         - Mimo session: spi a kazdou ~minutu kontroluje
         - Po wake-up: spusti startup recovery, pak normalni loop
      1) Zrusi expirovane pendingy.
      2) Nacte poslednich 300 baru a detekuje vlny.
      3) Sesynchronizuje signaly z MT5 (pendingy + pozice) do `sent_signals`.
      4) Projde vlny:
           - prilis stare (> max_wave_age_hours) -> oznaci jako zpracovane, preskoci
           - mimo povolenou session -> oznaci jako zpracovane, preskoci
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


def _maybe_fire_pp_break_event(*, cfg: BotConfig, df, waves,
                                current_trend: str,
                                processed_pp_wave_times: Set[str],
                                wave_birth_by_time: dict[str, int],
                                entries_allowed: bool = True) -> None:
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
    bar_close = float(df["close"].iloc[-1])
    trend_dir = 1 if current_trend == "bull" else -1
    last_bar_idx = len(df) - 1

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


def run_live_loop(cfg: BotConfig, sent_signals: Set[str]) -> None:
    from config.position_modes import apply_wave_positions_only_to_bot_config

    cfg = apply_wave_positions_only_to_bot_config(cfg)
    if bool(getattr(cfg, "wave_positions_only", False)):
        log_event(
            cfg,
            "info",
            "WAVE_POSITIONS_ONLY",
            message="Live: jen klasické WAVE pozice, ostatní moduly vynuceně vypnuté",
        )
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
    # Posledni EXT vlna cekajici na prvni opacnou (WAVE SL na ext_low/ext_high).
    ext_sl_anchor: Optional[dict] = None
    was_mt5_connected: bool = True

    # TREND state cross-cycle: pamatujeme si posledni nezavinene "non-neutral"
    # smer trendu, abychom detekovali BOS flip (bull → bear / bear → bull).
    # Pri startu None → prvni cyklus jen inicializuje, neflipuje.
    last_known_trend_dir: str | None = None  # "bull" / "bear" / None
    # Cas posledniho close baru z predchoziho cyklu (close-BOS flip jen na novejsich barech).
    prev_cycle_last_bar_time: Optional[datetime] = None

    # WAVE_TARGET_N — TP-vlny W(N) uz zpracovane; forming_tp_watch pro G.
    # Po restartu / kazdem cyklu sync z historickych vln + MT5 CNTR_ (parita backtest).
    processed_tp_wave_times: Set[str] = set()
    forming_tp_watch: Optional[FormingTpWatch] = None

    # PP wave_times, kterym uz PP order byl polozen (max 1x per vlna).
    # Po restartu se inicializuje z MT5 (comment prefix PP_).
    processed_pp_wave_times: Set[str] = set()
    pp_latest_wave_by_trend: Dict[str, str] = {}
    ext_runtime = ExtLiveRuntime()
    ext_runtime.sync_from_mt5(cfg)

    while True:
        try:
            now = get_broker_now(cfg)

            # ───── SESSION MANAGER ─────────────────────────
            if is_session_enabled(cfg):
                in_session = is_in_session(cfg, now)

                # 1) Pre-close buffer - zrusit pendingy (jen jednou za den)
                if is_pre_close_buffer(cfg, now):
                    today_key = now.date()
                    if last_pre_close_date != today_key:
                        log_event(
                            cfg,
                            "info",
                            "SESSION_PRE_CLOSE",
                            time=now.strftime("%H:%M:%S"),
                            buffer_min=cfg.session_pre_close_buffer_min,
                        )
                        cancel_all_pendings(cfg)

                        # V patek volitelne zavreme i pozice
                        if (cfg.session_close_positions_on_friday
                                and is_week_close_pre_buffer(cfg, now)):
                            log_event(cfg, "info", "SESSION_WEEK_CLOSE_POSITIONS")
                            close_all_positions(cfg)

                        last_pre_close_date = today_key

                # 2) Mimo session - usnout do open
                if not in_session:
                    if not was_outside_session:
                        secs = seconds_until_open(cfg, now)
                        wake_at = now + timedelta(seconds=secs)
                        log_event(
                            cfg,
                            "info",
                            "SESSION_SLEEP",
                            now=now.strftime("%Y-%m-%d %H:%M:%S"),
                            wake_at=wake_at.strftime("%Y-%m-%d %H:%M:%S"),
                            seconds_to_wake=int(secs),
                        )
                        was_outside_session = True

                    # Chytry sleep: spi az do open (max 60s pro pripad ze user
                    # zmeni system clock nebo neco)
                    secs = seconds_until_open(cfg, now)
                    sleep_for = min(60.0, max(1.0, secs))
                    time.sleep(sleep_for)
                    continue

                # 3) V session - pokud jsme byli mimo, probudili jsme se
                if was_outside_session:
                    log_event(
                        cfg,
                        "info",
                        "SESSION_WAKE_UP",
                        time=now.strftime("%Y-%m-%d %H:%M:%S"),
                    )
                    was_outside_session = False
                    # Po wake-up spustime startup recovery, abychom obnovili
                    # vlny ktere vznikly behem spanku
                    try:
                        from runtime.startup import (
                            block_historical_waves,
                            restore_pine_style_pending_orders,
                        )
                        recovered = restore_pine_style_pending_orders(cfg)
                        sent_signals |= recovered
                        sent_signals = block_historical_waves(cfg, sent_signals)
                        ext_runtime.sync_from_mt5(cfg)
                        log.info(
                            f"SESSION WAKE-UP RECOVERY: dohromady {len(sent_signals)} signalu"
                        )
                    except Exception as e:
                        log.error(f"SESSION WAKE-UP RECOVERY: chyba {e}", exc_info=True)

            # ───── HLAVNI LOOP ─────────────────────────────
            # Trade tracker - detekce ORDER_FILLED, POSITION_OPENED/CLOSED, MT5 connection
            update_trade_tracker(cfg, tracker_state, adx14_runtime=adx14_runtime)
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

            df = get_bars(cfg, 300)
            if df is None:
                log.warning("Nepodarilo se nacist data, zkousim znovu...")
                time.sleep(cfg.sleep_sec)
                continue

            if adx14_runtime.active and adx14_runtime.needs_history_bars():
                adx14_df = get_bars(cfg, int(getattr(cfg, "adx14_history_bars", 5000)))
                adx14_runtime.update(adx14_df if adx14_df is not None else df, now)
            elif adx14_runtime.active:
                adx14_runtime.update(df, now)

            entries_allowed = adx14_runtime.entries_allowed

            waves = detect_waves(df, cfg)
            if not waves:
                # POZN.: I bez vln muze byt aktivni BOS_EXIT (kdyz mam otevrene pozice
                # a trend se mezi tim flipl). Pri zadne vlne ale nemame swing levels,
                # takze trend stav by byl 'neutral' → nic se nezavre. Bezpecne preskocit.
                time.sleep(cfg.sleep_sec)
                continue

            from strategy.wf_wave_list import prepare_waves_after_wf_eval

            wf_prep = prepare_waves_after_wf_eval(df, cfg, waves)
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

            # BOS vlna (zpusobi close-based flip trendu) — vstupove dostupna i
            # kdyz je proti aktualnimu trendu / mimo HH/HL strukturu. Vystup
            # je nezavisly na `tp_mode` a `last_known_trend_dir`.
            if cfg.trend_filter_enabled:
                bos_wave_times = compute_bos_wave_times(df, waves, cfg)
            else:
                bos_wave_times = set()

            seq_info, protected_waves = sync_wave_sequence_state(df, waves, cfg)

            ext_runtime.refresh_simulation(
                df, cfg, seq_info=seq_info, protected_waves=protected_waves, waves=waves,
            )
            ext_runtime.run_ext1_rrr_better_exit(cfg, df)
            last_bar_idx = len(df) - 1
            ext1_per_bar = ext_runtime._ext1_protection_per_bar

            if two_sided_enabled(cfg):
                global _live_two_sided_tracker
                for w in waves:
                    ts_parent = trend_states_per_wave.get(
                        str(w.get("wave_time", ""))
                    )
                    if parent_wave_qualifies(w, cfg, trend_state=ts_parent):
                        end_bar = int(w.get("draw_right", max(0, len(df) - 1)))
                        _live_two_sided_tracker.register_parent(
                            w,
                            end_bar,
                            cfg,
                            df=df,
                            sync_from_bar=parent_monitor_start_bar(w),
                            trend_state=ts_parent,
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
                bar_trend_states = compute_trend_states_per_bar(df, waves, cfg)
                current_trend = bar_trend_states[-1].direction if bar_trend_states else "neutral"
            fill_trend_state = (
                bar_trend_states[-1]
                if bar_trend_states
                else None
            )
            if cfg.trend_filter_enabled and fill_trend_state is not None:
                try:
                    cancel_counter_trend_wave_pendings(cfg, fill_trend_state, waves)
                except Exception as e:
                    log.error(f"TREND_FILL_GUARD cancel selhal: {e}", exc_info=True)

            if wf_prep.wf_wave is not None:
                try:
                    _wf_wave = wf_prep.wf_wave
                    _wf_result = wf_prep.eval_result or {}
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
                            activation_close=float(df.iloc[-1]["close"]),
                            window_size_bars=int(_wf_result.get("window_size", 0)),
                        )
                        if send_order(
                            _wf_wave,
                            cfg,
                            entry_mode=cfg.entry_mode,
                            trend_state_at_fill=fill_trend_state,
                        ):
                            sent_signals.add(_wf_sig_key)
                            new_signal_sent = True
                    if wf_prep.resumed_count:
                        log_event(
                            cfg,
                            "info",
                            "WF_CLASSIC_WAVES_RESUMED",
                            wf_wave_id=_wf_wt_str,
                            resumed=wf_prep.resumed_count,
                        )
                except Exception as _wf_exc:
                    log.error("WF live aktivace selhala: %s", _wf_exc, exc_info=True)

            # Rozhodovaci tabulka pro BOS-driven actions:
            #   close_positions: zavre pozice ve smeru staré trendovky (ridi tp_mode)
            #   cancel_pendings: zrusi pendingy ve smeru staré trendovky
            #                    - "trend"  → vzdy
            #                    - "number" → nikdy (jen časová expirace)
            from config.enums import PendingCancelMode as _PCM
            pcm_raw = getattr(cfg, "pending_cancel_mode", _PCM.NUMBER)
            try:
                pcm = _PCM(pcm_raw) if isinstance(pcm_raw, str) else pcm_raw
            except ValueError:
                pcm = _PCM.NUMBER
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
                _bos_flip_map = compute_bos_wave_flip_map(df, waves, cfg)
                _wt_bos = _bos_flip_map.get(int(_flip_bar_ix))
                if _wt_bos:
                    _bos_protect_wave_time = str(_wt_bos)

            _last_bar = df.iloc[-1] if not df.empty else None
            _bar_high = float(_last_bar["high"]) if _last_bar is not None else None
            _bar_low = float(_last_bar["low"]) if _last_bar is not None else None

            if do_close_pos and _last_bar is not None:
                br = bos_per_bar_close_reason(cfg)
                _dir_kw = dict(
                    reason=br,
                    protected_wave_times=protected_waves,
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
            # Rodina wave_target_n / wave_target_n_g: pre-compute TP-wave ceny do `waves`.
            # G preset: tp_mode=wave_target_n_g (viz strategy/wave_target_n_mode.py).
            # `send_order` -> `resolve_effective_tp` pak cte `wave["wave_target_tp_price"]`
            # — kdyz neni nastaveno (K<N nebo non-TP-wave index), vrati None
            # (= broker dostane TP=0.0, bez TP).
            if is_wave_target_n_family(cfg):
                target_n = int(getattr(cfg, "tp_target_wave_index", 0) or 0)
                _tp_bar = df.iloc[-1] if not df.empty else None
                bar_high = float(_tp_bar["high"]) if _tp_bar is not None else 0.0
                bar_low = float(_tp_bar["low"]) if _tp_bar is not None else 0.0
                bar_close = float(_tp_bar["close"]) if _tp_bar is not None else 0.0
                bar_open = float(_tp_bar["open"]) if _tp_bar is not None else 0.0

                _tp_sync = sync_wave_target_n_live_state(
                    cfg,
                    df,
                    waves,
                    seq_info,
                    birth_by_time=ext_runtime._wave_birth_by_time,
                    last_bar_idx=last_bar_idx,
                    active_counter_wave_times=get_active_counter_wave_times(cfg),
                )
                processed_tp_wave_times |= _tp_sync.processed_tp_wave_times

                g_tp_cycle_counter_placed = False
                g_tp_cycle_fallback_birth = False
                g_tp_cycle_extension_done = False

                if wave_target_n_early_g_enabled(cfg):
                    forming_tp_watch = _tp_sync.forming_tp_watch
                    if _tp_sync.catch_up_extension and forming_tp_watch is not None:
                        try:
                            ext_stats = close_positions_on_extension_tp_hit(
                                cfg,
                                trend_dir=int(_tp_sync.catch_up_trend_dir),
                                armed_tp=float(_tp_sync.catch_up_armed_tp or 0.0),
                                bar_high=float(_tp_sync.catch_up_high or 0.0),
                                bar_low=float(_tp_sync.catch_up_low or 0.0),
                                bar_close=float(_tp_sync.catch_up_close or 0.0),
                                bar_open=float(_tp_sync.catch_up_open or 0.0),
                                ext1_protection_per_bar=ext1_per_bar,
                                current_bar_idx=int(_tp_sync.catch_up_bar or last_bar_idx),
                                wave_birth_by_time=ext_runtime._wave_birth_by_time,
                                main_trend_dir=(
                                    1 if current_trend == "bull"
                                    else -1 if current_trend == "bear" else 0
                                ),
                            )
                            forming_tp_watch.extension_hit_done = True
                            g_tp_cycle_extension_done = True
                            log_event(
                                cfg,
                                "info",
                                "TP_EXTENSION_CATCH_UP",
                                catch_up_bar=int(_tp_sync.catch_up_bar or -1),
                                armed_tp=float(_tp_sync.catch_up_armed_tp or 0.0),
                                trend_dir_closed=int(ext_stats["trend_dir_closed"]),
                            )
                            if _g_extension_hit_closed_positions(ext_stats):
                                _place_live_counter_from_g_extension(
                                    cfg=cfg,
                                    watch=forming_tp_watch,
                                    entries_allowed=entries_allowed,
                                )
                            g_tp_cycle_counter_placed = bool(
                                forming_tp_watch.counter_placed
                            )
                        except Exception as e:
                            log.error(
                                "TP_EXTENSION catch-up selhal: %s", e, exc_info=True,
                            )

                if wave_target_n_early_g_enabled(cfg):
                    for w in waves:
                        if int(w.get("draw_right", -1)) != last_bar_idx:
                            continue
                        wt = str(w["wave_time"])
                        info = seq_info.get(wt)
                        if info is None or info.index_in_trend is None:
                            continue
                        idx = int(info.index_in_trend)
                        if is_tp_wave_index(idx, target_n):
                            if forming_tp_watch is not None:
                                g_tp_cycle_counter_placed = bool(
                                    forming_tp_watch.counter_placed
                                )
                                if (
                                    not forming_tp_watch.extension_hit_done
                                    and tp_wave_early_fallback_birth(cfg)
                                ):
                                    g_tp_cycle_fallback_birth = True
                            forming_tp_watch = None
                            continue
                        new_watch = start_forming_tp_watch(
                            prev_wave=w,
                            index_in_trend=idx,
                            target_n=target_n,
                            start_bar=last_bar_idx,
                        )
                        if new_watch is not None:
                            forming_tp_watch = new_watch

                    if (
                        wave_target_n_extension_exit_enabled(cfg)
                        and forming_tp_watch is not None
                        and not forming_tp_watch.extension_hit_done
                    ):
                        forming_tp_watch.update_extreme(bar_high, bar_low)
                        forming_tp_watch.try_arm(cfg)
                        if forming_tp_watch.armed and extension_tp_hit_on_bar(
                            forming_tp_watch,
                            high=bar_high,
                            low=bar_low,
                            close=bar_close,
                            open_=bar_open,
                        ):
                            try:
                                ext_stats = close_positions_on_extension_tp_hit(
                                    cfg,
                                    trend_dir=int(forming_tp_watch.trend_dir),
                                    armed_tp=float(forming_tp_watch.armed_tp or 0.0),
                                    bar_high=bar_high,
                                    bar_low=bar_low,
                                    bar_close=bar_close,
                                    bar_open=bar_open,
                                    ext1_protection_per_bar=ext1_per_bar,
                                    current_bar_idx=last_bar_idx,
                                    wave_birth_by_time=ext_runtime._wave_birth_by_time,
                                    main_trend_dir=(
                                        1 if current_trend == "bull"
                                        else -1 if current_trend == "bear" else 0
                                    ),
                                )
                                forming_tp_watch.extension_hit_done = True
                                g_tp_cycle_extension_done = True
                                log_event(
                                    cfg,
                                    "info",
                                    "TP_EXTENSION_HIT",
                                    armed_tp=float(forming_tp_watch.armed_tp or 0.0),
                                    trend_dir=int(forming_tp_watch.trend_dir),
                                    trend_dir_closed=int(ext_stats["trend_dir_closed"]),
                                    wave_counters_closed=int(
                                        ext_stats["wave_counter_closed"]
                                    ),
                                    sl_protected=int(ext_stats["sl_protected"]),
                                    bar_close=float(bar_close),
                                )
                                if _g_extension_hit_closed_positions(ext_stats):
                                    _place_live_counter_from_g_extension(
                                        cfg=cfg,
                                        watch=forming_tp_watch,
                                        entries_allowed=entries_allowed,
                                    )
                                    g_tp_cycle_counter_placed = bool(
                                        forming_tp_watch.counter_placed
                                    )
                            except Exception as e:
                                log.error(
                                    "TP_EXTENSION close_positions selhal: %s",
                                    e,
                                    exc_info=True,
                                )

                for w in waves:
                    w.pop("wave_target_tp_price", None)
                    info = seq_info.get(w["wave_time"])
                    if info is None:
                        continue
                    idx = info.index_in_trend if info else None
                    if idx is None:
                        continue
                    if not is_tp_wave_index(idx, target_n):
                        continue
                    prev_w = find_wave_by_time(waves, info.prev_same_dir_in_trend_wave_time)
                    tp_price = compute_wave_target_tp_price(w, prev_w, cfg)
                    if tp_price is not None:
                        w["wave_target_tp_price"] = float(tp_price)

                # ───── TP-WAVE EVENT (live) ─────
                # Backtest-aligned: aktivne zavre dle should_close_trade_on_tp_wave_n
                # (trend-dir + CNTR/TS2 + EXT block E23_/ECT_/ECB_).
                # a wave counter (CNTR_) na TP-vlne N. Pendingy se nemeni.
                for w in waves:
                    wt = w["wave_time"]
                    if wt in processed_tp_wave_times:
                        continue
                    if int(w.get("draw_right", -1)) != last_bar_idx:
                        continue

                    try:
                        # UZIV. POZADAVEK: V live se musi zkusit callnout TP_WAVE_N i kdyz tp_raw chybí,
                        # protože tp_mode=wave_target_n primárně zavírá na narození vlny W(N)
                        # (legacy), nebo dříve na extension hit (varianta G: tp_wave_early_mode=
                        # forming_qualified + tp_wave_exit_on=extension_hit). Bez toho se neshoduje
                        # backtest a live a 4. vlny občas ignorují TP.
                        
                        # Ziskame idx
                        # Musíme ale ověřit, jestli tahle vlna je ta TP vlna (idx 4 apod.)
                        if "index_in_trend" not in w or w["index_in_trend"] is None:
                            continue
                        idx = w["index_in_trend"]
                        target_n = int(getattr(cfg, "tp_target_wave_index", 0) or 0)
                        if not is_tp_wave_index(idx, target_n):
                            continue

                        if (
                            wave_target_n_early_g_enabled(cfg)
                            and g_tp_cycle_extension_done
                        ):
                            processed_tp_wave_times.add(wt)
                            continue
                        if (
                            wave_target_n_early_g_enabled(cfg)
                            and forming_tp_watch is not None
                        ):
                            if forming_tp_watch.extension_hit_done:
                                forming_tp_watch = None
                                processed_tp_wave_times.add(wt)
                                continue
                            if not tp_wave_early_fallback_birth(cfg):
                                forming_tp_watch = None
                                processed_tp_wave_times.add(wt)
                                continue
                        forming_tp_watch = None

                        tp_raw = w.get("wave_target_tp_price", 0.0)

                        info = seq_info.get(wt)
                        if info is None:
                            continue
                        trend_dir = int(w["dir"])
                        tp_price = float(tp_raw) if tp_raw is not None else 0.0
                        
                        close_stats = close_positions_on_tp_wave_n(
                            cfg,
                            trend_dir=trend_dir,
                            bar_high=bar_high,
                            bar_low=bar_low,
                            bar_close=bar_close,
                            reason="TP_WAVE_N",
                            ext1_protection_per_bar=ext1_per_bar,
                            current_bar_idx=last_bar_idx,
                            current_wave_time=str(wt),
                            wave_birth_by_time=ext_runtime._wave_birth_by_time,
                            main_trend_dir=1 if current_trend == "bull" else -1 if current_trend == "bear" else 0,
                        )

                        processed_tp_wave_times.add(wt)
                        log_event(
                            cfg, "info", "TP_WAVE_EVENT",
                            wave_time=str(wt),
                            wave_dir=int(trend_dir),
                            tp_price=float(tp_price),
                            trend_dir_closed=int(close_stats["trend_dir_closed"]),
                            wave_counters_closed=int(close_stats["wave_counter_closed"]),
                            two_sided_closed=int(close_stats.get("two_sided_closed", 0)),
                            sl_protected=int(close_stats["sl_protected"]),
                            bar_close=float(bar_close),
                        )
                        if (
                            wave_target_n_early_g_enabled(cfg)
                            and g_tp_cycle_fallback_birth
                            and not g_tp_cycle_counter_placed
                        ):
                            _place_live_counter_position(
                                cfg=cfg,
                                wave=w,
                                info=info,
                                trend_dir=trend_dir,
                                tp_price=float(tp_price) if tp_price else float(tp_raw or 0.0),
                                all_waves=waves,
                                entries_allowed=entries_allowed,
                            )

                    except Exception as e:
                        log.error(f"TP_WAVE close_positions_on_tp_wave_n selhal: {e}", exc_info=True)

            # Po vyhodnoceni BOS-exit / TP-wave eventu aktualizuj last_known_trend_dir
            # pro pristi cyklus (slouzi k detekci skutecneho flipu).
            if current_trend in ("bull", "bear"):
                last_known_trend_dir = current_trend
            if not df.empty:
                prev_cycle_last_bar_time = pd.Timestamp(
                    df["time"].iloc[-1]
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
                    bar_trend_states = compute_trend_states_per_bar(df, waves, cfg)
                current_trend_local = (
                    bar_trend_states[-1].direction if bar_trend_states else "neutral"
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
            if mt5_connected_now and not was_mt5_connected:
                log_event(cfg, "info", "MT5_CONNECTION", status="RECONNECTED")
            was_mt5_connected = mt5_connected_now

            if mt5_connected_now and failed_signals:
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
                        is_wave_too_old(wt, cfg, now=now)
                        or not is_wave_in_allowed_session(wt, cfg)
                        or is_wave_too_large(
                            wave["move_pct"], cfg, is_ext=is_ext_wave(wave, cfg),
                        )
                    ):
                        failed_signals.pop(sig_key, None)
                        sent_signals.add(sig_key)
                        continue
                    if bool(wave.get("post_ext_trend_suppressed", False)):
                        failed_signals.pop(sig_key, None)
                        sent_signals.add(sig_key)
                        continue
                    # TREND FILTER (BOS) — replay nesmi filter obejit.
                    # Trend state hodnotime aktualnim snapshotem k baru narozeni vlny;
                    # pokud uz neplati (mezi tim BOS prevratil smer), signal zahodime.
                    if cfg.trend_filter_enabled:
                        ts = trend_states_per_wave.get(wt)
                        if ts is None:
                            ts = trend_states_per_wave.get(str(wt))
                        if ts is None:
                            failed_signals.pop(sig_key, None)
                            sent_signals.add(sig_key)
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
                            failed_signals.pop(sig_key, None)
                            sent_signals.add(sig_key)
                            continue
                    if sig_key in sent_signals or sig_key in active_signal_keys:
                        failed_signals.pop(sig_key, None)
                        sent_signals.add(sig_key)
                        continue

                    if not bool(getattr(cfg, "wave_position_enabled", True)):
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
                if is_wave_too_old(wt, cfg, now=now):
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
                    if not bool(getattr(cfg, "wave_position_enabled", True)):
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
                                counter = prepare_two_sided_counter_signal(wave_order, cfg)
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
                    if not allowed and str(wt) in bos_wave_times:
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

                    placed_meta: Dict[str, Any] = {}
                    if send_order(
                        wave_order,
                        cfg,
                        entry_mode=cfg.entry_mode,
                        placed_meta=placed_meta,
                        trend_state_at_fill=fill_trend_state,
                        bypass_trend_filter=wave_bypass_trend_fill,
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

            old_waves_since_last_text += old_waves_this_cycle
            skipped_session_since_last_text += skipped_session_this_cycle
            old_waves_since_last_jsonl += old_waves_this_cycle
            skipped_session_since_last_jsonl += skipped_session_this_cycle
            skipped_trend_filter_since_last_text += skipped_trend_filter_this_cycle
            skipped_trend_filter_since_last_jsonl += skipped_trend_filter_this_cycle

            now = get_broker_now(cfg)

            # HEARTBEAT — dle cfg.heartbeat_interval_sec
            if now - last_heartbeat_time >= timedelta(seconds=heartbeat_interval_sec):
                log_event(
                    cfg, "info", "HEARTBEAT",
                    uptime_sec=int(time.time() - bot_start_ts),
                )
                last_heartbeat_time = now

            text_status_due = (
                cfg.status_log_text_hours > 0
                and now - last_status_text_time >= timedelta(hours=cfg.status_log_text_hours)
            )
            jsonl_status_due = (
                cfg.status_log_jsonl_hours > 0
                and now - last_status_jsonl_time >= timedelta(hours=cfg.status_log_jsonl_hours)
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
                        last_status_text_time = now
                    if jsonl_status_due:
                        log_event(
                            cfg,
                            "info",
                            "STATUS",
                            log_targets=LOG_TARGETS_JSONL_ONLY,
                            **status_kwargs,
                        )
                        last_status_jsonl_time = now
                else:
                    # Fallback: MT5 account_info nedostupne -> všude viditelné varování
                    log_event(
                        cfg,
                        "warning",
                        "LOG",
                        message="STATUS preskocen: MT5 account_info() vratilo None",
                        logger="runtime.live_loop",
                    )
                    if text_status_due:
                        last_status_text_time = now
                    if jsonl_status_due:
                        last_status_jsonl_time = now

            text_old_waves_due = (
                cfg.old_waves_log_text_hours > 0
                and now - last_old_waves_text_time >= timedelta(hours=cfg.old_waves_log_text_hours)
            )
            jsonl_old_waves_due = (
                cfg.old_waves_log_jsonl_hours > 0
                and now - last_old_waves_jsonl_time >= timedelta(hours=cfg.old_waves_log_jsonl_hours)
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
                    last_old_waves_text_time = now
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
                    last_old_waves_jsonl_time = now

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