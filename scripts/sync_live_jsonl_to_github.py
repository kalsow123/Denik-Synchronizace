from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import subprocess
import sys
import time
import atexit
from pathlib import Path


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _env(name: str, env_file: dict[str, str], default: str | None = None) -> str | None:
    return os.environ.get(name, env_file.get(name, default))


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        text=True,
        capture_output=True,
        check=False,
    )


def _recover_stuck_git_rebase(repo: Path) -> None:
    """Uvolni telemetry repo zaseknute v rebase (typicky pri vice sync procesech najednou)."""
    git_dir = repo / ".git"
    for name in ("rebase-merge", "rebase-apply"):
        marker = git_dir / name
        if not marker.exists():
            continue
        abort = _run_git(repo, "rebase", "--abort")
        if abort.returncode != 0:
            import shutil

            shutil.rmtree(marker, ignore_errors=True)
        print(f"[sync] Git rebase obnoven (uvolnen {name})")


def _ensure_git_repo(repo: Path, branch: str) -> None:
    if not repo.exists():
        raise FileNotFoundError(f"TELEMETRY_REPO_PATH neexistuje: {repo}")
    chk = _run_git(repo, "rev-parse", "--is-inside-work-tree")
    if chk.returncode != 0:
        raise RuntimeError(f"Cesta neni git repozitar: {repo}\n{chk.stderr.strip()}")
    _recover_stuck_git_rebase(repo)
    _run_git(repo, "checkout", branch)


def _copy_if_changed(src: Path, dst: Path) -> bool:
    if not src.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and filecmp.cmp(src, dst, shallow=False):
        return False
    shutil.copy2(src, dst)
    return True


def _pull_rebase(repo: Path, branch: str) -> None:
    _recover_stuck_git_rebase(repo)
    fetch = _run_git(repo, "fetch", "origin", branch)
    if fetch.returncode != 0:
        raise RuntimeError(f"git fetch selhal:\n{fetch.stderr.strip()}")
    pull = _run_git(repo, "pull", "--rebase", "origin", branch)
    if pull.returncode != 0:
        _recover_stuck_git_rebase(repo)
        pull = _run_git(repo, "pull", "--rebase", "origin", branch)
        if pull.returncode != 0:
            raise RuntimeError(f"git pull --rebase selhal:\n{pull.stderr.strip()}")


def _commit_and_push(repo: Path, files_rel: list[str], branch: str, bot_id: str) -> bool:
    if not files_rel:
        return False
    add = _run_git(repo, "add", "--", *files_rel)
    if add.returncode != 0:
        raise RuntimeError(f"git add selhal:\n{add.stderr.strip()}")

    diff = _run_git(repo, "diff", "--cached", "--quiet")
    if diff.returncode == 0:
        return False

    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    message = f"telemetry: {bot_id} update {ts}"
    commit = _run_git(repo, "commit", "-m", message)
    if commit.returncode != 0:
        raise RuntimeError(f"git commit selhal:\n{commit.stderr.strip()}")

    push = _run_git(repo, "push", "origin", branch)
    if push.returncode != 0:
        _pull_rebase(repo, branch)
        push = _run_git(repo, "push", "origin", branch)
        if push.returncode != 0:
            raise RuntimeError(f"git push selhal po rebase:\n{push.stderr.strip()}")
    return True


