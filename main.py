

import atexit
import os
import signal
import sys
import re
import argparse
from datetime import datetime
from pathlib import Path

from config.bot_config import CONFIG_REGISTRY, LIVE_BOT_CONFIG
from core.logging_utils import (
    BOT_INSTANCE_ID,
    log_event,
    setup_logging,
    write_bot_config_snapshot_jsonl,
)
from infra.mt5_client import connect, shutdown
from infra.telemetry_sync import ensure_telemetry_sync_running, stop_telemetry_sync
from infra.session_manager import is_session_enabled, is_in_session, get_broker_now
from runtime.instance_lock import LiveInstanceAlreadyRunning, ensure_single_live_instance
from runtime.live_loop import run_live_loop
ACTIVE_CFG = LIVE_BOT_CONFIG


# ───── TURNING BOT ON/OFF, CONNECTOR  ──────────────────────────

# Bot backtest actual version: python -m backtest.run_backtest --profile live_match
# Pro grid research: python -m backtest.run_backtest --profile grid --grid-profile best_candidates


"""Tento soubor jen orchestruje:
  1. Setup loggeru
  2. Load config (z CONFIG_REGISTRY, default LIVE_BOT_CONFIG)
  3. Connect do MT5
  4. Startup recovery (pine-style + blokace historickych vln)
     - pokud session_enabled a startujeme MIMO session, recovery se preskoci
       a probehne az pri prvni wake-up (varianta 2A: necha bezne, ale upraveno
       kvuli prevenci falsoveho vystaveni pendingu pred close)
  5. Live loop
  6. Graceful shutdown na KeyboardInterrupt """

    # Config z CONFIG_REGISTERY
def _resolve_config(name: str | None):
    if name is None:
        return LIVE_BOT_CONFIG
    if name not in CONFIG_REGISTRY:
        print(f"CHYBA: Neznamy config '{name}'.")
        print(f"Dostupne: {list(CONFIG_REGISTRY.keys())}")
        sys.exit(1)
    return CONFIG_REGISTRY[name]


def main() -> None:
    global ACTIVE_CFG
    parser = argparse.ArgumentParser(description="Live trading bot (MT5)")
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Jmeno configu z CONFIG_REGISTRY (default: LIVE_BOT_CONFIG)",
    )
    args = parser.parse_args()

    cfg = _resolve_config(args.config)
    from runtime.live_wave_isolation import (
        audit_mt5_non_wave_exposure,
        resolve_live_execution_config,
    )

    cfg = resolve_live_execution_config(cfg)
    ACTIVE_CFG = cfg

    from runtime.live_wave_isolation import log_live_execution_mode

    # 1) Setup loggeru (pred lockem — pokus o 2. instanci se zapise do .jsonl)
    safe_bot_name = re.sub(r"[^A-Za-z0-9._=-]+", "_", cfg.bot_name).strip("._-") or "bot"
    json_log_file = f"{safe_bot_name}.jsonl"
    config_snapshot_file = f"{safe_bot_name}_config.jsonl"
    write_bot_config_snapshot_jsonl(cfg, config_snapshot_file)
    log = setup_logging(
        "live_bot.log",
        json_file=json_log_file,
        json_retention_days=cfg.jsonl_retention_days,
    )
    log_live_execution_mode(cfg)

    try:
        ensure_single_live_instance(cfg)
    except LiveInstanceAlreadyRunning as exc:
        log_event(
            cfg,
            "warning",
            "DUPLICATE_INSTANCE_BLOCKED",
            message=str(exc),
            pid_attempted=os.getpid(),
        )
        print(f"CHYBA: {exc}", file=sys.stderr)
        sys.exit(2)

    # 2) Connect do MT5
    if not connect(cfg):
        return

    audit_mt5_non_wave_exposure(cfg)

    # 3) Hlavni BOT_START event
    log_event(
        cfg,
        "info",
        "BOT_START",
        version="1.0.0",
        strategy_name="WAVE_FIB_RRR",
        bot_instance_id=BOT_INSTANCE_ID,
        config_name=cfg.bot_name,
        symbol=cfg.symbol,
        timeframe=cfg.timeframe_label,
        wave_min_pct=cfg.wave_min_pct,
        rrr=cfg.rrr,
        entry_fib_level=cfg.entry_fib_level,
        risk_usd=cfg.risk_usd,
        expiry_days=cfg.order_expiry_days,
        startup_bars=cfg.startup_bars,
        max_wave_age_h=cfg.max_wave_age_hours,
        sleep_sec=cfg.sleep_sec,
        session_enabled=cfg.session_enabled,
        wave_session_filter=cfg.wave_session_filter_enabled,
        equity_target_usd=cfg.equity_target_usd,
        wf_enabled=bool(getattr(cfg, "wf_enabled", False)),
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
    _wf_on = bool(getattr(cfg, "wf_enabled", False))
    log.info("WF_ENABLED=%s (Wick Fakeout Recovery)", _wf_on)

    # 4) Startup recovery
    # Pokud session manager zapnuty a startujeme mimo session,
    # recovery proveden bude, ale loop se rovnou rozhodne usnout - vlny budou
    # blokovane a po probuzeni se recovery udela znovu, takze stav je konzistentni.
    if is_session_enabled(cfg) and not is_in_session(cfg):
        log.info(
            "SESSION: Startujeme MIMO trading session - "
            "recovery probehne pri prvni wake-up v live loopu."
        )
        sent_signals = set()
    else:
        # Standardni startup: session snapshot + pine recovery + blokace historickych vln
        from runtime.startup import run_full_startup_recovery

        sent_signals = run_full_startup_recovery(cfg)

    # ─── BOT_STOP / CRASH HANDLERS ────────────────────────
    _stop_logged = {"done": False}

    def _log_stop(reason: str):
        if _stop_logged["done"]:
            return
        _stop_logged["done"] = True
        try:
            log_event(cfg, "info", "BOT_STOP", reason=reason)
        except Exception:
            pass

    def _signal_handler(signum, frame):
        _log_stop("MANUAL_STOP")
        sys.exit(0)

    def _excepthook(exc_type, exc_value, exc_tb):
        _log_stop("CRASH")
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    signal.signal(signal.SIGINT, _signal_handler)   # Ctrl+C
    signal.signal(signal.SIGTERM, _signal_handler)  # kill
    sys.excepthook = _excepthook
    atexit.register(lambda: _log_stop("SHUTDOWN"))

    ensure_telemetry_sync_running(log, cfg)

    # 5) Live loop
    run_live_loop(cfg, sent_signals, json_log_file=json_log_file)
    stop_telemetry_sync()
    shutdown()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        from core.logging_utils import log_event
        log_event(
            ACTIVE_CFG,
            "info",
            "BOT_STOP",
            reason="KeyboardInterrupt",
        )
        shutdown()