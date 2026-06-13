"""Centralizovaná pravidla viditelnosti vln — shodná s HTML chartem a index_in_trend."""
from __future__ import annotations

from typing import Any, FrozenSet, Set

from strategy.trend_bos import _wave_is_wf_origin
from strategy.wick_fakeout import WAVE_ORIGIN_WF


def wave_passes_visual_filter(
    wave: dict,
    cfg: Any,
    *,
    check_bos: bool = True,
    bos_wave_times: Set[str] | FrozenSet[str] | None = None,
    wf_visual_wave_times: Set[str] | FrozenSet[str] | None = None,
    two_sided_fired_times: Set[str] | FrozenSet[str] | None = None,
    exclude_wf_from_bos: bool = True,
) -> bool:
    """
    True pokud se vlna vykresluje v HTML (box + label).

    check_bos=False: BOS výjimka se nevyhodnocuje (počítadlo index_in_trend —
    BOS větve řeší engine/wave_sequence zvlášť).

    _visual_trade_anchor: jen HTML — vlna doplněná u obchodu bez boxu (viz
    supplement_visual_waves_for_trades); runtime filtr se nemění.
    """
    if wave.get("_visual_trade_anchor"):
        return True

    wt = str(wave.get("wave_time", "") or "")
    bos_set = bos_wave_times or frozenset()
    wf_vis = wf_visual_wave_times or frozenset()
    ts_set = two_sided_fired_times or frozenset()

    if check_bos and wt and wt in bos_set:
        if not (exclude_wf_from_bos and _wave_is_wf_origin(wave)):
            return True

    is_ext = bool(wave.get("is_ext", False))
    if not is_ext and cfg is not None:
        try:
            from strategy.ext_logic import is_ext_wave

            is_ext = is_ext_wave(wave, cfg)
        except Exception:
            pass

    is_in_ext_range = bool(wave.get("in_ext_range", False)) and bool(
        getattr(cfg, "ext_trade_both_sides_in_range", False)
    )
    is_wf = (
        str(wave.get("wave_origin", "")) == WAVE_ORIGIN_WF
        or bool(wave.get("is_wf", False))
        or (wt and wt in wf_vis)
    )
    is_two_sided = bool(
        wave.get("_two_sided_counter")
        or wave.get("two_sided_show")
        or wave.get("is_two_sided_counter")
        or (wt and wt in ts_set)
    )

    if is_ext or is_in_ext_range or is_wf or is_two_sided:
        return True

    if wave.get("post_ext_trend_suppressed", False):
        return False
    if wave.get("post_ext_confirmed_trend_lock", False):
        return False

    if cfg is not None and getattr(cfg, "trend_hh_hl_filter_enabled", False):
        if not wave.get("hh_hl_pass", True):
            return False

    return True
