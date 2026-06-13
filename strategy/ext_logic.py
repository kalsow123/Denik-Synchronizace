"""
EXT BLOK — ciste vypocty pro velke vlny (move_pct >= cfg.ext_wave_min_pct).

Tento modul je sdileny mezi live botem (`runtime.ext_live`, `runtime.live_loop`)
a backtest enginem (`backtest.engine`). Obsahuje:
  - `is_ext_wave`              : test, zda vlna splnuje EXT prah a EXT je zapnuty.
  - `compute_ext_metadata`     : doplnek do `wave` dictu (ext_high, ext_low,
                                 sekundarni fib, BOS level).
  - `compute_secondary_signal` : synth wave pro sekundarni EXT entry vstup
                                 (tag = "ext_0236").
  - `compute_counter_signal`   : synth signal pro counter pozici (time / BOS).
  - `bos_triggered_for_ext_close` : close-based detekce EXT BOS pro 1 bar.
  - `parse_ext_counter_time`   : robustni parser HH:MM stringu.
  - `bar_time_at_or_past_counter_time` : test casoveho counter triggeru.

POZN. EXT logika ZA JEDNOTLIVE PRIPADY VYTVARENI ORDERU SE NESMI starat —
zde vracime pouze cisty popis "co" + "za jakou cenu", caller ridi side effecty
(MT5 / backtest engine) a deduplikaci pres `core.signal_keys.get_signal_key`
a `infra.order_comments` prefixy.
"""
from __future__ import annotations

from datetime import datetime, time
from typing import Any, Iterable, Literal, Optional

from config.bot_config import BotConfig


# ---------------------------------------------------------------------------
# Entry tag konstanty — sjednoceny popis pro dedup keys + MT5 commenty
# ---------------------------------------------------------------------------

ENTRY_TAG_BASE = "base"                  # standardni vstup z vlny (i EXT primary)
ENTRY_TAG_EXT_SECONDARY = "ext_0236"     # sekundarni EXT vstup (default fib 0.236)
ENTRY_TAG_EXT_COUNTER_TIME = "ext_counter_time"
ENTRY_TAG_EXT_COUNTER_BOS = "ext_counter_bos"

# MT5 comment prefixy (parita s infra.orders — bez importu kvuli cyklu)
EXT_PRIMARY_WAVE_COMMENT_PREFIX = "EWP_"
EXT_SECONDARY_COMMENT_PREFIX = "E23_"
EXT_COUNTER_TIME_COMMENT_PREFIX = "ECT_"
EXT_COUNTER_BOS_COMMENT_PREFIX = "ECB_"


def is_ext_wave_pending_comment(comment: str) -> bool:
    """
    True pro pending s delsi expiraci (`ext_order_expiry_days`) a BOS imunitou:
      - EWP_ … primarni fib retracement na EXT vlne (W{wave_time} drive nebyl chraneny),
      - E23_ … sekundarni EXT LIMIT.
    Nezahrnuje ECT_/ECB_ (typicky market, ne pending lifecycle).
    """
    c = str(comment or "")
    return (
        c.startswith(EXT_PRIMARY_WAVE_COMMENT_PREFIX)
        or c.startswith(EXT_SECONDARY_COMMENT_PREFIX)
    )


def is_ext_block_trade(obj: Any) -> bool:
    """True pro otevrene/pending pozice z EXT bloku (E23_ / ECT_ / ECB_)."""
    if not getattr(obj, "is_ext", False):
        return False
    tag = str(getattr(obj, "entry_tag", "") or "")
    return tag in (
        ENTRY_TAG_EXT_SECONDARY,
        ENTRY_TAG_EXT_COUNTER_TIME,
        ENTRY_TAG_EXT_COUNTER_BOS,
    )


def is_ext_counter_trade(obj: Any) -> bool:
    """True pro EXT counter pozice (cas / BOS), ne WAVE counter."""
    if not getattr(obj, "is_ext", False):
        return False
    tag = str(getattr(obj, "entry_tag", "") or "")
    return tag in (ENTRY_TAG_EXT_COUNTER_TIME, ENTRY_TAG_EXT_COUNTER_BOS)


