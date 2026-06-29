"""
Pouziva PRIMO strategicke moduly z trading_bot/strategy/:
  - detect_waves         (wave_detection.py -> wave_detection_pine.py)
  - get_signal_key       (z core/signal_keys.py)
  - calc_lot_backtest    (z core/risk.py - bez zavislosti na MT5)

Poradi operaci na kazdem baru odpovida live logice:
  1) pending trigger (drive nez nova vlna)
  2) SL/TP check otevrenych pozic
  3) wave detection + zpracovani noveho signalu
  4) expiry pending orderu

Simulovana logika:
  - Pending STOP/LIMIT trigger s gap fill (jako MT5)
  - SL/TP hit od baru AFTER entry (identicke s simulate_pine)
  - market_fallback / limit_fallback / no_fallback
  - ORDER_EXPIRY_DAYS (stejne jako live: business_time_delta, vikendy So+Ne se nezapocitavaji)
  - MAX_WAVE_AGE_HOURS
  - Deduplikace pres get_signal_key (dir + fib50 + sl)
  - Wave session filter (broker time)
"""
from __future__ import annotations

import pandas as pd
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from config.bot_config import BotConfig, abort_fib_shift_sl_mode
from config.enums import EntryMode, PendingCancelMode, TPMode
from backtest.sim_params import DEFAULT_BACKTEST_SLIPPAGE, DEFAULT_BACKTEST_SPREAD
from backtest.ohlc_arrays import ohlc_from_dataframe

# IMPORT STRATEGIE PRIMO Z TRADING_BOT - sdilime logiku s live botem!
from strategy.wave_detection import detect_waves
from strategy.filters import is_wave_in_allowed_session, is_wave_too_large
from strategy.trend_bos import (
    bos_per_bar_close_reason,
    collect_bos_flip_events,
    compute_bos_wave_flip_map,
    compute_close_based_bos_flip_bar_indices,
    compute_trend_states_per_bar,
    compute_trend_states_per_wave,
    build_pp_trend_confirmed_per_bar,
    find_pp_candidate_wave,
    pp_wave_eligible_for_break,
    _detect_close_bos_timeline_flips,
    reconcile_bos_flip_map_with_wave_sequence,
    _wave_is_wf_origin,
    entry_allowed_at_fill_bar,
    resolve_effective_tp,
    bos_entry_in_rrr_fixed_enabled,
    bos_entry_should_open_on_flip,
    bos_flip_handler_should_run,
    tp_mode_uses_bos_per_bar_exit,
    wave_allowed_for_entry,
)
from strategy.wave_sequence import (
    WaveSequenceInfo,
    compute_ladder_sl_from_wave_size,
    compute_sl_pct_from_entry_and_sl,
    compute_sl_price_from_pct,
    compute_wave_sequence_info_per_wave,
    propagate_seq_info_to_waves,
    sync_wave_sequence_state,
    compute_ext1_protection_bars,
    _get_ext1_protect_flag,
    compute_wave_2_no_tp_protected_waves,
    compute_wave_target_tp_price,
    find_wave_by_time,
    is_tp_wave_index,
    wave_counter_min_sl_pct,
    compute_wave_counter_take_profit,
    compute_wave_counter_sl_setup,
    is_wave_counter_trade,
    is_two_sided_mirror_trade,
    is_bos_flip_follower_trade,
    should_close_trade_on_bos_flip,
    should_close_trade_on_tp_wave_n,
)
from strategy.wave_target_n_mode import WAVE_TARGET_N_FAMILY, is_wave_target_n_g
from strategy.two_sided import (
    TwoSidedTracker,
    bar_touched_price,
    find_parent_wave_for_two_sided,
    parent_monitor_start_bar,
    parent_wave_qualifies,
    prepare_two_sided_counter_signal,
    skip_primary_entry_on_parent_wave,
    should_open_two_sided_counter,
    two_sided_enabled,
    wave_counter_two_sided_enabled,
    wave_counter_two_sided_orders_enabled,
)
from core.signal_keys import get_signal_key
from core.risk import calc_lot_backtest, round_to_step
from strategy.ext_logic import (
    ENTRY_TAG_EXT_SECONDARY,
    advance_ext_bos_state,
    apply_first_opposite_wave_sl_after_ext,
    bar_time_at_or_past_counter_time,
    bos_triggered_for_ext_close,
    ext_bos_allowed_at_bar,
    compute_counter_signal,
    compute_ext_secondary_take_profit,
    compute_secondary_signal,
    is_ext_secondary_trade,
    is_ext_wave,
    is_ext_block_trade_from_wave,
    is_trade_within_parent_ext_window,
    is_ext_counter_trade,
    is_ext_primary_wave_trade,
    ext_bos_market_entry_allowed,
    ext_bos_on_bar_handler_enabled,
    ext_counter_time_may_open,
    ext_counter_time_suppressed_at_bar,
    has_open_ext_counter_peer,
    parse_ext_counter_time,
)
from core.trading_days import business_time_delta
from backtest.position_cap import apply_pending_prune, enforce_market_overflow
from backtest.causal_policy import (
    bos_flip_wave_at_bar,
    causal_debug_summary,
    policy_from_cfg,
    retro_bos_entry_allowed,
    wave_for_entry_at_bar,
)
from backtest.adx14_gate_sim import Adx14BacktestSim
from strategy.wick_fakeout import (
    WAVE_ORIGIN_NORMAL,
    WAVE_ORIGIN_WF,
    WickFakeoutTracker,
    build_wf_wave,
    resume_classic_waves_after_wf,
)
from strategy.ext_range import pending_protected_from_bos_direction_cancel

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - jen typy
    from backtest.executor import BacktestExecutor, BarContext, Executor


def _resolve_tp_mode(cfg: BotConfig) -> TPMode:
    """Vrat TPMode z BotConfig (bezpecne pro string / enum / neznamou hodnotu)."""
    tpm = getattr(cfg, "tp_mode", TPMode.RRR_FIXED)
    if isinstance(tpm, str):
        try:
            return TPMode(tpm)
        except ValueError:
            return TPMode.RRR_FIXED
    return tpm


def _resolve_pending_cancel_mode(cfg: BotConfig) -> PendingCancelMode:
    """Vrat PendingCancelMode z BotConfig (bezpecne pro string / enum / neznamou)."""
    pcm = getattr(cfg, "pending_cancel_mode", PendingCancelMode.NUMBER)
    if isinstance(pcm, str):
        try:
            return PendingCancelMode(pcm)
        except ValueError:
            return PendingCancelMode.NUMBER
    return pcm


def _dt_for_business_age(d: datetime) -> datetime:
    """
    Jednotny datetime pro business_time_delta (shoda s infra/orders.cancel_expired_pending).
    pd.Timestamp i timezone-aware hodnoty z CSV sjednotime na naive UTC.
    """
    if isinstance(d, pd.Timestamp):
        d = d.to_pydatetime()
    if d.tzinfo is not None:
        return d.astimezone(timezone.utc).replace(tzinfo=None)
    return d

# ───── BECKTESTER ENGINE ──────────────────────────

# ---------------------------------------------------------------------------
# Interni datove struktury
# ---------------------------------------------------------------------------

class PendingOrder:
    """
    PendingOrder — interni datova struktura backtest engine.

    Pole:
      tp: Optional[float] — `None` znamena "ZADNY TP" (broker MT5 = 0.0).
                            Engine v _check_sl_tp pri tp=None TP-kontrolu preskoci.
      is_counter: bool   — True pokud jde o protipozici (LIMIT v opacnem smeru
                            trendu na TP urovni TP-vlny, WAVE_TARGET_N rezim).
                            Protipozice maji vlastni semantiku:
                              - neexpiruji (jsou ignorovany v `_expire_pending`)
                              - rusi se POUZE pri BOS flipu
                              - lot/SL z ladderu, TP=None az do prvni TP-vlny
                                v nove trend dir.
      is_bos_reentry: bool — True pokud jde o re-entry MARKET po BOS flipu.
                              Realne se ulozi rovnou jako OpenTrade, PendingOrder
                              tu slouzi jen jako kontejner kvuli signature
                              OpenTrade(pending=...).
      is_pp: bool      — True pokud jde o PP pending (Push-through po close-baru
                          nad wave HIGH / pod wave LOW v trendu). PP pendingy
                          neexpiruji casem; rusi se pri BOS flipu (broken_dir)
                          nebo pri vytvoreni noveho PP z dalsi vlny v trendu.
      is_two_sided_mirror: bool — True pokud jde o "mirror" pozici opacneho
                          smeru otevrenou pri two_sided_entry_enabled.
    """
    def __init__(self, signal: dict, order_type: str, entry_price: float,
                 sl: float, tp: Optional[float], lot: float, created_bar: int,
                 created_time: datetime,
                 *,
                 dir_override: Optional[int] = None,
                 is_counter: bool = False,
                 is_bos_reentry: bool = False,
                 is_pp: bool = False,
                 is_two_sided_mirror: bool = False,
                 entry_tag: str = "base",
                 is_ext: bool = False):
        self.signal = signal
        self.order_type = order_type
        self.entry_price = entry_price
        self.sl = sl
        self.tp = tp
        self.lot = lot
        self.created_bar = created_bar
        self.created_time = created_time
        self.wave_time = signal["wave_time"]
        self.dir = dir_override if dir_override is not None else signal["dir"]
        self.is_counter = bool(is_counter)
        self.is_bos_reentry = bool(is_bos_reentry)
        self.is_pp = bool(is_pp)
        self.is_two_sided_mirror = bool(is_two_sided_mirror)
        self.entry_tag = str(entry_tag or "base")
        self.is_ext = bool(is_ext)
        self.wave_origin = str(signal.get("wave_origin", "normal"))


class OpenTrade:
    """
    OpenTrade — otevrena pozice v backtestu.

    Pole:
      tp: Optional[float] — None = bez TP (engine TP nekontroluje, jen SL/BOS).
      is_counter:    True pokud jde o protipozici (chrani pred BOS-exit handlerem
                     do prvniho BOS flipu, ktery ji pravdepodobne pretoci na
                     stranu trendu).
      is_bos_reentry: True pokud jde o re-entry market po BOS flipu.
    """
    def __init__(self, pending: PendingOrder, entry_bar: int, actual_entry: float,
                 entry_time: datetime, entry_type: str, sl: float,
                 tp: Optional[float]):
        self.pending = pending
        self.entry_bar = entry_bar
        self.actual_entry = actual_entry
        self.entry_time = entry_time
        self.entry_type = entry_type
        self.sl = sl
        self.tp = tp
        self.lot = pending.lot
        self.dir = pending.dir
        self.wave_time = pending.wave_time
        self.is_counter = bool(getattr(pending, "is_counter", False))
        self.is_bos_reentry = bool(getattr(pending, "is_bos_reentry", False))
        self.is_pp = bool(getattr(pending, "is_pp", False))
        self.is_two_sided_mirror = bool(getattr(pending, "is_two_sided_mirror", False))
        self.entry_tag = str(getattr(pending, "entry_tag", "base"))
        self.is_ext = bool(getattr(pending, "is_ext", False))
        self.wave_origin = str(getattr(pending, "wave_origin", "normal"))


class ClosedTrade:
    def __init__(self, trade: OpenTrade, close_bar: int, close_price: float,
                 close_time: datetime, close_reason: str):
        self.wave_time = trade.wave_time
        self.dir = trade.dir
        self.lot = trade.lot
        self.entry_price = trade.actual_entry
        self.entry_time = trade.entry_time
        self.entry_type = trade.entry_type
        self.sl = trade.sl
        self.tp = trade.tp
        self.close_price = close_price
        self.close_time = close_time
        self.close_bar = close_bar
        self.close_reason = close_reason
        self.bars_held = close_bar - trade.entry_bar
        self.pnl_usd: float = 0.0
        # Diagnosticke flagy — propagace z OpenTrade pro statistiky / reporty.
        self.is_counter = bool(getattr(trade, "is_counter", False))
        self.is_bos_reentry = bool(getattr(trade, "is_bos_reentry", False))
        self.is_pp = bool(getattr(trade, "is_pp", False))
        self.is_two_sided_mirror = bool(getattr(trade, "is_two_sided_mirror", False))
        self.entry_tag = str(getattr(trade, "entry_tag", "base"))
        self.is_ext = bool(getattr(trade, "is_ext", False))
        self.wave_origin = str(getattr(trade, "wave_origin", "normal"))


# ---------------------------------------------------------------------------
# Pomocne funkce
# ---------------------------------------------------------------------------

    # Normalizace cfg.entry:mode na string - FUnkce market_fallback etc..
def _entry_mode_str(cfg: BotConfig) -> str:
    em = cfg.entry_mode
    if isinstance(em, EntryMode):
        return em.value
    return str(em)


# ---------------------------------------------------------------------------
# Hlavni engine
# ---------------------------------------------------------------------------

    # Bar-by-bar simulator historických OHLC dat.
    # Strategie z trading_bot.core (from trading bot)
