# Konfigurace bota
from dataclasses import dataclass, field

try:
    import MetaTrader5 as mt5
    _HAS_MT5 = True
except ImportError:
    # MT5 neni potreba pro backtest - ten pracuje z CSV
    class _MT5Stub:
        TIMEFRAME_M1  = 1
        TIMEFRAME_M3  = 3
        TIMEFRAME_M5  = 5
        TIMEFRAME_M15 = 15
        TIMEFRAME_M30 = 30
        TIMEFRAME_H1  = 16385 #správné označení v bin kodu pro odlišení hodin od minut
        TIMEFRAME_H4  = 16388
        TIMEFRAME_D1  = 16408
        TIMEFRAME_W1  = 32769
    mt5 = _MT5Stub()
    _HAS_MT5 = False

from config.enums import (
    EntryMode,
    TPMode,
    TpWaveEarlyMode,
    TpWaveExitOn,
    TpWaveIntrabarPriority,
)


# Mapovani timeframe stringu na MT5 int konstanty.
# Pouziva se v gridu (kde grid pisemne definuje "M15") i v live botu.
TIMEFRAME_MAP: dict = {
    "M1":  mt5.TIMEFRAME_M1,
    "M3":  mt5.TIMEFRAME_M3,
    "M5":  mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1":  mt5.TIMEFRAME_H1,
    "H4":  mt5.TIMEFRAME_H4,
    "D1":  mt5.TIMEFRAME_D1,
    "W1":  mt5.TIMEFRAME_W1,
}

# Reverzni mapa pro logging (int -> string)
TIMEFRAME_LABEL_MAP: dict = {v: k for k, v in TIMEFRAME_MAP.items()}

# Režim pro abort_fib_level (str) — viz BotConfig.abort_fib_level a POPIS_NASTAVENI_BOTA_A_BACKTESTERU.txt
# Hluboký retracement za „fib_abort“: místo přeskočení vstup (market/stop fallback) s posunutým SL.
# V gridu lze zadat "deep_retrace_shift_sl" nebo alias "shift_sl".
ABORT_FIB_SHIFT_SL = "deep_retrace_shift_sl"

