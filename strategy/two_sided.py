"""
TWO-SIDED ENTRY — doplnkovy WAVE vstup na prvni protivlni Pine vlnu po velke vlně.

Nezasahuje do EXT / PP / BOS re-entry / beznych WAVE na ostatnich vlnach.
Pouze kdyz by jinak na protivlni nevznikla pozice:

  1. Rodic A musi byt ve smeru AKTUALNIHO trendu (UP v bullu, DOWN v bearu).
     V neutralnim trendu (pred prvni potvrzenou vlnou) se nikdy nestane rodicem.
  2. Rodic A: move_pct v [two_sided_entry_min_wave_pct, ext_wave_min_pct), ne EXT.
  3. A NESMI byt v EXT range (po EXT do potvrzeni noveho trendu pokryva EXT
     obousmerne obchodovani — WAVE_TWO_SIDED by bylo zdvojene).
  4. Dotek FIB entry_fib_level (typ. 0.5) boxu A.
  5. B = prvni opacna potvrzena Pine vlna po A (>= wave_min_pct).
  6. B jen WAVE (ne EXT). B NESMI byt v EXT range ani v trend-direction
     (pokud byl mezi A a B BOS flip, B by mohla byt v novem trendu — pak ji
     odmitnem, protoze counter ma znacit jen counter-trend vlnu).
  7. Na B LIMIT na fib50 + min SL two_sided_entry_min_sl_move_pct.

Trend state se predava jako snapshot k baru NAROZENI vlny (out of
`strategy.trend_bos.compute_trend_states_per_wave`). Bez snapshotu (None)
zustava jen velikostni / EXT filtr — pouziva se v testech / diagnostice;
PRODUKCNI volaci (backtest engine, live loop) MUSI snapshot predat, jinak
two-sided "rezi" v lax modu.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

import pandas as pd

from config.bot_config import BotConfig
from strategy.ext_logic import _ensure_min_sl_distance, is_ext_wave

if TYPE_CHECKING:
    from strategy.trend_bos import TrendState


def two_sided_min_wave_size_pct(cfg: BotConfig) -> float:
    return float(getattr(cfg, "two_sided_entry_min_wave_pct", 0.0) or 0.0)


def two_sided_parent_max_wave_pct(cfg: BotConfig) -> float:
    """Horni mez rodice (exclusive) = cfg.ext_wave_min_pct."""
    return float(getattr(cfg, "ext_wave_min_pct", 0.0) or 0.0)


def two_sided_min_sl_move_pct(cfg: BotConfig) -> float:
    raw = getattr(cfg, "two_sided_entry_min_sl_move_pct", None)
    if raw is not None:
        try:
            v = float(raw)
            if v > 0.0:
                return v
        except (TypeError, ValueError):
            pass
    return float(getattr(cfg, "ext_min_sl_move_pct", 0.16) or 0.16)


def wave_counter_two_sided_enabled(cfg: BotConfig) -> bool:
    """Master switch: WAVE_COUNTER + WAVE_TWO_SIDED (grid: wave_counter_two_sided_enabled)."""
    if bool(getattr(cfg, "wave_counter_two_sided_enabled", False)):
        return True
    return bool(getattr(cfg, "counter_position_enabled", False)) or bool(
        getattr(cfg, "two_sided_entry_enabled", False)
    )


def wave_isolation_study_enabled(cfg: BotConfig) -> bool:
    """Wave study: routing counter/two-sided bez realnych orderu."""
    return bool(getattr(cfg, "wave_isolation_study", False))


def wave_counter_two_sided_routing_enabled(cfg: BotConfig) -> bool:
    """Skip primary, tracker, two_sided_only — stejne jako zapnuty modul."""
    return wave_counter_two_sided_enabled(cfg) or wave_isolation_study_enabled(cfg)


def wave_counter_two_sided_orders_enabled(cfg: BotConfig) -> bool:
    """Skutecne counter / two-sided ordery."""
    if wave_isolation_study_enabled(cfg):
        if bool(getattr(cfg, "live_study_two_sided_mirror_orders", False)):
            return wave_counter_two_sided_enabled(cfg)
        return False
    return wave_counter_two_sided_enabled(cfg)


def two_sided_enabled(cfg: BotConfig) -> bool:
    """Routing (skip/tracker). Pro ordery viz wave_counter_two_sided_orders_enabled."""
    return wave_counter_two_sided_routing_enabled(cfg)


def skip_primary_entry_on_parent_wave(
    wave: dict,
    cfg: BotConfig,
    *,
    trend_state: Any = None,
) -> bool:
    """Ma se primarni WAVE vstup na two-sided rodici A preskocit?

    `trend_state` musi byt snapshot k baru narozeni vlny (TrendState). Bez nej se
    pravidlo aplikuje pouze velikostne / EXT range — pouziva se v testech a
    diagnostice. Engine i live loop predavaji snapshot vzdy.

    Ridi se flagem cfg.skip_primary_entry_on_parent_wave_enable:
      True  = primarni vstup na rodici A se PRESKOCI (jen two-sided protipozice
              na protivlni B) — puvodni chovani.
      False = rodic A obchoduje i svuj vlastni primarni WAVE vstup (fib50,
              klasicky SL 0.8) NAVIC k two-sided protipozici (default).
    """
    if not two_sided_enabled(cfg):
        return False
    if not bool(getattr(cfg, "wave_position_enabled", True)):
        return False
    # Skip vypnuty → rodic obchoduje i svuj primarni vstup.
    if not bool(getattr(cfg, "skip_primary_entry_on_parent_wave_enable", False)):
        return False
    return parent_wave_qualifies(wave, cfg, trend_state=trend_state)


def _trend_dir_int(trend_state: Any) -> int | None:
    """Prevedeni TrendState.direction na +1 / -1 / None (neutral / no-state)."""
    if trend_state is None:
        return None
    direction = getattr(trend_state, "direction", None)
    if direction == "bull":
        return 1
    if direction == "bear":
        return -1
    return None


def parent_wave_qualifies(
    wave: dict,
    cfg: BotConfig,
    *,
    trend_state: Any = None,
) -> bool:
    """Rodic: WAVE v [min_two_sided, ext_wave_min_pct), ne EXT, NE v EXT range.

    Pri predanem `trend_state` (snapshot k baru narozeni vlny) navic plati:
      - smer rodice MUSI souhlasit se smerem trendu (bull → dir=+1, bear → dir=-1)
      - neutral trend → rodic se neaktivuje (zatim neni co kontrovat)

    Bez `trend_state` se trend kontrola preskoci (lax mode pro testy /
    diagnostiku). Engine + live loop musi `trend_state` vzdy predat — jinak se
    obnovuje stara chyba "counter pozice prebijejici trend-direction vlny".
    """
    if not two_sided_enabled(cfg):
        return False
    if is_ext_wave(wave, cfg):
        return False
    # Po EXT pokryva ext_trade_both_sides_in_range obchodovani na obe strany;
    # WAVE_TWO_SIDED by se zdvojovalo — odmitnem rodice v EXT range.
    if bool(wave.get("in_ext_range", False)):
        return False
    try:
        size_pct = float(wave.get("move_pct", 0.0))
    except (TypeError, ValueError):
        return False
    min_pct = two_sided_min_wave_size_pct(cfg)
    max_pct = two_sided_parent_max_wave_pct(cfg)
    if size_pct < min_pct:
        return False
    if max_pct > 0.0 and size_pct >= max_pct:
        return False
    if trend_state is not None:
        trend_dir = _trend_dir_int(trend_state)
        if trend_dir is None:
            # neutral / unknown trend → bez rodicu.
            return False
        try:
            wdir = int(wave.get("dir", 0))
        except (TypeError, ValueError):
            return False
        if wdir != trend_dir:
            return False
    return True


def counter_wave_qualifies_for_two_sided(
    wave: dict,
    cfg: BotConfig,
    *,
    trend_state: Any = None,
) -> bool:
    """Protivlna B = jen klasicka WAVE (ne EXT, ne in_ext_range, ne v trend-dir).

    Po EXT do potvrzeni noveho trendu obchoduje EXT range obousmerne; B ve
    `in_ext_range` by se prekryvala s tim rezimem → odmitnem.

    Pri predanem `trend_state` (snapshot k baru narozeni B) navic plati:
      - B MUSI byt counter-trend k aktualnimu trendu (bull → dir=-1, bear → dir=+1)
      - pri trend-dir B by counter "znacil" trend-dir vlnu, coz uzivatel zakazal
        (typicky scenar: BOS flip mezi rodicem A a counterem B, B se ocitla v
         novem trendu — pak two-sided nesmi vzniknout).
    """
    if is_ext_wave(wave, cfg):
        return False
    if bool(wave.get("in_ext_range", False)):
        return False
    if trend_state is not None:
        trend_dir = _trend_dir_int(trend_state)
        if trend_dir is None:
            # neutral / unknown trend → counter se neaktivuje (nemame proti cemu).
            return False
        try:
            wdir = int(wave.get("dir", 0))
        except (TypeError, ValueError):
            return False
        # B musi byt counter-trend.
        if wdir == trend_dir:
            return False
    return True


def retracement_fib_price(wave: dict, cfg: BotConfig) -> float:
    box_top = float(wave["box_top"])
    box_bottom = float(wave["box_bottom"])
    w_range = box_top - box_bottom
    fib_lvl = float(cfg.entry_fib_level)
    w_dir = int(wave["dir"])
    if w_dir == 1:
        return box_top - w_range * fib_lvl
    return box_bottom + w_range * fib_lvl


def bar_touched_price(high: float, low: float, level: float) -> bool:
    return float(low) <= float(level) <= float(high)


def wave_meets_pine_min_size(wave: dict, cfg: BotConfig) -> bool:
    try:
        return float(wave.get("move_pct", 0.0)) >= float(cfg.wave_min_pct)
    except (TypeError, ValueError):
        return False


def parent_monitor_start_bar(wave: dict) -> int:
    """Bar zacatku boxu parenta — od tohoto baru se sleduje dotek FIB."""
    try:
        return int(wave.get("draw_left", 0))
    except (TypeError, ValueError):
        return 0


def parent_monitor_end_bar(wave: dict) -> int:
    """Bar konce boxu parenta — vlna je hotova, tracker se registruje."""
    try:
        return int(wave.get("draw_right", wave.get("draw_left", 0)))
    except (TypeError, ValueError):
        return 0


def find_parent_wave_for_two_sided(
    waves: List[dict],
    counter_wave: dict,
    cfg: BotConfig,
    *,
    trend_states_per_wave: Optional[Dict[str, Any]] = None,
) -> Optional[dict]:
    """
    Rodic A = posledni velka vlna pred B v trend-direction (parent_wave_qualifies).
    B musi byt prvni opacna Pine vlna po A (>= wave_min_pct).
    Poradi dle draw_left (cas boxu), ne poradi v seznamu po detekci.

    `trend_states_per_wave` je mapa {wave_time → TrendState k baru narozeni}.
    Pri predani se kvalifikace rodice plne aplikuje (smer trendu + EXT range +
    velikost). Pri None se trend kontrola u rodice preskoci (lax mode).
    """
    try:
        cdir = int(counter_wave["dir"])
    except (TypeError, ValueError, KeyError):
        return None
    if not wave_meets_pine_min_size(counter_wave, cfg):
        return None

    try:
        c_left = int(counter_wave.get("draw_left", 0))
    except (TypeError, ValueError):
        c_left = 0

    ordered = sorted(
        waves,
        key=lambda w: (
            int(w.get("draw_left", 0)),
            str(w.get("wave_time", "")),
        ),
    )

    parent: Optional[dict] = None
    blocked = False
    counter_wt = counter_wave.get("wave_time")

    for w in ordered:
        wt = w.get("wave_time")
        if wt == counter_wt:
            if parent is None or blocked:
                return None
            return parent
        try:
            wdir = int(w["dir"])
            w_left = int(w.get("draw_left", 0))
            p_right = int(w.get("draw_right", w_left))
        except (TypeError, ValueError, KeyError):
            continue

        if w_left > c_left:
            continue

        ts_w = (
            trend_states_per_wave.get(str(wt))
            if trend_states_per_wave is not None
            else None
        )
        if (
            parent_wave_qualifies(w, cfg, trend_state=ts_w)
            and wdir == -cdir
        ):
            if p_right > c_left:
                continue
            parent = w
            blocked = False
            continue

        if parent is None or blocked:
            continue

        if wdir == int(parent["dir"]):
            continue
        if wdir == cdir and wave_meets_pine_min_size(w, cfg):
            blocked = True

    return None


@dataclass
class TwoSidedWatch:
    wave_time: str
    fib_price: float
    birth_bar: int
    touched: bool = False
    touch_bar: int = -1


@dataclass
class ArmedTwoSided:
    parent: dict
    fib_touched: bool = False
    touch_bar: int = -1
    entry_fired: bool = False


@dataclass
class TwoSidedTracker:
    watches: Dict[str, TwoSidedWatch] = field(default_factory=dict)
    armed: Dict[str, ArmedTwoSided] = field(default_factory=dict)
    # B-vlny (counter) navázané na aktivního parenta A — i když A už není v `waves`.
    counter_b_wave_times: Set[str] = field(default_factory=set)

    def register_parent(
        self,
        wave: dict,
        monitor_bar: int,
        cfg: BotConfig,
        *,
        df: pd.DataFrame | None = None,
        sync_from_bar: int | None = None,
        trend_state: Any = None,
    ) -> bool:
        """
        Zaregistruje hotovou parent vlnu (0.5 % … EXT, v trend-direction).
        sync_from_bar: od ktereho baru hledat dotek FIB (typ. draw_left).
        `trend_state`: snapshot k baru narozeni vlny (TrendState) — viz
            `parent_wave_qualifies`. Bez snapshotu se trend kontrola preskoci.
        Vraci True pokud byla nova registrace.
        """
        if not parent_wave_qualifies(wave, cfg, trend_state=trend_state):
            return False
        wt = str(wave.get("wave_time", ""))
        if not wt:
            return False
        if wt in self.watches:
            return False
        start = (
            int(sync_from_bar)
            if sync_from_bar is not None
            else parent_monitor_start_bar(wave)
        )
        self.watches[wt] = TwoSidedWatch(
            wave_time=wt,
            fib_price=retracement_fib_price(wave, cfg),
            birth_bar=int(monitor_bar),
            touched=False,
        )
        self.armed[wt] = ArmedTwoSided(parent=dict(wave))
        if df is not None and len(df) > 0:
            self.sync_touches_from_df(df, from_bar=start)
        return True

    def _mark_fib_touch(self, wt: str, bar_idx: int) -> None:
        watch = self.watches.get(wt)
        if watch and not watch.touched:
            watch.touched = True
            watch.touch_bar = int(bar_idx)
        armed = self.armed.get(wt)
        if armed and not armed.fib_touched:
            armed.fib_touched = True
            armed.touch_bar = int(bar_idx)

    def update_bar(self, high: float, low: float, bar_idx: int) -> None:
        for wt, watch in self.watches.items():
            if not watch.touched and bar_touched_price(high, low, watch.fib_price):
                self._mark_fib_touch(wt, bar_idx)

    def fib_was_touched(self, parent_wave_time: str) -> bool:
        w = self.watches.get(str(parent_wave_time))
        return bool(w and w.touched)

    def discard_parent(self, parent_wave_time: str) -> None:
        wt = str(parent_wave_time)
        self.watches.pop(wt, None)
        self.armed.pop(wt, None)

    def clear_all(self) -> None:
        self.watches.clear()
        self.armed.clear()
        self.counter_b_wave_times.clear()

    def register_counter_b_wave(self, wave_time: str) -> None:
        """Označí vlnu jako B (counter) pro aktivního parenta — blokuje primární WAVE."""
        wt = str(wave_time or "").strip()
        if wt:
            self.counter_b_wave_times.add(wt)

    def is_b_wave_for_any_parent(self, wave_time: str) -> bool:
        """True pokud je vlna registrovaná jako two-sided B (counter child)."""
        return str(wave_time or "").strip() in self.counter_b_wave_times

    def waves_with_armed_parents(self, visible_waves: List[dict]) -> List[dict]:
        """Doplní do lookup seznamu parent A z trackeru (i když HH/HL filtr je skryl)."""
        by_wt: Dict[str, dict] = {}
        for w in visible_waves:
            wt = str(w.get("wave_time", ""))
            if wt:
                by_wt[wt] = w
        for armed in self.armed.values():
            parent = armed.parent
            pwt = str(parent.get("wave_time", ""))
            if pwt and pwt not in by_wt:
                by_wt[pwt] = parent
        return list(by_wt.values())

    def link_counter_b_wave_if_matches(
        self,
        counter_wave: dict,
        visible_waves: List[dict],
        cfg: BotConfig,
        *,
        trend_states_per_wave: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Pokud je counter_wave platná B pro některého armed parenta, zaregistruje ji.
        Parent A může chybět v `visible_waves` — bere se z trackeru.
        """
        if not two_sided_enabled(cfg):
            return False
        cwt = str(counter_wave.get("wave_time", ""))
        if not cwt:
            return False
        merged = self.waves_with_armed_parents(visible_waves)
        parent = find_parent_wave_for_two_sided(
            merged,
            counter_wave,
            cfg,
            trend_states_per_wave=trend_states_per_wave,
        )
        if parent is None:
            return False
        parent_wt = str(parent.get("wave_time", ""))
        touched = self.fib_was_touched(parent_wt)
        ts_parent = (
            trend_states_per_wave.get(parent_wt)
            if trend_states_per_wave is not None
            else None
        )
        ts_counter = (
            trend_states_per_wave.get(cwt)
            if trend_states_per_wave is not None
            else None
        )
        if not should_open_two_sided_counter(
            parent,
            counter_wave,
            cfg,
            parent_fib_touched=touched,
            parent_trend_state=ts_parent,
            counter_trend_state=ts_counter,
        ):
            return False
        self.register_counter_b_wave(cwt)
        return True

    def sync_touches_from_df(
        self,
        df: pd.DataFrame,
        *,
        from_bar: int = 0,
    ) -> None:
        n = len(df)
        for wt, watch in self.watches.items():
            if watch.touched:
                continue
            start = max(from_bar, watch.birth_bar)
            for i in range(start, n):
                row = df.iloc[i]
                if bar_touched_price(
                    float(row["high"]), float(row["low"]), watch.fib_price
                ):
                    self._mark_fib_touch(wt, i)
                    break


