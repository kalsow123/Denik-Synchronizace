"""
Test: trend snapshot pri narozeni vlny pouziva `draw_right` (extrem) misto
`birth_bar` (potvrzeni).

Scenar:
  Bear vlna B ma extreme na baru X. Pine emulator ji potvrdi az na baru
  X + min_opp_bars (typicky 3+ bary za extremem). V dobe mezi extremem a
  potvrzenim se na nektery silne bullish bar zavre BOS line predchozi
  bear vlny -> trend flipne bear -> bull. Vlna B se potvrdi a v current
  trendu (uz bull) je klasifikovana jako "wave_against_trend".

  Spravna semantika: vlna B patri do bear trendu (jeji pohyb probehl
  pred BOS), takze trend snapshot k jejimu narozeni musi byt bear.
  Implementace: snapshot bere stav k `draw_right` (extrem) a nikoli k
  `birth_bar` (potvrzeni).

  Stejne plati pro `compute_wave_sequence_info_per_wave` (index_in_trend),
  ktery je klicovy pro TP-wave detekci (tp_mode=WAVE_TARGET_N).
"""
from __future__ import annotations

import pandas as pd

from config.bot_config import BotConfig
from config.enums import EntryMode, TPMode
from strategy.trend_bos import (
    compute_trend_states_per_wave,
    wave_allowed_for_entry,
)
from strategy.wave_detection_pine import (
    compute_wave_birth_bars_pine,
    detect_waves_pine,
)
from strategy.wave_sequence import compute_wave_sequence_info_per_wave


def _make_synthetic_bear_then_bos_data() -> tuple[pd.DataFrame, BotConfig]:
    """
    Postavi syntetický syntetic chart:
      - Bar 0-9:   bear vlna A (1.2000 -> 1.1900, ~0.83%, prvni bear)
      - Bar 10-13: bull korekce (1.1900 -> 1.1960, 0.5% > wave_min_pct=0.26)
      - Bar 14-23: bear vlna B s pivotom na bar 14 (1.1960 -> 1.1700, ~2.17%)
                   extrem na bar 23.
      - Bar 24:    OBROVSKY bullish bar: low 1.1700 -> high 1.2050 -> close 1.2010
                   (close > A.box_top=1.2000 -> BOS flip bear->bull)
      - Bar 25-27: 3 bullish closes (potvrzeni vlny B)
      -> B birth = bar 27, B draw_right = bar 23 (extrem).
      -> BOS na bar 24, takze pri birth_bar=27 je trend uz bull.
      -> Pri draw_right=23 je trend jeste bear (BOS jeste neprisel).
    """
    rows = []
    # Bar 0-9: bear A, klesa z 1.2000 do 1.1900 v 10 barech
    for i in range(10):
        price = 1.2000 - 0.001 * i
        rows.append(dict(
            open=price + 0.0005,
            high=price + 0.0005,
            low=price - 0.0005,
            close=price - 0.0003,  # bearish close (C < O)
        ))
    # Bar 10-13: bull korekce 1.1900 -> 1.1960
    for i in range(4):
        price = 1.1900 + 0.0015 * (i + 1)
        rows.append(dict(
            open=price - 0.0005,
            high=price + 0.0005,
            low=price - 0.0010,
            close=price + 0.0003,  # bullish close (C > O)
        ))
    # Bar 14-23: bear B, klesa z 1.1960 do 1.1700 v 10 barech
    for i in range(10):
        price = 1.1960 - 0.0026 * (i + 1)
        rows.append(dict(
            open=price + 0.0005,
            high=price + 0.0005,
            low=price - 0.0005,
            close=price - 0.0003,
        ))
    # Bar 24: ENORMNI BULL bar - low 1.1700, high 1.2050, close 1.2010
    rows.append(dict(
        open=1.1700,
        high=1.2050,
        low=1.1700,
        close=1.2010,  # > A.box_top=1.2005 (high baru 0)? Spis: nad swing level posledni bear
    ))
    # Bar 25-27: dalsi 3 bullish bary (pro min_opp_bars=3 potvrzeni B)
    for i in range(3):
        price = 1.2010 + 0.0010 * (i + 1)
        rows.append(dict(
            open=price - 0.0005,
            high=price + 0.0005,
            low=price - 0.0005,
            close=price + 0.0003,
        ))

    df = pd.DataFrame(rows)
    df["time"] = pd.date_range("2026-04-01 00:00", periods=len(df), freq="30min")
    df = df[["time", "open", "high", "low", "close"]]

    cfg = BotConfig(
        wave_min_pct=0.26,
        min_opp_bars=3,
        entry_fib_level=0.5,
        sl_fib_level=0.8,
        rrr=2.0,
        wave_plus=False,
        ext_enabled=False,
        entry_mode=EntryMode.MARKET_FALLBACK,
        tp_mode=TPMode.WAVE_TARGET_N,
        tp_target_wave_index=2,  # pro tento syntetic test (jen 2 bear vlny v trendu)
        trend_filter_enabled=True,
        trend_hh_hl_filter_enabled=False,
    )
    return df, cfg


