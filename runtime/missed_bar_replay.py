"""
Sekvenční replay zmeškaných uzavřených barů po výpadku / restartu live bota.

Parita s backtest engine: každý missed closed bar projde BOS akcemi,
WAVE_TARGET_N/G extension catch-up a vstupy vln narozených na tomto baru.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Set

import pandas as pd

from config.bot_config import BotConfig

def replay_two_sided_tracker_live_parity(
    tracker: Any,
    df: Any,
    bar_idx: int,
    waves_by_end_bar: dict,
    waves_by_birth_bar: dict,
    cfg: BotConfig,
    trend_states_per_wave: dict,
) -> None:
    from strategy.two_sided import two_sided_enabled, parent_wave_qualifies
    if not two_sided_enabled(cfg):
        return
    
    row = df.iloc[bar_idx]
    high = float(row["high"])
    low = float(row["low"])
    
    for w in waves_by_end_bar.get(bar_idx, []):
        wt = str(w.get("wave_time", ""))
        tracker.register_parent(
            w, bar_idx, cfg, df=df, sync_from_bar=int(w.get("draw_left", 0)),
            trend_state=trend_states_per_wave.get(wt)
        )
        
    tracker.update_bar(high, low, bar_idx)
    
    for w in waves_by_birth_bar.get(bar_idx, []):
        wt = str(w.get("wave_time", ""))
        from strategy.two_sided import parent_wave_qualifies
        if parent_wave_qualifies(w, cfg, trend_state=trend_states_per_wave.get(wt)):
            tracker.register_parent(
                w, bar_idx, cfg, df=df, sync_from_bar=int(w.get("draw_left", 0)),
                trend_state=trend_states_per_wave.get(wt)
            )

from config.enums import PendingCancelMode as PCM
from core.signal_keys import get_signal_key
from infra.orders import (
    cancel_pendings_by_direction,
    cancel_flip_follower_pendings_on_bos,
    close_flip_follower_positions_on_bos,
    close_positions_by_direction,
    send_order,
)
from strategy.trend_bos import (
    bos_entry_should_open_on_flip,
    bos_per_bar_close_reason,
    find_close_bos_flip_for_target_since,
    tp_mode_uses_bos_per_bar_exit,
    wave_allowed_for_entry,
)
from strategy.wave_target_n_mode import is_wave_target_n_family

from strategy.filters import (
    is_wave_in_allowed_session,
    is_wave_too_large,
    is_wave_too_old,
)
from strategy.ext_logic import is_ext_wave, apply_first_opposite_wave_sl_after_ext
from strategy.two_sided import (
    find_parent_wave_for_two_sided,
    prepare_ts2_mirror_entry_signal,
    skip_primary_entry_on_parent_wave,
    should_open_two_sided_counter,
    two_sided_enabled,
    wave_counter_two_sided_orders_enabled,
)
from strategy.wave_sequence import find_wave_by_time
from strategy.wf_wave_list import WfWavePrepResult

# --- DIAGNOSTIKA (env-gated): trasovani rozhodovaci cesty pro vybrane vlny ---
import os as _os

_TRACE_WAVES: Set[str] = set(
    filter(None, _os.environ.get("E2E_TRACE_WAVES", "").split(","))
)
_TRACE_LOG: list = []


def _trace(wt, bar_idx, branch: str, **kw) -> None:
    if str(wt) in _TRACE_WAVES:
        _TRACE_LOG.append((int(bar_idx), str(wt), branch, kw))


def new_closed_bar_indices(
    df: pd.DataFrame,
    last_processed: pd.Timestamp | None,
) -> list[int]:
    """Indexy uzavřených barů novějších než last_processed."""
    out: list[int] = []
    for i in range(len(df)):
        ts = pd.Timestamp(df["time"].iloc[i])
        if last_processed is None or ts > last_processed:
            out.append(i)
    return out


@dataclass
class MissedBarReplayState:
    last_known_trend_dir: str | None
    prev_cycle_last_bar_time: datetime | None
    processed_tp_wave_times: Set[str]
    forming_tp_watch: Any
    ext_sl_anchor: Any
    retro_bos_attempted: Set[str]
    promoted_two_sided_wave_times: Set[str]


def _bar_ohlc(df: pd.DataFrame, bar_idx: int) -> tuple[float, float, float, float]:
    row = df.iloc[bar_idx]
    return (
        float(row["high"]),
        float(row["low"]),
        float(row["close"]),
        float(row["open"]),
    )


def _handle_wf_activations_for_bar(
    *,
    cfg: BotConfig,
    df: pd.DataFrame,
    bar_idx: int,
    bar_close: float,
    wf_activations: list[WfWavePrepResult],
    sent_signals: Set[str],
    fill_trend_state: Any,
) -> bool:
    sent = False
    for wf_act in wf_activations:
        if wf_act.wf_wave is None:
            continue
        if wf_act.activation_bar_idx is not None and int(wf_act.activation_bar_idx) != int(bar_idx):
            continue
        _wf_wave = wf_act.wf_wave
        _wf_wt_str = str(_wf_wave.get("wave_time", ""))
        from core.signal_keys import get_signal_key as _gsk
        from infra.orders import send_order as _send

        import MetaTrader5 as mt5

        symbol_info = mt5.symbol_info(cfg.symbol)
        signal_digits = int(getattr(symbol_info, "digits", 4)) if symbol_info else 4
        _wf_sig_key = _gsk(_wf_wave, digits=signal_digits)
        if _wf_sig_key in sent_signals:
            continue
        if _send(
            _wf_wave,
            cfg,
            entry_mode=cfg.entry_mode,
            trend_state_at_fill=fill_trend_state,
            bar_close=bar_close,
        ):
            sent_signals.add(_wf_sig_key)
            sent = True
    return sent


def replay_missed_closed_bar(
    *,
    cfg: BotConfig,
    df: pd.DataFrame,
    waves: list,
    bar_idx: int,
    state: MissedBarReplayState,
    bar_trend_states: list | None,
    seq_info: dict,
    protected_waves: set,
    bos_flip_map: dict[int, str],
    bos_wave_times: set[str],
    trend_states_per_wave: dict,
    ext1_per_bar: list[bool] | None,
    ext_runtime: Any,
    wf_activations: list[WfWavePrepResult],
    sent_signals: Set[str],
    failed_signals: Dict[str, Dict[str, Any]],
    signal_digits: int,
    entries_allowed: bool,
    wave_birth_by_time: dict[str, int],
    active_counter_wave_times: Set[str],
    pcm: PCM,
    place_live_bos_reentry: Callable[..., None],
    place_live_counter_from_g_extension: Callable[..., None],
    g_extension_hit_closed_positions: Callable[[dict], bool],
    place_live_counter_position: Callable[..., None],
    log_event_fn: Callable[..., None],
    two_sided_tracker: Any = None,
    get_open_comments: Callable[[], list[str]] | None = None,
) -> MissedBarReplayState:
    """Jeden missed closed bar — BOS, TP/G, WF, wave entries."""
    from runtime import live_loop as _ll

    bar_high, bar_low, bar_close, bar_open = _bar_ohlc(df, bar_idx)
    last_bar_time = pd.Timestamp(df["time"].iloc[bar_idx]).to_pydatetime()

    fill_trend_state = (
        bar_trend_states[bar_idx]
        if bar_trend_states and bar_idx < len(bar_trend_states)
        else None
    )
    current_trend = (
        fill_trend_state.direction if fill_trend_state is not None else "neutral"
    )

    bos_active = tp_mode_uses_bos_per_bar_exit(cfg)
    do_close_pos = bos_active
    do_cancel_pend = pcm == PCM.TREND

    _trend_dir_changed = (
        state.last_known_trend_dir in ("bull", "bear")
        and current_trend in ("bull", "bear")
        and current_trend != state.last_known_trend_dir
    )

    _close_bos_flip = None
    _bos_protect_wave_time: str | None = None
    if _trend_dir_changed:
        _close_bos_flip = find_close_bos_flip_for_target_since(
            df,
            waves,
            cfg,
            target_direction=current_trend,
            after_time=state.prev_cycle_last_bar_time,
        )
    if bos_active:
        _flip_bar_ix = bar_idx
        if _close_bos_flip is not None:
            _flip_bar_ix = int(_close_bos_flip[2])
        _wt_bos = bos_flip_map.get(int(_flip_bar_ix))
        if _wt_bos:
            _bos_protect_wave_time = str(_wt_bos)

    if do_close_pos:
        br = bos_per_bar_close_reason(cfg)
        _promoted_ts2 = (
            state.promoted_two_sided_wave_times
            if bool(getattr(cfg, "live_study_promoted_two_sided_as_wave", False))
            else None
        )
        _dir_kw = dict(
            reason=br,
            protected_wave_times=protected_waves,
            promoted_two_sided_wave_times=_promoted_ts2,
            protect_ext_block_from_wave=_bos_protect_wave_time,
            ext1_protection_per_bar=ext1_per_bar,
            current_bar_idx=bar_idx,
            bar_high=bar_high,
            bar_low=bar_low,
            wave_birth_by_time=ext_runtime._wave_birth_by_time,
            main_trend_dir=(
                1 if current_trend == "bull" else -1 if current_trend == "bear" else 0
            ),
        )
        if current_trend == "bear":
            close_positions_by_direction(cfg, direction=+1, **_dir_kw)
        elif current_trend == "bull":
            close_positions_by_direction(cfg, direction=-1, **_dir_kw)

    if do_close_pos and _trend_dir_changed and _close_bos_flip is not None:
        br = bos_per_bar_close_reason(cfg)
        _flip_broken_dir = +1 if state.last_known_trend_dir == "bull" else -1
        close_flip_follower_positions_on_bos(
            cfg,
            broken_dir=_flip_broken_dir,
            bar_high=bar_high,
            bar_low=bar_low,
            reason=br,
            protected_wave_times=protected_waves,
            promoted_two_sided_wave_times=_promoted_ts2,
            ext1_protection_per_bar=ext1_per_bar,
            current_bar_idx=bar_idx,
            protect_ext_block_from_wave=_bos_protect_wave_time,
            wave_birth_by_time=ext_runtime._wave_birth_by_time,
            main_trend_dir=(
                1 if current_trend == "bull" else -1 if current_trend == "bear" else 0
            ),
        )

        from runtime.two_sided_promote_live import on_bos_flip_promote_two_sided
        _comments = get_open_comments() if get_open_comments else []
        state.promoted_two_sided_wave_times = on_bos_flip_promote_two_sided(
            flipped=True,
            existing_promoted=state.promoted_two_sided_wave_times,
            open_comments=_comments,
            cfg=cfg,
        )

    if do_cancel_pend and _trend_dir_changed and _close_bos_flip is not None:
        broken_dir = +1 if state.last_known_trend_dir == "bull" else -1
        cancel_pendings_by_direction(
            cfg, direction=broken_dir, reason="BOS_CANCEL_PENDING", waves=waves,
        )
        cancel_flip_follower_pendings_on_bos(cfg)

    if bos_entry_should_open_on_flip(cfg) and _trend_dir_changed and _close_bos_flip is not None:
        place_live_bos_reentry(
            cfg=cfg,
            new_trend_dir=current_trend,
            broken_trend_dir=state.last_known_trend_dir,
            bar_trend_states=bar_trend_states,
            waves=waves,
            entries_allowed=entries_allowed,
        )

    if is_wave_target_n_family(cfg):
        from runtime.wave_target_n_bar import run_wave_target_n_bar_cycle

        _tp_result = run_wave_target_n_bar_cycle(
            cfg=cfg,
            df=df,
            waves=waves,
            seq_info=seq_info,
            bar_idx=bar_idx,
            birth_by_time=wave_birth_by_time,
            active_counter_wave_times=active_counter_wave_times,
            processed_tp_wave_times=state.processed_tp_wave_times,
            forming_tp_watch=state.forming_tp_watch,
            ext1_per_bar=ext1_per_bar,
            current_trend=current_trend,
            entries_allowed=entries_allowed,
            bar_high=bar_high,
            bar_low=bar_low,
            bar_close=bar_close,
            bar_open=bar_open,
            place_g_extension_counter=place_live_counter_from_g_extension,
            g_extension_closed=g_extension_hit_closed_positions,
            place_fallback_counter=place_live_counter_position,
            log_event_fn=log_event_fn,
        )
        state.forming_tp_watch = _tp_result.forming_tp_watch

    _handle_wf_activations_for_bar(
        cfg=cfg,
        df=df,
        bar_idx=bar_idx,
        bar_close=bar_close,
        wf_activations=wf_activations,
        sent_signals=sent_signals,
        fill_trend_state=fill_trend_state,
    )

    if cfg.trend_filter_enabled and bos_flip_map:
        _retro_wt = bos_flip_map.get(int(bar_idx))
        if _retro_wt:
            _retro_wave = find_wave_by_time(waves, _retro_wt)
            if _retro_wave is not None:
                _sent, state.ext_sl_anchor = _ll._attempt_live_bos_retro_entry(
                    cfg=cfg,
                    wave=_retro_wave,
                    last_bar_idx=bar_idx,
                    last_bar_time=last_bar_time,
                    wave_birth_by_time=wave_birth_by_time,
                    bos_flip_map=bos_flip_map,
                    sent_signals=sent_signals,
                    failed_signals=failed_signals,
                    retro_bos_attempted=state.retro_bos_attempted,
                    signal_digits=signal_digits,
                    entries_allowed=entries_allowed,
                    fill_trend_state=fill_trend_state,
                    ext_sl_anchor=state.ext_sl_anchor,
                    seq_info=seq_info,
                    waves=waves,
                )

    for wave in waves:
        wt = wave["wave_time"]
        sig_key = get_signal_key(wave, digits=signal_digits)
        if bool(wave.get("post_ext_trend_suppressed", False)):
            _trace(wt, bar_idx, "skip:post_ext_trend_suppressed")
            sent_signals.add(sig_key)
            continue
        if bool(wave.get("wf_wave_position", False)):
            _trace(wt, bar_idx, "skip:wf_wave_position")
            continue
        # GATE: post-ext confirmed trend lock blokuje obe strany (parita s hl. smyckou)
        if getattr(cfg, "ext_post_confirmed_trend_lock_blocks_both_sides", True) and wave.get(
            "post_ext_confirmed_trend_lock", False
        ):
            _trace(wt, bar_idx, "skip:post_ext_confirmed_lock")
            sent_signals.add(sig_key)
            log_event_fn(
                cfg, "info", "POST_EXT_CONFIRMED_LOCK_SKIP",
                wave_id=str(wt),
                confirmed_dir=wave.get("post_ext_confirmed_trend_dir"),
                reason="lock_blocks_both_sides",
            )
            continue
        # GATE: vlna bez trend-state snapshotu se neobchoduje (parita s hl. smyckou)
        if (
            cfg.trend_filter_enabled
            and trend_states_per_wave.get(wt) is None
            and trend_states_per_wave.get(str(wt)) is None
            and not bool(wave.get("wf_continued_classic", False))
        ):
            _trace(wt, bar_idx, "skip:no_trend_state_snapshot")
            sent_signals.add(sig_key)
            log_event_fn(
                cfg, "info", "WAVE_NO_TREND_STATE_SNAPSHOT",
                wave_id=str(wt),
                reason="missing_in_trend_states_per_wave",
            )
            continue
        if is_wave_too_old(wt, cfg, ref_time=last_bar_time):
            _trace(wt, bar_idx, "skip:too_old")
            sent_signals.add(sig_key)
            continue
        if not is_wave_in_allowed_session(wt, cfg):
            _trace(wt, bar_idx, "skip:session")
            sent_signals.add(sig_key)
            continue
        if is_wave_too_large(wave["move_pct"], cfg, is_ext=is_ext_wave(wave, cfg)):
            _trace(wt, bar_idx, "skip:too_large")
            sent_signals.add(sig_key)
            continue
        if sig_key in sent_signals:
            _trace(wt, bar_idx, "skip:already_sent")
            continue

        _bos_flip_bar = (
            _ll._bos_flip_bar_for_wave(wt, bos_flip_map)
            if cfg.trend_filter_enabled
            else None
        )
        if not _ll._apply_birth_bar_gate(
            wt,
            wave_birth_by_time=wave_birth_by_time,
            last_bar_idx=bar_idx,
            sent_signals=sent_signals,
            sig_key=sig_key,
            bos_flip_bar=_bos_flip_bar,
            is_bos_retro_candidate=str(wt) in bos_wave_times,
        ):
            _b = wave_birth_by_time.get(str(wt))
            # predporodni sum (birth > bar) nezaznamenavat
            if _b is None or int(bar_idx) >= int(_b):
                _trace(wt, bar_idx, "skip:birth_bar_gate",
                       birth=_b, flip_bar=_bos_flip_bar,
                       is_retro=str(wt) in bos_wave_times)
            continue

        if not bool(getattr(cfg, "wave_position_enabled", True)):
            if _ll._try_live_counter_only_on_wave(
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
            _trace(wt, bar_idx, "skip:entries_not_allowed")
            _ll._log_adx14_entry_blocked(cfg, entry_type="WAVE", wave_id=str(wt))
            sent_signals.add(sig_key)
            continue

        wave_order = wave
        if is_ext_wave(wave, cfg):
            state.ext_sl_anchor = wave
            if two_sided_tracker is not None and two_sided_enabled(cfg):
                two_sided_tracker.clear_all()
        else:
            wave_order, state.ext_sl_anchor = apply_first_opposite_wave_sl_after_ext(
                wave, ext_anchor=state.ext_sl_anchor, cfg=cfg,
            )

        # TWO-SIDED routing + parent-skip (parita s engine/hl. smyckou).
        if two_sided_tracker is not None:
            ts_current = trend_states_per_wave.get(str(wt))
            if two_sided_enabled(cfg):
                two_sided_tracker.link_counter_b_wave_if_matches(
                    wave_order, waves, cfg, trend_states_per_wave=trend_states_per_wave,
                )
            waves_for_two_sided = (
                two_sided_tracker.waves_with_armed_parents(waves)
                if two_sided_enabled(cfg)
                else waves
            )
            prev_wave = find_parent_wave_for_two_sided(
                waves_for_two_sided, wave_order, cfg,
                trend_states_per_wave=trend_states_per_wave,
            )
            two_sided_only = False
            if (
                two_sided_enabled(cfg)
                and bool(getattr(cfg, "wave_position_enabled", True))
                and prev_wave is not None
            ):
                parent_wt = str(prev_wave.get("wave_time", ""))
                touched = two_sided_tracker.fib_was_touched(parent_wt)
                ts_parent = trend_states_per_wave.get(parent_wt)
                if should_open_two_sided_counter(
                    prev_wave, wave_order, cfg,
                    parent_fib_touched=touched,
                    parent_trend_state=ts_parent,
                    counter_trend_state=ts_current,
                ):
                    two_sided_only = True
                    two_sided_tracker.register_counter_b_wave(str(wt))
                    if wave_counter_two_sided_orders_enabled(cfg):
                        counter = prepare_ts2_mirror_entry_signal(wave_order, cfg)
                        if send_order(
                            counter, cfg, entry_mode=cfg.entry_mode,
                            trend_state_at_fill=fill_trend_state,
                            is_two_sided_mirror=True, bar_close=bar_close,
                            bar_open=bar_open,
                        ):
                            sent_signals.add(sig_key)
                            failed_signals.pop(sig_key, None)
                            two_sided_tracker.discard_parent(parent_wt)
                        else:
                            prev = failed_signals.get(
                                sig_key, {"wave": counter, "attempts": 0}
                            )
                            prev["wave"] = counter
                            prev["attempts"] = int(prev.get("attempts", 0)) + 1
                            failed_signals[sig_key] = prev
                            continue
            skip_parent_primary = skip_primary_entry_on_parent_wave(
                wave_order, cfg, trend_state=ts_current,
            )
            skip_b_primary = two_sided_enabled(cfg) and two_sided_tracker.is_b_wave_for_any_parent(
                str(wt)
            )
            if two_sided_only or skip_parent_primary or skip_b_primary:
                _trace(wt, bar_idx, "skip:two_sided_primary",
                       two_sided_only=bool(two_sided_only),
                       skip_parent=bool(skip_parent_primary),
                       skip_b=bool(skip_b_primary))
                sent_signals.add(sig_key)
                log_event_fn(
                    cfg, "info", "MISSED_BAR_TWO_SIDED_SKIP_PRIMARY",
                    wave_id=str(wt),
                    two_sided_only=bool(two_sided_only),
                    skip_parent=bool(skip_parent_primary),
                    skip_b=bool(skip_b_primary),
                )
                continue

        # TREND FILTER (BOS) — primarni WAVE (two-sided counter uz probehl vyse).
        if cfg.trend_filter_enabled:
            ts = trend_states_per_wave.get(wt)
            allowed, _reason = wave_allowed_for_entry(wave, ts, cfg)
            if not allowed and str(wt) in bos_wave_times and not _ll._wave_is_wf_origin(wave):
                allowed = True
            if not allowed:
                _trace(wt, bar_idx, "skip:trend_filter", reason=_reason)
                continue

        placed_meta: Dict[str, Any] = {}
        if send_order(
            wave_order,
            cfg,
            entry_mode=cfg.entry_mode,
            placed_meta=placed_meta,
            trend_state_at_fill=fill_trend_state,
            bar_close=bar_close,
        ):
            _trace(wt, bar_idx, "SENT_PRIMARY")
            _ll._maybe_place_live_counter_from_tp(
                cfg=cfg,
                wave=wave_order,
                seq_info=seq_info,
                tp_price=placed_meta.get("tp_price"),
                all_waves=waves,
                entries_allowed=entries_allowed,
            )
            sent_signals.add(sig_key)
            failed_signals.pop(sig_key, None)
            log_event_fn(
                cfg,
                "info",
                "MISSED_BAR_WAVE_ENTRY",
                wave_id=str(wt),
                bar_idx=int(bar_idx),
            )

    if current_trend in ("bull", "bear"):
        state.last_known_trend_dir = current_trend
    state.prev_cycle_last_bar_time = last_bar_time
    return state