def _acquire_sync_lock(lock_path: Path) -> bool:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            atexit.register(lambda: lock_path.unlink(missing_ok=True))
            return True
        except FileExistsError:
            try:
                pid = int(lock_path.read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                pid = -1
            if pid > 0:
                alive = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", f"Get-Process -Id {pid} -ErrorAction SilentlyContinue"],
                    capture_output=True,
                    check=False,
                )
                if alive.returncode == 0:
                    print(f"[sync] Jiny sync proces uz bezi (PID {pid}), koncim.")
                    return False
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                print(f"[sync] Nelze ziskat lock: {lock_path}")
                return False
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto-sync live_bot.jsonl do telemetry git repa.")
    parser.add_argument("--env-file", default=".env.sync", help="Cesta k env souboru (default: .env.sync).")
    parser.add_argument("--once", action="store_true", help="Provede jen jeden synchronizacni cyklus a skonci.")
    args = parser.parse_args()

    env_file = _load_env_file(Path(args.env_file))

    source_jsonl = _env("SOURCE_JSONL_PATH", env_file)
    source_config_jsonl = _env("SOURCE_CONFIG_JSONL_PATH", env_file)
    telemetry_repo = _env("TELEMETRY_REPO_PATH", env_file)
    bot_id = _env("BOT_ID", env_file)
    branch = _env("TARGET_BRANCH", env_file, "main")
    interval_sec = int(_env("SYNC_INTERVAL_SEC", env_file, "60") or "60")
    poll_raw = _env("SYNC_POLL_SEC", env_file)
    if poll_raw is None or str(poll_raw).strip() == "":
        poll_sec = float(interval_sec)
    else:
        poll_sec = float(poll_raw)
    poll_sec = max(0.15, poll_sec)
    config_interval_raw = _env("SYNC_CONFIG_JSONL_INTERVAL_SEC", env_file)
    if config_interval_raw is None or str(config_interval_raw).strip() == "":
        config_interval_sec = -1
    else:
        config_interval_sec = int(config_interval_raw)

    missing = [k for k, v in {
        "TELEMETRY_REPO_PATH": telemetry_repo,
        "BOT_ID": bot_id,
    }.items() if not v]
    if not source_jsonl and not source_config_jsonl:
        missing.append("SOURCE_JSONL_PATH or SOURCE_CONFIG_JSONL_PATH")
    if missing:
        print(f"Chybi povinne promenne: {', '.join(missing)}")
        return 2

    repo = Path(str(telemetry_repo)).resolve()
    bot_name = str(bot_id).strip()
    sources: list[tuple[Path, Path]] = []
    if source_jsonl:
        src_live = Path(str(source_jsonl)).resolve()
        rel_live = Path("logs") / bot_name / "live_bot.jsonl"
        sources.append((src_live, rel_live))
    if source_config_jsonl:
        src_cfg = Path(str(source_config_jsonl)).resolve()
        rel_cfg = Path("logs") / bot_name / "bot_config.jsonl"
        sources.append((src_cfg, rel_cfg))

    try:
        _ensure_git_repo(repo, str(branch))
    except Exception as exc:
        print(f"Init chyba: {exc}")
        return 3

    lock_path = Path(args.env_file).resolve().parent / "locks" / "telemetry_sync.lock"
    if not _acquire_sync_lock(lock_path):
        return 0

    for src, rel_target in sources:
        print(f"[sync] Source: {src}")
        print(f"[sync] Target: {rel_target.as_posix()} (branch={branch})")
    print(f"[sync] Repo:   {repo}")
    print(f"[sync] Poll (kontrola zmen na disku): {poll_sec}s")
    if source_config_jsonl:
        if config_interval_sec < 0:
            print("[sync] Config JSONL: jen pri zmene souboru (mtime)")
        elif config_interval_sec > 0:
            print(f"[sync] Config JSONL max 1x za: {config_interval_sec}s (jinak kazdy poll)")

    last_config_sync_mono = -float("inf")
    config_mtime_seen: dict[str, float] = {}

    while True:
        try:
            changed_files: list[str] = []
            now_mono = time.monotonic()
            for src, rel_target in sources:
                is_bot_config_dst = rel_target.name == "bot_config.jsonl"
                if is_bot_config_dst and source_config_jsonl:
                    if config_interval_sec < 0:
                        try:
                            src_mtime = src.stat().st_mtime
                        except OSError:
                            src_mtime = None
                        prev_mtime = config_mtime_seen.get(str(src))
                        if prev_mtime is not None and src_mtime == prev_mtime:
                            continue
                    elif (
                        config_interval_sec > 0
                        and (now_mono - last_config_sync_mono) < float(config_interval_sec)
                    ):
                        continue
                dst = repo / rel_target
                if _copy_if_changed(src, dst):
                    changed_files.append(rel_target.as_posix())
                if is_bot_config_dst and source_config_jsonl:
                    if config_interval_sec < 0:
                        try:
                            config_mtime_seen[str(src)] = src.stat().st_mtime
                        except OSError:
                            pass
                    elif config_interval_sec > 0:
                        last_config_sync_mono = now_mono
            if changed_files:
                pushed = _commit_and_push(repo, changed_files, str(branch), bot_name)
                if pushed:
                    print(f"[sync] Pushed: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            if args.once:
                return 0
        except Exception as exc:
            print(f"[sync] Chyba: {exc}")
            if args.once:
                return 1
        time.sleep(poll_sec)


if __name__ == "__main__":
    sys.exit(main())
