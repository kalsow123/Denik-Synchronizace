

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
    place_counter_position_pending,
    place_pp_market_fallback,
    place_pp_pending,
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
    find_wave_by_time,
    is_tp_wave_index,
    wave_counter_min_sl_pct,
)
from strategy.wave_target_n_mode import is_wave_target_n_family, is_wave_target_n_g
from strategy.wave_target_n_early import FormingTpWatch
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
    Retro vstup resi BacktestEngine.process_bar (LiveEngineSession).
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

            # ───── 2F: JEDEN rozhodovač — engine.process_bar přes LiveEngineSession ─────
            # Veškerá strategická rozhodnutí (WAVE/BOS/PP/counter/two-sided/EXT/WF/
            # WAVE_TARGET_N) dělá process_bar; live_loop jen orchestruje IO. Cold start /
            # reset každý cyklus nad aktuálním closed df, pak process_bar přes nové closed
            # bary (catch-up = N× process_bar nad sdíleným ctx = parita s batch).
            # Live-only kontrakt zůstává mimo process_bar: forming-bar strip (load_bars
            # include_forming=False), session pre-close cancel, cancel_expired_pending,
            # guard/dedup (LiveExecutor), recovery (startup.py), TZ align (session_manager).
            # Catch-up indexy + MISSED_BARS_CATCH_UP log = LiveEngineSession.catch_up_missed.
            from runtime.live_engine_session import LiveEngineSession

            live_engine_session = LiveEngineSession(cfg, df)
            new_bar_indices = live_engine_session.catch_up_missed(
                df, last_processed_closed_bar_time
            )
            if not new_bar_indices:
                _emit_live_periodic_logs()
                continue

            last_processed_closed_bar_time = closed_bar_ts
            live_engine_session.process_closed_bars(df, new_bar_indices)

            # ───── MT5 reconnect detekce (live-only kontrakt) ─────
            mt5_connected_now = bool(mt5.terminal_info()) and bool(mt5.account_info())
            if mt5_connected_now:
                from infra.mt5_client import enforce_mt5_session

                enforce_mt5_session(cfg)
            if mt5_connected_now and not was_mt5_connected:
                log_event(cfg, "info", "MT5_CONNECTION", status="RECONNECTED")
                from runtime.failed_signals_replay import (
                    clear_failed_signals_on_recovery,
                )

                clear_failed_signals_on_recovery(
                    failed_signals, cfg=cfg, reason="mt5_reconnect"
                )
            was_mt5_connected = mt5_connected_now

            # failed_signals = JEN IO retry transientně neodeslaných WAVE orderů
            # (NE nové rozhodnutí). send_order žije v failed_signals_replay / live_executor,
            # NIKDY v live_loop (viz tests/test_live_loop_no_strategy_imports.py).
            if mt5_connected_now and failed_signals:
                from runtime.failed_signals_replay import replay_failed_signals

                replay_failed_signals(
                    cfg,
                    failed_signals=failed_signals,
                    sent_signals=sent_signals,
                )

            _emit_live_periodic_logs()

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