class BacktestEngine:

    def __init__(
        self,
        cfg: BotConfig,
        backtest_position_cap_mode: str = "off",
        backtest_max_open_positions: int | None = None,
        *,
        backtest_spread: float | None = None,
        backtest_slippage: float | None = None,
    ):
        self.cfg = cfg
        self.backtest_position_cap_mode = backtest_position_cap_mode
        self.backtest_max_open_positions = backtest_max_open_positions
        self.backtest_spread = (
            float(backtest_spread)
            if backtest_spread is not None
            else DEFAULT_BACKTEST_SPREAD
        )
        self.backtest_slippage = (
            float(backtest_slippage)
            if backtest_slippage is not None
            else DEFAULT_BACKTEST_SLIPPAGE
        )
        self.pending_orders: List[PendingOrder] = []
        self.open_trades: List[OpenTrade] = []
        self.closed_trades: List[ClosedTrade] = []
        self.sent_signals: set = set()
        self.wave_debug: dict = {}
        self.signal_key_digits: int = 4
        self.last_waves: List[dict] = []
        self.last_waves_for_visual: List[dict] = []
        self.wave_birth_by_time: dict = {}
        # Udalosti pendingu pro vizual (expirace / prune / vznik) — lehky seznam dictu.
        self.pending_vis: List[dict] = []
        # TREND FILTER (BOS) — snapshot trend stavu k baru narozeni kazde vlny.
        # Plnene jen pokud cfg.trend_filter_enabled=True; jinak prazdne (no-op).
        self.trend_states_per_wave: dict = {}
        # BOS flipy (cas, swing uroven, popisek) — jen pri retain_wave_snapshot pro HTML/PNG.
        self.bos_flip_events: List = []
        # Per-wave index v trendu + reference predchozi same-dir vlny.
        # Pouziva se pro WAVE_TARGET_N a take pro WAVE_COUNTER mimo wave_target_n.
        self.wave_sequence_info: dict = {}
        # Lookup vlny podle wave_time (pro pristup prev_same_dir vlny atd.).
        self.waves_by_wave_time: dict = {}
        # Cache: TPMode pro rychly test bez re-parsovani stringu.
        self._tp_mode: TPMode = _resolve_tp_mode(self.cfg)
        # Cache: PendingCancelMode — funkce ovládající ruseni pending LIMITu
        # NEZAVISLE na tp_mode (off/number/trend).
        self._pending_cancel_mode: PendingCancelMode = _resolve_pending_cancel_mode(self.cfg)
        self.adx14_sim: Adx14BacktestSim | None = None
        self._ext_active_waves: List[dict] = []
        self._ext_sl_anchor: Optional[dict] = None
        self._ext_secondary_sent: set = set()
        self._ext_counter_time_done: set = set()
        self._ext_counter_suppress_from_bar: dict = {}
        self._ext_forming_first_bar: dict = {}
        self._all_ext_waves: List[dict] = []
        self._ext_counter_bos_done: set = set()
        self._ext_bos_triggered: set = set()
        # EXT BOS market: po EXT aktivni; rusi se prvni novou vlnou ve smeru EXT.
        self._ext_bos_state: dict[str, str] = {}
        self._two_sided_tracker = TwoSidedTracker()
        self._two_sided_fired_wave_times: set[str] = set()
        # Wick Fakeout Recovery (WF) tracker — per-bar sledování WF okna.
        self._wf_tracker = WickFakeoutTracker()
        # WF continuation vlny pro visual_waves (černé pozadí v plotu).
        self._wf_visual_waves: List[dict] = []
        self._forming_tp_watch = None
        # BOS vlna (vlna ktera ZPUSOBI close-based flip trendu) — vzdy
        # vykreslena ve vizualu A vzdy vstupove dostupna, i kdyz trend_filter
        # ji jinak povazoval za wave_against_trend.
        # Mapa: bar_idx flipu -> wave-dict bos-vlny (jedna vlna na flip).
        self._bos_flip_wave_by_bar: dict[int, dict] = {}
        # Mnozina wave_time bos-vln pro vyjimky ve filtrech / vizualu.
        self._bos_wave_times: set[str] = set()
        self._visual_bos_wave_times: set[str] = set()
        # Sleduje, kterym BOS vlnam uz proslo "retro" otevreni — at se na
        # dalsich barech (po flipu) neopakuje.
        self._retro_bos_attempted: set[str] = set()
        self._causal_policy = policy_from_cfg(cfg)
        self._run_df: pd.DataFrame | None = None
        # Executor (I/O hranice) — nastaveno v run() po prepare(); BacktestExecutor
        # obaluje in-memory simulaci. process_bar provadi ordery JEN pres nej.
        self._executor: "Executor | None" = None
        # WaveSource pouzity v prepare() (default LegacyWaveSource) — viz wave_source.py.
        self._wave_source = None

    def prepare(
        self,
        df: pd.DataFrame,
        *,
        wave_source=None,
    ) -> "BarContext":
        """
        Reset stavu + precompute → vrati `BarContext` pro `process_bar()`.

        Default `wave_source` = LegacyWaveSource (legacy_precompute): reprodukuje
        DNESNI precompute (run_pine_wave_simulation pres cache + wave_plus extend /
        merge / wick cleanup s vedomim budoucnosti). Vysledek je bit-identicky.
        """
        self.pending_orders.clear()
        self.open_trades.clear()
        self.closed_trades.clear()
        self.sent_signals.clear()
        self.last_waves = []
        self.wave_birth_by_time = {}
        self.pending_vis = []
        self.bos_flip_events = []
        self._ext1_protection_per_bar = []
        self._ext1_wave_times: set[str] = set()
        self.wave_debug = {
            "waves_detected_total": 0,
            "waves_birth_total": 0,
            "waves_skipped_too_old": 0,
            "waves_skipped_session": 0,
            "waves_skipped_trend_filter": 0,
            "waves_skipped_wave_max_pct": 0,
            "waves_skipped_duplicate": 0,
            "waves_skipped_sl_breached": 0,
            "waves_skipped_abort_fib": 0,
            "waves_opened_abort_shift_sl": 0,
            "waves_skipped_wave_positions_disabled": 0,
            "waves_accepted": 0,
            "orders_created_pending": 0,
            "orders_created_market": 0,
            "orders_created_stop_fallback": 0,
            "orders_skipped_limit_fallback_deprecated": 0,
            "position_cap_pending_pruned": 0,
            "position_cap_market_closed": 0,
            "bos_exit_trades_closed": 0,
            "bos_exit_pending_cancelled": 0,
            "ext_range_pending_bos_cancel_skipped": 0,
            # SL SAFETY pri BOS exitu: pocet pozic, ktere BOS handler chtel zavrit
            # na close baru, ale jejich SL byl behem baru porusen (high/low presahly
            # trade.sl) → zavreni na SL cene s reason="SL" namisto BOS_EXIT.
            "bos_exit_sl_protected": 0,
            # WAVE counters / TP-wave eventy
            # WAVE_TARGET_N "wave 4" event aktivne zaviralo trend-dir pozice
            # na bar_close (s SL safety pri prurazu SL) a rusilo trend-dir
            # pendingy. Drive jen nastavovalo TP a cekalo — coz casto skoncilo
            # az na BOS s nejistou cenou.
            "tp_wave_events_fired": 0,
            "tp_wave_events_skipped_no_prev": 0,
            "tp_wave_positions_closed": 0,          # aktivne uzavrene na bar_close
            "tp_wave_positions_sl_protected": 0,    # uzavrene na SL (bar prekrocil SL)
            "tp_wave_pendings_cancelled": 0,        # zrusene trend-dir pendingy
            # Pozn.: drive existovaly counters tp_wave_positions_tp_set /
            # tp_wave_pendings_tp_set / tp_wave_skipped_wrong_side_* — nyni
            # se TP uz nenastavuje, pozice se aktivne uzaviraji.
            "counter_positions_placed": 0,
            "counter_positions_filled": 0,
            "counter_positions_cancelled": 0,
            "counter_positions_skipped_no_prev": 0,
            "counter_positions_skipped_no_sequence_info": 0,
            "bos_reentry_positions_opened": 0,
            # TWO-SIDED ENTRY counters
            "two_sided_mirror_attempts": 0,
            "two_sided_mirror_accepted": 0,
            "two_sided_mirror_skipped_sl_breached": 0,
            "two_sided_mirror_skipped_session": 0,
            "two_sided_mirror_skipped_too_old": 0,
            "two_sided_mirror_skipped_wave_max_pct": 0,
            # PP counters
            "pp_breaks_detected": 0,
            "pp_skipped_wave_not_finished": 0,
            "pp_skipped_hh_hl": 0,
            "pp_skipped_post_ext_suppressed": 0,
            "pp_skipped_ext_wave": 0,
            "pp_skipped_in_ext_range": 0,
            "pp_skipped_trend_from_seed_reset": 0,
            "pp_orders_placed": 0,
            "pp_orders_cancelled_new_wave": 0,
            "pp_positions_filled": 0,
            "pp_positions_closed": 0,
            "ext_waves_detected": 0,
            "ext_secondary_attempts": 0,
            "ext_secondary_placed": 0,
            "ext_secondary_skipped_invalid_geom": 0,
            "ext_secondary_skipped_adx14_gate": 0,
            "ext_counter_skipped_adx14_gate": 0,
            "ext_counter_time_placed": 0,
            "ext_counter_time_skipped_wave_after_ext": 0,
            "ext_counter_bos_placed": 0,
            "ext_bos_triggered": 0,
            "ext_bos_skipped_cancelled_by_trend": 0,
            "ext_bos_trend_closed": 0,
            "waves_skipped_adx14_gate": 0,
            "waves_deferred_trend_flip": 0,
            "waves_opened_after_bos_flip": 0,
            # WF countery
            "wf_activations": 0,
            "wf_skipped_ext": 0,
            "wf_classic_waves_resumed": 0,
        }
        self._ext_active_waves = []
        self._ext_sl_anchor = None
        self._ext_secondary_sent = set()
        self._ext_counter_time_done = set()
        self._ext_counter_suppress_from_bar = {}
        self._ext_forming_first_bar = {}
        self._all_ext_waves = []
        self._ext_counter_bos_done = set()
        self._ext_bos_triggered = set()
        self._ext_bos_state = {}
        self._bos_flip_wave_by_bar = {}
        self._bos_wave_times = set()
        self._close_bos_flip_bar_indices = set()
        self._retro_bos_attempted = set()
        self.adx14_sim = Adx14BacktestSim(self.cfg)
        if self.adx14_sim.active:
            self.adx14_sim.prepare(df)
        self.trend_states_per_wave = {}
        # PP pending state — max 1 PP pending najednou (`_pp_current_pending`).
        # Kandidat = vzdy NEJNOVEJSI narozena vlna ve smeru aktualniho trendu.
        # `_pp_broken_wave_times` = vlny, u kterych uz byl break (1× per vlna).
        # Nova vlna ve smeru trendu rusi predchozi PP pending (jina wave_time).
        self._pp_current_pending: Optional[PendingOrder] = None
        self._forming_tp_watch = None
        self._pp_broken_wave_times: set = set()
        self._pp_trend_phase: Optional[str] = None
        # Lookup: bar_idx -> wave_birth (= obraceny smer mappingu wave_birth).
        # Pouziva PP per-bar logika pri hledani "aktualne aktivni vlny ve smeru trendu".
        self._wave_by_birth_bar: dict = {}
        # Per-bar trend timeline pro BOS_EXIT (jen kdyz je rezim aktivni;
        # jinak prazdne — engine to nepouzije a setrime CPU/RAM).
        self.trend_states_per_bar = []
        self.wave_sequence_info = {}
        self.waves_by_wave_time = {}
        self._tp_mode = _resolve_tp_mode(self.cfg)
        self._pending_cancel_mode = _resolve_pending_cancel_mode(self.cfg)

        cfg = self.cfg
        df = df.reset_index(drop=True)
        self._run_df = df
        self._causal_policy = policy_from_cfg(self.cfg)
        self._ohlc = ohlc_from_dataframe(df)
        self._wf_resume_cache: dict = {}
        self.signal_key_digits = self._infer_signal_key_digits(df)

        if df.empty:
            raise ValueError("Prazdny DataFrame - zkontroluj CSV a date_from/date_to")

        from strategy.trend_bos import apply_tp_mode_to_waves
        from strategy.wave_source import LegacyWaveSource, make_wave_source

        # Zdroj vln (1B/1D): default LegacyWaveSource = obal nad
        # run_pine_wave_simulation pres cache → bit-identicke s dnesnim engine.
        if wave_source is None:
            wave_source = make_wave_source(df, cfg)
        if not isinstance(wave_source, LegacyWaveSource):
            # 1D: prepare() podporuje zatim jen precompute zdroj. IncrementalWaveSource
            # (per-bar advance, reference pro live paritu) se zapoji az v 1F.
            raise NotImplementedError(
                "engine.prepare() zatim podporuje jen LegacyWaveSource "
                "(legacy_precompute); incremental_causal se zapoji v akci 1F."
            )
        self._wave_source = wave_source
        all_waves = wave_source.all_waves()
        wave_birth = wave_source.birth_map()
        ext_suppress_from = dict(wave_source.ext_counter_suppress_from_bar)
        ext_forming_from = dict(wave_source.ext_forming_first_bar)
        apply_tp_mode_to_waves(all_waves, cfg)
        self.wave_birth_by_time = wave_birth
        self._ext_counter_suppress_from_bar = ext_suppress_from
        self._ext_forming_first_bar = ext_forming_from
        self._all_ext_waves = [
            w for w in all_waves if is_ext_wave(w, cfg)
        ]

        # Slovnik: bar_index -> list signalu vznikajicich na tomto baru
        waves_by_bar: dict = {}
        self.wave_debug["waves_detected_total"] = len(all_waves)
        for w in all_waves:
            birth = wave_birth.get(w["wave_time"])
            if birth is not None:
                # detect_waves nevraci wave_time_dt - dopocitame
                if "wave_time_dt" not in w:
                    w["wave_time_dt"] = pd.to_datetime(w["wave_time"], format="%Y%m%d%H%M")
                waves_by_bar.setdefault(birth, []).append(w)
                # PP per-bar logika potrebuje rychly lookup "narodila se vlna
                # na baru <= X v danem smeru" — drzime obraceny mapping.
                self._wave_by_birth_bar.setdefault(birth, []).append(w)

        # TREND FILTER (BOS) — snapshot trendu pro kazdou vlnu.
        # Pocitame ho take pri zapnutem TWO-SIDED (parent A musi byt v
        # trend-direction, counter B counter-trend; viz strategy/two_sided.py).
        # Jinak no-op a setrime CPU. Vystup pouziva `_process_new_wave` skrz
        # `wave_allowed_for_entry` a two-sided gate funkce.
        if (
            getattr(cfg, "trend_filter_enabled", False)
            or two_sided_enabled(cfg)
        ):
            self.trend_states_per_wave = compute_trend_states_per_wave(df, all_waves, cfg)
        else:
            self.trend_states_per_wave = {}

        need_per_bar = (
            tp_mode_uses_bos_per_bar_exit(cfg)
            or bool(getattr(cfg, "pp_enabled", False))
            or self._pending_cancel_mode == PendingCancelMode.TREND
            or getattr(cfg, "trend_filter_enabled", False)
            or bos_entry_in_rrr_fixed_enabled(cfg)
        )
        self._need_per_bar_trend = need_per_bar

        self._all_waves = all_waves
        self._sync_wave_sequence_state()

        from strategy.ext_range import ext_range_enabled, reapply_ext_range_tags

        if ext_range_enabled(cfg):
            reapply_ext_range_tags(all_waves, cfg, df=df, wave_birth=wave_birth)
            self._sync_wave_sequence_state()

        self._recompute_bos_state(df, all_waves, wave_birth)

        if not self.waves_by_wave_time:
            self.waves_by_wave_time = {w["wave_time"]: w for w in all_waves}
        self._two_sided_tracker = TwoSidedTracker()
        self._two_sided_fired_wave_times = set()
        self._wf_tracker = WickFakeoutTracker()
        self._wf_visual_waves = []
        # Lookup pro WF: posledni potvrzena vlna pro kazdy bar
        self._wf_last_birth_bar: int = -1
        waves_by_end_bar: dict = {}
        if two_sided_enabled(cfg):
            for w in all_waves:
                end_ix = int(w.get("draw_right", w.get("draw_left", 0)))
                waves_by_end_bar.setdefault(end_ix, []).append(w)

        from backtest.executor import BarContext

        ctx = BarContext(
            df=df,
            ohlc=self._ohlc,
            cfg=cfg,
            waves_by_bar=waves_by_bar,
            waves_by_end_bar=waves_by_end_bar,
            all_waves=all_waves,
            wave_birth=wave_birth,
        )
        return ctx

    def process_bar(self, i: int, ctx: "BarContext", executor: "Executor") -> None:
        """
        Zpracuje jeden bar `i` v poradi 1–8 (viz VARIANTA A.txt §3.4):
          1. EXT1 / forming TP watch
          2. BOS exit + cancel pendings
          3. position-cap prune
          4. two-sided tracker update
          5. TP_WAVE_N event (pred entries)
          6. nove vlny → entry pipeline
          7. WF / EXT / PP / counter / BOS reentry
          8. executor: trigger pending → SL/TP check

        Rozhodovani je v enginu/strategy; order lifecycle (place/cancel/close/
        modify/fill) JEN pres `executor`. Stav baru je v `ctx`.
        """
        cfg = self.cfg
        df = ctx.df
        ohlc = ctx.ohlc
        waves_by_bar = ctx.waves_by_bar
        waves_by_end_bar = ctx.waves_by_end_bar
        bar = ohlc.bar_view(i)
        bar_time = ohlc.bar_time_at(i)
        high = bar.high
        low = bar.low
        open_ = bar.open
        close_ = bar.close
        mid_price = open_

        self._maybe_rrr_fixed_better_exit_after_ext1_protect_end(
            i, bar_time, close_,
        )

        # Dynamický výpočet protected_waves per bar (pouze vlny, které se už narodily)
        # Optimalizace: počítáme jen když vznikne nová vlna
        new_waves = waves_by_bar.get(i, [])
        if new_waves:
            ctx.waves_up_to_now.extend(new_waves)
            ctx.protected_waves_bar = compute_wave_2_no_tp_protected_waves(
                ctx.waves_up_to_now, self.wave_sequence_info, cfg,
            )

        self._update_forming_tp_watch_on_bar(high, low)

        # 0) BOS_EXIT / BOS_EXIT_PRIORITY — BOS proti smeru pozic → zavreni na close baru
        # pred standardni SL/TP kontrolou. Vracene flipped/flip_direction uz
        # nepouzivame na deferred entry — retro-aktivace BOS-vlny bezi
        # nezavisle pres `_bos_flip_wave_by_bar`.
        # POZN: predavame i high/low, aby BOS-exit handler mohl respektovat
        # SL — pokud bar prekonal SL pozice, zavreme na SL cene (ne na close),
        # aby ztrata neprekrocila planovany SL (intra-bar by SL fired drive).
        # close_positions: zavre pozice ve smeru staré trendovky na close baru
        #                  (BOS_EXIT-like cleanup) — ridi tp_mode.
        # cancel_pendings: zrusi pendingy ve smeru staré trendovky na BOS flipu —
        #                  ridi tp_mode + pending_cancel_mode (TREND vynuti i v RRR_FIXED;
        #                  NUMBER zrusi BOS-based cancel i v BOS_EXIT-like modes).
        if self.trend_states_per_bar:
            close_pos = tp_mode_uses_bos_per_bar_exit(cfg)
            pcm = self._pending_cancel_mode
            cancel_pend = pcm == PendingCancelMode.TREND
            if bos_flip_handler_should_run(
                cfg, close_pos=close_pos, cancel_pend=cancel_pend,
            ):
                self._handle_bos_exit_on_bar(
                    i, bar_time, close_, high, low,
                    close_positions=close_pos,
                    cancel_pendings=cancel_pend,
                    protected_waves=ctx.protected_waves_bar,
                )

        # Position cap varianta 2: preventivne prune pendingy bez re-queue
        # (position-cap prune jde pres executor — gap-check).
        for o in executor.prune_pendings(mid_price):
            self._append_pending_vis("pending_pruned", i, bar_time, o)

        if two_sided_enabled(cfg):
            for w in waves_by_end_bar.get(i, []):
                ts_parent = self.trend_states_per_wave.get(
                    str(w.get("wave_time", ""))
                )
                self._two_sided_tracker.register_parent(
                    w,
                    i,
                    cfg,
                    df=df,
                    sync_from_bar=parent_monitor_start_bar(w),
                    trend_state=ts_parent,
                )
            self._two_sided_tracker.update_bar(high, low, i)

        # 3) Nove signaly vznikajici na tomto baru
        # (new_waves uz nacteno na zacatku smycky)
        # 3.a) TP-wave event (WAVE_TARGET_N): pred entry processingem
        # AKTIVNE UZAVRE vsechny pozice ve smeru trendu (na bar_close)
        # a polozi counter pending — beze vlivu na to, zda nove vlna projde
        # filtry pro otevreni vstupu. high/low slouzi pro SL safety check.
        if self._tp_mode in WAVE_TARGET_N_FAMILY and new_waves:
            for wave in new_waves:
                self._on_wave_born_forming_tp_context(wave, i)
            for wave in new_waves:
                self._maybe_fire_tp_wave_event(
                    wave, i, bar_time, close_, high, low
                )
        # 3.b) Bezne zpracovani nove vlny (entry pipeline) + volitelny
        # TWO-SIDED counter (doplnkovy WAVE na protivlni po doteku FIB rodice).
        for wave in new_waves:
            # POST-EXT ZAMEK: vlna proti seed-smeru v zamcene zone vubec
            # neexistuje — ani jako rodic two-sided mirroru, ani pro PP /
            # EXT BOS state. Skip celou iteraci a zaznamenej do debugu.
            if bool(wave.get("post_ext_trend_suppressed", False)):
                self.wave_debug["waves_skipped_post_ext_trend_suppressed"] = (
                    self.wave_debug.get(
                        "waves_skipped_post_ext_trend_suppressed", 0
                    ) + 1
                )
                continue
            self._advance_ext_bos_state_with_wave(wave, i)
            if bool(getattr(cfg, "pp_enabled", False)):
                self._pp_on_new_wave_born(wave, i, bar_time)
            self.wave_debug["waves_birth_total"] += 1
            wave_is_ext = bool(wave.get("is_ext", False)) and is_ext_wave(wave, cfg)
            ext_bypass_trend = False
            if bool(getattr(cfg, "ext_trade_both_sides_in_range", False)):
                if wave_is_ext:
                    ext_bypass_trend = True
                elif bool(wave.get("in_ext_range", False)):
                    ext_bypass_trend = True
            wave_entry = wave
            if wave_is_ext:
                self._ext_sl_anchor = wave
                if two_sided_enabled(cfg):
                    self._two_sided_tracker.clear_all()
            else:
                wave_entry, self._ext_sl_anchor = apply_first_opposite_wave_sl_after_ext(
                    wave,
                    ext_anchor=self._ext_sl_anchor,
                    cfg=cfg,
                )
                if wave_entry.get("sl") != wave.get("sl"):
                    self.wave_debug["wave_sl_at_ext_extreme"] = (
                        self.wave_debug.get("wave_sl_at_ext_extreme", 0) + 1
                    )
            ts_current = self.trend_states_per_wave.get(
                str(wave.get("wave_time", ""))
            )
            if two_sided_enabled(cfg):
                self._two_sided_tracker.link_counter_b_wave_if_matches(
                    wave_entry,
                    self._all_waves,
                    cfg,
                    trend_states_per_wave=self.trend_states_per_wave,
                )
            waves_for_two_sided = (
                self._two_sided_tracker.waves_with_armed_parents(self._all_waves)
                if two_sided_enabled(cfg)
                else self._all_waves
            )
            prev_wave = find_parent_wave_for_two_sided(
                waves_for_two_sided, wave_entry, cfg,
                trend_states_per_wave=self.trend_states_per_wave,
            )
            two_sided_only = False
            if (
                two_sided_enabled(cfg)
                and bool(getattr(cfg, "wave_position_enabled", True))
                and prev_wave is not None
            ):
                parent_wt = str(prev_wave.get("wave_time", ""))
                touched = self._two_sided_tracker.fib_was_touched(parent_wt)
                ts_parent = self.trend_states_per_wave.get(parent_wt)
                if should_open_two_sided_counter(
                    prev_wave,
                    wave_entry,
                    cfg,
                    parent_fib_touched=touched,
                    parent_trend_state=ts_parent,
                    counter_trend_state=ts_current,
                ):
                    two_sided_only = True
                    self._two_sided_tracker.register_counter_b_wave(
                        str(wave_entry.get("wave_time", ""))
                    )
                    if wave_counter_two_sided_orders_enabled(cfg):
                        self._maybe_fire_two_sided_counter(
                            prev_wave, wave_entry, i, bar_time, bar
                        )

            skip_parent_primary = skip_primary_entry_on_parent_wave(
                wave_entry, cfg, trend_state=ts_current,
            )
            skip_b_primary = (
                two_sided_enabled(cfg)
                and self._two_sided_tracker.is_b_wave_for_any_parent(
                    str(wave_entry.get("wave_time", ""))
                )
            )
            if skip_b_primary:
                self.wave_debug["two_sided_primary_skip_tracker"] = (
                    self.wave_debug.get("two_sided_primary_skip_tracker", 0) + 1
                )
            if not two_sided_only and not skip_parent_primary and not skip_b_primary:
                self._process_new_wave(
                    wave_entry,
                    i,
                    bar_time,
                    bar,
                    bypass_trend_filter=ext_bypass_trend,
                )
            if wave_is_ext:
                self.wave_debug["ext_waves_detected"] = (
                    self.wave_debug.get("ext_waves_detected", 0) + 1
                )
                self._ext_active_waves.append(wave)
                self._ext_bos_state[str(wave["wave_time"])] = "armed"
                if bool(getattr(cfg, "ext_secondary_enabled", False)):
                    self._process_ext_secondary_for_wave(wave, i, bar_time, bar)

            if two_sided_enabled(cfg) and parent_wave_qualifies(
                wave, cfg, trend_state=ts_current,
            ):
                self._two_sided_tracker.register_parent(
                    wave,
                    i,
                    cfg,
                    df=df,
                    sync_from_bar=parent_monitor_start_bar(wave),
                    trend_state=ts_current,
                )
        # =====================================================================
        # WICK FAKEOUT RECOVERY (WF)
        # ---------------------------------------------------------------------
        # Co to dělá:
        #   WF řeší situaci, kdy po dokončení vlny ve směru trendu přijde
        #   protisměrový pohyb, který NENÍ validní BOS (jen wick nad/pod
        #   extrémem last wave, žádný close na druhé straně). Pak se trh
        #   vrátí ve směru trendu a udělá close za opačným extrémem last wave.
        #   Engine by tuto situaci jinak nechal bez definice — WF v tomto
        #   momentě vytvoří NOVOU continuation vlnu od fakeout pivotu
        #   (nejvyšší wick high pro downtrend, nejnižší wick low pro uptrend).
        #
        # Kdy se aktivuje (downtrend):
        #   1) Last wave šla dolů, má definované last_wave_high a last_wave_low.
        #   2) V okně mezi koncem last wave a aktuálním barem byl alespoň
        #      jeden bar s high > last_wave_high (= wick).
        #   3) ŽÁDNÝ bar v okně neměl close > last_wave_high (= nebyl validní
        #      close-based BOS).
        #   4) Aktuální bar má close < last_wave_low (= trend pokračuje).
        #   5) Trh NENÍ ve stavu EXT.
        #   Pro uptrend mirror (last wave nahoru, wick pod low, žádný close
        #   pod low, close nad high last wave, ne EXT).
        #
        # Fakeout pivot:
        #   = max(bar.high) v okně pro downtrend (nejvyšší wick).
        #   = min(bar.low)  v okně pro uptrend  (nejnižší wick).
        #   Bez ohledu na to, ve kterém pořadí v okně tento wick byl.
        #
        # Co se stane:
        #   Vznikne nová vlna ve směru trendu, jejíž swing extrém = fakeout
        #   pivot. Dál ji engine obhospodařuje standardně — standardní pending
        #   STOP setup po jejím dokončení, LFT (pokud zapnuté), filtry, dedup,
        #   všechno jako u jakékoli jiné vlny.
        #
        # WF NEMÁ vlastní entry logiku:
        #   WF jen "dořeší" vykreslení vlny. Vstupy řeší existující flow.
        #
        # Žádný timeout, žádný lookback limit:
        #   Okno je definováno strukturou (konec last wave → aktivační close).
        #   Buď přijde aktivační close → WF aktivace.
        #   Nebo přijde close-based BOS → standardní logika obratu, WF se
        #   neaktivuje.
        #   Nebo trh zůstává uvnitř range → engine čeká dál.
        #
        # Výjimka EXT:
        #   Pokud je trh ve stavu EXT, WF se NEAKTIVUJE. EXT režim má vlastní
        #   logiku a WF tam nepatří. Logni WF_SKIPPED_EXT pro debug.
        #
        # Config:
        #   WF_ENABLED: bool — master switch (default False).
        #   Žádné další WF-specific configy. Vše ostatní (RRR, RISK_USD,
        #   filtry, MAGIC, atd.) sdílené se standardním flow.
        # =====================================================================
        # Krok 1: Aktualizuj WF okno o data aktuálního baru.
        self._wf_tracker.on_bar(high, low, close_, bar_idx=i)

        # Krok 2: Zkontroluj WF podmínky (před resetem trackeru novou vlnou).
        if bool(getattr(cfg, "wf_enabled", False)):
            _wf_result = self._wf_tracker.check_wf(close_, bar_idx=i, cfg=cfg)
            if _wf_result is not None:
                if _wf_result["status"] == "ext_skipped":
                    self.wave_debug["wf_skipped_ext"] += 1
                    import logging as _logging
                    _logging.getLogger(__name__).debug(
                        "WF_SKIPPED_EXT | wave_id=%s | reason=ext_active",
                        str(_wf_result["last_wave"].get("wave_time", "?")),
                    )
                elif _wf_result["status"] == "activate":
                    # Krok 3: Vytvoř syntetický WF wave dict.
                    _wf_wt_raw = df["time"].iloc[i]
                    _wf_wt_str = (
                        _wf_wt_raw.strftime("%Y%m%d%H%M")
                        if hasattr(_wf_wt_raw, "strftime")
                        else str(_wf_wt_raw)
                    )
                    _wf_wave = build_wf_wave(
                        cfg,
                        last_wave=_wf_result["last_wave"],
                        fakeout_pivot=float(_wf_result["fakeout_pivot"]),
                        fakeout_bar_idx=int(_wf_result["fakeout_bar_idx"]),
                        activation_bar_idx=i,
                        wave_time_str=_wf_wt_str,
                        window_min_low=_wf_result.get("window_min_low"),
                        window_max_high=_wf_result.get("window_max_high"),
                    )
                    if _wf_wave is not None:
                        _wf_wave["wave_time_dt"] = pd.to_datetime(
                            _wf_wave["wave_time"], format="%Y%m%d%H%M"
                        )
                        self.wave_debug["wf_activations"] += 1
                        import logging as _logging
                        _wf_last = _wf_result["last_wave"]
                        _wf_ref_h = float(
                            _wf_result.get(
                                "wf_ref_high",
                                _wf_last.get("box_top", 0.0),
                            )
                        )
                        _wf_ref_l = float(
                            _wf_result.get(
                                "wf_ref_low",
                                _wf_last.get("box_bottom", 0.0),
                            )
                        )
                        _logging.getLogger(__name__).info(
                            "WF_ACTIVATED | wave_id=%s | w_dir=%s"
                            " | last_wave_high=%.5f | last_wave_low=%.5f"
                            " | fakeout_pivot=%.5f | activation_close=%.5f"
                            " | window_size_bars=%d",
                            _wf_wt_str,
                            "down" if int(_wf_last.get("dir", 0)) == -1 else "up",
                            _wf_ref_h,
                            _wf_ref_l,
                            float(_wf_result["fakeout_pivot"]),
                            float(close_),
                            int(_wf_result["window_size"]),
                        )
                        # Krok 4–7: WF entry (fib pozice), PP, navázání klasických vln.
                        self._on_wf_wave_activated(
                            _wf_wave,
                            i,
                            bar_time,
                            bar,
                            df,
                            waves_by_bar,
                        )
                        # Krok 8: WF vlna se stane novým last_wave pro příští okno.
                        self._wf_tracker.on_new_wave(
                            _wf_wave,
                            birth_bar=int(_wf_wave.get("draw_right", i)),
                            df=df,
                            force_reset=True,
                        )
                        self._wf_visual_waves.append(_wf_wave)

        # Krok 6: Reset / nastavení trackeru novou potvrzenou vlnou (po WF check).
        for _wf_w in new_waves:
            if not bool(_wf_w.get("post_ext_trend_suppressed", False)):
                self._wf_tracker.on_new_wave(_wf_w, birth_bar=i, df=df)
        # ─────────────────────────────────────────────────────────────

        # BOS-vlna retro-aktivace: na baru close-based flipu se pokusi
        # otevrit pozici z TE jedne vlny, kterou flip zpusobil
        # (bypass trend filtru). Funguje pro vsechny tp_mody.
        bos_wave = self._bos_flip_wave_by_bar.get(i)
        if bos_wave is not None and _wave_is_wf_origin(bos_wave):
            bos_wave = None
        if bos_wave is not None and self._causal_policy.enabled:
            bos_wave = bos_flip_wave_at_bar(
                self._causal_policy,
                self._bos_flip_wave_by_bar,
                i,
                self.wave_birth_by_time,
            )
        if bos_wave is not None:
            bos_wt = str(bos_wave.get("wave_time", "") or "")
            birth_ix = self.wave_birth_by_time.get(bos_wt)
            if bos_wt and bos_wt not in self._retro_bos_attempted:
                if retro_bos_entry_allowed(
                    self._causal_policy,
                    wave=bos_wave,
                    flip_bar=i,
                    birth=birth_ix,
                ):
                    self._retro_bos_attempted.add(bos_wt)
                    entry_wave = wave_for_entry_at_bar(
                        self._causal_policy,
                        bos_wave,
                        i,
                        df,
                        cfg,
                    )
                    self._process_new_wave(
                        entry_wave,
                        i,
                        bar_time,
                        bar,
                        bypass_trend_filter=True,
                        is_two_sided_mirror=False,
                    )

        # 8) Executor fill model — trigger pending az PO vytvoreni novych orderu
        # na tomto baru (two-sided LIMIT muze fillnout jeste na stejnem baru).
        executor.on_bar_open(i, bar_time, high, low, open_)
        executor.enforce_overflow(i, bar_time, mid_price)

        if bool(getattr(cfg, "ext_enabled", False)) and bool(
            getattr(cfg, "ext_counter_enabled", False)
        ):
            self._process_ext_counter_time(i, bar_time, open_)

        self._maybe_fire_extension_tp_on_bar(
            i, bar_time, open_, high, low, close_,
        )
        executor.on_bar_range(i, bar_time, high, low)

        # 3.c) PP break — kontroluje, zda na tomto baru nedoslo k close-baru
        # nad/pod high/low aktualni trend-dir vlny. Pokud ano, polozi PP LIMIT.
        if bool(getattr(cfg, "pp_enabled", False)):
            self._process_pp_break_on_bar(i, bar_time, close_)

        if ext_bos_on_bar_handler_enabled(cfg):
            self._process_ext_bos_on_bar(i, bar_time, close_)

        # Kdyz vzniknou nove pendingy v tomto baru, varianta 2 je hned proreze.
        for o in executor.prune_pendings(mid_price):
            self._append_pending_vis("pending_pruned", i, bar_time, o)

        # 4) Expiry pending (pres executor)
        executor.expire_pendings(i, bar_time)

        # ADX14 gate timeline až po SL/TP/nových vstupech — BOS restart může skončit pauzu v tomže baru.
        if self.adx14_sim is not None and self.adx14_sim.active:
            self.adx14_sim.on_bar(i, bar_time)

    def run(
        self,
        df: pd.DataFrame,
        *,
        retain_wave_snapshot: bool = False,
        wave_source=None,
    ) -> List[ClosedTrade]:
        """
        engine.run = prepare(df, wave_source) + smyčka process_bar(i, ctx, executor)
        + finalize. Pro legacy_precompute je vysledek bit-identicky s puvodnim
        monolitem (golden regrese 164 / +39040.88).
        """
        from backtest.executor import BacktestExecutor

        ctx = self.prepare(df, wave_source=wave_source)
        executor = self._executor = BacktestExecutor(self)

        cfg = self.cfg
        df = ctx.df
        ohlc = ctx.ohlc
        all_waves = ctx.all_waves
        wave_birth = ctx.wave_birth

        for i in range(1, ohlc.n):
            self.process_bar(i, ctx, executor)

        # Zbyle otevrene pozice uzavreme na poslednim close
        last_ix = len(df) - 1
        self._close_remaining(last_ix, df)
        if self.adx14_sim is not None and self.adx14_sim.active and last_ix >= 1:
            last_bt = pd.Timestamp(df.iloc[last_ix]["time"]).to_pydatetime()
            if (
                self.adx14_sim.timeline
                and self.adx14_sim.timeline[-1]["time"] == last_bt
            ):
                self.adx14_sim.timeline.pop()
            self.adx14_sim.on_bar(last_ix, last_bt)

        if retain_wave_snapshot:
            merged = list(self._all_waves)
            by_wt = {str(w.get("wave_time", "")): w for w in merged}
            for _wf_vis in self._wf_visual_waves:
                _wf_wt = str(_wf_vis.get("wave_time", "") or "")
                if not _wf_wt:
                    continue
                if _wf_wt not in by_wt:
                    merged.append(_wf_vis)
                by_wt[_wf_wt] = _wf_vis
            known = set(by_wt.keys())
            for ct in self.closed_trades:
                if not getattr(ct, "is_two_sided_mirror", False):
                    continue
                wt = str(getattr(ct, "wave_time", "") or "")
                if wt and wt in by_wt:
                    by_wt[wt]["_two_sided_counter"] = True
                    by_wt[wt]["two_sided_show"] = True
                    by_wt[wt]["is_two_sided_counter"] = True
            merged.sort(
                key=lambda w: (
                    int(w.get("draw_left", 0)),
                    str(w.get("wave_time", "")),
                )
            )
            self.last_waves = merged
            # Zde se jiz nevola reapply_ext_range_tags, protoze se vola hned po detekci v wave_detection_pine.py
            self.last_waves_for_visual = self._build_waves_for_visual(merged, df)
            self.wave_birth_by_time = dict(wave_birth)
            for _wf_vis in self._wf_visual_waves:
                _wf_wt = _wf_vis.get("wave_time")
                if _wf_wt:
                    self.wave_birth_by_time[_wf_wt] = int(
                        _wf_vis.get(
                            "draw_right",
                            _wf_vis.get("draw_left", 0),
                        )
                    )
            self.bos_flip_events = (
                collect_bos_flip_events(df, all_waves, cfg) if not df.empty else []
            )
        else:
            self.last_waves = []
            self.last_waves_for_visual = []
            self._visual_bos_wave_times = set()
            self.wave_birth_by_time = {}
            self.bos_flip_events = []

        return self.closed_trades

    # ------------------------------------------------------------------

    def _sync_wave_sequence_state(self) -> None:
        """Přepočet index_in_trend po změně `_all_waves` (např. WF merge)."""
        df = self._run_df
        all_waves = self._all_waves
        self.wave_sequence_info, self._wave_2_no_tp_protected_waves = (
            sync_wave_sequence_state(df, all_waves, self.cfg)
        )
        self._ext1_protection_per_bar = compute_ext1_protection_bars(
            df, all_waves, self.cfg,
        )
        self._ext1_wave_times = {
            str(w["wave_time"])
            for w in all_waves
            if bool(w.get("is_ext")) and w.get("index_in_trend") == 1
        }
        self.waves_by_wave_time = {w["wave_time"]: w for w in all_waves}

    def _recompute_bos_state(
        self,
        df: pd.DataFrame,
        all_waves: List[dict],
        wave_birth: dict,
    ) -> None:
        """BOS flip mapa + per-bar trend po finalnich EXT tagach a wave_sequence."""
        cfg = self.cfg
        flips = _detect_close_bos_timeline_flips(
            df, all_waves, cfg, wave_birth_bars=wave_birth
        )
        flip_map = compute_bos_wave_flip_map(
            df, all_waves, cfg, wave_birth_bars=wave_birth
        )
        flip_map = reconcile_bos_flip_map_with_wave_sequence(
            flip_map,
            flips,
            all_waves,
            self.wave_sequence_info,
            wave_birth,
        )
        wt_to_wave = {w["wave_time"]: w for w in all_waves}
        self._bos_flip_wave_by_bar = {
            int(bar_ix): wt_to_wave[wt]
            for bar_ix, wt in flip_map.items()
            if wt in wt_to_wave
        }
        self._bos_wave_times = set(flip_map.values())

        need_per_bar = bool(getattr(self, "_need_per_bar_trend", False))
        if need_per_bar:
            self.trend_states_per_bar = compute_trend_states_per_bar(
                df, all_waves, cfg
            )
            self._close_bos_flip_bar_indices = compute_close_based_bos_flip_bar_indices(
                df, all_waves, cfg
            )
            if bool(getattr(cfg, "pp_enabled", False)):
                self._pp_trend_confirmed_per_bar = build_pp_trend_confirmed_per_bar(
                    df, all_waves, cfg, self.trend_states_per_bar
                )
            else:
                self._pp_trend_confirmed_per_bar = []
        else:
            self.trend_states_per_bar = []
            self._close_bos_flip_bar_indices = set()
            self._pp_trend_confirmed_per_bar = []

    def _build_waves_for_visual(self, waves: List[dict], df: pd.DataFrame | None = None) -> List[dict]:
        """Vlny pro HTML vizual — filtr podle flagu z detekce (ne re-simulace trendu)."""
        from backtest.visual_wave_filter import (
            merge_lock_trend_segments_for_visual,
            wave_passes_visual_filter,
        )

        cfg = self.cfg
        bos_times: set[str] = set(self._bos_wave_times or ())
        by_wt = {str(w.get("wave_time", "") or ""): w for w in waves}
        bos_times = {
            wt
            for wt in bos_times
            if wt in by_wt and not _wave_is_wf_origin(by_wt[wt])
        }
        two_sided_times = set(self._two_sided_fired_wave_times or ())
        wf_vis_times = {
            str(w.get("wave_time", "") or "")
            for w in self._wf_visual_waves
            if w.get("wave_time")
        }
        out: List[dict] = []
        for w in waves:
            if wave_passes_visual_filter(
                w,
                cfg,
                bos_wave_times=bos_times,
                wf_visual_wave_times=wf_vis_times,
                two_sided_fired_times=two_sided_times,
                include_lock_trend_waves=True,
            ):
                out.append(w)
        out = merge_lock_trend_segments_for_visual(out, df, cfg)
        self._visual_bos_wave_times = bos_times
        return out

    def _compute_wave_birth_bars(self, df: pd.DataFrame) -> dict:
        """
        Pro kazdou vlnu bar index potvrzeni (stejna simulace jako detect_waves / Pine emulator).
        """
        if df.empty:
            return {}
        from strategy.wave_detection_pine import compute_wave_birth_bars_pine

        return compute_wave_birth_bars_pine(df, self.cfg)

    def _tag_two_sided_counter_wave(self, wave_time: str, sig: dict) -> None:
        """Označí vlnu pro vizualizaci (tyrkysová) a doplní SL/TP ze signálu."""
        wt = str(wave_time or "")
        if not wt:
            return
        self._two_sided_fired_wave_times.add(wt)
        for w in self._all_waves:
            if str(w.get("wave_time", "")) == wt:
                w["_two_sided_counter"] = True
                w["two_sided_show"] = True
                w["is_two_sided_counter"] = True
                w["sl"] = float(sig["sl"])
                w["tp"] = float(sig["tp"])
                break

    def _purge_bos_attribution_for_wave(self, wave_time: str) -> None:
        """WF vlna nema mit BOS roli — odstranit z mapy i mnoziny bos-vln."""
        wt = str(wave_time or "")
        if not wt:
            return
        self._bos_wave_times.discard(wt)
        for bar_ix in list(self._bos_flip_wave_by_bar.keys()):
            if str(self._bos_flip_wave_by_bar[bar_ix].get("wave_time", "")) == wt:
                del self._bos_flip_wave_by_bar[bar_ix]

    def _purge_bos_attribution_in_wf_freeze(self, wf_wave: dict) -> None:
        """Odstranit BOS atribuce na barech uvnitr WF boxu (draw_left..draw_right)."""
        from strategy.trend_bos import bar_in_wf_bos_freeze, build_wf_bos_freeze_ranges

        ranges = build_wf_bos_freeze_ranges([wf_wave])
        if not ranges:
            return
        for bar_ix in list(self._bos_flip_wave_by_bar.keys()):
            if not bar_in_wf_bos_freeze(int(bar_ix), ranges):
                continue
            w = self._bos_flip_wave_by_bar.pop(int(bar_ix), None)
            if w:
                self._bos_wave_times.discard(str(w.get("wave_time", "")))


    def _on_wf_wave_activated(
        self,
        wf_wave: dict,
        bar_idx: int,
        bar_time: datetime,
        bar: pd.Series,
        df: pd.DataFrame,
        waves_by_bar: dict,
    ) -> None:
        """
        WF vlna dokončena — wf_wave pozice (fib EP/SL), PP registrace,
        navázání klasické Pine detekce od draw_right+1.
        """
        cfg = self.cfg
        wf_wave["wf_wave_position"] = True
        wf_wave.setdefault("wave_origin", WAVE_ORIGIN_WF)
        wt = str(wf_wave.get("wave_time", ""))
        self._purge_bos_attribution_for_wave(wt)
        self._purge_bos_attribution_in_wf_freeze(wf_wave)

        if self.trend_states_per_bar and bar_idx < len(self.trend_states_per_bar):
            self.trend_states_per_wave[wt] = self.trend_states_per_bar[bar_idx]

        self._all_waves.append(wf_wave)
        self.wave_birth_by_time[wt] = int(bar_idx)
        self._wave_by_birth_bar.setdefault(int(bar_idx), []).append(wf_wave)
        self.waves_by_wave_time[wt] = wf_wave

        if bool(getattr(cfg, "pp_enabled", False)):
            self._pp_on_new_wave_born(wf_wave, bar_idx, bar_time)

        if self._process_new_wave(wf_wave, bar_idx, bar_time, bar):
            self.wave_debug["wf_wave_positions_accepted"] = (
                self.wave_debug.get("wf_wave_positions_accepted", 0) + 1
            )

        from_bar = int(wf_wave.get("draw_right", bar_idx)) + 1
        wt_key = str(wf_wave.get("wave_time", ""))
        cache_key = (wt_key, from_bar)
        if cache_key not in self._wf_resume_cache:
            self._wf_resume_cache[cache_key] = resume_classic_waves_after_wf(
                df, cfg, wf_wave,
            )
        continued, continued_birth = self._wf_resume_cache[cache_key]
        if continued:
            self._merge_wf_continued_classic_waves(
                wf_wave,
                from_bar,
                continued,
                continued_birth,
                waves_by_bar,
            )

    def _merge_wf_continued_classic_waves(
        self,
        wf_wave: dict,
        from_bar: int,
        continued: list,
        continued_birth: dict,
        waves_by_bar: dict,
    ) -> None:
        """Nahradí upfront vlny od from_bar navázanými klasickými vlnami po WF."""
        cfg = self.cfg
        from strategy.wf_wave_list import merge_wf_continued_classic_waves

        remove_times = merge_wf_continued_classic_waves(
            self._run_df,
            cfg,
            self._all_waves,
            wf_wave,
            continued,
            continued_birth,
            wave_birth_by_time=self.wave_birth_by_time,
            ohlc=self._ohlc,
        )

        if remove_times:
            for b in list(waves_by_bar.keys()):
                waves_by_bar[b] = [
                    w
                    for w in waves_by_bar[b]
                    if str(w.get("wave_time", "")) not in remove_times
                ]
                if not waves_by_bar[b]:
                    del waves_by_bar[b]
            for b in list(self._wave_by_birth_bar.keys()):
                self._wave_by_birth_bar[b] = [
                    w
                    for w in self._wave_by_birth_bar[b]
                    if str(w.get("wave_time", "")) not in remove_times
                ]
                if not self._wave_by_birth_bar[b]:
                    del self._wave_by_birth_bar[b]
            for wwt in remove_times:
                self.wave_birth_by_time.pop(wwt, None)
                self.waves_by_wave_time.pop(wwt, None)

        for w in continued:
            wwt = str(w["wave_time"])
            b = int(continued_birth[wwt])
            self.wave_birth_by_time[wwt] = b
            self.waves_by_wave_time[wwt] = w
            waves_by_bar.setdefault(b, []).append(w)
            self._wave_by_birth_bar.setdefault(b, []).append(w)

        if getattr(cfg, "trend_filter_enabled", False) or two_sided_enabled(cfg):
            from strategy.trend_bos import recompute_trend_states_per_wave_from_bar

            self.trend_states_per_wave = recompute_trend_states_per_wave_from_bar(
                self._run_df,
                self._all_waves,
                cfg,
                from_bar,
                previous=self.trend_states_per_wave,
                drop_wave_times=remove_times,
            )

        self.wave_debug["wf_classic_waves_resumed"] = (
            self.wave_debug.get("wf_classic_waves_resumed", 0) + len(continued)
        )
        self._sync_wave_sequence_state()

    def _process_new_wave(self, wave: dict, bar_idx: int, bar_time: datetime,
                          bar: pd.Series, *, bypass_trend_filter: bool = False,
                          is_two_sided_mirror: bool = False) -> bool:
        """
        Zpracuje novy signal - odpovida bloku 'NOVA VLNA' v live_bot.py::main().
        Trzni cena = close tohoto baru + polovina spreadu (simulace bid/ask).

        Parametry:
          bypass_trend_filter: pri True se preskoci trend_filter check (pouziva
                               se pro mirror pozice z `two_sided_entry_enabled`).
          is_two_sided_mirror: pri True se vystupni pending/market oznaci jako
                               two_sided_mirror (pro statistiky a vizualizaci).

        Vraci True pokud byl vytvoren pending nebo market vstup.
        """
        cfg = self.cfg
        if self._causal_policy.enabled and self._run_df is not None:
            wave = wave_for_entry_at_bar(
                self._causal_policy, wave, bar_idx, self._run_df, cfg,
            )
        sig_key = get_signal_key(wave, digits=self.signal_key_digits)
        # Mirror pozice se musi odlisit od originalu (jinak by ji deduplikace
        # se sent_signals zablokovala). Pridame sufix "_M" k sig_key.
        if is_two_sided_mirror:
            sig_key = f"{sig_key}_M"

        # POST-EXT ZAMEK: vlna proti seed-smeru v zamcene zone neexistuje.
        # Bezpodminecne odmitnuti — bypass trend filtru ani BOS retro NEMA
        # vliv, vlna se nesmi obchodovat ani po flipnuti trendu.
        if bool(wave.get("post_ext_trend_suppressed", False)):
            self.wave_debug["waves_skipped_post_ext_trend_suppressed"] = (
                self.wave_debug.get("waves_skipped_post_ext_trend_suppressed", 0) + 1
            )
            self.sent_signals.add(sig_key)
            return False

        if not self._adx14_entries_allowed():
            if is_two_sided_mirror:
                self.wave_debug["two_sided_mirror_skipped_adx14_gate"] = (
                    self.wave_debug.get("two_sided_mirror_skipped_adx14_gate", 0) + 1
                )
            else:
                self.wave_debug["waves_skipped_adx14_gate"] += 1
            self.sent_signals.add(sig_key)
            return False

        # MAX_WAVE_AGE_HOURS
        wave_dt = wave.get("wave_time_dt")
        if wave_dt is None:
            wave_dt = pd.to_datetime(wave["wave_time"], format="%Y%m%d%H%M")
        if hasattr(wave_dt, "to_pydatetime"):
            wave_dt = wave_dt.to_pydatetime()
        age_sec = (bar_time - wave_dt).total_seconds()
        if age_sec > cfg.max_wave_age_hours * 3600:
            if is_two_sided_mirror:
                self.wave_debug["two_sided_mirror_skipped_too_old"] = (
                    self.wave_debug.get("two_sided_mirror_skipped_too_old", 0) + 1
                )
            else:
                self.wave_debug["waves_skipped_too_old"] += 1
            self.sent_signals.add(sig_key)
            return False

        # WAVE SESSION FILTER - vlna mimo povolene session se preskoci
        if not is_wave_in_allowed_session(wave["wave_time"], cfg):
            if is_two_sided_mirror:
                self.wave_debug["two_sided_mirror_skipped_session"] = (
                    self.wave_debug.get("two_sided_mirror_skipped_session", 0) + 1
                )
            else:
                self.wave_debug["waves_skipped_session"] += 1
            self.sent_signals.add(sig_key)
            return False

        # TREND FILTER (BOS) — vlna proti smeru trendu / mimo HH+HL strukturu se preskoci.
        # Aktivni POUZE pokud cfg.trend_filter_enabled=True; jinak `wave_allowed_for_entry`
        # vrati (True, "trend_filter_disabled") a tato vetev je no-op.
        # Two-sided mirror pozice OBCHAZI filter (bypass_trend_filter=True), pokud
        # je `cfg.two_sided_entry_bypass_trend_filter` True (default).
        if getattr(cfg, "trend_filter_enabled", False) and not bypass_trend_filter:
            ts = self.trend_states_per_wave.get(wave["wave_time"])
            allowed, _reason = wave_allowed_for_entry(wave, ts, cfg)
            # BOS vlna ZPUSOBI flip — i kdyz je v okamziku narozeni proti
            # smeru trendu, vstup z ni je povolen (bypass HH/HL i smer trendu).
            # Skutecne otevreni se ale typicky deje az na baru flipu z
            # `_bos_flip_wave_by_bar` (kdy uz cena pro retest nemusi sedet);
            # pripad kdy je vlna a flip na stejnem baru → projde rovnou tady.
            if (
                not allowed
                and str(wave.get("wave_time", "")) in self._bos_wave_times
                and not _wave_is_wf_origin(wave)
            ):
                allowed, _reason = True, "retro_after_bos_flip"
            if not allowed:
                if _reason == "wave_against_trend":
                    self.wave_debug["waves_deferred_trend_flip"] = (
                        self.wave_debug.get("waves_deferred_trend_flip", 0) + 1
                    )
                    return False
                else:
                    self.wave_debug["waves_skipped_trend_filter"] += 1
                    self.sent_signals.add(sig_key)
                    return False

        # MAX_WAVE_PCT - prilis velka vlna se neobchoduje (EXT vlny vyjimka)
        is_ext = bool(wave.get("is_ext", False))
        if is_wave_too_large(wave["move_pct"], cfg, is_ext=is_ext):
            if is_two_sided_mirror:
                self.wave_debug["two_sided_mirror_skipped_wave_max_pct"] = (
                    self.wave_debug.get("two_sided_mirror_skipped_wave_max_pct", 0) + 1
                )
            else:
                self.wave_debug["waves_skipped_wave_max_pct"] += 1
            self.sent_signals.add(sig_key)
            return False

        if sig_key in self.sent_signals:
            self.wave_debug["waves_skipped_duplicate"] += 1
            return False

        # WAVE (fib retracement) — vypnuto: bez primárního LIMIT/MARKET/STOP vstupu.
        # WAVE_COUNTER muze bezet i bez primarniho WAVE vstupu (jen TP-vlny).
        if (
            not is_two_sided_mirror
            and not bool(getattr(cfg, "wave_position_enabled", True))
        ):
            self.sent_signals.add(sig_key)
            if wave_counter_two_sided_orders_enabled(cfg):
                ep = float(wave["fib50"])
                sl = float(wave["sl"])
                is_buy = int(wave["dir"]) == 1
                tp = resolve_effective_tp(cfg, wave, ep, sl, is_buy=is_buy)
                self._maybe_place_counter_from_tp(wave, tp, bar_idx, bar_time)
                self.wave_debug["waves_counter_only_processed"] = (
                    self.wave_debug.get("waves_counter_only_processed", 0) + 1
                )
                return True
            self.wave_debug["waves_skipped_wave_positions_disabled"] = (
                self.wave_debug.get("waves_skipped_wave_positions_disabled", 0) + 1
            )
            return False

        ep = float(wave["fib50"])
        sl = float(wave["sl"])
        direction = wave["dir"]
        is_buy = (direction == 1)

        if wave.get("counted_via_volatility_threshold", False):
            try:
                min_sl_pct = float(getattr(cfg, "ext_post_both_sides_default_sl_pct", 0.10))
                if min_sl_pct > 0:
                    current_sl_pct = abs(sl - ep) / abs(ep) * 100.0
                    if current_sl_pct < min_sl_pct:
                        from strategy.wave_sequence import compute_sl_price_from_pct
                        sl = compute_sl_price_from_pct(ep, min_sl_pct, is_buy=is_buy)
            except (ValueError, TypeError):
                pass

        close_price = float(bar["close"])

        ask = close_price + self.backtest_spread / 2
        bid = close_price - self.backtest_spread / 2

        # ── Pojistka: cena uz prosla pres SL na uzavreni baru ──
        # Trend-follow strategie selhala drive nez vubec mohla vstoupit.
        if direction == 1 and ask <= sl:
            self.sent_signals.add(sig_key)
            if is_two_sided_mirror:
                self.wave_debug["two_sided_mirror_skipped_sl_breached"] = (
                    self.wave_debug.get("two_sided_mirror_skipped_sl_breached", 0) + 1
                )
            else:
                self.wave_debug["waves_skipped_sl_breached"] = (
                    self.wave_debug.get("waves_skipped_sl_breached", 0) + 1
                )
            return False
        if direction == -1 and bid >= sl:
            self.sent_signals.add(sig_key)
            if is_two_sided_mirror:
                self.wave_debug["two_sided_mirror_skipped_sl_breached"] = (
                    self.wave_debug.get("two_sided_mirror_skipped_sl_breached", 0) + 1
                )
            else:
                self.wave_debug["waves_skipped_sl_breached"] = (
                    self.wave_debug.get("waves_skipped_sl_breached", 0) + 1
                )
            return False

        fa_raw = wave.get("fib_abort")
        past_abort = False
        if fa_raw is not None:
            fib_abort = float(fa_raw)
            if direction == 1 and ask <= fib_abort:
                past_abort = True
            elif direction == -1 and bid >= fib_abort:
                past_abort = True

        if past_abort:
            if not abort_fib_shift_sl_mode(cfg):
                self.sent_signals.add(sig_key)
                self.wave_debug["waves_skipped_abort_fib"] = (
                    self.wave_debug.get("waves_skipped_abort_fib", 0) + 1
                )
                return False

        risk_span = None
        if past_abort and abort_fib_shift_sl_mode(cfg):
            risk_span = abs(float(wave["fib50"]) - float(wave["sl"]))

        self.sent_signals.add(sig_key)
        if is_two_sided_mirror:
            self.wave_debug["two_sided_mirror_accepted"] = (
                self.wave_debug.get("two_sided_mirror_accepted", 0) + 1
            )
        else:
            self.wave_debug["waves_accepted"] += 1

        # Two-sided: vzdy LIMIT na fib50 counter vlny (zadny market/stop fallback).
        if is_two_sided_mirror:
            lot = calc_lot_backtest(ep, sl, cfg)
            if direction == 1:
                tp = resolve_effective_tp(cfg, wave, ep, sl, is_buy=True)
                self._add_pending(
                    wave, "BUY_LIMIT", ep, sl, tp, lot, bar_idx, bar_time,
                    is_two_sided_mirror=True,
                )
            else:
                tp = resolve_effective_tp(cfg, wave, ep, sl, is_buy=False)
                self._add_pending(
                    wave, "SELL_LIMIT", ep, sl, tp, lot, bar_idx, bar_time,
                    is_two_sided_mirror=True,
                )
            return True

        # TREND-FOLLOW LIMIT primary:
        #   BUY:  ask > ep  -> BUY  LIMIT (ceka na pokles na ep)
        #   SELL: bid < ep  -> SELL LIMIT (ceka na rust na ep)
        # Jinak fallback dle entry_mode.
        # TP resi `resolve_effective_tp` — pro WAVE_EXTENSION_PCT vezme extension
        # z predchozi vlny a podlahu z cfg.rrr od skutecne entry (viz trend_bos).
        if direction == 1:
            if ask > ep:
                lot = calc_lot_backtest(ep, sl, cfg)
                tp = resolve_effective_tp(cfg, wave, ep, sl, is_buy=True)
                self._add_pending(
                    wave, "BUY_LIMIT", ep, sl, tp, lot, bar_idx, bar_time,
                    is_two_sided_mirror=is_two_sided_mirror,
                )
                self._maybe_place_counter_from_tp(wave, tp, bar_idx, bar_time)
            else:
                self._handle_fallback(
                    wave, "BUY", ask, sl, bar_idx, bar_time,
                    is_two_sided_mirror=is_two_sided_mirror,
                    risk_span=risk_span,
                    bypass_trend_filter=bypass_trend_filter,
                )
        else:
            if bid < ep:
                lot = calc_lot_backtest(ep, sl, cfg)
                tp = resolve_effective_tp(cfg, wave, ep, sl, is_buy=False)
                self._add_pending(
                    wave, "SELL_LIMIT", ep, sl, tp, lot, bar_idx, bar_time,
                    is_two_sided_mirror=is_two_sided_mirror,
                )
                self._maybe_place_counter_from_tp(wave, tp, bar_idx, bar_time)
            else:
                self._handle_fallback(
                    wave, "SELL", bid, sl, bar_idx, bar_time,
                    is_two_sided_mirror=is_two_sided_mirror,
                    risk_span=risk_span,
                    bypass_trend_filter=bypass_trend_filter,
                )
        return True

    def _trend_state_at_bar(self, bar_idx: int):
        if not self.trend_states_per_bar or bar_idx >= len(self.trend_states_per_bar):
            return None
        return self.trend_states_per_bar[bar_idx]

    def _fill_trend_allowed(
        self,
        wave: dict,
        bar_idx: int,
        *,
        bypass_trend_filter: bool = False,
        is_counter: bool = False,
        is_bos_reentry: bool = False,
        is_pp: bool = False,
        is_two_sided_mirror: bool = False,
    ) -> tuple[bool, str]:
        pp_ok = None
        if is_pp:
            if (
                self._pp_trend_confirmed_per_bar
                and bar_idx < len(self._pp_trend_confirmed_per_bar)
            ):
                pp_ok = bool(self._pp_trend_confirmed_per_bar[bar_idx])
            else:
                pp_ok = False
        return entry_allowed_at_fill_bar(
            wave,
            self._trend_state_at_bar(bar_idx),
            self.cfg,
            bypass_trend_filter=bypass_trend_filter,
            is_counter=is_counter,
            is_bos_reentry=is_bos_reentry,
            is_pp=is_pp,
            is_two_sided_mirror=is_two_sided_mirror,
            pp_trend_confirmed=pp_ok,
        )

    def _handle_fallback(self, wave: dict, side: str, market_price: float,
                         sl: float, bar_idx: int, bar_time: datetime,
                         *, is_two_sided_mirror: bool = False,
                         risk_span: float | None = None,
                         bypass_trend_filter: bool = False):
        """
        Cena uz je za entry smerem k SL. Reakce dle cfg.entry_mode:
          - no_fallback     : skip
          - market_fallback : vstup za market (lot/TP prepocitan, SL drzi nebo
            pri risk_span=|fib50-sl| z režimu abort_shift_sl: SL od market_price)
          - stop_fallback   : BUY_STOP / SELL_STOP zpet na entry urovni
          - limit_fallback  : DEPRECATED v trend-follow (LIMIT je primary). Skip.

        risk_span: kladna vzdalenost v cene; pouzito u market_fallback pri
          abort_fib_level=shift_sl (SL neni u puvodniho fib SL, ale posunuty).
        """
        cfg = self.cfg
        ep = wave["fib50"]
        mode = _entry_mode_str(cfg)

        if mode == "no_fallback":
            return

        if mode == "limit_fallback":
            # Stara hodnota - v trend-follow strategii nedava smysl.
            self.wave_debug["orders_skipped_limit_fallback_deprecated"] = (
                self.wave_debug.get("orders_skipped_limit_fallback_deprecated", 0) + 1
            )
            return

        if mode == "market_fallback":
            allowed, _reason = self._fill_trend_allowed(
                wave,
                bar_idx,
                bypass_trend_filter=bypass_trend_filter,
                is_two_sided_mirror=is_two_sided_mirror,
            )
            if not allowed:
                self.wave_debug["market_fallback_skipped_trend_filter"] = (
                    self.wave_debug.get("market_fallback_skipped_trend_filter", 0) + 1
                )
                return
            is_buy = (side == "BUY")
            sl_eff = float(sl)
            if risk_span is not None and risk_span > 0:
                sl_eff = (market_price - risk_span) if is_buy else (market_price + risk_span)
                self.wave_debug["waves_opened_abort_shift_sl"] = (
                    self.wave_debug.get("waves_opened_abort_shift_sl", 0) + 1
                )
            lot = calc_lot_backtest(market_price, sl_eff, cfg)
            tp = resolve_effective_tp(cfg, wave, market_price, sl_eff, is_buy=is_buy)
            actual_entry = market_price + self.backtest_slippage * (1 if is_buy else -1)
            dummy_pending = PendingOrder(
                wave, f"{side}_MARKET", market_price, sl_eff, tp, lot,
                bar_idx, bar_time,
                is_two_sided_mirror=is_two_sided_mirror,
                is_ext=is_ext_wave(wave, self.cfg),
            )
            trade = OpenTrade(dummy_pending, bar_idx, actual_entry, bar_time, "MARKET", sl_eff, tp)
            self.open_trades.append(trade)
            self.wave_debug["orders_created_market"] += 1
            self._maybe_place_counter_from_tp(wave, tp, bar_idx, bar_time)
            return

        if mode == "stop_fallback":
            if risk_span is not None and risk_span > 0:
                self.wave_debug["waves_opened_abort_shift_sl"] = (
                    self.wave_debug.get("waves_opened_abort_shift_sl", 0) + 1
                )
            # BUY_STOP / SELL_STOP zpet na entry urovni (cena se musi vratit pres entry).
            # _trigger_pending uz umi STOP types - hit logika zustava (BUY_STOP: high>=ep,
            # SELL_STOP: low<=ep). Slippage se aplikuje pri triggeru jako u kazdeho pendingu.
            lot = calc_lot_backtest(ep, sl, cfg)
            tp = resolve_effective_tp(cfg, wave, ep, sl, is_buy=(side == "BUY"))
            order_type = "BUY_STOP" if side == "BUY" else "SELL_STOP"
            self._add_pending(
                wave, order_type, ep, sl, tp, lot, bar_idx, bar_time,
                is_two_sided_mirror=is_two_sided_mirror,
            )
            self.wave_debug["orders_created_stop_fallback"] = (
                self.wave_debug.get("orders_created_stop_fallback", 0) + 1
            )
            self._maybe_place_counter_from_tp(wave, tp, bar_idx, bar_time)
            return

        # Neznamy mode - tise (engine nesmi shodit cely backtest pro 1 vlnu).
        return

    def _append_pending_vis(
        self, kind: str, bar_idx: int, bar_time: datetime, order: PendingOrder
    ) -> None:
        self.pending_vis.append(
            {
                "kind": kind,
                "bar": int(bar_idx),
                "time": bar_time,
                "wave_time": order.wave_time,
                "order_type": order.order_type,
                "ep": float(order.entry_price),
                "sl": float(order.sl),
                "tp": None if order.tp is None else float(order.tp),
                "is_counter": bool(getattr(order, "is_counter", False)),
                "is_bos_reentry": bool(getattr(order, "is_bos_reentry", False)),
                "is_pp": bool(getattr(order, "is_pp", False)),
                "is_two_sided_mirror": bool(getattr(order, "is_two_sided_mirror", False)),
            }
        )

    def _get_executor(self) -> "Executor":
        """
        Vrati executor (I/O hranice). `run()` vytvari cerstvy `BacktestExecutor`
        per beh; tento lazy fallback obsluhuje PRIMA volani enginu (testy/utilitky)
        bez `run()`/`prepare()`, kde `self._executor` jeste neexistuje. Parita
        zustava — `BacktestExecutor` jen obaluje stejnou in-memory simulaci.
        """
        if self._executor is None:
            from backtest.executor import BacktestExecutor

            self._executor = BacktestExecutor(self)
        return self._executor

    def _add_pending(self, wave: dict, order_type: str, ep: float, sl: float,
                     tp: float, lot: float, bar_idx: int, bar_time: datetime,
                     *, is_two_sided_mirror: bool = False):
        po = PendingOrder(
            wave, order_type, ep, sl, tp, lot, bar_idx, bar_time,
            is_two_sided_mirror=is_two_sided_mirror,
            is_ext=is_ext_wave(wave, self.cfg),
        )
        # Order placement jde pres executor protokol (I/O hranice). §24 TS2 lot
        # mirror se sem dostane s lotem ze strategy/two_sided.py — viz gap-check.
        self._get_executor().place_pending(po, bar_idx, bar_time)

    def _trigger_pending(self, bar_idx: int, bar_time: datetime,
                         high: float, low: float, open_: float):
        remaining = []
        for order in self.pending_orders:
            triggered = False
            actual_entry = order.entry_price

            if order.order_type == "BUY_STOP":
                if high >= order.entry_price:
                    actual_entry = max(order.entry_price, open_)
                    triggered = True
            elif order.order_type == "SELL_STOP":
                if low <= order.entry_price:
                    actual_entry = min(order.entry_price, open_)
                    triggered = True
            elif order.order_type == "BUY_LIMIT":
                if low <= order.entry_price:
                    actual_entry = min(order.entry_price, open_)
                    triggered = True
            elif order.order_type == "SELL_LIMIT":
                if high >= order.entry_price:
                    actual_entry = max(order.entry_price, open_)
                    triggered = True

            if triggered:
                allowed, _reason = self._fill_trend_allowed(
                    order.signal,
                    bar_idx,
                    bypass_trend_filter=False,
                    is_counter=bool(getattr(order, "is_counter", False)),
                    is_bos_reentry=bool(getattr(order, "is_bos_reentry", False)),
                    is_pp=bool(getattr(order, "is_pp", False)),
                    is_two_sided_mirror=bool(getattr(order, "is_two_sided_mirror", False)),
                )
                if not allowed:
                    self.wave_debug["pending_fill_skipped_trend_filter"] = (
                        self.wave_debug.get("pending_fill_skipped_trend_filter", 0) + 1
                    )
                    self._append_pending_vis(
                        "pending_fill_skipped_trend", bar_idx, bar_time, order
                    )
                    continue
                effective_sl = float(order.sl)
                if getattr(order, "is_counter", False):
                    # Counter pending muze byt fillnut lepsi cenou nez byl puvodni LIMIT.
                    # Drzime proto puvodni SL model v procentech i po gap-fillu.
                    effective_sl_pct = max(
                        wave_counter_min_sl_pct(self.cfg),
                        compute_sl_pct_from_entry_and_sl(order.entry_price, order.sl),
                    )
                    effective_sl = compute_sl_price_from_pct(
                        actual_entry, effective_sl_pct, is_buy=(order.dir == 1)
                    )
                # Lot pri fillu:
                #  - PP pozice: pouzij `pp_risk_usd` (oddeleny risk od bezneho).
                #  - ostatni:    bezny cfg.risk_usd.
                if getattr(order, "is_pp", False):
                    new_lot = self._pp_calc_lot(actual_entry, effective_sl)
                else:
                    new_lot = calc_lot_backtest(actual_entry, effective_sl, self.cfg)
                slipped = actual_entry + self.backtest_slippage * (1 if order.dir == 1 else -1)
                # TP pri triggeru:
                #  - wave counter: RRR/BOS_EXIT safety TP z fill entry+SL; WAVE_TARGET_N None
                #  - EXT counter / bos_reentry: drzet tp z order (typicky None)
                #  - PP / two-sided / bezne pendingy: resolve_effective_tp ze skutecne entry+SL
                if (
                    getattr(order, "is_counter", False)
                    and not getattr(order, "is_ext", False)
                    and str(getattr(order, "entry_tag", "")) == "wave_counter"
                ):
                    new_tp = compute_wave_counter_take_profit(
                        self.cfg, slipped, effective_sl, is_buy=(order.dir == 1)
                    )
                elif (
                    getattr(order, "is_counter", False)
                    or getattr(order, "is_bos_reentry", False)
                ):
                    new_tp = order.tp
                else:
                    new_tp = resolve_effective_tp(
                        self.cfg, order.signal, slipped, effective_sl, is_buy=(order.dir == 1)
                    )
                trade = OpenTrade(order, bar_idx, slipped, bar_time,
                                  order.order_type.split("_")[1], effective_sl, new_tp)
                trade.lot = new_lot
                self.open_trades.append(trade)
                if getattr(order, "is_counter", False):
                    self.wave_debug["counter_positions_filled"] = (
                        self.wave_debug.get("counter_positions_filled", 0) + 1
                    )
                if getattr(order, "is_pp", False):
                    self.wave_debug["pp_positions_filled"] = (
                        self.wave_debug.get("pp_positions_filled", 0) + 1
                    )
                    # Po fillu prestava byt PP pending aktivnim "current PP" —
                    # uvolni se misto pro dalsi PP z nove vlny.
                    if self._pp_current_pending is order:
                        self._pp_current_pending = None
            else:
                remaining.append(order)
        self.pending_orders = remaining

    def _check_sl_tp(self, bar_idx: int, bar_time: datetime,
                     high: float, low: float):
        """
        SL/TP jen od baru AFTER entry — identicke s simulate_pine_pending_state.

        Pri trade.tp == None se TP nekontroluje (= zadny broker TP); pozice
        muze skoncit jen na SL nebo BOS exit (WAVE_TARGET_N / BOS_EXIT_PRIORITY).

        SL SAFETY: TP-hit se uplatni jen kdyz je TP na SPRAVNE strane skutecne
        entry (BUY: tp > entry; SELL: tp < entry). Pokud by nastal pripad TP na
        spatne strane (napr. WAVE_TARGET_N s wave geometrii produkujici TP pod
        BUY entry), TP-hit se ignoruje a pozice zustane otevrena pro SL/BOS.
        Tim zarucujeme, ze TP nikdy nezavre pozici se ztratou vetsi nez SL.
        """
        still_open = []
        for trade in self.open_trades:
            if bar_idx <= trade.entry_bar:
                still_open.append(trade)
                continue
            has_tp = trade.tp is not None
            tp_on_correct_side = (
                has_tp
                and (
                    (trade.dir == 1 and float(trade.tp) > float(trade.actual_entry))
                    or (trade.dir == -1 and float(trade.tp) < float(trade.actual_entry))
                )
            )
            if trade.dir == 1:
                sl_hit = low <= trade.sl
                tp_hit = bool(tp_on_correct_side and high >= trade.tp)
            else:
                sl_hit = high >= trade.sl
                tp_hit = bool(tp_on_correct_side and low <= trade.tp)

            # EXT-1 ochrana: TP (a jakykoliv non-SL exit) je behem okna blokovan;
            # pozice muze skoncit jen na SL.
            if tp_hit and not sl_hit and self._ext1_close_blocked(
                bar_idx, "TP", trade=trade,
            ):
                tp_hit = False
            # E23_: behem parent EXT okna jen SL (parita s BOS/TP_WAVE_N ochranou).
            if (
                tp_hit
                and not sl_hit
                and is_ext_secondary_trade(trade)
                and is_trade_within_parent_ext_window(
                    trade,
                    wave_birth_by_time=self.wave_birth_by_time,
                    bar_idx=bar_idx,
                )
            ):
                tp_hit = False
                self.wave_debug["ext_secondary_protected_broker_tp"] = (
                    self.wave_debug.get("ext_secondary_protected_broker_tp", 0) + 1
                )
            if sl_hit or tp_hit:
                reason = "SL" if sl_hit else "TP"
                close_price = trade.sl if sl_hit else trade.tp
                ct = self._make_closed(trade, bar_idx, close_price, bar_time, reason)
                self._append_closed_trade(ct, bar_time)
            else:
                still_open.append(trade)
        self.open_trades = still_open

    def _expire_pending(self, bar_idx: int, bar_time: datetime):
        """
        Stejna semantika jako live `cancel_expired_pending`: stari = business_time_delta
        (So+Ne se do uplynuleho casu nezapocitavaji).

        Limit expirace per pending:
          - EXT WAVE pending (is_ext=True, !is_counter) → `cfg.ext_order_expiry_days`
            (default 7 dnu). EXT WAVE NIKDY neexpiruje cele BOS-cancellation, jen
            timeem.
          - `pending_cancel_mode = "number"` → `cfg.pending_cancel_after_days`
            pro VSECHNY ostatni pendingy (nezavisle na tp_mode).
          - Jinak → `cfg.order_expiry_days`.

        VYJIMKA: counter / two-sided / PP pendingy expiraci ignoruji:
          - counter + two-sided: rusi se jen pri BOS flipu,
          - PP: rusi se pri nove PP vlne nebo BOS flipu.
        """
        default_limit = timedelta(days=self.cfg.order_expiry_days)
        ext_limit = timedelta(days=int(getattr(self.cfg, "ext_order_expiry_days", 7)))
        number_limit = timedelta(days=int(getattr(self.cfg, "pending_cancel_after_days", 14)))
        use_number = self._pending_cancel_mode == PendingCancelMode.NUMBER
        bt = _dt_for_business_age(bar_time)
        kept: List[PendingOrder] = []
        for o in self.pending_orders:
            if (
                getattr(o, "is_counter", False)
                or getattr(o, "is_two_sided_mirror", False)
                or getattr(o, "is_pp", False)
            ):
                kept.append(o)
                continue
            is_ext = bool(getattr(o, "is_ext", False))
            if is_ext:
                limit = ext_limit
            elif use_number:
                limit = number_limit
            else:
                limit = default_limit
            ct = _dt_for_business_age(o.created_time)
            if business_time_delta(ct, bt) <= limit:
                kept.append(o)
            else:
                self._append_pending_vis("pending_expired", bar_idx, bar_time, o)
        self.pending_orders = kept

    def get_run_info(self) -> dict:
        """Diagnosticke informace o zpracovani vln a tvorbe orderu."""
        out = dict(self.wave_debug)
        if self._causal_policy.enabled:
            out.update(causal_debug_summary(self._causal_policy))
        return out

    def _adx14_entries_allowed(self) -> bool:
        if self.adx14_sim is None or not self.adx14_sim.active:
            return True
        return self.adx14_sim.allow_new_entries()

    def _append_closed_trade(self, ct: "ClosedTrade", bar_time: datetime) -> None:
        self.closed_trades.append(ct)
        if self.adx14_sim is None or not self.adx14_sim.active:
            return
        if getattr(ct, "is_pp", False):
            note = "PP"
        elif getattr(ct, "is_bos_reentry", False):
            note = "BOS_REENTRY"
        elif getattr(ct, "is_counter", False):
            note = "COUNTER"
        elif getattr(ct, "is_two_sided_mirror", False):
            note = "TWO_SIDED_MIRROR"
        else:
            note = str(getattr(ct, "entry_type", "") or "WAVE")
        self.adx14_sim.on_trade_closed(
            pnl_usd=float(ct.pnl_usd),
            close_time=bar_time,
            source_risk_usd=float(self.cfg.risk_usd),
            note=note,
        )

    def _bos_flip_state_on_bar(self, bar_idx: int) -> tuple[bool, str, str]:
        """Vrati (flipped, current_direction, previous_direction) pro dany bar."""
        trend_states = getattr(self, "trend_states_per_bar", None)
        if not trend_states or bar_idx >= len(trend_states):
            return False, "neutral", "neutral"
        direction = trend_states[bar_idx].direction
        if direction == "neutral":
            return False, "neutral", "neutral"
        prev_dir = (
            trend_states[bar_idx - 1].direction
            if bar_idx > 0 else "neutral"
        )
        # BOS entry / counter-cancel jen pri close-based flipu, ne pri seed-resetu.
        close_flip = bar_idx in getattr(self, "_close_bos_flip_bar_indices", set())
        flipped = (
            close_flip
            and (prev_dir != direction)
            and (prev_dir != "neutral")
        )
        return flipped, direction, prev_dir

    def _handle_bos_exit_on_bar(self, bar_idx: int, bar_time: datetime,
                                bar_close: float,
                                bar_high: float, bar_low: float,
                                *,
                                close_positions: bool = True,
                                cancel_pendings: bool = True,
                                protected_waves: set[str] | None = None) -> tuple[bool, str]:
        """
        Per-bar BOS-exit logika. Zachovava stare semantiky (zavreni pozic proti
        smeru trendu) a navic:
          - chrani counter / BOS entry / PP / EXT pozice pred per-bar uzavrenim,
          - na BOS flipu (zmena direction[i-1] -> direction[i]) zrusi VSECHNY
            counter pendingy a otevre BOS entry market pozici v novem smeru
            (kdyz cfg.bos_entry_enable=True).

        Parametry:
          close_positions: True → zavre pozice ve smeru rozbiteho trendu (default).
            False → pozice se nezaviraji (pouzito kdyz tp_mode neni BOS_EXIT-like
            a chceme jen pending cleanup pres `pending_cancel_mode = "trend"`).
          cancel_pendings: True → zrusi pendingy ve smeru rozbiteho trendu (default).
            False → pendingy se nerusi (pouzito pri `pending_cancel_mode = "number"`,
            kde pendingy expiruji jen casem).

        SL SAFETY:
          - Pokud bar prekrocil SL pozice (BUY: low <= sl; SELL: high >= sl),
            zavreme pozici na SL cene s reason="SL" namisto na close baru.
            Duvod: v realnem live by SL byl trigerovan intra-bar (broker drzi
            SL jako stop order), takze pozice by se zavrela na SL drive nez
            BOS flip na close baru. Bez teto kontroly by ztrata mohla
            prekrocit planovany SL (pri velkem rozdilu mezi SL a close baru).

        EXT WAVE PROTECTION:
          - EXT WAVE pendingy (is_ext=True) jsou TRVALE chranene pred BOS-cancellation
            i BOS-driven position close — EXT retracementy maji vlastni casovy ramec
            (`cfg.ext_order_expiry_days`, default 7 dnu) a nesmi byt rusene jinou
            funkci.

        Vyhodnocuje se PRED `_trigger_pending` i `_check_sl_tp`.
        """
        flipped, direction, prev_dir = self._bos_flip_state_on_bar(bar_idx)
        if direction == "neutral":
            return False, "neutral"

        # broken direction = ten, ktery uz neni trendem
        broken_dir = -1 if direction == "bull" else +1
        close_reason = bos_per_bar_close_reason(self.cfg)

        # 1) Pozice broken smeru → zavri na close baru (resp. na SL pokud
        #    behem baru doslo k pruraz SL — viz "SL SAFETY" v docstringu).
        #    VYJIMKA: wave_2_no_tp a EXT block pri EXT BOS 0,35 (viz
        #    `_close_ext_trend_positions`). Wave counter / two-sided / EXT
        #    counter pri flipu: should_close_trade_on_bos_flip.
        if close_positions:
            still_open: List[OpenTrade] = []
            protected_waves_set = protected_waves or set()
            bos_wave_time = ""
            bw = self._bos_flip_wave_by_bar.get(bar_idx)
            if bw is not None:
                bos_wave_time = str(bw.get("wave_time", ""))
            for trade in self.open_trades:
                if bar_idx <= trade.entry_bar:
                    still_open.append(trade)
                    continue
                if not should_close_trade_on_bos_flip(
                    trade,
                    broken_dir=broken_dir,
                    flipped=flipped,
                    protected_wave_times=protected_waves_set,
                ):
                    # Pokud je to counter pozice a prezila flip (jede s novym trendem),
                    # musime ji vymazat puvodni TP (pokud tp_mode neni RRR_FIXED),
                    # aby se nezavrela predcasne na starem fixnim TP a pockala na TP_WAVE_N.
                    if flipped and is_bos_flip_follower_trade(trade):
                        if self._tp_mode != TPMode.RRR_FIXED:
                            trade.tp = None
                            
                    # TADY JE KONTROLA NA BOS_EXIT_WAVE_TARGET:
                    # Pokud se jedna o counter pozici, ktera prezila flip, a ma nastaveny
                    # tp_mode na BOS_EXIT_WAVE_TARGET (coz je vlastne WAVE_TARGET_N),
                    # tak ji NEZAVIRAME na BOS flipu, ale cekame na target vlnu.
                    # Protoze counter pozice po flipu jedou s trendem, nechceme je zavrit.
                    
                    still_open.append(trade)
                    continue

                if is_trade_within_parent_ext_window(
                    trade,
                    wave_birth_by_time=self.wave_birth_by_time,
                    bar_idx=bar_idx,
                ):
                    if trade.dir == 1:
                        sl_hit = bar_low <= trade.sl
                    else:
                        sl_hit = bar_high >= trade.sl
                    if sl_hit:
                        ct = self._make_closed(
                            trade, bar_idx, trade.sl, bar_time, "SL"
                        )
                        self._append_closed_trade(ct, bar_time)
                        self.wave_debug["bos_exit_sl_protected"] = (
                            self.wave_debug.get("bos_exit_sl_protected", 0) + 1
                        )
                    else:
                        still_open.append(trade)
                        self.wave_debug["ext_protected_within_parent_window_bos"] = (
                            self.wave_debug.get("ext_protected_within_parent_window_bos", 0) + 1
                        )
                    continue

                # SL SAFETY: detekce SL prurazu v tomto baru.
                if trade.dir == 1:
                    sl_hit = bar_low <= trade.sl
                else:
                    sl_hit = bar_high >= trade.sl
                # EXT-1 ochrana: skip BOS close pokud win[bar_idx]; EXT counter
                # z predchoziho trendu se neblokuje (viz _ext1_close_blocked).
                if not sl_hit and self._ext1_close_blocked(
                    bar_idx, close_reason, trade=trade,
                ):
                    still_open.append(trade)
                    continue
                if sl_hit:
                    ct = self._make_closed(
                        trade, bar_idx, trade.sl, bar_time, "SL"
                    )
                    self._append_closed_trade(ct, bar_time)
                    self.wave_debug["bos_exit_sl_protected"] = (
                        self.wave_debug.get("bos_exit_sl_protected", 0) + 1
                    )
                else:
                    ct = self._make_closed(
                        trade, bar_idx, bar_close, bar_time, close_reason
                    )
                    self._append_closed_trade(ct, bar_time)
                    self.wave_debug["bos_exit_trades_closed"] = (
                        self.wave_debug.get("bos_exit_trades_closed", 0) + 1
                    )
            self.open_trades = still_open

        # 2) Pendingy broken smeru → zrus (pokud cancel_pendings=True).
        #    counter pendingy nesleduji "broken_dir" pravidlo per-bar — rusi se
        #    jen pri skutecnem BOS flipu (flipped=True).
        #    EXT WAVE pendingy (is_ext=True) jsou TRVALE chranene pred BOS-cancel.
        if cancel_pendings:
            kept_pendings: List[PendingOrder] = []
            for order in self.pending_orders:
                is_counter = bool(getattr(order, "is_counter", False))
                is_ext = bool(getattr(order, "is_ext", False))
                # EXT WAVE: nikdy se nezavre BOS-cancellation, ma vlastni expiraci.
                if is_ext and not is_counter:
                    kept_pendings.append(order)
                    continue
                if is_wave_counter_trade(order) or is_two_sided_mirror_trade(order):
                    if flipped:
                        self._append_pending_vis("counter_bos_cancelled", bar_idx, bar_time, order)
                        self.wave_debug["counter_positions_cancelled"] = (
                            self.wave_debug.get("counter_positions_cancelled", 0) + 1
                        )
                    else:
                        kept_pendings.append(order)
                    continue
                if order.dir == broken_dir:
                    if pending_protected_from_bos_direction_cancel(
                        order,
                        self.cfg,
                        waves_by_time=self.waves_by_wave_time,
                    ):
                        kept_pendings.append(order)
                        self.wave_debug["ext_range_pending_bos_cancel_skipped"] = (
                            self.wave_debug.get("ext_range_pending_bos_cancel_skipped", 0) + 1
                        )
                        continue
                    self._append_pending_vis("pending_bos_cancelled", bar_idx, bar_time, order)
                    self.wave_debug["bos_exit_pending_cancelled"] = (
                        self.wave_debug.get("bos_exit_pending_cancelled", 0) + 1
                    )
                    # Pokud BOS prave zrusil PP pending, ktery byl drzen v
                    # `_pp_current_pending`, uvolni i tu referenci, aby pristi
                    # PP cyklus nemusel resit stale reference.
                    if (getattr(order, "is_pp", False)
                            and self._pp_current_pending is order):
                        self._pp_current_pending = None
                else:
                    kept_pendings.append(order)
            self.pending_orders = kept_pendings

        # 3) Pri skutecnem BOS flipu: informacni flagy (wave counter / two-sided
        #    uz byly uzavreny vyse spolecne s ostatnimi tp_mode follower pozicemi).
        if flipped:
            for trade in self.open_trades:
                if getattr(trade, "is_counter", False):
                    trade.is_counter = False
                if getattr(trade, "is_two_sided_mirror", False):
                    trade.is_two_sided_mirror = False
                if is_ext_primary_wave_trade(trade):
                    trade.is_ext = False

        # 4) BOS entry MARKET — po BOS flipu (bos_entry_enable nebo RRR-only flag).
        if flipped and bos_entry_should_open_on_flip(self.cfg):
            self._place_bos_reentry_market(
                new_trend_dir=direction,
                broken_trend_dir=prev_dir,
                bar_idx=bar_idx,
                bar_time=bar_time,
                bar_close=bar_close,
            )
        return flipped, direction

    # ------------------------------------------------------------------
    # WAVE_TARGET_N — varianta G (forming qualified + extension hit)
    # ------------------------------------------------------------------

    def _update_forming_tp_watch_on_bar(self, high: float, low: float) -> None:
        from strategy.wave_target_n_early import wave_target_n_extension_exit_enabled

        watch = getattr(self, "_forming_tp_watch", None)
        if not wave_target_n_extension_exit_enabled(self.cfg) or watch is None:
            return
        if watch.extension_hit_done:
            return
        watch.update_extreme(high, low)
        if watch.try_arm(self.cfg):
            self.wave_debug["tp_extension_armed"] = (
                self.wave_debug.get("tp_extension_armed", 0) + 1
            )

    def _on_wave_born_forming_tp_context(self, wave: dict, bar_idx: int) -> None:
        from strategy.wave_target_n_early import (
            start_forming_tp_watch,
            wave_target_n_early_g_enabled,
        )

        if not wave_target_n_early_g_enabled(self.cfg):
            return
        if not self.wave_sequence_info:
            return
        info = self.wave_sequence_info.get(wave["wave_time"])
        if info is None or info.index_in_trend is None:
            return
        target_n = int(getattr(self.cfg, "tp_target_wave_index", 0) or 0)
        idx = int(info.index_in_trend)
        if is_tp_wave_index(idx, target_n):
            self._forming_tp_watch = None
            return
        watch = start_forming_tp_watch(
            prev_wave=wave,
            index_in_trend=idx,
            target_n=target_n,
            start_bar=bar_idx,
        )
        if watch is not None:
            self._forming_tp_watch = watch
            self.wave_debug["tp_extension_watch_started"] = (
                self.wave_debug.get("tp_extension_watch_started", 0) + 1
            )

    def _maybe_fire_extension_tp_on_bar(
        self,
        bar_idx: int,
        bar_time: datetime,
        open_: float,
        high: float,
        low: float,
        close_: float,
    ) -> None:
        from strategy.wave_target_n_early import (
            extension_tp_hit_on_bar,
            tp_wave_intrabar_tp_before_sl,
            trade_exit_on_extension_bar,
            wave_target_n_extension_exit_enabled,
        )

        watch = getattr(self, "_forming_tp_watch", None)
        if not wave_target_n_extension_exit_enabled(self.cfg) or watch is None:
            return
        if watch.extension_hit_done:
            return
        watch.update_extreme(high, low)
        watch.try_arm(self.cfg)
        if not watch.armed:
            return
        if not extension_tp_hit_on_bar(
            watch, high=high, low=low, close=close_, open_=open_,
        ):
            return

        trend_dir = int(watch.trend_dir)
        tp_before_sl = tp_wave_intrabar_tp_before_sl(self.cfg)
        armed_tp = watch.armed_tp
        ext_hit = True
        still_open: List[OpenTrade] = []
        closed = 0
        sl_closed = 0
        for trade in self.open_trades:
            if bar_idx <= trade.entry_bar:
                still_open.append(trade)
                continue
            if not should_close_trade_on_tp_wave_n(trade, trend_dir):
                still_open.append(trade)
                continue
            if is_trade_within_parent_ext_window(
                trade,
                wave_birth_by_time=self.wave_birth_by_time,
                bar_idx=bar_idx,
            ):
                from strategy.wave_target_n_early import sl_hit_for_trade
                if sl_hit_for_trade(trade, high=high, low=low):
                    ct = self._make_closed(
                        trade, bar_idx, trade.sl, bar_time, "SL",
                    )
                    self._append_closed_trade(ct, bar_time)
                    sl_closed += 1
                else:
                    still_open.append(trade)
                continue
            if self._ext1_close_blocked(
                bar_idx, "TP_EXTENSION_HIT", trade=trade,
            ):
                still_open.append(trade)
                continue
            price, reason = trade_exit_on_extension_bar(
                trade,
                high=high,
                low=low,
                armed_tp=armed_tp,
                ext_hit=ext_hit,
                tp_before_sl=tp_before_sl,
            )
            if price is None or reason is None:
                still_open.append(trade)
                continue
            ct = self._make_closed(
                trade, bar_idx, float(price), bar_time, reason,
            )
            self._append_closed_trade(ct, bar_time)
            if reason == "SL":
                sl_closed += 1
            else:
                closed += 1
        self.open_trades = still_open
        if closed or sl_closed:
            watch.extension_hit_done = True
            self.wave_debug["tp_extension_hit_events"] = (
                self.wave_debug.get("tp_extension_hit_events", 0) + 1
            )
            self.wave_debug["tp_extension_positions_closed"] = (
                self.wave_debug.get("tp_extension_positions_closed", 0) + closed
            )
            self.wave_debug["tp_extension_sl_protected"] = (
                self.wave_debug.get("tp_extension_sl_protected", 0) + sl_closed
            )
            self._open_wave_counter_market_from_g_watch(
                watch, bar_idx, bar_time,
            )

    # ------------------------------------------------------------------
    # WAVE_TARGET_N — TP-wave event, counter pozice, BOS re-entry
    # ------------------------------------------------------------------

    def _maybe_fire_tp_wave_event(self, wave: dict, bar_idx: int,
                                  bar_time: datetime,
                                  bar_close: float,
                                  bar_high: float, bar_low: float) -> None:
        """
        Kdyz se na baru rodi VLNA, ktera je TP-vlnou aktualniho trendu
        (cfg.tp_target_wave_index nebo +2, +4, ...), provede:
          1) AKTIVNE UZAVRE vsechny otevrene pozice ve smeru trendu (= smer
             vlny) na cene bar_close s reason="TP_WAVE_N". To je hlavni
             "wave target N" exit semantika — pozice se nezavre az na BOS
             s nejistou cenou, ale aktivne na baru wave-N.
             SL SAFETY: pokud bar zasahl SL, zavre na SL cene s reason="SL"
             (intra-bar by SL fired drive).

        UPDATE (uziv. pozadavek):
          - DRIVE tato funkce taky rusila pendingy ve smeru trendu (PP, WAVE,
            EXT, BOS_REENTRY) na TP-fire eventu. UZIVATEL pozaduje toto chovani
            ODSTRANIT — TP-wave event nesmi rusit dalsi ordery ve smeru trendu.
            Pendingy zustanou ve fronte a budou se ridit normalnim lifecycle
            (BOS cancel, expirace, pending_cancel_mode).
          - DRIVE tato funkce taky umistila counter LIMIT pending v opacnem
            smeru na TP cene. UZIVATEL pozaduje, aby v WAVE_TARGET_N counter
            pozice fungovaly STEJNE jako v BOS_EXIT — tj. counter pending se
            umisti IHNED pri narozeni vlny (v `_maybe_place_counter_from_tp`),
            ne az na TP-fire eventu. Toto umisteni je tedy take ODSTRANENE.

        Tato funkce je volana NEZAVISLE na tom, zda nasledne wave projde
        filtry pro otevreni nove pozice — TP-vlna ovlivnuje uz otevrene pozice
        bez ohledu na to, zda se sama stane vstupem pro novou.

        Pozn.: scope = `trade.dir == trend_dir` zachycuje i byvale counter
        pozice, ktere po BOS flipu prisly o `is_counter` flag a jsou nyni
        v novem trend_dir — i tyto se uzavou na nejbliz wave-N eventu noveho
        trendu (mistono drzeni az do dalsiho BOS).
        """
        if self._tp_mode not in WAVE_TARGET_N_FAMILY:
            return
        if not self.wave_sequence_info:
            return

        info = self.wave_sequence_info.get(wave["wave_time"])
        if info is None:
            return
        target_n = int(getattr(self.cfg, "tp_target_wave_index", 0) or 0)
        idx = info.index_in_trend if info else None
        if idx is None:
            return
        if not is_tp_wave_index(idx, target_n):
            return

        from strategy.wave_target_n_early import (
            tp_wave_early_fallback_birth,
            wave_target_n_early_g_enabled,
        )

        watch = getattr(self, "_forming_tp_watch", None)
        g_fallback_birth = False
        g_counter_already_placed = False
        if wave_target_n_early_g_enabled(self.cfg) and watch is not None:
            g_counter_already_placed = bool(watch.counter_placed)
            if watch.extension_hit_done:
                self._forming_tp_watch = None
                return
            if not tp_wave_early_fallback_birth(self.cfg):
                self._forming_tp_watch = None
                return
            g_fallback_birth = True
        self._forming_tp_watch = None

        # Smer trendu = smer TP-vlny (z definice indexu >= 1 je vlna ve smeru trendu).
        trend_dir = int(wave["dir"])
        current_wave_time = str(wave.get("wave_time", ""))

        # 1) AKTIVNI UZAVRENI pozic ve smeru trendu na bar_close
        # (SL safety: pokud bar prekrocil SL, zavri na SL cene).
        # EXT block pozice (E23_/ECT_/ECB_) se zaviraji spolecne s tp_mode
        # (viz should_close_trade_on_tp_wave_n), ne zde.
        positions_closed = 0
        positions_sl_protected = 0
        still_open: List[OpenTrade] = []
        for trade in self.open_trades:
            if not should_close_trade_on_tp_wave_n(trade, trend_dir):
                still_open.append(trade)
                continue

            if is_trade_within_parent_ext_window(
                trade,
                wave_birth_by_time=self.wave_birth_by_time,
                bar_idx=bar_idx,
            ):
                if trade.dir == 1:
                    sl_hit = bar_low <= trade.sl
                else:
                    sl_hit = bar_high >= trade.sl
                if sl_hit:
                    ct = self._make_closed(
                        trade, bar_idx, trade.sl, bar_time, "SL"
                    )
                    self._append_closed_trade(ct, bar_time)
                    positions_sl_protected += 1
                else:
                    still_open.append(trade)
                    self.wave_debug["ext_protected_within_parent_window_tp"] = (
                        self.wave_debug.get("ext_protected_within_parent_window_tp", 0) + 1
                    )
                continue

            # SL SAFETY (stejna logika jako v _handle_bos_exit_on_bar)
            if trade.dir == 1:
                sl_hit = bar_low <= trade.sl
            else:
                sl_hit = bar_high >= trade.sl
            # EXT-1 ochrana: TP_WAVE_N (non-SL) je behem okna blokovan.
            if not sl_hit and self._ext1_close_blocked(
                bar_idx, "TP_WAVE_N", trade=trade,
            ):
                still_open.append(trade)
                continue
            if sl_hit:
                ct = self._make_closed(
                    trade, bar_idx, trade.sl, bar_time, "SL"
                )
                self._append_closed_trade(ct, bar_time)
                positions_sl_protected += 1
            else:
                ct = self._make_closed(
                    trade, bar_idx, bar_close, bar_time, "TP_WAVE_N"
                )
                self._append_closed_trade(ct, bar_time)
                positions_closed += 1
        self.open_trades = still_open

        # 2) (ODSTRANENO per uziv. pozadavek) — TP-wave event UZ NERUSI pendingy
        # ve smeru trendu. Pendingy se ridi normalnim lifecycle (BOS cancel,
        # expirace, pending_cancel_mode).

        self.wave_debug["tp_wave_events_fired"] = (
            self.wave_debug.get("tp_wave_events_fired", 0) + 1
        )
        self.wave_debug["tp_wave_positions_closed"] = (
            self.wave_debug.get("tp_wave_positions_closed", 0) + positions_closed
        )
        self.wave_debug["tp_wave_positions_sl_protected"] = (
            self.wave_debug.get("tp_wave_positions_sl_protected", 0)
            + positions_sl_protected
        )

        if (
            is_wave_target_n_g(self.cfg)
            and g_fallback_birth
            and not g_counter_already_placed
        ):
            tp_raw = wave.get("wave_target_tp_price")
            tp_for_counter = float(tp_raw) if tp_raw is not None else None
            self._maybe_place_counter_from_tp(
                wave,
                tp_for_counter,
                bar_idx,
                bar_time,
                allow_g_fallback=True,
            )

        # 3) (ODSTRANENO per uziv. pozadavek) — counter pending UZ NEUMISTUJE
        # tento event. V WAVE_TARGET_N se counter klade ihned pri narozeni vlny
        # (`_maybe_place_counter_from_tp`), stejne jako v BOS_EXIT.

    def _safety_rrr_tp(self, entry: float, sl: float, is_buy: bool) -> float | None:
        """
        Spocti safety RRR TP cenu = entry + R x |entry - sl| (BUY) nebo
        entry - R x |entry - sl| (SELL). Pouziva se v WAVE_TARGET_N modu pro
        umistovani counter LIMITu pri narozeni vlny (kdy resolve_effective_tp
        vrati None, protoze WAVE_TARGET_N nepocita TP pri narozeni vlny).
        """
        try:
            sl_dist = abs(float(entry) - float(sl))
            if sl_dist <= 0.0:
                return None
            rrr = float(getattr(self.cfg, "rrr", 1.0))
            return (
                float(entry) + rrr * sl_dist if is_buy else float(entry) - rrr * sl_dist
            )
        except (TypeError, ValueError):
            return None

    def _maybe_place_counter_from_tp(
        self,
        wave: dict,
        tp_price: float | None,
        bar_idx: int,
        bar_time: datetime,
        *,
        allow_g_fallback: bool = False,
    ) -> None:
        """
        Polozi counter pending pri narozeni vlny.

        Counter vzdy jen na TP-vlne (N, N+2, ...) dle `tp_target_wave_index`
        a `is_tp_wave_index` — stejne pro vsechny tp_mode.

        - WAVE_TARGET_N: extension TP (`wave_target_tp_price` / compute).
        - RRR_FIXED / BOS_EXIT: TP z `resolve_effective_tp` (RRR u hlavniho vstupu
          na te TP-vlne). Zadny safety RRR fallback pro vlny K < N.
        - wave_target_n_g: skip z WAVE entry; fallback birth vola s allow_g_fallback.
        """
        if is_wave_target_n_g(self.cfg) and not allow_g_fallback:
            return
        if not wave_counter_two_sided_orders_enabled(self.cfg):
            return
        if bool(wave.get("post_ext_trend_suppressed", False)):
            self.wave_debug["counter_skipped_post_ext_suppressed"] = (
                self.wave_debug.get("counter_skipped_post_ext_suppressed", 0) + 1
            )
            return
        info = self.wave_sequence_info.get(wave["wave_time"])
        if info is None:
            self.wave_debug["counter_positions_skipped_no_sequence_info"] = (
                self.wave_debug.get("counter_positions_skipped_no_sequence_info", 0) + 1
            )
            return

        target_n = int(getattr(self.cfg, "tp_target_wave_index", 0) or 0)
        idx = info.index_in_trend if info else None
        if idx is None:
            return
        if target_n <= 0 or not is_tp_wave_index(idx, target_n):
            return

        if self._tp_mode in WAVE_TARGET_N_FAMILY:
            raw_tp = wave.get("wave_target_tp_price")
            if raw_tp is not None:
                tp_price = float(raw_tp)
            else:
                prev_w = find_wave_by_time(
                    getattr(self, "_all_waves", None) or list(self.waves_by_wave_time.values()),
                    info.prev_same_dir_in_trend_wave_time,
                )
                tp_price = compute_wave_target_tp_price(wave, prev_w, self.cfg)
        if tp_price is None:
            return

        self._place_counter_position_pending(
            wave=wave,
            trend_dir=int(wave["dir"]),
            tp_price=float(tp_price),
            bar_idx=bar_idx,
            bar_time=bar_time,
            info=info,
        )

    def _place_counter_position_pending(self, wave: dict, trend_dir: int,
                                        tp_price: float, bar_idx: int,
                                        bar_time: datetime,
                                        info: WaveSequenceInfo) -> None:
        """
        Polozi counter LIMIT pending v opacnem smeru na cenu tp_price.

        SL counter pozice: dle SL-ladderu z velikosti PREDCHOZI stejnosmerne vlny
        v trendu (= ta sama vlna, ze ktere se pocita extension TP).
        Lot: calc_lot_backtest(entry=tp_price, sl=counter_sl, cfg) → respektuje
        cfg.risk_usd a aktualni ladder %.

        Counter pending:
          - nema expiraci (is_counter=True flag chrani v _expire_pending),
          - rusi se POUZE pri BOS flipu (v `_handle_bos_exit_on_bar`),
          - RRR_FIXED / BOS_EXIT: TP = cfg.rrr × SL vzdalenost od entry;
          - WAVE_TARGET_N: tp=None, exit aktivne na TP-vlne N (TP_WAVE_N event);
          - is_counter=True → ochrana pred per-bar BOS-exit do prvniho flipu.
        """
        prev_wt = info.prev_same_dir_in_trend_wave_time
        if not prev_wt:
            self.wave_debug["counter_positions_skipped_no_prev"] = (
                self.wave_debug.get("counter_positions_skipped_no_prev", 0) + 1
            )
            return
        prev_wave = self.waves_by_wave_time.get(prev_wt)
        if prev_wave is None:
            self.wave_debug["counter_positions_skipped_no_prev"] = (
                self.wave_debug.get("counter_positions_skipped_no_prev", 0) + 1
            )
            return

        if not self._adx14_entries_allowed():
            self.wave_debug["counter_positions_skipped_adx14_gate"] = (
                self.wave_debug.get("counter_positions_skipped_adx14_gate", 0) + 1
            )
            return

        # Counter je v OPACNEM smeru nez trend. SL counter pozice je na opacne
        # strane vstupu nez u trend-dir pozice: BUY → sl pod entry, SELL → sl nad entry.
        counter_dir = -trend_dir
        is_buy_counter = (counter_dir == 1)
        prev_size_pct = float(prev_wave.get("move_pct", 0.0))
        sl_pct, counter_sl = compute_ladder_sl_from_wave_size(
            tp_price,
            prev_size_pct,
            self.cfg,
            is_buy=is_buy_counter,
            min_sl_pct=wave_counter_min_sl_pct(self.cfg),
        )
        if sl_pct <= 0.0:
            return

        lot = calc_lot_backtest(tp_price, counter_sl, self.cfg)
        if lot <= 0.0:
            return

        counter_tp = compute_wave_counter_take_profit(
            self.cfg, float(tp_price), float(counter_sl), is_buy=is_buy_counter
        )

        order_type = "BUY_LIMIT" if is_buy_counter else "SELL_LIMIT"
        # Synteticky signal s prepsanym smerem, aby PendingOrder.dir odpovidal
        # counter smeru (vychozi by se cetl z wave["dir"] = trend_dir).
        synth_signal = dict(wave)
        synth_signal["dir"] = counter_dir
        synth_signal.pop("wave_target_tp_price", None)
        # Counter neexpiruje, ale created_time je info-only.
        po = PendingOrder(
            signal=synth_signal,
            order_type=order_type,
            entry_price=float(tp_price),
            sl=float(counter_sl),
            tp=counter_tp,
            lot=float(lot),
            created_bar=int(bar_idx),
            created_time=bar_time,
            dir_override=counter_dir,
            is_counter=True,
            entry_tag="wave_counter",
        )
        self.pending_orders.append(po)
        self._append_pending_vis("counter_pending_created", bar_idx, bar_time, po)
        self.wave_debug["counter_positions_placed"] = (
            self.wave_debug.get("counter_positions_placed", 0) + 1
        )

    def _open_wave_counter_market_from_g_watch(
        self,
        watch: Any,
        bar_idx: int,
        bar_time: datetime,
    ) -> None:
        """G varianta: MARKET counter na armed_tp ve stejnem baru jako TP_EXTENSION_HIT."""
        from strategy.wave_target_n_early import (
            g_counter_wave_time,
            wave_counter_entry_allowed,
        )

        if not is_wave_target_n_g(self.cfg):
            return
        if bool(getattr(watch, "counter_placed", False)):
            return
        if not wave_counter_entry_allowed(self.cfg):
            return
        armed_tp = getattr(watch, "armed_tp", None)
        if armed_tp is None:
            return
        if not self._adx14_entries_allowed():
            self.wave_debug["counter_positions_skipped_adx14_gate"] = (
                self.wave_debug.get("counter_positions_skipped_adx14_gate", 0) + 1
            )
            return

        prev_wave = dict(getattr(watch, "prev_wave", {}) or {})
        tp_price = float(armed_tp)
        setup = compute_wave_counter_sl_setup(
            self.cfg,
            trend_dir=int(watch.trend_dir),
            tp_price=tp_price,
            prev_wave=prev_wave,
        )
        if setup is None:
            return
        counter_dir, _sl_pct, counter_sl, counter_tp = setup
        lot = calc_lot_backtest(tp_price, counter_sl, self.cfg)
        if lot <= 0.0:
            return

        wave_time_key = g_counter_wave_time(watch)
        is_buy_counter = (counter_dir == 1)
        actual_entry = tp_price + self.backtest_slippage * (1 if is_buy_counter else -1)
        side = "BUY" if is_buy_counter else "SELL"
        synth_signal = dict(prev_wave)
        synth_signal["wave_time"] = wave_time_key
        synth_signal["dir"] = counter_dir
        synth_signal.pop("wave_target_tp_price", None)
        dummy = PendingOrder(
            synth_signal,
            f"{side}_MARKET",
            tp_price,
            float(counter_sl),
            (None if counter_tp is None else float(counter_tp)),
            float(lot),
            int(bar_idx),
            bar_time,
            dir_override=counter_dir,
            is_counter=True,
            entry_tag="wave_counter",
        )
        trade = OpenTrade(
            dummy,
            int(bar_idx),
            actual_entry,
            bar_time,
            "MARKET",
            float(counter_sl),
            (None if counter_tp is None else float(counter_tp)),
        )
        self.open_trades.append(trade)
        watch.counter_placed = True
        watch.counter_wave_time_key = wave_time_key
        self.wave_debug["counter_positions_placed"] = (
            self.wave_debug.get("counter_positions_placed", 0) + 1
        )
        self.wave_debug["counter_positions_placed_g_extension"] = (
            self.wave_debug.get("counter_positions_placed_g_extension", 0) + 1
        )

    def _place_bos_reentry_market(self, new_trend_dir: str, broken_trend_dir: str,
                                   bar_idx: int, bar_time: datetime,
                                   bar_close: float) -> None:
        """
        Po BOS flipu otevre MARKET pozici v NOVEM smeru trendu.

        SL: dle SL-ladderu z velikosti POSLEDNI vlny v ROZBITEM smeru
        (pri bear→bull flipu se bere posledni DOWN vlna ze stareho bear trendu;
        pri bull→bear flipu posledni UP vlna ze stareho bull trendu — to je vlna,
        ktera definovala swing level, jenz se na tomto baru proboural).
        Risk: cfg.risk_usd; lot prepocitan z calc_lot_backtest.

        Re-entry pozice:
          - dir = +1 pro bull, -1 pro bear,
          - tp = None (nasleduje WAVE_TARGET_N / BOS pravidla v novem trendu),
          - is_bos_reentry=True (informacni; jinak se chova jako bezna pozice).
        """
        if not self._adx14_entries_allowed():
            self.wave_debug["bos_reentry_skipped_adx14_gate"] = (
                self.wave_debug.get("bos_reentry_skipped_adx14_gate", 0) + 1
            )
            return
        # Smer noveho trendu jako int
        new_dir = 1 if new_trend_dir == "bull" else -1
        # Posledni vlna rozbiteho trendu: pri flipu z bullu (broken="bull") = posledni UP.
        # POZN.: trend_states_per_bar[bar_idx] uz reflektuje NOVY trend (state
        # se reset po BOS), tedy last_*_box_* tam nejsou validni jako "rozbita
        # vlna". Pouzijeme stav z PREDESLEHO baru (bar_idx - 1).
        if bar_idx <= 0 or bar_idx >= len(self.trend_states_per_bar):
            return
        prev_state = self.trend_states_per_bar[bar_idx - 1]
        if broken_trend_dir == "bull":
            broken_wave_time = prev_state.last_up_wave_time
        else:
            broken_wave_time = prev_state.last_down_wave_time
        if not broken_wave_time:
            return
        broken_wave = self.waves_by_wave_time.get(broken_wave_time)
        if broken_wave is None:
            return

        is_buy = (new_dir == 1)
        # Slippage shodne s _handle_fallback / _trigger_pending
        actual_entry = float(bar_close) + self.backtest_slippage * (1 if is_buy else -1)
        wave_size_pct = float(broken_wave.get("move_pct", 0.0))
        sl_pct, sl_price = compute_ladder_sl_from_wave_size(
            actual_entry, wave_size_pct, self.cfg, is_buy=is_buy
        )
        if sl_pct <= 0.0:
            return

        lot = calc_lot_backtest(actual_entry, sl_price, self.cfg)
        if lot <= 0.0:
            return

        # Synteticky signal pro PendingOrder/OpenTrade
        synth_signal = dict(broken_wave)
        synth_signal["dir"] = new_dir
        synth_signal.pop("wave_target_tp_price", None)
        # Wave_time prefix, aby slo v logu poznat re-entry (zachova jednoznacnost)
        synth_signal["wave_time"] = f"BOS_REENTRY_{broken_wave_time}_{int(bar_idx)}"
        tp = resolve_effective_tp(
            self.cfg, synth_signal, actual_entry, sl_price, is_buy=is_buy,
        )

        side = "BUY" if is_buy else "SELL"
        order_type = f"{side}_MARKET"
        dummy_pending = PendingOrder(
            signal=synth_signal,
            order_type=order_type,
            entry_price=float(bar_close),
            sl=float(sl_price),
            tp=(None if tp is None else float(tp)),
            lot=float(lot),
            created_bar=int(bar_idx),
            created_time=bar_time,
            dir_override=new_dir,
            is_bos_reentry=True,
        )
        trade = OpenTrade(
            dummy_pending, bar_idx, actual_entry, bar_time, "MARKET",
            float(sl_price), (None if tp is None else float(tp)),
        )
        self.open_trades.append(trade)
        self.wave_debug["bos_reentry_positions_opened"] = (
            self.wave_debug.get("bos_reentry_positions_opened", 0) + 1
        )

    # ------------------------------------------------------------------
    # TWO-SIDED ENTRY
    # ------------------------------------------------------------------

    def _maybe_fire_two_sided_counter(
        self,
        parent_wave: dict,
        counter_wave: dict,
        bar_idx: int,
        bar_time: datetime,
        bar: pd.Series,
    ) -> None:
        """
        WAVE protipozice na protivlni B po doteku FIB rodice A (viz strategy.two_sided).
        """
        if not wave_counter_two_sided_orders_enabled(self.cfg):
            return
        self.wave_debug["two_sided_mirror_attempts"] = (
            self.wave_debug.get("two_sided_mirror_attempts", 0) + 1
        )
        sig = prepare_two_sided_counter_signal(counter_wave, self.cfg)
        sig["__two_sided_parent_wave_time"] = str(parent_wave.get("wave_time", ""))
        cwt = str(counter_wave.get("wave_time", ""))
        bypass = bool(getattr(self.cfg, "two_sided_entry_bypass_trend_filter", True))
        if self._process_new_wave(
            sig,
            bar_idx,
            bar_time,
            bar,
            bypass_trend_filter=bypass,
            is_two_sided_mirror=True,
        ):
            self._tag_two_sided_counter_wave(cwt, sig)
            pwt = str(parent_wave.get("wave_time", ""))
            armed = self._two_sided_tracker.armed.get(pwt)
            if armed is not None:
                armed.entry_fired = True
            self._two_sided_tracker.discard_parent(pwt)

    # ------------------------------------------------------------------
    # PP POSITIONS  (Push-through / Pokracovani pozice)
    # ------------------------------------------------------------------

    def _pp_calc_lot(self, entry_price: float, sl_price: float) -> float:
        """Lot pro PP pozici (vlastni `cfg.pp_risk_usd`, contract_size beze zmeny)."""
        cfg = self.cfg
        sl_dist = abs(float(entry_price) - float(sl_price))
        if sl_dist == 0.0:
            return float(getattr(cfg, "min_lot", 0.01))
        risk_per_lot = sl_dist * float(cfg.contract_size)
        if risk_per_lot <= 0:
            return float(getattr(cfg, "min_lot", 0.01))
        risk_usd = float(getattr(cfg, "pp_risk_usd", cfg.risk_usd))
        return round_to_step(risk_usd / risk_per_lot, cfg)

    def _cancel_current_pp_pending(self, bar_idx: int, bar_time: datetime) -> bool:
        """Zrusi aktualni PP pending (pokud existuje a stale je v pending_orders)."""
        po = self._pp_current_pending
        if po is None:
            return False
        try:
            self.pending_orders.remove(po)
        except ValueError:
            # Uz nebyl v pendingech (treba dobehl BOS-exit cleanup).
            self._pp_current_pending = None
            return False
        self._append_pending_vis("pp_replaced", bar_idx, bar_time, po)
        self.wave_debug["pp_orders_cancelled_new_wave"] = (
            self.wave_debug.get("pp_orders_cancelled_new_wave", 0) + 1
        )
        self._pp_current_pending = None
        return True

    def _pp_sync_trend_phase(self, bar_idx: int) -> None:
        """Pri BOS prepnuti bull<->bear vycistit PP broken set (nove vlny v nove fazi)."""
        if not self.trend_states_per_bar or bar_idx >= len(self.trend_states_per_bar):
            return
        phase = self.trend_states_per_bar[bar_idx].direction
        if phase not in ("bull", "bear"):
            return
        if phase != self._pp_trend_phase:
            self._pp_trend_phase = phase
            self._pp_broken_wave_times.clear()

    def _pp_on_new_wave_born(self, wave: dict, bar_idx: int, bar_time: datetime) -> None:
        """
        Nova vlna ve smeru aktualniho trendu → zrusit PP pending z predchozi vlny.
        (Order je identifikovan pres `is_pp` + `wave_time` v PendingOrder / MT5 PP_ comment.)
        """
        if not self.trend_states_per_bar or bar_idx >= len(self.trend_states_per_bar):
            return
        state = self.trend_states_per_bar[bar_idx]
        if state.direction not in ("bull", "bear"):
            return
        trend_dir = 1 if state.direction == "bull" else -1
        if int(wave.get("dir", 0)) != trend_dir:
            return
        po = self._pp_current_pending
        if po is None:
            return
        if str(po.wave_time) == str(wave.get("wave_time")):
            return
        self._cancel_current_pp_pending(bar_idx, bar_time)

    def _find_pp_candidate_wave(self, bar_idx: int, trend_dir: int) -> Optional[dict]:
        """
        Nejnovejsi narozena vlna ve smeru trendu bez PP break.
        Eligibility (ukoncena vlna, HH/HL) se kontroluje v `_process_pp_break_on_bar`.
        """
        return find_pp_candidate_wave(
            self._all_waves,
            self.wave_birth_by_time,
            bar_idx,
            trend_dir,
            broken_wave_times=self._pp_broken_wave_times,
        )

    def _process_pp_break_on_bar(self, bar_idx: int, bar_time: datetime,
                                  bar_close: float) -> None:
        """
        Per-bar PP detekce: zjisti aktualni trend (z `trend_states_per_bar`).
        Pokud je trend bull/bear a aktualni close prekroci box_top (bull) / box_bottom
        (bear) "kandidatni" trend-dir vlny (= nejnovejsi vlna ve smeru trendu,
        ktera jeste nebyla PP-brokana), polozi PP LIMIT (s fallback na MARKET
        kdyby v live broker neprijal LIMIT — v backtestu LIMIT vzdy uspesny).

        """
        if not self.trend_states_per_bar or bar_idx >= len(self.trend_states_per_bar):
            return
        self._pp_sync_trend_phase(bar_idx)
        state = self.trend_states_per_bar[bar_idx]
        if state.direction not in ("bull", "bear"):
            return
        pp_ok = (
            bar_idx < len(self._pp_trend_confirmed_per_bar)
            and bool(self._pp_trend_confirmed_per_bar[bar_idx])
        )
        if not pp_ok:
            self.wave_debug["pp_skipped_trend_from_seed_reset"] = (
                self.wave_debug.get("pp_skipped_trend_from_seed_reset", 0) + 1
            )
            return
        trend_dir = 1 if state.direction == "bull" else -1

        candidate = self._find_pp_candidate_wave(bar_idx, trend_dir)
        if candidate is None:
            return

        pp_ok_wave, pp_skip_reason = pp_wave_eligible_for_break(
            candidate,
            bar_idx=bar_idx,
            wave_birth=self.wave_birth_by_time,
            cfg=self.cfg,
        )
        if not pp_ok_wave:
            if pp_skip_reason == "wave_not_finished":
                self.wave_debug["pp_skipped_wave_not_finished"] = (
                    self.wave_debug.get("pp_skipped_wave_not_finished", 0) + 1
                )
            elif pp_skip_reason == "hh_hl_fail":
                self.wave_debug["pp_skipped_hh_hl"] = (
                    self.wave_debug.get("pp_skipped_hh_hl", 0) + 1
                )
            elif pp_skip_reason == "post_ext_trend_suppressed":
                self.wave_debug["pp_skipped_post_ext_suppressed"] = (
                    self.wave_debug.get("pp_skipped_post_ext_suppressed", 0) + 1
                )
            elif pp_skip_reason == "ext_wave":
                self.wave_debug["pp_skipped_ext_wave"] = (
                    self.wave_debug.get("pp_skipped_ext_wave", 0) + 1
                )
            elif pp_skip_reason == "in_ext_range":
                self.wave_debug["pp_skipped_in_ext_range"] = (
                    self.wave_debug.get("pp_skipped_in_ext_range", 0) + 1
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

        if not self._adx14_entries_allowed():
            self.wave_debug["pp_skipped_adx14_gate"] = (
                self.wave_debug.get("pp_skipped_adx14_gate", 0) + 1
            )
            return

        # NOVY PP! Zrus pripadny stary PP pending (max 1 najednou).
        self._cancel_current_pp_pending(bar_idx, bar_time)

        # PP order: LIMIT @ trigger_level, SL z `pp_sl_pct`, TP dle tp_mode
        # (resolve_effective_tp; pro WAVE_TARGET_N vrati None — TP se nastavi
        # az pri nasledujici TP-vlne v trendu).
        cfg = self.cfg
        is_buy = (trend_dir == 1)
        pp_sl_pct = float(getattr(cfg, "pp_sl_pct", 0.21))
        sl_price = compute_sl_price_from_pct(trigger_level, pp_sl_pct, is_buy=is_buy)
        tp = resolve_effective_tp(cfg, candidate, trigger_level, sl_price, is_buy=is_buy)
        lot = self._pp_calc_lot(trigger_level, sl_price)
        if lot <= 0.0:
            return

        order_type = "BUY_LIMIT" if is_buy else "SELL_LIMIT"
        po = PendingOrder(
            signal=candidate,
            order_type=order_type,
            entry_price=float(trigger_level),
            sl=float(sl_price),
            tp=(None if tp is None else float(tp)),
            lot=float(lot),
            created_bar=int(bar_idx),
            created_time=bar_time,
            dir_override=trend_dir,
            is_pp=True,
        )
        self.pending_orders.append(po)
        self._pp_current_pending = po
        self._pp_broken_wave_times.add(str(candidate["wave_time"]))
        self._append_pending_vis("pp_created", bar_idx, bar_time, po)
        self.wave_debug["pp_breaks_detected"] = (
            self.wave_debug.get("pp_breaks_detected", 0) + 1
        )
        self.wave_debug["pp_orders_placed"] = (
            self.wave_debug.get("pp_orders_placed", 0) + 1
        )

    # ------------------------------------------------------------------
    # EXT BLOK — sekundarni vstup, counter time/BOS, close-trend handler
    # ------------------------------------------------------------------

    def _process_ext_secondary_for_wave(self, wave: dict, bar_idx: int,
                                         bar_time: datetime,
                                         bar: pd.Series) -> None:
        cfg = self.cfg
        if not is_ext_wave(wave, cfg):
            return
        sec_signal = compute_secondary_signal(wave, cfg)
        if sec_signal is None:
            self.wave_debug["ext_secondary_skipped_invalid_geom"] = (
                self.wave_debug.get("ext_secondary_skipped_invalid_geom", 0) + 1
            )
            return
        self.wave_debug["ext_secondary_attempts"] = (
            self.wave_debug.get("ext_secondary_attempts", 0) + 1
        )

        wave_time = str(wave["wave_time"])
        if wave_time in self._ext_secondary_sent:
            return

        if not self._adx14_entries_allowed():
            self.wave_debug["ext_secondary_skipped_adx14_gate"] = (
                self.wave_debug.get("ext_secondary_skipped_adx14_gate", 0) + 1
            )
            return

        sig_key = get_signal_key(
            sec_signal, digits=self.signal_key_digits,
            entry_tag=ENTRY_TAG_EXT_SECONDARY,
        )
        if sig_key in self.sent_signals:
            return
        self.sent_signals.add(sig_key)

        ep = float(sec_signal["fib50"])
        sl = float(sec_signal["sl"])
        direction = int(sec_signal["dir"])
        close_price = float(bar["close"])

        ask = close_price + self.backtest_spread / 2
        bid = close_price - self.backtest_spread / 2

        if direction == 1 and ask <= sl:
            return
        if direction == -1 and bid >= sl:
            return

        if direction == 1:
            if ask > ep:
                lot = calc_lot_backtest(ep, sl, cfg)
                if lot <= 0.0:
                    return
                tp = compute_ext_secondary_take_profit(
                    cfg, ep, sl, is_buy=True,
                )
                self._add_pending_ext_secondary(
                    sec_signal, "BUY_LIMIT", ep, sl, tp, lot, bar_idx, bar_time,
                )
            else:
                self._handle_fallback_ext_secondary(
                    sec_signal, "BUY", ask, sl, bar_idx, bar_time,
                )
        else:
            if bid < ep:
                lot = calc_lot_backtest(ep, sl, cfg)
                if lot <= 0.0:
                    return
                tp = compute_ext_secondary_take_profit(
                    cfg, ep, sl, is_buy=False,
                )
                self._add_pending_ext_secondary(
                    sec_signal, "SELL_LIMIT", ep, sl, tp, lot, bar_idx, bar_time,
                )
            else:
                self._handle_fallback_ext_secondary(
                    sec_signal, "SELL", bid, sl, bar_idx, bar_time,
                )

        self._ext_secondary_sent.add(wave_time)

    def _add_pending_ext_secondary(self, signal: dict, order_type: str,
                                   ep: float, sl: float, tp: Optional[float], lot: float,
                                   bar_idx: int, bar_time: datetime) -> None:
        po = PendingOrder(
            signal, order_type, ep, sl, tp, lot, bar_idx, bar_time,
            entry_tag=ENTRY_TAG_EXT_SECONDARY, is_ext=True,
        )
        self.pending_orders.append(po)
        self._append_pending_vis("pending_created", bar_idx, bar_time, po)
        self.wave_debug["ext_secondary_placed"] = (
            self.wave_debug.get("ext_secondary_placed", 0) + 1
        )

    def _handle_fallback_ext_secondary(self, signal: dict, side: str,
                                       market_price: float, sl: float,
                                       bar_idx: int, bar_time: datetime) -> None:
        cfg = self.cfg
        ep = float(signal["fib50"])
        mode = _entry_mode_str(cfg)
        if mode == "no_fallback" or mode == "limit_fallback":
            return

        if mode == "market_fallback":
            is_buy = (side == "BUY")
            risk_span = abs(ep - float(sl))
            sl_eff = (market_price - risk_span) if is_buy else (market_price + risk_span)
            lot = calc_lot_backtest(market_price, sl_eff, cfg)
            if lot <= 0.0:
                return
            tp = compute_ext_secondary_take_profit(
                cfg, market_price, sl_eff, is_buy=is_buy,
            )
            actual_entry = market_price + self.backtest_slippage * (1 if is_buy else -1)
            dummy = PendingOrder(
                signal, f"{side}_MARKET", market_price, sl_eff, tp, lot,
                bar_idx, bar_time,
                entry_tag=ENTRY_TAG_EXT_SECONDARY, is_ext=True,
            )
            trade = OpenTrade(dummy, bar_idx, actual_entry, bar_time, "MARKET", sl_eff, tp)
            self.open_trades.append(trade)
            self.wave_debug["ext_secondary_placed"] = (
                self.wave_debug.get("ext_secondary_placed", 0) + 1
            )
            return

        if mode == "stop_fallback":
            lot = calc_lot_backtest(ep, sl, cfg)
            if lot <= 0.0:
                return
            tp = compute_ext_secondary_take_profit(
                cfg, ep, sl, is_buy=(side == "BUY"),
            )
            order_type = "BUY_STOP" if side == "BUY" else "SELL_STOP"
            self._add_pending_ext_secondary(
                signal, order_type, ep, sl, tp, lot, bar_idx, bar_time,
            )

    def _process_ext_counter_time(self, bar_idx: int, bar_time: datetime,
                                   open_: float) -> None:
        cfg = self.cfg
        counter_t = parse_ext_counter_time(getattr(cfg, "ext_counter_time", None))
        if counter_t is None:
            return
        if not bar_time_at_or_past_counter_time(bar_time, counter_t):
            return
        for wave in list(self._all_ext_waves):
            wt = str(wave["wave_time"])
            birth_bi = self.wave_birth_by_time.get(wt)
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
                counter_time_already_done=(wt in self._ext_counter_time_done),
                counter_bos_already_done=(wt in self._ext_counter_bos_done),
            ):
                continue
            if has_open_ext_counter_peer(self.open_trades, source="time"):
                self.wave_debug["ext_counter_skipped_peer_open"] = (
                    self.wave_debug.get("ext_counter_skipped_peer_open", 0) + 1
                )
                continue
            counter_dir = -int(wave["dir"])
            is_buy = (counter_dir == 1)
            mp = float(open_) + (self.backtest_spread / 2.0 if is_buy else -self.backtest_spread / 2.0)
            counter_sig = compute_counter_signal(wave, cfg, source="time", market_price=mp)
            if counter_sig is None:
                continue
            self._open_ext_counter_market(
                counter_sig, wave_time=wt, bar_idx=bar_idx, bar_time=bar_time,
                market_price=mp, source="time",
            )

    def _advance_ext_bos_state_with_wave(self, wave: dict, bar_idx: int) -> None:
        """Kazda potvrzena vlna po EXT muze EXT BOS aktivovat nebo zrusit."""
        if not self._ext_bos_state:
            return
        try:
            wdir = int(wave.get("dir", 0))
        except (TypeError, ValueError):
            return
        if wdir not in (1, -1):
            return
        for ext_wt, state in list(self._ext_bos_state.items()):
            if state == "cancelled":
                continue
            ext_bi = self.wave_birth_by_time.get(ext_wt)
            if ext_bi is None or bar_idx <= int(ext_bi):
                continue
            ext_w = self.waves_by_wave_time.get(ext_wt)
            if ext_w is None:
                continue
            try:
                ext_dir = int(ext_w.get("dir", 0))
            except (TypeError, ValueError):
                continue
            self._ext_bos_state[ext_wt] = advance_ext_bos_state(
                state, ext_dir=ext_dir, wave_dir=wdir,
            )

    def _process_ext_bos_on_bar(self, bar_idx: int, bar_time: datetime,
                                 close_: float) -> None:
        cfg = self.cfg
        for wave in list(self._all_ext_waves):
            wt = str(wave["wave_time"])
            if not ext_bos_allowed_at_bar(wave, bar_idx):
                continue
            if not bos_triggered_for_ext_close(wave, close_):
                continue

            if wt not in self._ext_bos_triggered:
                self._ext_bos_triggered.add(wt)
                self.wave_debug["ext_bos_triggered"] = (
                    self.wave_debug.get("ext_bos_triggered", 0) + 1
                )
                if bool(getattr(cfg, "ext_close_trend_positions_on_bos", False)):
                    self._close_ext_trend_positions(
                        ext_dir=int(wave["dir"]),
                        ext_wave_time=wt,
                        bar_idx=bar_idx,
                        bar_time=bar_time,
                        close_=close_,
                    )

            if not bool(getattr(cfg, "ext_counter_enabled", False)):
                continue

            if wt in self._ext_counter_time_done or wt in self._ext_counter_bos_done:
                continue

            bos_state = self._ext_bos_state.get(wt, "armed")
            if not ext_bos_market_entry_allowed(bos_state):
                self.wave_debug["ext_bos_skipped_cancelled_by_trend"] = (
                    self.wave_debug.get("ext_bos_skipped_cancelled_by_trend", 0) + 1
                )
                continue
            if has_open_ext_counter_peer(self.open_trades, source="bos"):
                self.wave_debug["ext_counter_skipped_peer_open"] = (
                    self.wave_debug.get("ext_counter_skipped_peer_open", 0) + 1
                )
                continue

            counter_dir = -int(wave["dir"])
            is_buy = (counter_dir == 1)
            mp = float(close_) + (self.backtest_spread / 2.0 if is_buy else -self.backtest_spread / 2.0)
            counter_sig = compute_counter_signal(
                wave, cfg, source="bos", market_price=mp,
            )
            if counter_sig is not None:
                self._open_ext_counter_market(
                    counter_sig, wave_time=wt, bar_idx=bar_idx,
                    bar_time=bar_time, market_price=mp, source="bos",
                )

    def _open_ext_counter_market(self, sig: dict, *, wave_time: str,
                                  bar_idx: int, bar_time: datetime,
                                  market_price: float, source: str) -> None:
        if has_open_ext_counter_peer(self.open_trades, source=source):
            self.wave_debug["ext_counter_skipped_peer_open"] = (
                self.wave_debug.get("ext_counter_skipped_peer_open", 0) + 1
            )
            return
        if not self._adx14_entries_allowed():
            self.wave_debug["ext_counter_skipped_adx14_gate"] = (
                self.wave_debug.get("ext_counter_skipped_adx14_gate", 0) + 1
            )
            return
        is_buy = (int(sig["dir"]) == 1)
        sl = float(sig["sl"])
        lot = calc_lot_backtest(market_price, sl, self.cfg)
        if lot <= 0.0:
            return
        actual_entry = market_price + self.backtest_slippage * (1 if is_buy else -1)
        side = "BUY" if is_buy else "SELL"
        order_type = f"{side}_MARKET"
        entry_tag = (
            "ext_counter_time" if source == "time" else "ext_counter_bos"
        )
        synth_signal = dict(sig)
        synth_signal["wave_time"] = wave_time
        tp = resolve_effective_tp(
            self.cfg, synth_signal, actual_entry, sl, is_buy=is_buy,
        )
        dummy = PendingOrder(
            synth_signal, order_type, market_price, sl,
            (None if tp is None else float(tp)), lot,
            bar_idx, bar_time,
            dir_override=int(sig["dir"]),
            is_counter=True,
            entry_tag=entry_tag,
            is_ext=True,
        )
        trade = OpenTrade(
            dummy, bar_idx, actual_entry, bar_time, "MARKET", sl,
            (None if tp is None else float(tp)),
        )
        self.open_trades.append(trade)
        if source == "time":
            self._ext_counter_time_done.add(wave_time)
            self.wave_debug["ext_counter_time_placed"] = (
                self.wave_debug.get("ext_counter_time_placed", 0) + 1
            )
        else:
            self._ext_counter_bos_done.add(wave_time)
            self.wave_debug["ext_counter_bos_placed"] = (
                self.wave_debug.get("ext_counter_bos_placed", 0) + 1
            )

    def _close_ext_trend_positions(
        self,
        ext_dir: int,
        ext_wave_time: str,
        bar_idx: int,
        bar_time: datetime,
        close_: float,
    ) -> None:
        """
        Pri EXT BOS 0,35 zavre ne-EXT pozice ve smeru EXT vlny (WAVE/PP/BOS…).
        
        EXT block pozice (E23_/ECT_/ECB_) otevrene z `ext_wave_time` se NEZAVIRAJI.
        NOVE: EXT block z JINE parent EXT se take NEZAVIRAJI, dokud jsou v okne 
        sve vlastni parent EXT vlny (= zadna dalsi wave po jejich parent jeste 
        nevznikla). Plne sjednoceno s is_trade_within_parent_ext_window.
        """
        from strategy.ext_logic import (
            is_ext_block_trade_from_wave,
            is_trade_within_parent_ext_window,
        )

        parent_ext = str(ext_wave_time)
        still_open: List[OpenTrade] = []
        closed = 0
        for trade in self.open_trades:
            # 1) Stejna parent EXT — beze zmeny chovani (chranena)
            if is_ext_block_trade_from_wave(trade, parent_ext):
                still_open.append(trade)
                continue
            
            # 2) NOVE: EXT block z jine parent EXT v okne sve parent vlny — chranena
            if is_trade_within_parent_ext_window(
                trade,
                wave_birth_by_time=self.wave_birth_by_time,
                bar_idx=bar_idx,
            ):
                # SL safety
                # (close_ je close baru, ale pro EXT_BOS_CLOSE se pouziva close_)
                # SL kontrola tady neni primarni — pokud SL bylo prurazeno, 
                # _handle_bos_exit_on_bar uz to mel chytit. Tady jen hold.
                still_open.append(trade)
                self.wave_debug["ext_protected_within_parent_window_extbos"] = (
                    self.wave_debug.get("ext_protected_within_parent_window_extbos", 0) + 1
                )
                continue
                
            # UZIVATELSKY POZADAVEK: Vsechny counter pozice (WAVE_COUNTER, EXT_COUNTER, TWO_SIDED_MIRROR)
            # musi prezit EXT_BOS_CLOSE a nesmi se zavrit.
            # Zaviraji se az na SL, TP nebo na dalsim BOS flipu proti nim (handled by _handle_bos_exit_on_bar).
            if is_wave_counter_trade(trade) or is_two_sided_mirror_trade(trade) or is_ext_counter_trade(trade):
                still_open.append(trade)
                continue
            
            # 3) wave_2_no_tp ochrana — beze zmeny
            if trade.wave_time in self._wave_2_no_tp_protected_waves:
                still_open.append(trade)
                continue
            
            # 4) EXT-1 ochrana — beze zmeny
            if self._ext1_close_blocked(bar_idx, "EXT_BOS_CLOSE", trade=trade):
                still_open.append(trade)
                continue
            
            # 5) Zavrit pozice ve smeru parent EXT (originalni logika)
            if trade.dir == ext_dir:
                ct = self._make_closed(
                    trade, bar_idx, close_, bar_time, "EXT_BOS_CLOSE",
                )
                self._append_closed_trade(ct, bar_time)
                closed += 1
            else:
                still_open.append(trade)
        
        self.open_trades = still_open
        if closed:
            self.wave_debug["ext_bos_trend_closed"] = (
                self.wave_debug.get("ext_bos_trend_closed", 0) + closed
            )

    def _close_remaining(self, last_bar: int, df: pd.DataFrame):
        bar = df.iloc[last_bar]
        close_price = float(bar["close"])
        bar_time = pd.Timestamp(bar["time"]).to_pydatetime()
        for trade in self.open_trades:
            ct = self._make_closed(trade, last_bar, close_price, bar_time, "END_OF_DATA")
            self._append_closed_trade(ct, bar_time)
        self.open_trades.clear()

    def _open_positions_from_ext1(self) -> List[OpenTrade]:
        """Otevrene pozice z EXT1 vlny (primary EXT nebo EXT block z parent EXT1)."""
        ext1_times = getattr(self, "_ext1_wave_times", None) or set()
        if not ext1_times:
            return []
        out: List[OpenTrade] = []
        for trade in self.open_trades:
            wt = str(getattr(trade, "wave_time", "") or "")
            if wt in ext1_times:
                out.append(trade)
                continue
            for parent_wt in ext1_times:
                if is_ext_block_trade_from_wave(trade, parent_wt):
                    out.append(trade)
                    break
        return out

    def _rrr_target_price(self, trade: OpenTrade) -> float:
        """RRR TP uroven z limit TP nebo z entry/SL/rrr (RRR_FIXED)."""
        if trade.tp is not None:
            return float(trade.tp)
        dist = abs(float(trade.actual_entry) - float(trade.sl))
        rrr = float(getattr(self.cfg, "rrr", 0.0) or 0.0)
        if trade.dir == 1:
            return float(trade.actual_entry) + rrr * dist
        return float(trade.actual_entry) - rrr * dist

    def _close_position_market(
        self,
        trade: OpenTrade,
        *,
        reason: str,
        price: float,
        bar_idx: int,
        bar_time: datetime,
    ) -> None:
        ct = self._make_closed(trade, bar_idx, price, bar_time, reason)
        self._append_closed_trade(ct, bar_time)
        self.open_trades = [t for t in self.open_trades if t is not trade]

    def _log(self, event: str, **fields: Any) -> None:
        import logging

        logging.getLogger(__name__).info("%s | %s", event, fields)
        key = event.lower()
        self.wave_debug[key] = self.wave_debug.get(key, 0) + 1

    def _maybe_rrr_fixed_better_exit_after_ext1_protect_end(
        self,
        bar_idx: int,
        bar_time: datetime,
        current_close: float,
    ) -> None:
        if self._tp_mode != TPMode.RRR_FIXED:
            return
        if not _get_ext1_protect_flag(self.cfg):
            return
        bars = getattr(self, "_ext1_protection_per_bar", [])
        if bar_idx <= 0 or bar_idx >= len(bars):
            return
        if not (bars[bar_idx - 1] and not bars[bar_idx]):
            return
        for pos in list(self._open_positions_from_ext1()):
            rrr_target = self._rrr_target_price(pos)
            if (pos.dir == 1 and current_close > rrr_target) or (
                pos.dir == -1 and current_close < rrr_target
            ):
                self._close_position_market(
                    pos,
                    reason="rrr_fixed_better_after_ext1_protect",
                    price=current_close,
                    bar_idx=bar_idx,
                    bar_time=bar_time,
                )
                self._log(
                    "EXT1_PROTECT_END_BETTER_RRR_TP",
                    bar=bar_idx,
                    position=str(getattr(pos, "wave_time", "")),
                    original_rrr_target=rrr_target,
                    market_exit_price=current_close,
                )

    def _ext1_protection_active_on_bar(self, bar_idx: int) -> bool:
        """True pokud na baru plati EXT-1 ochrana (config: ext1_protect_positions_until_wave2)."""
        if not _get_ext1_protect_flag(self.cfg):
            return False
        win = getattr(self, "_ext1_protection_per_bar", None)
        if not win:
            return False
        if bar_idx < 0 or bar_idx >= len(win):
            return False
        return bool(win[bar_idx])

    def _ext1_close_blocked(
        self,
        bar_idx: int,
        reason: str,
        *,
        trade: Optional[OpenTrade] = None,
    ) -> bool:
        """
        True pokud se pozice na baru `bar_idx` NESMI zavrit z duvodu `reason`.
        Sdilena logika s live: `ext1_close_blocked_on_bar`.
        """
        from strategy.wave_sequence import ext1_close_blocked_on_bar

        per_bar = getattr(self, "_ext1_protection_per_bar", None) or []
        
        flipped, direction, prev_dir = self._bos_flip_state_on_bar(bar_idx)
        main_trend_dir = 1 if direction == "bull" else -1 if direction == "bear" else 0
        
        return ext1_close_blocked_on_bar(
            bar_idx, per_bar, self.cfg, reason, trade=trade, main_trend_dir=main_trend_dir
        )

    def _make_closed(self, trade: OpenTrade, bar_idx: int, close_price: float,
                     close_time: datetime, reason: str) -> ClosedTrade:
        ct = ClosedTrade(trade, bar_idx, close_price, close_time, reason)
        if trade.dir == 1:
            ct.pnl_usd = (close_price - trade.actual_entry) * trade.lot * self.cfg.contract_size
        else:
            ct.pnl_usd = (trade.actual_entry - close_price) * trade.lot * self.cfg.contract_size
        return ct

    @staticmethod
    def _infer_signal_key_digits(df: pd.DataFrame) -> int:
        """
        Odhadne precision pro signal key z close cen v CSV.
        Tím se backtest lepe priblizi live, kde se pouziva symbol_info().digits.
        """
        if "close" not in df.columns or df.empty:
            return 4
        max_digits = 0
        sample = df["close"].dropna().head(500)
        for value in sample:
            text = f"{float(value):.10f}".rstrip("0").rstrip(".")
            if "." in text:
                max_digits = max(max_digits, len(text.split(".", 1)[1]))
        if max_digits <= 0:
            return 4
        return min(8, max_digits)