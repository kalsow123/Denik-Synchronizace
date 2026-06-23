"""
Prevod grid kombinaci (dict s parametry) na BotConfig instance.

Klicovy modul: prepojuje grid (kde je timeframe string "M15") na BotConfig
(kde je timeframe int z MT5 konstant).

WAVE SESSION FILTER:
  - Pokud `wave_allowed_sessions` je None -> filter vypnuty.
  - Pokud je to list (napr. ["LONDON", "USA"]) -> filter zapnuty.
  - `wave_custom_window` (tuple "HH:MM", "HH:MM") prepisuje sessions.
"""
from __future__ import annotations

from typing import List

from config.bot_config import BotConfig, TIMEFRAME_MAP, parse_abort_fib_level_grid
from config.enums import EntryMode, TPMode


def _grid_bool(v, default: bool = False) -> bool:
    """True/False z gridu (bool, int, nebo retezec true/false/1/0)."""
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return bool(int(v))
    s = str(v).strip().lower()
    if s in ("0", "false", "no", "off", ""):
        return False
    if s in ("1", "true", "yes", "on"):
        return True
    return default


def grid_dict_to_bot_config(d: dict) -> BotConfig:
    """
    Prevede jeden dict z grid generatoru na BotConfig.

    Vstupni dict ma stringy ("M15", "market_fallback", ...).
    Vystupni BotConfig ma int timeframe a EntryMode enum.
    """
    tf_str = d.get("timeframe", "M5")
    if tf_str not in TIMEFRAME_MAP:
        raise ValueError(
            f"Neznamy timeframe: '{tf_str}'. Dostupne: {list(TIMEFRAME_MAP.keys())}"
        )
    tf_int = TIMEFRAME_MAP[tf_str]

    em_str = d.get("entry_mode", "market_fallback")
    try:
        entry_mode = EntryMode(em_str)
    except ValueError:
        raise ValueError(
            f"Neznamy entry_mode: '{em_str}'. Dostupne: "
            f"{[m.value for m in EntryMode]}"
        )

    # Wave session filter:
    # None = vypnuto, list = zapnuto
    allowed = d.get("wave_allowed_sessions", None)
    custom = d.get("wave_custom_window", None)
    session_enabled = (allowed is not None and len(allowed) > 0) or (custom is not None)
    # Pokud je allowed None, dame default list (ale enabled bude False)
    if allowed is None:
        allowed = ["LONDON", "USA"]
    # Pokud doslo z gridu prazdny tuple/list misto None, taky beren jako vypnuto
    if isinstance(allowed, (list, tuple)) and len(allowed) == 0 and custom is None:
        session_enabled = False
        allowed = ["LONDON", "USA"]

    # sl_fib_level / abort_fib_level: pokud nejsou v gridu, BotConfig default.
    # abort_fib_level — float = číselná pasiónka; str = parse_abort_fib_level_grid (deep_retrace_shift_sl / shift_sl).
    sl_fib_kwargs = {}
    if "sl_fib_level" in d and d["sl_fib_level"] is not None:
        sl_fib_kwargs["sl_fib_level"] = float(d["sl_fib_level"])
    if "abort_fib_level" in d and d["abort_fib_level"] is not None:
        # float | str → viz POPIS_NASTAVENI_BOTA_A_BACKTESTERU.txt sekce abort_fib_level
        sl_fib_kwargs["abort_fib_level"] = parse_abort_fib_level_grid(d["abort_fib_level"])

    # Chybí-li klíč v kombinaci, výchozí = True (sjednoceno s BotConfig.wave_plus a grid profily).
    wave_plus = _grid_bool(d.get("wave_plus"), True)

    # TREND FILTER (BOS): pres grid lze zapnout filter smeru trendu i HH/HL subfilter.
    # Pokud klic v gridu chybi, default = False (= filter vypnuty, bit-perfect stejne
    # vysledky jako pred zavedenim trend featur).
    trend_filter_enabled = _grid_bool(d.get("trend_filter_enabled"), False)
    trend_hh_hl_filter_enabled = _grid_bool(d.get("trend_hh_hl_filter_enabled"), False)

    # TP MODE: rrr_fixed / bos_exit / bos_exit_priority / wave_target_n / wave_target_n_g.
    # Pokud klic v gridu chybi, drzime se default RRR_FIXED → bit-perfect stejne vysledky.
    tp_mode_raw = d.get("tp_mode", "rrr_fixed")
    if isinstance(tp_mode_raw, TPMode):
        tp_mode = tp_mode_raw
    else:
        try:
            tp_mode = TPMode(str(tp_mode_raw))
        except ValueError:
            raise ValueError(
                f"Neznamy tp_mode: '{tp_mode_raw}'. Dostupne: {[m.value for m in TPMode]}"
            )
    wave_extension_pct = float(d.get("wave_extension_pct", 0.20))

    # WAVE_TARGET_N rodina — wave_target_n_g dostane G preset v normalize_wave_target_n_cfg().
    # Fine-tuning u wave_target_n: tp_wave_early_mode / tp_wave_exit_on / ...
    from config.enums import TpWaveEarlyMode, TpWaveExitOn, TpWaveIntrabarPriority

    _early_raw = str(d.get("tp_wave_early_mode", "off")).lower()
    try:
        tp_wave_early_mode = TpWaveEarlyMode(_early_raw)
    except ValueError:
        tp_wave_early_mode = TpWaveEarlyMode.OFF
    _exit_raw = str(d.get("tp_wave_exit_on", "birth")).lower()
    try:
        tp_wave_exit_on = TpWaveExitOn(_exit_raw)
    except ValueError:
        tp_wave_exit_on = TpWaveExitOn.BIRTH
    tp_wave_early_fallback_birth = _grid_bool(
        d.get("tp_wave_early_fallback_birth"), True,
    )
    _prio_raw = str(d.get("tp_wave_intrabar_priority", "tp_before_sl")).lower()
    try:
        tp_wave_intrabar_priority = TpWaveIntrabarPriority(_prio_raw)
    except ValueError:
        tp_wave_intrabar_priority = TpWaveIntrabarPriority.TP_BEFORE_SL

    # WAVE_TARGET_N (volitelne — pri jinych tp_mode se klice ignoruji, ale ulozi do BotConfig):
    tp_target_wave_index = int(d.get("tp_target_wave_index", 4))
    from config.position_modes import plan_grid_position_flags

    pos = plan_grid_position_flags(d)
    wave_counter_two_sided_enabled = pos.wave_counter_two_sided_enabled
    counter_position_enabled = pos.counter_position_enabled
    bos_entry_enable = pos.bos_entry_enable
    bos_entry_in_rrr_fixed = pos.bos_entry_in_rrr_fixed
    sl_ladder_base = float(d.get("wave_size_sl_ladder_base_pct", 0.21))
    sl_ladder_step = float(d.get("wave_size_sl_ladder_step_pct", 0.11))
    sl_ladder_band = float(d.get("wave_size_sl_ladder_band_size_pct", 0.50))

    # TWO-SIDED ENTRY (protipozice na protivlni po velke vlně + dotek FIB rodice):
    two_sided_entry_enabled = pos.two_sided_entry_enabled
    two_sided_entry_min_wave_pct = float(d.get("two_sided_entry_min_wave_pct", 0.55))
    two_sided_entry_min_sl_move_pct = float(
        d.get("two_sided_entry_min_sl_move_pct", 0.16)
    )
    two_sided_entry_bypass_trend_filter = _grid_bool(
        d.get("two_sided_entry_bypass_trend_filter"), True
    )
    skip_primary_entry_on_parent_wave_enable = _grid_bool(
        d.get("skip_primary_entry_on_parent_wave_enable"), True  # preskocit primarni WAVE vstup na two-sided rodici A; jen protipozice na protivlni B
    )
    wave_positions_only = pos.wave_positions_only

    # klasické vlny — wave_position_enabled (default True = chování jako dřív).
    wave_position_enabled = pos.wave_position_enabled
    # Min SL pro standardní WAVE pozice; aliasy kvůli uživatelskému pojmenování.
    wave_min_sl = float(
        d.get(
            "wave_min_sl",
            d.get("wave_min_sl_pct", d.get("wave_min_sl_%", 0.12)),
        )
    )

    # PP POSITIONS (Push-through po close-baru nad WAVE high/low v trendu):
    pp_enabled = pos.pp_enabled
    pp_sl_pct = float(d.get("pp_sl_pct", 0.21))
    pp_risk_usd = float(d.get("pp_risk_usd", 500.0))
    pp_disabled_in_ext_context = _grid_bool(d.get("pp_disabled_in_ext_context"), True)

    ext_enabled = pos.ext_enabled
    ext_secondary_enabled = pos.ext_secondary_enabled
    ext_wave_min_pct = float(d.get("ext_wave_min_pct", 1.0))
    # Weekend-gap relax pro EXT prah (viz BotConfig.ext_weekend_gap_relax_factor).
    ext_weekend_gap_relax_factor = float(d.get("ext_weekend_gap_relax_factor", 0.0))
    ext_primary_fib_level = float(d.get("ext_primary_fib_level", 0.5))
    ext_secondary_fib_level = float(d.get("ext_secondary_fib_level", 0.236))
    ext_secondary_sl_fib_level = float(d.get("ext_secondary_sl_fib_level", 0.4))
    ext_min_sl_move_pct = float(d.get("ext_min_sl_move_pct", 0.16))
    ext_counter_enabled = pos.ext_counter_enabled
    ext_counter_time = str(d.get("ext_counter_time", "21:00"))
    ext_counter_sl_pct = float(d.get("ext_counter_sl_pct", 0.21))
    ext_counter_min_sl_enabled = _grid_bool(d.get("ext_counter_min_sl_enabled"), True)
    ext_counter_min_sl_pct = float(d.get("ext_counter_min_sl_pct", 0.16))
    ext_bos_fib_level = float(d.get("ext_bos_fib_level", 0.35))
    ext_trade_both_sides_in_range = _grid_bool(
        d.get("ext_trade_both_sides_in_range"), False
    )
    ext_range_protect_pendings_from_bos_cancel = _grid_bool(
        d.get("ext_range_protect_pendings_from_bos_cancel"), True
    )
    ext_close_trend_positions_on_bos = _grid_bool(
        d.get("ext_close_trend_positions_on_bos"), False
    )
    wave_2_no_tp_enable = _grid_bool(d.get("wave_2_no_tp_enable"), False)
    wave_2_no_tp_max_index = int(d.get("wave_2_no_tp_max_index", 2))
    wf_enabled = _grid_bool(d.get("wf_enabled"), True)  # Wick Fakeout Recovery

    adx14_change_enabled = _grid_bool(d.get("adx14_change_enabled"), False)
    adx14_equity_gate_enabled = _grid_bool(d.get("adx14_equity_gate_enabled"), False)
    pnl_base_tracker_enabled = _grid_bool(d.get("pnl_base_tracker_enabled"), True)
    causal_mode = _grid_bool(d.get("causal_mode"), False)
    run_e2e_parity = _grid_bool(d.get("run_e2e_parity"), False)

    cfg = BotConfig(
        bot_name=d.get("bot_name", "GRID_BOT"),
        magic=d.get("magic", 999_999),
        symbol=d.get("symbol", "EURUSD"),
        timeframe=tf_int,
        wave_min_pct=float(d["wave_min_pct"]),
        causal_mode=causal_mode,
        run_e2e_parity=run_e2e_parity,
        min_opp_bars=int(d["min_opp_bars"]),
        rrr=float(d["rrr"]),
        entry_fib_level=float(d["fib_level"]),
        entry_mode=entry_mode,
        risk_usd=float(d.get("risk_usd", 500.0)),
        contract_size=float(d.get("contract_size", 100_000.0)),
        order_expiry_days=int(d.get("order_expiry_days", 14)),
        ext_order_expiry_days=int(d.get("ext_order_expiry_days", 7)),
        pending_cancel_mode=str(d.get("pending_cancel_mode", "number")),
        pending_cancel_after_days=int(d.get("pending_cancel_after_days", 14)),
        max_wave_age_hours=int(d.get("max_wave_age_hours", 8)),
        wave_max_pct=(None if d.get("wave_max_pct", None) is None else float(d.get("wave_max_pct"))),
        wave_session_filter_enabled=session_enabled,
        wave_allowed_sessions=list(allowed) if isinstance(allowed, (list, tuple)) else allowed,
        wave_custom_window=tuple(custom) if isinstance(custom, (list, tuple)) else custom,
        wave_plus=wave_plus,
        trend_filter_enabled=trend_filter_enabled,
        trend_hh_hl_filter_enabled=trend_hh_hl_filter_enabled,
        tp_mode=tp_mode,
        tp_target_wave_index=tp_target_wave_index,
        wave_extension_pct=wave_extension_pct,
        tp_wave_early_mode=tp_wave_early_mode,
        tp_wave_exit_on=tp_wave_exit_on,
        tp_wave_early_fallback_birth=tp_wave_early_fallback_birth,
        tp_wave_intrabar_priority=tp_wave_intrabar_priority,
        wave_positions_only=wave_positions_only,
        wave_counter_two_sided_enabled=wave_counter_two_sided_enabled,
        counter_position_enabled=counter_position_enabled,
        bos_entry_enable=bos_entry_enable,
        bos_entry_in_rrr_fixed=bos_entry_in_rrr_fixed,
        wave_size_sl_ladder_base_pct=sl_ladder_base,
        wave_size_sl_ladder_step_pct=sl_ladder_step,
        wave_size_sl_ladder_band_size_pct=sl_ladder_band,
        two_sided_entry_enabled=two_sided_entry_enabled,
        two_sided_entry_min_wave_pct=two_sided_entry_min_wave_pct,
        two_sided_entry_min_sl_move_pct=two_sided_entry_min_sl_move_pct,
        two_sided_entry_bypass_trend_filter=two_sided_entry_bypass_trend_filter,
        skip_primary_entry_on_parent_wave_enable=skip_primary_entry_on_parent_wave_enable,
        wave_position_enabled=wave_position_enabled,
        wave_min_sl=wave_min_sl,
        pp_enabled=pp_enabled,
        pp_sl_pct=pp_sl_pct,
        pp_risk_usd=pp_risk_usd,
        pp_disabled_in_ext_context=pp_disabled_in_ext_context,
        ext_enabled=ext_enabled,
        ext_secondary_enabled=ext_secondary_enabled,
        ext_wave_min_pct=ext_wave_min_pct,
        ext_weekend_gap_relax_factor=ext_weekend_gap_relax_factor,
        ext_primary_fib_level=ext_primary_fib_level,
        ext_secondary_fib_level=ext_secondary_fib_level,
        ext_secondary_sl_fib_level=ext_secondary_sl_fib_level,
        ext_min_sl_move_pct=ext_min_sl_move_pct,
        ext_counter_enabled=ext_counter_enabled,
        ext_counter_time=ext_counter_time,
        ext_counter_sl_pct=ext_counter_sl_pct,
        ext_counter_min_sl_enabled=ext_counter_min_sl_enabled,
        ext_counter_min_sl_pct=ext_counter_min_sl_pct,
        ext_bos_fib_level=ext_bos_fib_level,
        ext_trade_both_sides_in_range=ext_trade_both_sides_in_range,
        ext_range_protect_pendings_from_bos_cancel=ext_range_protect_pendings_from_bos_cancel,
        ext_close_trend_positions_on_bos=ext_close_trend_positions_on_bos,
        wave_2_no_tp_enable=wave_2_no_tp_enable,
        wave_2_no_tp_max_index=wave_2_no_tp_max_index,
        wf_enabled=wf_enabled,  # Wick Fakeout Recovery
        adx14_change_enabled=adx14_change_enabled,
        adx14_equity_gate_enabled=adx14_equity_gate_enabled,
        pnl_base_tracker_enabled=pnl_base_tracker_enabled,
        **sl_fib_kwargs,  # sl_fib_level + volitelně abort_fib_level (číslo nebo deep_retrace_shift_sl)
    )
    from strategy.wave_target_n_mode import normalize_wave_target_n_cfg
    return normalize_wave_target_n_cfg(cfg)


