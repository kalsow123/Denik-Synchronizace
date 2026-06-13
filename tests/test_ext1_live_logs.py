"""
Ověření live logů EXT1 ochrany bez MT5 (mock) na reálném EXT1 → U2 scénáři z EURUSD CSV.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

if "MetaTrader5" not in sys.modules:
    _mt5_stub = MagicMock()
    _mt5_stub.POSITION_TYPE_BUY = 0
    _mt5_stub.POSITION_TYPE_SELL = 1
    _mt5_stub.TRADE_RETCODE_DONE = 10009
    sys.modules["MetaTrader5"] = _mt5_stub

import MetaTrader5 as mt5
import pytest

from config.bot_config import LIVE_BOT_CONFIG
from config.enums import TPMode
from infra.orders import (
    close_flip_follower_positions_on_bos,
    close_positions_by_direction,
)
from runtime.ext1_protect_live import maybe_rrr_fixed_better_exit_after_ext1_protect_end
from strategy.wave_detection import detect_waves
from strategy.wave_sequence import build_ext1_wave_times, compute_ext1_protection_bars


def _load_eurusd_slice():
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    df = df[(df["time"] >= "2026-03-03") & (df["time"] <= "2026-05-10")].reset_index(drop=True)
    return df


def _cfg_rrr_ext1_protect():
    return replace(
        LIVE_BOT_CONFIG,
        ext1_protect_positions_until_wave2=True,
        tp_mode=TPMode.RRR_FIXED,
    )


@pytest.fixture(scope="module")
def eurusd_ext1_u2_scenario():
    """
    Reálný EXT1 → U2 z EURUSD M30 (2026-03-03):
      EXT1 202603031300 @ bar 26 (idx=1)
      U2   202603031700 @ bar 34 (idx=2) → konec ochrany
      Ochrana: bary 26–33, přechod na baru 34.
    """
    df = _load_eurusd_slice()
    cfg = _cfg_rrr_ext1_protect()
    waves = detect_waves(df, cfg)
    per_bar = compute_ext1_protection_bars(df, waves, cfg)
    ext1_times = build_ext1_wave_times(waves)

    protected_bar = 28
    transition_bar = 34
    ext1_wt = "202603031300"
    u2_wt = "202603031700"

    assert ext1_wt in ext1_times, f"EXT1 {ext1_wt} chybí v {ext1_times}"
    assert per_bar[protected_bar], f"Bar {protected_bar} musí být v ochraně"
    assert per_bar[transition_bar - 1] and not per_bar[transition_bar], (
        f"Přechod ochrany očekáván na baru {transition_bar}"
    )

    u2 = next(w for w in waves if str(w["wave_time"]) == u2_wt)
    assert u2.get("index_in_trend") == 2, "U2 musí mít index_in_trend=2"

    return {
        "df": df,
        "cfg": cfg,
        "per_bar": per_bar,
        "ext1_times": ext1_times,
        "protected_bar": protected_bar,
        "transition_bar": transition_bar,
        "ext1_wt": ext1_wt,
        "u2_wt": u2_wt,
    }


    def test_ext1_protect_skip_close_on_real_ext1_u2_window(eurusd_ext1_u2_scenario):
        """BOS close během ochrany → log EXT1_PROTECT_SKIP_CLOSE, 0 zavřených pozic."""
        s = eurusd_ext1_u2_scenario
        cfg = s["cfg"]
        bar_idx = s["protected_bar"]
        pos = type(
            "P",
            (),
            {
                "ticket": 1,
                "magic": int(cfg.magic),
                "type": 1,  # SELL, aby odpovídal BEAR EXT vlně
                "volume": 0.01,
                "price_open": 1.12,
                "sl": 1.10,
                "tp": 0.0,
                "comment": "W202603031300",
            },
        )()

    with patch("infra.orders.log_event") as log_event, patch(
        "infra.orders.mt5.positions_get", return_value=[pos],
    ):
        closed = close_positions_by_direction(
            cfg,
            direction=+1,
            reason="BOS_EXIT",
            ext1_protection_per_bar=s["per_bar"],
            current_bar_idx=bar_idx,
        )

    assert closed == 0
    log_event.assert_called_once()
    assert log_event.call_args[0][2] == "EXT1_PROTECT_SKIP_CLOSE"
    assert log_event.call_args[1]["bar_idx"] == bar_idx
    assert log_event.call_args[1]["reason"] == "BOS_EXIT"


def test_ext1_protect_keeps_aligned_ext_counter_open_on_bos_flip(eurusd_ext1_u2_scenario):
    """ECT_ ve smeru noveho trendu se pri flipu NEZAVIRA (ani behem EXT1 ochrany)."""
    s = eurusd_ext1_u2_scenario
    cfg = s["cfg"]
    bar_idx = s["protected_bar"]
    # SELL ECT_ po bull→bear (broken_dir=+1, new_trend=SELL) — aligned, flip follower nema zavrit
    pos = type(
        "P",
        (),
        {
            "ticket": 99,
            "magic": int(cfg.magic),
            "type": 1,
            "volume": 0.01,
            "price_open": 1.12,
            "sl": 1.14,
            "tp": 0.0,
            "comment": "ECT_EXT1",
        },
    )()
    info = type("I", (), {"point": 0.00001, "digits": 5})()

    with patch("infra.orders.mt5.positions_get", return_value=[pos]), patch(
        "infra.orders.mt5.symbol_info", return_value=info,
    ), patch("infra.orders._close_mt5_position_market", return_value=True) as close_mkt:
        n = close_flip_follower_positions_on_bos(
            cfg,
            broken_dir=+1,
            bar_high=1.124,
            bar_low=1.110,
            reason="BOS_EXIT",
            ext1_protection_per_bar=s["per_bar"],
            current_bar_idx=bar_idx,
        )

    assert n == 0
    assert not close_mkt.called


def test_ext1_protect_allows_ext_counter_bos_close_broken_dir(eurusd_ext1_u2_scenario):
    """ECT_ ve broken_dir se zavre pres close_positions_by_direction i behem EXT1."""
    s = eurusd_ext1_u2_scenario
    cfg = s["cfg"]
    bar_idx = s["protected_bar"]
    pos = type(
        "P",
        (),
        {
            "ticket": 99,
            "magic": int(cfg.magic),
            "type": 0,
            "volume": 0.01,
            "price_open": 1.12,
            "sl": 1.10,
            "tp": 0.0,
            "comment": "ECT_EXT1",
        },
    )()
    info = type("I", (), {"point": 0.00001, "digits": 5})()
    tick = type("T", (), {"bid": 1.11, "ask": 1.12})()
    done = SimpleNamespace(retcode=mt5.TRADE_RETCODE_DONE, comment="ok")

    with patch("infra.orders.mt5.positions_get", return_value=[pos]), patch(
        "infra.orders.mt5.symbol_info", return_value=info,
    ), patch("infra.orders.mt5.symbol_info_tick", return_value=tick), patch(
        "infra.orders._order_send_with_retry", return_value=done,
    ) as order_send:
        n = close_positions_by_direction(
            cfg,
            direction=+1,
            reason="BOS_EXIT",
            ext1_protection_per_bar=s["per_bar"],
            current_bar_idx=bar_idx,
        )

    assert n == 1
    assert order_send.called


def test_ext1_protect_end_better_rrr_tp_on_real_ext1_u2_transition(eurusd_ext1_u2_scenario):
    """Po U2 (bar 34) — EXT1 SELL pozice za RRR targetem → EXT1_PROTECT_END_BETTER_RRR_TP."""
    s = eurusd_ext1_u2_scenario
    cfg = s["cfg"]
    transition_bar = s["transition_bar"]
    df = s["df"].iloc[: transition_bar + 1].reset_index(drop=True)
    per_bar = s["per_bar"][: len(df)]
    close_at_transition = float(df.iloc[-1]["close"])

    # SELL z EXT1: target = entry - rrr*|entry-sl|; close < target → better exit
    entry = 1.16000
    sl = 1.16200
    rrr_target = entry - cfg.rrr * abs(entry - sl)
    assert close_at_transition < rrr_target, (
        f"close {close_at_transition} musí být pod RRR target {rrr_target}"
    )

    pos = SimpleNamespace(
        ticket=90001,
        magic=int(cfg.magic),
        type=int(mt5.POSITION_TYPE_SELL),
        volume=0.01,
        price_open=entry,
        sl=sl,
        tp=0.0,
        comment=f"W{s['ext1_wt']}",
    )
    info = SimpleNamespace(point=0.00001, digits=5)

    with patch("runtime.ext1_protect_live.log_event") as log_event, patch(
        "runtime.ext1_protect_live.mt5.positions_get", return_value=[pos],
    ), patch(
        "runtime.ext1_protect_live.mt5.symbol_info", return_value=info,
    ), patch(
        "infra.orders._close_mt5_position_market", return_value=True,
    ) as close_market:
        done_time = maybe_rrr_fixed_better_exit_after_ext1_protect_end(
            cfg,
            df,
            ext1_protection_per_bar=per_bar,
            ext1_wave_times=s["ext1_times"],
            rrr_edge_done_bar_time=None,
        )

    assert close_market.called
    log_event.assert_called_once()
    assert log_event.call_args[0][2] == "EXT1_PROTECT_END_BETTER_RRR_TP"
    assert log_event.call_args[1]["wave_id"] == s["ext1_wt"]
    assert log_event.call_args[1]["market_exit_price"] == pytest.approx(close_at_transition)
    assert log_event.call_args[1]["original_rrr_target"] == pytest.approx(rrr_target)
    assert done_time == str(df.iloc[-1]["time"])


def test_ext_live_runtime_rrr_log_via_run_helper(eurusd_ext1_u2_scenario):
    """ExtLiveRuntime.run_ext1_rrr_better_exit — stejný log na reálném přechodu."""
    from runtime.ext_live import ExtLiveRuntime

    s = eurusd_ext1_u2_scenario
    cfg = s["cfg"]
    transition_bar = s["transition_bar"]
    df = s["df"].iloc[: transition_bar + 1].reset_index(drop=True)

    rt = ExtLiveRuntime()
    rt._ext1_protection_per_bar = s["per_bar"][: len(df)]
    rt._ext1_wave_times = s["ext1_times"]
    rt._ext1_rrr_edge_done_bar_time = None

    entry = 1.16000
    sl = 1.16200
    pos = SimpleNamespace(
        ticket=90002,
        magic=int(cfg.magic),
        type=int(mt5.POSITION_TYPE_SELL),
        volume=0.01,
        price_open=entry,
        sl=sl,
        tp=0.0,
        comment=f"W{s['ext1_wt']}",
    )
    info = SimpleNamespace(point=0.00001, digits=5)

    with patch("runtime.ext1_protect_live.log_event") as log_event, patch(
        "runtime.ext1_protect_live.mt5.positions_get", return_value=[pos],
    ), patch(
        "runtime.ext1_protect_live.mt5.symbol_info", return_value=info,
    ), patch("infra.orders._close_mt5_position_market", return_value=True):
        rt.run_ext1_rrr_better_exit(cfg, df)

    assert log_event.call_args[0][2] == "EXT1_PROTECT_END_BETTER_RRR_TP"
