"""Window-shift regression test — `IncrementalWaveSource(burn_in_df=...)` fix.

ROOT CAUSE (viz `scripts/_diag_window_shift_check.py` pro plnou diagnostiku):
`PineWaveDetector` (a `run_pine_wave_simulation`) cold-seeduje pivot/cand stav
z baru 0 sveho VSTUPNIHO `df`, s HARDCODED `pivot_dir=1`
(`strategy/wave_detection_pine.py`). To je v poradku pro FIXNI df (cely
backtest) — seed efekt "vyprchava" po nekolika desitkach/stovkach baru.

ALE zivy bot (`runtime.live_engine_session.LiveEngineSession.refresh_df_if_needed`)
dostava z MT5 ROLLING okno (`cfg.startup_bars` nejnovejsich baru), ktere se s
KAZDYM novym barem posune o 1 — bar 0 (=seed bod) je pri kazdem refreshi JINY.
`strategy/ext_range.py`'s trackery pak propaguji tuto odchylku bar-po-baru, takze
uz POUZITE vlny (vstup jiz odeslan) se muzou retroaktivne prekvalifikovat jen
kvuli posunu okna — NE kvuli genuine nove cenove informaci.

Tento test pokryva SLIDING (shifting) okno — na rozdil od
`tests/test_live_catch_up_parity.py` / `tests/test_live_engine_decision_parity.py`,
ktere testuji jen RUSTOUCI (append-only) df bez posunu zacatku okna.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from backtest.grid.data_cache import load_data
from config.bot_config import LIVE_BOT_CONFIG
from config.enums import WaveDetectionMode
from config.position_modes import resolve_grid_engine_config
from runtime.live_loop import _WAVE_CAUSAL_BURN_IN_BARS
from strategy.wave_source import IncrementalWaveSource

WINDOW_SIZE = 1440
# Sleduje skutecnou produkcni hodnotu (runtime/live_loop.py), aby tento test
# vzdy overoval realny burn-in pouzity v zivem botu, ne fixni hardcoded cislo.
BURN_IN_BARS = _WAVE_CAUSAL_BURN_IN_BARS
# Bar-index anchor v centralnim 2y EURUSD M30 datasetu, empiricky vybrany
# (viz _diag_window_shift_check.py) tak, aby PRED fixem reprodukoval divergenci
# pro VSECHNY testovane shifty (1/5/50 baru).
BASE = 5540
SHIFTS = (1, 5, 50)

_COMPARE_FIELDS = ("dir", "box_top", "box_bottom", "fib50", "sl", "tp", "move_pct")


def _cfg():
    base_cfg = resolve_grid_engine_config(LIVE_BOT_CONFIG)
    return replace(base_cfg, wave_detection_mode=WaveDetectionMode.INCREMENTAL_CAUSAL)


def _wave_key(w: dict) -> tuple:
    return tuple(
        round(float(w[f]), 8) if f != "dir" else int(w[f]) for f in _COMPARE_FIELDS
    )


def _waves_by_abs_bar(full_df, cfg, *, window_start: int, burn_in_bars: int) -> dict:
    """Vlny narozene v okne [window_start, window_start+WINDOW_SIZE), klicovane
    ABSOLUTNIM bar indexem vuci `full_df` (aby sla porovnat dve ruzne posunuta okna)."""
    df_window = full_df.iloc[window_start : window_start + WINDOW_SIZE].reset_index(
        drop=True
    )
    burn_in_df = None
    if burn_in_bars > 0:
        lo = max(0, window_start - burn_in_bars)
        if lo < window_start:
            burn_in_df = full_df.iloc[lo:window_start].reset_index(drop=True)

    src = IncrementalWaveSource(df_window, cfg, burn_in_df=burn_in_df)
    for i in range(1, len(df_window)):
        src.waves_at(i)

    birth = src.birth_map()
    by_wt = {str(w["wave_time"]): w for w in src.all_waves()}
    out: dict = {}
    for wt, local_bar in birth.items():
        w = by_wt.get(wt)
        if w is not None:
            out[window_start + int(local_bar)] = w
    return out


def _find_mismatches(wavesA: dict, wavesB: dict, overlap_lo: int, overlap_hi: int) -> list:
    mismatches = []
    for b in range(overlap_lo, overlap_hi):
        wa, wb = wavesA.get(b), wavesB.get(b)
        if wa is None and wb is None:
            continue
        if wa is None or wb is None or _wave_key(wa) != _wave_key(wb):
            mismatches.append(b)
    return mismatches


@pytest.fixture(scope="module")
def full_df():
    cfg = _cfg()
    return load_data(cfg.symbol, cfg.timeframe_label, None, None)


def test_sliding_window_without_burn_in_reproduces_bug(full_df):
    """Sanity/dokumentace PUVODNIHO bugu: BEZ burn-in mění posun rolling okna
    (byť jen o 1 bar) definice vln uvnitř překryvu obou oken.

    Pokud tento test začne selhávat (0 mismatchů i bez burn-in), zvolený
    BASE/SHIFT přestal reprodukovat bug (např. po nesouvisející změně dat) —
    over/aktualizuj BASE pomocí `scripts/_diag_window_shift_check.py`, NE
    smaž test.
    """
    cfg = _cfg()
    any_mismatch = False
    for shift in SHIFTS:
        wavesA = _waves_by_abs_bar(full_df, cfg, window_start=BASE, burn_in_bars=0)
        wavesB = _waves_by_abs_bar(
            full_df, cfg, window_start=BASE + shift, burn_in_bars=0
        )
        mismatches = _find_mismatches(wavesA, wavesB, BASE + shift, BASE + WINDOW_SIZE)
        if mismatches:
            any_mismatch = True
    assert any_mismatch, (
        "Ocekavany pre-fix window-shift bug se u BASE="
        f"{BASE} / SHIFTS={SHIFTS} neprojevil — aktualizuj BASE "
        "(scripts/_diag_window_shift_check.py)."
    )


@pytest.mark.parametrize("shift", SHIFTS)
def test_sliding_window_with_burn_in_is_shift_invariant(full_df, shift):
    """FIX: s `burn_in_df` (viz `IncrementalWaveSource`) jsou definice vln pro
    bary společné oběma oknům IDENTICKÉ, nezávisle na tom, kde přesně rolling
    okno začíná — i když se okno POSOUVÁ (sliding), ne jen roste (growing,
    to už pokrývají `test_live_catch_up_parity.py` / `test_live_engine_decision_parity.py`).
    """
    cfg = _cfg()
    wavesA = _waves_by_abs_bar(full_df, cfg, window_start=BASE, burn_in_bars=BURN_IN_BARS)
    wavesB = _waves_by_abs_bar(
        full_df, cfg, window_start=BASE + shift, burn_in_bars=BURN_IN_BARS
    )
    mismatches = _find_mismatches(wavesA, wavesB, BASE + shift, BASE + WINDOW_SIZE)
    assert not mismatches, (
        f"shift={shift}: {len(mismatches)} vln se lisi mezi posunutymi okny i PO "
        f"burn-in fixu (prvni rozdilne abs_bar: {mismatches[:5]})"
    )
