"""Denní inkrement výstupních složek: results/{SYMBOL}/{run_name}_{YYYYMMDD}_{NNN}/."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

from config.bot_config import BotConfig

_SAFE_PART_RE = re.compile(r"[^A-Za-z0-9._-]+")


def symbol_folder_name(symbol: str) -> str:
    """
    Název složky pro výsledky backtestu — bez koncovky poskytovatele likvidity.

    USDCAD.x, EURUSD.r, GER40.cash → USDCAD, EURUSD, GER40 (část za poslední tečkou se ignoruje).
    Symbol bez tečky (EU50p) zůstane beze změny.
    """
    s = str(symbol or "").strip()
    if not s:
        return "UNKNOWN"
    if "." in s:
        s = s.rsplit(".", 1)[0].strip()
    return safe_path_part(s) if s else "UNKNOWN"


def safe_path_part(value: str, *, max_len: int = 120) -> str:
    """Povolené znaky: [A-Za-z0-9._-], ostatní → _."""
    s = str(value or "").strip()
    if not s:
        return "_"
    out = _SAFE_PART_RE.sub("_", s)
    while "__" in out:
        out = out.replace("__", "_")
    out = out.strip("._-") or "_"
    if len(out) > max_len:
        out = out[:max_len].rstrip("._-") or "_"
    return out


def _next_increment_subdir(parent: Path, prefix: str) -> Path:
    """Vrátí parent/{prefix}{NNN}/ kde NNN = max existující + 1 (001, 002, …)."""
    parent.mkdir(parents=True, exist_ok=True)
    max_n = 0
    if parent.is_dir():
        for p in parent.iterdir():
            if not p.is_dir() or not p.name.startswith(prefix):
                continue
            suffix = p.name[len(prefix) :]
            if len(suffix) == 3 and suffix.isdigit():
                max_n = max(max_n, int(suffix))
    out = parent / f"{prefix}{max_n + 1:03d}"
    out.mkdir(parents=True, exist_ok=True)
    return out


def next_daily_output_dir(
    base_output: str | Path,
    symbol: str,
    run_name: str,
    *,
    run_date: datetime | None = None,
) -> Path:
    """
    Vytvoří {base_output}/{SYMBOL}/{run_name}_{YYYYMMDD}_{NNN}/.

    NNN = 001, 002, … pro stejný den + symbol + run_name (max existující suffix + 1).
    """
    base = Path(base_output)
    sym = symbol_folder_name(symbol)
    name = safe_path_part(run_name)
    day = (run_date or datetime.now()).strftime("%Y%m%d")
    parent = base / sym
    return _next_increment_subdir(parent, f"{name}_{day}_")


def _unique_ordered(values: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for v in values:
        t = str(v).strip()
        if t and t not in seen:
            seen.append(t)
    return seen


def grid_output_symbol(combos: list[dict]) -> str:
    """Jeden pár (bez .x/.r/.cash) → složka; více párů v gridu → MIXED."""
    syms = _unique_ordered(symbol_folder_name(c.get("symbol", "") or "") for c in combos)
    if not syms or syms == ["UNKNOWN"]:
        return "UNKNOWN"
    if len(syms) == 1:
        return syms[0]
    return "MIXED"


def grid_output_timeframe(combos: list[dict]) -> str:
    """Jeden TF → M15, H1, …; více → MIXED_TF."""
    tfs = _unique_ordered(c.get("timeframe", "") or "" for c in combos)
    if not tfs:
        return "UNKNOWN"
    if len(tfs) == 1:
        return safe_path_part(tfs[0])
    return "MIXED_TF"


def _date_range_label(value: str | None) -> str:
    """YYYY-MM-DD pro název složky; prázdné → UNKNOWN."""
    if value is None:
        return "UNKNOWN"
    s = str(value).strip()[:10]
    return safe_path_part(s) if s else "UNKNOWN"


def grid_output_date_range(combos: list[dict]) -> tuple[str, str]:
    """
    Jedno testované období → (date_from, date_to) jako YYYY-MM-DD;
    více různých hodnot v gridu → (MIXED, MIXED).
    """
    dfs = _unique_ordered(
        _date_range_label(c.get("date_from")) for c in combos if c.get("date_from")
    )
    dts = _unique_ordered(
        _date_range_label(c.get("date_to")) for c in combos if c.get("date_to")
    )
    if not dfs or not dts:
        return "UNKNOWN", "UNKNOWN"
    if len(dfs) == 1 and len(dts) == 1:
        return dfs[0], dts[0]
    return "MIXED", "MIXED"


def run_name_live_match(cfg: BotConfig) -> str:
    return f"live_match_{safe_path_part(cfg.bot_name)}_{safe_path_part(cfg.timeframe_label)}"


def run_name_compare(configs: list[BotConfig]) -> str:
    tfs = _unique_ordered(c.timeframe_label for c in configs)
    tf = safe_path_part(tfs[0]) if len(tfs) == 1 else "MIXED_TF"
    return f"compare_{tf}"


def run_name_grid(grid_profile: str, combos: list[dict]) -> str:
    return f"grid_{safe_path_part(grid_profile)}_{grid_output_timeframe(combos)}"


def grid_run_output_dir(
    base_output: str | Path,
    grid_profile: str,
    combos: list[dict],
) -> Path:
    """
    Výstup gridu: {base_output}/{SYMBOL}/grid_{profil}_{TF}_{date_from}_{date_to}_{NNN}/.

    NNN = 001, 002, … pro stejný profil + TF + testované období (date_from/date_to z kombinací).
    Příklad: grid_full_grid_M30_2025-04-24_2026-04-24_002
    """
    base = Path(base_output)
    sym = grid_output_symbol(combos)
    name = run_name_grid(grid_profile, combos)
    date_from, date_to = grid_output_date_range(combos)
    parent = base / sym
    prefix = f"{name}_{date_from}_{date_to}_"
    return _next_increment_subdir(parent, prefix)


def output_symbol_for_config(cfg: BotConfig) -> str:
    return symbol_folder_name(cfg.symbol)


def output_symbol_for_configs(configs: list[BotConfig]) -> str:
    syms = _unique_ordered(symbol_folder_name(c.symbol) for c in configs)
    if not syms or syms == ["UNKNOWN"]:
        return "UNKNOWN"
    if len(syms) == 1:
        return syms[0]
    return "MIXED"
