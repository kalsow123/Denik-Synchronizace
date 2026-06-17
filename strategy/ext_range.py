"""
EXT range — po EXT vlne se do potvrzeni noveho trendu pocitaji a obchoduji vlny na obe strany.

Ukonceni range (novy trend ustaven):
  - Jakmile se po EXT vytvori 2 vlny STEJNEHO smeru (nemusi jit hned po sobe).
  - Nebo jakykoli trendovy BOS flip (WAVE_BOS) po EXT.

Behem range:
  - nizsi prah velikosti vlny (`ext_range_wave_min_pct`),
  - vlny nesou `in_ext_range=True` (engine bypass trend filtru, vizualizace).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional
import pandas as pd

from config.bot_config import BotConfig
from strategy.trend_bos import TrendState


@dataclass
class ExtRangeTracker:
    """Stav behem jedne EXT range faze v pine simulaci."""

    active: bool = False
    up_waves: int = 0
    down_waves: int = 0


@dataclass
class ExtRangeMeasureTracker:
    """Oddeleny stav pro mereni vln (snizeny ext_range_wave_min_pct)."""

    active: bool = False
    new_trend_dir: int = 0
    consecutive_structural: int = 0
    trend: TrendState = field(default_factory=TrendState)


@dataclass
class ExtPostTrendSeedTracker:
    """
    Samostatny tracker pro definici trendu po EXT.

    Na rozdil od `ExtRangeTracker` NEresi, jestli je jeste povoleno obchodovat
    obe strany. Pocita jen potvrzene vlny po EXT a hleda "druhou vlnu stejneho
    smeru", ktera se ma stat seed-vlnou noveho trendu.

    Dulezite:
      - bezi jen po EXT,
      - ignoruje mezitimni BOS flipy,
      - resetuje se az novou EXT vlnou nebo po nalezeni seed-vlny.
    """

    active: bool = False
    up_waves: int = 0
    down_waves: int = 0


def ext_range_wave_min_pct(cfg: BotConfig) -> float:
    """Prah move_pct pro vlny behem EXT range (mensi nez wave_min_pct)."""
    raw = getattr(cfg, "ext_range_wave_min_pct", None)
    if raw is not None:
        try:
            v = float(raw)
            if v > 0.0:
                return v
        except (TypeError, ValueError):
            pass
    return max(0.08, float(cfg.wave_min_pct) * 0.5)


def ext_range_enabled(cfg: BotConfig) -> bool:
    return bool(getattr(cfg, "ext_enabled", False)) and bool(
        getattr(cfg, "ext_trade_both_sides_in_range", False)
    )


def start_ext_range(tracker: ExtRangeTracker, ext_wave: dict) -> None:
    ext_dir = int(ext_wave.get("dir", 0))
    if ext_dir not in (1, -1):
        return
    tracker.active = True
    tracker.up_waves = 0
    tracker.down_waves = 0


def start_ext_range_measure(tracker: ExtRangeMeasureTracker, ext_wave: dict) -> None:
    ext_dir = int(ext_wave.get("dir", 0))
    if ext_dir not in (1, -1):
        return
    tracker.active = True
    tracker.new_trend_dir = -ext_dir
    tracker.consecutive_structural = 0
    tracker.trend = TrendState()


def start_ext_post_trend_seed_tracking(
    tracker: ExtPostTrendSeedTracker,
    ext_wave: dict,
) -> None:
    ext_dir = int(ext_wave.get("dir", 0))
    if ext_dir not in (1, -1):
        return
    tracker.active = True
    tracker.up_waves = 0
    tracker.down_waves = 0


def ext_range_confirm_waves(cfg: BotConfig) -> int:
    return max(1, int(getattr(cfg, "ext_range_confirm_waves", 2) or 2))


def _structural_wave_for_new_trend(tracker: ExtRangeMeasureTracker, wave: dict) -> bool:
    """True pokud vlna jde smerem noveho trendu a splni HH+HL / LL+LH."""
    from strategy.trend_bos import _wave_passes_hh_hl_structure

    wdir = int(wave.get("dir", 0))
    if wdir != tracker.new_trend_dir:
        return False
    return _wave_passes_hh_hl_structure(tracker.trend, wave)


def on_wave_confirmed_in_ext_measure_range(
    tracker: ExtRangeMeasureTracker,
    wave: dict,
    cfg: BotConfig,
) -> None:
    """
    Aktualizuje tracker mereni po potvrzeni vlny.
    Pouziva se jen pro side-effect nad `tracker`.
    """
    if not tracker.active:
        return False

    from strategy.trend_bos import maybe_update_trend_state_with_wave

    if _structural_wave_for_new_trend(tracker, wave):
        tracker.consecutive_structural += 1
        if tracker.consecutive_structural >= int(
            getattr(cfg, "ext_range_confirm_waves", 2) or 2
        ):
            tracker.active = False
            tracker.consecutive_structural = 0
    else:
        tracker.consecutive_structural = 0

    maybe_update_trend_state_with_wave(tracker.trend, wave, cfg)


def _check_wave_min_pct_and_tag(wave: dict, cfg: BotConfig, in_ext_window: bool) -> bool:
    if not (in_ext_window and getattr(cfg, "wave_min_pct_enable", False)):
        return True

    move_pct = float(wave.get("move_pct", 0.0))
    eff_pct = effective_wave_min_pct(cfg, in_ext_window)
    std_pct = float(cfg.wave_min_pct)

    if move_pct >= eff_pct:
        if move_pct < std_pct:
            wave["counted_via_volatility_threshold"] = True
        return True
    return False


def on_wave_confirmed_in_ext_range(
    tracker: ExtRangeTracker,
    wave: dict,
    cfg: BotConfig,
    ext_ref: dict | None = None,
) -> int:
    """
    Aktualizuje tracker po potvrzeni vlny v EXT range.
    Vraci smer noveho trendu (+1/-1), pokud range prave skoncila.
    """
    if not tracker.active:
        return 0

    if not _check_wave_min_pct_and_tag(wave, cfg, in_ext_window=True):
        return 0

    # §1.2(a): HH stejnosměrné vůči EXT extremu ukončí range okamžitě.
    if ext_ref is not None and ext_post_wave_makes_hh_vs_ref(wave, ext_ref):
        tracker.active = False
        return int(wave.get("dir", 0))

    wdir = int(wave.get("dir", 0))
    if wdir == 1:
        tracker.up_waves += 1
        seen = tracker.up_waves
    elif wdir == -1:
        tracker.down_waves += 1
        seen = tracker.down_waves
    else:
        return 0

    if seen >= ext_range_confirm_waves(cfg):
        tracker.active = False
        return wdir
    return 0


def on_wave_confirmed_in_ext_post_trend_seed(
    tracker: ExtPostTrendSeedTracker,
    wave: dict,
    cfg: BotConfig,
    in_ext_window: bool,
) -> int:
    """
    Pocita vsechny potvrzene vlny po EXT pro definici trendu po EXT.

    Na rozdil od `on_wave_confirmed_in_ext_range` se tento tracker
    NEukoncuje pri mezitimnim BOS flipu. Jakmile najde druhou vlnu stejneho
    smeru po EXT, vrati jeji smer (+1/-1) a vlna se ma oznacit jako
    `ext_post_trend_seed_dir`.
    """
    if not tracker.active:
        return 0

    if not _check_wave_min_pct_and_tag(wave, cfg, in_ext_window=in_ext_window):
        return 0

    wdir = int(wave.get("dir", 0))
    if wdir == 1:
        tracker.up_waves += 1
        seen = tracker.up_waves
    elif wdir == -1:
        tracker.down_waves += 1
        seen = tracker.down_waves
    else:
        return 0

    if seen >= ext_range_confirm_waves(cfg):
        tracker.active = False
        return wdir
    return 0


def tag_wave_ext_range(wave: dict, *, in_range: bool) -> None:
    wave["in_ext_range"] = bool(in_range)


def tag_wave_ext_post_trend_seed(wave: dict, *, trend_dir: int | None) -> None:
    if trend_dir in (1, -1):
        wave["ext_post_trend_seed_dir"] = int(trend_dir)
    else:
        wave.pop("ext_post_trend_seed_dir", None)


def tag_wave_post_ext_trend_suppressed(wave: dict, *, suppressed: bool) -> None:
    """
    Vlna v post-EXT lock zone proti smeru seed-trendu — neexistuje vizualne
    ani obchodne (do nasledujiciho BOS, ktery zamek ukonci).
    """
    if suppressed:
        wave["post_ext_trend_suppressed"] = True
    else:
        wave.pop("post_ext_trend_suppressed", None)


def wave_post_ext_trend_suppressed(wave: dict) -> bool:
    return bool(wave.get("post_ext_trend_suppressed", False))


def wave_in_ext_range(wave: dict, cfg: BotConfig) -> bool:
    if not ext_range_enabled(cfg):
        return False
    return bool(wave.get("in_ext_range", False))


def _resolve_wave_for_pending_protection(
    order,
    waves_by_time: dict | None,
) -> dict | None:
    signal = getattr(order, "signal", None) or {}
    wave_time = str(signal.get("wave_time", getattr(order, "wave_time", "")))
    if waves_by_time and wave_time:
        wave = waves_by_time.get(wave_time)
        if wave is not None:
            return wave
    if signal.get("wave_time"):
        return signal
    return None


def pending_protected_from_bos_direction_cancel(
    order,
    cfg: BotConfig,
    *,
    waves_by_time: dict | None = None,
) -> bool:
    """True = bezny W-pending v EXT range nezrusit pri BOS broken_dir cancel."""
    if not getattr(cfg, "ext_range_protect_pendings_from_bos_cancel", True):
        return False
    if not ext_range_enabled(cfg):
        return False
    if getattr(order, "is_ext", False) and not getattr(order, "is_counter", False):
        return False
    if getattr(order, "is_pp", False):
        return False
    if getattr(order, "is_counter", False):
        return False
    if getattr(order, "is_two_sided_mirror", False):
        return False
    from strategy.wave_sequence import is_wave_counter_trade

    if is_wave_counter_trade(order):
        return False
    wave = _resolve_wave_for_pending_protection(order, waves_by_time)
    if wave is None:
        return False
    return wave_in_ext_range(wave, cfg)


def pending_protected_from_bos_direction_cancel_by_comment(
    comment: str,
    cfg: BotConfig,
    waves_by_time: dict | None,
) -> bool:
    """Live varianta: W{wave_time} pending v EXT range nezrusit BOS cancel."""
    if not getattr(cfg, "ext_range_protect_pendings_from_bos_cancel", True):
        return False
    if not ext_range_enabled(cfg):
        return False
    if not waves_by_time:
        return False
    c = (comment or "").strip()
    if not c.upper().startswith("W"):
        return False
    wave = waves_by_time.get(c[1:])
    if wave is None:
        return False
    return wave_in_ext_range(wave, cfg)


def wave_allowed_in_ext_range(wave: dict, cfg: BotConfig) -> bool:
    """Behem EXT range obchoduj oba smery (nezavisle na BOS trend filtru)."""
    return wave_in_ext_range(wave, cfg)


def check_close_breaks_ext_extreme(bar_close: float, ext_wave: dict, direction: int) -> bool:
    """True pokud close baru prorazi HIGH EXT UP / LOW EXT DOWN."""
    try:
        if direction == 1:
            return bar_close > float(ext_wave.get("ext_high", ext_wave.get("box_top")))
        elif direction == -1:
            return bar_close < float(ext_wave.get("ext_low", ext_wave.get("box_bottom")))
    except (KeyError, TypeError, ValueError):
        pass
    return False


def _bos_flipped_before_wave(
    ext_wave: dict,
    wave: dict,
    df: Any,
    wave_birth: dict[str, int],
) -> bool:
    """True pokud close prorazil EXT swing (BOS) pred narozenim `wave`."""
    try:
        ext_bi = int(wave_birth[str(ext_wave["wave_time"])])
        bi = int(wave_birth[str(wave["wave_time"])])
    except (KeyError, TypeError, ValueError):
        return False
    if bi <= ext_bi:
        return False
    ext_dir = int(ext_wave.get("dir", 0))
    closes = df["close"].astype(float).to_numpy()
    # Vcetne baru narozeni vlny (close na tomto baru muze uz byt BOS).
    scan_end = min(bi + 1, len(closes))
    if ext_dir == -1:
        level = float(ext_wave["box_top"])
        for i in range(ext_bi + 1, scan_end):
            if float(closes[i]) > level:
                return True
    elif ext_dir == 1:
        level = float(ext_wave["box_bottom"])
        for i in range(ext_bi + 1, scan_end):
            if float(closes[i]) < level:
                return True
    return False


def _wave_bos_flipped_before_wave(
    ext_wave: dict,
    wave: dict,
    wave_birth: dict[str, int],
    bos_flip_bars: set[int],
) -> bool:
    """True pokud po EXT a pred narozenim `wave` nastal libovolny trendovy BOS flip."""
    try:
        ext_bi = int(wave_birth[str(ext_wave["wave_time"])])
        bi = int(wave_birth[str(wave["wave_time"])])
    except (KeyError, TypeError, ValueError):
        return False
    if bi <= ext_bi:
        return False
    return any(ext_bi < int(fb) <= bi for fb in bos_flip_bars)


def _collect_trend_bos_flip_bars(
    df: Any,
    waves: List[dict],
    cfg: BotConfig,
    wave_birth: dict[str, int],
) -> set[int]:
    """Vrati bary, na kterych po close doslo k trendovemu BOS flipu."""
    if df is None or getattr(df, "empty", False) or not waves or not wave_birth:
        return set()

    from strategy.trend_bos import _detect_close_bos_timeline_flips

    return {
        int(fb)
        for fb, _ft in _detect_close_bos_timeline_flips(
            df, waves, cfg, wave_birth_bars=wave_birth
        )
    }


def _apply_post_ext_trend_suppression(
    waves: List[dict],
    wave_birth: dict[str, int],
    bos_flip_bars: set[int],
) -> None:
    """
    Po EXT vlne, kde byla potvrzena nova trendova smerovka (`ext_post_trend_seed_dir`),
    plati ZAMEK az do naseldujiciho close-based BOS flipu PROTI tomuto seed smeru:
      * Vsechny vlny narozene v zamcene zone, ktere jdou proti seed-smeru, dostanou
        tag `post_ext_trend_suppressed=True`.
      * Po BOS flipu (bar > bar flipu) zamek konci a dalsi vlny jsou normalni.

    Pravidlo je nezavisle od BOS retro mechanismu — ten tyto vlny preskoci
    (kontroluje `post_ext_trend_suppressed`), takze post-EXT zamcena BEAR vlna
    se nikdy nestane "BOS-vlnou" pro retro vykresleni / vstup.
    """
    if not waves or not wave_birth:
        return

    ordered = sorted(
        waves,
        key=lambda w: (
            int(wave_birth.get(str(w.get("wave_time", "")), 0)),
            str(w.get("wave_time", "")),
        ),
    )
    sorted_flips = sorted(int(b) for b in bos_flip_bars)

    lock_dir: int = 0
    lock_end_bar: int = -1  # exkluzivni: vlny narozene > lock_end_bar uz nejsou zamcene

    for w in ordered:
        wt = str(w.get("wave_time", ""))
        b_raw = wave_birth.get(wt)
        if b_raw is None:
            continue
        b = int(b_raw)

        if lock_dir != 0 and 0 <= lock_end_bar < b:
            lock_dir = 0
            lock_end_bar = -1

        if lock_dir != 0 and int(w.get("dir", 0)) == -lock_dir:
            tag_wave_post_ext_trend_suppressed(w, suppressed=True)
        else:
            tag_wave_post_ext_trend_suppressed(w, suppressed=False)

        seed = w.get("ext_post_trend_seed_dir")
        if seed in (1, -1):
            lock_dir = int(seed)
            # Hledej prvni BOS flip STRIKTNE po baru narozeni seed-vlny.
            next_flip: int | None = None
            for fb in sorted_flips:
                if fb > b:
                    next_flip = fb
                    break
            lock_end_bar = next_flip if next_flip is not None else 10**9


def tag_waves_post_ext_confirmed_trend_lock(
    waves: List[dict],
    wave_birth: dict[str, int],
    bos_flip_bars: set[int],
    cfg: BotConfig,
    df: Any = None,
) -> None:
    """
    Po každé EXT vlně sleduj následující vlny. Jakmile mezi nimi padnou
    N (cfg.ext_post_confirmed_trend_count) vln v jednom směru:
      - confirm_dir = ten směr,
      - od následující vlny po confirmation dál nastav vlnám flag
        wave["post_ext_confirmed_trend_lock"] = True
        a wave["post_ext_confirmed_trend_dir"] = confirm_dir.
    
    Lock končí:
      - další EXT vlna (nový reset),
      - close-based BOS flip,
      - vyčerpání cfg.max_wave_age_hours od EXT vlny (záchranný timeout).
    """
    if not waves or not getattr(cfg, "ext_post_confirmed_trend_lock_enabled", True):
        for w in waves:
            w["post_ext_confirmed_trend_lock"] = False
        return

    from strategy.ext_logic import is_ext_wave

    target_count = int(getattr(cfg, "ext_post_confirmed_trend_count", 2) or 2)
    max_age_h = int(getattr(cfg, "max_wave_age_hours", 8) or 8)

    ordered = sorted(
        waves,
        key=lambda w: (
            int(wave_birth.get(str(w.get("wave_time", "")), 0)),
            str(w.get("wave_time", "")),
        ),
    )
    sorted_flips = sorted(int(b) for b in bos_flip_bars)

    lock_active = False
    lock_dir = 0
    lock_end_bar = -1
    ext_time_dt = None

    up_count = 0
    down_count = 0

    for w in ordered:
        wt = str(w.get("wave_time", ""))
        b_raw = wave_birth.get(wt)
        if b_raw is None:
            continue
        b = int(b_raw)

        # Check lock end condition: BOS flip
        if lock_active and 0 <= lock_end_bar < b:
            lock_active = False
            lock_dir = 0
            lock_end_bar = -1

        # Check lock end condition: max_wave_age_hours
        if lock_active and ext_time_dt is not None:
            if df is not None and not df.empty and b < len(df):
                cur_time = pd.Timestamp(df["time"].iloc[b])
                if (cur_time - ext_time_dt).total_seconds() / 3600.0 > max_age_h:
                    lock_active = False
                    lock_dir = 0
                    lock_end_bar = -1

        if is_ext_wave(w, cfg):
            lock_active = False
            lock_dir = 0
            lock_end_bar = -1
            up_count = 0
            down_count = 0
            if df is not None and not df.empty and b < len(df):
                ext_time_dt = pd.Timestamp(df["time"].iloc[b])
            else:
                ext_time_dt = None
            w["post_ext_confirmed_trend_lock"] = False
            continue

        if lock_active:
            w["post_ext_confirmed_trend_lock"] = True
            w["post_ext_confirmed_trend_dir"] = lock_dir
            continue

        w["post_ext_confirmed_trend_lock"] = False

        # Not locked yet, count waves
        if ext_time_dt is not None:  # We are after an EXT wave
            wdir = int(w.get("dir", 0))
            if wdir == 1:
                up_count += 1
            elif wdir == -1:
                down_count += 1

            if up_count >= target_count or down_count >= target_count:
                lock_active = True
                lock_dir = 1 if up_count >= target_count else -1
                # Find next BOS flip
                next_flip = None
                for fb in sorted_flips:
                    if fb > b:
                        next_flip = fb
                        break
                lock_end_bar = next_flip if next_flip is not None else 10**9

    if lock_active:
        # Final check for the end of df
        b = int(wave_birth.get(str(ordered[-1].get("wave_time", "")), 0)) if ordered else 0
        for fb in sorted_flips:
            if fb > b:
                lock_active = False
                lock_dir = 0
                break
        if lock_active and ext_time_dt is not None and df is not None and not df.empty:
            cur_time = pd.Timestamp(df["time"].iloc[-1])
            if (cur_time - ext_time_dt).total_seconds() / 3600.0 > max_age_h:
                lock_active = False
                lock_dir = 0

    if waves:
        waves[0]["_live_post_ext_lock_active"] = lock_active
        waves[0]["_live_post_ext_lock_dir"] = lock_dir


def reapply_ext_range_tags(
    waves: List[dict],
    cfg: BotConfig,
    df: Any = None,
    wave_birth: dict[str, int] | None = None,
) -> None:
    """
    Po merge / wave_plus znovu nastavi `in_ext_range` + `ext_post_trend_seed_dir`
    + `post_ext_trend_suppressed`.

    Behy:
      1) Vycisteni vsech relevantnich tagu.
      2) Pre-tagging BOS flip detekce (cista strukturalni — bez seed efektu)
         pro terminaci trade-range trackeru.
      3) Hlavni iterace nad vlnami: nastavi `in_ext_range` a oznaci seed-vlnu
         (2x same-dir post-EXT).
      4) Recompute BOS flips uz SE seed tagy.
      5) Aplikace post-EXT lock suppresse pro vlny v zamcene zone.
    """
    if not waves:
        return
    from strategy.ext_logic import is_ext_wave

    if not ext_range_enabled(cfg):
        for w in waves:
            tag_wave_ext_range(w, in_range=False)
            tag_wave_ext_post_trend_seed(w, trend_dir=None)
            tag_wave_post_ext_trend_suppressed(w, suppressed=False)
        from strategy.trend_bos import tag_waves_hh_hl_pass

        tag_waves_hh_hl_pass(df, waves, cfg)
        return

    wave_birth = wave_birth or {}

    # 1) Vycisteni.
    for w in waves:
        tag_wave_ext_range(w, in_range=False)
        tag_wave_ext_post_trend_seed(w, trend_dir=None)
        tag_wave_post_ext_trend_suppressed(w, suppressed=False)

    # 2) Strukturalni BOS flipy (bez seed tagu — slouzi pro terminaci range trackeru).
    bos_flip_bars = _collect_trend_bos_flip_bars(df, waves, cfg, wave_birth)

    # 3) Range tracker + seed tagging.
    tracker = ExtRangeTracker()
    seed_tracker = ExtPostTrendSeedTracker()
    ext_ref: dict | None = None
    ordered = sorted(
        waves,
        key=lambda x: (int(x.get("draw_left", 0)), str(x.get("wave_time", ""))),
    )
    for w in ordered:
        if is_ext_wave(w, cfg):
            start_ext_range(tracker, w)
            start_ext_post_trend_seed_tracking(seed_tracker, w)
            ext_ref = w
            tag_wave_ext_range(w, in_range=True)
        else:
            seed_dir = on_wave_confirmed_in_ext_post_trend_seed(
                seed_tracker, w, cfg, in_ext_window=tracker.active
            )
            if seed_dir in (1, -1):
                tag_wave_ext_post_trend_seed(w, trend_dir=seed_dir)

            if tracker.active and not is_ext_wave(w, cfg):
                if (
                    ext_ref is not None
                    and (
                        (
                            df is not None
                            and wave_birth
                            and _bos_flipped_before_wave(ext_ref, w, df, wave_birth)
                        )
                        or (
                            wave_birth
                            and bos_flip_bars
                            and _wave_bos_flipped_before_wave(
                                ext_ref, w, wave_birth, bos_flip_bars,
                            )
                        )
                    )
                ):
                    tracker.active = False
                    seed_tracker.active = False
                    ext_ref = None
            if tracker.active:
                confirmed_dir = on_wave_confirmed_in_ext_range(
                    tracker, w, cfg, ext_ref=ext_ref
                )
                if confirmed_dir in (1, -1):
                    seed_tracker.active = False
                    ext_ref = None
                else:
                    tag_wave_ext_range(w, in_range=True)

    # 3b) Po wave_sequence: synchronizuj in_ext dle terminatoru (CESTA D / §6.7).
    apply_in_ext_range_from_sequence_terminators(waves, cfg)

    # 4) BOS flipy se SEEDS — pouzity pro vymezeni lock zon (pass 5).
    bos_flip_bars_with_seeds = _collect_trend_bos_flip_bars(
        df, waves, cfg, wave_birth
    )

    # 5) Post-EXT lock suppression — nezavisla, oddelena funkce.
    _apply_post_ext_trend_suppression(
        waves, wave_birth, bos_flip_bars_with_seeds
    )

    # 6) Post-EXT confirmed trend lock
    tag_waves_post_ext_confirmed_trend_lock(
        waves, wave_birth, bos_flip_bars_with_seeds, cfg, df=df
    )

    # 7) HH/HL pass tagovani — vola se az po post-EXT zamceni
    from strategy.trend_bos import tag_waves_hh_hl_pass
    tag_waves_hh_hl_pass(df, waves, cfg)


def effective_wave_min_pct(cfg: BotConfig, in_ext_window: bool) -> float:
    """
    Vrací wave_min_pct dle stavu EXT both-sides okna.
    - Pokud in_ext_window=True a cfg.wave_min_pct_enable=True
      vrátí cfg.ext_post_both_sides_wave_min_pct
    - Jinak vrátí cfg.wave_min_pct
    """
    if in_ext_window and getattr(cfg, "wave_min_pct_enable", False):
        try:
            return float(getattr(cfg, "ext_post_both_sides_wave_min_pct", 0.13))
        except (ValueError, TypeError):
            pass
    return float(cfg.wave_min_pct)


def ext_post_wave_makes_hh_vs_ref(wave: dict, ext_ref: dict) -> bool:
    """§1.2(a): stejnosměrná vlna s novým extremem oproti parent EXT."""
    ed = int(ext_ref.get("dir", 0))
    if ed == 1:
        w_top, e_top = wave.get("box_top"), ext_ref.get("box_top")
        if w_top is None or e_top is None:
            return False
        return float(w_top) > float(e_top)
    if ed == -1:
        w_bot, e_bot = wave.get("box_bottom"), ext_ref.get("box_bottom")
        if w_bot is None or e_bot is None:
            return False
        return float(w_bot) < float(e_bot)
    return False


def _ext_post_range_should_terminate(
    ext_ref: dict,
    wave: dict,
    *,
    post_same_dir_count: int,
    post_opposite_count: int,
) -> bool:
    """§1.2(a)/(b): konec both-sides režimu (in_ext_range) — bez volání wave_sequence."""
    parent_dir = int(ext_ref.get("dir", 0))
    wdir = int(wave.get("dir", 0))
    if wdir == parent_dir:
        if ext_post_wave_makes_hh_vs_ref(wave, ext_ref):
            return True
        if post_same_dir_count >= 2:
            return True
        return False
    if (parent_dir == 1 and wdir == -1) or (parent_dir == -1 and wdir == 1):
        return post_opposite_count >= 2
    return False


def apply_in_ext_range_from_sequence_terminators(
    waves: List[dict],
    cfg: BotConfig,
) -> None:
    """
    Po `sync_wave_sequence_state`: vynutí in_ext_range=False od vlny s
    `ext_post_range_terminator` (§6.7 / CESTA D).

    Nepřepisuje in_ext=True — ten nastavuje hlavní smyčka (včetně BOS flip stop).
    """
    if not ext_range_enabled(cfg) or not waves:
        return
    from strategy.ext_logic import is_ext_wave

    ordered = sorted(
        waves,
        key=lambda x: (int(x.get("draw_left", 0)), str(x.get("wave_time", ""))),
    )
    terminated = False
    for w in ordered:
        if is_ext_wave(w, cfg):
            terminated = False
            continue
        if w.get("ext_post_range_terminator"):
            terminated = True
            tag_wave_ext_range(w, in_range=False)
            continue
        if terminated:
            tag_wave_ext_range(w, in_range=False)


def check_close_breaks_ext_extreme(
    bar_close: float,
    ext_wave: dict,
) -> bool:
    """True pokud close baru prorazí HIGH EXT UP / LOW EXT DOWN."""
    if ext_wave.get("dir") == 1:
        return bar_close > float(ext_wave["box_top"])
    elif ext_wave.get("dir") == -1:
        return bar_close < float(ext_wave["box_bottom"])
    return False


def check_ext_bos_via_fib_35(
    bar_close: float,
    ext_wave: dict,
) -> bool:
    """True pokud close baru prorazí 0.35 fib úroveň EXT vlny.
    
    EXT UP: bear BOS na close < ext_fib_35_level
    EXT DOWN: bull BOS na close > ext_fib_35_level
    
    ext_fib_35_level musí být uložené na ext_wave dict (z ext_logic.py).
    """
    fib_35 = ext_wave.get("ext_bos_level") or ext_wave.get("ext_fib_35_level")
    if fib_35 is None:
        return False
    if ext_wave.get("dir") == 1:
        return bar_close < float(fib_35)
    elif ext_wave.get("dir") == -1:
        return bar_close > float(fib_35)
    return False


def ext_scenario_classify(
    wave: dict,
    state: "TrendState",
    bar_close: float,
    swing_levels: dict,
) -> str:
    """Klasifikuje EXT vlnu do scénáře A/B/C/D dle Kroku 2.
    
    swing_levels: {"last_up_box_bottom": ..., "last_down_box_top": ...}
    
    Returns: "A" (BOS vlna), "B" (counter), "C" (trend-dir), "D" (neutral).
    """
    if state.direction == "neutral":
        return "D"
    if wave.get("dir") == 1 and state.direction == "bull":
        return "C"
    if wave.get("dir") == -1 and state.direction == "bear":
        return "C"
    # wave.dir je opačný k state.direction
    if state.direction == "bull":
        swing_level = swing_levels.get("last_up_box_bottom")
        if swing_level is not None and bar_close < float(swing_level):
            return "A"
        return "B"
    elif state.direction == "bear":
        swing_level = swing_levels.get("last_down_box_top")
        if swing_level is not None and bar_close > float(swing_level):
            return "A"
        return "B"
    return "B"