def test_retroactive_snapshot_bear_wave_before_bos_flip():
    """
    Bear vlna B ma extrem PRED BOS flipom. Po retroactive fixu musi snapshot
    pri jejim narozeni byt 'bear' (= snapshot k draw_right), ne 'bull' (=
    snapshot k birth_bar po BOS).
    """
    df, cfg = _make_synthetic_bear_then_bos_data()
    waves = detect_waves_pine(df, cfg)
    assert len(waves) >= 2, f"detect_waves: ocekavame >= 2 vlny, mame {len(waves)}"

    # Najdi dve bear vlny.
    bears = [w for w in waves if int(w["dir"]) == -1]
    assert len(bears) >= 2, f"ocekavame 2 bear vlny, mame {len(bears)}"
    A, B = bears[0], bears[1]

    birth = compute_wave_birth_bars_pine(df, cfg)
    a_birth = birth[str(A["wave_time"])]
    b_birth = birth[str(B["wave_time"])]
    b_draw_right = int(B["draw_right"])

    # Klicova assertion: B se potvrdila AZ PO BOS flipu (= birth > draw_right + 1).
    # BOS flip prijde mezi extremem (draw_right=23) a potvrzenim (birth=27).
    assert b_draw_right < b_birth, (
        f"birth ({b_birth}) musi byt za extremem ({b_draw_right})"
    )
    assert b_birth > b_draw_right + 1, (
        f"birth ({b_birth}) musi byt aspon 2 bary za extremem ({b_draw_right}); "
        "jinak BOS flip uz neni v okne"
    )

    # Trend snapshot per vlnu — pod retroactive fixem snapshot bere stav k
    # draw_right, takze trend@birth u B musi byt 'bear' (BOS jeste neprisel).
    trend_states = compute_trend_states_per_wave(df, waves, cfg)
    assert str(B["wave_time"]) in trend_states
    b_snapshot = trend_states[str(B["wave_time"])]
    assert b_snapshot.direction == "bear", (
        f"B mela byt klasifikovana jako bear-in-bear-trend "
        f"(snapshot k extremu), dostali jsme: {b_snapshot.direction}"
    )

    # B musi projit trend filtrem jako "passed" (= bear vlna v bear trendu).
    allowed, reason = wave_allowed_for_entry(B, b_snapshot, cfg)
    assert allowed, (
        f"B mela byt allowed (bear-in-bear), reason={reason}"
    )