def should_open_two_sided_counter(
    parent_wave: Optional[dict],
    counter_wave: dict,
    cfg: BotConfig,
    *,
    parent_fib_touched: bool,
    parent_trend_state: Any = None,
    counter_trend_state: Any = None,
) -> bool:
    """Konecny gate pred vystavenim TWO-SIDED counter pozice.

    `parent_trend_state` / `counter_trend_state` = snapshoty TrendState k barum
    narozeni odpovidajicich vln. Bez nich se trend kontrola preskoci (lax mode).
    Pri zapnute kontrole se vyzaduje:
      - parent ve smeru trendu k jeho baru narozeni (parent_wave_qualifies)
      - counter B counter-trend k jeho baru narozeni (counter_wave_qualifies_*)
      - mezi A a B nesmela trend prevratit tak, aby B byla v novem trendu.
    """
    if not two_sided_enabled(cfg):
        return False
    if parent_wave is None or not parent_fib_touched:
        return False
    if not parent_wave_qualifies(parent_wave, cfg, trend_state=parent_trend_state):
        return False
    if not counter_wave_qualifies_for_two_sided(
        counter_wave, cfg, trend_state=counter_trend_state
    ):
        return False
    try:
        p_dir = int(parent_wave["dir"])
        c_dir = int(counter_wave["dir"])
    except (TypeError, ValueError, KeyError):
        return False
    if p_dir not in (1, -1) or c_dir not in (1, -1):
        return False
    return c_dir == -p_dir