def is_ext_block_comment(comment: str) -> bool:
    """True pokud MT5 comment patri EXT bloku (E23_ / ECT_ / ECB_)."""
    c = str(comment or "")
    return (
        c.startswith(EXT_SECONDARY_COMMENT_PREFIX)
        or c.startswith(EXT_COUNTER_TIME_COMMENT_PREFIX)
        or c.startswith(EXT_COUNTER_BOS_COMMENT_PREFIX)
    )


def ext_block_wave_time_from_comment(comment: str) -> Optional[str]:
    """Vrati parent EXT wave_time z MT5 commentu E23_/ECT_/ECB_{wave_time}."""
    c = str(comment or "")
    for prefix in (
        EXT_SECONDARY_COMMENT_PREFIX,
        EXT_COUNTER_TIME_COMMENT_PREFIX,
        EXT_COUNTER_BOS_COMMENT_PREFIX,
    ):
        if c.startswith(prefix):
            wt = c[len(prefix):]
            return wt if wt else None
    return None


def is_ext_block_trade_from_wave(obj: Any, ext_wave_time: str) -> bool:
    """True pokud obj je EXT block pozice otevrena z dane parent EXT vlny."""
    if not is_ext_block_trade(obj):
        return False
    return str(getattr(obj, "wave_time", "") or "") == str(ext_wave_time)


def is_ext_secondary_trade(obj: Any) -> bool:
    """True pro EXT secondary pozici (E23_, fib 0.236 i 0.5)."""
    if not getattr(obj, "is_ext", False):
        return False
    tag = str(getattr(obj, "entry_tag", "") or "")
    return tag == ENTRY_TAG_EXT_SECONDARY


def is_ext_primary_wave_trade(obj: Any) -> bool:
    """
    Primarni WAVE vstup z EXT vlny (fib50 / sl_fib), ne EXT block E23_/ECT_/ECB_.

    Chova se jako WAVE_COUNTER: proti trendu prezije az do BOS flipu; po flipu ve
    smeru noveho trendu jede dal (TP_WAVE_N / SL), proti novemu trendu se zavre.
    """
    if not getattr(obj, "is_ext", False):
        return False
    if is_ext_block_trade(obj):
        return False
    if getattr(obj, "is_counter", False):
        return False
    if getattr(obj, "is_two_sided_mirror", False):
        return False
    tag = str(getattr(obj, "entry_tag", "") or "")
    return tag in ("", ENTRY_TAG_BASE, "base")


def is_ext_block_trade_on_parent_wave(obj: Any, current_wave_time: str) -> bool:
    """
    True pokud obj je EXT block pozice (E23_ / ECT_ / ECB_) a aktuální vlna
    (`current_wave_time`) je SAMA parent EXT vlna, ze které pozice vznikla.

    Použití: ochrana proti zavření EXT pozice na stejné vlně, ze které vznikla.
    Pravidlo (uziv. pozadavek): EXT secondary, BOS_EXT a BOS_EXT_TIME pozice
    se nesmí zavřít mimo SL na stejné EXT vlně, ze které vznikly.
    """
    if not is_ext_block_trade(obj):
        return False
    return str(getattr(obj, "wave_time", "") or "") == str(current_wave_time)