def test_retroactive_index_in_trend_for_late_confirmed_bear():
    """
    Vlna B (pozdne-potvrzena bear) musi prispet do `index_in_trend` poradi v
    bear trendu, i kdyz mezi extremem a potvrzenim doslo k BOS flipu.
    """
    df, cfg = _make_synthetic_bear_then_bos_data()
    waves = detect_waves_pine(df, cfg)

    seq_info = compute_wave_sequence_info_per_wave(df, waves, cfg)
    bears = [w for w in waves if int(w["dir"]) == -1]
    assert len(bears) >= 2, f"ocekavame 2 bear vlny, mame {len(bears)}"
    A, B = bears[0], bears[1]

    a_info = seq_info[str(A["wave_time"])]
    b_info = seq_info[str(B["wave_time"])]

    assert a_info.index_in_trend == 1, (
        f"A jako prvni bear v trendu ma byt idx=1, je {a_info.index_in_trend}"
    )
    assert b_info.index_in_trend == 2, (
        f"B (pozdne-potvrzena bear PRED BOS flipom) ma byt idx=2 (= 2. bear "
        f"v bear trendu), je {b_info.index_in_trend}. Bez retroactive fixu "
        "by byla idx=0 (wave_against_trend) protoze birth nastane uz po "
        "BOS flipu."
    )


def test_normal_bear_in_bear_trend_unchanged():
    """
    Pravidelna bear vlna potvrzena PRED BOS flipom (typicky pripad) — retroactive
    fix nesmi zmenit jeji klasifikaci. Validuje, ze fix neni regresivni pro
    standardni scenare.
    """
    # Postavime data s 2 bear vlnami a klidnym mezikrokem (bez quick reversal).
    rows = []
    # Bar 0-9: bear A 1.2000 -> 1.1900
    for i in range(10):
        price = 1.2000 - 0.001 * i
        rows.append(dict(
            open=price + 0.0005,
            high=price + 0.0005,
            low=price - 0.0005,
            close=price - 0.0003,
        ))
    # Bar 10-13: pomale bull korekce 1.1900 -> 1.1960 (kvalifikuje jako wave)
    for i in range(4):
        price = 1.1900 + 0.0015 * (i + 1)
        rows.append(dict(
            open=price - 0.0005,
            high=price + 0.0005,
            low=price - 0.0010,
            close=price + 0.0003,
        ))
    # Bar 14-23: bear B 1.1960 -> 1.1700
    for i in range(10):
        price = 1.1960 - 0.0026 * (i + 1)
        rows.append(dict(
            open=price + 0.0005,
            high=price + 0.0005,
            low=price - 0.0005,
            close=price - 0.0003,
        ))
    # Bar 24-28: 5 pomalych bullish baru, ale neprurazi A.box_top (zadny BOS).
    for i in range(5):
        price = 1.1710 + 0.0010 * (i + 1)
        rows.append(dict(
            open=price - 0.0005,
            high=price + 0.0005,
            low=price - 0.0005,
            close=price + 0.0003,
        ))

    df = pd.DataFrame(rows)
    df["time"] = pd.date_range("2026-04-01 00:00", periods=len(df), freq="30min")
    df = df[["time", "open", "high", "low", "close"]]

    cfg = BotConfig(
        wave_min_pct=0.26,
        min_opp_bars=3,
        entry_fib_level=0.5,
        sl_fib_level=0.8,
        rrr=2.0,
        wave_plus=False,
        ext_enabled=False,
        entry_mode=EntryMode.MARKET_FALLBACK,
        tp_mode=TPMode.WAVE_TARGET_N,
        tp_target_wave_index=2,
        trend_filter_enabled=True,
        trend_hh_hl_filter_enabled=False,
    )

    waves = detect_waves_pine(df, cfg)
    bears = [w for w in waves if int(w["dir"]) == -1]
    assert len(bears) >= 2, f"ocekavame 2 bear vlny, mame {len(bears)}"

    seq_info = compute_wave_sequence_info_per_wave(df, waves, cfg)
    trend_states = compute_trend_states_per_wave(df, waves, cfg)
    A, B = bears[0], bears[1]

    # A: 1st bear, neutral start
    assert seq_info[str(A["wave_time"])].index_in_trend == 1
    # B: 2nd bear, no BOS happened -> still bear trend
    assert trend_states[str(B["wave_time"])].direction == "bear"
    assert seq_info[str(B["wave_time"])].index_in_trend == 2