def study_ts2_limit_lot_entry_ref(
    ep: float,
    is_buy: bool,
    *,
    bar_open: float | None,
    decision_ask: float,
    decision_bid: float,
) -> float:
    """Odhad fill entry pro lot — parita engine _trigger_pending (same-bar fill)."""
    if bar_open is None:
        return ep
    o = float(bar_open)
    if is_buy:
        if decision_bid <= ep:
            return min(ep, o)
        return ep
    if decision_ask >= ep:
        return max(ep, o)
    return ep


def live_study_ts2_use_wave_primary_sizing(cfg: BotConfig) -> bool:
    """Live study B+: TS2_ mirror má použít W-primary EP/SL (parita engine WAVE slice)."""
    return bool(
        getattr(cfg, "live_study_two_sided_mirror_orders", False)
        and getattr(cfg, "live_study_promoted_two_sided_as_wave", False)
    )


def prepare_ts2_mirror_entry_signal(wave: dict, cfg: BotConfig) -> dict:
    """Vstupní signál pro TS2_ — lot parita s W-primary v orders.py / E2E fill."""
    return prepare_two_sided_counter_signal(wave, cfg)


def prepare_two_sided_counter_signal(wave: dict, cfg: BotConfig) -> dict:
    """Two-sided counter signal.

    SL je **vzdy presne** na absolutnim extremu (wick) counter vlny B:
      - BUY  (B = UP wave po DOWN rodici) → SL = LOW (box_bottom, vc. wicku) B
      - SELL (B = DOWN wave po UP rodici) → SL = HIGH (box_top, vc. wicku) B

    `sl_fib_level` se ignoruje, `two_sided_entry_min_sl_move_pct` se aplikuje
    jen jako poslednim zachrana proti SL=entry (kdyz by lot prepocet byl
    deleny nulou). Pokud wick lezi az pod min SL, ponechame ho na wicku.
    """
    sig = dict(wave)
    entry = float(sig["fib50"])
    direction = int(sig["dir"])
    is_buy = direction == 1
    if is_buy:
        sl = float(sig.get("box_bottom", sig.get("sl")))
    else:
        sl = float(sig.get("box_top", sig.get("sl")))
    # Pojistka jen pri SL == entry (degenerated wick — nesmi byt deleno nulou
    # v lot calc). Jinak NETLACIME SL pres wick — i kdyz je blizsi nez min SL.
    if abs(entry - sl) < 1e-9:
        sl = _ensure_min_sl_distance(
            entry, sl, is_buy=is_buy, min_pct=two_sided_min_sl_move_pct(cfg)
        )
    sl_dist = abs(entry - sl)
    rrr = float(cfg.rrr)
    tp = entry + rrr * sl_dist if is_buy else entry - rrr * sl_dist
    sig["sl"] = sl
    sig["tp"] = tp
    sig["_two_sided_counter"] = True
    return sig


