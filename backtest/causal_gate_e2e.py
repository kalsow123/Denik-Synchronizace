"""
Causal gate + live E2E parity — orchestrátor BT vs live replay cesty.

Spuštění: run_e2e_parity=True v BotConfig nebo --e2e v run_backtest.
E2E config = resolve_live_execution_config() bez override (parita s main.py / deploy).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from backtest.causal_policy import causal_debug_summary, policy_from_cfg
from backtest.engine import BacktestEngine
from backtest.sim_params import DEFAULT_BACKTEST_SPREAD
from backtest.stats import classify_position_kind
from config.bot_config import BotConfig


def _wave_only_trades(trades: list) -> list:
    out = []
    for t in trades:
        kind = classify_position_kind(
            is_pp=bool(getattr(t, "is_pp", False)),
            is_counter=bool(getattr(t, "is_counter", False)),
            is_bos_reentry=bool(getattr(t, "is_bos_reentry", False)),
            is_two_sided_mirror=bool(getattr(t, "is_two_sided_mirror", False)),
            is_ext=bool(getattr(t, "is_ext", False)),
            entry_tag=str(getattr(t, "entry_tag", "base")),
        )
        if kind == "WAVE":
            out.append(t)
    return out


def _e2e_live_config(cfg: BotConfig) -> BotConfig:
    from dataclasses import fields, replace

    from runtime.live_wave_isolation import (
        live_wave_isolation_requested,
        resolve_live_execution_config,
    )

    # Backtester predava engine cfg (wave_isolation_study=False — grid parita).
    # Live deploy (main.py) cte registry se study=True; bez obnovy by E2E bezel
    # v rezimu wave_only misto wave_study_wave_only.
    seed = cfg
    if bool(getattr(cfg, "wave_positions_only", False)) and not live_wave_isolation_requested(cfg):
        names = {f.name for f in fields(BotConfig)}
        seed = replace(
            cfg,
            **{k: v for k, v in {"wave_isolation_study": True}.items() if k in names},
        )
    return resolve_live_execution_config(seed)


def _run_live_e2e(df: pd.DataFrame, cfg: BotConfig) -> list:
    from scripts.e2e_live_broker_sim import (
        filter_e2e_wave_closed,
        install_fake,
        run_e2e,
    )

    live_cfg = _e2e_live_config(cfg)
    fake = install_fake(live_cfg.symbol, float(live_cfg.contract_size))
    closed = run_e2e(df, live_cfg, fake)
    return filter_e2e_wave_closed(
        closed, live_cfg, promoted_waves=fake.promoted_waves,
    )


@dataclass
class CausalGateE2EResult:
    backtest_trades: list
    backtest_stats: dict
    live_e2e_closed: list
    live_e2e_stats: dict
    parity: dict


def run_e2e_parity_after_backtest(
    df: pd.DataFrame,
    cfg: BotConfig,
    *,
    backtest_trades: list,
    backtest_stats: dict,
    combo: dict | None = None,
) -> CausalGateE2EResult:
    """E2E + parity report po dokončeném backtestu (bez druhého BT běhu)."""
    from scripts.e2e_live_broker_sim import pnl_ddi_from_closed

    combo_cfg = dict(combo or backtest_stats.get("config") or {})
    date_from = combo_cfg.get("date_from")
    date_to = combo_cfg.get("date_to")

    live_closed = _run_live_e2e(df, cfg)
    lv_stats = pnl_ddi_from_closed(
        live_closed,
        bot_name=cfg.bot_name,
        date_from=str(date_from) if date_from else None,
        date_to=str(date_to) if date_to else None,
        combo=combo_cfg,
    )

    bt_wave = _wave_only_trades(backtest_trades)
    bt_wt = {str(getattr(t, "wave_time", "")) for t in bt_wave}
    lv_wt = {str(getattr(t, "wave_time", "")) for t in live_closed}
    causal_dbg = {
        k: v for k, v in backtest_stats.items()
        if str(k).startswith("causal_") or k == "causal_mode"
    }
    if not causal_dbg:
        causal_dbg = causal_debug_summary(policy_from_cfg(cfg))
    parity = {
        "backtest_wave_count": len(bt_wave),
        "live_e2e_wave_count": len(live_closed),
        "common_wave_times": len(bt_wt & lv_wt),
        "bt_only_wave_times": len(bt_wt - lv_wt),
        "lv_only_wave_times": len(lv_wt - bt_wt),
        "backtest_net_pnl_usd": float(backtest_stats.get("net_pnl_usd", 0) or 0),
        "live_e2e_net_pnl_usd": float(lv_stats.get("net_pnl_usd", 0) or 0),
        "backtest_win_rate_pct": float(backtest_stats.get("win_rate_pct", 0) or 0),
        "live_e2e_win_rate_pct": float(lv_stats.get("win_rate_pct", 0) or 0),
        "causal_debug": causal_dbg,
    }
    return CausalGateE2EResult(
        backtest_trades=backtest_trades,
        backtest_stats=backtest_stats,
        live_e2e_closed=live_closed,
        live_e2e_stats=lv_stats,
        parity=parity,
    )


def run_causal_gate_e2e(
    df: pd.DataFrame,
    cfg: BotConfig,
    *,
    spread: float = DEFAULT_BACKTEST_SPREAD,
    retain_wave_snapshot: bool = False,
) -> CausalGateE2EResult:
    """Backtest (causal dle cfg.causal_mode) + E2E fake broker + parity souhrn."""
    from backtest.stats import compute_stats, trades_to_df
    from scripts.e2e_live_broker_sim import pnl_ddi_from_closed

    engine = BacktestEngine(cfg)
    bt_trades = engine.run(df, retain_wave_snapshot=retain_wave_snapshot)
    bt_df = trades_to_df(bt_trades)
    bt_stats = compute_stats(bt_df) if not bt_df.empty else {"error": "no trades"}
    bt_stats.update(engine.get_run_info())

    live_closed = _run_live_e2e(df, cfg)
    lv_stats = pnl_ddi_from_closed(live_closed, bot_name=cfg.bot_name)

    bt_wave = _wave_only_trades(bt_trades)
    bt_wt = {str(getattr(t, "wave_time", "")) for t in bt_wave}
    lv_wt = {str(getattr(t, "wave_time", "")) for t in live_closed}
    common = bt_wt & lv_wt
    bt_only = bt_wt - lv_wt
    lv_only = lv_wt - bt_wt

    parity = {
        "backtest_wave_count": len(bt_wave),
        "live_e2e_wave_count": len(live_closed),
        "common_wave_times": len(common),
        "bt_only_wave_times": len(bt_only),
        "lv_only_wave_times": len(lv_only),
        "backtest_net_pnl_usd": float(bt_stats.get("net_pnl_usd", 0) or 0),
        "live_e2e_net_pnl_usd": float(lv_stats.get("net_pnl_usd", 0) or 0),
        "backtest_win_rate_pct": float(bt_stats.get("win_rate_pct", 0) or 0),
        "live_e2e_win_rate_pct": float(lv_stats.get("win_rate_pct", 0) or 0),
        "causal_debug": {
            k: v for k, v in bt_stats.items()
            if str(k).startswith("causal_") or k == "causal_mode"
        },
    }

    return CausalGateE2EResult(
        backtest_trades=bt_trades,
        backtest_stats=bt_stats,
        live_e2e_closed=live_closed,
        live_e2e_stats=lv_stats,
        parity=parity,
    )


def _format_stats_suffix(stats: dict) -> str:
    parts: list[str] = []
    wr = stats.get("win_rate_pct")
    if wr is not None:
        parts.append(f"WR={float(wr):.1f}%")
    prof = stats.get("ddi_profile") or {}
    max_dd = stats.get("max_drawdown_pct")
    max_ddi = prof.get("max_ddi_pct")
    p90 = prof.get("p90_ddi_pct")
    if max_dd is not None:
        parts.append(f"max_dd_%_vs_initial={float(max_dd):.2f}%")
    if max_ddi is not None:
        parts.append(f"max_ddi={float(max_ddi):.2f}%")
    if p90 is not None:
        parts.append(f"p90_ddi={float(p90):.2f}%")
    return "  " + "  ".join(parts) if parts else ""


def print_causal_gate_e2e_report(result: CausalGateE2EResult) -> None:
    p = result.parity
    print("\n=== CAUSAL GATE + E2E PARITY ===")
    print(
        f"  BACKTEST WAVE: {p['backtest_wave_count']} / "
        f"{p['backtest_net_pnl_usd']:.2f} USD"
        f"{_format_stats_suffix(result.backtest_stats)}"
    )
    print(
        f"  LIVE E2E WAVE: {p['live_e2e_wave_count']} / "
        f"{p['live_e2e_net_pnl_usd']:.2f} USD"
        f"{_format_stats_suffix(result.live_e2e_stats)}"
    )
    print(
        f"  common={p['common_wave_times']}  BT-only={p['bt_only_wave_times']}  "
        f"LV-only={p['lv_only_wave_times']}"
    )
    dbg = p.get("causal_debug") or {}
    if dbg.get("causal_mode"):
        keys = [k for k in dbg if k != "causal_mode"]
        if keys:
            print("  causal counters:", ", ".join(f"{k}={dbg[k]}" for k in sorted(keys)))