def is_trade_within_parent_ext_window(
    trade: Any,
    *,
    wave_birth_by_time: dict[str, int],
    bar_idx: int,
) -> bool:
    """
    True pokud EXT block trade je v ochrannem okne sve parent EXT vlny:
    
      = parent EXT (trade.wave_time) je stale NEJNOVEJSI naroizena vlna 
        na aktualnim baru bar_idx
        
      = ZADNA dalsi vlna po parent EXT jeste nevznikla
    
    Pouziti: ochrana proti zavreni EXT block pozice (E23_/ECT_/ECB_) na 
    jakemkoli baru behem zivota jeji parent EXT vlny — TP_WAVE_N, BOS, 
    EXT_BOS_CLOSE musi byt blokovany. Povolen je pouze SL a END_OF_DATA.
    
    Pravidlo (uziv. pozadavek):
      EXT block pozice se NESMI zavrit mimo SL na sve parent EXT vlne. 
      Jakmile po parent EXT vznikne nova wave, ochrana konci a pozice 
      se ridi pravidly noveho trendu / nasledujici vlny.
    
    Argumenty:
      trade: OpenTrade / PendingOrder / dict-like s atributy is_ext + entry_tag + wave_time
      wave_birth_by_time: mapa wave_time -> bar_idx narozeni vlny (engine.wave_birth_by_time)
      bar_idx: aktualni bar index
    
    Vraci False pokud:
      - trade neni EXT block (E23_ / ECT_ / ECB_)
      - parent EXT wave_time neni v wave_birth_by_time
      - po parent EXT uz vznikla nejaka jina wave s wave_birth <= bar_idx
    """
    if not is_ext_block_trade(trade):
        return False
    
    parent_wt = str(getattr(trade, "wave_time", "") or "")
    if not parent_wt:
        return False
    
    parent_birth = wave_birth_by_time.get(parent_wt)
    if parent_birth is None:
        return False
    
    # Najdi nejnovejsi narozenou wave do bar_idx vcetne
    latest_birth = -1
    for wt, birth in wave_birth_by_time.items():
        try:
            b = int(birth)
        except (TypeError, ValueError):
            continue
        if b <= int(bar_idx) and b > latest_birth:
            latest_birth = b
    
    # Ochrana plati POKUD parent EXT je nejnovejsi narozena wave
    return int(parent_birth) == latest_birth


# ---------------------------------------------------------------------------
# Detekce EXT vlny + metadata
# ---------------------------------------------------------------------------

def is_ext_wave(wave: dict[str, Any], cfg: BotConfig) -> bool:
    """
    True pokud je `cfg.ext_enabled` a wave["move_pct"] >= effective_threshold.

    Effective threshold:
      effective_threshold = max(wave_min_pct,
                                ext_wave_min_pct - relax_factor * weekend_gap_pct)

    `weekend_gap_pct` (default 0) je pripsano vlne v `wave_detection_pine`
    pouze pokud vlna prekracuje vikendovy data gap a smer gapu se shoduje se
    smerem vlny. `relax_factor` ridi `cfg.ext_weekend_gap_relax_factor`
    (default 0.0 = vypnuto, 0.5 = doporuceno pro LIVE).

    Pri `ext_weekend_gap_relax_factor=0` se chovani zachova bit-perfect (test
    backward compatibility).
    """
    if not bool(getattr(cfg, "ext_enabled", False)):
        return False
    try:
        move_pct = float(wave.get("move_pct", 0.0))
    except (TypeError, ValueError):
        return False
    threshold = float(getattr(cfg, "ext_wave_min_pct", 0.0) or 0.0)
    if threshold <= 0.0:
        return False

    relax_factor = float(getattr(cfg, "ext_weekend_gap_relax_factor", 0.0) or 0.0)
    if relax_factor > 0.0:
        try:
            gap_pct = float(wave.get("weekend_gap_pct", 0.0) or 0.0)
        except (TypeError, ValueError):
            gap_pct = 0.0
        if gap_pct > 0.0:
            # Floor je `wave_min_pct` (aby se EXT nepripsalo bezne male vlne).
            wave_min = float(getattr(cfg, "wave_min_pct", 0.0) or 0.0)
            effective = max(wave_min, threshold - relax_factor * gap_pct)
            return move_pct >= effective

    return move_pct >= threshold


def _ensure_min_sl_distance(entry: float, sl: float, *, is_buy: bool,
                            min_pct: float) -> float:
    """
    Vrati SL upraveny tak, aby |entry-sl| odpovidal alespon `min_pct` % entry.

    `min_pct` je v procentech (napr. 0.16 = 0.16% trhu). Funkce neposunuje SL
    na blizsi stranu — pokud uz je dal nez minimum, vraci ho nezmeneny.
    """
    if min_pct <= 0.0:
        return float(sl)
    min_dist = abs(float(entry)) * float(min_pct) / 100.0
    if min_dist <= 0.0:
        return float(sl)
    cur_dist = abs(float(entry) - float(sl))
    if cur_dist >= min_dist:
        return float(sl)
    if is_buy:
        return float(entry) - min_dist
    return float(entry) + min_dist


