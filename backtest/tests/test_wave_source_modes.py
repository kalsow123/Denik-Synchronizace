"""
Smoke testy pro akci 1B (VARIANTA A.txt §3.2):

  (a) enum/pole `wave_detection_mode` existuje a ma spravny default,
  (b) COUPLING: wave_detection_mode == "incremental_causal" vynuti causal_mode=True
      (na urovni BotConfig i pri odvozeni policy),
  (c) IncrementalWaveSource / PineWaveDetector.advance na realnem slice dat
      produkuje vlny s birth == i (a je O(n) — advance vola jeden krok / bar).

Dukladna birth-parita legacy vs incremental je akce 1C (pozdeji) — zde drzime
test minimalni, ale zeleny.
"""
from __future__ import annotations

import os

import pytest

from backtest.causal_policy import policy_from_cfg
from config.bot_config import BotConfig
from config.enums import WaveDetectionMode
from strategy.wave_detection_pine import PineWaveDetector
from strategy.wave_source import (
    IncrementalWaveSource,
    LegacyWaveSource,
    make_wave_source,
)

_DATA_CSV = "data/EURUSD_H1.csv"


def _load_slice(n: int = 700):
    if not os.path.exists(_DATA_CSV):
        pytest.skip(f"chybi data CSV {_DATA_CSV}")
    from backtest.data_loader import load_csv

    df = load_csv(_DATA_CSV)
    return df.iloc[:n].reset_index(drop=True)


def _cfg(mode: WaveDetectionMode) -> BotConfig:
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
        wave_detection_mode=mode,
    )


# ── (a) enum / pole existuje ────────────────────────────────────────────────
def test_wave_detection_mode_field_exists_with_legacy_default():
    cfg = BotConfig()
    assert hasattr(cfg, "wave_detection_mode")
    assert cfg.wave_detection_mode == WaveDetectionMode.LEGACY_PRECOMPUTE
    # default legacy NESMI menit chovani gridu: causal_mode zustava False.
    assert cfg.causal_mode is False


def test_wave_detection_mode_enum_values():
    assert WaveDetectionMode.LEGACY_PRECOMPUTE.value == "legacy_precompute"
    assert WaveDetectionMode.INCREMENTAL_CAUSAL.value == "incremental_causal"


# ── (b) COUPLING: incremental_causal -> causal_mode=True ────────────────────
def test_incremental_causal_forces_causal_mode_on_config():
    cfg = BotConfig(wave_detection_mode=WaveDetectionMode.INCREMENTAL_CAUSAL)
    assert cfg.causal_mode is True


def test_incremental_causal_forces_policy_enabled():
    cfg = BotConfig(wave_detection_mode=WaveDetectionMode.INCREMENTAL_CAUSAL)
    assert policy_from_cfg(cfg).enabled is True


def test_legacy_precompute_leaves_causal_mode_default():
    # Grid (legacy_precompute) — causal_mode zustava False; policy vypnuta.
    cfg = BotConfig(wave_detection_mode=WaveDetectionMode.LEGACY_PRECOMPUTE)
    assert cfg.causal_mode is False
    assert policy_from_cfg(cfg).enabled is False


def test_legacy_precompute_explicit_causal_true_still_respected():
    # legacy + causal_mode=True = dnesni --causal (parita), nezmenit.
    cfg = BotConfig(
        wave_detection_mode=WaveDetectionMode.LEGACY_PRECOMPUTE, causal_mode=True
    )
    assert cfg.causal_mode is True
    assert policy_from_cfg(cfg).enabled is True


# ── (c) PineWaveDetector.advance produkuje vlny s birth == i ────────────────
def test_incremental_detector_births_equal_bar_index():
    df = _load_slice()
    cfg = _cfg(WaveDetectionMode.INCREMENTAL_CAUSAL)
    det = PineWaveDetector(df, cfg)

    born_total = 0
    advance_calls = 0
    for i in range(1, len(df)):
        born = det.advance(i)
        advance_calls += 1
        for w in born:
            born_total += 1
            # Klicove pravidlo 1B: vlna narozena na baru i ma birth == i.
            assert det.birth[w["wave_time"]] == i
            # wave_plus extend max do bar_i (NE do budoucnosti / last_ix).
            assert int(w["draw_right"]) <= i

    assert born_total > 0, "incremental detektor neprodukoval zadne vlny na slice"
    # O(n): prave jeden krok stavoveho stroje na bar (ne re-run prefixu / segmentu).
    assert advance_calls == len(df) - 1


def test_incremental_wave_source_matches_detector_births():
    df = _load_slice()
    cfg = _cfg(WaveDetectionMode.INCREMENTAL_CAUSAL)
    src = IncrementalWaveSource(df, cfg)
    seen = 0
    for i in range(1, len(df)):
        for w in src.waves_at(i):
            seen += 1
            assert src.birth_map()[w["wave_time"]] == i
    assert seen > 0


def test_advance_rejects_non_monotonic_calls():
    df = _load_slice(200)
    cfg = _cfg(WaveDetectionMode.INCREMENTAL_CAUSAL)
    det = PineWaveDetector(df, cfg)
    det.advance(10)
    with pytest.raises(ValueError):
        det.advance(5)


# ── LegacyWaveSource sanity (reprodukuje dnesni precompute) ─────────────────
def test_legacy_wave_source_groups_by_birth():
    df = _load_slice()
    cfg = _cfg(WaveDetectionMode.LEGACY_PRECOMPUTE)
    src = LegacyWaveSource(df, cfg)
    assert len(src.all_waves()) > 0
    birth = src.birth_map()
    for i in range(0, len(df)):
        for w in src.waves_at(i):
            assert birth[str(w["wave_time"])] == i


def test_make_wave_source_dispatches_by_mode():
    df = _load_slice(300)
    assert isinstance(
        make_wave_source(df, _cfg(WaveDetectionMode.LEGACY_PRECOMPUTE)),
        LegacyWaveSource,
    )
    assert isinstance(
        make_wave_source(df, _cfg(WaveDetectionMode.INCREMENTAL_CAUSAL)),
        IncrementalWaveSource,
    )
