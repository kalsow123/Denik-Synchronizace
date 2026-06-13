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
    wave_isolation_study: bool = False  # grid metadata; engine flag nepouzivat (viz translator)
    two_sided_entry_enabled: bool = False
    two_sided_entry_min_wave_pct: float = 0.55
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
    startup_bars:        int = 1500   # kolik baru nacist pri startu pro pine-style recovery
    retry_market_attempts: int = 2
    retry_pending_attempts: int = 3
    retry_backoff_sec: float = 0.35   # time (cislo = sekundy)
    equity_target_usd: float | None = None
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
    session_open_time: str = "23:05"
    session_close_time: str = "21:45"
    session_pre_close_buffer_min: int = 5 # minut pred session_close_time se zrusi pendingy (kazdy den)
    session_weekdays_only: bool = True
    session_week_close_weekday: int = 4 # 0=Po .. 6=Ne (typicky 4=Pá po close_time)
    session_week_close_time: str = "21:45"
    session_week_open_weekday: int = 6 # 0=Po .. 6=Ne (typicky 0=Ne po close_time)
    session_week_open_time: str = "23:05"
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

LIVE_BOT_CONFIG = BotConfig(
    # ============== MARKET SETTING ==============
    symbol="EURUSD.x",
    timeframe=mt5.TIMEFRAME_M30,
    wave_min_pct=0.26,

    # ============== TP SETTINGS ==============
    rrr=2.0,
    tp_mode=TPMode.WAVE_TARGET_N_G,
    tp_target_wave_index=4,
    wave_extension_pct=0.10,
    bos_entry_in_rrr_fixed=False,

    # ============== MARKET OPTIMALISATION ==============
    min_opp_bars=3,
    entry_fib_level=0.5,
    sl_fib_level=0.8,
    abort_fib_level=ABORT_FIB_SHIFT_SL,
    wave_plus=True,
    entry_mode=EntryMode.MARKET_FALLBACK,
    order_expiry_days=3,
    ext_order_expiry_days=7,
    pending_cancel_mode="trend",
    pending_cancel_after_days=7,
    wave_max_pct=1.0,
    max_wave_age_hours=12,

    # ============== RISK MANAGEMENT ==============
    risk_usd=500.0,
    pp_risk_usd=500.0,
    contract_size=100_000.0,
    magic=100_001,

    # ============== WAVE & PP ==============
    wave_min_sl=0.12,
    wave_position_enabled=True,
    wave_counter_two_sided_enabled=True,
    two_sided_entry_enabled=True,
    two_sided_entry_min_wave_pct=0.55,
    skip_primary_entry_on_parent_wave_enable=True,  # preskocit primarni WAVE vstup na two-sided rodici A; jen protipozice na protivlni B
    wf_enabled=True,
    pp_enabled=True,
    pp_sl_pct=0.21,
    pp_disabled_in_ext_context=True,

    # ============== TREND FILTER & BOS ==============
    trend_filter_enabled=True,
    trend_hh_hl_filter_enabled=True,
    counter_position_enabled=True,
    bos_entry_enable=True,
    wave_size_sl_ladder_base_pct=0.21,
    wave_size_sl_ladder_step_pct=0.16,
    wave_size_sl_ladder_band_size_pct=0.50,

    # ============== EXT ==============
    ext_enabled=True,
    ext_secondary_enabled=False,
    ext_wave_min_pct=0.76,
    ext_weekend_gap_relax_factor=0.5,
    ext_counter_enabled=True,
    ext_counter_time="23:00",
    ext_counter_min_sl_enabled=True,
    ext_counter_min_sl_pct=0.16,
    ext_trade_both_sides_in_range=True,
    ext_range_protect_pendings_from_bos_cancel=True,
    ext_close_trend_positions_on_bos=True,

    # ============== OTHERS ==============
    bot_name="LIVE_EURUSD_M30_v1",
    dynamic_risk_enabled=False,
    risk_pct_of_equity=0.5,  # 0.5% z equity na 1 obchod
    live_position_cap_mode="off",
    live_max_open_positions=None,
    equity_target_usd=None,
)

