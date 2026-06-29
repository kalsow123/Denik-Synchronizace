"""Autostart a dohled nad sync live JSONL -> telemetry git repo."""
from __future__ import annotations

import atexit
import os
import re
import subprocess
import sys
from pathlib import Path

from config.bot_config import BotConfig

_sync_proc: subprocess.Popen | None = None
_atexit_registered = False


def _safe_bot_name(cfg: BotConfig) -> str:
    return re.sub(r"[^A-Za-z0-9._=-]+", "_", cfg.bot_name).strip("._-") or "bot"


def _load_env_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def _parse_env(lines: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def update_env_sync_from_config(cfg: BotConfig, root: Path) -> Path | None:
    """
    Aktualizuje SOURCE_* a BOT_ID v .env.sync podle aktualniho live configu.
    TELEMETRY_REPO_PATH a dalsi existujici klice zachova.
    """
    env_path = root / ".env.sync"
    example_path = root / ".env.sync.example"
    if not env_path.is_file():
        if example_path.is_file():
            env_path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            return None

    bot_name = _safe_bot_name(cfg)
    updates = {
        "SOURCE_JSONL_PATH": str((root / f"{bot_name}.jsonl").resolve()),
        "SOURCE_CONFIG_JSONL_PATH": str((root / f"{bot_name}_config.jsonl").resolve()),
        "BOT_ID": bot_name,
    }

    lines = _load_env_lines(env_path)
    seen: set[str] = set()
    new_lines: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        new_lines.append(raw)

    for key, value in updates.items():
        if key not in seen:
            new_lines.append(f"{key}={value}")

    env_path.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")
    return env_path


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _terminate_process(pid: int) -> None:
    if not _pid_alive(pid):
        return
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    except Exception:
        pass


def _remove_stale_lock(lock_path: Path, keep_pid: int | None) -> None:
    if not lock_path.is_file():
        return
    try:
        lock_pid = int(lock_path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        lock_pid = -1
    if lock_pid == keep_pid or not _pid_alive(lock_pid):
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def terminate_stale_sync_processes(root: Path, *, keep_pid: int | None = None) -> list[int]:
    """Ukonci osirele sync podprocesy pro tuto Denik instanci (po restartu bota)."""
    root_key = str(root.resolve()).lower()
    script_key = "sync_live_jsonl_to_github.py"
    terminated: list[int] = []

    try:
        if sys.platform == "win32":
            proc = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                    "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            import json

            raw = (proc.stdout or "").strip()
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict):
                    data = [data]
                for item in data:
                    pid = int(item.get("ProcessId", 0))
                    cmd = str(item.get("CommandLine", "")).lower()
                    if (
                        pid
                        and pid != keep_pid
                        and script_key in cmd
                        and root_key in cmd
                    ):
                        _terminate_process(pid)
                        terminated.append(pid)
        else:
            out = subprocess.run(
                ["ps", "-eo", "pid,args"],
                capture_output=True,
                text=True,
                check=False,
            )
            for line in (out.stdout or "").splitlines()[1:]:
                parts = line.strip().split(None, 1)
                if len(parts) != 2:
                    continue
                pid_s, cmd = parts
                if script_key in cmd and root_key in cmd.lower():
                    pid = int(pid_s)
                    if pid != keep_pid:
                        _terminate_process(pid)
                        terminated.append(pid)
    except Exception:
        pass

    _remove_stale_lock(root / "locks" / "telemetry_sync.lock", keep_pid)

    env_path = root / ".env.sync"
    if env_path.is_file():
        env = _parse_env(_load_env_lines(env_path))
        telemetry_repo = env.get("TELEMETRY_REPO_PATH", "").strip()
        if telemetry_repo:
            repo_lock = Path(telemetry_repo).resolve() / ".telemetry_sync.lock"
            _remove_stale_lock(repo_lock, keep_pid)

    return terminated


def stop_telemetry_sync() -> None:
    global _sync_proc
    if _sync_proc is None:
        return
    if _sync_proc.poll() is None:
        _sync_proc.terminate()
        try:
            _sync_proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            _sync_proc.kill()
    _sync_proc = None


def ensure_telemetry_sync_running(log, cfg: BotConfig) -> None:
    """Spusti sync pri startu bota; pri padu podprocesu ho znovu nastartuje."""
    global _sync_proc, _atexit_registered

    if os.environ.get("DISABLE_TELEMETRY_SYNC", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        log.info("TELEMETRY_SYNC: vypnuto (DISABLE_TELEMETRY_SYNC)")
        return

    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "sync_live_jsonl_to_github.py"
    if not script.is_file():
        log.warning("TELEMETRY_SYNC: scripts/sync_live_jsonl_to_github.py nenalezen")
        return

    env_path = update_env_sync_from_config(cfg, root)
    if env_path is None:
        log.info("TELEMETRY_SYNC: .env.sync chybi — autostart preskocen")
        return

    if _sync_proc is not None and _sync_proc.poll() is None:
        return

    if _sync_proc is not None and _sync_proc.poll() is not None:
        log.warning(
            "TELEMETRY_SYNC: podproces skoncil (exit=%s), restartuji",
            _sync_proc.returncode,
        )
        _sync_proc = None

    stale = terminate_stale_sync_processes(root)
    if stale:
        log.info("TELEMETRY_SYNC: ukonceno %d starych sync procesu: %s", len(stale), stale)

    log_path = root / "telemetry_sync.log"
    log_handle = open(log_path, "a", encoding="utf-8")

    popen_kw: dict = {
        "args": [sys.executable, "-u", str(script), "--env-file", str(env_path)],
        "cwd": str(root),
        "stdout": log_handle,
        "stderr": log_handle,
    }
    if sys.platform == "win32":
        popen_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        _sync_proc = subprocess.Popen(**popen_kw)
        log.info(
            "TELEMETRY_SYNC: spusten podproces PID=%s (log: %s)",
            _sync_proc.pid,
            log_path.name,
        )
        if not _atexit_registered:
            atexit.register(stop_telemetry_sync)
            _atexit_registered = True
    except Exception as exc:
        log.warning("TELEMETRY_SYNC: nepodařilo se spustit: %s", exc)
        try:
            log_handle.close()
        except Exception:
            pass
