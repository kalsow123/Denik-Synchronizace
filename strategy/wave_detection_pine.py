"""
Emulator struktury vlny podle Pine indicatoru („Zatím v6-Y3 + extend + Fibo“, v6 overlay).

Nepocita TV kreslene objekty (liveBox / extend lastWBox pri invalidate) —
jen stavovy stroj ovlivnujici kvalifikaci %, opp, invalidate a potvrzeni vlny.

Data gap (vikend / chybejici svicky v CSV):
  - Sousedni bar s casovym skokem > 2.5× median TF se bere jako gap (bez baru uprostred).
  - Stav vlny (pivot/cand/opp_cnt) se pres gap NERESI — potvrzeni se odlozi, dokud za gap
    neprijde dalsi bar (stejne jako TV: jedna vlna pres mezeru).
  - Skok ceny gapu (predchozi close → open/high/low noveho baru) se zapocita do
    pivot/cand a tim i do move_pct (EXT prah pak muze sedet s TradingView).

Volano z strategy.wave_detection.detect_waves (jedina cesta detekce vln v projektu).
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from config.bot_config import BotConfig, abort_fib_trigger_ratio

# Nasobitel medianu casoveho kroku — vetsi mezera = data gap (vikend, delsi vypadek dat).
_DATA_GAP_MEDIAN_MULT = 2.5
# Max. pocet baru mezi koncem vlny A a zacatkem vlny B pro slouceni pres vikend (M30 ~ 2–4 bary).
_DATA_GAP_MAX_BRIDGE_BARS = 12


def _is_opp_bar(w_dir: int, close_: float, open_: float) -> bool:
    if w_dir == 1:
        return close_ <= open_
    return close_ >= open_


def _enforce_wave_min_sl(entry: float, sl: float, *, direction: int,
                         cfg: BotConfig) -> float:
    """
    Min SL jen pro standardní WAVE geometrii.

    Pokud je fib SL příliš blízko entry, odtlačí SL minimálně o `cfg.wave_min_sl`
    procent od entry ceny.
    """
    try:
        min_sl_pct = float(getattr(cfg, "wave_min_sl", 0.12))
    except (TypeError, ValueError):
        min_sl_pct = 0.12
    if min_sl_pct <= 0.0 or direction not in (1, -1):
        return float(sl)

    min_dist = abs(float(entry)) * min_sl_pct / 100.0
    current_dist = abs(float(entry) - float(sl))
    if current_dist + 1e-12 >= min_dist:
        return float(sl)
    if direction == 1:
        return float(entry) - min_dist
    return float(entry) + min_dist


def _format_wave_time_str(raw) -> str:
    """Konzistentni wave_time (%Y%m%d%H%M) — i pro numpy datetime64 z OHLC pole."""
    ts = pd.Timestamp(raw)
    return ts.strftime("%Y%m%d%H%M")


def _median_bar_timedelta(times: pd.Series) -> pd.Timedelta:
    t = pd.to_datetime(times)
    if len(t) < 2:
        return pd.Timedelta(minutes=30)
    deltas = t.diff().dropna()
    if deltas.empty:
        return pd.Timedelta(minutes=30)
    md = deltas.median()
    if pd.isna(md) or md <= pd.Timedelta(0):
        return pd.Timedelta(minutes=30)
    return md


def _compute_after_data_gap_mask(times: pd.Series,
                                 gap_mult: float = _DATA_GAP_MEDIAN_MULT) -> List[bool]:
    """mask[i] True = bar i nasleduje po casove mezere bez svice (vikend apod.)."""
    n = len(times)
    mask = [False] * n
    if n < 2:
        return mask
    t = pd.to_datetime(times)
    threshold = _median_bar_timedelta(times) * gap_mult
    for i in range(1, n):
        if t[i] - t[i - 1] > threshold:
            mask[i] = True
    return mask


def _bridge_gap_prices(
    w_dir: int,
    pivot_price: float,
    cand_price: float,
    *,
    prev_close: float,
    prev_high: float | None = None,
    prev_low: float | None = None,
    open_: float,
    high: float,
    low: float,
) -> Tuple[float, float]:
    """
    Zapocte skok pres data gap do extremu vlny (close pred mezerou → open po mezere).
    UP vlna: rozsah niz (pivot) a vysoko (cand); DOWN naopak.
    """
    pivot_price, cand_price, _, _ = _bridge_gap_prices_with_refs(
        w_dir,
        pivot_price,
        cand_price,
        prev_close=prev_close,
        prev_high=prev_high,
        prev_low=prev_low,
        open_=open_,
        high=high,
        low=low,
    )
    return pivot_price, cand_price


def _bridge_gap_prices_with_refs(
    w_dir: int,
    pivot_price: float,
    cand_price: float,
    *,
    prev_close: float,
    prev_high: float | None = None,
    prev_low: float | None = None,
    open_: float,
    high: float,
    low: float,
) -> Tuple[float, float, str, str]:
    """
    Varianta gap bridge s navratenim reference, KDE novy extrem vznikl:

      - "existing" = extrem zustal na puvodnim baru
      - "prev"     = extrem nově odpovida `prev_close` (pred-gap)
      - "cur"      = extrem nově odpovida pondelnimu/open-gap baru

    To je dulezite pro "nedotazenou" vikendovou vlnu: gap dnes umi zmenit cenu
    extremu, ale bez teto reference se cas/index extremu nemusi posunout. Vysledkem
    pak muze byt vlna s korektni cenou, ale useknuta v case. Tady modelujeme
    hybrid A+B: virtuální gap segment uvnitr detektoru, bez pridavani realne
    svicky do `df` nebo do backtest enginu.
    """
    pivot_ref = "existing"
    cand_ref = "existing"

    # U gapu ber i cele pred-gap OHLC (ne jen close). To je klicove pro
    # pripad "patek high -> nedele gap down", kde se do DOWN vlny musi
    # propsat patkovy high jako pivot.
    ph = float(prev_high) if prev_high is not None else float(prev_close)
    pl = float(prev_low) if prev_low is not None else float(prev_close)

    if w_dir == 1:
        if prev_close < pivot_price:
            pivot_price = prev_close
            pivot_ref = "prev"
        if pl < pivot_price:
            pivot_price = pl
            pivot_ref = "prev"
        if open_ < pivot_price:
            pivot_price = open_
            pivot_ref = "cur"
        if low < pivot_price:
            pivot_price = low
            pivot_ref = "cur"

        if prev_close > cand_price:
            cand_price = prev_close
            cand_ref = "prev"
        if ph > cand_price:
            cand_price = ph
            cand_ref = "prev"
        if open_ > cand_price:
            cand_price = open_
            cand_ref = "cur"
        if high > cand_price:
            cand_price = high
            cand_ref = "cur"
    else:
        if prev_close > pivot_price:
            pivot_price = prev_close
            pivot_ref = "prev"
        if ph > pivot_price:
            pivot_price = ph
            pivot_ref = "prev"
        if open_ > pivot_price:
            pivot_price = open_
            pivot_ref = "cur"
        if high > pivot_price:
            pivot_price = high
            pivot_ref = "cur"

        if prev_close < cand_price:
            cand_price = prev_close
            cand_ref = "prev"
        if pl < cand_price:
            cand_price = pl
            cand_ref = "prev"
        if open_ < cand_price:
            cand_price = open_
            cand_ref = "cur"
        if low < cand_price:
            cand_price = low
            cand_ref = "cur"

    return pivot_price, cand_price, pivot_ref, cand_ref


def _segment_extremes_with_gaps(
    df: pd.DataFrame,
    lo: int,
    hi: int,
    wdir: int,
    after_gap_mask: List[bool],
    *,
    ohlc=None,
) -> Tuple[float, float]:
    """box_top / box_bottom z useku baru [lo..hi] vcetne skoku pres data gapy."""
    if ohlc is not None:
        return _segment_extremes_with_gaps_arrays(ohlc, lo, hi, wdir, after_gap_mask)
    if hi < lo or lo < 0 or hi >= len(df):
        return 0.0, 0.0
    seg = df.iloc[lo : hi + 1]
    if seg.empty:
        return 0.0, 0.0
    bt = float(seg["high"].max())
    bb = float(seg["low"].min())
    for pos in range(lo + 1, hi + 1):
        if pos >= len(after_gap_mask) or not after_gap_mask[pos]:
            continue
        prev = df.iloc[pos - 1]
        row = df.iloc[pos]
        pc = float(prev["close"])
        o, h, l = float(row["open"]), float(row["high"]), float(row["low"])
        if wdir == 1:
            bb = min(bb, pc, o, l)
            bt = max(bt, pc, o, h)
        else:
            bt = max(bt, pc, o, h)
            bb = min(bb, pc, o, l)
    return bt, bb


def _segment_extremes_with_gaps_arrays(
    ohlc,
    lo: int,
    hi: int,
    wdir: int,
    after_gap_mask: List[bool],
) -> Tuple[float, float]:
    if hi < lo or lo < 0 or hi >= ohlc.n:
        return 0.0, 0.0
    bt = float(np.max(ohlc.high[lo : hi + 1]))
    bb = float(np.min(ohlc.low[lo : hi + 1]))
    for pos in range(lo + 1, hi + 1):
        if pos >= len(after_gap_mask) or not after_gap_mask[pos]:
            continue
        pc = float(ohlc.close[pos - 1])
        o = float(ohlc.open[pos])
        h = float(ohlc.high[pos])
        l = float(ohlc.low[pos])
        if wdir == 1:
            bb = min(bb, pc, o, l)
            bt = max(bt, pc, o, h)
        else:
            bt = max(bt, pc, o, h)
            bb = min(bb, pc, o, l)
    return bt, bb


def _waves_linked_by_data_gap(
    end_b: int,
    other: dict,
    after_gap_mask: List[bool],
    *,
    max_bridge_bars: int = _DATA_GAP_MAX_BRIDGE_BARS,
) -> bool:
    """
    True jen kdyz vlna `other` zacina hned za vikendovym (data) gapem po konci predchozi vlny.

    Nesmi platit pro libovolny gap v mesicnim useku (to by slepilo cely trend do jedne vlny).
    """
    start_o = int(other["draw_left"])
    if start_o <= end_b or start_o - end_b > max_bridge_bars:
        return False
    for g in range(end_b + 1, min(start_o + 1, len(after_gap_mask))):
        if after_gap_mask[g]:
            return True
    return False


def _waves_contiguous_after_gap(
    end_b: int,
    other: dict,
    *,
    max_bridge_bars: int = _DATA_GAP_MAX_BRIDGE_BARS,
) -> bool:
    """
    Po navázání přes víkendový gap dovol i bezprostřední pokračování stejného směru.

    Důvod:
      Reálný pátek→pondělí pohyb se může po prvním post-gap potvrzení rozdělit
      na více stejnosměrných segmentů (např. podle `min_opp_bars`). Tyto segmenty
      patří do jednoho "weekend carry" pohybu a bez sloučení vlna vyjde kratší
      (často pod EXT prahem).
    """
    start_o = int(other["draw_left"])
    return start_o > end_b and (start_o - end_b) <= max_bridge_bars


def _wave_spans_data_gap(wave: dict, after_gap_mask: List[bool]) -> bool:
    """True pokud samotná vlna už obsahuje víkendový/data gap uvnitř svého rozsahu."""
    lo = max(0, int(wave.get("draw_left", 0)))
    hi = min(len(after_gap_mask) - 1, int(wave.get("draw_right", 0)))
    if hi < lo:
        return False
    for i in range(lo + 1, hi + 1):
        if after_gap_mask[i]:
            return True
    return False


def _compute_weekend_gap_pct(
    df: pd.DataFrame,
    wave: dict,
    after_gap_mask: List[bool],
) -> float:
    """
    Velikost vikendoveho gapu uvnitr vlny v procentech, jen pokud je smer gapu
    shodny se smerem vlny. Pouziva se pro relax EXT prahu (viz `is_ext_wave`).

    Vraci max gap_pct z vsech gapu uvnitr vlny, ktere jsou ve smeru vlny.
    Pokud zadny takovy gap neni, vraci 0.0.
    """
    if df is None or df.empty:
        return 0.0
    lo = max(0, int(wave.get("draw_left", 0)))
    hi = min(len(after_gap_mask) - 1, int(wave.get("draw_right", 0)))
    if hi < lo:
        return 0.0
    wdir = int(wave.get("dir", 0))
    if wdir not in (1, -1):
        return 0.0
    best = 0.0
    for i in range(lo + 1, hi + 1):
        if not after_gap_mask[i]:
            continue
        try:
            prev_close = float(df["close"].iloc[i - 1])
            cur_open = float(df["open"].iloc[i])
        except (IndexError, KeyError, TypeError, ValueError):
            continue
        jump = cur_open - prev_close
        # Gap musi byt ve smeru vlny:
        #   DOWN vlna (wdir=-1) potrebuje DOWN gap (jump < 0)
        #   UP   vlna (wdir=+1) potrebuje UP   gap (jump > 0)
        if (wdir == -1 and jump >= 0) or (wdir == 1 and jump <= 0):
            continue
        denom = abs(prev_close)
        if denom <= 1e-12:
            continue
        gap_pct = abs(jump) / denom * 100.0
        if gap_pct > best:
            best = gap_pct
    return float(best)


def _tag_weekend_gap_pct(
    df: pd.DataFrame,
    waves: List[dict],
    after_gap_mask: List[bool],
) -> None:
    """In-place oznaceni vlny `weekend_gap_pct` (>=0) podle gapu uvnitr range."""
    for w in waves:
        try:
            w["weekend_gap_pct"] = _compute_weekend_gap_pct(df, w, after_gap_mask)
        except Exception:
            w["weekend_gap_pct"] = 0.0
    return None


def _merge_wave_group(
    df: pd.DataFrame,
    cfg: BotConfig,
    group: List[dict],
    after_gap_mask: List[bool],
) -> dict | None:
    """Slouci sousedni vlny stejneho smeru oddelene jen data gapem (vikend)."""
    if not group:
        return None
    wdir = int(group[0]["dir"])
    lo = min(int(w["draw_left"]) for w in group)
    hi = max(int(w["draw_right"]) for w in group)
    bt, bb = _segment_extremes_with_gaps(df, lo, hi, wdir, after_gap_mask)
    if bt <= bb:
        return None
    pivot_level = float(bb if wdir == 1 else bt)
    cand_level = float(bt if wdir == 1 else bb)
    return _append_wave_sig(
        cfg,
        w_dir=wdir,
        pivot_level=pivot_level,
        cand_level=cand_level,
        box_top=float(bt),
        box_bottom=float(bb),
        pivot_bar_idx=lo,
        cand_bar_idx=hi,
        wave_time_str=str(group[0]["wave_time"]),
    )


def _is_small_counter_wave(cfg: BotConfig, wave: dict) -> bool:
    """Kratka protismerna vlna mezi dvema stejnymi smery pres vikend (napr. pondelni korekce)."""
    span = int(wave["draw_right"]) - int(wave["draw_left"])
    move = float(wave.get("move_pct") or 0.0)
    return span <= _DATA_GAP_MAX_BRIDGE_BARS and move < float(cfg.wave_min_pct) * 1.5


def _merge_waves_across_data_gaps(
    df: pd.DataFrame,
    cfg: BotConfig,
    waves: List[dict],
    birth: Dict[str, int],
) -> Tuple[List[dict], Dict[str, int]]:
    """
    Slouci jen sousedni (nebo oddelene jednou malou protivlnou) stejneho smeru,
    pokud druha zacina hned za vikendovym data gapem — ne cely trend pres mesice.
    """
    if len(waves) < 2:
        return waves, birth
    after_gap = _compute_after_data_gap_mask(df["time"])
    merged: List[dict] = []
    new_birth: Dict[str, int] = {}
    i = 0
    while i < len(waves):
        w0 = waves[i]
        wdir = int(w0["dir"])
        group: List[dict] = [w0]
        end_b = int(w0["draw_right"])
        has_gap_link = _wave_spans_data_gap(w0, after_gap)
        j = i + 1

        while j < len(waves) and int(waves[j]["dir"]) == wdir:
            if _waves_linked_by_data_gap(end_b, waves[j], after_gap):
                group.append(waves[j])
                end_b = int(waves[j]["draw_right"])
                has_gap_link = True
                j += 1
            elif has_gap_link and _waves_contiguous_after_gap(end_b, waves[j]):
                # Po prvnim weekend-linku dovol navazujici stejny smer
                # i bez dalsiho gap markeru (dotazeni vlny po pondelnim openu).
                group.append(waves[j])
                end_b = int(waves[j]["draw_right"])
                j += 1
            else:
                break

        if j < len(waves) and int(waves[j]["dir"]) != wdir:
            mid = waves[j]
            if (
                j + 1 < len(waves)
                and int(waves[j + 1]["dir"]) == wdir
                and _is_small_counter_wave(cfg, mid)
                and _waves_linked_by_data_gap(end_b, waves[j + 1], after_gap)
            ):
                group.append(waves[j + 1])
                j += 2

        if len(group) > 1:
            sig = _merge_wave_group(df, cfg, group, after_gap)
            if sig is not None:
                merged.append(sig)
                wt = str(sig["wave_time"])
                births = [
                    birth[str(w["wave_time"])]
                    for w in group
                    if str(w.get("wave_time")) in birth
                ]
                if births:
                    new_birth[wt] = min(births)
            else:
                for w in group:
                    merged.append(w)
                    wt = str(w.get("wave_time", ""))
                    if wt and wt in birth:
                        new_birth[wt] = birth[wt]
            i = j
        else:
            merged.append(w0)
            wt = str(w0.get("wave_time", ""))
            if wt and wt in birth:
                new_birth[wt] = birth[wt]
            i += 1
    return merged, new_birth


def _append_wave_sig(
    cfg: BotConfig,
    *,
    w_dir: int,
    pivot_level: float,
    cand_level: float,
    box_top: float,
    box_bottom: float,
    pivot_bar_idx: int,
    cand_bar_idx: int,
    wave_time_str: str,
) -> dict | None:
    """fib50/sl/tp jako legacy; move_pct jako Pine (abs cand-pivot)/pivot*100)."""
    if box_top <= box_bottom or cand_bar_idx <= pivot_bar_idx:
        return None
    w_range = box_top - box_bottom
    move_pct = abs(cand_level - pivot_level) / max(1e-12, abs(pivot_level)) * 100.0

    fib_lvl = float(cfg.entry_fib_level)
    sl_lvl = float(cfg.sl_fib_level)
    fib50 = box_top - w_range * fib_lvl if w_dir == 1 else box_bottom + w_range * fib_lvl
    sl = box_top - w_range * sl_lvl if w_dir == 1 else box_bottom + w_range * sl_lvl
    pos_dir = w_dir
    sl_valid = (sl < fib50) if pos_dir == 1 else (sl > fib50)
    if not sl_valid:
        return None
    sl = _enforce_wave_min_sl(fib50, sl, direction=pos_dir, cfg=cfg)
    sl_dist = abs(fib50 - sl)
    tp = fib50 + cfg.rrr * sl_dist if pos_dir == 1 else fib50 - cfg.rrr * sl_dist

    # fib_abort: při zapnuté pasiónce nebo řetězcovém režimu (shift SL) — hraniční cena retracementu.
    # Poměr a_ratio z cfg (číslo uživatele vs. 2/3 mezi entry a SL fib u deep_retrace_shift_sl); viz bot_config.abort_fib_trigger_ratio.
    fib_abort = None
    a_ratio = abort_fib_trigger_ratio(cfg)
    if a_ratio is not None:
        a_lvl = float(a_ratio)
        if fib_lvl < a_lvl < sl_lvl:
            fib_abort = (
                box_top - w_range * a_lvl
                if w_dir == 1
                else box_bottom + w_range * a_lvl
            )

    sig: dict = {
        "dir": pos_dir,
        "fib50": fib50,
        "sl": sl,
        "tp": tp,
        "move_pct": float(move_pct),
        "wave_time": wave_time_str,
        "box_top": float(box_top),
        "box_bottom": float(box_bottom),
        "draw_left": int(pivot_bar_idx),
        "draw_right": int(cand_bar_idx),
    }
    if fib_abort is not None:
        sig["fib_abort"] = fib_abort
    # EXT metadata se doplni pres `strategy.ext_logic.compute_ext_metadata`,
    # pokud `cfg.ext_enabled=True` a vlna prekroci `cfg.ext_wave_min_pct`.
    # (Weekend-gap relax se dopocita az po detekci, kdyz je znamy weekend_gap_pct.)
    try:
        from strategy.ext_logic import compute_ext_metadata
        compute_ext_metadata(sig, cfg)
    except Exception:
        pass
    return sig


def _apply_wave_plus_extend(
    df: pd.DataFrame,
    cfg: BotConfig,
    waves: List[dict],
    *,
    start_idx: int = 0,
    ohlc=None,
) -> None:
    """
    WAVE + (`cfg.wave_plus`):
      1) Protáhne draw_right v čase k baru před začátkem následující vlny (nebo ke konci řady u poslední vlny).
      2) V tomto intervalu doplní finální extrém směru vlny: UP → max(high), DOWN → min(low).
      3) Přepočítá box_top/box_bottom, fib50, SL, TP, move_pct (stejný vzorec jako při potvrzení).
    wave_time zůstává (deduplikace / birth mapa).

    start_idx: inkrementální re-extend od indexu (WF merge — nemusí prepocitat cele pole vln).
    """
    if not waves or len(df) < 1:
        return
    if ohlc is None:
        from backtest.ohlc_arrays import ohlc_from_dataframe

        ohlc = ohlc_from_dataframe(df)
    n = ohlc.n
    last_ix = n - 1
    after_gap_mask = ohlc.after_data_gap.tolist()
    waves.sort(key=lambda w: int(w.get("draw_left", 0)))
    start_idx = max(0, min(int(start_idx), len(waves) - 1)) if waves else 0
    for j in range(start_idx, len(waves)):
        cur = waves[j]
        wdir = int(cur["dir"])
        left = int(cur["draw_left"])
        cur_right = int(cur["draw_right"])
        if j + 1 < len(waves):
            gap_end = min(last_ix, max(0, int(waves[j + 1]["draw_left"]) - 1))
        else:
            gap_end = last_ix
        if gap_end < cur_right:
            gap_end = cur_right
        lo, hi = min(left, gap_end), max(left, gap_end)
        if hi < lo:
            continue
        bt, bb = _segment_extremes_with_gaps(
            df, lo, hi, wdir, after_gap_mask, ohlc=ohlc,
        )
        if bt <= bb:
            continue
        pivot_level = bb if wdir == 1 else bt
        cand_level = bt if wdir == 1 else bb
        new_sig = _append_wave_sig(
            cfg,
            w_dir=wdir,
            pivot_level=float(pivot_level),
            cand_level=float(cand_level),
            box_top=bt,
            box_bottom=bb,
            pivot_bar_idx=left,
            cand_bar_idx=gap_end,
            wave_time_str=str(cur["wave_time"]),
        )
        if new_sig is None:
            continue
        cur.update(new_sig)


def _remove_wick_invalidated_corrections(
    df: pd.DataFrame,
    cfg: BotConfig,
    waves: List[dict],
    birth: Dict[str, int],
) -> Tuple[List[dict], Dict[str, int]]:
    """
    Post-detection cleanup pro specificky vzorec "wick bez BOS close":

    Mezi dvema trend-vlnami stejneho smeru (A, C) lezi korekce B v opacnem
    smeru. Po B-confirmation a pred C-confirmation prijde bar, ktery svym
    wickem prekroci B-extrem (= zamyslena BOS-flip line trendu), ALE close
    baru ho neprekroci. C pak v trendu pokracuje za B-extrem a udela nove
    extreme (= podminka uzivatele 2: A + B + C).

    V tomto pripade byla B fakticky sum — bot ji puvodne potvrdil, ale
    prurazem WICKU vznikla v grafu "NIC" zona (mezi koncem A a confirmation C),
    ve ktere se neotevíraly pozice. Funkce ji odstrani a rozsiri C tak, ze
    pohlti celou NIC zonu (podminka 3b):
      - new_C.draw_left  = puvodni B.draw_left (= konec predchozi trendove vlny v case)
      - new_C.box_top/box_bottom prepocteno z celeho noveho rozsahu vc. wicku
      - fib/sl/tp/move_pct prepocteno standardnim `_append_wave_sig`

    Ochrany proti regresim:
      - Triggeruje VYHRADNE kdyz dirA == dirC a dirB opacny (= triplet
        trend-korekce-trend, ne EXT flip).
      - EXT korekce explicitne preskakujeme (`is_ext`) — uzivatel potvrdil,
        ze EXT je vyloucena uz % velikosti, ale pro jistotu i guard.
      - Vyzaduje skutecny wick (high > B.box_top & close <= B.box_top, nebo
        symetricky pro DOWN). Bez nej se vlna NEzasahne.
      - Vyzaduje C-pokracovani trendu za B-extrem; bez nej se vlna NEzasahne.
    """
    if df is None or df.empty or len(waves) < 3:
        return waves, birth

    out = list(waves)
    new_birth = dict(birth)
    n_bars = len(df)
    after_gap = _compute_after_data_gap_mask(df["time"])

    safety = 0
    safety_limit = max(8, len(out) * 3)
    i = 1

    while i < len(out) - 1 and safety < safety_limit:
        safety += 1
        A = out[i - 1]
        B = out[i]
        C = out[i + 1]

        dirA = int(A["dir"])
        dirB = int(B["dir"])
        dirC = int(C["dir"])

        # Trend-korekce-trend triplet (A a C stejny smer, B opacny).
        if dirA == dirB or dirC != dirA:
            i += 1
            continue

        # EXT korekce se nikdy nepovazuje za sum.
        if bool(B.get("is_ext", False)):
            i += 1
            continue

        b_wt = str(B.get("wave_time", ""))
        c_wt = str(C.get("wave_time", ""))
        b_birth = new_birth.get(b_wt)
        c_birth = new_birth.get(c_wt)
        if b_birth is None or c_birth is None:
            i += 1
            continue
        if b_birth + 1 > c_birth or c_birth >= n_bars:
            i += 1
            continue

        # Podminky A + B: hledame WICK prurazu B-extremu BEZ BOS close
        seg = df.iloc[b_birth + 1 : c_birth + 1]
        if seg.empty:
            i += 1
            continue
        highs = seg["high"].astype(float)
        lows = seg["low"].astype(float)
        closes = seg["close"].astype(float)

        wick_price: float | None = None
        if dirB == 1:
            # UP korekce -> wick HIGH > box_top, close <= box_top
            threshold = float(B["box_top"])
            mask = (highs > threshold) & (closes <= threshold)
            if mask.any():
                wick_idx = highs[mask].idxmax()
                wick_price = float(highs.loc[wick_idx])
        else:
            # DOWN korekce -> wick LOW < box_bottom, close >= box_bottom
            threshold = float(B["box_bottom"])
            mask = (lows < threshold) & (closes >= threshold)
            if mask.any():
                wick_idx = lows[mask].idxmin()
                wick_price = float(lows.loc[wick_idx])

        if wick_price is None:
            i += 1
            continue

        # Podminka C: C skutecne pokracuje v trendu za B-extrem (nove low/high).
        if dirC == -1:
            if not (float(C["box_bottom"]) < float(B["box_bottom"])):
                i += 1
                continue
        else:
            if not (float(C["box_top"]) > float(B["box_top"])):
                i += 1
                continue

        # Vse splneno -> odstran B, rozsir C pres celou NIC zonu.
        new_draw_left = int(B["draw_left"])
        new_draw_right = int(C["draw_right"])
        if (
            new_draw_left < 0
            or new_draw_right < 0
            or new_draw_left >= n_bars
            or new_draw_right >= n_bars
            or new_draw_left >= new_draw_right
        ):
            i += 1
            continue

        new_box_top, new_box_bot = _segment_extremes_with_gaps(
            df, new_draw_left, new_draw_right, dirC, after_gap
        )
        if new_box_top <= new_box_bot:
            i += 1
            continue

        # Pojistka: vynutit zahrnuti wicku (typicky uz v rozsahu segmentu, ale safety).
        if dirC == -1:
            new_box_top = max(new_box_top, wick_price)
        else:
            new_box_bot = min(new_box_bot, wick_price)

        pivot_lvl = new_box_bot if dirC == 1 else new_box_top
        cand_lvl = new_box_top if dirC == 1 else new_box_bot

        rebuilt = _append_wave_sig(
            cfg,
            w_dir=dirC,
            pivot_level=float(pivot_lvl),
            cand_level=float(cand_lvl),
            box_top=float(new_box_top),
            box_bottom=float(new_box_bot),
            pivot_bar_idx=new_draw_left,
            cand_bar_idx=new_draw_right,
            wave_time_str=c_wt,
        )
        if rebuilt is None:
            i += 1
            continue

        new_C = dict(C)
        new_C.update(rebuilt)

        if b_wt in new_birth:
            del new_birth[b_wt]

        out[i + 1] = new_C
        del out[i]
        # i nezvysujeme: rekontrolujeme z teze pozice (kaskadovani triplet).
        continue

    return out, new_birth


def pine_seed_state_after_wave(wave: dict, bar_idx: int, row) -> dict:
    """
    Pine detektor stav ihned po potvrzení vlny na baru bar_idx.

    Používá se pro navázání klasické detekce vln po dokončení WF vlny
    (WF draw_right = bar_idx).
    """
    w_dir = int(wave.get("dir", 0))
    high = float(row["high"])
    low = float(row["low"])
    t = row["time"]
    end_bar = int(wave.get("draw_right", bar_idx))
    if w_dir == -1:
        pivot_price = float(wave["box_bottom"])
    elif w_dir == 1:
        pivot_price = float(wave["box_top"])
    else:
        pivot_price = float(wave.get("box_top", high))
    return {
        "pivot_price": pivot_price,
        "pivot_time": t,
        "pivot_bar": end_bar,
        "pivot_dir": w_dir,
        "cand_price": high if w_dir == -1 else low,
        "cand_time": t,
        "cand_bar": int(bar_idx),
        "w_qualified": False,
        "opp_cnt": 0,
        "forming_ext_first_hit": -1,
    }


def run_pine_wave_simulation_from_seed(
    df: pd.DataFrame,
    cfg: BotConfig,
    wf_wave: dict,
) -> Tuple[List[dict], Dict[str, int]]:
    """
    Klasická Pine detekce navázaná na konec WF vlny (draw_right + 1).

    Vrací nové klasické vlny a jejich birth mapu (bez WF).
    """
    end_bar = int(wf_wave.get("draw_right", -1))
    if df is None or end_bar < 0 or end_bar >= len(df) - 1:
        return [], {}
    from backtest.ohlc_arrays import ohlc_from_dataframe

    ohlc = ohlc_from_dataframe(df)
    seed = pine_seed_state_after_wave(wf_wave, end_bar, ohlc.bar_view(end_bar))
    waves, birth, _, _ = run_pine_wave_simulation(
        df,
        cfg,
        start_bar=end_bar + 1,
        initial_state=seed,
        segment_mode=True,
    )
    return waves, birth


def run_pine_wave_simulation(
    df: pd.DataFrame,
    cfg: BotConfig,
    *,
    start_bar: int = 1,
    initial_state: dict | None = None,
    segment_mode: bool = False,
) -> Tuple[List[dict], Dict[str, int], Dict[str, int], Dict[str, int]]:
    """
    Jedna historicka projekce: seznam signalu vlny + mapa wave_time -> bar index narozeni (potvrzeni).

    Treti slovnik: ext_counter_suppress_from_bar — prvni bar, kdy kandidat prvni vlny
    po EXT dosahl ext_wave_min_pct jeste pred min_opp_bars (blokace counter casu).

    Ctvrty: ext_forming_first_bar — prvni bar, kdy kandidat na EXT dosahl
    ext_wave_min_pct jeste pred potvrzenim (counter cas muze zacit drive).
    """
    waves: List[dict] = []
    birth: Dict[str, int] = {}
    ext_counter_suppress_from_bar: Dict[str, int] = {}

    ext_forming_first_bar: Dict[str, int] = {}

    if df is None or len(df) < 2:
        return waves, birth, ext_counter_suppress_from_bar, ext_forming_first_bar

    from backtest.ohlc_arrays import ohlc_from_dataframe

    ohlc = ohlc_from_dataframe(df)
    after_data_gap = ohlc.after_data_gap.tolist()
    n_bars = ohlc.n

    loop_start = max(1, int(start_bar))
    if initial_state is not None:
        pivot_price = float(initial_state["pivot_price"])
        pivot_time = initial_state["pivot_time"]
        pivot_bar = int(initial_state["pivot_bar"])
        pivot_dir = int(initial_state["pivot_dir"])
        cand_price = float(initial_state["cand_price"])
        cand_time = initial_state["cand_time"]
        cand_bar = int(initial_state["cand_bar"])
        w_qualified = bool(initial_state.get("w_qualified", False))
        opp_cnt = int(initial_state.get("opp_cnt", 0))
        forming_ext_first_hit = int(initial_state.get("forming_ext_first_hit", -1))
    else:
        pivot_price = float(ohlc.high[0])
        pivot_time = ohlc.time_at(0)
        pivot_bar = 0
        pivot_dir = 1

        cand_price = float(ohlc.low[0])
        cand_time = ohlc.time_at(0)
        cand_bar = 0

        w_qualified = False
        opp_cnt = 0
        forming_ext_first_hit = -1

    from strategy.ext_range import (
        ExtRangeMeasureTracker,
        ExtRangeTracker,
        ext_range_enabled,
        ext_range_wave_min_pct,
        on_wave_confirmed_in_ext_measure_range,
        on_wave_confirmed_in_ext_range,
        start_ext_range_measure,
        start_ext_range,
        tag_wave_ext_range,
        tag_wave_ext_post_trend_seed,
    )
    from strategy.ext_logic import is_ext_wave

    ext_tracker = ExtRangeTracker()
    ext_measure_tracker = ExtRangeMeasureTracker()
    ext_range_on = ext_range_enabled(cfg)

    ext_counter_on = bool(getattr(cfg, "ext_enabled", False)) and bool(
        getattr(cfg, "ext_counter_enabled", False)
    )
    ext_size_thr = float(getattr(cfg, "ext_wave_min_pct", 0.0) or 0.0)
    ext_counter_watch_wt: str | None = None
    ext_counter_watch_from_bar = -1

    for i in range(loop_start, n_bars):
        high = float(ohlc.high[i])
        low = float(ohlc.low[i])
        open_ = float(ohlc.open[i])
        close_ = float(ohlc.close[i])
        t = ohlc.time_at(i)

        w_dir = -pivot_dir

        if after_data_gap[i]:
            prev_close = float(ohlc.close[i - 1])
            prev_high = float(ohlc.high[i - 1])
            prev_low = float(ohlc.low[i - 1])
            prev_time = ohlc.time_at(i - 1)
            pivot_price, cand_price, pivot_ref, cand_ref = _bridge_gap_prices_with_refs(
                w_dir,
                pivot_price,
                cand_price,
                prev_close=prev_close,
                prev_high=prev_high,
                prev_low=prev_low,
                open_=open_,
                high=high,
                low=low,
            )
            # Hybrid A+B: gap pusobi jako virtualni segment uvnitr detektoru.
            # Pokud novy extrem vlny vznikl na pred-gap close/high/low nebo na
            # prvnim post-gap baru, musi se posunout i jeho cas/index — jinak
            # se vlna cenove prepocita spravne, ale zustane useknuta v case.
            #
            # ref hodnoty:
            #   "existing" — extrem zustal puvodni, neaktualizuj
            #   "prev"     — extrem patri pred-gap baru (i-1)
            #   "cur"      — extrem patri prvnimu post-gap baru (i)
            if pivot_ref == "prev":
                pivot_time = prev_time
                pivot_bar = i - 1
            elif pivot_ref == "cur":
                pivot_time = t
                pivot_bar = i

            if cand_ref == "prev":
                cand_time = prev_time
                cand_bar = i - 1
            elif cand_ref == "cur":
                cand_time = t
                cand_bar = i

        invalidate = False
        if not w_qualified:
            if w_dir == 1 and low < pivot_price:
                invalidate = True
            elif w_dir == -1 and high > pivot_price:
                invalidate = True

        if invalidate:
            pivot_price = cand_price
            pivot_time = cand_time
            pivot_bar = cand_bar
            pivot_dir = w_dir
            cand_price = low if w_dir == 1 else high
            cand_time = t
            cand_bar = i
            opp_cnt = 0
            w_qualified = False
            forming_ext_first_hit = -1

        is_opp_now = _is_opp_bar(w_dir, close_, open_)

        # Post-gap bar (Monday open po vikendu nebo jakykoli data gap) NESMI byt
        # pocitan jako "opacna" svicka pro confirm vlny:
        #   - Open prvni post-gap baru je netradovatelny — zavisi na vikendovem
        #     newsflow a uderu Monday open, ne na realnem reverze vlny.
        #   - Close>=open (pro DOWN vlnu) muze vzniknout cistym poklesem cele
        #     svicky (gap-down a pak intra-bar mirny bounce do close > open),
        #     coz neni realny opp signal.
        # Bez tohoto guard se vlna predcasne potvrdi 3 svicemi (gap + 2 dalsi)
        # a nedotahne se k skutecnemu post-gap extremu.
        gap_bar = bool(after_data_gap[i])
        effective_is_opp = is_opp_now and not gap_bar

        if not invalidate:
            if w_dir == 1:
                if high > cand_price:
                    cand_price = high
                    cand_time = t
                    cand_bar = i
                    opp_cnt = 1 if (effective_is_opp and w_qualified) else 0
                elif effective_is_opp and w_qualified:
                    opp_cnt += 1
            else:
                if low < cand_price:
                    cand_price = low
                    cand_time = t
                    cand_bar = i
                    opp_cnt = 1 if (effective_is_opp and w_qualified) else 0
                elif effective_is_opp and w_qualified:
                    opp_cnt += 1

        move_pct = abs(cand_price - pivot_price) / max(1e-12, abs(pivot_price)) * 100.0
        wave_min_thr = (
            ext_range_wave_min_pct(cfg)
            if ext_range_on and ext_measure_tracker.active
            else float(cfg.wave_min_pct)
        )
        if (not w_qualified) and move_pct >= wave_min_thr:
            w_qualified = True

        if ext_counter_on and ext_size_thr > 0.0 and move_pct >= ext_size_thr:
            if forming_ext_first_hit < 0:
                forming_ext_first_hit = i

        do_confirm = w_qualified and opp_cnt >= int(cfg.min_opp_bars)
        # Nevytvarej novou vlnu tesne pred data gapem — pokracuj pres vikend jako TV.
        if do_confirm and i + 1 < n_bars and after_data_gap[i + 1]:
            do_confirm = False

        if (
            ext_counter_on
            and ext_size_thr > 0.0
            and ext_counter_watch_wt
            and i > ext_counter_watch_from_bar
            and ext_counter_watch_wt not in ext_counter_suppress_from_bar
            and move_pct >= ext_size_thr
        ):
            ext_counter_suppress_from_bar[ext_counter_watch_wt] = i

        if do_confirm:
            box_top = cand_price if w_dir == 1 else pivot_price
            box_bot = pivot_price if w_dir == 1 else cand_price

            if box_top > box_bot and cand_bar > pivot_bar:
                wt_str = _format_wave_time_str(ohlc.time_at(cand_bar))
                sig = _append_wave_sig(
                    cfg,
                    w_dir=w_dir,
                    pivot_level=float(pivot_price),
                    cand_level=float(cand_price),
                    box_top=box_top,
                    box_bottom=box_bot,
                    pivot_bar_idx=pivot_bar,
                    cand_bar_idx=cand_bar,
                    wave_time_str=wt_str,
                )
                if sig is not None:
                    tag_wave_ext_post_trend_seed(sig, trend_dir=None)
                    if ext_range_on:
                        if is_ext_wave(sig, cfg):
                            start_ext_range(ext_tracker, sig)
                            start_ext_range_measure(ext_measure_tracker, sig)
                            tag_wave_ext_range(sig, in_range=True)
                        else:
                            tag_wave_ext_range(sig, in_range=bool(ext_tracker.active))
                            if ext_tracker.active:
                                confirmed_dir = on_wave_confirmed_in_ext_range(
                                    ext_tracker, sig, cfg
                                )
                                if confirmed_dir in (1, -1):
                                    tag_wave_ext_range(sig, in_range=False)
                                    tag_wave_ext_post_trend_seed(
                                        sig, trend_dir=confirmed_dir,
                                    )
                            if ext_measure_tracker.active:
                                on_wave_confirmed_in_ext_measure_range(
                                    ext_measure_tracker, sig, cfg,
                                )
                    waves.append(sig)
                    birth[sig["wave_time"]] = i
                    if ext_counter_on and ext_size_thr > 0.0:
                        wt_sig = str(sig["wave_time"])
                        if is_ext_wave(sig, cfg):
                            if forming_ext_first_hit >= 0:
                                ext_forming_first_bar[wt_sig] = forming_ext_first_hit
                            ext_counter_watch_wt = wt_sig
                            ext_counter_watch_from_bar = i
                        elif (
                            ext_counter_watch_wt
                            and i > ext_counter_watch_from_bar
                        ):
                            ext_counter_watch_wt = None
                            ext_counter_watch_from_bar = -1
                    forming_ext_first_hit = -1

            pivot_price = cand_price
            pivot_time = cand_time
            pivot_bar = cand_bar
            pivot_dir = w_dir
            cand_price = low if w_dir == 1 else high
            cand_time = t
            cand_bar = i
            opp_cnt = 0
            w_qualified = False

    # Vikendovy gap-merge MUSI bezet i v segment_mode (WF resume). Jinak by se
    # po prvni WF aktivaci cely zbytek behu detekoval v segmentu a vikendove
    # gapy by se uz nikdy nespojily do jedne vlny (rozpad bear/bull vlny pres
    # vikend na dve utnute casti). Merge je bezpecny — spojuje jen sousedni
    # stejnosmerne vlny svazane data-gapem v ramci tohoto segmentu.
    waves, birth = _merge_waves_across_data_gaps(df, cfg, waves, birth)

    if getattr(cfg, "wave_plus", False):
        _apply_wave_plus_extend(df, cfg, waves, ohlc=ohlc)

    if not segment_mode:
        waves, birth = _remove_wick_invalidated_corrections(df, cfg, waves, birth)

    # Vsechny vlny po finalnim post-processingu (merge, wave_plus, wick cleanup)
    # dostanou `weekend_gap_pct`. Hodnotu pak cte `is_ext_wave` v ext_logic pro
    # relax EXT prahu pri pohybu v souladu s gap-jumpem.
    _tag_weekend_gap_pct(df, waves, after_data_gap)
    # Recompute EXT metadata once we know weekend_gap_pct, in pripade vln,
    # ktere driv nebyly EXT, ale teprve s relax prahem jimi byt mohou.
    from strategy.ext_logic import compute_ext_metadata as _recompute_ext

    for _w in waves:
        try:
            _recompute_ext(_w, cfg)
        except Exception:
            pass

    if ext_range_on:
        from strategy.ext_range import reapply_ext_range_tags
        reapply_ext_range_tags(waves, cfg, df=df, wave_birth=birth)
    else:
        from strategy.trend_bos import tag_waves_hh_hl_pass
        tag_waves_hh_hl_pass(df, waves, cfg)

    if ext_counter_on:
        surviving_ext = {
            str(w["wave_time"])
            for w in waves
            if is_ext_wave(w, cfg)
        }
        ext_counter_suppress_from_bar = {
            k: v
            for k, v in ext_counter_suppress_from_bar.items()
            if k in surviving_ext
        }
        ext_forming_first_bar = {
            k: v
            for k, v in ext_forming_first_bar.items()
            if k in surviving_ext
        }

    return waves, birth, ext_counter_suppress_from_bar, ext_forming_first_bar


def detect_waves_pine(df, cfg: BotConfig) -> List[dict]:
    from backtest.wave_sim_cache import run_pine_wave_simulation_cached

    w, _, _, _ = run_pine_wave_simulation_cached(df, cfg)
    return w


def compute_wave_birth_bars_pine(df: pd.DataFrame, cfg: BotConfig) -> Dict[str, int]:
    from backtest.wave_sim_cache import run_pine_wave_simulation_cached

    _, b, _, _ = run_pine_wave_simulation_cached(df, cfg)
    return b