# ───── CONFIGURACE ──────────────────────────
# Při přidání nového pole do BotConfig aktualizujte i HTML souhrn v backtestu:
#   backtest/bot_config_summary_html.py (scroll export plots_scroll_combined).
# Slovní popis parametrů pro uživatele: POPIS_NASTAVENI_BOTA_A_BACKTESTERU.txt (kořen projektu).
@dataclass
class BotConfig:
    # ============== MARKET SETTING ==============
    symbol:          str   = "EU50p"
    timeframe:       int   = mt5.TIMEFRAME_M30
    wave_min_pct:    float = 0.55
    # Backtest-only: kauzální brány (retro po birth, clamp EP/SL) — parita s live; grid default False
    causal_mode: bool = False
    # Backtest-only: po BT spustit live E2E parity (replay + fake MT5); ne pro grid
    run_e2e_parity: bool = False

    # ============== TP SETTINGS ==============
    rrr:             float = 2.0
    tp_mode: TPMode = TPMode.RRR_FIXED
    tp_target_wave_index: int = 4
    wave_extension_pct: float = 0.20
    bos_entry_in_rrr_fixed: bool = False
    wave_2_no_tp_enable: bool = False
    wave_2_no_tp_max_index: int = 2

    # ============== MARKET OPTIMALISATION ==============
    # --- STRATEGY (TREND-FOLLOW LIMIT) ---
    # entry_fib_level / sl_fib_level / abort_fib_level — viz POPIS_NASTAVENI_BOTA_A_BACKTESTERU.txt
    min_opp_bars:    int   = 2
    entry_fib_level: float = 0.5  # Hodnota musi byt v (0, 1).
    sl_fib_level:    float = 0.8  # Musi byt > entry_fib_level a <= 1.0.
    abort_fib_level: float | str | None = None  # None | float pasiónka | ABORT_FIB_SHIFT_SL (= deep_retrace_shift_sl)
    wave_plus: bool = True  # WAVE + zapnuto — čas + extrém + přepočet Fiba; False = vypnuto
    entry_mode:      EntryMode = EntryMode.MARKET_FALLBACK
    order_expiry_days:   int = 14     # pending order will cancel after N days
    ext_order_expiry_days: int = 7
    pending_cancel_mode: str = "number"
    pending_cancel_after_days: int = 14
    wave_max_pct:    float | None = None  #Jinak číslo maximální velikosti vlny v %:  wave_max_pct:    float | None = 3
    max_wave_age_hours:  int = 8      # maximalni stari vlny pro nove live signaly

    # ============== RISK MANAGEMENT ==============
    risk_usd: float = 500.0
    pp_risk_usd: float = 500.0
    contract_size: float = 1.0  # Default 100_000 = forex; Indexy/komodity/krpyto) = 1 Pouze pro backtester only; live bot bere data z MT5
    magic:         int   = 1  # Musi byt originalni oproti jinym botum; 2XX_XXX je pro XAUUSD
    adx14_change_enabled: bool = False
    adx14_equity_gate_enabled: bool = False
    pnl_base_tracker_enabled: bool = True

    # ============== WAVE & PP ==============
    wave_min_sl: float = 0.12
    wave_position_enabled: bool = True  # klasické vlny zapnuté/vypnuté
    wave_positions_only: bool = False  # jen klasické WAVE; ostatní moduly vynuceně off (viz position_modes)
    wave_counter_two_sided_enabled: bool = False  # master: WAVE_COUNTER + WAVE_TWO_SIDED
    wave_isolation_study: bool = False  # grid metadata; live MT5 slice viz live_mt5_wave_slice_only
    live_mt5_wave_slice_only: bool = False  # runtime: MT5 smi otevírat jen WAVE (apply_live_mt5_wave_slice_execution)
    two_sided_entry_enabled: bool = False
    two_sided_entry_min_wave_pct: float = 0.55
    live_study_two_sided_mirror_orders: bool = False
    live_study_promoted_two_sided_as_wave: bool = True
    skip_primary_entry_on_parent_wave_enable: bool = True  # preskocit primarni WAVE vstup na two-sided rodici A; jen protipozice na protivlni B
    wf_enabled: bool = True  # Wick Fakeout Recovery
    pp_enabled: bool = False
    pp_sl_pct: float = 0.21
    pp_disabled_in_ext_context: bool = True  # True = neotevírat PP z EXT / in_ext_range vln

    # ============== TREND FILTER & BOS ==============
    trend_filter_enabled: bool = False
    trend_hh_hl_filter_enabled: bool = False
    counter_position_enabled: bool = False
    bos_entry_enable: bool = False
    bos_reentry_enabled: bool = False
    wave_size_sl_ladder_base_pct: float = 0.21
    wave_size_sl_ladder_step_pct: float = 0.11
    wave_size_sl_ladder_band_size_pct: float = 0.50

    # ============== EXT ==============
    ext_enabled: bool = False
    ext_wave_min_pct: float = 1.0
    ext_secondary_enabled: bool = False
    ext_weekend_gap_relax_factor: float = 0.0
    # Master: EXT counter market (TIME + BOS trigger); ext_counter_time / min SL parametry níže.
    ext_counter_enabled: bool = False
    ext_counter_time: str = "21:00"
    ext_counter_min_sl_enabled: bool = True
    ext_counter_min_sl_pct: float = 0.16
    ext_trade_both_sides_in_range: bool = False
    ext_range_protect_pendings_from_bos_cancel: bool = True  # W-pending in_ext_range nezrusit BOS broken_dir cancel
    wave_min_pct_enable: bool = False
    ext_post_both_sides_wave_min_pct: float = 0.13
    ext_post_both_sides_default_sl_pct: float = 0.10
    ext_close_trend_positions_on_bos: bool = False

    # ============== WAVE FILTERING & PROPFIRMS ==============
    wave_session_filter_enabled: bool = False
    wave_allowed_sessions: list = field(default_factory=lambda: ["LONDON", "USA"])
    wave_custom_window: tuple = None

    # ============== OTHERS ==============
    sleep_sec:       int   = 5    # Kontrola bota s MT5 v reálném čase (cislo = sekundy)
    lot_step: float = 0.01
    min_lot: float = 0.01
    max_lot: float = 100.0
    dynamic_risk_enabled: bool = False
    risk_pct_of_equity: float = 0.5
    bot_name:      str   = "TESTING"
    startup_bars:        int = 1440   # kolik baru nacist z MT5 (~1 mesic na M30: 30*48)
    retry_market_attempts: int = 2
    retry_pending_attempts: int = 3
    retry_backoff_sec: float = 0.35   # time (cislo = sekundy)
    equity_target_usd: float | None = None  # po předepsaném targetu PNL bot zavře všechny pozice a vypne se.
    adx14_change_normalizer_json: str = "runtime/adx14_normalizer.json"
    adx14_change_html_path: str = "results/live_adx14_change.html"
    adx14_change_threshold: float = 1.3  # viz poznámka výše; gate = adx14_disable_threshold
    adx14_history_bars: int = 5000  # kolik barů načíst pro výpočet denního ADX14 (live loop)
    adx14_disable_threshold: float = 1.3  # vypnutí nových vstupů při adx14_signal >= tato hodnota
    adx14_auto_restart_calendar_months: int = 2
    adx14_bos_confirm_enabled: bool = True  # False = vypnout restart přes potvrzený nový high PnL základní
    adx14_bos_confirm_calendar_weeks: int = 2  # platí jen při adx14_bos_confirm_enabled=True
    adx14_gate_state_path: str = "runtime/adx14_equity_gate_state.json"
    adx14_gate_jsonl_path: str = "runtime/adx14_equity_gate.jsonl"
    pnl_base_tracker_risk_usd: float = 500.0
    pnl_base_tracker_state_path: str = "runtime/pnl_base_tracker_state.json"
    pnl_base_tracker_jsonl_path: str = "runtime/pnl_base_tracker.jsonl"
    pnl_base_tracker_csv_path: str = "runtime/pnl_base_curve.csv"
    live_position_cap_mode: str = "off"
    live_max_open_positions: int | None = None # Cislo za druhe "none" definuje maximalni pocet otevrenych pozic napr. [4] nebo [6]
    status_log_text_hours: float = 1 
    status_log_jsonl_hours: float = 10 / 60  # 10 min (JSONL)
    old_waves_log_text_hours: float = 8.0   # konzole + live_bot.log
    old_waves_log_jsonl_hours: float = 1.0  # jen JSONL
    heartbeat_interval_sec: int = 180
    jsonl_retention_days: int = 2
    ext_post_confirmed_trend_count: int = 2
    ext_post_confirmed_trend_lock_enabled: bool = True
    ext_post_confirmed_trend_lock_blocks_both_sides: bool = True
    tp_wave_early_mode: TpWaveEarlyMode = TpWaveEarlyMode.OFF
    tp_wave_exit_on: TpWaveExitOn = TpWaveExitOn.BIRTH
    tp_wave_early_fallback_birth: bool = True
    tp_wave_intrabar_priority: TpWaveIntrabarPriority = TpWaveIntrabarPriority.TP_BEFORE_SL
    two_sided_entry_min_sl_move_pct: float = 0.16
    two_sided_entry_bypass_trend_filter: bool = True
    ext_primary_fib_level: float = 0.5
    ext_secondary_fib_level: float = 0.236
    ext_secondary_sl_fib_level: float = 0.4
    ext_min_sl_move_pct: float = 0.16
    ext_counter_sl_pct: float = 0.21  # legacy alias; prefer ext_counter_min_sl_pct
    ext_bos_fib_level: float = 0.35
    ext_range_wave_min_pct: float | None = None  # None → 50 % z wave_min_pct (min 0.08)
    ext_range_confirm_waves: int = 2  # pocet po sobe jdoucich strukturalnich vln noveho smeru
    ext1_protect_positions_until_wave2: bool = True
    session_enabled: bool = True
    session_timezone: str = "broker"  # "broker" | "UTC+3" / "GMT+3" | IANA zona pro session on/off
    session_open_time: str = "01:05"
    session_close_time: str = "23:45"
    session_pre_close_buffer_min: int = 5 # minut pred session_close_time se zrusi pendingy (kazdy den)
    session_weekdays_only: bool = True
    session_week_close_weekday: int = 4 # 0=Po .. 6=Ne (typicky 4=Pá po close_time)
    session_week_close_time: str = "23:45"
    session_week_open_weekday: int = 6 # 0=Po .. 6=Ne (typicky 0=Ne po close_time)
    session_week_open_time: str = "01:05"
    session_close_positions_on_friday: bool = False

    def __post_init__(self) -> None:
        """Sjednoti novy a starsi alias pro BOS entry prepinac."""
        final = bool(self.bos_entry_enable) or bool(self.bos_reentry_enabled)
        self.bos_entry_enable = final
        self.bos_reentry_enabled = final
        from strategy.wave_target_n_mode import normalize_wave_target_n_cfg
        normalize_wave_target_n_cfg(self)

    @property
    def timeframe_label(self) -> str:
        """M1/M15/H1… odvozeno z `timeframe` (MT5 int) přes TIMEFRAME_LABEL_MAP."""
        return TIMEFRAME_LABEL_MAP.get(self.timeframe, f"TF_{int(self.timeframe)}")


