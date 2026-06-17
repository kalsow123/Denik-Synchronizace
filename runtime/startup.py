
import logging
from datetime import datetime, timezone
from typing import Set

import MetaTrader5 as mt5

from config.bot_config import BotConfig
from core.logging_utils import log_event
from core.signal_keys import get_signal_key
from core.trading_days import is_older_than_business_days
from infra.live_order_guard import deduplicate_magic_pendings
from infra.market_data import get_bars
from infra.orders import send_startup_pending_only
from infra.pending_snapshot import restore_session_pending_snapshot
from infra.state_sync import get_active_wave_times, get_position_wave_times
from strategy.filters import is_wave_in_allowed_session, is_wave_too_large
from strategy.pine_recovery import simulate_pine_pending_state
from strategy.wave_detection import detect_waves

log = logging.getLogger(__name__)


def _last_closed_bar_close(df) -> float | None:
    """Close posledniho uzavreneho baru z MT5 df (posledni radek = forming)."""
    if df is None or df.empty:
        return None
    if len(df) >= 2:
        return float(df["close"].iloc[-2])
    return float(df["close"].iloc[-1])


def _signal_key_for_wave(signal: dict, signal_digits: int) -> str:
    return get_signal_key(signal, digits=signal_digits)


def restore_pine_style_pending_orders(cfg: BotConfig) -> Set[str]:
    """
    Pine recovery po snapshotu / cold startu:
      1) Simuluje pending vs otevrene obchody k poslednimu close baru.
      2) Otevrene simulovane obchody oznaci jako zpracovane; pokud chybi MT5
         pozice, NEREOPEN (market za starou cenu by byl chyba) — jen log.
      3) Chybejici pending LIMIT doplni (pine_recovery=True, rozhodnuti dle
         close baru — ne aktualniho ticku).
      4) Vraci signal_keys vsech relevantnich setupu pro sent_signals.
    """
    df = get_bars(cfg, cfg.startup_bars)
    if df is None or len(df) < 2:
        log.warning("STARTUP RECOVERY: nedostatek dat pro pine-style recovery")
        return set()

    pending_setups, simulated_open_trades = simulate_pine_pending_state(df, cfg)
    bar_close = _last_closed_bar_close(df)

    symbol_info = mt5.symbol_info(cfg.symbol)
    signal_digits = int(getattr(symbol_info, "digits", 4)) if symbol_info else 4

    simulated_open_wts: Set[str] = {
        str(t["wave_time"]) for t in simulated_open_trades
    }
    expected_pending_wts: Set[str] = {
        str(p["wave_time"]) for p in pending_setups
    }

    log.info(
        "STARTUP RECOVERY: pine simulace | pending=%s open_sim=%s bar_close=%s",
        len(pending_setups),
        len(simulated_open_trades),
        (None if bar_close is None else f"{bar_close:.5f}"),
    )

    recovered_signal_keys: Set[str] = set()
    now_utc = datetime.now(timezone.utc)
    skipped_session = 0
    placed = 0
    skipped_open_no_position = 0

    active_order_times = get_active_wave_times(cfg)
    active_position_times = get_position_wave_times(cfg)

    # --- Simulovane OTEVRENE obchody: nikdy znovu pending, nikdy market ---
    for trade in simulated_open_trades:
        wt = str(trade["wave_time"])
        sig_key = _signal_key_for_wave(trade, signal_digits)
        recovered_signal_keys.add(sig_key)

        if wt in active_position_times:
            continue
        if wt in active_order_times:
            continue

        skipped_open_no_position += 1
        log_event(
            cfg,
            "info",
            "RECOVERY_SIMULATED_OPEN_NO_POSITION",
            wave_id=wt,
            message=(
                "Simulace: LIMIT uz fillnut na barech, ale MT5 pozice chybi — "
                "nereopen (mozna mezitim zavreno)"
            ),
        )

    # --- Pending setupy: doplnit chybejici LIMIT ---
    for signal in pending_setups:
        wt = str(signal["wave_time"])
        sig_key = _signal_key_for_wave(signal, signal_digits)
        recovered_signal_keys.add(sig_key)

        if wt in simulated_open_wts:
            continue

        wave_dt_utc = datetime.strptime(wt, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
        if is_older_than_business_days(wave_dt_utc, now_utc, int(cfg.order_expiry_days)):
            continue

        active_order_times = get_active_wave_times(cfg)
        active_position_times = get_position_wave_times(cfg)
        if wt in active_order_times or wt in active_position_times:
            continue

        if not is_wave_in_allowed_session(wt, cfg):
            skipped_session += 1
            continue

        if is_wave_too_large(signal["move_pct"], cfg):
            continue

        if not bool(getattr(cfg, "wave_position_enabled", True)):
            continue

        if send_startup_pending_only(
            signal,
            cfg,
            pine_recovery=True,
            bar_close=bar_close,
        ):
            placed += 1
            active_order_times = get_active_wave_times(cfg)

    if skipped_session > 0:
        log.info(
            "STARTUP RECOVERY: %s setupu preskoceno mimo povolenou wave session",
            skipped_session,
        )

    # --- Reconcile audit ---
    active_order_times = get_active_wave_times(cfg)
    active_position_times = get_position_wave_times(cfg)
    covered = active_order_times | active_position_times | simulated_open_wts
    still_missing = expected_pending_wts - covered

    log_event(
        cfg,
        "info",
        "RECOVERY_RECONCILE",
        expected_pending=len(expected_pending_wts),
        simulated_open=len(simulated_open_wts),
        placed=int(placed),
        mt5_pending=len(active_order_times),
        mt5_positions_wave=len(active_position_times),
        still_missing_pending=len(still_missing),
        simulated_open_no_mt5=int(skipped_open_no_position),
    )
    if still_missing:
        log.warning(
            "STARTUP RECOVERY: stale chybi pending pro vlny: %s",
            sorted(still_missing)[:12],
        )

    return recovered_signal_keys


def restore_all_pending_orders(cfg: BotConfig) -> Set[str]:
    """
    Startup / session wake-up:
      1) Obnovi pendingy ze session snapshotu (CNTR/PP/EXT/WAVE/...).
      2) Deduplikace (snapshot + pine nesmi nechat 2x stejny comment).
      3) Pine recovery doplni chybejici WAVE LIMIT a sladí stav s simulací.
      4) Deduplikace znovu.
    """
    snap_n = restore_session_pending_snapshot(cfg)
    if snap_n:
        log.info(
            "STARTUP RECOVERY: ze session snapshotu obnoveno %s pending orderu",
            snap_n,
        )

    dedup1 = deduplicate_magic_pendings(cfg)
    if dedup1:
        log.info("STARTUP RECOVERY: po snapshotu dedup %s duplicit", dedup1)

    recovered = restore_pine_style_pending_orders(cfg)

    dedup2 = deduplicate_magic_pendings(cfg)
    if dedup2:
        log.info("STARTUP RECOVERY: po pine dedup %s duplicit", dedup2)

    return recovered


def run_full_startup_recovery(
    cfg: BotConfig,
    sent_signals: Set[str] | None = None,
    *,
    failed_signals: dict | None = None,
    recovery_reason: str = "startup",
) -> Set[str]:
    """
    Jednotna recovery pro main.py start i SESSION_WAKE_UP v live loopu:
      snapshot + pine + block_historical_waves.
    """
    if failed_signals is not None:
        from runtime.failed_signals_replay import clear_failed_signals_on_recovery

        clear_failed_signals_on_recovery(
            failed_signals, cfg=cfg, reason=recovery_reason,
        )

    out: Set[str] = set(sent_signals or [])
    recovered = restore_all_pending_orders(cfg)
    out |= recovered
    out = block_historical_waves(cfg, out)
    log.info(
        "STARTUP RECOVERY: celkem %s signal_keys v sent_signals (vcetne historickych vln)",
        len(out),
    )
    return out


def block_historical_waves(cfg: BotConfig, sent_signals: Set[str]) -> Set[str]:
    """
    Detekuje vsechny historicke vlny v poslednich STARTUP_BARS barech
    a oznaci je jako uz zpracovane, aby je live loop neposlal pres send_order()
    s povolenym market fallbackem.
    """
    df_boot = get_bars(cfg, cfg.startup_bars)
    symbol_info = mt5.symbol_info(cfg.symbol)
    signal_digits = int(getattr(symbol_info, "digits", 4)) if symbol_info else 4
    if df_boot is not None and len(df_boot) >= 2:
        boot_waves = detect_waves(df_boot, cfg)
        sent_signals |= set(
            get_signal_key(w, digits=signal_digits) for w in boot_waves
        )
    return sent_signals
