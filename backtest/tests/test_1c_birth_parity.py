"""
Akce 1C (VARIANTA A.txt §3.3) — birth-parita + coupling incremental⇒causal.

Test 1 — birth-parita:
  PineWaveDetector.advance(i) vs legacy birth bar (LegacyWaveSource /
  run_pine_wave_simulation).  Kde se liší množina vln = legacy look-ahead
  post-processing (merge pres gapy, wick cleanup, wave_plus extend do last_ix).
  Invariant: kazda legacy vlna existuje v incremental birth_map se STEJNYM
  birth bar indexem; per-bar legacy.waves_at(i) je podmnozina incremental.

Test 2 — coupling:
  wave_detection_mode == incremental_causal ⇒ causal_mode=True (BotConfig +
  policy_from_cfg defense-in-depth).

Okno:
  - rychle: EURUSD H1 slice 700 baru (stejny vzor jako test_wave_source_modes)
  - pomale: centralni 2-lete okno (default_backtest_date_range, M30)
"""
from __future__ import annotations

import os
from dataclasses import replace
from typing import Dict, List, Tuple

import pytest

from backtest.causal_policy import policy_from_cfg
from backtest.data_loader import (
    default_backtest_date_range,
    filter_by_date_range,
    load_csv,
)
from backtest.grid.data_cache import csv_path_for
from config.bot_config import BotConfig, LIVE_BOT_CONFIG
from config.enums import WaveDetectionMode
from strategy.wave_detection_pine import PineWaveDetector
from strategy.wave_source import IncrementalWaveSource, LegacyWaveSource

_DATA_H1 = "data/EURUSD_H1.csv"
_H1_SLICE = 700  # rychly deterministicky slice — staci pro subset birth-paritu


def _wave_cfg() -> BotConfig:
    return BotConfig(
        symbol="EURUSD",
        wave_min_pct=0.26,
        min_opp_bars=3,
        entry_fib_level=0.5,
        sl_fib_level=0.8,
        rrr=2.0,
        wave_plus=True,
        ext_enabled=True,
        ext_wave_min_pct=0.76,
    )


def _load_h1_slice(n: int = _H1_SLICE):
    if not os.path.exists(_DATA_H1):
        pytest.skip(f"chybi data CSV {_DATA_H1}")
    return load_csv(_DATA_H1).iloc[:n].reset_index(drop=True)


def _load_2y_m30_window():
    path = csv_path_for(LIVE_BOT_CONFIG.symbol, LIVE_BOT_CONFIG.timeframe_label)
    if not path.exists():
        pytest.skip(f"chybi data CSV {path}")
    df_full = load_csv(path)
    date_from, date_to = default_backtest_date_range(df_full)
    df = filter_by_date_range(df_full, date_from, date_to)
    if df.empty:
        pytest.skip("2-lete okno je prazdne")
    return df, date_from, date_to


def _run_incremental_birth(
    df, cfg: BotConfig
) -> Tuple[Dict[str, int], Dict[int, List[str]]]:
    """advance(1..n-1) → birth_map + per-bar wave_time mnoziny."""
    det = PineWaveDetector(df, cfg)
    by_bar: Dict[int, List[str]] = {}
    for i in range(1, len(df)):
        for w in det.advance(i):
            wt = str(w["wave_time"])
            by_bar.setdefault(i, []).append(wt)
    return dict(det.birth), by_bar


def _assert_legacy_birth_subset_of_incremental(
    legacy_birth: Dict[str, int],
    inc_birth: Dict[str, int],
    *,
    context: str,
) -> None:
    """Kazda legacy vlna ma v incremental stejny birth bar (§3.3 invariant)."""
    mismatches: List[str] = []
    missing: List[str] = []
    for wt, leg_bar in legacy_birth.items():
        inc_bar = inc_birth.get(wt)
        if inc_bar is None:
            missing.append(f"{wt}: legacy_birth={leg_bar}")
        elif int(inc_bar) != int(leg_bar):
            mismatches.append(f"{wt}: legacy={leg_bar} incremental={inc_bar}")
    assert not missing, (
        f"{context}: legacy vlny chybi v incremental birth_map "
        f"(prvnich 5): {missing[:5]}"
    )
    assert not mismatches, (
        f"{context}: birth bar rozdily legacy vs advance "
        f"(prvnich 5): {mismatches[:5]}"
    )