def trade_risk_usd(cfg: BotConfig, *, is_pp: bool = False) -> float:
    """Per-trade risk for PnL scaling (WAVE/counter vs PP)."""
    return float(cfg.pp_risk_usd if is_pp else cfg.risk_usd)


# ---------------------------------------------------------------------------
# LIVE / REGISTRY — presety BotConfig (TREND & BOS)
# ---------------------------------------------------------------------------
# U kazdeho volani BotConfig(...) nize jsou explicitne uvedeny klice trend/BOS/TP,
# aby bylo z kodu jasne, co preset dela (obdoba bloku TREND & BOS v grid profilech).
# Plny popis vyznamu: v ramci tridy BotConfig sekce "TREND & BOS" + config/enums.TPMode.
#
# LIVE_BOT_CONFIG — produkcni / defaultni live preset:
#
#   tp_mode — dve moznosti (EXAMPLE grid vetev wave_target_n):
#     TPMode.WAVE_TARGET_N    — legacy: exit TP_WAVE_N na birth W(N), counter LIMIT pri narozeni TP-vlny
#     TPMode.WAVE_TARGET_N_G  — G: extension hit @ armed_tp, fallback birth W(N), G counter MARKET (default)
#
#   Spolecne: tp_target_wave_index=4, wave_extension_pct=0.10, counter ON, pending_cancel_mode=trend.
#   G preset (forming_qualified + extension_hit) se nastavi automaticky v __post_init__.
#
# Tento config ctete `main.py` pri startu live bota.
# Prepnuti: zmente tp_mode nize, nebo python -m main --config <NAZEV>

