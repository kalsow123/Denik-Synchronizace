
import logging
import sys
from pathlib import Path

import MetaTrader5 as mt5

from config.bot_config import BotConfig
from core.logging_utils import log_event
from mt5_credentials import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH

# ───── MT5 CONNECTION ──────────────────────────

log = logging.getLogger(__name__)


def _normalize_terminal_dir(path: Path | str) -> Path:
    """Srovna cestu k MT5 instalaci (slozka s terminal64.exe)."""
    p = Path(path).resolve()
    if p.name.lower() in ("terminal64.exe", "terminal.exe"):
        return p.parent
    return p


def verify_mt5_session() -> tuple[bool, str, dict]:
    """
    Overi, ze Python API je pripojene k dedikovanemu terminalu a uctu z credentials.
    Vraci (ok, reason, details) — details je vhodny pro log_event.
    """
    ti = mt5.terminal_info()
    ai = mt5.account_info()
    if ti is None or ai is None:
        return False, "MT5 session neni aktivni", {}

    expected_dir = _normalize_terminal_dir(MT5_PATH)
    actual_dir = _normalize_terminal_dir(ti.path)
    details = {
        "expected_terminal": str(expected_dir),
        "actual_terminal": str(actual_dir),
        "expected_login": int(MT5_LOGIN),
        "actual_login": int(ai.login),
        "expected_server": str(MT5_SERVER),
        "actual_server": str(ai.server),
    }

    if actual_dir != expected_dir:
        return (
            False,
            f"Spatny MT5 terminal (ocekavan {expected_dir}, pripojen {actual_dir})",
            details,
        )

    if int(ai.login) != int(MT5_LOGIN):
        return (
            False,
            f"Spatny MT5 ucet (ocekavan {MT5_LOGIN}, pripojen {ai.login})",
            details,
        )

    if str(ai.server) != str(MT5_SERVER):
        return (
            False,
            f"Spatny MT5 server (ocekavan {MT5_SERVER}, pripojen {ai.server})",
            details,
        )

    return True, "", details


def enforce_mt5_session(cfg: BotConfig) -> None:
    """Prubezna pojistka — pri nesouladu okamzite ukonci live bota."""
    ok, reason, details = verify_mt5_session()
    if ok:
        return

    log_event(
        cfg,
        "critical",
        "MT5_SESSION_MISMATCH",
        reason=reason,
        **details,
        message="Live bot zastaven: MT5 terminal nebo ucet nesedi s mt5_credentials.py",
    )
    log.critical("MT5_SESSION_MISMATCH: %s | %s", reason, details)
    try:
        mt5.shutdown()
    except Exception:
        pass
    sys.exit(3)


# Inicializuje spojeni s MT5 a zaloguje aktivni account mode (DEMO/LIVE).
def connect(cfg: BotConfig) -> bool:
    terminal_path = str(Path(MT5_PATH))
    if not Path(terminal_path).exists():
        log.error(f"MT5 terminal path neexistuje: {terminal_path}")
        return False

    if not mt5.initialize(
        path=terminal_path,
        login=MT5_LOGIN,
        password=MT5_PASSWORD,
        server=MT5_SERVER,
    ):
        log.error(f"MT5 initialize() selhal: {mt5.last_error()}")
        return False

    ok, reason, details = verify_mt5_session()
    if not ok:
        log_event(
            cfg,
            "error",
            "MT5_SESSION_MISMATCH",
            reason=reason,
            **details,
            message="Pripojeni odmitnuto: MT5 terminal nebo ucet nesedi s mt5_credentials.py",
        )
        log.error("MT5_SESSION_MISMATCH pri startu: %s | %s", reason, details)
        mt5.shutdown()
        return False

    info = mt5.account_info()
    if info is None:
        log.error("MT5 account_info() vratilo None po uspesnem verify")
        mt5.shutdown()
        return False

    trade_mode = "DEMO" if info.trade_mode == 0 else "LIVE"
    log_event(
        cfg,
        "info",
        "MT5_CONNECTED",
        account=info.login,
        server=info.server,
        trade_mode=trade_mode,
        terminal=str(_normalize_terminal_dir(MT5_PATH)),
    )

    return True


def shutdown() -> None:
    mt5.shutdown()
