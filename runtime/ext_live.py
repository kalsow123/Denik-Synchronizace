"""
EXT blok pro live bota — parita s `backtest.engine` (sekundarni, counter cas/BOS).

Pouziva sdilene vypocty z `strategy.ext_logic` a MT5 ordery z `infra.orders`.
Stav (co uz bylo odeslano) se drzi v pameti + synchronizuje z MT5 comment prefixu.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional, Set

import MetaTrader5 as mt5
import pandas as pd

from config.bot_config import BotConfig
from config.enums import EntryMode
from core.logging_utils import log_event
from core.signal_keys import get_signal_key
from infra.live_order_guard import (
    block_duplicate_ext_counter_bos,
    block_duplicate_ext_counter_time,
    block_duplicate_ext_secondary,
)
from infra.orders import (
    place_ext_counter_market,
    place_ext_secondary_order,
)
from infra.session_manager import get_broker_now
from strategy.wave_sequence import (
    build_ext1_wave_times,
    compute_ext1_protection_bars,
    compute_wave_2_no_tp_protected_waves,
    compute_wave_sequence_info_per_wave,
    propagate_seq_info_to_waves,
)
from strategy.ext_logic import (
    ENTRY_TAG_EXT_COUNTER_BOS,
    ENTRY_TAG_EXT_COUNTER_TIME,
    ENTRY_TAG_EXT_SECONDARY,
    bar_time_at_or_past_counter_time,
    bos_triggered_for_ext_close,
    ext_bos_allowed_at_bar,
    build_ext_bos_state_map,
    compute_counter_signal,
    compute_secondary_signal,
    ext_bos_market_entry_allowed,
    ext_counter_time_may_open,
    ext_counter_time_suppressed_at_bar,
    is_ext_wave,
    parse_ext_counter_time,
)
log = logging.getLogger(__name__)


class ExtLiveRuntime:
    """Drzi EXT stav mezi cykly live loopu."""

    def __init__(self) -> None:
        self._secondary_sent: Set[str] = set()
        self._counter_time_done: Set[str] = set()
        self._counter_bos_done: Set[str] = set()
        self._bos_triggered: Set[str] = set()
        self._wave_birth_by_time: dict[str, int] = {}
        self._ext_counter_suppress_from_bar: dict[str, int] = {}
        self._ext_forming_first_bar: dict[str, int] = {}
        self._all_ext_waves: list[dict[str, Any]] = []
        self._ext_bos_state: dict[str, str] = {}
        self._ext1_protection_per_bar: list[bool] = []
        self._ext1_wave_times: set[str] = set()
        self._ext1_rrr_edge_done_bar_time: str | None = None

    def sync_from_mt5(self, cfg: BotConfig) -> None:
        """Po startu / wake-up doplni 'done' sady z existujicich MT5 orderu/pozic."""
        from infra.orders import (
            get_ext_counter_bos_wave_times,
            get_ext_counter_time_wave_times,
            get_ext_secondary_wave_times,
        )

        self._secondary_sent |= get_ext_secondary_wave_times(cfg)
        self._counter_time_done |= get_ext_counter_time_wave_times(cfg)
        self._counter_bos_done |= get_ext_counter_bos_wave_times(cfg)
        self._bos_triggered |= self._counter_bos_done

    def refresh_simulation(
        self,
        df: pd.DataFrame,
        cfg: BotConfig,
        *,
        seq_info=None,
        protected_waves=None,
        waves: list | None = None,
    ) -> None:
        """Predpocet birth / suppress / forming map z Pine simulace (stejne jako engine)."""
        if not bool(getattr(cfg, "ext_enabled", False)):
            self._all_waves = []
            self._all_ext_waves = []
            self._wave_birth_by_time = {}
            self._ext_counter_suppress_from_bar = {}
            self._ext_forming_first_bar = {}
            self._ext_bos_state = {}
            self._seq_info = seq_info or {}
            self._protected_waves = protected_waves or set()
            src = list(waves or [])
            self._ext1_protection_per_bar = compute_ext1_protection_bars(df, src, cfg)
            self._ext1_wave_times = build_ext1_wave_times(src)
            return
        from strategy.wave_detection_pine import run_pine_wave_simulation

        all_waves, wave_birth, ext_suppress, ext_forming = run_pine_wave_simulation(
            df, cfg,
        )
        self._all_waves = all_waves
        self._wave_birth_by_time = dict(wave_birth)
        self._ext_counter_suppress_from_bar = dict(ext_suppress)
        self._ext_forming_first_bar = dict(ext_forming)
        self._all_ext_waves = [w for w in all_waves if is_ext_wave(w, cfg)]
        self._ext_bos_state = build_ext_bos_state_map(
            all_waves, wave_birth, cfg,
        )
        
        if seq_info is None or protected_waves is None:
            self._seq_info = compute_wave_sequence_info_per_wave(df, all_waves, cfg)
            propagate_seq_info_to_waves(all_waves, self._seq_info)
            self._protected_waves = compute_wave_2_no_tp_protected_waves(all_waves, self._seq_info, cfg)
        else:
            self._seq_info = seq_info
            self._protected_waves = protected_waves

        self._ext1_protection_per_bar = compute_ext1_protection_bars(df, all_waves, cfg)
        self._ext1_wave_times = build_ext1_wave_times(all_waves)

    def run_ext1_rrr_better_exit(self, cfg: BotConfig, df: pd.DataFrame) -> None:
        """RRR_FIXED market exit EXT1 pozic po skonceni EXT-1 ochranneho okna."""
        from runtime.ext1_protect_live import maybe_rrr_fixed_better_exit_after_ext1_protect_end

        self._ext1_rrr_edge_done_bar_time = maybe_rrr_fixed_better_exit_after_ext1_protect_end(
            cfg,
            df,
            ext1_protection_per_bar=self._ext1_protection_per_bar,
            ext1_wave_times=self._ext1_wave_times,
            rrr_edge_done_bar_time=self._ext1_rrr_edge_done_bar_time,
        )

    def process_cycle(
        self,
        cfg: BotConfig,
        df: pd.DataFrame,
        *,
        entries_allowed: bool,
        signal_digits: int,
        sent_signals: Set[str],
        on_adx14_blocked: Optional[Callable[[str], None]] = None,
    ) -> None:
        """
        Jedna iterace live loopu: sekundarni EXT, counter cas, EXT BOS + close trend.
        """
        if not bool(getattr(cfg, "ext_enabled", False)) or df is None or df.empty:
            return

        last_ix = len(df) - 1
        ext1_per_bar = self._ext1_protection_per_bar
        bar = df.iloc[last_ix]
        bar_time = pd.Timestamp(bar["time"]).to_pydatetime()
        close_ = float(bar["close"])
        broker_now = get_broker_now(cfg)

        if bool(getattr(cfg, "ext_secondary_enabled", False)):
            self._process_secondary_batch(
                cfg, bar, last_ix, entries_allowed=entries_allowed,
                signal_digits=signal_digits, sent_signals=sent_signals,
                on_adx14_blocked=on_adx14_blocked,
            )

        if bool(getattr(cfg, "ext_counter_enabled", False)):
            self._process_counter_time(
                cfg, df, last_ix, broker_now=broker_now,
                entries_allowed=entries_allowed,
                on_adx14_blocked=on_adx14_blocked,
            )

        from strategy.ext_logic import ext_bos_on_bar_handler_enabled

        if ext_bos_on_bar_handler_enabled(cfg):
            self._process_ext_bos(
                cfg,
                df=df,
                last_ix=last_ix,
                bar_high=float(bar["high"]),
                bar_low=float(bar["low"]),
                close_=close_,
                bar_time=bar_time,
                ext1_protection_per_bar=ext1_per_bar,
                entries_allowed=entries_allowed,
                on_adx14_blocked=on_adx14_blocked,
            )

    def _process_secondary_batch(
        self,
        cfg: BotConfig,
        bar: pd.Series,
        bar_idx: int,
        *,
        entries_allowed: bool,
        signal_digits: int,
        sent_signals: Set[str],
        on_adx14_blocked: Optional[Callable[[str], None]],
    ) -> None:
        for wave in list(self._all_ext_waves):
            wt = str(wave["wave_time"])
            if wt in self._secondary_sent:
                continue
            birth = self._wave_birth_by_time.get(wt)
            if birth is not None and int(birth) > int(bar_idx):
                continue

            sec_signal = compute_secondary_signal(wave, cfg)
            self._secondary_sent.add(wt)
            if sec_signal is None:
                continue

            sig_key = get_signal_key(
                sec_signal,
                digits=signal_digits,
                entry_tag=ENTRY_TAG_EXT_SECONDARY,
            )
            if sig_key in sent_signals:
                continue

            if not entries_allowed:
                if on_adx14_blocked:
                    on_adx14_blocked("EXT_SECONDARY")
                continue

            ok = place_ext_secondary_order(
                sec_signal, cfg, entry_mode=cfg.entry_mode, ext_wave_time=wt,
                bar_close=float(bar["close"]),
            )
            if ok:
                sent_signals.add(sig_key)
                log_event(
                    cfg, "info", "EXT_SECONDARY_PLACED",
                    wave_time=wt, entry_tag=ENTRY_TAG_EXT_SECONDARY,
                )

    def _process_counter_time(
        self,
        cfg: BotConfig,
        df: pd.DataFrame,
        bar_idx: int,
        *,
        broker_now,
        entries_allowed: bool,
        on_adx14_blocked: Optional[Callable[[str], None]],
    ) -> None:
        counter_t = parse_ext_counter_time(getattr(cfg, "ext_counter_time", None))
        if counter_t is None:
            return
        if not bar_time_at_or_past_counter_time(broker_now, counter_t):
            return

        open_ = float(df.iloc[bar_idx]["open"])
        for wave in list(self._all_ext_waves):
            wt = str(wave["wave_time"])
            birth_bi = self._wave_birth_by_time.get(wt)
            form_bi = self._ext_forming_first_bar.get(wt, birth_bi)
            try:
                active_from = int(form_bi if form_bi is not None else birth_bi)
            except (TypeError, ValueError):
                active_from = -1
            if bar_idx < active_from:
                continue

            suppressed = ext_counter_time_suppressed_at_bar(
                wt, bar_idx, self._ext_counter_suppress_from_bar,
            )
            bos_state = self._ext_bos_state.get(wt, "armed")
            if not ext_counter_time_may_open(
                bos_state=bos_state,
                suppressed_after_subsequent_wave=suppressed,
                counter_time_already_done=(wt in self._counter_time_done),
                counter_bos_already_done=(wt in self._counter_bos_done),
            ):
                continue

            counter_dir = -int(wave["dir"])
            is_buy = counter_dir == 1
            tick = mt5.symbol_info_tick(cfg.symbol)
            if tick is None:
                return
            mp = float(tick.ask if is_buy else tick.bid)
            counter_sig = compute_counter_signal(
                wave, cfg, source="time", market_price=mp,
            )
            if counter_sig is None:
                continue

            if not entries_allowed:
                if on_adx14_blocked:
                    on_adx14_blocked("EXT_COUNTER_TIME")
                continue

            if block_duplicate_ext_counter_time(cfg, wt):
                self._counter_time_done.add(wt)
                continue

            ok = place_ext_counter_market(
                cfg,
                counter_sig=counter_sig,
                ext_wave_time=wt,
                source="time",
            )
            if ok:
                self._counter_time_done.add(wt)
                log_event(
                    cfg, "info", "EXT_COUNTER_TIME_PLACED",
                    wave_time=wt, entry_tag=ENTRY_TAG_EXT_COUNTER_TIME,
                )

    def _process_ext_bos(
        self,
        cfg: BotConfig,
        df: pd.DataFrame,
        *,
        last_ix: int,
        bar_high: float,
        bar_low: float,
        close_: float,
        bar_time,
        ext1_protection_per_bar: list[bool],
        entries_allowed: bool,
        on_adx14_blocked: Optional[Callable[[str], None]],
    ) -> None:
        from infra.orders import close_positions_by_direction

        for wave in list(self._all_ext_waves):
            wt = str(wave["wave_time"])
            if not ext_bos_allowed_at_bar(wave, last_ix):
                continue
            if not bos_triggered_for_ext_close(wave, close_):
                continue

            if wt not in self._bos_triggered:
                self._bos_triggered.add(wt)
                log_event(cfg, "info", "EXT_BOS_TRIGGERED", wave_time=wt)

            if bool(getattr(cfg, "ext_close_trend_positions_on_bos", False)):
                ext_dir = int(wave["dir"])
                closed = close_positions_by_direction(
                    cfg,
                    ext_dir,
                    reason="EXT_BOS_CLOSE",
                    protected_wave_times=self._protected_waves,
                    protect_ext_block_from_wave=wt,
                    ext1_protection_per_bar=ext1_protection_per_bar,
                    current_bar_idx=last_ix,
                    bar_high=bar_high,
                    bar_low=bar_low,
                )
                if closed:
                    log_event(
                        cfg, "info", "EXT_BOS_TREND_CLOSED",
                        wave_time=wt, closed_count=int(closed), ext_dir=ext_dir,
                    )

            if not bool(getattr(cfg, "ext_counter_enabled", False)):
                continue

            if wt in self._counter_time_done or wt in self._counter_bos_done:
                continue

            bos_state = self._ext_bos_state.get(wt, "armed")
            if not ext_bos_market_entry_allowed(bos_state):
                continue

            counter_dir = -int(wave["dir"])
            is_buy = counter_dir == 1
            tick = mt5.symbol_info_tick(cfg.symbol)
            if tick is not None:
                mp = float(tick.ask if is_buy else tick.bid)
                counter_sig = compute_counter_signal(
                    wave, cfg, source="bos", market_price=mp,
                )
                if counter_sig is not None:
                    if entries_allowed:
                        if not block_duplicate_ext_counter_bos(cfg, wt):
                            ok = place_ext_counter_market(
                                cfg,
                                counter_sig=counter_sig,
                                ext_wave_time=wt,
                                source="bos",
                            )
                            if ok:
                                self._counter_bos_done.add(wt)
                                log_event(
                                    cfg, "info", "EXT_COUNTER_BOS_PLACED",
                                    wave_time=wt,
                                    entry_tag=ENTRY_TAG_EXT_COUNTER_BOS,
                                )
                    elif on_adx14_blocked:
                        on_adx14_blocked("EXT_COUNTER_BOS")