# LIVE_BOT_CONFIG — grid combo_no 50 (EURUSD xlsx 2024-06-10 .. 2025-07-10, w2notpFalse).
# Runtime engine: resolve_grid_engine_config() — plna simulace (counter/EXT ordery).
# wave_isolation_study=True (varianta B): engine plny routing, MT5 WAVE + TS2_ mirror
# (live_study_two_sided_mirror_orders); wave_pnl = equity slice.

LIVE_BOT_CONFIG = BotConfig(
    # ============== MARKET SETTING ==============
    symbol="EURUSD",  #L FTMO/broker symbol (".x"/".r" jsou jen jine nazvy stejneho symbolu)
    timeframe=mt5.TIMEFRAME_M30,
    wave_min_pct=0.2,
    causal_mode=False,  #L True = backtest bez look-ahead (parita live); False = legacy grid
    run_e2e_parity=False,  #L True = po BT spustit E2E parity (live_match / --e2e)
    wave_session_filter_enabled=False,

    # ============== TP SETTINGS ==============
    rrr=2.5,
    tp_mode=TPMode.WAVE_TARGET_N,
    tp_target_wave_index=2,
    wave_extension_pct=0.10,
    bos_entry_in_rrr_fixed=False,
    wave_2_no_tp_enable=False,
    wave_2_no_tp_max_index=2,

    # ============== MARKET OPTIMALISATION ==============
    min_opp_bars=3,
    entry_fib_level=0.55,
    sl_fib_level=0.8,
    abort_fib_level=ABORT_FIB_SHIFT_SL,
    wave_plus=True,
    entry_mode=EntryMode.MARKET_FALLBACK,
    order_expiry_days=3,
    ext_order_expiry_days=7,
    pending_cancel_mode="trend",
    pending_cancel_after_days=7,
    wave_max_pct=1.0,
    max_wave_age_hours=20,

    # ============== RISK MANAGEMENT ==============
    risk_usd=800.0,
    pp_risk_usd=800.0,
    contract_size=100_000.0,  #L backtest; live lot z MT5
    magic=100_200,

    # ============== WAVE & PP ==============
    wave_min_sl=0.12,
    wave_position_enabled=True,
    wave_positions_only=True,
    wave_isolation_study=True,
    wave_counter_two_sided_enabled=True,
    two_sided_entry_enabled=True,
    two_sided_entry_min_wave_pct=0.55,
    live_study_two_sided_mirror_orders=True,  #L study B: posilat TS2_ mirror na MT5 (guard + wave_counter_two_sided_orders)
    live_study_promoted_two_sided_as_wave=True,
    skip_primary_entry_on_parent_wave_enable=True,
    wf_enabled=True,
    pp_enabled=False,
    pp_sl_pct=0.21,
    pp_disabled_in_ext_context=True,

    # ============== TREND FILTER & BOS ==============
    trend_filter_enabled=True,
    trend_hh_hl_filter_enabled=True,
    counter_position_enabled=True,
    bos_entry_enable=False,
    wave_size_sl_ladder_base_pct=0.21,
    wave_size_sl_ladder_step_pct=0.16,
    wave_size_sl_ladder_band_size_pct=0.50,

    # ============== EXT (combo 50 — detekce/kontext; MT5 ordery jen WAVE) ==============
    ext_enabled=True,
    ext_secondary_enabled=False,
    ext_wave_min_pct=0.76,
    ext_weekend_gap_relax_factor=0.76,
    ext_counter_enabled=False,
    ext_counter_time="23:00",
    ext_counter_min_sl_enabled=True,
    ext_counter_min_sl_pct=0.16,
    ext_trade_both_sides_in_range=True,
    ext_range_protect_pendings_from_bos_cancel=True,  #L
    wave_min_pct_enable=False,
    ext_post_both_sides_wave_min_pct=0.13,
    ext_post_both_sides_default_sl_pct=0.10,
    ext_close_trend_positions_on_bos=True,

    # ============== OTHERS ==============
    bot_name="3_EURUSD_100k_n=2_FTMO",  #L telemetry BOT_ID == bot_name
    heartbeat_interval_sec=180,  #L HEARTBEAT do jsonl kazde 3 min
    jsonl_retention_days=2,  #L jsonl: smazat radky starsi nez 2 dny (i sync do gitu)
    startup_bars=1440,  #L ~1 mesic zpetne z MT5 (M30: 30*48)
    dynamic_risk_enabled=False,  #L
    risk_pct_of_equity=0.5,  #L
    live_position_cap_mode="off",  #L
    live_max_open_positions=None,  #L
    equity_target_usd=52_020.0,  #L profit target — zavře pozice a vypne bota

    # ============== TIME RESET ==============
    session_enabled=True,  #L vypnout/zapnout bot podle casu (session_manager + live_loop)
    session_timezone="UTC+3",  #L session on/off v GMT+3; strategie dale broker/MT5
    session_open_time="01:05",  #L GMT+3 — zacatek denni session
    session_close_time="23:45",  #L GMT+3 — konec denni session
    session_pre_close_buffer_min=5,  #L min pred close: snapshot + cancel_all_pendings (23:40)
    session_weekdays_only=True,  #L tydenni pauza mezi week_close a week_open
    session_week_close_weekday=4,  #L 0=Po .. 6=Ne (4=Pá)
    session_week_close_time="23:45",  #L GMT+3
    session_week_open_weekday=6,  #L 0=Po .. 6=Ne (6=Ne)
    session_week_open_time="01:05",  #L GMT+3
    session_close_positions_on_friday=False,  #L pred tydennim close zavrit i pozice

    # ============== ADX 14  & PNL ==============
    adx14_change_enabled=False,  #L ADX14 normalizer + HTML report (runtime/adx14_live.py)
    adx14_equity_gate_enabled=False,  #L vypnout nove vstupy pri adx14_signal >= threshold
    pnl_base_tracker_enabled=False,  #L krivka PnL zakladni; restart gate pres BOS confirm
    adx14_change_normalizer_json="runtime/adx14_normalizer.json",  #L
    adx14_change_html_path="results/live_adx14_change.html",  #L
    adx14_change_threshold=1.3,  #L legacy alias; gate pouziva adx14_disable_threshold
    adx14_history_bars=5000,  #L bary pro vypocet denniho ADX14
    adx14_disable_threshold=1.3,  #L vypnuti novych vstupu pri adx14_signal >= tato hodnota
    adx14_auto_restart_calendar_months=2,  #L auto restart gate po N kalendarnich mesicich
    adx14_bos_confirm_enabled=True,  #L restart gate po potvrzenem novem high PnL zakladni
    adx14_bos_confirm_calendar_weeks=2,  #L platí jen pri adx14_bos_confirm_enabled=True
    adx14_gate_state_path="runtime/adx14_equity_gate_state.json",  #L
    adx14_gate_jsonl_path="runtime/adx14_equity_gate.jsonl",  #L
    pnl_base_tracker_risk_usd=300.0,  #L
    pnl_base_tracker_state_path="runtime/pnl_base_tracker_state.json",  #L
    pnl_base_tracker_jsonl_path="runtime/pnl_base_tracker.jsonl",  #L
    pnl_base_tracker_csv_path="runtime/pnl_base_curve.csv",  #L
)