def grid_backtest_position_cap_settings(d: dict) -> tuple[str, int | None]:
    mode = str(d.get("backtest_position_cap_mode", "off")).lower()
    raw_limit = d.get("backtest_max_open_positions", None)
    if raw_limit is None:
        return mode, None
    try:
        limit = int(raw_limit)
    except Exception:
        return mode, None
    return mode, (limit if limit > 0 else None)


# Vychozi simulacni parametry gridu (EXAMPLE profil) — live_match parita s grid backtesterem.
_LIVE_MATCH_GRID_SIM_DEFAULTS: dict = {
    "spread": 0.0001,
    "slippage": 0.0,
    "track_concurrent_positions": True,
    "backtest_position_cap_mode": "off",
    "backtest_max_open_positions": None,
}


def bot_config_to_grid_combo_dict(
    cfg: BotConfig,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    combo_no: int = 1,
) -> dict:
    """
    Grid combo dict z BotConfig — jen skalární klíče jako u grid profilu (EXAMPLE).
    """
    from config.bot_config import ABORT_FIB_SHIFT_SL

    def _enum(v):
        return v.value if hasattr(v, "value") else v

    abort = cfg.abort_fib_level
    if abort in (ABORT_FIB_SHIFT_SL, "deep_retrace_shift_sl", "shift_sl"):
        abort_fib: float | str | None = "shift_sl"
    else:
        abort_fib = abort

    combo: dict = {
        "symbol": cfg.symbol,
        "timeframe": cfg.timeframe_label,
        "date_from": date_from,
        "date_to": date_to,
        "bot_name": cfg.bot_name,
        "_grid_test_pozice": int(combo_no),
        "wave_min_pct": cfg.wave_min_pct,
        "causal_mode": cfg.causal_mode,
        "run_e2e_parity": cfg.run_e2e_parity,
        "min_opp_bars": cfg.min_opp_bars,
        "rrr": cfg.rrr,
        "fib_level": cfg.entry_fib_level,
        "entry_mode": _enum(cfg.entry_mode),
        "tp_mode": _enum(cfg.tp_mode),
        "tp_target_wave_index": cfg.tp_target_wave_index,
        "wave_extension_pct": cfg.wave_extension_pct,
        "sl_fib_level": cfg.sl_fib_level,
        "abort_fib_level": abort_fib,
        "wave_plus": cfg.wave_plus,
        "order_expiry_days": cfg.order_expiry_days,
        "ext_order_expiry_days": cfg.ext_order_expiry_days,
        "pending_cancel_mode": _enum(cfg.pending_cancel_mode),
        "pending_cancel_after_days": cfg.pending_cancel_after_days,
        "wave_max_pct": cfg.wave_max_pct,
        "max_wave_age_hours": cfg.max_wave_age_hours,
        "risk_usd": cfg.risk_usd,
        "pp_risk_usd": cfg.pp_risk_usd,
        "contract_size": cfg.contract_size,
        "magic": cfg.magic,
        "wave_min_sl": cfg.wave_min_sl,
        "wave_position_enabled": cfg.wave_position_enabled,
        "wave_positions_only": cfg.wave_positions_only,
        "wave_isolation_study": cfg.wave_isolation_study,
        "wave_counter_two_sided_enabled": cfg.wave_counter_two_sided_enabled,
        "counter_position_enabled": cfg.counter_position_enabled,
        "two_sided_entry_enabled": cfg.two_sided_entry_enabled,
        "two_sided_entry_min_wave_pct": cfg.two_sided_entry_min_wave_pct,
        "skip_primary_entry_on_parent_wave_enable": cfg.skip_primary_entry_on_parent_wave_enable,
        "wf_enabled": cfg.wf_enabled,
        "pp_enabled": cfg.pp_enabled,
        "pp_sl_pct": cfg.pp_sl_pct,
        "pp_disabled_in_ext_context": cfg.pp_disabled_in_ext_context,
        "trend_filter_enabled": cfg.trend_filter_enabled,
        "trend_hh_hl_filter_enabled": cfg.trend_hh_hl_filter_enabled,
        "bos_entry_enable": cfg.bos_entry_enable,
        "bos_entry_in_rrr_fixed": cfg.bos_entry_in_rrr_fixed,
        "wave_2_no_tp_enable": cfg.wave_2_no_tp_enable,
        "wave_2_no_tp_max_index": cfg.wave_2_no_tp_max_index,
        "wave_size_sl_ladder_base_pct": cfg.wave_size_sl_ladder_base_pct,
        "wave_size_sl_ladder_step_pct": cfg.wave_size_sl_ladder_step_pct,
        "wave_size_sl_ladder_band_size_pct": cfg.wave_size_sl_ladder_band_size_pct,
        "ext_enabled": cfg.ext_enabled,
        "ext_wave_min_pct": cfg.ext_wave_min_pct,
        "ext_secondary_enabled": cfg.ext_secondary_enabled,
        "ext_weekend_gap_relax_factor": cfg.ext_weekend_gap_relax_factor,
        "ext_counter_enabled": cfg.ext_counter_enabled,
        "ext_counter_time": cfg.ext_counter_time,
        "ext_counter_min_sl_enabled": cfg.ext_counter_min_sl_enabled,
        "ext_counter_min_sl_pct": cfg.ext_counter_min_sl_pct,
        "ext_trade_both_sides_in_range": cfg.ext_trade_both_sides_in_range,
        "wave_min_pct_enable": cfg.wave_min_pct_enable,
        "ext_post_both_sides_wave_min_pct": cfg.ext_post_both_sides_wave_min_pct,
        "ext_post_both_sides_default_sl_pct": cfg.ext_post_both_sides_default_sl_pct,
        "ext_close_trend_positions_on_bos": cfg.ext_close_trend_positions_on_bos,
        "adx14_change_enabled": cfg.adx14_change_enabled,
        "adx14_equity_gate_enabled": cfg.adx14_equity_gate_enabled,
        "pnl_base_tracker_enabled": cfg.pnl_base_tracker_enabled,
        "wave_allowed_sessions": None,
        "wave_custom_window": None,
    }
    combo.update(_LIVE_MATCH_GRID_SIM_DEFAULTS)
    return combo


def grid_dicts_to_bot_configs(combos: List[dict]) -> List[BotConfig]:
    """Prevede seznam grid kombinaci na seznam BotConfig instanci."""
    return [grid_dict_to_bot_config(d) for d in combos]