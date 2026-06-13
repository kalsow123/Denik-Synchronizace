"""
Wick Fakeout Recovery (WF) — detekce a aktivace.

Tento modul poskytuje stav pro bar-by-bar sledování WF okna (BacktestEngine)
i jednorázovou scan funkci pro live loop (evaluate_wf_from_df).

Viz docs/WICK_FAKEOUT_RECOVERY.txt pro plný popis featury.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from config.bot_config import BotConfig

# =====================================================================
# WICK FAKEOUT RECOVERY (WF)
# ---------------------------------------------------------------------
# Co to dělá:
#   WF řeší situaci, kdy po dokončení vlny ve směru trendu přijde
#   protisměrový pohyb, který NENÍ validní BOS (jen wick nad/pod
#   extrémem last wave, žádný close na druhé straně). Pak se trh
#   vrátí ve směru trendu a udělá close za opačným extrémem last wave.
#   Engine by tuto situaci jinak nechal bez definice — WF v tomto
#   momentě vytvoří NOVOU continuation vlnu od fakeout pivotu
#   (nejvyšší wick high pro downtrend, nejnižší wick low pro uptrend).
#
# Kdy se aktivuje (downtrend):
#   1) Last wave šla dolů, má definované last_wave_high a last_wave_low.
#   2) V okně mezi koncem last wave a aktuálním barem byl alespoň
#      jeden bar s high > last_wave_high (= wick).
#   3) ŽÁDNÝ bar v okně neměl close > last_wave_high (= nebyl validní
#      close-based BOS).
#   4) Aktuální bar má close < last_wave_low (= trend pokračuje).
#   5) Trh NENÍ ve stavu EXT.
#   Pro uptrend mirror (last wave nahoru, wick pod low, žádný close
#   pod low, close nad high last wave, ne EXT).
#
# Fakeout pivot:
#   = max(bar.high) v okně pro downtrend (nejvyšší wick).
#   = min(bar.low)  v okně pro uptrend  (nejnižší wick).
#   Bez ohledu na to, ve kterém pořadí v okně tento wick byl.
#
# Co se stane:
#   Vznikne nová vlna ve směru trendu, jejíž swing extrém = fakeout
#   pivot. Dál ji engine obhospodařuje standardně — standardní pending
#   STOP setup po jejím dokončení, LFT (pokud zapnuté), filtry, dedup,
#   všechno jako u jakékoli jiné vlny.
#
# WF NEMÁ vlastní entry logiku:
#   WF jen "dořeší" vykreslení vlny. Vstupy řeší existující flow.
#
# Žádný timeout, žádný lookback limit:
#   Okno je definováno strukturou (konec last wave → aktivační close).
#   Buď přijde aktivační close → WF aktivace.
#   Nebo přijde close-based BOS → standardní logika obratu, WF se
#   neaktivuje.
#   Nebo trh zůstává uvnitř range → engine čeká dál.
#
# Výjimka EXT:
#   Pokud je trh ve stavu EXT, WF se NEAKTIVUJE. EXT režim má vlastní
#   logiku a WF tam nepatří. Logni WF_SKIPPED_EXT pro debug.
#
# Config:
#   WF_ENABLED: bool — master switch (default False).
#   Žádné další WF-specific configy. Vše ostatní (RRR, RISK_USD,
#   filtry, MAGIC, atd.) sdílené se standardním flow.
# =====================================================================

WAVE_ORIGIN_NORMAL = "normal"
WAVE_ORIGIN_WF = "wf_continuation"


@dataclass
class WickFakeoutWindowState:
    """
    Stav WF okna — sleduje bary od potvrzení last wave po aktuální bar.

    WF referenční HIGH/LOW (`wf_ref_*`) odpovídají lokálnímu swingu vlny
    ve vizualizaci (ne plochému box_top od vzdáleného pivotu).
    """
    last_wave: Optional[dict] = None
    last_wave_birth_bar: int = -1
    wf_ref_high: float = 0.0
    wf_ref_low: float = 0.0

    # Wick tracking
    window_has_wick: bool = False
    fakeout_pivot: Optional[float] = None       # max high (down) / min low (up)
    fakeout_bar_idx: int = -1                   # integer bar index v df

    # Close-based BOS tracking (invalidace WF)
    window_has_close_bos: bool = False

    # Box extremes v okně (pro výpočet box_bottom / box_top WF vlny)
    window_min_low: Optional[float] = None
    window_max_high: Optional[float] = None

    # Počet barů v okně (pro log window_size_bars)
    window_size: int = 0


class WickFakeoutTracker:
    """
    Bar-by-bar WF tracker pro BacktestEngine.

    Volání:
      1. tracker.on_new_wave(wave, birth_bar) — při každé nové potvrzené vlně
      2. tracker.on_bar(high, low, close, bar_idx) — po zpracování vln na baru
      3. result = tracker.check_wf(close, bar_idx, cfg) → dict nebo None

    Je bezpečné volat i při wf_enabled=False — všechny metody jsou pak no-op.
    """

    def __init__(self) -> None:
        self._state = WickFakeoutWindowState()

    def on_new_wave(
        self,
        wave: dict,
        birth_bar: int,
        df: pd.DataFrame | None = None,
        *,
        force_reset: bool = False,
    ) -> None:
        """
        Nastaví novou last_wave a WF referenční HIGH/LOW.

        Reset se neprovádí, pokud:
          - nová vlna je STEJNÝ směr a WF okno už běží (wick / aktivní okno),
          - aby mikro-vlna ve stejném trendu nepřerušila fakeout recovery.

        force_reset=True po WF aktivaci — nová WF continuation vlna.
        """
        st = self._state
        new_dir = int(wave.get("dir", 0))
        if (
            not force_reset
            and st.last_wave is not None
            and int(st.last_wave.get("dir", 0)) == new_dir
            and (
                st.window_has_wick
                or st.window_has_close_bos
                or st.window_size > 0
            )
        ):
            return

        wf_high, wf_low = _wf_reference_levels_from_wave(
            wave, df, end_bar=birth_bar
        )
        self._state = WickFakeoutWindowState(
            last_wave=wave,
            last_wave_birth_bar=birth_bar,
            wf_ref_high=wf_high,
            wf_ref_low=wf_low,
        )

    def on_bar(self, high: float, low: float, close: float, bar_idx: int) -> None:
        """
        Aktualizuje WF okno o aktuální bar.
        Okno začíná od baru AFTER last_wave_birth_bar (exkluzivně).
        """
        st = self._state
        if st.last_wave is None:
            return
        if bar_idx <= st.last_wave_birth_bar:
            return

        st.window_size += 1
        wave_dir = int(st.last_wave.get("dir", 0))
        last_high = float(st.wf_ref_high)
        last_low = float(st.wf_ref_low)

        if wave_dir == -1:
            # Po potvrzení vlny může low prohlubovat jen DO prvního fakeout wicku.
            if not st.window_has_wick and low < last_low:
                st.wf_ref_low = float(low)
                last_low = float(st.wf_ref_low)
            if high > last_high:
                st.window_has_wick = True
                if st.fakeout_pivot is None or high > st.fakeout_pivot:
                    st.fakeout_pivot = high
                    st.fakeout_bar_idx = bar_idx
            if close > last_high:
                st.window_has_close_bos = True

        elif wave_dir == 1:
            if not st.window_has_wick and high > last_high:
                st.wf_ref_high = float(high)
                last_high = float(st.wf_ref_high)
            if low < last_low:
                st.window_has_wick = True
                if st.fakeout_pivot is None or low < st.fakeout_pivot:
                    st.fakeout_pivot = low
                    st.fakeout_bar_idx = bar_idx
            if close < last_low:
                st.window_has_close_bos = True

        # Sleduj extremy celého okna pro box vlny
        if st.window_min_low is None or low < st.window_min_low:
            st.window_min_low = low
        if st.window_max_high is None or high > st.window_max_high:
            st.window_max_high = high

    def check_wf(
        self,
        close: float,
        bar_idx: int,
        cfg: BotConfig,
        *,
        bar_time_str: str = "",
    ) -> Optional[dict]:
        """
        Zkontroluje WF podmínky na aktuálním baru.

        Vrací dict s výsledkem:
          {"status": "activate", "fakeout_pivot": ..., "fakeout_bar_idx": ...,
           "window_size": ..., "last_wave": ...}
          nebo
          {"status": "ext_skipped", "last_wave": ...}
          nebo None (podmínky nejsou splněny)

        EXT skipped se vrací jen pokud by WF jinak aktivovalo (pro logování).
        """
        if not bool(getattr(cfg, "wf_enabled", False)):
            return None

        st = self._state
        if st.last_wave is None:
            return None
        if st.window_has_close_bos:
            return None
        if not st.window_has_wick:
            return None

        wave_dir = int(st.last_wave.get("dir", 0))
        last_low = float(st.wf_ref_low)
        last_high = float(st.wf_ref_high)

        # Podmínka 4: aktivační close za opačným extrémem last wave
        if wave_dir == -1 and close >= last_low:
            return None
        if wave_dir == 1 and close <= last_high:
            return None

        # Podmínka 5: výjimka EXT
        is_ext_last = _is_ext_wave(st.last_wave, cfg)
        if is_ext_last:
            return {"status": "ext_skipped", "last_wave": st.last_wave}

        return {
            "status": "activate",
            "fakeout_pivot": st.fakeout_pivot,
            "fakeout_bar_idx": st.fakeout_bar_idx,
            "window_size": st.window_size,
            "last_wave": st.last_wave,
            "window_min_low": st.window_min_low,
            "window_max_high": st.window_max_high,
            "wf_ref_high": last_high,
            "wf_ref_low": last_low,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def compute_wf_reference_levels(
    wave: dict,
    df: pd.DataFrame,
    *,
    end_bar: int,
) -> Optional[tuple[float, float]]:
    """
    Referenční HIGH/LOW pro WF — odpovídá poslednímu swingu vlny ve vizualizaci.

    BEAR: low = min(low) v [draw_left..end_bar]; high = max(high) od baru
          tohoto low do end_bar (lokální rebound / vrchol před fakeout oknem).
    BULL: mirror — high = max(high), low = min(low) od baru tohoto high.
    """
    wave_dir = int(wave.get("dir", 0))
    if wave_dir not in (1, -1) or df is None or df.empty:
        return None

    draw_left = int(wave.get("draw_left", 0))
    end_bar = int(end_bar)
    if draw_left < 0 or end_bar < draw_left or end_bar >= len(df):
        return None

    seg = df.iloc[draw_left : end_bar + 1]
    if seg.empty:
        return None

    highs = seg["high"].astype(float)
    lows = seg["low"].astype(float)

    if wave_dir == -1:
        low_pos = int(lows.idxmin())
        low_val = float(lows.min())
        tail = df.iloc[low_pos : end_bar + 1]
        high_val = float(tail["high"].astype(float).max())
        return high_val, low_val

    high_pos = int(highs.idxmax())
    high_val = float(highs.max())
    tail = df.iloc[high_pos : end_bar + 1]
    low_val = float(tail["low"].astype(float).min())
    return high_val, low_val


def _wf_reference_levels_from_wave(
    wave: dict,
    df: pd.DataFrame | None,
    *,
    end_bar: int,
) -> tuple[float, float]:
    """Fallback na box_top/box_bottom, pokud nelze spočítat z df."""
    if df is not None:
        refs = compute_wf_reference_levels(wave, df, end_bar=end_bar)
        if refs is not None:
            return refs
    return float(wave.get("box_top", 0.0)), float(wave.get("box_bottom", 0.0))


def _is_ext_wave(wave: dict, cfg: BotConfig) -> bool:
    """True pokud je vlna EXT a EXT režim je zapnutý."""
    if not bool(getattr(cfg, "ext_enabled", False)):
        return False
    try:
        from strategy.ext_logic import is_ext_wave
        return bool(is_ext_wave(wave, cfg))
    except Exception:
        return bool(wave.get("is_ext", False))


def build_wf_wave(
    cfg: BotConfig,
    *,
    last_wave: dict,
    fakeout_pivot: float,
    fakeout_bar_idx: int,
    activation_bar_idx: int,
    wave_time_str: str,
    window_min_low: Optional[float] = None,
    window_max_high: Optional[float] = None,
) -> Optional[dict]:
    """
    Vytvoří synthetický wave dict pro WF continuation vlnu.

    Pro DOWN wave: box_top = fakeout_pivot, box_bottom = window_min_low
    Pro UP wave:   box_top = window_max_high, box_bottom = fakeout_pivot

    Vrací None pokud geometrie nevychází (volá _append_wave_sig).
    """
    from strategy.wave_detection_pine import _append_wave_sig

    wave_dir = int(last_wave.get("dir", 0))
    if wave_dir not in (1, -1):
        return None
    if fakeout_pivot is None:
        return None
    if fakeout_bar_idx < 0 or activation_bar_idx <= fakeout_bar_idx:
        return None

    if wave_dir == -1:  # downtrend continuation
        pivot_level = float(fakeout_pivot)
        low_extreme = float(window_min_low) if window_min_low is not None else pivot_level * 0.99
        cand_level = low_extreme
        box_top = pivot_level
        box_bottom = low_extreme
    else:  # uptrend continuation
        pivot_level = float(fakeout_pivot)
        high_extreme = float(window_max_high) if window_max_high is not None else pivot_level * 1.01
        cand_level = high_extreme
        box_top = high_extreme
        box_bottom = pivot_level

    if box_top <= box_bottom:
        return None

    sig = _append_wave_sig(
        cfg,
        w_dir=wave_dir,
        pivot_level=pivot_level,
        cand_level=cand_level,
        box_top=box_top,
        box_bottom=box_bottom,
        pivot_bar_idx=fakeout_bar_idx,
        cand_bar_idx=activation_bar_idx,
        wave_time_str=wave_time_str,
    )
    if sig is None:
        return None

    sig["wave_origin"] = WAVE_ORIGIN_WF
    sig["wf_wave_position"] = True
    return sig


def resume_classic_waves_after_wf(
    df: pd.DataFrame,
    cfg: BotConfig,
    wf_wave: dict,
) -> tuple[list, dict]:
    """
    Po dokončení WF vlny (draw_right) spustí klasickou Pine detekci
    navázanou na geometrii WF — vrátí nové klasické vlny + birth mapu.
    """
    from strategy.wave_detection_pine import run_pine_wave_simulation_from_seed

    return run_pine_wave_simulation_from_seed(df, cfg, wf_wave)


# ---------------------------------------------------------------------------
# Live loop: jednorázový scan z df (bez inkrementálního state)
# ---------------------------------------------------------------------------

def evaluate_wf_from_df(
    df: pd.DataFrame,
    last_wave: Optional[dict],
    cfg: BotConfig,
) -> Optional[dict]:
    """
    Vyhodnotí WF podmínky přes df (pro live loop, který nemá bar-by-bar stav).

    Používá stejný WickFakeoutTracker jako backtest (referenční HIGH/LOW,
    dynamické prohlubování low v okně). Okno od draw_right+1 po poslední bar.
    """
    if not bool(getattr(cfg, "wf_enabled", False)):
        return None
    if last_wave is None or df is None or df.empty:
        return None

    wave_dir = int(last_wave.get("dir", 0))
    if wave_dir not in (1, -1):
        return None

    draw_right = int(last_wave.get("draw_right", -1))
    if draw_right < 0 or draw_right >= len(df) - 1:
        return None

    tracker = WickFakeoutTracker()
    tracker.on_new_wave(last_wave, birth_bar=draw_right, df=df)

    activation_idx = len(df) - 1
    last_result: Optional[dict] = None
    for i in range(draw_right + 1, activation_idx + 1):
        row = df.iloc[i]
        h = float(row["high"])
        l = float(row["low"])
        c = float(row["close"])
        tracker.on_bar(h, l, c, i)
        last_result = tracker.check_wf(c, i, cfg=cfg)
        if last_result is not None:
            if last_result.get("status") == "activate":
                last_result = dict(last_result)
                last_result["activation_bar_idx"] = i
            return last_result
    return None