def compute_ext_metadata(wave: dict[str, Any], cfg: BotConfig) -> None:
    """
    In-place doplneni EXT metadat do `wave` dictu, pokud je EXT aktivni a vlna
    prekroci prah. Ulozi:
      - is_ext: bool
      - ext_high, ext_low (= box_top, box_bottom)
      - ext_secondary_entry, ext_secondary_sl: ceny sekundarniho EXT vstupu
        (s aplikovanym `ext_min_sl_move_pct`).
      - ext_bos_level: cenova hranice BOS (close-based; smer dle smeru vlny).

    Pokud is_ext=False, do wave nedosadi nic (zachova bit-perfect existujici
    chovani pro vsechny ne-EXT cesty).
    """
    if not is_ext_wave(wave, cfg):
        return

    try:
        box_top = float(wave["box_top"])
        box_bot = float(wave["box_bottom"])
    except (KeyError, TypeError, ValueError):
        return
    if box_top <= box_bot:
        return

    direction = int(wave.get("dir", 0))
    if direction not in (1, -1):
        return

    rng = box_top - box_bot
    sec_lvl = float(getattr(cfg, "ext_secondary_fib_level", 0.236))
    sec_sl_lvl = float(getattr(cfg, "ext_secondary_sl_fib_level", 0.4))
    bos_lvl = float(getattr(cfg, "ext_bos_fib_level", 0.35))
    min_sl_move_pct = float(getattr(cfg, "ext_min_sl_move_pct", 0.0) or 0.0)

    if direction == 1:
        sec_entry = box_top - rng * sec_lvl
        sec_sl = box_top - rng * sec_sl_lvl
        bos_level = box_top - rng * bos_lvl
    else:
        sec_entry = box_bot + rng * sec_lvl
        sec_sl = box_bot + rng * sec_sl_lvl
        bos_level = box_bot + rng * bos_lvl

    sec_sl = _ensure_min_sl_distance(
        sec_entry, sec_sl, is_buy=(direction == 1), min_pct=min_sl_move_pct
    )

    wave["is_ext"] = True
    wave["ext_high"] = float(box_top)
    wave["ext_low"] = float(box_bot)
    wave["ext_secondary_entry"] = float(sec_entry)
    wave["ext_secondary_sl"] = float(sec_sl)
    wave["ext_bos_level"] = float(bos_level)


# ---------------------------------------------------------------------------
# WAVE SL po EXT — prvni opacna vlna: SL na extrém EXT (ne sl_fib_level)
# ---------------------------------------------------------------------------

def sl_at_ext_extreme_for_opposite_wave(
    wave: dict[str, Any],
    ext_wave: dict[str, Any],
) -> Optional[float]:
    """
    SL pro prvni opacnou vlnu po EXT:
      - LONG (UP, dir=1) po BEAR EXT → ext_low (LOW EXT)
      - SHORT (DOWN, dir=-1) po BULL EXT → ext_high (HIGH EXT)

    Vraci None pokud geometrie entry/SL neni validni (caller pouzije wave["sl"]).
    """
    try:
        direction = int(wave.get("dir", 0))
        entry = float(wave["fib50"])
    except (KeyError, TypeError, ValueError):
        return None
    if direction not in (1, -1):
        return None

    try:
        if direction == 1:
            sl = float(ext_wave.get("ext_low", ext_wave["box_bottom"]))
            if sl < entry:
                return sl
        else:
            sl = float(ext_wave.get("ext_high", ext_wave["box_top"]))
            if sl > entry:
                return sl
    except (KeyError, TypeError, ValueError):
        return None
    return None


