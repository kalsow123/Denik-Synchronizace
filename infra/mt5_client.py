
import logging
from pathlib import Path
import MetaTrader5 as mt5

from config.bot_config import BotConfig
from core.logging_utils import log_event
from mt5_credentials import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH

# ───── MT5 CONNECTION ──────────────────────────

log = logging.getLogger(__name__)


# Inidializuje spojeni s MT5 a zaloguje aktivni account mode (DEMO/LIVE).
def connect(cfg: BotConfig) -> bool:
    terminal_path = str(Path(MT5_PATH))
    if not Path(terminal_path).exists():
        log.error(f"MT5 terminal path neexistuje: {terminal_path}")
        return False

    if not mt5.initialize(path=terminal_path, login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
        log.error(f"MT5 initialize() selhal: {mt5.last_error()}")
        return False

    info = mt5.account_info()
    trade_mode = "DEMO" if info.trade_mode == 0 else "LIVE"
    log_event(
        cfg,
        "info",
        "MT5_CONNECTED",
        account=info.login,
        server=info.server,
        trade_mode=trade_mode,
    )

    return True

    # Ukončení MT5 spojení
def shutdown() -> None:
    mt5.shutdown()
