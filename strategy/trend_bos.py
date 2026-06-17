"""
TREND & BOS (Break of Structure) — modul stavu trendu nad detekovanymi vlnami.

Slouzi jako post-processing nad vystupem `strategy.wave_detection.detect_waves`
(Varianta B z planu — bez zasahu do Pine emulatoru). Pouziva ho live bot
(`runtime.live_loop`) i backtester (`backtest.engine`) stejnym zpusobem,
aby chovani filtru bylo bit-perfect identicke v obou prostredich.

KLICOVE POJMY
-------------
- Bull trend  : platne dokud ZADNA svicka NEZAVRE pod LOW posledni potvrzene
                UP vlny (= jeji `box_bottom`, coz je pivot/zacatek UP impulsu).
- Bear trend  : platne dokud ZADNA svicka NEZAVRE nad HIGH posledni potvrzene
                DOWN vlny (= jeji `box_top`).
- BOS (Break of Structure):
                Close-based prurazka swing levelu definovaneho vyse. V okamziku
                breakoutu se trend prohlasi za otoceny a swing-historie pro
                navazujici HH/HL kontrolu se resetuje.
- Neutral     : Vychozi stav pred prvni potvrzenou vlnou. Pokud je
                `trend_filter_enabled=True`, v neutralnim stavu se nic neotevira.

VEREJNE API
-----------
- `TrendState`                       — dataclass snapshot stavu trendu.
- `compute_trend_states_per_wave`    — projde df bar-by-bar, pro kazdou vlnu
                                       vrati snapshot trend stavu v MOMENTE
                                       jejiho narozeni (= pred propsanim
                                       vlny do stavu).
- `wave_allowed_for_entry`           — vyhodnoti dvojici filtru
                                       (smer trendu + HH/HL) podle BotConfig.
- `bos_triggered_close`              — nizkourovnova close-based BOS kontrola
                                       pro pouziti v engine smyckach nebo
                                       live BOS exit logice (Faze B).

POZNAMKA K MEZNI SITUACI BOS NA STEJNEM BARU JAKO NOVA VLNA
-----------------------------------------------------------
Pine emulator potvrzuje vlnu na zaver baru (close), stejne tak se BOS
vyhodnocuje close-based. Pokud na jednom baru DOJDE k BOS *i* k narozeni
vlny v opacnem smeru, BOS se vyhodnocuje JAKO PRVNI (trend se otoci) a
nova vlna pak vidi uz prevracenou strukturu (typicky bude rovnou
"prvni vlna noveho trendu" → bez HH/HL omezeni).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

import pandas as pd

from config.bot_config import BotConfig
from config.enums import TPMode
from strategy.wave_detection_pine import compute_wave_birth_bars_pine


def _trend_market_arrays(df: pd.DataFrame):
    """Numpy close + time + n (sdilene s backtest.ohlc_arrays cache na df)."""
    from backtest.ohlc_arrays import ohlc_from_dataframe

    ohlc = ohlc_from_dataframe(df)
    return ohlc.close, ohlc.time, ohlc.n


def _time_at(times, index: int):
    return times[index]


def tp_mode_uses_bos_per_bar_exit(cfg: BotConfig) -> bool:
    """
    Rezimy, ktere maji per-bar BOS exit logiku (uzavreni pozic + ruseni pendingu
    pri close-based prurazu swing levelu):
      - BOS_EXIT             (broker TP = RRR safety, exit hlavni cestou pres BOS)
      - BOS_EXIT_PRIORITY    (zadny TP, jen SL nebo BOS)
      - WAVE_TARGET_N        (TP az od N-te vlny, pak BOS / SL)
    """
    tpm = getattr(cfg, "tp_mode", TPMode.RRR_FIXED)
    if isinstance(tpm, str):
        try:
            tpm = TPMode(tpm)
        except ValueError:
            return False
    return tpm in (
        TPMode.BOS_EXIT,
        TPMode.BOS_EXIT_PRIORITY,
        TPMode.WAVE_TARGET_N,
        TPMode.WAVE_TARGET_N_G,
    )


def _cfg_tp_mode(cfg: BotConfig) -> TPMode:
    tpm = getattr(cfg, "tp_mode", TPMode.RRR_FIXED)
    if isinstance(tpm, TPMode):
        return tpm
    try:
        return TPMode(str(tpm))
    except ValueError:
        return TPMode.RRR_FIXED


def bos_entry_in_rrr_fixed_enabled(cfg: BotConfig) -> bool:
    """BOS entry (WAVE_BOS) jen pro tp_mode=RRR_FIXED a explicitni prepinac."""
    return (
        _cfg_tp_mode(cfg) == TPMode.RRR_FIXED
        and bool(getattr(cfg, "bos_entry_in_rrr_fixed", False))
    )


def bos_flip_handler_should_run(
    cfg: BotConfig, *, close_pos: bool, cancel_pend: bool,
) -> bool:
    """Per-bar BOS handler: exit/cancel dle tp_mode+pcm, nebo jen entry pro RRR flag."""
    return bool(close_pos) or bool(cancel_pend) or bos_entry_in_rrr_fixed_enabled(cfg)


def bos_entry_should_open_on_flip(cfg: BotConfig) -> bool:
    """
    Otevrit MARKET BOS re-entry po close-based flipu.
    RRR_FIXED: bos_entry_enable (legacy, napr. pcm=trend) nebo bos_entry_in_rrr_fixed.
    Ostatni tp_mode: jen bos_entry_enable / bos_reentry_enabled.
    """
    legacy = bool(getattr(cfg, "bos_entry_enable", False)) or bool(
        getattr(cfg, "bos_reentry_enabled", False)
    )
    if _cfg_tp_mode(cfg) == TPMode.RRR_FIXED:
        return legacy or bos_entry_in_rrr_fixed_enabled(cfg)
    return legacy


def bos_per_bar_close_reason(cfg: BotConfig) -> str:
    """Textovy duvod uzavreni pozice pri BOS (backtest / MT5 comment)."""
    tpm = getattr(cfg, "tp_mode", TPMode.RRR_FIXED)
    if isinstance(tpm, str):
        try:
            tpm = TPMode(tpm)
        except ValueError:
            return "BOS_EXIT"
    if tpm == TPMode.BOS_EXIT_PRIORITY:
        return "BOS_EXIT_PRIORITY"
    if tpm in (TPMode.WAVE_TARGET_N, TPMode.WAVE_TARGET_N_G):
        return "BOS_EXIT_WAVE_TARGET"
    return "BOS_EXIT"


# ----------------------------------------------------------------------------
# Datova struktura: snapshot trendu v jednom okamziku
# ----------------------------------------------------------------------------

@dataclass
class TrendState:
    """
    Snapshot stavu trendu k jednomu okamziku (typicky k baru narozeni vlny).

    Pole `last_up_*` / `last_down_*` ukazuji na POSLEDNI HH/HL-validni vlnu daneho
    smeru *v ramci aktualne bezicho trendu* (pri `trend_hh_hl_filter_enabled=True`;
    jinak posledni potvrzenou vlnu). Po BOS se historie vyresetuje
    (last_up_* / last_down_* = None), takze HH/HL filter dostane v prvni vlne
    noveho trendu "no comparison" → povoli ji.

    `last_up_box_bottom` je swing level pro BOS dolu (close < tato hodnota).
    `last_down_box_top`  je swing level pro BOS nahoru (close > tato hodnota).
    """
    # bull | bear | neutral (neutral = pred prvni potvrzenou vlnou, nebo
    # explicitne po system resetu — beznym chodem se v "neutral" nevracime).
    direction: str = "neutral"

    # Posledni UP vlna v aktualnim trendu (definuje swing low pro BOS dolu
    # a baseline pro HH/HL u dalsi UP vlny).
    last_up_box_top: Optional[float] = None
    last_up_box_bottom: Optional[float] = None
    last_up_wave_time: Optional[str] = None

    # Posledni DOWN vlna v aktualnim trendu (definuje swing high pro BOS nahoru
    # a baseline pro LL/LH u dalsi DOWN vlny).
    last_down_box_top: Optional[float] = None
    last_down_box_bottom: Optional[float] = None
    last_down_wave_time: Optional[str] = None

    # Pocet vln v aktualnim trendu (po BOS resetovan na 0). Pouziva se primarne
    # pro vyhodnoceni "prvni vlna v trendu" v HH/HL filtru.
    up_waves_in_trend: int = 0
    down_waves_in_trend: int = 0

    # Vznikla referencni `last_up_*` / `last_down_*` vlna pres WF aktivaci?
    # WF box_top/box_bottom na opacne strane je fakeout WICK (nejvyssi/nejnizsi
    # wick okna), ne cisty strukturalni swing. Sekundarni HH/HL podminka
    # (LH u DOWN, HL u UP) vuci wicku je proto neprimerene prisna a utne prave tu
    # trend-pokracujici vlnu, kterou ma WF "dotahnout". Pri True povolime nasledujici
    # trend-vlnu uz jen na novem extremu (LL/HH).
    last_up_from_wf: bool = False
    last_down_from_wf: bool = False

    is_bos_wave_pending: bool = False

    # True pokud PRVNI vlna aktualniho trendu byla EXT vlna (= EXT vlna idx 1).
    # V takovem trendu se PRVNI klasicky close-based BOS "odpusti" (neotaci
    # trend, neotevira/nerusi BOS pozice) — viz `_bos_close_flip_with_forgive`.
    # One-shot: po odpusteni prvniho BOS se vynuluje a dalsi BOS uz flipne.
    # Pokud ma EXT v trendu cislo 2+, tato ochrana neplati (flag zustava False).
    trend_established_by_ext: bool = False


# ----------------------------------------------------------------------------
# Nizkourovnova BOS kontrola
# ----------------------------------------------------------------------------

def bos_triggered_close(direction: str,
                        swing_level: Optional[float],
                        bar_close: float) -> bool:
    """
    Close-based detekce BOS pro jeden bar.

    Args:
        direction:    aktualni smer trendu ("bull" | "bear" | "neutral")
        swing_level:  level, jehoz prurazka definuje BOS:
                      - bull → `TrendState.last_up_box_bottom` (low UP vlny)
                      - bear → `TrendState.last_down_box_top`  (high DOWN vlny)
        bar_close:    close svicky aktualne hodnoceneho baru

    Returns:
        True pokud doslo k BOS (close pod/nad swing levelem), jinak False.
        Pri direction="neutral" nebo swing_level=None vraci False.
    """
    if swing_level is None:
        return False
    if direction == "bull":
        return bar_close < float(swing_level)
    if direction == "bear":
        return bar_close > float(swing_level)
    return False


# ----------------------------------------------------------------------------
# Hlavni vypocet: pro kazdou vlnu snapshot trendu k baru jejiho narozeni
# ----------------------------------------------------------------------------

def _wave_is_wf_origin(wave: dict) -> bool:
    """True pokud vlna vznikla pres WF aktivaci (synteticka continuation)."""
    if bool(wave.get("wf_wave_position", False)):
        return True
    if bool(wave.get("is_wf", False)):
        return True
    try:
        from strategy.wick_fakeout import WAVE_ORIGIN_WF

        if str(wave.get("wave_origin", "")) == WAVE_ORIGIN_WF:
            return True
    except Exception:
        pass
    return "wf" in str(wave.get("wave_origin", "")).lower()


def build_wf_bos_freeze_ranges(waves: List[dict]) -> List[tuple[int, int]]:
    """
    Bary, kde je BOS uplne vypnuty — `[draw_left, draw_right]` kazde WF vlny.
    BOS na WF vubec ne (ani flip, ani swing z WF boxu).
    """
    ranges: List[tuple[int, int]] = []
    for w in waves or []:
        if not _wave_is_wf_origin(w):
            continue
        try:
            dl = int(w.get("draw_left", 0))
            dr = int(w.get("draw_right", dl))
        except (TypeError, ValueError):
            continue
        if dr >= dl:
            ranges.append((dl, dr))
    ranges.sort()
    merged: List[tuple[int, int]] = []
    for lo, hi in ranges:
        if merged and lo <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def bar_in_wf_bos_freeze(
    bar_idx: int,
    wf_bos_freeze_ranges: Optional[Sequence[tuple[int, int]]],
) -> bool:
    """True pokud je bar uvnitr WF freeze okna (BOS se nevyhodnocuje)."""
    if not wf_bos_freeze_ranges:
        return False
    bi = int(bar_idx)
    for lo, hi in wf_bos_freeze_ranges:
        if lo <= bi <= hi:
            return True
    return False


def _bos_close_flip_direction(state: TrendState, bar_close: float) -> int:
    """
    Close-based BOS flip: 0 = nic, -1 = bull→bear, +1 = bear→bull.

    Swing level z WF continuation vlny se pro BOS nepouziva (`last_*_from_wf`).
    Pro freeze BOS uvnitr WF okna se pouziva post-filter ve `compute_bos_wave_flip_map`
    a `collect_bos_flip_events`, ne tento state-machine helper.
    """
    if state.direction == "bull":
        if state.last_up_from_wf:
            return 0
        if bos_triggered_close("bull", state.last_up_box_bottom, bar_close):
            return -1
    elif state.direction == "bear":
        if state.last_down_from_wf:
            return 0
        if bos_triggered_close("bear", state.last_down_box_top, bar_close):
            return 1
    return 0


def _bos_close_flip_with_forgive(
    state: TrendState, bar_close: float
) -> Tuple[int, TrendState]:
    """
    Close-based BOS flip s pravidlem "odpust PRVNI BOS po EXT vlne idx 1".

    Vraci (flipped_to, new_state):
      - flipped_to = 0  → zadny zaznamenany flip (vc. odpusteneho prvniho BOS),
      - flipped_to = -1 → bull→bear, +1 → bear→bull.

    Pokud `state.trend_established_by_ext` (trend zalozila EXT vlna idx 1) a doslo
    by k BOS, prvni takovy BOS se ODPUSTI: trend se NEotoci, jen se vynuluje
    prorazeny swing level (aby se stejny prurazil neopakoval) a one-shot flag se
    spotrebuje. Dalsi (novy) BOS uz flipne normalne. Tim se v obchodnim jadre
    nezalozi/neuzavre BOS pozice na prvnim BOS a trend dal "bezi".
    """
    flip = _bos_close_flip_direction(state, bar_close)
    if flip == 0:
        return 0, state
    if state.trend_established_by_ext:
        ns = replace(state, trend_established_by_ext=False)
        if state.direction == "bull":
            ns.last_up_box_bottom = None
        else:
            ns.last_down_box_top = None
        return 0, ns
    if flip == -1:
        return -1, TrendState(direction="bear")
    return 1, TrendState(direction="bull")


def _apply_bos_close_flip(state: TrendState, bar_close: float) -> TrendState:
    """Aplikuje close-based BOS flip (reset po flipu). WF swing level ignorovan."""
    return _bos_close_flip_with_forgive(state, bar_close)[1]


def _maybe_seed_state_from_ext_post_trend(state: TrendState, wave: dict) -> TrendState:
    """
    Druha vlna stejneho smeru po EXT muze zalozit novy trend i bez BOS flipu.
    V tom pripade resetujeme stav a zacneme merit strukturu od teto vlny.
    """
    raw = wave.get("ext_post_trend_seed_dir")
    try:
        seed_dir = int(raw)
    except (TypeError, ValueError):
        seed_dir = 0
    if seed_dir == 1:
        return TrendState(direction="bull")
    if seed_dir == -1:
        return TrendState(direction="bear")
    return state


def _build_waves_by_extreme_bar(waves: List[dict], n: int) -> Dict[int, List[dict]]:
    """Index vln podle baru extremu (`draw_right`) — stejne jako wave_sequence."""
    out: Dict[int, List[dict]] = {}
    for w in waves:
        try:
            dr = int(w["draw_right"])
        except (KeyError, TypeError, ValueError):
            continue
        if dr < 0 or dr >= n:
            continue
        out.setdefault(dr, []).append(w)
    return out


def _advance_bos_timeline_bar(
    state: TrendState,
    bar_close: float,
    bar_ix: int,
    *,
    cfg: BotConfig,
    waves_by_extreme_bar: Dict[int, List[dict]],
    waves_by_birth_bar: Dict[int, List[dict]],
    birth_dir_last_seen: Optional[Dict[int, str]] = None,
) -> tuple[TrendState, int]:
    """
    Jeden bar BOS timeline: close flip, pak swing update na draw_right,
    pak seed + doplneni na birth (bez dvojite aplikace na stejnem baru).

    Sjednocuje `compute_trend_states_per_wave`, flip mapu a close-flip iteraci.
    """
    if birth_dir_last_seen is not None:
        for w in waves_by_birth_bar.get(bar_ix, []):
            if bool(w.get("post_ext_trend_suppressed", False)):
                continue
            wdir = int(w.get("dir", 0))
            if wdir in (1, -1):
                birth_dir_last_seen[wdir] = str(w["wave_time"])

    flipped_to, state = _bos_close_flip_with_forgive(state, bar_close)

    for w in waves_by_extreme_bar.get(bar_ix, []):
        if bool(w.get("post_ext_trend_suppressed", False)):
            continue
        if w.get("ext_post_range_terminator"):
            # §1.4 / CESTA D: po ukončení EXT (W2…) už neplatí forgive prvního BOS
            # z původní EXT1 — WAVE_BOS z terminátoru musí projít.
            state.trend_established_by_ext = False
        maybe_update_trend_state_with_wave(state, w, cfg)

    for w in waves_by_birth_bar.get(bar_ix, []):
        state = _maybe_seed_state_from_ext_post_trend(state, w)
        if bool(w.get("post_ext_trend_suppressed", False)):
            continue
        try:
            dr = int(w.get("draw_right", bar_ix))
        except (TypeError, ValueError):
            dr = bar_ix
        if dr != bar_ix:
            maybe_update_trend_state_with_wave(state, w, cfg)

    return state, flipped_to


def _detect_close_bos_timeline_flips(
    df: pd.DataFrame,
    waves: List[dict],
    cfg: BotConfig,
    *,
    wave_birth_bars: Optional[Dict[str, int]] = None,
) -> List[tuple[int, int]]:
    """Seznam (flip_bar, flipped_to) kde flipped_to je 1=bull, -1=bear."""
    if df is None or df.empty or not waves:
        return []

    if wave_birth_bars is None:
        wave_birth_bars = compute_wave_birth_bars_pine(df, cfg)

    n = len(df)
    waves_by_birth_bar: Dict[int, List[dict]] = {}
    for w in waves:
        birth = wave_birth_bars.get(w["wave_time"])
        if birth is None:
            continue
        waves_by_birth_bar.setdefault(int(birth), []).append(w)
    waves_by_extreme_bar = _build_waves_by_extreme_bar(waves, n)

    state = TrendState()
    closes = df["close"].astype(float).to_numpy()
    flips: List[tuple[int, int]] = []

    for i in range(n):
        state, flipped_to = _advance_bos_timeline_bar(
            state,
            float(closes[i]),
            i,
            cfg=cfg,
            waves_by_extreme_bar=waves_by_extreme_bar,
            waves_by_birth_bar=waves_by_birth_bar,
        )
        if flipped_to != 0:
            flips.append((i, flipped_to))

    return flips


def reconcile_bos_flip_map_with_wave_sequence(
    flip_map: Dict[int, str],
    flips: List[tuple[int, int]],
    waves: List[dict],
    wave_sequence_info: Dict[str, Any],
    wave_birth_bars: Dict[str, int],
    *,
    max_bars_after_flip: int = 32,
) -> Dict[int, str]:
    """
    Doplni / opravi BOS atribuci podle `wave_sequence_info.is_bos_wave`.

    Phase B flip mapy muze pripsat starou protisměrnou vlnu (napr. pred WAVE4
    break); wave_sequence zna skutecnou BOS seed vlnu noveho trendu.
    """
    if not flips or not wave_sequence_info:
        return dict(flip_map)

    wt_to_dir = {str(w.get("wave_time", "")): int(w.get("dir", 0)) for w in waves}
    flip_by_bar = {int(fb): int(ft) for fb, ft in flips}
    out = dict(flip_map)

    for wt_raw, info in wave_sequence_info.items():
        wt = str(wt_raw)
        if not getattr(info, "is_bos_wave", False):
            continue
        if wt in out.values():
            continue
        try:
            birth = int(wave_birth_bars[wt])
        except (KeyError, TypeError, ValueError):
            continue
        wdir = wt_to_dir.get(wt)
        if wdir not in (1, -1):
            continue

        best_flip: Optional[int] = None
        for fb in sorted(flip_by_bar):
            if fb >= birth:
                break
            if flip_by_bar[fb] != wdir:
                continue
            if birth - fb <= int(max_bars_after_flip):
                best_flip = fb

        if best_flip is None:
            continue

        old_wt = out.get(best_flip)
        if old_wt is not None and old_wt != wt:
            old_info = wave_sequence_info.get(str(old_wt))
            if getattr(old_info, "is_bos_wave", False):
                continue
        out[best_flip] = wt

    return out


def compute_bos_wave_flip_map(df: pd.DataFrame,
                              waves: List[dict],
                              cfg: BotConfig,
                              *,
                              wave_birth_bars: Optional[Dict[str, int]] = None,
                              ) -> Dict[int, str]:
    """
    Vrati mapu {bar_index_flipu: wave_time bos-vlny}, kde bos-vlna je
    POSLEDNI dosud potvrzena vlna ve smeru noveho trendu k okamziku BOS flipu.

    Jedna BOS vlna na flip; bez flipu vraci {}. Pouziva se pro:
      * retro-aktivaci vstupu z bos-vlny (i pri trend_filter_enabled),
      * vizualizaci (bos-vlna se vzdy zobrazi i kdyz neprochazi HH/HL filtrem).

    `wave_birth_bars` lze predat, pokud uz je spocteno (engine), jinak se
    dopocita pres `compute_wave_birth_bars_pine` (zhruba cena jednoho
    Pine simulace).
    """
    if df is None or df.empty or not waves:
        return {}

    if wave_birth_bars is None:
        wave_birth_bars = compute_wave_birth_bars_pine(df, cfg)

    waves_by_birth_bar: Dict[int, List[dict]] = {}
    for w in waves:
        birth = wave_birth_bars.get(w["wave_time"])
        if birth is None:
            continue
        waves_by_birth_bar.setdefault(int(birth), []).append(w)

    flips = _detect_close_bos_timeline_flips(
        df, waves, cfg, wave_birth_bars=wave_birth_bars
    )

    # FAZE B — pro kazdy flip vyber BOS-vlnu = nejnovejsi strukturalni vlnu ve
    # smeru noveho trendu, jejiz IMPULZ (draw_left) zacal nejpozdeji na baru
    # flipu. Tim se opravuji dva tridy chyb:
    #   * BOS pripsan stare drobne proti-trendove vlne, protoze skutecna
    #     strukturalni vlna se narodi par baru PO flipu (potvrzeni se zpozdi),
    #   * BOS pripsan davne vlne, protoze ta spravna byla post_ext_trend_suppressed
    #     a stara logika ji vyloucila — pritom prave ona prorazila strukturu.
    # Retro-vstup (`_bos_flip_wave_by_bar`) se kotvi na flip_bar — vstup ve chvili
    # close-based BOS, ne az pri pozdejsim narozeni seed vlny (kauzalni).
    dir_waves: Dict[int, List[tuple[int, int, int, str]]] = {1: [], -1: []}
    for w in waves:
        wt = str(w["wave_time"])
        birth = wave_birth_bars.get(wt)
        if birth is None:
            continue
        wdir = int(w.get("dir", 0))
        if wdir not in (1, -1):
            continue
        if _wave_is_wf_origin(w):
            continue
        dl = int(w.get("draw_left", birth))
        dr = int(w.get("draw_right", birth))
        dir_waves[wdir].append((dl, dr, int(birth), wt))

    # Okno potvrzeni: vlna jejiz extrem/narozeni je tesne ZA flipem stale patri
    # k temuz strukturalnimu pohybu (potvrzeni vlny se zpozduje o opacne svice).
    confirm_window = 8
    out: Dict[int, str] = {}
    for flip_bar, flipped_to in flips:
        cands = [
            (dr, birth, wt)
            for (dl, dr, birth, wt) in dir_waves[flipped_to]
            if dr <= flip_bar and birth <= flip_bar + confirm_window
        ]
        if not cands:
            # fallback: posledni vlna ve smeru narozena do flipu (jakkoliv stara).
            cands = [
                (dr, birth, wt)
                for (dl, dr, birth, wt) in dir_waves[flipped_to]
                if dr <= flip_bar and birth <= flip_bar
            ]
        if cands:
            # BOS-vlna = ta, jejiz EXTREM (draw_right) je nejbliz baru flipu —
            # tj. vlna, ktera prave ted prorazila strukturu. NE "max draw_right"
            # (to by chytalo velkou obalujici vlnu, ktera zacala davno a sahá
            # daleko za flip). Tie-break: pozdejsi extrem, pak pozdejsi narozeni.
            dr, birth, wt = min(
                cands, key=lambda c: (abs(c[0] - flip_bar), -c[0], -c[1])
            )
            out[flip_bar] = wt

    # Post-filter: zadny BOS flip nesmi byt uvnitr WF okna (BOS na WF vubec ne).
    wf_freeze = build_wf_bos_freeze_ranges(waves)
    if wf_freeze:
        out = {
            bar_ix: wt
            for bar_ix, wt in out.items()
            if not bar_in_wf_bos_freeze(bar_ix, wf_freeze)
        }

    return out


def compute_bos_wave_times(df: pd.DataFrame,
                           waves: List[dict],
                           cfg: BotConfig,
                           *,
                           wave_birth_bars: Optional[Dict[str, int]] = None,
                           ) -> set:
    """Mnozina wave_time vsech bos-vln (viz `compute_bos_wave_flip_map`)."""
    return set(
        compute_bos_wave_flip_map(
            df, waves, cfg, wave_birth_bars=wave_birth_bars
        ).values()
    )


def compute_trend_states_per_wave(df: pd.DataFrame,
                                  waves: List[dict],
                                  cfg: BotConfig) -> Dict[str, TrendState]:
    """
    Projde `df` bar-by-bar a postavi state machine trendu nad uz detekovanymi
    vlnami. Pro kazdou vlnu vrati snapshot `TrendState` k okamziku, kdy vlna
    dosahla sveho EXTREMU (`draw_right`) — NIKOLI k okamziku jejiho
    potvrzeni (`birth_bar`).

    Proc draw_right misto birth_bar?
      Pine emulator potvrzuje vlnu az po `min_opp_bars` opp barech ZA
      extremem. Mezi extremem a potvrzenim (typicky 3+ bary) muze pri
      rychle protismerne recovery prijit BOS flip swingove urovne starsi
      same-dir vlny. Vlna pak vznikne v okamziku, kdy uz trend "patri" do
      opacne strany — i kdyz jeji vlastni move probehl jeste v puvodnim
      trendu. Pouzitim draw_right snapshotu (= okamzik extremu, pred
      pripadnym BOS) se vlna klasifikuje podle smeru trendu v dobe jejiho
      vlastniho pohybu, coz odpovida vizualnimu pohledu na grafu.

      Snapshot se bere PRED aplikaci vlny na stav — filter pak vidi
      "predchozi" same-dir vlnu jako last_*.

    Algoritmus:
      pro kazdy bar i v df:
        1) zkontrolovat BOS proti aktualnimu trendu — pokud nastal, flip
           trendu + reset last_up_* / last_down_*
        2) pro kazdou vlnu s `draw_right == i` ulozit snapshot stavu
           a teprve potom vlnou stav aktualizovat

    Args:
        df:    DataFrame s OHLC + sloupcem `time` (stejny vstup jako pro
               detect_waves).
        waves: vystup `detect_waves(df, cfg)` (seznam dictu s `wave_time`,
               `dir`, `box_top`, `box_bottom`, `draw_right`, ...).
        cfg:   BotConfig.

    Returns:
        dict {wave_time: TrendState} kde wave_time je presne ten string, ktery
        je v `wave["wave_time"]`. Vlny bez `draw_right` se ve vystupu
        neobjevi.
    """
    if df is None or df.empty or not waves:
        return {}

    closes, _times, n = _trend_market_arrays(df)
    waves_by_extreme_bar: Dict[int, List[dict]] = {}
    for w in waves:
        try:
            dr = int(w["draw_right"])
        except (KeyError, TypeError, ValueError):
            continue
        if dr < 0 or dr >= n:
            continue
        waves_by_extreme_bar.setdefault(dr, []).append(w)

    state = TrendState()
    result: Dict[str, TrendState] = {}

    for i in range(n):
        bar_close = float(closes[i])

        # 1) BOS check proti aktualnimu trendu
        state = _apply_bos_close_flip(state, bar_close)
        # direction == "neutral" → zatim zadny swing level, BOS nemuze nastat

        # 2) Vlny s extremem na tomto baru
        new_waves = waves_by_extreme_bar.get(i)
        if not new_waves:
            continue

        for w in new_waves:
            state = _maybe_seed_state_from_ext_post_trend(state, w)
            # snapshot PRED propsanim vlny → filter pak vidi "predchozi" vlnu jako last_*
            result[w["wave_time"]] = replace(state)
            maybe_update_trend_state_with_wave(state, w, cfg)

    return result


def recompute_trend_states_per_wave_from_bar(
    df: pd.DataFrame,
    waves: List[dict],
    cfg: BotConfig,
    from_bar: int,
    *,
    previous: Dict[str, TrendState],
    drop_wave_times: Set[str] | None = None,
) -> Dict[str, TrendState]:
    """
    Inkrementalni prepocet po WF merge: zachova snapshoty vln s draw_right < from_bar,
    zbytek prepocte stejnym algoritmem jako compute_trend_states_per_wave.
    """
    if df is None or df.empty or not waves:
        return {}
    drop = drop_wave_times or set()
    result = {
        str(k): v for k, v in previous.items() if str(k) not in drop
    }
    for w in waves:
        try:
            dr = int(w.get("draw_right", -1))
        except (TypeError, ValueError):
            continue
        if dr >= from_bar:
            result.pop(str(w.get("wave_time", "")), None)

    closes, _times, n = _trend_market_arrays(df)
    waves_by_extreme_bar: Dict[int, List[dict]] = {}
    for w in waves:
        try:
            dr = int(w["draw_right"])
        except (KeyError, TypeError, ValueError):
            continue
        if dr < 0 or dr >= n:
            continue
        waves_by_extreme_bar.setdefault(dr, []).append(w)

    state = TrendState()
    for i in range(n):
        bar_close = float(closes[i])
        state = _apply_bos_close_flip(state, bar_close)
        new_waves = waves_by_extreme_bar.get(i)
        if not new_waves:
            continue
        for w in new_waves:
            state = _maybe_seed_state_from_ext_post_trend(state, w)
            wt = str(w["wave_time"])
            if i >= from_bar:
                result[wt] = replace(state)
            maybe_update_trend_state_with_wave(state, w, cfg)

    return result


def tag_waves_hh_hl_pass(
    df: pd.DataFrame,
    waves: List[dict],
    cfg: BotConfig,
) -> None:
    """
    Pro kazdou vlnu nastavi wave["hh_hl_pass"] = True/False podle HH/HL
    strukturalniho filtru v trendu, ve kterem vznikla (extrem na draw_right).

    Pravidla:
      - trend_hh_hl_filter_enabled=False → vsechny vlny hh_hl_pass=True
      - neutral trend → True
      - prvni trend-dir vlna po BOS / seed → True (bez srovnani)
      - bull UP / bear DOWN → True jen pri HH+HL / LL+LH
      - counter-trend → False
      - post_ext_trend_suppressed → False
    """
    if not waves:
        return
    if not getattr(cfg, "trend_hh_hl_filter_enabled", False):
        for w in waves:
            w["hh_hl_pass"] = True
        return
    if df is None or df.empty:
        for w in waves:
            w["hh_hl_pass"] = True
        return

    closes, _times, n = _trend_market_arrays(df)
    waves_by_extreme_bar: Dict[int, List[dict]] = {}
    for w in waves:
        try:
            dr = int(w["draw_right"])
        except (KeyError, TypeError, ValueError):
            continue
        if dr < 0 or dr >= n:
            continue
        waves_by_extreme_bar.setdefault(dr, []).append(w)

    state = TrendState()

    for i in range(n):
        bar_close = float(closes[i])
        state = _apply_bos_close_flip(state, bar_close)

        new_waves = waves_by_extreme_bar.get(i)
        if not new_waves:
            continue

        for w in new_waves:
            if bool(w.get("post_ext_trend_suppressed", False)):
                w["hh_hl_pass"] = False
                continue

            state = _maybe_seed_state_from_ext_post_trend(state, w)
            wdir = int(w["dir"])
            trend_dir = state.direction

            if trend_dir == "neutral":
                w["hh_hl_pass"] = True
            else:
                is_trend_dir = (
                    (wdir == 1 and trend_dir == "bull")
                    or (wdir == -1 and trend_dir == "bear")
                )
                if not is_trend_dir:
                    w["hh_hl_pass"] = False
                else:
                    w["hh_hl_pass"] = _wave_passes_hh_hl_structure_live(state, w)

            maybe_update_trend_state_with_wave(state, w, cfg)

    for w in waves:
        if "hh_hl_pass" not in w:
            w["hh_hl_pass"] = True


def _wave_passes_hh_hl_structure_live(state: TrendState, wave: dict) -> bool:
    """
    UP: HH+HL vuci predchozi UP (`last_up_*`); DOWN: LL+LH vuci predchozi DOWN.
    Bez predchozi vlny stejneho smeru ve stavu → True (prvni v trendu).

    Vyjimka WF: pokud je referencni vlna stejneho smeru WF continuation, jeji
    box edge na opacne strane je fakeout WICK (ne cisty swing), takze sekundarni
    podminku (LH u DOWN / HL u UP) vynechame a pozadujeme jen novy extrem
    (LL u DOWN / HH u UP). Tim necht WF prirozene "dotahne" trend-pokracujici
    vlnu az na nove low/high misto utnuti o par pipu na wicku.
    """
    wdir = int(wave["dir"])
    wbt = float(wave["box_top"])
    wbb = float(wave["box_bottom"])
    if wdir == 1:
        prev_top = state.last_up_box_top
        prev_bot = state.last_up_box_bottom
        if prev_top is None or prev_bot is None:
            return True
        if state.last_up_from_wf:
            return wbt > prev_top
        return wbt > prev_top and wbb > prev_bot
    prev_top = state.last_down_box_top
    prev_bot = state.last_down_box_bottom
    if prev_top is None or prev_bot is None:
        return True
    if state.last_down_from_wf:
        return wbb < prev_bot
    return wbb < prev_bot and wbt < prev_top


def _wave_passes_hh_hl_structure(state: TrendState, wave: dict) -> bool:
    cached = wave.get("hh_hl_pass")
    if cached is not None:
        return bool(cached)
    return _wave_passes_hh_hl_structure_live(state, wave)


def _min_move_pct_for_bos_swing_update(wave: dict, cfg: BotConfig) -> float:
    """Prah velikosti vlny pro aktualizaci BOS swingu (EXT both-sides snizuje v okne)."""
    in_ext = bool(wave.get("in_ext_range", False))
    if in_ext and getattr(cfg, "wave_min_pct_enable", False):
        try:
            return float(getattr(cfg, "ext_post_both_sides_wave_min_pct", 0.13))
        except (TypeError, ValueError):
            pass
    return float(getattr(cfg, "wave_min_pct", 0.26))


def _wave_move_pct_below_swing_threshold(
    wave: dict,
    cfg: BotConfig,
) -> bool:
    raw = wave.get("move_pct")
    if raw is None:
        return False
    try:
        move = float(raw)
    except (TypeError, ValueError):
        return False
    return move < _min_move_pct_for_bos_swing_update(wave, cfg)


def should_update_trend_state_for_wave(state: TrendState,
                                      wave: dict,
                                      cfg: BotConfig) -> bool:
    """
    Ma potvrzena vlna posunout `last_up_*` / `last_down_*` (a tim BOS swing)?

    Pri `trend_hh_hl_filter_enabled=False` → vzdy ano (puvodni chovani).
    Pri True:
      - neutral / prvni vlna → ano;
      - trend-dir bez HH+HL / LL+LH → ne (sumova vlna);
      - trend-dir pod wave_min_pct kdyz uz existuje swing → ne (pine ripple);
      - counter-trend → ne (neposkvrnuje swing aktualniho trendu).
    """
    if not getattr(cfg, "trend_hh_hl_filter_enabled", False):
        return True
    wdir = int(wave["dir"])
    if state.direction == "neutral":
        return True
    # Vlna ukoncujici EXT §1.2 — musi nastavit swing pro nasledny WAVE_BOS.
    if bool(wave.get("ext_post_range_terminator", False)):
        if wdir == 1 and state.direction == "bull":
            return True
        if wdir == -1 and state.direction == "bear":
            return True
        return False
    # §1.4: uvnitř EXT both-sides okna se WAVE_BOS netvoří — neposouvat BOS swing.
    if bool(wave.get("in_ext_range", False)):
        return False
    if wdir == 1 and state.direction == "bull":
        if state.last_up_box_top is not None and _wave_move_pct_below_swing_threshold(
            wave, cfg
        ):
            return False
        return _wave_passes_hh_hl_structure(state, wave)
    if wdir == -1 and state.direction == "bear":
        if state.last_down_box_top is not None and _wave_move_pct_below_swing_threshold(
            wave, cfg
        ):
            return False
        return _wave_passes_hh_hl_structure(state, wave)
    return False


def maybe_update_trend_state_with_wave(state: TrendState,
                                       wave: dict,
                                       cfg: BotConfig) -> None:
    """Aktualizuje trend stav vlnou jen pokud `should_update_trend_state_for_wave`."""
    if should_update_trend_state_for_wave(state, wave, cfg):
        _update_state_with_wave(state, wave)
    if bool(wave.get("ext_post_range_terminator")):
        state.trend_established_by_ext = False


def filter_waves_for_structure_display(df: pd.DataFrame,
                                       waves: List[dict],
                                       cfg: BotConfig) -> List[dict]:
    """
    Vlny pro vizualizaci (scroll-combined, --visual-waves).

    Pri trend_hh_hl_filter_enabled=True vraci jen vlny, ktere posouvaji
    strukturu trendu / BOS swing (HH+HL / LL+LH v trendu, prvni vlna po BOS).
    Sumove a counter-trend vlny se na pozadi nevykresluji.
    EXT vlny (is_ext / move_pct >= ext_wave_min_pct) se vzdy vykresli modre pozadi.
    Pri vypnutem filtru vraci cely seznam beze zmeny.
    """
    if not getattr(cfg, "trend_hh_hl_filter_enabled", False):
        return list(waves)
    if df is None or df.empty or not waves:
        return []

    from strategy.wick_fakeout import WAVE_ORIGIN_WF

    wave_birth_bars = compute_wave_birth_bars_pine(df, cfg)
    waves_by_birth_bar: Dict[int, List[dict]] = {}
    for w in waves:
        birth = wave_birth_bars.get(w["wave_time"])
        if birth is None and str(w.get("wave_origin", "")) == WAVE_ORIGIN_WF:
            birth = int(w.get("draw_right", w.get("draw_left", 0)))
        if birth is None:
            continue
        waves_by_birth_bar.setdefault(int(birth), []).append(w)

    state = TrendState()
    keep_times: set[str] = set()
    closes = df["close"].astype(float).to_numpy()

    for i in range(len(df)):
        bar_close = float(closes[i])
        state = _apply_bos_close_flip(state, bar_close)

        for w in waves_by_birth_bar.get(i, []):
            state = _maybe_seed_state_from_ext_post_trend(state, w)
            if should_update_trend_state_for_wave(state, w, cfg):
                keep_times.add(str(w["wave_time"]))
            maybe_update_trend_state_with_wave(state, w, cfg)

    # BOS vlna (vlna ktera ZPUSOBI close-based flip trendu) musi byt vzdy
    # vykreslena — i kdyz je proti smeru aktualniho trendu a HH/HL filter ji
    # jinak zahodil. Jen TAHLE jedna vlna, zadne dalsi sumove vlny okolo.
    keep_times.update(
        compute_bos_wave_times(df, waves, cfg, wave_birth_bars=wave_birth_bars)
    )

    from strategy.ext_logic import is_ext_wave
    from strategy.ext_range import wave_in_ext_range, wave_post_ext_trend_suppressed
    from strategy.two_sided import wave_show_in_visual

    out: List[dict] = []
    for w in waves:
        # Post-EXT zamcena vlna proti seed-smeru neexistuje vizualne, ani kdyz
        # by ji jina cesta (vcetne BOS retro) protlacila.
        if wave_post_ext_trend_suppressed(w):
            continue
        wt = str(w.get("wave_time", ""))
        if wt in keep_times:
            out.append(w)
        elif is_ext_wave(w, cfg):
            out.append(w)
        elif wave_in_ext_range(w, cfg):
            out.append(w)
        elif wave_show_in_visual(w):
            out.append(w)
    return out


def _update_state_with_wave(state: TrendState, wave: dict) -> None:
    """
    Aktualizuje stav trendu o nove potvrzenou vlnu.
    """
    wdir = int(wave["dir"])
    is_wf = _wave_is_wf_origin(wave)
    # Prvni vlna trendu (stav cerstve po flipu/seedu/neutral) = idx 1. Pokud je
    # to EXT vlna, oznac trend jako "zalozeny EXT vlnou idx 1" → prvni klasicky
    # BOS se v tomto trendu odpusti (viz `_bos_close_flip_with_forgive`).
    if state.up_waves_in_trend == 0 and state.down_waves_in_trend == 0:
        if bool(wave.get("is_ext")):
            state.trend_established_by_ext = True
    if wdir == 1:
        if state.direction == "neutral":
            state.direction = "bull"
        state.up_waves_in_trend += 1
        if not is_wf:
            state.last_up_box_top = float(wave["box_top"])
            state.last_up_box_bottom = float(wave["box_bottom"])
            state.last_up_wave_time = str(wave["wave_time"])
            state.last_up_from_wf = False
    else:
        if state.direction == "neutral":
            state.direction = "bear"
        state.down_waves_in_trend += 1
        if not is_wf:
            state.last_down_box_top = float(wave["box_top"])
            state.last_down_box_bottom = float(wave["box_bottom"])
            state.last_down_wave_time = str(wave["wave_time"])
            state.last_down_from_wf = False


# ----------------------------------------------------------------------------
# Verejny filter — pouzivany v live_loop i backtest engine
# ----------------------------------------------------------------------------

def wave_allowed_for_entry(wave: dict,
                           trend_state: Optional[TrendState],
                           cfg: BotConfig) -> Tuple[bool, str]:
    """
    Rozhodne, zda vlna projde TREND FILTREM a (volitelne) HH/HL FILTREM podle
    konfigurace. Pouziva se shodne v live i v backtestu PRED odeslanim orderu.

    Filtry (oba se daji nezavisle vypnout v configu):

      cfg.trend_filter_enabled (bool, default False):
        - False  → filter VYPNUTY, funkce vzdy vrati (True, "trend_filter_disabled").
                   Vsechny vlny prochazi (stav pred zavedenim trend featur).
        - True   → obchoduje se POUZE ve smeru trendu:
                     bull trend  → povolene jen UP vlny (BUY)
                     bear trend  → povolene jen DOWN vlny (SELL)
                     neutral     → blokovano vse (nepotvrzena struktura)

      cfg.trend_hh_hl_filter_enabled (bool, default False):
        - Subfilter, aktivni POUZE pokud trend_filter_enabled=True.
        - False  → staci splnit smer trendu (libovolna vlna ve smeru projde).
        - True   → navic vyzaduje sekvencni pravidlo struktury; sumove vlny
                     neaktualizuji BOS swing (`maybe_update_trend_state_with_wave`):
                     bull trend, nova UP vlna:
                         box_top    > last_up.box_top      (Higher High)
                         box_bottom > last_up.box_bottom   (Higher Low)
                     bear trend, nova DOWN vlna:
                         box_bottom < last_down.box_bottom (Lower Low)
                         box_top    < last_down.box_top    (Lower High)
                     Prvni vlna v novem trendu (po BOS / po startu) nema
                     srovnani → vzdy povolena.

    Args:
        wave:        signal z `detect_waves` (musi mit `dir`, `box_top`,
                     `box_bottom`).
        trend_state: snapshot z `compute_trend_states_per_wave` pro tuto vlnu;
                     muze byt None, pokud filter zapnuty neni a snapshoty
                     se vubec nepocitaly.
        cfg:         BotConfig (cte `trend_filter_enabled`,
                     `trend_hh_hl_filter_enabled`).

    Returns:
        (allowed, reason)
        - allowed: True kdyz vlna projde vsemi zapnutymi filtry
        - reason:  textovy duvod (pro logging / debug counters), napr.:
                     "trend_filter_disabled" (filter vypnuty)
                     "trend_neutral"         (neutralni stav)
                     "wave_against_trend"    (smer nesouhlasi)
                     "no_hh_hl"              (UP vlna nesplnila HH+HL)
                     "no_ll_lh"              (DOWN vlna nesplnila LL+LH)
                     "first_in_trend"        (prvni vlna v trendu — pass)
                     "passed"                (HH/HL splnene)
    """
    if not getattr(cfg, "trend_filter_enabled", False):
        return True, "trend_filter_disabled"

    if getattr(cfg, "ext_post_confirmed_trend_lock_blocks_both_sides", True) and wave.get("post_ext_confirmed_trend_lock", False):
        return False, "post_ext_confirmed_lock"

    try:
        from strategy.ext_range import wave_allowed_in_ext_range

        if wave_allowed_in_ext_range(wave, cfg):
            return True, "ext_range_both_sides"
    except Exception:
        pass

    if trend_state is None:
        # Bezpecnostni fallback: filter zapnuty, ale snapshot chybi → blokujeme,
        # at se chyba nezamasti tichym otevrenim pozice.
        return False, "no_trend_state"

    wdir = int(wave["dir"])

    if trend_state.direction == "neutral":
        return False, "trend_neutral"
    if wdir == 1 and trend_state.direction != "bull":
        return False, "wave_against_trend"
    if wdir == -1 and trend_state.direction != "bear":
        return False, "wave_against_trend"

    if not getattr(cfg, "trend_hh_hl_filter_enabled", False):
        return True, "passed"

    # HH/HL — snapshot pred propsanim vlny → last_* = predchozi validni vlna v trendu.
    prev_top = trend_state.last_up_box_top if wdir == 1 else trend_state.last_down_box_top
    prev_bot = trend_state.last_up_box_bottom if wdir == 1 else trend_state.last_down_box_bottom
    if prev_top is None or prev_bot is None:
        return True, "first_in_trend"
    if _wave_passes_hh_hl_structure(trend_state, wave):
        return True, "passed"
    return False, "no_hh_hl" if wdir == 1 else "no_ll_lh"


def wave_allowed_for_fill_direction(
    wave: dict,
    trend_state: Optional[TrendState],
    cfg: BotConfig,
) -> Tuple[bool, str]:
    """
    Fill-time trend check: pouze smer vlny vs smer trendu na fill baru.
    HH/HL se zde NEOPAKUJE — ta se validuje jen pri zalozeni signálu/pendingu.
    """
    if not getattr(cfg, "trend_filter_enabled", False):
        return True, "trend_filter_disabled"

    try:
        from strategy.ext_range import wave_allowed_in_ext_range

        if wave_allowed_in_ext_range(wave, cfg):
            return True, "ext_range_both_sides"
    except Exception:
        pass

    if trend_state is None:
        return False, "no_trend_state"

    wdir = int(wave["dir"])
    if trend_state.direction == "neutral":
        return False, "trend_neutral"
    if wdir == 1 and trend_state.direction != "bull":
        return False, "wave_against_trend"
    if wdir == -1 and trend_state.direction != "bear":
        return False, "wave_against_trend"
    return True, "passed"


def entry_allowed_at_fill_bar(
    wave: dict,
    trend_state: Optional[TrendState],
    cfg: BotConfig,
    *,
    bypass_trend_filter: bool = False,
    is_counter: bool = False,
    is_bos_reentry: bool = False,
    is_pp: bool = False,
    is_two_sided_mirror: bool = False,
    pp_trend_confirmed: Optional[bool] = None,
) -> Tuple[bool, str]:
    """
    Trend re-check v okamziku fillu pendingu nebo MARKET fallbacku.

    Pouziva trend snapshot z fill baru (`trend_states_per_bar[i]`), ne snapshot
    z narozeni vlny. Kontroluje JEN smer trendu (ne HH/HL). Counter / BOS
    re-entry / two-sided mirror (s bypass) trend filtr neaplikuji.
    """
    if not getattr(cfg, "trend_filter_enabled", False):
        return True, "trend_filter_disabled"

    if bypass_trend_filter or is_counter or is_bos_reentry:
        return True, "trend_bypass"

    if is_two_sided_mirror and getattr(cfg, "two_sided_entry_bypass_trend_filter", True):
        return True, "two_sided_bypass"

    if is_pp:
        if pp_trend_confirmed is True:
            return True, "pp_trend_confirmed"
        if pp_trend_confirmed is False:
            return False, "pp_trend_not_confirmed"
        return False, "pp_trend_unknown"

    return wave_allowed_for_fill_direction(wave, trend_state, cfg)


# ============================================================================
# TP MODY (BOS_EXIT, BOS_EXIT_PRIORITY, WAVE_TARGET_N) + per-bar trend timeline
# ============================================================================
#
# Per-bar trend timeline pouziva backtest engine pro detekci BOS uvnitr smycky
# (kazdy bar dostane svuj snapshot trend stavu) a live loop pro odecet
# AKTUALNIHO trend stavu (poslednim prvkem listu).
#
# TP resolver `resolve_effective_tp` se vola z backtest engine i z infra.orders
# (live) pri vytvareni / triggeru orderu. Pro WAVE_TARGET_N vraci to, co engine
# pripravil v signal["wave_target_tp_price"] (nebo None pro K<N a non-TP vlny);
# pro BOS_EXIT_PRIORITY vraci vzdy None (= ZADNY TP); pro BOS_EXIT i RRR_FIXED
# klasicke RRR od skutecne entry.


def compute_trend_states_per_bar(df: pd.DataFrame,
                                 waves: List[dict],
                                 cfg: BotConfig) -> List[TrendState]:
    """
    Bar-by-bar projde df a vrati seznam TrendState[N] kde N=len(df).
    `result[i]` = stav trendu PO zpracovani baru i:
      - nejprve se na baru vyhodnoti BOS proti aktualnimu stavu (mozny flip),
      - pak se aplikuje pripadna nove potvrzena vlna (update last_up_* /
        last_down_*).

    Vyuziti:
      - backtest engine: porovna `states[i].direction` s `states[i-1].direction`,
        pri zmene zavre pozice "broken direction" na close baru i.
      - live loop: `states[-1]` = aktualni trend k poslednimu nactenemu baru.

    Pokud df je prazdny, vraci [] (engine / live tuto situaci umi).
    """
    if df is None or df.empty:
        return []

    wave_birth_bars = compute_wave_birth_bars_pine(df, cfg)
    waves_by_birth_bar: Dict[int, List[dict]] = {}
    for w in waves:
        birth = wave_birth_bars.get(w["wave_time"])
        if birth is None:
            continue
        waves_by_birth_bar.setdefault(int(birth), []).append(w)

    n = len(df)
    waves_by_extreme_bar = _build_waves_by_extreme_bar(waves, n)
    state = TrendState()
    states: List[TrendState] = []
    closes, _times, _n = _trend_market_arrays(df)

    for i in range(n):
        bar_close = float(closes[i])
        state, _flipped_to = _advance_bos_timeline_bar(
            state,
            bar_close,
            i,
            cfg=cfg,
            waves_by_extreme_bar=waves_by_extreme_bar,
            waves_by_birth_bar=waves_by_birth_bar,
        )
        states.append(replace(state))

    return states


def _bos_flip_target_from_label(label: str) -> Optional[str]:
    if "bear" in label:
        return "bear"
    if "bull" in label:
        return "bull"
    return None


def iter_close_based_bos_flips(
    df: pd.DataFrame,
    waves: List[dict],
    cfg: BotConfig,
) -> Iterator[Tuple[int, Any, str, str, float, Any]]:
    """
    Kazdy close-based BOS flip (bar_index, flip_time, target_dir, label,
    swing_price, segment_start). Seed-reset po EXT se nezapocitava.
    """
    if df is None or df.empty or not waves:
        return

    wave_birth_bars = compute_wave_birth_bars_pine(df, cfg)
    waves_by_birth_bar: Dict[int, List[dict]] = {}
    for w in waves:
        birth = wave_birth_bars.get(w["wave_time"])
        if birth is None:
            continue
        waves_by_birth_bar.setdefault(int(birth), []).append(w)

    n = len(df)
    waves_by_extreme_bar = _build_waves_by_extreme_bar(waves, n)
    state = TrendState()
    closes, times, n = _trend_market_arrays(df)

    def _birth_time_for_wave(wave_time: Optional[str]):
        if not wave_time:
            return None
        bi = wave_birth_bars.get(wave_time)
        if bi is None:
            return None
        try:
            return _time_at(times, int(bi))
        except (IndexError, TypeError, ValueError):
            return None

    def _segment_start_time(wave_time: Optional[str]):
        if not wave_time:
            return None
        for w in waves:
            if str(w.get("wave_time")) != str(wave_time):
                continue
            dl = w.get("draw_left")
            if dl is not None:
                try:
                    j = int(dl)
                    if 0 <= j < n:
                        return _time_at(times, j)
                except (IndexError, TypeError, ValueError):
                    pass
            return _birth_time_for_wave(wave_time)
        return _birth_time_for_wave(wave_time)

    for i in range(n):
        bar_close = float(closes[i])
        t = _time_at(times, i)

        prev_up_bottom = state.last_up_box_bottom
        prev_down_top = state.last_down_box_top
        prev_up_wt = state.last_up_wave_time
        prev_down_wt = state.last_down_wave_time

        state, flipped_to = _advance_bos_timeline_bar(
            state,
            bar_close,
            i,
            cfg=cfg,
            waves_by_extreme_bar=waves_by_extreme_bar,
            waves_by_birth_bar=waves_by_birth_bar,
        )

        if flipped_to == -1:
            t0 = _segment_start_time(prev_up_wt)
            if t0 is None:
                t0 = t
            yield (
                int(i),
                t,
                "bear",
                "BOS: close pod UP low → bear",
                float(prev_up_bottom) if prev_up_bottom is not None else 0.0,
                t0,
            )
        elif flipped_to == 1:
            t0 = _segment_start_time(prev_down_wt)
            if t0 is None:
                t0 = t
            yield (
                int(i),
                t,
                "bull",
                "BOS: close nad DOWN high → bull",
                float(prev_down_top) if prev_down_top is not None else 0.0,
                t0,
            )


def compute_close_based_bos_flip_bar_indices(
    df: pd.DataFrame,
    waves: List[dict],
    cfg: BotConfig,
) -> Set[int]:
    """Bary, na kterych doslo k close-based BOS (bez seed-resetu)."""
    return {i for i, *_ in iter_close_based_bos_flips(df, waves, cfg)}


def find_close_bos_flip_for_target_since(
    df: pd.DataFrame,
    waves: List[dict],
    cfg: BotConfig,
    *,
    target_direction: str,
    after_time: Any = None,
) -> Optional[Tuple[Any, str, int]]:
    """
    Posledni close-based flip do `target_direction` s casem baru > after_time
    (exclusive). Vraci (flip_time, label, bar_index) nebo None.
    """
    if target_direction not in ("bull", "bear"):
        return None
    after_ts = None if after_time is None else pd.Timestamp(after_time)
    hit: Optional[Tuple[Any, str, int]] = None
    for i, t, target, label, _lvl, _t0 in iter_close_based_bos_flips(df, waves, cfg):
        if target != target_direction:
            continue
        ts = pd.Timestamp(t)
        if after_ts is not None and ts <= after_ts:
            continue
        hit = (t, label, int(i))
    return hit


def _trend_direction_without_ext_seed_at_end(
    df: pd.DataFrame,
    waves: List[dict],
    cfg: BotConfig,
) -> str:
    """
    Smer trendu na konci dat bez EXT post-trend seed-resetu (close-BOS + vlny).
    Pouziva se pro PP: kdyz nebyl zadny close-based flip, musi `current_trend`
    odpovidat tomuto smeru (ne seed-only bull/bear z `compute_trend_states_per_bar`).
    """
    if df is None or df.empty or not waves:
        return "neutral"

    wave_birth_bars = compute_wave_birth_bars_pine(df, cfg)
    waves_by_birth_bar: Dict[int, List[dict]] = {}
    for w in waves:
        birth = wave_birth_bars.get(w["wave_time"])
        if birth is None:
            continue
        waves_by_birth_bar.setdefault(int(birth), []).append(w)

    state = TrendState()
    closes, _times, n = _trend_market_arrays(df)

    for i in range(n):
        bar_close = float(closes[i])
        state = _apply_bos_close_flip(state, bar_close)

        for w in waves_by_birth_bar.get(i, []):
            maybe_update_trend_state_with_wave(state, w, cfg)

    if state.direction in ("bull", "bear"):
        return state.direction
    return "neutral"


def compute_no_seed_trend_direction_per_bar(
    df: pd.DataFrame,
    waves: List[dict],
    cfg: BotConfig,
) -> List[str]:
    """Smer trendu na kazdem baru bez EXT seed-resetu (close-BOS + vlny)."""
    if df is None or df.empty or not waves:
        return []

    closes, _times, n = _trend_market_arrays(df)

    wave_birth_bars = compute_wave_birth_bars_pine(df, cfg)
    waves_by_birth_bar: Dict[int, List[dict]] = {}
    for w in waves:
        birth = wave_birth_bars.get(w["wave_time"])
        if birth is None:
            continue
        waves_by_birth_bar.setdefault(int(birth), []).append(w)

    state = TrendState()
    out: List[str] = ["neutral"] * n

    for i in range(n):
        bar_close = float(closes[i])
        state = _apply_bos_close_flip(state, bar_close)

        for w in waves_by_birth_bar.get(i, []):
            maybe_update_trend_state_with_wave(state, w, cfg)

        if state.direction in ("bull", "bear"):
            out[i] = state.direction

    return out


def build_pp_trend_confirmed_per_bar(
    df: pd.DataFrame,
    waves: List[dict],
    cfg: BotConfig,
    trend_states_per_bar: List[TrendState],
) -> List[bool]:
    """
    Pro kazdy bar: muze PP break pouzit `trend_states_per_bar[i].direction`?
    Jednorazovy O(n) predpočet — nevolat `pp_trend_confirmed_by_close_bos` v kazdem baru.
    """
    n = len(trend_states_per_bar)
    if n == 0:
        return []

    last_close_at_bar: List[Optional[str]] = [None] * n
    last_close: Optional[str] = None
    for i, _t, target, _label, _lvl, _t0 in iter_close_based_bos_flips(df, waves, cfg):
        if 0 <= int(i) < n:
            last_close = target
            last_close_at_bar[int(i)] = target

    last_close_filled: List[Optional[str]] = [None] * n
    running: Optional[str] = None
    for i in range(n):
        if last_close_at_bar[i] is not None:
            running = last_close_at_bar[i]
        last_close_filled[i] = running

    no_seed_dirs = compute_no_seed_trend_direction_per_bar(df, waves, cfg)
    confirmed: List[bool] = []
    for i in range(n):
        td = trend_states_per_bar[i].direction
        if td not in ("bull", "bear"):
            confirmed.append(False)
            continue
        lc = last_close_filled[i]
        if lc is not None:
            confirmed.append(lc == td)
        else:
            ns = no_seed_dirs[i] if i < len(no_seed_dirs) else "neutral"
            confirmed.append(ns == td)
    return confirmed


def pp_trend_confirmed_by_close_bos(
    df: pd.DataFrame,
    waves: List[dict],
    cfg: BotConfig,
    current_trend: str,
) -> bool:
    """
    PP smi jen ve smeru trendu potvrzeneho close-based BOS (ne seed-reset).

    - Po poslednim close-based flipu musi `current_trend` = cil toho flipu.
    - Bez close-based flipu musi `current_trend` = smer bez EXT seed-resetu.

    Live loop vola jednou za cyklus; backtest pouziva `build_pp_trend_confirmed_per_bar`.
    """
    if current_trend not in ("bull", "bear"):
        return False

    last_close_target: Optional[str] = None
    for _i, _t, target, _label, _lvl, _t0 in iter_close_based_bos_flips(df, waves, cfg):
        last_close_target = target

    if last_close_target is not None:
        return last_close_target == current_trend

    return _trend_direction_without_ext_seed_at_end(df, waves, cfg) == current_trend


def find_pp_candidate_wave(
    waves: List[dict],
    wave_birth: Dict[str, int],
    bar_idx: int,
    trend_dir: int,
    *,
    broken_wave_times: Set[str] | frozenset[str],
) -> Optional[dict]:
    """
    Nejnovejsi narozena vlna ve smeru trendu (birth <= bar_idx), jeste bez PP break.
    Sdileno backtest + live.
    """
    best: Optional[dict] = None
    best_birth = -1
    for w in waves:
        wt = str(w.get("wave_time", ""))
        if not wt or wt in broken_wave_times:
            continue
        if int(w.get("dir", 0)) != int(trend_dir):
            continue
        try:
            b = int(wave_birth[wt])
        except (KeyError, TypeError, ValueError):
            continue
        if b <= int(bar_idx) and b > best_birth:
            best_birth = b
            best = w
    return best


def pp_wave_finished_for_break(
    candidate: dict,
    *,
    bar_idx: int,
    wave_birth: Dict[str, int],
) -> bool:
    """
    True pokud vlna uz skoncila: bar je za birth a po ni existuje dalsi narozena vlna.

    PP (push-through) ma jit az po ukonceni swing high/low — ne na stejnem baru
    jako potvrzeni vlny ani drive, nez zacne dalsi vlna v sekvenci.
    """
    wt = str(candidate.get("wave_time", ""))
    try:
        birth = int(wave_birth[wt])
    except (KeyError, TypeError, ValueError):
        return False
    if int(bar_idx) <= birth:
        return False
    for owt, ob in wave_birth.items():
        if str(owt) == wt:
            continue
        try:
            if int(ob) > birth:
                return True
        except (TypeError, ValueError):
            continue
    return False


def pp_wave_eligible_for_break(
    candidate: dict,
    *,
    bar_idx: int,
    wave_birth: Dict[str, int],
    cfg: BotConfig,
) -> tuple[bool, str]:
    """
    PP kandidat musi byt trend-dir swing s ukoncenou vlnou (viz finished_for_break).
    Vraci (ok, reason) pro debug / log.
    """
    if bool(candidate.get("post_ext_trend_suppressed", False)):
        return False, "post_ext_trend_suppressed"
    if getattr(cfg, "pp_disabled_in_ext_context", True):
        from strategy.ext_logic import is_ext_wave
        from strategy.ext_range import wave_in_ext_range

        if is_ext_wave(candidate, cfg):
            return False, "ext_wave"
        if wave_in_ext_range(candidate, cfg):
            return False, "in_ext_range"
    if getattr(cfg, "trend_hh_hl_filter_enabled", False):
        if candidate.get("hh_hl_pass") is False:
            return False, "hh_hl_fail"
    if not pp_wave_finished_for_break(
        candidate, bar_idx=bar_idx, wave_birth=wave_birth,
    ):
        return False, "wave_not_finished"
    return True, "ok"


def bos_flip_time_to_log_str(flip_time: Any) -> str:
    """Casovy string pro JSONL log (`bos_event_time`)."""
    return str(pd.Timestamp(flip_time))


def collect_bos_flip_events(
    df: pd.DataFrame,
    waves: List[dict],
    cfg: BotConfig,
) -> List[Tuple[Any, float, str, Any]]:
    """
    Seznam BOS flipu ve stejne logice jako `compute_trend_states_per_bar`:
    kazdy pruraz swing urovne close-em → zmena smeru trendu.

    Vraci ctverice ``(time_flip, swing_price, label, time_segment_start)`` pro
    vykresleni usecky: od narozeni vlny, ktera definuje swing (UP low / DOWN high),
    po svicku, na ktere doslo k BOS. ``time_flip`` = cas baru s prurazem;
    ``swing_price`` = last_up_box_bottom (bull→bear) nebo last_down_box_top
    (bear→bull);     ``time_segment_start`` = cas baru ``draw_left`` prislusne vlny (levy okraj
    boxu ve vizualu), jinak cas baru narozeni vlny.
    """
    if df is None or df.empty or not waves:
        return []

    out: List[Tuple[Any, float, str, Any]] = []
    wf_bos_freeze_ranges = build_wf_bos_freeze_ranges(waves)
    _closes, times, n = _trend_market_arrays(df)
    wf_segment_start_times: set = set()
    for w in waves:
        if not _wave_is_wf_origin(w):
            continue
        try:
            dl = int(w.get("draw_left", 0))
            if 0 <= dl < n:
                wf_segment_start_times.add(pd.Timestamp(_time_at(times, dl)))
        except (TypeError, ValueError, IndexError):
            pass

    def _append_bos_event(
        flip_time: Any,
        swing_price: float,
        label: str,
        segment_start: Any,
    ) -> None:
        target = _bos_flip_target_from_label(label)
        if out and target is not None:
            prev = _bos_flip_target_from_label(str(out[-1][2]))
            if prev == target:
                return
        out.append((flip_time, swing_price, label, segment_start))

    for i, t, _target, label, lvl, t0 in iter_close_based_bos_flips(df, waves, cfg):
        if bar_in_wf_bos_freeze(i, wf_bos_freeze_ranges):
            continue
        if t0 is not None and pd.Timestamp(t0) in wf_segment_start_times:
            continue
        # BOS cara se kresli JEN pri close-based prurazu swing levelu;
        # seed-reset po EXT se zde vedome nevykresluje (změna trendu
        # nastane interně, ale neodpovida tradičnímu Break of Structure).
        # Po tichém seed flipu muze nasledovat dalsi close-BOS do stejneho
        # smeru — takovou druhou caru preskocime (viz _append_bos_event).
        _append_bos_event(t, lvl, label, t0)

    return out


def find_prev_wave_by_birth(current_wave: dict,
                            all_waves: List[dict]) -> Optional[dict]:
    """
    Vrati vlnu s NEJVYSSIM `draw_left` (= bar narozeni), ktery je < `draw_left`
    aktualni vlny. V Pine emulatoru je to vzdy vlna OPACNEHO smeru (vlny strikne
    alternuji UP-DOWN-UP-...).

    Pomocna funkce ponechana pro pripadne dalsi pouziti; pro WAVE_TARGET_N
    se "predchozi vlna stejneho smeru v aktualnim trendu" pocita ve
    `strategy.wave_sequence.compute_wave_sequence_info_per_wave`.

    Vraci None, pokud zadna predchozi vlna neexistuje (= prvni vlna v datasetu).
    """
    cur_left = int(current_wave.get("draw_left", -1))
    if cur_left < 0:
        return None
    best: Optional[dict] = None
    best_left = -1
    for w in all_waves:
        wl = int(w.get("draw_left", -1))
        if wl < 0 or wl >= cur_left:
            continue
        if wl > best_left:
            best_left = wl
            best = w
    return best


def apply_tp_mode_to_waves(waves: List[dict], cfg: BotConfig) -> None:
    """
    POST-PROCESSING vystupu `detect_waves_pine` podle `cfg.tp_mode`.

    Aktualne uz neni potreba zadny TP-extension preprocessing (WAVE_TARGET_N
    pocita TP cenu az v engine / live na zaklade wave_sequence indexu, ne
    pri detekci). Funkce je ponechana jako no-op stub kvuli zpetne
    kompatibilite caller signature.
    """
    return


def resolve_effective_tp(cfg: BotConfig,
                         signal: dict,
                         entry_actual: float,
                         sl: float,
                         is_buy: bool) -> Optional[float]:
    """
    Vrati TP cenu, kterou ma realne pouzit caller (live `infra.orders._place_*`
    nebo backtest engine pri triggeru / market fallbacku). None znamena
    "ZADNY TP" (broker: MT5 TP = 0.0 → bez TP; backtest: vynechat TP kontrolu).

    Logika podle cfg.tp_mode:
      RRR_FIXED             →  TP = entry_actual ± cfg.rrr × |entry_actual − sl|

      BOS_EXIT / BOS_EXIT_PRIORITY →  None  (zadny TP, jen SL nebo BOS flip)

      WAVE_TARGET_N         →  TP urcuje engine podle wave_sequence indexu:
        - signal["wave_target_tp_price"] obsahuje vypoctenou TP cenu (pokud
          vlna je TP-vlna a ma platnou prev_same_dir);
        - jinak None (pro K < N vlny i pro K >= N nebo non-TP-wave indexy
          se TP nastavi pozdeji pri TP-event eventu nebo nikdy a vystup
          je jen SL / BOS).
        Tato funkce respektuje, co engine pripravil — necha rozhodnuti na
        nem (vrati signal["wave_target_tp_price"] pokud existuje, jinak None).

    Args:
        cfg:           BotConfig
        signal:        wave dict (volitelne `wave_target_tp_price` od engine)
        entry_actual:  skutecna entry cena (limit price / slipped fill / market price)
        sl:            stop-loss
        is_buy:        True pro BUY, False pro SELL

    Returns:
        TP cena (float) NEBO None pro „ZADNY TP".
    """
    tpm = getattr(cfg, "tp_mode", TPMode.RRR_FIXED)
    if isinstance(tpm, str):
        try:
            tpm = TPMode(tpm)
        except ValueError:
            tpm = TPMode.RRR_FIXED

    sl_dist = abs(float(entry_actual) - float(sl))
    rrr = float(getattr(cfg, "rrr", 1.0))

    if tpm in (TPMode.BOS_EXIT_PRIORITY, TPMode.BOS_EXIT):
        return None

    from strategy.wave_target_n_mode import is_wave_target_n_family

    if is_wave_target_n_family(cfg):
        raw = signal.get("wave_target_tp_price") if isinstance(signal, dict) else None
        if raw is None:
            return None
        tp_val = float(raw)
        # SL SAFETY: TP musi byt na spravne strane skutecne entry. Pokud wave
        # geometrie (box_bottom + ext*prev_size pro UP, box_top - ext*prev_size
        # pro DOWN) skoncila na spatne strane skutecne fill ceny (napr. slipnuti
        # nebo entry na okraji vlny), nesmime ji nastavit — jinak by TP fungoval
        # jako "loss-take" a triggeral by se hned s vetsi ztratou nez SL.
        if is_buy and tp_val <= float(entry_actual):
            return None
        if (not is_buy) and tp_val >= float(entry_actual):
            return None
        return tp_val

    # RRR_FIXED a fallback
    if is_buy:
        return float(entry_actual) + rrr * sl_dist
    return float(entry_actual) - rrr * sl_dist