# Priklad experimentalniho configu pro testovani.
# Muzete pridat libovolne dalsi a registrovat je v CONFIG_REGISTRY nize.
#
# EXAMPLE_EURUSD_M15 — experimentalni preset (symbol/EU50P apod.):
#   stejne jako LIVE: trend/BOS filtry vypnuty, RRR_FIXED; upravte zde nebo v gridu
#   pro test bos_exit / wave_extension_pct / trend_filter_enabled=True.
EXAMPLE_EURUSD_M15 = BotConfig(
    bot_name="EXAMPLE_EURUSD_M15_v1",
    magic=100_001,
    symbol="EURUSD.x",
    timeframe=mt5.TIMEFRAME_M15,
    wave_min_pct=0.20,
    min_opp_bars=4,
    rrr=1.5,
    entry_fib_level=0.618,
    sl_fib_level=0.85,
    entry_mode=EntryMode.MARKET_FALLBACK,
    risk_usd=500.0,
    contract_size=100_000.0,  #Pro forex; pro indic, crypto, comodities 1
    order_expiry_days=14,
    ext_order_expiry_days=7,
    pending_cancel_mode="number",
    pending_cancel_after_days=14,
    # --- TREND & BOS (explicitne; zmente pro experiment) ---
    trend_filter_enabled=False,
    trend_hh_hl_filter_enabled=False,
    tp_mode=TPMode.RRR_FIXED,
    tp_target_wave_index=4,
    wave_extension_pct=0.20,
    counter_position_enabled=False,
    bos_entry_enable=False,
    # bos_entry_in_rrr_fixed — WAVE_BOS po BOS flipu jen v rrr_fixed; zapni pri tp_mode=RRR_FIXED.
    bos_entry_in_rrr_fixed=False,
    # TWO-SIDED ENTRY + PP — defaultne vypnuto
    two_sided_entry_enabled=False,
    two_sided_entry_min_wave_pct=0.55,
    wave_position_enabled=True,  # klasické vlny
    wave_min_sl=0.12,
    pp_enabled=False,
    pp_sl_pct=0.21,
    pp_risk_usd=500.0,
    pp_disabled_in_ext_context=True,
    # Weekend-gap relax pro EXT prah (viz BotConfig).
    ext_weekend_gap_relax_factor=0.5,
    ext_secondary_enabled=False,
    # --- WICK FAKEOUT RECOVERY (WF) ---
    wf_enabled=True,  # Wick Fakeout Recovery
)

# Jen klasické WAVE — live preset (apply_wave_positions_only_to_bot_config pri startu).
WAVE_ONLY_LIVE = BotConfig(
    bot_name="WAVE_ONLY_LIVE",
    symbol=LIVE_BOT_CONFIG.symbol,
    timeframe=LIVE_BOT_CONFIG.timeframe,
    wave_min_pct=LIVE_BOT_CONFIG.wave_min_pct,
    min_opp_bars=LIVE_BOT_CONFIG.min_opp_bars,
    rrr=LIVE_BOT_CONFIG.rrr,
    entry_fib_level=LIVE_BOT_CONFIG.entry_fib_level,
    sl_fib_level=LIVE_BOT_CONFIG.sl_fib_level,
    abort_fib_level=LIVE_BOT_CONFIG.abort_fib_level,
    wave_plus=LIVE_BOT_CONFIG.wave_plus,
    entry_mode=LIVE_BOT_CONFIG.entry_mode,
    risk_usd=LIVE_BOT_CONFIG.risk_usd,
    contract_size=LIVE_BOT_CONFIG.contract_size,
    magic=LIVE_BOT_CONFIG.magic,
    tp_mode=LIVE_BOT_CONFIG.tp_mode,
    tp_target_wave_index=LIVE_BOT_CONFIG.tp_target_wave_index,
    wave_extension_pct=LIVE_BOT_CONFIG.wave_extension_pct,
    trend_filter_enabled=LIVE_BOT_CONFIG.trend_filter_enabled,
    trend_hh_hl_filter_enabled=LIVE_BOT_CONFIG.trend_hh_hl_filter_enabled,
    wave_positions_only=True,
    wave_position_enabled=True,
    wave_counter_two_sided_enabled=False,
    counter_position_enabled=False,
    two_sided_entry_enabled=False,
    pp_enabled=False,
    bos_entry_enable=False,
    bos_entry_in_rrr_fixed=False,
    ext_enabled=False,
    ext_counter_enabled=False,
    ext_secondary_enabled=False,
    wf_enabled=LIVE_BOT_CONFIG.wf_enabled,
    wave_min_sl=LIVE_BOT_CONFIG.wave_min_sl,
    order_expiry_days=LIVE_BOT_CONFIG.order_expiry_days,
    pending_cancel_mode=LIVE_BOT_CONFIG.pending_cancel_mode,
)

# Registr vsech configu, dostupny v live i v backtestu pres --config <NAZEV>.
# TREND & BOS: kazdy zaznam nize ma v tele BotConfig(...) explicitne nastavene
# trend_filter_enabled / tp_mode / ... (viz komentare u LIVE_BOT_CONFIG a EXAMPLE_EURUSD_M15).
CONFIG_REGISTRY: dict = {
    "LIVE_BOT_CONFIG":      LIVE_BOT_CONFIG,
    "WAVE_ONLY_LIVE":       WAVE_ONLY_LIVE,
    "EXAMPLE_EURUSD_M15":   EXAMPLE_EURUSD_M15,
}

# Zpetna kompatibilita

DEFAULT_CONFIG = LIVE_BOT_CONFIG
 # Volím si z čeho bude bot vycházet

# ------ Live bot s defaultním configem ------
# python main.py

# ------ Live bot s jiným configem  ------
# python main.py --config EXAMPLE_EURUSD_M15

# ------Backtest s defaultním configem ------
# python -m backtest.run_backtest -- profile live_match

# ------ Backtest s jiným configem ------
# python -m backtest.run_backtest --profile live_match --config EXAMPLE_EURUSD_M15


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