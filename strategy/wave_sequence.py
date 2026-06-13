"""
strategy/wave_sequence.py
=========================

Pocitani poradi vln v aktualnim trendu (close-based BOS resetuje pocitadlo).
Modul bezi NEZAVISLE na cfg.trend_filter_enabled — pouziva ho tp_mode =
WAVE_TARGET_N pro identifikaci TP-vln a pro vyhledani predchozi vlny stejneho
smeru pri vypoctu TP ceny.

KONCEPCE
--------
- BOS state machine sdili pravidla s `strategy.trend_bos.compute_trend_states_per_wave`
  (casovani k `draw_right`, ne k `birth_bar`):
  bull / bear / neutral; pri close-based pruraze swing levelu se trend otoci a
  pocitadlo se resetuje. EXT post-trend seed (`ext_post_trend_seed_dir`) trend
  znovu zalozi bez BOS; vlny s `post_ext_trend_suppressed=True` se nepoctou
  a neposouvaji stav (stejne jako v BOS flip map / entry pipeline).

- "Trend-direction vlna" = vlna ve smeru aktualne bezicho trendu (UP v bullu,
  DOWN v bearu). Pocitadlo `index_in_trend` se inkrementuje POUZE pro
  trend-direction vlny; counter-trend vlny dostavaji `index_in_trend = None`.

- BOS vlna (close prorazi swing level opacneho smeru na baru extremu) dostane
  `index_in_trend = 1` v novem trendu a flag `is_bos_wave=True` (obejde HH/HL).

- Pri `trend_hh_hl_filter_enabled=True` se v poctu i v BOS swingu zohledni jen
  HH/HL-validni trend-direction vlny. Sumova trend-dir vlna dostane
  `index_in_trend = None` a neposouva `last_up_*` / `last_down_*`.

- "TP-wave" = vlna s `index_in_trend == N` nebo `N+2, N+4, ...` kde N =
  `cfg.tp_target_wave_index`. Vyhodnoceni v `is_tp_wave_index`.

VEREJNE API
-----------
- `WaveSequenceInfo`                   — dataclass per-vlnove info.
- `compute_wave_sequence_info_per_wave` — hlavni precompute (dict wave_time -> WaveSequenceInfo).
- `propagate_seq_info_to_waves`       — propsani seq_info do wave dict (HTML vizualizace).
- `_get_ext1_protect_flag`             — cfg flag EXT-1 ochrany (novy/stary klic).
- `is_tp_wave_index(index, target_n)`  — boolean: vlna s tim indexem je TP-wave.
- `compute_wave_target_tp_price`       — absolutni TP cena pro TP-vlnu (od prev same-dir).
- `compute_sl_pct_from_wave_size_ladder` — SL % z ladderu (counter-position, BOS re-entry).
- `compute_sl_price_from_pct`          — SL cena z entry + sl% + smeru.
- `compute_sl_pct_from_entry_and_sl`   — odvozeni efektivniho SL % z entry/sl cen.
- `wave_counter_min_sl_pct`            — min SL % pro WAVE_COUNTER (parovano s EXT secondary).
- `is_wave_counter_trade`              — True pro WAVE counter (ne EXT counter).
- `is_two_sided_mirror_trade`          — True pro two-sided mirror pozici/pending.
- `should_close_trade_on_tp_wave_n`    — scope TP-vlny N (trend-dir + counter + two-sided + EXT block).
- `should_close_trade_on_bos_flip`     — scope BOS flip close (broken_dir + counter + two-sided + EXT counter).
- `compute_wave_counter_take_profit`   — RRR/BOS_EXIT TP pro wave counter; None pro WAVE_TARGET_N.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pandas as pd

from config.bot_config import BotConfig
from config.enums import TPMode
from strategy.ext_logic import (
    bos_triggered_for_ext_close,
    is_ext_block_trade,
    is_ext_counter_trade,
    is_ext_primary_wave_trade,
)
from strategy.ext_range import check_close_breaks_ext_extreme, effective_wave_min_pct, check_ext_bos_via_fib_35, ext_scenario_classify
from strategy.trend_bos import (
    TrendState,
    _maybe_seed_state_from_ext_post_trend,
    _wave_is_wf_origin,
    _wave_passes_hh_hl_structure,
    maybe_update_trend_state_with_wave,
)
from strategy.wick_fakeout import WAVE_ORIGIN_WF


def _get_ext1_protect_flag(cfg: Any) -> bool:
    """Backward compat: prefer nový klíč, fallback na starý."""
    if hasattr(cfg, "ext1_protect_positions_until_wave2"):
        return bool(cfg.ext1_protect_positions_until_wave2)
    if hasattr(cfg, "ext1_protect_positions_until_ext2"):
        return bool(cfg.ext1_protect_positions_until_ext2)
    return True


# ---------------------------------------------------------------------------
# Dataclass: per-wave info
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WaveSequenceInfo:
    """
    Snapshot poradi vlny v aktualnim trendu (BOS-resetovany).

    index_in_trend:
      None = vlna se nepocita do TP-pocitadla (counter-trend NEBO trend-dir
           bez HH/HL pri zapnutem filtru).
      0    = vyhrazeno pro wave_two_sided counter (zatim neimplementovano).
      1+   = poradi trend-direction vlny v aktualnim trendu (1, 2, 3, ...).
             BOS vlna vzdy zacina na 1 v novem trendu.

    prev_same_dir_in_trend_wave_time:
      wave_time predchozi VALID trend-direction vlny stejneho smeru v tomtez
      trendu (= vlna s index_in_trend = K-1 kde K = aktualni index, nebo presneji
      "posledni vlna stejneho smeru, ktera index zvedla").
      None pokud:
        - vlna ma index_in_trend = None
        - vlna je prvni svuho smeru v aktualnim trendu (index_in_trend = 1)
        
    is_bos_wave:
      True pokud tato vlna zpusobila close-based BOS flip a je prvni vlnou
      noveho trendu.
    """
    index_in_trend: Optional[int] = None
    prev_same_dir_in_trend_wave_time: Optional[str] = None
    is_bos_wave: bool = False


def _all_first_n_waves_are_ext(
    trend_nodes: List[str],
    seq_info: Dict[str, WaveSequenceInfo],
    waves_by_wt: Dict[str, dict],
    n: int,
) -> bool:
    """
    True pokud trend ma vlny s index_in_trend 1..n a kazda z nich je EXT.

    Pouziti: wave_2_no_tp_max_index = n a EXT1..EXTn → ochrana wave_2_no_tp
    pro cely trend neplati (pozice se zaviraji SL / EXT_BOS / TP dle rezimu).
    """
    if n <= 0:
        return False
    by_idx: Dict[int, bool] = {}
    for node in trend_nodes:
        node_info = seq_info.get(node)
        if not node_info or node_info.index_in_trend is None:
            continue
        idx = int(node_info.index_in_trend)
        if 1 <= idx <= n:
            by_idx[idx] = bool(waves_by_wt.get(node, {}).get("is_ext"))
    if len(by_idx) < n:
        return False
    return all(by_idx[k] for k in range(1, n + 1))


def compute_wave_2_no_tp_protected_waves(
    waves: List[dict],
    seq_info: Dict[str, WaveSequenceInfo],
    cfg: BotConfig,
) -> set[str]:
    """
    Vraci mnozinu wave_time (identifikatoru vln), ktere jsou chraneny
    pred uzavrenim z duvodu `wave_2_no_tp_enable`.
    
    Ochrana plati pro vlny s `index_in_trend <= wave_2_no_tp_max_index`,
    pokud zaroven cely jejich trend dosahl maximalne tohoto indexu.
    Jakmile trend dosahne vyssiho indexu (napr. 3), ochrana pro VSECHNY
    jeho vlny okamzite pada.
    VÝJIMKA: Pokud jsou prvni n vlny v trendu (indexy 1..n, n =
    wave_2_no_tp_max_index) vsechny EXT, ochrana pro cely trend NEPLATÍ.
    """
    if not getattr(cfg, "wave_2_no_tp_enable", False):
        return set()
        
    max_idx = int(getattr(cfg, "wave_2_no_tp_max_index", 2))
    waves_by_wt = {str(w["wave_time"]): w for w in waves}
    
    # 1. Zmapujeme vazby "dalsi vlna v trendu" (traverzovani dopredu)
    next_in_trend = {}
    for wt, info in seq_info.items():
        prev_wt = info.prev_same_dir_in_trend_wave_time
        if prev_wt is not None:
            next_in_trend[prev_wt] = wt
            
    protected = set()
    processed_roots = set()
    
    # 2. Projdeme trendy po jednotlivych vetvich od korene
    for w in waves:
        wt = str(w["wave_time"])
        info = seq_info.get(wt)
        if not info or info.index_in_trend is None:
            continue
            
        # Najdeme koren trendu (vlna bez predchudce v tom samem trendu)
        root = wt
        while seq_info.get(root) and seq_info[root].prev_same_dir_in_trend_wave_time:
            root = seq_info[root].prev_same_dir_in_trend_wave_time
            
        if root in processed_roots:
            continue
        processed_roots.add(root)
        
        # Projdeme cely trend od korene
        curr_node = root
        trend_max_idx = 1
        trend_nodes = []
        
        while curr_node:
            if curr_node not in waves_by_wt:
                break
            trend_nodes.append(curr_node)
            node_info = seq_info.get(curr_node)
            if node_info and node_info.index_in_trend is not None:
                idx = node_info.index_in_trend
                if idx > trend_max_idx:
                    trend_max_idx = idx
                    
            curr_node = next_in_trend.get(curr_node)

        ext_prefix_voids = _all_first_n_waves_are_ext(
            trend_nodes, seq_info, waves_by_wt, max_idx
        )
            
        # Pokud trend nepresahl max_idx a neni zneplatnen EXT prefixem 1..n
        if trend_max_idx <= max_idx and not ext_prefix_voids:
            for node in trend_nodes:
                node_info = seq_info.get(node)
                if node_info and node_info.index_in_trend is not None and node_info.index_in_trend <= max_idx:
                    protected.add(node)
                    
    return protected


# ---------------------------------------------------------------------------
# Helpery: TP-wave detekce + cenove vypocty
# ---------------------------------------------------------------------------

def is_tp_wave_index(index: Optional[int], target_n: int) -> bool:
    """
    Vrati True pokud vlna s `index` patri mezi TP-vlny pro cilovou N.

    TP-vlny: N, N+2, N+4, ...   (tj. index >= N a (index - N) je sude).
    Pro `target_n <= 0` vraci vzdy False (neaktivni).
    Pro `index <= 0` nebo `None` vraci vzdy False (counter-trend / HH-HL fail).
    """
    if index is None:
        return False
    if target_n <= 0 or index <= 0:
        return False
    if index < target_n:
        return False
    return (index - target_n) % 2 == 0


def compute_wave_target_tp_price(wave: dict,
                                 prev_same_dir_wave: Optional[dict],
                                 cfg: BotConfig) -> Optional[float]:
    """
    Vypocet TP ceny pro TP-vlnu (tp_mode = WAVE_TARGET_N).

    Vzorec:
      UP   (dir=+1): TP = box_bottom_aktualni + cfg.wave_extension_pct × |prev_same_dir|
      DOWN (dir=-1): TP = box_top_aktualni    − cfg.wave_extension_pct × |prev_same_dir|

    Velikost prev_same_dir = box_top − box_bottom predchozi vlny stejneho smeru
    v aktualnim trendu (napr. pro UP4 -> UP3, ne DOWN3).

    Vraci None pokud:
      - prev_same_dir_wave je None (prvni stejnosmerna vlna v trendu)
      - prev box neni validni (box_top <= box_bottom)
      - cfg.wave_extension_pct <= 0
    """
    if prev_same_dir_wave is None:
        return None
    try:
        prev_top = float(prev_same_dir_wave["box_top"])
        prev_bot = float(prev_same_dir_wave["box_bottom"])
    except (KeyError, TypeError, ValueError):
        return None
    prev_size = prev_top - prev_bot
    if prev_size <= 0.0:
        return None

    ext = float(getattr(cfg, "wave_extension_pct", 0.0) or 0.0)
    if ext <= 0.0:
        return None

    wdir = int(wave["dir"])
    box_top = float(wave["box_top"])
    box_bot = float(wave["box_bottom"])

    if wdir == 1:
        return box_bot + ext * prev_size
    return box_top - ext * prev_size


def compute_sl_pct_from_wave_size_ladder(wave_size_pct: float,
                                          cfg: BotConfig) -> float:
    """
    Vrati SL % podle ladderu:
        band = floor(wave_size_pct / band_size_pct)
        sl_pct = base_pct + band × step_pct

    Vstup `wave_size_pct` je velikost vlny v % (= move_pct vlny: |box_top − box_bottom|
    / pivot × 100, vcetne wicku — viz strategy.wave_detection_pine.move_pct).

    Defaultni hodnoty (uzivatelska specifikace):
      wave_size <= 0.49%   -> SL 0.21%   (band 0)
      wave_size 0.50..0.99% -> SL 0.32%  (band 1)
      wave_size 1.00..1.49% -> SL 0.43%  (band 2)
      wave_size 1.50..1.99% -> SL 0.54%  (band 3)
      ... (linear: 0.11% za kazde dalsi 0.50% velikosti)
    """
    base = float(getattr(cfg, "wave_size_sl_ladder_base_pct", 0.21))
    step = float(getattr(cfg, "wave_size_sl_ladder_step_pct", 0.11))
    band_size = float(getattr(cfg, "wave_size_sl_ladder_band_size_pct", 0.50))
    if band_size <= 0.0:
        return max(base, 0.0)
    # Maly epsilon, aby float chyby (napr. 0.4999...998 ulozene jako 0.5)
    # neposunuly band o 1 dolu. Pricitame eps PRED delenim — hranice je
    # INCLUSIVE pro vyssi band (0.50 -> band 1; 0.49 -> band 0).
    eps = 1e-9
    band = int(max(0.0, wave_size_pct + eps) / band_size)
    return max(base + band * step, 0.0)


def compute_sl_price_from_pct(entry_price: float, sl_pct: float,
                              is_buy: bool) -> float:
    """
    SL cena pro pozici z entry + sl_pct + smeru.

      BUY:  sl = entry × (1 − sl_pct/100)
      SELL: sl = entry × (1 + sl_pct/100)

    sl_pct je v procentech (np. 0.21 = 0.21%).
    """
    delta = float(entry_price) * float(sl_pct) / 100.0
    if is_buy:
        return float(entry_price) - delta
    return float(entry_price) + delta


def compute_sl_pct_from_entry_and_sl(entry_price: float, sl_price: float) -> float:
    """
    Vrati efektivni vzdalenost SL od entry v procentech ceny entry.

    Pouziva se napr. pri repricingu counter pendingu po gap-fillu, aby se
    zachoval puvodni procentni model SL i po zmene skutecne fill ceny.
    """
    entry = abs(float(entry_price))
    if entry <= 0.0:
        return 0.0
    return abs(float(sl_price) - float(entry_price)) / entry * 100.0


def wave_counter_min_sl_pct(cfg: BotConfig) -> float:
    """
    Minimalni SL % pro WAVE_COUNTER.

    Je zamerne parovany se stejnym nastavenim jako EXT secondary, aby oba typy
    pouzivaly shodny minimalni odstup SL od entry.
    """
    raw = getattr(cfg, "ext_min_sl_move_pct", None)
    if raw is None:
        return 0.16
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.16


def is_wave_counter_trade(obj: Any) -> bool:
    """
    True pro WAVE counter pozici/pending (entry_tag=wave_counter, ne EXT counter).

    EXT counter ma is_ext=True nebo entry_tag zacinajici na ext_counter_*.
    """
    if getattr(obj, "is_ext", False):
        return False
    tag = str(getattr(obj, "entry_tag", "") or "")
    if tag == "wave_counter":
        return True
    if not getattr(obj, "is_counter", False):
        return False
    return not tag.startswith("ext_counter")


def is_two_sided_mirror_trade(obj: Any) -> bool:
    """True pro two-sided mirror pozici/pending."""
    return bool(getattr(obj, "is_two_sided_mirror", False))


def is_bos_flip_follower_trade(obj: Any) -> bool:
    """
    Pozice s chovanim WAVE_COUNTER po BOS flipu:
    WAVE_COUNTER, TWO_SIDED, EXT_COUNTER, primarni WAVE z EXT vlny.
    """
    return (
        is_wave_counter_trade(obj)
        or is_two_sided_mirror_trade(obj)
        or is_ext_counter_trade(obj)
        or is_ext_primary_wave_trade(obj)
    )


def should_close_trade_on_tp_wave_n(trade: Any, trend_dir: int) -> bool:
    """
    TP-vlna N zavírá pozice, které jsou ve shodě s aktuálním trendem (trend_dir).
    
    Pro counter pozice (WAVE_COUNTER, EXT_COUNTER, TWO_SIDED_MIRROR):
      - Zavřou se POUZE tehdy, pokud jsou ve shodě s trendem (trade.dir == trend_dir).
        To se stane typicky po BOS flipu, kdy se counter pozice stane trendovou.
        
    Pro ostatní pozice (WAVE, PP, BOS_REENTRY, EXT_SECONDARY):
      - Zavřou se, pokud jsou ve shodě s trendem.
      - EXT_SECONDARY se zavírá i pokud je proti trendu (is_ext_block_trade),
        ale je chráněna v _maybe_fire_tp_wave_event pomocí is_trade_within_parent_ext_window
        dokud nevznikne nová vlna.
    """
    if is_bos_flip_follower_trade(trade):
        return int(getattr(trade, "dir", 0)) == int(trend_dir)

    if int(getattr(trade, "dir", 0)) == int(trend_dir):
        return True

    return is_ext_block_trade(trade)


def should_close_trade_on_bos_flip(
    trade: Any,
    *,
    broken_dir: int,
    flipped: bool,
    protected_wave_times: set[str] | frozenset[str] | None = None,
) -> bool:
    """
    BOS flip: broken_dir pozice + pri flipu i wave counter / two-sided /
    EXT counter — POKUD nejsou v souladu se smerem noveho trendu.

    Counter pozice (WAVE_COUNTER, TWO_SIDED_MIRROR, EXT_COUNTER) po flipu 
    ve smeru noveho trendu (trade.dir == -broken_dir) se NEZAVIRAJI — 
    pokracuji s novym trendem a zavrou se az na dalsim BOS flipu.
    """
    wt = str(getattr(trade, "wave_time", "") or "")
    if protected_wave_times and wt in protected_wave_times:
        return False

    if is_bos_flip_follower_trade(trade):
        # Flip-follower: per-bar broken_dir nezavira; pri flipu jen proti novemu trendu.
        if not flipped:
            return False
        return int(getattr(trade, "dir", 0)) == int(broken_dir)

    if int(getattr(trade, "dir", 0)) == int(broken_dir):
        return True
    if flipped:
        return False
    return False


def compute_wave_counter_take_profit(
    cfg: BotConfig,
    entry: float,
    sl: float,
    *,
    is_buy: bool,
) -> Optional[float]:
    """
    TP pro wave counter podle cfg.tp_mode (stejna RRR safety logika jako hlavni vstup):

      RRR_FIXED             →  entry ± cfg.rrr × |entry − sl|
      WAVE_TARGET_N         →  None (exit aktivne na TP-vlne N spolecne s ostatnimi)
      BOS_EXIT / BOS_EXIT_PRIORITY →  None
    """
    from strategy.trend_bos import resolve_effective_tp

    tpm = getattr(cfg, "tp_mode", TPMode.RRR_FIXED)
    if isinstance(tpm, str):
        try:
            tpm = TPMode(tpm)
        except ValueError:
            tpm = TPMode.RRR_FIXED
    from strategy.wave_target_n_mode import is_wave_target_n_family

    if is_wave_target_n_family(cfg) or tpm in (TPMode.BOS_EXIT_PRIORITY, TPMode.BOS_EXIT):
        return None
    return resolve_effective_tp(cfg, {}, float(entry), float(sl), is_buy=bool(is_buy))


def compute_wave_counter_sl_setup(
    cfg: BotConfig,
    *,
    trend_dir: int,
    tp_price: float,
    prev_wave: dict,
) -> Optional[tuple[int, float, float, Optional[float]]]:
    """
    SL/TP setup pro wave counter (sdilene backtest + live).

    Vraci (counter_dir, sl_pct, counter_sl, counter_tp) nebo None.
    """
    counter_dir = -int(trend_dir)
    is_buy_counter = (counter_dir == 1)
    prev_size_pct = float(prev_wave.get("move_pct", 0.0))
    sl_pct, counter_sl = compute_ladder_sl_from_wave_size(
        float(tp_price),
        prev_size_pct,
        cfg,
        is_buy=is_buy_counter,
        min_sl_pct=wave_counter_min_sl_pct(cfg),
    )
    if sl_pct <= 0.0:
        return None
    counter_tp = compute_wave_counter_take_profit(
        cfg, float(tp_price), float(counter_sl), is_buy=is_buy_counter
    )
    return counter_dir, float(sl_pct), float(counter_sl), counter_tp


def compute_ladder_sl_from_wave_size(
    entry_price: float,
    wave_size_pct: float,
    cfg: BotConfig,
    *,
    is_buy: bool,
    min_sl_pct: float | None = None,
) -> tuple[float, float]:
    """
    Shared SL helper pro oba typy ladder pozic:
      - WAVE_COUNTER (counter po TP-vlne N)
      - BOS_REENTRY

    Oba typy maji sdileny model:
      1) sl_pct z `compute_sl_pct_from_wave_size_ladder(wave_size_pct, cfg)`
      2) SL cena z `compute_sl_price_from_pct(entry_price, sl_pct, is_buy=...)`

    Volitelne lze vynutit minimalni `sl_pct` pres `min_sl_pct`.

    Vraci `(sl_pct, sl_price)`.
    """
    sl_pct = compute_sl_pct_from_wave_size_ladder(wave_size_pct, cfg)
    if min_sl_pct is not None:
        sl_pct = max(float(min_sl_pct), float(sl_pct))
    sl_price = compute_sl_price_from_pct(entry_price, sl_pct, is_buy=is_buy)
    return float(sl_pct), float(sl_price)


# ---------------------------------------------------------------------------
# Hlavni precompute
# ---------------------------------------------------------------------------

def _wave_is_visible(w: dict, cfg: BotConfig, hh_hl_filter: bool) -> bool:
    """Stejná pravidla jako HTML chart (`wave_passes_visual_filter`, check_bos=False)."""
    from backtest.visual_wave_filter import wave_passes_visual_filter

    _ = hh_hl_filter  # cfg.trend_hh_hl_filter_enabled je zdroj pravdy ve filtru
    return wave_passes_visual_filter(w, cfg, check_bos=False)


def _ghost_skip_wave(
    w: dict,
    cfg: BotConfig,
    hh_hl_filter: bool,
    result: Dict[str, WaveSequenceInfo],
    wt: str,
) -> bool:
    """Neviditelná vlna → idx None, počítadlo se neposune."""
    if _wave_is_visible(w, cfg, hh_hl_filter):
        return False
    result[wt] = WaveSequenceInfo(None, None)
    return True


def _reset_ext1_count_state() -> tuple[bool, int, Optional[str]]:
    """Oprava 1: EXT-1 paralelní počítadlo platí jen v rámci jednoho trendu."""
    return False, 0, None


def sync_wave_sequence_state(
    df: pd.DataFrame,
    waves: List[dict],
    cfg: BotConfig,
) -> tuple[Dict[str, WaveSequenceInfo], set[str]]:
    """
    Přepočet index_in_trend + propagate do wave dict + TP ceny (WAVE_TARGET_N).
    Sdílené backtest engine (_sync) a live loop (po WF merge).
    """
    from strategy.wave_target_n_mode import is_wave_target_n_family

    seq_info = compute_wave_sequence_info_per_wave(df, waves, cfg)
    propagate_seq_info_to_waves(waves, seq_info)
    protected = compute_wave_2_no_tp_protected_waves(waves, seq_info, cfg)

    if is_wave_target_n_family(cfg):
        target_n = int(getattr(cfg, "tp_target_wave_index", 0) or 0)
        for w in waves:
            w.pop("wave_target_tp_price", None)
            info = seq_info.get(w["wave_time"])
            if info is None:
                continue
            idx = info.index_in_trend
            if idx is None or not is_tp_wave_index(idx, target_n):
                continue
            prev_w = find_wave_by_time(waves, info.prev_same_dir_in_trend_wave_time)
            tp_price = compute_wave_target_tp_price(w, prev_w, cfg)
            if tp_price is not None:
                w["wave_target_tp_price"] = float(tp_price)

    return seq_info, protected


def propagate_seq_info_to_waves(
    waves: list[dict],
    seq_info: dict[str, "WaveSequenceInfo"],
) -> None:
    for w in waves:
        wt = str(w.get("wave_time", ""))
        info = seq_info.get(wt)
        if info is not None:
            w["index_in_trend"] = info.index_in_trend
            w["is_bos_wave"] = bool(info.is_bos_wave)
            w["prev_same_dir_in_trend_wave_time"] = info.prev_same_dir_in_trend_wave_time
        else:
            w.setdefault("index_in_trend", None)
            w.setdefault("is_bos_wave", False)
            w.setdefault("prev_same_dir_in_trend_wave_time", None)


def _retro_claim_bos_seed_wave(
    result: Dict[str, WaveSequenceInfo],
    waves: List[dict],
    flip_bar: int,
    new_dir: str,
    *,
    confirm_window: int = 8,
) -> Optional[tuple[str, int, int, Optional[str]]]:
    """
    Vlna noveho trendu potvrzena tesne pred flipem dostala idx=None jen proto,
    ze flip probehl az na close — prirad ji idx=1 (BOS seed).

    `post_ext_trend_suppressed` vlny se NEvynechavaji: jsou potlacene pro trend
    filter / entry, ale strukturalne existuji a mohou byt BOS seed (viz May 23
    UP po bear W3 — flip map i WAVE_BOS je na nich, retro claim musi dat idx 1).

    Strukturalni BOS seed vzdy zacina na idx=1 (stejne jako pending blok po flipu),
    bez EXT-1 paralelniho pocitani.
    """
    if new_dir not in ("bull", "bear"):
        return None
    lo = int(flip_bar) - int(confirm_window)
    cands: List[tuple[int, str, int]] = []
    for w in waves:
        wt = str(w["wave_time"])
        info = result.get(wt)
        if info is None or info.index_in_trend is not None:
            continue
        if w.get("is_two_sided_counter"):
            continue
        if bool(w.get("is_wf")) or str(w.get("wave_origin", "")) == WAVE_ORIGIN_WF:
            continue
        try:
            dr = int(w["draw_right"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (lo <= dr < flip_bar):
            continue
        wdir = int(w.get("dir", 0))
        if new_dir == "bull" and wdir != 1:
            continue
        if new_dir == "bear" and wdir != -1:
            continue
        cands.append((dr, wt, wdir))
    if not cands:
        return None
    # Nejbližší draw_right k flipu (shodne s logikou compute_bos_wave_flip_map).
    _dr, wt, wdir = min(cands, key=lambda c: (abs(c[0] - int(flip_bar)), -c[0]))
    return wt, wdir, 1, None


def compute_wave_sequence_info_per_wave(df: pd.DataFrame,
                                        waves: List[dict],
                                        cfg: BotConfig,
                                        *,
                                        window_out: Optional[List[bool]] = None,
                                        ) -> Dict[str, WaveSequenceInfo]:
    """
    Hlavni precompute poradi vln.

    Idempotence: na zacatku kazdeho behu maze `is_bos_wave` na vstupnich `waves`.
    BOS flag je v navratovem `WaveSequenceInfo.is_bos_wave` (ne side-effect na wave dict).

    `window_out` (volitelne): pokud je predan list, naplni se per-bar booleany
    (delka == len(df)) udavajici, zda je na danem baru AKTIVNI EXT-1 ochranne
    okno (`ext1_protect_window`). Engine ho pouziva pro ochranu pozic: behem
    EXT-1 okna se zadna pozice nesmi zavrit jinak nez na SL.
    """
    if df is None or df.empty or not waves:
        if window_out is not None:
            window_out[:] = [False] * (0 if df is None else len(df))
        return {}

    for w in waves:
        w.pop("is_bos_wave", None)

    hh_hl_filter = bool(getattr(cfg, "trend_hh_hl_filter_enabled", False))

    waves_by_extreme: Dict[int, List[dict]] = {}
    n = len(df)

    for w in waves:
        try:
            dr = int(w["draw_right"])
        except (KeyError, TypeError, ValueError):
            continue
        if dr < 0 or dr >= n:
            continue
        waves_by_extreme.setdefault(dr, []).append(w)

    ext_active_wave: Optional[dict] = None
    first_ext_counter_wt: Optional[str] = None
    # Smer reverzni vlny (opacny k trend-dir EXT climaxu), ktera ma dostat idx 1.
    # Prezije vycisteni ext_active_wave (Mechanismus A/B), dokud nepride opacna
    # vlna nebo dokud cena neprorazi EXT extrem (pak EXT nebyl climax).
    ext_climax_reversal_dir: Optional[int] = None
    # Climax-continuation watch (uziv. pravidlo): po trend-dir EXT (scenar C)
    # NEni jiste, ze EXT byl finalni climax. Pokud nasledna STEJNOSMERNA vlna
    # prorazi EXT extrem (nove LOW v down trendu / HIGH v up), jde o POKRACOVANI
    # trendu (idx = climax_idx+1, napr. EXT 6 -> 7) a tim KONCI vliv EXT. Watch
    # PREZIJE i docasny opacny bounce (1 bounce nezaklada novy trend) — flip se
    # stane realnym az strukturalnim BOS / opacnym EXT. climax_extreme drzi
    # nejzazsi cenu ve smeru climaxu (box_bottom pro down, box_top pro up).
    climax_dir: Optional[int] = None
    climax_idx: Optional[int] = None
    climax_extreme: Optional[float] = None
    # True pokud byl AKTUALNI trend zalozen EXT vlnou s idx 1. Po dobu takoveho
    # trendu (PERSISTENTNE az do EXT-2) se klasicky BOS i fib-0.35 reverzace
    # NEbere jako flip — trend se NEotoci a existujici pozice se nezaviraji
    # (ochrana zije v trading core). Protismerne vlny se VSAK pocitaji jako
    # nezavisla sekvence 1,2,3,4 (uziv. upresneni: "Limitace na BOS pokud je EXT1
    # NEzakazuje pocitat vlny opacnym smerem"). Resetuje az EXT-2 / counter-EXT.
    trend_established_by_ext: bool = False
    # Counting-okno EXT-1: PERSISTENTNI (na rozdil od one-shot forgive flagu vyse).
    # True po dobu trendu zalozeneho EXT-1, dokud nepride EXT-2 (same-dir) nebo
    # realny flip/counter-EXT. Behem nej se PROTISMERNE vlny pocitaji jako
    # nezavisla sekvence 1,2,3,4. NEovlivnuje flip — struktura trendu zustava.
    ext1_count_window: bool = False
    ext1_protect_window: bool = False
    # Paralelni citac protismernych ("counter") vln behem EXT-1 okna (bear 1,2,3,4
    # kdyz je EXT-1 UP). Resetuje se na zacatku kazdeho noveho EXT-1 okna.
    ext1_counter_idx: int = 0
    last_ext1_counter_wt: Optional[str] = None
    counter_up: int = 0
    counter_down: int = 0
    last_same_dir_up_wt: Optional[str] = None
    last_same_dir_down_wt: Optional[str] = None
    result: Dict[str, WaveSequenceInfo] = {}
    state: TrendState = TrendState()
    
    closes = df["close"].astype(float).to_numpy()

    for i in range(n):
        bar_close = float(closes[i])
        
        # KROK 1: PRED iterací vln na baru
        mech_b_fired = False
        if ext_active_wave is not None:
            if check_close_breaks_ext_extreme(bar_close, ext_active_wave):
                # Mechanismus A: konec both-sides okna (cena prorazila EXT extrem =
                # trend potvrzen). Ocekavani reverzni vlny (ext_climax_reversal_dir) 
                # NEcistime — intrabar prurazeni bez nove same-dir VLNY climax neruší.
                # UZIV. POZADAVEK: ext1_count_window se nesmi predcasne vypnout Mech A,
                # aby se protismerne vlny mohly dopocitat az do 4 (TP wave n).
                ext_active_wave = None
                first_ext_counter_wt = None
                ext1_count_window = False
                ext1_protect_window = False
            elif check_ext_bos_via_fib_35(bar_close, ext_active_wave):
                # Mechanismus B = EXT BOS (fib-0.35). Uziv. pozadavek:
                # "BOS EXT nesmi menit trend ani pocitani vln" — EXT BOS jen
                # UKONCI both-sides okno. NEpreklapi state.direction, NEnastavi
                # is_bos_wave_pending a NEnuluje countery. Pozice zavira engine
                # (`_close_ext_trend_positions`, mimo EXT-1). Trend se otoci az
                # klasickym strukturalnim BOS (Mech C / wave BOS) nebo 2-vln
                # seedem po EXT. Drive zde byl flip jen pro ne-EXT-1 vetev — ten
                # je odstranen, obe vetve se chovaji stejne (jen ukonci okno).
                trend_established_by_ext = False
                ext_active_wave = None
                first_ext_counter_wt = None
                mech_b_fired = True

        # Mechanismus C: klasický swing BOS na baru bez vlny.
        # EXT-aware fib35 reverzace (Mech B) ma prednost — pokud na tomto baru
        # flipla, NEspoustime klasicky swing BOS opacnym smerem.
        if not mech_b_fired and not any(w.get("draw_right") == i for w in waves):
            if state.direction == "bull" and state.last_up_box_bottom is not None:
                if bar_close < state.last_up_box_bottom:
                    if trend_established_by_ext:
                        # Forgive PRVNI klasicky BOS po EXT-1: neotacet trend, jen
                        # znulovat prorazeny swing a spotrebovat one-shot. Counting-
                        # okno bezi dal (protismer se pocita). Dalsi BOS uz flipne.
                        trend_established_by_ext = False
                        state.last_up_box_bottom = None
                    else:
                        state.direction = "bear"
                        state.is_bos_wave_pending = True
                        ext_climax_reversal_dir = None
                        counter_up = 0
                        counter_down = 0
                        ext1_protect_window = False
                        if ext_active_wave is None and ext1_count_window:
                            ext1_count_window, ext1_counter_idx, last_ext1_counter_wt = (
                                _reset_ext1_count_state()
                            )
                        # Mirror enginu (`_bos_close_flip_with_forgive` vraci cerstvy
                        # TrendState): po flipu vynuluj OBA swing levely. Jinak by
                        # invertovane levely (lub > ldt) zpusobily oscilaci trendu
                        # na kazdem baru (dead-zone) a kazda vlna by dostala idx=1.
                        state.last_up_box_bottom = None
                        state.last_down_box_top = None
            elif state.direction == "bear" and state.last_down_box_top is not None:
                if bar_close > state.last_down_box_top:
                    if trend_established_by_ext:
                        trend_established_by_ext = False
                        state.last_down_box_top = None
                    else:
                        state.direction = "bull"
                        state.is_bos_wave_pending = True
                        ext_climax_reversal_dir = None
                        counter_up = 0
                        counter_down = 0
                        ext1_protect_window = False
                        if ext_active_wave is None and ext1_count_window:
                            ext1_count_window, ext1_counter_idx, last_ext1_counter_wt = (
                                _reset_ext1_count_state()
                            )
                        state.last_up_box_bottom = None
                        state.last_down_box_top = None

        if state.is_bos_wave_pending:
            claimed = _retro_claim_bos_seed_wave(
                result,
                waves,
                i,
                state.direction,
            )
            if claimed is not None:
                wt_claim, wdir_claim, new_idx, prev_wt = claimed
                result[wt_claim] = WaveSequenceInfo(
                    new_idx, prev_wt, is_bos_wave=new_idx == 1
                )
                if wdir_claim == 1:
                    counter_up = new_idx
                    last_same_dir_up_wt = wt_claim
                    counter_down = 0
                    last_same_dir_down_wt = None
                else:
                    counter_down = new_idx
                    last_same_dir_down_wt = wt_claim
                    counter_up = 0
                    last_same_dir_up_wt = None
                state.is_bos_wave_pending = False
                ext_climax_reversal_dir = None
                trend_established_by_ext = False
                ext1_count_window = False
                ext1_protect_window = False
                ext1_counter_idx = 0
                last_ext1_counter_wt = None

        # KROK 2: Iterace vln s draw_right == i (index_in_trend)
        new_waves = waves_by_extreme.get(i, [])
        for w in new_waves:
            wt = str(w["wave_time"])
            wdir = int(w["dir"])
        
            is_ext = bool(w.get("is_ext"))

            # KROK 0: Pre-check
            if w.get("post_ext_trend_suppressed"):
                bos_seed_after_suppress = (
                    state.is_bos_wave_pending
                    and not is_ext
                    and (
                        (state.direction == "bull" and wdir == 1)
                        or (state.direction == "bear" and wdir == -1)
                    )
                )
                if not bos_seed_after_suppress:
                    result[wt] = WaveSequenceInfo(None, None)
                    continue
            if w.get("is_two_sided_counter"):
                result[wt] = WaveSequenceInfo(0, None)
                continue
            # WF vlna (mimo EXT) se necisluje, ale NERESETUJE pocitadlo —
            # nasledujici trend-dir vlna pokracuje v sekvenci (uziv. pozadavek:
            # "WF nesmi rusit cislovani v trendu"). WF se v HTML kresli jako "WF"
            # (ne cislo) — proto NESMI spotrebovat index, jinak vznikne diura.
            # Identifikace i pres wave_origin (nektere WF nemaji flag is_wf).
            is_wf_wave = bool(w.get("is_wf")) or (
                str(w.get("wave_origin", "")) == WAVE_ORIGIN_WF
            )
            if is_wf_wave and not is_ext:
                result[wt] = WaveSequenceInfo(None, None)
                continue
            # HH/HL filtr NESMI vyradit EXT vlnu ani trend-direction vlnu z
            # pocitani. Uziv. pozadavek: trend-dir vlna se pocita i kdyz
            # nepresahne nove high/low v trendu ("WAVE 2 UP ma byt 2").
            # Counter vlna bez HH/HL spadne na None az v KROK 3/4.
            if (w.get("hh_hl_pass") is False) and not is_ext:
                wave_is_trend_dir = (
                    (state.direction == "bull" and wdir == 1)
                    or (state.direction == "bear" and wdir == -1)
                )
                # Reverzni vlna po trend-dir EXT (stane se BOS idx 1) HH/HL obejde.
                is_reversal = (
                    ext_climax_reversal_dir is not None
                    and wdir == ext_climax_reversal_dir
                )
                if (
                    state.direction != "neutral"
                    and not wave_is_trend_dir
                    and not is_reversal
                ):
                    result[wt] = WaveSequenceInfo(None, None)
                    continue

            if state.is_bos_wave_pending and not is_ext:
                wave_dir_matches_flip = (
                    (state.direction == "bull" and wdir == 1)
                    or (state.direction == "bear" and wdir == -1)
                )
                if wave_dir_matches_flip:
                    new_idx = 1
                    prev_wt = None
                    ext1_count_window = False
                    ext1_protect_window = False
                    ext1_counter_idx = 0
                    last_ext1_counter_wt = None

                    if wdir == 1:
                        counter_up = new_idx
                        last_same_dir_up_wt = wt
                        counter_down = 0
                        last_same_dir_down_wt = None
                    else:
                        counter_down = new_idx
                        last_same_dir_down_wt = wt
                        counter_up = 0
                        last_same_dir_up_wt = None
                    result[wt] = WaveSequenceInfo(
                        new_idx, prev_wt, is_bos_wave=True
                    )
                    state.is_bos_wave_pending = False
                    ext_climax_reversal_dir = None
                    climax_dir = climax_idx = climax_extreme = None
                    trend_established_by_ext = bool(w.get("is_ext"))
                    if w.get("is_ext"):
                        ext1_count_window = True
                        ext1_protect_window = True
                        ext_active_wave = w
                        first_ext_counter_wt = None
                        ext1_counter_idx = 0
                        last_ext1_counter_wt = None
                    maybe_update_trend_state_with_wave(state, w, cfg)
                    continue
                else:
                    result[wt] = WaveSequenceInfo(None, None)
                    continue

            # KROK 2: EXT vlna detekce
            if w.get("is_ext"):
                wave_dir_matches_flip = (
                    (state.direction == "bull" and wdir == 1)
                    or (state.direction == "bear" and wdir == -1)
                )
                if state.is_bos_wave_pending:
                    if wave_dir_matches_flip and (
                        ext1_count_window or ext1_counter_idx > 0
                    ):
                        # Mech C behem both-sides EXT-1 okna: EXT neni BOS seed.
                        state.is_bos_wave_pending = False
                    elif not wave_dir_matches_flip:
                        result[wt] = WaveSequenceInfo(None, None)
                        continue

                swing_levels = {
                    "last_up_box_bottom": state.last_up_box_bottom,
                    "last_down_box_top": state.last_down_box_top,
                }
                scenario = ext_scenario_classify(w, state, bar_close, swing_levels)
                if state.is_bos_wave_pending:
                    scenario = "A"
            
                if scenario == "A":
                    # EXT je BOS vlna
                    forced_bos = state.is_bos_wave_pending
                    state.direction = "bear" if wdir == -1 else "bull"
                    state.last_up_box_bottom = None
                    state.last_down_box_top = None
                    new_idx = 1
                    if (ext1_count_window or ext1_counter_idx > 0) and not forced_bos:
                        new_idx = ext1_counter_idx + 1
                        prev_wt = last_ext1_counter_wt
                    else:
                        prev_wt = None
                    if wdir == 1:
                        counter_up = new_idx
                        last_same_dir_up_wt = wt
                        counter_down = 0
                        last_same_dir_down_wt = None
                    else:
                        counter_down = new_idx
                        last_same_dir_down_wt = wt
                        counter_up = 0
                        last_same_dir_up_wt = None
                    result[wt] = WaveSequenceInfo(
                        new_idx,
                        prev_wt,
                        is_bos_wave=new_idx == 1,
                    )
                    ext_active_wave = w
                    first_ext_counter_wt = None
                    ext_climax_reversal_dir = None
                    climax_dir = climax_idx = climax_extreme = None
                    state.is_bos_wave_pending = False
                    trend_established_by_ext = True
                    ext1_count_window = True
                    ext1_protect_window = True
                    ext1_counter_idx = 0
                    last_ext1_counter_wt = None
                    maybe_update_trend_state_with_wave(state, w, cfg)
                    continue
            
                elif scenario == "B":
                    # EXT je counter k aktualnimu trendu. Uziv. pozadavek:
                    # opacna vlna po/pri EXT zaklada novy smer => okamzity flip,
                    # idx 1 (EXT vlna MUSI mit cislo).
                    forced_bos = state.is_bos_wave_pending
                    state.direction = "bear" if wdir == -1 else "bull"
                    state.last_up_box_bottom = None
                    state.last_down_box_top = None
                    new_idx = 1
                    if (ext1_count_window or ext1_counter_idx > 0) and not forced_bos:
                        new_idx = ext1_counter_idx + 1
                        prev_wt = last_ext1_counter_wt
                    else:
                        prev_wt = None
                    if wdir == 1:
                        counter_up = new_idx
                        last_same_dir_up_wt = wt
                        counter_down = 0
                        last_same_dir_down_wt = None
                    else:
                        counter_down = new_idx
                        last_same_dir_down_wt = wt
                        counter_up = 0
                        last_same_dir_up_wt = None
                    result[wt] = WaveSequenceInfo(
                        new_idx, prev_wt, is_bos_wave=new_idx == 1
                    )
                    ext_active_wave = w
                    first_ext_counter_wt = None
                    ext_climax_reversal_dir = None
                    climax_dir = climax_idx = climax_extreme = None
                    state.is_bos_wave_pending = False
                    trend_established_by_ext = True
                    ext1_count_window = True
                    ext1_protect_window = True
                    ext1_counter_idx = 0
                    last_ext1_counter_wt = None
                    maybe_update_trend_state_with_wave(state, w, cfg)
                    continue
            
                elif scenario == "C":
                    # EXT je trend-dir (climax trendu) = EXT-2+. Pocita se dal v
                    # trendu (napr. EXT-1 -> EXT-2 = idx 2), KONCI EXT-1 okno
                    # (trend_established_by_ext=False, "to uz neni EXT 1") a PRVNI
                    # opacna vlna po ni dostane idx 1 (reverzni trend).
                    trend_established_by_ext = False
                    ext1_count_window = False
                    ext1_protect_window = False
                    if wdir == 1:
                        counter_up += 1
                        result[wt] = WaveSequenceInfo(counter_up, last_same_dir_up_wt)
                        last_same_dir_up_wt = wt
                    else:
                        counter_down += 1
                        result[wt] = WaveSequenceInfo(counter_down, last_same_dir_down_wt)
                        last_same_dir_down_wt = wt
                    ext_active_wave = w
                    first_ext_counter_wt = None
                    ext_climax_reversal_dir = -wdir
                    # Climax-continuation watch: zapamatuj smer, idx a extrem
                    # EXT climaxu. Stejnosmerna vlna s novym extremem pak dostane
                    # climax_idx+1 (pokracovani), i kdyz mezitim bounce flipnul trend.
                    climax_dir = wdir
                    climax_idx = counter_up if wdir == 1 else counter_down
                    climax_extreme = (
                        float(w.get("box_top")) if wdir == 1 else float(w.get("box_bottom"))
                    )
                    maybe_update_trend_state_with_wave(state, w, cfg)
                    continue
            
                elif scenario == "D":
                    # EXT v neutrálním state
                    state.direction = "bull" if wdir == 1 else "bear"
                    if wdir == 1:
                        counter_up = 1
                        last_same_dir_up_wt = wt
                    else:
                        counter_down = 1
                        last_same_dir_down_wt = wt
                    result[wt] = WaveSequenceInfo(1, None)
                    ext_active_wave = w
                    first_ext_counter_wt = None
                    ext_climax_reversal_dir = None
                    climax_dir = climax_idx = climax_extreme = None
                    trend_established_by_ext = True
                    ext1_count_window = True
                    ext1_protect_window = True
                    ext1_counter_idx = 0
                    last_ext1_counter_wt = None
                    maybe_update_trend_state_with_wave(state, w, cfg)
                    continue
        
            # Climax-continuation: STEJNOSMERNA vlna (jako EXT climax) ktera
            # prorazi EXT extrem = POKRACOVANI trendu (idx = climax_idx+1, napr.
            # EXT 6 -> 7). Ma PREDNOST pred klasickym BOS i climax-reversalem —
            # plati i kdyz mezitim opacny bounce docasne flipnul trend (uziv.:
            # 1 bounce nezaklada novy trend). Konci vliv EXT (both-sides).
            if (
                climax_dir is not None
                and wdir == climax_dir
                and climax_extreme is not None
            ):
                if _ghost_skip_wave(w, cfg, hh_hl_filter, result, wt):
                    continue
                wext = (
                    float(w.get("box_bottom")) if wdir == -1 else float(w.get("box_top"))
                )
                makes_new_extreme = (
                    wext < climax_extreme if wdir == -1 else wext > climax_extreme
                )
                if makes_new_extreme:
                    state.direction = "bear" if wdir == -1 else "bull"
                    climax_idx += 1
                    climax_extreme = wext
                    if wdir == 1:
                        counter_up = climax_idx
                        counter_down = 0
                        last_same_dir_up_wt = wt
                        last_same_dir_down_wt = None
                    else:
                        counter_down = climax_idx
                        counter_up = 0
                        last_same_dir_down_wt = wt
                        last_same_dir_up_wt = None
                    result[wt] = WaveSequenceInfo(climax_idx, None)
                    ext_active_wave = None
                    first_ext_counter_wt = None
                    # Po pokracovani opet cekej opacnou reverzaci (dalsi climax watch).
                    ext_climax_reversal_dir = -wdir
                    state.is_bos_wave_pending = False
                    trend_established_by_ext = False
                    ext1_count_window = False
                    ext1_protect_window = False
                    continue

            # Klasický BOS check
            is_bos_wave = False
            if state.direction == "bull" and wdir == -1 and state.last_up_box_bottom is not None:
                if bar_close < state.last_up_box_bottom:
                    is_bos_wave = True
            elif state.direction == "bear" and wdir == 1 and state.last_down_box_top is not None:
                if bar_close > state.last_down_box_top:
                    is_bos_wave = True
            
        
            # Forgive PRVNI klasicky BOS po EXT-1: vlna NEotaci trend (spotrebuje
            # one-shot), propadne do KROK 3/4 jako counter — tam dostane paralelni
            # index z counting-okna (ext1_count_window). Dalsi BOS uz flipne.
            if is_bos_wave and trend_established_by_ext:
                trend_established_by_ext = False
                if wdir == -1:
                    state.last_up_box_bottom = None
                else:
                    state.last_down_box_top = None
                is_bos_wave = False

            if is_bos_wave:
                state.direction = "bear" if wdir == -1 else "bull"
            
                new_idx = 1
                prev_wt = None
                if ext1_count_window:
                    new_idx = ext1_counter_idx + 1
                    prev_wt = last_ext1_counter_wt
                
                if wdir == 1:
                    counter_up = new_idx
                    counter_down = 0
                    last_same_dir_up_wt = wt
                    last_same_dir_down_wt = None
                else:
                    counter_down = new_idx
                    counter_up = 0
                    last_same_dir_down_wt = wt
                    last_same_dir_up_wt = None
                result[wt] = WaveSequenceInfo(new_idx, prev_wt, is_bos_wave=True)
                state.is_bos_wave_pending = False
                ext_active_wave = None  # BOS mimo EXT zóny ukončí both-sides
                first_ext_counter_wt = None
                ext_climax_reversal_dir = None
                # Strukturalni BOS = realny flip => konec climax-continuation watch.
                climax_dir = climax_idx = climax_extreme = None
                trend_established_by_ext = False
                ext1_count_window = False
                ext1_protect_window = False
                ext1_counter_idx = 0
                last_ext1_counter_wt = None
                maybe_update_trend_state_with_wave(state, w, cfg)
                continue

            # Reverzni vlna po trend-dir EXT
            # idx 1 a otoci trend i bez prurazu struktury. Prezila vycisteni
            # ext_active_wave (Mechanismus A/B) — dokud cena neprorazila EXT extrem.
            if ext_climax_reversal_dir is not None and wdir == ext_climax_reversal_dir:
                wave_is_counter = (
                    (state.direction == "bull" and wdir == -1)
                    or (state.direction == "bear" and wdir == 1)
                )
                if wave_is_counter:
                    state.direction = "bear" if wdir == -1 else "bull"
                    state.last_up_box_bottom = None
                    state.last_down_box_top = None
                    if wdir == 1:
                        counter_up = 1
                        last_same_dir_up_wt = wt
                        counter_down = 0
                        last_same_dir_down_wt = None
                    else:
                        counter_down = 1
                        last_same_dir_down_wt = wt
                        counter_up = 0
                        last_same_dir_up_wt = None
                    result[wt] = WaveSequenceInfo(1, None, is_bos_wave=True)
                    ext_active_wave = None
                    first_ext_counter_wt = None
                    ext_climax_reversal_dir = None
                    state.is_bos_wave_pending = False
                    trend_established_by_ext = bool(w.get("is_ext"))
                    ext1_count_window = bool(w.get("is_ext"))
                    if w.get("is_ext"):
                        ext1_counter_idx = 0
                        last_ext1_counter_wt = None
                    maybe_update_trend_state_with_wave(state, w, cfg)
                    continue
        
            # KROK 3: Vlna PO EXT (ext_active_wave aktivní)
            if ext_active_wave is not None:
                # 3.1: Counter vlna po EXT
                wave_is_counter = (
                    (state.direction == "bull" and wdir == -1)
                    or (state.direction == "bear" and wdir == 1)
                )
                if wave_is_counter:
                    # EXT-1 okno: protismerne vlny se pocitaji jako nezavisla
                    # sekvence 1,2,3,4 (jen vykreslene — ghost hhX vlny vypadnou).
                    parallel_ext1_counting = ext1_count_window or ext1_counter_idx > 0
                    if parallel_ext1_counting:
                        if ext1_count_window and _ghost_skip_wave(
                            w, cfg, hh_hl_filter, result, wt
                        ):
                            continue
                        ext1_counter_idx += 1
                        result[wt] = WaveSequenceInfo(ext1_counter_idx, last_ext1_counter_wt)
                        last_ext1_counter_wt = wt
                        continue
                    # Counter vlna po EXT, ktera trend zalozila/flipnula (scenar
                    # A/B/D) = standardni counter (None). Reverzni vlnu po
                    # trend-dir EXT (scenar C) resi ext_climax_reversal_dir vyse.
                    result[wt] = WaveSequenceInfo(None, None)
                    continue
            
                # 3.2: Trend-dir vlna po EXT (3.2.a nebo 3.2.b). Trend pokracuje
                # same-dir => EXT nebyl climax, zrus ocekavani reverzni vlny.
                ext_climax_reversal_dir = None
                if _ghost_skip_wave(w, cfg, hh_hl_filter, result, wt):
                    continue
                if wdir == 1:
                    counter_up += 1
                    result[wt] = WaveSequenceInfo(counter_up, last_same_dir_up_wt)
                    last_same_dir_up_wt = wt
                else:
                    counter_down += 1
                    result[wt] = WaveSequenceInfo(counter_down, last_same_dir_down_wt)
                    last_same_dir_down_wt = wt
                maybe_update_trend_state_with_wave(state, w, cfg)
                continue
        
            # KROK 4: Vlna mimo EXT (ext_active_wave is None)
            wave_is_trend_dir = (
                (state.direction == "bull" and wdir == 1)
                or (state.direction == "bear" and wdir == -1)
            )
            # Neutral start: prvni vlna nastavi smer; bere se jako trend-dir #1.
            if state.direction == "neutral":
                state.direction = "bull" if wdir == 1 else "bear"
                if wdir == 1:
                    counter_up = 1
                    counter_down = 0
                    last_same_dir_up_wt = wt
                    last_same_dir_down_wt = None
                else:
                    counter_down = 1
                    counter_up = 0
                    last_same_dir_down_wt = wt
                    last_same_dir_up_wt = None
                result[wt] = WaveSequenceInfo(1, None)
                trend_established_by_ext = False
                ext1_count_window = False
                ext1_protect_window = False
                maybe_update_trend_state_with_wave(state, w, cfg)
                continue

            if wave_is_trend_dir:
                # Trend pokracuje same-dir => EXT nebyl climax, zrus reverzni flag.
                ext_climax_reversal_dir = None
                # 2-vln pravidlo: trend-dir vlna v OPACNEM smeru nez climax = uz
                # druha vlna reverzniho trendu (po prvnim bouncu) => reverzace
                # potvrzena, konec climax-continuation watch.
                if climax_dir is not None and wdir == -climax_dir:
                    climax_dir = climax_idx = climax_extreme = None
                if _ghost_skip_wave(w, cfg, hh_hl_filter, result, wt):
                    continue
                if wdir == 1:
                    counter_up += 1
                    result[wt] = WaveSequenceInfo(counter_up, last_same_dir_up_wt)
                    last_same_dir_up_wt = wt
                else:
                    counter_down += 1
                    result[wt] = WaveSequenceInfo(counter_down, last_same_dir_down_wt)
                    last_same_dir_down_wt = wt
                if state.is_bos_wave_pending:
                    state.is_bos_wave_pending = False
                maybe_update_trend_state_with_wave(state, w, cfg)
            else:
                # Counter mimo EXT. EXT-1 okno: pocitej protismer 1,2,3,4
                # (jen vykreslene vlny — ghost hhX vypadnou).
                parallel_ext1_counting = ext1_count_window or ext1_counter_idx > 0
                if parallel_ext1_counting:
                    if ext1_count_window and _ghost_skip_wave(
                        w, cfg, hh_hl_filter, result, wt
                    ):
                        pass
                    else:
                        ext1_counter_idx += 1
                        result[wt] = WaveSequenceInfo(ext1_counter_idx, last_ext1_counter_wt)
                        last_ext1_counter_wt = wt
                else:
                    result[wt] = WaveSequenceInfo(None, None)

        # Per-bar EXT-1 ochranne okno (po vsech KROK 1/KROK 2 prechodech na baru i).
        if window_out is not None:
            window_out.append(bool(ext1_protect_window))

    return result


def _wave_bar_index(w: dict, df: pd.DataFrame) -> int:
    """Bar index vlny (draw_right = potvrzeny extrem, shodne s wave_sequence smyckou)."""
    dr = w.get("draw_right")
    if dr is not None:
        return int(dr)
    dl = w.get("draw_left")
    if dl is not None:
        return int(dl)
    return 0 if df is None or df.empty else max(0, len(df) - 1)


def _is_trend_flip_wave(w: dict) -> bool:
    """True pokud vlna zpusobila BOS flip a zacina novy trend (is_bos_wave z seq precompute)."""
    return bool(w.get("is_bos_wave"))


def _tp_mode_str(cfg: BotConfig) -> str:
    tpm = getattr(cfg, "tp_mode", TPMode.RRR_FIXED)
    if isinstance(tpm, TPMode):
        return tpm.value
    return str(tpm).lower()


def compute_ext1_protection_bars(df: pd.DataFrame,
                                 waves: List[dict],
                                 cfg: BotConfig) -> List[int]:
    """
    Per-bar int array — směr EXT-1 ochrany proti BOS exitu.
    1 = UP ochrana, -1 = DOWN ochrana, 0 = žádná ochrana.

    Logika ukončení (dle tp_mode a wave_2_no_tp_enable):
    - tp_mode = rrr_fixed: ochrana končí na první wave s idx >= 2 ve směru trendu
    - jinak + wave_2_no_tp_enable=True: ochrana končí na první wave s idx > wave_2_no_tp_max_index
    - jinak + wave_2_no_tp_enable=False: ochrana končí na první wave s idx >= 2

    Start ochrany: bar EXT1 v trendu; pokud EXT1 je zaroven BOS flip, od bar+1.
    Konec ochrany: bar prvni trend-dir vlny s idx >= threshold (ten bar je False).
    Pri BOS flipu se rozpracovane okno uzavre a resetuje pred novym EXT1.
    """
    n = 0 if df is None else len(df)
    bars = [0] * n

    if not waves or n == 0:
        return bars

    if not _get_ext1_protect_flag(cfg):
        return bars

    seq_info = compute_wave_sequence_info_per_wave(df, waves, cfg)
    propagate_seq_info_to_waves(waves, seq_info)

    tp_mode = _tp_mode_str(cfg)

    if tp_mode == TPMode.RRR_FIXED.value:
        protection_end_min_idx = 2
    else:
        no_tp_enabled = bool(getattr(cfg, "wave_2_no_tp_enable", False))
        if no_tp_enabled:
            max_idx = int(getattr(cfg, "wave_2_no_tp_max_index", 2))
            protection_end_min_idx = max_idx + 1
        else:
            protection_end_min_idx = 2

    current_trend_dir: Optional[int] = None
    ext1_start_bar: Optional[int] = None

    def _mark_protection_window(start: int, end: int, prot_dir: int) -> None:
        for b in range(start, end):
            if 0 <= b < n:
                bars[b] = prot_dir

    sorted_waves = sorted(
        (w for w in waves if w.get("draw_right") is not None),
        key=lambda w: int(w["draw_right"]),
    )

    for w in sorted_waves:
        w_bar = _wave_bar_index(w, df)
        w_dir = int(w.get("dir", 0) or 0)
        w_idx = w.get("index_in_trend")
        w_is_ext = bool(w.get("is_ext"))
        flip_wave = _is_trend_flip_wave(w)

        if flip_wave:
            if ext1_start_bar is not None and current_trend_dir is not None:
                _mark_protection_window(ext1_start_bar, w_bar, current_trend_dir)
            current_trend_dir = w_dir
            ext1_start_bar = None

        if w_is_ext and w_idx == 1:
            if current_trend_dir is None:
                current_trend_dir = w_dir
            if w_dir == current_trend_dir:
                # Novy trend po BOS flipu: ochrana az od baru PO flip/EXT1 baru.
                start_bar = w_bar + 1 if flip_wave else w_bar
                ext1_start_bar = start_bar if start_bar < n else None

        if ext1_start_bar is not None and current_trend_dir is not None and w_dir == current_trend_dir:
            if w_idx is not None and int(w_idx) >= protection_end_min_idx:
                _mark_protection_window(ext1_start_bar, w_bar, current_trend_dir)
                ext1_start_bar = None

    if ext1_start_bar is not None and current_trend_dir is not None:
        _mark_protection_window(ext1_start_bar, n, current_trend_dir)

    return bars


def build_ext1_wave_times(waves: List[dict]) -> set[str]:
    """wave_time EXT1 vln ve směru trendu (index_in_trend == 1, po propagate_seq_info)."""
    return {
        str(w["wave_time"])
        for w in waves
        if bool(w.get("is_ext")) and w.get("index_in_trend") == 1
    }


def ext1_protection_active_on_bar(
    bar_idx: int, per_bar: List[int], cfg: BotConfig,
) -> int:
    """Vrací směr EXT-1 ochrany na daném baru (1, -1, nebo 0)."""
    if not _get_ext1_protect_flag(cfg):
        return 0
    if not per_bar or bar_idx < 0 or bar_idx >= len(per_bar):
        return 0
    return int(per_bar[bar_idx])


def ext1_close_blocked_on_bar(
    bar_idx: int,
    per_bar: List[int],
    cfg: BotConfig,
    reason: str,
    *,
    trade: Any = None,
    main_trend_dir: int = 0,
) -> bool:
    """
    True pokud se na baru nesmi zavrit z duvodu `reason` kvuli EXT-1 ochrane.

    Vyjimky: SL, END_OF_DATA; EXT counter (ECT_/ECB_) a WAVE counter z predchoziho trendu.
    """
    if reason in ("SL", "END_OF_DATA"):
        return False
    if trade is not None and (is_ext_counter_trade(trade) or is_wave_counter_trade(trade)):
        return False
        
    active_ext1_dir = ext1_protection_active_on_bar(bar_idx, per_bar, cfg)
    if active_ext1_dir == 0:
        return False
        
    # Blokace platí POUZE pro pozice, které jsou ve směru samotné EXT 1 vlny.
    # Např. EXT 1 UP (active_ext1_dir = 1) blokuje pouze long pozice (trade_dir = 1).
    if trade is not None:
        trade_dir = int(getattr(trade, "dir", 0))
        if trade_dir != 0 and trade_dir != active_ext1_dir:
            return False # Pozice jede proti směru EXT 1 ochrany, propustíme ji.
            
    return True


def find_wave_by_time(waves: List[dict], wave_time: Optional[str]) -> Optional[dict]:
    """Helper: linearni najdi vlnu podle wave_time (krajni edge case None -> None)."""
    if not wave_time:
        return None
    target = str(wave_time)
    for w in waves:
        if str(w.get("wave_time")) == target:
            return w
    return None