def wave_show_in_visual(wave: dict) -> bool:
    from strategy.wick_fakeout import WAVE_ORIGIN_WF

    return bool(
        wave.get("_two_sided_counter")
        or wave.get("two_sided_show")
        or wave.get("wf_continued_classic")
        or str(wave.get("wave_origin", "")) == WAVE_ORIGIN_WF
    )


def waves_for_visual_display(
    df: pd.DataFrame,
    waves: List[dict],
    cfg: BotConfig,
    *,
    extra_wave_times: Optional[set[str]] = None,
) -> List[dict]:
    """Strukturalni filtr + two-sided counter vlny pro HTML."""
    from strategy.trend_bos import filter_waves_for_structure_display

    out = list(filter_waves_for_structure_display(df, waves, cfg))
    out_times = {str(w.get("wave_time", "")) for w in out}
    want = set(extra_wave_times or ())
    for w in waves:
        wt = str(w.get("wave_time", ""))
        if not wt:
            continue
        if w.get("post_ext_trend_suppressed") or w.get("post_ext_confirmed_trend_lock"):
            continue
        if wave_show_in_visual(w) or wt in want:
            want.add(wt)
    for w in waves:
        wt = str(w.get("wave_time", ""))
        if w.get("post_ext_trend_suppressed") or w.get("post_ext_confirmed_trend_lock"):
            continue
        if wt in want and wt not in out_times:
            out.append(w)
            out_times.add(wt)
    out.sort(
        key=lambda w: (
            int(w.get("draw_left", 0)),
            str(w.get("wave_time", "")),
        )
    )
    return out


