"""
Causal gate + live E2E parity — orchestrátor BT vs live replay cesty.

Spuštění: run_e2e_parity=True v BotConfig nebo --e2e v run_backtest.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from dataclasses import replace

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
    from runtime.live_wave_isolation import resolve_live_execution_config

    live_cfg = resolve_live_execution_config(cfg)
    return replace(
        live_cfg,
        live_study_two_sided_mirror_orders=True,
        live_study_promoted_two_sided_as_wave=True,
    )


def _run_live_e2e(df: pd.DataFrame, cfg: BotConfig) -> list:
    from scripts.e2e_live_broker_sim import install_fake, run_e2e

    live_cfg = _e2e_live_config(cfg)
    fake = install_fake(live_cfg.symbol, float(live_cfg.contract_size))
    closed = run_e2e(df, live_cfg, fake)
    return _wave_only_trades(closed)


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
) -> CausalGateE2EResult:
    """E2E + parity report po dokončeném backtestu (bez druhého BT běhu)."""
    from scripts.e2e_live_broker_sim import pnl_ddi_from_closed

    live_closed = _run_live_e2e(df, cfg)
    lv_stats = pnl_ddi_from_closed(live_closed, bot_name=cfg.bot_name)

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


def print_causal_gate_e2e_report(result: CausalGateE2EResult) -> None:
    p = result.parity
    print("\n=== CAUSAL GATE + E2E PARITY ===")
    print(f"  BACKTEST WAVE: {p['backtest_wave_count']} / {p['backtest_net_pnl_usd']:.2f} USD")
    print(f"  LIVE E2E WAVE: {p['live_e2e_wave_count']} / {p['live_e2e_net_pnl_usd']:.2f} USD")
    print(
        f"  common={p['common_wave_times']}  BT-only={p['bt_only_wave_times']}  "
        f"LV-only={p['lv_only_wave_times']}"
    )
    dbg = p.get("causal_debug") or {}
    if dbg.get("causal_mode"):
        keys = [k for k in dbg if k != "causal_mode"]
        if keys:
            print("  causal counters:", ", ".join(f"{k}={dbg[k]}" for k in sorted(keys)))
