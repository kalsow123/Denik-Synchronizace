import pandas as pd
from typing import List, Dict

from config.bot_config import BotConfig
from strategy.wave_sequence import (
    compute_wave_sequence_info_per_wave,
    compute_ext1_protection_bars,
)

def _cfg():
    cfg = BotConfig(
        symbol="EURUSD",
        timeframe=30,
        trend_hh_hl_filter_enabled=True,
    )
    return cfg

def _w(time, dir_val, draw_right, is_ext=False, hh_hl_pass=True, **kwargs):
    w = {
        "wave_time": time,
        "dir": dir_val,
        "draw_right": draw_right,
        "hh_hl_pass": hh_hl_pass,
        "box_top": kwargs.get("box_top", 1.20),
        "box_bottom": kwargs.get("box_bottom", 1.10),
    }
    if is_ext:
        w["is_ext"] = True
    for k, v in kwargs.items():
        w[k] = v
    return w

def test_ext_is_bos_wave_gets_idx_1():
    # Bull trend (UP1, UP2, UP3), EXT DOWN prorazi swing -> EXT.is_bos_wave=True, idx=1
    df = pd.DataFrame({
        "time": ["T0", "T1", "T2", "T3", "T4"],
        "close": [1.15, 1.16, 1.17, 1.18, 1.05] # T4 close < UP3.box_bottom (1.10)
    })
    waves = [
        _w("W1", 1, 1, box_bottom=1.14),
        _w("W2", 1, 2, box_bottom=1.15),
        _w("W3", 1, 3, box_bottom=1.10),
        _w("W_EXT", -1, 4, is_ext=True)
    ]
    res = compute_wave_sequence_info_per_wave(df, waves, _cfg())
    assert res["W_EXT"].is_bos_wave is True
    assert res["W_EXT"].index_in_trend == 1

def test_first_trend_dir_after_ext_bos_gets_idx_2():
    # EXT DOWN BOS (idx=1), pak DOWN1 -> DOWN1.idx=2
    df = pd.DataFrame({
        "time": ["T0", "T1", "T2", "T3", "T4", "T5"],
        "close": [1.15, 1.16, 1.17, 1.18, 1.05, 1.04]
    })
    waves = [
        _w("W1", 1, 1, box_bottom=1.14),
        _w("W_EXT", -1, 4, is_ext=True), # BOS
        _w("W_DOWN", -1, 5) # Trend-dir po BOS
    ]
    res = compute_wave_sequence_info_per_wave(df, waves, _cfg())
    assert res["W_EXT"].is_bos_wave is True
    assert res["W_EXT"].index_in_trend == 1
    assert res["W_DOWN"].index_in_trend == 2

def test_ext_counter_flips_to_idx_1():
    # Bull trend, EXT DOWN (opacna k trendu). Uziv. pozadavek: opacna EXT vlna
    # zaklada novy smer -> flip, idx 1, is_bos_wave=True (EXT MUSI mit cislo).
    df = pd.DataFrame({
        "time": ["T0", "T1", "T2", "T3"],
        "close": [1.15, 1.16, 1.17, 1.16] # T3 close > W2.box_bottom (1.10)
    })
    waves = [
        _w("W1", 1, 1, box_bottom=1.14),
        _w("W2", 1, 2, box_bottom=1.10),
        _w("W_EXT", -1, 3, is_ext=True)
    ]
    res = compute_wave_sequence_info_per_wave(df, waves, _cfg())
    assert res["W_EXT"].is_bos_wave is True
    assert res["W_EXT"].index_in_trend == 1

