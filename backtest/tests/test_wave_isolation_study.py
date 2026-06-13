"""wave_isolation_study — stejne WAVE obchody jako plny beh bez counter orderu."""
from __future__ import annotations

import copy

import pytest

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.data_cache import clear_cache, load_data
from backtest.grid.study_mode import resolve_study_mode
from backtest.grid.translator import grid_dict_to_bot_config
from backtest.stats import classify_position_kind, compute_stats, trades_to_df
from config.bot_config import BotConfig
from strategy.two_sided import (
    skip_primary_entry_on_parent_wave,
    wave_counter_two_sided_orders_enabled,
    wave_counter_two_sided_routing_enabled,
    wave_isolation_study_enabled,
)


def _down_wave(move_pct: float = 0.7) -> dict:
    return {
        "dir": -1,
        "box_top": 1.1000,
        "box_bottom": 1.0900,
        "fib50": 1.0950,
        "sl": 1.0980,
        "tp": 1.0890,
        "move_pct": move_pct,
        "wave_time": "202603121800",
        "draw_left": 10,
        "draw_right": 30,
    }


class _FakeTrend:
    def __init__(self, direction: str):
        self.direction = direction


def test_isolation_helpers():
    full = BotConfig(
        wave_counter_two_sided_enabled=True,
        skip_primary_entry_on_parent_wave_enable=True,
    )
    iso = BotConfig(
        wave_counter_two_sided_enabled=False,
        wave_isolation_study=True,
        skip_primary_entry_on_parent_wave_enable=True,
    )
    rerun = BotConfig(wave_counter_two_sided_enabled=False, wave_isolation_study=False)

    assert wave_counter_two_sided_routing_enabled(full)
    assert wave_counter_two_sided_orders_enabled(full)
    assert wave_counter_two_sided_routing_enabled(iso)
    assert not wave_counter_two_sided_orders_enabled(iso)
    assert not wave_counter_two_sided_routing_enabled(rerun)

    assert skip_primary_entry_on_parent_wave(
        _down_wave(0.7), iso, trend_state=_FakeTrend("bear")
    )
    assert not skip_primary_entry_on_parent_wave(_down_wave(0.7), rerun)


def test_study_mode_labels():
    assert resolve_study_mode({"wave_counter_two_sided_enabled": True}) == "full"
    assert (
        resolve_study_mode(
            {
                "wave_isolation_study": True,
                "finish_variant": "wave_only",
            }
        )
        == "wave_target_n_sweep"
    )
    assert (
        resolve_study_mode(
            {"wave_isolation_study": True, "wave_counter_two_sided_enabled": False}
        )
        == "wave_isolation"
    )
    assert (
        resolve_study_mode(
            {
                "wave_positions_only": True,
                "wave_counter_two_sided_enabled": False,
            }
        )
        == "wave_only"
    )


def _wave_trade_signature(trades_df):
    if trades_df.empty:
        return []
    rows = []
    for _, r in trades_df.sort_values(["entry_time", "close_time"]).iterrows():
        kind = r.get("position_kind")
        if kind is None:
            kind = classify_position_kind(
                is_pp=bool(r.get("is_pp", False)),
                is_counter=bool(r.get("is_counter", False)),
                is_bos_reentry=bool(r.get("is_bos_reentry", False)),
                is_two_sided_mirror=bool(r.get("is_two_sided_mirror", False)),
                is_ext=bool(r.get("is_ext", False)),
                entry_tag=str(r.get("entry_tag", "base")),
            )
        if kind != "WAVE":
            continue
        rows.append(
            (
                str(r["entry_time"]),
                str(r["close_time"]),
                round(float(r["pnl_usd"]), 4),
                str(r.get("close_reason", "")),
            )
        )
    return rows


def _run_combo_stats(combo: dict) -> dict:
    cfg = grid_dict_to_bot_config(combo)
    df = load_data(
        combo["symbol"],
        combo["timeframe"],
        combo.get("date_from"),
        combo.get("date_to"),
    )
    trades = BacktestEngine(cfg).run(df)
    tdf = trades_to_df(trades)
    stats = compute_stats(tdf)
    return {
        "stats": stats,
        "wave_sig": _wave_trade_signature(tdf),
    }


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_cache()
    yield
    clear_cache()


def _pick_full_combo() -> dict:
    combos = generate_combinations(get_profile("EXAMPLE"))
    for c in combos:
        if (
            c.get("wave_counter_two_sided_enabled")
            and c.get("skip_primary_entry_on_parent_wave_enable")
            and c.get("tp_mode") == "bos_exit"
            and not c.get("pp_enabled")
        ):
            return c
    pytest.skip("EXAMPLE combo s counter+skip_primary nenalezena")


def test_wave_isolation_matches_full_wave_slice():
    full = _pick_full_combo()
    iso = copy.deepcopy(full)
    iso["wave_positions_only"] = True
    iso["wave_counter_two_sided_enabled"] = False
    iso["wave_isolation_study"] = True

    full_r = _run_combo_stats(full)
    iso_r = _run_combo_stats(iso)

    assert iso["wave_isolation_study"] is True
    assert iso["wave_counter_two_sided_enabled"] is False
    assert full_r["stats"]["trades_wave"] == iso_r["stats"]["trades_wave"]
    assert full_r["stats"]["net_pnl_wave_usd"] == iso_r["stats"]["net_pnl_wave_usd"]
    assert full_r["wave_sig"] == iso_r["wave_sig"]
    assert (
        full_r["stats"]["trades_wave_counter"] == iso_r["stats"]["trades_wave_counter"]
    )


def test_wave_only_rerun_differs_when_skip_primary_on():
    full = _pick_full_combo()
    rerun = copy.deepcopy(full)
    rerun["wave_counter_two_sided_enabled"] = False
    rerun["wave_isolation_study"] = False

    full_r = _run_combo_stats(full)
    rerun_r = _run_combo_stats(rerun)

    assert (
        full_r["stats"]["trades_wave"] != rerun_r["stats"]["trades_wave"]
        or full_r["stats"]["net_pnl_wave_usd"] != rerun_r["stats"]["net_pnl_wave_usd"]
    )