CONFIG_REGISTRY: dict = {
    "LIVE_BOT_CONFIG": LIVE_BOT_CONFIG,
}

# Zpetna kompatibilita

DEFAULT_CONFIG = LIVE_BOT_CONFIG

# ------ Live bot (pracovní adresář: Denik/) ------
# cd "C:\Users\a2010\Desktop\TRADING\Trading bot\BOT_EDIT\IMPLEMENTACE\Denik"
# python main.py

# ------ Backtest s LIVE configem ------
# z IMPLEMENTACE:
#   cd "C:\Users\a2010\Desktop\TRADING\Trading bot\BOT_EDIT\IMPLEMENTACE"
#   python -m backtest.run_backtest --profile live_match --date-from 2025-11-10 --date-to 2026-05-09
# z Denik (doporučeno):
#   cd "C:\Users\a2010\Desktop\TRADING\Trading bot\BOT_EDIT\IMPLEMENTACE\Denik"
#   python -m backtest.run_backtest --profile live_match --date-from 2025-11-10 --date-to 2026-05-09
# výstup: Denik/results/EURUSD/grid_LIVE_BOT_M30_{date_from}_{date_to}_001/
#         grid_report.xlsx + CSV + *_trades.xlsx


def abort_fib_shift_sl_mode(cfg: BotConfig) -> bool:
    """
    True, pokud je `cfg.abort_fib_level` řetězcový režim posunu SL (deep_retrace_shift_sl / aliasy).

    V tomto režimu kontrola fib_abort vlna nezahodí — pokračuje se do fallbacku s upravenou geometrií
    SL u market vstupu (viz engine / orders).
    """
    a = cfg.abort_fib_level
    if a is None or isinstance(a, bool):
        return False
    if isinstance(a, (int, float)):
        return False
    s = str(a).strip().lower().replace("-", "_")
    return s in (ABORT_FIB_SHIFT_SL, "deep_retrace_shift_sl", "shift_sl")