def apply_first_opposite_wave_sl_after_ext(
    wave: dict[str, Any],
    *,
    ext_anchor: Optional[dict[str, Any]],
    cfg: BotConfig,
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    """
    Pokud `ext_anchor` je posledni EXT vlna a `wave` je prvni opacna vlna po ni,
    prepise `sl` na extrém EXT. Jinak vrati wave beze zmeny.

    Vraci (wave_pro_vstup, novy_anchor). Po prvni opacne vlne je anchor None.
    """
    if not bool(getattr(cfg, "ext_enabled", False)):
        return wave, ext_anchor
    if ext_anchor is None or is_ext_wave(wave, cfg):
        return wave, ext_anchor

    try:
        wdir = int(wave.get("dir", 0))
        edir = int(ext_anchor.get("dir", 0))
    except (TypeError, ValueError):
        return wave, ext_anchor
    if wdir not in (1, -1) or edir not in (1, -1) or wdir != -edir:
        return wave, ext_anchor

    sl_ext = sl_at_ext_extreme_for_opposite_wave(wave, ext_anchor)
    out = dict(wave)
    if sl_ext is not None:
        out["sl"] = float(sl_ext)
    return out, None


# ---------------------------------------------------------------------------
# Synth signaly — runtime + backtest pak posila do entry pipeline
# ---------------------------------------------------------------------------

def compute_secondary_signal(wave: dict[str, Any], cfg: BotConfig) -> Optional[dict[str, Any]]:
    """
    Vrati novy dict reprezentujici sekundarni EXT vstup (tag = ext_0236).

    Stejny smer jako vlna, entry/sl z EXT metadat. TP urcuje caller pres
    `resolve_effective_tp` (podle cfg.tp_mode).

    Vraci None pokud wave nema EXT metadata nebo SL/entry geometrie neni validni.
    """
    if not wave.get("is_ext"):
        return None
    try:
        sec_entry = float(wave["ext_secondary_entry"])
        sec_sl = float(wave["ext_secondary_sl"])
    except (KeyError, TypeError, ValueError):
        return None

    direction = int(wave.get("dir", 0))
    if direction not in (1, -1):
        return None

    is_buy = (direction == 1)
    sl_valid = (sec_sl < sec_entry) if is_buy else (sec_sl > sec_entry)
    if not sl_valid:
        return None

    sig = dict(wave)
    sig["fib50"] = float(sec_entry)
    sig["sl"] = float(sec_sl)
    sig.pop("tp", None)
    sig["entry_tag"] = ENTRY_TAG_EXT_SECONDARY
    sig["_ext_origin"] = "secondary"
    # Sekundarni EXT vstup obchazi pasionku puvodni vlny — ma vlastni geometrii.
    sig.pop("fib_abort", None)
    return sig


def compute_ext_secondary_take_profit(
    cfg: BotConfig,
    entry: float,
    sl: float,
    *,
    is_buy: bool,
) -> Optional[float]:
    """
    TP pro EXT secondary (E23_): vzdy None — bez broker TP.

    E23_ se na parent EXT vlne zavira jen na SL nebo aktivnimi pravidly
    (BOS / TP-vlna N / EXT_BOS close po narozeni dalsi vlny). Broker TP by
    pozici zaviral prilis brzy (parita s ochranou EXT block na parent vlne).
    """
    return None


def compute_counter_signal(
    wave: dict[str, Any],
    cfg: BotConfig,
    *,
    source: str,
    market_price: float,
) -> Optional[dict[str, Any]]:
    """
    Vrati synth signal pro counter (protipozici).

    `source` urci entry tag:
      - "time" -> ENTRY_TAG_EXT_COUNTER_TIME (timer counter)
      - "bos"  -> ENTRY_TAG_EXT_COUNTER_BOS (counter z EXT BOS)

    Counter:
      - je v OPACNEM smeru nez EXT vlna (UP wave -> SELL counter, DOWN wave -> BUY counter).
      - entry_price = market_price (vstup market v live, market simulace v backtestu).
      - SL primarne na ext_high/ext_low parent EXT vlny; volitelny minimalni floor
        (`ext_counter_min_sl_enabled` + `ext_counter_min_sl_pct`, default 0.16 %).
        Pri vypnutem flooru zustava SL ciste na extrému. Legacy: ext_counter_sl_pct.
        BUY counter -> SL pod entry, SELL counter -> SL nad entry.
      - TP urcuje caller pres `resolve_effective_tp` (rrr_fixed/bos_exit);
        u bos_exit_priority / wave_target_n zustava None.

    Casovy counter (`source="time"`, tag ext_counter_time):
      - otevre se az v `cfg.ext_counter_time` (napr. 21:00), dokud kandidat prvni
        nasledujici vlny po EXT nedosahl `cfg.ext_wave_min_pct` (jeste pred
        cfg.min_opp_bars — „nedefinovana“ vlna, která teprve roste).
      - blokace v engine: bar index z Pine simulace (`ext_counter_suppress_from_bar`).
      - po prvni takove pozici pro danou EXT uz dalsi counter (cas ani bos) nevznikne
        (rizeni v engine pres done mnoziny).
    """
    if not wave.get("is_ext"):
        return None
    if source not in ("time", "bos"):
        return None

    direction = int(wave.get("dir", 0))
    if direction not in (1, -1):
        return None
    counter_dir = -direction
    sl_anchor = compute_ext_counter_sl_price(
        wave, market_price=float(market_price), counter_dir=counter_dir, cfg=cfg
    )
    if sl_anchor is None:
        return None

    tag = ENTRY_TAG_EXT_COUNTER_TIME if source == "time" else ENTRY_TAG_EXT_COUNTER_BOS

    sig = dict(wave)
    sig["dir"] = int(counter_dir)
    sig["fib50"] = float(market_price)
    sig["sl"] = float(sl_anchor)
    sig["tp"] = None
    sig.pop("wave_target_tp_price", None)
    sig["entry_tag"] = tag
    sig["_ext_origin"] = f"counter_{source}"
    sig.pop("fib_abort", None)
    return sig


def ext_counter_min_sl_enabled(cfg: BotConfig) -> bool:
    """True = u EXT counter uplatnit min SL % od entry (ext_counter_min_sl_pct)."""
    return bool(getattr(cfg, "ext_counter_min_sl_enabled", True))


def ext_counter_min_sl_pct_value(cfg: BotConfig) -> float:
    """Minimalni SL % od entry pro EXT counter (time + EXT_BOS). 0 = vypnuto."""
    if not ext_counter_min_sl_enabled(cfg):
        return 0.0
    raw = getattr(cfg, "ext_counter_min_sl_pct", None)
    if raw is None:
        raw = getattr(cfg, "ext_counter_sl_pct", 0.16)
    try:
        return max(float(raw), 0.0)
    except (TypeError, ValueError):
        return 0.16


def compute_ext_counter_sl_price(
    wave: dict[str, Any],
    *,
    market_price: float,
    counter_dir: int,
    cfg: BotConfig,
) -> Optional[float]:
    """
    Shared SL helper pro oba EXT counter typy:
      - EXT counter time
      - EXT_BOS

    NOVE PRAVIDLO: SL je umisten na extremu parent EXT vlny (ext_high pro SHORT, 
    ext_low pro LONG). Pokud by byl SL prilis blizko entry (nebo dokonce na opacne 
    strane) a `ext_counter_min_sl_enabled` je True, pouzije se
    `ext_counter_min_sl_pct` jako minimalni vzdalenost.
    """
    if counter_dir not in (1, -1):
        return None
    
    try:
        if counter_dir == 1:
            # LONG counter (po DOWN EXT vlne) -> SL na ext_low
            base_sl = float(wave.get("ext_low", wave.get("box_bottom", market_price)))
        else:
            # SHORT counter (po UP EXT vlne) -> SL na ext_high
            base_sl = float(wave.get("ext_high", wave.get("box_top", market_price)))
    except (TypeError, ValueError):
        base_sl = float(market_price)

    sl_pct = ext_counter_min_sl_pct_value(cfg)

    # Zajistit, ze SL je na spravne strane a dostatecne daleko
    return _ensure_min_sl_distance(
        entry=market_price,
        sl=base_sl,
        is_buy=(counter_dir == 1),
        min_pct=sl_pct,
    )


# ---------------------------------------------------------------------------
# EXT BOS
# ---------------------------------------------------------------------------

ExtBosState = Literal["armed", "cancelled"]


def advance_ext_bos_state(
    current_state: ExtBosState,
    *,
    ext_dir: int,
    wave_dir: int,
) -> ExtBosState:
    """
    Stav EXT BOS market counteru vzhledem k potvrzenym vlnam po EXT:
      - armed     : EXT BOS smi triggernout na close pres 0.35
      - cancelled : po EXT uz prisla vlna ve stejnem smeru jako EXT

    EXT BOS se po EXT neceka na potvrzeni opacne vlny. Samotny close pres 0.35
    je uz dostatecny trigger proti smeru EXT. Jakmile ale po EXT vznikne nova
    vlna ve STEJNEM smeru, EXT BOS se rusi a znovu se neaktivuje.
    """
    if current_state == "cancelled":
        return current_state
    if ext_dir not in (1, -1) or wave_dir not in (1, -1):
        return current_state
    if wave_dir == ext_dir:
        return "cancelled"
    return current_state


def classify_ext_bos_state(
    ext_wave_time: str,
    ext_dir: int,
    all_waves: list[dict[str, Any]],
    wave_birth: dict[str, int],
) -> ExtBosState:
    """
    Vyhodnoti aktualni EXT BOS stav z potvrzenych vln po EXT.

    EXT BOS je po EXT aktivni hned a plati do chvile, nez po dane EXT vznikne
    nova potvrzena vlna ve smeru puvodni EXT.
    """
    try:
        ext_bi = int(wave_birth[str(ext_wave_time)])
        edir = int(ext_dir)
    except (KeyError, TypeError, ValueError):
        return "armed"
    if edir not in (1, -1):
        return "armed"

    ordered = sorted(
        all_waves,
        key=lambda w: int(wave_birth.get(str(w.get("wave_time", "")), 10**9)),
    )
    state: ExtBosState = "armed"
    for w in ordered:
        wt = str(w.get("wave_time", ""))
        if not wt or wt == str(ext_wave_time):
            continue
        try:
            bi = int(wave_birth[wt])
            wdir = int(w.get("dir", 0))
        except (KeyError, TypeError, ValueError):
            continue
        if bi <= ext_bi:
            continue
        state = advance_ext_bos_state(state, ext_dir=edir, wave_dir=wdir)
        if state == "cancelled":
            break
    return state


def ext_bos_market_entry_allowed(state: ExtBosState | None) -> bool:
    """True pokud EXT BOS zatim nebyl zrusen novou vlnou ve smeru EXT."""
    return state != "cancelled"


def build_ext_bos_state_map(
    all_waves: list[dict[str, Any]],
    wave_birth: dict[str, int],
    cfg: BotConfig,
) -> dict[str, ExtBosState]:
    """Pro kazdou EXT vlnu aktualni stav EXT BOS (live refresh / diagnostika)."""
    out: dict[str, ExtBosState] = {}
    for w in all_waves:
        if not is_ext_wave(w, cfg):
            continue
        wt = str(w.get("wave_time", ""))
        if not wt:
            continue
        out[wt] = classify_ext_bos_state(
            wt, int(w.get("dir", 0)), all_waves, wave_birth,
        )
    return out


def ext_bos_allowed_at_bar(wave: dict[str, Any], bar_idx: int) -> bool:
    """
    EXT BOS handler smi triggernout az po potvrzenem extrému vlny (draw_right).

    Po vikendovem merge muze byt birth bar drive nez draw_right — vlna jeste
    roste; EXT BOS nesmi zavirat pozice ani counter driv, nez je box kompletni.
    Bezne EXT vlny maji birth >= draw_right — chovani beze zmeny.
    """
    try:
        dr = int(wave["draw_right"])
    except (KeyError, TypeError, ValueError):
        return False
    try:
        return int(bar_idx) >= dr
    except (TypeError, ValueError):
        return False


def ext_bos_visual_left_bar(
    wave: dict[str, Any],
    *,
    birth_bar: int | None = None,
    draw_left: int | None = None,
    draw_right: int | None = None,
) -> int:
    """
    Levy okraj segmentu EXT BOS linky ve vizualu.

    Pri birth < draw_right (vikendovy merge) kreslit az od draw_right, ne od
    draw_left — pred dokoncenim EXT neni 0,35 linka k dispozici.
    """
    try:
        dl = int(draw_left if draw_left is not None else wave.get("draw_left", 0))
        dr = int(draw_right if draw_right is not None else wave.get("draw_right", dl))
    except (TypeError, ValueError):
        return int(draw_left or 0)
    if birth_bar is not None:
        try:
            if int(birth_bar) < dr:
                return dr
        except (TypeError, ValueError):
            pass
    return dl


def bos_triggered_for_ext_close(wave: dict[str, Any], bar_close: float) -> bool:
    """
    Close-based EXT BOS detekce pro JEDEN bar.

      UP wave (dir=+1): BOS pokud close < ext_bos_level.
      DOWN wave (dir=-1): BOS pokud close > ext_bos_level.

    Vraci False pokud chybi metadata.
    """
    try:
        level = float(wave["ext_bos_level"])
    except (KeyError, TypeError, ValueError):
        return False
    direction = int(wave.get("dir", 0))
    if direction == 1:
        return float(bar_close) < level
    if direction == -1:
        return float(bar_close) > level
    return False


def ext_bos_on_bar_handler_enabled(cfg: BotConfig) -> bool:
    """
    Spustit per-bar EXT BOS handler (zavření trend pozic a/nebo BOS counter entry).
    Counter TIME+BOS řídí společně ext_counter_enabled.
    """
    if not bool(getattr(cfg, "ext_enabled", False)):
        return False
    return (
        bool(getattr(cfg, "ext_close_trend_positions_on_bos", False))
        or bool(getattr(cfg, "ext_counter_enabled", False))
    )


# ---------------------------------------------------------------------------
# Counter time helpers
# ---------------------------------------------------------------------------

def parse_ext_counter_time(value: object) -> Optional[time]:
    """Parse "HH:MM" string na `datetime.time`. Vraci None pri chybe."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        hh, mm = s.split(":")
        return time(hour=int(hh), minute=int(mm))
    except (ValueError, AttributeError):
        return None


def bar_time_at_or_past_counter_time(bar_time: datetime, counter_t: time) -> bool:
    """True kdyz bar_time.time() >= counter_t (porovnani v ramci dne)."""
    if bar_time is None:
        return False
    return bar_time.time() >= counter_t


def ext_counter_time_suppressed_at_bar(
    ext_wave_time: str,
    bar_idx: int,
    suppress_from_bar: dict[str, int],
) -> bool:
    """True pokud na tomto baru uz kandidat po EXT dosahl ext_wave_min_pct."""
    try:
        sbar = suppress_from_bar.get(str(ext_wave_time))
        if sbar is None:
            return False
        return int(bar_idx) >= int(sbar)
    except (TypeError, ValueError):
        return False


def has_open_ext_counter_peer(
    open_trades: Iterable[Any],
    *,
    source: Literal["time", "bos"],
) -> bool:
    """
    True pokud uz bezi EXT counter druheho typu (TIME vs BOS).

    Mutex: dokud jeden bezi, druhy se neotevira — i pri ruznem wave_time
    (pine pre-sim vs runtime EXT).
    """
    peer_tag = (
        ENTRY_TAG_EXT_COUNTER_BOS if source == "time" else ENTRY_TAG_EXT_COUNTER_TIME
    )
    for trade in open_trades:
        if not is_ext_counter_trade(trade):
            continue
        if str(getattr(trade, "entry_tag", "") or "") == peer_tag:
            return True
    return False


def ext_counter_time_may_open(
    *,
    bos_state: ExtBosState | None,
    suppressed_after_subsequent_wave: bool,
    counter_time_already_done: bool,
    counter_bos_already_done: bool,
) -> bool:
    """
    True pokud engine smi otevrit casovy EXT counter pro danou EXT vlnu.

    Sdili stejny cancel stav jako EXT BOS market counter. Navic si zachovava
    dosavadni blokaci pro casovy counter: kandidat prvni vlny po EXT uz dosahl
    ext_wave_min_pct (pred min_opp_bars), nebo bar >= suppress_from_bar.
    """
    if not ext_bos_market_entry_allowed(bos_state):
        return False
    if suppressed_after_subsequent_wave:
        return False
    if counter_time_already_done or counter_bos_already_done:
        return False
    return True
