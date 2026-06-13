from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
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


def _ensure_git_repo(repo: Path, branch: str) -> None:
    if not repo.exists():
        raise FileNotFoundError(f"TELEMETRY_REPO_PATH neexistuje: {repo}")
    chk = _run_git(repo, "rev-parse", "--is-inside-work-tree")
    if chk.returncode != 0:
        raise RuntimeError(f"Cesta neni git repozitar: {repo}\n{chk.stderr.strip()}")
    _run_git(repo, "checkout", branch)


def _copy_if_changed(src: Path, dst: Path) -> bool:
    if not src.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        src_stat = src.stat()
        dst_stat = dst.stat()
        if src_stat.st_size == dst_stat.st_size and int(src_stat.st_mtime) <= int(dst_stat.st_mtime):
            return False
    shutil.copy2(src, dst)
    return True


def _commit_and_push(repo: Path, file_rel: str, branch: str, bot_id: str) -> bool:
    add = _run_git(repo, "add", "--", file_rel)
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
        raise RuntimeError(f"git push selhal:\n{push.stderr.strip()}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto-sync live_bot.jsonl do telemetry git repa.")
    parser.add_argument("--env-file", default=".env.sync", help="Cesta k env souboru (default: .env.sync).")
    parser.add_argument("--once", action="store_true", help="Provede jen jeden synchronizacni cyklus a skonci.")
    args = parser.parse_args()

    env_file = _load_env_file(Path(args.env_file))

    source_jsonl = _env("SOURCE_JSONL_PATH", env_file)
    telemetry_repo = _env("TELEMETRY_REPO_PATH", env_file)
    bot_id = _env("BOT_ID", env_file)
    branch = _env("TARGET_BRANCH", env_file, "main")
    interval_sec = int(_env("SYNC_INTERVAL_SEC", env_file, "60") or "60")

    missing = [k for k, v in {
        "SOURCE_JSONL_PATH": source_jsonl,
        "TELEMETRY_REPO_PATH": telemetry_repo,
        "BOT_ID": bot_id,
    }.items() if not v]
    if missing:
        print(f"Chybi povinne promenne: {', '.join(missing)}")
        return 2

    src = Path(str(source_jsonl)).resolve()
    repo = Path(str(telemetry_repo)).resolve()
    bot_name = str(bot_id).strip()
    rel_target = Path("logs") / bot_name / "live_bot.jsonl"
    dst = repo / rel_target

    try:
        _ensure_git_repo(repo, str(branch))
    except Exception as exc:
        print(f"Init chyba: {exc}")
        return 3

    print(f"[sync] Source: {src}")
    print(f"[sync] Repo:   {repo}")
    print(f"[sync] Target: {rel_target.as_posix()} (branch={branch})")
    print(f"[sync] Interval: {interval_sec}s")

    while True:
        try:
            changed = _copy_if_changed(src, dst)
            if changed:
                pushed = _commit_and_push(repo, rel_target.as_posix(), str(branch), bot_name)
                if pushed:
                    print(f"[sync] Pushed: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            if args.once:
                return 0
        except Exception as exc:
            print(f"[sync] Chyba: {exc}")
            if args.once:
                return 1
        time.sleep(max(5, interval_sec))


if __name__ == "__main__":
    sys.exit(main())
