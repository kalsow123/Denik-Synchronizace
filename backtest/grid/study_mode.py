"""Rezim grid study — srovnatelnost WAVE vs plny beh."""
from __future__ import annotations

import pandas as pd

from config.position_modes import (
    grid_backtest_isolation_study,
    grid_is_wave_positions_only,
)

STUDY_PAIR_SKIP_KEYS = frozenset({
    "wave_counter_two_sided_enabled",
    "counter_position_enabled",
    "two_sided_entry_enabled",
    "wave_positions_only",
    "wave_isolation_study",
    "finish_variant",
    "source_combo_no",
    "bot_name",
    "_grid_test_pozice",
})


def _study_key_part(value) -> object:
    """Normalizace pro hashovatelný study_base_key (list/dict z BotConfig → tuple)."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, tuple):
        return tuple(_study_key_part(v) for v in value)
    if isinstance(value, list):
        return tuple(_study_key_part(v) for v in value)
    if isinstance(value, dict):
        return tuple(
            (str(k), _study_key_part(v))
            for k, v in sorted(value.items(), key=lambda item: str(item[0]))
        )
    return str(value)


def study_base_key(cfg: dict) -> tuple:
    """Klic pro parovani study radku s plnou kombinaci (stejne parametry, jiny rezim)."""
    return tuple(
        (k, _study_key_part(cfg[k]))
        for k in sorted(cfg)
        if not str(k).startswith("__") and k not in STUDY_PAIR_SKIP_KEYS
    )


def filter_trades_df_for_grid_stats(
    trades_df: pd.DataFrame,
    cfg: dict,
) -> pd.DataFrame:
    """Stejná sada obchodů jako grid_runner / xlsx (WAVE slice pro wave_isolation_study)."""
    if trades_df is None or trades_df.empty:
        return trades_df
    if grid_backtest_isolation_study(cfg) and "position_kind" in trades_df.columns:
        return trades_df[trades_df["position_kind"] == "WAVE"].copy()
    return trades_df


def apply_wave_isolation_report_stats(stats: dict, cfg: dict | None = None) -> dict:
    """
    Report pro wave_isolation_study: zobraz jen WAVE slice.
    Engine bezi plne (counter routing pro shodne WAVE obchody); v Excelu jsou
    counter/two_sided/pp/ext/bos sloupce pro study radky nulove.
    """
    if cfg is not None and not grid_backtest_isolation_study(cfg):
        return stats
    if cfg is None and not stats.get("wave_isolation_study"):
        return stats

    out = dict(stats)
    zero_int = (
        "trades_wave_counter",
        "trades_wave_two_sided",
        "trades_pp",
        "trades_ext",
        "trades_bos",
        "trades_ext_bos",
    )
    zero_float = (
        "net_pnl_wave_counter_usd",
        "net_pnl_wave_two_sided_usd",
        "net_pnl_pp_usd",
        "net_pnl_ext_usd",
        "net_pnl_bos_usd",
        "net_pnl_ext_bos_usd",
        "net_pnl_non_pp_usd",
        "max_drawdown_pct_wave_counter",
        "max_drawdown_pct_wave_two_sided",
        "max_drawdown_pct_pp",
        "max_drawdown_pct_ext",
        "max_drawdown_pct_bos",
        "max_drawdown_pct_ext_bos",
    )
    for k in zero_int:
        if k in out:
            out[k] = 0
    for k in zero_float:
        if k in out:
            out[k] = 0.0

    wave_trades = int(out.get("trades_wave", 0) or 0)
    wave_pnl = float(out.get("net_pnl_wave_usd", 0) or 0)
    out["total_trades"] = wave_trades
    out["net_pnl_usd"] = wave_pnl
    out["net_pnl_non_pp_usd"] = wave_pnl
    if wave_trades > 0:
        out["win_rate_pct"] = round(
            100.0 * int(out.get("wins", 0) or 0) / max(int(out.get("total_closes", wave_trades) or 1), 1),
            1,
        )
    return out


def resolve_study_mode(cfg: dict) -> str:
    """
    full — vsechny moduly dle configu.
    wave_target_n_sweep — bot_finish wave study (finish_variant + isolation).
    wave_isolation — jen WAVE v reportu; engine plna simulace (wave_isolation_study).
    wave_only — jen klasické WAVE bez isolation (jina strategie nez WAVE slice z full).
    """
    if cfg.get("finish_variant") in ("wave_only", "wave_pp"):
        return "wave_target_n_sweep"
    if grid_backtest_isolation_study(cfg):
        return "wave_isolation"
    if grid_is_wave_positions_only(cfg):
        return "wave_only"
    return "full"
