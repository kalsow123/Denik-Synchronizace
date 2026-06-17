
import logging
from datetime import datetime, timezone
from typing import Set

import MetaTrader5 as mt5

from config.bot_config import BotConfig
from core.signal_keys import get_signal_key
from core.trading_days import is_older_than_business_days
from infra.market_data import get_bars
from infra.orders import send_startup_pending_only
from infra.pending_snapshot import restore_session_pending_snapshot
from infra.state_sync import get_active_wave_times, get_position_wave_times
from strategy.filters import is_wave_in_allowed_session, is_wave_too_large
from strategy.pine_recovery import simulate_pine_pending_state
from strategy.wave_detection import detect_waves

# ───── ŠPUŠTĚNÍ BOTA ──────────────────────────

# Co se děje po spuštění bota
# Procházení zpětně X svící, definice vln, definice těch u kterých ještě neproběhl settup, zadání orderů
log = logging.getLogger(__name__)


def restore_pine_style_pending_orders(cfg: BotConfig) -> Set[str]:
    """
    Startup recovery (TREND-FOLLOW LIMIT strategie):
      1) Nacte STARTUP_BARS baru historie z MT5.
      2) Pres simulate_pine_pending_state simuluje, jake setupy by k poslednimu
         baru mely zit jako PENDING LIMIT (cena se jeste nedotkla entry)
         nebo jako otevrene obchody (cena dosahla entry, jeste nezavreno SL/TP).
      3) Pro PENDING setupy, ktere jeste nejsou v MT5 ani jako pending ani jako
         pozice, zalozi LIMIT order pres send_startup_pending_only().
         (Otevrene obchody se NEDOPLNUJI - chybejici otevrena pozice znamena
          ze byla mezitim zavrena, market vstup za starou cenu by byl chyba.)
      4) Vraci set signal_keys vsech historickych pending setupu, aby je live
         loop nikdy nepovazoval za "nove" a neposlal pres send_order() znovu.
    """
    df = get_bars(cfg, cfg.startup_bars)
    if df is None or len(df) < 2:
        log.warning("STARTUP RECOVERY: nedostatek dat pro pine-style recovery")
        return set()

    pending_setups, simulated_open_trades = simulate_pine_pending_state(df, cfg)

    active_order_times = get_active_wave_times(cfg)
    active_position_times = get_position_wave_times(cfg)
    active_mt5_wave_times = active_order_times | active_position_times

    recovered_signal_keys: Set[str] = set()
    skipped_session = 0
    symbol_info = mt5.symbol_info(cfg.symbol)
    signal_digits = int(getattr(symbol_info, "digits", 4)) if symbol_info else 4

    log.info(
        f"STARTUP RECOVERY: pine simulace nalezla {len(pending_setups)} cekajicich setupu "
        f"a {len(simulated_open_trades)} otevrenych obchodu na poslednim baru"
    )

    now_utc = datetime.now(timezone.utc)

    for signal in pending_setups:
        wt = signal["wave_time"]
        wave_dt_utc = datetime.strptime(wt, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)

        # Startup recovery bere jen setupy v poslednich X obchodnich dnech (Po-Pa).
        # Vikendy se do tohoto limitu nezapocitavaji.
        if is_older_than_business_days(wave_dt_utc, now_utc, int(cfg.order_expiry_days)):
            continue

        sig_key = get_signal_key(signal, digits=signal_digits)

        # tuhle historickou vlnu vzdy oznac jako zpracovanou,
        # aby ji prvni live loop uz nikdy nevzal jako novou
        recovered_signal_keys.add(sig_key)

        # na tomto wave_time uz neco v MT5 existuje -> nic neposilej
        if wt in active_mt5_wave_times:
            continue

        # WAVE SESSION FILTER - vlna mimo povolene session se preskoci
        if not is_wave_in_allowed_session(wt, cfg):
            skipped_session += 1
            continue

        # Wave je prilis velka -> neobchodujeme ji ani pri startup recovery.
        if is_wave_too_large(signal["move_pct"], cfg):
            continue

        if not bool(getattr(cfg, "wave_position_enabled", True)):
            continue

        # pri startu posilame jen pending LIMIT, bez market / stop fallbacku
        send_startup_pending_only(signal, cfg)

    if skipped_session > 0:
        log.info(
            f"STARTUP RECOVERY: {skipped_session} setupu preskoceno mimo povolenou wave session"
        )

    return recovered_signal_keys


def restore_all_pending_orders(cfg: BotConfig) -> Set[str]:
    """
    Startup / session wake-up:
      1) Obnovi vsechny pendingy ze session snapshotu (CNTR/PP/EXT/WAVE/...).
      2) Pine recovery doplni chybejici WAVE LIMIT (cold start bez snapshotu).
    """
    snap_n = restore_session_pending_snapshot(cfg)
    if snap_n:
        log.info("STARTUP RECOVERY: ze session snapshotu obnoveno %s pending orderu", snap_n)
    return restore_pine_style_pending_orders(cfg)


def block_historical_waves(cfg: BotConfig, sent_signals: Set[str]) -> Set[str]:
    """
    Druhy krok startupu (puvodne primo v main()):
    Detekuje vsechny historicke vlny v poslednich STARTUP_BARS barech
    a oznaci je jako uz zpracovane, aby je live loop neposlal pres send_order()
    s povolenym market fallbackem.
    """
    df_boot = get_bars(cfg, cfg.startup_bars)
    symbol_info = mt5.symbol_info(cfg.symbol)
    signal_digits = int(getattr(symbol_info, "digits", 4)) if symbol_info else 4
    if df_boot is not None and len(df_boot) >= 2:
        boot_waves = detect_waves(df_boot, cfg)
        sent_signals |= set(get_signal_key(w, digits=signal_digits) for w in boot_waves)
    return sent_signals