def test_reversal_after_trend_dir_ext_gets_idx_1():
    # Trend-dir EXT (scenar C) = climax trendu; PRVNI opacna vlna po ni dostane
    # idx 1 (reverzni trend), i bez prurazu struktury.
    df = pd.DataFrame({
        "time": ["T0", "T1", "T2", "T3"],
        "close": [1.10, 1.20, 1.30, 1.22]
    })
    waves = [
        _w("W1", 1, 1, box_bottom=1.05, box_top=1.12),       # neutral -> bull, idx 1
        _w("EXT_UP", 1, 2, is_ext=True, box_bottom=1.10, box_top=1.35),  # scenar C, idx 2
        _w("D1", -1, 3, box_bottom=1.18, box_top=1.28),      # reverzni vlna -> idx 1
    ]
    res = compute_wave_sequence_info_per_wave(df, waves, _cfg())
    assert res["EXT_UP"].index_in_trend == 2
    assert res["D1"].index_in_trend == 1
    assert res["D1"].is_bos_wave is True

def test_counter_after_establishing_ext1_gets_parallel_index():
    # EXT-1 (scenar D) zaklada trend a otevira EXT-1 counting-okno: protismerne
    # vlny se pocitaji jako nezavisla sekvence 1,2,3,4 (uziv.: "po EXT se pocita
    # na obe strany — prvni BEAR WAVE je 1"). Trend se NEotaci (ochrana pozic).
    df = pd.DataFrame({
        "time": ["T0", "T1", "T2"],
        "close": [1.15, 1.25, 1.20]
    })
    waves = [
        _w("EXT_UP", 1, 1, is_ext=True, box_bottom=1.10, box_top=1.30),
        _w("D1", -1, 2, box_bottom=1.15)
    ]
    res = compute_wave_sequence_info_per_wave(df, waves, _cfg())
    assert res["EXT_UP"].index_in_trend == 1
    assert res["D1"].index_in_trend == 1

def test_ext_trend_dir_continues_index():
    df = pd.DataFrame({
        "time": ["T0", "T1", "T2", "T3"],
        "close": [1.15, 1.16, 1.17, 1.20]
    })
    waves = [
        _w("W1", 1, 1),
        _w("W2", 1, 2),
        _w("EXT", 1, 3, is_ext=True)
    ]
    res = compute_wave_sequence_info_per_wave(df, waves, _cfg())
    assert res["EXT"].index_in_trend == 3

def test_wave_after_ext_no_high_gets_index():
    df = pd.DataFrame({
        "time": ["T0", "T1", "T2"],
        "close": [1.15, 1.20, 1.18]
    })
    waves = [
        _w("EXT", 1, 1, is_ext=True, box_top=1.30),
        _w("UP", 1, 2, box_top=1.25) # Neprekona EXT.box_top (1.30) ale je to trend-dir
    ]
    res = compute_wave_sequence_info_per_wave(df, waves, _cfg())
    assert res["EXT"].index_in_trend == 1
    assert res["UP"].index_in_trend == 2

def test_close_above_ext_high_ends_both_sides():
    df = pd.DataFrame({
        "time": ["T0", "T1", "T2", "T3"],
        "close": [1.15, 1.20, 1.35, 1.10]
    })
    waves = [
        _w("EXT", 1, 1, is_ext=True, box_top=1.30),
        # T2: close 1.35 > 1.30 (EXT top). Mech A se spusti
        _w("D1", -1, 3) # Jiz mimo both-sides, jako bezna counter
    ]
    res = compute_wave_sequence_info_per_wave(df, waves, _cfg())
    assert res["EXT"].index_in_trend == 1
    assert res["D1"].index_in_trend is None

def test_ext_bos_via_fib_35_flips_trend():
    df = pd.DataFrame({
        "time": ["T0", "T1", "T2", "T3"],
        "close": [1.15, 1.20, 1.10, 1.05]
    })
    waves = [
        _w("EXT", 1, 1, is_ext=True, ext_fib_35_level=1.12),
        # T2: close 1.10 < 1.12 -> Mech B flip
        _w("D1", -1, 3) # Prvni vlna ve smeru flipu
    ]
    res = compute_wave_sequence_info_per_wave(df, waves, _cfg())
    assert res["D1"].is_bos_wave is True
    assert res["D1"].index_in_trend == 1