def abort_fib_trigger_ratio(cfg: BotConfig) -> float | None:
    """
    Poměr retracementu (0…1) pro výpočet ceny fib_abort ve vlně (_append_wave_sig).

    None … `abort_fib_level is None` (pasionka vypnutá).
    Číslo … přímé použití float(cfg.abort_fib_level), musí ležet mezi entry a SL fib.
    Řetězcový režim (shift SL) … 2/3 mezi entry_fib_level a sl_fib_level
    (výchozí 0.5 + 0.8 → ~0.7), aby hloubka odpovídala běžnému číselnému nastavení.
    """
    if cfg.abort_fib_level is None:
        return None
    fib_lvl = float(cfg.entry_fib_level)
    sl_lvl = float(cfg.sl_fib_level)
    if abort_fib_shift_sl_mode(cfg):
        return fib_lvl + (sl_lvl - fib_lvl) * (2.0 / 3.0)
    return float(cfg.abort_fib_level)


def parse_abort_fib_level_grid(value) -> float | str:
    """
    Převod hodnoty z gridu na BotConfig.abort_fib_level.

    • číslo → float (pasionka; musí být mezi fib a SL při kombinaci s daným gridem),
    • řetězec deep_retrace_shift_sl / shift_sl / aliasy → kanonicky ABORT_FIB_SHIFT_SL,
    • jiný řetězec → ValueError (překlep).
    """
    if isinstance(value, str):
        s = value.strip()
        if not s:
            raise ValueError("abort_fib_level: prázdný řetězec")
        low = s.lower().replace("-", "_")
        if low in (ABORT_FIB_SHIFT_SL, "deep_retrace_shift_sl", "shift_sl"):
            return ABORT_FIB_SHIFT_SL
        raise ValueError(
            f"abort_fib_level: neznámý řetězec {value!r}. "
            f"Číslo mezi fib a SL, nebo {ABORT_FIB_SHIFT_SL!r} (alias shift_sl)."
        )
    return float(value)