def build_two_sided_wave_bar_maps(
    waves: List[dict],
    wave_birth_by_time: Dict[str, int],
) -> tuple[Dict[int, List[dict]], Dict[int, List[dict]]]:
    """Stejne mapovani jako backtest engine: birth_bar a draw_right."""
    waves_by_bar: Dict[int, List[dict]] = {}
    waves_by_end_bar: Dict[int, List[dict]] = {}
    for w in waves:
        wt = str(w.get("wave_time", ""))
        birth = wave_birth_by_time.get(wt)
        if birth is not None:
            waves_by_bar.setdefault(int(birth), []).append(w)
        end_ix = int(w.get("draw_right", w.get("draw_left", 0)))
        waves_by_end_bar.setdefault(end_ix, []).append(w)
    return waves_by_bar, waves_by_end_bar


def replay_two_sided_tracker_engine_parity(
    tracker: TwoSidedTracker,
    df: pd.DataFrame,
    waves: List[dict],
    cfg: BotConfig,
    *,
    wave_birth_by_time: Dict[str, int],
    trend_states_per_wave: Optional[Dict[str, Any]] = None,
    preserve_counter_b_wave_times: bool = True,
) -> None:
    """
    Bar-by-bar replay two-sided trackeru — stejne poradi jako backtest/engine.py:
      1) register_parent pro vlny s draw_right == i
      2) update_bar(high, low, i)
      3) register_parent pro nove narozene vlny (birth_bar == i) splnujici parent_wave_qualifies
    """
    saved_counter_b: Set[str] = set()
    if preserve_counter_b_wave_times:
        saved_counter_b = set(tracker.counter_b_wave_times)
    tracker.clear_all()
    if preserve_counter_b_wave_times:
        tracker.counter_b_wave_times |= saved_counter_b

    if not two_sided_enabled(cfg) or df is None or len(df) < 2 or not waves:
        return

    ts_map = trend_states_per_wave or {}
    waves_by_bar, waves_by_end_bar = build_two_sided_wave_bar_maps(
        waves, wave_birth_by_time
    )

    for i in range(1, len(df)):
        row = df.iloc[i]
        high = float(row["high"])
        low = float(row["low"])

        for w in waves_by_end_bar.get(i, []):
            wt = str(w.get("wave_time", ""))
            tracker.register_parent(
                w,
                i,
                cfg,
                df=df,
                sync_from_bar=parent_monitor_start_bar(w),
                trend_state=ts_map.get(wt),
            )

        tracker.update_bar(high, low, i)

        for w in waves_by_bar.get(i, []):
            wt = str(w.get("wave_time", ""))
            if parent_wave_qualifies(w, cfg, trend_state=ts_map.get(wt)):
                tracker.register_parent(
                    w,
                    i,
                    cfg,
                    df=df,
                    sync_from_bar=parent_monitor_start_bar(w),
                    trend_state=ts_map.get(wt),
                )