def test_neutral_first_wave_is_ext():
    df = pd.DataFrame({"time": ["T0"], "close": [1.15]})
    waves = [_w("EXT", 1, 0, is_ext=True)]
    res = compute_wave_sequence_info_per_wave(df, waves, _cfg())
    assert res["EXT"].is_bos_wave is False
    assert res["EXT"].index_in_trend == 1

def test_two_ext_in_row():
    df = pd.DataFrame({"time": ["T0", "T1"], "close": [1.15, 1.20]})
    waves = [
        _w("EXT1", 1, 0, is_ext=True),
        _w("EXT2", 1, 1, is_ext=True)
    ]
    res = compute_wave_sequence_info_per_wave(df, waves, _cfg())
    assert res["EXT1"].index_in_trend == 1
    assert res["EXT2"].index_in_trend == 2

def test_first_classic_bos_after_ext1_is_forgiven():
    # EXT-1 zaklada trend (scenar D, idx 1). PRVNI klasicky BOS (DOWN pod swing)
    # se ODPUSTI: trend se NEotaci (is_bos_wave=False), ale protismerna vlna se
    # POCITA v EXT-1 counting-okne (D1 idx 1). Trend-dir UP pokracuje (idx 2), az
    # DRUHY BOS flipne (D2 idx 2, is_bos_wave=True), cimz counting-okno konci.
    df = pd.DataFrame({
        "time": ["T0", "T1", "T2", "T3", "T4"],
        "close": [1.15, 1.25, 1.05, 1.20, 1.08],
    })
    waves = [
        _w("EXT", 1, 1, is_ext=True, box_bottom=1.10, box_top=1.30),  # idx 1
        _w("D1", -1, 2, box_bottom=1.04, box_top=1.20),  # forgive, counter idx 1
        _w("U2", 1, 3, box_bottom=1.12, box_top=1.32),   # trend-dir -> idx 2
        _w("D2", -1, 4, box_bottom=1.02, box_top=1.18),  # close 1.08 < 1.12 -> 2. BOS flip
    ]
    res = compute_wave_sequence_info_per_wave(df, waves, _cfg())
    assert res["EXT"].index_in_trend == 1
    assert res["D1"].index_in_trend == 1
    assert res["D1"].is_bos_wave is False
    assert res["U2"].index_in_trend == 2
    assert res["D2"].index_in_trend == 2
    assert res["D2"].is_bos_wave is True


def test_classic_bos_not_forgiven_when_ext_is_idx_2():
    # EXT je trend-dir s idx 2 (scenar C) -> ochrana NEPLATI; opacna vlna
    # po ni flipne trend na idx 1 (neni odpustena).
    df = pd.DataFrame({
        "time": ["T0", "T1", "T2", "T3"],
        "close": [1.10, 1.15, 1.28, 1.05],
    })
    waves = [
        _w("W1", 1, 1, box_bottom=1.08, box_top=1.20),               # neutral -> bull idx 1
        _w("EXT", 1, 2, is_ext=True, box_bottom=1.10, box_top=1.30),  # scenar C, idx 2
        _w("D1", -1, 3, box_bottom=1.02, box_top=1.18),               # flip -> idx 1
    ]
    res = compute_wave_sequence_info_per_wave(df, waves, _cfg())
    assert res["EXT"].index_in_trend == 2
    assert res["D1"].index_in_trend == 1
    assert res["D1"].is_bos_wave is True


def test_bos_via_fib_35_pending_until_next_wave():
    df = pd.DataFrame({
        "time": ["T0", "T1", "T2", "T3"],
        "close": [1.15, 1.20, 1.10, 1.05]
    })
    waves = [
        _w("EXT", 1, 1, is_ext=True, ext_fib_35_level=1.12),
        # T2: close < 1.12 (bez vlny) -> pending bos
        _w("D1", -1, 3) # Prvni vlna
    ]
    res = compute_wave_sequence_info_per_wave(df, waves, _cfg())
    assert res["D1"].is_bos_wave is True
    assert res["D1"].index_in_trend == 1


