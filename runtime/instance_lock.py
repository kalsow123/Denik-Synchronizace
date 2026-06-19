"""
Zámek proti souběžnému spuštění live bota (stejný config: bot_name + symbol + magic).

Pouze live (`main.py`). Backtester tento modul nepoužívá.
"""
from __future__ import annotations

import atexit
import os
import re
import sys
from pathlib import Path

from config.bot_config import BotConfig


class LiveInstanceAlreadyRunning(RuntimeError):
    """Druhá instance live bota se stejným config profilem už běží."""


def _lock_path(cfg: BotConfig) -> Path:
    safe_name = re.sub(r"[^A-Za-z0-9._=-]+", "_", cfg.bot_name).strip("._-") or "bot"
    symbol = re.sub(r"[^A-Za-z0-9._-]+", "_", str(cfg.symbol)).strip("._-") or "sym"
    return Path("locks") / f"{safe_name}_{symbol}_{int(cfg.magic)}.lock"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class LiveInstanceLock:
    """Exkluzivní lock soubor — jedna live instance na config."""

    def __init__(self, cfg: BotConfig) -> None:
        self._path = _lock_path(cfg)
        self._fd: int | None = None
        self._held = False

    def acquire(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                self._fd = os.open(
                    self._path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
                os.write(self._fd, str(os.getpid()).encode("ascii"))
                self._held = True
                atexit.register(self.release)
                return
            except FileExistsError:
                pid = self._read_stored_pid()
                if pid is not None and _pid_alive(pid):
                    raise LiveInstanceAlreadyRunning(
                        f"Live bot už běží (PID {pid}). Lock: {self._path.resolve()}"
                    )
                try:
                    self._path.unlink(missing_ok=True)
                except OSError as exc:
                    raise LiveInstanceAlreadyRunning(
                        f"Nelze získat lock (soubor obsazený?): {self._path.resolve()}"
                    ) from exc
        raise LiveInstanceAlreadyRunning(
            f"Nelze získat lock po opakovaném pokusu: {self._path.resolve()}"
        )

    def _read_stored_pid(self) -> int | None:
        try:
            return int(self._path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return None

    def release(self) -> None:
        if not self._held:
            return
        try:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            self._path.unlink(missing_ok=True)
        except OSError:
            pass
        self._held = False


def ensure_single_live_instance(cfg: BotConfig) -> LiveInstanceLock:
    lock = LiveInstanceLock(cfg)
    lock.acquire()
    return lock