def _assert_per_bar_legacy_subset(
    df,
    legacy: LegacyWaveSource,
    inc_by_bar: Dict[int, List[str]],
    *,
    context: str,
) -> None:
    for i in range(1, len(df)):
        leg_wts = {str(w["wave_time"]) for w in legacy.waves_at(i)}
        inc_wts = set(inc_by_bar.get(i, []))
        extra_in_legacy = leg_wts - inc_wts
        assert not extra_in_legacy, (
            f"{context}: bar {i} legacy ma vlny navic oproti advance(): "
            f"{sorted(extra_in_legacy)[:3]}"
        )


# ── Test 2: coupling incremental ⇒ causal ───────────────────────────────────
def test_1c_coupling_incremental_forces_causal_mode_on_config():
    cfg = BotConfig(
        wave_detection_mode=WaveDetectionMode.INCREMENTAL_CAUSAL,
        causal_mode=False,
    )
    assert cfg.causal_mode is True


def test_1c_coupling_replace_cannot_disable_causal_for_incremental():
    cfg = BotConfig(wave_detection_mode=WaveDetectionMode.INCREMENTAL_CAUSAL)
    cfg2 = replace(cfg, causal_mode=False)
    assert cfg2.causal_mode is True
    assert policy_from_cfg(cfg2).enabled is True


def test_1c_coupling_policy_from_cfg_defense_in_depth():
    """policy_from_cfg zapne brany i kdyby causal_mode nebyl na cfg propsan."""

    class _Cfg:
        causal_mode = False
        wave_detection_mode = WaveDetectionMode.INCREMENTAL_CAUSAL

    assert policy_from_cfg(_Cfg()).enabled is True


def test_1c_coupling_incremental_wave_source_implies_causal():
    df = _load_h1_slice(200)
    cfg = _wave_cfg()
    cfg = replace(cfg, wave_detection_mode=WaveDetectionMode.INCREMENTAL_CAUSAL)
    assert cfg.causal_mode is True
    src = IncrementalWaveSource(df, cfg)
    assert isinstance(src, IncrementalWaveSource)
    assert policy_from_cfg(cfg).enabled is True
    _ = src.waves_at(10)


# ── Test 1: birth-parita (legacy ⊆ incremental, stejny birth bar) ───────────
def test_1c_birth_parity_legacy_subset_h1_slice():
    df = _load_h1_slice()
    cfg = _wave_cfg()
    legacy = LegacyWaveSource(df, cfg, use_cache=False)
    inc_birth, inc_by_bar = _run_incremental_birth(df, cfg)

    _assert_legacy_birth_subset_of_incremental(
        legacy.birth_map(), inc_birth, context="H1 slice"
    )
    _assert_per_bar_legacy_subset(df, legacy, inc_by_bar, context="H1 slice")

    # Incremental muze narodit vice vln (legacy look-ahead post-processing).
    assert len(inc_birth) >= len(legacy.birth_map())
    assert len(inc_birth) > len(legacy.birth_map()), (
        "ocekavame alespon par incremental-only vln na realnem slice"
    )


@pytest.mark.slow
def test_1c_birth_parity_legacy_subset_2y_window():
    """Plne 2-lete okno (BACKTEST_WINDOW_YEARS) — EURUSD M30."""
    df, date_from, date_to = _load_2y_m30_window()
    cfg = _wave_cfg()
    legacy = LegacyWaveSource(df, cfg, use_cache=False)
    inc_birth, inc_by_bar = _run_incremental_birth(df, cfg)

    _assert_legacy_birth_subset_of_incremental(
        legacy.birth_map(),
        inc_birth,
        context=f"M30 2y ({date_from}..{date_to or 'end'}, {len(df)} bars)",
    )
    _assert_per_bar_legacy_subset(
        df,
        legacy,
        inc_by_bar,
        context=f"M30 2y ({len(df)} bars)",
    )
    # Na 2y okne: ~97 incremental-only vln oproti legacy (look-ahead post-proc).
    only_inc = set(inc_birth) - set(legacy.birth_map())
    assert len(only_inc) > 0, "ocekavame incremental-only vlny (legacy look-ahead)"