def test_ext1_fib35_reversal_forgiven_trend_continues():
    # EXT UP zaklada bull trend (scenar D, idx 1) -> trend_established_by_ext.
    # PRVNI fib-0.35 reverzace (Mechanismus B) se ODPUSTI: trend se neotoci, jen
    # se zavre EXT okno a spotrebuje one-shot. Nasledna UP vlna pokracuje (idx 2).
    df = pd.DataFrame({
        "time": ["T0", "T1", "T2"],
        "close": [1.20, 1.15, 1.25],
    })
    waves = [
        _w("EXT", 1, 0, is_ext=True, box_bottom=1.10, box_top=1.30, ext_bos_level=1.18),
        # T1: close 1.15 < fib35 1.18 (bez vlny) -> Mechanismus B, ale forgive
        _w("UP2", 1, 2, box_bottom=1.21, box_top=1.28),
    ]
    res = compute_wave_sequence_info_per_wave(df, waves, _cfg())
    assert res["EXT"].index_in_trend == 1
    assert res["UP2"].index_in_trend == 2
    # EXT okno se po forgive zavrelo, trend zustal bull (zadny flip).


def test_no_oscillation_when_swing_levels_inverted():
    # Regrese: prebar BOS flip musi vynulovat OBA swing levely (jako engine),
    # jinak invertovane levely (lub > ldt) zpusobi oscilaci trendu a kazda vlna
    # dostane idx=1. Bull trend (UP1) pak DOWN-flip, nova DOWN vlna = idx 2.
    df = pd.DataFrame({
        "time": ["T0", "T1", "T2", "T3", "T4"],
        "close": [1.20, 1.30, 1.05, 1.04, 1.03],
    })
    waves = [
        _w("U1", 1, 1, box_bottom=1.18, box_top=1.32),   # neutral -> bull idx 1
        _w("D1", -1, 2, box_bottom=1.02, box_top=1.16),  # close 1.05 < U1.bottom -> flip bear idx 1
        _w("D2", -1, 4, box_bottom=1.00, box_top=1.10),  # trend-dir bear -> idx 2 (ne 1)
    ]
    res = compute_wave_sequence_info_per_wave(df, waves, _cfg())
    assert res["U1"].index_in_trend == 1
    assert res["D1"].index_in_trend == 1
    assert res["D2"].index_in_trend == 2


def test_ext1_protection_window_active_until_wave2():
    # EXT-1 UP zaklada trend -> okno od baru EXT-1. Konec na prvni trend-dir vlne
    # s idx >= 2 (U2), ne az na EXT-2 (T_FIX_C: until_wave2).
    df = pd.DataFrame({
        "time": ["T0", "T1", "T2", "T3", "T4"],
        "close": [1.15, 1.25, 1.20, 1.26, 1.40],
    })
    waves = [
        _w("EXT_UP", 1, 1, is_ext=True, box_bottom=1.10, box_top=1.30),
        _w("D1", -1, 2, box_bottom=1.15, box_top=1.24),
        _w("U2", 1, 3, box_bottom=1.21, box_top=1.30),
        _w("EXT2", 1, 4, is_ext=True, box_bottom=1.28, box_top=1.45),
    ]
    win = compute_ext1_protection_bars(df, waves, _cfg())
    assert len(win) == len(df)
    assert win[0] == 0
    assert win[1] == 1
    assert win[2] == 1
    assert win[3] == 0
    assert win[4] == 0


def test_ext1_protection_window_empty_without_ext1():
    # Bezny bull trend bez EXT-1 -> zadne ochranne okno.
    df = pd.DataFrame({
        "time": ["T0", "T1", "T2"],
        "close": [1.10, 1.15, 1.20],
    })
    waves = [
        _w("U1", 1, 0, box_bottom=1.08, box_top=1.12),
        _w("U2", 1, 1, box_bottom=1.12, box_top=1.17),
        _w("U3", 1, 2, box_bottom=1.17, box_top=1.22),
    ]
    win = compute_ext1_protection_bars(df, waves, _cfg())
    assert win == [0, 0, 0]
